# Registry Maintenance Plan

この文書は、registryの正しさ、運用コスト、保存量、保守性を改善するための作業計画です。
作業用notesであり、実装済み機能や検証状況の正本ではありません。maintenance branch内の進捗と検証は
[registry-maintenance-status.md](registry-maintenance-status.md)、次作業は
[registry-maintenance-handoff.md](registry-maintenance-handoff.md)へ置きます。通常運用へ適用済みの内容だけを
`docs/status.md`と`docs/thread-handoff.md`へ反映し、見送った候補は`docs/roadmap.md`へ移します。

## 目的

- 再現済みの不具合と運用上の高コスト箇所を、個別に適用できる変更へ分ける。
- 保存量と調査品質を計測し、推測ではなく実データに基づいて次の保存契約を決める。
- `registry.py`の分割を、機能凍結の名目で先送りせず、修正したhotspotから段階的に行う。
- 今回実装しない指摘も、採否・前提・再検討条件が分かるTODOとして残す。

全改善項目を一度に完了させることは目標にしません。phase間の依存関係は保ちつつ、作業とcheckpointを
通常運用へ段階的に適用できる単位に分け、残件がroadmapとmaintenance用handoffへ移った時点で
maintenance seriesを区切れます。

## 進め方の原則

- 通常運用中にもbug fixや小規模なUI改善が並行して入る可能性を前提にする。
- 通常運用が続くことを理由に、旧内部API、deprecated alias、二重readerを恒久的には残さない。
- 各作業単位の開始前と適用前に最新実装との差分を確認し、小さいcommitで統合する。
- 機械的移動と挙動変更は別commitにする。
- 一時的なre-export facadeが移動中に必要でも、同じphase内でcall siteを移し終え、恒久互換層として
  残さない。
- 正本DBへのcleanup、migration、repairは、コード変更のmergeとは別のmaintenance operationとして扱う。

## 実行管理とcloseout gate

各実行単位はこの文書の`P<phase>.<item>` IDで追跡し、進捗・commit・検証・残件は
[registry-maintenance-status.md](registry-maintenance-status.md)の実行台帳へ1対1で記録します。

状態は次だけを使います。

- `完了`: itemの完了条件と必要な検証を満たした。
- `一部完了`: 実装または検証に具体的な残件がある。一時状態であり、series closeout時には残さない。
- `未着手`: まだ作業していない。
- `別operation待ち`: codeとは別の承認、maintenance window、外部接続が必要。
- `roadmapへ延期`: 今回実施しない判断と再検討条件を`docs/roadmap.md`へ移した終端状態。

maintenance seriesを閉じる前に、全IDを`完了`または`roadmapへ延期`へします。正本DB操作や実外部接続を
今回実施しない場合も、`別operation待ち`のまま曖昧に閉じず、通常運用へ適用する前提として実行するか、
具体的な再検討条件とともにroadmapへ移します。maintenance handoffは台帳上の未終端itemだけを参照し、
完了履歴を複製しません。

文書更新時は少なくとも次を集合比較します。

- planに現れるID集合とstatus台帳のID集合が一致する。
- statusで`一部完了`、`未着手`、`別operation待ち`のID集合とhandoffのID集合が一致する。
- statusに記録したcommit hashがrepository内で解決できる。

## 通常運用との並行方針

### 段階的に適用できる作業

- temp DBだけで完結するbug fixと回帰test。
- Python runtime、wrapper、test commandの整備。
- pure function、CLI adapter、表示専用処理の機械的移動。
- read-only report、dry-run、観測用logの追加。
- 現行仕様を変えない文書修正。

これらは通常運用DBのmigrationを必要としません。Web/workerが旧processのまま動いている場合、更新後の
codeはprocess再起動まで使われないことを明示します。

### maintenance windowが必要な作業

- 正本DBの既存payload cleanup。
- investigation historyの保存形式変更、圧縮、stub化、table分離。
- current stateを一括再評価するrepair。
- same-siteやSembleの過去誤帰属を正本DBへ反映するcleanup。

