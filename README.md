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

Python 3.10以上の隔離環境を使います。公開wrapperは`.venv/bin/python`を優先し、無ければ`python3`を使います。
選ばれたruntimeが3.10未満の場合はmoduleをimportせず、version errorで終了します。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PATH="$PWD/bin:$PATH"
```

`tldextract` を使って PSL 判定を行います。入力は repo 内の [vendor/public_suffix_list.dat](vendor/public_suffix_list.dat) に固定し、ライブラリの cache は [data/tldextract-cache](data/tldextract-cache) 配下に置きます。外部の絶対パスには依存しません。

PSL に無い hosted suffix を手動で足したい場合は、ローカル設定 `config/domain-overrides.json` の `custom_multi_tenant_suffixes` に追加します。ここに `example.com` を入れると、`foo.example.com` と `bar.example.com` を別 service family として扱います。初期形は [config/domain-overrides.example.json](config/domain-overrides.example.json) です。discovery source の設定は [config/sources.json](config/sources.json) に分離しています。

登録済み project への影響も確認する場合は、監査コマンドから追加できます。

```bash
project-registry audit-source-config example.com
```

既定は interactive です。追加確認後に `domain-overrides.json` を更新し、新しい family 基準で同一 project に複数 tenant が混在していないかを表示します。tenant group の番号を選んだ場合だけ、その group の identifier をまとめて新 project へ切り出します。split 後も元 project に複数 tenant が残る場合は、再計算した同 project の suggestion を続けて表示します。JSON dry-run だけを見る場合は `--no-interactive`、確認なしで suffix 追記まで行う場合は `--execute --no-interactive` を使います。non-interactive では split を適用しません。

## テスト

テストは標準ライブラリの `unittest` で実行します。外部 API、実 DB、実 LLM endpoint には触れず、必要な SQLite DB は temp directory に作ります。

```bash
python3 -m unittest discover -s tests -v
```

## zsh補完

CLIとWeb UI用のwrapper、zsh completionを追加しています。

- [bin/project-registry](bin/project-registry) / [completions/_project-registry](completions/_project-registry)
- [bin/project-registry-web](bin/project-registry-web) / [completions/_project-registry-web](completions/_project-registry-web)

wrapper で呼ぶ場合:

```bash
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

公開command入口は`bin/project-registry`と`bin/project-registry-web`です。利用手順と生成commandは`project-registry` / `project-registry-web`に統一し、PATHには`bin/`だけを足す前提です。

新しいDBを作る場合だけ、通常commandやWebを起動する前に明示的に初期化します。

```bash
project-registry init-db
```

通常command、Web、background workerはDBを暗黙作成しません。既存DBが無い、またはschema version 2として
初期化されていない場合はerrorで停止します。別pathを初期化する場合は`init-db --db-path PATH`を使います。

## Web UI

schema v2対応のローカルWeb UIを起動できます。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

project検索、review/promotion/merge queue、normalized identifier/relation/decision、最新調査状態の表示、日常的なidentifier・tracking・merge操作に対応しています。enrich、設定済みsourceのsync、tracking classification reviewは別processのbackground jobとしてWebから開始・監視できます。enrich bulkだけは中断とcheckpoint再開に対応し、sync/reviewの停止は現在のoperation完了後に反映されます。

このUIはloopback上の個人利用向けです。token bootstrap、localStorage、plain HTTP、localhost外bindの制約は[利用手順](docs/project-registry.md#access境界)を確認してください。

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
project-registry enrich --all --llm-reasoning-effort none
project-registry enrich --all --new-only --llm-reasoning-effort high
```

## 保存形式

現行DBはregistry schema version 2です。`projects`はscalar、`profile_json`、`state_json`を保持し、identifier、relation evidence、operator decisionは専用tableへ保存します。詳細は [docs/project-record.md](docs/project-record.md) を参照してください。

旧DB schemaは現行導線の対象外です。

既存のservice evidenceと調査payloadは、runを削除せずに新しい保存budgetへ縮約できます。既定はdry-runです。

```bash
project-registry cleanup-storage-payloads
```

適用は正本DBのbackupを取り、Web、worker、別CLIを停止したmaintenance windowでだけ行います。詳細は
[docs/project-registry.md](docs/project-registry.md)を参照してください。

古い調査履歴は自動削除しません。既定で90日以内の全runとprojectごとの最新3 runを残し、それより古いrunをdry-run確認・手動削除できます。

```bash
project-registry prune-investigation-history
project-registry prune-investigation-history --execute
```

DB file自体も縮める場合だけ、他processを停止して`--execute --vacuum`を使います。詳細は[docs/project-registry.md](docs/project-registry.md)を参照してください。

## 文書

利用者向け:

- [docs/project-registry.md](docs/project-registry.md)
- [docs/project-record.md](docs/project-record.md)

運用・設計向け:

- [docs/brief.md](docs/brief.md): 目的、利用者、通常flow、成果物、主要判断、非目標
- [docs/status.md](docs/status.md): 現在の通常導線、既知の制約、検証状況
- [docs/registry-internals.md](docs/registry-internals.md)
- [docs/investigation-model.md](docs/investigation-model.md)
- [docs/background-jobs.md](docs/background-jobs.md)
- [docs/roadmap.md](docs/roadmap.md): 未完了・将来候補
- [docs/thread-handoff.md](docs/thread-handoff.md): 次スレッド固有の差分
- [docs/notes/](docs/notes/): 調査・比較・履歴資料

実装:

- [scripts/project_registry_core/](scripts/project_registry_core)
- [bin/project-registry](bin/project-registry)
- [prompts/project-triage.md](prompts/project-triage.md)
