# Project Registry Roadmap

この文書は今後到達したい状態、達成の判断、着手・再検討条件、合意済みの依存・優先関係を示します。
列挙順を実施順とは扱いません。「ここまでできた」を追記せず、部分達成で残る目標が変われば項目を書き直し、
達成した目標は現役の項目から外します。現在の接続・適用・制約は[status](status.md)、
仕様と作業記録の更新先は[文書管理方針](documentation-policy.md)を参照してください。

## 目的の優先順位

1. 深掘りする価値がある候補を、背景と根拠とともに提示できる
2. 追加調査と短い紹介文の出発点になる核データを保てる
3. 運用上必要なreview、merge、evidenceを保てる
4. 公開手段の第一候補であるapp record exportへ、紹介用の情報を再利用できる状態を保つ

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon完全互換を今すぐ満たすこと
- 不確かなapp metadataで`app`を埋めること
- exportの都合だけでcoreやopsの設計を歪めること
- 不確かな帰属やmergeを自動確定してreview件数だけを減らすこと

紹介フローを主導に置き、その延長でapp export可能性を確保する順序を維持します。

## 現在の段階

帰属・根拠・手動判断の責務分離を維持し、蓄積した情報が候補選定と予備調査の負担軽減にどう結びつくかを
検討します。詳細な実装・適用状況は[status](status.md)を参照します。

## レビューで挙がった検討事項

### 並行する処理から蓄積した情報と判断を守る

定期調査と手動操作を併用しても、後から保存された古い調査結果によって、人の訂正や別の調査成果を失わないことが
目的です。単一利用者でも別processの同時実行はあり、重複runの実績があります。ただし、更新消失や中断原因は
未確認であり、重複実行そのものと分けて扱います。

