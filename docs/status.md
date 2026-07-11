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
  - `backfill-derived-identifiers`は保存済みidentifierを保持し、current payloadから導出できる不足行だけを補完
  - `repair-same-site-evidence`はcurrent probeのredirect same-site派生判定だけを現行domain policyで修復
  - exact identifier lookupは`handle` / `did`を含めnormalized indexを使用
  - 2026-07-11に正本DBへ適用済み。payload 3,064,663,886 bytes削減、identifier 5,681行補完、same-site stale probe 29件修復
  - VACUUMは未実施で、解放pageは後続writeが再利用する
- bounded LLM evidence
  - planning/triageへ渡すevidence JSONは整形後UTF-8 24 KiB上限
  - field別件数上限で先頭・末尾の代表を残し、縮約時は観測件数と採用件数をprompt内へ明示
  - 最終prompt bytesはrun metricsで別に観測
- replay regression corpus
  - 通常replay 6件とoperator correctionのblock/defer/confirm 3件をnetworkなしで評価
  - fixture exportはsanitizer v2を通し、commit前のmanual縮約を必須とする
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
- `assist-db-edit`の共通background job化
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

2026-06-18から2026-06-20の変更時点で確認済み:

- unit test 224件
- JavaScript syntax check
- temp SQLiteでmigration、normalized row roundtrip、review、profile編集、履歴cleanup
- fake workerと実workerによるbackground operation protocol
- in-app Browserによるdesktop/mobileの主要Web導線
- 2540 projectの通常DBでdetail初期表示と非同期merge解析
  - 対象projectの初期detailは変更前6.13秒、変更後0.002秒
  - merge解析は後続requestで1.21秒
- 2026-06-18の通常DB migration report
  - project 2575件、identifier 16554件、decision 116件、relation 0件
  - integrity `ok`、block衝突、根拠不明legacy confirmed、normalized roundtrip diffはいずれも0
- 2026-06-21の移行後通常DB read-only audit
  - integrity `ok`、foreign key違反0件
  - 当時のv1由来identifier 14950件は全件`v1-quarantine-1`
  - 根拠不明legacy confirmed、active block衝突、policy欠落はいずれも0
- 2026-07-01のmigration provenance cleanup
  - `project_identifiers` 14022行の移行専用metadataを削除
  - 削除後のv1 migration metadata件数0、SQLite integrity `ok`、foreign key違反0件
- 2026-07-01のresource URL record cleanup
  - active `projects.state_json` の `resource_url_records[].confidence = "legacy"` を `candidate` へ吸収
  - 変換後のactive `confidence = "legacy"` 件数0、SQLite integrity `ok`
  - investigation history側の過去bundleは履歴として残している
- 2026-07-02のresource URL過収集guard
  - 同一siteリンクでも、repo候補またはdocs/blog/support/status/privacy/terms等の補助ページroleを判定できないURLは `resource_url_records` に保存しない
  - `resource_url_records` はURL+role単位へmergeし、project全体最大32件、role別、host+role別のbudgetで代表選抜する
  - 対象unit test `ProjectRegistryResourceUrlRecordTest` は23件OK、`test_project_registry_core.py` 全体は113件OK
  - active DBでは `resource_url_records` 79,505件中 `role="unknown"` が62,625件あり、最大caseはLongformの13,947件であることをread-only確認済み
  - 作業中の別process更新とみられる変化により、後続snapshotでは79,509件 / unknown 62,626件も観測した
  - 後続snapshotに現行の正規化budgetをread-only適用した概算では、`resource_url_records` は4,613件相当
  - read-only dry-runでは、同一siteかつ補助ページrole不明の候補は56,864件。ただし `/blog`、`/about`、`/specs` など残すべき可能性のあるURLも混ざるため、削除対象としては未確定
  - 既存DB cleanupは未適用。2026-07-10 clone全3,239 projectへの現行prompt evidence投影は最大24,489 bytes、
    count/byte budgetが発動するprojectは41件
- 2026-06-25の判定feedback対応
  - service follow-up/deep page URL、service URL guard、service support relation、repo/NSIDからauthority domainへの片方向根拠、repo内lexicon ownership、README URL抽出のtargeted unit test
  - 補助ページservice URLの既存DB cleanupを`repair-db --execute`で適用済み
  - 適用前backup `data/survey.db.backup-20260625-judgement-feedback` は2026-06-30に削除済み
  - `proj:bbd470871ccc`の`https://leaflet.pub`は片方向根拠だけでconfirmed service URLになっていたため、`defer-identifier`でdefer済み
  - 追加backup `data/survey.db.backup-20260625-one-way-service-trust` は2026-06-30に削除済み
  - `defines_schema`はaccepted repoとaccepted NSIDが揃う場合だけ保存し、repo内lexicon JSONだけの既存relation 444件を`mentions_collection`へdowngrade済み
  - 追加backup `data/survey.db.backup-20260625-defines-schema-policy` は2026-06-30に削除済み
  - service pageのNSID直接言及だけではconfirmed service URLへ昇格しない
  - 旧NSID直接言及ruleだけでconfirmedになった可能性があるservice URLを既存DBで確認済み
    - 2026-06-26 read-only preliminary count: confirmed service URL pairは652、当時supported判定の`operates_service`を持つものは544、そのうちmanual seedではない上限候補は56
    - 保存済みprobe本文をprojectのNSIDと照合した直接NSID言及候補3件はuser確認済み。`atplan`と`Tree Appreciation`はOK、`atjam`のround個別ページはproject紐付け対象として外した
    - 2026-06-27: 候補projectを順次enrichし、35/36 projectは完了。先に`.envrc`なしで実行した3件は`.envrc`ありで再実行し、`last_run_errors`が空になることを確認済み
    - `proj:3b0767e1cad9`は通常enrichが240秒timeout、個別再実行も600秒timeout
    - 最終user review: `https://granary-demo.appspot.com`は正しいが不要、`https://granary.readthedocs.io`はGitHub側linkで確認済み。旧NSID直接言及rule由来の追加manual review対象は残っていない
  - `.venv/bin/python`で全unit test 245件

未検証:

- Web workerから実外部sync sourceへ接続するend-to-end実行
- 長時間運用時の実API retry/cache/budget値
- `proj:29a36771674f`など、複数projectを跨いで残るidentifier collisionの個別分離・merge判断

この節は網羅的なテスト履歴ではなく、現在の信頼境界を示すsnapshotです。個々の過去の
実行結果はGit履歴と関連notesに残します。
