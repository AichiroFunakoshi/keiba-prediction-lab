# JRA-VAN Data Lab. ローカル取得

## 採用範囲

JRA-VAN Data Lab.は、契約者本人が公式JV-Linkを使う取得経路とする。[JRA公式公開ページを使う無料ローカル運用](JRA_WEB_WORKFLOW.md)は利用判断を利用者に委ねる実験経路であり、標準または承認済みデータ源とは扱わない。JBIS、netkeiba.comを対象にした取得器は追加しない。

確認日：2026年9月3日

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

## 開催日入力の一括作成

Windows側で作成した履歴、今週RACE、当日`0B14`の3スナップショットをMacのローカル領域へ渡し、RA・SE・WE・WH・AVを公式JV-Data 4.9仕様の固定位置で変換する。

```bash
keiba-lab prepare-jra-van-race-day \
  local/jra-van/history \
  local/jra-van/20260829-race \
  local/jra-van/20260829-realtime \
  --race-date 2026-08-29 \
  --observed-at 2026-08-29T09:10:00+09:00 \
  --output local/prepared/20260829
```

この処理は次を一括生成する。

- `history.csv`と`training.csv`
- コーナー・上がり順位用`pace-history.csv`
- 取消・除外を除いたレース別`targets.csv`
- レース別`pace-profiles.csv`と`pace-scenario.json`
- そのまま`predict-race-day`へ渡せる`race-day-plan.json`
- 原スナップショットと全出力のSHA-256を持つアダプターマニフェスト

取得マニフェストの改変、取得時刻が観測時刻より後、当日馬場不足、発走後観測、3頭未満、未対応トラックコードがあれば、出力全体を残さず失敗する。

## 現在の実行境界

公開コードにはJV-Link取得器、JV-Data 4.9固定長アダプター、脚質・想定ペース生成器を含む。実際のJV-Data取得には、利用者本人のData Lab.契約、利用キー、Windows環境が必要である。未契約時にJV-Link成功結果を合成しない。無料経路は別コマンドとして明示的に実行し、自動的に切り替えない。

アダプターはRA・SE・WE・WH・AVの必要項目だけを版固定している。JRA-VANが仕様を更新した場合は、仕様差分を確認してアダプター版と合成fixtureを同時に更新する。
