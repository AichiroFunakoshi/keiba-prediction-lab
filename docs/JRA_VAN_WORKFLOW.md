# JRA-VAN Data Lab. ローカル取得

## 採用範囲

中央競馬の自動取得元は、公式SDKでプログラム取得が案内されているJRA-VAN Data Lab.に限定する。JRA一般Webページ、JBIS、netkeiba.comを対象にしたHTMLスクレイパーは追加しない。

確認日：2026年8月29日

- 月額2,090円（税込）
- 公式の`JV-Link`を通じてJV-Dataを取得
- `JV-Link`はWindows用ActiveX COM。macOSでは動作しない
- 利用キーはJV-Linkの設定画面で管理し、コマンド引数・設定ファイル・GitHubへ保存しない
- 取得レコード、正規化CSV、派生した脚質データ、学習済みモデルは再配布せず`local/`に置く

公式参照先：

- <https://jra-van.jp/dlb/>
- <https://jra-van.jp/dlb/sdv/sdk.html>
- <https://jra-van.jp/dlb/sdv/faq.html>

## M1 Macでの構成

M1 MacではWindows 11 ARMのVM、または別Windows PCを取得端末にする。Windows側とMac側で共有するフォルダも`local/`配下として扱い、クラウド公開しない。

Windows側の仮想環境にだけ`pywin32`を追加する。

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -e .
py -m pip install pywin32
```

JV-LinkとData Lab.利用キーを公式手順で設定した後、今週の基本レース情報を取得する。

```powershell
keiba-lab fetch-jra-van `
  --dataspec RACE `
  --fromtime 00000000000000 `
  --option 2 `
  --output local\jra-van\20260829-race
```

当日の天候・馬場状態などの一括速報は、公式FAQ記載の`0B14`と開催日キーで取得する。

```powershell
keiba-lab fetch-jra-van-realtime `
  --dataspec 0B14 `
  --key 20260829 `
  --output local\jra-van\20260829-realtime
```

各取得先には、改変していないJV-DataレコードのJSONLと、取得時刻・要求条件・件数・SHA-256を含むマニフェストを新規作成する。既存出力は上書きしない。JV-Linkのダウンロード待ちは公式戻り値`-3`に従って待機し、その他の負の戻り値では出力全体を除去して失敗する。

## 脚質・想定ペース

JV-Dataの確定成績から作るペース履歴CSVは次の列を持つ。

```text
race_id,scheduled_at,result_known_at,horse_id,field_size,first_corner_position,final_corner_position,finish_position,last_3f_rank
```

対象レースの`targets.csv`と合わせて自動生成する。

```bash
keiba-lab generate-pace-inputs \
  local/pace-history.csv \
  local/targets/20260829-tokyo-01.csv \
  --output local/pace/20260829-tokyo-01
```

出力は既存予測パイプラインがそのまま読む`pace-profiles.csv`と`pace-scenario.json`、入力ハッシュと生成器版を固定する`pace-generation-manifest.json`である。

推定は予測観測時刻までに結果が確定した直近5走だけを使用する。序盤位置、終盤の位置上昇、上がり順位、先行して失速しなかった度合いを0〜1へ正規化し、履歴が少ない馬は0.5へ縮約する。履歴が1件もないレースは`average`、信頼度0とする。

## 現在の実行境界

公開コードにはJV-Link取得器と脚質・想定ペース生成器を含む。実際の当日レコード取得には、利用者本人のData Lab.契約、利用キー、Windows環境が必要である。この3点がない環境で成功結果を合成したり、JRA一般Webページのスクレイピングへ自動的に切り替えたりしない。

取得した固定長JV-Dataを共通の`history.csv`・`targets.csv`・`pace-history.csv`へ変換する版固定アダプターは次工程である。変換完了までは、取得成功を「開催日一括予測が完了した」とは扱わない。
