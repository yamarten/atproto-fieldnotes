# Registry Internals

registry schema version 3の内部仕様と運用メモです。利用手順は[利用ガイド](../user/guide.md)、帰属と信頼の判断モデルは[登録プロジェクトへの帰属と信頼](project-attribution-and-trust.md)、保存モデルの正本は[project-record.md](project-record.md)を参照してください。

## 運用前提

このリポジトリは個人用のローカル運用を前提にします。

- `data/survey.db` は単一利用者のローカル DB として扱う
- 他ユーザーや共有 DB の存在は考慮しない
- 同時編集の懸念は、自分の別プロセスや GUI 操作による衝突だけを想定する

## 実装の依存境界

- `application/sync.py`は`source_sync.py`を呼び、source別の取得、watermark、既存projectへの接続を実行する
- `application/enrichment.py`は`enrichment_pipeline.py`を呼び、1 projectのcollector、LLM、保存前のcurrent state更新を実行する
- `commands/queue_interaction.py`はreview/promotion queueのinteractive入出力を担当し、queue policy自体は`registry.py`に残す
- `project_record.py`はproject shape、保存payload、fetch/list/save、normalized exact lookup、project key allocationを所有する
- `registry.py`はcollector/evaluation orchestration、finalize、merge/queue policyを所有する。repositoryのprovider別取得や解析素材抽出は所有しない

`source_sync.py`と`enrichment_pipeline.py`はproject stateや保存を重複実装せず、`project_record.py`を直接利用します。
`project_record.py`から`registry.py`への逆依存と、旧内部symbolを維持するためのre-exportは置きません。

## データの持ち方

現行DBは`registry_meta.schema_version=2`を必須とします。`projects`はscalar、`profile_json`、`state_json`を持ち、identifier、relation、operator decisionはそれぞれ`project_identifiers`、`project_relations`、`project_decisions`へ保存します。merge済みsource keyは`project_key_aliases`、調査履歴は`investigation_runs` / `collector_runs`です。

人間向け`profile_json.core.notes`と機械生成履歴の`state_json.evidence.system_notes`はwrite pathで分離します。current DBは分離済みであり、project読込時にnote本文をpattern分類する処理は持ちません。`collector_runs`も`result_json`にcountを持つ現行projectionだけを読み、旧`records_json` / `failures_json`本文へのfallbackは行いません。

SQLite接続は30秒のbusy timeoutとWAL modeを使います。Web background jobが無い時でも、detail表示、merge analysis、CLI操作、別Web requestが同じDBを開くため短いlock競合は起こり得ます。WALは通常のread/write競合を減らしますが、同時writeや長いtransactionを完全に排除するものではありません。Web server内のwrite操作は`mutation_lock`で直列化しますが、CLIや別processとの排他はSQLiteのlockとbusy timeoutに委ねます。

新規DBの作成は`project-registry init-db [--db-path PATH]`だけが明示的に行います。通常CLI、Web、background
workerは既存のschema version 3 DBを必須とし、missing pathや未初期化fileから空DBを暗黙作成しません。version 2 DBも通常openでは暗黙移行しません。
新規schema初期化はschema versionの保存まで同じ接続内でcommitします。既存DBのopenではschema versionを
同値upsertせず、open処理由来のwrite transactionを保持しません。

旧schemaは通常起動で自動変換せず、現行CLIにも旧DBを開く・変換する入口はありません。

`repair-db`はv2 current stateの正規化と派生評価更新を行い、schema migrationには使いません。

調査履歴は自動削除しません。`prune-investigation-history`の既定policyは完了から90日以内の全runとprojectごとの最新3 runを保持し、それ以外の`investigation_runs`と対応する`collector_runs`を手動削除対象にします。dry-runが既定です。通常のDELETE後にできたSQLite free pageは再利用されますがfile sizeは縮まないため、実容量を縮める場合だけ別processを停止して`--execute --vacuum`を実行します。operator decisionとcurrent project stateはこのprune対象に含めません。

`investigation_runs`の4つの論理JSON列はheader付きzlib BLOBです。`storage.py`の共通codecが保存・読込を担当し、
viewer、replay、LLM/repository evaluation export、storage cleanupは直接`json_extract`や`LIKE`を使いません。
既存TEXT rowの一括変換は`compress-investigation-history`でdry-runとexecuteを分けます。

Webのproject一覧検索は`projects` rowを起点に、`project_identifiers`、`project_relations`、`project_decisions`を`EXISTS` queryでfilterします。一覧候補作成時に全projectのnormalized childをhydrateしません。project detailは選択した1件について各child tableを直接取得します。

## 旧 discovery フローの扱い

旧 `discover.py` 系の独立フローは廃止しました。今後の本線は `project-registry` だけを前提にします。

ただし、次の発想は今後も流用価値があります。

- 手動で見つけた候補を一旦積む inbox
- `first_seen_at` / `last_seen_at` / recent activity を使った差分ベースの priority 補正
- 差分だけをざっと確認する report view

一方で、次は戻しません。

- `discover.py` 専用の snapshot/state テーブル群
- collection / GitHub / Tangled / manual を別レポートへ流す独立パイプライン

## source設定とpolicyの境界

設計上、operatorが運用中に変更するsource URL、enabled状態、query、request budget、取得scopeはconfigへ
置けます。一方、payloadの解釈、evidence抽出、identity/ownershipの信頼条件、classification、auto-attachや
promotionの条件はcodeとtestを正本とします。

通常導線では`config/sources.json`がintroduced、collection inventory、GitHub、Semble、Tangledのoperator設定正本です。
sourceごとのenabled、URL、query、tracked collection、pages/per-page/limit/lookback等のrequest budgetを持ち、
application sync serviceが解決します。CLI/Web/workerから明示された値はそのrunだけ上書きします。disabled sourceは
network request前に拒否します。この境界はgeneric plugin frameworkを実装済みという意味ではなく、source追加の
たびにconfig schemaや抽象interfaceを増やす目標でもありません。builtin lexicon prefix、payload解釈、evidence抽出、
matching/classification/auto-attachはcodeとtestのままで、config DSL化しません。

## project record の最小コア

優先して埋める項目:

- `name`
- `website_url`
- `service_urls`
- `tagline`
- `tagline_is_provisional`
- `service_status`
- `project_repository_urls`
- `introduced_url`
- `tracking_state`
- `tracking_note`

詳細な field 一覧は [project-record.md](project-record.md) を参照してください。

### URL field の整理

canonical fieldは`website_url`、`introduced_url`、confirmed/candidateのservice・repo identifierです。`website_url`は代表公開URLであり、service identifierとは独立しています。補助pageは`resource_url_records`に残します。

現行の`normalize_url()`はscheme/host/pathを正規化し、末尾slash、query、fragmentを破棄します。queryや
fragmentだけが異なるURLは同じidentifierとして扱われます。これは実装済みの現行制約ですが、queryで
resource identityが変わるserviceに対して妥当かは未検証です。具体例が出るまでは例外や別URL型を追加しません。

### Trust graph

この節は現行`trust-graph-v1`の実装、保存、処理経路を説明します。登録プロジェクトへの帰属、信頼グラフの責務、接続マトリクス、relation別の静的用途は[登録プロジェクトへの帰属と信頼](project-attribution-and-trust.md)を正本とします。

`trust-graph-v1`のpage↔repository edgeは、`project_relations`の現在の観測から導出します。
`repository_trust.project_edges()`が共通投影で、candidate probe、URL trust評価、Explain、shadow比較が同じ投影を使います。
`trust_graph.graph()`は判定用、`stored_graph()`は保存用です。page↔repository以外のedgeと評価履歴は
`state_json.evidence.trust_graph`に残します。DB schemaは増やしていません。

- `project_repository`のwebsite/service→repo、repo→website/serviceを方向付きedgeへ投影し、confirmedだけを強い経路として扱います。
- `repository_homepage`は単独ではedgeにせず、confirmedな逆向きのpage→repoとexact URLが一致する場合だけ`repository_homepage_pair`を導出します。
- HTMLの解釈、READMEリンク、LLMの同一project判断もsource/method/locator、observed_at、context付きrelationに保存します。
  LLMは許可された入力pairに対する`same_project`をconfirmed、`related`/`uncertain`をcandidateとして保存します。
  出力にないpairは前回判断を維持し、明示的な新判断だけが同じpairを置換します。
- 同一page↔repo観測のclassificationは時刻が新しい結果で置換します。source/method/locatorが異なる独立観測は保持します。
  取得失敗では以前のpage観測を削除しません。
- URLの現在の支持はrelationと現在の採用済み起点から再評価します。保存された評価やadoptionのsource_refは過去の判断の説明であり、現在の支持の代用にはしません。
- `relation_corroboration`も通常のtrust評価・guard・採用decision記録を通します。
- 旧page↔repo保存edgeは現在の判定には使いません。通常saveは未解決edgeを一括削除しません。
  `migrate-repository-trust`のdry-runはcurrent relation、復元可能なLLM判断、要reviewに分類します。
  review済みplanの実行時にDBの再照合を行い、説明できるedgeだけを除去し、復元可能な判断をrelationへ保存します。
  要reviewのedgeは保存したまま残り、再調査または個別reviewが必要です。

- node kind は `service_url` / `repo_url` / `handle` / `did` / `domain` / `collection`
- 既存の `account` identifier はUI/storage上のroleであり、trust graph nodeにはしません
- edge は `subject` / `predicate` / `object` / `source` / `locator` / `method` / `classification` / `eligible_for_auto_confirm` / `observed_at` / `context` を持ちます
- relationの`confirmed`はpredicateの直接事実が確定したことを表し、endpoint identifierのconfirmedやtrust edgeへの採用を意味しません。信頼経路ではpredicate、方向、roleを静的policyへ照合し、適格なedgeだけを使います
- 実装済みの実動作適用は `service_url` / `repo_url` の自動confirm、`collection` ownership判定、`did` / `handle` / `domain` のtrust評価・監査です
- `did` / `handle` / `domain` は単独identifierとして評価できますが、通常enrich導線で新しいconfirmed developer accountへ自動保存昇格する処理は未接続です
- 未clearの`confirm_identifier`はgraph外anchorです。sourceでoperator入力と採用済みsource syncを区別します
- `repo_url` decisionはactiveなconfirmed `project_repository`に対応する場合だけanchorにします。defer/blockされたidentifierはdecisionが残っていてもanchorにしません
- 移行由来の根拠不明confirmed identifierは監査対象であり、新規URL昇格のtrusted anchorにはしません

`seed` / `manual_seed` roleは正本DBから移行済みで、現行コードは生成・読込しません。現行の`add-seed`はfactual roleを直接confirmedへ投影し、その採用を`confirm_identifier`へ記録します。seed scalarは初期入力の由来・検索用で、anchor判定には使いません。

service URLのrootに対するblock/deferは、その配下の自動候補にも適用します。ただし、子pathそのものにactiveな`confirm_identifier` decisionがある場合はそのexact URLをguardの例外にします。operatorが後から同じexact URLをblock/deferすると、先のconfirm decisionをclearしてguardを優先します。

URL candidate は、trusted anchor との間に `eligible_for_auto_confirm=true` の強い有向経路が双方向にある場合だけ自動confirmできます。片方向の `repo -> service`、片方向の `service -> repo`、READMEの任意URL、profile link、lexicon JSONの存在、fork/upstream、host suffix match は単独では自動confirmに使いません。

