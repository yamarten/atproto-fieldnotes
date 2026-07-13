# Project Registry Status

この文書は、通常導線で現在できること、既知の制約、直近の検証状況の正本です。
恒久的なデータ契約やheuristicの詳細は各仕様書、未着手の候補は
[roadmap.md](roadmap.md)、次スレッド固有の引き継ぎは
[thread-handoff.md](thread-handoff.md)を参照してください。

## 現在の通常導線

実装済み:

- schema version 2のSQLite registry
  - `projects`のscalar、`profile_json`、`state_json`
  - normalized `project_identifiers`、`project_relations`、`project_decisions`
  - `investigation_runs`、`collector_runs`の調査履歴
- 公開command入口
  - `bin/project-registry`
  - `bin/project-registry-web`
  - CLI/Web/test wrapperは現在checkoutのvenvを優先し、worktreeではGit共通checkoutのvenvも利用
- 内部実行境界
  - source sync本体は`source_sync.py`
  - 1 projectのenrich本体は`enrichment_pipeline.py`
  - interactive queue入出力は`commands/queue_interaction.py`
  - project state、保存、lookup、merge/queue policyは`registry.py`に残る
- Web UIの日常運用
  - normalized tableを直接使うproject検索と構造化filter
    - 自由入力検索は `project_key`、`name`、`website_url`、identifier value、relation subject/object/locator、decision target/note の大小文字を無視した部分一致
    - summary、tagline、raw evidence、README/HTML全文、自然語の意図や概念は検索対象外
  - identifier、relation、collector failure、developer accountのreview action
  - project nameとnotesのpreview付き編集
  - bounded investigation run viewerとread-only replay
    - 新規enrich runのphase別wall timeとLLM request size/statusを表示
  - service verification比較、ownership、OAuth、URL aliasの確認
  - 保存済みstateに対するExplain
  - source/targetの主要属性とidentifierを並べるmerge review
  - identifierのrole/classification対応を行単位で表示
  - background jobの経過時間、progress比から算出したETA、手動details更新
  - Git checkoutではSettingsにcommit hashとdirty状態を表示
- background operation
  - enrich、sync、reviewを別processで実行
  - operation別のprogressと協調停止
  - enrich checkpointの再発見と再開
  - bulk enrichはproject失敗をcheckpointへ残して後続を続行し、run全体をfailedとして失敗分をresume可能
  - review dry-run planの選択実行とfingerprint再検証
  - Operationsからallowlist済みjob presetを手動または定期発火
    - 通常`enrich --all`、全tracking state対象`enrich --all`後のstalled review一括反映、`sync introduced`、introduced以外のsync一括後`enrich --all --new-only`
    - presetごとに毎週または第N週の曜日、時刻、time zoneを設定可能
- external source設定
  - introduced/collection/GitHub/Semble/Tangledのenabled、URL、query、scope、request budgetは`config/sources.json`が正本
  - application serviceでCLI/Web/worker共通に解決し、matching/classification policyはcode/testに固定
- Web serverの手動reload
  - Settingsから同じ起動引数を保ち、package entrypointでprocessを再起動
  - active background jobへ協調停止を要求し、timeout後はworkerを終了してbrowserを再接続
- Web診断とlocal access境界
  - HTTP request logはquery/bodyを除いたmethod、path、status、所要時間だけをstderrへ記録
  - unexpected HTTP exceptionはserver側へtracebackを残し、job stderrとは分離
  - scheduled workerの起動失敗はqueueを終端し、presetの最終status/messageへ保存
  - Web execute syncの件数、bounded候補参照、成否、所要時間はsourceごと直近4回を`job-schedules.json`へ保存
  - POST JSON bodyはmutation lock外でparseし、mutation処理だけを直列化
  - API tokenはconstant-timeで比較
  - UIのHTML templateは外部文字列を共通escape/link/JSON helperへ通す
- project detail
  - 初期detailはmerge候補計算を待たずに表示
  - merge候補は後続requestで計算し、完了次第表示
  - 計算結果はDBへ保存しない
- atomic project mutationとmerge lineage
  - merge、split、separateの複数project書込みを単一transactionで実行
  - merge済みsource keyをcanonical projectへ解決
  - source keyのinvestigation runをtarget detailへ束ね、保存snapshotは維持
- 手動cleanup
  - investigation履歴は既定90日かつprojectごとに最新3 runを保持
- bounded service verification保存
  - current stateはsurfaceごとの最新結果を最大32件保持
  - HTML extract、links、URLsへUTF-8/JSON byte budgetと件数上限を適用
  - relation/evaluationは取得結果全体から先に計算し、保存payloadだけを縮約
- bounded investigation history保存
  - record/failure本文は`bundle_json`へ一度だけ保存
  - `collector_runs`はsource/operation別のstatus/count projectionで、本文列は新規runでは空配列
  - GitHub API snapshotを使用fieldへ縮約し、同一repoの一run内再収集をしない