検討するのは、どの操作の組合せで何を失い得るか、既存の保存時検査でどこまで防げるか、追加の制御が必要かです。
必要な場合も、人のreviewや通常調査を妨げずに守る範囲を決め、排他方式や全write pathの変更を先に固定しません。
根拠は[並行実行の確認範囲](status.md#jobとweb運用)、現行契約は
[background-jobs.md](developer/specs/background-jobs.md)を参照します。

### 既存評価を深掘り候補の選定と省力化の判断につなげる

目的は、有用な候補を得るまでのどこに取りこぼしや手戻りがあり、どの改善が日常の予備調査を楽にするかを判断することです。
評価の実施自体や計測項目を増やすことを目的にしません。

[source評価](notes/source-lane-evaluation-2026-08-30.md)は固有候補、取得範囲、処理時間等から情報源の価値を、
[manual gold・紹介実績分析](developer/specs/investigation.md#replay-と-benchmark)は判断規則と紹介候補の捕捉を、
[Lexicon sidecar評価](developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)は追加観測の固有価値と
継続・拡張の必要性を既に扱っています。これらを重複して評価するのではなく、結果が次の問いまで答えているかを確認します。

- 発見・調査した情報が、利用者の深掘り判断に必要な時点で、選べる候補と判断材料として届いたか。
- 候補を判断するまでに、人がどの確認・訂正・再調査を行う必要があり、何が負担として残ったか。
- 選べなかった有用候補の原因が、情報源、取込条件、帰属判断、説明不足、推薦順位のどこにあるか。

既存記録で答えられる範囲と不足する範囲を分け、改善判断に必要な情報が欠ける場合だけ追加観測を検討します。
紹介済みprojectの現在値から当時の発見・推薦を推定せず、未紹介projectも負例にしません。新しい評価期間や
記録方法はここでは固定せず、追加の記録負担に見合う判断が得られるかも含めて検討します。

### 説明に残す内容と時間経過の扱い

蓄積した情報を後から再利用するには、現在使えるかだけでなく、そのprojectが何を提供し、どのような技術的特徴を
持っていたかが分かる必要があります。撤退したserviceの説明を「アクセス不可」だけに置き換えると、調査で得た
理解を失います。一方、提供終了や機能変更を反映しなければ、過去の説明を現在の事実として読ませてしまいます。

更新頻度や編集UIを決める前に、次の内容を検討・議論します。

- summary・tagline等に何を書くか。用途・機能・技術的特徴の説明と、時点を伴う稼働・公開状態をどう分担させるか。
- 提供終了、主要機能の変化、再活発化があったとき、過去の説明を何のために残し、現在の説明へ何を反映するか。
  当時は正しかった説明と、もともと誤っていた記述の訂正を区別する。
- 一時的な取得失敗、古い成功根拠、確認済みの提供終了をどう表すか。取得できないことだけで既知の用途や特徴を消さず、
  過去の成功も現在の稼働保証にはしない。
- 人の編集意図を保ちながら、事実の変化に伴う見直しをどう判断するか。また、`stable`や`out_of_scope`の
  追跡方針を見直す条件を、説明文の更新やserviceの状態とどう区別するか。

提供終了しても説明を残したいservice、機能が変わったproject、一時的に取得できない対象を例に、読み手に何が
伝わるべきかを合意します。その内容に基づいて更新規則・保存形・訂正手段の不足を判断し、設計途中の議論は
`docs/notes/`、確定した説明の更新規則は[project評価](developer/specs/project-evaluation.md)、時間情報の保存は[project record](developer/specs/project-record.md)へ反映します。

## 条件付き短期候補

### 発見経路と未確定候補の入口

目的は、人が調べたい対象を発見した時点で保持でき、帰属が未確定でも次の確認へ進めることです。情報を拾うことと
正しいprojectへ確定採用することは必要な根拠が異なるため、誤採用を防ぐ条件が発見自体を失わせていないかが論点です。

有用な対象が既存sourceで拾えない、取込条件で落ちる、または帰属判断の材料が足りず止まる実例がある場合に検討します。
path付きURLや独立したfork等は確認候補であり、取りこぼしを断定しません。既存sourceによる補完も踏まえて、
追加で拾える価値と増えるnoiseを比較します。

手動の`Add seed`は確認済みroleの採用として意味を保ちます。未確定の発見を入れる必要があれば、操作の説明で
足りるか、入口を分けるべきかを判断します。candidate repositoryの内容を確認する必要がある場合も、人の判断材料と
確定profileへの採用を分け、どの不明点を解消したいかに応じて扱いを決めます。

### Repository analysisの誤解釈を減らす

repositoryの役割とcollection利用を根拠どおり説明できる状態を目指します。
拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、
package配布repositoryの役割、fixture Lexiconのdefine扱いは、通常運用で頻度または実害を確認した場合だけ再開します。
再開時も固定sampleの追加を既定にせず、個別repositoryや既知NSIDに依存しない一般規則を優先します。

### 信頼・帰属policyの追加接続

判断原則は[Project境界](developer/specs/project-boundaries.md)、現行接続範囲は
[status](status.md)を参照します。接続済みのREADME公開先宣言によるservice採用について追加評価する場合は、
既存の検証結果で判断できない実データ上の効果や残る手動確認を対象にします。全件の再解析・再採用は別判断です。

以下は未完了の別課題です。

- 汎用API・AppView独自APIの実在確認。endpoint・引数・応答契約の取得方法を決めてから接続する。
  現在のprotocol確認はorigin rootの標準AT Protocol describeServerに限定する。
- 代表project_siteの自動採用。serviceとして使えるsurfaceと資料の区別、代表URLの選択条件を検討する。
- 以下の広いrole-aware policyやLLM relation assessorの接続。公開先宣言による限定接続とは独立した目標として扱う。

広いrole-aware trust policyは、次を説明できる範囲だけproductionへ接続します。

- 新policyによって省ける手動確認
- 新たに必要になるreviewと誤採用の危険
- project、developer、organization、publisherを推測しない入力契約
- fixed caseと正本DB sampleでの差分
- 現行自動confirmからの切替範囲とrollback境界

candidate生成、信頼判定結果、identifier採用、block / defer、operator判断は別責務のまま維持します。
`eligible_for_auto_confirm`をcollectorごとに増やさず、predicate、方向、role、source / methodを中央policyへ
照合します。

個別に残る設計候補です。

- accepted repositoryからのdocumentation / project site候補について、代表URLとして自動採用するための帰属・役割条件
- account role、publisher、provider owner、authority domainを構成要素やtrusted anchorと区別する契約
- collector観測、relation interpretation、semantic relation、検索用inverseの保存分担
- 同じidentifierの観測更新、新規project登録、既存projectへのidentifier追加、弱いsuggestionの境界監査

### project間の時間・派生関係

後継、買収、分割、統合、ownership移転、mirror、instance、tenant、forkは、同一project内のidentifier
transitionやmergeへ押し込みません。Basker / InfoEnon、DateSky / Nomareなどの実例をreviewし、表示・検索・
紹介文で必要になった時だけ保存形を追加します。

旧マージ推奨reviewで未完了のidentifier処遇を再開する場合も、名称markerだけで一括補正せず、実在・利用時期・
公式な移行根拠を確認できたidentifierを通常の編集経路で扱います。

### service URLと補助pageの分類

confirmed service URLへの過剰昇格が実測された場合、次を組み合わせて改善します。

- top pageからのlink、anchor text、redirect、canonical URL
- page title、meta description、本文のdocs / login / support / statusらしさ
- service root、app shell、auth entry、docs / support / status pageの役割
- operator decision、過去probe、anonymous browser結果
- Web pageを持たないappとbrowser extension等の配布先

path名だけで恒久的に分類せず、認証後の利用可能性は匿名browser検証から分離します。

### atproto寄与度と紹介候補の順位

紹介候補には、使えるserviceと、atprotoの設計や実装を知るうえで価値のある対象の両方を含めたいという目的があります。
現在の寄与度はNSIDやschema等の根拠を重視するため、独自schemaを持つ付加機能と、独自NSIDを持たずprotocolを深く
扱うlibrary / toolの価値を十分に表せない可能性があります。情報を確認しやすい対象だけが上位に並ばないかが論点です。

検討するのは、protocol evidenceの確認度、主要機能に占める利用深度、technical interest、service利用可否を
それぞれ何の判断に使うか、また一つの推薦順位にまとめるとき何を優先するかです。未確認候補を見せる時点や
`draft`等の紹介準備の充足との関係も、利用者がどのような候補を選びたいかに照らして整理します。

既存の紹介実績分析やmanual goldから不足を確認し、実際の候補選定とずれる場合に評価規則・閾値の変更を検討します。
有用候補の捕捉と不要なreviewの増減を併せて判断し、fieldの改名・追加を先に決めません。未紹介projectを負例にしません。

### 調査pipeline

実際の欠落や運用費が確認された項目だけを追加します。

- OAuth `include:` permission setの再帰解決と循環・取得失敗の記録
- repo contents、PDS、Bluesky sessionを含むcollectorのbudget / cache policy統一
- investigation viewerのbounded evidenceで不足する具体例の収集
- 複数projectを跨ぐ永続circuit breaker

### 内部構造

#### DID exact lookupのcandidate hydration除去

現在はnormalized indexで候補を絞った後、候補projectだけをhydrateしてnormalized-only candidate DIDを
除外します。次を満たした時だけindex-onlyへ変更します。

- normalized-only `developer_did`を有効identifierとして扱うかstale rowとして除去するか決める
- current payloadから表現できない有効rowを失わない保存形またはrepairを用意する
- 既知の5 DID collisionを含む正本DB auditで期待projectとの差分が0になる
- exact DID lookupがproject hydrateを呼ばない回帰testを追加する

#### relation normalizeの性能

最大payload runでrelation normalizeが二乗的に長時間化する経路を再現できた場合、storage cleanupとは
分けて改善します。

### 開発文書の用語整理

`docs/developer/`の未定義語、対象が曖昧な`run` / `review`を実例単位で整理します。現行仕様の不整合の確認とは
区別し、全面翻訳や一括置換は行わず、コード、DB、enumと対応する用語は維持します。

## 条件付き長期候補

### 検索

現行のnormalized検索で不足が明確になった場合、自然語・概念検索を優先します。

- 「ブログサービス」からblogging、publishing、CMS、article posting系projectを探すquery expansion
- project category、feature、summary evidenceを使う検索材料
- SQLite FTS、typo tolerance、OR / 否定条件
- 保存済み検索、URL queryへのfilter保持、pagination

raw JSON任意path検索は通常のproject探索に必要な具体例が出た場合だけ追加します。

### JetstreamでのLexicon publication観測

Jetstream v2で`com.atproto.lexicon.schema`を観測できるため、public recordの活動を集計する
`sync-collections`では見つからないLexiconの存在検知に使える可能性があります。既存collectionの網羅性改善より、
recordをまだ持たないschema、query / procedure / subscription等の非record Lexicon、Permissioned Data提案の
space typeとその`collections`宣言の発見を主な価値候補とします。

緊急性は低く、独立観測を続ける価値とregistry表現へ進む必要性を判断することが目標です。
現在の観測状態は[status](status.md)、判断材料は
[比較記録](notes/jetstream-lexicon-publication-comparison-2026-09-04.md)と
[初期運用評価](notes/lexicon-publication-catalog-initial-operations-2026-09-05.md)を参照してください。
保存、取得上限、publisher検証、periodの数え方は[sidecar catalog仕様](developer/specs/lexicon-publication-catalog.md)を正本とします。

今後は4つの異なる比較可能periodが揃った時、固有候補数、再検証変化、非record表現需要を人が再評価し、
registryへ新しいrole/relation表現を設計する、sidecar観測を継続する、presetを停止する、のいずれかを決めます。
4期間前でも検証済みpublisherのdelete / identity置換、具体的な非record利用要求があれば前倒しします。
判定に使う保存signalは[再評価gate](developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)を参照してください。
新規未登録authority / spaceは週次指標として蓄積し、即時の個別候補reviewにはしません。

非record Lexiconをregistryで追跡する具体的用途が生じた場合にだけ、project role / relation表現を別途設計します。
その際はprojectへの一意な帰属、独立project候補、catalogだけの観測を区別してsample reviewします。
全publisherの周期verify、自動project作成、identifier採用、ownership / promotion変更はこの観測フェイズの非目標です。

### 保存済み判断を使うLLM適応

operator decisionや人が確認した評価labelを、LLMの出力品質改善へ再利用できる可能性があります。ただし、
`project_decisions`の多くはidentifier採否、reviewの保留・dismiss、別project判断などの運用上の正本であり、
project-level triageやrepository解析のgold labelとはそのまま扱いません。

同種の誤りが蓄積し、promptやbounded material契約だけでは十分に減らせず、独立した人手確認済みcaseを
訓練用と評価用に分けられる量が揃った場合に、次を検討します。

- category、identifier・relation判定、repository解析、紹介文生成など、入力・出力契約が異なる処理を分けて
  学習対象と成功条件を定義する
- 判断時点のevidence、実際のoperator action、対象role、判断理由を対応付け、現在stateから結果だけを逆算した
  dataや、LLM出力を根拠にした派生fieldを教師へ混ぜない
- projectと時期を跨いだholdoutを確保し、prompt調整や学習に使ったcaseと未使用caseを分ける
- retrieval / few-shot example選択、model別prompt、後段の決定規則を先に比較し、その上でfine-tuning、LoRA、
  adapter等が固定評価、memory、latency、運用コストで実利を持つか確認する
- 学習済みmodelの出力も現行のschema・evidence検査とoperator reviewを通し、正本DBへの自動確定は別判断にする

手法を先にLoRAへ固定せず、採用判断と再現可能な評価corpusを作れることを着手条件とします。

### 編集支援の拡張

人が根拠と編集意図を確かめながら、蓄積した説明を少ない手戻りで訂正できるようにすることが目的です。
「説明に残す内容と時間経過の扱い」の議論を踏まえ、既存の編集手段では負担が大きい場合に、次を検討します。

- project単位の自然言語による訂正指示と、現在のprofile / evidenceをLLMへ渡す更新案生成
- name、tagline、summary、features、website、app links等への編集範囲拡張とfield単位の差分preview
- 採用した訂正指示と生成元の来歴保存、revision check、根拠表示

同種の個別訂正を通常triage promptの恒久ruleへ増やしません。identifier、relation、decisionの正本を
直接JSON編集させず、既存review actionまたは専用mutationを使います。

### Web UI

- 日常フローと安全境界だけを案内する短いHelp導線
- sync / reviewのcheckpoint再開
- server再起動後のprocess job一覧・event復元
- bounded evidenceで不足が確認された場合の表示上限調整
- 同じ識別子の一つのroleを否定する操作で、残したい別roleまで失わないためのrole単位identifier decision。
  別々に採否判断したい実例がある場合に、操作の説明・表示で足りるか、保存契約を変える必要があるか検討する
- 詳細の前後移動とtab jumpに限ったkeyboard shortcut
- read-onlyのrelation / ownership graph

PWA / local app化は、Firefox中心の運用でもstandalone表示や軽いreload耐性に実利があり、manifestと単純な
service workerで済む場合だけ再評価します。オフラインDB操作、build step、複雑なcache invalidationは導入しません。

### 保存量とmigration

- DB allocated bytesが10 GiBを超えるかmaintenance競合が実測された場合のhistory DB分離
- job履歴がcheckpointだけでは不足した場合のevent永続化とretention policy
- 次のschema改版が具体化した時のincremental migration runner
- 複数DBまたは同一schema内のdata policy世代差が必要になった場合のDB-level policy marker

process間writer競合の論点は「並行する処理から蓄積した情報と判断を守る」に分けます。

DB-level policy markerを追加する場合は、open時に無条件付与せず、markerなしDBのread-only audit合格後に
additive updateします。現状の単一正本DBとoffline maintenanceのためだけには追加しません。

### GitHub公開の準備

将来的にrepository自体をGitHubで公開する可能性はありますが、優先度は低く、公開時期を決めるまでは
準備作業を開始しません。公開を判断する前に、少なくとも次を確認します。

- 全commit、branch、tagを対象に、個人情報、credential、ローカル環境固有情報、公開対象外データが
  過去履歴へ含まれていないか監査する。現時点では混入の有無は未確認
- 履歴からの除去が必要な場合は、影響範囲、履歴書換え、既存cloneやbackupの扱いを決めてから実施する
- README、導入、初回起動、基本的な調査・review・maintenanceの導線を、人間がCodexの支援なしでも
  理解して試せるか確認し、Codex向けの暗黙知や内部運用メモに依存する箇所を整理する
- sample data、設定例、ライセンス、依存物の帰属、securityとsupportの境界を公開repository向けに確認する

ここでいうGitHub公開はsource repositoryの公開候補です。現在のWeb UIをインターネットへ公開することや、
複数利用者向けサービスにすることは含みません。

### データmodelの拡張

developer node、provider account、native appのbundle / package / store identifier、record以外を含む汎用NSIDは、
既存DID、handle、identifier role、relationで表現できない実例が確認された場合だけ追加します。

### その他

- cache / source budgetのread-only表示
- static JS / CSS / route tableが保守上の問題になった場合の分割
- LAN、個人利用、必要時Tailscale越しという前提に応じたsecurity / safety再評価
- plugin-style collector configuration
- Investigation notebook、Assisted review、LLM triage side panel、multi-project compare、review session
- bulk maintenance dashboard、source health dashboard
