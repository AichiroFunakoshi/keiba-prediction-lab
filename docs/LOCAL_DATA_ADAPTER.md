# ローカル実データ取込アダプター

提供元ごとの取得処理と予測コードを分離するため、ローカルに用意した2つのCSVから正式な`FeatureRow`を生成する。

## ファイルの分離

- `history.csv`: 対象レースより前に結果が判明した過去走だけを格納する
- `targets.csv`: 予測対象の発走前情報だけを格納する

`targets.csv`へ着順、払戻、確定オッズなどの列を追加すると取込を拒否する。未知の列も黙って無視しない。`history.csv`の`result_known_at`が対象の`observed_at`より後なら、特徴量生成全体を停止する。

両ファイルはローカル専用であり、利用条件が明確に許可しない限りGitHubへコミットしない。

## 実行

```bash
PYTHONPATH=src python -m keiba_prediction_lab.cli prepare-features \
  local/history.csv local/targets.csv --output local/features.json
```

出力には入力2ファイルのSHA-256、両ハッシュから決定的に生成した`input_data_version`、モデルへ渡す特徴量を記録する。既存の出力ファイルは上書きしない。

## 必須列

日時はタイムゾーン付きISO 8601、`surface`は`turf`、`dirt`、`jump`のいずれかとする。空欄を許すのは`body_weight_kg`だけである。

履歴CSVには、レースID、発走・結果判明時刻、馬・騎手・調教師ID、競馬場、コース種別、馬場、距離、馬番、斤量、馬体重、着順を含める。

対象CSVには、レースID、発走・観測時刻、馬・騎手・調教師ID、競馬場、コース種別、馬場、距離、馬番、斤量、馬体重を含める。結果列は含めない。
