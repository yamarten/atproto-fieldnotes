# Registry Internals

registry schema version 2の内部仕様と運用メモです。利用手順は [project-registry.md](project-registry.md)、保存モデルの正本は [project-record.md](project-record.md) を参照してください。

## 運用前提

このリポジトリは個人用のローカル運用を前提にします。

- `data/survey.db` は単一利用者のローカル DB として扱う
- 他ユーザーや共有 DB の存在は考慮しない
- 同時編集の懸念は、自分の別プロセスや GUI 操作による衝突だけを想定する

## データの持ち方

現行DBは`registry_meta.schema_version=2`を必須とします。`projects`はscalar、`profile_json`、`state_json`を持ち、identifier、relation、operator decisionはそれぞれ`project_identifiers`、`project_relations`、`project_decisions`へ保存します。merge済みsource keyは`project_key_aliases`、調査履歴は`investigation_runs` / `collector_runs`です。

旧schemaは通常起動で自動変換せず、`project-registry migrate-db OLD_DB_PATH`で一方向移行します。成功時は旧DBをtimestamp付きbackupへ移し、新DBを元pathへ置きます。

v1 migrationは`v1-quarantine-1` trust policyを適用します。旧confirmed fieldはそのままv2 confirmedへ写さず、operator confirmationまたは現行ruleで再現できる根拠がないclaimをcandidate rowへ降格します。降格したrowは`metadata_json`に`migration_source=schema_v1`、`legacy_field`、`legacy_classification`、`trust_policy`、`downgrade_reason`を持ちます。active block対象は移行せず、active defer対象はcandidateとして残します。untrustedなprimary website/repo/NSIDは`projects.website_url`、`source_code_url`、`primary_nsid`へhydrateしません。

移行reportは旧confirmed、維持confirmed、candidate降格、block除外、defer維持、primary scalar clear、active block衝突、根拠不明legacy confirmed、normalized row roundtrip diffを出します。active block衝突、根拠不明legacy confirmed、roundtrip diffはhard acceptance conditionで、非0なら移行は失敗してsource DBを変更しません。

`repair-db`はv2 current stateの正規化と派生評価更新を行い、schema migrationには使いません。

調査履歴は自動削除しません。`prune-investigation-history`の既定policyは完了から90日以内の全runとprojectごとの最新3 runを保持し、それ以外の`investigation_runs`と対応する`collector_runs`を手動削除対象にします。dry-runが既定です。通常のDELETE後にできたSQLite free pageは再利用されますがfile sizeは縮まないため、実容量を縮める場合だけ別processを停止して`--execute --vacuum`を実行します。operator decisionとmigration provenanceはこのprune対象に含めません。

v1 migration provenanceは通常運用で増加しない固定量です。安定運用後は`prune-migration-provenance`で対象件数とmetadata削減量を確認し、`--execute`でidentifier rowを残したまま移行専用keyを削除できます。`legacy_record`内の通常metadataは平坦化して残します。

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

## 文書更新の方針

実装を変えた時は、コードだけでなく対応する文書も更新します。目安は次の通りです。

- 利用者向けの操作や出力が変わる時
  - `README.md`
  - `docs/project-registry.md`
- 内部の判定基準、保存構造、heuristic、運用前提が変わる時
  - `docs/registry-internals.md`
- 調査 pipeline の恒久的な責務境界や evidence 契約が変わる時
  - `docs/investigation-model.md`
- 長時間処理のprogress、中断、checkpoint、worker境界が変わる時
  - `docs/background-jobs.md`
- その場限りではないが、まだ安定していない残件や次の論点を残したい時
  - `docs/thread-handoff.md`
- 中長期の計画や、まだ着手していない改善案を整理したい時
  - `docs/roadmap.md`
- 現行仕様へ確定する前の比較、保証関係、運用案を詳しく残したい時
  - `docs/notes/`
- データモデルの見方や field の意味が変わる時
  - `docs/project-record.md`

特に次の変更では、利用者向け文書と内部文書の両方を見る前提です。

- DB schema / storage layout の変更
- CLI command / option / interactive flow の変更
- sync / enrich / merge / review 系の判定ロジック変更
- queue の signal や promotion / stalled 判定の変更

## テスト方針

テストは `unittest` を正本にし、次で実行します。

```bash
python3 -m unittest discover -s tests -v
```

heuristic を変更する時は、該当する core/storage テストを追加または更新します。

- name / URL / service status / LLM response parser / merge scoring のような DB 非依存の判定は `tests/test_project_registry_core.py`
- `profile_json` / `state_json` / normalized child tables 分割、`save_project` / `fetch_project` / `repair-db` 相当の保存構造は `tests/test_project_registry_storage.py`
- Web APIの認証、schema v2 payload、normalized mutation、static asset、error shapeは `tests/test_project_registry_web.py`

外部 API、実 DB `data/survey.db`、実 LLM endpoint は通常テストから切り離します。必要な SQLite DB は temp directory に作ります。

## project record の最小コア

優先して埋める項目:

