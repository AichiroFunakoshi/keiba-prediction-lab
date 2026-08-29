# 実データ候補の監査

最終確認日：2026年8月29日

## 判定基準

データが無料でダウンロードできるだけでは採用しない。次のすべてを確認する。

1. 元データの作成者・提供者を追跡できる
2. 取得方法が提供元の条件に反していない
3. 研究利用と再配布の範囲を説明できる
4. 予測時点で利用可能な列と、結果確定後の列を分離できる
5. 更新日、ファイルハッシュ、スキーマを記録できる

機械可読な判定は [`data/sources.json`](../data/sources.json) に保存する。
コードと利用者のローカルデータを分離する規則は [`DATA_USAGE_POLICY.md`](DATA_USAGE_POLICY.md) に定める。

## 現在の結論

### JRA-VAN Data Lab.

状態：`approved`（契約者本人のローカル利用に限定）

- 提供元：JRAシステムサービス株式会社
- 取得方式：公式SDKのActiveX COMモジュール`JV-Link`
- 料金：月額2,090円（税込、2026年8月29日確認）
- 対応環境：Windows。公式FAQはmacOS版のサポート終了とmacOS非動作を明記
- データ：今週情報、過去走、結果、コーナー通過順、速報情報などをJV-Data仕様で提供
- 自動取得：公式開発者FAQは、JV-Link経由の定期取得やオッズ巡回取得を想定し、更新間隔を超える要求を避けるよう案内
- 保存・公開：利用キーと取得データは利用者のローカル領域だけに保存し、GitHubへ置かない。本リポジトリでは再配布禁止として扱う

結論：中央競馬の正式な自動取得元として採用する。ただしJV-Linkを実行するWindows環境と有効なData Lab.利用キーが必要である。M1 Mac単体からJV-Linkを直接呼び出すことはできないため、Windows VMまたは別Windows PCで取得し、厳格な受渡し契約を通じてMac側へ渡す。

### 合成テストデータ

状態：`approved`

リポジトリ内で手作業により生成し、実在する馬・レースを含まない。取込処理、欠損、重複、未来情報の隔離をテストする目的に限って使用する。モデル学習には使用しない。

### Kaggle：JRA Horse Racing Dataset

状態：`review_required`

- 表示ライセンス：CC BY 4.0
- 公開範囲：1986年から2021年
- 説明上の取得元：netkeiba.comからのスクレイピング
- 長所：結果、払戻、ラップ、コーナー通過順が整理されている
- 保留理由：Kaggle上のライセンス表示は明確だが、投稿者が元サイトのデータを再許諾できるか確認できていない

結論：コードの設計資料には使えるが、ダウンロード、再配布、本番学習への採用は保留する。

### Hugging Face：KBlueLeaf/jp-racing-horse

状態：`review_required`

- 表示ライセンス：Apache-2.0
- 内容：1990～2025年等のSQLiteデータベース、合計約1.75GB
- Dataset Viewer：利用不可
- README：ライセンス宣言のみ
- 保留理由：元データ、取得方法、加工方法が記載されていない

結論：ライセンス表示だけではデータ内容の権利と来歴を確認できないため、採用しない。

### NAR公式：地方競馬情報サイト データダウンロード

状態：`review_required`

- 提供元：地方競馬全国協会の公式サイト
- 形式：日次・月次ZIP内のCSV
- 範囲：レース情報は1998年以降、オッズ情報は2026年3月以降
- 長所：公式提供で、出馬表、結果、オッズの項目仕様が公開されている
- 保留理由：対象が地方競馬で本プロジェクトの中央競馬とは異なる。公開リポジトリでの再配布許諾も明示確認が必要

結論：取込方式の検証候補として有望だが、JRAモデルの学習データとしては扱わない。

## 参照先

- [JRA-VAN Data Lab.](https://jra-van.jp/dlb/)
- [JRA-VAN SDK提供コーナー](https://jra-van.jp/dlb/sdv/sdk.html)
- [JRA-VAN開発者FAQ](https://jra-van.jp/dlb/sdv/faq.html)

- [Kaggle dataset](https://www.kaggle.com/datasets/takamotoki/jra-horse-racing-dataset)
- [Hugging Face dataset](https://huggingface.co/datasets/KBlueLeaf/jp-racing-horse)
- [NARデータダウンロード説明書](https://www.keiba.go.jp/pdf/manual/data_pdf_manual.pdf)
- [NAR利用規約](https://sp.keiba.go.jp/terms.html)
- [JRAホームページ利用条件](https://www.jra.go.jp/use/)

## 次の判断

中央競馬の実データについて、権利と継続取得方法を確認できる無料データ源が見つかるまでは、コードを合成データで開発する。候補データを利用する場合も、元ファイルをGitHubへコミットせず、利用者が自分で取得してハッシュを記録する方式に限定する。

利用者自身がローカルへダウンロードする方式であっても、取得方法やローカルでの加工が提供元の条件に反してよいことにはならない。特定サイト向け取得器を追加する場合は、公式API・SDKの有無、自動取得の許可、アクセス頻度、保存・加工範囲を事前に確認する。
