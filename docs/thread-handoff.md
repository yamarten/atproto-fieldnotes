# Thread Handoff

## 現在の基準

- 実装済み: registry schema version 2
  - `projects`のscalar + `profile_json` + `state_json`
  - `project_identifiers`
  - `project_relations`
  - `project_decisions`
- 実装済み: `scripts/project-registry migrate-db OLD_DB_PATH`
  - 既定で移行・検証・backup・元path置換まで実行
  - `--dry-run`は一時DBを検証後に削除
  - v2 DBへの再実行はno-op
- 実装済み: CLI launcherは`scripts/project-registry`
- 削除済み: `scripts/project_registry.py` top-level Python API
- 削除済み: runtime compatibility field
  - `website_url_candidates`
  - `github_repo_urls`
  - `tangled_repo_urls`
  - `resource_urls`
  - `introduced`
- 実装済み: EvidenceBundleはschema v2のみを通常readerで受理

## 動作確認済み

- temp SQLiteでv1→v2 migration、backup、dry-run、再実行
- identifier/relation/decisionのsave→fetch
- relation重複排除
- merge後のblock/defer保持と優先
- investigation replay / benchmark
- CLI helpと既存command parser
- Web entrypointがschema v2未接続を明示して終了すること
- 全通常テスト166件
- 2026-06-14にミラー`data/survey.db`を実移行
  - project 2540件、identifier 18388件、decision 113件、relation 0件
  - SQLite integrity / foreign key / canonical snapshot検証済み
  - 旧DBは`data/survey.db.v1-backup-20260614-120431`
  - 移行後の再実行は`already_current`

## 未接続

- Web UI全体。旧Web実装はschema v2 repositoryへ未接続で、entrypointは意図的に停止する
- command handlerの用途別分割。launcher、parser、`main()`は`project_registry_core.cli`へ分離済みだが、handler本体の多くは`project_registry_core.registry`に残る
- Tangled optional adapterの通常実行
- OAuth `include:` permission解決
- Webからのinvestigation run/evidence表示

## 互換性のためだけに残しているもの

- CLIの既存command名と主要option
- collector/evaluationが使うcanonical project dict field
- `service_url_candidate_values()`、`provider_repo_url_values()`、`resource_url_values()`などの導出helper

旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerは維持しない。

## 次候補

- Web UIをschema v2 repositoryへ接続する
- `project_registry_core.registry`からCLI command handlerを用途別moduleへ切り出す
- identifier tableを直接queryする検索・collision監査へ移し、全project hydrateを減らす
- service browser laneとidentity corroborationのcorpusを増やす

恒久仕様は[project-record.md](project-record.md)、調査契約は[investigation-model.md](investigation-model.md)、内部の具体値は[registry-internals.md](registry-internals.md)、将来課題は[roadmap.md](roadmap.md)を参照してください。
