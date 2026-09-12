# Project Registry 利用ガイド

Project Registryは、atproto関連のプロジェクト、サービス、ライブラリ、lexiconをシードから登録し、調査結果と利用者の判断をSQLiteへ蓄積するローカルツールです。この文書は、最初の準備と日常利用の入口だけを扱います。

- 画面ごとの操作: [Web UIガイド](web-ui.md)
- 全コマンドの入出力とDB変更: [CLIガイド](cli.md)
- 保存形式と内部仕様: [開発者向け文書](../documentation-policy.md#開発仕様)

## 環境準備

Python 3.10以上の隔離環境を使います。公開wrapperは`.venv/bin/python`を優先し、無ければ`python3`を使います。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm install
export PATH="$PWD/bin:$PATH"
```

Tangledの外部情報取り込みを使う場合は、Jetstream v2 API keyをprocess環境だけに設定します。

```bash
export ATPROTO_SURVEY_JETSTREAM_API_KEY='...'
```

`.envrc`を使う場合もこの名前でexportします。keyはrepository、設定ファイル、job result、SQLiteへ保存しません。

公開入口は`project-registry`と`project-registry-web`です。`PATH`を変更しない場合は`bin/project-registry`、`bin/project-registry-web`を実行してください。

新しいDBは、通常コマンドやWeb UIを起動する前に明示的に初期化します。

```bash
project-registry init-db
```

既定DBは`data/survey.db`です。通常コマンド、Web UI、バックグラウンド処理はDBを暗黙作成しません。存在しないDBやschema version 3ではないDBを指定するとエラーで停止します。`init-db`以外のCLIにはDBのパスを切り替えるオプションがなく、正本の`data/survey.db`を使います。別DBをWeb UIで使う場合は次のように準備します。

```bash
project-registry init-db --db-path data/survey.dev.db
project-registry-web --db-path data/survey.dev.db
```

## 基本用語

| 用語 | この文書での意味 |
| --- | --- |
| 登録プロジェクト | Project Registryが1つの調査対象としてDBに保存する単位。コマンドやJSONでは`project`と表記される。 |
| シード | 登録プロジェクトを作成・特定する入力。NSID、リポジトリURL、サービスURLを指定できる。識別子のroleではなく、由来は情報源と採用判断へ分けて保存する。 |
| Website | 人が登録プロジェクトを知るための代表的な公式URL。紹介、documentation、native appの配布pageでもよく、Websiteであることだけでは識別子や信頼の起点にならない。 |
| サービスURL | 登録プロジェクトの機能を実際に提供するURL。Web app、API、PDSなどが該当し、1つの登録プロジェクトに複数あってよい。Websiteと同じURLでも別の責務として扱う。 |
| `enrich` | 登録プロジェクトを調査し、現在の情報と調査履歴を更新する処理。 |
| 外部情報の取り込み（`sync`） | 設定済みの外部情報源を走査し、既存の登録プロジェクトとの照合結果や新規登録候補を作る処理。 |
| 調査実行記録 | 1つの登録プロジェクトに対する`enrich`などの保存済み調査履歴。画面やJSONではinvestigation runまたはrunと表記される。 |
| 追跡状態 | 継続調査上の扱いを示す状態。コマンドやJSONでは`tracking_state`と表記される。 |
| 分類 | 主な利用者と利用形態による登録プロジェクトの分類。保存値は`project`、`lexicon`、`library`、`infra_tool`、`personal_tool`、`name_only`、`reject`。詳しい境界は[Project Record Schema](../developer/specs/project-record.md#category)を参照。 |
| 採用 | 候補の識別子を、その登録プロジェクトに属するものとして利用者が認める操作。保存値は`confirmed`。 |
| 手動判断 | 採用、除外、保留、別プロジェクト指定など、自動判定より優先される利用者の判断。 |
| マージ | 重複した2つの登録プロジェクトを1つにまとめる操作。 |

## 扱うシード

シードの種類は`nsid`、`repo_url`、`service_url`です。通常は値から自動判定し、曖昧なURLだけ`--type`またはWeb UIの`type: value`形式で明示します。

`nsid`として、NSIDそのものに加えて次のURLも受け付けます。

- `at://did.../collection[/rkey]`
- `https://pdsls.dev/at://did.../collection[/rkey]`
- `https://pds.ls/at://did.../collection[/rkey]`
- `https://lexicon.garden/lexicon/did.../collection.nsid`

既知のforgeのfile/tree URLはリポジトリのルートURLへ正規化します。未知のforgeや曖昧なURLは`repo_url`を明示してください。

## 日常フロー

### Web UIを使う

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

起動時に表示されるURLを開き、次の順で操作します。

1. `Projects`の`Add seed`でシードをプレビューして登録する。
2. 登録プロジェクトの詳細画面にある`Enrich`で調査を開始する。
3. `Overview`と各タブで保存結果を確認する。
4. `Review`で要レビュー項目、紹介候補、マージ候補を処理する。
5. 複数の登録プロジェクトの調査や外部情報の取り込み（`sync`）は`Operations`で開始・監視する。

画面内の変更操作は確認後にSQLiteへ保存します。`Skip for now`はDBへ書かず、現在の一覧内で次へ進むだけです。詳細は[Web UIガイド](web-ui.md)を参照してください。

### CLIを使う

```bash
project-registry add-seed sh.tangled.repo
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry show --seed sh.tangled.repo --type nsid
project-registry review-queue
```

既存の登録プロジェクトへシードを足す場合は`--project-key`を指定します。プロジェクトキーは`proj:xxxxxxxxxxxx`形式で、CLIでは一意な接頭辞でも指定できます。

```bash
project-registry add-seed https://example.com --project-key 2f2acb
```

シードは対応する所有NSID、サービス、ソースリポジトリとして`confirmed`保存し、採用判断も記録します。NSIDの`owned`はprojectによるcollection所有を表し、単なる利用・言及を表しません。サービスURLはパスを保持するため、複数のツールを異なるパスで公開するサイトも指定できます。明示入力には補助ページ名のheuristicを適用しません。`add-seed`で新規登録するサービスURLは、Websiteが空の場合だけ同じURLを初期値にします。その後の候補採用や収集ではWebsiteを自動設定せず、両者は個別に変更できます。

正確な引数は`project-registry COMMAND --help`、出力形式と副作用は[CLIガイド](cli.md)を確認してください。

## 操作前に区別すること

Project Registryの操作は、次の3点を分けて判断します。

| 種類 | 意味 |
| --- | --- |
| 読み取り専用 | DBを書き換えず、外部情報源にも接続しない。`show`、`explain`、`audit-url-fields`、`audit-trust-graph`など。 |
| dry-run | DBへは書かないが、外部情報の取り込み（`sync`）やサービス確認は外部ネットワークへ接続することがある。 |
| 書き込み | 常時書くコマンドと、`--execute`または確認後だけ書くコマンドがある。 |

`enrich`は調査結果と調査実行記録を常にDBへ保存します。`add-seed`、`set-*`、`block-*`、`defer-*`も常時書き込みです。外部情報の取り込み、マージ、分割、修復、整理、履歴削除は既定がdry-runで、`--execute`を付けた時だけDBを変更します。

正本DBへ書く前は、Web UI、別のCLI、バックグラウンド処理が同時に動いていないか確認してください。特に修復、マージ、整理、履歴削除、`VACUUM`はバックアップを取り、DBへ書き込む処理を止めた保守時間帯に実行します。

## 出力と終了状態

- 多くのCLIコマンドはstdoutへJSONを1件出す。
- `show`と`explain`は`--format text|json`を選べる。
- `enrich`は進捗と結果をJSON Linesで出す。
- 対話操作の入力要求はTTYに表示し、最終結果はstdoutへ出す。
- 引数エラーは終了コード`2`、処理失敗は通常`1`。
- 一括`enrich`をCtrl+Cで協調停止した場合は、処理中の登録プロジェクトを保存してチェックポイントを更新した後、終了コード`130`で終わる。

スクリプトから使う場合は表示文ではなくJSONと終了コードを確認してください。

## LLMと外部情報源の設定

command方式のLLM:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='your-local-model-id'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
```

OpenAI互換endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='your-local-model-id'
export ATPROTO_SURVEY_LLM_API_KEY='...'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_REASONING_EFFORT='none'
export ATPROTO_SURVEY_LLM_API_STYLE='auto'
```

特定のmodel構成だけに追加のtriage規則を適用する場合は、`enrich`へ
`--llm-triage-prompt-suffix-file PATH`を指定するか、定期jobを含む既定値として
`ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE`へPATHを設定します。これは共通promptの末尾へUTF-8文書を追加する設定で、
CLI指定は環境変数より優先します。使用した本文のhashはinvestigation runへ保存されます。
現行Qwenのmodel IDでは`prompts/model-rules/qwen-3.6-35b-a3b.txt`を自動選択します。別modelにはQwen用規則を
適用せず、そのmodelで確認した追加規則がある場合だけCLIまたは環境変数で指定します。
固定評価と同じ出力上限で通常処理を確認する場合は、`--llm-max-output-tokens 2048`のように指定します。
この値はplanning、confirmed repositoryの解析、triageの各LLM requestへ共通して適用されます。省略時はendpoint側の設定を使います。

Bluesky検索、GitHub検索、Web UI token:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
export GITHUB_TOKEN='ghp_...'
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
```

`ATPROTO_SURVEY_LLM_ENDPOINT`は利用者が管理する信頼済みのendpointとして扱い、localhostやLANも許可します。Web UIからLLM接続設定を変更する機能はありません。`DNS_RESOLVER_URL`や`UFOS_API`は現在の環境変数ではありません。

候補収集に使う外部情報源の有効・無効、URL、検索条件、対象範囲、リクエスト上限は`config/sources.json`、PSLにないホスト用接尾辞は`config/domain-overrides.json`で管理します。接尾辞変更の影響確認には`audit-source-config`を使います。

## zsh補完

```bash
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

`project-registry <TAB>`と`project-registry-web --<TAB>`で公開コマンドとオプションを補完できます。

## 次に読む文書

- Web中心で使う: [Web UIガイド](web-ui.md)
- CLIやmaintenanceを使う: [CLIガイド](cli.md)
- 現在の実装状態と制約を確認する: [status.md](../developer/status.md)
- DB保存契約を確認する: [project-record.md](../developer/specs/project-record.md)