これらはwriter停止、backup、dry-run差分、人間確認、integrity checkを必須にします。通常運用を継続したまま
古いcodeと新しいDB形式を並行稼働させるcompatibility layerは作りません。必要なら、停止・code更新・DB更新・
検証・再起動を一つのmaintenance手順にします。

### 並行変更と競合しやすい作業

- `registry.py`、Webの`app.js`、`registry-internals.md`を広く触る作業。
- collection、authority、promotion heuristicの変更。

これらは着手直前に最新実装を取り込み、対象範囲を一つの責務へ限定します。並行変更と同じ関数へ
重なる場合、古い実装を機械的に再適用せず、その時点の実装意図を再確認します。

## Phase 0 [P0]: 計画と再現可能な基準を固定する

このphaseの成果物:

- 本計画をrepositoryへ残す。
- roadmapへ採用済みworkstreamを要約し、maintenance用handoffを実際の次作業と同期する。
- baseline testを適合Pythonで実行し、結果をmaintenance用statusへ記録する。

baselineで記録するもの:

- Python versionと選択経路。
- 公開wrapperを含む全unit test。
- JavaScript syntax check。
- investigation benchmark。

このphaseでは、過去のtest件数を現行HEADのgreenとみなしません。実行後の結果だけをmaintenance用statusへ
反映します。

実際のcommit、実行日、結果、未検証範囲は
[registry-maintenance-status.md](registry-maintenance-status.md)へ置き、この計画へ固定snapshotを複製しません。

## Phase 1: 独立した高確度bugを返済する

各項目を原則1 topic / 1 commitにします。

### P1.1: DB open transaction

実装対象:

- 新規DBのschema version rowがclose時にrollbackされないようにする。
- 既存DBをopenしただけで長いwrite transactionを保持しないようにする。
- open -> read only -> close -> reopenの回帰testを追加する。
- 2 connectionでread openが別writerを不要にblockしないことをtestする。

注意:

単に`conn.commit()`を1行足すだけならlock保持時間は短くなりますが、既存DBのopenごとの同値upsert自体は
残ります。新規作成時のschema ensureと既存DBの通常openを分け、read pathを実質的にwrite-freeへ寄せるかを
実装時に確認します。DB-level read-onlyが必要になった場合の専用audit接続は、この修正には含めません。

通常運用への影響:

- schema migrationなし。temp DBで完結し、早期マージ可能。
- 既存processは再起動まで旧codeを使う。

### P1.2: Python runtime / wrapper契約

実装対象:

- 最低Python versionを、READMEと機械的checkの両方で明示する。
- `.venv`が無い場合に、非対応のsystem Pythonでimport errorを出すのではなく、短い明示errorにする。
- 2つの公開wrapperをtest対象に含める。

判断:

- 最低versionは実装が実際に使う構文・stdlibと、正式にtestするversionから決める。
- package公開やbuild system導入は目的にしない。
- 非対応Pythonを動かすためのsyntax fallbackは追加しない。

通常運用への影響:

- DBと通常runtime stateを変更しない。早期マージ可能。

### P1.3: DB path / create契約

実装対象:

- 通常のread、Web起動、別checkout/worktreeからの実行で、DB pathのtypoから空DBを黙って作らない。
- 新規DB作成を明示的な初期化導線に限定する。
- CLIとWebでDB pathの解決・表示・存在checkを揃える。

既存の正本DBやcustom temp DBを開く導線は維持しますが、暗黙作成のfallbackは互換性のために残しません。
DB open transaction修正と同じファイルを触るため、同じphaseで扱いますが、挙動変更とtestは別commitにします。

### P1.4: `split-project --new-project-key`

採用方針:

- explicit key optionは削除する方向を第一候補とする。

根拠:

- project keyは内部のopaque keyで、自動採番導線が既にある。
- optionは現状一度も正常に機能せず、互換性を守る実績がない。
- explicit keyを必要とする通常運用例が文書・test・現行call siteに無い。

