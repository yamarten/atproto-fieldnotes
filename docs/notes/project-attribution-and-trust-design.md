# 登録プロジェクトへの帰属と信頼グラフの設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。この文書は、信頼グラフ（実装名: Trust Graph / `trust_graph`）の接続規則を決める前に必要な原理、2026-08-13までの議論で合意した方針、レビュー済みの接続マトリクスを保持します。identifierの採用理由、former、non-production、確定transition、noiseを分離するschema version 3は実装・複製DB検証済みですが、正本DBへの切替と本書の正文化は未実施です。マトリクス中の仮称relationや未実装policyは、比較結果のreviewなしに正本挙動へ反映しません。

## この設計が扱うこと

このツールは、人が行っていたatproto関連プロジェクトの調査を、収集と判断に分けて機械化します。判断の中心には次の二つがあります。

- 識別子が調査対象の登録プロジェクトに属する構成要素か
- 収集した情報が、その登録プロジェクトの説明や「何であるか」の判断に役立つか

信頼グラフは、前者について作られた「この識別子はこの登録プロジェクトの構成要素である」という帰属主張を受け取り、その主張がどの直接関係と関係経路によって支持されるかを評価します。構成要素候補は帰属policyが作り、説明に役立つ情報は調査・profile policyが選びます。

概念上の処理は次の順です。

1. 外部情報を観測として収集する
2. 関係の意味を解釈し、構成要素、構成要素ではない関係者、関連情報、未分類の観測に分ける
3. 構成要素などのroleを主張できる対象だけを識別子候補にする
4. 信頼グラフが、そのrole付き帰属主張を直接関係と関係経路から評価する
5. 評価結果、block/deferなどの採用を止める条件、手動判断を採用規則が組み合わせ、採用可否を決める

候補生成と信頼評価は、同じrelationを参照する場合があっても別の判断です。二つの登録プロジェクトをマージする判断も、既存の囲いへ構成要素を追加する判断とは前提が異なるため、別policyとして扱います。

## 登録プロジェクトの暫定的な意味

ここでいう登録プロジェクトは、所有されたLexicon、主要実装、source repository、提供service、native appなどを、一つの説明・追跡対象としてまとめる編集上の単位です。互いに異なる種類のresourceを、来歴・管理・提供目的などの関係に基づいて一つの囲いへ収めます。実装・保存上の`project record`は、この登録プロジェクトを表すrecordです。以下の開発上の説明では`project`または`project record`を使います。

Lexicon、実装、repository、service は互いに別の resource ですが、関係が適切なら同じ project record の構成要素になれます。一方、同じdeveloperが関与する、同じcollectionを利用する、同じrepositoryへ言及する、といった事実だけでは同じ project record に収めません。

この定義は、将来の mirror、instance、複数project同居repo、fork、template、派生 project の境界を判断する際にも使います。これらの詳細は未決ですが、同じcode、同じowner、同じdomainのいずれか一つだけでproject同一性を確定しないことを出発点にします。

project recordは、説明と追跡に十分な代表的resourceを保持する編集記録です。構成要素の完全なinventoryではなく、未収集のservice surface、API endpoint、補助repositoryが残ることを許容します。たとえばBlueskyについて`bsky.app`と主要repositoryがあれば、`api.bsky.app`や`video.bsky.app`まで必ず収集する必要はありません。

serviceを持たない共有Lexicon/specification initiativeもprojectになれます。`standard.site`や`lexicon.community.*`のような集合は、共通のspecification、governance、repository、release、管理主体に基づいて一つのprojectとして扱えます。wildcard表記自体はidentifierではありません。

## 区別する判断

### 資源同一性（resource identity）

二つのURL、DID、handleなどが、同じresourceまたはそのcanonical aliasを指すかを扱います。

### 登録プロジェクトへの帰属（project attribution）

あるresourceを、このregistryで同じproject recordへ所属させるべき構成要素と判断するかを扱います。resource同士が同一物である必要はありません。

### 信頼（trust）

「このresourceはこのprojectの構成要素である」という主張が、騙りや偶然のlinkではなく、信頼できるsourceと関係で裏付けられているかを扱います。

### マージ（project merge）

独立して形成された二つのproject recordが、同じ説明・追跡単位を二重に表しているかを扱います。既存projectへ新しい構成要素を追加するconfirmationとは非対称性と前提が異なるため、信頼グラフのpathをそのままmerge scoreへ流用しません。

### 説明用情報の評価

developer account、dependency、used collectionなど、projectの説明や調査には役立つが、通常はproject identifierにしない情報を扱います。これは信頼グラフの主目的ではありません。

これらは順番に一つの結論へ収束する評価段階ではありません。たとえば、developer accountとの関係が事実でも、そのaccountを登録プロジェクトの構成要素にするとは限りません。また、同じrepository identityであることを確認できても、repositoryを共有する二つの登録プロジェクトをマージするとは限りません。

## 信頼グラフの責務

信頼グラフは、role付きの帰属主張と識別子間の直接関係を入力に、どの識別子と情報源がどの方向から主張を支持しているかを評価し、根拠となる経路を説明可能にする機構です。本設計では、直接出力を主張単位の信頼判定結果（内部名: Trust verdict）と位置づけ、採用規則がその結果を採用判断へ利用します。

graph nodeには次が混在し得ます。

- `confirmed`または`candidate`のproject identifier
- developer DIDやorganization accountなど、構成要素ではないidentifier
- protocol解決の中継点
- claimの対象

developer DIDやauthority domainは、登録プロジェクトの構成要素候補でなくても、帰属主張の根拠または経路の中継点としてgraph nodeになります。

信頼判定結果は、主張がどの程度支持されているか、どの方向の確認が欠けているか、矛盾または再取得があるかを表します。最終的な採用は、登録プロジェクトへの帰属、信頼判定結果、採用を止める条件、手動判断を組み合わせて決めます。

後半の接続マトリクスは直接relationを重複なく棚卸しし、用途はその後の静的policy表で示します。同じrelationを候補生成と信頼評価が参照する場合も、それぞれの利用可否を別のpolicy列で判断します。

### 責務境界

- 帰属規則が、観測からrole付きの識別子候補を作る
- 信頼グラフが、既に型付けされた帰属主張の支持状況を評価する
- 採用規則が、信頼判定結果、採用を止める条件、手動判断を組み合わせて採用可否を決める
- マージ規則が、二つのproject recordを同じ編集単位としてまとめるかを判断する
- graph nodeであること自体は、project identifierへの登録、構成要素への帰属、信頼判定の起点にできることのいずれも意味しない

## この文書で使う設計用語

ユーザー向けの基本用語は[`docs/user/guide.md`](../user/guide.md)を正本とし、編集方針は[`user-facing-terminology.md`](user-facing-terminology.md)に従います。この開発メモではコードと照合できるよう、必要な箇所だけ内部名を併記します。

- 帰属主張（claim）: 「このidentifierは、このroleで、この登録プロジェクトに属する」という判断対象
- role: identifierが登録プロジェクトにとって何であるかを表す。identifier自体の種類（kind）とは別
- role確定済み候補（typed candidate）: identifier kindとroleが定まった識別子候補。未分類linkや検索hitは含まない
- 直接関係（direct relation）: 一つの観測が直接示す関係。複数の関係から推移的に作った関係は含まない
- 関係経路（path）: 複数の直接関係を向きに沿ってつないだもの
- graph-only node: project identifierとして保存・採用されていない、帰属主張の根拠または経路の中継用node
- 信頼判定の起点（root anchor）: 手動判断または承認済みの登録起点によって採用され、新しい帰属主張を評価する経路の出発点にしてよいrole付きidentifier
- 採用を止める条件（guard）: block、deferなど、自動採用を禁止または保留する条件
- 信頼判定結果（Trust verdict）: 信頼グラフによる帰属主張単位の評価結果。identifierを変更する処理そのものではない
- 導出識別子（derived identifier）: 別の観測から機械的に導出された識別子。導出されたことだけでは信頼判定の起点にならない

