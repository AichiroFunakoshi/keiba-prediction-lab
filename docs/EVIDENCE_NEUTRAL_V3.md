# 履歴件数を直接加点しない候補モデル

`--evidence-neutral-v3` は馬場対応v2をベースに、保存履歴の件数を能力の直接加点から外す比較用モデルである。取得できた記録数はキャリア全体の経験数とは限らないため、その多さだけで順位が上がる影響を検証する。

外す項目は馬・騎手・調教師・馬と同じ芝／ダート等および馬場状態の履歴件数の対数。騎手と調教師の勝率、馬の各条件別勝率と3着内率は維持する。件数は従来どおり勝率等の平滑化に使い、少数記録の率を過信しないようにする。件数を外すこと自体が性能改善を保証するわけではない。

```sh
python -m keiba_prediction_lab.cli evaluate-walk-forward \
  local/training.csv local/fixed-windows.json --evidence-neutral-v3 \
  --report reports/evidence-neutral-evaluation.json
python -m keiba_prediction_lab.cli train-model local/training.csv \
  --evidence-neutral-v3 --calibration-races 60 --output local/candidate-model.json
```

このフラグと `--track-condition-v2` は同時指定できない。既定モデルは変更しない。モデル版は `conditional-logit-evidence-neutral-v3`、確率校正ありは `-temperature-v1` 付き、モデル成果物スキーマは1.3。読み込みは旧スキーマ1.0～1.2との互換を保つ。モデル版・15要素の特徴量・係数・学習フラグの不一致は拒否する。

## 採否の判断

同一入力・同一固定期間でv2と比較し、1着一致と確率誤差を併記する。的中が増えても確率誤差や校正が悪化すれば、確率から作る三連単・WIN5等も含めて基準への採用を保留する。固定期間の結果を見て何度も候補を変更する場合は探索回数を記録し、未使用期間を別に残す。

個別開催は仮説の材料に限定する。特徴量のスコア差はモデル内部の評価差であり、現実の敗因の因果推定ではない。履歴がないことを未出走、弱さ、不適性と断定しない。直近着順、対戦クラス、ペース、馬体重変化、騎手と馬の組合せなどの追加候補は、発走前時刻と取得範囲を監査できるデータが揃ってから検証する。

実レースの診断・評価結果とモデル本体はローカル専用。既存の事前予測を新モデルで置換せず、別成果物として保存する。
