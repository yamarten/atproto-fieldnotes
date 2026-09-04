# Jetstream Lexicon publication比較（2026-09-04）

この文書は、`com.atproto.lexicon.schema`のJetstream v2 snapshotを既存registryとread-only比較した作業記録です。
現行仕様ではなく、恒久laneを接続するか判断するための観測です。

## 結論

Jetstream publicationには`sync-collections`と重ならない固有価値があります。特に、record activityを必要としない
query / procedure / subscription、permission-set、spaceと、そのspaceが宣言するcollectionを発見できます。
一方、同じschema IDのmirror publicationが非常に多く、eventだけではpublisher ownershipもproject帰属も
確定できません。

次段階として、projectやpromotionへ直接接続せず、DID+rkey単位のcurrent publication catalogとsealed sequence
watermarkだけを持つread-only discovery laneを実装する価値があります。初回全履歴の再走査は約11分かかり、1 runの
600秒上限でも完走しなかったため、通常運用はtransaction成功時だけwatermarkを進める増分方式を前提とします。
promotion signal、project作成、identifier採用、ownership確定はまだ接続しません。

## 比較契約

- source: Jetstream v2 `com.atproto.lexicon.schema` commit event
- snapshot: `afterSeq` exclusiveからplannerが固定した`sealedTipSeq`まで
- bounds: segmentごとに最大10,000 eventかwall time上限
- fold key: `(DID, rkey)`
- fold order: 大きい`seq`をcurrentとし、古いまたは重複したeventを無視する
- create / update: recordをcurrent候補にする
- delete: 同じDID+rkeyのcurrent候補を除く
- 保存したevent field: `seq`、`time`、`did`、`operation`、commit `rev`、collection、rkey、record
- schema検査: record `$type`、rkeyと`id`の一致、`lexicon` version、`defs`、definition type、
  space definitionの`collections`
- `known schema`: DBの`state_json.evidence.lexicon_schema_samples`にあるIDとのexact一致
- `known NSID`: `project_identifiers(kind=nsid)`とのexact一致。schema取得済みとは扱わない
- `sync-collections observed`: 同じ比較で取得したcollection inventory中のexact collection NSID一致。
  DBのnamespace集約済み`collection_observations`や`owned_nsid`は活動観測の代用にしない
- DB: SQLite URI `mode=ro`に加え`PRAGMA query_only=ON`

入力eventとcollection inventoryは`.tmp/lexicon-publication-comparison/`へ保存し、複数snapshot segmentを
networkなしで再foldできます。`.tmp/`は成果物ではなく、commitしません。

## 実行範囲

最初のsegmentは`afterSeq=0`から開始し、sealed tip `25520354715`を固定しました。600秒で9,003 event、
`lastEventSeq=25152824197`まで進み、time boundで終了しました。次のsegmentを同じsequenceから再開し、1,198 eventを
取得してsnapshot iteratorが正常終了しました。両segmentのsealed tipは同一で、結合後は次の範囲です。

- event: 10,201（create 9,178、update 704、delete 319）
- event time: 2026-08-04T13:13:09Z〜2026-09-04T13:22:15Z
- tracked DID+rkey: 10,102
- current publication candidate: 9,852
- deleted current key: 250
- malformed event: 0
- stale / duplicate input event: 0

Jetstream archive開始前から存在し、この期間にeventがなかったrecordは復元できません。このため「current」は
このsealed archiveでcreate/update/deleteを観測できたDID+rkeyに限ります。

collection inventoryは2026-09-04T14:23:16Z頃に取得しました。正本DBは4,655 project、2,555,985,920 bytes、
mtime `1788492630`で、比較前後にsizeとmtimeは変化しませんでした。比較接続は`query_only=true`、別の
`sqlite3 -readonly`による`integrity_check`も`ok`でした。

## 結果

publicationはmirrorを含むため、publication件数とunique schema ID件数を分けます。

| 観測 | 件数 |
| --- | ---: |
| current publication | 9,852 |
| valid schema publication | 9,795 |
| valid unique schema ID | 5,091 |
| 複数DIDからpublishされたvalid schema ID | 4,338 |
| DBのknown schemaと重複するpublication | 2,747 |
| DBのknown schemaと重複するunique ID | 1,376 |
| record definitionを持つvalid unique collection | 2,193 |
| inventoryで活動未観測のrecord collection | 566 |
| query / procedure / subscriptionを持つpublication | 4,099 |
| permission-set publication | 444（226 unique ID / 135 DID） |
| space publication | 136（74 valid unique ID / 51 DID） |
| spaceが宣言するvalid unique collection | 201 |
| うちinventoryで活動未観測 | 148 |
| 未知authority group | 388 |

