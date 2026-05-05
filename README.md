# atproto survey automation

新しく生まれたatprotoプロジェクトを発見し、追跡するための作業用リポジトリです。

主対象は「新規lexiconを持つ独立プロジェクト」です。ライブラリやauth-onlyサービスは原則除外し、infraツールは別カテゴリで保持します。

## このリポジトリでやること

- 候補を収集する
  - `collection_count_view`
  - `applog.json`
- seedからproject recordを育てる
  - 事実収集
  - LLMによる要約と分類
  - 継続調査
- 紹介済みかどうか、継続調査対象かどうかを管理する

## 主要コマンド

seedを1件登録:

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://example.com
```

1件調査:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

全件調査:

```bash
python3 scripts/project_registry.py enrich --all
```

紹介済みURLの取り込み:

```bash
python3 scripts/project_registry.py import-applog applog.json --execute
```

既知collection一覧の取り込み:

```bash
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json" --execute --create-unmatched
```

DB直接編集後の整合性回復:

```bash
python3 scripts/project_registry.py repair-db
```

## LLMの最小設定

command方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
```

endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_TIMEOUT='180'
```

Bluesky post検索を使う場合:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
```

## 文書

利用者向け:

- [docs/project-registry.md](docs/project-registry.md)
- [docs/project-record.md](docs/project-record.md)
- [docs/manual-workflow.md](docs/manual-workflow.md)

運用・設計向け:

- [docs/automation-plan.md](docs/automation-plan.md)
- [docs/automation-architecture.md](docs/automation-architecture.md)
- [docs/registry-internals.md](docs/registry-internals.md)
- [docs/thread-handoff.md](docs/thread-handoff.md)

実装:

- [scripts/project_registry.py](scripts/project_registry.py)
- [scripts/discover.py](scripts/discover.py)
- [prompts/project-triage.md](prompts/project-triage.md)
