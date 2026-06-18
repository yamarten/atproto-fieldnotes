# Project Record Schema

この文書はregistry schema version 2のproject保存モデルと、各データの意味を定める正本です。調査pipelineの責務は[investigation-model.md](investigation-model.md)、具体的な判定規則は[registry-internals.md](registry-internals.md)を参照してください。

## 設計意図

registryの一次目的は、atproto関連projectを継続的に発見し、紹介・追跡・再調査に必要な情報を保守的に保持することです。

- projectの現在状態と、外部sourceから観測したidentifierを分離する
- collector由来のrelation evidenceを、後から再評価できる形で残す
- block/deferなどoperator判断を自動評価より優先する
- 不確かな値を紹介用profileへ無理に昇格させない
- 一覧表示に必要な値と、調査・運用上の詳細を分ける

app lexiconとの完全互換や即時exportは主目的ではありません。`profile_json.app`は将来再利用しやすい確定情報の置き場ですが、不確かな情報で埋めるより空のままにします。

この文書では、project保存モデルを構成するrelational tableとcolumn、および人が読む`profile_json`を保存契約として明示します。主に機械が読み書きする`state_json`は、内部fieldを網羅せず、責務、正本との境界、保存上のinvariantだけを定めます。projectに属さないsync stateなどのregistry-wide tableは[registry-internals.md](registry-internals.md)の対象です。

## Schema Version

現行schemaはversion 2です。`registry_meta.schema_version=2`を持たない旧DBは通常起動では読み込みません。

```sh
project-registry migrate-db data/survey.db
```

移行成功時は旧DBを同じdirectoryのtimestamp付きbackupへ移し、新DBを元pathへ置きます。失敗時は旧DBを変更しません。
`migrate-db`は別processとの排他lockを取得しないため、Web、enrich、他のCLI writerを停止したoffline状態で実行します。すでにschema v2のDBはtrust policy適用世代に関係なく`already_current`になるため、旧方式v2を現行migration済みDBとして扱わないでください。

v1からの移行では`v1-quarantine-1` trust policyを適用します。旧DBでconfirmed相当だったidentifierでも、現在の根拠で再現できないものは`project_identifiers`のcandidate rowへ降格し、`metadata_json`へ旧field、旧classification、policy、降格理由を残します。`manual_seed`はoperator入力としてconfirmed rowにできますが、同じ値であることだけをownershipやsource repositoryのconfirmed根拠にはしません。`confirm_identifier` decisionが残るoperator確認済みidentifier、または現行ruleで再構成できる根拠だけをconfirmedとして維持します。

移行reportは旧confirmed件数、維持/降格/除外件数、primary scalarを空にしてcandidate rowへ移した件数、active block衝突、根拠不明legacy confirmed、normalized rowの無変更roundtrip diffを含みます。active block衝突、根拠不明legacy confirmed、roundtrip diffがある場合は移行失敗として扱います。

## 保存責務

### `projects`

projectの現在状態を表すmaterialized recordです。

- `project_key`: `proj:...`形式の内部識別子
- `name`: 表示用の代表名
- `website_url`: 代表公開URL。未確定なら空
- `service_status`: 公開・到達状態
- `tracking_state`: 継続調査上の扱い
- `introduced_url`: 紹介済みであることの根拠URL。service identityには使わない
- `profile_json`: 紹介・表示に使う編集済み情報
- `state_json`: 評価結果、候補、調査補助、queue状態
- `first_seen_at`, `last_seen_at`, `last_enriched_at`: registry内での観測・調査時刻

scalar columnは一覧、filter、運用判定で頻繁に使う値だけに限定します。

### `project_key_aliases`

mergeで削除したsource project keyを、存続するcanonical project keyへ解決するregistry-wide tableです。

- `alias_project_key`: merge前のsource project key
- `canonical_project_key`: 現在存続するproject key
- `reason`: 現状は`merge`
- `created_at`: alias作成時刻

