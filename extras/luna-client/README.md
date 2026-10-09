# Luna手動client

疑わしい主張の個別確認や日本語説明の下書きに使う、macOS用の独立した手動clientです。通常enrich・Web・定期job・DB保存には接続しません。2026-10-09の試用を踏まえ、当面は必要時にだけ使える入口として保持します。既定modelの置換や、追加評価の実施予定を意味しません。

このdirectoryでソース・手順・評価の要点を管理します。導入済みSDK・認証・入力・生出力はGit外に置きます。同じMacではどのcheckoutから起動しても既定で同じ認証保存先を使うため、別アカウントで試す場合は保存先も分けてください。

## 既存の環境で使う

リポジトリのルートから実行します。`--help`はElectronを起動せず、通信もしません。

```sh
extras/luna-client/luna --help
mkdir -p .tmp/luna-manual
extras/luna-client/luna catalog .tmp/luna-manual/models.json
extras/luna-client/luna request .tmp/luna-manual/response.json .tmp/luna-manual/request.json
```

`catalog`はmodel一覧の取得です。`request`は用意した入力で1回生成します。入力例は次のJSONです。送信する資料と依頼を`prompt`へ入れます。

```json
{"model":"gpt-6-luna","reasoningEffort":"high","prompt":"ここに資料と依頼を記述","timeoutMs":180000}
```

`request`は完了本文`text`、`metadata`、`inferenceSeconds`、`status`をJSONへ保存します。`response.completed`を受けた場合だけ成功とし、API失敗時はcode・HTTP status等の診断のみを保存します。出力先directoryは事前に用意し、入力・出力には試行ごとに新しいfile名を使います。既存fileを上書きするため、以前の結果のpathを再利用しないでください。出力fileは新規作成時0600です。入力・出力をGit管理しないでください。

個別の難例確認にはhighが候補です。日本語の流暢さだけではlow/highの明確な優劣を確認していません。件数・時間・停止条件を決めて手動で開始します。自動retryはなく、1回のtimeoutは最大180秒です。token上限とtemperatureは指定しません。modelの判断だけで結果を自動採用せず、本文と根拠を確認します。

### 2件の逐次・並列実行

`batch`は必ず2件を受け取り、`parallel`を1または2にします。`id`は重複しない英数字・`_`・`-`です。modelの応答本文はJSONである必要があります。

```json
{"parallel":2,"items":[{"id":"A","model":"gpt-6-luna","reasoningEffort":"high","prompt":"資料Aを確認し、JSONだけで回答してください"},{"id":"B","model":"gpt-6-luna","reasoningEffort":"high","prompt":"資料Bを確認し、JSONだけで回答してください"}]}
```

```sh
extras/luna-client/luna batch .tmp/luna-manual/batch-result.json .tmp/luna-manual/batch-request.json
```

各結果を`OUTPUT.json.ID.json`へ独立して保存してから集約します。並列では片方が失敗しても、開始済みの他方の保存を待ちます。逐次では最初が失敗したら2件目を開始しません。JSON構文エラー時は本文を保存せず、hash・byte数・構文エラー位置等の診断だけを残します。集約の`status`と各fileを確認してください。batchの終了codeが0でも、各生成が成功したとは限りません。

## SDK・認証の準備と所在