実装時にcall siteと利用記録を再確認し、実需要が見つかった場合だけ「新規key validatorを実装する」へ
変更します。壊れたoptionをdeprecated aliasとして残しません。

通常運用への影響:

- DB migrationなし。collision作業は自動採番で継続できる。

### P1.5: same-site first-label fallback

実装対象:

- unrelated registrable domainを先頭label一致だけでsame-siteにしない。
- exact host、registrable domain、明示的なhosted-domain policy、観測済みredirect/canonicalを区別する。
- service/repo/resource/redirectの主要consumerに回帰testを追加する。

未検証:

- 正本DBにこの誤判定由来のconfirmed identifierやrelationが何件あるか。

通常運用への影響:

- 新規評価は保守的になるため、codeだけはDB migrationなしでマージ可能。
- 過去データのaudit/repairはPhase 3のmaintenanceへ分ける。
- `registry.py`の競合が起きやすいため、着手直前に最新実装を取り込む。

### P1.6: Web `--read-only` modeの削除

削除対象:

- 公開CLIの`--read-only` option。
- `RegistryWebServer.read_only`と全POST拒否分岐。
- settings APIの`read_only` field。
- Web UIの`state.server.read_only`によるbutton・操作表示の分岐。
- read-only時だけschedulerを無効化する接続。
- read-only専用testと利用手順。

このmodeはWeb操作を抑止するapplication-level機能で、SQLite connection、CLI、別process、誤DB pathからの
書込みを防ぎません。運用利用実績がなく、開発時もtemp DBまたはSQLite snapshotを使うため、維持しません。
deprecated option、無視するalias、settings payloadの互換fieldは残しません。

次は別機能なので削除対象に含めません。

- `replay-investigation`のnetwork/DB非更新実行。
- `audit-*` command。
- mutationやcleanupのdry-run。
- SQLite snapshotに対するread-only集計。

将来DB-level read-only接続が具体的に必要になった場合は、Web modeを復活させず、SQLite URI `mode=ro`または
`PRAGMA query_only`を使う専用audit接続として別途判断します。

通常運用への影響:

- DB schemaと正本DBを変更しないため、code/test/docsを一括してマージできる。
- `app.js`と`server.py`を横断するため、着手直前に最新実装を取り込む。
- reload引数保存testは、read-only固有ではない別の一般optionで継続する。

## Phase 2: product brief、運用baseline、観測性

### P2.1: product brief

`docs/brief.md`を新設し、次だけを置きます。

- 目的、利用者、通常の週次flow、最終成果物。
- 紹介対象、調査対象、記録のみ、ignoreの境界。
- 外部directoryや検索はdiscovery upstreamとして利用できるが、operator decision、canonical project、
  merge/split、紹介済み状態はlocal registryを正本とする責務境界。
- 主要設計判断と非目標。
- 成功指標の名前。

数値目標はユーザー判断です。少なくとも次を決める必要があります。

- 週当たりoperator時間。
- 1件当たりreview時間。
- 全件または増分enrichの許容時間。
- 紹介候補、紹介文draft、網羅directoryのどれを最終成果物にするか。

数値未決定の間は「未決定」と明記し、仮の値を仕様にしません。

### P2.2: 静止DBでのbaseline report

通常運用中のDBへ重い集計を直接かけず、SQLiteの整合したsnapshotを使います。

計測対象:

- table/column別bytes、run当たりbytes、project別outlier。
- `resource_url_records`、`service_verification_results`、LLM evidenceの内訳。
- tracking state、last enrich age、queue件数、source別failure。
- 代表10〜20 projectのcollector / LLM / save / review時間。
- prompt長分布とtimeoutの分離。

過去snapshotの容量や内訳を現行DBの値として再利用せず、このreportで現在値を確定します。

### P2.3: phase timing

enrichへcollector、integration/evaluation、LLM、saveの所要時間を追加します。既存event契約を無制限に
増やさず、operatorが30時間の内訳を判断できる最小粒度にします。

通常運用への影響:

