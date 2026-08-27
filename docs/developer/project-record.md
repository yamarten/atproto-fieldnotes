# Project Record Schema

この文書はregistry schema version 3のproject保存モデルと、各データの意味を定める開発者向けの正本です。帰属と信頼の判断モデルは[登録プロジェクトへの帰属と信頼](project-attribution-and-trust.md)、調査pipelineの責務は[investigation-model.md](investigation-model.md)、現行コードの処理経路は[registry-internals.md](registry-internals.md)を参照してください。

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

実装上の現行schemaはversion 3です。通常起動は`registry_meta.schema_version=3`だけを読み、version 2を暗黙移行しません。version 2からの更新はreview済みmanifestを使って新しい複製DBへmaterializeし、検証後にDBを切り替える明示的な運用です。正本DBは2026-08-14にreview済みmanifestを適用したversion 3へ切り替えました。

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

alias chainはmerge時にcanonical keyへ平坦化します。旧keyをCLI/Webへ入力した場合はcanonical projectへ解決し、canonical projectのInvestigation一覧にはalias元keyで保存されたrunも含めます。
`investigation_runs.project_key`とrun内snapshot/bundleは、調査当時のidentityを残すため書き換えません。

このtableはschema version 2で追加され、version 3でも継続利用します。

### `profile_json`

人が読むproject profileとして比較的安定した情報です。

`core`:

- `tagline`: 短い紹介文
- `tagline_is_provisional`: 暫定文であることを示すflag
- `summary`: projectの説明
- `notes`: operatorが保持する人間向け編集メモ。merge履歴、split履歴、sync/import由来などの機械生成メモは入れない

`app`:

- `tags`: 確定した分類tag
- `platforms`: 対応platform
- `links`: canonical identifierとconfirmed resourceから再構築できる公開link view

`links`で現在使う主なroleは`website`, `webApp`, `repository`, `docs`, `support`, `status`, `privacyPolicy`, `termsOfService`, `webManifest`, `changelog`, `appStore`, `playStore`, `fdroid`, `distribution`です。roleはopen unionとして扱い、未知値も保持します。identifierの正本ではなく表示・将来export用のviewなので、project保存時に既存値を引き継がず正本から再構築します。

### Category

`category`は成熟度、稼働状態、発見経路ではなく、主な利用者と利用形態を表します。現行値は
`project`, `lexicon`, `library`, `infra_tool`, `personal_tool`, `name_only`, `reject`です。

- `project`
  - 一般利用者が直接利用するservice、app、client、bot、利用者向け拡張機能
- `lexicon`
  - 第三者による実装または相互運用を意図し、共有規約として公開・文書化されたlexicon
  - NSID、schema sample、record sample、namespace名だけでは該当しない
- `library`
  - 開発者が他のsoftwareへ組み込むSDK、package、framework、adapter、開発用plugin
- `infra_tool`
  - projectやserviceの背後で動き、その提供・保守・運用を支える実行物またはservice
  - ATProtoのprotocol、data、network、repository、運用環境自体を操作・検査・保存する実行物またはservice
  - online infrastructureに限定せず、`goat`、comail、AT Backup、Airglowのような開発・調査・backup用toolを含む
- `personal_tool`
  - 想定利用者が作者本人、特定PDSのmember、または固定された小集団に限定されるtoolやservice
  - 利用者の少なさ、単機能、特定の趣味への特化だけでは該当しない
- `name_only`
  - 記録する価値はあるが、対象の実体または主な役割を判断できない場合の暫定値
- `reject`
  - 登録しようとしている対象そのものが調査範囲外、無関係、または候補として成立しない

CLIやpluginなどの配布形式だけでは決めません。一般利用者が直接使うCLIや拡張機能は`project`、開発者が
依存packageとしてcodeへ組み込むpluginは`library`、既存serviceや外部toolへ導入してATProto連携を追加する
pluginは`infra_tool`です。ATProto自体を扱うCLIや運用componentも`infra_tool`です。既存projectに別物の
URLが混入した場合はidentifier関係の欠陥であり、projectを`reject`にしません。

停止、削除、archiveはcategoryを変える理由にしません。過去のcategoryを確認できる場合は維持し、削除後に
初めて発見して元の役割を復元できない古い対象では`name_only`を許容します。

複数の説明が当てはまる場合は、対象外なら`reject`、役割不明なら`name_only`、固定利用者なら
`personal_tool`、文書化された共有schemaなら`lexicon`、組み込む部品なら`library`、projectの背後で動くか
ATProto自体を扱う実行物なら`infra_tool`、一般利用者が直接使うものなら`project`の順で確認します。

