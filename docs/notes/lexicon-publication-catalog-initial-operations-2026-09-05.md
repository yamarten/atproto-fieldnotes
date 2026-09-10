# Lexicon publication catalog初期運用評価（2026-09-05）

この文書は、当時の恒久運用の配置・頻度・trust接続を決めたbounded実データ評価記録です。ここでの
「scheduler不要」は後続の観測フェイズ設計で置き換えられています。現行契約は
[developer仕様](../developer/specs/lexicon-publication-catalog.md)を参照してください。

## 結論

初期運用は`data/lexicon-publication-catalog.db`をCodex評価専用の恒久sidecarとして継続保持し、週1回を目安に
必要時だけcatalogを更新します。全publisherの定期検証はせず、評価対象のschema IDまたはDIDだけを期限付きで
検証します。scheduler、Web、background jobへの接続は不要です。

実データ140 publicationでは67件がexact DNSとcurrent record再取得の両方に成功し、73件はDNS DID不一致でした。
current record再取得失敗は0件です。`did:web:lexicon.store`等のmirrorを落としつつ、既存project一意候補40件、
未登録authority 13 group、verified space publisher 12件を抽出できたため、調査入力としての価値はあります。一方、
publisher確認だけではproject帰属とaccount roleを確定できず、非record Lexiconは現行`owned_nsid`の対象でもありません。
sidecarからの自動project作成、identifier採用、ownership/promotion変更は行いません。

## 入力と実行境界

- source: `https://jetstream.us-west.bsky.network`
- fixed sealed tip / watermark: `25539389975`
- event time: `2026-08-04T13:13:09.344191Z`〜`2026-09-05T00:20:58.468161Z`
- segment bound: 20,000 event / 300秒。2 segment、約8分
- registry: `data/survey.db`
- registry接続: SQLite `mode=ro` + `query_only=true`
- 検証用sidecar: `.tmp/lexicon-publication-operations/catalog.db`、46,202,880 bytes
- 評価JSON: `.tmp/lexicon-publication-operations/evaluation-final.json`、contract version 1
- API keyは母艦workspaceの既存`direnv`環境からだけ渡し、出力へ保存していない

bootstrapは最初のsegmentで8,188 eventをstageし、watermark/current catalogを0のまま維持しました。第2 segment成功時に
全10,232 eventをtransaction適用し、watermarkを固定tipへ進めました。反映はnew 9,814、update 99、delete 319で、
DID+rkeyは10,110、current 9,864、tombstone 246です。直後の増分試行は同じsealed tipに対して変更0でした。

正本DBは評価後も2,555,985,920 bytes、mtime `1788492630`で、導入smoke前の値と同じでした。project 4,655、
`PRAGMA integrity_check=ok`をread-only接続で確認しました。sidecarも`integrity_check=ok`です。

## collection inventory

設定済み`https://collectiondata.usounds.work/collection_count_view`を既存`http_transport.request_json`で取得しました。
30秒試行は0 byte timeout、60秒上限の再試行は成功し、10,025行、valid NSID 9,642件でした。payload内の最新観測は
`2026-09-05T01:23:12.785`、SHA-256は
`410e07269ddf8f93f3a48acac1e1d7e566996a484027c4dd57d4c63612b6a66c`です。

fresh inventoryをregistry保存済み観測へ一時併合すると、space宣言435件中124件は活動あり、311件は未観測でした。
inventoryなしでは活動あり2、未観測433だったため、space評価でfresh inventoryを省略すると大きく過小判定します。
対象限定した12 space schemaの宣言60件では活動あり16、未観測44でした。`app.bulleted.*`と`app.kimbia.*`には
活動があり、`at.colocate.*`や`am.noz.at.spl.*`の一部は未観測でした。宣言と活動は別signalのまま維持します。

## publisher sample review