- reportとtiming codeは随時マージ可能。
- snapshot作成だけは通常writerとの調整が必要。

### P2.4: bounded real-source smoke

既存sourceを1 page / 1 query / 1 itemへ制限し、dry-runまたは複製DBで次を確認します。

- workerから外部sourceまでの通常接続。
- pagination、watermark、partial failure分類、cancel境界。
- 再実行時の差分。

通常unit testや常時network CIには混ぜません。credentialが必要なsource、upstream failureの扱い、実行頻度を
先に決めます。新規collectorの追加ではなく、接続済み経路の未検証範囲を狭める作業です。

### P2.5: Web診断・scheduler・安全境界

- request path、status、所要時間を、tokenやpayloadを含めずstderrへ残す。
- unexpected exceptionの調査に必要なserver-side tracebackを保持する。
- job stderrとHTTP request logの責務を分ける。
- scheduler起動失敗を握り潰して毎分再試行せず、既存の`last_status` / `last_message`へ記録する。
- POST bodyの読み取りを`mutation_lock`外へ出し、検証済みpayloadのmutationだけを直列化する。
- token比較をstdlibのconstant-time比較へ置き換える。
- 外部由来文字列を描画する`innerHTML`箇所を一度全数監査し、escape漏れをtestまたはDOM helperで防ぐ。
- 初回token URL、localStorage、plain HTTP、localhost外bindの現行制約を利用手順へ明記する。

公開access log基盤やremote telemetryは追加しません。TLS前段やtoken rotationは、localhost外のwrite運用を
正式に使う場合だけ判断します。

## Phase 3: 保存量を止めてから既存DBを掃除する

順序を固定します。

### P3.1: 新規増加を止める

- `service_verification_results.links` / `urls` / extractに件数とbyte budgetを設ける。
- current stateには判断に使うdigest、typed relation、代表例、件数だけを残す方向へ寄せる。
- `resource_url_records`の既存budgetが、すべての生成経路で適用されることを確認する。
- LLM prompt/bundle生成時にも入力上限を設け、保存budgetとprompt budgetを別にする。

### P3.2: cleanup dry-run

- resource URL、service verification、重複LLM evidenceを別categoryで報告する。
- project、role、host、path pattern、削除bytes、semantic field差分を出す。
- `/blog`、`/about`、`/specs`等をpath名だけで一律削除しない。

### P3.3: maintenance実行

- writer停止。
- backupとintegrity check。
- dry-run reportの人間承認。
- execute、foreign key/integrity/semantic audit。
- Web/worker再起動。

### P3.4: history表現の判断gate

cleanup後の増加率を測り、まだ必要な場合だけ次を比較します。

- bundleとcollector recordsの二重保存解消。
- input/output snapshotの差分化または片側廃止。
- no-change runのstub化。
- JSON圧縮。
- 日数だけでなくrun数・bytesを使うretention。
- merge済みaliasをproject別の最新保持件数でどう数えるかを明示し、偶然のalias単位保持に依存しない。
- history DBの別file化。

JSON圧縮は候補ですが採用済み仕様ではありません。複数方式を同時に実装せず、
効果とreplay契約を比較して最小の変更を選びます。schema v3やdual readerを先に作りません。

## Phase 4: 保守的matchingと長時間operation

### P4.1: Semble weak match

- 着手時点のcollection sync実装を取り込んだ後に再監査する。
- exact match以外を既存projectへ直接attachせず、他syncと同じsuggestion contractへ寄せる。
- 実データのdry-runで対象件数と誤attach例を確認する。

### P4.2: identifier lookup

- exact kind/value lookupはnormalized indexを使う。
- 採番だけの全project hydrateを既存allocatorへ置き換える。
- fuzzy/namespace/host-family matchはexact lookupと分け、意味を変えずに段階移動する。