first-party page上のrepository linkは、完全HTMLやproject名の類似ではなく、linkのexact locator、canonical repository root、可視文字、accessibility label、子要素label、最寄り見出し・block、landmark、同一groupとpage内のrepository数からなるbounded contextで評価します。`Open source`、`View source`、単独`Source`、`Repository`等の直接表示はGitHub、Tangled、Codeberg、GitLabで共通に扱います。provider名だけ、`Fair source`、mirror、dependency、外部project一覧は明示的な公式repository表示とは扱いません。可視文字のないlinkはpage内にrepositoryが一つだけで、listや外部参照文脈にない場合だけ静的に支持できます。

この静的判断がconfirmedになり、subject URLがcurrent confirmed `service`または`project_site`と完全一致する場合だけ、repository identifierをcandidateへ投影します。relationのconfirmedは方向付き関係の意味が確定したことを表し、identifierのconfirmedではありません。補助docs pageやcomponent外pageのrelationはcandidateに留め、identifier candidateを作りません。旧anchor text、owner、project名一致によるcandidate化・confirmはservice/resource page経路では併用しません。

candidate repositoryは通常のrepository enrich、project nameのidentity token、紹介文生成用のLLM evidenceには使いません。confirmedなpage→repository relationから作ったcandidateに限り、repositoryのhomepage/READMEにある逆向きservice linkだけを先に収集します。repo側の現行逆リンクによって相互trust pathが成立した時点でrepositoryをconfirmし、その後に初めて通常repo contentを適用します。LLM relation assessorによる自動昇格は接続していません。LLM investigation planningには、追加調査先を決めるためURLだけを渡してよいものとします。

`enrich`は全collector・LLM evidenceの適用と通常cleanupを終えた後、URL candidateのtrust評価を一度reconcileします。これによりrun後半で逆向きedgeが加わった場合も同じrunでaggregateな`service_url` / `repo_url`をconfirmします。個々の`source_repository_record`が持つ観測時のconfidenceは変更しません。成功した同一hostのHTTPからHTTPSへのredirectでHTTPS側がconfirmedなら、HTTP側は独立candidateとして昇格させず削除します。`repair-trust-graph`は従来どおりevaluationの再計算・保存だけを行い、identifierを昇格しません。

collection ownership は、operatorが採用したconfirmed `owned_nsid`、confirmed developer DIDとの相互trust path、またはoperator/trust-policy confirmedなroot serviceとの相互trust pathがある場合だけ成立します。operatorの採用はactiveな`confirm_identifier`で表します。developer DIDをanchorにする代表的なpathは`did_publishes_schema`と`collection_authority_domain -> domain_exact_handle -> handle_resolves_did`の組です。root serviceをanchorにするpathは、完全一致hostに限った`service_exact_domain` / `domain_exact_service`と、`_lexicon.<authority>`のDNS解決後に同じcollectionのschema recordを取得できた`domain_publishes_lexicon_record` / `collection_authority_domain`の組です。path付きservice URL、host suffix一致、未採用serviceはcollection ownership根拠にしません。

identity identifier は、manual/operator anchor との間に相互 path がある場合だけ `trust-graph-v1` 評価でauto-confirm可能です。`handle -> did` の解決だけでは片方向なので不足で、`did_document_claims_handle` のようなDID document側の逆向きclaimが必要です。domainは exact handle と同一値の場合に限り `domain_exact_handle` / `handle_exact_domain` を使います。suffix一致やhosted handleはdomain ownership根拠にしません。

確認用 command:

- `project-registry explain --topic trust -p <project_key>`
- `project-registry audit-trust-graph`
- `project-registry repair-trust-graph`

`audit-trust-graph` は読み取り専用で、既存 confirmed identifier を次のように分類します。`repair-trust-graph --execute` は edge/evaluation の再計算を保存します。`--downgrade-unsupported` を付けた場合は、operator decision や相互 trust path で説明できない confirmed identifier を候補・派生相当へ降格します。

- `needs_reprobe`: 片方向の強い edge だけがあり、service/repo の再検証が必要
- `should_downgrade`: operator decision でも相互信頼でも説明できない
- `guarded_confirmed_identifier`: block/defer 済みなのに confirmed 相当で残っている

`repair-trust-graph` はデフォルトでは trust edge/evaluation の再計算結果だけを保存します。confirmed identifier の降格は `--execute --downgrade-unsupported` を明示した場合だけ行い、`needs_reprobe` は降格せず再検証候補として残します。

provider別repo viewとresource URL一覧は`provider_repo_url_values()` / `resource_url_values()`で導出します。rootと`www`は観測済みredirect/canonical/content relationがある場合だけaliasとして畳み、他subdomainは独立surfaceとして残します。

same-siteの`/docs`、`/documentation`、`/about`、`/blog`、`/faq`、`/help`配下の情報ページと、`/login`、`/signin`、`/signup`、`/register`などの認証入口はservice identityではなく`resource_url_records`へ残します。これは補助ページをconfirmed service URLへ過剰昇格させないための暫定heuristicで、path名だけを長期的な信頼根拠とはみなしません。service follow-up probeやdeep page probeの結果はservice statusの補助evidenceには使いますが、補助ページURL自体をconfirmed service URLへ昇格しません。`/app`はapp surfaceとして使われることがあるため、この補助ページ判定だけでは落としません。`block_identifier` / `defer_identifier`はservice identifierとそのservice観測・relationへ作用します。同じ値のWebsiteや補助pageは別の責務なので自動削除しません。

same-site familyは、PSLとknown/configured multi-tenant suffixを反映した`root_domain()`が一致するhostだけです。
同じregistrable domainのsubdomain、または同じhosted suffix上の同一tenantはsame-siteですが、異なるregistrable
domainで先頭labelだけが同じhostはsame-siteにしません。

`project_registry_core/runtime_view.py`がnormalized child tableとcollector/evaluation向けflat dictの対応を一元管理します。既存projectの保存は完全にhydrateされたruntime viewだけを受け付け、partial dictからの更新によるidentifier/relation/decision全消去を拒否します。runtime markerは保存JSON、investigation snapshot、Web payloadへ出しません。

runtime viewはnormalized rowの無変更roundtripを維持します。candidate developer DIDなど、flat dictだけでは元rowのrole/classificationを表せない値は内部runtime stateに保持し、変更がない保存では元rowをそのまま書き戻します。NSIDは`owned_nsid`へ統一し、`resolved_nsids`をconfirmed、`input_nsids`をcandidateのruntime viewとして使います。NSIDの代表値は生成しません。

代表公開URLの正本は`projects.website_url`です。normalized service URLはconfirmed/candidateとも`service` roleへ統一します。同じ値がWebsiteとserviceの両方にあってもよい一方、通常の正規化、candidate confirm、収集では一方から他方を補完せず、Webでも一致をprimary serviceとして表示しません。

exact identifier lookupは`project_identifiers(kind, value, classification)` indexを使います。source syncで使う
NSID、service URL、repository URL、domainとprovider別repositoryはproject payloadを全件hydrateしません。
collection namespace、registrable namespace、service host familyなどのfuzzy候補はexact lookupの後段に分離し、
安全判定を通らない一致はsuggestionに留めます。`handle` / `did`を含むexact lookupはnormalized indexだけを使います。
flat runtime viewは一部のroleを表現できないため、そこからnormalized rowの不足を推定しません。保存時はruntime markerに保持した元rowとの差分だけを反映し、無変更roundtripを維持します。

`audit-url-fields`はcanonical fieldの件数とinvariant違反を読み取り専用で報告します。`profile_json.app.links`はproject保存時にcanonical identifierとconfirmed resource recordから純粋に再構築します。既存payloadへの一度きり再構築は適用済みです。

## enrich の流れ

`enrich` は次の順で動きます。

1. seed由来の事実収集
2. service / repo / lexicon の確認済み情報を更新
3. lexicon repository単位で namespace 集約
4. confirmed repositoryの解析素材を収集し、revisionごとのrepository analysisを更新
5. GitHub repo / GitHub search / Bluesky search の structured evidence を収集
6. evidence を project へ統合し、rule-based evaluation / finalize を実行
7. local LLM による summary / category / status 補完
8. 再度 finalize し、duplicate / merge / 昇格判定を更新

### 調査 pipeline の実装状況

調査 pipeline の責務、evidence classification、正本関係、partial failure、replay、LLM の位置づけは
[investigation-model.md](investigation-model.md) を正本とします。ここでは実装ファイルと現行接続状況だけを記録します。

実装済み:

- `project_registry_core/evidence.py`
  - `EvidenceBundle` schema version 2
  - `EvidenceRecord`, relation、`CollectorFailure`, `CollectorResult`
- `project_registry_core/investigation.py`
  - deterministic / LLM query planning
  - provider共通repository snapshot collectorとGitHub / Codeberg adapter
  - GitHub repository search、Bluesky actor/post/feed collector
  - collector は project dict を変更せず evidence bundle を返す
  - source別 retry、request budget、rate-limit stop
- `project_registry_core/integration.py`
  - GitHub / Bluesky / service / Tangled / collector snapshot evidence を既存 project field へ適用する
  - GitHub lexicon hint の owned / reference 判定は収集後に行う
- `project_registry_core/repository_analysis.py`
  - confirmed repositoryのmetadata、README、tree、manifest、configuration、Lexicon、限定source抜粋をprovider共通の解析素材へ縮約する
  - repository revisionと解析policy単位で結果を再利用し、成功結果と最新試行を分けてcurrent stateへ保存する
- `project_registry_core/repository_sources.py`
  - GitHub / Codeberg / Tangledのmetadata、README、tree、個別file取得と短期cacheを所有する
  - provider固有のHTTP契約を閉じ込め、projectへの適用判断は行わない
- `project_registry_core/repository_material.py`
  - 取得済みtreeとfileからmanifest、configuration、Lexicon、限定source hintを決定的に抽出する
  - network取得、LLM判定、project mutationは行わない
- `project_registry_core/evaluation.py`
  - ownership、atproto寄与度、service status の純粋評価本体
- `project_registry_core/storage.py` / `replay.py`
  - investigation run保存、network-free replay、fixture benchmark
- `project_registry_core/service.py` / `tangled.py` / `relations.py`
  - service検証、Tangled PDS/UFOs、typed relation
  - service verification evidenceの縮約と、`lane + requested_url`単位の最新32 surface projectionは
    `service.py`が所有する
- `project_registry_core/domain_policy.py`
  - 正式依存`tldextract`へvendored PSLだけを入力し、known/configured multi-tenant suffix、registrable domain、
    same-site判定を所有する。依存またはvendored PSL欠落時に別の自作policyへfallbackしない
  - suffix設定の読書きもこのmoduleが所有し、`registry.py`は旧symbolをre-exportしない
- `project_registry_core/http_transport.py`
  - outbound URL検証、内部host遮断、redirect先再検証、bytes/JSON requestを所有する
  - collectorはこのmoduleをtest seamとして使い、`registry.py`は旧transport symbolをre-exportしない
- `project_registry_core/identifiers.py`
  - canonical URL/NSID、抽出URLの句読点除去、authority domain導出、generic値を除外するalias token正規化を所有する
  - domain/HTTP transportも同じURL正規化を使い、`registry.py`は旧symbolをre-exportしない
