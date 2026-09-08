# Homepage観測経路の接続とrole判定の検討

作業・比較記録。現在の取得・保存契約は[investigation-model](../developer/investigation-model.md)を正本とする。

## 判断

新しいLLM role classifierを追加する根拠は得られなかった。既存の「構造化観測→規則による判定」を維持し、
repoのhomepageをパス付きで観測する入口を接続した。ページの取得、projectへの帰属、service / project siteの
役割、代表Websiteの選択は別の判断として扱う。project siteを文書だけに限定しない。

この設計は以前からの方向性と一致する。関連履歴は`be4412d`（public browser surface）、
`8610835`（current service observation projection）、`7e659af`（repository evidence contract）。
同じ設計を新方式として提案し直す必要はない。

## LLMと既存規則の確認

保存済み7ページをQwen `qwen/qwen3.6-35b-a3b`へ各2回入力した。JSON成功14/14、roleの再現は6/7。
これは正解率ではない。HTML抜粋とリンクからの試行であり、roleの分類とservice statusの分類も同一ではない。

- at.fund: LLMは2回ともproject siteと回答。しかし保存HTMLにはdialog内のform、handle入力、Sign inボタンがある。
  当初の「positiveなUI材料がない」という説明は誤り。表示・利用可能性までは未確認。
- idunno.bluesky: 既存static規則は`agent.Login()`のコード例をloginとして検出する。実UIの検出ではない。
- Matadisco: 入力HTMLの先頭がCSSで占められ、UIがないとは判断できない。
- 個人siteのrole enumは2回で変化した。LLMの「公式」という説明も単独では帰属の根拠にならない。

LLMは文脈を読める場合もあるが、既に取得した構造を見落とす場合もある。既存browserのforms、auth actions、
service actionsの観測を使う方向を維持する。今回browserのlive検証やstatic規則の変更は行っていない。

## 今回の実装と検証

- confirmed homepage relationは元のURLのまま保存し、service候補への自動変換を撤去した。
- 現在採用済みrepoとguardを確認し、docsを含むWebページを通常enrichの取得対象へ追加した。
- 未判定ページは`repository_homepage` laneで観測する。取得・相互repoリンクだけでserviceのroleや状態を上げない。
  既にexact URLがserviceと確認済みのページは既存static経路を使う。
- 保存とreplayをtemp DBで確認。既知のGitHub Pages / fieldworkのパス、about、docs、guard、
  非採用repo、失敗後のrelation保持、取得重複防止を回帰テストに追加した。
- 比較時の7ページの保存済み抜粋でもrequested/final URLが7/7保持され、未判定service採用は0件。
  この確認は役割の正解判定でも、新規live取得の成功確認でもない。
- 全813 unit test成功。正本DBへの書き込み・再enrichは未実施。

試行入力・結果は`.tmp/web-role-evaluation/`、全testログは`.tmp/homepage-tests.log`に置いた一時生成物。

## 残る検討

未判定ページをservice / project siteへ採用する判定、observed redirect先と既存componentの照合による採用、
未判定URLのbrowser対象への自動追加は未接続。CLIの既存`verify-service --url ... --lane browser`では
URLを明示して確認できるが、自動採用方針の決定を代替するものではない。
観測入口の接続だけで、手動判断削減という本来のゴールを完了としない。

## Browser観測の追加検証と修正

既存5件のbrowser calibrationは再現した。一方、最小再現例では紹介ページから別URLのloginへ進むと、
開始URLに対する集約結果が`live`になる。採用済みserviceがなくてもbrowser観測からprojectの稼働状態は付くが、
これだけでservice identifierが採用されるわけではない。既存statusをrole判定へ直接転用してはいけない。

ページごとの要求／到達URL、表示状態、表示された材料だけによるsurface特徴をcollectorへ追加した。
閉じたdialog、コード例、非表示input/submit/OAuth、表示されたform/OAuth、`display: contents` formの8ケースを
実Chromiumへ検証用HTMLとして読み込ませ、期待どおりの表示状態と特徴を確認した。外部サイトへはアクセスしていない。
全819 unit test成功。temp DBへの保存・replayでもページと材料の対応を保持する。

稼働状態の既存集約規則は維持した。新しい特徴は未判定URLを自動採用する規則ではなく、次のrole検討で使える材料。
表示状態が不明な過去の観測は表示済みと扱わず、surface未検出をproject siteの肯定材料にもしない。
検証用HTMLと実行記録は一時生成物`.tmp/check_browser_visibility.py` / `.tmp/browser-visibility-smoke.json`に置いた。
