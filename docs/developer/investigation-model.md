# Investigation Model

project 調査の責務境界と evidence の恒久的な契約を定める正本文書です。source ごとの具体的な
heuristic と request 上限は [registry-internals.md](registry-internals.md)、保存 field は
[project-record.md](project-record.md)、検討中の論点は [notes/](../notes/) を参照してください。

## 目的

- collector の追加や変更で読む working set を小さくする
- 外部からの観測、project への統合、評価、保存を分離する
- source ごとの失敗を隔離し、得られた evidence は失わない
- 保存済み evidence から network なしで評価を反復する
- manual confirmation、defer、block を自動評価より優先する

## Pipeline

調査は `collector -> evidence -> integration -> evaluation -> save` の順で扱います。

1. collector
   - query planning を含め、外部 source を読み structured evidence を返す
   - 接続済み collector は共有 project dict を直接変更しない
2. evidence
   - 観測と source の完了状態を JSON-compatible な bundle に正規化する
   - relation は subject、predicate、object、locator、抽出方法を持つ
3. integration
   - evidenceから既存project fieldへの適用を担う責務。実装は`integration.py`だけでなく`registry.py`の適用関数と、project copyからpatchを返すcollector adapterにも分かれる
   - project 文脈、manual guard、複数 source の corroboration を適用する
4. evaluation
   - ownership、atproto寄与度、service status などを rule-based に計算する
   - network、DB、LLM に依存しない関数を優先する
5. save
   - `projects` を現在状態の materialized record として保存する
   - 同じ SQLite に input/output snapshot、評価、bundle、source 状態も保存する

公開command入口は`bin/project-registry`と`bin/project-registry-web`です。調査CLIの`main()`は`project_registry_core.cli`、1 projectの調査orchestrationは`project_registry_core.enrichment_pipeline`、保存・checkpointは`project_registry_core.application.enrichment`にあります。collector適用とfinalizeの補助処理は`project_registry_core.registry`にも残ります。旧top-level Python APIと`scripts/`配下のCLI wrapperは削除済みです。

Web detailはproject単位のinvestigation run summaryをSQLite rowから一覧し、選択したrunだけbundleをdecodeします。run detailのevidence/failureは上限付きで、raw HTMLやREADME全文を保存・表示しません。Replayは保存runを現行ruleで要求時に再評価するread-only処理で、結果をDBへ永続化しません。

### Run observability

新規enrich runは、次のwall timeをmillisecondで保存します。

- initial collection + integration
- investigation planning
- investigation collection + integration
- LLM triage + application
- final evaluation
- investigation run historyのserialization + SQL
- current projectのnormalization、serialization + SQL

collectorのnetwork取得とevidence適用は現行pipelineで交互に行うため、前半2つのcollection区間をnetwork時間だけと
見なしません。transaction commit待ち時間も未計測です。`total_ms`は上記のenrich開始からproject SQL書込みまでで、
process job全体、複数projectの待ち時間、operator review時間ではありません。

planningとtriageの各LLM requestには、purpose、success/failure、error category、duration、UTF-8 prompt bytes、
response JSON bytes、設定された1 attempt当たりtimeout、model/API style等の非secret metadataを残します。prompt本文、
endpoint、command、API keyは保存しません。token数はprovider共通に取得できないため未接続です。`timeout`は
明示的なtimeout例外または既存clientが返すtimeout errorから分類し、transport failure、endpoint incompatible、
rate limit、その他HTTP error、invalid responseと分けます。endpoint互換性エラー以外では同じpromptを自動再送しません。

保存先は`evaluation_json._run_metrics`ですが、load時はsemantic `evaluation`と`metrics`に分けます。そのため
非決定的な時間値はreplay diffやfixture exportへ入りません。Webのlatest run、run list、run detailから確認できます。

### LLM evidence budget

planningとtriageへ渡す可変evidence JSONは、整形後UTF-8で24 KiBを上限とします。fieldごとの代表件数上限を先に
適用し、長いlistは先頭と末尾から均等に残します。それでも超える場合は低優先fieldから追加で縮約します。
`project_key`等のscalarはlist budgetで落としません。縮約した場合は`evidence_budget`へfield別の観測件数と採用件数を
入れます。planningとtriageは調査の異なる時点で同じbudget規則を使い、triageへ渡したevidenceは
`llm_source.evidence`にも保存します。

これはLLM入力契約であり、service HTMLやEvidenceBundleの保存budgetとは別です。prompt templateの固定文まで24 KiBに
含める契約ではありません。実requestの最終prompt bytesはrun metricsで別に観測します。

