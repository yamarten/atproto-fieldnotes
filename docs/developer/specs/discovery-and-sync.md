# 発見と取り込み

外部情報源（source）から候補を発見し、既存projectへの照合または新規登録へ進める契約です。source設定、取得範囲、取り込み済みの境界を示すwatermark、未一致・曖昧な一致の扱いを定めます。処理は「設定されたsourceを取得する → identifierなどで既存projectへ照合する → 更新または新規登録する」です。未知候補を新規登録するsyncと、既存projectの紹介済み状態だけを更新するsyncを分けます。未一致・曖昧な一致は、sourceごとの進行位置とともに保留情報へ残します。取り込みの後に内容を詳しく調べる処理がenrichです。

## 新規登録と既存projectへの追加

新規projectの登録は調査対象の採用を伴います。`confirmed`と採用理由を同時に保存し、発見した情報源と採用理由を区別します。

| 入口 | 新規projectが採用する対象 |
| --- | --- |
| `sync-lexicons` | 最初に受け入れた完全NSID一つ。公開Lexiconの各型に共通。残りは候補 |
| `sync-collections` | 割当契約でまとめた全full collection。代表collectionにもadoptionを保存 |
| GitHub / Tangled | 受入条件を通過したrepository URL |
| Semble / seed list | 受け入れたseedのNSID、repository URL、service URL |
| `add-seed` / Webのシード登録 | 利用者が指定した対象。operator adoptionとして記録 |

