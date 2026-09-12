# Investigation Model

調査pipeline、sourceからの取得・観測、evidence、部分失敗とreplayの契約を定めます。
登録済みprojectを起点に、外部情報を観測として集め、採用できる根拠を現在状態へ反映します。
sourceごとの失敗を分けて記録し、一部を取得できなくても得られた根拠を保持します。
保存した調査runは、外部へ再接続せずに現行規則で再評価できます。

## Pipeline

調査は `collector -> evidence -> integration -> evaluation -> save` の順で扱います。

1. collector
   - query planning を含め、外部 source を読み structured evidence を返す
   - 接続済み collector は共有 project dict を直接変更しない
2. evidence
   - 観測と source の完了状態を JSON-compatible な bundle に正規化する
   - relation は subject、predicate、object、locator、抽出方法を持つ
3. integration
   - evidenceから既存project fieldへの適用を担う責務。実装は`integration.py`だけでなく`registry.py`の適用関数と、project copyからpatchを返すcollector adapterにも分かれる
   - project 文脈、manual guard、複数 source の corroboration を適用し、operatorのconfirm/defer/blockを自動評価より優先する
4. evaluation
   - ownership、atproto寄与度、service status などを rule-based に計算する
   - network、DB、LLM に依存しない関数を優先する
5. save
   - `projects` を現在状態の materialized record として保存する
   - 同じ SQLite に input/output snapshot、評価、bundle、source 状態も保存する

Web detailはproject単位のinvestigation run summaryをSQLite rowから一覧し、選択したrunだけbundleをdecodeします。run detailのevidence/failureは上限付きで、raw HTMLやREADME全文を保存・表示しません。Replayは保存runを現行ruleで要求時に再評価するread-only処理で、結果をDBへ永続化しません。

Service / ownership reviewも保存済みcurrent state、normalized relation、collector runだけを読みます。画面表示のためにnetwork collectorを再実行しません。保存根拠がない状態は`not_observed`であり、到達不能・非ownership・permissionなしの確定結果ではありません。

### Enrichの実行順序

`enrich` は次の順で動きます。

1. seed由来の事実収集
2. service / repo / lexicon の確認済み情報を更新
3. lexicon repository単位で namespace 集約
4. confirmed repositoryの解析素材を収集し、revisionごとのrepository analysisを更新
5. GitHub repo / GitHub search / Bluesky search の structured evidence を収集
6. evidence を project へ統合し、rule-based evaluation / finalize を実行
7. local LLM による summary / category / status 補完
8. 再度 finalize し、duplicate / merge / 昇格判定を更新

## 根拠と調査状態

### Evidence classification

- `confirmed`: operator decision、first-party claim、検証済み identity path など強い帰属を持つ観測
- `candidate`: 関連性はあるが、帰属または意味の追加確認が必要な観測
- `derived`: 他の観測から決定的に導出した query、正規化結果、usage relation
- `heuristic`: score、文字列類似、community source など誤判定し得る推定

classification は project field の confidence と同義ではありません。integration が guard と project
文脈を適用し、confirmed field、candidate field、observation のどこへ反映するかを決めます。

### EvidenceBundle v2

通常readerが受理するのはEvidenceBundle v2だけです。旧v1 bundleの互換readerは提供しません。

- `EvidenceBundle`
  - `schema_version`, `project_key`, `collected_at`
  - 順序付き `records`, `failures`, `collector_results`
- `EvidenceRecord`
  - `kind`, `source`, `classification`, `observed_at`
  - JSON-compatible な `value` と query、score、repo path などの `context`
  - service verificationのHTML/link/URLはcollector内の判定後に保存budgetを適用し、観測総数とdigestを併記する
- `CollectorFailure`
  - `source`, `operation`, `target`, `category`, `message`, `retryable`
  - `retryable`は最終的な失敗の性質を示すmetadataで、次runの自動予約・再試行時刻を決める指示ではない
- `CollectorResult`
  - `success | partial | skipped | rate_limited | failed`
  - request/result 数、cursor、stop reason、retry 時刻、cache hit
  - GitHub collectorは実際に送信したHTTP request数を記録し、process-local cache hitはrequest数へ含めない
  - GitHub API応答に`X-RateLimit-*`または`Retry-After`があれば、最後に観測した
    `limit`, `remaining`, `used`, `reset`, `resource`, `retry_after`を`rate_limit`へ保存する

#### Collector history projection

