# 保存済み予測によるオッズ重みの検証

`review-market-weights` は発走前に保存した市場併用予測と公式結果を比較する。最終オッズで過去の予測を再構成しない。実データ、レビュー成果物、生成モデルは Git 除外済みのローカル領域に保存する。

入力は `forecast`、`results`、`result_manifest` の3キーを持つ項目の配列。各パスは入力JSONの親からの相対パスで、予測は監査可能な市場併用成果物、結果はJRA取得器の結果JSON、予測の隣にハッシュの一致する `race-day.json` と監査済み各予測バンドルが必要。結果マニフェストには `results_sha256` とタイムゾーン付き `acquired_at` を必要とする。

```sh
python -m keiba_prediction_lab.cli review-market-weights local/review-input.json \
  --selection-end 2099-01-01 --output reports/weight-review.json
python -m keiba_prediction_lab.cli build-reviewed-market-blend \
  local/future/race-day.json local/future/cards.json reports/weight-review.json \
  --output local/future/market-blend.json
```

上記の日付・パスは例。選定終了日以前の結果が確認期間の発走より前に取得済みでなければ拒否する。日付境界は日本時間。候補は市場重み0、20、35、50、65、80、100%で、前半の勝馬対数損失が最小のものを選ぶ。0と100%は比較対照。後半の成績で候補を選び直さない。

採用には選定300レース以上、確認100レース以上、同一元モデル版と学習済みモデルのハッシュ、確認期間の対数損失改善と多クラスBrier非悪化が必要。条件未達は35%を保持する。この標本数は運用上の下限であり、統計的優位を保証しない。日付窓や候補を結果を見て繰り返し変更すれば選択バイアスが生じるため、今後の未使用開催でも前向きに確認する。

レビューは内包する行から再計算できるハッシュ付き成果物で、上書き保存しない。評価対象の発走・結果取得以前に遡って予測へ適用できない。採用可能なレビューを別版モデルに適用することも拒否する。従来の `build-market-blend` の既定値は変更しない。

## 実天気と払戻

```sh
python -m keiba_prediction_lab.cli import-result-observation local/result.html \
  --source-url 'https://www.jra.go.jp/JRADB/accessS.html?CNAME=...' \
  --acquired-at '2099-01-02T18:00:00+09:00' --output local/result-observation.json
```

保存済み結果HTMLから天候と芝・ダート別馬場を抽出する。発走後に取得したことを確認し、`post_event_diagnostic_only` を付ける。これは予報ではなく事後情報なので、同日の発走前入力に流用しない。ライブラリの `parse_result_payouts` は公式HTMLの明記された6券種払戻を解析し、不完全な表を拒否する。

近隣観測所を使う場合は競馬場実測と区別し、観測時刻、取得時刻、出典、元HTMLのハッシュ、欠測を保存する。実測の追加だけで当日の予測を作り直したり、1日分の結果に合わせて天気係数を決めたりしない。
