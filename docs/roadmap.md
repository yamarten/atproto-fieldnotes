# Project Registry Roadmap

この文書は今後到達したい状態、達成の判断、着手・再検討条件、合意済みの依存・優先関係を示します。列挙順を実施順とは扱いません。

## 目的の優先順位

1. 深掘りする価値がある候補を、背景と根拠とともに提示できる
2. 追加調査と短い紹介文の出発点になる核データを保てる
3. 運用上必要なレビュー、登録プロジェクトのマージ、判断根拠を保てる
4. 公開手段の第一候補であるapp record exportへ、紹介用の情報を再利用できる状態を保つ

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon完全互換を今すぐ満たすこと
- 不確かなapp metadataで`app`を埋めること
- exportの都合だけでcoreやopsの設計を歪めること
- 不確かな帰属やmergeを自動確定してreview件数だけを減らすこと

紹介フローを主導に置き、その延長でapp export可能性を確保する順序を維持します。

## 現在の段階

帰属・根拠・手動判断の責務分離を維持し、蓄積した情報が候補選定と予備調査の負担軽減にどう結びつくかを検討します。

## レビューで挙がった検討事項

### 既存評価を深掘り候補の選定と省力化の判断につなげる

目的は、有用な候補を得るまでのどこに取りこぼしや手戻りがあり、どの改善が日常の予備調査を楽にするかを判断することです。

外部情報源ごとの固有候補・取得範囲・処理時間、人が期待判断を定めた固定事例（manual gold）による判断規則の評価、紹介実績との比較、registryと独立した専用DB（sidecar）でのLexicon観測の追加価値は、それぞれ確認を進めてきました。既存の結果が次の問いまで答えているかを確認します。

- 発見・調査した情報が、利用者の深掘り判断に必要な時点で、選べる候補と判断材料として届いたか。
- 候補を判断するまでに、人がどの確認・訂正・再調査を行う必要があり、何が負担として残ったか。
- 選べなかった有用候補の原因が、情報源、取込条件、帰属判断、説明不足、推薦順位のどこにあるか。

既存記録で答えられる範囲と不足する範囲を分け、改善判断に必要な情報が欠ける場合だけ追加観測を検討します。紹介済みprojectの現在値から当時の発見・推薦を推定せず、未紹介projectも負例にしません。新しい評価期間や記録方法はここでは固定せず、追加の記録負担に見合う判断が得られるかも含めて検討します。

### 説明更新とservice観測の運用品質を確認する

説明の用途・技術的特徴と役立つ過去情報を保ち、時制・訂正を根拠どおり扱えるかを、実LLMの出力で確認します。終了告知、機能変更、元の誤記、一時障害の固定素材を通常triageへ通し、根拠IDの形式適合だけでなく文面を人が評価します。既存profileは来歴を推定で補わず、読み取り専用のsample確認から適用範囲を判断します。