alias chainはmerge時にcanonical keyへ平坦化します。旧keyをCLI/Webへ入力した場合はcanonical projectへ
解決し、canonical projectのInvestigation一覧にはalias元keyで保存されたrunも含めます。
`investigation_runs.project_key`とrun内snapshot/bundleは、調査当時のidentityを残すため書き換えません。

このtableはschema version 2へのadditive tableです。既存v2 DBは通常readerでopenした時に
idempotentに作成でき、v1からのmigrationをやり直す必要はありません。

### `profile_json`

人が読むproject profileとして比較的安定した情報です。

`core`:

- `tagline`: 短い紹介文
- `tagline_is_provisional`: 暫定文であることを示すflag
- `summary`: projectの説明
- `notes`: operatorが保持する編集メモ

`app`:

- `tags`: 確定した分類tag
- `platforms`: 対応platform
- `links`: canonical identifierとconfirmed resourceから再構築できる公開link view

`links`で現在使うroleは`website`, `webApp`, `sourceCode`, `docs`, `support`, `status`, `privacyPolicy`, `termsOfService`です。identifierの正本ではなく表示・将来export用のviewなので、`cleanup-app-links`で再構築できます。

### `state_json`

機械による評価・調査のcurrent stateを保持します。内部は概ね次の責務に分かれます。

- `ops`: classification、review/promotion/merge signal、評価score、errorなど、現在の運用・評価結果
- `evidence`: 候補値、bounded observation、調査logなど、project単位で参照する補助材料

保存上のinvariant:

- identifier、typed relation、operator decisionの正本を含めない
- raw EvidenceBundleやsource別実行履歴の正本にしない。それらは`investigation_runs` / `collector_runs`へ保存する
- 候補や機械評価を`profile_json`の確定値と同じ意味で扱わない
- fieldの欠落をconfirmedな否定として扱わない。未調査、partial failure、証拠なしは調査履歴と評価detailで区別する

代表例として、service statusの根拠、atproto寄与度、review signal、resource page observation、summary候補などを保持します。内部fieldはcollectorやheuristicの変更に合わせて変えられる実装detailであり、この文書では網羅的なpayload APIとして固定しません。具体的な現行判定は[registry-internals.md](registry-internals.md)を参照してください。

## `project_identifiers`

projectに結び付くNSID、service URL、repo URL、account、DID、authority domainを順序付きで保持します。

- `project_key`: 所属project
- `ordinal`: project内の保存順
- `kind`: identifierの種類
- `value`: 正規化済みの値
- `role`: project内での役割
- `classification`: `confirmed | candidate | derived | heuristic`
- `is_primary`: 代表値か
- `provider`: GitHub、Tangled、Blueskyなどのprovider hint
- `metadata_json`: repo観測やaccount detailなど、そのidentifier固有の補助情報
- `observed_at`: 観測時刻

主なroleと意味:

- NSID
  - `primary_nsid`: runtimeで代表として使うowned NSID
  - `input_nsid`: seedや調査入力として得たが、ownership確定前のNSID
  - `owned_nsid`: ownership評価を通過したNSID
  - `observed_lexicon`: project周辺で観測したlexicon
  - `referenced_nsid`: projectが利用・参照する外部NSID
  - `recorded_namespace`: collection観測から導出したnamespace
- service
  - `website`: 代表公開URL
  - `service`: confirmedまたはcandidateのservice surface
- repository
  - `source_repository`: projectのsource候補またはconfirmed repo
  - `source_repository_record`: metadata、README、treeなどを取得したrepo observation
- identity
  - `developer_account`: 開発者・運営者account
  - `developer_did`: accountに対応するDID
  - `brand_did`: project自身のDID
  - `project_account`: project名義のaccount候補
  - `authority_domain`: NSIDやidentity pathから導出したdomain
- `manual_seed`: operatorが明示登録した入力。自動収集値より強く扱う

