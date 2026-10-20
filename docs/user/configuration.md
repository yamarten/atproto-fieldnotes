# LLMと外部情報源の設定

すべての設定が必須というわけではありません。DBの初期化、CLIでの登録・閲覧・手動編集には、このページの環境変数は不要です。Web UIを使う場合はログイン用トークンだけ用意すれば、保存済みデータを閲覧・編集できます。LLMや外部サービスは、使う機能に必要なものだけ設定します。

| 使いたい機能 | 必要な設定 | 未設定の場合 |
| --- | --- | --- |
| Web UIの閲覧・編集 | `--token`または`ATPROTO_SURVEY_WEB_TOKEN` | Webサーバーを起動できない。CLIは利用できる |
| LLMによる説明・分類、リポジトリ解析 | LLM接続先、または外部コマンド。接続先に応じたmodel・API key | LLM処理は失敗し、説明・分類やリポジトリ解析の結果は生成できない |
| GitHubの公開情報の検索・取得 | `GITHUB_TOKEN`は任意 | 認証なしで取得するため、API利用制限を受けやすい |
| Bluesky認証付き検索・紹介投稿の照合 | `BSKY_HANDLE`と`BSKY_APP_PASSWORD` | 認証付きの取得経路は利用できない |
| Tangledの新規候補収集 | `ATPROTO_SURVEY_JETSTREAM_API_KEY` | Tangled候補の同期はエラーになる |

LLMや認証の未設定は、`enrich`から該当処理を省略する指定ではありません。利用できる経路の取得が進んでも、未設定の処理は失敗として残り、通常の調査を完了したことにはなりません。保存済みデータの閲覧・手動編集や、必要な接続先を設定済みの個別機能は利用できます。