## 外部対象・グラフ上の点・登録プロジェクトでのroleを分ける

接続マトリクスでは、外部世界にある対象、その対象を指すgraph node、登録プロジェクトとの関係を表すroleを別の層として扱います。同じ対象でもprojectとの関係は複数あり得て、node kindだけからroleは決まりません。

| 対象 | この文書で指すもの | 現行graph node | 登録プロジェクトとの関係の例 |
| --- | --- | --- | --- |
| service surface | serviceを利用・操作するために到達するURL上の面。serviceという機能のまとまりやdeployment/instance全体とは区別する | `service_url`。値はsurfaceのURL | projectが提供する代表的surfaceは`service`。第三者client、tenant、mirror、補助pageは別relationまたは観測になり得る |
| repository | source code、schema、文書、build定義などを版管理する外部resource | `repo_url`。値はrepositoryを参照するURL | projectのsourceを含む場合は`source_repository`。dependency、fork、mirror、単なる参照は別relation |
| ATProto account | DIDを安定した識別子、handleを変更可能な現在名として持つ論理的なaccount | account全体のnodeは置かず、`did`と`handle`を別nodeとして保持する | project account、developer、organization、publisher、service operatorなど。roleはidentity解決だけから決めない |
| domain | DNS名。service host、handle、NSID authority、`did:web` identityなど複数の文脈に現れる | `domain` | 文脈に応じてservice host、handle、authority、中継点などになる。domainというkindだけではproject roleは決まらない |
| NSID / record collection | AT Protocolのnamespaced identifier。現行設計では、そのうちrecord collectionを表すfull NSIDを主対象とする | `collection`。値はcaseを保持したfull NSID | projectが所有する場合は`owned_nsid`。利用、schema参照、permission要求、record作成はownershipと別relation |
| native app | bundle/packageとして配布・実行されるapp。backend serviceやdistribution pageとは別resource | 現行6 kindには専用nodeがない | projectの構成要素になり得るが、bundle/package identifier、store URL、backendとの関係は保留 |

### 資源同一性の補足

resource identityは、二つのidentifierが同じ外部resourceまたはその時系列上のaliasを指すかを扱います。repositoryではproviderの不変repository IDを共有するrename/transfer前後、accountでは相互検証されたDIDとcurrent handle、service surfaceではcanonical URLや移転履歴が代表例です。mirror、fork、同一content、同じowner、同じhostは、それぞれ別resource間でも成立する関係として扱います。

現行graphは観測されたidentifierをnodeとして保持し、aliasらしい値を暗黙に一つへ畳みません。identityを確認する直接relationと、projectへの帰属relationを分けます。

### 登録プロジェクトでのrole

project roleは「外部対象がこのproject recordにとって何であるか」という主張です。`source_repository`や`owned_nsid`は対象自体の種類ではなく帰属の意味を表し、developer、dependency、mirrorなどとの関係は同じkindのnodeに対しても別に記述できます。role、`candidate` / `confirmed`、根拠のprovenanceはそれぞれ別の軸として評価します。

## 収集対象の暫定分類

### 1. 登録プロジェクトの構成要素

同じproject recordへ、特定roleで所属させる対象です。

- owned Lexicon / NSID
- sourceまたは主要実装repository
- operated serviceの代表的なservice surface
- native app。bundle/package identifierとdistribution URL、backend serviceは別resourceとして扱う
- その他、一つの説明・追跡対象として扱うresource

### 2. 構成要素ではない関係者

帰属主張の裏付けや説明には使えますが、通常はproject identifierにしません。

- developer account
- organization
- publisher DID
- service operator

専用のproject accountはproject自身を表す構成要素になり得ます。一方、複数projectを代表するorganization accountは、複数projectに関係するaccountとして保持します。publisher DIDをdeveloper、project、organizationのいずれかへ自動的に読み替えません。現行roleとの対応と移行方向は、後述する実装差分として分けて扱います。

### 3. 関連情報

projectとの関係は事実でも、そのprojectの構成要素ではない対象です。

- used collection
- dependency
- upstream、template、mirror
- docs、distribution、supporting page

### 4. 観測・調査先

意味や帰属をまだ判断していない収集結果です。

- README mention
- search result
- 未分類link
- 追加取得先としてだけ必要なidentifier

identifier kindだけでは分類を決めません。同じrepo URLでも、source repository、dependency、mirrorではprojectとの関係が異なります。

## `candidate`と`confirmed`の意味

`candidate` identifierは、role付きの主張をまだ採用・信頼していない状態です。candidateの対象はroleによりproject構成要素にも、developer accountなどの補助情報にもなり得ます。raw search result、未分類link、単なるmentionはrole確定済みの帰属主張になっていないためcandidateではなく観測です。project record内に保存されていることだけを理由に信頼判定の起点にしたり、そこからの関係を強い根拠とみなしたりしません。

`confirmed` identifierは、そのrole付き主張が採用可能かつ信頼できると判断された状態です。confirmedの意味を構成要素への所属だけへ狭めません。confirmedな補助actorも存在できますが、それだけでproject構成要素または信頼判定の起点になるわけではなく、用途はroleごとの規則で決めます。

roleは「何を主張しているか」、candidate/confirmedは「その主張を採用・信頼したか」です。`derived` / `heuristic`は本来、導出方法やprovenanceであり、Trust状態とは別の軸です。現行の`classification = confirmed | candidate | derived | heuristic`は二つの軸を混在させているため、保存契約の差分監査対象とします。

candidate生成を厳密にする目的は、project境界とreview品質を保ち、後段で誤用される対象を減らすことです。信頼評価上の主要な安全条件は、candidate、graph-only node、導出識別子を信頼判定の起点にしないことと、candidate自身の自己申告だけで自動採用を成立させないことです。

## kind別の帰属規則

前節のnode modelを前提に、各kindについて現在合意している帰属・共有・信頼上の制約を補足します。以下は対象そのものの網羅的な定義ではなく、接続マトリクスをレビューするための暫定policyです。個別事例や現行6 kindへの表現方法は、将来の保存形式や分類をそのまま固定するものではありません。

identifierについて、次の性質を同じ意味として扱いません。

- projectの構成要素か
- 人間向けにprojectを代表するか
- 他projectとの共有を許すか
- discoveryまたはlookupの入口に使えるか
- Trust評価のanchorに使えるか

### Repository

repositoryは一つのprojectに複数存在でき、一つのrepositoryが複数projectにまたがることもあります。複数project同居repo、共通runtime、mirror、forkがあるため、confirmed repositoryの一致だけでprojectを自動マージしません。ArabicaとOolongのように、一つのrepo/runtimeに別projectが同居した実例があります。repoの帰属は「そのrepoにprojectのsourceが含まれる」を意味し、repo全体の排他的所有を意味しません。

package、subdirectory、manifest、Lexicon pathなどはrelation contextとして保持できますが、共通runtimeのように一つのpathへ切れない場合もあるため必須にはしません。接続マトリクス確定前に新しいidentifier kindを追加しません。

provider上のrenameまたはtransferで不変repository IDが同じ場合は、project attributionではなくresource identityとURL履歴の問題です。fork、mirror、対等なGitHub/Tangled hostingは別repository間のcode lineageまたはreplicaであり、関係だけでは同じprojectへ帰属させません。両方が公式sourceとして直接示される場合は、各repoをprojectへ独立に帰属させればよく、repo間edgeは必須ではありません。

### NSID / collection

