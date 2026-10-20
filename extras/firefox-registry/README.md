# Project Registry（Firefox拡張）

Firefoxのツールバーボタンから、見ているサービスやリポジトリを自分のProject Registry Web UIで探す拡張です。登録状況の確認、候補の採用、理由付きの保留、紹介予定の取り置きへ進めます。拡張内にDB・認証・保存処理を持たず、Web UIへ接続します。元のサイトのタブは残ります。

登録有無はポップアップで確認できます。既定の開き方はポップアップで、接続先設定の`開き方`を`タブ`にすると別タブ表示へ切り替えられます。

対象はデスクトップ版Firefox 140以降です。Androidでの動作は検証対象に含めていません。Web UI側の入口・操作は[Web UIガイド](../../docs/user/web-ui.md#サイトから記録)を参照してください。

## ポップアップで確認する

小窓にはWeb UIをiframeで埋め込みます。登録済みプロジェクトでは`紹介予定に入れる`から理由・メモを添えて保存し、理由の編集・解除も小窓内で行えます。`シードを追加`は入力フォームで種類・値・追加先をプレビューし、確認して保存します。別プロジェクトに登録済みの値は追加せず、そのプロジェクトを開けます。

未登録のURLは`このURLで登録`からプレビューし、`この内容で登録`で保存します。登録後の詳細から、必要なら`紹介予定に入れる`を押します。登録だけで紹介予定へ自動追加はしません。保存中・成功・失敗は小窓内に表示し、更新競合の場合は詳細を再読込して再操作を案内します。

選択したプロジェクト、詳細の表示タブ、サイトURL、検索条件は上部の`タブで開く`へ引き継ぎます。追跡状態の変更・調査・紹介候補の保留など詳細上部のほかの操作はタブ表示で行います。未保存のフォーム入力は引き継がず、外をクリックすると小窓は閉じます。閉じた後に再び開くと、元のサイトURLから検索し直します。

小窓の初期サイズは480×590pxです。上部の`横幅`スライダーで360〜780px（20px刻み）に調整でき、選んだ幅はブラウザープロファイルごとの`storage.local`に保存します。幅の変更で埋め込みページを再読込しないため、検索や選択はそのまま続けられます。高さは590pxです。Web UI側の狭い画面向け表示を使い、一覧から詳細へ切り替えます。iframeが読み込めない場合も`タブで開く`を使えます。外部の認証画面を埋め込む機能ではありません。

通常タブとiframeでlocalStorageが分かれる場合、小窓にもAPIトークンの入力フォームが表示されます。トークンは接続先Web UIのlocalStorageだけに保存し、拡張のstorageやタブへ渡すメッセージには含めません。通常タブでも必要なら従来どおりログインします。小窓の表示・認証状態共有はFirefoxのプライバシー設定によって異なるため、一時ロードで実際の環境を確認してください。

## 接続先と渡す情報

初回は小窓の`設定`から、普段使うWeb UIのルートURLを保存し、元のサービスのタブへ戻ってボタンを押します。タブ表示を選んでいる場合は、接続先未設定時に設定が直接開きます。接続先・開き方・小窓の横幅はブラウザープロファイルごとの`storage.local`に保持し、他端末へ同期しません。個人環境の接続先を配布ファイルへ書き込む必要はありません。

APIトークンは拡張へ保存せず、Web UIの通常のログインを使います。接続先のURLには認証情報・クエリ・フラグメント・ルート以外のパスを指定できません。Tailscale等のHTTPS入口を使う場合は、そのoriginでWeb UIへログインしてください。

`activeTab`でボタンを押したタブのURLを読み、クエリ文字列とフラグメントを除いて`?screen=browser&url=...`へ渡します。HTTP(S)以外や認証情報を含むURLは渡しません。本文、OAuthスコープ、入力内容、Cookie、他のタブや閲覧履歴は読み取りません。拡張自身の閲覧URL保存、バックグラウンド送信、解析・広告・外部収集先はありません。開いたWeb UIのURLは通常のブラウザー履歴に残り、Web UIのserverへ届きます。

権限は`activeTab`と`storage`だけで、host permissionsは要求しません。URLを利用者指定のserverへ渡す用途のため、Firefoxのデータ送信申告では`browsingActivity`を必須としています。通常のタブアクセス権限と、インストール時のデータ送信への同意は別です。[Mozillaのデータ送信申告](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/)に従っています。

## 開発と検証

JavaScriptの構文と、ボタンクリック時のURL受け渡し・設定不足・不正URLの扱いは、リポジトリのunit testで確認します。Firefox APIはこのtestでは固定した応答を使うため、Firefox本体での実行を保証しません。

```sh
bin/project-registry-test -p test_browser_feedback.py
node --check extras/firefox-registry/entry.js
node --check extras/firefox-registry/background.js
node --check extras/firefox-registry/popup.js
node --check extras/firefox-registry/options.js
```

Web UIの手動確認には[共通ダミーDBと確認用server](../../docs/developer/workflows/change-acceptance.md#cliwebの共通ダミーdb)を使います。拡張を開発用Firefoxへ一時ロードする場合は、`about:debugging`の「このFirefox」からこのdirectoryの`manifest.json`を選びます。一時ロードはFirefox再起動後の継続利用や更新を検証するものではありません。

既に一時ロード済みなら`about:debugging#/runtime/this-firefox`のこの拡張の`再読み込み`を押します。Firefoxを終了する必要はありません。小窓の試用では、登録済みURLの自動選択、domainのNSID候補一覧、未一致、検索して選んだプロジェクトの`タブで開く`、小窓の初回トークン入力、閉じて再表示、設定の`タブ`への切替を確認します。編集では、紹介予定の保存・理由編集・解除、未登録URLの登録から紹介予定への追加、既存プロジェクトへのシード追加・再適用・重複拒否、保存失敗・更新競合の表示を確認します。Web UIの静的ファイルだけの更新なら、小窓を開き直すと反映され、拡張の再署名と確認用serverの再起動は不要です。

幅調整の手動確認では、スライダーを動かした際のFirefox本体の小窓サイズ、検索・選択の維持、閉じて再表示した際の幅の復元を確認します。

## 継続導入と更新

通常版Firefoxへの継続導入にはMozillaの署名が必要です。個人用ではAMOの公開一覧に載せないself-distribution（unlisted）を使えます。提出にはMozillaアカウントと規約への同意が必要で、署名時・事後の審査対象になります。署名・提出・インストールは、このリポジトリのbuildやtestでは行いません。[Mozillaの署名と配布](https://extensionworkshop.com/documentation/publish/signing-and-distribution-overview/)を参照してください。

提出用ZIPは標準Pythonだけで生成します。ソースを変更せず、固定した10ファイルだけをZIPのルートへ入れ、既定ではGit管理外の`.tmp/firefox-registry/`へ保存します。このZIPは未署名で、通常版Firefoxへ継続導入する成果物ではありません。

```sh
bin/_project-registry-python extension-build extras/firefox-registry/build.py
```

AMO Developer Hubでself-distributionを選び、ZIPを提出して署名済みXPIを取得します。Firefoxのアドオンマネージャーの「ファイルからアドオンをインストール」からXPIを導入し、接続先を設定します。`web-ext`やAPIによる署名を使う場合は、そのツールと認証情報を別途準備します。

初版には自動更新の配信先を設けていません。更新時はmanifestのversionを上げ、同じadd-on IDで再署名したXPIをインストールします。個人設定は同じIDの更新で引き継ぎ、拡張の削除・再インストールでは保持を前提にしません。自動更新を追加する場合は`update_url`と更新manifest・署名済みXPIの継続配信先を用意する別の運用判断が必要です。[Mozillaの自己配布手順](https://extensionworkshop.com/documentation/publish/self-distribution/)に従います。

継続導入の受入では、署名済みXPIの通常インストール、Firefox再起動後のボタンと接続先、Web UI認証、登録確認・候補選択・登録・保留・紹介予定の回収、同じIDの次versionへの更新を確認します。この確認が済むまでは、unit testや一時ロードだけで継続利用・更新を確認済みと扱いません。