`auth_only`はcategoryではありません。認証packageは`library`、認証proxyや外部toolへ導入する認証pluginは
`infra_tool`に分類します。`atproto_auth_only=true`は、独立したappまたはserviceがATProtoをaccountの
sign-inまたはOAuth認証にだけ利用し、records、schema、repository、protocol dataを扱わない場合に保存します。
ATProto認証機能そのものを実装・提供するlibrary、plugin、proxy、reference codeには付けません。それらの役割は
categoryで表します。強いprotocol利用の根拠があれば、`atproto_contribution_level`はそちらを優先します。

旧`category=auth_only`の正本DB rowは個別移行済みです。読み取り互換は持たず、LLM出力のcategoryとしても
受け付けません。

### `state_json`

機械による評価・調査のcurrent stateを保持します。内部は概ね次の責務に分かれます。

- `ops`: classification、review/promotion/merge signal、評価score、errorなど、現在の運用・評価結果
- `evidence`: 候補値、bounded observation、調査log、`trust_graph`など、project単位で参照する補助材料

保存上のinvariant:

- identifier、typed relation、operator decisionの正本を含めない
- raw EvidenceBundleやsource別実行履歴の正本にしない。それらは`investigation_runs` / `collector_runs`へ保存する
- 候補や機械評価を`profile_json`の確定値と同じ意味で扱わない
- fieldの欠落をconfirmedな否定として扱わない。未調査、partial failure、証拠なしは調査履歴と評価detailで区別する

代表例として、service statusの根拠、atproto寄与度、review signal、resource page observation、trust graph、summary候補などを保持します。内部fieldはcollectorやheuristicの変更に合わせて変えられる実装detailであり、この文書では網羅的なpayload APIとして固定しません。具体的な現行判定は[registry-internals.md](registry-internals.md)を参照してください。

`system_notes` は、merge/split/importなどの機械生成された運用履歴を保持する補助fieldです。人間が編集するprofile `notes` とは保存時点で分離し、紹介用profileやoperatorの自由記述には混ぜません。読込時に`notes`本文をpattern判定して`system_notes`へ移す互換処理は行いません。

## `project_identifiers`

projectの現行構成要素と、構成要素候補であるNSID、service URL、project site URL、repo URL、account、handle、DIDを順序付きで保持します。

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
  - `owned_nsid`: projectが所有するcollection。`confirmed`はsource assignmentまたは採用判断で確定、`candidate`は所有候補としてレビュー対象
- service
  - `service`: confirmedまたはcandidateのservice surface
- project site
  - `project_site`: projectを説明・提示する代表的なWeb presence。`site_url` kindで保持し、利用・操作する`service`や個別の補助pageとは分ける
- repository
  - `project_repository`: projectに属する公式repositoryの候補またはconfirmed値。source codeに限らず、documentation、specification、Lexicon、issue、release等を主目的とするrepositoryも含む
- identity
  - `developer_account`: 開発者・運営者accountの保存・操作単位。値は同じaccount recordのDIDまたはhandleで、Web表示では対応するhandle/DID成分と1つのidentity groupに畳む。trust graph nodeにはしない
  - `developer_handle`: `developer_account` recordから展開したhandle成分。handle検索、domain/handle関係、表示上の補足に使う
  - `developer_did`: `developer_account` recordから展開したDID成分。DID/schema record/Tangled recordなどDID単位の関係評価に使う
  - `brand_did`: project自身のDID
  - `project_account`: project名義のaccount候補
  - `project_handle`: project名義のhandle候補
`input_nsid`, `observed_lexicon`, `referenced_nsid`, `recorded_namespace`, `seed`, `manual_seed`は移行済みの旧roleであり、現行コードは生成・読込・保存しません。観測・参照したNSIDはfactual identifierへ混ぜず、relation、schema sample、collection observationなど元の根拠に保存します。

`source_repository_record`はrepoを調査した観測記録であり、`state_json.evidence.source_repo_records`に保存します。`authority_domain`はconfirmed `owned_nsid`からruntimeで導出し、identifier rowとして保存しません。いずれもproject構成要素、exact attribution、trusted anchorにはしません。

現行の`add-seed` / Web `Add seed`は、NSID、service URL、repo URLをそれぞれconfirmed `owned_nsid` / `service` / `project_repository`へ投影し、`confirm_identifier` decisionを記録します。明示的にservice URLを追加し、Websiteが空の場合だけ、同じURLをWebsiteの初期値にも使います。candidate confirmや自動sourceではWebsiteを設定しません。`seed_type` / `seed_value` scalarは初期入力の由来・検索用でありidentifier roleやtrust anchorではありません。自動sourceの由来は`discovery_sources`、採用根拠はdecisionまたはsource assignmentとして分離します。