既存projectへの追加には各入口の照合・帰属規則を使い、登録起点と同じ扱いで候補全体を採用しません。`sync-bluesky-lexicons`は既存の一意な対象だけを更新し、新規projectを作りません。`sync-introduced`も新規登録せず、既存projectへの紹介URLの反映と要確認の保存を行います。対象の採用と発行元の真正性・projectの追跡価値は[project境界](project-boundaries.md#登録時に対象を採用する)で区別します。

## source設定とpolicyの境界

設計上、operatorが運用中に変更するsource URL、有効・無効、query、request数の上限、取得対象範囲はconfigへ置けます。一方、payloadの解釈、evidence抽出、同一性・所有の信頼条件、identifierやrelationのclassification、既存projectへの自動追加や紹介候補の選定条件はcodeとtestを正本とします。

通常導線では`config/sources.json`がintroduced、collection inventory、Lexicon公開情報、GitHub、Semble、Tangledのoperator設定正本です。sourceごとのenabled、URL、query、tracked collection、pages/per-page/limit/lookback等の取得上限・期間を持ち、application sync serviceが解決します。CLI/Web/workerから明示された値は、その取り込み実行中だけ設定値より優先します。disabled sourceはnetwork request前に拒否します。source追加時もこの責務分担に従い、builtin lexicon prefix、payload解釈、evidence抽出、照合・classification・自動追加の規則はcodeとtestで管理します。

## Source別の取り込み

`sync-collections` / `sync-github-search` / `sync-semble-collections` は未知候補の project 作成が既定 ON です。候補を作らず `unmatched_entries` の棚卸しだけ行う場合は `--no-create-unmatched`を付けます。`sync-introduced` は紹介済み判定専用なので、新規 project を作りません。

### `sync-introduced`

- 入力は Bluesky post search の結果です
- 現行の自動照合は facet URL から seed を抽出し、`collect_applog_entries()` で project 候補に束ねます。本文中の非 facet URL は自動照合せず、要確認として記録します
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
- 取得した投稿は投稿URIとURLの組（URLがない場合は投稿URI）を識別子として、抽出・照合の結果を同じqueryの`sync_state.watermark_json.review_items`に保存します。facetにない本文URL、seedにならないURL、URLなし、未一致、複数project一致、既存の別の紹介URLにより未反映のURLを理由付きで要確認にします。自動反映できたURLも同じ投稿の他URLと区別して記録します。保存済みの旧`pending_entries`は読込み時に一覧へ投影し、次の実行または手動判断で`review_items`へ移します
- 投稿本文の表示用に、URLの完全一致またはUTF-8境界・範囲を検証したfacet位置から対象の1行を`post_line`に保存します。同じURLが複数行にある場合や位置を特定できない場合は空にします。既存項目で`post_line`がない場合は、保存済み本文の完全URL一致で特定できる1行だけをquery時に投影します。要確認APIは保存済みの`matched_project_keys`に対応する現行の名前・紹介URLを`matched_projects`へ、手動紐付け先を`linked_project`へ投影し、再照合やDB更新は行いません
- 未一致のfacet seedは後からprojectが登録された時に再照合します。複数projectに一致した場合は従来どおり最優先の一件へ自動反映し、選ばれなかった一致先も含めて要確認に残します。手動で対象外・紐付け済みと判断した投稿URLは再同期で自動反映せず、判断を取り消した時だけ再び要確認になります
- Webの「紹介投稿の要確認」では元投稿とURL・理由を確認し、登録projectへの`introduced_url`設定または対象外判断で解消します。`introduced_url`が別URLなら明示的な付け替え確認を要します。この操作はidentifierのclassificationを変更しません。複数一致でこの同期が先頭projectへ紹介URLを自動設定した項目は、別projectへの手動紐付けや対象外判断でその自動設定だけを外します。同じ投稿の別URLが先頭projectへ自動一致または手動紐付けされている間は外しません。判断取消時は、同じ投稿への他の紐付けがなく、この操作で変更した値がまだ同じ場合だけ元へ戻し、再照合は要確認項目に対して明示的に実行できます
- introducedの保存実行では、投稿取得後のproject更新・レビュー項目・watermarkを一つのtransactionで保存します。途中の例外ではすべてrollbackします
- executeの取得がpage上限や停止で未完走でも、取得済み投稿の要確認項目と未完走状態は保存し、watermarkは進めません。要確認0件は検索結果を完全取得した証拠ではなく、走査完了は設定された検索範囲内だけを示します。dry-runはDBへ書きません
- この記録を導入する前に取得され、seedを作れず消えた投稿の本文・URLは既存DBから復元できません。必要な過去範囲の再取得は別の同期実行を要します

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

### `sync-lexicons`

公開Lexiconの完全なNSIDを発見し、同一NSID、直属namespace、新規暫定projectの順で候補を照合します。spaceのcollection宣言は参照として保存し、対象collectionの所有候補は作りません。取得・検証・再開・dry-run・予算の契約は[Lexicon公開情報](lexicon-publication-catalog.md)を参照してください。

`sync-collections`の完全取得成功時には、project集約とは別に完全NSIDのinventoryを保存します。失敗時は比較不能状態にし、最後の成功一覧を保持します。

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
- 既存URLに一致しないrecordは、`source`にTangled fork元の宣言がある場合、新規作成から除外します。GitHubの通常検索の`fork:false`に合わせるもので、独立性screeningを通常発見の例外にはしません
- API keyは`ATPROTO_SURVEY_JETSTREAM_API_KEY`環境変数からだけ読み、request、event、設定、stateへ保存しません。

enrich時のPDS・repository inspectionは[調査仕様](investigation.md#tangledの調査)を参照してください。

#### Forkの新規発見と明示登録

公式[`sh.tangled.repo` lexicon](https://tangled.org/tangled.org/core/blob/master/lexicons/repo/repo.json)の`source`は任意のURI文字列です。公式[`ForkRepo`処理](https://tangled.org/tangled.org/core/blob/master/appview/repo/repo.go)は親repositoryの`repoDid`を、未割当なら親の`sh.tangled.repo` AT-URIを保存します。`repoDid`はrepositoryの識別子で、owner DIDではありません。この保存契約に該当する`source`をfork元の宣言として扱い、親の取得・生存確認や内容比較は行いません。

| `source`の状態 | 結果の`fork` / `fork_source_status` | 未登録URLの扱い |
| --- | --- | --- |
| DID構文に合うrepository DID | `true` / `repo_did` | `skipped_fork`。新規projectを作らない |
| DID authority、collection=`sh.tangled.repo`、有効なrkeyを持つAT-URI | `true` / `repo_at_uri` | 同上 |
| 欠落、null、空文字 | `null` / `missing` | fork不明として従来の関連性・作成条件を使う |
| 外部HTTP/git URIなど上記の形式に該当しない文字列 | `null` / `unrecognized` | 同上。mirror/import/cloneとforkを同一視しない |
| 型不正、空白混入、壊れたDID/AT-URI、別collection | `null` / `invalid` | 同上。不正な宣言からforkを確定しない |

構文検査は参照先の実在や所有の検証ではありません。親が消失・参照不能でも宣言の解釈は変わりません。`source`を持たないcloneや、未知形式で親を示すrepoは検出できません。fork元を同じprojectへの照合・merge根拠には使いません。

owner解決と既存repo URLへのexact照合を先に行います。owner解決不能なら従来の保留へ進み、fork除外で保留を捨てません。既存projectへ一致したforkは通常どおり更新します。独立したforkを調査対象にしたい場合は、従来の`add-seed`等で明示登録でき、その後の同期更新も継続します。website、READMEやLexiconがあるだけでは通常の新規発見のfork除外を解除しません。GitHubの明示的なfork検索＋`--screen-unmatched`にある候補受入判定とは別の経路です。

結果の`skipped_forks`は新規作成から除外した件数です。各`results`とprogressには`outcome=skipped_fork`、`reason=tangled_fork_excluded_from_discovery`、`fork_source`とその形式を返します。解決済みownerの通常結果にも`fork`と`fork_source_status`を返し、不明を`false`にしません。`results`の既存の先頭100件制限は維持します。

除外は追跡不要のoperator判断ではなく、取り込み対象の選別です。`--no-create-unmatched`や`--all-history`でも選別し、除外だけなら未一致の保留とは数えずsequenceを進められます。除外候補の専用再試行queueは作りません。新しいrecord updateや明示的な履歴再走査で再評価しますが、再走査も同じ選別規則を使います。登録したいforkは明示登録してください。既存projectの削除、ignore化、mergeは行いません。

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
