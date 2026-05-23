# atproto survey automation

新しく生まれたatprotoプロジェクトを発見し、追跡するための作業用リポジトリです。

主対象は「新規lexiconを持つ独立プロジェクト」です。ライブラリやauth-onlyサービスは原則除外し、infraツールは別カテゴリで保持します。

## このリポジトリでやること

- 候補を収集する
  - `collection_count_view`
  - Bluesky 投稿検索
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

## zsh補完

wrapper の [bin/project-registry](bin/project-registry) と、zsh completion の [completions/_project-registry](completions/_project-registry) を追加しています。

普段 `project-registry` で呼びたい場合:

```bash
export PATH="$PWD/bin:$PATH"
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

これで:

```bash
project-registry <TAB>
project-registry enrich --llm-api-style <TAB>
```

のように subcommand と主要オプションを補完できます。

`scripts/` をそのまま `PATH` に入れると Python ファイルまでコマンド候補に出るので、`PATH` には `bin/` だけを足す前提にしています。`python3 scripts/project_registry.py ...` をそのまま使う場合でも、completion 定義には `project_registry.py` を紐付けているので、zsh 上では同じ候補が出ます。

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

Bluesky 検索から紹介済み判定を同期:

```bash
python3 scripts/project_registry.py sync-introduced
python3 scripts/project_registry.py sync-introduced --execute
```

`sync-introduced` は seed を追加せず、既存 project に対して `introduced_url` を付けるためだけに使います。

collection inventory APIから直接同期:

```bash
python3 scripts/project_registry.py sync-collections
python3 scripts/project_registry.py sync-collections --execute --create-unmatched
python3 scripts/project_registry.py sync-collections --execute --create-unmatched --lookback-days 10
```

`sync-collections` は API 全件を取得しますが、取り込み対象は既知collectionを除いた「`min` が直近 `--lookback-days` 日以内」のものだけです。
既知 collection についても、`count` / `recent_count` / `max` は activity refresh 用に見直します。これにより `latest_collection_activity_at` は「最後に新規 collection が生えた日」ではなく、既知 collection を含めた最近の record 活動に近い値になります。`tracking_state=stalled` の project でも、ここで最近の活動が見えれば `active` に戻せます。

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

追跡頻度を見直す候補の確認:

```bash
python3 scripts/project_registry.py review-stalled
python3 scripts/project_registry.py review-stalled --inactivity-days 60 --enrich-age-days 45
```

`review-stalled` は `active/watchlist` を `stalled` に落とす候補だけでなく、最近 activity が戻った `stalled` を `active` に戻す候補や、collection だけ動いているものを `stable` に落とす候補も出します。repo 更新日時と collection activity の両方を見ていて、どちらか一方でも最近動いていれば `active` のまま残します。

次に紹介する候補の確認:

```bash
python3 scripts/project_registry.py promotion-queue
python3 scripts/project_registry.py promotion-queue --kinds service_ready_for_introduction
python3 scripts/project_registry.py promotion-queue --include-personal-tools
python3 scripts/project_registry.py promotion-queue --kinds lexicon_ready_for_introduction --interactive
```

`promotion-queue` は、未紹介 (`introduced_url` なし) で

- 利用可能なサービス (`live` / `beta` / `invite_only`)
- 共有準備済みと見なせる `lexicon` project

を別 signal で一覧化します。`personal_tool` は既定では除外し、`--include-personal-tools` を付けた時だけ含めます。

誤って付いた `service_url` / `repo_url` / `nsid` を今後も戻さないようにしたい場合:

```bash
python3 scripts/project_registry.py block-identifier --project-key proj:xxxx --identifier-type service_url --value https://example.com
```

曖昧な自然文から DB 修正コマンド案を出したい場合:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい'
python3 scripts/project_registry.py assist-db-edit 'proj:aaaa と proj:bbbb をマージしたい'
```

一意に解決できた時だけ、そのまま反映することもできます:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい' --execute
```

TTY 上で一意に解決できた場合は、`--execute` なしでもその場で確認プロンプトを出します。ローカル LLM が設定されていればそれで解釈し、未設定でも `proj:` / URL / NSID が明示されているケースは heuristic である程度動きます。

## 環境変数

実装上の環境変数は次で全部です。

command方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
```

endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_API_KEY='...'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
export ATPROTO_SURVEY_LLM_REASONING_EFFORT='none'
```

Bluesky post検索を使う場合:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
```

GitHub検索の rate limit を避けたい場合:

```bash
export GITHUB_TOKEN='ghp_...'
```

`DNS_RESOLVER_URL` や `UFOS_API` などは現状コード定数で、環境変数では上書きしません。

`thinking` / reasoning effort を切り替えたい場合は `ATPROTO_SURVEY_LLM_REASONING_EFFORT`、または CLI の `--llm-reasoning-effort` を使います。例えば:

```bash
python3 scripts/project_registry.py enrich --all --llm-reasoning-effort none
python3 scripts/project_registry.py enrich --all --new-only --llm-reasoning-effort high
```

## 保存形式

`projects` テーブルは現在、次を保持します。

- scalar columns
  - 一覧や絞り込みで多用する値
- `record_json`
  - 高信頼の project record
- `ops_json`
  - 候補、調査、LLM 出力、merge 制御、activity などの運用情報

`data_json` はもう読み書きしません。移行完了後は schema からも外しています。`record_json` は「確信のあるものだけ」を入れ、判断が難しい URL / name / NSID は原則 `ops_json` の candidate 側へ退避します。

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