confirmed `owned_nsid`はprojectの構成要素であり、信頼判定の経路上で使えますが、人間向けにprojectを代表するとは限りません。一つのprojectは複数NSIDを所有できます。独立した信頼判定の起点にできるかは、採用根拠を含む後続の採用規則で決めます。

current ownershipでは、同じfull NSIDは最大一つのprojectにだけ帰属するものとして扱います。他projectによる利用、互換実装、参照はownershipではなくrelationです。二つのprojectが同じconfirmed `owned_nsid`を持つ場合、直ちにmergeせず、重複record、誤帰属、分割ミス、ownership移転のreview対象にします。

repository内にLexicon JSONが存在することだけでは、そのprojectによるNSID ownershipを意味しません。

同じ直属namespace、すなわちLexicon解決単位に属するfull NSIDは、既定では同じprojectへ帰属させます。たとえば`dev.idiolect.recommendation`と`dev.idiolect.adapter`は同じ単位です。一方、`network.comind.memory`と`network.comind.void.memory`のような親子namespace、同じroot domain、似た名前だけではまとめません。明示的な手動判断（operator decision）または別governanceの確認は既定規則より優先します。

collectionを観測した時点で暫定projectを作る現行運用は、調査の入口として許容します。このprojectはcollection/namespaceの調査単位であり、authority domain自体をserviceまたはprojectとして採用したものではありません。真のcollectionに騙りや未使用collectionが少数混ざることより、別projectを誤って同居させること、atprotoと無関係なdomainをprojectへ引き込むことを優先して防ぎます。

### Service surfaceとnative app

一つのserviceに複数surfaceがあり得ますが、project recordには説明と追跡に十分な代表的surfaceを保持できればよく、網羅を要求しません。customer tenant、self-hosted instance、mirror、proxyは通常は関連観測であり、独自にbrand付けされ追跡対象となる場合だけ別projectになり得ます。

同じ正規化済みservice URLは通常一つのprojectの構成要素ですが、同じhostまたはdomainを複数projectが共有することはあります。native appはserviceとは別の構成要素です。bundle/package/extension identifier、store URL、backend serviceのどれをnode化するかは、現行6 kindのマトリクス確定後の保留事項です。第三者clientが同じbackendまたはprotocolを利用するだけなら別projectです。

### Domain

domainはservice host、handle、NSID authority、`did:web` identityなどで共有されるため、domain node自体をprojectの構成要素やactorとして扱いません。完全一致したhost、handle、authority間のrelationは信頼経路の中継に使えますが、subdomain、同じregistrable domain、兄弟hostは構造または調査情報として扱います。

### Account、DID、handle

handleとDIDはgraph上では別node kindですが、双方向検証後は一つの論理的なATProto accountを表します。DIDは安定identifier、handleは変更可能な現在名です。handle resolverとDID documentの二つの主張が一致した場合だけcurrent identityを確認し、片方向だけのaliasはconfirmed identityにしません。DID/handle対応はaccount identityを確認しますが、projectへの帰属根拠が二つ得られたとは数えません。

project accountはproject自身を表す場合に限り構成要素になり得ます。developer/organization accountは複数projectで共有可能なactorです。accountのroleをidentity解決、repo ownership、schema publicationだけから推定しません。

## 直接関係と経路の合意済み原則

### 関係とidentifierの状態を分ける

graph nodeはedgeのendpointまたはpathの中継点です。node自体にcandidate/confirmed分類が暗黙に付くわけではなく、edge classificationと`project_identifiers`のrole/classificationは別の主張です。protocolまたは一次情報が入力identifierに対して特定の相手identifierを直接返す場合、その直接relationを保存します。pathが存在するという理由で、恣意的な推移edgeを新しく作りません。

### 候補生成と信頼評価を分ける

role確定済み候補を作れるのは、原則としてconfirmedなproject identifierから対象identifierへの、静的に許可された直接の帰属関係がある場合です。対象identifierからconfirmed identifierへの自己申告方向は根拠として保存できますが、それだけではcandidateを作りません。`uses`、`mentions`、名前一致、registrable domain一致、host suffix、任意README linkは帰属関係へ昇格しません。

この原則は、collectorがcollection観測から暫定projectを作る入口まで禁止するものではありません。暫定projectの生成と、既存projectへ別kindのidentifierをcandidate admissionする処理を区別します。

### Sync時の既存projectへの取込を分ける

source syncが観測を既存projectへ割り当てる処理は、重複projectを作らないための単なる検索に見えますが、新しいidentifierを追加する場合はproject attributionと採用判断を含みます。マージ規則で後から修復することを前提にせず、次の四つを分けます。

1. 既に同じcurrent identifierがあるprojectの観測を更新する
2. 承認済みの収集入口から、そのidentifierを起点とする新規projectを作る
3. 既存projectへ、それまで無かった新しいrole付きidentifierを追加する
4. 弱い一致を既存projectのsuggestionまたは新規project候補として残す

1は同じresourceについての追加観測であり、project境界を広げません。2では、たとえばcollection inventoryにrecordが存在するcollectionを起点に暫定projectを作り、そのcollectionを登録起点として採用できます。これは「collectionを中心に調査するprojectを作った」というbootstrapであり、別の既存projectとの同一性を主張しません。

3は既存projectの囲いを広げるため、collectorのmatch scoreだけでconfirmedへ追加しません。許可済みの直接関係からrole付きcandidateを作り、通常の信頼・採用規則へ渡します。承認済みsourceにrole固有のbootstrap契約がある場合も、target projectを一意に選べる規則と、そのclaimを採用できる規則を別々に示します。

collection syncでは、同じ直属namespace、すなわち同じLexicon解決単位に属するfull NSIDを同じprojectへまとめる合意済み規則を適用できます。既存のcurrent confirmed `owned_nsid`から一意なtargetが決まり、block/defer、former、non-production、noise、`separate_project`と衝突しない場合は、新しいcollectionをそのprojectへ採用できます。新規project作成時と既存projectへの追加時のどちらも、collection sourceによる採用理由と登録起点の資格を持てます。ただし信頼判定ではfull NSIDごとに独立した根拠が増えたとは数えず、直属namespace単位の同じ論理的な起点として扱います。

完全一致以外のroot domain、registrable namespace、service host family、名前、owner/developer account、repository homepageは、それだけでtarget projectを確定しません。一意なsuggestion、candidate admissionに許可された直接relation、または新規project候補として残します。複数projectに同じcurrent identifierがあるなど、resource identityの段階でtargetが競合する場合は、どれかを選んだり第三のprojectを作ったりせずcollision reviewへ送ります。

4で弱い一致を持つ新規project候補を作ることは許容します。弱い一致は別projectにも重複recordにも成立するため、新規作成したこと自体を`separate_project`判断や後続マージの正の根拠にしません。syncは既存projectを自動マージせず、マージ規則は作成後に残った重複候補を独立に評価します。

全体policyが定めるのは、完全一致、既存囲いの拡張、登録起点、曖昧一致、guardの扱いです。各collectorには、観測した関係の意味、provenance、role、どのmatch kindを返せるか、そのsourceを特定roleのbootstrapとして承認できるかを定めます。これによりcollector固有の検出改善を待たずに境界を固定し、個別collectorの誤分類はreviewから一般則へ戻せます。

### 自動採用は信頼グラフ全体の往路と復路を見る

自動採用はcandidateと信頼判定の起点の間に、静的に許可された強い往路と復路があることを要求し、中継nodeを許します。往路と復路で同じpredicateを使う必要はありません。たとえばserviceからcollectionへのOAuth permissionと、collectionからauthority domain、完全一致service hostへ戻るpathを組み合わせられます。

