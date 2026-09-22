# Promotion一覧の速度改善と統合確認

通常の紹介候補一覧HTTP応答を同条件比較で5.66秒から3.30秒へ短縮しました。推薦規則を変えず、完全project復元の後に重複していた正規化とbaseline作成、本体再読込みを除去しています。保留一覧は判断履歴による先行絞込みを追加しました。母艦統合と実運用serverへの反映も完了しています。

## 計測条件と結果

2026-09-23、同じMac、Python 3.14.7 / SQLite 3.53.4。開始commitは`0f6a3842b186017ac57d3ae1e81dd0c3755b1926`。正本`data/survey.db`を`mode=ro`で読み、5,054 project（active 3,382件）を確認しました。HTTP比較・判断保存後の確認には、read snapshotを固定したSQLite online backupを1つだけ作り、`127.0.0.1:8765`、検証token、同じ複製DBを使用しました。正本への書込み・本番Webの停止再起動・外部収集はしていません。

変更前は開始時の`registry.py`、`project_record.py`、`runtime_view.py`から対象関数を復元し、変更後は現在の関数を使用しました。どちらも同じ`RegistryWebServer` / handler / application serviceで認証付きGETを送り、DB接続、候補生成、JSON送信を含むclient側の経過時間を測りました。時間内訳用wrapper以外のHTTP経路は通常実装です。各viewを順番に3巡、`limit=50`、`offset=0`、personal tool除外、理由・kind指定なし。通常／技術候補は既定のactive、他viewは全tracking stateです。計測中に検証側の別ジョブは走らせていません。OS cacheをflushしたcold測定や複数clientの負荷試験ではありません。

| view | 候補数 | 変更前の中央値（範囲） | 変更後の中央値（範囲） |
| --- | ---: | ---: | ---: |
| 確認対象 / inbox | 448 | 5.664秒（5.600–5.821） | 3.297秒（3.194–3.320） |
| 技術候補 | 16 | 5.638秒（5.612–5.674） | 3.280秒（3.182–4.167） |
| 保留 | 0 | 9.225秒（9.175–9.296） | 0.179秒（0.179–0.179） |
| Lowered | 1 | 0.004秒（0.004–0.004） | 0.003秒（0.003–0.003） |
| すべて | 502 | 9.129秒（9.030–9.224） | 5.112秒（5.014–5.162） |

通常HTTPの内訳中央値は、project読込みが3.498→3.098秒、queue評価が2.075→0.115秒、payload全体が5.661→3.293秒でした。DB接続は約0.001秒、JSON生成・送信も約0.001秒で、今回の主要因ではありません。過去の21.8秒を今回の変更前値としては使いません。正本を直接読んだ最初のpayloadは8.21秒でしたが、HTTP比較とcache条件が異なるため改善率の基準から外しています。

診断用cProfileではactive 3,382件に対しshape正規化が10,146→3,382回、baseline作成が6,764→3,382回になりました。通常一覧の残る約3秒と「すべて」の約5秒は主に完全runtime viewの復元です。即時表示やデータ増加後の時間は保証しません。詳細を開いた際の非同期マージ解析は今回の最適化対象外で、実画面操作時に6.2–6.9秒を観測しました。その処理と重なった保留一覧GETは0.63秒でした。request間cacheによる期限・更新の失効管理、保存する推薦projectionや推薦規則のSQL二重実装は、今回の重複除去には不要なので追加していません。

## 同値性と更新整合性

- 同じ複製DBの全5,054件について、旧・新の完全runtime view（内部baseline・子行・revisionも含む）のcanonical JSON hashが全件一致しました。保存用の完全復元契約を省略していません。
- 旧・新の全候補について、`queue`、`inbox`、`technical`、`waiting`、`lowered`、`all`の全fieldと順序が一致しました。5 Web viewの実HTTP応答も全field一致です。
- temp DBの回帰testで、各view、kind、personal tool、Unicode理由検索、理由別件数、total、offset/limit、空ページ、紹介済み／対象外、signal消失、旧確認期限、保留設定・解除、優先度解除、期限切れ、根拠変化を確認しました。既存のmerge・保存roundtrip・stale mutation testも通しています。
- 別connectionが本体選択後にscalarと判断を更新しても、取得結果の本体・子行・revisionが更新前snapshotに揃い、次回取得で更新後を返すtestを追加しました。Loweredの同時解除testも維持しています。
- `bin/project-registry-test`全1,020件、関連Promotion 18件、Web APIを含む全unit test、`node --check scripts/project_registry_web/static/app.js`を実行しました。Node依存は母艦にある既存`node_modules`を検証中だけ参照し、依存追加はありません。
- Codex内蔵Chromium、複製DBの実画面で通常一覧50/448→100/448のページ送り、技術候補16件、Loweredの理由検索、保留0件、すべて502件を確認しました。複製DBだけでy-pdsを理由付き保留にし、保留1件への反映と解除後の通常448件への復帰を確認しました。browser warning/errorなし。UI・JSは変更していないためmobile layoutの再確認はしていません。

検証serverとbrowserは停止・終了し、正本複製とSQLite補助ファイルは`cleanup-workspace-temp --older-than-hours 0 --execute`で削除しました。

## 実運用の確認と残る境界

2026-09-23、母艦統合後の全1,020unit testとJS構文確認が成功し、承認後にjob・workerなしを確認してWebをreloadしました。build `0bb692a70a55`と同じ正本DBを確認しています。本番各一覧のGETは通常448件、技術16件、保留0件、Lowered1件、すべて502件でした。再起動後の初回GETは通常6.106秒、技術3.199秒、保留1.147秒、Lowered0.003秒、すべて5.329秒。追加2回は通常3.142／3.196秒、保留0.174／0.173秒でした。これは本番の少数回確認で、変更前後の同条件比較やcold性能保証ではありません。HTTPSの実Chromiumで技術候補・詳細・理由任意の保留ダイアログを確認し、保存せずキャンセルしました。DB更新・migration・repair・再enrichは実施していません。同時収集下の待ち時間と長期の候補評価運用は未確認です。

今回の範囲で推薦精度や`lexicon_count`の既知保存問題は変更していません。精度評価は[再発見導線の運用評価](promotion-rediscovery-review.md)を参照してください。ジョブ再開・復元は対象外です。このnotesは残る完全復元負荷を再検討するための比較条件を保持し、その残件が解消したらstatusからの参照とともに整理します。
