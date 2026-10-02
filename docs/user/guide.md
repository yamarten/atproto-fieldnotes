# Project Registry 利用ガイド

Project Registryは、atproto関連のプロジェクト、サービス、ライブラリ、lexiconをシードから登録し、調査結果と利用者の判断をSQLiteへ蓄積するローカルツールです。この文書は、最初の準備と日常利用の入口を案内します。

- 画面ごとの操作: [Web UIガイド](web-ui.md)
- 全コマンドの入出力とDB変更: [CLIガイド](cli.md)
- 保存形式と内部仕様: [開発者向け文書](../documentation-policy.md#開発仕様)

## 環境準備

uvでPython本体とvenvをcheckout内の`.runtime/世代名/`へ準備し、`.venv`から選択します。既定のminor versionは[.python-version](../../.python-version)のCPython 3.14です。Python 3.10以上が実装上の下限ですが、通常環境はこの既定versionで検証します。

Tangledの取得とOAuth scopeの解析にはNode.js 22.15以上と以下のNode依存が必要です。OAuthの解析はローカルで行い、追加のLLM呼出しはありません。

```bash
bin/project-registry-python-env prepare initial
bin/project-registry-python-env activate initial
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

## Python環境の管理

Python本体・標準ライブラリ・venvを同じcheckoutの`.runtime/世代名/`に保持します。`.venv`は選択中の世代の`venv/`へのsymlinkです。世代を準備しても選択や起動中processは変わらず、`activate`はsymlinkだけを原子的に置き換えます。世代名は英数字・`_`・`-`を使い、既存名は再利用しません。失敗した準備も上書きせず別名でやり直します。

uvは導入・保守時だけ必要です。macOSでは`brew install uv`、他の環境では[公式導入手順](https://docs.astral.sh/uv/getting-started/installation/)を使います。Homebrewでuv自体を更新しても、アプリのPython本体は更新・削除されません。`prepare`はPythonをAstralのpython-build-standalone配布先、依存をPyPIから取得するため、通信・書込権限が必要です。ユーザー共通のuv Python保存先やPATH上のPythonを変更しません。

公開wrapperの選択順序は`PROJECT_REGISTRY_PYTHON_ENV`、このcheckoutの`.venv`、このcheckoutに`.venv`が存在しない場合だけ本家checkoutの`.venv`です。指定済み環境の破損・依存欠落・Homebrew等の外部Python参照はエラーで停止し、システムPythonへfallbackしません。activateは各checkoutだけに作用しますが、本家環境を借りるworktreeの次回起動には本家の選択変更が反映されます。独立検証するworktreeには自身の世代を準備してください。

wrapperは選択symlinkを解決した世代の絶対pathからPythonを起動します。Webのworkerと通常reloadはこの`sys.executable`を使うため、`.venv`を切り替えても稼働中Webは旧世代を使い続けます。activateは既存processを停止しません。通常起動でuvを呼ばず、アプリにPythonの自動更新機能はありません。

### 準備と検証

`prepare NAME [PYTHON_VERSION [REQUIREMENTS]]`は専用Python本体、venv、依存を作ります。version省略時は`.python-version`のminorに対応するuvの最新patchを選び、取得済み本体の実pathでvenvを作ります。[uvのminor symlinkによる自動patch切替](https://docs.astral.sh/uv/concepts/python-versions/#minor-version-directories)を避け、世代内のversionを維持します。利用可能versionはuvの版に依存するため、保守時に必要ならuvを更新します。minor変更は`.python-version`の変更とアプリ検証を伴う別の判断です。

新規導入は環境準備の`initial`を使います。既存環境の移行やPythonだけの更新では依存を同時更新しないよう、現環境の固定一覧を渡します。以下はcheckoutのrootで行います。

```bash
mkdir -p .tmp/python-env
# 初回移行時: 既存venvの導入済みversionを読み取る（既存環境は変更しない）
uv pip freeze --python .venv/bin/python > .tmp/python-env/requirements.txt
bin/project-registry-python-env prepare candidate 3.14 .tmp/python-env/requirements.txt
# 既に専用世代を利用中なら、その世代のrequirements.txtを代わりに渡せる
export PROJECT_REGISTRY_PYTHON_ENV="$PWD/.runtime/candidate/venv"
bin/project-registry-python-env status
bin/project-registry-test
bin/project-registry --help
bin/project-registry-web-service --help
# 開発DB未作成の場合だけ生成（既存DBは再利用）
bin/project-registry dev-db
bin/project-registry --db-path data/dev/survey.db show --all
bin/project-registry-web --host 127.0.0.1 --port 8765 --db-path data/dev/survey.db --token web-ui-dev
```

検証用Webは実運用token・定期設定を使いません。画面・静的asset・設定・一覧APIと、外部通信のないreview dry-run等の実workerを確認して停止します。[変更の受入](../developer/workflows/change-acceptance.md)に従い、使用中8765を避けるために別portへ逃がしません。`status`でPython本体・標準ライブラリが候補世代の`python/`内、実行入口が候補世代の`venv/`内であることを確認します。各世代の`requirements.txt`は実際に導入したversion一覧で、再構築用に保持します。venv・Python本体は絶対pathを含むため別checkoutへコピー・移動せず、移行先で再準備します。

### 切替と復旧

検証後は`unset PROJECT_REGISTRY_PYTHON_ENV`し、候補を選択します。CLIだけなら既存処理の終了を待ちます。常駐Webがある場合は実行中job・workerとCLIを確認し、[WebのPython切替手順](web-ui.md#python環境の切替)で停止・再起動します。本番適用は実装受入とは別の承認段階です。

既存`.venv`が実directoryの場合、activateは上書きを拒否します。使用process停止後に`mv .venv .venv-legacy`で保持してから切り替えます。この退避は元venvのscript内絶対pathを書き換えないため、旧環境への復旧時は必ず元の`.venv`へ戻します。

```bash
unset PROJECT_REGISTRY_PYTHON_ENV
bin/project-registry-python-env activate candidate
bin/project-registry-python-env status
```

切替が失敗した場合は旧選択が残ります。新世代での確認が失敗した場合はprocessを停止し、`bin/project-registry-python-env activate OLD_NAME`で旧世代を選び直して起動します。旧世代は準備・切替で変更されません。

初回移行の復旧では新Webを停止し、`.venv`のsymlinkだけを外して`mv .venv-legacy .venv`で元に戻します。新wrapperは外部Pythonを拒否するため、このcommitも戻して旧wrapperを使う必要があります。旧Homebrew本体が既に消えている場合、venvの退避だけでは復旧できません。専用環境の再構築を優先します。

### 更新と不要環境の整理

保守時は新しい世代名で`prepare`→候補指定で検証→Web・worker・CLIの停止→activate→新規起動→HTTP/HTTPS確認の順に行います。`uv python upgrade`や`uv python uninstall`を既存世代に対して実行せず、導入済み依存もその場で書き換えません。uv cacheは`.tmp/uv-cache`へ置き、venvには依存をcopyするためcacheの削除は稼働環境に影響しません。

旧世代を消すのは、選択中でなく、その世代を使うWeb・worker・CLIがすべて終了し、新環境の運用確認と復旧用の保持期間を終えた後です。`status`は選択環境の情報で、旧processが終了した証拠にはなりません。`ps -axo pid,command`や対象serviceの状態を照合し、不明なprocessがあれば保持します。通常reloadでは旧世代が残ることにも注意してください。条件確認後、対象を限定して`.runtime/OLD_NAME/`全体を削除します。自動cleanupは行いません。

`.runtime/`と`.venv`はGit管理外です。worktreeをarchive・削除する前にもそのworktree内のPythonを使うprocessを停止します。失敗した未選択世代は利用processがないことを確認して削除できます。Python本体も失った場合は、保存したversionと`requirements.txt`から新名で再準備・検証します。DBの復旧とは別操作です。

## 基本用語

| 用語 | この文書での意味 |
| --- | --- |
| 登録プロジェクト | Project Registryが1つの調査対象としてDBに保存する単位。コマンドやJSONでは`project`と表記される。 |
| シード | 登録プロジェクトを作成・特定する入力。NSID、リポジトリURL、サービスURLを指定できる。入力した値の役割、情報源、採用判断はそれぞれ保存する。 |
| 公式サイト（Website） | 人が登録プロジェクトを知るための代表的な公式URL。紹介、documentation、native appの配布pageでもよく、公式サイトであることだけでは識別子や信頼の起点にならない。 |
| サービスURL | 登録プロジェクトの機能を実際に提供するURL。Web app、API、PDSなどが該当し、1つの登録プロジェクトに複数あってよい。公式サイトと同じURLでも別の責務として扱う。 |
| `enrich` | 登録プロジェクトを調査し、現在の情報と調査履歴を更新する処理。 |
| 外部情報の取り込み（`sync`） | 設定済みの外部情報源を走査し、既存の登録プロジェクトとの照合結果や新規登録候補を作る処理。 |
| 調査実行記録 | 1つの登録プロジェクトに対する`enrich`などの保存済み調査履歴。JSONでは`investigation run`または`run`と表記される。 |
| 追跡状態 | 継続調査上の扱いを示す状態。コマンドやJSONでは`tracking_state`と表記される。 |
| 要レビュー一覧 | 人による確認が必要な項目の一覧。CLIでは`review-queue`。 |
| 主機能でのatproto利用 | 主機能のデータ・基盤、補助的な利用、Bluesky専用操作、認証のみ、利用範囲は不明という理由付きの手動判断。サービスを利用できるか、技術的な学びがあるかとは別に記録し、LLMでは自動確定しない。 |
| 技術候補 | 具体的なprotocol設計・相互運用・実装上の学びを理由に選ぶ紹介候補。サービスの有無と独立した入口でも確認できる。 |
| 紹介候補の保留 | 確認対象から一時的に外す判断。理由は任意、既定30日。期限・根拠変化・明示解除で再評価できる。 |
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

1. `登録プロジェクト`の`シードを登録`でシードをプレビューして登録する。
2. 登録プロジェクトの詳細画面にある`調査`で調査を開始する。
3. `概要`と各タブで保存結果を確認する。
4. `レビュー`で要レビュー項目、紹介投稿の要確認、紹介候補、マージ候補を処理する。
5. 複数の登録プロジェクトの調査や外部情報の取り込み（`sync`）は`処理・定期実行`で開始・監視する。

画面内の変更操作は確認後にSQLiteへ保存します。`今回は先送り`はDBへ書かず、現在の一覧内で次へ進むだけです。詳細は[Web UIガイド](web-ui.md)を参照してください。

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

Webの概要では説明と確認時点を別々に読めます。「最新の試行」と「最後に利用面を確認」の日時は異なる場合があります。成功が古いことや今回の取得失敗だけでは、現在の利用可否・終了を確定できません。

紹介情報の「編集」では説明・短い紹介文・特徴を編集できます。人が編集した説明と来歴不明の既存説明は自動で上書きせず、根拠付きの更新案を確認して採用・修正・見送りできます。明示的に自動反映を許可することもできます。

低頻度の確認候補は`monitor-services`で確認できます。Webの`サービス観測` presetは初期状態では無効です。確認結果から説明を更新するにはenrich、追跡を戻すには追跡状態のレビューまたは手動変更を使います。観測だけで`stable / out_of_scope`を`active`へ戻しません。

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

endpointとモデルを指定した通常の`enrich`では、根拠・実行条件・手動判断が同じなら、30日未満の説明・分類結果を再利用します。検索と根拠の取得は毎回続けます。モデル名を変えずに中身を入れ替えた時など、同じ材料でも生成し直すには`ATPROTO_SURVEY_TRIAGE_REUSE=0 bin/project-registry enrich -p KEY`を使います。command方式では再利用しません。生成日時は元のまま保持し、調査実行記録の`metrics.triage_reuse`で再利用か再生成かを確認できます。

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