自動採用済みidentifierも、confirmed project identifierとして後続のcandidate生成に使え、信頼経路のendpointまたは中継点になれます。ただし、そこから行う後続の自動採用は、手動判断または承認済みの登録起点まで根拠を追跡できることを要求します。毎回元の起点から再探索するか、元の起点と判定経路を引き継いで再利用するかは、後続設計と実測で決めます。

信頼グラフに保存する各edgeは、subjectからobjectへの意味上の保証を表します。一つの観測が両方向を保証する場合は、同じprovenanceを持つ二本の方向付きedgeへ展開し、往路と復路の両方に利用できます。一方向の保証から検索用の逆引きを作る場合は、逆向きedgeを信頼グラフへ保存しません。両方向で意味が異なる場合は、handle resolverの`handle_resolves_did`とDID documentの`did_document_claims_handle`のように別predicateで表します。これらはaccount identityを双方向確認できますが、project attributionの根拠が二つ得られたことにはなりません。

candidate、graph-only node、導出識別子は、それだけでは信頼判定の起点にしません。自動採用済みidentifierやconfirmedなdeveloper/organization/publisherも、元の根拠から切り離された独立の起点にはしません。実測した入次数・出次数によるscoreは導入せず、predicate、方向、subject role、object role、中継可能性を静的規則として定義します。

### 完全一致と階層関係を分ける

service URLの正確なhost、domainとhandleの完全一致は強いpathの一部にできます。subdomain、同じregistrable domain、兄弟hostは構造または調査情報であり、強いpathには使いません。shared hosting、tenant、organization配下の別projectを巻き込まないことを優先します。

### 資源同一性と時間関係を分ける

HTTP redirect、repository rename/transfer、handle変更、serviceのtest版から正式版への昇格、旧domain、projectの改名・後継は時間情報を必要とします。ただし、時間情報を一つの状態へまとめると、同じresourceか、同じ登録プロジェクトか、現在の構成要素か、現在到達可能かが混同されます。次の軸を別々に判断します。

| 判断すること | 問い | 例 |
| --- | --- | --- |
| 登録プロジェクトの境界 | 変化の前後を同じ説明・追跡単位に収めるか | projectの改名か、別projectによる後継か |
| resource identity | 前後のidentifierが同じ外部resourceを指すか | provider上の同一repositoryのrename、同じDIDのhandle変更 |
| projectに対するidentifierの立場 | 現行構成要素、旧構成要素、開発・試験用、無関係なnoiseのどれか | 旧service URL、preview URL、誤記NSID |
| identifier間の変化 | 何から何へ、どの種類の変化が確定したか | rename、replacement、test版から正式版への切替 |
| 観測時点の状態 | 取得時点で到達・利用・解決できたか | HTTP 404、parked domain、schema recordの存在 |

`current`は、現在のproject recordでそのrole付き関係を有効なclaimとして扱うという編集上の立場です。candidate/confirmedとは別軸で、構成要素roleのconfirmed claimなら現在の構成要素を表します。到達可能、一般公開中、唯一の代表URL、最新観測で正常という意味ではありません。一つのprojectに複数のcurrent service surfaceやrepositoryがあっても構いません。

`former`は、過去にそのprojectの構成要素だったという正の関係を残しつつ、現在の構成要素とTrust入力から外す立場です。提供終了したservice URL、repository rename前のURL、過去handleなどが候補です。旧identifierからprojectを検索できるようにしますが、それ自体を信頼判定の起点にはしません。旧URLがredirectで現在も到達可能な場合や、反対に現行URLが一時的に落ちている場合もあるため、`former`とlive/deadは独立です。

`non_production`は、開発・試験用としてprojectとの正の関係は確認できるものの、productionの構成要素としては採用しない立場です。過去にcurrentだったことを含意しません。staging、preview、dev、tempなどを細分化せず、識別できる範囲でこの一分類へまとめます。後から同じidentifierをproductionとして正式採用した場合はcurrentへ移せます。開発用identifierを残す主な目的は、collection syncによる再作成の抑止と将来の検索・監査であり、Trust経路へ加えることではありません。

noiseは、そのprojectと正の関係を持たない既知の誤検出です。`app.bsky.post`のような誤記NSID、`com.example.test`のようなplaceholder、無関係だが繰り返し検出されるURLが該当します。過去に有効だった`former`やprojectに関係する`non_production`と同じ分類にはしません。

live/deadやHTTP応答、schema recordの存在は、原則として取得時点を伴う観測です。project全体の`service_status`は複数観測を人間向けに要約した値であり、各identifierの時間上の立場を代替しません。特にcollectionの「live」を一般化するには、schemaの解決、recordの存在、現在の作成実績など複数の意味があり得るため、この設計では定義しません。

#### 同じ登録プロジェクトとして継続する場合

名称、repository、service surface、domain、handle、NSID、実装が変わっても、説明・追跡対象としての提供物、管理上の連続性、利用者に対する位置付けが継続しているなら、同じproject recordの更新として扱えます。全構成要素が同一である必要はありません。旧identifierを`former`へ移し、新identifierをcurrentとして独立に採用し、変化が裏付けられた場合だけ両者のtransitionを記録します。

提供終了したservice URLは、過去に正当な構成要素だったなら`former`で保持します。移転先が確認できる場合は新しいidentifierをcurrentとして追加します。旧URLが終了したことと、新URLが同じprojectへ属することは別々に判断し、前者だけから後者を自動採用しません。

#### 別の登録プロジェクトとして扱う場合

旧projectのcodeやLexiconを引き継いでいても、別の提供物、管理、対象範囲、brandとして独立に追跡する方が説明しやすい場合は別project recordにします。この場合の「後継」「派生」「移管」はproject間の関係であり、一つのproject内のidentifier transitionへ押し込みません。共有repository、同じowner、redirect、同一contentのいずれか一つだけでは、同じprojectか別projectかを確定しません。

Basker→InfoEnon、DateSky→Nomareは、時間関係を検討する既知事例ですが、この文書ではまだ同一projectとも別projectとも確定しません。調査時には次の三通りを区別します。

| 調査結果 | project recordの扱い | identifierの扱い |
| --- | --- | --- |
| 同じ提供物の改名・移転と確認できる | 同じprojectを継続 | 旧identifierを`former`、新identifierをcurrentとし、裏付けがあればtransitionを記録 |
| 別の提供物が旧projectを引き継いだと確認できる | 別projectを維持 | 各projectのidentifierを独立に採用し、将来のproject間successor/derivation関係として扱う |
| 公開情報だけでは区別できない | 現状を維持してreviewを残す | redirect、名前一致、同一contentだけからtransitionやmergeを作らない |

#### identifier transitionの意味

transitionは、二つのidentifierが並存する事実ではなく、「旧identifierから新identifierへのrenameまたはreplacementが確定した」という説明・監査用の関係です。同じproject record内の変化だけを対象とし、次の用途へは直接使いません。

- 新identifierのcandidate生成または自動採用
- 旧identifierのTrust復活
- project recordの自動マージ
- 前後のresourceが同一物だという一律の推定

repositoryの不変provider IDはresource identityを直接支持できますが、service URLのreplacementは通常、異なるresource間のproject内交代です。このため、transitionを記録しても前後のidentifierをaliasとして一つへ正規化しません。transitionの種類、根拠参照、分かる場合の発効時点を保持し、根拠参照なしには確定しません。

raw redirectや同一content表示は観測であり、それだけでcanonical alias、同じservice、同じproject、確定transitionとはしません。同じcontentはalpha版、mirror、copy、共通templateでも生じます。redirect、公式announcement、旧・新surfaceの相互参照、providerの不変IDなどを個別の直接事実として保持し、意味が十分明らかな場合またはoperator review後にだけtransitionへ確定します。

