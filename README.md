# Keiba Prediction Lab

中央競馬の「1着」と「三連単」を、発走前情報だけで再現可能に予測・検証するオープンソース研究プロジェクトです。

目的は利益最大化ではありません。まず1着確率を高め、その確率から三連単の着順同時確率を作り、予測が実際にどこまで当たるかを長期的に検証します。マーチンゲール、オッズに応じた購入額変更、多点購入による見かけ上の的中率向上は研究対象にしません。

## 研究上の固定ルール

| 区分 | 扱い |
|---|---|
| 実購入候補 | 三連単1点、100円固定 |
| 三連単1・3・5・10点 | 購入額0円の影予測。点数追加の有効性を事後検証する |
| 単勝・複勝・馬連・馬単・3連複・3連単 | 各券種の最上位候補を購入額0円で記録する |
| オッズ | 予測と分離する。予算の多寡で予測を変更しない |
| モデル更新 | 個々のレース結果ですぐ更新せず、固定した検証単位で判断する |
| データ | 権利と取得条件を確認したローカル入力だけを使う |

そのほか、次を必須とします。

- オッズを見る前の予想を、発走前の時刻とともに固定する
- 予測時点で利用できない結果や未来情報を特徴量に入れない
- 学習・検証・最終評価を時間順に分離する
- 1着的中率、確率校正、三連単的中率、回収率を混ぜずに評価する
- 回収率を示す場合は、高配当への依存度も併記する
- 予想モデルと買い目生成を分離する

詳しい判断基準は [プロジェクト原則](docs/PROJECT_PRINCIPLES.md)、開発段階は [ロードマップ](docs/ROADMAP.md) を参照してください。
別のMacや新しい作業環境から再開する場合は、[プロジェクト申し送り](docs/HANDOFF.md)を最初に確認してください。

## 現在できること

- 権利確認済みのローカルCSVを監査する
- 過去成績から、予測時点より前の情報だけで特徴量・学習行を作る
- 条件付きロジット初期モデルを学習し、入力ハッシュ付きで保存する
- 1レース分の入力一式を事前監査する
- 1着順位、三連単1点、二つの三連単影予測、全6馬券種影予測を一括固定する
- 保存済み予測の改ざん・ファイル差し替え・方針違反を監査する
- 監査済み予測を日本語Markdownレポートにする
- 結果と払戻を別ファイルで追加し、券種別・期間別・条件別に事後評価する

実データ源はまだプロジェクトとして承認していません。自動スクレイピングも実装しておらず、テストには合成データだけを使用しています。そのため、現段階では実競馬に対する的中率を主張しません。

## 動作環境

- Python 3.11以上
- 外部Pythonパッケージ不要
- テスト: `unittest`（標準ライブラリ）

```bash
git clone https://github.com/AichiroFunakoshi/keiba-prediction-lab.git
cd keiba-prediction-lab
python3 -m venv .venv
source .venv/bin/activate
python -m unittest discover -s tests
```

以下の例では、ソースツリーから直接CLIを実行します。

```bash
export PYTHONPATH=src
```

## 最短ワークフロー

### 1. 入力ひな型を作る

```bash
python -m keiba_prediction_lab.cli init-input-templates --output local/race-inputs
```

生成されたCSVとJSONに、利用権を確認したデータを入力します。ひな型は不完全な例であり、そのまま予測には使えません。

### 2. 学習データを監査し、モデルを固定する

```bash
python -m keiba_prediction_lab.cli audit-csv local/training.csv
python -m keiba_prediction_lab.cli prepare-training local/training.csv --output local/training.json
python -m keiba_prediction_lab.cli train-model local/training.csv --output local/model.json
```

`model.json`にはモデル係数だけでなく、学習期間、入力ハッシュ、学習条件、モデル内容のSHA-256が保存されます。既存ファイルは上書きしません。

### 3. 予測前に入力一式を監査する

モデルの時間順性能を検証する場合は、学習後・校正後・評価後の境界を先に `local/windows.json` へ固定します。

```json
[
  {
    "train_end": "2025-10-31T23:59:59+09:00",
    "calibration_end": "2025-11-30T23:59:59+09:00",
    "evaluation_end": "2025-12-31T23:59:59+09:00"
  }
]
```

```bash
python -m keiba_prediction_lab.cli evaluate-walk-forward \
  local/training.csv local/windows.json \
  --report reports/walk-forward.json
python -m keiba_prediction_lab.cli audit-walk-forward-report \
  reports/walk-forward.json
```

各窓は必ず「学習→校正→未来評価」の順とし、評価期間の重複を拒否します。標準出力にはMarkdown、`--report`には学習CSVと窓定義のSHA-256を含む上書き不可のJSONを保存します。保存後は専用監査で改変、型、窓・集計・校正・診断の不整合を確認できます。窓を結果に合わせて自動選択する機能はありません。

```bash
python -m keiba_prediction_lab.cli audit-race-inputs \
  local/model.json \
  local/history.csv \
  local/targets.csv \
  local/pace-profiles.csv \
  local/pace-scenario.json \
  --frozen-at 2026-02-01T10:05:00+09:00
```

ここでは予測ファイルを保存しません。レースID、観測時刻、発走時刻、モデル学習期限、馬IDの一致などを先に確認します。

### 4. 正式予測を発走前に固定する