- `name`
- `website_url`
- `service_urls`
- `tagline`
- `tagline_is_provisional`
- `service_status`
- `source_code_urls`
- `introduced_url`
- `tracking_state`
- `tracking_note`

詳細な field 一覧は [docs/project-record.md](project-record.md) を参照してください。

### URL field の整理

canonical fieldは`website_url`、`introduced_url`、confirmed/candidateのservice・repo identifierです。補助pageは`resource_url_records`に残します。

削除済みのruntime compatibility field:

- `website_url_candidates`
- `github_repo_urls`
- `tangled_repo_urls`
- `resource_urls`
- `introduced`

provider別repo viewとresource URL一覧は`provider_repo_url_values()` / `resource_url_values()`で導出します。rootと`www`は観測済みredirect/canonical/content relationがある場合だけaliasとして畳み、他subdomainは独立surfaceとして残します。

same-siteの`/docs`、`/documentation`、`/about`、`/blog`、`/faq`、`/help`配下の情報ページと、`/login`、`/signin`、`/signup`、`/register`などの認証入口はservice identityではなく`resource_url_records`へ残します。これは補助ページをconfirmed service URLへ過剰昇格させないための暫定heuristicで、path名だけを長期的な信頼根拠とはみなしません。service follow-up probeやdeep page probeの結果はservice statusの補助evidenceには使いますが、補助ページURL自体をconfirmed service URLへ昇格しません。`/app`はapp surfaceとして使われることがあるため、この補助ページ判定だけでは落としません。`block_identifier` / `defer_identifier`されたservice rootは同一originの配下URLにもguardとして効き、confirmed service URL、supporting resource record、`operates_service` relationから除外します。

`project_registry_core/runtime_view.py`がnormalized child tableとcollector/evaluation向けflat dictの対応を一元管理します。既存projectの保存は完全にhydrateされたruntime viewだけを受け付け、partial dictからの更新によるidentifier/relation/decision全消去を拒否します。runtime markerは保存JSON、investigation snapshot、Web payloadへ出しません。

runtime viewはnormalized rowの無変更roundtripを維持します。candidate primary NSID、candidate owned NSID、candidate developer DIDなど、flat dictだけでは元rowのrole/classificationを表せない値は内部runtime stateに保持し、変更がない保存では元rowをそのまま書き戻します。complete runtime viewでは`input_nsids`から`primary_nsid`や`recorded_namespace`を暗黙生成しません。

`audit-url-fields`はcanonical fieldの件数とinvariant違反を読み取り専用で報告します。`cleanup-app-links`は`profile_json.app.links`をcanonical identifierとconfirmed resource recordから再構築します。

## enrich の流れ

`enrich` は次の順で動きます。

1. seed由来の事実収集
2. service / repo / lexicon の確認済み情報を更新
3. lexicon repository単位で namespace 集約
4. GitHub repo / GitHub search / Bluesky search の structured evidence を収集
5. evidence を project へ統合し、rule-based evaluation / finalize を実行
6. local LLM による summary / category / status 補完
7. 再度 finalize し、duplicate / merge / 昇格判定を更新

### 調査 pipeline の実装状況

調査 pipeline の責務、evidence classification、正本関係、partial failure、replay、LLM の位置づけは
[investigation-model.md](investigation-model.md) を正本とします。ここでは実装ファイルと現行接続状況だけを記録します。

実装済み:

- `project_registry_core/evidence.py`
  - `EvidenceBundle` schema version 2
  - `EvidenceRecord`, relation、`CollectorFailure`, `CollectorResult`
- `project_registry_core/investigation.py`
  - deterministic / LLM query planning
  - GitHub repository snapshot collector
  - GitHub repository search、Bluesky actor/post/feed collector
  - collector は project dict を変更せず evidence bundle を返す
  - source別 retry、request budget、rate-limit stop
- `project_registry_core/integration.py`
  - GitHub / Bluesky / service / Tangled / legacy evidence を既存 project field へ適用する
  - GitHub lexicon hint の owned / reference 判定は収集後に行う
- `project_registry_core/evaluation.py`
  - ownership、atproto寄与度、service status の純粋評価本体
- `project_registry_core/storage.py` / `replay.py`
  - investigation run保存、network-free replay、fixture benchmark
- `project_registry_core/service.py` / `tangled.py` / `relations.py`
  - service検証、Tangled PDS/UFOs、typed relation
- `project_registry_core/application/`
  - project query、tracking、decision、merge/split、review queue、sync、investigation、maintenance、enrichのCLI/Web共通service
  - `argparse.Namespace`やstdoutへ依存せず、明示引数とresult payloadを使う
  - `presentation.py`はschema v2のproject detail、queue、search payloadを組み立てるtransport非依存service
- `project_registry_core/jobs.py`
  - 長時間処理のrequest/event/resultと協調的cancellation契約
- `project_registry_core/commands/`
  - application serviceを呼び出し、CLI向けJSON/textへ変換するadapter