Service observationの定期実行を有効にする前に、実URLでの限定確認により取得負荷・失敗率・期限到来件数を確認し、初期値の頻度と上限が運用に適するか判断します。具体的な未検証範囲と再開材料は[運用前確認](notes/temporal-description-validation.md)、現在の接続・確認範囲は[status](developer/status.md#説明紹介候補追跡)を参照します。全件再調査や定期実行の有効化は別判断とします。

## 条件付き短期候補

### 発見経路と未確定候補の入口

目的は、人が調べたい対象を発見した時点で保持でき、帰属が未確定でも次の確認へ進めることです。情報を拾うことと正しいprojectへ確定採用することは必要な根拠が異なるため、誤採用を防ぐ条件が発見自体を失わせていないかが論点です。

有用な対象が既存の情報源で拾えない、取り込み条件から外れる、または帰属判断の材料が足りず止まる実例がある場合に検討します。パス付きURLや独立したfork等は、取りこぼしの有無を確認する候補です。既存の情報源による補完も踏まえて、追加で拾える価値と増える無関係な候補を比較します。

手動の`Add seed`は、入力した値を対応する役割で採用する操作として意味を保ちます。未確定の発見を入れる必要があれば、操作の説明で足りるか、入口を分けるべきかを判断します。候補リポジトリの内容を確認する必要がある場合も、人の判断材料と確定プロフィールへの採用を分け、どの不明点を解消したいかに応じて扱いを決めます。

### Repository analysisの誤解釈を減らす

repositoryの役割とcollection利用を根拠どおり説明できる状態を目指します。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、package配布repositoryの役割、fixture Lexiconのdefine扱いは、通常運用で頻度または実害を確認した場合だけ再開します。再開時も固定sampleの追加を既定にせず、個別repositoryや既知NSIDに依存しない一般規則を優先します。

### 信頼・帰属policyの追加接続

判断原則は[Project境界](developer/specs/project-boundaries.md)、現行接続範囲は[status](developer/status.md)を参照します。接続済みのREADME公開先宣言によるservice採用について追加評価する場合は、既存の検証結果で判断できない実データ上の効果や残る手動確認を対象にします。全件の再解析・再採用は別判断です。

以下は未完了の別課題です。

- 汎用API・AppView独自APIの実在確認。endpoint・引数・応答契約の取得方法を決めてから接続する。現在のprotocol確認はorigin rootの標準AT Protocol describeServerに限定する。
- 代表サイト（`project_site`）の自動採用。サービスを利用する画面と説明資料の区別、代表URLの選択条件を検討する。
- 以下の広いrole-aware policyやLLM relation assessorの接続。公開先宣言による限定接続とは独立した目標として扱う。

役割に応じた信頼規則（role-aware trust policy）の適用を広げる際は、次を説明できる範囲だけ通常の自動判定へ接続します。

- 新policyによって省ける手動確認
- 新たに必要になるreviewと誤採用の危険
- project、developer、organization、publisherを推測しない入力契約
- 固定事例と正本DBから選んだ事例での判定差分
- 現行自動confirmからの切替範囲とrollback境界

候補の生成、信頼判定、識別子の採用、除外・保留、利用者の判断は、それぞれの責務を維持します。自動採用の可否は、関係の種類（predicate）・方向・役割・情報源・取得方法を共通の規則へ照合して決めます。収集処理ごとに`eligible_for_auto_confirm`を増やすことは避けます。

個別に残る設計候補です。

- 採用済みリポジトリから見つけた文書・プロジェクトサイト候補を、代表URLとして自動採用するための帰属・役割条件
- account role、publisher、provider owner、authority domainを構成要素やtrusted anchorと区別する契約
- collector観測、relation interpretation、semantic relation、検索用inverseの保存分担
- 同じidentifierの観測更新、新規project登録、既存projectへのidentifier追加、弱いsuggestionの境界監査

#### collectionの実利用と既存の採用判断を信頼確認へ使う

人がserviceとcollectionの対応を確認する負担を減らすため、以下を残る検証候補とします。採用済みLexiconと同じ直属authority・検証済み発行元による既存候補の信頼補強は[現行仕様](developer/specs/project-boundaries.md#承認済みlexiconと同じ発行元による信頼の補強)へ分けます。利用・権限要求・通常recordの保持と、schemaの所有・projectへの帰属は区別します。

- serviceが表示するコンテンツと公開recordの対応確認。AT URI、DID、record key、特徴的なfield等を手掛かりに、単なる文面一致や転載・汎用viewerとの区別を検討します。collectionを指定したOAuthの書込み権限要求と、実際の作成・表示の観測も区別します。匿名で確認できる範囲から、`uses_collection`や既存ownership candidateの信頼確認へ使えるか評価します。
- 採用済みrepository↔collection関係を起点とした、同じprefixの別collectionの帰属確認。現行の`defines_schema`保存はownership評価に依存するため、独立した定義位置の観測を確保し、この循環を解消できる場合に再検討します。複数project同居・vendor・test・共有schema・反対のoperator判断を区別し、prefix一致だけで所有を拡張しません。
- 確認済みproject/service accountによる通常record保持は低優先度とします。project境界の保証にはならないため、既存観測に対する追加の確認削減効果が見込める時に再検討します。account roleとserviceへの対応を確認し、developer個人accountや第三者利用を区別します。recordがあることは利用・関連の支持であって、namespace管理やschema所有、主張全体の真正性の証明にはしません。

手動判断済みの少数の正例と、共有viewer・第三者client・複数project同居repo等の反例を用意できた時に、既存観測で判別できる範囲と追加取得が必要な範囲を比較します。通常の自動採用へ進めるかは、省ける確認と誤採用の危険をsample reviewで判断します。現行の関係ごとの保証範囲は[Project境界](developer/specs/project-boundaries.md#関係別の静的規則)を参照します。

### project間の時間・派生関係

後継、買収、分割、統合、所有権移転、mirror、instance、tenant、forkは、プロジェクト間の関係として扱い、同一プロジェクト内の識別子の移転やマージと区別します。Basker / InfoEnon、DateSky / Nomareなどの実例を確認し、表示・検索・紹介文で必要になった時だけ保存形を追加します。

旧マージ推奨レビューで未完了の識別子の扱いを再検討する場合も、名前に含まれる目印だけで一括補正せず、実在・利用時期・公式な移行根拠を確認できた識別子を通常の編集経路で扱います。

### service URLと補助pageの分類

confirmed service URLへの過剰昇格が実測された場合、次を組み合わせて改善します。

- top pageからのlink、anchor text、redirect、canonical URL
- page title、meta description、本文のdocs / login / support / statusらしさ
- service root、app shell、auth entry、docs / support / status pageの役割
- operator decision、過去probe、anonymous browser結果
- Web pageを持たないappとbrowser extension等の配布先

path名だけで恒久的に分類せず、認証後の利用可能性は匿名browser検証から分離します。

### atproto寄与度と紹介候補の順位

紹介候補には、使えるserviceと、atprotoの設計や実装を知るうえで価値のある対象の両方を含めたいという目的があります。現在の寄与度はNSIDやschema等の根拠を重視するため、独自schemaを持つ付加機能と、独自NSIDを持たずprotocolを深く扱うlibrary / toolの価値を十分に表せない可能性があります。情報を確認しやすい対象だけが上位に並ばないかが論点です。

検討するのは、プロトコル利用の根拠の確認度、主要機能に占める利用深度、技術的な学習価値（technical interest）、サービス利用可否をそれぞれ何の判断に使うか、また一つの推薦順位にまとめるとき何を優先するかです。未確認候補を見せる時点や`draft`等の紹介準備の充足との関係も、利用者がどのような候補を選びたいかに照らして整理します。

既存の紹介実績分析やmanual goldから不足を確認し、実際の候補選定とずれる場合に評価規則・閾値の変更を検討します。有用候補の捕捉と不要なreviewの増減を併せて判断し、fieldの改名・追加を先に決めません。未紹介projectを負例にしません。

### 調査pipeline

実際の欠落や運用費が確認された項目だけを追加します。

- OAuth `include:` permission setの再帰解決と循環・取得失敗の記録
- repo contents、PDS、Bluesky sessionを含むcollectorのbudget / cache policy統一
- 調査履歴画面の根拠表示が、保存上限のために不足する具体例の収集
- 失敗が続く情報源へのアクセスを一時停止する仕組み（circuit breaker）を、複数プロジェクトの調査をまたいで保持すること

### 内部構造

#### DID exact lookupのcandidate hydration除去

現在は正規化した検索用データで候補を絞った後、候補projectの保存データから処理用の形を組み立て直し（hydrate）、検索用データにだけ残る候補DIDを除外します。次を満たした時だけ、検索用データのみで結果を返す方式（index-only）へ変更します。

- normalized-only `developer_did`を有効identifierとして扱うかstale rowとして除去するか決める
- current payloadから表現できない有効rowを失わない保存形またはrepairを用意する
- 既知の5 DID collisionを含む正本DB auditで期待projectとの差分が0になる
- exact DID lookupがproject hydrateを呼ばない回帰testを追加する

#### relation normalizeの性能

保存データ量が最大の調査実行記録で、関係の正規化（relation normalize）がデータ量の二乗に応じて長時間化する経路を再現できた場合、保存データの整理とは別の変更として改善します。

## 条件付き長期候補

### 検索

現行のnormalized検索で不足が明確になった場合、自然語・概念検索を優先します。

- 「ブログサービス」からblogging、publishing、CMS、article posting系projectを探すquery expansion
- project category、feature、summary evidenceを使う検索材料
- SQLite FTS、typo tolerance、OR / 否定条件
- 保存済み検索、URL queryへのfilter保持、pagination

raw JSON任意path検索は通常のproject探索に必要な具体例が出た場合だけ追加します。

### JetstreamでのLexicon publication観測

Jetstream v2で`com.atproto.lexicon.schema`を観測できるため、public recordの活動を集計する`sync-collections`では見つからないLexiconの存在検知に使える可能性があります。既存collectionの網羅性改善より、recordをまだ持たないschema、query / procedure / subscription等の非record Lexicon、Permissioned Data提案のspace typeとその`collections`宣言の発見を主な価値候補とします。

緊急性は低く、独立観測を続ける価値とregistry表現へ進む必要性を判断することが目標です。現在の観測状態は[status](developer/status.md#lexiconの独立観測)、保存・取得上限・publisher検証・periodの数え方は[sidecar catalog仕様](developer/specs/lexicon-publication-catalog.md)を参照してください。

今後は比較条件を満たした週（valid period）が4週分揃った時、固有候補数、再検証での変化、非record Lexiconを表現する需要を人が再評価し、registryへ新しい役割・関係の表現を設計する、専用DBでの観測を継続する、定期実行設定（preset）を停止する、のいずれかを決めます。4週分が揃う前でも、検証済み公開者による公開記録の削除、公開者DIDの置き換わり、具体的な非record利用要求があれば前倒しします。判定に使う保存signalは[再評価gate](developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)を参照してください。新規未登録authority / spaceは週次指標として蓄積し、即時の個別候補レビューにはしません。

非record Lexiconをregistryで追跡する具体的用途が生じた場合にだけ、project role / relation表現を別途設計します。その際はprojectへの一意な帰属、独立project候補、catalogだけの観測を区別してsample reviewします。全publisherの周期verify、自動project作成、identifier採用、ownership / promotion変更はこの観測フェイズの非目標です。

### Model構成の継続評価

年数回程度を目安にmodel構成を見直し、通常enrichの品質・安定性・処理時間・常駐コストに改善の余地があるか評価を続けたいと考えています。具体的な時期と候補は未定です。通常enrichの置換候補と低優先度処理用の候補を分け、既存の[評価手順](developer/workflows/llm-model-evaluation.md)で比較します。個別modelの[試用停止・再開材料](notes/llm-model-evaluation.md)を踏まえ、評価結果から既定modelを維持するか変更するかを判断します。

### 保存済み判断を使うLLM適応

operator decisionや人が確認した評価labelを、LLMの出力品質改善へ再利用できる可能性があります。ただし、`project_decisions`の多くはidentifier採否、reviewの保留・dismiss、別project判断などの運用上の正本であり、project-level triageやrepository解析のgold labelとはそのまま扱いません。

保存済みの訂正・保留・除外判断と当時のevidenceから、回帰評価用のfixture候補を自動抽出し、人が期待結果を確認してcorpusへ取り込める状態を目指します。学習用dataの蓄積と、heuristicやprompt変更の評価に再利用します。各候補は当時の根拠と判断理由を人が確認して期待結果を定め、現行の[replay・sanitize契約](developer/specs/investigation.md#replay-と-benchmark)へ接続します。抽出commandの設計は未確定です。

同種の誤りが蓄積し、プロンプトや解析素材の選択・入力上限の規則だけでは十分に減らせず、独立した人手確認済み事例を訓練用と評価用に分けられる量が揃った場合に、次を検討します。

- category、identifier・relation判定、repository解析、紹介文生成など、入力・出力契約が異なる処理を分けて学習対象と成功条件を定義する
- 判断時点のevidence、実際のoperator action、対象role、判断理由を対応付け、現在stateから結果だけを逆算したdataや、LLM出力を根拠にした派生fieldを教師へ混ぜない
- projectと時期を跨いだholdoutを確保し、prompt調整や学習に使ったcaseと未使用caseを分ける
- retrieval / few-shot example選択、model別prompt、後段の決定規則を先に比較し、その上でfine-tuning、LoRA、adapter等が固定評価、memory、latency、運用コストで実利を持つか確認する
- 学習済みmodelの出力も現行のschema・evidence検査とoperator reviewを通し、正本DBへの自動確定は別判断にする

採用判断と再現可能な評価用入力の集合（corpus）を作れることを着手条件とし、上記の比較に基づいて手法を選びます。

### 編集支援の拡張

人が根拠と編集意図を確かめながら、蓄積した説明を少ない手戻りで訂正できるようにすることが目的です。「説明に残す内容と時間経過の扱い」の議論を踏まえ、既存の編集手段では負担が大きい場合に、次を検討します。

- project単位の自然言語による訂正指示と、現在のprofile / evidenceをLLMへ渡す更新案生成
- name、tagline、summary、features、website、app links等への編集範囲拡張とfield単位の差分preview
- 採用した訂正指示と生成元の来歴保存、revision check、根拠表示

同種の個別訂正を通常triage promptの恒久ruleへ増やしません。identifier、relation、decisionの正本を直接JSON編集させず、既存review actionまたは専用mutationを使います。

### Web UI

- 日常フローと安全境界だけを案内する短いHelp導線
- 外部情報の取り込み・追跡状態レビューを、保存した進行状況（checkpoint）から再開すること
- サーバー再起動後のバックグラウンドジョブ一覧・進捗eventの復元
- 保存上限付きの根拠では判断材料が不足すると確認された場合の表示上限調整
- 同じ識別子の一つのroleを否定する操作で、残したい別roleまで失わないためのrole単位identifier decision。別々に採否判断したい実例がある場合に、操作の説明・表示で足りるか、保存契約を変える必要があるか検討する
- 詳細の前後移動とtab jumpに限ったkeyboard shortcut
- read-onlyのrelation / ownership graph

PWA / local app化は、Firefox中心の運用でもstandalone表示や軽いreload耐性に実利があり、manifestと単純なservice workerで済む場合だけ再評価します。オフラインDB操作、build step、複雑なcache invalidationは導入しません。

### 保存量とmigration

- DB allocated bytesが10 GiBを超えるかmaintenance競合が実測された場合のhistory DB分離
- job履歴がcheckpointだけでは不足した場合のevent永続化とretention policy
- 次のschema改版が具体化した時のincremental migration runner
- 複数DBまたは同一schema内のdata policy世代差が必要になった場合のDB-level policy marker

process間writer競合の論点は「並行する処理から蓄積した情報と判断を守る」に分けます。

DB-level policy markerを追加する場合は、open時に無条件付与せず、markerなしDBのread-only audit合格後にadditive updateします。現状の単一正本DBとoffline maintenanceのためだけには追加しません。

### GitHub公開の準備

将来的にrepository自体をGitHubで公開する可能性はありますが、優先度は低く、公開時期を決めるまでは準備作業を開始しません。公開を判断する前に、少なくとも次を確認します。

- 全commit、branch、tagを対象に、個人情報、credential、ローカル環境固有情報、公開対象外データが過去履歴へ含まれていないか監査する。現時点では混入の有無は未確認
- 履歴からの除去が必要な場合は、影響範囲、履歴書換え、既存cloneやbackupの扱いを決めてから実施する
- README、導入、初回起動、基本的な調査・review・maintenanceの導線を、人間がCodexの支援なしでも理解して試せるか確認し、Codex向けの暗黙知や内部運用メモに依存する箇所を整理する
- sample data、設定例、ライセンス、依存物の帰属、securityとsupportの境界を公開repository向けに確認する

ここでいうGitHub公開はsource repositoryの公開候補です。現在のWeb UIをインターネットへ公開することや、複数利用者向けサービスにすることは含みません。

### データmodelの拡張

developer node、provider account、native appのbundle / package / store identifier、record以外を含む汎用NSIDは、既存DID、handle、identifier role、relationで表現できない実例が確認された場合だけ追加します。

### その他

- cache / source budgetのread-only表示
- static JS / CSS / route tableが保守上の問題になった場合の分割
- LAN、個人利用、必要時Tailscale越しという前提に応じたsecurity / safety再評価
- plugin-style collector configuration
- Investigation notebook、Assisted review、LLM triage side panel、multi-project compare、review session
- bulk maintenance dashboard、source health dashboard
