# Project Registry 利用ガイド

Project Registryは、atproto関連のproject、service、library、lexiconをseedから登録し、調査結果とoperator判断をSQLiteへ蓄積するローカルツールです。この文書は、最初の準備と日常利用の入口だけを扱います。

- 画面ごとの操作: [Web UIガイド](web-ui.md)
- 全コマンドの入出力と副作用: [CLIガイド](cli.md)
- 保存形式と内部仕様: [開発者向け文書](../developer/registry-internals.md)

## 環境準備

Python 3.10以上の隔離環境を使います。公開wrapperは`.venv/bin/python`を優先し、無ければ`python3`を使います。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PATH="$PWD/bin:$PATH"
```

公開入口は`project-registry`と`project-registry-web`です。`PATH`を変更しない場合は`bin/project-registry`、`bin/project-registry-web`と実行してください。

新しいDBは、通常コマンドやWeb UIを起動する前に明示的に初期化します。

```bash
project-registry init-db
```

既定DBは`data/survey.db`です。通常コマンド、Web UI、background workerはDBを暗黙作成しません。存在しないDBやschema version 2ではないDBを指定するとerrorで停止します。`init-db`以外のCLIはDB pathを切り替えるoptionを持たず、正本の`data/survey.db`を使います。別DBをWeb UIで使う場合は次のように準備します。

```bash
project-registry init-db --db-path data/survey.dev.db
project-registry-web --db-path data/survey.dev.db
```

## 扱うseed

seed typeは`nsid`、`repo_url`、`service_url`です。通常は値から自動判定し、曖昧なURLだけ`--type`またはWeb UIの`type: value`形式で明示します。

`nsid`として、NSIDそのものに加えて次のURLも受け付けます。

- `at://did.../collection[/rkey]`
- `https://pdsls.dev/at://did.../collection[/rkey]`
- `https://pds.ls/at://did.../collection[/rkey]`
- `https://lexicon.garden/lexicon/did.../collection.nsid`

既知forgeのfile/tree URLはrepository rootへ正規化します。未知のforgeや曖昧なURLは`repo_url`を明示してください。

## 日常フロー

### Web UIを使う

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

起動時に表示されるURLを開き、次の順で操作します。

1. `Projects`の`Add seed`でseedをpreviewして登録する。
2. project detailの`Enrich`で調査を開始する。
3. `Overview`と各detail tabで保存結果を確認する。
4. `Review`でreview、promotion、merge候補を処理する。
5. 複数projectの調査やsyncは`Operations`で開始・監視する。

画面内の変更操作は確認後にSQLiteへ保存します。`Skip for now`はDBへ書かず、現在の一覧内で次へ進むだけです。詳細は[Web UIガイド](web-ui.md)を参照してください。

### CLIを使う

```bash
project-registry add-seed sh.tangled.repo
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry show --seed sh.tangled.repo --type nsid
project-registry review-queue
```

既存projectへseedを足す場合は`--project-key`を指定します。project keyは`proj:xxxxxxxxxxxx`形式で、CLIでは一意なprefixでも指定できます。

```bash
project-registry add-seed https://example.com --project-key 2f2acb
```

正確な引数は`project-registry COMMAND --help`、出力形式と副作用は[CLIガイド](cli.md)を確認してください。

## 操作前に区別すること

Project Registryの操作は、次の3点を分けて判断します。

| 種類 | 意味 |
| --- | --- |
| 読み取り専用 | DBも外部sourceも変更しない。`show`、`explain`、`audit-url-fields`、`audit-trust-graph`など。 |
| dry-run | DBへは書かないが、syncやservice確認は外部networkへ接続することがある。 |
| 書き込み | 常時書くコマンドと、`--execute`または確認後だけ書くコマンドがある。 |

`enrich`は調査結果とrun履歴を常にDBへ保存します。`add-seed`、`set-*`、`block-*`、`defer-*`も常時書き込みです。sync、merge、split、repair、cleanup、pruneは既定がdry-runで、`--execute`を付けた時だけDBを変更します。

正本DBへ書く前は、Web UI、別CLI、background workerが同時に動いていないか確認してください。特にrepair、merge、cleanup、prune、`VACUUM`はbackupを取り、writerを止めたmaintenance windowで実行します。

## 出力と終了状態

- 多くのCLIコマンドはstdoutへJSONを1件出す。
- `show`と`explain`は`--format text|json`を選べる。
- `enrich`は進捗と結果をJSON Linesで出す。
- interactive操作のpromptはTTYに表示し、最終結果はstdoutへ出す。
- 引数errorは終了コード`2`、処理失敗は通常`1`。
- bulk enrichをCtrl+Cで協調停止した場合は、処理中projectを保存してcheckpointを更新した後、終了コード`130`で終わる。

スクリプトから使う場合は表示文ではなくJSONと終了コードを確認してください。

## LLMと外部sourceの設定

command方式のLLM:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
```

OpenAI互換endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_API_KEY='...'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
export ATPROTO_SURVEY_LLM_REASONING_EFFORT='none'
export ATPROTO_SURVEY_LLM_API_STYLE='auto'
```

Bluesky検索、GitHub検索、Web UI token:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
export GITHUB_TOKEN='ghp_...'
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
```

`ATPROTO_SURVEY_LLM_ENDPOINT`はoperatorが管理するtrusted endpointとして扱い、localhostやLANも許可します。Web UIからLLM接続設定を変更する機能はありません。`DNS_RESOLVER_URL`や`UFOS_API`は現在の環境変数ではありません。

discovery sourceのenabled、URL、query、scope、request budgetは`config/sources.json`、PSLにないhosted suffixは`config/domain-overrides.json`で管理します。suffix変更の影響確認は`audit-source-config`を使います。

## zsh補完

```bash
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

`project-registry <TAB>`と`project-registry-web --<TAB>`で公開コマンドとoptionを補完できます。

## 次に読む文書

- Web中心で使う: [Web UIガイド](web-ui.md)
- CLIやmaintenanceを使う: [CLIガイド](cli.md)
- 現在の実装状態と制約を確認する: [status.md](../status.md)
- DB保存契約を確認する: [project-record.md](../developer/project-record.md)
