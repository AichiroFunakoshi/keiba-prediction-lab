# ローカル実データ取込アダプター

提供元ごとの取得処理と予測コードを分離するため、ローカルCSVから正式な`FeatureRow`と`TrainingRow`を生成する。

## 旧ローカルJSONの変換

以前の臨時運用で保存した履歴JSONと出馬表JSONは、取得処理を再実行せず共通CSVへ変換できる。

```bash
PYTHONPATH=src python -m keiba_prediction_lab.cli convert-local-history-snapshot \
  local/raw/history.json \
  --source-id local-private-snapshot \
  --acquired-at 2026-08-23T08:30:00+09:00 \
  --output local/converted-history
```

変換結果には`history.csv`、`training.csv`、`snapshot-manifest.json`を含む。manifestには元ファイルと出力のSHA-256、取得時刻、通信を行っていないこと、代理の観測・結果判明時刻を記録する。既定の結果判明時刻は発走20分後とするが、実際の確定時刻がある場合は`--result-delay-minutes`による一律代理値ではなく、将来の入力契約で実測値を保持する。既存の出力ディレクトリは変更しない。

馬、騎手、調教師のIDはUnicode NFKC正規化と空白正規化を行った名前から、`horse:name:...`、`jockey:name:...`、`trainer:name:...`として生成する。騎手名先頭の減量記号（`▲△☆★◇`）は時期によって変わり得るためIDから除外する。履歴側に名前、本番側に馬番を使うと、過去成績特徴量が全て事前率へ戻るため禁止する。正式CSVの取込でも、明白な数値ID対文字IDまたは名前空間の不一致を拒否する。

予測対象カードは、結果情報を含まない旧JSONと、別途確認した馬場状態JSONから1レース1CSVへ変換する。

```bash
PYTHONPATH=src python -m keiba_prediction_lab.cli convert-local-target-snapshot \
  local/raw/cards.json local/raw/track-conditions.json \
  --source-id local-private-snapshot \
  --acquired-at 2026-08-23T08:30:00+09:00 \
  --race-date 2026-08-23 \
  --observed-at 2026-08-23T08:30:00+09:00 \
  --output local/converted-targets
```

この変換器はローカルファイルだけを読み、外部サイトへアクセスしない。取得方法や利用権の確認を代替せず、元JSON・変換CSV・manifestはいずれもGit管理しない。

変換された複数の`targets/*.csv`は、各レースの脚質CSVと想定ペースJSONを用意し、開催日計画へ列挙して`predict-race-day`へ渡せる。計画は日付、競馬場、レース番号と3入力パスを明示し、プログラムはディレクトリから対象レースを推測しない。全レースが同じモデル、履歴、発走前固定時刻、予測段階で検証できた場合だけ、UI用開催日マニフェストを含む出力を保存する。詳細は[予測パイプライン](PIPELINE.md)を参照する。

## ファイルの分離

- `history.csv`: 対象レースより前に結果が判明した過去走だけを格納する
- `targets.csv`: 予測対象の発走前情報だけを格納する

`targets.csv`へ着順、払戻、確定オッズなどの列を追加すると取込を拒否する。未知の列も黙って無視しない。`history.csv`の`result_known_at`が対象の`observed_at`より後なら、特徴量生成全体を停止する。

両ファイルはローカル専用であり、利用条件が明確に許可しない限りGitHubへコミットしない。

## 実行

```bash
PYTHONPATH=src python -m keiba_prediction_lab.cli audit-training-csv \
  local/training.csv

PYTHONPATH=src python -m keiba_prediction_lab.cli prepare-features \
  local/history.csv local/targets.csv --output local/features.json

PYTHONPATH=src python -m keiba_prediction_lab.cli prepare-training \
  local/training.csv --output local/training.json
```

出力には入力2ファイルのSHA-256、両ハッシュから決定的に生成した`input_data_version`、対象レースID、発走・観測時刻、モデルへ渡す特徴量を記録する。既存の出力ファイルは上書きしない。

空の雛形一式は`init-input-templates --output local/race-inputs`で生成できる。雛形は意図的に未完成であり、`_REPLACE_`表示を残したまま正式予測には使用できない。生成先には、外部データの誤コミットを避ける`.gitignore`と入力ガイドも含める。既存ディレクトリは変更しない。

## 必須列

日時はタイムゾーン付きISO 8601、`surface`は`turf`、`dirt`、`jump`のいずれかとする。空欄を許すのは`body_weight_kg`だけである。

履歴CSVには、レースID、発走・結果判明時刻、馬・騎手・調教師ID、競馬場、コース種別、馬場、距離、馬番、斤量、馬体重、着順を含める。

対象CSVには、レースID、発走・観測時刻、馬・騎手・調教師ID、競馬場、コース種別、馬場、距離、馬番、斤量、馬体重を含める。結果列は含めない。

## 時点安全な学習行

通常の`training.csv`は履歴CSVの全列に、各レースの発走前観測時刻`observed_at`を加える。この時刻は推測せず、実際にその入力情報を固定した時刻を明示する。例外として旧JSON変換器は、実測時刻がないことをmanifestへ明記したうえで代理時刻を生成する。この出力は回顧的な基準評価に限り、発走前固定の運用実績とは扱わない。各レースは全頭で発走・観測・結果判明時刻とレース条件を共有し、2頭以上、重複しない馬ID・馬番、1着馬を含む必要がある。

各レースの特徴量には、その`observed_at`以前に全頭分の結果が判明した別レースだけを使う。結果判明が遅れたレースは一部の行だけを採用せず、全体を利用可能になるまで除外する。自己レースの着順は特徴量生成後に教師ラベルとして結合する。入力順は学習行の内容に影響しないが、入力ファイル自体のSHA-256は監査用にそのまま記録する。出力JSONは既存ファイルを上書きしない。
