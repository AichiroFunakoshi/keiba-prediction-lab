# 独自モデルとオッズを一緒に選ぶ

独自モデル単体の確率誤差だけで混合版の採否を決めない。同じ過去入力・発走前オッズで、モデル候補と混合比率を一緒に比較する。前半で選定し、後半で確認する。主指標と同点時の規則を結果を見る前に固定する。1着一致と確率誤差の改善は別々に報告する。すでに開発判断に使った日を「未使用の最終評価」と呼ばない。

`local/active-prediction-profile.json` にローカル設定を保存する。実データで決めた比率・モデル・検証結果をGitへ追加しない。設定の形式は次の通り。パスは設定ファイルからの相対パス、ハッシュは各ファイルのSHA-256である。

```json
{
  "schema_version": "1.0",
  "profile_id": "local-release",
  "model_path": "models/primary.json",
  "model_sha256": "<64 hexadecimal characters>",
  "market_weight": 0.8,
  "activated_at": "2026-01-31T18:00:00+09:00",
  "validation_path": "validation.json",
  "validation_sha256": "<64 hexadecimal characters>",
  "validation_summary": "選定期間と確認期間、指標、未使用開催での確認状況を記載",
  "comparison_profile": "comparison-profile.json"
}
```

`comparison_profile` は省略可能。比較設定は同じ形式で、さらに比較設定を連鎖させることはできない。`market_weight: 0` はオッズを使わない独自予測。正の比率での混合は既存の対数プールを使う。80%は確率の単純な加重平均ではなく、市場側の指数の重みである。

次回生成は比率・モデルを手入力せず、この設定を使う。

```sh
keiba-lab predict-profile-day local/history.csv local/plan.json local/market-snapshot \
  --frozen-at <timezone-aware-time> --output local/new-race-day \
  --win5-race-ids <race1> <race2> <race3> <race4> <race5>
```

WIN5対象は公式日程で確認して明示指定する。指定しなければWIN5は作成しない。直前予測では `--require-complete-body-weight` を追加する。

独立予測、混合版、任意のWIN5、設定受領記録、任意の比較版を一括保存する。途中失敗時は出力を公開せず、既存出力は置換しない。市場取得時刻は設定開始以降かつ予測固定以前に限る。全レースが市場混合に含まれなければ失敗する。比較版も同じ入力・時刻を使う。

RaceWeaveは有効な設定を上部に表示し、保存済み予測の当時の比率を別に表示する。新方式で生成した開催日は主表示と比較版を切り替えられ、WIN5も対応する版へ切り替わる。過去開催の予測へ新しい比率を後付けしない。無効な設定は黙って旧設定へ戻さず起動時にエラーとする。

前日段階で馬体重や当日馬場が欠けている場合、欠測として扱う。翌日の結果による比較を積み重ねるまで、恒常的な精度改善とは断定しない。