- 既存保存payloadのmaintenance
  - `cleanup-storage-payloads`はdry-run既定で、project current、run snapshot、bundle、collector projectionを報告
  - `--execute`はrunを削除せず一transactionで縮約し、commit前にforeign key/integrity checkを行う
  - multi-tenant suffixをWebまたは`audit-source-config --execute`で追加すると、同じapplication serviceがcurrent same-site判定を再評価する
  - 保存済みservice evidenceをcurrent stateへ再適用する時は、requested/final URLからsame-site判定を現行policyで再計算する。investigation runの履歴payloadは変更しない
  - `profile_json.app.links`は保存時にcanonical identifierとconfirmed resourceから毎回再構築し、既存の派生linkを入力として引き継がない
  - `backfill-derived-identifiers`はflat runtime viewが失ったroleを不足rowと誤認するため削除。normalized identifierは無変更roundtripを維持する
  - exact identifier lookupは`handle` / `did`を含めnormalized indexを使用
  - 2026-07-11に正本DBへ適用済み。payload 3,064,663,886 bytes削減、identifier 5,681行補完、same-site stale probe 29件修復。identifier件数は当時の作業記録であり現行commandの契約ではない
  - 2026-07-13にVACUUM済み。DB fileは約4.4 GiBから約1.5 GiBへ縮小し、`freelist_count=0` / `integrity_check=ok`を確認済み
  - 2026-07-13にactive `evidence.resource_url_records`から旧形式由来の補助URL 247件 / 174 projectsを削除済み。過去LLM入力snapshot内の同source名は履歴として残す
  - 2026-07-13に正本DBのsame-site stale probe 4件を現行policyで更新し、`app.links`から正本にない4,868 link / 449 projectsを削除。再監査0件、foreign key違反0、integrity `ok`を確認後、一度きりCLI commandを削除
  - 2026-07-13に474 projectの旧machine note 1,071行を`system_notes`へ移動。runtime project同値、normalized child・履歴不変、再監査0件、foreign key違反0、integrity `ok`を確認し、読込時のpattern変換とcollector旧projection fallbackを削除
- bounded LLM evidence
  - planning/triageへ渡すevidence JSONは整形後UTF-8 24 KiB上限
  - field別件数上限で先頭・末尾の代表を残し、縮約時は観測件数と採用件数をprompt内へ明示
  - 最終prompt bytesはrun metricsで別に観測
- replay regression corpus
  - 通常replay 6件とoperator correctionのblock/defer/confirm 3件をnetworkなしで評価
  - fixture exportはsanitizer v2を通し、commit前のmanual縮約を必須とする
  - manual gold setはoperator reviewから固定した11件で、identity / service / repositoryの期待actionを実project key・時刻・期限なしで保持
- LLM transport failure境界
  - command/endpointは共通の1 attempt timeoutを使用
  - endpoint互換性エラーだけ別payload/endpointを試し、timeout・transport・429/5xx・invalid responseは再送しない

利用手順は[project-registry.md](project-registry.md)、保存契約は
[project-record.md](project-record.md)、調査契約は
[investigation-model.md](investigation-model.md)、background operation契約は
[background-jobs.md](background-jobs.md)を正本とします。

## 既知の制約

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
  - enrichの未完了checkpoint再発見は接続済み
- CLIなど別processを含むwriter排他
- schema v2以降のincremental migration
  - 次世代schemaへ更新するrunnerは未実装
- DB-level trust/data policy marker
  - `registry_meta`はschema versionしか持たない
  - 正本DBが単一のため、直近の実装優先から外した
  - 将来必要になった場合は、audit合格後のadditive updateとして後付けする
- Tangled timeline/community search/bot adapterの通常実行
- Tangled repoの完全なtree取得。現状は公開HTMLとREADME raw取得に限定
- OAuth `include:` permission setの再帰解決

現行設計の非目標:

- raw HTMLやREADME全文のWeb表示
- job eventのSQLite永続化
- collector request途中の強制停止
- 旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerの互換維持

安全上の制約:

- identifierのconfirm/block/defer/split decisionは現行保存契約どおり`kind + value`全体へ作用し、role単位ではない
- job ETAは直近の`current / total`を開始時からの平均速度へ線形外挿した目安であり、progress総数が無いoperationでは表示しない
- enrich run timingはproject SQL書込みまでで、transaction commit、process job全体、operator review時間を含まない

## 動作確認状況

2026-07-13時点で確認済み:

- 全unit test 395件
- JavaScript syntax check、Web API test、主要なlist/detail/job導線の実browser確認
- investigation replay benchmark 9 case、semantic regression 0件
- GitHub、Semble、Bluesky introduced、collection inventory、Tangledの
  [bounded real-source smoke](notes/registry-maintenance-real-source-smoke-2026-07-11.md)
- 正本DB maintenance後のforeign key違反0、integrity `ok`
- 2026-07-13の正本DB複製でsame-site 4件と`app.links` 4,868件を適用後、再監査0件、normalized roundtrip差分0、履歴table不変、foreign key違反0、integrity `ok`
- 3,239 projectから作ったhandle/DIDを含む12,849 exact lookupで、従来判定との差分0
- 正本DBでKlearsky、Postgame、radio4000の重複project 3組をmergeし、confirmed canonical identifierのcollision 0件、foreign key違反0、integrity `ok`を確認

未検証:

- 長時間運用時の実API retry/cache/budget値
- cleanup後30日または新規1,000 runs時点の保存量増加率

この節は網羅的なテスト履歴ではなく、現在の信頼境界を示すsnapshotです。個々の過去の
実行結果はGit履歴と関連notesに残します。