- `project_registry_core/repo_urls.py`
  - GitHub、Tangled、Codeberg、GitLab、SourceHutのrepo URL正規化とprovider/owner判定を所有する
  - `registry.py`はconsumerとしてmoduleを呼び、旧repo helper symbolをre-exportしない
- `project_registry_core/naming.py`
  - HTML titleの短縮、name candidate正規化、service/repo source labelのpure helperを所有する
  - project固有のcandidate採否・score・mutationは`registry.py`に残す
- `project_registry_core/llm_transport.py`
  - command/OpenAI互換endpointの呼出し、response parser、timeout/error分類、request metricsを所有する
  - prompt/evidence構築とprojectへの適用はtransport外に残し、testはcommand/endpointをfakeへ差し替える
- `project_registry_core/project_record.py`
  - projectの既定shapeと保存前正規化、scalar/profile/state payloadへの投影を所有する
  - schema v3 rowのhydrate、fetch/list/save、normalized exact lookup、project key allocationを所有する
  - `database.py` / `runtime_view.py`と専門moduleを利用し、`registry.py`へ依存しない
- `project_registry_core/application/`
  - project query、tracking、decision、merge/split、review queue、sync、investigation、maintenance、enrichのCLI/Web共通service
  - `argparse.Namespace`やstdoutへ依存せず、明示引数とresult payloadを使う
  - `presentation.py`はproject detail、queue、search payloadを組み立てるtransport非依存service
- `project_registry_core/jobs.py`
  - 長時間処理のrequest/event/resultと協調的cancellation契約
- `project_registry_core/commands/`
  - application serviceを呼び出し、CLI向けJSON/textへ変換するadapter
  - review queueとstalled/ignore/out-of-scopeのinteractive入出力も`commands/review.py`が担当する
- `project_registry_web/`
  - schema v2 Web API adapter、HTTP server、build不要のHTML/CSS/ES modules
  - API handlerはapplication serviceだけを呼び、hydrated project dictを直接保存しない
  - `jobs.py`は別process worker、直近event、停止・再開を管理する
  - JSON responseはDB schema versionと独立した`api_version: 2`を持つ
  - presenterがidentifier family、relation summary、investigation summary、project revisionを生成する
  - mutationは`expected_revision`を必須とし、stale detailを`409 stale_project`で拒否する
  - server内のmutationはlockで直列化する
- `project_registry_core/worker.py`
  - stdinの`OperationRequest`をapplication serviceへ渡し、event/resultをJSON Linesで返す
- `project_registry_core/registry.py`
  - collector/evaluation orchestration、finalize、merge/queue policyと未分離の補助処理
  - `collect -> apply -> finalize/evaluate -> save` の orchestration
  - `argparse.Namespace`を受け取る`cmd_*`は残っていない

現行の依存方向は完全分離後の目標図ではありません。確認済みの境界は次の通りです。

- `database.py`はschema、接続、normalized rowのsnapshotを所有する。通常open/save pathは`registry.py`をimportせず、
  maintenance専用のnormalized roundtrip verifierは`project_record.py`の`fetch_project` / `save_project`を遅延importする
- `application/`はCLI/Web共通serviceとして分離済みだが、多くのserviceがdomain/storage facadeとして
  `registry.py`とproject保存用の`project_record.py`を用途別にimportする。`commands/`も同じ境界を使う
- Web APIの主要mutation/queryはapplication serviceを通る一方、server bootstrap、検索、補助read pathには
  `registry.py`または`project_record.py`への直接依存が残る
- `database`、`runtime_view`、`evidence`、`investigation`、`integration`、`evaluation`、`service`、transport、
  identifier/repo URL/naming/domain policyの抽出moduleを`registry.py`経由へ戻すre-exportは置かない
- 残るqueue、enrich orchestrationの分割はroadmapで追跡し、
  現時点でapplication/databaseが巨大moduleから独立済みとは扱わない

この現行責務mapは一時的な移動計画ではなく内部仕様のsnapshotなので、本書を正本とします。同内容を
`docs/notes/`へ複製せず、将来の分割候補だけをroadmapで追跡します。

動作確認済み:

- collector単体が入力 project を変更しないこと
- GitHub relevance threshold、最大1 repo採用、最初の採用queryでの打ち切り
- GitHub README URL、owned/reference lexicon hint の既存反映
- Bluesky/GitHubのpartial failureとtimeout/rate-limit分類
- fixture evidence から ownership / atproto寄与度だけをnetworkなしで再評価できること
- temp SQLiteでの `enrich_project -> save_project -> fetch_project`
- 保存 run の replay、6 case benchmark、fake browser、Tangled snapshot replay

未接続:

- OAuth `include:` permission set の再帰解決
- Web UI の run/evidence inspection

現行の制約:

- project row は現在状態の正本、run table は再評価可能な調査履歴
- resource page は既存のresource-page mutatorを project copy 上で実行し、許可 field の patch として統合する暫定実装
- cache と circuit breaker は process/source 単位で、複数 project を跨ぐ永続 policy ではない

collector reliability と replay の設計途中の論点は
[collector-reliability.md](../notes/collector-reliability.md) と
[evidence-replay.md](../notes/evidence-replay.md) に分離しています。

lexicon enrich に成功した場合、LLM evidence には lexicon schema の縮約サンプルも入ります。さらに `ufos-api.microcosm.blue/records` から recent record sample を取得できた場合は、それも判断材料に含めます。

confirmed repositoryは通常`enrich`中に専用のrepository analysisを行い、結果を`state_json.evidence.repository_analysis_results`へ保存します。candidate repositoryは相互trust確認に必要な限定取得以外では解析せず、confirm後に初めて対象にします。GitHubとCodebergはmetadata、README、tree、主要manifest、documentation / deployment設定、Lexiconを取得し、READMEとdescriptionだけでは目的を説明できない場合に限ってsource候補を最大4 file、各file最大2 excerptへ絞ります。Tangledは現行取得能力に合わせてmetadata、README、限定treeを使い、取得できない範囲を`coverage`で`unavailable`とします。未対応providerも黙って欠落させず`coverage.reason=unsupported_provider`を保存します。

repository analysisはrepo全体を単一`artifact_kind`へ分類しません。出力は短いsummaryとその`summary_evidence`、複数の`observations`、証跡付き`capabilities`、caseを保持したcollection利用です。観測述語はCLI entrypoint、import可能package、service entrypoint、deployment設定、Lexicon定義、host integration、documentation source / generator / site URL / deploymentなどの非排他的な事実です。`basis=declared|observed|inferred`はrepo内の観測方法でありtrust classificationではありません。pathだけのhost integrationは`inferred`に留まり、browser extension宣言にはmanifestまたはREADMEの明示を必要とします。Lexicon JSONの存在はcollection ownershipを確定しません。

証跡は別のfact IDを作らず、解析対象`source_revision`へ結び付く`path + line`または`path + json_pointer`を各claimへ直接保存します。同じ根拠本文をclaimごとに複製せず、重複を除いたbounded excerptを結果の`evidence_excerpts`へ最大6件、各600文字で保存します。triageへ渡す時は旧形式のclaim内excerptも同じ形へ縮約します。LLMには、README、metadata、manifest、configuration、Lexicon、source excerptから作った利用可能な根拠位置を、prompt内だけで有効な`E01`のような短い番号とともに渡します。LLMは番号だけを返し、保存前に対応する根拠位置へ戻します。番号自体は保存しません。ファイル一覧のpathだけでは内容を裏付けられないため、LLMが新しいsummaryやclaimの証跡として使うことはできません。tree pathから安全に決められる観測は決定的な抽出処理だけが追加します。summaryにも1件以上の根拠位置を必須とします。repository本文はuntrusted dataとして扱い、READMEやsource内の指示に従わないことを専用promptとtriage promptの両方で明示します。

cache identityはcanonical repo URL、`source_revision`（取得できないproviderでは`material_hash`）、`policy_version`の組です。成功結果と`last_attempt`は分離し、失敗時に旧成功結果のrevisionやpolicyを書き換えません。直前の根拠付き成功結果はtriageへ`stale=true`で渡しますが、旧形式の根拠なしsummaryは渡しません。timeout、rate limit、HTTP系など一時的な外部失敗だけは同じmaterialとpolicyへの再試行を24時間抑制し、LLM未設定、endpoint非互換、invalid response、local command失敗は設定修正後の手動再実行を妨げません。run evidenceにはbounded materialとそれを参照する解析結果を別recordで残し、current stateにはrepoごとの最新成功結果と最新試行だけを残します。

LLM endpointまたはmodel出力をJSONとして解釈できなかった場合、run metricsには失敗段階、UTF-8 byte数、SHA-256、JSON構文エラー位置、先頭と末尾がJSON記号か、code fenceを含むかだけを記録します。利用不能だった応答本文は保存しません。これにより、出力上限で途中終了した可能性と形式違反を、repository内容を追加保存せず区別できます。

triageへ渡す解析結果はconfirmed repoだけを対象に最大6件とし、operator-confirmed repo、代表repo、coverageの順で優先します。repository observationはproject categoryやidentifier confirmを直接上書きしません。categoryはservice、collection、現在profileなどを合わせたproject-level triageが決めます。`lexicon`はschema自体が追跡対象である積極的根拠を必要とし、NSID seedやLexicon JSONの存在だけでは選びません。repositoryが宣言したdocumentation site URLは`docs` roleのcandidate resource URLに留めます。

`enrich --all` は既定で `tracking_state=active` のみ対象です。`stable`、`stalled`、`out_of_scope` は、明示的に include したときだけ回します。

`enrich`のproject選択、保存、checkpoint、再開は`project_registry_core.application.enrichment`へ接続済みです。CLIは`project_registry_core.commands.enrichment`で引数を`OperationRequest`へ変換し、serviceのevent dataをJSON linesへ変換します。

`enrich --all` の中断/再開は DB schema ではなく、`data/enrich-runs/*.json` の checkpoint で扱います。開始時に選択した project key listを固定し、各projectの保存後に`completed_project_keys`を更新します。Ctrl+Cは協調的な停止要求となり、処理中projectを保存した後に`status=interrupted`と再開commandを記録します。次回`--resume-run`は完了済みkeyを飛ばします。契約の正本は[background-jobs.md](background-jobs.md)です。

