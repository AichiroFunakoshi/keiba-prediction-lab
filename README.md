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

ロードマップの段階1、3、4を完了し、段階2の実データ源監査を継続しています。無料データ候補の監査台帳、CSV品質検査、未来情報ゲート、3つの比較用ベースライン、観測時刻以前の情報だけを使う特徴量生成器を収録しています。実データ源は未承認のため、テストには合成データだけを使用します。

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
```

CSV監査は内容を外部送信せず、SHA-256、行数、欠損、重複、日付・着順の異常をJSONで出力します。

## 公開データに関する方針

リポジトリには、コード、仕様、テスト、出所と再配布条件を確認できるサンプルだけを含めます。取得元の利用条件が不明なレースデータ、スクレイピング結果、認証情報、学習済みモデルはコミットしません。

実際のレースデータは、利用者が提供元の最新の利用規約・ライセンス・指定された取得方法を確認し、正当に利用できるものだけを自分のローカル環境へ配置してください。本プロジェクトのMIT Licenseは、外部データの取得・加工・再配布権を付与しません。また、「自己責任」という表示だけで禁止された取得や利用が許されるものではありません。

詳しくは [docs/DATA_USAGE_POLICY.md](docs/DATA_USAGE_POLICY.md) を参照してください。

比較用モデルの定義は [docs/BASELINES.md](docs/BASELINES.md) を参照してください。
特徴量と基準時刻の定義は [docs/FEATURES.md](docs/FEATURES.md) を参照してください。

## ライセンス

プログラムコードはMIT Licenseです。外部データにはこのライセンスは適用されず、それぞれの提供元の条件に従います。