[公式Sign in with ChatGPT DevKit](https://github.com/openai/sign-in-with-chatgpt-devkit)のrevision `0a36fefeb913055c8c7a1a29b63d82b96b2e841a`を利用します。[SDK拡張patch](devkit-extension.patch)はreasoning要求と完了metadata取得だけを追加します。HTTP・OAuth・refresh・stream解釈は公式SDKに任せます。テキストの入出力だけを扱い、[公式のmodels/inference経路](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)を使います。Codexの認証情報を流用しません。

| 環境変数 | 既定値・内容 |
| --- | --- |
| `LUNA_STATE_DIR` | `$HOME/Library/Application Support/atproto-survey/luna-client`。`credentials/`と`electron-user-data/`を保持 |
| `LUNA_SDK_DIR` | `$LUNA_STATE_DIR/devkit`。patch適用・build済みの公式SDKとlocked dependencies |

このMacでは既存の保全先をそのまま使えます。認可済みアプリ名`atproto survey Luna evaluation`とapp ID `atproto-survey-luna-evaluation`は変更しません。認証は公式サンプル由来の[暗号化アダプタ](credential-encryption.mjs)で保存し、macOSのKeychainと実行環境に依存します。認証fileをGitや別の資料保存先へ複製しません。別Macでは再認可が必要になり得ます。

### 新しい環境を準備する場合

macOS、Node.js 22.15以上、npm、Gitが必要です。以下はネットワークから固定revisionとlocked dependenciesを取得し、Git外にSDKを準備します。既存環境では繰り返す必要はありません。空の保存先に対してリポジトリのルートから実行します。

```sh
LUNA_SOURCE_DIR="$PWD/extras/luna-client"
LUNA_STATE_DIR="$HOME/Library/Application Support/atproto-survey/luna-client"
export LUNA_STATE_DIR
mkdir -p "$LUNA_STATE_DIR"
chmod 700 "$LUNA_STATE_DIR"
git clone https://github.com/openai/sign-in-with-chatgpt-devkit.git "$LUNA_STATE_DIR/devkit"
git -C "$LUNA_STATE_DIR/devkit" checkout --detach 0a36fefeb913055c8c7a1a29b63d82b96b2e841a
git -C "$LUNA_STATE_DIR/devkit" apply "$LUNA_SOURCE_DIR/devkit-extension.patch"
cd "$LUNA_STATE_DIR/devkit"
npm ci
npm run build -w @siwc/local
cd "$LUNA_SOURCE_DIR"
node --test *.test.mjs
mkdir -p "$LUNA_STATE_DIR/manual-output"
./luna authorize "$LUNA_STATE_DIR/manual-output/authorization.json"
./luna catalog "$LUNA_STATE_DIR/manual-output/models.json"
```

`authorize`は初回または認可が切れた時にだけ実行し、開いたブラウザで認可します。認証directoryは0700、credential fileは0600です。clientはアカウントの追加クレジット許可・利用上限を変更しません。契約内の利用可否、modelの提供状態、現在の利用設定は再開時に確認します。

## 検証

```sh
node --test extras/luna-client/*.test.mjs
node --check extras/luna-client/client.mjs
sh -n extras/luna-client/luna
```

testは通信・実LLM生成・既存認証の読み書きを行いません。`sdk-extension.test.mjs`は準備済みSDKをimportし、HTTP応答を固定してrequestと完了metadataを確認します。SDK全体のtestはSDK側で`npm test`を実行します。本体の全unit testは従来どおり`bin/project-registry-test`です。

既存SDKと同じrevision・lockfileによるbuildは試用時に確認しました。今回のGitへの整理では既存SDKを利用し、別Macでの新規導入・認可や長期backupの復旧は確認していません。

## 2026-10-09の試用結果

当面は手動の補助利用に留めます。以下は次の利用判断に必要な観測と限界で、追加評価の残件一覧ではありません。

| 用途 | 観測と次の扱い |
| --- | --- |
| project triage | lowの追加12事例では形式検査を通っても、公開ATProtoレコードがある対象の誤除外、通常PDS保存のhigh評価、説明保持を主張しながら書き換える例が残った。既定modelの置換は見送る |
| 出力の再診断 | 3軸の診断では誤診の追認と正常例への誤警告があり、単一主張へ絞るだけでも解消しなかった。同じ3本文をlow/high各2回試すと、highは根拠の見落としと正常なnotable評価を2事例で適切に扱ったが、残る技術評価はhighでも揺れた。highによる個別確認は候補とし、supported等を自動採用の条件にしない |
| 日本語生成 | 短い原資料からtagline・summary・featuresを生成し、設定を伏せた3ペアではlow/highとも自然な日本語で、highの明確な総合優位はなかった。highでも重要な過去のPDS設計を省く例があり、流暢さと情報の選択を分けて確認する |
| 2並列 | 同じ短い2入力をhighで逐次・2並列各2セット、計8回実行し、全て完了。2件を処理する起動込み平均は逐次21.75秒、2並列10.11秒で、両並列セットの出力streamも重なった。応答長・reasoning量も異なるため一般的な性能比や長期安定性の保証にはしない |

選抜した過去実例・既存合成素材の評価で、人間goldや代表的な精度評価ではありません。形式・参照IDの検査、生出力、通常の適用関数による説明保護は区別します。説明保持と参照IDの存在を機械で点検する作業用検査は試しましたが、本番への新しい検査や診断経路は組み込んでいません。日本語生成の比較は各条件1回で、反復安定性と通常triage全体での文章品質は未確認です。

逐次試行でもstream中断とHTTP 503が各1件ありました。2並列の実API試験では失敗がなく、部分失敗時の兄弟結果保持はオフラインtestだけで確認しています。今後の手動利用も件数・時間・停止条件を限定し、認証・利用枠・通信の失敗を区別します。ローカルQwenのKV cache負荷を理由にLunaの並列を制限する必要はありませんが、長期連続実行や同時認証更新は未検証です。

repository解析の現行契約、通常enrich、DB保存への接続は未検証です。

## 保持・廃止

固定入力・生出力・段階別report・過去の試行スクリプトは非公開資料として別途保全済みです。その所在と保持条件は非公開の運用記録で管理します。古いworkspace依存の試行スクリプトや完了済みledgerを直接再実行せず、新しい試行はこのclientと新しいrequest/output fileで行います。

必要時の補助利用を続ける間はSDK・認証・必要な評価資料を保持します。worktreeや一時fileの清掃とは分けて扱います。廃止する時はアプリ認可、Keychain、SDK、評価資料をそれぞれ確認して処置します。通常運用へ接続する場合は、その時の解析契約・保存経路を別途検証し、正本DBへの反映は承認を得ます。

## ライセンス

公式SDK由来の`credential-encryption.mjs`と、SDKの変更を含む`devkit-extension.patch`には[Sign-in with ChatGPT DevKit Noncommercial License v1.0](LICENSE.devkit)を適用します。SDKは同梱せず、取得したSDK内のLICENSE・NOTICE・依存物の権利表記を保持します。それ以外の独立して作成したclient・test・文書はリポジトリの[MIT License](../../LICENSE)に従います。