Web UIはschema v3 repositoryへ接続済みです。project detail payloadは`project`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`を分離します。identifier groupは同値行をまとめつつ、`entries`にrole/classification/providerの対応を保持します。Websiteはprofile、service URLはidentifierとして別々に表示し、値が同じでもprimary markerは付けません。DID/handle/accountは同じaccount metadataを持つ場合に1つのidentity groupへ畳みます。confirmはcandidate entryから開始でき、同じ値のblock/deferを解除してrole付きconfirm decisionとadoptionを保存します。profile編集で明示したnameは`operator_edit`候補として保存し、自動生成候補より優先します。日常的なproject query、tracking、identifier decision、split/mergeは`project_registry_core.application`を共有入口として使います。former/non-productionの表示・編集UIは未接続です。

enrich、sync、review classificationの別process runnerとWeb API/UIは接続済みです。job stateはserver process内、enrich checkpointはDB directory隣接の`enrich-runs/`に保存します。同じWeb serverからの同時background jobは1件に制限しますが、CLIなど別processとの排他は行いません。enrichはproject境界のprogress/cancellationとbulk checkpoint再開、server再起動後の固定rootからのcheckpoint発見を持ちます。syncはsource内page/item境界、reviewはcandidate境界でprogressとcancelを確認します。sync/reviewのcheckpoint再開とserver再起動後のprocess job一覧復元は未接続です。旧Web helperは暫定復活させません。Investigation tabにはrun一覧、上限付きevidence、要求時のread-only replay差分を接続済みですが、evidence全文は表示しません。

Explain serviceは`project_registry_core.application.explain`をCLIとWebで共有します。`service_status`、ownership、atproto contribution、merge candidateについて、保存済みcurrent stateとrelation sourceだけから結論、適用rule、支持/反証signalを返します。Explain実行中はnetwork collectorやLLMを呼ばず、replay結果もDBへ保存しません。Webの初期detailは全project hydrateを行わず、merge candidateだけを`/api/projects/{key}/merge-analysis`で非同期計算します。同service内ではduplicate candidate計算を1回だけ行い、その結果をmerge recommendationとExplainの両方へ渡します。source revisionが計算中に変化した場合は結果を破棄し、計算済みviewはDBへ追加しません。

## enrich の判断材料

`enrich` は「confirmed に上げる条件」と「candidate に留める条件」を分けています。LLM は後段ですが、一次情報で確定できないものだけを整理させる位置づけです。

### 1. lexicon から取るもの

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

### 2. GitHub repo から取るもの

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

### 3. service URL から取るもの

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
- `service_probe_supports_project()` は `trust-graph-v1` の URL 相互信頼で判定します
  - service page の GitHub / Tangled repo link は `service_links_repo`
  - `Source` / `repository`等の明示表示はconfirmed edgeにし、曖昧な表示や無表示はcandidate edgeにする
  - candidate edgeは既知のconfirmed/candidate repoとの組だけをtriageの関係判定へ渡し、`same_project`だけをauto-confirm対象へ昇格する
  - service probeのrelationは同じpageを正常に再観測した時に置換し、以前のlink textによる強い判定を残留させない。edge自体は保存しない
  - GitHub repository metadataのhomepageは`repository_homepage`として観測を保存する。confirmed first-party serviceから同じrepositoryへの明示的`project_repository` relationがあり、service URLとhomepageがexact一致する場合だけ、信頼経路用の`repo_links_service`を再構成する
  - candidate service URL は、manual/operator/trust-policy confirmed anchor との間に `repo -> service` と `service -> repo` の有向経路が両方ある場合だけ support/confirmed になります
  - activeなservice URL採用decisionのhostそのものはanchorとしてsupportします
- service page の repo link は、未知 repo を新たに confirmed へ昇格する根拠ではありません。`service -> repo` と `repo -> service` の相互信頼が成立する場合だけ URL confirmed に使います。
- `lexicon_resolution_entries` に resolved NSID / record URI / authority domain が揃っていても、それは `repo/NSID -> authority domain` の片方向根拠です。service host がそのauthority domainと一致して正常応答するだけでは、`domain/service -> project` の根拠として扱わず、confirmed service URLへ昇格しません。
- service page が project の NSID を直接言及しているだけでは、互換実装や利用関係と区別できないため、`domain/service -> project` のconfirmed service support根拠にしません。
- service page が authority domain、alias token、atproto marker を自称しているだけでは、`domain/service -> project` のconfirmed service support根拠にしません。
- service page 上の Bluesky profile link は `official_account` relation evidence としては残しますが、それ単独では `domain/service -> project` のconfirmed service support根拠にしません。
- probe結果がprojectを支持しないページは、既知repositoryとの関係判定に必要なrepo linkだけを内部evidenceとして使います。そのページ由来のtitle/description、docs/blog等のresource、Bluesky profileはprojectの公開profileへ投影せず、以前の観測で保存済みなら再観測時に除去します。
- 検証済みauthorityの完全一致hostが正常応答し、titleまたはdescriptionを持つ場合、汎用classifierが `unverified` でも `verified_authority_service_surface` evidence付きの `live` として扱います。`none` / `unreleased` / `beta` などの明示分類は上書きしません
- `service_status` の決め方:
  - parked domain, `404/410` は `dead`
  - `401/403` は `private`
  - coming soon は `unreleased`
  - invite/waitlist 文言は `invite_only`
  - beta 文言は `beta`
  - login surface や app shell が見えれば `live`
  - docs だけで app surface が無ければ `none`
  - `5xx` や network error は `unverified`
- confirmed に上げる条件:
  - probe が project を支持する
  - HTTP が `2xx/3xx`
  - URL がfollow-up/deep page由来の補助ページではない
  - `trust-graph-v1` で candidate URL と trusted anchor の間に相互の強い有向経路がある
- 上の条件を満たさないが page は支持している時:
  - `service_url_candidates` に留めます
- page が project を支持しない時:
  - その URL は project から外します
- 到達できたがproject supportが無いURL:
  - `service_verification` recordは保存します
  - `operates_service` relationは作りません
  - candidate repo linkやalias tokenだけではsupport根拠にしません
- `4xx` / fetch error は:
  - `failed_service_urls` に残します
- probe が project を支持し、HTTP が成功した時:
  - `supported_service_urls` に URL 単位で残します
  - NSID authority から推測した registrable root でも、後段 prune で未検証 guess として消しません

### 3.2 `developer_accounts` の confirmed / candidate

- 現在の account まわりは二層です
  - `developer_accounts`
    - confirmed 扱い
  - `candidate_developer_accounts`
    - service page / follow-up / Bluesky search 由来の未確認候補
- service/search 由来の handle は、まず `candidate_developer_accounts` に入れます
- `bsky.app/profile/{subject}` は service URL として扱いません
  - `{subject}` が handle 構文なら `handle`、DID 構文なら `did` として account evidence に分けます
  - `profile`, `user` のような一般 path 語だけではなく、構造化された handle / DID を優先して account-scoped URL を判定します
- confirmed に上がる主な条件:
  - confirmed service host と handle ドメインが整合する
  - 例: `https://example.com` と `alice.example.com`
- `developer_accounts` だけを identity 補強に使います
  - `candidate_developer_accounts` は ownership や service support の強い根拠に使わない方針

### name 候補の扱い

- repo seed / GitHub repo metadata 由来の repo slug は、短い汎用語へ切り詰めずに全体を名前候補として使います
  - 例: `atproto-visualizers` は `atproto` ではなく `atproto-visualizers`
- `Bluesky` / `atproto` / `bsky` のような汎用プラットフォーム名は、service title / resource title / LLM / lexicon 由来だけなら project name として採用しません
- ただし `https://bsky.app` の title が `Bluesky` であるように、source URL の host が名前と強く対応する場合は候補として残します
- ただし repo 自体がその名前を明示している場合は、repo 由来候補として別扱いします
- `Error` / `Not Found` / `Forbidden` のような汎用エラーページ title は、service/resource title および既存 name から除外します
- service/resource title と既存 name は title cleanup を通して評価します
  - 例: `Relay.app: Turn plain English into reliable automations` は `Relay.app` として扱い、tagline 部分を project name に残しません
- NSID authority domain から推定した URL の title は、NSID 固有 token と対応しない場合は name 候補から除外します
  - 例: `com.hectorsector.book.status` 由来の `https://hectorsector.com` が `@hectorsector.com on Bluesky` を返しても、`book` / `status` と対応しないため project name には採用しません
  - 同じ値が既存 `name` や LLM output にコピーされている場合も、元の service title 候補と照合して除外します
- LLM などが full NSID そのものを name として返した場合は弱い候補として除外します
  - lexicon name 由来の tail 候補は別扱いで残します
- `Home` / `About` / `Docs` / `Login` のような短いページ役割名は、service/resource title 由来なら name 候補から除外します
  - 同じ値が既存 `name` や LLM output にコピーされている場合も、元の service/resource title 候補と照合して除外します
  - repo 由来など、ページ title ではない経路の同名候補はこの rule では除外しません
- service/resource title 由来の名前は、title の意味 token の大半が seed / repo / NSID / authority domain に対応する場合だけ採用します
  - 例: `Book Status` は `com.hectorsector.book.status` と対応するため残します
  - 例: `Browse Listings` は `listing` だけが対応し、`Browse` がページ操作名なので除外します
  - 汚染値が既存 `name` や LLM output にコピーされている場合も、元の service/resource title 候補と照合して除外します

### 3.1 `service_status` の現在地と残件

現在の `service_status` は、以前の単純なキーワード分類から一段進めて、scalar columnと`state_json`の評価detailに次を持ちます。

- `service_status`
- `service_status_confidence`
- `service_status_evidence`

現在見ている主な signal:

- `http_<code>`
- `dns_failed`
- `dns_temporary_failure`
- `tls_failed`
- `timeout`
- `connection_refused`
- `parked_domain_detected`
- `login_surface_detected`
- `invite_text_detected`
- `beta_text_detected`
- `coming_soon_detected`
- `docs_only_surface_detected`
- `app_shell_detected`
- `mixed_launch_signals_detected`

現在の扱い:

- parked domain, `404/410`
  - `dead`
- `401/403`
  - `private`
- coming soon / launching soon
  - `unreleased`
- invite / waitlist
  - `invite_only`
- beta 文言
  - `beta`
- login surface / app shell
  - `live`
- docs-only
  - `none`
- `5xx` や network 系失敗
  - `unverified`
- launch 文言と app surface が混在
  - `unverified`

実装済み:

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

未接続:

- deep investigation の結果を既存 DB 全体へ反映する運用。既存 row の洗い替えには再 `enrich` が必要
- browser 認証後の機能確認と Web UI 専用表示

static probe、deep page、browser 検証の役割分担と次段階の検討は
[service-verification.md](../notes/service-verification.md) を参照してください。

### 4. first-party link の扱い

- service page / resource page のリンクは `classify_official_link()` で分類します
  - `source_repo`
  - `source_like`
  - `docs`
  - `blog`
  - `bluesky_profile`
  - `same_site`
- confirmed に上げるのは:
  - first-party page が repo を明示し、repo側からも同じserviceへ戻る信頼エッジがある時
  - same-site family の repo URL でも片方向リンクだけでは candidate に留めます
- それ以外の repo-like URL は candidate に留めます
- docs/blog/same-site 補助ページと、`support` / `status` / `privacyPolicy` / `termsOfService`
  など URL path から補助ページ role を判定できる同一siteリンクは `resource_url_records`
  に残し、`resource_url_values()` 経由で必要なら追加 probe の対象にします
- 投稿、profile、listなど補助ページ role を判定できない同一site content URL は
  `resource_url_records` に保存しません。これは補助URL evidence の過収集を防ぐための
  guardであり、既存DB内の過去recordを自動削除する cleanup ではありません
- `resource_url_records` はリンク発見ログではなく代表補助ページの集合です。正規化時に
  URL+role単位へmergeし、project全体、role単位、host+role単位のbudgetで絞ります。
  confirmed、直接的なsource、root/短い入口URL、既知roleを優先し、tag/archive/profile/post
  など大量contentになりやすいpathは低く扱います
- budgetは通常の`add_resource_url`だけでなく、supporting-page移送、collector `project_patch`適用、
  block/defer guard、shape hydration、project merge、saveの各境界で再適用します。外部patchやraw stateを
  直接渡しても、次の境界まで無制限listを保持する経路はありません