- `project_registry_web/`
  - schema v2 Web API adapter、HTTP server、build不要のHTML/CSS/ES modules
  - API handlerはapplication serviceだけを呼び、hydrated project dictを直接保存しない
  - `jobs.py`は別process worker、直近event、停止・再開を管理する
  - JSON responseはDB schema versionと独立した`api_version: 2`を持つ
  - presenterがidentifier family、relation summary、investigation summary、project revisionを生成する
  - mutationは`expected_revision`を必須とし、stale detailを`409 stale_project`で拒否する
  - server内のmutationはlockで直列化し、`--read-only`では全POSTを拒否する
- `project_registry_core/worker.py`
  - stdinの`OperationRequest`をapplication serviceへ渡し、event/resultをJSON Linesで返す
- `project_registry_core/registry.py`
  - domain helperとcollector/evaluation orchestration
  - `collect -> apply -> finalize/evaluate -> save` の orchestration

動作確認済み:

- collector単体が入力 project を変更しないこと
- GitHub relevance threshold、最大1 repo採用、最初の採用queryでの打ち切り
- GitHub README URL、owned/reference lexicon hint の既存反映
- Bluesky/GitHubのpartial failureとtimeout/rate-limit分類
- fixture evidence から ownership / atproto寄与度だけをnetworkなしで再評価できること
- temp SQLiteでの `enrich_project -> save_project -> fetch_project`
- 保存 run の replay、6 case benchmark、fake browser、Tangled pagination

未接続:

- Tangled timeline/community search/bot adapter の通常実行
- OAuth `include:` permission set の再帰解決
- Web UI の run/evidence inspection

現行の制約:

- project row は現在状態の正本、run table は再評価可能な調査履歴
- resource page は legacy mutator を project copy 上で実行し、許可 field の patch として統合する暫定実装
- cache と circuit breaker は process/source 単位で、複数 project を跨ぐ永続 policy ではない

collector reliability と replay の設計途中の論点は
[collector-reliability.md](notes/collector-reliability.md) と
[evidence-replay.md](notes/evidence-replay.md) に分離しています。

lexicon enrich に成功した場合、LLM evidence には lexicon schema の縮約サンプルも入ります。さらに `ufos-api.microcosm.blue/records` から recent record sample を取得できた場合は、それも判断材料に含めます。

`enrich --all` は既定で `tracking_state=active|watchlist` のみ対象です。`stable`、`stalled`、`out_of_scope` は、明示的に include したときだけ回します。

`enrich`のproject選択、保存、checkpoint、再開は`project_registry_core.application.enrichment`へ接続済みです。CLIは`project_registry_core.commands.enrichment`で引数を`OperationRequest`へ変換し、serviceのevent dataをJSON linesへ変換します。

`enrich --all` の中断/再開は DB schema ではなく、`data/enrich-runs/*.json` の checkpoint で扱います。開始時に選択した project key listを固定し、各projectの保存後に`completed_project_keys`を更新します。Ctrl+Cは協調的な停止要求となり、処理中projectを保存した後に`status=interrupted`と再開commandを記録します。次回`--resume-run`は完了済みkeyを飛ばします。契約の正本は[background-jobs.md](background-jobs.md)です。