現行schema version 3の`project_noncurrent_identifiers`と`project_identifier_transitions`は、同一project内のformer/non-productionと確定transitionを保持する器です。project間のsuccessor、買収、分割、統合、派生関係はまだ表現せず、必要性と実例のreview後に別途設計します。この時間整理のために、新しいidentifier kindまたはroleは追加しません。

### 関係の品質を三段階に分ける

- 正式な参照・直接保証: protocolが入力identifierに対して特定の相手を返す、またはprojectが制御するsurfaceがSource、official account、official service等として相手を明示する関係。Admissionまたは強いTrustの候補になる
- 共有可能な正しい参照: `uses_collection`、dependency、mirror、provider owner、developer/organization actorなど、relation自体は事実でもprojectへの排他的帰属を意味しない関係。説明・調査または限定したTrustに使う
- mention/noise: 任意README link、example、test、vendor、検索hit、名前一致、共通hostなど。観測sourceとしては残せるが、Admission、auto-confirm、マージには使わない

「一次情報に書かれている」ことだけで正式な帰属参照にはなりません。そのlinkがsource、official account、利用、依存、単なるmentionのどれを意味するかをpredicateで区別します。

## Identifier kind接続マトリクス

行をsubject kind、列をobject kindとします。ここでは、帰属候補の生成、信頼評価、またはpath endpointのresource identity確認に使う正の接続関係を扱います。projectの構成要素同士だけでなく、DID、handle、domainのような情報の発信元、identity表現、中継点も含みます。一方、project同一性を否定する材料、マージを抑止する材料、説明・診断・調査だけに使う観測は外に分けます。`現行`は現在の信頼グラフまたは`project_relations`に存在するpredicate、`仮称`は今回の議論でrelationの意味を区別するために置いた名前です。セル内で状態が異なる名前はグループを分けて表示します。`なし`は、現在のproject判断に使う直接relationを設けないセルです。

| subject \ object | `service_url` | `repo_url` | `handle` | `did` | `domain` | `collection` |
| --- | --- | --- | --- | --- | --- | --- |
| `service_url` | 現行: `service_canonical`、`redirects_to`。仮称: `official_alternate_surface` | 現行: `service_links_repo` / `source_repository` | 現行: `official_account`。明示されたaccount role | 現行: `official_account`。明示されたaccount role | 現行: `service_exact_domain` | 現行: `requests_permission` / `uses_collection` / `defines_schema`。`defines_schema`は明示的な定義claimに限定 |
| `repo_url` | 現行: `repo_links_service` / `source_repository` | 仮称: `same_repository_identity` | 仮称: provider ownerまたは明示的account link。GitHubではprovider accountの中継が必要 | 仮称: Tangled owner DID、または検証済みprovider account link。現行`did_links_repo`の逆引きだけでは生成しない | なし | 現行: `defines_schema` / `uses_collection` |
| `handle` | 仮称: accountからの明示的なservice claim。Admissionには使わない | 仮称: accountのrepo link/ownership。Admissionには使わない | なし。変更履歴は時間関係 | 現行: `handle_resolves_did` | 現行: `handle_exact_domain` | なし。DIDを中継する |
| `did` | 現行: `operates_service`（`project_relations`）。明示claimに限定 | 現行: `did_links_repo`（Tangled、auto-confirm対象外）などのrepo link/ownership。Admissionには使わない | 現行: `did_document_claims_handle` | なし。follow等は保留 | 仮称: `did_web_identity_domain`。PDS endpointはproject関係に使わない | 現行: `did_publishes_schema`。仮称: `has_record_in`、`earliest_observed_creator` |
| `domain` | 現行: `domain_exact_service` | なし | 現行: `domain_exact_handle` | 仮称: `_lexicon`の`lexicon_authority_resolves_to_did`、`did:web` document公開 | なし | 現行: `domain_publishes_lexicon_record`は複合事実。設計上はauthority/DID/schemaへ分解 |
| `collection` | 一般的な直接relationなし。authority domainを中継 | 仮称: canonical sourceの明示参照だけ。検索hitは不可 | なし。DIDを中継 | 仮称: authority解決がpublisher DIDを返す`collection_resolves_to_did`。通常record事実の逆引きだけでは生成しない | 現行: `collection_authority_domain` | 仮称: `same_lexicon_authority` |

### マトリクスの読み方

- `service_url`はservice全体ではなくservice surfaceのidentifierです。本書では曖昧な`service`単体表記を避けます。
- handleとDIDは別nodeですが同じ論理的accountを表し得るため、account relationの保存先は観測sourceに応じて分かれます。
- `domain_publishes_lexicon_record`は現行実装のpredicateです。DNSによるpublisher DID委譲と、そのDID repoにmatching schema recordがあることを一つに畳んでいるため、新設計では別事実として扱う案です。
- 同じ観測から両方向のedgeを生成する場合、両edgeに同じsource、locator、methodなどのprovenanceを保持します。由来の共通性は往路・復路への利用を妨げません。
- `collection`は現行どおりrecord collectionを主対象とします。permission-set、XRPC、record以外のLexiconへ一般化する場合は`nsid` nodeとsubtypeを将来検討します。

### マトリクス外の関係と観測

主マトリクスから外すことは、観測自体を捨てることや保存を禁止することを意味しません。帰属・信頼の正のpathへ混ぜず、用途に応じた別のpolicyで扱います。

#### 系譜・複製・マージ抑止

`fork_of`はrepo間のcode lineageを表す候補relationですが、あるidentifierがprojectへ帰属することや、その帰属主張が信頼できることを支持しません。このため、帰属・信頼の正の接続マトリクスには含めません。

用途は、似たrepoを同じprojectとして自動マージしないための負の証拠です。ただし、provider上のforkという事実だけで別projectだと断定はしません。独自service surface、domain、collection、accountなど別projectとしてのidentityも観測できた場合は強い抑止にし、それらが不明な場合はmerge confidenceを下げてreviewへ回す材料にします。系譜の表示や調査には利用できますが、AdmissionとTrust pathには利用しません。

`mirror_of`、replica、対等なGitHub/Tangled hostingも、それだけではprojectへの帰属やTrustを支持しません。双方を公式source repositoryとして扱う場合は、各repoについてprojectからの直接参照を別々に確認します。repo間の複製・同期関係は、系譜表示、同期診断、マージreviewへ使う別relationとして保持できます。

派生project、template/example由来、source-only forkを含む詳しい扱いは[派生関係の設計メモ](derivation-relations.md)で保持します。現行実装には`fork_of` predicateはなく、providerから得たfork metadataとreview heuristicが近い情報を扱っています。predicateとして保存するか、マージ判断専用の事実として保存するかは未確定です。

#### 説明・調査用の関係

- 現行`mentions_collection`は、repo内のdocs、example、test、検索hitなどでcollectionを観測した事実です。`defines_schema`や`uses_collection`へ昇格できない参照を失わないために保持できますが、Admission、Trust、Mergeには使いません。
- schema dependency/referenceは実装やLexiconの説明には役立ちますが、collection ownershipを支持しません。依存先と同じprojectであることも意味しません。
- 同一contentの表示はalpha版、mirror、copy、共通templateを区別できないためrelation edgeとして保存しません。必要なら取得時点の観測または比較結果として扱います。
- 任意のHomepage、Source、profile URLをrepo↔domainの汎用relationにはしません。意味を判別できたものだけをservice、account、source repositoryなどのrelationへ型付けします。

#### 構造・基盤・診断

