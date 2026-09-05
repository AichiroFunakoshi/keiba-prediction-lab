# Mac間継続手順

この手順は、コードをGitHubで同期しながら、実レースデータと生成成果物を各Macのローカル領域に保つための短い申し送りである。判断基準は[プロジェクト申し送り](HANDOFF.md)、[プロジェクト原則](PROJECT_PRINCIPLES.md)、[ローカル成果物復旧](LOCAL_RESULT_RECOVERY.md)を優先する。

## 移動元のMac

1. `git status --short --branch`で、未コミットのコード変更とローカル生成物を区別する。
2. 実データ、学習済みモデル、予測、結果、レポートが`local/`、`outputs/`、`models/`、`artifacts/`、`reports/`などGit除外済み領域にあることを確認する。
3. GitHubへ送るのは汎用コード、合成テスト、契約、手順だけとする。
4. ローカル成果物を本人管理の媒体へ移す必要がある場合は、提供元の利用条件を確認し、ディレクトリ構造を保ったまま暗号化して移送する。認証情報を同梱しない。
5. 移送対象のSHA-256を移動前後で照合する。予測バンドルや開催日成果物の一部だけを編集・改名しない。

## 移動先のMac

```bash
git clone https://github.com/AichiroFunakoshi/keiba-prediction-lab.git
cd keiba-prediction-lab
git switch main
git pull --ff-only origin main
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[desktop]'
python -m unittest discover -s tests
./build-raceweave-app.command
```

全テストが`OK`になり、`dist/RaceWeave.app`のad-hoc署名検証が成功することを確認する。生成された`.app`はそのMac専用のローカル成果物で、GitHubへ追加しない。

過去予測や結果を扱う前に、次を実行する。

```bash
keiba-lab local-artifact-status
```

限定された探索範囲で見つかった候補だけを読み取り専用で扱う。6ファイルの正式バンドル、開催日来歴、対応する払戻が監査できないレースを正式評価へ混ぜず、結果から予測を再構成しない。

## RaceWeaveで開く

Finderから`dist/RaceWeave.app`を開く。同じチェックアウトの`local/`に監査済み開催日があれば、開催日と固定時刻が最も新しいものを自動表示する。見つからない場合だけmacOS標準画面で開催日出力の`race-day.json`を選ぶ。アプリは同じディレクトリの`race-day-provenance.json`と全予測バンドルを再監査し、成功した場合だけ表示する。自動検出できず選択もキャンセルすると合成デモが開く。

アプリは読み取り専用であり、学習、予測計算、結果取得、ファイル保存、馬券購入を行わない。選択パスも保持しない。別Macで同じ開催日を開く場合も、移送後にアプリまたは`keiba-lab audit-race-day`で再監査する。

## 次の開発判断

- 個別開催の診断だけで係数、特徴量、閾値を自動更新しない。
- 改善候補は、今後の評価期間を含まない固定ウォークフォワードで現行モデルと比較する。
- 発走直前の正式固定では`--require-complete-body-weight`を指定する。夜間の研究予測とは出力先と固定時刻を分ける。
- 実購入候補は三連単1点100円だけとし、3・5・10点と他券種は購入額0円の影予測として維持する。
- 新しいデータ源や外部配布は、利用条件、Developer ID署名、公証、依存ライセンスを確認してから別PRで進める。