### 4.1 NSID ownership の現在の優先順位

`project_owned_lexicon_nsid()` は現在、次の順で `owned` を判定します。

1. confirmed `owned_nsid`そのもの
2. source syncが同じprojectへ割り当てた同一collection namespaceのNSID
3. `trust-graph-v1` で collection と confirmed developer DID の間に相互の強い有向経路がある
4. `trust-graph-v1` で collection と confirmed root serviceの完全一致domainとの間に、`_lexicon` DNS解決とschema record取得で裏付けられた相互の強い有向経路がある

意図は、「偽 NSID -> 既存サービス -> それっぽい account」という自己強化ループを止めることです。confirmed service host、authority domain、identity token、repo内lexicon JSONのいずれか単独では ownership を確定しません。service経路はactiveなtrusted anchor、root URLとauthority domainの完全一致、`_lexicon`解決、同じcollectionのschema record取得をすべて要求します。
collection namespaceからの拡張は、`dev.idiolect.recommendation` から `dev.idiolect.adapter` のように親namespaceが完全一致する場合だけです。`network.comind.memory` から `network.comind.void.memory` のような親子namespaceや深さ違いは自動owned扱いしません。

repo内にlexicon JSONがあるだけの観測は、accepted repo と accepted NSID が揃うまで `defines_schema` として保存せず、`mentions_collection` に落とします。`defines_schema` は repo と NSID の両方が project 側で受け入れ済みの場合だけ成立する relation です。repo seedはproject repositoryの根拠であり、そのrepoに含まれる全NSIDの所有根拠ではありません。`uses_collection`、`mentions_collection`、OAuth `requests_permission` は ownership を確定しません。
OAuth `include:` の再帰解決と organization/individual account の委任関係は未接続です。
domain、account、DID、NSID、repo、service 間の保証関係と検討中の根拠強度は
[identity-ownership-evidence.md](../notes/identity-ownership-evidence.md) に分離しています。

### 5. investigation collector の扱い

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

### 6. LLM が決めるもの / 決めないもの

- LLM が直接埋める主な field:
  - `name`
  - `tagline`
  - `summary`
  - `features`
  - `inspired_by`
  - `category`
  - `classification_reason`
  - `exclude_reason`
  - `watch_reason`
  - `manual_review_*`
  - `confidence`
  - `service_status` の補助更新
- ただし LLM 出力の identifier は保守的に扱います
  - `project_repository_urls`
    - 既に confirmed 済みなら confirmed 側に残す
    - 新規提案なら candidate に留める
  - `service_urls`
    - 既存の confirmed service と一致する時だけ confirmed 扱い
    - それ以外は candidate
  - `lexicons`
    - 既に `resolved_nsids` にあるものだけ採用

`service_status` についても、LLM は補助更新に留めます。最終的な保存値は rule-based probe の evidence を優先し、特に `unverified` は安易に `live/private` へ確定させません。

### 6.1 atproto 寄与度

`atproto_contribution_level` / `atproto_contribution_score` / `atproto_contribution_evidence` は
category と独立した derived field です。`ensure_project_shape()` と `save_project()` で
rule-based に再計算します。

- `core` / `90`
  - resolved NSID または lexicon schema sample がある
- `substantial` / `70`
  - recent record sample または record count を持つ collection observation がある
- `optional` / `40`
  - input NSID または external lexicon hint だけがある
- `auth_only` / `20`
  - `atproto_auth_only=true`で、上記の強いprotocol surfaceが無い
  - 旧`category=auth_only`は正本DBで個別移行済みであり、読み取り互換を持たない
- `unknown` / `0`
  - rule-based evidence が無い

`incidental` は level として予約済みですが、現時点で rule-based に確定する source は未接続です。
review queue は寄与度を sort score に足し、promotion queue も寄与度を補助点として使います。
LLM による曖昧ケースの補助分類は将来の設計目標です。
既存 row の保存値を洗い替える場合は `repair-db` を使います。queue 表示時は読み取り shape に
対しても再計算するため、DB へ書き戻す前でも sort には反映されます。

### 6.2 service status manual review の絞り込み

LLM の `manual_review_recommended=true` は保存しますが、`manual_review_kind=service_status_manual_check`
は単に `service_status_confidence=low` というだけでは review queue に載せません。

`llm_manual_review_recommended` signal を残すのは、手動確認の結果で次のいずれかが変わる時です。

- `promotion_status_resolution`
  - `draft` で、service status の解決が昇格判断に影響する
- `introduction_validation`
  - 未紹介の `project` / `personal_tool` で、usable service の confidence が `none` / `low`
- `conflicting_service_status_evidence`
  - `mixed_launch_signals_detected` があり、status evidence が矛盾している

採用した理由は signal の `details.action_basis` に残します。
`identity_resolution` など、service status 以外の LLM manual review kind は従来通りです。

### 6.3 保存済みprofileの更新

LLM triageのevidenceには、現在保存されている`tagline` / `summary` / `features`を
`current_profile`として含めます。LLMは新しい根拠が以前の説明を実質的に改善する場合だけ内容を変え、
根拠が変わらない時は現在の文面を維持します。

前回の`llm_source.evidence`と今回のevidenceを比較し、新しいsupported service URL、confirmedな
docs / blog / support URL、confirmed repository、または確認済みwebsiteで新たに観測した実利用面を、
説明に使える強いsourceの追加とみなします。実利用面はlogin、app shell、browserで確認したformや
public app surfaceに限り、beta等の文言だけでは認めません。この時だけ非空のsummaryと暫定taglineを
新しいLLM提案へ更新します。同じsourceの再取得や文章差だけでは置き換えません。暫定でないtaglineは
強いsourceが増えても自動更新しません。

比較結果の専用fieldは保存しません。前回LLM入力は既存の`llm_source.evidence`に残り、今回の成功後は
同fieldが新しい比較基準になります。過去に更新機会を逃したprofileはこの規則だけでは遡及更新されないため、
内容を確認した上で個別に適用します。schema / serviceなど内容別の専用signalは設けません。

`llm_output.name`は履歴ではなく、直近に成功したLLM reviewの現在提案です。成功したreviewを適用する時は、
`name_candidates`に残る以前の`source=llm_output`候補を現在提案で置き換えます。collectorまたはLLM呼び出しが
失敗して新しいoutputを適用できなかった場合は、直前の現在提案を維持します。operator edit、service title、
resource title、repository名など、LLM以外をsourceとする名前候補はこの置換の対象外です。

`finalize_project_shape()`も保存済み`llm_output.name`とLLM名候補を同期してから代表名を再評価します。このため、
旧実装で複数のLLM名候補が蓄積したprojectは、通常の保存または`repair-db`で現在提案だけに補正されます。
過去のLLM出力を調べる必要がある場合は、current stateの候補配列ではなくinvestigation runを参照します。

### 7. 昇格判定

- `apply_promotion_state()` の blocker は次です
  - seed が無い
  - confirmed website/repository/lexicon が無い
  - `service_status` が `unknown`
  - `summary` が無い
  - `tagline` が無い
  - `features` が 3 件未満
  - `category` が無い
  - `llm_source` が無い
- blocker が 1 件でもあれば `status=draft` のままです

## sync の判断材料

`sync-collections` / `sync-github-search` / `sync-semble-collections` は未知候補の project 作成が
既定 ON です。候補を作らず `unmatched_entries` の棚卸しだけ行う場合は `--no-create-unmatched`
を付けます。`sync-introduced` は紹介済み判定専用なので、新規 project を作りません。

### `sync-introduced`

- 入力は Bluesky post search の結果です
- post 本文や facet URL から seed を抽出し、`collect_applog_entries()` で project 候補に束ねます
- seed として採るもの:
  - collection NSID
  - GitHub/Tangled の repo root
  - path を持たない service root
- seed として捨てるもの:
  - `bsky.app`
  - `raw.githubusercontent.com`
  - YouTube
  - `docs.bsky.app`
  - `docs.tangled.org`
  - `blog.tangled.org`
  - path 付きの一般 service URL
- DB 側では exact identifier match のみ見ます
- 既存 project に一致した時だけ:
  - `introduced = true`
  - `introduced_url`
  - `discovery_sources += bsky_introduced_sync`
- 新規 project は作りません
- query ごとの watermark を `sync_state` に保存します
- 初回は 7 日 lookback、以後は Bluesky search の `since` と `sort=latest` を使います
- unmatched entry は state に保留し、後から project が登録された時に再照合します

### `sync-collections`

- まず inventory API 全件を取得します
- 通常の増分実行では、既知 collection の activity refresh を import 対象の選別とは別に毎回行います
  - `count`
  - `recent_count`
  - `min`
  - `max`
- import 対象にするのは:
  - collection NSID が正常
  - 通常の増分実行では既知 NSID ではない
  - 最新活動の `max` が watermark より新しい
  - watermark が無い初回は `lookback_days` 以内
- `--all-history`ではwatermarkと既知判定を外し、先行activity refreshを行わず、保存済みcollection stateを現在の全payloadから再構築します
  - `collection_inventory_import`と旧`collection_inventory_url:*`由来のobservationを一旦除去します
  - legacy observationのmember一覧にだけ由来が残る旧seed roleも、通常の採用decisionと区別して削除します
  - payloadから消えた旧observationも再構築時の削除対象です
- import 前にfull collectionごとに既存projectとの一致を判定します
  - `exact_namespace` / `exact_namespace_scan` / `seed_parent_namespace`で一致したcollectionはtarget projectごとに集約します
  - 自動attach対象として一致しないcollectionは、直近の親namespace単位の新規project候補にまとめます
  - 同じregistrable namespaceでも、`site.filae.chorus.*`と`site.filae.agora.*`のように親namespaceが異なるcollectionは別候補です
  - 既存projectへ一致したcollectionを新規候補側へ巻き込みません
- reject する主な条件:
  - namespace が短すぎる
  - authority domain が import 許可ルールに合わない
- 既存 project への match 順:
  1. payload内のfull collectionから得た親namespaceが、既存projectのconfirmed `owned_nsid`の親namespaceと一意に一致する
  2. `exact_namespace`
  3. `exact_namespace_scan`
  4. `registrable_namespace`
  5. `service_host_family`
- 自動 attach するのは 1、2、3 だけです
- 4 と 5 は `suggested_project_key` に落とし、既存 project に自動では吸いません
- bucket内の全full collectionを`owned_nsid:confirmed`として保存します。これはcollection inventoryの割当契約によるfactual ownershipで、legacy seed roleは作りません
- 代表collectionは新規projectの初期`seed_value`選択だけに使い、identifier化の範囲を制限しません
- `collection_observations`は親namespace単位の`count` / `recent_count` / `min` / `max`を保存し、member一覧は永続化しません
- `--all-history`のsummaryは`collection_observation_rebuild_project_count`と`collection_observation_cleanup_count`を返します

### `sync-bluesky-lexicons`

- `config/sources.json`のBluesky公式repositoryを走査し、`lexicons/app/bsky/`と`lexicons/chat/bsky/`配下にある`defs.main.type=record`のschemaだけを対象にします
- `com.atproto.*`は同じrepositoryにAppView実装も含まれ所有projectを一意に定められないため対象外です
- confirmed service `https://bsky.app`を持つprojectが一意な時だけ、collectionを`owned_nsid:confirmed`として追加します。targetが無い場合は`unmatched`、複数なら`ambiguous`を返し、新規projectは作りません
- dry-runでもrepositoryへ接続します。`--execute`時だけidentifierと`bluesky_official_lexicons` discovery sourceを保存します

