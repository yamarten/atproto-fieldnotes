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

実行例:

```bash
bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py init \
  --sidecar-db .tmp/lexicon-publications/catalog.db

bin/_project-registry-python lexicon-publication-catalog scripts/lexicon_publication_catalog.py update \
  --sidecar-db .tmp/lexicon-publications/catalog.db \
  --registry-db data/survey.db \
  --max-events 1000 --max-seconds 120 --verify-limit 10 \
  --output .tmp/lexicon-publications/evaluation.json
```

途中終了したrunは同じcommandを再実行します。sidecarのpending sealed tipと`afterSeq`から再開し、新しいtipを
混ぜません。offline fixtureまたは保存済みsnapshot segmentを使う場合は、順番に`--events-jsonl`へ指定します。
`report`はnetworkへ接続せず現在stateを評価し、`--advance`指定時だけ差分基準revisionを進めます。
space宣言の活動比較はregistry内の保存済み`collection_observations`をread-onlyで使い、より新しいinventoryを
照合する場合は`--collection-inventory-json`を追加して集合を併合します。inventoryはsidecarやregistryへ保存しません。

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
