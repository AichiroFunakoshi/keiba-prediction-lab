# Keiba Prediction Lab

中央競馬の着順確率を、再現可能な手順で予測・検証するためのオープンソース研究プロジェクトです。

目的は利益最大化ではありません。各馬の勝率・3着内率・着順を推定し、その精度を継続的に改善します。馬券収支は予測の性質を確認する補助指標として扱い、購入額はすべての買い目で1点100円に固定します。

## 原則

- オッズを見る前の予想を時刻付きで固定する
- 予測時点で利用できない情報を特徴量に入れない
- 学習・検証・最終評価を時間順に分離する
- 的中率、確率校正、回収率を別々に評価する
- 回収率とともに高配当への依存度を表示する
- 予想モデルと買い目生成を分離する
- 有料データや利用条件の不明なデータを前提にしない

詳しい判断基準は [docs/PROJECT_PRINCIPLES.md](docs/PROJECT_PRINCIPLES.md) を参照してください。
開発段階と完了条件は [docs/ROADMAP.md](docs/ROADMAP.md) を参照してください。

## 現在の段階

ロードマップの段階1、3、4、8を完了し、段階2の実データ源監査、段階5の初期モデル、段階6の馬券種別評価、段階7のウォークフォワード検証を進めています。実購入候補は三連単1点100円に限定し、三連単の1・3・5・10点と全6馬券種の確率表・最上位候補は購入しない影の評価として発走前に別保存します。実データ源は未承認のため、テストには合成データだけを使用します。

## 開発環境

- Python 3.11以上
- unittest（Python標準ライブラリ）

```bash
python -m venv .venv
source .venv/bin/activate
python -m unittest discover -s tests
```

データ候補の判定と、ローカルCSVの品質を確認できます。

```bash
PYTHONPATH=src python -m keiba_prediction_lab.cli list-sources
PYTHONPATH=src python -m keiba_prediction_lab.cli audit-csv tests/fixtures/synthetic_race_results.csv
PYTHONPATH=src python -m keiba_prediction_lab.cli prepare-features local/history.csv local/targets.csv --output local/features.json
PYTHONPATH=src python -m keiba_prediction_lab.cli prepare-training local/training.csv --output local/training.json
PYTHONPATH=src python -m keiba_prediction_lab.cli train-model local/training.csv --output local/model.json
PYTHONPATH=src python -m keiba_prediction_lab.cli predict-race local/model.json local/history.csv local/targets.csv local/pace-profiles.csv local/pace-scenario.json --frozen-at 2026-02-01T10:05:00+09:00 --output outputs/race-1
PYTHONPATH=src python -m keiba_prediction_lab.cli init-input-templates --output local/race-inputs
PYTHONPATH=src python -m keiba_prediction_lab.cli audit-race-inputs local/model.json local/history.csv local/targets.csv local/pace-profiles.csv local/pace-scenario.json --frozen-at 2026-02-01T10:05:00+09:00
PYTHONPATH=src python -m keiba_prediction_lab.cli audit-prediction-bundle outputs/race-1
PYTHONPATH=src python -m keiba_prediction_lab.cli evaluate-bet-types outputs/race-1 outputs/race-2 --report reports/bet-types-evaluation.json
PYTHONPATH=src python -m keiba_prediction_lab.cli compare-bet-type-reports reports/baseline.json reports/candidate.json
PYTHONPATH=src python -m keiba_prediction_lab.cli bootstrap-bet-type-reports reports/baseline.json reports/candidate.json --samples 10000 --seed 0
PYTHONPATH=src python -m keiba_prediction_lab.cli bootstrap-bet-type-reports reports/baseline.json reports/candidate.json --samples 10000 --seed 0 --resampling-unit race-date
PYTHONPATH=src python -m keiba_prediction_lab.cli diagnose-bet-type-reports reports/baseline.json reports/candidate.json --top-races 5
PYTHONPATH=src python -m keiba_prediction_lab.cli diagnose-bet-type-segments reports/baseline.json reports/candidate.json
```

CSV監査は内容を外部送信せず、SHA-256、行数、欠損、重複、日付・着順の異常をJSONで出力します。`evaluate-bet-types` は各ディレクトリの事前固定予測、払戻表、`race-context.json`を検証し、全6馬券種を混ぜずにMarkdownで一括評価します。`--report` を指定すると、3入力のSHA-256、開催日、レース条件、構造化集計、レース別決済台帳を上書き不可のJSONにも保存します。`compare-bet-type-reports` は同一レース・同一払戻・同一条件を確認してから基準モデルとの差を表示し、`bootstrap-bet-type-reports` はレース単位または開催日単位の対応95%区間を固定シードで推定します。`diagnose-bet-type-reports` は的中と払戻差を開催日・レース・券種別に、`diagnose-bet-type-segments`は競馬場・コース種別・馬場・距離帯・クラス・頭数別に分解します。

## 公開データに関する方針

リポジトリには、コード、仕様、テスト、出所と再配布条件を確認できるサンプルだけを含めます。取得元の利用条件が不明なレースデータ、スクレイピング結果、認証情報、学習済みモデルはコミットしません。

実際のレースデータは、利用者が提供元の最新の利用規約・ライセンス・指定された取得方法を確認し、正当に利用できるものだけを自分のローカル環境へ配置してください。本プロジェクトのMIT Licenseは、外部データの取得・加工・再配布権を付与しません。また、「自己責任」という表示だけで禁止された取得や利用が許されるものではありません。

詳しくは [docs/DATA_USAGE_POLICY.md](docs/DATA_USAGE_POLICY.md) を参照してください。

比較用モデルの定義は [docs/BASELINES.md](docs/BASELINES.md) を参照してください。
特徴量と基準時刻の定義は [docs/FEATURES.md](docs/FEATURES.md) を参照してください。
初期確率モデルの定義は [docs/MODEL.md](docs/MODEL.md) を参照してください。
時系列検証の定義は [docs/WALK_FORWARD.md](docs/WALK_FORWARD.md) を参照してください。
予想固定とレポートの定義は [docs/FROZEN_PREDICTIONS.md](docs/FROZEN_PREDICTIONS.md) を参照してください。
三連単の条件付き確率と影の評価は [docs/TRIFECTA_PORTFOLIOS.md](docs/TRIFECTA_PORTFOLIOS.md) を参照してください。
脚質と想定ペースを使う第2基準線は [docs/PACE_MODEL.md](docs/PACE_MODEL.md) を参照してください。
三連単生成モデルの公平な対応比較は [docs/MODEL_COMPARISON.md](docs/MODEL_COMPARISON.md) を参照してください。
1レース分の実購入候補と影予測を一括固定する流れは [docs/PIPELINE.md](docs/PIPELINE.md) を参照してください。
全6馬券種の固定100円評価は [docs/BET_TYPE_EVALUATION.md](docs/BET_TYPE_EVALUATION.md) を参照してください。
全6馬券種の確率表と事前固定候補は [docs/BET_TYPE_SHADOW_FORECASTS.md](docs/BET_TYPE_SHADOW_FORECASTS.md) を参照してください。
権利確認済みのローカルデータを特徴量・時点安全な学習行へ変換する契約は [docs/LOCAL_DATA_ADAPTER.md](docs/LOCAL_DATA_ADAPTER.md) を参照してください。

## ライセンス

プログラムコードはMIT Licenseです。外部データにはこのライセンスは適用されず、それぞれの提供元の条件に従います。
