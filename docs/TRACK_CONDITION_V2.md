# 馬場適性特徴量 v2

`track-condition-v2`は、天候そのものを勝率へ手作業で加点する方式ではない。発走前に観測した馬場状態について、各馬の過去成績を次の組み合わせで集計し、条件付きロジットへ学習させる。

- 芝・ダート・障害を分離した同一馬場状態の勝率
- 同じ条件の3着内率
- 同じ条件を経験した回数

従来版は「馬場状態」だけで集計していたため、例えば芝の重馬場とダートの重馬場を区別できなかった。v2はサーフェスを必ず組み合わせ、経験数の少ない馬は全体事前分布へ平滑化する。雨だから一律に逃げ馬や人気薄を優遇する処理は行わない。

## 固定比較

候補を採用する前に、同じ学習CSVと事前固定した窓で従来版とv2を別々に評価する。

```bash
python -m keiba_prediction_lab.cli evaluate-walk-forward \
  local/training.csv local/windows.json \
  --report reports/legacy-walk-forward.json

python -m keiba_prediction_lab.cli evaluate-walk-forward \
  local/training.csv local/windows.json \
  --track-condition-v2 \
  --report reports/track-condition-v2-walk-forward.json
```

比較では1着的中率だけでなく、Brier score、Log loss、ECEを確認する。対象開催の結果を見てから、その開催を学習・校正・モデル選択へ混ぜてはならない。

## 学習と予測

固定比較を通した場合だけ、v2のモデルを別成果物として学習する。

```bash
python -m keiba_prediction_lab.cli train-model \
  local/training.csv \
  --track-condition-v2 \
  --calibration-races 60 \
  --output local/model-track-condition-v2.json
```

旧モデル成果物は引き続き読み込める。v2モデルはモデル版と特徴量名を別に保存するため、旧版と取り違えない。

## 運用上の限界

v2が使うのは予測時点の馬場状態である。早朝の「良」が発走前に「重」へ変わった場合、早朝入力のままではv2でも補正できない。降雨がある日は`refresh-jra-web-race-day`で最新状態を取得し、未発走レースは新しい入力・固定時刻・出力先で再予測する。発走済みレースの予測ファイルは変更しない。

気温、降水量、風速などの気象値は、同じ定義の履歴が学習期間に揃うまで正式特徴量へ加えない。現在の実装は「結果後に天候を推定して過去予測を書き換える」ことを禁止する。