NSIDには代表値を置きません。runtime上の`resolved_nsids`はconfirmed、`input_nsids`はcandidateの`owned_nsid` viewです。NSIDのname部を含め大小文字を保持します。`project_site_urls`と`project_site_url_candidates`は`site_url` / `project_site`のruntime viewです。人間向けの代表公開URLは引き続き`projects.website_url`を正本とし、Website値だけからidentifier、classification、採用理由を導出しません。同じURLのconfirmed `service`がWebsiteを兼ねる場合は`project_site`を二重登録せず、独立した代表siteが構成要素として必要な場合だけ`project_site`を保存します。

load時は専用runtime codecがnormalized child tableを読み、collector/evaluationが使うcanonical project dictへhydrateします。このdictは保存正本ではなく、既存の調査ロジックへ渡す完全なruntime viewです。

既存projectを保存する時は、identifier、relation、decisionを読み込んだ完全なruntime viewであることを要求します。検索結果や部分的なdictをそのまま保存するとchild tableを全消去し得るため、partial dictからの更新は拒否します。runtime viewを識別する内部markerは`profile_json`、`state_json`、Web payload、investigation snapshotへ保存しません。

runtime codecは、loadして無変更saveした場合にidentifier、relation、decision、adoption rowのclassification、role、primary、provider、metadata、observed_at、採用来歴を維持します。candidateのowned NSIDやcandidate developer DIDなど、flat runtime fieldだけでは表現できないrowは内部runtime stateから保存します。

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

relationの`classification=confirmed`は、保存したpredicateの方向付き事実を直接観測または確定できたことを表します。subject/object identifierがproject構成要素としてconfirmedであることや、そのrelationを信頼経路へ無条件に利用できることは表しません。identifierの採用状態と、信頼経路への利用可否は別のpolicyで評価します。帰属、信頼、採用の責務境界は[登録プロジェクトへの帰属と信頼](project-attribution-and-trust.md)を参照してください。

主要predicate:

- `defines_schema`: accepted repoやaccountがaccepted schemaを定義する
- `uses_collection`: read/write/queryなどでcollectionを利用する
- `mentions_collection`: docs、example、test、unaccepted schema fileなどで言及するだけ
- `official_account`: siteやrepoがaccountを公式として示す
- `operates_service`: identityがserviceを運営する
- `repository_homepage`: repository metadataのWebsite欄がURLを直接示す。URLのtarget roleやproject帰属は含意しない
- `project_repository`: app/siteとrepoの間に公式project repositoryとしての直接参照がある
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
- `target_role`: 対象claimのrole。一意に決められない旧decisionでは空
- `note`: operatorが記録した理由
- `created_at`, `expires_at`, `cleared_at`: 作成、失効、解除時刻
- `metadata_json`: decision固有の補助情報

現在の`kind`:

- `block_identifier`: 誤ったidentifierの再付着を恒久的に抑止する
- `defer_identifier`: identifierの確定を期限付きで保留する
- `confirm_identifier`: operator入力または採用済みsource seedによるidentifier採用判断
- `defer_promotion`: promotion queueへの再出現を期限付きで保留する
- `lower_promotion_priority`: promotion候補を一覧に残したまま通常候補より後ろへ送る。理由は`note`に保存し、clear後は通常優先度へ戻る
- `dismiss_review`: 特定review signalをdismissする
- `dismiss_review_target`: fingerprintで特定したrelationまたはcollector failureをdismissする
- `defer_review_target`: fingerprintで特定したrelationまたはcollector failureを期限付きで保留する
- `separate_project`: 2 projectを別物としてmerge候補から除外する

各rowはtarget、note、created/expires/cleared timestamp、追加metadataを持ちます。clear後も履歴を残すdecisionがあります。`confirm_identifier`はmigrationや再評価で自動判定より優先する明示的な採用根拠として扱い、`source`でoperator入力とsource syncを区別します。

operatorがcandidateをconfirmすると、同じ`kind + value`を対象とするexactなblock/deferを解除して`confirm_identifier`を追加します。operatorがblock/deferすると、同じ対象のactiveなconfirmと採用理由を終了させます。現行mutationは同じ値を持つ全roleへ作用し、`target_role`は判断対象のroleを記録しますがrole単位のguardにはまだ使いません。古い矛盾したdecisionを併存させて優先順位を毎回推測せず、最新の明示的判断をactiveな状態として保存します。

## 非現行identifier、採用理由、移転、noise

`project_noncurrent_identifiers`はprojectとの正の関係を保つが現行構成要素ではないclaimを保持します。`standing`は過去に現行だった`former`と、開発・試験用の`non_production`です。exact検索には現行claimより低い優先度で使えますが、trusted anchorにはしません。同一`project_key + kind + value + role`を`project_identifiers`と同時に保持せず、現行へ再採用した時は非現行rowを削除します。

