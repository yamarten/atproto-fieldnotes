# Service観測の対象・頻度と検出範囲

[TODOのservice監視](../developer/todo.md#説明更新の品質とservice監視)で、サービスの変化を低い負荷で拾うために、何をどの頻度で観測し、どこまで利用可能と判断するかを決めるための予備調査です。巡回対象数と上限の比較、異なる利用面を持つ6 URLのbrowser観測から、頻度案と検出不足を整理します。説明をどう書き換えるかの方式比較は[説明更新の評価](../../tests/fixtures/project_registry/description-refresh-evaluation/README.md)で扱います。

低頻度の観測経路はありますが、監視対象・頻度・login等の検出範囲は未決です。実運用の有効化・設定変更・正本への観測保存は未承認です。以下は2026-09-15の観測で、現在のURLやbacklogを再確認した値ではありません。

## Service観測と頻度の判断

当時の正本dry-runでは674 URLが期限到来で、全件browser試行未記録でした。archived／ignoreは対象外です。週20 URLでは一巡34回必要で、30日の再選択間隔は鮮度保証になりません。

通常先頭20候補から用途・状態を分けた6 URLを、Playwright 1.61.0の通常Chromium経路で約29秒かけて観測しました。URL取得失敗は0でしたが、全件`surface_confirmed=false`です。subresource失敗は終了・非公開の根拠にしません。認証入力・登録・投稿はしていません。

| URL | 観測の意味 |
| --- | --- |
| [Trailcast](https://trailcast.shino3.net) | 日本語loginボタンとhandle入力の説明が見えるが、formとして抽出されず、観測時の英語語彙によるoauth判定にも該当しない |
| [Firehose Network](https://firehose.network) | relay一覧と案内を取得。browserの投稿/login検出だけでprotocol serviceを確認できない |
| [RankThat](https://rankthat.net) | demo/mockである旨の公開説明を取得。buttonのLoginはlink follow-upの対象外 |
| [FreeMix](https://freemix.fm) | beta申込み案内を取得。試験DBの旧 `beta` は最新の未確認観測により `unverified`。終了とは判定しない |
| [Crate](https://app.crate.social) | `/login`へredirect。表示されたhandle formのplaceholderが `alice.bsky.social` で、観測時のemail/username/handle等の入力判定が拾わない |
| [Cartes](https://cartes.app) | 地図・地域名を取得。観測時の投稿/login条件に合わず、閲覧型利用面は確認扱いにならない |

説明・tracking state・最終enrich日時を変えずに試験DBへ保存できたことは確認しましたが、正本backlogの解消や再観測の差分noiseは未確認です。

再開時は次を判断します。

- 全対象を概ね30日で追うなら、当時の規模では日平均約23 URLが必要です。毎日25 URLは候補ですが、少数観測からの負荷外挿は保証値ではありません。対象を限定して週20を維持する案と比較します。
- 検出を広げる場合は日本語・form外入力・handle例と、検索欄・newsletter・単なる案内buttonの反例で評価します。個別site名を例外にしません。browserでの案内取得とprotocol serviceの稼働確認は分けます。
- 長期失敗率、timeoutの多いbatch、再観測noise、人のreview件数・時間、連続運転は未検証です。観測のみの試用範囲と再評価日を先に決めます。

当時の取得素材と試験DBは本checkoutに収録していません。再開時は現在のURL・backlogから対象を選び、上記の検出不足と反例を含めて新たに観測します。

## この資料の役割が終わる条件

監視の対象・頻度・検出範囲を決め、必要な検出例を仕様・testへ引き継いだ時に削除します。監視を有効化しないと決めた場合や、新しい観測で判断材料を置き換えた場合も閉じられます。試用を選ぶ場合の実施・確認待ちはTODOで管理し、長期運転の全項目を確認するまで旧観測を保持する必要はありません。