- handleがdomainのsubdomainであること、domain間の`subdomain_of`、同じregistrable domain、兄弟hostは構造情報です。完全一致とは区別し、Admissionまたは強いTrust pathに使いません。
- 親子namespaceまたは共通rootはNSID文字列の構造情報です。直属Lexicon authorityが同じ場合とは区別し、Admission、Trust、Mergeに使いません。
- accountのPDS/service endpointは利用中のidentity infrastructureを示すだけで、accountまたはprojectによるdomainの所有・運営を示しません。診断情報としてのみ扱います。
- DID間のfollow、mention、同じPDS、keyや過去handleの共有は、現時点ではidentityまたはproject Trustのrelationにしません。

#### 外部媒体に掲載された主張

開発者の個人site、account profile、Bluesky投稿などは、現時点では新しいproject componentやgraph node kindではなく、account→service、account→repoなどのclaimを観測したsource/locatorとして考えます。個人site自体がprojectの公式service surfaceでもある場合だけ、別途component候補になり得ます。

これらの媒体から帰属主張を抽出する方法、発言者DIDとの結び付け、特定projectについての主張か単なる紹介か、投稿の更新・削除・時間経過、developerまたはorganizationによる主張をどこまで根拠として認めるかは、今回の6 kind接続マトリクスでは決めません。将来、情報源別の取得・品質規則として設計し、意味を確定できた帰属主張だけを既存の有向relationへ入力します。

## 関係別の静的規則

`Admission`はrole確定済みのcandidate claimを作れるか、`Trust`は強いpathまたは補助pathに使えるか、`Merge`はprojectの自動マージに使えるかを表します。ここには主マトリクスに残したrelationだけを置き、上記の観測・抑止材料はそれぞれの別規則で扱います。いずれもrelation単独の扱いです。`条件付き`の具体的条件は意味欄に記します。Relation欄の`現行` / `仮称`は接続マトリクスと同じ意味です。名前をまだ置いていない行は、保存するrelationの必要性またはpredicate名自体が未確定です。現案ではrelation単独で自動マージを許す行はありませんが、設計上の非対称性を確認できるよう`Merge`列は明示的に残します。

| Relation | 向き・多重度 | 意味と強さ | Admission | Trust | Merge | 観測・表示 |
| --- | --- | --- | --- | --- | --- | --- |
| 公式alternate（仮称: `official_alternate_surface`） | service↔service、多対多 | 同じprojectが公式に提示した別surface。網羅不要 | 条件付き可 | 強い。別の復路が必要 | 不可 | 可 |
| canonical service URL（現行: `service_canonical`） | service↔service、currentは同一観測から両方向 | 同一hostのHTTP→HTTPSまたはsame-site redirectをcanonical identityとして扱う現行edge。raw redirectと区別する | identity条件を満たす場合可 | identity確認。現行条件の妥当性は時間関係と併せて再監査 | project mergeではない | URL履歴として可 |
| redirect（現行: `redirects_to`） | service→service、時間込みで多対多 | status、chain、時刻付きHTTP観測。同一exact host内の`/`→`/login`等は同じsurface内のpath差として扱い、外部login/support/payment等は除外 | confirmed surfaceから条件付き可 | 単独不可。別predicateの復路が必要 | 不可 | 可 |
| serviceがsource repoを明示（現行: `service_links_repo` / `source_repository`） | service→repo、多対多 | Source/Repositoryと明示された正式参照 | 可 | 強い | 不可 | 可 |
| repoが公式serviceを明示（現行: `repo_links_service` / `source_repository`） | repo→service、多対多 | description、README、manifest等でprojectとの対応範囲が明確 | 可 | 強い | 不可 | 可 |
| serviceがaccountを明示（現行: `official_account`） | service→handle/DID、多対多 | project/developer/operator等。role不明なら未分類relationとして保持 | roleを明示できる場合可 | role policy次第 | 不可 | 可 |
| accountがserviceをlink（現行: `operates_service`） | handle/DID→service、多対多 | accountによるservice claim。profile/bio/post等からの抽出・品質評価は別policy | 原則不可 | project-scopedな明示claimなら独立candidateへの逆方向evidence | 不可 | 可 |
| service URLのexact host（現行: `service_exact_domain` / `domain_exact_service`） | service↔domain、serviceは多対一、domainは一対多 | path付きURLでも正確なhostを採る。同じ観測が両方向を保証する。shared tenant guardを適用 | domain candidateにはしない | 強い中継 | 不可 | 可 |
| OAuth permission（現行: `requests_permission`） | service→collection、多対多 | 利用とatproto relevanceを示すがownershipではない | owned NSID candidateにはしない | exact authority/service domain等と組み合わせて強い | 不可 | 可 |
| collection利用（現行: `uses_collection`） | service/repo→collection、多対多 | read/write/queryの実利用 | owned NSID candidateにはしない | 補助。role/context次第 | 不可 | 可 |
| collection定義（現行: `defines_schema`） | repoまたは明示source→collection、多対多 | matching schema idとproject-scoped contextを持つ。vendor/generated/test/exampleを除外 | 可 | 強い | 不可 | 可 |
| 同じprovider repository identity（仮称: `same_repository_identity`） | repo URL↔repo URL、currentは1対1 | rename/transfer前後で不変provider IDが同じ。resource identity | attribution外 | identity確認 | project mergeではない | URL履歴として可 |
| repo provider owner（仮称） | repo→actor、repoごとに多対一、actorは一対多 | GitHub account/Tangled DIDの所有。maintainerは多対多で別 | roleを推定しない | actor identity確認後に補助 | 不可 | 可 |
| accountからrepoへのlink/ownership（現行: `did_links_repo`等） | handle/DID→repo、多対多 | project-scopedなrepo claim、provider ownership、Tangled owner等。profile/postからの抽出・品質評価は別policy。accountが複数projectに関与し得る | 不可 | 独立candidateへの補助evidence | 不可 | 可 |
| handle resolver（現行: `handle_resolves_did`） | handle→DID、current validatedは1対1 | DNS/HTTPSが指すDID | counterpart補完 | DID document claimと組み合わせidentity確認 | project merge不可 | 可 |
| DID document handle claim（現行: `did_document_claims_handle`） | DID→handle、current validatedは1対1 | 先頭の有効な`at://` handle | counterpart補完 | handle resolverと組み合わせidentity確認 | project merge不可 | 可 |
| exact handle/domain（現行: `handle_exact_domain` / `domain_exact_handle`） | handle↔domain、完全一致 | validated handleと同じdomain文字列 | 単独不可 | 強い中継 | 不可 | 可 |
| Lexicon authority delegation（仮称: `lexicon_authority_resolves_to_did`） | domain→DID、currentはdomainごとに1 DID、DIDは複数domain可 | `_lexicon` DNSが返すpublisher DID。schema存在とは別 | roleを推定しない | 中継 | 不可 | 可 |
| `did:web` identity domain（仮称: `did_web_identity_domain`） | DID↔domain、DIDは一つのdomain/path、domainは複数DID可 | domainがDID documentを公開 | roleを推定しない | 中継 | 不可 | 可 |
| schema publication（現行: `did_publishes_schema`） | DID→collection、DIDは一対多、collectionはcurrentで一publisher | matching Lexicon schema recordを公開 | 未決。publisherというだけでは不可 | 強い | 不可 | 可 |
| 通常record所有（仮称: `has_record_in`） | DID→collection、多対多 | DID repoでcollectionのrecordを観測 | 不可 | confirmed project accountなら支持。他roleは弱い | 不可 | 可 |
| earliest observed creator（仮称） | DID→collection、多対多 | 観測範囲内の最初。絶対的first creatorではない | 単独不可 | 時刻/source horizon付き補助 | 不可 | 可 |
| collection authority domain（現行: `collection_authority_domain`） | collection→domain、多対一 | NSID構文から一意に導出 | 不可 | 中継 | 不可 | 可 |
| collection authority resolution（仮称: `collection_resolves_to_did`） | collection→DID、現時点の想定はcollectionごとに1 DID、DIDは一対多 | 入力collectionのauthorityに対するDNS解決がpublisher DIDを直接返す。通常recordの逆引きとは別 | roleを推定しない | 中継 | 不可 | 可 |
| domainによるLexicon record公開（現行: `domain_publishes_lexicon_record`） | domain→collection、一対多 | DNSで委譲されたpublisher DIDにmatching schema recordがある複合事実 | 不可 | 現行では強い。新設計ではauthority delegationとschema publicationのpathへ分解 | 不可 | 可 |
| collectionがcanonical source repoを明示（仮称） | collection→repo、多対多 | protocol/schema/specが特定repoを公式sourceとして直接返す場合だけ | 条件付き可 | 強い | 不可 | 可 |
| 同じ直属Lexicon authority（仮称: `same_lexicon_authority`） | collection↔collection、authority内で多対多 | 同じ解決単位。pairwise edgeは保存せず導出可能 | confirmed collectionから可 | 支持するが単独auto-confirm不可 | 自動不可、重複はreview | 可 |

