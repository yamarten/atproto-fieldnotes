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
- Web UIの日常運用
  - normalized tableを直接使うproject検索と構造化filter
  - identifier、relation、collector failure、developer accountのreview action
  - project nameとnotesのpreview付き編集
  - bounded investigation run viewerとread-only replay
  - service verification比較、ownership、OAuth、URL aliasの確認
  - 保存済みstateに対するExplain
- background operation
  - enrich、sync、reviewを別processで実行
  - operation別のprogressと協調停止
  - enrich checkpointの再発見と再開
  - review dry-run planの選択実行とfingerprint再検証
- project detail
  - 初期detailはmerge候補計算を待たずに表示
  - merge候補は後続requestで計算し、完了次第表示
  - 計算結果はDBへ保存しない
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
- 複数project mutationのtransaction ownership
  - `save_project()`が内部commitするため、merge、split、separate更新は全体を単一transactionとしてrollbackできない
- merge後のproject lineage
  - source project rowは削除されるが、sourceの`investigation_runs.project_key`と他projectからの参照はtargetへ付け替えない
  - investigation row自体は削除されないが、target projectの通常detailからは参照できない
- schema v2以降のincremental migration
  - 現状の`migrate-db`はv1からv2だけを扱い、既存v2 DBを次世代schemaへ更新するrunnerはない
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

## 動作確認状況

2026-06-18から2026-06-19の変更時点で確認済み:

- unit test 210件
- JavaScript syntax check
- temp SQLiteでmigration、normalized row roundtrip、review、profile編集、履歴cleanup
- fake workerと実workerによるbackground operation protocol
- in-app Browserによるdesktop/mobileの主要Web導線
- 2540 projectの通常DBでdetail初期表示と非同期merge解析
  - 対象projectの初期detailは変更前6.13秒、変更後0.002秒
  - merge解析は後続requestで1.21秒

未検証:

- 本番稼働中のv1 DBに対するdry-runと通常運用DBのoffline置換
- Web workerから実外部sync sourceへ接続するend-to-end実行
- 長時間運用時の実API retry/cache/budget値

この節は網羅的なテスト履歴ではなく、現在の信頼境界を示すsnapshotです。個々の過去の
実行結果はGit履歴と関連notesに残します。