最初に未検証current publicationの安定順100件を検証し、verified 44、DNS DID不一致56でした。辞書順sampleの偏りを
補うため、未検証spaceをauthority group別に12 schema、registry未登録authorityをgroup別に12 schema抽出し、重複を
除いた23 schema / 40 publicationを対象限定で追加検証しました。追加分はverified 23、DNS DID不一致17でした。

合計140 publicationの分類:

| 分類 | 件数 |
| --- | ---: |
| exact DNS + current record verified | 67 |
| DNS DID mismatch | 73 |
| DNS成功後のrecord再取得・URI・hash失敗 | 0 |
| verifiedの既存project一意候補 | 40（exact schema/NSID 23、authority group 17） |
| verifiedの未登録authority | 13 group / 14 publication |
| verifiedの独立候補 | 1 group（`at.colocate`、2 schema、同一DID） |
| verified space publication | 12 |

DNS mismatchは`did:web:lexicon.store` 73件中61件と、`ai.syui.*`をpublishしていた別DID 12件です。後者もexact DNSが
別のpublisher DIDを返したためmirror側に留まりました。verified例は次の通りです。

- `africa.kandake.asha.recipe.scrapeUrl`: query/procedure系をcollection activityなしでも確認できた
- `app.bulleted.space`: 6 collectionのspace宣言と、その全6件のinventory活動を別々に確認できた
- `at.colocate.group`: 未登録authorityで、同一DIDの`at.colocate.comment`と合わせ独立候補条件を満たした
- `app.bsky.actor.contentVisibilityDeclaration`: DNS/current recordは確認できたが、registryのproject帰属は別判断として残った

67 verifiedのdefinition typeにはrecord 40、space 12、query 6、permission-set 4、procedure 2が含まれます
（一つのschemaに複数typeがあるため合計は一致しません）。publisher検証はmirror除外には高精度でしたが、
project帰属の精度を表すものではありません。

## trust・project・promotionへの接続判断

- `verified_publisher_candidates`: 調査入力だけに使う。publisher DIDをproject account/developer/organizationへ変換しない。
- `existing_project_unique_candidates`: project detailを照合し、record NSIDのownershipまで確認できた時だけ既存
  `add-seed --type nsid --project-key ...`へ渡す。authority group一致だけでは実行しない。
- `unregistered_authority_groups`: 新規調査候補として扱う。`independent_candidate=true`も自動project作成に使わない。
- `space_declared_collections`: inventory比較と探索にだけ使い、`owned_nsid`や`collection_observations`へ書かない。
- `mirror_or_catalog_only` / delete / failed / expired: 採用対象外。必要な候補だけ1日TTL後に再検証する。
- promotion: sidecarから直接signalを作らない。人がrecord NSIDを明示採用した後、通常`enrich`で現行の
  DNS/schema evidenceを再取得し、既存`lexicon_publisher_confirmed`へ接続する。

この境界なら既存の信頼・帰属policyに価値を渡せますが、sidecar固有の自動mutationや互換layerは不要です。

## 保持と再現

恒久sidecar本体はwatermark、tombstone、期限付きverificationを含むため継続保持します。raw event logは保存せず、
inventoryと評価JSONは`.tmp`に限ります。sidecarは再構築可能なので定期backupを作らず、schema変更や破壊的maintenance前
だけSQLite online backupを1世代作ります。検証用sidecar、inventory、評価JSONはcommitしません。

対象限定検証例:

```bash
bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py verify \
  --sidecar-db data/lexicon-publication-catalog.db \
  --registry-db data/survey.db \
  --schema-id at.colocate.group --schema-id at.colocate.comment \
  --verify-limit 10 --dns-timeout 5 --http-timeout 10 \
  --output .tmp/lexicon-publications/at-colocate.json
```

実評価JSONのSHA-256は`2c80ac30f759fb4a1e61645a17c8fdf0049069de05898141a03574a7d51c9c04`です。
成果物自体はcommitせず、本記録の件数と具体例を再現可能な境界として残します。
