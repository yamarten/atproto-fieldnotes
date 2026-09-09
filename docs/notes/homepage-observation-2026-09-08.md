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

## 観測入口の接続時点で残っていた検討

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

## Role判断への接続（v1の試行記録）

`repository-homepage-role-v1`を通常enrichへ接続した。既存roleのexact再利用、採用済みrepoのhomepage指定と
既存bounded policyによる明示的逆参照、同じ到達URLの表示されたsurfaceを条件とするservice採用、
project site候補化、衝突／guard／失敗の保留を分ける。採用は共通のconfirmationとadoption記録を使う。
browserは未確認・古い確認を優先して1 enrichにつき1 homepageまで。失敗もattemptとして保持する。
Websiteは変更しない。project site候補化はUI不存在の判断ではなく、repoからの代表homepage指定と逆参照に基づく。

全827 unit test成功。通常enrichからの採用、temp DB保存・active adoption、replay、path保持、
異なるbrowser到達URL、表示されないform、guard、他projectとの衝突、browser失敗を確認した。

正本DBをread-onlyで走査し、380 projectのhomepage relationから現在の採用・対象URL条件を通過する347 URLを検討した。
保存済みHTML抜粋を使うため、リンクが見つからないことは実ページでの不存在や帰属否定の証明ではない。

|結果|URL数|
|---|---:|
|既存service再利用|137|
|既存project site再利用|1|
|取得観測なし|60|
|帰属材料不足|108|
|取得失敗等|32|
|project site候補／browser確認対象|2|
|他projectと衝突|5|
|Web page対象外のresource|1|
|guard／非現行|1|

browser確認対象はarroba（`https://arroba.readthedocs.io` → `/en/stable`）とBarazo
（`https://docs.barazo.forum`）。既存HTTP／Chromium経路で現在の2ページを取得し、双方で明示的な逆参照は確認できたが、
表示されたsurfaceは検出されず、project site候補となった。この実例で新規service採用は0件。
新規service採用の動作確認はfixtureであり、実運用での手動判断削減効果はまだ示せていない。

残るpolicy判断は、採用済みrepoの代表homepage指定とページの明示的逆参照を、project siteの自動確定に十分な
肯定材料とするか。この条件による自動確定はまだ実装しない。旧edgeの未解決分のreviewと正本DB適用も別途残る。
走査・live smokeは正本DBに書き込まず、結果を`.tmp/web-role-evaluation/homepage-adoption-scan.json`と
`homepage-adoption-live.json`へ保存した。

## 帰属確定と複数repo衝突の補正（v2の試行記録）

v1で「明示的な逆参照」と呼んだものは、実際にはbounded link policyの解釈結果だった。
このpolicyはラベルのない単一repoリンクやglobal chromeのリンクも肯定し得る。
`CONFIRM_PROJECT_REPOSITORY`というdispositionと、保存されたrelationの`classification: confirmed`は別物である。
v1はここを混同し、ページの帰属確定を経ずにrole採用へ進めたため、`repository-homepage-role-v2`へ修正した。

v2は、現在の到達URL→採用済みrepoのconfirmed `project_repository` relationを必須とし、今回のページでの
逆参照観測は別に保持する。評価と適用の間でrelationが候補へ格下げされた場合も採用を止める。
旧v1のassessmentから自動採用を再実行しない。過去の失敗形である「解釈上の肯定／現在のcandidate」を
回帰テストに含めた。これは同一projectを厳しく推測する新規heuristicではなく、既存の帰属確定を利用する境界である。

衝突検査は他projectのconfirmed URLだけでなく、採用済みrepoが指定する未採用homepageへ拡張した。
保存済みの1段のHTTP redirectも含め、未採用repoとguard対象のhomepageは競合元にしない。
同じprojectの複数repoはまとめる。異なるprojectの競合は、URL採用前の状態でも双方を保留する。
将来取得する未観測のredirectを予測したり、共有siteを誤登録と断定したり、自動mergeしたりはしない。

read-only再走査は380 project / 347 URL。既存role再利用137、取得観測なし60、帰属材料不足96、
取得失敗等32、衝突20、対象外resource 1、guard 1。新規browser確認対象は0件だった。
v1で候補だったarrobaとBarazoは、現在のconfirmed relationが足りず帰属確認の保留になった。
別projectからの未採用homepage競合には、indigo／Ozone等の`atproto.com`、UFOs／Slingshotの`microcosm.blue`、
複数Ruby関連projectの`ruby.sdk.blue`などがある。共有する意図がある可能性は残し、片方へ自動採用しない。