Service / ownership reviewも保存済みcurrent state、normalized relation、collector runだけを読みます。画面表示のためにnetwork collectorを再実行しません。保存根拠がない状態は`not_observed`であり、到達不能・非ownership・permissionなしの確定結果ではありません。

## Evidence classification

- `confirmed`: operator decision、first-party claim、検証済み identity path など強い帰属を持つ観測
- `candidate`: 関連性はあるが、帰属または意味の追加確認が必要な観測
- `derived`: 他の観測から決定的に導出した query、正規化結果、usage relation
- `heuristic`: score、文字列類似、community source など誤判定し得る推定

classification は project field の confidence と同義ではありません。integration が guard と project
文脈を適用し、confirmed field、candidate field、observation のどこへ反映するかを決めます。

## EvidenceBundle v2

通常readerが受理するのはEvidenceBundle v2だけです。旧v1 bundleの互換readerは提供しません。

- `EvidenceBundle`
  - `schema_version`, `project_key`, `collected_at`
  - 順序付き `records`, `failures`, `collector_results`
- `EvidenceRecord`
  - `kind`, `source`, `classification`, `observed_at`
  - JSON-compatible な `value` と query、score、repo path などの `context`
  - service verificationのHTML/link/URLはcollector内の判定後に保存budgetを適用し、観測総数とdigestを併記する
- `CollectorFailure`
  - `source`, `operation`, `target`, `category`, `message`, `retryable`
- `CollectorResult`
  - `success | partial | skipped | rate_limited | failed`
  - request/result 数、cursor、stop reason、retry 時刻、cache hit
  - GitHub collectorは実際に送信したHTTP request数を記録し、process-local cache hitはrequest数へ含めない
  - GitHub API応答に`X-RateLimit-*`または`Retry-After`があれば、最後に観測した
    `limit`, `remaining`, `used`, `reset`, `resource`, `retry_after`を`rate_limit`へ保存する

### Collector history projection

通常enrichでは、現在の採用とguardを通過したrepositoryのconfirmed `repository_homepage`を、
パスを保持した観測先として使います。docsも取得対象ですが、account、package、funding、repository自体は対象外です。
既存の観測先と正規化URLが一致すれば同じrunで取得を重複させません。

既にexact URLがserviceであればstatic laneを使います。それ以外は`service_verification`の
`repository_homepage` laneへ本文、リンク、requested/final URLを既存budgetで保存します。
page→repoには既存のbounded policyを使い、取得成功だけでserviceの採用・状態・Websiteを変更しません。
redirectはwebsite間の観測として保存し、同一hostという理由でpathを同一視しません。
取得失敗で前回のpage→repo relationは消さず、成功時は同じpage sourceの観測を置換します。
保存とreplayにも同じ境界を適用します。

このlane自体はroleを確定しません。通常enrichは取得結果を`repository-homepage-role-v4`へ渡します。
現在採用済みrepoのhomepageを起点に、成功した取得の到達URLで既存service / project siteを照合します。
既存serviceは再利用します。既存project siteでも新しい公開先根拠と実在確認があればserviceを評価します。
別URLのalias登録やWebsiteの置換はしません。

未知のroleは`project_site`と`service`の帰属主張として別々に評価します。採用済みrepoのhomepage指定と
今回のページのbounded policyによる逆参照はproject site候補を支持しますが、代表siteの確定根拠とはしません。
候補化はpage→repoのconfirmed relationやページの採用済みroleを前提にしません。候補はtrust anchorになりません。
service確定には、現在の採用済みrepoによる公開先宣言、または対象project→到達URLのconfirmed `operates_service` relationを要求します。
既存supportから生成されるprobe由来（methodが`*_probe`またはsourceが`service_*`）のrelationは独立した根拠にしません。
広義の`project_repository`、単なるリンク解釈、表示されたloginだけでは運営帰属を確定しません。
公開先宣言は`repository-analysis-v16`の`declares_service_url`観測です。READMEの本文付き候補URLをLLMが選び、
`current_project`または`uncertain`ならservice候補へ追加します。候補化には取得成功・逆参照・操作画面を要求しません。
LLMへ提示するURL候補は特定の公開・利用語句に依存せず、既存budget内のREADME本文から抽出します。
Markdownの`<https://…>` autolinkはHTMLタグとして削除せず、本文中のURLとして保持します。
否定・仮定・第三者・旧公開先・区分欠落はこの経路の候補にはしません。デモは一律除外せず、パスを保持します。
自動確定は最新成功結果の`current_project`だけを使い、同じrepoへの今回の逆参照と追加確認を要求します。
公開先の観測を`operates_service`へ読み替えません。新しい公開先は同じenrich内で解析後に取得・評価します。
追加確認はbrowserまたはprotocolのいずれか1 URL／enrichで、未確認、次いで古い確認を優先します。
失敗もattemptとして保存するため、一件の失敗がほかのhomepageの確認を恒久的に塞ぎません。
browser結果は`repository_homepage_browser` laneへ保存し、未採用URLの観測でprojectの稼働状態を上げません。

