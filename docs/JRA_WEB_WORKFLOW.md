# JRA公式公開ページを使う無料ローカル運用

## 前提

この経路は、利用者本人のMac内での私的な分析に限る実験機能であり、プロジェクトとして利用許諾を保証しない。JRAの公開ページはオープンデータではなく、`robots.txt`に拒否指定がないことも自動取得や二次利用の許諾を意味しない。利用者は実行前に最新の利用条件を確認し、判断できない場合は使用しない。取得したHTML由来JSON、CSV、モデル、予測成果物をGitHubや第三者へ再配布しない。

参照：

- <https://www.jra.go.jp/use/>
- <https://www.jra.go.jp/robots.txt>

## 初期更新

新しい依存関係を仮想環境へ反映する。

```bash
cd /path/to/keiba-prediction-lab
source .venv/bin/activate
python -m pip install -e .
```

## 当日データと過去結果の取得

開催日の朝、最初のレースより十分前に実行する。次の例は2026年8月30日である。

```bash
keiba-lab fetch-jra-web \
  --race-date 2026-08-30 \
  --max-history-races 360 \
  --delay-seconds 1.0 \
  --accept-private-use-terms \
  --output local/jra-web/20260830-raw
```

取得器は、開催選択ページ、各競馬場のレース選択、全出馬表、現在の芝・ダート馬場状態、各出走馬の出馬表に掲載された過去結果リンクをたどる。対象過去結果は日付の新しい順で上限360レースとする。既定で各要求間を1秒以上空ける。

`acquisition-manifest.json`には全URL、HTTP方式、取得時刻、応答サイズ、SHA-256を保存する。途中で1件でも取得・解析に失敗した場合、出力ディレクトリ全体を残さない。

## 正式入力への変換

### 発走前の当日更新

夜間に取得した履歴を再利用し、最初のレース前に当日の出馬表、馬体重、単勝オッズ、取消、馬場だけを再取得する。履歴は元スナップショットからバイト単位でコピーされ、元マニフェストのSHA-256も保存される。

```bash
keiba-lab refresh-jra-web-race-day \
  local/jra-web/20260830-raw \
  --delay-seconds 1.0 \
  --accept-private-use-terms \
  --output local/jra-web/20260830-morning-raw
```

更新後は夜間版ではなく`20260830-morning-raw`を正式入力へ変換する。レース集合が元スナップショットと一致しない場合、更新全体を保存せず停止する。

```bash
keiba-lab prepare-jra-web-race-day \
  local/jra-web/20260830-morning-raw \
  --output local/jra-web/20260830-morning-prepared
```

次を一括生成する。

- `history/history.csv`
- `history/training.csv`
- `targets/targets/*.csv`
- `pace-history.csv`
- レース別`pace-profiles.csv`
- レース別`pace-scenario.json`
- `race-day-plan.json`

脚質と想定ペースは過去の最初・最後のコーナー順位、着順、上がり3F順位から既存の時間安全な推定器で作る。履歴がない馬は平均へ縮約する。

## 学習・予測

```bash
keiba-lab audit-training-csv local/jra-web/20260830-morning-prepared/history/training.csv

keiba-lab train-model \
  local/jra-web/20260830-morning-prepared/history/training.csv \
  --output local/jra-web/20260830-model.json
```

`prepared-manifest.json`の`observed_at`を、発走前固定時刻と一致させて予測する。

```bash
keiba-lab predict-race-day \
  local/jra-web/20260830-model.json \
  local/jra-web/20260830-morning-prepared/history/history.csv \
  local/jra-web/20260830-morning-prepared/race-day-plan.json \
  --frozen-at 'prepared-manifest.jsonに記録されたobserved_at' \
  --require-complete-body-weight \
  --output local/jra-web/20260830-predictions
```

`--require-complete-body-weight`は発走直前の正式固定用である。1頭でも当日馬体重が欠ける場合は開催日全体を保存前に拒否する。馬体重発表前の夜間研究予測では省略し、直前更新後の固定時に指定する。

独立予想を固定した後、オッズをモデル入力へ混ぜずに市場乖離だけを別途監査できる。詳細は[市場乖離ガード](MARKET_GUARD.md)を参照する。

取得完了時刻が対象レースの発走後なら、当該開催日全体の発走前固定は成立しないため変換を拒否する。

## 既知の限界

- JRAのHTML構造変更に影響される。必要項目が見つからない場合は停止する
- 出馬表に掲載される過去走を起点とするため、JRA-VANの全履歴取得と同等ではない
- 360レース取得には通信状況により時間がかかる。2回目以降の差分キャッシュは未実装
- 同日更新は履歴を再利用するが、別開催日の履歴を横断蓄積する機能は未実装
- 現行17特徴量モデルとペース係数の実競馬での有効性は未検証
- オッズは取得スナップショットへ保存するが、現行の独立予想モデル入力には使用しない
- 市場乖離ガードは研究用影成果物であり、既定閾値の改善効果は未検証
