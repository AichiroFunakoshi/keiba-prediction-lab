# 別のMacに残る発走前予測と結果の復旧手順

## 目的

GitHubに置かない方針の実レース成果物が別のMacに残っている場合、その端末でGitHubの最新版へ同期した後、発走前予測と結果を安全に発見し、改変せず再監査して精度診断へ利用する。

GitHubはコードと仕様の正である。ローカル成果物は評価の入力であり、GitHubへ取り込む対象ではない。

## 自動探索の順序

最新版へ同期した直後は、まず次の読み取り専用コマンドを実行する。

```bash
keiba-lab local-artifact-status
```

保存場所が分かっている場合は探索範囲を明示できる。`--root`は複数回指定できる。

```bash
keiba-lab local-artifact-status --root /path/to/keiba-project
```

出力の`status`が`ready_for_evaluation`なら、監査済み予測と整合する払戻表が1件以上ある。`predictions_found`は正式予測のみ、`invalid_candidates_only`は候補が監査不合格、`no_candidates`は限定範囲内に候補がないことを表す。このコマンドは探索と監査だけを行い、ファイルの作成、修復、移動、Git追加、評価を行わない。

既定コマンドは、次の順で読み取り専用確認を行う。

1. 現在のリポジトリの`local/`、`outputs/`、`reports/`、`artifacts/`、`models/`
2. 現在のリポジトリの親にある、名前に`keiba`、`競馬`、`racing`を含むフォルダ
3. `$HOME/keiba-prediction-lab`と`$HOME/競馬`
4. `$HOME/Documents`と`$HOME/Desktop`直下の競馬プロジェクト候補

探索対象は、予測バンドルを識別する次のファイル名に限定する。

- `manifest.json`
- `actual.json`
- `baseline-shadow.json`
- `pace-shadow.json`
- `bet-types-shadow.json`
- `input-provenance.json`
- `bet-types-payouts.json`
- `race-context.json`
- `walk-forward.json`

候補保存場所が分かっている場合は、その場所を最優先する。Mac全体を無差別に走査せず、競馬プロジェクトと無関係な個人領域は読まない。

## 正式予測として採用する条件

同じディレクトリに必須6ファイルが揃っているだけでは正式予測と断定しない。最新版コードで次を実行し、`is_valid: true`となったものだけを採用する。

```bash
PYTHONPATH=src python3 -m keiba_prediction_lab.cli \
  audit-prediction-bundle /absolute/path/to/race-directory
```

監査は、内部ハッシュ、マニフェスト、レースID、時刻、モデル版、入力データ版、実購入1点100円、影予測0円の対応を確認する。失敗した候補は修復や編集をせず、パスと失敗理由をローカル作業記録へ残す。

## 結果と対応付ける

`bet-types-payouts.json`が同じレースディレクトリに存在する場合、予測バンドルとレースIDを照合する。複数のコピーが見つかった場合は、次をすべて比較する。

- レースID
- 発走予定時刻
- 予測固定時刻
- モデル版
- 入力データ版
- ファイルSHA-256

一致しないコピーを一つの評価へ混ぜない。結果ファイルだけが存在し、対応する発走前予測バンドルがない場合、そのレースは過去予測の精度評価へ使用しない。

## 発見後に自動実行する処理

監査済み予測と結果が揃ったレースディレクトリをまとめて指定する。

```bash
mkdir -p reports
PYTHONPATH=src python3 -m keiba_prediction_lab.cli \
  diagnose-winner-misses \
  /absolute/path/to/race-1 \
  /absolute/path/to/race-2 \
  --format markdown \
  > reports/winner-misses-recovered.md
```

同じレース群について、全券種の評価も実行する。

```bash
PYTHONPATH=src python3 -m keiba_prediction_lab.cli \
  evaluate-bet-types \
  /absolute/path/to/race-1 \
  /absolute/path/to/race-2 \
  --report reports/bet-types-recovered.json
```

出力先に同名ファイルがある場合は上書きせず、日付または一意な識別子を付けた別名を使う。

## 診断後の判断

診断では、1着的中率、実勝馬の予測順位、上位2・3頭カバー率、1位と2位の確率差、高信頼の外れを確認する。詳細は[1着予測精度を改善するための実行基準](ACCURACY_IMPROVEMENT.md)に従う。

直近開催だけの結果は、次モデルの仮説生成には使えるが、改善の証明には使わない。モデル候補は別の固定ウォークフォワード期間で比較し、今週末など将来の開催を最終評価として残す。

## 禁止事項

- 発見した実データや生成物をGitへ追加する
- ローカル絶対パスや個人情報をPR本文へ記載する
- 監査を通すために予測ファイルやハッシュを書き換える
- 結果を見て発走前予測を再構成する
- 複数Macの不一致ファイルを都合よく組み合わせる
- 個別開催だけを根拠に正式モデルを自動更新する