definition typeのpublication内出現数はrecord 4,103、query 2,424、procedure 1,648、subscription 27、
permission-set 444、space 136でした。1 schema内に複数typeがあるため合計はpublication数と一致しません。

schema shapeでは、missingまたはinvalidな`id`が57、`defs`不足9、rkey / ID不一致29、`lexicon != 1`が1、
space内のinvalid collectionが1でした。これらは未知仕様へ丸めず個別problemとして残します。record `$type`不一致は
ありませんでした。NSIDの大文字小文字は維持し、inventoryとのexact比較でも小文字化しません。

## sample review

分類はpublication単位で、帰属の決定ではありません。

- known schema overlap: 2,747。`blue.flashes.feed.post`、`store.88x31.button`、
  `social.bragsheet.entry`など、DBでschema sample取得済みのIDを再発見した。
- 既存projectへ一意に関連しそうな候補: 1,800。authority groupがDBの1 projectにだけ現れるもの。
  `blue.recipes.feed.getRecipe` / `getRecipes`というquery、`id.sifa.authProfile`等のpermission-setは、
  collection activity中心の導線では得にくい補完情報だった。
- 独立project候補: 59。DBに同authority groupがなく、同じDIDが同groupで2件以上のcurrent schemaを
  publishしているものに限定した。`dev.engineered.memory`の3 space、`at.colocate`のspace・record・
  permission-set群が代表例。ただしschema catalogやmirrorである可能性を排除しない。
- schema catalog上の観測: 5,189。authorityがDBの複数projectへ曖昧に対応するもの、単独schema、
  複数DID publicationなど。`com.getorbyt`のquery/procedure群や`at.noted`のrecord群はこの段階に留めた。

spaceの代表例には`app.bulleted.space`、`at.colocate.group`、`garden.atmo.circle`、
`social.colibri.beta.*`、`space.ziran.*`があります。space宣言201 valid collectionのうち148はinventoryに無く、
record activityなしの期待collection集合を見つけるという仮説を強く支持します。反対に53 collectionはinventoryにも
存在し、space declarationと実活動は別々のsignalとして扱う必要があります。

同じvalid schema IDを複数DIDがpublishする例が4,338 / 5,091 IDあり、`did:web:lexicon.store`等のcatalog mirrorも
観測しました。したがってevent DIDをauthority ownerと解釈できません。

## 仕様状態と限界

比較実装が直接使ったJetstream clientはrepositoryで固定済みの`@bsky/jetstream 1.0.0`です。snapshot plannerの
sealed tipと`afterSeq`契約はこのversionの実装に基づきます。

2026-09-04時点でworkspaceに導入されている`@atproto/lex-schema 0.2.6`はpermission-setを実装していますが、
spaceを通常のschema typeとして公開していることは確認できませんでした。本比較ではspaceをPermissioned Dataの
提案段階の観測型として扱い、確定仕様とはしません。提案文書のrevisionはこの作業では独立にpinできなかったため、
恒久validatorを実装する前に公式proposalのrevisionを確認する必要があります。

この比較ではDNSもcurrent PDS record再取得も行っていません。exact `_lexicon` DNSがevent DIDを指すか、current
recordが取得できるかは未確認です。DNS変更はJetstream commit eventに含まれないため、将来確認する場合も
`dns_same_did`と`current_record_refetched`を別々に記録します。

## 最小の次段階

1. projectとは別のpublication catalogをDID+rkeyで持ち、schema ID、record `$type`、lexicon version、defs type、
   space collections、seq/rev/time、record hashまたはbounded raw recordを保存する。
2. bootstrap完了後はsealed tipをwatermarkにし、update/deleteを同じkeyとsequenceで反映する。catalog更新transactionが
   完了した場合だけwatermarkを進める。
3. schema ID単位でmirrorを束ねるがpublisher DIDを捨てない。eventだけからcanonical publisherを選ばない。
4. 既存projectへの一意候補、独立project候補、catalog-onlyをreview queueとして出す。自動project作成はしない。
5. 必要な候補だけDNSとcurrent recordを再検証する。確認済みpublisher候補になっても、既存のownership/trust policyを
   経由するまではidentifierやpromotionへ反映しない。

再現例:

```sh
bin/_project-registry-python lexicon-publication-comparison scripts/lexicon_publication_comparison.py \
  --db-path /path/to/survey.db \
  --events-jsonl .tmp/lexicon-publication-comparison/events-full.jsonl \
    .tmp/lexicon-publication-comparison/events-tail.jsonl \
  --collection-inventory-json .tmp/lexicon-publication-comparison/collection-inventory.json
```
