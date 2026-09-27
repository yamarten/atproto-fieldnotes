# Service観測の対象・頻度と検出範囲

低頻度の観測経路はありますが、監視対象・頻度・login等の検出範囲は未決です。実運用の有効化・設定変更・正本への観測保存は未承認です。以下は2026-09-15の観測で、現在のURLやbacklogを再確認した値ではありません。

## Service観測と頻度の判断

当時の正本dry-runでは674 URL（active 394、stable 253、stalled 17、out_of_scope 10）が期限到来で、全件browser試行未記録でした。archived／ignoreは対象外です。週20 URLでは一巡34回必要で、30日の再選択間隔は鮮度保証になりません。

通常先頭20候補から用途・状態を分けた6 URLを、Playwright 1.61.0の通常Chromium経路で観測しました。約29秒、6 navigation、249 request、完了body約4.6 MB。URL取得失敗は0でしたが、全件`surface_confirmed=false`です。subresource失敗は終了・非公開の根拠にしません。認証入力・登録・投稿はしていません。

| URL | 秒 | request数 | request失敗 | 完了body bytes | 観測の意味 |
| --- | ---: | ---: | ---: | ---: | --- |
| [Trailcast](https://trailcast.shino3.net) | 3.32 | 37 | 0 | 745,755 | 日本語loginボタンとhandle入力の説明が見えるが、formとして抽出されず、現行英語語彙のoauth判定にも該当しない |
| [Firehose Network](https://firehose.network) | 3.60 | 12 | 1 | 149,573 | relay一覧と案内を取得。browserの投稿/login検出だけでprotocol serviceを確認できない |
| [RankThat](https://rankthat.net) | 3.21 | 4 | 0 | 335,144 | demo/mockである旨の公開説明を取得。buttonのLoginはlink follow-upの対象外 |
| [FreeMix](https://freemix.fm) | 2.91 | 12 | 3 | 161,920 | beta申込み案内を取得。試験DBの旧 `beta` は最新の未確認観測により `unverified`。終了とは判定しない |
| [Crate](https://app.crate.social) | 4.13 | 12 | 0 | 354,834 | `/login`へredirect。表示されたhandle formのplaceholderが `alice.bsky.social` で、現行のemail/username/handle等の入力判定が拾わない |
| [Cartes](https://cartes.app) | 11.61 | 172 | 0 | 2,852,677 | 地図・地域名を取得。現行の投稿/login条件に合わず、閲覧型利用面は確認扱いにならない |

説明・tracking state・最終enrich日時を変えずに試験DBへ保存できたことは確認しましたが、正本backlogの解消や再観測の差分noiseは未確認です。

再開時は次を判断します。

- 全対象を概ね30日で追うなら、当時の規模では日平均約23 URLが必要です。毎日25 URLは候補ですが、少数観測からの負荷外挿は保証値ではありません。対象を限定して週20を維持する案と比較します。
- 検出を広げる場合は日本語・form外入力・handle例と、検索欄・newsletter・単なる案内buttonの反例で評価します。個別site名を例外にしません。browserでの案内取得とprotocol serviceの稼働確認は分けます。
- 長期失敗率、timeoutの多いbatch、再観測noise、人のreview件数・時間、連続運転は未検証です。観測のみの試用範囲と再評価日を先に決めます。

## 再開資料

主checkoutの`.tmp/temporal-validation-20260915/services-report.json`に取得素材・run ID・計測、`run_services.py`に実行手順があります。正本6件の必要fieldだけで作った試験DBは削除済みです。素材は母艦管理の一時保全で、恒久fixtureではありません。監視方針の再検討時に恒久保持／破棄を判断し、実験不採用の場合も生素材の処分は別途判断します。説明更新の材料は[説明品質](description-refresh-quality.md)を参照してください。
