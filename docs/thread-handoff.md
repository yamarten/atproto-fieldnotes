# Thread Handoff

## 現在の基準

- 実装済み: registry schema version 2
  - `projects`のscalar + `profile_json` + `state_json`
  - `project_identifiers`
  - `project_relations`
  - `project_decisions`
- 実装済み: `project-registry migrate-db OLD_DB_PATH`
  - 既定で移行・検証・backup・元path置換まで実行
  - `--dry-run`は一時DBを検証後に削除
  - v2 DBへの再実行はno-op
- 実装済み: 公開CLIは`bin/project-registry`へ一本化
- 削除済み: `scripts/project-registry`互換wrapper
- 削除済み: `scripts/project_registry.py` top-level Python API
- 削除済み: runtime compatibility field
  - `website_url_candidates`
  - `github_repo_urls`
  - `tangled_repo_urls`
  - `resource_urls`
  - `introduced`
- 実装済み: EvidenceBundleはschema v2のみを通常readerで受理
- 実装済み: `project_registry_core.application`
  - project query/tracking、decision、merge/split、review queue
  - sync、investigation replay/verify/export、maintenance
- 実装済み: 通常CLI adapterを`project_registry_core.commands`へ用途別分割
- 実装済み: enrich background job境界
  - `OperationRequest` / `OperationEvent` / `OperationResult` / `CancellationToken`
  - enrichのproject選択、保存、checkpoint、再開をapplication serviceへ移動
  - CLIはevent dataをJSON linesへ変換するadapter
- 実装済み: Web enrich subprocess runner
  - 同じWeb serverからの同時background jobは1件。CLIなど別processとの排他は未実装
  - 開始、一覧、詳細、処理中project後の停止、cancelled/failed bulk runの再開
  - Operations画面で設定、進捗、event/resultを表示
- 実装済み: schema v2 Web UI
  - `Review / Projects / Operations / Settings`
  - project検索、review/promotion/merge queue、seed preview・追加
  - normalized identifier/relation/decision、評価、最新investigation概要の表示
  - tracking、candidate confirm/reject、block/defer、split、separate解除、merge
- 削除済み: `--enrich-age-days` option alias。`--max-enrich-age-days`のみ使用する

## 動作確認済み

- temp SQLiteでv1→v2 migration、backup、dry-run、再実行
- identifier/relation/decisionのsave→fetch
- relation重複排除
- merge後のblock/defer保持と優先
- investigation replay / benchmark
- CLI helpと既存command parser
- Web API認証、static asset、v2 detail、normalized mutation、partial collector failure、SQLite busy error
- in-app Browserでdesktop検索・mutation・merge確認とmobile detail遷移
- temp SQLiteでenrichのproject境界中断、checkpoint再開、API key非保存
- fake subprocess workerで完了、競合拒否、SIGINT停止、再開
- 実workerと空のtemp DBでnetwork-free protocol実行
- in-app Browserでtemp DBのbulk enrich起動と完了event/result表示
- 全通常テスト184件
- in-app Browserでschema v2 DBのreview-stalled dry-runを実行
  - DB更新なし、全候補839件のうちlimit 50件をresultへ返すことを確認
  - GitHub sync選択時だけscreening optionが有効になることを確認
  - 実外部sync sourceのWeb worker経由実行は未検証
- 2026-06-14にミラー`data/survey.db`を実移行
  - project 2540件、identifier 18388件、decision 113件、relation 0件
  - SQLite integrity / foreign key / canonical snapshot検証済み
  - 旧DBは`data/survey.db.v1-backup-20260614-120431`
  - 移行後の再実行は`already_current`

## 未接続

- sync/reviewのbackground job接続は完了。source page/candidate単位progress、途中停止、checkpoint再開は未接続
- interactive reviewの候補選択式plan実行と`assist-db-edit`の共通job化
- server再起動後のjob一覧復元とcheckpoint discovery
- Tangled optional adapterの通常実行
- OAuth `include:` permission解決
- Webからのinvestigation run一覧、evidence全文、replay差分表示

## 互換性のためだけに残しているもの

- CLIの既存command名と主要option
- collector/evaluationが使うcanonical project dict field
- `service_url_candidate_values()`、`provider_repo_url_values()`、`resource_url_values()`などの導出helper

旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerは維持しない。

## 次候補

- syncのsource内部progress/cancellationとreviewの候補選択式planを追加する
- server再起動後も既存checkpointを発見・再開できる導線を追加する
- identifier tableを直接queryする検索・collision監査へ移し、全project hydrateを減らす
- service browser検証とidentity corroborationのcorpusを増やす

恒久仕様は[project-record.md](project-record.md)、調査契約は[investigation-model.md](investigation-model.md)、長時間処理契約は[background-jobs.md](background-jobs.md)、内部の具体値は[registry-internals.md](registry-internals.md)、将来課題は[roadmap.md](roadmap.md)を参照してください。
