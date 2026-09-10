# 発見と取り込み

外部sourceから候補を発見し、既存projectへの照合または新規登録へ進める契約です。
source設定、取得範囲、watermark、未一致・曖昧な一致の扱いを定めます。
取り込むresourceの帰属と採用は[project境界](project-boundaries.md)、
登録後の調査は[investigation](investigation.md)を参照してください。

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

## Source別の取り込み

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
- 非現行NSIDとactive exclusionは再登録を抑止します。`--all-history`でも解除しません
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
- 上記sourceはsuccessful executeかつ走査完了時だけ更新し、dry-run、例外、page上限到達、未一致を残す`--no-create-unmatched`では更新しません。Tangledのsequence checkpointは次節の契約を使います
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
enrich時のPDS・repository inspectionは[調査仕様](investigation.md#tangledの調査)を参照してください。

## Source別の外部アクセス

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
