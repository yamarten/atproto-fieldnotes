# Lexicon publication sidecar catalog

この文書は、Jetstreamの`com.atproto.lexicon.schema` publicationを発見用途で保持するsidecar SQLiteと、
Codex向け評価JSONの保存・更新契約を定めます。project registryへの自動反映、ownership確定、promotion変更は
このlaneの責務ではありません。

## 境界と入口

developer/operator入口は`scripts/lexicon_publication_catalog.py`です。publicな`project-registry` command、Web UI、
background jobには接続しません。sidecar pathは毎回明示し、`survey.db`と同じpathは拒否します。registry照合は
SQLite URIの`mode=ro`と`PRAGMA query_only=ON`を併用します。
既存の別用途tableを持つSQLiteへ`init`することも拒否します。
既定sourceは現行Jetstream v2 archive host `https://jetstream.us-west.bsky.network`で、別hostは
`--service`で明示します。

初期運用の恒久sidecarは`data/lexicon-publication-catalog.db`へ置きます。これはCodexが評価時に使う内部DBで、
`survey.db`のbackup、Webの実運用DB、`.tmp`の検証用DBとは別物です。pathを設定やpublic commandの既定値にせず、
developer scriptの実行ごとに明示します。将来`survey.db`へ統合する場合はschemaとreaderを置換し、このpathを維持する
互換layerは作りません。

実行例:

```bash
bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py init \
  --sidecar-db .tmp/lexicon-publications/catalog.db

bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py update \
  --sidecar-db data/lexicon-publication-catalog.db \
  --registry-db data/survey.db \
  --max-events 20000 --max-seconds 300 --verify-limit 0 \
  --output .tmp/lexicon-publications/evaluation.json

bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py verify \
  --sidecar-db data/lexicon-publication-catalog.db \
  --registry-db data/survey.db \
  --schema-id example.app.record --verify-limit 10 \
  --output .tmp/lexicon-publications/verification.json
```

途中終了したrunは同じcommandを再実行します。sidecarのpending sealed tipと`afterSeq`から再開し、新しいtipを
混ぜません。offline fixtureまたは保存済みsnapshot segmentを使う場合は、順番に`--events-jsonl`へ指定します。
`report`はnetworkへ接続せず現在stateを評価し、`--advance`指定時だけ差分基準revisionを進めます。
space宣言の活動比較はregistry内の保存済み`collection_observations`をread-onlyで使い、より新しいinventoryを
照合する場合は`--collection-inventory-json`を追加して集合を併合します。inventoryはsidecarやregistryへ保存しません。

`verify`は評価対象をrepeatableな`--schema-id`または`--did`で明示し、期限切れ、content変更後、または未検証の
current publicationだけを検証します。両方を指定した場合はschemaとDIDの積集合です。全catalogを辞書順に少しずつ
検証することは初期coverageにも再検証期限にも適さないため、通常の`update`は`--verify-limit 0`とし、Codexが候補を
評価する時に対象限定`verify`を使います。

## 初期運用

- catalog更新は週1回を目安に、Codexがこのlaneを評価する時だけ手動実行します。前回の成功から7日未満で、評価対象の
  publication変更も疑われない場合は再取得しません。scheduler、Web、background jobには接続しません。
- 成功検証の7日TTLは同じ候補を評価する際の再取得抑制に使います。失敗の1日TTLは翌日以降に対象限定で再試行できる
  境界であり、失敗全件を日次で再走査する指示ではありません。
- 2026-09-05のbootstrapは約1か月分10,232 eventを2 segment、約8分で処理しました。週次増分はこの初回量より十分
  小さいと見込めますが、必ず20,000 event / 300秒のboundを維持し、完了しなければ同じsealed tipを再開します。
- sidecar本体はwatermark、tombstone、verification期限を含む運用stateなので継続保持します。raw event logと評価JSON、
  collection inventoryは`.tmp/`だけに置き、評価の記録へ必要な集計を転記後に削除できます。
- sidecarはJetstreamから再構築可能なため定期backupは作りません。schema変更や破壊的maintenanceを行う時だけSQLiteの
  online backupを`data/backups/`へ1世代作り、検証後に旧世代を削除します。`cleanup-workspace-temp`は`.tmp`だけが対象で、
  恒久sidecarを削除しません。

## SQLite schema version 1

sidecarはproject tableへの外部キーやproject schemaへの暗黙依存を持ちません。

- `catalog_metadata`: schema version、適用済みsealed watermark、catalog/report revision、処理中sealed tipとresume位置
- `pending_publication_events`: 未完了sealed snapshotのbounded staging。snapshot完了後に全行を削除する
- `publication_catalog`: `(did, rkey)`ごとのcurrent/tombstone。event sequence、revision、time、schema shape、
  record hash、最大64 KiBのbounded record material、row変更revisionを保持する
- `publication_verifications`: exact DNSとcurrent record再取得を別fieldで保持する。理由、観測時刻、次回期限、
  retry可否、検証対象content hashも保持する