Web UIはschema v2 repositoryへ接続済みです。project detail payloadは`project`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`を分離し、削除済みcompatibility fieldを再構築しません。identifier groupは同値行をまとめつつ、`entries`にrole/classification/providerの対応を保持します。confirmはcandidate entryから開始できますが、confirm/block/defer/split decisionのidentityは現行どおり`kind + value`です。日常的なproject query、tracking、identifier decision、split/mergeは`project_registry_core.application`を共有入口として使います。

enrich、sync、review classificationの別process runnerとWeb API/UIは接続済みです。job stateはserver process内、enrich checkpointはDB directory隣接の`enrich-runs/`に保存します。同じWeb serverからの同時background jobは1件に制限しますが、CLIなど別processとの排他は行いません。enrichはproject境界のprogress/cancellationとbulk checkpoint再開、server再起動後の固定rootからのcheckpoint発見を持ちます。syncはsource内page/item境界、reviewはcandidate境界でprogressとcancelを確認します。sync/reviewのcheckpoint再開とserver再起動後のprocess job一覧復元は未接続です。旧Web helperは暫定復活させません。Investigation tabにはrun一覧、上限付きevidence、要求時のread-only replay差分を接続済みですが、evidence全文は表示しません。

Explain serviceは`project_registry_core.application.explain`をCLIとWebで共有します。`service_status`、ownership、atproto contribution、merge candidateについて、保存済みcurrent stateとrelation sourceだけから結論、適用rule、支持/反証signalを返します。Explain実行中はnetwork collectorやLLMを呼ばず、replay結果もDBへ保存しません。Webの初期detailは全project hydrateを行わず、merge candidateだけを`/api/projects/{key}/merge-analysis`で非同期計算します。同service内ではduplicate candidate計算を1回だけ行い、その結果をmerge recommendationとExplainの両方へ渡します。source revisionが計算中に変化した場合は結果を破棄し、計算済みviewはDBへ追加しません。

## enrich の判断材料

`enrich` は「confirmed に上げる条件」と「candidate に留める条件」を分けています。LLM は後段ですが、一次情報で確定できないものだけを整理させる位置づけです。

### 1. lexicon から取るもの

- 入力:
  - `primary_nsid`
  - `input_nsids`
  - GitHub repo から後で見つかった NSID
- 取得の流れ:
  - `_lexicon.<authority_domain>` の TXT を引いて DID を解決
  - DID document から PDS endpoint を引く
  - PDS の `com.atproto.repo.getRecord` で `com.atproto.lexicon.schema/<nsid>` を引く
- ここで confirmed に入るもの:
  - `resolved_nsids`
  - `record_creators`
  - `authority_domains`
- ここで candidate に留めるもの:
  - authority domain から推定した service origin
  - lexicon description 由来の `summary_candidates` / `tagline_candidates`
- 保守的な点:
  - lexicon schema が取れても、それだけで `website_url` や `service_url` は confirmed にしません
  - authority domain 由来 URL は
    - まず probe する
    - `service_probe_supports_project()` を通った時だけ candidate に残す
    - unsupported なら候補化しない
    - supported だった URL は `supported_service_urls` に URL 単位で保存し、後段の guess prune で消さない

### 2. GitHub repo から取るもの

- manual `repo_url` seed は first-party 前提で `source_code_urls` に confirmed で入れます
- repo metadata から取るもの:
  - repo 名は `name_candidates` に入れ、`atproto-visualizers` のような slug はハイフン込みの全体を名前候補として扱います
  - `description` は `summary_candidates` / `tagline_candidates`
  - `homepage` は `service_url_candidates`
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
  - same site family の redirect 先が project を支持し、`website_url` が未設定なら、redirect 先を primary service URL として登録します
  - same site family でも既存 `website_url` がある場合は、redirect 先を candidate に留めます
  - cross-host redirect は investigation log に残し、redirect 先が project を支持する場合だけ candidate に入れます
  - cross-host redirect は自動で同一 service として confirmed にはしません
- `service_probe_supports_project()` が真になる主な条件:
  - service page から、既にこの project の manual seed または confirmed source repository として受け入れている GitHub repo へリンクしており、repo側にも同じservice familyへのhomepage/README等の参照がある
  - manual service seed の host そのもの
- service page の repo link は、未知 repo を新たに confirmed へ昇格する根拠ではありません。既存の `project -> repo` 根拠と `repo -> service` 参照を、別 source である `service -> repo` link が補強する場合だけ service support に使います。
- `lexicon_resolution_entries` に resolved NSID / record URI / authority domain が揃っていても、それは `repo/NSID -> authority domain` の片方向根拠です。service host がそのauthority domainと一致して正常応答するだけでは、`domain/service -> project` の根拠として扱わず、confirmed service URLへ昇格しません。
- service page が project の NSID を直接言及しているだけでは、互換実装や利用関係と区別できないため、`domain/service -> project` のconfirmed service support根拠にしません。
- service page が authority domain、alias token、atproto marker を自称しているだけでは、`domain/service -> project` のconfirmed service support根拠にしません。
- service page 上の Bluesky profile link は `official_account` relation evidence としては残しますが、それ単独では `domain/service -> project` のconfirmed service support根拠にしません。
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
  - `service_url_auto_confirmable()` が真
    - manual service host と一致
    - authority domain と一致
    - alias token と host が強く一致
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
[service-verification.md](notes/service-verification.md) を参照してください。

### 4. first-party link の扱い

- service page / resource page のリンクは `classify_official_link()` で分類します
  - `source_repo`
  - `source_like`
  - `docs`
  - `blog`
  - `bluesky_profile`
  - `same_site`
- confirmed に上げるのは:
  - first-party page が repo を明示し、owner や site family が project と噛む時
  - same-site family の repo URL で owner も噛む時
- それ以外の repo-like URL は candidate に留めます
- docs/blog/same-site 補助ページは `resource_url_records` に残し、`resource_url_values()` 経由で必要なら追加 probe の対象にします

### 4.1 NSID ownership の現在の優先順位

`project_owned_lexicon_nsid()` は現在、次の順で `owned` を判定します。

1. manual `nsid` seed そのもの
2. manual `nsid` seed の親 namespace と一致
3. confirmed 開発者アカウントが authority domain を持っていそう
4. confirmed 開発者 DID がその `NSID` の schema record を持っている
5. confirmed service host と authority domain が整合する
6. 既存の token ベース整合

意図は、「偽 NSID -> 既存サービス -> それっぽい account」という自己強化ループを弱めることです。

repo内にlexicon JSONがあるだけの観測は、accepted repo と accepted NSID が揃うまで `defines_schema` として保存せず、`mentions_collection` に落とします。`defines_schema` は repo と NSID の両方が project 側で受け入れ済みの場合だけ成立する relation です。manual repo seedはsource repositoryの根拠であり、そのrepoに含まれる全NSIDの所有根拠ではありません。`uses_collection`、`mentions_collection`、OAuth `requests_permission` は ownership を確定しません。
OAuth `include:` の再帰解決と organization/individual account の委任関係は未接続です。
domain、account、DID、NSID、repo、service 間の保証関係と検討中の根拠強度は
[identity-ownership-evidence.md](notes/identity-ownership-evidence.md) に分離しています。

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
  - `source_code_urls`
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
  - `category=auth_only` で、上記の強い protocol surface が無い
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
- 既知 collection の activity refresh は毎回やります
  - `count`
  - `recent_count`
  - `min`
  - `max`
- import 対象にするのは:
  - collection NSID が正常
  - 既知 NSID ではない
  - 最新活動の `max` が watermark より新しい
  - watermark が無い初回は `lookback_days` 以内
- import 前に namespace 集約します
  - full collection ではなく registrable namespace 単位で 1 project 候補にまとめます
- reject する主な条件:
  - namespace が短すぎる
  - authority domain が import 許可ルールに合わない
- 既存 project への match 順:
  1. `exact_namespace`
  2. `exact_namespace_scan`
  3. `registrable_namespace`
  4. `service_host_family`
- 自動 attach するのは 1 と 2 だけです
- 3 と 4 は `suggested_project_key` に落とし、既存 project に自動では吸いません

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
- 自動 attach するのは 1 と 2 だけです
- `--screen-unmatched` を付けた場合、fork repo は追加で厳しく見ます
  - GitHub 上で `fork=true`
  - かつ homepage / service candidate / owned lexicon surface のいずれも見えない
  - この条件なら `github_fork_without_independent_surface` で新規 project 候補から落とします

### `sync-tangled-repos`

- UFOs の `sh.tangled.repo` rolling window を `time_us` watermark で増分走査します
- 完全履歴とは扱わず、前回watermarkへ届かなければ `gap_possible=true` を返します
- atproto relevanceを通過したrecordだけを対象にします
- owner DIDまたはwebsiteで既存projectへ照合します
- unmatched project作成は既定offで、`--create-unmatched`が必要です
- confirmed developer handleがある通常enrichでは、handle -> DID -> PDSを解決し、
  `com.atproto.repo.listRecords`で本人のTangled repo recordを取得します
- owner DID一致はrepo所有の強い根拠ですが、対象projectとの関連性はname、website、内容で別にscoreします
- timeline、community search、bot adapterは正規化parserのみ実装済みで、config既定無効かつ通常導線未接続です

## マージ時の editorial 優先

`merge_project_records()` は単純な `current or incoming` ではなく、`project_editorial_preference()` を使って service 本体らしい project を優先します。

主に見ているもの:

- `manual_seeds` に `service_url` があるか
- `website_url`
- `service_urls`
- `source_code_urls`
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

## 将来的にやるべきこと

### 1. service 判定の後半強化

未実装の候補は、優先ページごとの signal 細分化、認証後の利用可能性、複数 surface の統合です。
anonymous browser 検証は opt-in CLI と review queue に接続済みです。現行実装との境界は
[service-verification.md](notes/service-verification.md) に整理しています。

### 2. editorial quality の洗い替え

- merge 時の主従は改善済みだが、既存 `tagline` / `summary` の品質問題は残る
- 直したい方向:
  - `service project` と `lexicon project` で prompt を分ける
  - `resource_url_records` や repo README を優先 evidence として使う
  - `tagline_is_provisional` が長く残っている project を review queue へ送る

### 3. rate limit / partial failure 耐性

source別 retry/backoff、TTL cache、request budget、source-local circuit breaker は接続済みです。
未接続なのは複数projectを跨ぐ永続circuit breaker、session/PDSを含む全collectorへの統一適用、
実運用値の調整です。現状と残件は [collector-reliability.md](notes/collector-reliability.md) を参照してください。

### 4. sync コマンドの整理

- `sync-collections`
  - fetch 全件はまだ残っている
  - API 側に差分 query が無いので、必要なら snapshot 比較を更に強化する
- `sync-github-search`
  - broad query の tuning
  - `screen_unmatched` の精度向上
- `sync-semble-collections`
  - 現状は固定 2 collection 埋め込み
  - 運用が固まったら config 化する余地がある

### 5. DB 編集 UX

- `assist-db-edit` は自然文から command 提案まで対応した
- 将来的には:
  - `split-project`
  - seed 追加
  - `review-stalled` の承認反映
  のような操作も対話的に扱いたい

### 6. 内部 shape の最終整理

- schema v2ではidentifier、relation、decisionを専用tableへ移行済み
- collector/evaluation間のflat project dictは`runtime_view.py`で組み立てる明示的な非永続viewになった
- 長期的にはtyped aggregateまたは用途別viewへ寄せ、全project hydrateを減らし、normalized tableを直接queryする検索・collision監査を増やす
- 3 は suggestion に落とします
- unmatched を `--screen-unmatched` する場合:
  - repo metadata / README / tree / lexicon candidate JSON を追加で見ます
  - 次のどれかがあれば通します
    - lexicon hint
    - lexicon らしい path
    - derived NSID
    - lexicon schema sample
    - repo homepage
    - service candidate
    - atproto keyword hit
  - 何も無ければ `screened_out_entries` に落とし、project を作りません

### `sync-semble-collections`

- 対象はコード内で固定した tracked Semble collection だけです
- 実装済み:
  - Semble の現行 XRPC API `https://api.semble.so/xrpc/network.cosmik.collection.getByAtUri` を使います
  - `handle`, `recordKey`, `page`, `limit` を query parameter として渡し、返却 payload の `urlCards` を取り込みます
  - collection label は Semble payload の `name` があればそれを優先し、無ければ tracked collection の local label を使います