### `sync-github-search`

- query には `fork:false` と `archived:false` を自動で補います
- query ごとに `updated_at` watermark を保存し、`updated:>=YYYY-MM-DD` と `sort=updated&order=desc` で候補量を絞ってから時刻単位の境界まで取得します
- import 対象にするのは:
  - 既知 repo URL ではない
  - `updated_at` が watermark 以上
  - watermark が無い初回は `lookback_days` 以内
- 既存 project への match 順:
  1. `repo_url`
  2. `homepage_exact`
  3. `homepage_family`
- `repo_url`のconfirmed完全一致は既存projectを継続して使います
- `homepage_exact`はproject候補の照合です。GitHub Website欄の`repository_homepage`観測を作り、一致先が一意なconfirmed serviceで、同じserviceからrepositoryへのconfirmed `project_repository`も存在する場合だけ、既存の共通信頼policyでrepositoryをconfirmします
- 一方向の`homepage_exact`、candidate / non-current serviceへの一致、同順位で複数projectに一致する場合はsuggestionに落とします。複数一致ではproject key順の一件を選びません
- `homepage_family`もsuggestionに留めます
- 既存projectへ共通信頼policyで採用したrepositoryは`trust_policy` adoptionになり、GitHub Search用のmanual seedや登録起点にはしません
- unmatched を `--screen-unmatched` する場合:
  - repo metadata / README / tree / lexicon candidate JSON を追加で見ます
  - lexicon hint、lexiconらしいpath、derived NSID、lexicon schema sample、repo homepage、service candidate、atproto keyword hitのいずれかがあれば通します
  - 何も無ければ `screened_out_entries` に落とし、projectを作りません
- `--screen-unmatched` を付けた場合、fork repo は追加で厳しく見ます
  - GitHub 上で `fork=true`
  - かつ homepage / service candidate / owned lexicon surface のいずれも見えない
  - この条件なら `github_fork_without_independent_surface` で新規 project 候補から落とします

### `sync-semble-collections`

- 対象は`config/sources.json`のtracked Semble collectionです
- 現行XRPC APIから`urlCards`を取得し、card URLを`nsid`、`repo_url`、root `service_url`のseedと、非rootのevidence-onlyへ分類します
- 非root service URLは既存root projectがある場合だけ`evidence_urls`へ添付し、単独projectを作りません
- collectionごとに`card_updated_at` watermarkを保存し、updatedAt降順を確認できたresponseだけ早期停止します
- 既存project解決はexact identifierを先に使い、NSIDのnamespace/family一致は安全判定後の候補に留めます

### sync state の更新条件

- `sync_state`はsourceごとの運用checkpointで、project recordではありません
- introducedはquery、collectionsはinventory URL、GitHubはquery、Sembleはtracked collectionごとにwatermarkを持ちます
- successful executeかつ走査完了時だけ更新し、dry-run、例外、page上限到達、未一致を残す`--no-create-unmatched`では更新しません
- 境界timestampと同時刻の項目は次回も処理します。exactly-onceではなくat-least-onceです

### `sync-tangled-repos`

- 公式`@bsky/jetstream` SDKでJetstream v2 snapshotを取得し、plan時点のsealed tipを固定します
- `sh.tangled.repo`のcreate/update/deleteをsequence順に畳み込み、保存済みsequence watermarkから増分再開します
- event上限で途中終了したexecuteは最後に処理したsequenceを保存し、次回に残りを再開します
- 通常上限は1,000 eventです。初回archiveも一括完走させず、複数runで進めます
- atproto relevanceを通過した現存recordだけを対象にします
- 正規化したTangled repo URLだけで既存projectへexact照合します。owner DIDやrecord websiteだけでは照合しません
- relevant eventのowner handleはDB変更前にDIDごとに解決・cacheし、解決失敗時はrunを適用しません
- unmatched project作成は既定onです
- API keyは`ATPROTO_SURVEY_JETSTREAM_API_KEY`環境変数からだけ読み、request、event、設定、stateへ保存しません
- confirmed developer handleがある通常enrichでは、handle -> DID -> PDSを解決し、
  `com.atproto.repo.listRecords`で本人のTangled repo recordを取得します
- accepted source repo URLがTangledの場合は、repo root HTMLを読み、READMEを
  `raw/{ref}/{path}` から取得して `tangled_repo_inspection` evidenceを作ります
- owner DID一致や共通websiteは補助根拠ですが、actorやwebsiteは複数projectに対応し得るためproject同一性の照合には使いません
- Tangled repo inspection はREADME URLと短いexcerptを候補テキスト・候補URLに反映します。
  GitHubのrecursive tree API相当の完全なcontents取得ではありません

## マージ時の editorial 優先

`merge_project_records()` は単純な `current or incoming` ではなく、`project_editorial_preference()` を使って service 本体らしい project を優先します。

主に見ているもの:

- confirmed service surfaceがあるか
- `website_url`
- `service_urls`
- `project_repository_urls`
- `resource_url_records`
- `service_status`
- `introduced_url`
- confirmed NSID 数

この優先は主に次の field に効きます。

- `name`
- `tagline`
- `summary`
- `website_url`

意図は、collection/lexicon project が後から service 本体の editorial 情報を上書きしないようにすることです。

ただし、既に保存済みの微妙な `tagline` / `summary` を自動で書き直す仕組みではありません。`proj:f4cead1df6f7` のような既存レコードは、LLM 再 `enrich` または手動修正がまだ必要です。

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
  - `enrich --llm-max-output-tokens`を指定した場合、planning、repository analysis、triageの全POSTへ同じ出力上限を渡す。省略時はendpoint既定値
    - triage
  - endpoint fallback が走ると chat/responses/completions を順に試すので、失敗時はこれより増えます
  - 実装済み: LLM endpoint は operator が明示設定する trusted endpoint として扱い、LAN / localhost 宛てでも POST を許可します
  - 実装済み: project data 由来の service / resource URL は同じ `request_bytes()` 系でも内部宛て block の対象です
    - DNS検証と実fetchは別の名前解決になるため、悪意あるDNS切替まで防ぐaddress pinningではありません。
      個人用調査toolの脅威モデルにおけるbest-effort SSRF guardとして扱います
  - 未接続: LLM endpoint 以外の内部 URL を operator が個別 allowlist する設定

### `sync-introduced`

- Bluesky authenticated post search だけです
- request 数はおおむね `ceil(limit / 100)` を `max_pages` で打ち切った数です
- 既定値は `limit=25` なので通常 1 request です
- 初回だけ `createSession` が 1 request 発生します
- token 失効時だけ `refreshSession` が 1 request 追加されます

### `sync-collections`

- collection inventory URL への fetch が 1 request です
- 既知 project の activity refresh や namespace 集約はその payload だけで行い、per-entry の追加 fetch はありません

### `sync-github-search`

- 基本 request 数:
  - `queries * pages`
  - 既定 config は 1 query, 1 page なので通常 1 request
- `--screen-unmatched` を付けない限り、search 以外の追加 fetch はありません
- `--screen-unmatched` を付けると unmatched repo ごとに追加 fetch します
  - screen 関数内の `fetch_github_repo()` はmetadata、README、branch、treeの通常4 requestです
  - 同一process内で同じrepoを続けて解析する場合、この基本取得はTTL cacheを再利用します
  - その後のlexicon、manifest、configuration、必要時のsource file取得はcache対象外です

### `sync-semble-collections`

- Semble API への page fetch だけです
- request 数は `tracked_collections * pages`
- 既定の tracked collection は 3 件です。増分境界へ各1 pageで到達する通常実行は 3 request、設定上限は`3 * 100 = 300` requestです

### 補足

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

## 昇格ゲート

本線扱いにする条件:

- seed が1件以上ある
- 確認済み識別子が1件以上ある
  - `website_url`
  - confirmed repository URL
  - resolved lexicon
- `service_status != unknown`
- `summary` と `tagline` がある
- `features` が3件以上ある
- `category` がある

満たさなければ `draft` のまま残し、`promotion_blockers` に理由を残します。

## 保存レイヤの方針

`projects`はscalar、`profile_json`、`state_json`だけを持つmaterialized current stateです。`profile_json`は紹介・表示に使う編集済み情報、`state_json`は低頻度の評価結果と調査補助を保持します。

identifier、relation evidence、operator decisionの正本はそれぞれ`project_identifiers`、`project_relations`、`project_decisions`です。`profile_json`/`state_json`へ複製保存しません。保存責務、`profile_json`のfield、child tableの契約は[project-record.md](project-record.md)を正本とします。`state_json`の内部fieldは固定payload APIではありません。

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
既知 collection を `sync-collections` した時も `collection_observations` の `count` / `recent_count` / `max` は更新します。`latest_collection_activity_at` 相当の用途では、これを「最近 record が流れた時刻」とみなします。

## 確認済みと候補の区別

- confirmed
  - `website_url`
  - `service_urls`
  - `project_repository_urls`
  - `confirmed_nsids`
- observation
  - `resource_url_records`
  - record ごとに `candidate / confirmed` を持つ
- candidate
  - `service_url_candidates`
  - `candidate_project_repository_urls`
- deferred
  - `deferred_identifiers`
  - URL / NSID を恒久 block せず一時保留する運用カテゴリ
  - URL は candidate に降格して観測可能なまま残す
  - NSID は専用 candidate 配列を追加せず `deferred_identifiers` に保持する
  - 既定 30 日。期限内は confirmed への自動昇格を抑制し、期限切れ後は通常導線へ戻す
- promotion deferred
  - `promotion_deferrals`
  - 紹介候補として再浮上させるまでの理由付き一時保留履歴
  - 初回の若いprojectは14日、通常初回は30日、履歴がある場合は45 / 60 / 90日。activeな期限内entryがある間だけ`promotion-queue`から除外する
  - 期限切れ entry や clear 済み entry も履歴として残し、再保留時の理由再利用と将来の復活条件判定に使える形にする
- promotion priority adjustment
  - `promotion_priority_adjustments`
  - operatorが候補の優先度を下げた理由と履歴。期限は持たず、clearまで維持する
  - 候補は`promotion-queue`に残るが通常候補より後ろへ並び、`defer_promotion`とは独立する
- `summary_candidates`
- `name_candidates`
- `tagline_candidates`
- failure / transient
  - `failed_service_urls`
  - `last_run_errors`

## source repo 調査

source repo は次の順で探します。

1. first-party page のリンク
2. README / homepage
3. GitHub repository search
4. Bluesky actor search / post search / author feed

confirmed に入れるのは、seed 由来か first-party の強い根拠があるものだけです。
GitHub repo を確認した場合は `source_repo_records[].updated_at` に upstream の `updated_at` も保持し、`review-stalled` で repo activity として使います。

## LLM

LLM は discovery の主役ではなく、整理の補助です。

planning/triageのevidence JSONは24 KiB上限です。大量候補を単純に先頭だけで切らず、field別件数上限で先頭・末尾を
代表として残した後、総byte上限まで低優先listを縮約します。縮約件数は`evidence_budget`に残します。固定prompt
template、response、保存済みcollector payloadはこのbudgetとは別です。

