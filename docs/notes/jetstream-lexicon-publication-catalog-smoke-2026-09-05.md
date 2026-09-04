# Jetstream Lexicon publication catalog smoke（2026-09-05）

この文書は、sidecar catalog導入時のbounded実データ確認記録です。現行仕様は
[developer仕様](../developer/lexicon-publication-catalog.md)を参照してください。

## 結論

`.tmp/lexicon-publication-catalog-smoke/catalog.db`へ固定sealed tip `25522928479`のsnapshotを4 segmentで
取り込み、途中3回はstagingだけを進め、最終segmentでcatalog反映とwatermark更新を完了しました。正本
`survey.db`は`mode=ro` + `query_only`で4,655 projectを照合し、実行前後のsize `2,555,985,920 bytes`、
mtime `1788492630`は変わりませんでした。

## 範囲と結果

- source: `https://jetstream.us-west.bsky.network`
- collection: `com.atproto.lexicon.schema`
- snapshot: `(0, 25522928479]`
- segment bounds: 初回50 event / 30秒、後続各20,000 event / 300秒
- 実event: 10,236（create/updateの新規反映9,812、同一key後続更新105、delete 319）
- tracked DID+rkey: 10,104
- current: 9,858
- tombstone: 246
- staging: 完了後0
- 所要時間: 約12分

最初の50 event後はpending 50、watermark 0、current 0でした。次の7,617 event後もpending 7,667、
watermark 0、current 0、次の1,537 event後もpending 9,204、watermark 0、current 0でした。最終segmentでだけ
全10,236 eventをtransaction適用し、pendingを消してwatermarkをsealed tipへ進めました。

## publisher検証

valid current publicationから安定順の先頭5件だけをDNS/current record再取得しました。

- 成功3: `actor.rpg.generator`、`actor.rpg.master`、`actor.rpg.sprite`を
  `did:plc:kwgllf365cwmxbnxitx4pjdj`で確認
- DNS DID不一致2: 同じ`actor.rpg.generator`、`actor.rpg.master`をpublishしていた
  `did:web:lexicon.store` mirror
- HTTP/DNS transport failure: 0

このsampleでは、同じschema IDのcatalog mirrorを保持しつつ、exact DNSとcurrent recordが一致するpublisherだけを
verified候補へ出せました。5件の検証後の評価JSONはverified publisher 3、verification failure 2でした。

space宣言はpublisher別に435件、registry保存済み活動観測だけとの比較では未観測433件でした。より新しい
`collectiondata.usounds.work` inventoryの取得も30秒上限で試しましたが0 bytesのtimeoutとなったため、実smokeの
活動比較はregistry保存済み観測に限定されます。任意inventory JSONの併合経路はunit testで確認しました。

## 補足

前回比較で使ったnumbered host `jetstream2.us-east.bsky.network`とwest相当はplannerをHTTP 404で返しました。
公式の現行v2 archive host `jetstream.us-west.bsky.network`では同じplanner契約が成功したため、developer scriptの
既定値を現行hostへ更新しました。raw snapshot、report、temp DBは`.tmp/`だけに置き、commitしません。
