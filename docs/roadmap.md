# Project Registry Roadmap

現在の実装状況は[status.md](status.md)、確定済みの保存形や責務分担は
[project-record.md](developer/project-record.md)、[registry-internals.md](developer/registry-internals.md)、
[investigation-model.md](developer/investigation-model.md)、
[project-attribution-and-trust.md](developer/project-attribution-and-trust.md)を正本とします。

この文書には、未完了の作業、着手条件、実施順だけを置きます。完了した比較結果や移行記録は
`status.md`または`docs/notes/`へ置き、現行仕様をroadmapへ重複させません。

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

identifier保存、Website / service / project site、repository帰属、promotion signal、信頼判定とmergeの
責務分離は一段落しました。次は判断モデルを広げるより、実運用の結果から精度、review効率、維持費を
確認する段階です。

直近は次の順序で進めます。

1. マージ推奨reviewとjob進捗表示の運用上の不便を解消する
2. その結果を見て、広いtrust policy、検索、編集、exportへ進む

## 直近の作業

### マージ推奨のreview

schema version 3で得たマージ推奨110件は、自動マージせず人間が判断します。次を満たすreview導線の
実装量と運用上の利益を比較します。

- 理由別またはtarget project別にまとめて表示する
- 比較対象を確認してから承認できる
- 保留と`separate_project`を保存できる
- candidate、同一developer、同一domainだけをmerge根拠にしない

既存queueを実際に処理するだけで十分なら、専用UIは追加しません。

### job進捗表示

`sync-introduced`のpage安全上限を総作業量のように`1 / 100`と表示する契約を修正します。処理本体と
watermark更新は正常なので、job進捗全体の再設計には広げません。

## 条件付き短期候補

### Repository analysisの残件

policy v14の通常sample確認は完了しています。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、
package配布repositoryの役割、fixture Lexiconのdefine扱いは、通常運用で頻度または実害を確認した場合だけ再開します。
再開時も固定sampleの追加を既定にせず、個別repositoryや既知NSIDに依存しない一般規則を優先します。

### 信頼・帰属policyの追加接続

判断原則と現行接続範囲は
[project-attribution-and-trust.md](developer/project-attribution-and-trust.md)を正本とします。schema version 3、
`project_repository`、`project_site`、bounded page-to-repository policyは接続済みです。

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

- accepted repositoryから代表documentationやproject siteへ明示的にlinkする場合の帰属条件
- account role、publisher、provider owner、authority domainを構成要素やtrusted anchorと区別する契約
- collector観測、relation interpretation、semantic relation、検索用inverseの保存分担
- 同じidentifierの観測更新、新規project登録、既存projectへのidentifier追加、弱いsuggestionの境界監査

### project間の時間・派生関係

後継、買収、分割、統合、ownership移転、mirror、instance、tenant、forkは、同一project内のidentifier
transitionやmergeへ押し込みません。Basker / InfoEnon、DateSky / Nomareなどの実例をreviewし、表示・検索・
紹介文で必要になった時だけ保存形を追加します。

### service URLと補助pageの分類

confirmed service URLへの過剰昇格が実測された場合、次を組み合わせて改善します。

- top pageからのlink、anchor text、redirect、canonical URL
- page title、meta description、本文のdocs / login / support / statusらしさ
- service root、app shell、auth entry、docs / support / status pageの役割
- operator decision、過去probe、anonymous browser結果
- Web pageを持たないappとbrowser extension等の配布先

path名だけで恒久的に分類せず、認証後の利用可能性は匿名browser検証から分離します。

### atproto寄与度

promotion queueの並びで不足が確認された場合、service利用可否とは別に次を改善します。

- `incidental`を確定できるevidence
- docs / READMEを材料にした主要機能と追加機能の区別
- LLMによる曖昧caseの補助分類
- owned surface、独自data model、technical interestの区別

紹介済みprojectと`sync-introduced`の結果をread-only分析し、未紹介projectを自動的な負例にはしません。

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