実装監査で、過去schema v2 DBにはaccount metadataから派生する一部の`handle` / `did` normalized rowが
未投影と判明した。この2種をindexだけへ切り替えるのは意味変更になるため、source syncが使う
NSID/service/repository/domain系を先にindex化し、`handle` / `did`はP3.3の全normalized row再投影後に
scan fallbackを削除して本itemを終端する。

### P4.3: bulk enrich failure policy

未決定:

- fail-fastを維持するか、bulkだけcontinue-on-errorを既定にするか。
- checkpointのfailed projectをskipしてresumeするoptionだけを足すか。

実障害とoperatorの期待を確認して決めます。例外を無条件に握り潰しません。続行する場合も最終resultを
完全成功にせず、failed project一覧と再実行導線を返します。

cancel resultは、checkpointをclearする前の`current_project_key`を返すか、field自体を削除します。
常に空文字を返す互換性は維持しません。

決定: bulk enrichはproject単位でrollback・失敗記録して後続を既定で続行する。1件でも失敗したrunの
checkpoint/result/job statusとCLI終了コードはfailed/1とし、成功扱いしない。resumeはcompleted keyだけを
除外する既存契約によりfailed projectを再試行する。診断時だけ`--fail-fast`で最初の失敗後に止める。
単体enrichは続行せずstructured failed resultを返す。cancel resultの常に空だった`current_project_key`は削除する。

### P4.4: LLM failure / retry

- transport failure、endpoint incompatibility、content parse failureを分ける。
- JSON不良で同一の長いpromptを別payloadへ自動再送しない。
- serverが前requestを処理中の可能性があるtimeout後は、即時retryを避ける。
- command方式とendpoint方式でtimeout設定の意味を揃え、command方式だけの固定300秒を残さない。

LM Studioの約1,000秒stallは過去に動作確認済みですが、現在再発中とは確認されていません。
根本原因追跡ではなく、再発時のtiming/log取得へ留めます。

決定: auto styleのfallbackはHTTP 400/404/405/415/422/501でrequestがendpointに拒否された場合だけ行う。
timeout、transport failure、429/5xx、response envelope/content JSON不良では同じpromptを再送しない。
明示`chat`はcompletion endpointへfallbackしない。command/endpointは共通の
`ATPROTO_SURVEY_LLM_TIMEOUT`を1 attemptの上限として使い、metricsの分類をtimeout、transport failure、
endpoint incompatible、invalid response、rate limit、その他HTTP errorに分ける。

### P4.5: source設定の整理

- source URL、enabled、query、request budgetなどoperatorが変更する値だけをconfigへ置く。
- evidence抽出、identity/trust、classification、auto-attach条件はcodeとtestへ残す。
- worker内のintroduced既定queryをconfigまたは明示requestへ移し、CLIとworkerの二重管理をなくす。
- generic plugin frameworkや、判定ロジックを記述するconfig DSLは作らない。

決定: `config/sources.json`を通常導線のoperator設定正本とし、introduced query、source enabled、
collection/GitHub/Semble/TangledのURL・scope・request budgetをapplication serviceで解決する。CLI/Web/workerの
明示requestはそのrunだけoverrideする。builtin lexicon prefix、evidence抽出、matching、classification、
auto-attach条件はcode/testへ置き、configから削除する。source plugin APIやpolicy DSLは追加しない。

## Phase 5: 調査品質corpus

役割を分けます。

- operator correction fixture: 既知のdefer/block/confirmが再発しないことを検証するregression corpus。
- manual gold set: confirmed precision、merge/split、紹介可否、調査時間を人が採点する受入corpus。

最初の作業:

- **P5.1**: existing decisionsとbundleの相関を現行snapshotで再集計するread-only export report。
- **P5.2**: sanitize ruleとfixtureに含めない情報を決める。
- **P5.3**: 少数caseで実際に退行を検出できることを確認する。

過去snapshotのdecision/bundle相関数を現行値として使わず、現在のsnapshotで再集計します。自動生成した
current stateをmanual goldの代わりにはしません。50〜100件のgold setは将来目標で、一度に作り切りません。

## Phase 6: `registry.py`の段階分割

着手条件:

- Phase 1のhigh-impact bugが修正済み。
- Phase 3でservice verificationのcurrent projectionが決まっている。
- network/DB seamを守るcharacterization testがある。

順序:

1. **P6.1**: `registry-internals.md`の混入節を修正し、現行責務mapをnotesへ残す。
2. **P6.2**: 残存`cmd_*`とCLI出力処理を`commands/`へ移す。
3. **P6.3**: same-site/domain policyを独立moduleへ移す。
4. **P6.4**: service verification collectorとcurrent projectionを分離する。
5. **P6.5**: HTTP transportとLLM transportを正式なtest seamへする。
6. **P6.6**: URL/NSID/namingのpure helperを用途別moduleへ移す。
7. **P6.7**: source、project shape、sync、queue、enrich orchestrationを、変更頻度と依存方向に従って移す。

domain helper抽出時に、`tldextract`を正式依存とするかPSL fallbackを残すかを決め、同じpolicyの二重実装を
恒久的に維持しません。

各stepの条件:

- 挙動変更を含めない。
- 移動前後の全testと代表snapshotを比較する。
- call siteを同じstepで更新する。
- 旧symbolのre-exportを恒久的に残さない。
- 最大file行数だけを成功条件にせず、application/databaseが巨大moduleを介さない依存方向を目標にする。

このphaseはbig-bangにしませんが、通常運用側の小変更を理由に無期限延期もしません。高churnの関数へ
並行変更が入る場合だけ順序を入れ替え、低依存moduleの抽出を進めます。

## Phase 7: 文書closeout

**P7.1**として、実装後に責務別へ反映します。

- `docs/brief.md`: 目的、成功指標、非目標。
- `docs/status.md`: 実装済み、動作確認済み、未接続、未検証。
- `docs/roadmap.md`: 今回見送った採用候補と再検討条件。
- `docs/thread-handoff.md`: 通常運用側へ引き継ぐ必要がある未完了作業だけ。
- `docs/notes/`: baseline report、比較、migration記録、調査途中の仮説。
- maintenance用status/handoff: 通常運用へ反映した内容を正本文書へ移し、series完了時に削除する。

**P7.2**として、READMEと`project-registry.md`の重複、絶対path link、stale test数、`registry-internals.md`の節混入は、
仕様変更と混ぜず文書auditの独立commitで直します。

## 直近のmaintenance scopeに含めないもの

直近では実装しない:

- 新規collector、OAuth include recursion、Tangled community/bot接続。
- FTS、自然語検索、PWA、relation graph UI、LLM side panel。
- job event永続化、sync/review checkpoint再開。
- schema v3、ORM、Web framework、frontend build system。
- public SaaS相当のOAuth/RBAC/multi-user対応。
- service liveness signalやatproto利用modeの新規拡張。
- trust/merge条件の緩和。

必要性が確認された時だけ再検討:

- LAN/Tailscale write運用向けのTLS前段・token運用強化。
- DB-level data policy marker。
- history DB分離。
- 専用audit接続としてのDB-level read-only。Webの操作抑止modeは復活させない。

これらは候補として挙がったという理由だけで通常導線へ接続しません。

## 適用checkpoint

maintenance seriesを区切って通常運用へ適用できる地点:

1. **計画checkpoint**: 本計画、roadmap、maintenance用handoffが同期し、baseline commandが明確。
2. **correctness checkpoint**: Phase 1を独立commitで完了し、full testがgreen。
3. **measurement checkpoint**: current DB snapshotのbaselineと人間判断事項が文書化済み。
4. **storage checkpoint**: 新規増加を止めるcodeがmerge済み。正本DB cleanupは別maintenanceでもよい。
5. **refactor checkpoint**: 少なくともCLI残存処理と修正済みhotspotが恒久moduleへ移動済み。

いずれのcheckpointでも、未完了項目をroadmapまたはmaintenance用handoffへ移せば段階的に適用可能です。大きなphaseを
完了扱いにするためにcompatibility layerや暫定実装を残しません。
