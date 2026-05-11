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

## 環境準備

隔離した Python 環境を使う前提にします。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`tldextract` を使って PSL 判定を行います。入力は repo 内の [vendor/public_suffix_list.dat](vendor/public_suffix_list.dat) に固定し、ライブラリの cache は [data/tldextract-cache](data/tldextract-cache) 配下に置きます。外部の絶対パスには依存しません。

PSL に無い hosted suffix を手動で足したい場合は、[config/sources.json](config/sources.json) の `custom_multi_tenant_suffixes` に追加します。ここに `example.com` を入れると、`foo.example.com` と `bar.example.com` を別 service family として扱います。

seedを1件登録:

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://tangled.sh/@example/app/tree/main/lexdocs/example
python3 scripts/project_registry.py add-seed https://example.com
```

`add-seed` は `tangled.sh` や GitHub の `blob/tree` URL を repo root に正規化します。`https://lexicon.garden/lexicon/.../collection.nsid` や `pdsls` の `at://.../collection` URL も `nsid` seed として受け付けます。

1件調査:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

全件調査:

```bash
python3 scripts/project_registry.py enrich --all
python3 scripts/project_registry.py enrich --all --new-only
```

`stable` や、立ち消え気味で低頻度追跡に落とした `stalled` も回したい場合:

```bash
python3 scripts/project_registry.py enrich --all --include-stable
python3 scripts/project_registry.py enrich --all --include-stalled
```

紹介済みURLの取り込み:

```bash
python3 scripts/project_registry.py import-applog applog.json --execute
```

Bluesky 検索から紹介済み判定を同期:

```bash
python3 scripts/project_registry.py sync-introduced
python3 scripts/project_registry.py sync-introduced --execute
```

`sync-introduced` は seed を追加せず、既存 project に対して `introduced=true` と `introduced_url` を付けるためだけに使います。

既知collection一覧の取り込み:

```bash
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json" --execute --create-unmatched
```

collection inventory APIから直接同期:

```bash
python3 scripts/project_registry.py sync-collections
python3 scripts/project_registry.py sync-collections --execute --create-unmatched
python3 scripts/project_registry.py sync-collections --execute --create-unmatched --lookback-days 10
```

`sync-collections` は API 全件を取得しますが、取り込み対象は既知collectionを除いた「`min` が直近 `--lookback-days` 日以内」のものだけです。

GitHub repository search から repo seed を同期:

```bash
python3 scripts/project_registry.py sync-github-search
python3 scripts/project_registry.py sync-github-search --execute --create-unmatched
```

`sync-github-search` は `config/sources.json` の `github_repo_search` を使い、既知 repo を除いた「`updated_at` が直近 `--lookback-days` 日以内」の未知 repo だけを対象にします。

1回だけ広く取りたい時は、まず dry-run で lookback を外し、未マッチ repo に対して README/tree/lexicon を見たスクリーニングをかけます。

```bash
python3 scripts/project_registry.py sync-github-search \
  --all-history \
  --create-unmatched \
  --screen-unmatched \
  --pages 10 \
  --per-page 100
```

問題なければ `--execute` を付けます。`--screen-unmatched` は atproto 関連シグナルが無い repo を DB に作らず、その場で `screened_out_entries` に落とします。

手元メモのような見出し付きテキストからまとめて seed を取り込む:

```bash
python3 scripts/project_registry.py import-seed-list tmpdata.txt
python3 scripts/project_registry.py import-seed-list tmpdata.txt --execute
```

`import-seed-list` は GitHub/Tangled/service URL/`pdsls`/`lexicon.garden` を seed に正規化します。`pdsls` の record URL からは collection NSID を拾い、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。

DB直接編集後の整合性回復:

```bash
python3 scripts/project_registry.py repair-db
```

誤って付いた `service_url` / `repo_url` / `nsid` を今後も戻さないようにしたい場合:

```bash
python3 scripts/project_registry.py block-identifier --project-key proj:xxxx --identifier-type service_url --value https://example.com
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
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
```

Bluesky post検索を使う場合:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
```

GitHub検索の rate limit を避けたい場合:

```bash
export GITHUB_TOKEN='ghp_...'
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