- 動作確認済み:
  - 2026-06-07 時点で旧 `/api/collections/at/{handle}/{rkey}` は Semble live API で 404
  - 同日時点で現行 XRPC API は tracked 2 collection について 200 を返し、`sync-semble-collections --pages 1 --per-page 1 --all-history --no-create-unmatched` の dry-run が成功
- 各 card URL を `classify_text_seed_value()` で seed に落とせるものだけ使います
  - `nsid`
  - `repo_url`
  - `service_url`
- tracked collection ごとに `card_updated_at` watermark を保存します
- `sorting.sortBy=updatedAt` / `sortOrder=desc` を確認できた response だけ、古い card 到達で早期停止します
- watermark が無い初回は `lookback_days` を使います
- 既存 project 解決は `resolve_project_key_for_seed_value()` を使います
  - `repo_url` / `service_url` は exact identifier match
  - `nsid` は `find_project_key_for_nsid_seed()` で namespace / registrable namespace / service host family まで見ます
- 注意:
  - 現状の `sync-semble-collections` は、`sync-collections` や `sync-github-search` と違って weak match を suggestion に落とさず、そのまま attach し得ます
  - 仕様として明示されているというより、今の実装挙動です

### sync state の更新条件

- `sync_state` は project record ではなく source ごとの運用 checkpoint です
- scope:
  - introduced: search query
  - collections: inventory source URL
  - GitHub: query set。watermark は query ごと
  - Semble: tracked collection set。watermark は collection ごと
