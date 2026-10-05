# Lexicon公開情報

Lexicon公開情報は、`com.atproto.lexicon.schema`の公開recordから得たschema本文・型・発行元と、DNS委譲先およびPDSからの再取得による検証結果です。registry DBに保存し、project候補の発見、帰属の調査、詳細表示、検索に使います。この文書は、その取得・保存・検証と型別の扱いを定めます。

## 発見と帰属

登録するidentityは完全なNSIDです。`record / space / query / procedure / subscription / permission-set`と定義のみのLexiconで`nsid` kind・`owned_nsid` roleを共有し、schema型を観測として別に保持します。直属namespaceはNSID末尾のnameを除いたものです。同じアプリの複数schemaを別々の暫定projectにしないための照合単位であり、identityをnamespaceへ置き換えません。

照合は完全なNSID一致、同じ直属namespace、新規暫定projectの順です。一意に照合できたprojectへ候補を追加し、未知のnamespaceは暫定projectを作ります。複数projectに一致する場合は、既存projectに競合する観測と要確認理由を保存します。帰属判断の共通規則は[Sync時の既存projectへの取込](project-boundaries.md#sync時の既存projectへの取込を分ける)に従います。

有効な公開schemaは未検証でも調査候補になります。syncは所有を自動確定しません。検証済みの公開も帰属とは別で、通常enrichで[既存の採用規則](project-boundaries.md#承認済みlexiconと同じ発行元による信頼の補強)を満たした場合だけ確定します。未検証候補を他identifierの自動採用・mergeの信頼起点にしません。

spaceの`collections`は参照宣言です。projectには`mentions_collection` relation（method `space_collections`）として保存し、所有・実利用とは区別します。宣言だけから対象collectionの`owned_nsid`候補を作りません。対象自身の公開や活動が発見された時に別途照合します。mirrorはNSIDごとの表示でまとめ、不正schema・削除記録・処理途中eventから新projectを作りません。

## 保存と原子的な適用

registry DBに以下を保存します。projectの手動判断と公開元の状態は分離します。

| Table / field | 内容 |
| --- | --- |
| `publication_catalog` | DID+rkeyごとの現在record、本文hash、schema id/type、space宣言、問題点、tombstone、revision |
| `publication_verifications` | DNS委譲先、record再取得結果、検証した本文hash、検証日時・次回日時 |
| `pending_publication_events` | sealed snapshotの未完了segment |
| `catalog_metadata` | 適用済みwatermark、revision、処理途中のsealed tipと再開sequence |
| `collection_inventory_members` | 完全なcollection NSIDの一覧・取得時点・元資料 |
| `registry_meta` | inventoryの最新取得状態・取得時点、直近publication syncの件数 |
| `state_json.evidence.publication_schemas` | projectに帰属・照合された公開観測の投影 |
| `state_json.evidence.publication_review` | 発行根拠や帰属の変化についての要確認理由 |

Jetstream v2 snapshotは同一sealed tipまで揃ってから現在状態へ原子的に適用します。途中segmentではwatermarkとprojectを進めず、pending位置を次回へ引き継ぎます。gap、sealed tipの混在、不正event、取得中のcatalog競合を拒否します。適用失敗はrollbackし、直前の再開点を保持します。外部取得中にregistryのwrite lockを保持せず、projectの照合と登録は一つの短いtransactionで行います。

本文は最大64 KiBの資料を保持し、それを超える場合はhashと要約・truncated状態を残します。切り詰めた資料をrecord型と推定してsample取得しません。

## 検証と型別処理

検証はNSIDからDNS委譲先DIDを解決し、そのDID documentのPDSから現在のschema recordを取得します。mirrorを順番に検証しません。同じauthority内のDNSとDID documentをrun内で再利用し、authority間は最終検証時刻を使って巡回します。authority内では未検証・space・古い検証・変更revisionを使って選び、同じ一群による枠の独占を避けます。

成功にはDNS authority、DID document id、record URI、`$type`、整数`lexicon=1`、完全なschema id、型、本文hashの一致が必要です。spaceではcollection配列と各NSIDも検査します。署名付きrepository proofではありません。成功結果の有効期限は7日です。週次実行が期限直前の結果を取り逃さないよう、成功は期限の1日前から更新対象とし、失敗は1日後に再試行します。本文hashの変更は期限を待たず対象にします。実際の検証は各syncの処理上限内で行います。新しいauthorityの回答は兄弟schemaにも適用し、別のpublisherを示した場合に古い発行元を支持しません。

型は共通identityに付随する観測であり、samplingと信頼経路の適用条件に使います。record samplingはrecord型、範囲付きpublication policyは各Lexicon型を対象とします。spaceのcollection宣言は`mentions_collection`として参照関係を表します。型変更は要確認に残し、明示的なNSID採用で解除されるまで新しい自動採用の支持に使いません。

内容・型・publisher変更、不正化、削除、期限切れで古い検証を新しい支持に流用しません。既存identifier・手動採用は自動削除せず、`lexicon_publication_changed`の要レビュー理由を保存します。要レビューを非表示にした後でも、新しい変化があれば再表示します。詳細は現在のcatalogと期限を読み直して表示します。

## 公開活動との比較

`sync-collections --execute`で、処理上限に達していない完全なinventoryを保存します。project単位に集約したcollection observationは不在判定に使いません。完全なNSID一致があれば`observed`、取得済みの一覧になければ観測範囲内の`not_observed`です。未取得・取得失敗は`unknown`とし、最後の成功一覧を消しません。空一覧を不在の証拠にはしません。

活動比較の対象はrecordとspace宣言先collectionです。space等のschema自身は`not_applicable`です。未観測を非公開での実利用、利用されていないこと、宣言一覧の網羅性へ読み替えません。表示にはinventoryの取得時点を付けます。

## 通常導線と処理量

`config/sources.json`の`lexicon_publications`でsource URL、event数、時間、検証件数を設定します。取得は既定20,000 event / 300秒です。検証は開始時点の未検証・変更・更新時期に達したNSIDを全件対象とし、`verify_limit`は既定`null`（件数制限なし）、`verification_seconds`は3,600秒です。`verify_limit`に整数を指定した場合はその件数に制限し、0は検証を省略します。

外部取得は最大4並列、同じauthority内は直列です。各取得のtimeoutを残り時間に合わせ、時間切れ・取消し時は新規取得を停止して実行中の取得を回収します。取得中はDBのwrite transactionを保持せず、完了した結果から短いtransactionで保存します。取得開始時の本文hashと保存時のcatalogが一致しない結果や、保存済み検証より古い結果は適用しません。

処理量の目安は、2026-10-04取得のcatalogで5,880 NSID・1,386 authorityです。12 NSIDの逐次取得は合計14.6秒、4並列の16 NSID検証は8.2秒でした。これらの小規模sampleから全件一巡を約30〜50分と見積もり、1時間を既定の時間枠にしています。失敗先や遅い接続を含む全件実測ではなく、期限内の完了保証ではありません。catalogの日時分布は各recordの最終eventだけを持つため、定期的な増分件数の実測には使いません。

eventの取り込み時に形式を検査し、snapshotの適用後に発行元を外部検証します。event数とNSID数は一対一ではありません。検証待ちが残っていても有効なschemaはproject候補になり、取り込みは完了できます。検証の結果は`verification.status`、`attempted`、`verified`、`failed`、`remaining`、`stop_reason`で区別します。時間・件数上限で残った対象は次のsyncで再開し、更新・再試行の時期に達していない同じ本文は再取得しません。`remaining=0`は対象の試行完了を示し、全件の検証成功とは区別します。

通常の`sync_sources_then_enrich`はGitHub、collection inventory、Lexicon、Semble、Tangled、`enrich --all --new-only`の順です。`new_only`は`last_enriched_at`がないprojectを指します。既存projectにschemaが追加・変更されたことだけでは追加enrichせず、保存した観測を直ちに表示し、説明更新は通常の週次enrichか明示調査で行います。LLMへ渡す公開観測はprojectあたり最大12 schemaに制限します。

enrichは対象projectのNSIDについて、調査資料の充足状態に応じてDNS・PDSから取得・検証します。今回取得した結果と失敗を、project・調査履歴の保存と同じtransactionで`publication_verifications`にも反映します。catalogとの本文・発行元の照合と検証条件はsyncと共有し、観測時刻は元の取得時点を保持します。保存競合で適用されなかった調査や、途中でcatalogが変わった対象は検証台帳へ反映しません。後続syncはこの検証結果を使って取得対象を選びます。

CLIは`sync-lexicons`、Webは「外部情報の取り込み」のLexicon sourceを使います。dry-runも外部取得・検証を行いますが、catalogの小さな一時コピーで計算し、registryへ書き込みません。結果には新規・更新project数、競合、拒否、検証結果、残件を含めます。途中snapshotはjobのpartialとして表示し、再実行時にpending位置から続けます。

project詳細の「Lexicon公開情報」で型・発行元・宣言・活動比較を確認し、登録プロジェクトのLexicon filterでspace、record、非record、宣言先活動の未観測を絞り込めます。宣言は関係tabでも参照できます。

## 運用への切替

DBの対応versionと移行方法は[Schema Version](project-record.md#schema-version)を参照してください。初回の`sync-lexicons`は全公開状態を取得します。sidecarで収集したデータがある場合も同じ取得経路を使い、不要になったDBの削除は切替確認後の別操作とします。

正本移行・初回取込・設定切替・実運用Web再起動は、dry-runの件数と初回enrich量を提示して別途承認を得ます。通常syncの設定が有効なら新しいLexicon stepも含まれるため、コード更新だけで運用切替を済ませたとは扱いません。旧専用presetは読込み時に対象外となります。