unconfirmedな`candidate_project_repository_urls`はtriage evidenceから除外します。候補URLを調査対象に含める必要があるinvestigation planningにだけURLを渡し、candidate repositoryのdescription、名前、lexicon hint等はconfirm前にはevidenceとして保存・適用しません。

- `summary`
- `tagline`
- `features`
- `category`
- `service_status`
- `lexicon_scope`
- `technical_interest_level`
- `technical_interest_reasons`
- `technical_interest_summary`

を生成します。説明系フィールドは日本語で出させます。`tagline` は助詞を含まない短い名詞句を想定し、最大30文字です。

`summary` / `tagline` / `features` は stability-first です。新しい LLM 出力は candidate / evidence として保持しますが、canonical 値は毎回そのまま置き換えず、既存値が十分近い場合は維持します。

NSID seed は、それだけでは共有 lexicon の根拠にしません。`lexicon_scope` は次の 3 値で保持します。

- `shared`
  - `standard.site` のように、第三者実装や相互運用を意図した共有 schema と判断できる
- `service_specific`
  - namespace と service / collection 面が同一 project を指している
- `unknown`
  - service がまだ見つかっていないだけか、共有 lexicon として設計されているかを区別できない

`category=lexicon` の project で `lexicon_scope=service_specific` になった場合は `category=project` へ戻します。`unknown` は無理に確定しません。LLM prompt にも、service URL 未発見を shared の根拠にしないことと、service の実態がある場合は schema 説明だけに縮退させないことを明記しています。

`category=library` は、他のsoftwareからimport、link、またはpackageとして利用する再利用codeに限定します。
repositoryを主な配布面とするだけではlibraryとせず、実行可能なapp、CLI、script、plugin、template、example、
reference implementation、prototypeは実際の役割に応じて`project` / `infra_tool` / `personal_tool` / `lexicon`
へ分類します。`personal_tool`はauthor-onlyまたは個人的用途の根拠がある場合に限り、単一用途のartifact全般の
fallbackにはしません。この区別は成熟度、紹介価値、配布形態ではなく、projectが提供する利用面を表します。
生成するsummaryがapp、CLI、plugin等と説明しているのにcategoryだけ`library`とする出力は不整合として扱います。
`current_profile`は既存の編集文面でありcategoryの根拠にはせず、根拠から役割を決めた後に文面を維持するか
判断させます。

技術的な学習価値は、実用性や紹介準備とは別軸で `technical_interest_*` に保持します。LLM は evidence に基づいて `none / notable / high / exceptional` を暫定評価し、理由を enum と短い説明で残します。`promotion-queue` の `technical_insight_candidate` は `high` / `exceptional` だけを対象にします。`notable` は将来の再評価材料として保存しますが、推薦 signal にはしません。

serviceの利用可否も独立軸として扱います。`category=project / infra_tool`かつ`service_status=live / beta / invite_only`でURLを持つもののうち、confidenceがmedium以上でlogin、app shell、browser到達、検証済みauthority surfaceなどを確認した候補は`service_surface_confirmed`とします。これはservice軸のevidenceであり、中核機能や編集上の紹介可否まで保証しません。`library`はdocs URL等をserviceと誤認しないよう、このsignalの対象に含めません。

`unverified`、HTTP 200だけ、docs / landing pageだけ、またはevidenceに矛盾がある候補も自動除外せず、`service_candidate_needs_validation`として約100点下の優先度で残します。backend障害とlanding-onlyは静的probeだけで安定して分けられないため、候補提示後のoperator reviewで扱います。これらのservice signalはatproto寄与度や`technical_insight_candidate`と独立です。

理由 enum:

- `novel_design_pattern`
- `protocol_gap_exposed`
- `interoperability_experiment`
- `unusual_architecture`
- `implementation_reference`

command/endpoint方式の1 attemptのtimeoutは`ATPROTO_SURVEY_LLM_TIMEOUT`で指定し、既定は`300`秒です。
auto API styleの別payload/endpoint試行は、HTTP 400/404/405/415/422/501でendpointがrequestを拒否した場合だけ
行います。timeout、接続失敗、rate limit、server error、response envelope/content JSON不良では、serverが
処理中または既に生成済みの可能性がある同じpromptを再送しません。明示`chat`はcompletion endpointへ
fallbackしません。

## merge

`duplicate_candidates` と `merge_recommendation` は派生データです。必要なら再計算できます。

Webのmerge候補planは保守的です。曖昧な一致は自動実行せず、pair reviewと個別actionで人が判断します。

merge/split/separateの複数project書込みはoperation単位のSQLite transactionで実行します。
repositoryの`save_project(..., commit=False)`を使い、2件目の保存、alias作成、source削除まで
成功した時だけcommitします。merge済みsource keyは`project_key_aliases`からcanonical keyへ解決し、
alias元の`investigation_runs`もcanonical projectの履歴一覧へ含めます。run JSON自体は変更しません。

merge実行時の統合後`duplicate_candidates`は、削除予定sourceと置換前targetを比較集合から除外し、統合後targetを
加えた同一snapshot上で再計算します。Web APIはその保存済み結果の先頭候補を`next_merge_review`として返し、
Merge reviewは一覧全体の再計算を挟まず次のpairへ進みます。この導線は候補scoreやbatch mergeの判定基準を変更しません。

`review-queue` は表示のたびに `duplicate_candidates` / `merge_recommendation` を再計算しません。通常導線では保存済みの派生値を使い、再計算は `repair-db`、seed 追加、merge / split / block / separate など identifier を変える操作に寄せます。

また、`review-queue` の通常表示は保存済み DB を信頼した軽量 shape を使います。保存時・repair 時の full normalization は維持しますが、一覧表示では URL / repo / lexicon の重い再正規化を避けます。

`promotion-queue` は通常表示と interactive の両方で、対象 project 全件から signal と `promotion_queue_sort_score()` を計算し、`priority_group`、`top_priority`、`sort_score`、`project_key` の順でグローバルソートしてから `--limit` を適用します。`priority_group=lowered`は通常候補の後ろへ送り、各group内では従来のsignalとscore順を維持します。紹介候補は上位から見ること自体が目的なので、interactive でも部分バッチ先行にはしません。

`lexicon_publisher_confirmed`はcategory横断の紹介候補signalです。confirmed `owned_nsid`、`_lexicon.<authority>` DNS委譲を経て取得した`lexicon_resolution_entries`、同じNSIDの`lexicon_schema_samples`が一致した場合だけ成立します。repository内schema、NSID hint、record利用実績だけでは成立しません。priorityは8、scoreの`lexicon_publication`は160で、confirmed live serviceの次の優先帯に置きます。`reject` / `name_only`は除外し、`personal_tool`は`--include-personal-tools`指定時だけ対象にします。

promotion policyは次を混ぜずに投影します。

- `candidate_visibility`: `eligible / deferred / excluded`。紹介済み、`out_of_scope / ignore`、activeな`defer_promotion`のようなproject全体の表示可否を表す
- `service_evidence_group`: `confirmed / needs_validation / not_applicable`。service signalだけの確認度を表し、単独紹介の可否は含めない
- `priority_group`: `normal / lowered`。`lowered`はnormalized decisionの明示判断だけから投影し、project名・説明文・service evidenceから自動推定しない。理由メモは任意
- `score_breakdown`: service、lexicon、technical interest、profile、activityなど既存scoreの加減点を成分別に返し、`total`を従来の`sort_score`として使う

したがって`defer_promotion`は個別service signalの評価を下げる操作ではなく、project全体を期限付きで非表示にする操作です。一方、serviceの`needs_validation`は候補を消さず、confirmedより100点低いsignalとして残します。

WebのPromotion cardはservice group、manual priority、validation理由、scoreの非ゼロ成分を表示します。詳細画面から優先度を下げる時は任意の理由メモを入力でき、保存後は現在cardを外して次候補へ進みます。`Refresh`後も候補自体は残りますが、通常候補より後ろへ並びます。

merge元とcanonical targetの両方にactiveなpromotion priority判断がある場合はtarget側を維持し、source側をclear済み履歴として残します。

`promotion_deferrals` に active な entry がある project は signal 計算前に除外します。未指定時の保留期間は `suggested_promotion_defer_days()` が決めます。初回の若い project は 14 日、通常初回は 30 日、過去の promotion defer 履歴がある場合は 45 / 60 / 90 日へ延ばします。`--days` または API payload の `days` が明示された場合はその値を優先します。過去履歴は消さないため、再保留では直近理由を既定値として再利用できます。理由から「未実装機能が実装された」などの復活条件を自動判定する処理は将来候補で、現状は期限だけを見ます。

`audit-source-config [domain]` はローカル設定 `config/domain-overrides.json` の `custom_multi_tenant_suffixes` 追加後基準で既存 project を読み取り監査します。discovery fetch 設定の `config/sources.json` とは保存責務を分離しています。service URL、NSID authority、authority domain を tenant 単位に分け、同一 project 内に複数 tenant が残る時だけ `split_suggestions` を返します。仮 tenant project を組み立てて `compute_duplicate_candidates()` を通すため、分割後に既存 project との merge candidate が成立するかも表示できます。

このコマンドはデフォルト interactive です。suffix追加時はcurrent service probeのsame-site派生判定も同じapplication serviceで再評価します。tenant group の番号を選んだ場合だけ、service URL / NSID / authority domain を 1 つの新 project へ原子的に切り出し、元 project に block、新旧 project に相互 `separate_project_keys` を入れます。split 適用後は suggestion を再構築し、同じ元 project に複数 tenant が残る間はその project を優先して再表示します。Enter で送った suggestion key はその実行中だけ skip set に残します。共有 repo は自動配賦せず ambiguous として表示します。`--execute --no-interactive` はsuffix設定とcurrent same-site判定を揃えますが、splitは自動適用しません。

`split-project --identifier-type domain --value <authority-domain>` は、指定 authority domain と同一 host の confirmed service URL、同じ authority domain から導出される NSID、authority domain identifier をまとめて新 project へ切り出します。repo URL は authority domain だけでは帰属を確定できないため自動移動しません。

### duplicate candidate の作り方

- まず各 project から duplicate profile を作ります
  - `project_repository_urls`
  - `github_repo_urls`
  - `candidate_project_repository_urls`
  - `service_urls`
    - `website_url` もここに含めます
  - `recorded_lexicon_namespaces`
  - `record_creators`
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
- これらの値ごとに inverted index を作り、どれか 1 つでも共有する相手だけを候補集合に入れます
- `github_repo_urls`は保存fieldではなく、`project_repository_urls`からGitHub providerだけを導出したduplicate比較用の内部keyです
- duplicate / merge 用の `authority_service_families` と `domain_identity_tokens` は、確認済みのservice/manual service、operator confirmed domain、manual/confirmed/trust-policy confirmed collectionから導いたauthorityだけを使います
  - `input_nsids` / `lexicons` / `authority_domains` / `service_url_candidates` のような候補・観測fieldだけでは、merge用のdomain/family根拠にしません
  - 同じlexiconを使っている、または同じlexicon authorityを観測しただけでは、medium以上のmerge candidateにしません
- ただし次は最初から除外します
  - 自分自身
  - `separate_project_keys` で相互 block された pair