最初のnavigationの到達URLがstatic取得の到達URLとexact一致し、表示状態が観測済みで、表示されたauth/public
surfaceの材料があり、当該pageにnetwork/console errorがなく、上記の帰属根拠がある場合だけWeb serviceを採用します。
`service_kind=api|pds`ではbrowserを要求せず、origin root URLだけを対象に`/xrpc/com.atproto.server.describeServer`を
15秒timeoutで取得します。HTTP 200、同じendpointへの到達、DIDとavailableUserDomainsを持つ応答を要求します。
パス・query・fragment付きの公開先は、このXRPC確認では未対応としてrequestせず候補に残します。
originへ丸めて別の公開先を確定しません。保存済み結果の適用時も対象URLと確認endpointの対応を再確認します。
plain text rootの独立行`Code: URL` / `Source code: URL`も、対象repoへの逆参照として使えます。
応答は`repository_homepage_protocol` laneとassessmentに保存します。汎用API・AppView独自APIの確認は未対応で、
HTTP 200・一般JSON・AppViewという自己紹介だけでは確定せずcandidateに残します。
他projectのconfirmed URLに加え、採用済みrepoからの未採用homepage指定・公開先宣言も衝突検査に含めます。
保存済みの1段のHTTP redirectによる一致も含み、host familyだけでは一致としません。
同じprojectの複数repoはまとめ、別projectの指定が競合すれば採用順にかかわらず保留します。
未採用repoやguard対象のhomepageはこの競合元にしません。未取得の将来の参照先まで予測する検査ではありません。
非現行URLやguardも保留し、採用は共通の確認・adoption記録を通します。
採用理由にrepo、homepage、運営relation、role別の評価理由、今回の逆参照、衝突元、browser evidenceのsource・時刻・特徴、policy versionを残します。
リンク先で見つかったlogin、host family一致、staticの`live`だけでは採用しません。

project site候補化はUI不存在やproject site roleの確定ではありません。公式documentationであることと
代表project siteであることも同一視しません。project siteの自動確定は未接続です。
`homepage_role_assessment`に入力事実・`role_assessments`・適用actionを保存します。replayでも現在の起点、
そのrepoのhomepage指定と逆参照の対応、guard、既存role、運営relationを再評価して適用します。
公開先宣言は記録時と現在のrepo・URL・revisionも照合します。旧v1〜v3のassessmentからの自動採用は実行しません。
他projectとの衝突判定は調査時のDB snapshotを記録したもので、replayは現在DBへの再照会ではありません。
既存serviceのbrowser verificationによる稼働判定は維持します。ページ単位の観測契約は下記を参照してください。

replayに使うrecord/failure本文は`bundle_json`へ一度だけ保存します。`collector_runs`はWebと集計用のprojectionで、
同じrunの同じ`source + operation`を一行へまとめ、worst status、request/result/cache count、record/failure countを
`result_json`へ保存します。source別payloadを`records_json` / `failures_json`へ反復複製しません。
同じsourceを集約する場合、rate limit観測は最も新しいcollector resultの値を保持します。

GitHub repo collectorは同じrepoを一runで二度取得しません。snapshotの`repo`はintegrationで使うname、description、
homepage、fork parent、updated timestamp等だけを保存します。source semantic hint本文は別のtyped relationに展開済みなので
snapshotへ重複保存せず件数だけを残します。README URLは最大64件、JSON 16 KiB、1 URL 2 KiBです。relationと
candidate生成はbudget適用前の全取得結果から行います。

通常接続済みcollectorは`CollectorResult`を明示的に返します。record/failureからsource状態を推測する互換処理は現行導線では使いません。failure categoryはtimeout、rate limit、request budget、network error、remote 5xx、invalid response、browser error、collector errorなど、再試行・運用判断に必要な単位で固定します。GitHubの403は`X-RateLimit-Remaining: 0`または`Retry-After`がある場合だけrate limitとして扱い、それ以外はforbiddenとして区別します。

