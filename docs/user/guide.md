# Project Registry 利用ガイド

Project Registryは、atproto関連のプロジェクト、サービス、ライブラリ、lexiconをシードから登録し、調査結果と利用者の判断をSQLiteへ蓄積するローカルツールです。この文書は、最初の準備と日常利用の入口を案内します。

- 画面ごとの操作: [Web UIガイド](web-ui.md)
- 全コマンドの入出力とDB変更: [CLIガイド](cli.md)
- 保存形式と内部仕様: [開発者向け文書](../documentation-policy.md#開発仕様)

## 環境準備

Python 3.10以上の隔離環境を使います。公開wrapperは`.venv/bin/python`を優先し、無ければ`python3`を使います。

Tangledの取得とOAuth scopeの解析にはNode.js 22.15以上と以下のNode依存が必要です。OAuthの解析はローカルで行い、追加のLLM呼出しはありません。

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

既定DBは`data/survey.db`です。通常コマンド、Web UI、バックグラウンド処理はDBを暗黙作成しません。存在しないDBやschema version 4ではないDBを指定するとエラーで停止します。通常CLIはcommandの前へ`--db-path PATH`を置くと別DBを使えます。`init-db`と一部の開発補助コマンドはcommandの後の専用`--db-path`を使うため、各`--help`で確認してください。CLI/Webの手動確認には架空データ入りのDBを次のように準備します。

```bash
project-registry dev-db
project-registry --db-path data/dev/survey.db show --all
project-registry-web --host 127.0.0.1 --port 8765 --db-path data/dev/survey.db --token web-ui-dev
```

既存のダミーDBは再利用し、初期状態へ戻す場合は使用processを停止して`project-registry dev-db --reset`を実行します。安全境界と確認範囲は[共通ダミーDBの手順](../developer/workflows/change-acceptance.md#cliwebの共通ダミーdb)を参照してください。

## 基本用語

| 用語 | この文書での意味 |
| --- | --- |
| 登録プロジェクト | Project Registryが1つの調査対象としてDBに保存する単位。コマンドやJSONでは`project`と表記される。 |
| シード | 登録プロジェクトを作成・特定する入力。NSID、リポジトリURL、サービスURLを指定できる。入力した値の役割、情報源、採用判断はそれぞれ保存する。 |
| Website | 人が登録プロジェクトを知るための代表的な公式URL。紹介、documentation、native appの配布pageでもよく、Websiteであることだけでは識別子や信頼の起点にならない。 |
| サービスURL | 登録プロジェクトの機能を実際に提供するURL。Web app、API、PDSなどが該当し、1つの登録プロジェクトに複数あってよい。Websiteと同じURLでも別の責務として扱う。 |
| `enrich` | 登録プロジェクトを調査し、現在の情報と調査履歴を更新する処理。 |
| 外部情報の取り込み（`sync`） | 設定済みの外部情報源を走査し、既存の登録プロジェクトとの照合結果や新規登録候補を作る処理。 |
| 調査実行記録 | 1つの登録プロジェクトに対する`enrich`などの保存済み調査履歴。画面やJSONでは`investigation run`または`run`と表記される。 |
| 追跡状態 | 継続調査上の扱いを示す状態。コマンドやJSONでは`tracking_state`と表記される。 |
| 要レビュー一覧 | 人による確認が必要な項目の一覧。CLIでは`review-queue`。 |
| 主機能でのatproto利用 | 主機能のデータ・基盤、補助的な利用、Bluesky専用操作、認証のみ、利用範囲は不明の区別。サービスを利用できるか、技術的な学びがあるかとは別に評価する。 |
| 技術候補 | 具体的なprotocol設計・相互運用・実装上の学びを理由に選ぶ紹介候補。サービスの有無と独立した入口でも確認できる。 |
| 紹介候補一覧 | 紹介する候補を選び、優先度や保留を判断する一覧。CLIでは`promotion-queue`。 |
| 分類 | 主な利用者と利用形態による登録プロジェクトの分類。保存値は`project`、`lexicon`、`library`、`infra_tool`、`personal_tool`、`name_only`、`reject`。詳しい境界は[Project Record Schema](../developer/specs/project-record.md#category)を参照。 |
| 採用 | 候補の識別子を、その登録プロジェクトに属するものとして利用者が認める操作。保存値は`confirmed`。この値は自動判定による採用にも使われ、利用者の操作は手動判断の記録で確認できる。 |
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

シードは対応する所有NSID、サービス、ソースリポジトリとして`confirmed`保存し、採用判断も記録します。NSIDの`owned`は登録プロジェクトによるcollection所有を表し、単なる利用・言及を表しません。サービスURLはパスを保持するため、複数のツールを異なるパスで公開するサイトも指定できます。明示入力では、`/docs`などのページ名だけを理由に補助ページ扱いにしません。`add-seed`で新規登録するサービスURLは、Websiteが空の場合だけ同じURLを初期値にします。その後の候補採用や収集ではWebsiteを自動設定せず、両者は個別に変更できます。

正確な引数は`project-registry COMMAND --help`、出力形式と副作用は[CLIガイド](cli.md)を確認してください。

### 説明を保ちながら変化を追う

説明には、その登録プロジェクトの用途・機能・技術的特徴を残します。一時的に取得できなくても「アクセス不可」だけへ置き換えません。提供終了を確認した場合は何を提供していたかを過去形で説明し、機能が変わった場合も理解に役立つ以前の特徴は時点を区別して残します。元々の誤記は訂正します。

WebのOverviewでは説明と確認時点を別々に読めます。「最新の試行」と「最後に利用面を確認」の日時は異なる場合があります。成功が古いことや今回の取得失敗だけでは、現在の利用可否・終了を確定できません。

ProfileのEditでは説明・短い紹介文・特徴を編集できます。人が編集した説明と来歴不明の既存説明は自動で上書きせず、根拠付きの更新案を確認して採用・修正・見送りできます。明示的に自動反映を許可することもできます。

低頻度の確認候補は`monitor-services`で確認できます。Webの`Service observation` presetは初期状態では無効です。確認結果から説明を更新するにはenrich、追跡を戻すには追跡状態のレビューまたは手動変更を使います。観測だけで`stable / out_of_scope`を`active`へ戻しません。

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

スクリプトから使う場合はJSONと終了コードで結果を判定してください。

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

登録プロジェクトの説明・分類を補うLLM処理をtriageと呼びます。特定のモデル構成に追加のtriage規則を適用する場合は、`enrich`へ`--llm-triage-prompt-suffix-file PATH`を指定するか、定期ジョブを含む既定値として`ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE`へPATHを設定します。これは共通プロンプトの末尾へUTF-8文書を追加する設定で、CLI指定は環境変数より優先します。使用した本文のハッシュ値は調査実行記録へ保存されます。

現行QwenのモデルIDでは`prompts/model-rules/qwen-3.6-35b-a3b.txt`を自動選択します。別モデルには、そのモデルで確認した追加規則がある場合だけCLIまたは環境変数で指定します。

固定評価と同じ出力上限で通常処理を確認する場合は、`--llm-max-output-tokens 2048`のように指定します。この値は調査計画の補助（planning）、採用済みリポジトリの解析、triageの各LLMリクエストへ共通して適用されます。省略時は接続先の設定を使います。

Bluesky検索、GitHub検索、Web UI token:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
export GITHUB_TOKEN='ghp_...'
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
```

`ATPROTO_SURVEY_LLM_ENDPOINT`は利用者が管理する信頼済みの接続先として扱い、localhostやLANも許可します。LLMの接続設定は上記の環境変数で行います。Web UIから変更する機能はありません。

候補収集に使う外部情報源の有効・無効、URL、検索条件、対象範囲、リクエスト上限は`config/sources.json`で管理します。ドメインの区切りを判断するPublic Suffix List（PSL）にないホスティングサービスの接尾辞は`config/domain-overrides.json`で補います。接尾辞変更の影響確認には`audit-source-config`を使います。

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
