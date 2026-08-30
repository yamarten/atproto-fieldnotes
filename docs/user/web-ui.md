# Project Registry Web UIガイド

`project-registry-web`はschema version 3のSQLite DBを操作する、1人の利用者向けのローカルWeb UIです。日常的な登録、調査、候補整理、登録プロジェクトの修正はWeb UIを主導線とし、低水準の監査や保守は[CLIガイド](cli.md)へ分けています。基本用語は[利用ガイド](guide.md#基本用語)を参照してください。

## 起動

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

| オプション | 意味 |
| --- | --- |
| `--host` | bind先。既定はloopback。 |
| `--port` | ポート。既定は`8765`。 |
| `--db-path` | 接続するschema v3 SQLite DB。既定は`data/survey.db`。 |
| `--token` | API共有token。`ATPROTO_SURVEY_WEB_TOKEN`でも指定できる。 |
| `--reload` | Web実装変更時にサーバープロセスを再起動する。 |
| `--reload-interval` | reload確認間隔。 |

別DBは先にCLIで初期化します。

```bash
project-registry init-db --db-path data/survey.dev.db
project-registry-web --db-path data/survey.dev.db --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

### macOSでの自動起動

`project-registry-web-service`は、現在のcheckoutを参照する利用者用LaunchAgentを`~/Library/LaunchAgents/`へ生成します。ログイン時に実運用設定の`0.0.0.0:8888`で起動し、異常終了時は30秒以上の間隔を置いて再起動します。同じMacからの状態確認と管理には`http://127.0.0.1:8888/`を使います。Web serverが起動するworkerと定期実行も同じprocess環境を引き継ぎます。

導入前に`direnv`をinstall・allowし、`.envrc`へ`ATPROTO_SURVEY_WEB_TOKEN`と必要なLLM設定を置きます。LaunchAgentは対話shellを読まないため、`direnv exec`経由でこの環境を読みます。秘密を含む`.envrc`は他の利用者から読めないpermissionにしてください。

```bash
chmod 600 .envrc
project-registry-web-service install
project-registry-web-service status
```

| コマンド | 動作 |
| --- | --- |
| `project-registry-web-service install` | plistを生成または置換して起動。正本DBの存在とschema、direnv、tokenを事前確認 |
| `project-registry-web-service status` | launchdのload・process状態、bind先とloopbackからの応答を表示 |
| `project-registry-web-service restart` | 認証済みreload APIでworkerを停止してから同じ引数で再起動 |
| `project-registry-web-service uninstall` | processを停止してplistを削除。エラーログは保持 |

plistにはtokenやAPI keyを保存しません。token入り起動URLが出る標準出力は保存せず、例外等の標準エラーだけを`~/Library/Logs/atproto-survey/project-registry-web.err.log`へ保存します。同じMacでは`http://127.0.0.1:8888/`、LAN内の別端末ではMacの管理下のaddressを開き、originごとにSettingsへtokenを入力します。tokenは従来どおりbrowserのlocalStorageに保存されます。

LaunchAgentはcheckoutとDBを絶対パスで参照します。リポジトリを移動した場合は旧checkoutで`uninstall`してから新しい場所で`install`し直してください。通常起動には`--reload`を付けません。コード更新後はSettingsの`Reload server`または`project-registry-web-service restart`を使います。

## 画面構成

| 画面 | 主な用途 | DB変更 |
| --- | --- | --- |
| `Review` | 要レビュー一覧、紹介候補一覧、任意の組み合わせを含むマージ候補の比較 | 操作を確認した時だけ書き込み。skipは書き込まない |
| `Projects` | 登録プロジェクトの検索・詳細表示、シードのプレビュー・追加 | 検索は読み取り専用。明示した編集・操作だけ書き込み |
| `Operations` | `enrich`、外部情報の取り込み（`sync`）、追跡状態の見直し、定期実行設定 | dry-runとexecuteを画面で区別 |
| `Settings` | 一覧件数、マージ方法、表示time zone、token、サーバー・DB情報 | UI設定はlocalStorage。サーバーのreloadはDBを変更しない |

検索条件、選択中の登録プロジェクト、詳細タブはURLに保存され、reload、back、forwardで復元します。tokenとUI設定はURLへ残しません。

## Projects

`Add seed`はNSID、リポジトリURL、サービスURLをプレビューしてから登録します。自動判定を上書きする場合は`repo_url: https://forge.example/dev/app`のように入力します。既存の登録プロジェクトの詳細画面から追加した値は、その登録プロジェクトの所有NSID、サービス、ソースリポジトリとして採用され、採用判断と入力元を別々に保存します。同じ値を再適用して旧データの不足を補えます。

サービスURLのパスは保持します。`https://example.com/tool-a`と`https://example.com/tool-b`を別プロジェクトの入口として登録できます。operatorが明示したservice URLには、path名による補助ページ判定を適用しません。同じURLのblock/deferは最新の明示登録で解除し、親URLに対するguardは残します。

フィルターで次を絞り込めます。

- 追跡状態、サービス状態、atprotoへの寄与度、notes・introduced・候補・エラーの有無
- 識別子の種類、値、分類
- 関係の種類、subject/objectの種類と値、情報源
- 手動判断の種類、対象の種類・値、有効・期限切れ・解除済み

追跡状態、サービス状態、atprotoへの寄与度は複数選択です。種類、分類、関係、subject/objectの種類、手動判断・対象の種類は選択肢から指定します。識別子・関係・手動判断の値は部分一致、関係の情報源は候補付き完全一致です。`Notes`の有無はprofileの利用者向け`notes`だけを対象とし、手動判断に付けた理由メモや`system_notes`は含みません。未選択のフィルターは絞り込みません。

登録プロジェクトの詳細画面は次のタブに分かれます。

| タブ | 表示内容 |
| --- | --- |
| `Overview` | profile、代表websiteとconfirmed service一覧、用途別のLinks / Resources、サービス・所有関係、保存済み結論のExplain、マージ候補、notes |
| `Identifiers` | 分類とrole付きの識別子、候補への操作 |
| `Relations` | 種類付きの関係根拠、除外・保留操作 |
| `Investigation` | 最新の調査実行記録、収集結果・失敗、履歴、保存済みの根拠、再評価 |
| `Decisions` | 有効な制御と手動判断の履歴 |
| `Raw` | schema v2 APIが返した登録プロジェクトのデータ |

`Overview`のExplainは保存済み状態を表示し、ネットワークやLLMを使いません。全登録プロジェクトとの比較が必要なマージ候補は詳細本体の後に別リクエストで計算し、DBへ保存しません。保存データがない項目は`not_observed`であり、否定結果ではありません。

`Identifiers`に表示するDIDとhandleは、`https://pds.ls/at://...`形式のアカウント参照へリンクします。表示中の識別子自体は変更しません。

`Links / Resources`は、利用・インストール先、プロジェクト情報、補足情報、その他の順にまとめます。App Store、Google Play、F-Droid、配布ページは利用先として先に表示し、changelogやWeb Manifestは補足情報として分けます。未知のroleも削除せず`Other`に表示します。

詳細画面から次を実行できます。

- 単体`enrich`、追跡状態の変更、シードの追加・再適用
- 識別子候補の採用・除外、識別子の再付着防止・保留、関係の除外・保留
- 紹介候補の期限付き保留、候補の優先度を下げる手動判断、分割、別プロジェクト指定の解除、マージ候補の比較
- profileの`name`、`introduced_url`、利用者向け`notes`のプレビュー・保存

変更は確認後にSQLiteへ保存します。profile編集はプレビュー後に登録プロジェクトが別プロセスで変わっていた場合、保存を拒否して再読込を促します。`system_notes`は機械生成履歴で、profile編集の対象ではありません。Overviewでは件数だけを表示し、履歴は必要な時に展開できます。

`Overview`のService / Ownershipでは、保存済みのconfirmed/candidate service URLを選び、匿名browser確認を
background jobとして開始できます。任意URL、認証情報、form入力は受け付けません。実行結果は確認後にSQLiteへ保存され、
service statusと根拠へ再投影されます。Playwright browser依存が必要です。

識別子の再付着防止・保留・分割は、同じ値のrole単位ではなく、画面に示す識別子全体へ作用します。保留解除は自動採用ではありません。

## Review

`Review Queue`は要レビュー項目、`Promotion`は紹介候補、`Merge`は重複候補または直接入力した任意の組み合わせを表示します。`Review Queue`の`Signal kind`へsignal名を入力すると、その種類だけに絞り込めます。適用中の条件はURLへ保持されるため、再読込やURLの共有後も復元されます。`Promotion`の候補カードでは、サービスとして利用できる画面を確認できたか、追加確認が必要な理由、紹介候補の並び順に使う点数と内訳を、詳細画面を開かず確認できます。

弱い候補には、優先度を下げる手動判断（画面では`Lower priority`）を保存できます。理由メモは任意ですが、具体的な扱いを残したい場合に記録できます。対象は紹介候補一覧に残りますが、`Refresh`後は通常候補より後ろへ並びます。期限付き保留は紹介候補一覧から一時的に外す別の操作です。

通常の確認操作や優先度を下げる判断を保存すると、表示中のカードだけを一覧から外して次へ進みます。一覧全体を自動再計算しないため、最新候補と件数を取り直す時は`Refresh`を使います。`Skip for now`はDBへ何も書かず、その表示中だけ候補を先送りします。

マージ候補の比較では、マージ元からマージ先へまとめる向きで、両方の登録プロジェクトのprofile、状態、シード、識別子、候補となった理由、マージ結果のプレビューを比較します。マージ先のプロジェクトIDと同優先度の値が残るため、`Reverse source and target`で向きを入れ替えてプレビューできます。`Keep separate`は2件を別の登録プロジェクトとして扱う手動判断を保存します。`Merge projects`は確認後にマージします。統合後の登録プロジェクトに別のマージ候補が残る場合は、一覧全体を再計算せず、その次候補の比較へ進みます。候補一覧にない組み合わせもプロジェクトキーを直接指定して比較できます。

PSLにないmulti-tenant suffixが原因らしい組み合わせでは、接尾辞候補を表示し、その場で`domain-overrides.json`へ追加できます。接尾辞の追加は今後のドメイン判定を変えるため、プレビュー内容を確認してください。

## Operations

バックグラウンドジョブはWebサーバーとは別プロセスで動き、同じWebサーバーでは同時に1件だけ実行できます。CLIなど別プロセスとの排他は行いません。

### Enrich

単体または一括実行を選び、詳細調査、未`enrich`のみ、前回失敗分のみ、追加の追跡状態を指定できます。一括`enrich`は登録プロジェクト間で停止し、チェックポイントから再開できます。1件の登録プロジェクトの失敗は記録して後続を処理し、一括実行全体を`failed`として再開可能にします。

### Sync（外部情報の取り込み）

設定済みのintroduced、GitHub、collection一覧、Semble、Tangledから外部情報を取り込みます。任意の検索条件やURLはCLIで指定します。dry-runでは取り込み候補を確認でき、外部ネットワークへ接続しますがSQLiteは更新しません。停止要求は現在の情報源のページ・項目境界で反映され、チェックポイントからの再開はありません。

TangledはJetstream v2 snapshotを使います。Web server環境に`ATPROTO_SURVEY_JETSTREAM_API_KEY`とNode.js 22.15以上、
workspaceに`npm install`済みの公式SDKが必要です。API keyはUI、job設定、結果へ保存しません。

### Review Ops

保存済みの登録プロジェクト情報から、`stalled`・`stable`、調査範囲外、追跡対象外への変更候補を再計算します。適用内容の作成はネットワークを使いません。dry-runで候補を確認し、execute時は表示対象を一括適用します。

### Scheduled Presets

許可済みの定期実行設定だけを手動または定期的に開始できます。任意のコマンドは実行できません。設定できるのは通常の一括`enrich`、全追跡状態の見直し、introduced情報の取り込み、その他の外部情報を取り込んだ後に行う新規登録分だけの`enrich`です。全追跡状態の見直しに続くstalled reviewは、候補件数で打ち切らず全候補を反映します。

各設定は有効・無効、毎週または月の第N週、曜日、時刻、time zoneを持ちます。設定と実行待ち一覧はDBと同じディレクトリの`job-schedules.json`へ保存します。予定から24時間を超えた未実行分は後から実行しません。サーバー再起動時に実行中だった段階は完了扱いにせず、同じ段階から再試行します。

### ジョブ履歴と再起動

ジョブ一覧とイベントはサーバープロセス内だけに保持され、サーバー再起動後は復元されません。一括`enrich`の未完了チェックポイントは`Discovered Checkpoints`から再発見・再開できます。外部情報の取り込みと追跡状態の見直しにはチェックポイントからの再開がありません。

Settingsの`Reload server`はWebプロセスを再起動し、SQLiteは変更しません。実行中のジョブへ協調停止を要求し、タイムアウトを超えたworkerは終了してから再起動します。ブラウザーは新プロセスの応答後に自動再読込します。LaunchAgentからの停止時も同じworker終了処理を行います。

## Settings

- 一覧・登録プロジェクト・マージ候補の件数上限、マージ方法、personal toolの包含
- 表示time zone。既定`Asia/Tokyo`、IANA名または`+09:00`形式
- API tokenの保存・削除
- 接続DB、Python version、起動時Git commit/dirty状態
- サーバーのreload

UI設定とtokenは同じoriginの`localStorage`に保存します。表示time zoneを変えてもDB timestampは変わりません。Git状態はサーバー起動時に一度取得するため、checkout変更後はreloadが必要です。

## アクセス境界

Web UIは1人の利用者が使う前提です。手動起動の既定は`127.0.0.1`ですが、実運用のLaunchAgentは管理下のLANから使うため`0.0.0.0:8888`へbindします。公開サーバー、共有端末、複数利用者向けのアクセス制御はありません。

- 起動時の初回URLだけqueryにtokenを含み、UIは読取直後にURLから除去します。ただしターミナル、ブラウザー拡張、初回navigationを観測できる相手からtokenを守る仕組みではありません。
- tokenは同じoriginの`localStorage`へ保存されます。同じブラウザープロファイル・originを使えるスクリプトや利用者とは秘密を分離できません。不要になったらSettingsの`Clear API token`で削除してください。
- サーバーはplain HTTPで、TLS終端やtoken rotationを提供しません。LaunchAgentを使うLANと端末は管理下に限定し、internetへport 8888を転送しないでください。
- 信頼できないnetworkを経由する場合やLAN外から使う場合は、LaunchAgentのbind設定をloopbackへ戻し、管理下のSSH tunnelまたはTLS付きreverse proxyを前段に置きます。

APIリクエストは共有token必須です。起動時URLのbootstrap token、`Authorization: Bearer ...`、`X-Project-Registry-Token`のいずれかを使います。静的assetはtokenなしで配信します。

## 現在未接続のもの

- サーバー再起動後のプロセス内ジョブ履歴復元。一括`enrich`チェックポイントの再発見は接続済み。
- Web UIからのLLM接続設定。
- Web UIからのサーバー終了。reloadは接続済み。