機能削減と主要な保存形整理が一段落したため、`docs/developer/`の未定義語、対象が曖昧な`run` / `review`、
古い説明を実例単位で整理します。全面翻訳や一括置換は行わず、コード、DB、enumと対応する用語は維持します。

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

緊急性は低く、恒久laneへ接続する前にboundedなread-only比較を行います。

- sealed tipまでのcreate / update / deleteをDIDとrkey単位で畳み、現行schema候補を作る
- recordのrkey、`id`、`lexicon`、主要definition typeを検査し、既存DBとの重複、未知authority group、
  `sync-collections`で未観測の宣言collectionを数える
- Jetstream eventだけをownership根拠にせず、exact `_lexicon` DNSが同じDIDを指し、current recordを再取得できる
  場合だけ正規publisher候補とする
- space typeはPermissioned Data proposal段階として観測し、`collections`は期待されるcollection集合の宣言として
  扱う。record活動、現行ownership、projectへの帰属を自動確定しない
- 未知schemaをすべて新規projectにせず、既存projectへの一意な帰属、独立project候補、schema catalogだけの観測を
  分けてsample reviewする

space typeの仕様が確定するか、read-only比較で固有候補が確認できた場合に、保存形、watermark、更新・削除追跡、
promotion signalへの接続を判断します。DNS委譲の変更はJetstream eventに含まれないため、別途再検証します。

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

### editorial qualityと編集

- project単位の自然言語による訂正指示と、現在のprofile / evidenceをLLMへ渡す更新案生成
- name、tagline、summary、featuresの差分preview
- 採用した訂正指示と生成元の来歴保存
- Webからのtagline、summary、website、app links編集をfield単位preview、revision check、根拠表示付きで追加

同種の個別訂正を通常triage promptの恒久ruleへ増やしません。identifier、relation、decisionの正本を
直接JSON編集させず、既存review actionまたは専用mutationを使います。

### app record exportとmetadata

app recordは公開手段の第一候補ですが、紹介用profileとconfirmed identifierからread-only validation reportを
先に作ります。不足fieldと根拠不足を報告し、不確かな`profile_json.app`をexportのために埋めません。

実例と公開先の要件が揃った場合、次を検討します。

- `images[]`、`langs[]`
- Lexiconのproduce / consumeとtest / prototype qualifier
- `account_indicators[]`
- 公開・保守状態を表す`app.status`

現行のapp link roleは[project-record.md](developer/project-record.md)を正本とします。

### Web UI

- 日常フローと安全境界だけを案内する短いHelp導線
- sync / reviewのcheckpoint再開
- server再起動後のprocess job一覧・event復元
- bounded evidenceで不足が確認された場合の表示上限調整
- 実例で必要になった場合のrole単位identifier decision
- 詳細の前後移動とtab jumpに限ったkeyboard shortcut
- read-onlyのrelation / ownership graph

PWA / local app化は、Firefox中心の運用でもstandalone表示や軽いreload耐性に実利があり、manifestと単純な
service workerで済む場合だけ再評価します。オフラインDB操作、build step、複雑なcache invalidationは導入しません。

### 保存量、migration、process排他

- DB allocated bytesが10 GiBを超えるかmaintenance競合が実測された場合のhistory DB分離
- job履歴がcheckpointだけでは不足した場合のevent永続化とretention policy
- 次のschema改版が具体化した時のincremental migration runner
- 複数DBまたは同一schema内のdata policy世代差が必要になった場合のDB-level policy marker
- 次の破壊的maintenance追加時、または重複writer事故が再現した場合のprocess間lock / preflight

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

## maintenance commandとして残すもの

通常のWeb操作へ移さず、必要時にCLIで実行します。

- `audit-source-config`によるhosted suffix split監査
- read-onlyを原則とする`audit-url-fields`
- 判定feedback後の既存DB cleanup audit
  - confirmed service URLに補助pageやdefer / block配下URLが残っていないか確認する
  - repoとNSIDの両方がacceptedではない`defines_schema` relationとowned NSIDを棚卸しする
  - dry-runで差分を出し、backup後にrepairまたは個別cleanupを実行する