load時は専用runtime codecがnormalized child tableを読み、collector/evaluationが使うcanonical project dictへhydrateします。このdictは保存正本ではなく、既存の調査ロジックへ渡す完全なruntime viewです。

既存projectを保存する時は、identifier、relation、decisionを読み込んだ完全なruntime viewであることを要求します。検索結果や部分的なdictをそのまま保存するとchild tableを全消去し得るため、partial dictからの更新は拒否します。runtime viewを識別する内部markerは`profile_json`、`state_json`、Web payload、investigation snapshotへ保存しません。

runtime codecは、loadして無変更saveした場合にidentifier、relation、decision rowのclassification、role、primary、provider、metadata、observed_atを維持します。candidateのprimary NSID、candidateのowned NSID、candidate developer DIDなど、flat runtime fieldだけでは表現できないrowは内部runtime stateから保存します。candidate primaryを`projects.website_url`、`primary_nsid`、`source_code_url`などのconfirmed用flat fieldへhydrateしません。

削除済みalias fieldも生成・保存しません。

## `project_relations`

「何が何をどのように保証・利用しているか」を保持するtyped relation evidenceです。

- `project_key`: 所属project
- `ordinal`: project内の保存順
- `subject_type`, `subject_value`
- `predicate`
- `object_type`, `object_value`
- `classification`
- `source`: GitHub、service page、OAuth metadataなどの根拠source
- `locator`: URLやrepo path
- `method`: lexicon JSON、source scan、HTTP redirectなどの抽出方法
- `context_json`: operation、score、bounded extractなどの補助情報
- `observed_at`

主要predicate:

- `defines_schema`: repoやaccountがschemaを定義する
- `uses_collection`: read/write/queryなどでcollectionを利用する
- `mentions_collection`: docs、example、testなどで言及するだけ
- `official_account`: siteやrepoがaccountを公式として示す
- `operates_service`: identityがserviceを運営する
- `source_repository`: app/siteとrepoがsource関係にある
- `requests_permission`: OAuth metadataがrepo permissionを要求する
- `redirects_to`: URL間の実観測redirect/canonical関係

同じproject内でsubject、predicate、object、source、locator、methodが同じrelationは重複保存しません。relationの意味とownershipへの使い方は[investigation-model.md](investigation-model.md)を参照してください。

重複relationを統合する場合は、より強いclassification、より新しい`observed_at`、両方のcontextを残します。DBのunique制約とruntime dedupeは同じkeyを使います。

## `project_decisions`

operator判断を履歴として保持し、自動収集・評価より優先します。

- `project_key`: 所属project
- `ordinal`: project内の保存順
- `kind`: decisionの種類
- `target_type`, `target_value`: decisionの対象。project全体へのdecisionでは空
- `note`: operatorが記録した理由
- `created_at`, `expires_at`, `cleared_at`: 作成、失効、解除時刻
- `metadata_json`: decision固有の補助情報

現在の`kind`:

- `block_identifier`: 誤ったidentifierの再付着を恒久的に抑止する
- `defer_identifier`: identifierの確定を期限付きで保留する
- `confirm_identifier`: operatorがcandidate identifierを明示確認した履歴
- `defer_promotion`: promotion queueへの再出現を期限付きで保留する
- `dismiss_review`: 特定review signalをdismissする
- `dismiss_review_target`: fingerprintで特定したrelationまたはcollector failureをdismissする
- `defer_review_target`: fingerprintで特定したrelationまたはcollector failureを期限付きで保留する
- `separate_project`: 2 projectを別物としてmerge候補から除外する

各rowはtarget、note、created/expires/cleared timestamp、追加metadataを持ちます。clear後も履歴を残すdecisionがあります。`confirm_identifier`はmigrationや再評価で自動判定より優先するoperator assertionとして扱います。

## Canonical View

- primary website: `projects.website_url`
- confirmed/candidate service: `project_identifiers(kind=service_url)`のclassification
- primary source repo: repo identifierの`is_primary=1`
- confirmed/candidate repo: `project_identifiers(kind=repo_url)`のclassification
- supporting page: `state_json.evidence.resource_url_records`
- introduction state: `introduced_url`が空でないか