- successful execute かつ走査完了時だけ更新します
- dry-run、例外終了、ページ上限到達では更新しません
- `--no-create-unmatched` で未一致が残った場合は、その候補を次回も出すため更新しません
- 境界 timestamp と同時刻の項目は次回も処理対象にします。exactly-once ではなく at-least-once です

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
- GitHub repo enrich:
  - 1 repo あたり通常 3 request
    - `GET /repos/{owner}/{repo}`
    - `GET /repos/{owner}/{repo}/readme`
    - `GET /repos/{owner}/{repo}/git/trees/{branch}?recursive=1`
  - さらに lexicon hint を精査する時は `contents/<path>` を最大 16 件追加
  - つまり 1 repo あたり概ね 3、最大で 19 request 程度です
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
  - `ATPROTO_SURVEY_LLM_ENDPOINT` なら通常 2 POST
    - investigation plan
    - triage
  - endpoint fallback が走ると chat/responses/completions を順に試すので、失敗時はこれより増えます
  - 実装済み: LLM endpoint は operator が明示設定する trusted endpoint として扱い、LAN / localhost 宛てでも POST を許可します
  - 実装済み: project data 由来の service / resource URL は同じ `request_bytes()` 系でも内部宛て block の対象です
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
  - screen 関数内の `fetch_github_repo()` で 3 request 前後
  - その後 `enrich_from_github_repo()` が同じ repo をもう一度 fetch するのでさらに 3 request 前後
  - さらに lexicon candidate JSON を最大 16 file 取得
  - つまり unmatched repo 1 件あたり概ね 6、最大で 22 request 程度です

### `sync-semble-collections`

- Semble API への page fetch だけです
- request 数は `tracked_collections * pages`
- 既定値では tracked collection が 2 件、`pages=1` なので通常 2 request です

### 補足

- Bluesky session は process 内で cache します
  - 同一 run 中の `createSession` は原則 1 回
- GitHub の search result も process 内 cache します
  - 同じ query を同じ run で繰り返す時だけ有効です
- GitHub repo metadata と service probe も process-local TTL cache を使います
- cache は永続化しないため、別 process の再実行では再fetchします

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

- `input_nsids`
  - 手動seedとして投入された NSID
- `resolved_nsids`
  - lexicon解決に成功した NSID
- `confirmed_nsids`
  - 内部で扱う高信頼 NSID
- `lexicons`
  - legacy 上の canonical NSID。移行後は evidence 側の観測値として残す
- `lexicon_namespaces`
  - project 粒度で記録する namespace

collection inventory import は project 粒度で集約しますが、lexicon 解決用に代表の full NSID を別に保持します。
既知 collection を `sync-collections` した時も `collection_observations` の `count` / `recent_count` / `max` は更新します。`latest_collection_activity_at` 相当の用途では、これを「最近 record が流れた時刻」とみなします。