`publication_catalog`はdelete後もtombstoneを残すため、削除差分とstale event拒否に必要な最後のsequenceを失いません。
schema ID単位のmirror groupingはreport時のprojectionで、publisher DIDをcatalogから削除またはcanonical化しません。
raw secret、API key、無制限event logは保存しません。

## sealed snapshotとtransaction

初回segmentでplannerが返した`sealedTipSeq`をpending stateへ固定します。後続segmentは直前の
`lastEventSeq`をexclusive `afterSeq`にし、同じtipだけを受け入れます。gap、overlap、別tip、範囲外event、
summaryとevent最大sequenceの不一致、同じkeyの衝突するduplicateは拒否します。

time/event boundで`truncated=true`になったsegmentはstagingだけをcommitし、watermarkとcurrent catalogを変更しません。
最終segmentを受け取ると、staging全eventをsequence順に一transactionでcurrent/tombstoneへ反映し、成功した時だけ
watermarkをsealed tipへ進め、stagingを消します。DID+rkeyの保存済みsequence以下のstale/duplicate eventは無視します。
最終transactionが失敗した場合はcatalog、watermark、staging消去をまとめてrollbackします。

## publisher検証

検証対象は、未検証、record hash更新後、または再検証期限到来のvalid current publicationだけです。1 runの
`--verify-limit`でrequest数を限定します。既存のLexicon解決部品とHTTP transportを使い、次を別々に記録します。

1. schema IDのexact authorityに対する`_lexicon` TXTのDIDがevent DIDと一致するか
2. 一致したDID documentのPDSから同じ`com.atproto.lexicon.schema/<rkey>`を再取得でき、URI、schema ID、
   event record hashが一致するか

両方が期限内で成功したrowだけを`verified_publisher_candidates`へ出します。DNS mismatch、DNS取得失敗、
DID document/PDS/record取得失敗、record/URI/hash不一致は理由とretry状態を保持します。既定の再検証期限は成功7日、
失敗1日です。DNS成功後にHTTPが失敗した場合も`dns_same_did=true`を失いません。

## 評価JSON contract version 1

JSON keyと配列順序は安定させ、配列はschema ID、DID、rkey等の決定的な順序で出します。主な区分は次の通りです。

- `changes.new_schemas` / `updated_schemas` / `deleted` / `verification_changed`: 指定revision以後の差分
- `verified_publisher_candidates`: DNSとcurrent recordを両方確認したpublication
- `existing_project_unique_candidates`: exact schema/NSIDを優先し、なければauthority groupがregistryの1 projectだけに
  対応するverified候補。照合根拠を別fieldで示し、自動帰属はしない
- `unregistered_authority_groups`: registryにauthority groupがないverified候補。単一DIDが2 schema以上をpublishする場合は
  `independent_candidate=true`を付ける
- `space_declared_collections`: space宣言とregistryのcollection活動観測を`activity_observed`で分離した一覧
- `space_declared_collections_without_activity`: 宣言されたが活動未観測のcollection
- `mirror_or_catalog_only`: publisher未検証のcatalog観測。複数publisher schemaでは未検証mirrorをここへ出し、
  verified候補と同じDID+rkeyを重複させない。verified側にもschemaのpublisher数を付けてambiguityを残す
- `deleted_or_verification_failed_or_expired`: delete、検証失敗、期限切れ

この分類は候補提示用です。space宣言から`owned_nsid`や`collection_observations`を書かず、projectとの関連候補から
project identifier、relation、decisionを作りません。採用する場合はCodexまたはoperatorが既存の明示操作とtrust policyを
別途使います。

初期運用では次の接続境界を使います。

- `verified_publisher_candidates`は調査入力です。DNSとcurrent recordはpublisherを確認しますが、publisher DIDの
  project roleやproject境界までは確認しません。
- `existing_project_unique_candidates`はproject detailと既存identifierを人が照合します。`definition_types`に`record`を
  含み、そのprojectへのownershipも確認できたNSIDだけ、既存の`add-seed --type nsid --project-key ...`へ渡せます。
  exact一致は既登録なら無変更でよく、authority groupだけの一致は自動採用しません。
- `unregistered_authority_groups`は新規調査候補です。`independent_candidate=true`も自動project作成条件ではありません。
  独立projectと確認できたrecord NSIDだけ、operatorが既存`add-seed`を明示実行します。
- query、procedure、subscription、permission-set、spaceは現行`owned_nsid`のrecord collection表現へ押し込みません。
  `space_declared_collections`はinventoryとの比較と調査候補抽出だけに使い、宣言を活動・ownershipへ変換しません。
- `mirror_or_catalog_only`と`deleted_or_verification_failed_or_expired`は採用操作へ渡しません。再評価が必要なら期限後に
  対象限定`verify`を行います。
- sidecar結果からpromotionを直接変更しません。record NSIDを明示採用したprojectは通常`enrich`で既存の
  DNS/schema evidenceを再観測し、その後に既存`lexicon_publisher_confirmed` signalを評価します。
