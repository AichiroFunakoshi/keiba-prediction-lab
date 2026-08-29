# プロジェクト申し送り

この文書は、別のMacまたは新しい作業環境から本プロジェクトを安全に再開するための基準書である。GitHubの`main`をコード・仕様の正とし、実データと生成物は各端末のローカル領域に分離する。

## 1. プロジェクトの目的と変更禁止の前提

目的は、中央競馬の1着確率と三連単着順確率を、発走前情報だけで再現可能に予測・検証することである。利益最大化、自動投票、購入額最適化は目的に含めない。

次の方針は、明示的な合意と版変更なしに変えない。

- 実購入候補は三連単1点、100円固定
- 三連単3・5・10点は購入しない影予測
- 単勝、複勝、馬連、馬単、3連複、3連単の研究候補も購入額0円
- 予算やオッズによって予測確率を変更しない
- 1着的中率、三連単的中率、確率校正、回収率を別々に評価する
- 個別レースの結果だけでモデルや係数を即時更新しない
- 発走後に判明した情報を発走前特徴量へ混入させない
- 実データの利用権が不明な場合は、取得・実装・公開を止める

詳細は[プロジェクト原則](PROJECT_PRINCIPLES.md)と[外部データ利用方針](DATA_USAGE_POLICY.md)を優先する。

## 2. GitHubと現在地

- リポジトリ: <https://github.com/AichiroFunakoshi/keiba-prediction-lab>
- 正式ブランチ: `main`
- Python: 3.11以上
- 外部Python依存: なし
- CI: Python 3.11、3.12、3.13
- この申し送りで確認した`main`: `ccd17116b01db4227e5d0528fcb25ad556bcecda`
- この申し送り直前の完了PR: `#41 Add race-day prediction dashboard`

現在の段階は[開発ロードマップ](ROADMAP.md)を正とする。概要は次のとおり。

| 段階 | 現在地 |
|---|---|
| 公開方針・データ契約・ベースライン・特徴量 | 合成データで外郭完成 |
| 実データ源 | 未承認。実データ性能は未検証 |
| 確率モデル | 条件付きロジット初期モデルを実装済み |
| 三連単・全6馬券種 | 事前固定、影予測、決済、比較、診断を実装済み |
| ウォークフォワード | ローカル実行と成果物保存を実装済み |
| 事前予測運用 | 1レース一括保存、監査、日本語レポートを実装済み |

「実装済み」は実競馬での精度を意味しない。現在の自動テストは合成データによる動作契約の確認である。

## 3. 別のMacでの開始手順

### 3.1 必要なツール

macOSのターミナルで、次を確認する。

```bash
git --version
python3 --version
```

Pythonが3.11未満の場合は、Homebrewや公式インストーラーなど、利用者が管理できる方法で3.11以上を用意する。GitHub CLIの`gh`はPR操作に便利だが、プログラムの実行には必須ではない。

### 3.2 クローンと初期検証

```bash
git clone https://github.com/AichiroFunakoshi/keiba-prediction-lab.git
cd keiba-prediction-lab
git switch main
git pull --ff-only origin main
python3 -m venv .venv
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests
```

期待値は、全テスト成功である。テスト数は開発により増えるため、申し送り時点の個数ではなく`OK`を基準にする。

CLIは次のいずれかで実行する。

```bash
export PYTHONPATH=src
python -m keiba_prediction_lab.cli --help
```

または、仮想環境へ編集可能インストールする。

```bash
python -m pip install -e .
keiba-lab --help
```

## 4. GitHubへ置くもの、置かないもの

### GitHubへ置く

- `src/`のプログラム
- `tests/`の合成テスト
- `docs/`、README、データ契約
- 出所と利用条件だけを記録する`data/sources.json`
- 実在レースを含まない、または再配布許諾が明確な最小資料

### GitHubへ置かない

- 実レースのCSV、取得済みHTML、スクレイピング結果
- Cookie、APIキー、契約番号、ログイン情報
- 実データで学習したモデル
- 予測バンドル、払戻表、評価JSON、生成レポート
- 利用条件が不明な外部データや派生データ

ルートの`.gitignore`は`local/`、`outputs/`、`models/`、`artifacts/`、`reports/`を除外する。作業前とコミット前に必ず確認する。

```bash
git status --short
git check-ignore -v local/training.csv outputs/race-1 reports/walk-forward.json
```

## 5. ローカルデータを別Macへ移す場合

GitHubはコードと仕様の同期にだけ使う。利用権を確認したローカルデータが必要なら、利用条件に反しない範囲で、本人管理の暗号化ストレージや外付け媒体を使う。