削除済みruntime field:

- `website_url_candidates`
- `github_repo_urls`
- `tangled_repo_urls`
- `resource_urls`
- `introduced`

provider別repo一覧とresource URL一覧はcanonical dataからhelperで導出します。

## Status Values

`tracking_state`:

- `active`: 通常の継続調査対象
- `watchlist`: 情報不足、未公開などで追跡を続ける
- `stable`: 安定しており通常の全件enrich頻度を下げる
- `stalled`: 立ち消え気味だがarchivedとは確定しない
- `out_of_scope`: atprotoとの関係はあるが現在の紹介・重点調査対象外
- `archived`: 開発終了または追跡完了
- `ignore`: 無関係・誤登録・再評価価値の低いnoise

`service_status`:

- `unknown`: まだ判断材料がない
- `unverified`: 調査したが到達性や公開状態を確定できない
- `none`: service surfaceを持たない
- `dead`: 消滅、parked、404/410など
- `live`: 一般利用可能なsurfaceを確認した
- `private`: 認証・権限制限などで一般公開性が低い
- `unreleased`: coming soonなど公開前
- `beta`: beta公開
- `invite_only`: 招待・waitlist制

`service_status_confidence`は`none | low | medium | high`です。status値と根拠の強さを別に保持します。

## Investigation History

`investigation_runs`と`collector_runs`はcurrent stateとは別の履歴・replay用tableであり、project rowの代替ではありません。

`investigation_runs`:

- `run_id`, `project_key`
- `started_at`, `completed_at`
- `evidence_schema_version`, `evaluation_version`
- `input_project_json`, `output_project_json`
- `evaluation_json`, `bundle_json`

`collector_runs`:

- `run_id`, `ordinal`
- `source`, `operation`, `status`, `observed_at`
- `result_json`, `records_json`, `failures_json`

JSON payloadの意味とreplay時の扱いは[investigation-model.md](investigation-model.md)を正本とします。

### Merge / Splitの履歴

mergeはtarget保存、source alias作成、第三projectの`separate_project`参照更新、source削除を
1 transactionで行います。途中で失敗した場合は全変更をrollbackします。source keyで保存済みの
investigation runはalias familyとしてtargetのrun一覧へ表示します。

split前のrunは、どのidentifierに属する証拠かを自動分配できないため元projectに残します。
split後の新しいrunだけを新project keyへ保存します。run snapshotを後から書き換えて
現在状態と見せることはしません。

### Retention Policy

増加量の大きい`investigation_runs` / `collector_runs`だけを定期削除の対象にします。既定policyは次の両方を保持します。

- 完了から90日以内の全run
- 日数にかかわらずprojectごとの最新3 run

削除は自動実行せず、`prune-investigation-history`のdry-runを確認してから`--execute`で行います。`investigation_runs`を削除すると対応する`collector_runs`もforeign-key cascadeで削除します。SQLiteはrow削除だけではfile sizeを縮めないため、filesystemへ空き領域を返す必要がある場合だけ、他のWeb/CLI/enrich processを止めて`--execute --vacuum`を使います。

次は自動削除しません。

- `project_decisions`: operator assertionの正本
- `project_identifiers`のmigration provenance: v1 migration時だけ増える固定量で、通常運用では増加し続けない。安定運用後は`prune-migration-provenance --execute`でidentifier rowを残したまま移行専用metadataだけを削除できる
- current project state: 履歴ではなく現在状態の正本

Web job eventはprocess内保持で再起動時に消えます。`sync_state`は最新watermarkだけを保持し、実行履歴として蓄積しません。enrich checkpoint fileの自動削除は未接続です。

## Non-goals

- app lexiconとの完全互換
- raw HTMLやREADME全文の永続化
- 旧schemaの通常読み込み互換
- Web UI向けpayload互換
