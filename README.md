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
PYTHONPATH=src python -m unittest discover -s tests
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

すでにローカルへ保存済みの旧JSONスナップショットがある場合は、ネット取得処理と切り離して、共通CSVへ変換できます。この変換器は通信せず、馬・騎手・調教師を学習時と予測時で共通の正規化名IDへ変換します。以前の臨時運用で生じた「履歴は馬名、本番は馬番」というID尺度の不一致を防ぎます。

```bash
python -m keiba_prediction_lab.cli convert-local-history-snapshot \
  local/raw/history.json \
  --source-id local-private-snapshot \
  --acquired-at 2026-08-23T08:30:00+09:00 \
  --output local/converted-history
```

結果は`history.csv`、`training.csv`、入力ハッシュと仮定を記録した`snapshot-manifest.json`です。過去レースの`observed_at`と`result_known_at`は実測値ではなく、明示したオフセットによる保守的な代理時刻として記録されます。この`training.csv`は回顧的な初期基準評価用であり、実際に発走前固定したことの証拠にはしません。

予測対象カードも、競馬場・コース種別ごとの馬場状態を別JSONで明示して、1レース1CSVへ変換できます。

```json
{"札幌:turf": "良", "札幌:dirt": "良", "default": "良"}
```

```bash
python -m keiba_prediction_lab.cli convert-local-target-snapshot \
  local/raw/cards.json local/raw/track-conditions.json \
  --source-id local-private-snapshot \
  --acquired-at 2026-08-23T08:30:00+09:00 \
  --race-date 2026-08-23 \
  --observed-at 2026-08-23T08:30:00+09:00 \
  --output local/converted-targets
```

この機能は取得済みファイルを変換するだけです。外部サイトへのアクセス、利用権の承認、スクレイピング、再配布許諾を代行しません。元JSON、変換CSV、manifestはGitHubへコミットしません。

```bash
python -m keiba_prediction_lab.cli audit-training-csv local/training.csv
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

まず画面だけを安全に確認したい場合は、実データを含まない12レース分の合成デモを生成して起動できます。`local/`はGit管理対象外で、既存フォルダは上書きしません。表示される確率・評価値はUI確認専用であり、実レースの予想や購入判断には使用できません。

```bash
python -m keiba_prediction_lab.cli init-ui-demo --output local/ui-demo
python -m keiba_prediction_lab.cli serve-ui-demo local/ui-demo
```

2つ目のコマンドは既定ブラウザで`http://127.0.0.1:8765/`を開きます。自動で開かない場合はURLを手動で開いてください。終了はターミナルで`Control-C`です。すでにデモを生成済みなら、次回は`serve-ui-demo`だけを実行します。ブラウザを自動起動しない場合は`--no-open-browser`を付けます。

macOSでは、リポジトリ直下の`open-ui-demo.command`をFinderでダブルクリックしても同じ画面を起動できます。初回だけ合成デモを自動生成し、2回目以降は保存済みデモを再監査して表示します。実行中はTerminalウインドウを閉じず、終了時はそのウインドウで`Control-C`を押します。仮想環境`.venv`がない場合や起動に失敗した場合は、原因を確認できるようTerminalを開いたまま停止します。

```bash
python -m keiba_prediction_lab.cli inspect-app-state \
  --prediction-bundle outputs/race-1 \
  --walk-forward-report reports/walk-forward.json \
  --win5-forecast outputs/win5-2026-08-30.json
```

同じ監査済みデータを、将来のローカル画面が読むHTTP APIとして配信できます。外部ネットワークには公開せず、IPv4ループバックアドレス`127.0.0.1`だけで待ち受けます。

```bash
python -m keiba_prediction_lab.cli serve-read-only-api \
  --prediction-bundle outputs/race-1 \
  --walk-forward-report reports/walk-forward.json \
  --win5-forecast outputs/win5-2026-08-30.json \
  --open-browser
```

開催日の全レースを入口画面にまとめる場合は、開催日マニフェストを用意します。各`prediction_bundle`はマニフェストからの相対パスまたは絶対パスで明示し、画面側は全バンドルを監査してから競馬場タブと1R〜12Rの一覧を作ります。

```json
{
  "schema_version": "1.0",
  "race_date": "2026-08-30",
  "venues": [
    {
      "venue": "新潟",
      "races": [
        {"race_number": 1, "prediction_bundle": "outputs/niigata-1R"},
        {"race_number": 2, "prediction_bundle": "outputs/niigata-2R"}
      ]
    }
  ]
}
```

```bash
python -m keiba_prediction_lab.cli serve-read-only-api \
  --race-day-manifest race-day-2026-08-30.json \
  --walk-forward-report reports/walk-forward.json \
  --win5-forecast outputs/win5-2026-08-30.json
```

開催日マニフェストを指定すると、最初に競馬場単位の全レース一覧を表示します。一覧は1着候補、1着確率、正式な三連単1点だけを示し、行を選ぶと従来の詳細画面へ移ります。WIN5影予測は一覧画面だけに表示されます。

起動後にブラウザで`http://127.0.0.1:8765/`を開くと、読み取り専用画面を表示できます。正式候補の三連単1点100円、1着確率順位、購入額0円の影予測、ウォークフォワード指標、任意指定したWIN5影予測を別領域で確認できます。WIN5欄は`--win5-forecast`を指定した場合だけ表示され、5点の買い目ではなく対象5レース各1頭の研究用組合せです。`http://127.0.0.1:8765/api/v1/state`は監査済みスナップショット、`http://127.0.0.1:8765/health`は稼働状態を返します。終了は`Control-C`です。

この段階ではファイル選択、学習、予測、保存、自動投票を提供しません。画面の再読込も監査済みスナップショットを読み直すだけであり、入力成果物を変更しません。書込み系HTTPメソッドも拒否します。

### 6. レース後に評価する

結果・払戻・レース条件は、発走前予測を変更せず別ファイルとして追加します。

```bash
python -m keiba_prediction_lab.cli evaluate-bet-types \
  outputs/race-1 outputs/race-2 \
  --report reports/bet-types-evaluation.json
```

評価は馬券種ごとに分離し、固定100円で集計します。最大払戻を除いた回収率や上位的中への依存度も表示するため、少数の高配当だけで性能が高く見える状態を確認できます。

### WIN5対象日の影予測

対象5レースをそれぞれ正式パイプラインで事前固定した後、5つの予測バンドルを発走順に束ねます。WIN5は現在の実購入方針へ加えず、購入額0円の研究用予測として保存します。

```bash
python -m keiba_prediction_lab.cli predict-win5 \
  outputs/win5-race-1 outputs/win5-race-2 outputs/win5-race-3 \
  outputs/win5-race-4 outputs/win5-race-5 \
  --frozen-at 2026-08-30T13:30:00+09:00 \
  --output outputs/win5-2026-08-30.json

python -m keiba_prediction_lab.cli audit-win5-forecast \
  outputs/win5-2026-08-30.json
```

各レースの1着確率1位を1頭ずつ選び、初期モデルでは5レース間を独立と仮定して同時成立確率を計算します。対象レースの自動判定は行わないため、日程を確認した利用者が正しい5レースを明示します。詳しくは[WIN5影予測](docs/WIN5.md)を参照してください。

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