### schemaの一致を確認する代表経路

matching schemaの取得は一つのrelationではなく、DNS委譲とschema record実在という二つの直接事実からなるpathです。

```text
authority domain --lexicon_authority_resolves_to_did--> publisher DID
publisher DID --did_publishes_schema--> collection
```

このpathを評価に使っても、authority domainからcollectionへの新しい推移edgeは作りません。現行の`domain_publishes_lexicon_record`をこの二つへ分解するかは、保存契約の検討事項です。

### OAuth scopeを使う代表経路

confirmed service surface `S`がcandidate collection `C`をOAuth scopeで直接要求し、collection authority domainがserviceのexact hostと一致する場合、次の別方向pathを評価できます。

```text
往路: S --requests_permission--> C
復路: C --collection_authority_domain--> D --domain_exact_service--> S
```

`C -> S`という推移edgeは保存しません。`requests_permission`はcollection ownershipを主張しないため、それだけでowned NSID candidateを生成しません。当面、collectionは`sync-collections`等のcollection観測経路から形成する想定であり、OAuthや一般的な利用関係から候補を広げません。別経路で形成済みのownership candidateについて、service側の利用とexact authorityの対応をTrust確認に使います。service surfaceは一つのserviceに複数存在できるため観測上は多対多ですが、一つのfull NSIDのcurrent owner projectは最大一つとします。

account→repoについても、profile link、投稿、provider ownershipだけではsource repository candidateを作りません。confirmed project accountが構造化されたproject-scopedなSource claimを直接返す関係は有用になり得ますが、現時点で正確に検出できる入力契約がないため、将来の再検討事項とします。

### Accountが所有するrecordを使う代表経路

通常recordはDIDに正規化したaccountからcollectionへの関係として扱い、仮称`has_record_in`で表します。AT URIがhandle表記でも、解決後のDIDへ結びます。confirmed project accountによるrecord所有はproject側からcollectionへの直接の支持にできますが、任意のaccountが任意のcollectionでrecordを作れるため、単独でownership candidateを作りません。この観測が保証するのはDID→collection方向であり、collectionから所有recordのDIDを検索できることだけを理由に逆向きedgeは生成しません。

Lexicon schema recordの公開は`did_publishes_schema`として分けます。matching schema id、publisher DID、authority delegationを確認できるため通常recordより強いrelationですが、publisher DIDのaccount roleは自動推定しません。

## 現行実装との差分（一次監査）

ここでは2026-08-03時点のコードと正本文書から確認できた差分だけを記します。必要な保存変更とmigrationの結論は、マトリクスのレビュー後に決めます。

### 現行の信頼グラフ

現行`trust_graph.py`のnode kindは、本マトリクスと同じ`service_url` / `repo_url` / `handle` / `did` / `domain` /`collection`です。globalな`AUTO_CONFIRM_PREDICATES`には次が入っています。

- `service_links_repo` / `repo_links_service`
- `service_canonical`
- `did_publishes_schema`
- `collection_authority_domain` / `domain_publishes_lexicon_record`
- `service_exact_domain` / `domain_exact_service`
- `domain_exact_handle` / `handle_exact_domain`
- `handle_resolves_did` / `did_document_claims_handle`

現行はpredicate allow-listとedgeの`classification` / `eligible_for_auto_confirm`を中心にpathを評価し、subject/objectのproject roleごとの用途をマトリクスとして持っていません。新設計では、同じDIDでもproject account、developer、organization、publisher、graph-only intermediaryで利用可否が違うため、kindだけでなくroleとclaim種別をpolicy入力にする必要があります。

edgeの`classification`にも`confirmed | candidate | derived | heuristic`があり、identifier classificationと同様にTrust状態とprovenanceが混在しています。現行edge schemaは、一つの観測が両方向を意味上保証する場合と、検索用に逆向きedgeを物理保存しただけの場合を区別する契約を持っていません。

### Collectionの所有とidentity

現行`collection_trusted_anchor_nodes()`はconfirmed developer DIDと`brand_did`をcollection ownershipのanchorへ加えます。今回の整理ではdeveloper/organization/publisherはroleだけで信頼判定の起点にせず、専用project accountとして採用されているか、そのaccountから得た情報を対象の関係に使えるかを別々に評価します。このため起点の規則変更と既存account roleの監査が必要です。

現行`handle_matches_confirmed_service_identity()`はhandleがconfirmed service hostのsubdomainの場合、またはregistrable domainが同じ場合にもdeveloper accountを採用します。今回の方針はexact domainだけを強い関係とし、subdomain/root共有を補助情報へ落とすため、このheuristicは差分です。

現行`domain_publishes_lexicon_record`は、authority domainのDNS解決とmatching schema record取得をdomain→collectionの一つのedgeに投影します。今回の案では、domain→publisher DIDの委譲とDID→collectionのschema publicationを別の直接事実として保存し、必要なpathで組み合わせます。現行edgeを残すか置換するか、既存DBを再生成するかは未決です。

collection import/project matchingには、直属namespace一致のほか、registrable authority/service host familyで既存projectへ割り当てる経路があります。今回の方針ではroot domain共有だけでprojectをまとめないため、collectorごとの実動作監査が必要です。

一次監査では、`sync-collections`は直属namespaceの一意一致を自動attachし、registrable namespaceとservice host familyをsuggestionへ落としています。この境界は本案と概ね整合します。schema version 3へ接続する際は、former/non-production/noiseの再発見をcurrentへ戻さないこと、同じnamespaceが複数projectへ競合した時に新規projectを増やさないこと、同じ直属namespaceの複数collectionを独立した複数根拠として数えないことを確認します。

`sync-github-search`はexact repo URLの既存project更新に加え、repository metadataの`homepage_exact`でも既存service projectへ自動attachし、repo seedをconfirmed `source_repository`として追加します。exact repo URLは同じresourceの更新ですが、`homepage_exact`はrepoからserviceへの一方向の自己申告です。本案ではtarget suggestionとrelation evidenceには利用できても、それだけでcandidate admissionまたはconfirmed repo追加を成立させません。復路または別の採用根拠が必要であり、collector実装を見直す際の具体的な差分です。

Semble syncのexact identifier一致と、Introduced syncがexact identifier一致後に紹介情報だけを更新する経路は、project境界を広げない限り同じresourceの観測更新として扱えます。Tangledのowner DID、website、repository homepageなどから既存projectを選ぶ経路は、actor共有と自己申告を含むため、matchとidentifier採用を分けてcollector監査します。

一方、authority domainからservice URL candidateを合成する`add_authority_service_candidates()`は現行ではno-opであり、「Lexicon authorityだけではservice surfaceを証明しない」という境界はこの入口には既に実装されています。

