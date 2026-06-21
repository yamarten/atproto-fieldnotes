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
- v1 DBからv2 DBへの保守的migration
  - 移行、検証、backup、元path置換
  - 由来不明のlegacy confirmed identifierをcandidateへ降格
  - operator確認済みidentifierとactive decisionを維持
- 通常DBのschema v2移行
  - 2026-06-18にoffline migrationを実行し、v1 backupと`data/migrate-log.json`を保持
  - migration時のhard acceptance condition、SQLite integrity、normalized roundtripは合格
- Web UIの日常運用
  - normalized tableを直接使うproject検索と構造化filter
  - identifier、relation、collector failure、developer accountのreview action
  - project nameとnotesのpreview付き編集
  - bounded investigation run viewerとread-only replay
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
  - review dry-run planの選択実行とfingerprint再検証
- Web serverの手動reload
  - Settingsから同じ起動引数を保ち、package entrypointでprocessを再起動
  - active background jobへ協調停止を要求し、timeout後はworkerを終了してbrowserを再接続
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
  - v1 migration専用provenance metadataを意味列を残して削除可能

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
  - 現状の`migrate-db`はv1からv2だけを扱い、既存v2 DBを次世代schemaへ更新するrunnerはない
- DB-level trust/data policy marker
  - 現行通常DBのv1由来identifierは`v1-quarantine-1` metadataを持つが、`registry_meta`はschema versionしか持たない
  - markerは再移行せず、audit合格後のadditive updateとして後付けする
- `assist-db-edit`の共通background job化
- Tangled timeline/community search/bot adapterの通常実行
- OAuth `include:` permission setの再帰解決

現行設計の非目標:

- raw HTMLやREADME全文のWeb表示
- job eventのSQLite永続化
- collector request途中の強制停止
- 旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerの互換維持

安全上の制約:

- Webの`--read-only`はPOSTとjob controlを拒否するapplication-level modeであり、SQLiteをOS/URI levelのread-onlyでは開かない
- identifierのconfirm/block/defer/split decisionは現行保存契約どおり`kind + value`全体へ作用し、role単位ではない
- job ETAは直近の`current / total`を開始時からの平均速度へ線形外挿した目安であり、progress総数が無いoperationでは表示しない

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
  - v1由来identifier 14950件は全件`v1-quarantine-1`
  - 根拠不明legacy confirmed、active block衝突、policy欠落はいずれも0

未検証:

- Web workerから実外部sync sourceへ接続するend-to-end実行
- 長時間運用時の実API retry/cache/budget値

この節は網羅的なテスト履歴ではなく、現在の信頼境界を示すsnapshotです。個々の過去の
実行結果はGit履歴と関連notesに残します。
