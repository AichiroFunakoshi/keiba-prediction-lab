# RaceWeave macOSアプリ

## 位置づけ

RaceWeaveは、既存の監査済み予測UIをmacOS専用ウインドウで表示するローカルアプリである。PWA、クラウドサービス、馬券購入アプリではない。Pythonの予測・監査ロジックをUIへ複製せず、従来と同じ`ReadOnlyAppSnapshot`とIPv4ループバックAPIを使う。

初期版はUI-1の読み取り専用機能を`.app`へ包装する。Finderからの起動では合成デモを表示する。実データのファイル選択、学習、予測実行、保存はUI-2以降で追加し、それまでは既存CLIで生成・監査した成果物だけを明示指定する。

## アプリ名とアイコン

- 表示名：`RaceWeave`
- 読み：レースウィーヴ
- Bundle ID：`jp.aichiro.raceweave`
- 意味：1着確率を起点に、異なる2着・3着の展開を織り上げる
- アイコン原版：`assets/macos/RaceWeave.png`

アイコンは本プロジェクト用に生成した独自画像であり、JRAその他の団体ロゴ、公式画像、実データを含まない。

## 初回ビルド

macOSとPython 3.11以上を使用する。同じリポジトリの`.venv`へデスクトップ版だけの依存を追加する。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[desktop]'
./build-raceweave-app.command
```

成功すると`dist/RaceWeave.app`が作られる。`dist/`、`build/`、PyInstallerの`.spec`はGit管理対象外であり、生成したアプリへローカルの実レースデータを同梱しない。

`build-raceweave-app.command`は、原版PNGをアプリアイコンへ変換し、WebKitウインドウ、Python実行環境、既存パッケージ、静的UIをひとつの`.app`へ包装する。iCloud等のFinderメタデータが署名へ混ざらないよう一時領域で組み立て、拡張属性を除去してad-hoc署名を検証してから`dist/`へ複製する。ビルド時にネットからデータを取得せず、必要なPythonパッケージが未導入なら停止する。

## 起動

Finderで`dist/RaceWeave.app`を開く。ブラウザとTerminalを操作する必要はない。初回だけ、次のGit管理外領域へ合成デモを作る。

```text
~/Library/Application Support/RaceWeave/ui-demo-v1
```

2回目以降は既存デモを変更せず、監査してから表示する。このデモは画面確認専用で、実レース予測や精度主張には使用できない。

開発中に監査済み実成果物を専用ウインドウで開く場合は、リポジトリの仮想環境から次のように起動する。

```bash
raceweave \
  --race-day-manifest outputs/2026-08-30/race-day.json \
  --walk-forward-report reports/walk-forward.json \
  --win5-forecast outputs/win5-2026-08-30.json
```

1レースだけなら`--prediction-bundle`を指定できる。`--demo-directory`と実成果物指定は同時に使えない。

## ローカル境界

- HTTP待受先は`127.0.0.1`だけで、空いている一時ポートを使用する
- 専用WebKitウインドウを閉じるとローカルHTTPサーバーも終了する
- テレメトリー、クラウドDB、外部アカウント、馬券購入機能を持たない
- 監査済みスナップショットだけを表示し、元成果物を変更しない
- 実データ、モデル、予測、結果、レポートをGitHubへ追加しない
- APIキー、Apple署名証明書、プロビジョニング情報をリポジトリへ追加しない

## 配布範囲

当面は、所有者本人が同じMac上でビルドして使うad-hoc署名の個人利用版とする。Apple Developer IDによる配布用署名と公証はまだ行わない。GitHubではソース、合成テスト、アイコン原版、ビルド手順だけを公開する。他者へバイナリ配布する段階で、Developer ID署名、公証、依存ライセンス一覧、配布物に実データがないことを別途確認する。
