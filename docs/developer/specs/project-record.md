# Project Record Schema

registry schema version 4では、紹介・検索・要レビュー項目の確認に使う現在状態と、判断の根拠・履歴を分けて保存します。同じSQLite DBで、観測履歴の保持と現在のproject情報の更新をそれぞれ担います。

| 保存するもの | 役割 |
| --- | --- |
| `projects` | projectの現在状態。scalar、説明用の`profile_json`、調査・評価用の`state_json`を持つ |
| `project_identifiers` / `project_relations` | resourceの採用状態と、観測・確定した方向付きの関係 |
| `project_decisions` | 自動評価が尊重するoperatorの判断 |
| `investigation_runs` / `collector_runs` | project一件ごとの調査入力・根拠・評価・結果と、収集処理ごとの成否 |

保存処理は現在状態を正規化し、識別子・関係・判断などを個別のtableの行（normalized row）へ分けます。読み出すときは、それらの行から調査・評価処理用のproject dict（runtime view）を組み立てます。この復元をhydrate、読み出した内容を変更せず再保存する確認を無変更roundtripと呼びます。調査履歴は過去の入力と結果を再評価する材料、現在状態は一覧や詳細の表示元です。

## 設計意図

registryの一次目的は、atproto関連projectを継続的に発見し、紹介・追跡・再調査に必要な情報を保守的に保持することです。

- projectの現在状態と、外部sourceから観測したidentifierを分離する
- collector由来のrelation evidenceを、後から再評価できる形で残す
- block/deferなどoperator判断を自動評価より優先する
- 不確かな値を紹介用profileへ無理に昇格させない
- 一覧表示に必要な値と、調査・運用上の詳細を分ける

`profile_json.app`には、表示やexportへ再利用する確定情報を保持し、未確定のfieldは空にします。app lexiconとの変換条件は[App record export](app-record-export.md)で定めます。

## Schema Version

実装上の現行schemaはversion 4です。通常openはversion 4と変更検出用table/triggerを必須とし、欠落時に補修・暗黙移行しません。既存version 3は`project-registry --db-path PATH migrate-db-v4`で対象を確認し、`--execute`で明示移行します。更新番号の初期化、履歴の適用状態column追加、version更新は一transactionです。既存履歴は`applied`として扱い、過去に実際に消失した更新や保存の正しさを遡って認定するものではありません。正本DBへの移行は別承認・別作業とし、旧processを停止してから適用します。

## DB接続と初期化

SQLite接続は30秒のbusy timeoutとWAL modeを使います。Web background jobが無い時でも、detail表示、merge analysis、CLI操作、別Web requestが同じDBを開くため短いlock競合は起こり得ます。WALは通常のread/write競合を減らしますが、同時writeや長いtransactionを完全に排除するものではありません。Web server内のwrite操作は`mutation_lock`で直列化しますが、CLIや別processとの排他はSQLiteのlockとbusy timeoutに委ねます。

空のregistry DBの初期化は`project-registry init-db [--db-path PATH]`で明示的に行います。通常CLI、Web、background workerは既存のschema version 4 DBを必須とし、missing pathや未初期化fileから空DBを暗黙作成しません。旧versionのDBも通常openでは暗黙移行しません。新規schema初期化はschema versionの保存まで同じ接続内でcommitします。既存DBのopenではschema versionを同値upsertせず、table/triggerの存在確認だけを行います。open処理由来のwrite transactionを保持しません。

`repair-db`はschema v4 current stateの正規化と派生評価更新を行い、schema migrationには使いません。