```bash
python -m keiba_prediction_lab.cli predict-race \
  local/model.json \
  local/history.csv \
  local/targets.csv \
  local/pace-profiles.csv \
  local/pace-scenario.json \
  --frozen-at 2026-02-01T10:05:00+09:00 \
  --output outputs/race-1
```

`outputs/race-1`には次のファイルが作られます。

| ファイル | 内容 |
|---|---|
| `actual.json` | 1着確率順位と実購入候補の三連単1点100円 |
| `baseline-shadow.json` | 基準モデルによる三連単1・3・5・10点の影予測 |
| `pace-shadow.json` | 脚質・想定ペースを加えた三連単影予測 |
| `bet-types-shadow.json` | 全6馬券種の確率表と最上位候補。全て購入額0円 |
| `input-provenance.json` | モデルと全入力ファイルのSHA-256 |
| `manifest.json` | 各成果物のハッシュ、生成器、購入方針 |

出力ディレクトリが既に存在する場合は失敗し、事前予測を上書きしません。

### 5. 保存内容を監査し、人間向けレポートを作る

```bash
python -m keiba_prediction_lab.cli audit-prediction-bundle outputs/race-1
python -m keiba_prediction_lab.cli report-prediction-bundle outputs/race-1 --output outputs/race-1-report.md
```

レポート生成時にも監査を行います。監査した同一のバイト列からレポートを作るため、監査後の読み直しによる差し替えを避けています。`--output`を省略するとMarkdownを標準出力へ表示します。レポートも既存ファイルを上書きしません。

将来のローカルUIが使用する読み取り専用データは、次のコマンドで確認できます。指定した成果物を監査し、実購入候補と影予測、ウォークフォワード指標を構造化JSONで返します。ファイルの探索・保存・変更は行いません。

```bash
python -m keiba_prediction_lab.cli inspect-app-state \
  --prediction-bundle outputs/race-1 \
  --walk-forward-report reports/walk-forward.json
```

### 6. レース後に評価する

結果・払戻・レース条件は、発走前予測を変更せず別ファイルとして追加します。

```bash
python -m keiba_prediction_lab.cli evaluate-bet-types \
  outputs/race-1 outputs/race-2 \
  --report reports/bet-types-evaluation.json
```

評価は馬券種ごとに分離し、固定100円で集計します。最大払戻を除いた回収率や上位的中への依存度も表示するため、少数の高配当だけで性能が高く見える状態を確認できます。

## 比較・診断コマンド

```bash
python -m keiba_prediction_lab.cli compare-bet-type-reports reports/baseline.json reports/candidate.json
python -m keiba_prediction_lab.cli bootstrap-bet-type-reports reports/baseline.json reports/candidate.json --samples 10000 --seed 0
python -m keiba_prediction_lab.cli bootstrap-bet-type-reports reports/baseline.json reports/candidate.json --samples 10000 --seed 0 --resampling-unit race-date
python -m keiba_prediction_lab.cli diagnose-bet-type-reports reports/baseline.json reports/candidate.json --top-races 5
python -m keiba_prediction_lab.cli diagnose-bet-type-segments reports/baseline.json reports/candidate.json
```

- `compare-bet-type-reports`: 同一レース・同一払戻・同一条件に限定してモデル差を比較
- `bootstrap-bet-type-reports`: レース単位または開催日単位の対応95%区間を固定シードで推定
- `diagnose-bet-type-reports`: 的中・払戻差を開催日、レース、券種別に分解
- `diagnose-bet-type-segments`: 競馬場、芝・ダート、馬場、距離帯、クラス、頭数別に分解

これらは次のモデル更新を決める材料であり、個々のレース結果から係数を自動更新する機能ではありません。

## データ利用と公開範囲

リポジトリには、コード、仕様、合成テスト、出所と再配布条件を確認できるサンプルだけを含めます。取得元の利用条件が不明なレースデータ、スクレイピング結果、認証情報、実データで学習したモデルはコミットしません。

実際のレースデータは、利用者が提供元の最新規約・ライセンス・指定された取得方法を確認し、正当に利用できるものだけをローカルへ配置してください。本リポジトリのMIT Licenseは、外部データの取得・加工・再配布権を付与しません。「自己責任」と記載しても、禁止された取得や利用が許されるわけではありません。

詳しくは [データ利用方針](docs/DATA_USAGE_POLICY.md) と [ローカルデータ契約](docs/LOCAL_DATA_ADAPTER.md) を参照してください。

## 設計資料

- [プロジェクト申し送り](docs/HANDOFF.md)
- [ローカルUI設計方針](docs/UI_ARCHITECTURE.md)
- [比較用モデル](docs/BASELINES.md)
- [特徴量と基準時刻](docs/FEATURES.md)
- [初期確率モデル](docs/MODEL.md)
- [時系列検証](docs/WALK_FORWARD.md)
- [予想固定と評価](docs/FROZEN_PREDICTIONS.md)
- [三連単の条件付き確率と影予測](docs/TRIFECTA_PORTFOLIOS.md)
- [脚質・想定ペースモデル](docs/PACE_MODEL.md)
- [三連単生成モデルの対応比較](docs/MODEL_COMPARISON.md)
- [1レース予測パイプライン](docs/PIPELINE.md)
- [全6馬券種の事前予測](docs/BET_TYPE_SHADOW_FORECASTS.md)
- [全6馬券種の事後評価](docs/BET_TYPE_EVALUATION.md)

## ライセンス

プログラムコードは [MIT License](LICENSE) です。外部データには適用されず、それぞれの提供元の条件に従います。