主要 relation predicate は `defines_schema`, `uses_collection`, `mentions_collection`,
`official_account`, `operates_service`, `project_repository`, `requests_permission`, `redirects_to` です。
同じ README から複数抽出した claim は独立 source と数えません。

## 正本と保存

実装済み:

- `projects` は UI、検索、merge、review が読む現在状態の正本
- `investigation_runs` は input project snapshot、bundle、evaluation version、保存済み評価、output snapshot を保持
- `collector_runs` は source/operation ごとの状態とrecord/failure件数をprojectionとして保持
- full HTML や README 本文は保存せず、bounded extract、hash、URL、repo path など判断に必要な材料だけを残す
- raw evidence の自動削除は行わない。手動retention commandで古いrun単位に削除できる

run 保存は project row の代替ではありません。再評価可能な調査成果と、その時点の materialized
record を対応づける履歴です。

runの4つの論理JSONはzlib圧縮して保存し、replayとexportは保存codecを直接扱わず共通readerで復元します。
decode failureはrun全体の破損として明示的に失敗させます。圧縮後のJSON fieldをSQLite JSON functionで直接
検索する契約は持たず、時刻・project・versionとcollector projectionのSQL監査、内容のreader経由監査を分けます。

repository analysisでは、revisionへ固定したbounded materialを`repository_analysis_material`としてrunへ保存し、解析recordはrepo URL、source revision、material hashでそのmaterialを参照します。READMEは先頭・末尾を含む最大6 block、合計6,000文字として元の行番号を保ち、選択数と切り詰め状態をcoverageへ記録します。LLMへ渡すのは本文を持つevidence blockだけで、tree pathは候補抽出やcoverageに使ってもclaimの根拠にはしません。repository metadataはdescriptionだけをclaim可能な本文とし、name、homepage、is_templateはcontextまたは決定的抽出に限ります。観測とcollection利用は、materialから事前生成したclosed candidate IDだけをLLMに選ばせます。README由来のcollection candidateはreverse-domain authorityが小文字でpublic suffixから始まるものに限り、case-sensitiveな最終nameとoperationを一体で固定します。候補が空なら空配列を要求します。current project stateはrepoごとの最新成功結果と`last_attempt`を分けて持ち、provider取得またはLLM解析の失敗で前回成功resultを上書きしません。project-level triageへ渡すのはconfirmed repositoryの保存済み成功結果だけです。

`repository-analysis-v16`では、documentation公開の記述があるREADME blockも公開先URL候補の対象にします。
URLは本文中の正確な値を保持し、homepage metadataだけでは宣言候補を作りません。設定ファイル・READMEの
`declares_documentation_site_url`候補をLLMが返す場合、`applicability`で`current_project` / `negated` /
`hypothetical` / `third_party` / `obsolete` / `uncertain`を区別します。`current_project`だけを観測へ反映し、
区分が欠けた回答も棄却します。候補IDの選択だけでは、否定・仮定のURLを公開先と誤認する実例があったためです。
この観測は従来どおり`role=docs, confidence=candidate`のresource候補へ接続し、project siteやserviceの自動確定、
独立した運営relationの生成には使いません。v15以前の解析結果はv16のcache hitにはしません。

既定retention policyは完了から90日以内の全runとprojectごとの最新3 runを保持します。`prune-investigation-history`はそれより古いrunをdry-runで列挙し、`--execute`時だけ対応するcollector runとまとめて削除します。fixtureへexportしたcaseはDB runとは独立して保持します。

## 調査状態

次を区別します。

- `success`: 予定範囲を完了した
- `partial`: usable record と failure の両方がある
- `skipped`: 設定不足、不要判定、budget 超過などで実行していない
- `rate_limited`: upstream 制約で打ち切った
- `failed`: usable result を得られなかった
- 証拠なし: 完了した collector が対象 evidence を返さなかった
- 未調査: collector 自体が実行されていない

「証拠なし」を否定的事実へ変換するには、対応 source が成功して必要範囲を走査したことが必要です。

## Relation と ownership

この節は調査結果からrelationを作る現行境界を説明します。relationを帰属候補、信頼経路、採用、mergeへ利用できる条件は[登録プロジェクトへの帰属と信頼](project-attribution-and-trust.md)を正本とします。