- fork/template/statusphere 派生のような「別物だが似ている」関係は、現状では `separate_project` で merge 候補から外します。将来的な弱い派生関係の設計メモは [derivation-relations.md](../notes/derivation-relations.md) に分離しています

### candidate の根拠

- `compute_duplicate_candidates()` は候補相手ごとに次の overlap を見ます
  - `project_repository_urls`
  - `github_repo_urls`
  - `candidate_project_repository_urls`
  - `service_urls`
  - `recorded_lexicon_namespaces`
  - `record_creators_and_namespace_lineage`
    - creator DID が重なり、かつ namespace が同一または親子
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
  - `collection_first_record_creator_dids`
    - 明示的に heuristic を有効化した時だけ
- `record_creators` 単独では merge 理由にしません
  - namespace lineage が同時に必要です
- `candidate_project_repository_urls` は confirmed repo より弱い根拠ですが、duplicate 候補の入口には使います

### score と confidence

- score は理由ごとの重み付き合計です
  - `project_repository_urls`: 8
  - `github_repo_urls`: 8
  - `service_urls`: 7
  - `record_creators_and_namespace_lineage`: 7
  - `recorded_lexicon_namespaces`: 6
  - `authority_service_families`: 6
  - `candidate_project_repository_urls`: 5
  - `domain_identity_tokens`: 5
  - `collection_first_record_creator_dids`: 6
  - `merge_aliases`: 3
- `merge_confidence` / `auto_merge_ready` は score だけではなく理由の組み合わせで決まります
  - `service_urls` があれば `high` かつ `auto_merge_ready=true`
  - confirmed repo overlap と alias/domain/family/namespace の複合なら `high`
  - candidate repo overlap でも alias/domain/family が乗れば `high`
  - creator DID + namespace lineage は `high`
  - domain token + alias だけなら `medium`
  - score だけ高くても、理由の質が弱ければ `medium` や `low` に留まります

### merge recommendation の作り方

- `merge_recommendation` は `duplicate_candidates` の先頭 1 件を採用したものです
- 返すのは:
  - `target_project_key`
  - `score`
  - `merge_confidence`
  - `auto_merge_ready`
  - `reasons`
- top 候補が `auto_merge_ready=false` で、2 位が同点の時だけ `ambiguous_with` を付けます
- つまり recommendation は「最有力候補」であって「必ず merge してよい」ではありません
- pair指定の `inspect-merge-pair` / Web merge review は、指定された2 projectだけを比較します
  - 一覧用の全project duplicate indexや、source/targetそれぞれの現在の `merge_recommendation` は計算しません
  - 表示する `source_pair_candidate` / `target_pair_candidate` は、一覧用 `compute_duplicate_candidates()` と同じpair判定関数から作ります
- Web merge review は、両projectのmerge identity hostが同じregistrable domain配下の別tenantに見える場合、未登録の `custom_multi_tenant_suffixes` 候補を表示します
  - 例: `foo.example.com` と `bar.example.com` なら `example.com`
  - 追加操作は `config/domain-overrides.json` を更新するだけで、既存 `separate_project` decision は自動解除しません
- collection first-creator heuristic は通常のWeb merge reviewでは初期計算しません
  - Webでは `Load first-creator details` を押した時だけ `listReposByCollection` の先頭DIDを補足情報として表示します
  - 照会collectionには保存済みのcase-preservingなNSID identifierを使います。`listReposByCollection`が返さないcollectionや取得失敗は、空欄にせず`not_found` / `error`として表示します
  - この補足表示は primary の `source_pair_candidate` / `target_pair_candidate` には混ぜません
  - CLIからheuristicを判定へ含める導線はありません

### merge候補planの追加安全策

- Webのmerge候補planは`duplicate_candidates`をそのままactionへ適用しません
- 候補 pair ごとに mutual recommendation を要求します
  - A の top が B
  - B の top も A
- さらに safe overlap を別ロジックで見直します
  - `strict`
    - strict GitHub/Tangled repo overlap
    - operator-confirmed repo overlap
    - operator-confirmed service host overlap
  - `moderate`
    - strict 条件に加えて
    - confirmed service overlap
    - candidate repo + alias overlap
    - creator DID + namespace lineage
    - domain token + alias overlap
    - authority family + domain token
    - service root domain + alias overlap
    - website root domain + alias overlap
- safe overlap が無い pair は recommendation があっても batch では落とします

### merge 方向の決め方

- `choose_batch_merge_direction()` は「どちらを target に残すか」を `project_batch_merge_preference()` で決めます
- 優先順位は概ね次です
  - confirmed factual identifierの強さ
    - service surface > owned NSID > project repository
  - editorial richness
    - `website_url`
    - `service_urls`
    - `project_repository_urls`
    - `resource_url_records`
    - `service_status`
    - `introduced_url`
    - confirmed NSID 数
  - confirmed identifier の数
  - `introduced_url` の有無
- つまり、より first-party surface が強く、より編集済みで、より confirmed identifier が多い側を target に残しやすいです

## app record export

`application/app_record_validation.py`は`community.lexicon.app.entry`だけをtargetにするread-only serviceです。
CLI adapterは指定DBを`mode=ro`かつ`query_only`で開き、readiness reportまたはentry exportを出力します。profile、
canonical scalar、confirmed current identifier/resourceをfield候補へ投影し、candidate/non-currentは診断用の除外入力として
残します。export対象と`createdAt`はversion付きpublication manifestから受け、project key順の裸のrecord配列を返します。
scope未確定keyは別allowlistが必要で、1件でも不適格なら成果物を書きません。

target revision、field mapping、readiness、evidence trace、再現性とJSON生成境界は
[App Record Export](app-record-export.md)を正本とします。

## app.status の将来分離

現行の canonical は DB column `service_status` です。

将来的に app export の要件が増えた場合は、次の 2 軸に分ける可能性があります。

- `service_status`
  - 到達性、公開状態、招待制、死活
- `app.status`
  - app record 向けの公開・保守状態

現時点では分離しません。`app.status` が必要になった時点で別 field として導入します。

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

current service projectionはJSON payloadだけでなくscalar列だけの差分も保存対象です。

旧 `data_json` 前処理はもう行いません。
schema v3実装は`registry_meta.schema_version=3`、normalized child tables、非現行identifier、採用理由、確定transition、registry-wide exclusionを前提にします。version 2は通常openせず、review済みmanifestによる新規複製DBのmaterializationだけを移行経路にします。正本DBは2026-08-14にこの経路でversion 3へ切り替えました。

新しいsuffix設定時はWebとCLIが同じapplication serviceを呼び、保存済みcurrent service probeのrequested/final URLを現在のdomain policyで再判定します。staleな`redirect_same_site_family` flagと対応する集約evidenceだけを修正し、観測URL、service status、confirmed/candidate identifier、investigation snapshotは変更しません。保存済みEvidenceBundleをcurrent stateへ再適用する場合もsame-site flagを現行policyで再計算します。genericなproject保存や履歴payloadの縮約では再計算しません。

## review-stalled

`review-stalled` は `active -> stalled` の候補だけではなく、

- `active -> stable`
  - collection は最近動いているが repo 側は止まっている
- `stalled -> active`
  - repo または collection に最近 activity が戻った

も返します。

`service_status=unknown/none` は単独では落とす根拠にしません。repo activity と collection activity を優先し、`dead/private` だけを強いシグナルとして扱います。

### activity の見方

- repo activity:
  - `source_repo_records[].updated_at` の最大値
- collection activity:
  - `collection_observations[].max` の最大値
  - ただし `recent_count > 0` の observation があれば、その `observed_at` も recent activity とみなします
- `activity_is_recent()` は `age < inactivity_days` の時だけ recent 扱いです

### `stalled -> active`

- いま `tracking_state=stalled` の project は、次のどちらかが recent なら `active` に戻す候補です
  - recent repo activity
  - recent collection activity
- 片方だけ戻っていても `active` 候補にします

### `active -> stable`

- 次の条件を満たす時だけ `stable` 候補です
  - いま `tracking_state=active`
  - repo は recent ではない
  - collection は recent
- これは「ソースコード更新は止まっているが、collection はまだ実運用されている」ケースを落とし過ぎないためです
### `active -> stalled`

- まず recent repo activity がある project は何もしません
- そのうえで、collection も recent でない時だけ stalled 判定に進みます
- `last_enriched_at` が無い、または `max_enrich_age_days` 以上古い場合は、停止を判断する材料が古いため降格候補から外します
- strong signal (`hard_signal`) として扱うもの:
  - collection が `inactivity_days` 以上止まっていて、しかも `recent_count>0` の observation が無い
  - repo が `inactivity_days` 以上止まっている
  - `service_status` が `dead` または `private`
- weak signal として理由には出すが、単独では落とさないもの:
  - `service_status=unknown` または `none`
  - confirmed identity が 1 件以下
    - `website_url`
    - `project_repository_urls`
    - `resolved_nsids`
- `stalled` を提案するには:
  - `hard_signal` が少なくとも 1 つ必要
  - 理由が 2 件以上必要
- つまり、単に `unknown` で証拠が薄いだけでは `stalled` にしません

### 調査鮮度ゲート

- `max_enrich_age_days` は停止の根拠ではなく、降格判定に使える調査結果の最大経過日数です
- 未enrichまたは期限超過の `active` は `stalled` 候補を返しません
- recent activityによる `stalled -> active` と `active -> stable` は、調査鮮度ゲートより先に判定します

### review-stalled の対象範囲

- `review-stalled` が見る tracking state は:
  - `active`
  - `stalled`
- `stable` / `out_of_scope` / `archived` / `ignore` はこの自動判定の対象外です
- `--execute` を付けると、提案された `suggested_tracking_state` をそのまま保存します

## review-out-of-scope / review-ignore

`review-out-of-scope` は関連性を否定せず、現在のsurvey対象から外す候補を返します。

- `auth_only_without_owned_protocol_surface`
  - `atproto_auth_only=true`
  - owned protocol surfaceがない
- `derivative_bluesky_utility`
  - `service_is_derivative_bluesky_tool()` が真
  - `introduced_url` がない
  - owned protocol surfaceがない
  - `category=lexicon` ではない

accepted projectは `tracking_state=out_of_scope` になります。この状態は通常の enrich/review/promotion対象外ですが、merge候補計算からは除外せず、明示enrichと手動復帰を許します。将来のsyncで新しいNSID等が既存projectへ紐づく経路も維持します。

`out_of_scope` projectにowned protocol surfaceが見つかった場合、同じコマンドは `owned_protocol_surface_detected` / `suggested_tracking_state=active` を返します。`--execute` なしでは更新せず、復帰も確認可能なqueueとして扱います。sync/enrichの途中で自動復帰する処理は未接続です。

`review-ignore` は inactivity review とは別で、ノイズ整理の候補だけを出します。

現状の kind は次です。

- `fork_without_independent_identity`
  - confirmed な project repository が GitHub fork
  - `website_url` / `service_urls` / `introduced_url` が無い
  - owned protocol surface (`resolved_nsids`, candidate `input_nsids`, `collection_observations`) も無い
  - `service_status=live|beta|invite_only` なら除外

目的はatproto無関係、誤登録、独立identityのない複製など、通常の再評価を期待しない候補を `tracking_state=ignore` へ寄せることです。既定では dry-run で JSON を返し、`--execute` を付けたときだけ保存します。