CLI/Webの手動確認用の`dev-db`は、既存schema・project保存・調査履歴保存の入口を使って架空データを生成します。`registry_meta.dev_fixture`はこの入口で生成したDBのリセット許可を示すmarkerで、schema versionや運用データの移行状態とは別物です。保存先・排他・リセットの範囲は[共通ダミーDBの手順](../workflows/change-acceptance.md#cliwebの共通ダミーdb)を参照してください。

## 保存責務

### `projects`

調査結果と編集を反映したprojectの現在状態を、読み出して使える形に保存するrecord（materialized record）です。

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

マージで削除した元project（source）のkeyを、存続先project（target）の正規keyへ解決する、registry全体で使うtableです。存続先をcanonical projectと呼びます。

- `alias_project_key`: merge前のsource project key
- `canonical_project_key`: 現在存続するproject key
- `reason`: 現状は`merge`
- `created_at`: alias作成時刻

alias chainはmerge時にcanonical keyへ平坦化します。旧keyをCLI/Webへ入力した場合はcanonical projectへ解決し、canonical projectの`Investigation`一覧にはalias元keyで保存された調査実行記録も含めます。`investigation_runs.project_key`と調査実行記録内のsnapshot/bundleは、調査当時のprojectとの対応を残すため書き換えません。存続先と、そこへ解決される旧keyのまとまりをalias familyと呼びます。

`project_key_aliases`に存在するkeyは、`projects`へ再保存できません。保存境界で更新番号とaliasを同時に検査し、merge後の古いsource snapshotを`StaleProjectWriteError`で拒否します。alias先へのsnapshot付け替えは行いません。未反映の調査履歴は調査当時のsource keyのまま保持し、alias familyから参照できます。

### 更新番号と保存transaction

`project_revisions(project_key, revision)`はproject単位の変更検出値です。`projects`、identifier、relation、decision、noncurrent identifier、adoption、transitionのINSERT/UPDATE/DELETEでDB triggerが更新し、alias変更は元keyと存続先keyにも作用します。削除後も番号を保持し、同じkeyの再作成を新規保存として受け付けません。番号は編集回数ではなく、同値UPDATEや子行置換でも進みます。無関係なprojectの変更や調査履歴だけの追加は、そのprojectの番号を進めません。

fetch/listは短いread transactionでscalar・子行・更新番号を一貫して読み、runtime viewの内部fieldへ番号を保持します。読込み終了後にtransactionを保持せず、外部取得・LLM処理はwrite lock外で行います。保存時は`BEGIN IMMEDIATE`でwriterを確保してから更新番号・aliasを検査し、project・子行・反映済み調査履歴を保存します。検査と更新の間へ別writerは入りません。通常保存はこのtransactionだけをcommitし、複数project操作は外側のtransactionへ参加します。例外時は保存単位をrollbackします。

既存projectの保存にはDBから取得した完全なruntime viewと更新番号が必要です。履歴snapshotをhydrateし直しただけの値や、番号なしの既存project更新は受け付けません。新規keyは番号0から開始します。Webの`expected_revision`は同じ番号を文字列で返し、request受付時の検査に加え、保存transaction内で再検査します。編集・確認ダイアログは開いた時点の番号を固定し、処理待ちの間に取得した新しい番号へ付け替えません。profile保存の競合時はダイアログを閉じ、最新状態から編集し直します。

merge/split/separateは入力元と出力先の番号を保存transaction内でまとめて検査します。第三projectのseparate参照更新もtriggerが番号を進めるため、そのprojectの古い調査が後着して判断を戻すことを防ぎます。更新内容を自動で差分再適用する処理はありません。

### 保護する書込み経路

| 経路 | 保存時の保護 |
| --- | --- |
| enrich・service確認 | 調査開始時の番号を検査し、競合成果は未反映履歴へ分離 |
| profile・追跡状態・confirm/block/defer・review操作 | 読込み／判定時の番号を共通保存へ渡す。Webは表示時の番号も検査 |
| sync・seed登録・repair | 共通保存でproject単位に検査。既にcommit済みの別project成果は維持 |
| merge/split/separate | 入力元・出力先をまとめて検査し、操作全体をrollback |
| mergeによる第三project判断更新・identifier storageの直接更新 | DB triggerにより既存snapshotを無効化 |
| storage cleanup・旧trust archive適用 | 既存のwrite transaction内で読み書きし、更新番号も進む。大量maintenanceのlock時間は短い通常保存と別 |
| materializer・shadow | 参照元はread-only、保存は新規複製側。実運用への通常保存や自動移行として扱わない |

registry全体のsource設定・watermark・identifier exclusionはproject snapshotの一部ではありません。任意SQL自体に楽観的検査を強制するものでもなく、直接更新を行う処理はtransaction内の最新読込みが必要です。triggerの削除・更新番号の改変、旧binaryの並行使用はこの契約の対象外です。

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

`category`は成熟度、稼働状態、発見経路ではなく、主な利用者と利用形態を表します。現行値は`project`, `lexicon`, `library`, `infra_tool`, `personal_tool`, `name_only`, `reject`です。

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

`auth_only`はcategoryではありません。認証packageは`library`、認証proxyや外部toolへ導入する認証pluginは`infra_tool`に分類します。`atproto_auth_only=true`は、独立したappまたはserviceがATProtoをaccountのsign-inまたはOAuth認証にだけ利用し、records、schema、repository、protocol dataを扱わない場合に保存します。ATProto認証機能そのものを実装・提供するlibrary、plugin、proxy、reference codeには付けません。それらの役割はcategoryで表します。強いprotocol利用の根拠があれば、`atproto_contribution_level`はそちらを優先します。

旧`category=auth_only`の正本DB rowは個別移行済みです。読み取り互換は持たず、LLM出力のcategoryとしても受け付けません。

分類の選択順序とLLM適用は[Categoryの選択](project-evaluation.md#categoryの選択)を参照してください。

### `state_json`

機械による評価・調査のcurrent stateを保持します。内部は概ね次の責務に分かれます。

- `ops`: projectの分類、要レビュー・紹介・マージ候補の判定理由（signal）、評価score、errorなど、現在の運用・評価結果
- `evidence`: 候補値、保存量を制限した観測、調査log、`trust_graph`など、project単位で参照する補助材料

保存時に維持する条件（invariant）:

- identifier、typed relation、operator decisionの正本を含めない
- raw EvidenceBundleやsource別実行履歴の正本にしない。それらは`investigation_runs` / `collector_runs`へ保存する
- 候補や機械評価を`profile_json`の確定値と同じ意味で扱わない
- fieldの欠落を否定的事実の確定として扱わない。未調査、部分失敗、証拠なしは調査履歴と評価detailで区別する

代表例として、service statusの根拠、atproto寄与度、review signal、resource page observation、trust graph、summary候補などを保持します。内部fieldはcollectorやheuristicの変更に合わせて変えられる実装detailであり、この文書では網羅的なpayload APIとして固定しません。具体的な現行判定は[project評価](project-evaluation.md)と[帰属・採用](project-boundaries.md#現行の採用規則)を参照してください。

`system_notes` は、merge/split/importなどの機械生成された運用履歴を保持する補助fieldです。人間が編集するprofile `notes` とは保存時点で分離し、紹介用profileやoperatorの自由記述には混ぜません。読込時に`notes`本文をpattern判定して`system_notes`へ移す互換処理は行いません。

### 説明の編集制御と観測時点

schema v4の`state_json.evidence.editorial`に説明の更新方法（`mode=automatic / review`）、現在文面の`profile_hash`、直近の`last_update`、必要な場合だけ更新案`proposal`と見送り案のhashを保存します。更新案は本文3field、比較元のhash、変更種別、理由、最大4件の根拠抜粋、生成時点を持ちます。全文の版履歴や全変更のevent列は作りません。説明本体は従来どおりprofileの`tagline / summary`とstateの`features`です。

Editで説明を変更すると保護側になります。明示的な自動反映の許可も同じpreview／revision検査／保存を通します。採用案と文面が一致した場合は`last_update`へ案の変更種別・理由・根拠を残し、手動で別の文面へ変えた場合はoperator editと記録します。mergeで文面を組み合わせる場合は保護側に戻し、古い案を持ち越しません。来歴未記録の既存文面の作成者や旧観測日時は推定で埋めません。

`service_verification_results`は`lane + requested_url`ごとの最新試行に`observed_at`を保持し、最後に利用面を確認できた観測の時点・lane・statusだけを`last_success`へ残します。失敗してもこの成功情報は消しません。HTMLや画面の過去本文は追加保存しません。同じ組の古い日時の入力は新しい試行を置き換えません。時点未記録の既存観測へ`last_enriched_at`などを流用しません。

`surface_change`は直前の利用面確認との差、`content_changed`は保存範囲の内容hashの差です。いずれも終了・機能変更の確定事実ではありません。これらと説明の編集制御はcurrent stateなので、調査履歴の削除後も保持されます。更新は既存のproject revision検査・transactionを通り、競合成果は既存の未反映履歴へ分離します。

## URL正規化

canonical fieldは`website_url`、`introduced_url`、confirmed/candidateのservice・repo identifierです。`website_url`は代表公開URLであり、service identifierとは独立しています。補助pageは`resource_url_records`に残します。

現行の`normalize_url()`はscheme/host/pathを正規化し、末尾slash、query、fragmentを破棄します。queryやfragmentだけが異なるURLは同じidentifierとして扱われます。これは実装済みの現行制約ですが、queryでresource identityが変わるserviceに対して妥当かは未検証です。具体例が出るまでは例外や別URL型を追加しません。

## Runtime viewとexact lookup

runtime codecの完全hydrate要件、無変更roundtrip、Websiteとidentifierの分離は[project_identifiers](#project_identifiers)を正本とします。実装は`runtime_view.py`が所有します。

exact identifier lookupは`project_identifiers(kind, value, classification)` indexを使います。source syncで使うNSID、service URL、repository URL、domainとprovider別repositoryはproject payloadを全件hydrateしません。collection namespace、registrable namespace、service host familyなどのfuzzy候補はexact lookupの後段に分離し、安全判定を通らない一致はsuggestionに留めます。`handle`のexact lookupはnormalized indexだけを使います。`did`と`service_url_candidate`はindexで候補を絞った後、候補projectをhydrateしてcurrent payloadとの一致を確認します。

`audit-url-fields`はcanonical fieldの件数とinvariant違反を読み取り専用で報告します。

## NSIDの扱い

NSID は storage / UI / external lookup で観測されたcaseを保持します。authority domain 由来の比較や alias token 化では小文字化した派生値を使うことがありますが、NSID本体、特にname部を正規化目的で小文字化しません。`listReposByCollection` などcase-sensitiveな外部APIへ渡す値も、保存済み値または観測値のcaseを保ちます。

- `input_nsids`
  - candidate `owned_nsid`のruntime view
- `resolved_nsids`
  - confirmed `owned_nsid`のruntime view
- `confirmed_nsids`
  - 内部で扱う高信頼 NSID
- `lexicons`
  - `resolved_nsids`から導出するcompatibility view
- `lexicon_namespaces`
  - project 粒度で記録する namespace

collection inventory import は project 粒度で集約しますが、lexicon 解決用に代表の full NSID を別に保持します。

## `project_identifiers`

projectの現行構成要素と、構成要素候補であるNSID、service URL、project site URL、repo URL、account、handle、DIDを順序付きで保持します。

- `project_key`: 所属project
- `ordinal`: project内の保存順
- `kind`: identifierの種類
- `value`: 正規化済みの値
- `role`: project内での役割
- `classification`: 通常生成するidentifierは`confirmed | candidate`。relation/evidenceの`derived | heuristic`とは区別する
- `is_primary`: 代表値か
- `provider`: GitHub、Tangled、Blueskyなどのprovider hint
- `metadata_json`: repo観測やaccount detailなど、そのidentifier固有の補助情報
- `observed_at`: 観測時刻

DB列にclassificationのenum制約はなく、runtime codecは既存rowの無変更roundtripを優先します。したがって通常生成の2値契約は、任意の既存rowを保存時に2値へ制限・補正する保証ではありません。

主なroleと意味:

- NSID
  - `owned_nsid`: projectが所有するcollection。`confirmed`はsource assignmentまたは採用判断で確定、`candidate`は所有候補としてレビュー対象
- service
  - `service`: 機能を利用・操作するURL上の画面やAPI（service surface）。confirmedまたはcandidateで保持する
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

`input_nsid`, `observed_lexicon`, `referenced_nsid`, `recorded_namespace`, `seed`, `manual_seed`は移行済みの旧roleであり、現行コードは生成・読込・保存しません。観測・参照したNSIDは、relation、schema sample、collection observationなど元の根拠に保存します。projectの所有を表すidentifierへの採用には、別途ownership判定が必要です。

`source_repository_record`はrepoを調査した観測記録であり、`state_json.evidence.source_repo_records`に保存します。`authority_domain`はconfirmed `owned_nsid`からruntimeで導出し、identifier rowとして保存しません。いずれもproject構成要素、exact attribution、trusted anchorにはしません。

現行の`add-seed` / Web `Add seed`は、NSID、service URL、repo URLをそれぞれconfirmed `owned_nsid` / `service` / `project_repository`へ投影し、`confirm_identifier` decisionを記録します。明示的にservice URLを追加し、Websiteが空の場合だけ、同じURLをWebsiteの初期値にも使います。candidate confirmや自動sourceではWebsiteを設定しません。`seed_type` / `seed_value` scalarは初期入力の由来・検索用でありidentifier roleやtrust anchorではありません。自動sourceの由来は`discovery_sources`、採用根拠はdecisionまたはsource assignmentとして分離します。

NSIDには代表値を置きません。runtime上の`resolved_nsids`はconfirmed、`input_nsids`はcandidateの`owned_nsid` viewです。NSIDのname部を含め大小文字を保持します。`project_site_urls`と`project_site_url_candidates`は`site_url` / `project_site`のruntime viewです。人間向けの代表公開URLは引き続き`projects.website_url`を正本とし、Website値だけからidentifier、classification、採用理由を導出しません。同じURLのconfirmed `service`がWebsiteを兼ねる場合は`project_site`を二重登録せず、独立した代表siteが構成要素として必要な場合だけ`project_site`を保存します。既存のproject siteを後からserviceとしても確定する場合は、以前のsite採用を自動撤回しません。

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

relationの`classification=confirmed`は、保存したpredicateの方向付き事実を直接観測または確定できたことを表します。subject/object identifierがproject構成要素としてconfirmedであることや、そのrelationを信頼経路へ無条件に利用できることは表しません。identifierの採用状態と、信頼経路への利用可否は別のpolicyで評価します。帰属、信頼、採用の責務境界は[登録プロジェクトへの帰属と信頼](project-boundaries.md)を参照してください。

主要predicate:

- `defines_schema`: project側で帰属を認めたrepoやaccountが、所有を認めたschemaを定義する。repoとNSIDの採用条件は[NSID ownership](project-boundaries.md#nsid-ownershipの優先順位)を参照
- `uses_collection`: read/write/queryなどでcollectionを利用する
- `mentions_collection`: docs、example、test、所有を認めていないschema fileなどでの言及
- `official_account`: siteやrepoがaccountを公式として示す
- `operates_service`: identityがserviceを運営する
- `repository_homepage`: repository metadataのWebsite欄がURLを直接示す。URLのtarget roleやproject帰属は含意しない
- `project_repository`: app/siteとrepoの間に公式project repositoryとしての直接参照がある
- `requests_permission`: OAuth metadataがrepo permissionを要求する
- `redirects_to`: URL間の実観測redirect/canonical関係

同じproject内でsubject、predicate、object、source、locator、methodが同じrelationは重複保存しません。relation生成時の境界は[Relationと採用](investigation.md#relationと採用の境界)、現行ownership判定は[NSID ownership](project-boundaries.md#nsid-ownershipの優先順位)を参照してください。

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
- `dismiss_review`: 特定の要レビュー理由（review signal）の表示を抑止する
- `dismiss_review_target`: 内容の変更検出値（fingerprint）で特定したrelationまたはcollector failureのレビュー表示を抑止する
- `defer_review_target`: fingerprintで特定したrelationまたはcollector failureのレビューを期限付きで保留する
- `separate_project`: 2 projectを別物としてmerge候補から除外する

各rowはtarget、note、created/expires/cleared timestamp、追加metadataを持ちます。clear後も履歴を残すdecisionがあります。`confirm_identifier`はmigrationや再評価で自動判定より優先する明示的な採用根拠として扱い、`source`でoperator入力とsource syncを区別します。

新しい`defer_promotion`のmetadataには判断時点の`fingerprint`を保存します。期限前でも現在の根拠が異なれば紹介候補の保留効果を解除し、元のdecision rowは履歴として維持します。再保留では期限内の旧entryをclearして新しい基準を保存します。fingerprintがない既存decisionは期限だけで判定し、読込み時の推測補完・移行はしません。Lower priorityの保存形、理由、解除済み履歴、merge時のtarget優先は維持します。

operatorがcandidateをconfirmすると、同じ`kind + value`を対象とするexactなblock/deferを解除して`confirm_identifier`を追加します。operatorがblock/deferすると、同じ対象のactiveなconfirmと採用理由を終了させます。現行mutationは同じ値を持つ全roleへ作用し、`target_role`は判断対象のroleを記録しますがrole単位のguardにはまだ使いません。古い矛盾したdecisionを併存させて優先順位を毎回推測せず、最新の明示的判断をactiveな状態として保存します。

### Promotionの確認記録

`state_json.ops.promotion_attention`は一覧の再評価用current stateです。`確認済み・次へ`、Defer、Lower priority、それらの解除の明示操作だけで更新し、一覧取得、詳細取得、Refresh、Skipでは保存しません。自動表示回数や未紹介を負例とするlabelは作りません。

- `snapshot` / `fingerprint`: 直近操作時の構造化判断材料とversion付きhash。対象は[再評価条件](project-evaluation.md#確認済み候補と根拠変化)を参照
- `last_action_at` / `last_outcome`: 直近操作時刻と`reviewed / defer / lower / restore`
- `outcome_counts`: 上記操作別の累積件数。閲覧回数・紹介採用率を表さない
- `reviewed_fingerprint` / `reviewed_until`: 明示した確認済みの対象と30日間の期限。解除で削除

snapshotは直近1件、確認済みのhashと期限は最大1件で、全event・順位・session履歴を追加しません。紹介の結果は既存`introduced_url`で参照できますが、判断当時の全profileや履歴遷移を復元する用途には使えません。schema v4の既存JSONへ保存し、新tableや正本DBのmigrationは不要です。保存は既存revision検査を通ります。mergeではtargetのcurrent stateを保持し、sourceの確認記録は集計しません。合成後の根拠が異なれば確認済み効果は外れます。

## 非現行identifier、採用理由、移転、noise

`project_noncurrent_identifiers`はprojectとの正の関係を保つが現行構成要素ではないclaimを保持します。`standing`は過去に現行だった`former`と、開発・試験用の`non_production`です。exact検索には現行claimより低い優先度で使えますが、trusted anchorにはしません。同一`project_key + kind + value + role`を`project_identifiers`と同時に保持せず、現行へ再採用した時は非現行rowを削除します。

`project_identifier_adoptions`は、現行confirmed構成要素をいつ、どの入口とpolicyで採用したかという来歴（adoption）を保持します。利用者による採用（operator）、承認済みの収集入口からの登録・割当（bootstrap）、信頼規則による自動採用（trust policy）を区別し、merge後も`origin_project_key`を維持します。現行confirmedの帰属主張と有効な採用理由の両方がある場合だけ、信頼判定の起点（trusted anchor）の候補になります。candidate、former、non-productionの採用理由はactiveにしません。identifierの`confirmed`にはこのように複数の採用経路があり、人間が確認したことだけを表す値ではありません。

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
- `dead`: 終了・消滅を表す既存の分類値
- `live`: 一般利用可能なsurfaceを確認した
- `private`: 一般公開性が低いことを表す既存の分類値
- `unreleased`: coming soonなど公開前
- `beta`: beta公開
- `invite_only`: 招待・waitlist制

`service_status_confidence`は`none | low | medium | high`です。status値と根拠の強さを別に保持します。

probe単体では404/410やparkedを`dead`、アクセス制限を`private`に分類する場合がありますが、projectの現行評価ではそれだけで恒久的な終了・非公開を確定せず、`unverified`へ投影します。保存済みscalarは評価・保存するまで変わりません。サービス終了の説明には、終了告知など文面を裏付ける材料を別に必要とします。

## Investigation History

`investigation_runs`と`collector_runs`は、project一件に対する調査実行記録と、その調査内の収集処理別の結果を保持する履歴・replay用tableです。この節のrunは調査実行記録を指します。一括enrichの再開単位とcheckpointは[Background jobs](background-jobs.md#enrich-checkpoint)で扱います。

`investigation_runs`:

- `run_id`, `project_key`
- `started_at`, `completed_at`
- `evidence_schema_version`, `evaluation_version`
- `input_project_json`, `output_project_json`
- `evaluation_json`, `bundle_json`
- `application_status`: `applied`（現在状態へ反映）または`unapplied`（競合により未反映）
- `conflict_reason`, `resolved_at`: 競合理由と再試行解消時刻

4つのJSON列は論理上JSONですが、新規runでは`project-registry:zlib-json:v1` header付きのzlib BLOBとして保存します。通常reader、Investigation viewer、replay、fixture/model evaluation export、storage maintenanceは共通codecでdecodeします。圧縮前のTEXT rowも移行完了までは同じreaderで読めます。header不明、zlib/UTF-8/JSON破損はrun IDと列名を含むdecode errorとして停止し、空payloadや部分的なrunへ置き換えません。

圧縮後の4列にはSQLite JSON function、`LIKE`、直接SQLによるfield監査を使えません。`run_id`、project key、時刻、versionと`collector_runs` projectionは引き続きSQLで監査でき、JSON内容は通常readerまたは`compress-investigation-history`のdecode auditを通して確認します。

`collector_runs`:

- `run_id`, `ordinal`
- `source`, `operation`, `status`, `observed_at`
- `result_json`, `records_json`, `failures_json`

新規runでは、record/failure本文の正本は`investigation_runs.bundle_json`だけです。`collector_runs`は`source + operation`単位にまとめたstatus/count projectionで、複数結果のstatusは最も強いproblem状態、request/result件数とcache hit数は合計を保持します。`result_json.evidence_storage = "bundle_json"`とrecord/failure countを持ち、`records_json` / `failures_json`は空配列です。旧形式の列内payloadを読むfallbackは持たず、現行projectionだけを対象にします。

`projects.state_json`の`service_verification_results`は、調査経路と対象URL（`lane + requested_url`）の組ごとに最新観測を持ち、最大32組を保持します。過去の観測は上記の調査履歴tableで確認します。laneの意味は[Service pageの観測](investigation.md#service-pageの観測)を参照してください。

`cleanup-storage-payloads`は既存project current view、run input/output snapshot、EvidenceBundle、collector projectionへ同じbudgetを投影します。run自体やoperator decisionは削除せず、既定dry-runです。`--execute`ではpayload rewriteを一transactionで行い、commit前にforeign key/integrity checkを通します。保存済みevaluationを再計算するoperationではありません。

`compress-investigation-history`は既定dry-runで全history JSONをdecode・JSON検証し、論理bytes、現在の保存bytes、圧縮後bytes、decode failureを報告します。`--execute`では未圧縮rowだけを1 transactionで書き換え、前後のintegrity/foreign-key checkを通します。SQLite fileを縮める必要がある場合だけ、別processを止めたうえで明示的に`--vacuum`を併用します。

JSON payloadの意味とreplay時の扱いは[Investigation](investigation.md)を正本とします。

調査入力・出力snapshotには、runtimeの採用来歴を`identifier_adoptions`として保存します。currentの`confirmed`だけから信頼起点を推測せず、当時のproject・role・採用source・取消し・review状態をreplayできるようにするためです。正本の採用来歴は引き続き`project_identifier_adoptions`です。古いsnapshotに採用来歴がない場合、classificationだけを新しい独立起点へ格上げしません。

run timingとLLM request metadataは`evaluation_json._run_metrics`、実際のtriage入力は`state_json`の`llm_source.evidence`へ保存します。計測対象、非secret metadata、semantic evaluationとの分離は[Run observability](investigation.md#run-observability)、入力上限と縮約の記録は[LLM evidence budget](investigation.md#llm-evidence-budget)を正本とします。

未反映履歴はcurrent stateを変更せず、`latest_investigation_run`の成功結果にも選びません。入力・出力・根拠を通常と同じbudget/codecで保持し、Webの履歴一覧・詳細・service比較で未反映と表示します。保存失敗で履歴も残せなかった場合は一般障害として扱います。再試行は現在状態から再調査し、古い出力を自動再適用しません。

### Merge / Splitの履歴

mergeは存続先targetの保存、マージ元sourceのalias作成、第三projectの`separate_project`参照更新、source削除を1 transactionで行います。途中で失敗した場合は全変更をrollbackします。source keyで保存済みの調査実行記録はalias familyの履歴としてtargetの調査一覧へ表示します。

mergeはsourceとtargetの現行identifierとconfirm判断を統合してから正規化します。identifierは`kind + value + role`で照合し、同じclaimのcandidateとconfirmedではconfirmed行を採り、同じ採用状態の衝突ではtarget行を採ります。選ばれた行のmetadata・provider・primary・observed_atと、異なるroleの行を保持します。confirm判断はrole・source・日時・metadataを含めて保持し、同一内容の重複だけをまとめます。

confirm判断のmetadataにはmerge時に採用元の`origin_project_key`を保持します。既存の一致する採用来歴から元projectが一意に分かる場合はその値を使い、来歴が無ければ判断を保持していたprojectを記録します。複数の元projectへ対応する古い判断は一つの元projectへ推定で固定しません。同時刻・同sourceの採用でも元projectを区別し、一方の解除判断だけで他方の採用を無効にしません。

採用来歴・非現行identifier・transitionを同じtransaction内でtargetへ移してからtargetを保存します。既存の採用eventに一致するconfirm判断から新しい採用を生成せず、`adoption_id`・採用source・created_at・origin_project_keyを連続mergeと再保存後も維持します。来歴だけから新しい現行identifierを作らず、旧repositoryのconfirm判断だけによる再採用もmergeでは行いません。現行状態と明示判断・非現行状態の衝突は[Project境界のMerge・Split規則](project-boundaries.md)に従って解消します。

merge後に完了したstale enrichの保存拒否は[project_key_aliases](#project_key_aliases)、rollback後の競合通知・再試行は[Background Job Model](background-jobs.md#中断)を参照してください。

split前のrunは、どのidentifierに属する証拠かを自動分配できないため元projectに残します。split後の新しいrunだけを新project keyへ保存します。run snapshotを後から書き換えて現在状態と見せることはしません。

### Retention Policy

増加量の大きい`investigation_runs` / `collector_runs`だけを定期削除の対象にします。未解決の`unapplied`履歴は削除対象から外します。再開時に保存または選択対象外として処理を終えた場合に解消し、単体の再調査成功でも開始時点より前の未反映履歴を解消します。解消後は`applied`と`unapplied`を別々に数え、次の両方を保持します。

- 完了から90日以内の全run
- 日数にかかわらずprojectごとの最新3 run

削除は自動実行せず、`prune-investigation-history`のdry-runを確認してから`--execute`で行います。`investigation_runs`を削除すると対応する`collector_runs`もforeign-key cascadeで削除します。SQLiteはrow削除だけではfile sizeを縮めないため、filesystemへ空き領域を返す必要がある場合だけ、他のWeb/CLI/enrich processを止めて`--execute --vacuum`を使います。

次は自動削除しません。

- `project_decisions`: operator assertionの正本
- projectの現在状態: 一覧・詳細・次の調査が読む正本

Web job eventはprocess内保持で再起動時に消えます。`sync_state`は最新watermarkだけを保持し、実行履歴として蓄積しません。enrich checkpoint fileの自動削除は未接続です。

## repair-db

`repair-db` の責務:

- `finalize_project_shape()` を通して正規化
- `duplicate_candidates` / `merge_recommendation` の再計算
- collision 監査

既定は dry-run です。`--execute` を付けた時だけ正規化と派生フィールド再計算を保存します。collision は報告のみで、自動 merge / separate は行いません。

出力カウンタの意味:

- `normalized_project_count`: `project_from_row()` で復元したpayloadを `ensure_project_shape()` に通しただけでJSON payloadが変わるproject数。
- `pruned_project_count`: `finalize_project_shape()` 後のpayload、developer account/handle補正、またはatproto contribution保存値補正で、保存対象payloadが変わるproject数。名前は歴史的なものですが、project削除数や削除row数ではありません。
- `manual_repo_seed_repair_count`: activeなmanual repo seedがconfirmed `project_repository` に存在しない旧shapeを補正する件数。
- `developer_account_repair_count`: DIDがdeveloper handle/accountのhandle欄に保存された旧shapeの検出件数。payload内のdeveloper account listとidentifier rowの両方を数えます。
- `refreshed_derived_project_count`: `duplicate_candidates` / `merge_recommendation` の再計算結果が保存済み値と異なるproject数。
- `collision_count`: 正規化後に同じconfirmed canonical identifierを複数projectが所有する衝突数。candidateとprovider別の派生viewは除外する。

`repair-db --execute` は上記を個別に選択せず、該当する正規化・補正・派生値更新をまとめて保存します。特定の旧shape修正を目的に実行しても、同じdry-run summaryに出ている別の補正やmerge候補再計算も保存対象になります。

service観測から現在の稼働状態を算出する処理では、JSON payloadの差分に加え、scalar列だけの差分も保存対象です。

`repair-db`はschema migrationを行いません。

新しいsuffix設定時はWebとCLIが同じapplication serviceを呼び、保存済みcurrent service probeのrequested/final URLを現在のdomain policyで再判定します。staleな`redirect_same_site_family` flagと対応する集約evidenceだけを修正し、観測URL、service status、confirmed/candidate identifier、investigation snapshotは変更しません。保存済みEvidenceBundleをcurrent stateへ再適用する場合もsame-site flagを現行policyで再計算します。genericなproject保存や履歴payloadの縮約では再計算しません。

## 登録後の返却と非現行identifier

`ensure_project_for_seed()`は保存後に`fetch_project()`で再読込した完全なruntime viewを返します。保存の途中でcanonical payloadへ投影したdictを、そのまま後続enrichの入力にはしません。`source_sync.py`のcollection importは非現行NSIDとactive exclusionを既知の抑止対象に含め、非現行化した値を新規projectとして再登録しません。通常の復帰・再採用は明示的な採用操作で扱います。

### Identifier保存方式の比較経路

`compare-identifier-storage --db-path PATH --output REPORT`はsource DBを`mode=ro`、`query_only`、単一read transactionで読み、identifier・採用理由・非現行値の保存投影を比較します。sourceを更新しません。`--materialized-db NEW --review-manifest MANIFEST`を明示した場合だけ、上書きを拒否する新規複製DBへreview済み投影を適用します。schema v2から現行schemaへの変換はこの複製側で行い、通常openや`repair-db`の互換処理ではありません。移行時のmanifestは当時の判断を再現する入力であり、後続のoperator判断で変わった現在の採否を示すものではありません。

## Non-goals

- app lexiconとの完全互換
- raw HTMLやREADME全文の永続化
- 旧schemaの通常読み込み互換
- Web UI向けpayload互換
