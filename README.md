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

## 環境準備

隔離した Python 環境を使う前提にします。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`tldextract` を使って PSL 判定を行います。入力は repo 内の [vendor/public_suffix_list.dat](vendor/public_suffix_list.dat) に固定し、ライブラリの cache は [data/tldextract-cache](data/tldextract-cache) 配下に置きます。外部の絶対パスには依存しません。

PSL に無い hosted suffix を手動で足したい場合は、[config/domain-overrides.json](config/domain-overrides.json) の `custom_multi_tenant_suffixes` に追加します。ここに `example.com` を入れると、`foo.example.com` と `bar.example.com` を別 service family として扱います。discovery source の設定は [config/sources.json](config/sources.json) に分離しています。

登録済み project への影響も確認する場合は、監査コマンドから追加できます。

```bash
scripts/project-registry audit-source-config example.com
```

既定は interactive です。追加確認後に `domain-overrides.json` を更新し、新しい family 基準で同一 project に複数 tenant が混在していないかを表示します。tenant group の番号を選んだ場合だけ、その group の identifier をまとめて新 project へ切り出します。split 後も元 project に複数 tenant が残る場合は、再計算した同 project の suggestion を続けて表示します。JSON dry-run だけを見る場合は `--no-interactive`、確認なしで suffix 追記まで行う場合は `--execute --no-interactive` を使います。non-interactive では split を適用しません。

## テスト

テストは標準ライブラリの `unittest` で実行します。外部 API、実 DB、実 LLM endpoint には触れず、必要な SQLite DB は temp directory に作ります。

```bash
python3 -m unittest discover -s tests -v
```

## zsh補完

CLI用のwrapperとzsh completionを追加しています。Web wrapperはschema v2未接続を明示するためだけに残しています。

- [bin/project-registry](bin/project-registry) / [completions/_project-registry](completions/_project-registry)
- [bin/project-registry-web](bin/project-registry-web) / [completions/_project-registry-web](completions/_project-registry-web)

wrapper で呼ぶ場合:

```bash
export PATH="$PWD/bin:$PATH"
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

これで:

```bash
project-registry <TAB>
project-registry enrich --llm-api-style <TAB>
project-registry-web --<TAB>
project-registry-web --host <TAB>
```

のように subcommand と主要オプションを補完できます。

`scripts/` をそのまま `PATH` に入れると Python ファイルまでコマンド候補に出るので、`PATH` には `bin/` だけを足す前提にしています。Python ファイルの直接実行は引き続き可能ですが、ここで説明する補完導線は `project-registry` / `project-registry-web` wrapper を対象にします。

## Web UI

Web UIはregistry schema v2へ未接続です。`project-registry-web`および`scripts/project_registry_web.py`は、旧schemaを誤って更新しないよう明示的なエラーで終了します。Web再接続は後続作業です。

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
scripts/project-registry enrich --all --llm-reasoning-effort none
scripts/project-registry enrich --all --new-only --llm-reasoning-effort high
```

## 保存形式

現行DBはregistry schema version 2です。`projects`はscalar、`profile_json`、`state_json`を保持し、identifier、relation evidence、operator decisionは専用tableへ保存します。詳細は [docs/project-record.md](docs/project-record.md) を参照してください。

旧DBは次で移行します。

```bash
scripts/project-registry migrate-db data/survey.db
```

## 文書

利用者向け:

- [docs/project-registry.md](docs/project-registry.md)
- [docs/project-record.md](docs/project-record.md)

運用・設計向け:

- [docs/registry-internals.md](docs/registry-internals.md)
- [docs/roadmap.md](docs/roadmap.md)
- [docs/thread-handoff.md](docs/thread-handoff.md)

実装:

- [scripts/project_registry_core/](scripts/project_registry_core)
- [scripts/project-registry](scripts/project-registry)
- [prompts/project-triage.md](prompts/project-triage.md)