## 確認済みと候補の区別

- confirmed
  - `website_url`
  - `service_urls`
  - `source_code_urls`
  - `confirmed_nsids`
- observation
  - `resource_url_records`
  - record ごとに `legacy / candidate / confirmed` を持つ
- candidate
  - `service_url_candidates`
  - `candidate_source_code_urls`
- deferred
  - `deferred_identifiers`
  - URL / NSID を恒久 block せず一時保留する運用カテゴリ
  - URL は candidate に降格して観測可能なまま残す
  - NSID は専用 candidate 配列を追加せず `deferred_identifiers` に保持する
  - 既定 30 日。期限内は confirmed への自動昇格を抑制し、期限切れ後は通常導線へ戻す
- promotion deferred
  - `promotion_deferrals`
  - 紹介候補として再浮上させるまでの理由付き一時保留履歴
  - 既定 7 日。active な期限内 entry がある間だけ `promotion-queue` から除外する
  - 期限切れ entry や clear 済み entry も履歴として残し、再保留時の理由再利用と将来の復活条件判定に使える形にする
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

技術的な学習価値は、実用性や紹介準備とは別軸で `technical_interest_*` に保持します。LLM は evidence に基づいて `none / notable / high / exceptional` を暫定評価し、理由を enum と短い説明で残します。`promotion-queue` の `technical_insight_candidate` は `high` / `exceptional` だけを対象にします。`notable` は将来の再評価材料として保存しますが、推薦 signal にはしません。

理由 enum:

- `novel_design_pattern`
- `protocol_gap_exposed`
- `interoperability_experiment`
- `unusual_architecture`
- `implementation_reference`

endpoint 方式の timeout は `ATPROTO_SURVEY_LLM_TIMEOUT` で指定し、既定は `300` 秒です。

## merge

`duplicate_candidates` と `merge_recommendation` は派生データです。必要なら再計算できます。

`merge-batch` は保守的です。曖昧な一致は自動実行せず、`merge` の dry-run / execute で人が判断する前提です。

merge/split/separateの複数project書込みはoperation単位のSQLite transactionで実行します。
repositoryの`save_project(..., commit=False)`を使い、2件目の保存、alias作成、source削除まで
成功した時だけcommitします。merge済みsource keyは`project_key_aliases`からcanonical keyへ解決し、
alias元の`investigation_runs`もcanonical projectの履歴一覧へ含めます。run JSON自体は変更しません。

`review-queue` は表示のたびに `duplicate_candidates` / `merge_recommendation` を再計算しません。通常導線では保存済みの派生値を使い、再計算は `repair-db`、seed 追加、merge / split / block / separate など identifier を変える操作に寄せます。

また、`review-queue` の通常表示は保存済み DB を信頼した軽量 shape を使います。保存時・repair 時の full normalization は維持しますが、一覧表示では URL / repo / lexicon の重い再正規化を避けます。

`promotion-queue` は通常表示と interactive の両方で、対象 project 全件から signal と `promotion_queue_sort_score()` を計算し、`top_priority`, `sort_score`, `project_key` の順でグローバルソートしてから `--limit` を適用します。紹介候補は上位から見ること自体が目的なので、interactive でも部分バッチ先行にはしません。

`promotion_deferrals` に active な entry がある project は signal 計算前に除外します。interactive の `h` は理由付きで既定 7 日の entry を追加します。過去履歴は消さないため、再保留では直近理由を既定値として再利用できます。理由から「未実装機能が実装された」などの復活条件を自動判定する処理は将来候補で、現状は期限だけを見ます。

`audit-source-config [domain]` は [config/domain-overrides.json](../config/domain-overrides.json) の `custom_multi_tenant_suffixes` 追加後基準で既存 project を読み取り監査します。discovery fetch 設定の `config/sources.json` とは保存責務を分離しています。service URL と NSID authority を tenant 単位に分け、同一 project 内に複数 tenant が残る時だけ `split_suggestions` を返します。仮 tenant project を組み立てて `compute_duplicate_candidates()` を通すため、分割後に既存 project との merge candidate が成立するかも表示できます。

このコマンドはデフォルト interactive です。tenant group の番号を選んだ場合だけ、service URL / NSID を 1 つの新 project へ原子的に切り出し、元 project に block、新旧 project に相互 `separate_project_keys` を入れます。split 適用後は suggestion を再構築し、同じ元 project に複数 tenant が残る間はその project を優先して再表示します。Enter で送った suggestion key はその実行中だけ skip set に残します。共有 repo は自動配賦せず ambiguous として表示します。`--execute --no-interactive` の責務は引数 domain の `domain-overrides.json` 追記だけで、split は自動適用しません。

### duplicate candidate の作り方