移送前後にファイルの同一性を確認する。

```bash
shasum -a 256 local/training.csv
shasum -a 256 local/model.json
```

注意事項：

- クラウド同期の許否も提供元の契約条件に従う
- 認証情報をデータファイルと同じ場所へ置かない
- 端末ごとにパスが違っても、ファイル内容のSHA-256が同じなら入力版を再現できる
- 予測済みバンドルはファイル単位で編集せず、ディレクトリ全体を保持する
- 移送後は`audit-prediction-bundle`で予測バンドルを再監査する

## 6. 標準の研究フロー

### 学習・時間順評価

```bash
python -m keiba_prediction_lab.cli audit-training-csv local/training.csv
python -m keiba_prediction_lab.cli prepare-training \
  local/training.csv --output local/training-features.json
python -m keiba_prediction_lab.cli evaluate-walk-forward \
  local/training.csv local/windows.json \
  --report reports/walk-forward.json
python -m keiba_prediction_lab.cli train-model \
  local/training.csv --output local/model.json
```

ウォークフォワードの窓は結果を見る前に決める。評価結果に合わせて窓を動かさない。最低300レースを目安、500レースを推奨とし、それ未満では改善を断定しない。

### 1レースの正式予測

```bash
python -m keiba_prediction_lab.cli audit-race-inputs \
  local/model.json local/history.csv local/targets.csv \
  local/pace-profiles.csv local/pace-scenario.json \
  --frozen-at 2026-02-01T10:05:00+09:00

python -m keiba_prediction_lab.cli predict-race \
  local/model.json local/history.csv local/targets.csv \
  local/pace-profiles.csv local/pace-scenario.json \
  --frozen-at 2026-02-01T10:05:00+09:00 \
  --output outputs/race-1

python -m keiba_prediction_lab.cli audit-prediction-bundle outputs/race-1
python -m keiba_prediction_lab.cli report-prediction-bundle \
  outputs/race-1 --output outputs/race-1-report.md
```

`actual.json`の三連単1点100円だけが実購入候補である。`baseline-shadow.json`、`pace-shadow.json`、`bet-types-shadow.json`は購入しない。正式出力は上書きせず、入力を直した場合は新しい出力ディレクトリと新しい固定時刻で作る。

### レース後評価

発走前ファイルは変更しない。結果、払戻、レース条件は別ファイルとして追加し、複数レースをまとめて評価する。詳細な形式とコマンドは[全6馬券種の事後評価](BET_TYPE_EVALUATION.md)を参照する。

## 7. 開発時のGit・PR運用

作業開始時：

```bash
git switch main
git pull --ff-only origin main
git status --short --branch
git switch -c codex/<短い作業名>
```