- `defines_schema` は accepted repo と accepted NSID の間だけに保存する schema definition relation
- repositoryにlexicon JSONらしいファイルが存在するだけの場合は、`defines_schema` ではなく弱い `mentions_collection` として扱う
- `uses_collection` と OAuth の `repo:` permission は利用関係であり ownership を確定しない
- `mentions_collection` は docs、example、test、fixture、vendor、generated などの弱い言及
- confirmed repo と first-party app が独立 source から相互リンクした場合、`project_repository` を昇格できる
- NSID ownership は operator-confirmed `owned_nsid`、公式 account の PDS schema record、authority までの identity path を優先する
- operator-confirmed repoはproject repositoryの根拠であり、そのrepo内にある全NSIDのownership根拠ではない
- service到達確認は`service_verification`として保存するが、`operates_service` relationはproject support判定後にだけ保存する
- defer / block された identifier は relation corroboration より優先して抑止する

## Replay と benchmark

実装済み:

- `replay-investigation`: 保存 run の integration / evaluation を network なしで再実行し semantic diff を出す
- `benchmark-investigation`: fixture corpus を一括評価し relation/evaluation の退行を報告する
- manual goldのpromotion evaluator testは全caseを実queue生成処理へ通し、policy、signal、score breakdown、候補集合全体の相対順位を評価する
- `scripts/promotion_outcome_analysis.py`はschema v3 DBをread-onlyで開き、紹介済みprojectの`introduced_url`だけを空にした反実仮想を現行promotion policyへ通す。category別捕捉率、signalなしの主因、service / lexicon / technical経路のblocker、紹介投稿単位のproject数をJSONで報告する
- `export-investigation-fixture`: 保存 run を sanitize 対象の fixture 形式へ出力する
- executable corpusは通常replay 6 caseに、operator correctionのblock/defer/confirm 3 caseを加えた9 caseを持つ
- `manual-gold-v1.json`はoperator reviewから固定したidentity / service / repository判断11 case、promotionのexclude / queue / deprioritize 14 case、紹介済みのpositive reference 5 caseを持つ
- `browser-service-calibration-v1.json`は描画後page要約と期待browser evidenceを5 caseに固定する

fixtureはraw response archiveではありません。誤判定を再現するための最小evidenceと期待評価を保存します。
自動exportはsecret、authorization/cookie/header、raw HTML/body/readme/text extract、prompt/command/endpoint、
local pathを削除またはredactし、URLのuserinfo/query/fragmentを除去します。export結果にはmanual review必須markerを
付けます。コミット前に実project/run key、時刻、handle/DID、自由記述を再現に必要な最小範囲へ人が縮約します。

manual gold setはDBのdecision rowを逐次mirrorしません。synthetic project ref、公開identifier、期待actionまたはpromotion disposition、根拠要約だけを保持し、実project key、run ID、decision時刻、有効期限、operator noteを除外します。promotion caseは`exclude / queue / deprioritize`を保持します。`deprioritize`は保存事実から機械推定せず、operator assertion後だけ`priority_group=lowered`となります。機械policyは`expected_policy`、候補集合全体の順序は`promotion_queue_expectation`、個別判断はfixtureのreasonとevidence summaryを正本とします。地域性など機械化しない基準はmanual contextに留めます。

promotion outcome分析は紹介実績を自動score変更へ直結させません。未紹介projectは負例ではなく、紹介済みprojectのcategory、status、profile、evidenceも紹介時点のsnapshotではなく現在値です。結果は現行policyが紹介実績を拾えない箇所の調査とsample抽出に使い、policy変更はmanual goldと人間のreviewで別途確認します。実行例:

```sh
bin/_project-registry-python promotion-outcome-analysis scripts/promotion_outcome_analysis.py --db data/survey.db --details no-signal
```

browser calibrationは実URLを取得し直さず、固定したpage URL、title、text excerpt、link、form input属性、submit label、OAuth候補、匿名menu展開数、service action、network / console errorを`collect_browser_service_evidence()`へ渡します。これにより公開pageが変化しても当時の入力に対する判定を再現できます。実URLへの`verify-service --lane browser`は現在状態のsmoke testに限り、固定期待値との差をsemantic regressionとして扱いません。

browserの`public_app_surface_detected`は、作成・投稿を表すactionと、supporting pageではない二階層以上の同一site link 3件以上が同じpageにある場合だけ成立します。CTA単独やcontent link単独では成立しません。action語彙は現在英語のpost / entry / create / write / compose / publish / uploadに限定し、固有project名による例外は持ちません。