Web UIは起動したprocessの環境変数を使うため、起動前に設定します。画面からLLM設定を変更する機能はありません。設定後は[利用ガイドの登録・調査](guide.md#1件を登録して調査する)へ戻ってください。

## LLM接続

`ATPROTO_SURVEY_LLM_ENDPOINT`は利用者が管理する信頼済みの接続先として扱い、localhostやLANも許可します。

HTTP接続ではHTTPSからHTTPへの転送を拒否します。認証headerまたは送信本文を伴うAPI呼出しは、同じscheme・host・portへの転送だけを許可します。別originへの転送が必要な接続先は、信頼できる最終endpointを設定してください。認証も本文もないページ取得は、既存の接続先検証を通る別originへの転送を許可します。

OpenAI互換endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='your-local-model-id'
export ATPROTO_SURVEY_LLM_API_KEY='...'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_REASONING_EFFORT='none'
export ATPROTO_SURVEY_LLM_API_STYLE='auto'
```

endpointとmodelは、起動済みのLLMサーバーに合わせて置き換えます。API keyが不要な接続先ではその設定を省略します。

例中の`ATPROTO_SURVEY_LLM_TIMEOUT`、`ATPROTO_SURVEY_LLM_REASONING_EFFORT`、`ATPROTO_SURVEY_LLM_API_STYLE`は任意です。省略時はtimeoutが300秒、API方式が`auto`になり、reasoning設定は要求しません。

外部コマンドを使う場合は次の設定です。コマンドはpromptを標準入力で受け取り、結果のJSONを標準出力へ返す必要があります。endpointと両方を設定するとcommandが優先されます。

command方式のLLM:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='your-local-model-id'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
```

### モデル別の規則と再生成

登録プロジェクトの説明・分類を補うLLM処理をtriageと呼びます。特定のモデル構成に追加のtriage規則を適用する場合は、`enrich`へ`--llm-triage-prompt-suffix-file PATH`を指定するか、定期ジョブを含む既定値として`ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE`へPATHを設定します。これは共通プロンプトの末尾へUTF-8文書を追加する設定で、CLI指定は環境変数より優先します。使用した本文のハッシュ値は調査実行記録へ保存されます。

現行QwenのモデルIDでは`prompts/model-rules/qwen-3.6-35b-a3b.txt`を自動選択します。別モデルには、そのモデルで確認した追加規則がある場合だけCLIまたは環境変数で指定します。

固定評価と同じ出力上限で通常処理を確認する場合は、`--llm-max-output-tokens 2048`のように指定します。この値は採用済みリポジトリの解析、triageの各LLMリクエストへ共通して適用されます。省略時は接続先の設定を使います。

endpointとモデルを指定した通常の`enrich`では、根拠・実行条件・手動判断が同じなら、30日未満の説明・分類結果を再利用します。検索と根拠の取得は毎回続けます。モデル名を変えずに中身を入れ替えた時など、同じ材料でも生成し直すには`ATPROTO_SURVEY_TRIAGE_REUSE=0 bin/project-registry enrich -p KEY`を使います。command方式では再利用しません。生成日時は元のまま保持し、調査実行記録の`metrics.triage_reuse`で再利用か再生成かを確認できます。

## 外部サービスとWebの認証

使う取得経路の値だけを設定します。`BSKY_AUTH_HOST`は省略すると`https://bsky.social`です。Tangledの候補収集には`ATPROTO_SURVEY_JETSTREAM_API_KEY`も設定してください。

Bluesky検索、GitHub検索、Web UI token:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
export GITHUB_TOKEN='ghp_...'
export ATPROTO_SURVEY_WEB_TOKEN='your-web-login-token'
```

`config/sources.json`の`lexicon_publications`は公開Lexiconの情報源です。`enabled`、`url`、`max_events`（既定20,000）、`max_seconds`（300）、`verify_limit`（`null`で件数制限なし、0で検証省略）、`verification_seconds`（3,600）を設定します。検証は未検証・変更・期限到来のNSIDを対象に最大4並列で行い、時間内に処理できなかった対象は次の取り込みで再開します。Jetstream API keyは既存の`ATPROTO_SURVEY_JETSTREAM_API_KEY`を使用します。

## 設定ファイルと認証情報

環境変数をdirenvで管理する場合は、[設定テンプレート](../../.envrc.example)から始めます。既存の`.envrc`がある場合は上書きせず、必要な項目だけ追加します。

```bash
cp -n .envrc.example .envrc
chmod 600 .envrc
```

`.envrc`を編集し、使う項目のコメントを外して自分の値を設定した後に`direnv allow`を実行します。Web UI tokenはブラウザーでのログインに使う合言葉です。自分で決めた秘密の値を設定し、初回の入力ダイアログにも同じ値を入力します。Blueskyにはアプリパスワードを使います。テンプレートの空の認証値は、そのまま接続に使える値ではありません。`.envrc`はGit管理外で、macOSのLaunchAgentもこのファイルをdirenv経由で読みます。環境変数を直接設定する運用では`.envrc`とdirenvは不要です。

候補収集に使う外部情報源の有効・無効、URL、検索条件、対象範囲、リクエスト上限は[config/sources.json](../../config/sources.json)で管理します。同梱設定は、このプロジェクトの公開紹介アカウントを検索し、公開Semble collectionを収集する設定です。別の紹介活動に使う場合は`introduced_search.query`を変更し、Sembleの対象は`semble_collections.tracked_collections`で選びます。収集しない情報源は`enabled: false`にします。認証値はsource設定へ書かず、上記の環境変数へ設定します。

定期実行のpresetでは、各取り込みの開始時に`enabled: false`の情報源を理由付きでスキップし、後続の取り込み・調査へ進みます。「今すぐ実行」、保留中の処理の再開、server再起動後の実行待ちも同じ扱いです。CLIや画面から情報源を個別指定した取り込みは、無効ならエラーを返します。設定不足や通信障害など、無効化以外の失敗をスキップにはしません。

Semble取り込みは、人が集めた公開リンク集を継続して確認し、他の発見経路では拾いにくいサービスURLや、collectionを作らないPDS実装などのインフラの発見を補うために使います。同梱の3 collectionは、この補完に役立つ初期対象として選んだものです。今後も拾いたい候補に合わせて収集先を選びます。リンク集への掲載は所有関係や紹介適性の保証ではなく、登録後の調査と判断が必要です。

ドメインの区切りを判断するPublic Suffix List（PSL）にないホスティングサービスの接尾辞を追加する場合だけ、任意の`config/domain-overrides.json`を用意します。未作成なら同梱PSLと既定の規則を使います。[設定例](../../config/domain-overrides.example.json)と接尾辞変更の影響確認に使う`audit-source-config`を参照してください。