全834 unit test成功。同一projectの複数repo、別projectの未採用homepageを両順序で評価するケース、
redirect競合、採用されていないrepo、ラベル有無だけの推定、relation格下げ、旧policy replayを検証した。
新たな外部取得は行わず、現在のDBをread-onlyで読み、一時結果を
`.tmp/web-role-evaluation/homepage-adoption-v2-scan.json`へ保存した。正本DBは未変更。

## Roleごとの帰属主張（現行v3）

v2は通常のpage→repo collectorがページ採用済みroleを要求することを見落としていた。未知ページの採用に
そのconfirmed relationを要求すると循環する。一方、「roleを無視して帰属を確定し、その後roleを選ぶ」
のも不適切である。公式docsの置き場、運営するservice、代表project siteでは確認する主張が異なる。

v3はproject site候補とserviceの主張を別々に評価し、`role_assessments`へ理由を保存する。
homepage指定と逆参照はproject site候補を作れるが、代表性の確認までは意味しない。
serviceはproject→exact URLのconfirmed `operates_service`と表示されたsurfaceを要求する。
既存supportからprobeが生成した運営relationは独立した根拠にならないため除外する。
広義のconfirmed `project_repository`、READMEへのリンク、login UIを運営証明に読み替えない。
運営根拠がなければ追加browser確認を消費しない。既存serviceの検証は従来どおりである。

現行collectorから独立した運営relationを自動確定する経路はない。このため今回のservice採用テストは
operator reviewによる運営relationを明示したfixtureであり、通常の自動収集だけで新規serviceが採用される
実例を示したものではない。これは条件を緩めて隠さず、次の観測・帰属規則の検討対象にする。

実例の診断:

- Barazo: 採用済みdocs repoの保存READMEに`docs.barazo.forum`で公開する宣言がある。
  保存済みrepository analysis v14のobservationsには対応する`declares_documentation_site_url`がなく、
  本文があることとtyped observationとして利用できることは別だった。今回README parserやLLM分類は変更していない。
  現行の候補化はhomepage指定と観測したGitHub footerの逆参照によるもので、README宣言の自動利用を実装したとはしない。
- arroba: 入口`https://arroba.readthedocs.io`は既存service anchorで、到達先`/en/stable`が別URLとして評価されていた。
  「未分類homepageの帰属根拠不足」だけでは説明できない。今回入口の既存roleの補正やalias採用はしていない。