browser pageにはnavigationごとの`requested_url`と到達先`url`を保存します。form、input、auth/service action、
linkの`visible`、フォーム内の`visible_submit_texts`をChromiumの`checkVisibility`で取得します。
CSS visibility、opacity、content-visibilityを含めた表示状態であり、操作成功や認証成功の証明ではありません。
`display: contents`のformにはboxがなくても、表示されたinputとsubmitを根拠として利用できます。

各pageの`visible_surface_features`は、保存budget適用後の表示済みcontrol/linkだけに既存auth/public surface規則を
適用した結果です。別pageの肯定材料は合算しません。旧観測の表示状態は不明（`visibility_observed: false`）であり、
表示済みと推定しません。`positive_surface: false`もserviceが存在しない、またはproject siteであるという判定ではありません。
これらはURLごとのrole検討材料であり、projectへの帰属やidentifier採用を確定しません。
run全体の従来の`login_surface_reached`等とcurrent service statusへのprojectionは維持し、新しいpage単位の材料とは区別します。

bundled Chromiumが`ERR_SOCKET_NOT_CONNECTED`を返した場合だけ、HTTP/2を無効にして1回再試行します。通常接続の失敗全般には適用せず、使用時は`transport_fallback`としてbrowser page evidenceに残します。

project直下の`service_status / service_status_confidence / service_status_evidence`は、保存済みの
`service_verification_results`を初期値なしで再評価するcurrent projectionです。過去scalarのmax / unionは入力にしません。

- current resultは`lane + requested_url`ごとに最新1件を使う
- supporting pageの`follow_up`と、static / deepのsupporting page URLはproject scalarへ入れない
- 同一URLのbase laneは`deep > static > authority`とし、肯定的browser observationをその上へ重ねる
- browserでloginまたは公開app surfaceを確認したURLは`live / high`とし、同じURLの`docs_only`、`coming_soon`、mixed launch分類を除く。`beta`と`invite_only`は両立するため維持する
- static / deep / authority resultはcollector時点の`supports_project`を保存して利用する。旧resultにこのfieldがない場合は、manual service host、確認済みservice host、またはそこへの確認済みsame-site redirectに限定して復元する
- 対象となるbase/browser observationがない場合は、LLM等が設定した既存scalarをprojectionで消さない

projectionはnetwork、browser、trust graph更新を行いません。`repair-db`は変更件数とbefore/after例をdry-runで報告し、investigation runのinput/output/bundle/evaluationは更新しません。

## LLM の位置づけ

- LLM は query planning、editorial 補助、曖昧な分類候補に使う
- identifier の新規提案は原則 candidate に留める
- ownership、高い寄与度、service の到達確認を LLM 単独で確定しない
- rule-based evaluation と manual guard を優先する
- LLM failure 時も deterministic plan と他 collector を継続する

## 接続状況

実装済み:

- GitHub repo/search、Bluesky actor/post/feed
- GitHub lexicon JSON と source usage の意味分類
- lexicon resolution、recent record sample、service static/deep、resource page の evidence 化
- OAuth client metadata の permission relation
- anonymous browser service verification。CLIとWeb project detailから、保存済みconfirmed/candidate service URLだけを対象に実行する。表示中の匿名menu展開、auth link追従、password form、handle/email form、OAuth入口、公開content itemを伴う作成action、描画後network/console errorを別evidenceとして保持する
- confirmed developer account からの Tangled PDS record 探索
- Tangled repo URL のHTML/README inspection
- Jetstream v2 snapshot の `sync-tangled-repos`
- root と `www` の観測済み redirect/canonical alias
- retry、Retry-After、TTL cache、source-local circuit breaker、request budget

未接続:

- OAuth `include:` permission set の再帰解決
- browser 認証後操作。登録、認証入力、投稿は設計上の非目標
- raw HTMLやREADME全文を含む無制限のevidence inspection

未検証 / 制約:

- Tangled repo inspection は公開HTMLと `raw/{ref}/{path}` でREADMEを取得する。GitHub APIのような完全なrecursive tree APIとは扱わない

Web UIのrun summary、bounded evidence、input/output/evaluation、要求時のread-only replay差分は
接続済みです。

未検証:

- 実 Playwright を用いた localhost smoke test
- 実 API での retry/cache/budget の長時間運用値
- Tangled Jetstream v2 full snapshotの初回実運用時間とAPI利用量

関連 notes:

- [identity-ownership-evidence.md](../notes/identity-ownership-evidence.md)
- [service-verification.md](../notes/service-verification.md)
- [evidence-replay.md](../notes/evidence-replay.md)
- [collector-reliability.md](../notes/collector-reliability.md)
