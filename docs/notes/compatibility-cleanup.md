# Compatibility / Migration Cleanup Notes

このメモは、互換性、移行、暫定heuristicのために残っているコードを棚卸しする作業用メモです。
現行仕様の正本ではありません。通常導線の実装状況は `docs/status.md`、未完了候補は
`docs/roadmap.md` を参照してください。

## 現在の見立て

実装済み:

- 旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 reader の通常互換は削除済み
- v1 DBからschema v2へ移行する `migrate-db` は残っている
- v1 migration provenance metadataを削る `prune-migration-provenance` は残っている
- removed compatibility field が保存JSONやWeb payloadへ漏れない guard は残っている
- `bootstrap_collection_creator_heuristic` は明示指定時だけ使う暫定merge補助として残っている

未検証:

- 通常DBと手元backup以外に、今後 `migrate-db` で読みたいv1 DBが残っているか
- v1 migration provenanceを削除した後も、過去のmigration判断をDB単体で追う必要があるか
- removed compatibility field guardを外しても、古いfieldが再混入しないことを十分にtestで担保できるか

## 削除候補

### v1 DB migration code

対象:

- `scripts/project_registry_core/database.py`
  - `legacy_flatten`
  - `legacy_raw_project_payload`
  - `legacy_project_payload`
  - `decode_legacy_project`
  - `migrate_legacy_project_snapshot`
  - `migrate_legacy_bundle`
  - `migrate_investigation_rows`
  - `validate_migrated_projects`
  - `migration_acceptance_report`
  - `merge_migration_reports`
  - `migrate_database` の v1 migration path
- `scripts/project_registry_core/application/maintenance.py`
  - `migrate_database`
- CLI `migrate-db`
- v1 migration向けtest fixture / tests

現状:

- 通常DBは2026-06-18にoffline migration済み
- 現行通常DBの正本運用だけなら、v1 migration pathは通常導線では不要

削除条件:

- 手元に再移行したいv1 DBがない、またはv1 backupを履歴としてだけ保持すると決める
- 今後のmigrationはv2以降のincremental migration runnerで扱う、と決める

注意:

- `migrate_database()` は既存v2 DBに対して `already_current` validationも返すため、削除する場合はこの確認導線をどう置き換えるか決める

### v1 migration provenance cleanup

対象:

- `prune-migration-provenance`
- `MIGRATION_PROVENANCE_KEYS`
- 関連test

現状:

- v1 migration由来metadataは通常運用では増えない固定量
- `prune-migration-provenance --execute` でidentifier rowの意味を残したまま移行専用metadataだけ削れる

削除条件:

- cleanupを実行済み、またはmigration provenanceを今後も保持すると決める
- その後、再度v1 migration metadataを扱う必要がない

### removed compatibility field guard

対象:

- `runtime_view.REMOVED_COMPATIBILITY_FIELDS`
- `application.presentation.REMOVED_COMPATIBILITY_FIELDS`
- `runtime_view.strip_runtime_fields()` / `sanitize_runtime_snapshot()` 内の旧field除去
- `database.validate_database()` の旧field漏れ検査
- 関連test

現状:

- 旧fieldは通常保存schemaでは非目標
- guardは互換レイヤではなく、旧field再混入を止めるinvariantとして機能している

削除条件:

- 旧field再混入のリスクを十分に低いと判断する
- 削除する場合でも、少なくとも保存JSONとWeb payloadの漏れ検査は別のinvariantとして残すか決める

### `include_legacy_view` argument

対象:

- `registry.resource_url_record_values(..., include_legacy_view=True)`
- `registry.resource_url_values(..., include_legacy_view=True)`

現状:

- 引数は残っているが、現在の実装では分岐に使われていない
- 互換APIとして積極的に維持する理由は薄い

削除条件:

- 呼び出し元に `include_legacy_view=` 指定がないことを確認する
- 外部Python API互換を非目標とする方針を維持する

### bootstrap collection creator heuristic

対象:

- `--bootstrap-collection-creator-heuristic`
- `bootstrap_collection_creator_heuristic` 引数群
- `bootstrap_first_record_creator_dids` reason

現状:

- `listReposByCollection` の先頭DIDを「最初のrecord作成者」とみなす暫定heuristic
- 通常Web merge reviewの主判定には混ぜない
- Webでは明示操作時だけ補足表示、CLIでは明示flag時だけ判定へ含める

削除条件:

- 起動時のmerge整理が終わり、このheuristicが通常運用で不要になった
- または、より堅いcreator/ownership evidenceへ置き換わった

## 削除候補ではないもの

### `legacy_collectors.py` / `apply_legacy_evidence`

名前はlegacyだが、現行のcollector結果をEvidenceBundleとして既存project mutationへ適用するadapterとして使われている。
通常enrich、service verification、resource page patchなどの現役導線から呼ばれるため、単純な削除候補ではない。

将来整理するなら、名前を変えるか、collector application serviceへ寄せるrefactorとして扱う。

### `project_key_aliases`

merge後の旧project keyをcanonical projectへ解決する現行機能です。
旧仕様互換だけのためではなく、merge lineageと入力復元に使うため削除候補ではありません。

### OpenAI-compatible endpoint の `/v1/...` path処理

LLM endpoint adapterの通常機能です。
DB migrationや旧schema互換ではありません。
