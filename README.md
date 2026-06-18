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
export PATH="$PWD/bin:$PATH"
```

`tldextract` を使って PSL 判定を行います。入力は repo 内の [vendor/public_suffix_list.dat](vendor/public_suffix_list.dat) に固定し、ライブラリの cache は [data/tldextract-cache](data/tldextract-cache) 配下に置きます。外部の絶対パスには依存しません。

PSL に無い hosted suffix を手動で足したい場合は、[config/domain-overrides.json](config/domain-overrides.json) の `custom_multi_tenant_suffixes` に追加します。ここに `example.com` を入れると、`foo.example.com` と `bar.example.com` を別 service family として扱います。discovery source の設定は [config/sources.json](config/sources.json) に分離しています。

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

## Web UI

schema v2対応のローカルWeb UIを起動できます。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

閲覧専用で起動する場合は`--read-only`を付けます。このmodeでは全てのWeb書込みとjob controlを拒否します。

project検索、review/promotion/merge queue、normalized identifier/relation/decision、最新調査状態の表示、日常的なidentifier・tracking・merge操作に対応しています。enrich、設定済みsourceのsync、tracking classification reviewは別processのbackground jobとしてWebから開始・監視できます。enrich bulkだけは中断とcheckpoint再開に対応し、sync/reviewの停止は現在のoperation完了後に反映されます。

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

旧DBは次で移行します。

```bash
project-registry migrate-db data/survey.db
```

v1からの移行では、旧confirmedを無条件には信頼しません。根拠を再構成できないidentifierはmetadata付きcandidateへ降格し、`manual_seed`または`confirm_identifier` decisionがあるものだけoperator根拠としてconfirmedを維持します。出力reportの`uncorroborated_legacy_confirmed_count`、`active_block_identifier_collision_count`、`normalized_roundtrip_diff_count`が0であることが移行成功条件です。置換前に確認したい場合は`--dry-run`を使います。

古い調査履歴は自動削除しません。既定で90日以内の全runとprojectごとの最新3 runを残し、それより古いrunをdry-run確認・手動削除できます。

```bash
project-registry prune-investigation-history
project-registry prune-investigation-history --execute
```

DB file自体も縮める場合だけ、他processを停止して`--execute --vacuum`を使います。詳細は[docs/project-registry.md](docs/project-registry.md)を参照してください。

v1移行後に十分安定したら、identifier rowを残したまま移行専用metadataだけを削除できます。

```bash
project-registry prune-migration-provenance
project-registry prune-migration-provenance --execute
```

## 文書

利用者向け:

- [docs/project-registry.md](docs/project-registry.md)
- [docs/project-record.md](docs/project-record.md)

運用・設計向け:

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