- まず各 project から duplicate profile を作ります
  - `source_code_urls`
  - `github_repo_urls`
  - `candidate_source_code_urls`
  - `service_urls`
    - `website_url` もここに含めます
  - `recorded_lexicon_namespaces`
  - `record_creators`
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
- これらの値ごとに inverted index を作り、どれか 1 つでも共有する相手だけを候補集合に入れます
- `github_repo_urls`は保存fieldではなく、`source_code_urls`からGitHub providerだけを導出したduplicate比較用の内部keyです
- ただし次は最初から除外します
  - 自分自身
  - `separate_project_keys` で相互 block された pair

### candidate の根拠

- `compute_duplicate_candidates()` は候補相手ごとに次の overlap を見ます
  - `source_code_urls`
  - `github_repo_urls`
  - `candidate_source_code_urls`
  - `service_urls`
  - `recorded_lexicon_namespaces`
  - `record_creators_and_namespace_lineage`
    - creator DID が重なり、かつ namespace が同一または親子
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
  - `bootstrap_first_record_creator_dids`
    - 明示的に heuristic を有効化した時だけ
- `record_creators` 単独では merge 理由にしません
  - namespace lineage が同時に必要です
- `candidate_source_code_urls` は confirmed repo より弱い根拠ですが、duplicate 候補の入口には使います

### score と confidence

- score は理由ごとの重み付き合計です
  - `source_code_urls`: 8
  - `github_repo_urls`: 8
  - `service_urls`: 7
  - `record_creators_and_namespace_lineage`: 7
  - `recorded_lexicon_namespaces`: 6
  - `authority_service_families`: 6
  - `candidate_source_code_urls`: 5
  - `domain_identity_tokens`: 5
  - `bootstrap_first_record_creator_dids`: 6
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

### merge-batch の追加安全策

- `merge-batch` は `duplicate_candidates` をそのまま自動実行しません
- 候補 pair ごとに mutual recommendation を要求します
  - A の top が B
  - B の top も A
- さらに safe overlap を別ロジックで見直します
  - `strict`
    - strict GitHub/Tangled repo overlap
    - manual repo seed overlap
    - manual service seed host overlap
  - `moderate`
    - strict 条件に加えて
    - confirmed service overlap
    - candidate repo + alias overlap
    - creator DID + namespace lineage
    - domain token + alias overlap
    - authority family + domain token
    - service root domain + alias overlap
    - website root domain + alias overlap
    - optional bootstrap first creator DID heuristic
- safe overlap が無い pair は recommendation があっても batch では落とします

### merge 方向の決め方

- `choose_batch_merge_direction()` は「どちらを target に残すか」を `project_batch_merge_preference()` で決めます
- 優先順位は概ね次です
  - manual seed の強さ
    - `service_url` > `nsid` > `repo_url`
  - editorial richness
    - `website_url`
    - `service_urls`
    - `source_code_urls`
    - `resource_url_records`
    - `service_status`
    - `introduced_url`
    - confirmed NSID 数
  - confirmed identifier の数
  - `introduced_url` の有無
- つまり、より first-party surface が強く、より編集済みで、より confirmed identifier が多い側を target に残しやすいです

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

旧 `data_json` 前処理はもう行いません。
schema cleanupも完了済みで、現行DBは`registry_meta.schema_version=2`とnormalized child tablesを前提にします。旧DBは`migrate-db`でのみ読み込みます。

## review-stalled

`review-stalled` は `active/watchlist -> stalled` の候補だけではなく、

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
- `watchlist` はここでは `stable` に落としません

### `active/watchlist -> stalled`

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
    - `source_code_urls`
    - `resolved_nsids`
- `stalled` を提案するには:
  - `hard_signal` が少なくとも 1 つ必要
  - 理由が 2 件以上必要
- つまり、単に `unknown` で証拠が薄いだけでは `stalled` にしません

### 調査鮮度ゲート

- `max_enrich_age_days` は停止の根拠ではなく、降格判定に使える調査結果の最大経過日数です
- 未enrichまたは期限超過の `active/watchlist` は `stalled` 候補を返しません
- recent activityによる `stalled -> active` と `active -> stable` は、調査鮮度ゲートより先に判定します

### review-stalled の対象範囲

- `review-stalled` が見る tracking state は:
  - `active`
  - `watchlist`
  - `stalled`
- `stable` / `out_of_scope` / `archived` / `ignore` はこの自動判定の対象外です
- `--execute` を付けると、提案された `suggested_tracking_state` をそのまま保存します

## review-out-of-scope / review-ignore

`review-out-of-scope` は関連性を否定せず、現在のsurvey対象から外す候補を返します。

- `auth_only_without_owned_protocol_surface`
  - `category=auth_only`
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
  - confirmed な source repo が GitHub fork
  - `website_url` / `service_urls` / `introduced_url` が無い
  - owned protocol surface (`resolved_nsids`, `input_nsids`, `recorded_lexicon_namespaces`, `collection_observations`) も無い
  - `service_status=live|beta|invite_only` なら除外

目的はatproto無関係、誤登録、独立identityのない複製など、通常の再評価を期待しない候補を `tracking_state=ignore` へ寄せることです。既定では dry-run で JSON を返し、`--execute` を付けたときだけ保存します。