- [Ruby SDK](https://ruby.sdk.blue/)は独立gemの共通サイトと明記し、[microcosm](https://www.microcosm.blue/)も複数serviceを紹介する。
  共有homepageは誤登録の証明ではない。Navyfragen feedの保存READMEもNavyfragen内容を集めるfeedと説明しており、
  homepage一致だけで同一projectへのmergeやserviceの帰属を決めない。共有20件の個別処遇は未確定。

2026-09-08に正本DBをread-only transactionで固定し、380 projectからの347 URLへv2/v3を比較した。
差分はarroba／Barazoの帰属保留→project site候補の2件のみ。既存service再利用136、project site再利用1、
取得観測なし60、帰属保留95、取得失敗31、衝突20、対象外1、guard 1。取得観測のある287件で独立した運営根拠は0件。
以前保存したv2走査との差にはhexaGenAppの取得失敗→帰属保留もあるが、同一snapshotでのpolicy差分ではない。
候補増加は確認できたが、自動確定や手動判断削減の効果はまだ示せていない。

全839 unit test成功。通常enrich、temp DB保存・replay、採用前のrole付き運営根拠、運営関係の格下げ、
repo homepage指定の変更、第三者instanceでも成立するsource link＋login、probe由来の運営relation、
Barazo footerを縮約したfixture、旧v1/v2 replay、共有homepage保留を確認した。
collectorの外部取得契約とbrowser実装は変更しておらず、新しい外部smokeは行っていない。
正本DBは未変更。一時比較結果は`.tmp/web-role-evaluation/homepage-role-comparison-v3.json`。

## 公開先宣言の抽出と文脈（2026-09-09、repository analysis v15）

Barazoで公開先が抽出されなかった直接原因は、v14のclosed candidate生成がdocumentation設定ファイルのURLに
限られ、READMEの公開先宣言を候補にしていなかったことだった。LLMが本文を読めても、その宣言を選べなかった。
v15は既存の宣言文脈判定をREADMEへ適用し、本文中の正確なURLとevidence locatorを候補にする。

単純に候補を増やす試行では、LLMの説明文が否定・仮定を正しく述べても、同時に肯定する候補IDを選ぶ誤りが出た。
設定ファイルのコメント内の否定URLでも同じ誤りがあり、READMEだけの問題ではなかった。
対策は否定語の個別除外ではなく、URL候補の`applicability`を明示する回答契約への変更とした。
正規化は`current_project`だけを採り、否定・仮定・第三者・旧公開先・不明・区分欠落を棄却する。

設定済みQwen `qwen/qwen3.6-35b-a3b`、reasoning none、各2回で比較:

|入力|候補IDのみの試行で正しく扱えた回数|文脈区分追加後|
|---|---:|---:|
|Barazoの保存README・設定・manifestを含むmaterial|2/2|2/2|
|第三者のdocumentation URL|2/2|2/2|
|公開していないと明記したURL|1/2|2/2|
|仮のデプロイ先|0/2|2/2|
|パス付き公開先と構築ツールへのリンク|2/2|2/2|
|設定のcurrent `site_url`|2/2|2/2|
|設定コメントの否定URL|0/2|2/2|

否定例は`Our documentation site is NOT hosted at ...`、仮定例は`This is an example only, not our published site`を含む。
Barazo以外は境界確認用の合成入力であり、14/14は一般的な正解率ではない。調整に使った同じ例での再確認である。
この結果を帰属のconfirmed relationやservice運営証明へ昇格しない。通常enrichでは従来のdocs resource候補へ渡す。
project siteの代表性やserviceの運営帰属は別途判断する。v15で古い解析cacheを無効化し、正本の既存結果は書き換えていない。

全841 unit test成功。README→候補ID→文脈区分の検証→docs候補の通常enrich経路、区分欠落・非currentの棄却、
exact URLの保持、homepage metadata単独での宣言禁止、service／project siteへ自動確定しないことを確認した。
比較出力は`.tmp/web-role-evaluation/{readme,config}-declarations-v15{,-applicability}.json`。正本DBは未変更。

service側では、以前の347 URL走査のproject keyを使ってPDSlsとKlearskyの保存repo資料も調べた。
PDSls（`proj:c4a5b7620ce0`）には本体`notjuliet/pdsls`とStratos対応fork `NorthskySocial/pdslss`が採用済み。
両repoの採用理由は`legacy_manual_seed_migration`であり、今回のrole別帰属判断で確定したものではない。
forkのREADMEは派生であることを明記する一方、homepageと`APP_DOMAIN`の既定値は本体の`pdsls.dev`を保持している。
provider metadataの`fork: false`だけでも派生を否定できない。設定されたdomainや同じproject record内のrepoという
事実だけを、現在のservice運営の根拠へ変換しない。既存projectの分割・identifierの変更は行っていない。

現在の`pdsls.dev`を既存browser collectorで観測するとSourceリンクはTangledを指していた。
GitHubの採用済みrepoとの対応は別途確認が必要。Klearskyのbrowser取得はnetworkidle待ちでtimeoutとなり、
表面や帰属が存在しないとは判断していない。両例ともserviceの自動確定に使える独立した運営根拠は確立していない。

## 3実例の処遇と迂回採用の修正（2026-09-09）

arrobaでは、種類を判断せず同一projectの根拠からserviceを採用した実害を確認した。
8月13日の[operator review](identifier-adoption-review.md#serviceではない公式websitedocumentation)は、
arrobaとBarazo docsをserviceから外すことを決定済みだった。arrobaの旧bootstrap adoptionは同日clearされ、
8月14日・19日・28日に`trust_policy`がserviceとして再採用している。保存`source_ref`の根拠は
「ドキュメントサイトは同一プロジェクトのソースコードを参照している」等の`llm_same_project`とrepo homepageであり、
運営serviceの根拠ではない。先行節の「代表性未確定」は一般規則の残件であって、この2件を再判断する必要はない。

homepage専用role policyだけでは防げず、通常の最終reconciliation、service probe、既存LLMの
`repository_link_assessments`から`confirm_service_url_with_trust`へ到達する経路が残っていた。
`trust-graph-v2`はservice採用時にrepositoryを起点・中継のどちらにも使わない。
relationやedgeを消さず、repository採用と方向付き経路の説明は維持する。
既存service同士のcanonical pathと、独立運営relation＋visible surfaceによるhomepage採用も維持する。
過去のactive adoptionは自動で取り消さないため、既存誤採用の補正は別操作となる。

合わせて、既知documentation hostのproject subdomainを認識する一般則を修正した。
正本の保存homepageではarroba、lexrpc、atlas-protocolのRead the Docs URLがunknown→documentationの3件。
host境界で照合し、`notreadthedocs.io`や`readthedocs.io.example.com`は含めない。
exact pathの観測とWebsite候補化は維持する。ドメイン分類だけで既存serviceを取り消す処理は追加しない。

### 実例の処遇

|対象|確認した根拠|処遇・適用境界|
|---|---|---|
|Barazo `proj:52743420e0c7`|過去review、README公開先宣言、現在docsページのmember/admin/developer向け案内|`docs.barazo.forum`をconfirmed docs resourceへ追加。既存Website/serviceのstaging URLは変更しない|
|arroba `proj:5c8ecd4e26e6`|過去reviewはlibraryの代表Website/docsと明記。旧service再採用は同一projectの説明だけ|入口の誤serviceとそのactive adoption/decisionを解除し、同じURLをconfirmed project siteへ。`/en/stable`をdocsとして保持。Websiteは同じ入口のまま|
|PDSls `proj:c4a5b7620ce0`|現在のSourceはTangled `pds.ls/pdsls`。同recordのPDSlss READMEはStratos対応forkと明記|PDSlssを別recordへ分けることを推奨するが、未合意。`pdsls.dev`をforkのserviceとして採用しない|

Barazo docsは今回HTTP 200で確認した。`barazo.forum`はHTTP 410だったが、この単一観測を理由に
既存staging serviceの廃止や代表URLの変更はしない。TangledのDID URLは
`https://tangled.org/pds.ls/pdsls`へ転送され、READMEはPDSls Atmosphere Explorerと説明する。
これは現在のsurface→source対応の確認であり、GitHubからの移転・mirrorの厳密な同一性までは確認していない。

PDSlss分離を選ぶ場合の変更対象は`https://github.com/NorthskySocial/pdslss`の採用先と、
そのrepoを根拠にした現行説明・観測の再整理。現PDSlsの紹介文にはStratos連携が混入している。
当該repoをsubject等に含むrelationは68行あるが、コード片の誤ったcollection候補も含むため、
そのままconfirmed情報として新recordへ複写しない。repoを起点として再調査し、本体にはforkへの関連を残す。
本体のGitHub repo、`pdsls.dev`候補、Tangled Sourceの観測は本体側に残す。service新規確定とは分ける。
同名の別record `proj:7c585b38d809`も存在するが、repo/serviceを持たず、名前だけで統合・分割先には使わない。

### 検証と効果

- 旧arrobaの実service adoptionの双方向根拠を再構成した比較は、修正前true→修正後false。
  現在の3件のrelationとrepo起点だけでは前後ともfalse。過去の誤判断の再発防止と、現在データの改善量を混同しない。
- 既知fixtureを用いた通常enrich（外部collectorはmock）→save→再読込→最終reconciliationでservice再採用なし。
  同一projectを返すLLM経路、repo経由のservice間迂回、service canonicalの維持も検証。
- 正本からread-onlyで取り出したBarazo/arrobaの2 recordを最小temp DBへ保存し、上記補正案を適用。
  最新の保存済みinvestigation bundleを通常replay→再評価→再保存しても補正を維持した。
  差分はBarazoのdocs追加、arrobaのservice除去・site追加・転送先docs追加。Websiteは両件不変。
  `service_status`は両件とも既存のunverifiedを維持し、live/noneへの改善は主張しない。
  foreign key違反0、integrity ok。temp DBは削除済み。補正用の参考materializerを使った検証であり、通常enrichによる自動補正ではない。
- 全846 unit test成功。新しい外部取得契約・Web UIの変更はない。正本DBへの補正、全件再enrich、運用反映は未実施。

過去の`website_only` review対象の再点検では、current service confirmed 20件、candidate 1件が残った。
これだけで20件すべてが誤登録とは判断しない。後続operator判断とsource_refを照合し、
同一project根拠だけで戻ったものと、その後service役割が確定したものを分ける監査を残す。
結果は`.tmp/web-role-evaluation/`の`service-role-fix-comparison.json`、`reviewed-role-correction.json`、
`prior-website-review-audit.json`。恒久regression fixtureは`tests/fixtures/project_registry/service-role-regression-v1.json`。


## PDSlss分離の合意と適用前検証（2026-09-09）

ユーザーがPDSlssを別projectへ分ける方針を承認した。境界判断は確定し、正本DBへの適用承認だけが残る。
既存`application.merge.split_identifier`へfork repoを渡し、正本から読み取ったPDSlsを最小temp DBで分離した。
本体からfork repoを外し、本体へのrepo blockと双方のseparate_project判断を保存する。
新recordにはfork repoだけを採用し、homepage・service・collectionを一括継承しない。
最新の保存済みinvestigation bundleを本体へreplayしてもfork repoは戻らなかった。

分離処理は過去の紹介文を自動で書き換えないため、今回のoperator reviewとして次を補正する。

- 本体summaryを「atprotoネットワークの公開データを閲覧・探索するWebアプリケーション。」へ変更。
  featuresは「公開データの閲覧・探索」「レコード検索」とし、Tangledのソース移転を機能連携とする説明も外す。
- 本体summary候補から、今回照合したStratos由来の候補と旧混在LLM紹介文を外す。一般的な単語除外規則ではなく、
  対象recordのレビュー補正である。履歴は保持する。
- 新record名をPDSlssとし、「Stratos対応を加えたPDSlsの派生Webアプリケーション。」を初期紹介文とする。
  repo解析結果は当該forkの保存済み結果だけを入力に使い、本体や関連repoの結果を混ぜない。

再調査の入力構築で`select_for_triage`がcoverageの`counts` dictをset要素として扱いTypeErrorになった。
coverageを取得対象6項目（metadata/readme/tree/manifests/schemas/source）だけで採点するよう修正した。
実例と同じcounts付き結果の順位比較testを追加し、全847 unit testが成功した。

設定済みQwenへ通常のtriage prompt／transport／結果適用を各1回実行した。新しい外部repoの再取得や
repository analysisの再実行ではなく、保存済みrepo解析を分離した上でのproject再評価である。
本体の解析入力は`notjuliet/pdsls`だけ、forkの解析入力は`NorthskySocial/pdslss`だけだった。
本体summaryは補正内容を維持し、forkの説明にはStratos対応が残った。保存後も本体のserviceは空で
`pdsls.dev`が候補、fork側はserviceもservice候補も空。名称はPDSls／PDSlssとなった。
サンプル各1回の確認であり、将来のLLM出力を保証するものではない。

temp DBはforeign key違反0、integrity okで削除済み。結果は
`.tmp/web-role-evaluation/pdsls-split-validation.json`。本体の既存履歴・関連観測は消さず、
fork由来の68 relationを新recordの確定情報へ一括コピーすることもしていない。
正本適用案は、先行節のBarazo docs追加、arroba role補正と、上記PDSls分離・紹介文補正の3件。
本体の同名別recordやTangled sourceの追加採用、他のwebsite_only対象の一括補正は含めない。


## 正本適用と一括enrichの再開（2026-09-09）

ユーザーは「安全に中断・再開できるなら適用」を承認した。Webの実行中ジョブはcheckpoint付きbulk enrichで、
通常cancel APIにより3595件保存後にinterruptedとなり、DBを開くworkerがなくなったことを確認した。
実運用checkoutを`dde92c7`から`8f131c7`へfast-forwardし、同じ補正スクリプトを最小temp DBで検証後、
正本SQLiteと中断checkpointを`data/backups/homepage-role-review-20260909T110921Z/`へbackupした。

正本へ適用した差分はBarazoのconfirmed docs resource追加、arrobaのserviceとactive adoption/decision解除・
project site採用・転送先docs追加、PDSlsからPDSlssの分離と紹介文補正。新project keyは`proj:efcd972d8d51`。
本体のrepositoryは`notjuliet/pdsls`、fork側は`NorthskySocial/pdslss`のみ。`pdsls.dev`候補は本体側に残り、
forkはservice/site/Websiteを持たない。既存Web service roleの一括再判定や、旧保存trust edgeの一括削除はしていない。
foreign key違反0、integrity ok。実行結果はbackup内`applied-review.json`。

Webを通常reload APIで再起動し、`/api/checkpoints`で同じrunの再開可能状態と3595完了を確認。
`enrich-20260908T111813Z-a27f8067`をresumeし、新job `job:1a9ff1910d57`で実行中となった。
最初に以前失敗したalias元projectを再試行し、続けて未処理projectの保存が進んだことを確認した。
再開直後には別のrepository request_errorも観測されており、全処理が無エラーとは主張しない。

停止前のcompleted keysと現在のlast_run_errorsをread-onlyで照合した結果、604 projectに旧coverageの
`unhashable type: 'dict'`が残っていた。これらはrunの完了扱いであるため通常resumeでは再評価されない。
対象は`data/backups/homepage-role-review-20260909T110921Z/coverage-failures-before-resume.json`に記録した。
現在run終了後に対象の再評価と、新しく分離したPDSlssの単体enrichが必要。新recordは固定selected keys外である。
現在runの全完了までは待っておらず、同時writerとなる追加enrichは起動していない。


## website_only後の再採用監査（2026-09-09、正本はread-only）

ユーザーは追加調査がenrichを指すことを確認し、604件と新PDSlssの追加enrichを当面保留とした。
現在の一括enrichは継続する。以降の監査は保存データの読み取りとtemp DB検証だけで行った。

arroba補正後の旧website_only対象にはconfirmed service 19件、candidate 1件が残っていた。
active adoption 76行をsource_refまで照合した。desertthunder.devには2026-08-25の
`operator_review:project_site_review_v1`によるservice採用があり、
[後続review](collector-relation-policy-shadow-comparison.md)でもblog/TILを登録理由としてトップURLをserviceとすることが明示される。
これは旧判断への機械的な巻き戻しをしてはいけない例で、現状を維持する。

残るconfirmed 18件はactive adoptionがtrust_policyだけだった。いずれもrepo起点の相互参照、
その結果を既存anchorとして使う再採用、canonical redirectの組合せであり、独立したservice役割の判断ではない。
pdsxはfirst_party_link、他の17件にはLLM same_projectが採用根拠に含まれる。
現在の独立operates_service relationは対象20件すべてで0だったが、その欠如だけを取り消し理由にはしていない。
過去の明示reviewと後続採用の根拠を組み合わせて補正対象とした。

|処遇|対象|
|---|---|
|serviceを除去し既存project siteを維持（5件）|Bluesky Comments、ToolShed、idiolect、mastrojs/atproto、Writer|
|serviceを除去し代表Websiteをproject siteとして採用（12件）|lexrpc、atproto-wc、tsky、atShare、atproto.dart、poptart、OpenFederation、agora、mcp-server-bluesky-py、Skeeditor、pdsx、Trotsky|
|誤serviceを除去しdocs resourceへ（1件）|BridgeBeatsのdocs.bridgebeats.link。実service bridgebeats.linkとWebsiteは維持|
|変更しない|後続operator reviewのdesertthunder.dev、candidateのLexicon Community（既にproject site）|

mastrojs/atprotoは8月13日には関連資料とされていたが、8月25日の後続reviewでproject siteとして採用されている。
この場合も新しいoperator判断を維持する。OpenFederation/agoraの別service追加という過去の判断は
今回の旧Website role補正と分け、失われた別URLの自動復元や新規service採用は行わない。

20実recordを同じread-only snapshotから最小temp DBへ保存し、19件の試行補正（候補のLexicon Communityも含む）と
最新の保存済みinvestigation bundleのreplay、最終reconciliation、save・再読込を行った。
対象URLのservice確定再発0、Website変更0、既存の別service喪失0、foreign key違反0、integrity ok。
project site追加は12件。Lexicon Communityはcandidateへ戻り実質変更がないため、最終補正案から外す。

audit対象URLは19試行ともservice候補には戻った。Bluesky Commentsの呼出し経路を追うと、
README relationのURL、repo snapshot内URL、過去service probeが候補生成元だった。
候補化を防止したとは主張せず、今回の補正効果は誤ったconfirmed role・trusted anchorの除去とする。
project siteが将来service surfaceにもなり得るため、site URLを一律blockする対応は採らない。

監査・検証結果は`.tmp/web-role-evaluation/website-readoption-audit.json`と
`website-readoption-correction-validation.json`。正本は未変更で、一括enrichも中断していない。
新規のheuristic変更や外部取得は行っていない。18件の正本補正には別途適用承認が必要。