repository homepageとREADME公開先の観測は、未採用URLの取得結果とprojectへの採用判断を分けます。
URLごとのlaneとassessmentをbundleへ残し、replayでも現在の起点とguardを再評価します。
取得対象、候補化、browser/protocol確認、衝突検査、旧assessmentの扱いは
[Repository homepageと公開先の評価](project-boundaries.md#repository-homepageと公開先の評価)を正本とします。

同じsourceを集約する場合、rate limit観測は最も新しいcollector resultの値を保持します。

通常接続済みcollectorは`CollectorResult`を明示的に返します。record/failureからsource状態を推測する互換処理は現行導線では使いません。failure categoryはtimeout、rate limit、request budget、network error、remote 5xx、invalid response、browser error、collector errorなど、再試行・運用判断に必要な単位で固定します。GitHubの403は`X-RateLimit-Remaining: 0`または`Retry-After`がある場合だけrate limitとして扱い、それ以外はforbiddenとして区別します。

主要 relation predicate は `defines_schema`, `uses_collection`, `mentions_collection`,
`official_account`, `operates_service`, `project_repository`, `requests_permission`, `redirects_to` です。
同じ README から複数抽出した claim は独立 source と数えません。

### 調査状態

次を区別します。

- `success`: 予定範囲を完了した
- `partial`: usable record と failure の両方がある
- `skipped`: 設定不足、不要判定、budget 超過などで実行していない
- `rate_limited`: upstream 制約で打ち切った
- `failed`: usable result を得られなかった
- 証拠なし: 完了した collector が対象 evidence を返さなかった
- 未調査: collector 自体が実行されていない

「証拠なし」を否定的事実へ変換するには、対応 source が成功して必要範囲を走査したことが必要です。

### Relationと採用の境界

collectorの観測からrelationを作ることと、identifierの帰属・採用は別の判断です。
relationの意味、`defines_schema`の成立条件、candidateの採用規則は
[project境界](project-boundaries.md#現行の採用規則)を正本とします。service到達の観測自体は、
採用できるかにかかわらず`service_verification`へ残します。取得失敗を否定や過去観測の消滅へ変換しません。

## Source別の観測

### Lexiconとauthorityの観測

- 入力:
  - `input_nsids`
  - `resolved_nsids`
  - GitHub repo から後で見つかった NSID
- 取得の流れ:
  - `_lexicon.<authority_domain>` の TXT を引いて DID を解決
  - DID document から PDS endpoint を引く
  - PDS の `com.atproto.repo.getRecord` で `com.atproto.lexicon.schema/<nsid>` を引く
- ここで confirmed に入るもの:
  - operator/source assignment済みの`resolved_nsids`
  - confirmed developer DID、またはconfirmed root serviceとの相互trust pathが成立したcandidate NSID
- 解決結果として保存するもの:
  - `record_creators`
  - `authority_domains`
  - `lexicon_resolution_entries`と`did_publishes_schema` / `domain_publishes_lexicon_record` / `collection_authority_domain` edge
- ここで candidate に留めるもの:
  - lexicon description 由来の `summary_candidates` / `tagline_candidates`
- 保守的な点:
  - lexicon schema が取れても、それだけで `website_url` や `service_url` は confirmed にしません
  - authority domain 由来の service URL は、NSIDから直接candidate保存せず、probe結果を見てから候補化します
  - authority probe は完全一致hostと registrable parent を同一chainとして並列fetchします。完全一致hostが2xx/3xxなら親は捨て、完全一致hostが4xxまたは恒久的なDNS不在で、親が2xx/3xxの時だけ親をfallback候補として扱います。timeout、DNSの一時障害、5xx、TLS失敗、接続拒否、その他のnetwork errorでは親へ広げません
  - 完全一致hostがregistrable parentへsame-site redirectする場合は、内容比較ではなく明示的なredirectを根拠に最終URLをcanonical candidateとして扱い、redirect元を独立したauthority candidateとして重複保存しません
  - authority domain 由来probeが成功し、実service surfaceらしく、完全一致authority hostのページ自身にAT Protocolの具体的な痕跡がある、record sample URLによるhost一致がある、または上記fallback条件がある場合だけ `service_url_candidates` に残します
  - authority domain 由来の失敗probe URLや、上記の補強がない旧candidateは、confirmed/supported/manual service として残っているものを除き `finalize_project_shape()` / `repair-db` で削除します
  - ページ上のAT Protocol言及はcandidate保存にだけ使います。`supports_project`やconfirmedへの自動昇格根拠にはしません

### Repositoryの取得とsnapshot

relationとcandidate生成はsnapshot budget適用前の全取得結果から行います。

- 採用済み`repo_url` seedは`project_repository_urls`にconfirmedで入り、入力元とは別に`confirm_identifier` decisionを持ちます
- repo metadata から取るもの:
  - repo 名は `name_candidates` に入れ、`atproto-visualizers` のような slug はハイフン込みの全体を名前候補として扱います
  - `description` は `summary_candidates` / `tagline_candidates`
  - `homepage` は公開URLとして妥当ならconfirmed `repository_homepage` relationとして保存する。homepage単独ではservice候補やtrust edgeを作らない。通常enrichは現在採用済みrepoのhomepageからWebページ（docsを含む）を元のpathで取得する。未判定ページは`repository_homepage` laneで観測だけを保存し、既にexact URLがserviceと確認済みなら従来のstatic laneを使う
  - `updated_at` は `source_repo_records[].updated_at`
- tree / lexicon hint の扱い:
  - `lexicon/`, `lexicons/`, `schema/` 配下などの JSON を lexicon 候補として走査します
  - その JSON が lexicon らしく、かつ `project_owned_lexicon_nsid()` が真の時だけ project 自身の NSID として採用します
  - ownership が弱い時は `github_repo_lexicon_reference` として investigation log に残すだけです
  - test / fixture / vendor / generated / docs / example path は `mentions_collection` に留めます
- source code の意味解析:
  - write/createRecord 系は `uses_collection` の write
  - read/query/subscription 系は `uses_collection` の read
  - operation が取れない出現は `mentions_collection`
  - package/library 提供、service 実装、用途不明を context に分けます
- README のリンクの扱い:
  - 同一 repo へのリンクは confirmed の補強に使います
  - 別 repo は `repo_candidate_allowed_for_project()` を満たす時だけ candidate に入れます
  - 外部 service URL は `repo_context_service_candidate_allowed()` を満たす時だけ candidate に入れます
- 同じrepo URLがinitial/provider viewの両方に現れても、一runでは一度だけinspectします
- 保存snapshotはGitHub API response全体ではなく、integrationで使うrepo fieldだけを保持します
  - source semantic hintはtyped relationへ展開し、snapshotには件数だけを残します
  - README URLは最大64件、JSON 16 KiB、1 URL 2 KiBです
  - lexicon hintはschema replayに使うため、このcheckpointでは保持します

### Repository analysis

lexicon enrich に成功した場合、LLM evidence には lexicon schema の縮約サンプルも入ります。さらに `ufos-api.microcosm.blue/records` から recent record sample を取得できた場合は、それも判断材料に含めます。

confirmed repositoryは通常`enrich`中に専用のrepository analysisを行い、結果を`state_json.evidence.repository_analysis_results`へ保存します。candidate repositoryは相互trust確認に必要な限定取得以外では解析せず、confirm後に初めて対象にします。GitHubとCodebergはmetadata、README、tree、主要manifest、documentation / deployment設定、Lexiconを取得し、READMEとdescriptionだけでは目的を説明できない場合に限ってsource候補を最大4 file、各file最大2 excerptへ絞ります。Tangledは現行取得能力に合わせてmetadata、README、限定treeを使い、取得できない範囲を`coverage`で`unavailable`とします。未対応providerも黙って欠落させず`coverage.reason=unsupported_provider`を保存します。

repository analysisでは、revisionへ固定したbounded materialを`repository_analysis_material`としてrunへ保存し、解析recordはrepo URL、source revision、material hashでそのmaterialを参照します。READMEは先頭・末尾を含む最大6 block、合計6,000文字として元の行番号を保ち、選択数と切り詰め状態をcoverageへ記録します。LLMへ渡すのは本文を持つevidence blockだけで、tree pathは候補抽出やcoverageに使ってもclaimの根拠にはしません。repository metadataはdescriptionだけをclaim可能な本文とし、name、homepage、is_templateはcontextまたは決定的抽出に限ります。観測とcollection利用は、materialから事前生成したclosed candidate IDだけをLLMに選ばせます。README由来のcollection candidateはreverse-domain authorityが小文字でpublic suffixから始まるものに限り、case-sensitiveな最終nameとoperationを一体で固定します。候補が空なら空配列を要求します。

`repository-analysis-v16`では、documentation公開の記述があるREADME blockも公開先URL候補の対象にします。
URLは本文中の正確な値を保持し、homepage metadataだけでは宣言候補を作りません。設定ファイル・READMEの
`declares_documentation_site_url`候補をLLMが返す場合、`applicability`で`current_project` / `negated` /
`hypothetical` / `third_party` / `obsolete` / `uncertain`を区別します。`current_project`だけを観測へ反映し、
区分が欠けた回答も棄却します。候補IDの選択だけでは、否定・仮定のURLを公開先と誤認する実例があったためです。
この観測は従来どおり`role=docs, confidence=candidate`のresource候補へ接続し、project siteやserviceの自動確定、
独立した運営relationの生成には使いません。v15以前の解析結果はv16のcache hitにはしません。

repository analysisはrepo全体を単一`artifact_kind`へ分類しません。出力は短いsummaryとその`summary_evidence`、複数の`observations`、証跡付き`capabilities`、caseを保持したcollection利用です。観測述語はCLI entrypoint、import可能package、service entrypoint、deployment設定、Lexicon定義、host integration、documentation source / generator / site URL / deploymentなどの非排他的な事実です。`basis=declared|observed|inferred`はrepo内の観測方法でありtrust classificationではありません。pathだけのhost integrationは`inferred`に留まり、browser extension宣言にはmanifestまたはREADMEの明示を必要とします。Lexicon JSONの存在はcollection ownershipを確定しません。

証跡は別のfact IDを作らず、解析対象`source_revision`へ結び付く`path + line`または`path + json_pointer`を各claimへ直接保存します。同じ根拠本文をclaimごとに複製せず、重複を除いたbounded excerptを結果の`evidence_excerpts`へ最大6件、各600文字で保存します。triageへ渡す時は旧形式のclaim内excerptも同じ形へ縮約します。LLMには、README、metadata、manifest、configuration、Lexicon、source excerptから作った利用可能な根拠位置を、prompt内だけで有効な`E01`のような短い番号とともに渡します。LLMは番号だけを返し、保存前に対応する根拠位置へ戻します。番号自体は保存しません。ファイル一覧のpathだけでは内容を裏付けられないため、LLMが新しいsummaryやclaimの証跡として使うことはできません。tree pathから安全に決められる観測は決定的な抽出処理だけが追加します。summaryにも1件以上の根拠位置を必須とします。repository本文はuntrusted dataとして扱い、READMEやsource内の指示に従わないことを専用promptとtriage promptの両方で明示します。

cache identityはcanonical repo URL、`source_revision`（取得できないproviderでは`material_hash`）、`policy_version`の組です。成功結果と`last_attempt`は分離し、失敗時に旧成功結果のrevisionやpolicyを書き換えません。直前の根拠付き成功結果はtriageへ`stale=true`で渡しますが、旧形式の根拠なしsummaryは渡しません。timeout、rate limit、HTTP系など一時的な外部失敗だけは同じmaterialとpolicyへの再試行を24時間抑制し、LLM未設定、endpoint非互換、invalid response、local command失敗は設定修正後の手動再実行を妨げません。run evidenceにはbounded materialとそれを参照する解析結果を別recordで残し、current stateにはrepoごとの最新成功結果と最新試行だけを残します。

LLM endpointまたはmodel出力をJSONとして解釈できなかった場合、run metricsには失敗段階、UTF-8 byte数、SHA-256、JSON構文エラー位置、先頭と末尾がJSON記号か、code fenceを含むかだけを記録します。利用不能だった応答本文は保存しません。これにより、出力上限で途中終了した可能性と形式違反を、repository内容を追加保存せず区別できます。

triageへ渡す解析結果はconfirmed repoだけを対象に最大6件とし、operator-confirmed repo、代表repo、coverageの順で優先します。repository observationはproject categoryやidentifier confirmを直接上書きしません。categoryはservice、collection、現在profileなどを合わせたproject-level triageが決めます。`lexicon`はschema自体が追跡対象である積極的根拠を必要とし、NSID seedやLexicon JSONの存在だけでは選びません。repositoryが宣言したdocumentation site URLは`docs` roleのcandidate resource URLに留めます。

### Tangledの調査

- confirmed developer handleがある通常enrichでは、handle -> DID -> PDSを解決し、
  `com.atproto.repo.listRecords`で本人のTangled repo recordを取得します
- accepted source repo URLがTangledの場合は、repo root HTMLを読み、READMEを
  `raw/{ref}/{path}` から取得して `tangled_repo_inspection` evidenceを作ります
- owner DID一致や共通websiteは補助根拠ですが、actorやwebsiteは複数projectに対応し得るためproject同一性の照合には使いません
- Tangled repo inspection はREADME URLと短いexcerptを候補テキスト・候補URLに反映します。
  GitHubのrecursive tree API相当の完全なcontents取得ではありません

### Service pageの観測

- 各 URL を 1 回 fetch し、HTML から次を見ます
  - HTTP status
  - title / meta description
  - 生テキスト
  - ページ内リンク
- `normalize_url()` は network を伴わない文字列正規化だけを行います
  - `http` と `https` は無条件には同一視しません
  - redirect の同一視は service probe の evidence として別扱いします
- service probe は `requested_url` / `final_url` / redirect flags を保持します
  - same host の `http -> https` redirect だけは canonical service URL として `https` 側へ寄せます
  - same site family のredirect先がprojectを支持する場合は、Websiteの有無に関係なくservice URLだけを更新します
  - service probeから`website_url`は設定・更新しません
  - cross-host redirect は investigation log に残し、redirect 先が project を支持する場合だけ candidate に入れます
  - cross-host redirect は自動で同一 service として confirmed にはしません
- service probeの判定とrelation抽出には取得したresponse全体を使いますが、EvidenceBundleとcurrent stateへ保存する
  raw surfaceはbounded representativeです。
  - `html_extract`: UTF-8で最大12 KiB。全文のSHA-256と取得時byte数は別に保持
  - `links`: 最大128件かつcompact JSON 64 KiB。1 linkのtextは512 bytes、URLは2 KiB
  - `urls`: 最大192件かつcompact JSON 32 KiB。1 URLは2 KiB
  - repo、Bluesky profile、docs/support/status/privacy/login等の役割を持つlink、同一host系link、その他の順で残す
  - 観測総件数を別fieldへ保存するため、保存件数をページ全体の件数と取り違えない
- current `service_verification_results`は履歴ではなく、`lane + requested_url`ごとの最新観測を保持します。
  異なるsurfaceは最大32件です。時系列の確認は`investigation_runs` / `collector_runs`を使います。

### Static・Deep・Browserの調査範囲

- 通常 enrich は service top page と same-site follow-up を軽量 probe します
- `--deep-investigation` は `resource_url_records` と service surface から優先ページ候補を作り、最大 6 page を追加 probe します
  - `/about`
  - `/docs`
  - `/faq`
  - `/app`
  - `/login`
  - `/signup`
- deep page probe は `service_status` / `service_status_confidence` / `service_status_evidence` を補助更新します
  - URL の path から `deep_page:about`, `deep_page:docs`, `deep_page:login` などを evidence に追加します
  - この処理は service URL の confirmed 化や merge 判断までは行いません
- `verify-service -p KEY --lane browser [--execute]` は optional Playwright で anonymous navigation を行います
  - 同一 site の login/signup link を最大2件追います
  - 認証入力、登録、投稿は行いません
  - browser結果は surface 到達と runtime/network error を evidence に残します

### Browser観測

browserの登録・認証入力・投稿と、無制限のevidence保存・表示は対象にしません。

browserの`public_app_surface_detected`は、作成・投稿を表すactionと、supporting pageではない二階層以上の同一site link 3件以上が同じpageにある場合だけ成立します。CTA単独やcontent link単独では成立しません。action語彙は現在英語のpost / entry / create / write / compose / publish / uploadに限定し、固有project名による例外は持ちません。

browser pageにはnavigationごとの`requested_url`と到達先`url`を保存します。form、input、auth/service action、
linkの`visible`、フォーム内の`visible_submit_texts`をChromiumの`checkVisibility`で取得します。
CSS visibility、opacity、content-visibilityを含めた表示状態であり、操作成功や認証成功の証明ではありません。
`display: contents`のformにはboxがなくても、表示されたinputとsubmitを根拠として利用できます。

各pageの`visible_surface_features`は、保存budget適用後の表示済みcontrol/linkだけに既存auth/public surface規則を
適用した結果です。別pageの肯定材料は合算しません。旧観測の表示状態は不明（`visibility_observed: false`）であり、
表示済みと推定しません。`positive_surface: false`もserviceが存在しない、またはproject siteであるという判定ではありません。
これらはURLごとのrole検討材料であり、projectへの帰属やidentifier採用を確定しません。
run全体の従来の`login_surface_reached`等とcurrent service statusへのprojectionは維持し、新しいpage単位の材料とは区別します。

bundled Chromiumが`ERR_SOCKET_NOT_CONNECTED`を返した場合だけ、HTTP/2を無効にして1回再試行します。通常接続の失敗全般には適用せず、使用時は`transport_fallback`としてbrowser page evidenceに残します。

観測からprojectの現在の稼働状態へ投影する優先順位は、
[Service statusの現在値](project-evaluation.md#service-statusの現在値)を参照してください。

### Search collector

- 調査 query はまず deterministic に seed から組み、設定があれば LLM で絞ります
- GitHub search:
  - 最大 2 query
  - 各 query で上位 5 repo を取得
  - `github_repo_relevance() >= 3` を候補にし、その中でも `max(6, top_score - 2)` 以上だけ採用
  - 採用数は 1 repo までで、confirmed ではなく candidate 扱いです
- Bluesky actor search:
  - 最大 2 query
  - `bsky_actor_relevance() >= 4` の handle だけ採用
- Bluesky post search:
  - 最大 2 query
  - post 内 URL から GitHub/Tangled repo candidate を拾います
  - author handle が project と一致すれば developer account に足します
- Bluesky author feed:
  - 最大 2 handle
  - feed 中 URL から repo candidate を追加します
- request policy:
  - retry は初回を含め最大3 attempt
  - timeout、429、5xx、network errorだけを retry
  - `Retry-After`を優先し、GitHub/Blueskyはsource別request budgetを持ちます
  - rate limit時はそのsource collectorだけを止めます

## 外部アクセス

外部アクセスはほぼすべて `request_bytes()` 経由です。`enrich` と各 `sync` の発生箇所は次の通りです。

### `enrich` 1 project あたり

- lexicon 解決:
  - 1 NSID あたり 3 request
  - `dns.google/resolve` で `_lexicon.<authority>`
  - `plc.directory` または `did:web` host で DID document
  - 解決した PDS の `com.atproto.repo.getRecord`
- recent record sample:
  - 1 request
  - `https://ufos-api.microcosm.blue/records`
  - 最大 6 collection をまとめて引きます
- GitHub / Codeberg repo enrich:
  - 1 repo あたり通常 4 request
    - `GET /repos/{owner}/{repo}`
    - `GET /repos/{owner}/{repo}/readme`
    - branch commit取得
    - revision固定tree取得
  - さらに lexicon hint を精査する時は `contents/<path>` を最大 16 件追加
  - README / descriptionだけでは目的を説明しにくい時だけ、source semantic hintを最大4 file追加
  - root寄りの`package.json`から関連fieldを読む時は最大4件追加
  - documentation / deployment設定を最大6件追加
  - つまり 1 repo あたり基本4 requestで、追加素材を全て取得する場合は最大34 request程度です
- service URL probe:
  - 1 URL あたり 1 request
  - same-site docs/blog など follow-up を最大 3 URL まで追加
  - つまり 1 service URL あたり最大 4 request
- `--deep-investigation`:
  - `resource_url_records` と service surface 由来の優先ページ候補を最大 6 page
  - 各 page で follow-up を最大 2 page
  - 最大 18 request
- investigation collector:
  - GitHub repository search 最大 2 request
  - Bluesky actor search 最大 2 request
  - Bluesky post search 最大 2 request
  - Bluesky author feed 最大 2 request
  - retry を含む budget は GitHub 6、Bluesky 12 request
- LLM:
  - `ATPROTO_SURVEY_LLM_COMMAND` なら外部 request なし
    - command文字列は`subprocess`のshell modeで実行するため、project dataやWeb入力から組み立てず、
      operatorが設定したtrusted local commandだけを使います
  - `ATPROTO_SURVEY_LLM_ENDPOINT` なら通常 2 POSTに加えて、revision未解析のconfirmed repositoryごとに1 POST
    - investigation plan
    - repository analysis（revision一致の保存済み結果があれば省略）
    - triage
  - `enrich --llm-max-output-tokens`を指定した場合、planning、repository analysis、triageの全POSTへ同じ出力上限を渡す。省略時はendpoint既定値
  - endpoint fallback が走ると chat/responses/completions を順に試すので、失敗時はこれより増えます
  - 実装済み: LLM endpoint は operator が明示設定する trusted endpoint として扱い、LAN / localhost 宛てでも POST を許可します
  - 実装済み: project data 由来の service / resource URL は同じ `request_bytes()` 系でも内部宛て block の対象です
    - DNS検証と実fetchは別の名前解決になるため、悪意あるDNS切替まで防ぐaddress pinningではありません。
      個人用調査toolの脅威モデルにおけるbest-effort SSRF guardとして扱います
  - 未接続: LLM endpoint 以外の内部 URL を operator が個別 allowlist する設定

### Cacheとrequest計測

- Bluesky session は process 内で cache します
  - 同一 run 中の `createSession` は原則 1 回
- GitHub の search result も process 内 cache します
  - 同じ query を同じ run で繰り返す時だけ有効です
- GitHub repo metadata と service probe も process-local TTL cache を使います
- cache は永続化しないため、別 process の再実行では再fetchします
- enrichのGitHub search/repository collectorは、見積もりではなく実際に送信したrequest数とcache hit数を
  `collector_runs.result_json`へ保存します
- GitHub APIの`X-RateLimit-*`と`Retry-After`は同じresultへ保存します。rate limitを示す403と、
  権限などによる通常の403はcollector status上も区別します

## LLM の位置づけ

- LLM は query planning、editorial 補助、曖昧な分類候補に使う
- identifier の新規提案は原則 candidate に留める
- ownership、高い寄与度、service の到達確認を LLM 単独で確定しない
- rule-based evaluation と manual guard を優先する
- LLM failure 時も deterministic plan と他 collector を継続する

### LLM evidence budget

planningとtriageへ渡す可変evidence JSONは、整形後UTF-8で24 KiBを上限とします。fieldごとの代表件数上限を先に
適用し、長いlistは先頭と末尾から均等に残します。それでも超える場合は低優先fieldから追加で縮約します。
`project_key`等のscalarはlist budgetで落としません。縮約した場合は`evidence_budget`へfield別の観測件数と採用件数を
入れます。planningとtriageは調査の異なる時点で同じbudget規則を使い、triageへ渡したevidenceは
`llm_source.evidence`にも保存します。

これはLLM入力契約であり、service HTMLやEvidenceBundleの保存budgetとは別です。prompt templateの固定文まで24 KiBに
含める契約ではありません。実requestの最終prompt bytesはrun metricsで別に観測します。

### LLM transportの失敗境界

OpenAI Responsesの`output`にreasoningとmessageが併存する場合はmessageだけをmodel出力として解析します。
Harmony control tokenを含むtextではfinal channelだけをJSON候補にします。

command/endpoint方式の1 attemptのtimeoutは`ATPROTO_SURVEY_LLM_TIMEOUT`で指定し、既定は`300`秒です。
auto API styleの別payload/endpoint試行は、HTTP 400/404/405/415/422/501でendpointがrequestを拒否した場合だけ
行います。timeout、接続失敗、rate limit、server error、response envelope/content JSON不良では、serverが
処理中または既に生成済みの可能性がある同じpromptを再送しません。明示`chat`はcompletion endpointへ
fallbackしません。

## Run observability

新規enrich runは、次のwall timeをmillisecondで保存します。

- initial collection + integration
- investigation planning
- investigation collection + integration
- LLM triage + application
- final evaluation
- investigation run historyのserialization + SQL
- current projectのnormalization、serialization + SQL

collectorのnetwork取得とevidence適用は現行pipelineで交互に行うため、前半2つのcollection区間をnetwork時間だけと
見なしません。transaction commit待ち時間も未計測です。`total_ms`は上記のenrich開始からproject SQL書込みまでで、
process job全体、複数projectの待ち時間、operator review時間ではありません。

planningとtriageの各LLM requestには、purpose、success/failure、error category、duration、UTF-8 prompt bytes、
response JSON bytes、設定された1 attempt当たりtimeout、model/API style等の非secret metadataを残します。prompt本文、
endpoint、command、API keyは保存しません。token数はprovider共通に取得できないため未接続です。`timeout`は
明示的なtimeout例外または既存clientが返すtimeout errorから分類し、transport failure、endpoint incompatible、
rate limit、その他HTTP error、invalid responseと分けます。endpoint互換性エラー以外では同じpromptを自動再送しません。

旧runでmetricsが欠落する場合は未記録として扱います。
保存先は`evaluation_json._run_metrics`ですが、load時はsemantic `evaluation`と`metrics`に分けます。そのため
非決定的な時間値はreplay diffやfixture exportへ入りません。Webのlatest run、run list、run detailから確認できます。

## 正本と保存

実装済み:

- `projects` は UI、検索、merge、review が読む現在状態の正本
- `investigation_runs` は input project snapshot、bundle、evaluation version、保存済み評価、output snapshot を保持
- `collector_runs` は source/operation ごとの状態とrecord/failure件数をprojectionとして保持
- full HTML や README 本文は保存せず、bounded extract、hash、URL、repo path など判断に必要な材料だけを残す
- raw evidence の自動削除は行わない。手動retention commandで古いrun単位に削除できる

run 保存は project row の代替ではありません。再評価可能な調査成果と、その時点の materialized
record を対応づける履歴です。

runの物理codec、decode失敗、SQL監査の制約は
[Investigation History](project-record.md#investigation-history)を参照してください。
replayとexportは共通readerを使い、保存codecを直接扱いません。

履歴の保持・削除条件は[Retention Policy](project-record.md#retention-policy)を正本とします。
fixtureへexportしたcaseはDB runとは独立して保持します。

## Replay と benchmark

実装済み:

- `replay-investigation`: 保存 run の integration / evaluation を network なしで再実行し semantic diff を出す
- `benchmark-investigation`: fixture corpus を一括評価し relation/evaluation の退行を報告する
- manual goldのpromotion evaluator testは全caseを実queue生成処理へ通し、policy、signal、score breakdown、候補集合全体の相対順位を評価する
- `scripts/promotion_outcome_analysis.py`はschema v3 DBをread-onlyで開き、紹介済みprojectの`introduced_url`だけを空にした反実仮想を現行promotion policyへ通す。category別捕捉率、signalなしの主因、service / lexicon / technical経路のblocker、紹介投稿単位のproject数をJSONで報告する
- `export-investigation-fixture`: 保存 run を sanitize 対象の fixture 形式へ出力する
- executable corpusは通常replay 6 caseに、operator correctionのblock/defer/confirm 3 caseを加えた9 caseを持つ
- `manual-gold-v1.json`はoperator reviewから固定したidentity / service / repository判断11 case、promotionのexclude / queue / deprioritize 14 case、紹介済みのpositive reference 5 caseを持つ
- `browser-service-calibration-v1.json`は描画後page要約と期待browser evidenceを5 caseに固定する

fixtureはraw response archiveではありません。誤判定を再現するための最小evidenceと期待評価を保存します。
自動exportはsecret、authorization/cookie/header、raw HTML/body/readme/text extract、prompt/command/endpoint、
local pathを削除またはredactし、URLのuserinfo/query/fragmentを除去します。export結果にはmanual review必須markerを
付けます。コミット前に実project/run key、時刻、handle/DID、自由記述を再現に必要な最小範囲へ人が縮約します。

manual gold setはDBのdecision rowを逐次mirrorしません。synthetic project ref、公開identifier、期待actionまたはpromotion disposition、根拠要約だけを保持し、実project key、run ID、decision時刻、有効期限、operator noteを除外します。promotion caseは`exclude / queue / deprioritize`を保持します。`deprioritize`は保存事実から機械推定せず、operator assertion後だけ`priority_group=lowered`となります。機械policyは`expected_policy`、候補集合全体の順序は`promotion_queue_expectation`、個別判断はfixtureのreasonとevidence summaryを正本とします。地域性など機械化しない基準はmanual contextに留めます。

promotion outcome分析は紹介実績を自動score変更へ直結させません。未紹介projectは負例ではなく、紹介済みprojectのcategory、status、profile、evidenceも紹介時点のsnapshotではなく現在値です。結果は現行policyが紹介実績を拾えない箇所の調査とsample抽出に使い、policy変更はmanual goldと人間のreviewで別途確認します。実行例:

```sh
bin/_project-registry-python promotion-outcome-analysis scripts/promotion_outcome_analysis.py --db data/survey.db --details no-signal
```

browser calibrationは実URLを取得し直さず、固定したpage URL、title、text excerpt、link、form input属性、submit label、OAuth候補、匿名menu展開数、service action、network / console errorを`collect_browser_service_evidence()`へ渡します。これにより公開pageが変化しても当時の入力に対する判定を再現できます。実URLへの`verify-service --lane browser`は現在状態のsmoke testに限り、固定期待値との差をsemantic regressionとして扱いません。