変更後：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
python3 -m compileall -q src tests
git diff --check
git status --short
```

変更したファイルだけを明示してコミットし、GitHubへpushしてPRを作る。Python 3.11〜3.13のCIが全て成功してからマージする。CodeRabbitは公開リポジトリの利用条件によりレビューをスキップする場合がある。その場合は、次を第三者視点でセルフレビューし、PR本文へ記録する。

- 発走後情報の混入がないか
- 監査後に別のファイルを読み直していないか
- 実購入候補と影予測を混同していないか
- 既存成果物を上書きしないか
- ハッシュ、時刻、レースID、モデル版の対応が保たれるか
- 集計の分母、購入点数、高配当依存度が隠れていないか

## 8. 次に取り組む順序

### 完了：ウォークフォワード成果物の再読込・監査

保存済み`walk-forward.json`を読み込み、内部ハッシュ・型・集計整合性を再検証するローダーと`audit-walk-forward-report`を実装済みである。UIからも同じ型付き公開APIを使用する。

完了条件：

- JSONキー重複、未知キー、型不正、非有限値、改変を拒否する
- 窓別評価レース数の合計と全体レース数の一致を確認する
- 条件別診断は重複区分を拒否する
- 監査が元ファイルを変更しない

### 優先1：実データ源の承認と取込監査

ユーザーが対象レースを指定しても、取得元の規約確認なしに自動取得を始めない。候補ごとに出所、取得方法、機械学習利用、ローカル保存、派生データ、再配布の可否を確認し、`data/sources.json`へ判断根拠と確認日を記録する。

少なくとも1つの利用可能なデータ源が承認された後にだけ、提供元固有アダプターを追加する。原データはGitHubへ置かず、合成または許諾済み最小fixtureでアダプターをテストする。

取得処理を含まない旧ローカルJSON変換器は実装済みである。`convert-local-history-snapshot`と`convert-local-target-snapshot`は、すでに取得済みのスナップショットを共通CSVへ変換し、入力ハッシュ、取得時刻、代理時刻の仮定、ID方式をmanifestへ固定する。変換器は馬・騎手・調教師に同じ`normalized-name-v2`方式を用い、騎手の減量記号はIDから除外する。旧臨時実行のように履歴で馬名、本番で馬番を使う入力は、正式CSV境界でも識別子ドメイン不一致として拒否する。

この変換器の存在はデータ源の承認を意味しない。取得元固有のネットワーク取得器を公開コードへ追加する作業は、引き続き利用条件の確認後に行う。

### 優先2：実データでの最初の固定評価

承認済みデータから最低300レース、可能なら500レースを時間順に用意し、窓を事前固定する。まず現行条件付きロジットと一様確率を比較する。結果が悪くてもモデルを都合よく変更せず、最初の基準値として保存する。

### 優先3：不足している診断軸

現状のウォークフォワード診断は競馬場、距離帯、頭数、信頼度を持つ。クラス別診断は入力契約にクラスがないため未実装である。実データ源の項目と観測時刻を確認してから、データ契約、特徴量、診断を一体で変更する。

### 優先4：次モデルの比較

LightGBMなどの表形式モデルは、実データの基準評価が得られた後に検討する。導入時は同じ入力、同じウォークフォワード窓、同じ評価レースで現行モデルと対応比較する。最終評価期間をモデル選択に使わない。

### UI並行作業：読み取り専用プロトタイプ

監査済み予測バンドル、開催日マニフェスト、ウォークフォワード成果物、任意のWIN5影予測を`ReadOnlyAppSnapshot`へ変換する型付き境界、IPv4ループバックだけで待ち受ける読み取り専用HTTP API、同APIだけを読む視覚画面は実装済みである。開催日マニフェストを指定すると競馬場別の全レース一覧が入口となり、レース選択で監査済み詳細へ移る。実購入候補1点100円、通常の影予測0円、WIN5影予測0円、勝率順位、評価指標は別領域に固定した。ファイル選択、学習、予測、保存操作はまだ追加しない。

実成果物がない新しいMacでも画面を確認できるよう、`init-ui-demo`は12レース分の合成入力、モデル、監査済み予測バンドル、ウォークフォワード成果物、開催日マニフェストを上書きせず生成する。`serve-ui-demo`は全成果物を再監査し、既定ブラウザで読み取り専用画面を開く。合成デモは`local/`だけに置き、実レースの精度主張や購入判断へ転用しない。実成果物を表示するときは`serve-read-only-api --open-browser`を使う。

macOSのFinderからは、リポジトリ直下の実行可能ファイル`open-ui-demo.command`をダブルクリックして合成デモを起動できる。ランチャーはリポジトリ内の`.venv`だけを使用し、初回デモ生成後に既存の`serve-ui-demo`へ処理を渡す。予測計算、監査、HTTP配信をシェルへ複製しない。

## 9. 現在の既知の限界

- 承認済みの中央競馬実データ源がない
- 実競馬での的中率・確率校正・回収率は未測定
- 自動スクレイピングを提供していない
- 取得済み旧JSONを共通CSVへ変換できるが、取得元固有の取得処理と実データ自体は含まない
- 脚質、序盤速度、終盤速度、ペース耐性、想定ペースは明示入力であり、自動推定ではない
- 現行三連単の実購入候補は基準Plackett–Luce分布の最上位1点で、ペースモデルは影予測のまま
- WIN5は明示された同日5レースの各1着確率1位を束ねる購入額0円の影予測で、対象レースの自動判定とレース間依存の推定は未実装
- クラス別ウォークフォワード診断は未実装
- 300〜500レース規模の実データ検証は未実施

## 10. 再開時の確認チェックリスト

- [ ] `main`を`origin/main`へ同期した
- [ ] Python 3.11以上を使用している
- [ ] 全テストが成功した
- [ ] [README](../README.md)、[ロードマップ](ROADMAP.md)、この申し送りを読んだ
- [ ] 実データと生成物がGit管理から除外されている
- [ ] 使用する外部データの権利と取得方法を確認した
- [ ] 作業ブランチを作成した
- [ ] 変更の完了条件と検証方法を先に決めた
- [ ] PRの全CIとレビュー状態を確認してからマージする

この文書と実装が食い違う場合は、推測で進めず、`main`のコード、テスト、プロジェクト原則、データ利用方針を照合して差異を明示する。