### Roleと保存契約

現行`project_identifiers`にはcomponent、actor、observation/intermediaryが混在しています。代表例は次です。

- component claim: `owned_nsid`、`service`、`source_repository`
- actor claim: `developer_account` / `developer_handle` / `developer_did`、`brand_did`、`project_account` / `project_handle`
- observation/intermediary: `source_repository_record`、`authority_domain`

manual DID seedは現行`apply_seed_to_project()`で同じDIDを`brand_did`とconfirmed `developer_account`の両方へ投影します。これはproject identityとdeveloper identityを分ける今回の整理と不整合であり、既存role/data監査の具体的な対象です。

Tangled snapshotのowner handle/DIDも、現行integrationではconfirmed `developer_account`へ直接投影されます。さらにrelevance scoreによりowner repoをsource repository candidateへ追加します。provider ownerからaccount roleを推定せず、ownershipだけでproject source candidateを作らない今回の方針との差分です。現行`did_links_repo` edge自体は直接観測として保持できますが、`eligible_for_auto_confirm=False`です。

今回の整理だけを理由に新roleを追加しません。現行`brand_did`の実データは専用project account側へ寄せ、概念としての広いbrand/organization accountはorganization accountとして扱う方向ですが、実データを監査してからrole変更を決めます。あわせてobservation/intermediaryを`project_identifiers`に残すかを監査します。roleとTrust state、provenanceを別軸へ分ける場合は保存契約変更とDB migrationが必要になります。一方、新predicateだけで済む変更は現行JSON/relations契約内で表現できる可能性があるため、SQL schema変更が必須とはまだ判断しません。

developer accountと専用project/service accountでは、帰属やTrustに利用できるrelationがほとんど重ならない可能性があります。後続の規則では包括的なaccount規則を作って例外で狭めることを前提にせず、roleごとに利用可能なrelationと、どの関係を裏付けられるかを定義します。当面はDID/handleで表すaccountにdeveloper roleを持たせ、複数account、個人site、provider identityなどを一人のactorとして束ねる必要が確認された場合にだけ独立したdeveloper nodeを再検討します。

### マージ

現行merge scoreはrepo URL、service URL、recorded namespaceに加え、`authority_service_families`、`domain_identity_tokens`、`collection_first_record_creator_dids`などを利用します。今回の整理ではrepo共有、developer/publisher一致、root domain共有、first creator単独をproject merge根拠にしないため、各signalの生成元、組合せ、`auto_merge_ready`条件を別途監査します。信頼グラフの自動採用pathをmerge scoreへそのまま移植しません。

## この文書の到達点と後続作業

6×6の接続マトリクスとrelation別の静的policyは2026-08-10に原則レベルのレビューを完了しました。各kind間の直接relation、向き、多重度、Admission、Trust、Merge、観測・表示の用途を確認し、負の証拠、説明・調査用relation、構造・診断情報、証拠の掲載場所を主マトリクスから分離しました。この文書はその設計チェックポイントとして締め、以下は別の設計作業として進めます。

### 1. 信頼判定結果と採用規則

後続の評価観点と設計は[信頼判定結果と採用規則の設計メモ](trust-verdict-and-adoption-policy.md)で扱います。

- 信頼グラフの直接出力を`SUPPORTED` / `INSUFFICIENT` / `CONFLICTING` / `NOT_EVALUATED`相当のclaim verdictとして定義する。名前は未確定
- self-claim、missing reverse path、stale observation、operator overrideをverdict reasonとして分ける
- 登録プロジェクトへの帰属、信頼判定結果、block/defer、手動判断を合成する
- dedicated project accountと共有actorについて、信頼判定の起点にできる条件と、どの関係を裏付けられるかを確定する
- project構成要素として妥当であること、帰属主張が信頼判定規則で支持されること、block/deferと手動判断の優先関係を合成する
- candidate、graph-only node、導出識別子を、それだけで信頼判定の起点にしない。自動採用済みidentifierを後続判断に利用する場合も、元の登録起点まで根拠を追跡する

### 2. マージ規則

confirmedなproject構成、role、共有・排他性、独立surface、派生関係、operator判断を入力に、二つのproject recordをマージするかを[登録プロジェクトのマージ規則設計メモ](project-merge-policy-design.md)で扱います。信頼グラフのauto-confirm path、developerやpublisherの一致、candidate identifierの一致をそのまま正のmerge根拠にしません。

### 3. 現行実装と既存DBの詳細監査

- collectorごとのcandidate生成箇所
- 調査先とproject identifier candidateの混同
- 信頼グラフが帰属判断やmutationまで担っている箇所
- roleを無視した`kind + value`評価
- candidate、graph-only node、導出識別子が信頼判定の起点になってしまう経路
- 一つの観測から両方向へ生成されたedgeが、それぞれ意味上の保証を持つか。検索用の逆向きedgeが混在していないか
- candidate repoや弱いdomain signalを使うmerge判定
- repositoryとNSIDの共有・排他性の現行動作

を確認します。

### 4. 時間関係の残件と現行6 kind外の別論点

- 同じproject内のcurrent/former/non-productionと確定transitionは前述の原則で扱い、schema version 3へ保存する。正本DBへの切替は未実施
- project間の後継、買収、分割、統合、ownership移転と、それらを保存・表示する関係
- mirror、instance、tenant、fork、派生projectの時間付き関係
- Basker→InfoEnon、DateSky→Nomareなど既知事例のproject境界review
- native appのbundle/package/store identifier
- provider account node
- account followなど、social graph上の関係を根拠に使う規則
- OAuth `include:<permission-set NSID>`から認証済みpermission-setを解決し、record collectionを関連付ける処理
- record以外のLexicon/XRPC/permission-setを含む汎用`nsid` node

permission-setは同じsetに含まれるcollection同士のpairwise edgeへ展開せず、set nodeを中継する方向です。初期実装を検討する場合も、OAuth scopeで明示された`include:`だけをboundedに解決し、global enumerationは行いません。

### 5. 正本文書化と実装計画

設計合意後、短いproject定義は`docs/brief.md`、保存上の分類とroleは`docs/developer/project-record.md`、観測・帰属・Trustの関係は`docs/developer/investigation-model.md`、具体的なpredicateと評価規則は`docs/developer/registry-internals.md`へ反映します。

`docs/brief.md`など利用者向けの層へ移す際は、project recordを「登録プロジェクト」、confirm操作を「採用」、operator decisionを「手動判断」とし、identifier以外も含む`confirmed`を一律に「採用済み」と読み替えません。

schema version 3の保存契約と通常mutationは実装・複製DB検証まで完了しており、正本DBへの切替後に関連する正本文書へ反映します。信頼policyの有効化、仮称predicateや新しいroleの追加、project間の時間関係はこの切替へ混ぜず、それぞれ比較とreviewを経て判断します。追加実装では、対象test、全unit test、既知fixtureまたはsample review、temp DB、正本DB複製のdry-run/auditを変更内容に応じて実施します。

## 現時点で固定しないもの

- 個別predicate名
- 信頼判定結果の保存名と段階
- 新しいidentifier role
- graph edgeと`project_relations`の保存分担
- repository subpath用の新identifier kind
- collection Trustをschema実在までどこまで厳密に要求するか
- project間の後継、買収、分割、統合、mirror、instance、複数project同居repo、ownership移転の時間付き最終分類
- provider account node、native app identifier、汎用`nsid` nodeの要否
- merge scoreと自動実行条件

## 関連する既存メモ

- [Identity and Ownership Evidence Notes](identity-ownership-evidence.md)
- [派生関係の設計メモ](derivation-relations.md)

これらは現行仕様の正本ではありません。本計画との整合を確認し、確定した内容だけを各正本文書へ移します。
