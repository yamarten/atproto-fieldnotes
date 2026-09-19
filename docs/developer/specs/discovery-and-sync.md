# 発見と取り込み

外部情報源（source）から候補を発見し、既存projectへの照合または新規登録へ進める契約です。source設定、取得範囲、取り込み済みの境界を示すwatermark、未一致・曖昧な一致の扱いを定めます。処理は「設定されたsourceを取得する → identifierなどで既存projectへ照合する → 更新または新規登録する」です。未知候補を新規登録するsyncと、既存projectの紹介済み状態だけを更新するsyncを分けます。未一致・曖昧な一致は、sourceごとの進行位置とともに保留情報へ残します。取り込みの後に内容を詳しく調べる処理がenrichです。

## source設定とpolicyの境界

設計上、operatorが運用中に変更するsource URL、有効・無効、query、request数の上限、取得対象範囲はconfigへ置けます。一方、payloadの解釈、evidence抽出、同一性・所有の信頼条件、identifierやrelationのclassification、既存projectへの自動追加や紹介候補の選定条件はcodeとtestを正本とします。

通常導線では`config/sources.json`がintroduced、collection inventory、GitHub、Semble、Tangledのoperator設定正本です。sourceごとのenabled、URL、query、tracked collection、pages/per-page/limit/lookback等の取得上限・期間を持ち、application sync serviceが解決します。CLI/Web/workerから明示された値は、その取り込み実行中だけ設定値より優先します。disabled sourceはnetwork request前に拒否します。source追加時もこの責務分担に従い、builtin lexicon prefix、payload解釈、evidence抽出、照合・classification・自動追加の規則はcodeとtestで管理します。

## Source別の取り込み

`sync-collections` / `sync-github-search` / `sync-semble-collections` は未知候補の project 作成が既定 ON です。候補を作らず `unmatched_entries` の棚卸しだけ行う場合は `--no-create-unmatched`を付けます。`sync-introduced` は紹介済み判定専用なので、新規 project を作りません。

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