`project_identifier_adoptions`は、現行confirmed構成要素をいつ、どの入口とpolicyで採用したかを保持します。operator、bootstrap、trust policyを区別し、merge後も`origin_project_key`を維持します。これは外部事実そのものではなく採用来歴です。current confirmed claimと有効な採用理由の両方がある場合だけtrusted anchor候補になります。candidate、former、non-productionの採用理由はactiveにしません。

`project_identifier_transitions`は、renameやreplacementを外部根拠またはoperator reviewで確定した場合だけ旧claimから新claimへの関係を保持します。新旧identifierが同居するだけでは生成しません。

`registry_identifier_exclusions`はprojectへ帰属しない既知noiseを完全一致で抑止するregistry-wide tableです。例えば誤記NSIDやplaceholderをcollection syncで何度も再作成しないために使います。prefixやpatternには展開せず、project固有のblock/deferも置き換えません。

## Canonical View

- primary website: `projects.website_url`
- confirmed/candidate service: `project_identifiers(kind=service_url)`のclassification
- primary project repository: repo identifierの`is_primary=1`
- confirmed/candidate repo: `project_identifiers(kind=repo_url)`のclassification
- supporting page: `state_json.evidence.resource_url_records`
- introduction state: `introduced_url`が空でないか

provider別repo一覧とresource URL一覧はcanonical dataからhelperで導出します。

## Status Values

`tracking_state`:

- `active`: 通常の継続調査対象
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

4つのJSON列は論理上JSONですが、新規runでは`project-registry:zlib-json:v1` header付きのzlib BLOBとして
保存します。通常reader、Investigation viewer、replay、fixture/model evaluation export、storage maintenanceは
共通codecでdecodeします。圧縮前のTEXT rowも移行完了までは同じreaderで読めます。header不明、zlib/UTF-8/JSON
破損はrun IDと列名を含むdecode errorとして停止し、空payloadや部分的なrunへ置き換えません。

圧縮後の4列にはSQLite JSON function、`LIKE`、直接SQLによるfield監査を使えません。`run_id`、project key、時刻、
versionと`collector_runs` projectionは引き続きSQLで監査でき、JSON内容は通常readerまたは
`compress-investigation-history`のdecode auditを通して確認します。

`collector_runs`:

- `run_id`, `ordinal`
- `source`, `operation`, `status`, `observed_at`
- `result_json`, `records_json`, `failures_json`

新規runでは、record/failure本文の正本は`investigation_runs.bundle_json`だけです。`collector_runs`は
`source + operation`単位にまとめたstatus/count projectionで、複数結果のstatusは最も強いproblem状態、request/result
件数は合計を保持します。`result_json.evidence_storage = "bundle_json"`とrecord/failure countを持ち、
`records_json` / `failures_json`は空配列です。旧形式の列内payloadを読むfallbackは持たず、現行projectionだけを対象にします。

`projects.state_json`の`service_verification_results`は時系列履歴ではありません。`lane + requested_url`単位の
最新観測を最大32 surface保持するcurrent viewです。過去runは上記history tableで確認します。

`cleanup-storage-payloads`は既存project current view、run input/output snapshot、EvidenceBundle、collector
projectionへ同じbudgetを投影します。run自体やoperator decisionは削除せず、既定dry-runです。`--execute`では
payload rewriteを一transactionで行い、commit前にforeign key/integrity checkを通します。保存済みevaluationを
再計算するoperationではありません。

`compress-investigation-history`は既定dry-runで全history JSONをdecode・JSON検証し、論理bytes、現在の保存bytes、
圧縮後bytes、decode failureを報告します。`--execute`では未圧縮rowだけを1 transactionで書き換え、前後の
integrity/foreign-key checkを通します。SQLite fileを縮める必要がある場合だけ、別processを止めたうえで明示的に
`--vacuum`を併用します。

JSON payloadの意味とreplay時の扱いは[investigation-model.md](investigation-model.md)を正本とします。

新しいenrich runでは、`evaluation_json`の予約key `_run_metrics`へrun timingとLLM request metadataを保存します。
applicationで読む時はsemantic evaluationから`metrics`へ分離するため、replay diffやfixtureの
`expected_evaluation`には含めません。旧runにはこのkeyが無く、timingは未記録として扱います。

`state_json`の`llm_source.evidence`は、実際にtriageへ渡した24 KiB上限のevidenceです。縮約が発生した
場合だけ`evidence_budget`を含み、field別の観測件数と採用件数を保持します。prompt本文そのものは保存しません。

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
- current project state: 履歴ではなく現在状態の正本

Web job eventはprocess内保持で再起動時に消えます。`sync_state`は最新watermarkだけを保持し、実行履歴として蓄積しません。enrich checkpoint fileの自動削除は未接続です。

## Non-goals

- app lexiconとの完全互換
- raw HTMLやREADME全文の永続化
- 旧schemaの通常読み込み互換
- Web UI向けpayload互換
