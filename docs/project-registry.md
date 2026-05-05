# Project Registry

手動で見つけたseedを登録し、project recordとして追跡するための操作ガイドです。

内部実装や昇格ロジックの詳細は [docs/registry-internals.md](registry-internals.md) に分けています。

## 扱うseed

- `nsid`
- `repo_url`
- `service_url`

次も `nsid` seed として受け付けます。

- `https://pdsls.dev/at://did.../collection[/rkey]`
- `https://pds.ls/at://did.../collection[/rkey]`
- `at://did.../collection[/rkey]`

## 基本フロー

1. seedを登録する
2. `enrich` で調査する
3. `show` で状態を見る
4. 必要なら `merge` や `set-tracking-state` を使う

## seed登録

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://github.com/example/project
python3 scripts/project_registry.py add-seed https://example.com
```

既存projectへ追加する場合:

```bash
python3 scripts/project_registry.py add-seed https://example.com --project-key 2f2acb
```

`project_key` は `proj:xxxxxxxxxxxx` 形式ですが、CLIでは `proj:` を省略した prefix でも指定できます。

## 調査

1件だけ:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
python3 scripts/project_registry.py enrich --project-key 2f2acb
```

全件:

```bash
python3 scripts/project_registry.py enrich --all
```

`stable` / `archived` / `ignore` も回したい場合:

```bash
python3 scripts/project_registry.py enrich --all --include-stable
python3 scripts/project_registry.py enrich --all --include-archived
python3 scripts/project_registry.py enrich --all --include-ignore
```

## 表示

```bash
python3 scripts/project_registry.py show --project-key 2f2acb
python3 scripts/project_registry.py show --seed sh.tangled.repo --type nsid
python3 scripts/project_registry.py show --all --format json
```

## 紹介済み候補の取り込み

`applog.json` から紹介済みURLを取り込む:

```bash
python3 scripts/project_registry.py import-applog applog.json
python3 scripts/project_registry.py import-applog applog.json --execute
python3 scripts/project_registry.py import-applog applog.json --execute --create-unmatched
```

## 既知collection一覧の取り込み

`collection_count_view` 由来JSONを取り込む:

```bash
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json"
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json" --execute --create-unmatched
```

このimportはproject粒度で集約して取り込みます。新規作成時は後段のlexicon解決用に代表のfull NSIDも保持します。

## merge

recommendationを見るだけ:

```bash
python3 scripts/project_registry.py merge \
  --project-key 2f2acb \
  --use-recommendation
```

実行:

```bash
python3 scripts/project_registry.py merge \
  --project-key 2f2acb \
  --use-recommendation \
  --execute
```

一括候補の確認:

```bash
python3 scripts/project_registry.py merge-batch
```

## tracking state

継続調査対象の切り替え:

```bash
python3 scripts/project_registry.py set-tracking-state \
  --project-key 2f2acb \
  --tracking-state stable \
  --note "安定運営"
```

## DBを直接編集した後

GUIやSQLで `projects` を直接編集した後は、正規化と派生データ再計算のためにこれを回します。

```bash
python3 scripts/project_registry.py repair-db
```

## LLM設定

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
