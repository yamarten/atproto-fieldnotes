# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- identifierの採用理由、旧identifier、non-production、時間関係、noiseを分離するtemp DB prototypeと正本DBのread-only移行投影まで完了しました。production schemaと挙動は未変更です。結果とreview対象は[Identifier保存方式のshadow比較](notes/identifier-storage-shadow-comparison.md)にあります。
- promotion queueのservice確認度、手動優先度、固定fixture評価と正本DB sample reviewまで完了しました。
- Qwen / Gemmaの通常処理model評価は完了しました。Gemmaはproject-level triageで一部優位でしたが、現行repository analysis policyの固定`goat` material 10回でJSON成功5・失敗5となったため通常処理候補から外し、既定modelと定期jobはQwenを維持します。正本DBでのGemma試用は実施していません。
- category契約、元profileなし／あり、prompt調整用／調整後確認用case、反復、文章、重い入力、memory、複製DB、repository解析と後段triageを分ける評価方法を実装・文書化しました。
- masterのrepository取得・material化・LLM解析を取り込み、根拠位置の検査、bounded excerpt、最大出力token数、本文を残さないJSON診断を追加しました。
- identifier roleはNSIDの`owned_nsid`とclassificationを中心に整理し、正本DBの移行とcase補正を適用済みです。一度きりのCLI移行・case補正コードは削除し、現行checkoutで全560 testを通しています。
- Qwenも固定`goat`の全件で根拠不足の観測が除外され、6/10件にmaterial外collectionがありました。既定model維持とは別に、repository解析内容は改善対象です。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- `projects.website_url`とservice identifierを分離し、projectを代表する公式website、機能を提供するservice surface、説明用resourceをどう保存・表示するか、移行実装前に決める。Lexicon Community、library documentation、native appの紹介・配布siteが既知事例。
- Qwen repository解析で除外された観測とmaterial外collectionを一般化して減らせるか、promptまたはmaterial契約を見直す。特定repository名や値に限定した規則は追加しない。

## DB 操作メモ

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認し、必要なら
その時点で新しいbackupまたは複製を用意してください。

history JSON圧縮は2026-07-26に正本DBへ適用済みです。VACUUMは行っておらず、解放pageは今後の書き込みで
再利用します。file sizeを直ちに縮める必要が生じた場合だけ、別processを止めてVACUUMを判断してください。

## 未検証・未接続

未検証:

- Web workerから実外部sync sourceへのend-to-end接続

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLIなど別processを含むwriter排他
- DB-level migration/data policy marker
  - 正本DBが単一で、移行後の修正も通常DBへ反映済みのため直近優先から外した
  - 将来、複数DBやpolicy世代分岐を扱う場合だけ再検討する

より広い制約と確認済み範囲は[status.md](status.md)を参照してください。
