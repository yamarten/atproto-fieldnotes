# FirefoxからProject Registryへ記録

Firefoxのツールバーボタンから、見ているサービスやリポジトリを自分のProject Registry Web UIで探す拡張です。登録状況の確認、候補の採用、理由付きの保留、紹介予定の取り置きへ進めます。拡張内にDB・認証・保存処理を持たず、Web UIを別タブに開きます。元のサイトのタブは残ります。

対象はデスクトップ版Firefox 140以降です。Androidでの動作は検証対象に含めていません。Web UI側の入口・操作は[Web UIガイド](../../docs/user/web-ui.md#サイトから記録)を参照してください。

## 接続先と渡す情報

初回にボタンを押すと接続先の設定が開きます。普段ログインしているWeb UIのルートURLを保存し、元のサービスのタブへ戻ってボタンを押します。接続先はブラウザープロファイルごとの`storage.local`に保持し、他端末へ同期しません。個人環境の接続先を配布ファイルへ書き込む必要はありません。

APIトークンは拡張へ保存せず、Web UIの通常のログインを使います。接続先のURLには認証情報・クエリ・フラグメント・ルート以外のパスを指定できません。Tailscale等のHTTPS入口を使う場合は、そのoriginでWeb UIへログインしてください。

`activeTab`でボタンを押したタブのURLを読み、クエリ文字列とフラグメントを除いて`?screen=browser&url=...`へ渡します。HTTP(S)以外や認証情報を含むURLは渡しません。本文、OAuthスコープ、入力内容、Cookie、他のタブや閲覧履歴は読み取りません。拡張自身の閲覧URL保存、バックグラウンド送信、解析・広告・外部収集先はありません。開いたWeb UIのURLは通常のブラウザー履歴に残り、Web UIのserverへ届きます。

権限は`activeTab`と`storage`だけで、host permissionsは要求しません。URLを利用者指定のserverへ渡す用途のため、Firefoxのデータ送信申告では`browsingActivity`を必須としています。通常のタブアクセス権限と、インストール時のデータ送信への同意は別です。[Mozillaのデータ送信申告](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/)に従っています。

## 開発と検証

JavaScriptの構文と、ボタンクリック時のURL受け渡し・設定不足・不正URLの扱いは、リポジトリのunit testで確認します。Firefox APIはこのtestでは固定した応答を使うため、Firefox本体での実行を保証しません。

```sh
bin/project-registry-test -p test_browser_feedback.py
node --check extras/firefox-registry/entry.js
node --check extras/firefox-registry/background.js
node --check extras/firefox-registry/options.js
```

Web UIの手動確認には[共通ダミーDBと確認用server](../../docs/developer/workflows/change-acceptance.md#cliwebの共通ダミーdb)を使います。拡張を開発用Firefoxへ一時ロードする場合は、`about:debugging`の「このFirefox」からこのdirectoryの`manifest.json`を選びます。一時ロードはFirefox再起動後の継続利用や更新を検証するものではありません。

## 継続導入と更新

通常版Firefoxへの継続導入にはMozillaの署名が必要です。個人用ではAMOの公開一覧に載せないself-distribution（unlisted）を使えます。提出にはMozillaアカウントと規約への同意が必要で、署名時・事後の審査対象になります。署名・提出・インストールは、このリポジトリのbuildやtestでは行いません。[Mozillaの署名と配布](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/)を参照してください。

提出用ZIPは標準Pythonだけで生成します。ソースを変更せず、固定した7ファイルだけをZIPのルートへ入れ、既定ではGit管理外の`.tmp/firefox-registry/`へ保存します。このZIPは未署名で、通常版Firefoxへ継続導入する成果物ではありません。

```sh
bin/_project-registry-python extension-build extras/firefox-registry/build.py
```

AMO Developer Hubでself-distributionを選び、ZIPを提出して署名済みXPIを取得します。Firefoxのアドオンマネージャーの「ファイルからアドオンをインストール」からXPIを導入し、接続先を設定します。`web-ext`やAPIによる署名を使う場合は、そのツールと認証情報を別途準備します。

初版には自動更新の配信先を設けていません。更新時はmanifestのversionを上げ、同じadd-on IDで再署名したXPIをインストールします。個人設定は同じIDの更新で引き継ぎ、拡張の削除・再インストールでは保持を前提にしません。自動更新を追加する場合は`update_url`と更新manifest・署名済みXPIの継続配信先を用意する別の運用判断が必要です。[Mozillaの自己配布手順](https://extensionworkshop.com/documentation/publish/self-distribution/)に従います。

継続導入の受入では、署名済みXPIの通常インストール、Firefox再起動後のボタンと接続先、Web UI認証、登録確認・候補選択・登録・保留・紹介予定の回収、同じIDの次versionへの更新を確認します。この確認が済むまでは、unit testや一時ロードだけで継続利用・更新を確認済みと扱いません。