この節の親namespaceは、collectionの完全なNSIDから最後のname部を除いたものです。`registrable_namespace`はauthority domainを登録可能domain・tenant境界までまとめて逆順表記にした値、`service_host_family`は同じdomain境界を使うservice hostの照合です。domain境界の規則は[same-site family](project-boundaries.md#trust-graph)を参照してください。

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
- import前にcollectionの完全なNSID（full collection）ごとに既存projectとの一致を判定します
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
- 4と5は`suggested_project_key`として既存projectとの関連候補を示し、自動追加は保留します
- 同じ取込先へまとめた全full collectionを`owned_nsid:confirmed`として保存します。これはcollection inventoryの割当契約による所有判定です
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
- 既存projectへ共通信頼policyで採用したrepositoryには、`trust_policy`の採用来歴（adoption）を記録します。GitHub Searchからの発見だけでは、手動入力や独立した信頼判定の起点として扱いません
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

- `sync_state`はsourceごとの取り込み境界と保留情報を保存します
- introducedはquery、collectionsはinventory URL、GitHubはquery、Sembleはtracked collectionごとにwatermarkを持ちます
- 上記sourceはsuccessful executeかつ走査完了時だけ更新し、dry-run、例外、page上限到達、未一致を残す`--no-create-unmatched`では更新しません。Tangledのsequence checkpointは次節の契約を使います
- 境界timestampと同時刻の項目は次回も処理するため、同じ項目を複数回取得し得るat-least-once方式です

### `sync-tangled-repos`

- 公式`@bsky/jetstream` SDKでJetstream v2 snapshotを取得し、plan時点で取得範囲の終端として確定したsequence（sealed tip）を固定します
- `sh.tangled.repo`のcreate/update/deleteをsequence順に畳み込み、保存済みsequence watermarkから増分再開します
- event上限で途中終了したexecuteは最後に処理したsequenceを保存し、次回に残りを再開します
- 通常上限は1,000 eventです。初回archiveも複数回の取り込みに分けて進めます
- atproto relevanceを通過した現存recordだけを対象にします
- 正規化したTangled repo URLだけで既存projectへexact照合します。owner DIDやrecord websiteだけでは照合しません
- atprotoとの関連条件を満たすrecordのowner handleはDB変更前にDIDごとに解決・cacheします。Bluesky `getProfile`には依存せず、DID documentの`id`一致、`alsoKnownAs`の最初の構文上有効なhandle、public AppViewの`com.atproto.identity.resolveHandle`による同じDIDへの逆引きを確認します
- unmatched project作成は既定onです
- API keyは`ATPROTO_SURVEY_JETSTREAM_API_KEY`環境変数からだけ読み、request、event、設定、stateへ保存しません。

enrich時のPDS・repository inspectionは[調査仕様](investigation.md#tangledの調査)を参照してください。

#### Ownerのidentityと解決不能record

Tangled URLはownerとrepo nameを表し、recordの`repoDid`はowner DIDではありません。公式の[URL例](https://docs.tangled.org/single-page#webhooks)にはowner DID形式もありますが、現行registryはhandle形式のrepo URLで照合・保存します。DID形式への一括変更や未検証handleへの代入は、既存URLとの二重登録・誤帰属につながるため行いません。ownerだけで複数repoを同じprojectへ結び付けることもありません。

Blueskyの停止・profile未登録はATProto identityの無効を意味しません。[DID仕様](https://atproto.com/specs/did)と[handle仕様](https://atproto.com/specs/handle)に従い、プロフィールの有無から独立した双方向検証を使います。`alsoKnownAs`に複数handleがあっても、最初の有効なclaimが逆引き不一致なら次のclaimへ逃げません。

| 解決結果 | 取り込みの扱い |
| --- | --- |
| DID document取得の404/410、未対応owner DID、documentのid不一致、handle claimなし／無効、handle逆引きのDID不一致 | そのownerのrecordを帰属未確定として保留し、有効ownerの処理を続ける |
| resolveHandleの400 `HandleNotFound`、または400 `InvalidRequest`かつmessageが厳密に`Unable to resolve handle` | 同じく保留。後者には上流DNSの一時障害も含み得るため、永久無効と断定しない |
| 通信例外・timeout、429、5xx | 最大3試行、待機は1回最大2秒。失敗が続けば書込み前に全体を失敗させ、watermarkを進めない |
| 未知の400、401/403、resolveHandle endpointの404、壊れた成功payload、その他の設定・契約エラー | 全体を失敗させる。job errorにはowner DID・取得段階・HTTP statusまたは例外種別を付ける |

解決不能recordは単純skipで捨てず、projectにもreview queueにも登録せず、`sync_state.watermark_json.pending_owner_records`に元のowner DID・rkey・record・seq・理由を保存します。URLを安全に作れない段階でproject帰属を与えないためです。これは独立したcatalog DBではなく、Tangled source内の保留情報です。

通常の増分実行では、新規event上限とは別に、同じ上限件数まで保留recordを再評価します。未解決の再評価済みrecordは末尾へ回し、未試行recordが先になります。同じURIの新しいupdateは置換、deleteは除去し、同一ownerの解決は実行内で1回です。保存する保留一覧は件数で切り捨てません。保留の蓄積でDB容量は増え得ますが、実行ごとの外部解決数は新規候補＋再評価上限で制限します。`--all-history`は保留の定期再評価をせずarchiveを先頭から処理し、観測したURIの保留状態も更新します。

#### 保存・結果・中断

- 正常に評価を終えたexecuteでは、project変更・保留一覧・sequenceを同じtransactionで保存します。保存例外は全体をrollbackし、次回は同じsequenceから再実行します
- 保留ownerがいても保存できればwatermarkを進めます。`create_unmatched=false`で有効ownerの未一致候補が残る場合、dry-run、`incremental=false`ではwatermarkと保留一覧を保存しません
- owner解決中のキャンセルはprojectを書きません。project処理中の協調的キャンセルは処理済みprojectだけをcommitし、watermark・保留一覧を進めません。再実行で同じ範囲を再評価・exact照合します
- event上限やJetstreamの途中取得エラーで取得済みprefixを処理した場合は、既存の契約どおり`last_event_seq`までcheckpointし、`truncated` / `snapshot_error`で残りの再開が必要なことを示します。全取得時はsealed tipまで進めます
- `skipped_owner`は今回保留にしたrecord数、`unresolved_owners`はそのowner数、`owner_errors`はownerごとの理由です。`retried_owner_records`は今回選んだ再評価件数、`pending_owner_records`は処理後の保留候補数です。永続化されたかは`state_updated`で確認します。`results`は先頭100件ですが、保留一覧の保存にはこの表示上限を適用しません
- progressは`owner_identity`段階とrecord処理を分け、保留recordには`outcome=deferred_owner`と理由を付けます。部分成功でもsource処理自体は成功ですが、保留件数は0とは限りません。`complete_history`は走査範囲の指標であり、全候補の登録完了を意味しません

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
