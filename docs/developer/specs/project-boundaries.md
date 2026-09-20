# 登録プロジェクトの境界

repository、service、Lexiconなどの外部対象（resource）の帰属・採用と、project同士のマージ・分割を扱います。両者は同じ説明・追跡単位の境界に関わり、それぞれ対象と前提に応じた規則で判断します。ここでいうprojectは、一つの説明・追跡対象としてresourceをまとめる編集上の単位です。同じownerや同じrepositoryに関係しているだけでは、同じprojectとは決めません。

| 判断 | 入力と結果 |
| --- | --- |
| 帰属・採用 | 観測した関係からresourceのroleと信頼根拠を評価し、既存projectへ採用するかを決める |
| 統合・分離 | 独立して登録されたproject同士の根拠を比較し、説明・追跡単位の重複や混在を見直す |

前半は両者を区別する判断モデルと合意済みの設計です。`仮称`や`未決`を含み、全規則が通常導線へ接続済みではありません。後半の「現行の採用規則」「Merge・Split」が現在の規則、末尾の「Shadow比較」が比較用の別経路を説明します。未実現の接続目標は[roadmap](../../roadmap.md#信頼帰属policyの追加接続)で管理します。

## 判断モデル

### この設計が扱うこと

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

候補生成と信頼評価は、同じrelationを参照する場合があっても別の判断です。二つの登録プロジェクトをマージする判断も、既存projectへ構成要素を追加する判断とは前提が異なるため、別policyとして扱います。

### 登録プロジェクトの意味

ここでいう登録プロジェクトは、所有されたLexicon、主要実装、公式repository、提供service、代表project site、native appなどを、一つの説明・追跡対象としてまとめる編集上の単位です。互いに異なる種類のresourceを、来歴・管理・提供目的などの関係に基づいてまとめます。実装・保存上の`project record`は、この登録プロジェクトを表すrecordです。以下の開発上の説明では`project`または`project record`を使います。

Lexicon、実装、repository、service surface、project siteは互いに別のresourceですが、関係が適切なら同じproject recordの構成要素になれます。一方、同じdeveloperが関与する、同じcollectionを利用する、同じrepositoryへ言及する、といった事実だけでは同じproject recordに収めません。

この定義は、将来の mirror、instance、複数project同居repo、fork、template、派生 project の境界を判断する際にも使います。これらの詳細は未決ですが、同じcode、同じowner、同じdomainのいずれか一つだけでproject同一性を確定しないことを出発点にします。

project recordは、説明と追跡に十分な代表的resourceを保持する編集記録です。構成要素の完全なinventoryではなく、未収集のservice surface、API endpoint、補助repositoryが残ることを許容します。たとえばBlueskyについて`bsky.app`と主要repositoryがあれば、`api.bsky.app`や`video.bsky.app`まで必ず収集する必要はありません。

serviceを持たない共有Lexicon/specification initiativeもprojectになれます。`standard.site`や`lexicon.community.*`のような集合は、代表project site、共通のspecification、governance、repository、release、管理主体に基づいて一つのprojectとして扱えます。wildcard表記自体はidentifierではありません。

### 区別する判断

#### 資源同一性（resource identity）

二つのURL、DID、handleなどが、同じresourceまたはそのcanonical aliasを指すかを扱います。

#### 登録プロジェクトの境界（project boundary）

登録プロジェクトの境界判断には、主に二つの対象があります。

- identifier attribution: あるresourceを、既存のproject recordと特定roleで関連づけるかを扱う。resource同士が同一物である必要はなく、repositoryのように複数projectと関係し得る対象では排他的な所属を意味しない
- project merge / separation: 独立して形成された二つのproject recordが、同じ説明・追跡単位を二重に表しているかを扱い、統合または分離を判断する

前者は既存projectへrole付きidentifierを追加する判断、後者はproject record同士の構成範囲を見直す判断です。前提と対象に応じて、それぞれのpolicyで評価します。

project mergeでは、既存projectへ新しい構成要素を追加するconfirmationとは非対称性と前提が異なるため、信頼グラフのpathをそのままmerge scoreへ流用しません。

#### 信頼（trust）

「このresourceはこのprojectの構成要素である」という主張が、騙りや偶然のlinkではなく、信頼できるsourceと関係で裏付けられているかを扱います。

#### 説明用情報の評価

developer account、dependency、used collectionなど、projectの説明や調査には役立つが、通常はproject identifierにしない情報を扱います。これは信頼グラフの主目的ではありません。

これらは順番に一つの結論へ収束する評価段階ではありません。たとえば、developer accountとの関係が事実でも、そのaccountを登録プロジェクトの構成要素にするとは限りません。また、同じrepository identityであることを確認できても、repositoryを共有する二つの登録プロジェクトをマージするとは限りません。

### 信頼グラフの責務

信頼グラフは、role付きの帰属主張と識別子間の直接関係を入力に、どの識別子と情報源がどの方向から主張を支持しているかを評価し、根拠となる経路を説明可能にする機構です。本設計では、直接出力を主張単位の信頼判定結果（内部名: Trust verdict）と位置づけ、採用規則がその結果を採用判断へ利用します。

graph nodeには次が混在し得ます。

- `confirmed`または`candidate`のproject identifier
- developer DIDやorganization accountなど、構成要素ではないidentifier
- protocol解決の中継点
- claimの対象

developer DIDやauthority domainは、登録プロジェクトの構成要素候補でなくても、帰属主張の根拠または経路の中継点としてgraph nodeになります。

信頼判定結果は、主張がどの程度支持されているか、どの方向の確認が欠けているか、矛盾や再取得の必要があるかを表します。最終的な採用は、登録プロジェクトへの帰属、信頼判定結果、採用を止める条件、手動判断を組み合わせて決めます。

後半の接続マトリクスは直接relationを重複なく棚卸しし、用途はその後の静的policy表で示します。同じrelationを候補生成と信頼評価が参照する場合も、それぞれの利用可否を別のpolicy列で判断します。

#### Relationとedgeの関係

page↔repositoryでは、relationを観測・方向付き判断の正本とし、graphで必要なものをedgeとして導出します。直接事実の確定と、相互経路によるidentifier採用は別の判断です。homepageが確定していても、それだけでproject帰属を支持する逆向きedgeにはしません。直接参照、解釈の根拠、判断時刻はrelation側へ保持します。将来relationに表現できない関係が必要になった場合はその観測契約を設計します。現時点でaccount/NSIDの保存方式までこの導出方式へ統一するものではありません。

同じ観測の新判断は旧判断を置換し、別sourceの観測は独立して保持します。取得失敗やLLMの未評価は否定に置き換えません。採用時の根拠履歴と、現在の観測で支持されるかという評価は分けます。

#### 責務境界

- 帰属規則が、観測からrole付きの識別子候補を作る
- 信頼グラフが、既に型付けされた帰属主張の支持状況を評価する
- 採用規則が、信頼判定結果、採用を止める条件、手動判断を組み合わせて採用可否を決める
- マージ規則が、二つのproject recordを同じ編集単位としてまとめるかを判断する
- graph nodeであること自体は、project identifierへの登録、構成要素への帰属、信頼判定の起点にできることのいずれも意味しない

### collector観測からrelationまでを分ける

collectorは外部sourceを取得し、そのsourceで実際に観測した方向付きのlinkやfieldを保存します。service pageのlink text、`aria-label`、title、GitHub homepage field、README内URLなどはこの段階では観測であり、構成要素間の意味付きrelationではありません。

collector固有の補助解析は、一つのsource内で分かるlinkの用途を整理します。たとえば`Source code`と明示されたrepository link、provider iconだけのlink、docs、funding、package pageを区別します。この解析は「そのsourceが何を表示または主張しているか」を扱い、現在の登録プロジェクトと同じprojectかを単独では決めません。

GitHub repository metadataのhomepageは、repository管理者がそのURLをWebsiteとして指定したrepo→URLの直接観測です。この直接参照と、repositoryが現在のprojectへ帰属するか、target URLがservice surfaceかは別々に評価します。homepageが正しく同じrepositoryのdocumentation、代表Website、accountを指していてもservice identifierにはならず、逆にrepository自体が誤ったprojectへ採用されていればhomepage観測だけを否定しても帰属問題は解消しません。

決定規則だけで意味を確定できない場合は、構成要素間関係判定が一つの提案relationについて両endpointの限定材料を比較します。自由に複数sourceを混ぜる推論器にはせず、当面は`service_url`または`site_url`と`repo_url`の間の一つの方向付きpredicateだけを評価します。入力は起点側の観測、相手側のbounded snapshot、利用できるrepository analysis、登録起点とrole付きの既存構成要素へ限定します。project名やprofileは参考情報にできますが、それだけでは支持としません。

関係判定は一つの提案relationについて`SUPPORTED`、`NOT_SUPPORTED`、`INSUFFICIENT`を返し、新しいURL、predicate、逆方向relationを生成しません。観測された逆方向のlinkが別にあれば、別の提案relationとして評価します。LLMを使う場合も、その役割は観測の意味を解釈することであり、identifierのconfirmやgraph pathの合成ではありません。

ここでの`SUPPORTED`は提案された一方向のrelationを作れるという意味です。後述する帰属主張の信頼判定結果にも同じ内部名がありますが、評価対象が異なります。一方向のrelationが成立しただけでは帰属主張の`SUPPORTED`にもidentifierのconfirmedにもなりません。

`SUPPORTED`になった方向だけをsemantic relationへ投影します。採用済みの構成要素から対象identifierへの許可済み直接relationはcandidate admissionへ使えますが、一方向だけではauto-confirmしません。auto-confirmは後段の静的な信頼policyが登録起点までの往路と復路を評価します。

意味付け前の最新link観測は、当面`state_json.evidence`のbounded current observationとして保持する方針です。runごとの観測履歴は`investigation_runs.bundle_json`、意味付きrelationは`project_relations`へ分けます。collectorが対象全体を正常に再取得した時だけ消滅したlinkをcurrent observationから外し、partialまたはfailedでは前回成功観測を否定へ変えません。新しいnormalized observation tableは、shadow運用でSQL横断監査や専用review UIが必要だと確認されるまで追加しません。

### この文書で使う設計用語

コードと照合できるよう、必要な箇所に内部名を併記します。

- 帰属主張（claim）: 「このidentifierは、このroleで、この登録プロジェクトに属する」という判断対象。repository解析などで述べる個々の事実もclaimと呼ぶため、この設計では帰属についての主張を明示する
- role: identifierが登録プロジェクトにとって何であるかを表す。identifier自体の種類（kind）とは別
- role確定済み候補（typed candidate）: identifier kindとroleが定まった識別子候補。未分類linkや検索hitは含まない
- 直接関係（direct relation）: 一つの観測が直接示す関係。複数の関係から推移的に作った関係は含まない
- 関係経路（path）: 複数の直接関係を向きに沿ってつないだもの
- graph-only node: project identifierとして保存・採用されていない、帰属主張の根拠または経路の中継用node
- 信頼判定の起点（root anchor）: 手動判断または承認済みの登録起点によって採用され、新しい帰属主張を評価する経路の出発点にしてよいrole付きidentifier
- 採用を止める条件（guard）: block、deferなど、自動採用を禁止または保留する条件
- 候補化（candidate admission / Admission）: roleが定まった帰属主張をidentifierの候補として追加すること
- 根拠の出所（provenance）: 観測した情報源、位置、抽出方法など、判断を元の観測へたどるための情報
- 信頼判定結果（Trust verdict）: 信頼グラフによる帰属主張単位の評価結果。identifierを変更する処理そのものではない
- 導出識別子（derived identifier）: 別の観測から機械的に導出された識別子。導出されたことだけでは信頼判定の起点にならない

### 外部対象・グラフ上の点・登録プロジェクトでのroleを分ける

接続マトリクスでは、外部世界にある対象、その対象を指すgraph node、登録プロジェクトとの関係を表すroleを別の層として扱います。同じ対象でもprojectとの関係は複数あり得て、node kindだけからroleは決まりません。

| 対象 | この文書で指すもの | node kindと実装状態 | 登録プロジェクトとの関係の例 |
| --- | --- | --- | --- |
| service surface | serviceを利用・操作するために到達するURL上の面。serviceという機能のまとまりやdeployment/instance全体とは区別する | `service_url`。値はsurfaceのURL | projectが提供する代表的surfaceは`service`。第三者client、tenant、mirror、補助pageは別relationまたは観測になり得る |
| project site | projectを説明・提示する代表的なWeb presence。documentation、specification、promotion、landing pageを含み得るが、利用・操作するservice surfaceと個別の補助pageは含めない | identifier kindは`site_url`。現行graphは独立した`site_url` nodeを持たず、website型relationのpage endpointを`service_url` nodeへ投影する。専用node/roleの接続とは区別する | projectを代表する場合は`project_site`。通常はcurrent primaryを一系統とし、明示的alternateと履歴を別扱いにする |
| repository | source code、schema、文書、issue、release、build定義などを版管理する外部resource | `repo_url`。値はrepositoryを参照するURL | projectが公式に利用する場合は`project_repository`。dependency、fork、mirror、単なる参照は別relation |
| ATProto account | DIDを安定した識別子、handleを変更可能な現在名として持つ論理的なaccount | account全体のnodeは置かず、`did`と`handle`を別nodeとして保持する | project account、developer、organization、publisher、service operatorなど。roleはidentity解決だけから決めない |
| domain | DNS名。service host、handle、NSID authority、`did:web` identityなど複数の文脈に現れる | `domain` | 文脈に応じてservice host、handle、authority、中継点などになる。domainというkindだけではproject roleは決まらない |
| NSID / record collection | AT Protocolのnamespaced identifier。現行設計では、そのうちrecord collectionを表すfull NSIDを主対象とする | `collection`。値はcaseを保持したfull NSID | projectが所有する場合は`owned_nsid`。利用、schema参照、permission要求、record作成はownershipと別relation |
| native app | bundle/packageとして配布・実行されるapp。backend serviceやdistribution pageとは別resource | 現行kindには専用nodeがない | projectの構成要素になり得るが、bundle/package identifier、store URL、backendとの関係は保留 |

#### 資源同一性の補足

resource identityは、二つのidentifierが同じ外部resourceまたはその時系列上のaliasを指すかを扱います。repositoryではproviderの不変repository IDを共有するrename/transfer前後、accountでは相互検証されたDIDとcurrent handle、service surfaceではcanonical URLや移転履歴が代表例です。mirror、fork、同一content、同じowner、同じhostは、それぞれ別resource間でも成立する関係として扱います。

現行graphは観測されたidentifierをnodeとして保持し、aliasらしい値を暗黙に一つへ畳みません。identityを確認する直接relationと、projectへの帰属relationを分けます。

#### 登録プロジェクトでのrole

project roleは「外部対象がこのproject recordにとって何であるか」という主張です。`project_site`、`project_repository`、`owned_nsid`は対象自体の種類ではなく帰属の意味を表し、developer、dependency、mirrorなどとの関係は同じkindのnodeに対しても別に記述できます。role、`candidate` / `confirmed`、根拠のprovenanceはそれぞれ別の軸として評価します。

後述する「構成要素」「構成要素ではない関係者」「関連情報」はroleまたはrelationを説明するための上位区分であり、roleと並ぶ独立した保存軸ではありません。未分類linkやauthority domainなど、まだproject roleを主張しない観測対象はproject identifierにせず、graph-only nodeまたはevidenceとして扱います。

### 収集対象の分類

#### 1. 登録プロジェクトの構成要素

同じproject recordへ、特定roleで所属させる対象です。

- owned Lexicon / NSID
- 公式repository。source、docs、specification、issue、release等を含み得る
- operated serviceの代表的なservice surface
- projectを代表するproject site。service surfaceや補助pageとは分ける
- native app。bundle/package identifierとdistribution URL、backend serviceは別resourceとして扱う
- その他、一つの説明・追跡対象として扱うresource

#### 2. 構成要素ではない関係者

帰属主張の裏付けや説明には使えますが、通常はproject identifierにしません。

- developer account
- organization
- publisher DID
- service operator

専用のproject accountはproject自身を表す構成要素になり得ます。一方、複数projectを代表するorganization accountは、複数projectに関係するaccountとして保持します。publisher DIDをdeveloper、project、organizationのいずれかへ自動的に読み替えません。現行roleと保存契約は[Project Record Schema](project-record.md)、未実装のrole拡張は[roadmap](../../roadmap.md#信頼帰属policyの追加接続)で管理します。

#### 3. 関連情報

projectとの関係は事実でも、そのprojectの構成要素ではない対象です。

- used collection
- dependency
- upstream、template、mirror
- primary project siteではない個別docs page、distribution、supporting page

#### 4. 観測・調査先

意味や帰属をまだ判断していない収集結果です。

- README mention
- search result
- 未分類link
- 追加取得先としてだけ必要なidentifier

identifier kindだけでは分類を決めません。同じrepo URLでも、project repository、dependency、mirrorではprojectとの関係が異なります。

### `candidate`と`confirmed`の意味

`candidate` identifierは、role付きの主張をまだ採用・信頼していない状態です。candidateの対象はroleによりproject構成要素にも、developer accountなどの補助情報にもなり得ます。raw search result、未分類link、単なるmentionはrole確定済みの帰属主張になっていないためcandidateではなく観測です。project record内に保存されていることだけを理由に信頼判定の起点にしたり、そこからの関係を強い根拠とみなしたりしません。

`confirmed` identifierは、そのrole付き主張が採用可能かつ信頼できると判断された状態です。構成要素に加え、developer accountなどの補助情報にも使います。project構成要素や信頼判定の起点として使えるかは、roleごとの規則で決めます。利用者の採用操作と自動採用の双方がこの状態を作り、経路の違いは採用来歴へ記録します。

roleは「何を主張しているか」、candidate/confirmedは「その主張を採用・信頼したか」です。`derived` / `heuristic`は導出方法やprovenanceであり、identifierの採用状態ではありません。schema version 3の通常生成は`confirmed | candidate`に限定し、導出値やheuristicをproject identifierへ混ぜない契約です。ただしDBのenum制約や既存rowの一律補正はなく、保存実装の保証範囲は[Project Record Schema](project-record.md)を参照してください。relation側のclassificationとprovenanceは別途整理します。

candidate生成を厳密にする目的は、project境界とreview品質を保ち、後段で誤用される対象を減らすことです。信頼評価上の主要な安全条件は、candidate、graph-only node、導出識別子を信頼判定の起点にしないことと、candidate自身の自己申告だけで自動採用を成立させないことです。

### 信頼判定結果と採用

信頼評価の単位はgraph nodeや`kind + value`ではなく、`project key + identifier kind + normalized value + role`で識別する帰属主張です。同じDIDやURLでも、project account、developer、publisher、service、project siteなどroleが異なれば別の主張として評価します。

信頼グラフと静的policyは帰属主張を変更せず、次の信頼判定結果、理由、利用した登録起点、往路、復路を返します。保存上の名前やcache形式は固定せず、直接関係から再生成できる評価結果として扱います。

| 判定値 | 意味 | 採用規則の通常動作 |
| --- | --- | --- |
| `SUPPORTED` | role別に許可された往路と復路が同じ登録起点との間に成立する | activeなguardがなければ自動採用できる |
| `INSUFFICIENT` | 評価に必要な観測を行ったが、片方向しかないか意味上の強さが不足する | 新規candidateを維持する。以前のtrust-policy採用が支持を失った場合はreview候補にする |
| `CONFLICTING` | activeなguard、排他的なclaim、または信頼できる観測同士が衝突する | 自動採用せずreviewへ送る |
| `NOT_EVALUATED` | roleが対象外、登録起点として別の理由で採用済み、または必要なprobeをまだ行っていない | 理由に応じて対象外、現状維持、targeted probeのいずれかとする |

`INSUFFICIENT`と`CONFLICTING`は「偽」の一種類へまとめません。観測不足、片方向の自己申告、矛盾、policy対象外では、必要な人間の対応が異なるためです。登録起点は信頼グラフによる自己評価で`SUPPORTED`にせず、operatorまたはbootstrapという採用理由を表示します。

採用規則は信頼判定結果をproject recordの変更へ変換し、手動判断を自動採用より優先します。operator操作時のconfirm/block/defer解除と採用理由の更新、現行mutationの全roleへの作用範囲は[project_decisions](project-record.md#project_decisions)を正本とします。判断モデルの対象はrole付きclaimです。承認済みsourceのbootstrap契約はbootstrap採用理由を作り、`SUPPORTED`はguardがない場合にtrust-policy採用理由を作れます。role単位の拒否の拡張条件は[roadmap](../../roadmap.md#web-ui)で管理します。

既にconfirmedのclaimが後の再評価で`INSUFFICIENT`になっても、通常のenrichだけで即時降格しません。一時的な取得失敗による連鎖降格を避け、採用理由、欠けたrelation、時間変化、誤帰属のどれかをreviewします。`CONFLICTING`になったclaimは、矛盾を解消またはoperatorが再判断するまで後続自動採用の起点に使いません。

現行identifierの採用理由は、利用者操作の`operator`、承認済み収集入口からの登録・割当の`bootstrap`、信頼規則による`trust_policy`を区別し、一つの帰属主張に複数保持できます。信頼判定の登録起点として利用できるかは、それぞれの採用理由の属性で表します。保存契約と現行decisionは[Project Record Schema](project-record.md#非現行identifier採用理由移転noise)を参照してください。

### kind別の帰属規則

前節のnode modelを前提に、各kindについて帰属・共有・信頼上の制約を補足します。新しいkindやroleを加える場合は、実例と利用目的から必要性を確認します。

identifierについて、次の性質を同じ意味として扱いません。

- projectの構成要素か
- 人間向けにprojectを代表するか
- 他projectとの共有を許すか
- discoveryまたはlookupの入口に使えるか
- Trust評価のanchorに使えるか

#### Repository

repositoryは一つのprojectに複数存在でき、一つのrepositoryが複数projectにまたがることもあります。複数project同居repo、共通runtime、mirror、forkがあるため、confirmed repositoryの一致だけでprojectを自動マージしません。ArabicaとOolongのように、一つのrepo/runtimeに別projectが同居した実例があります。`project_repository`への帰属は、sourceだけでなくdocs、specification、issue、release等の公式な置き場を含み得ますが、repo全体の排他的所有を意味しません。

package、subdirectory、manifest、Lexicon pathなどはrelation contextとして保持できますが、共通runtimeのように一つのpathへ切れない場合もあるため必須にはしません。接続マトリクス確定前に新しいidentifier kindを追加しません。

provider上のrenameまたはtransferで不変repository IDが同じ場合は、project attributionではなくresource identityとURL履歴の問題です。fork、mirror、対等なGitHub/Tangled hostingは別repository間のcode lineageまたはreplicaであり、関係だけでは同じprojectへ帰属させません。両方が公式repositoryとして直接示される場合は、各repoをprojectへ独立に帰属させればよく、repo間edgeは必須ではありません。

#### NSID / collection

confirmed `owned_nsid`はprojectの構成要素であり、信頼判定の経路上で使えますが、人間向けにprojectを代表するとは限りません。一つのprojectは複数NSIDを所有できます。独立した信頼判定の起点にできるかは、採用根拠を含む後続の採用規則で決めます。

current ownershipでは、同じfull NSIDは最大一つのprojectにだけ帰属するものとして扱います。他projectによる利用、互換実装、参照はownershipではなくrelationです。二つのprojectが同じconfirmed `owned_nsid`を持つ場合、直ちにmergeせず、重複record、誤帰属、分割ミス、ownership移転のreview対象にします。

repository内にLexicon JSONが存在することだけでは、そのprojectによるNSID ownershipを意味しません。

同じ直属namespace、すなわちLexicon解決単位に属するfull NSIDは、既定では同じprojectへ帰属させます。たとえば`dev.idiolect.recommendation`と`dev.idiolect.adapter`は同じ単位です。一方、`network.comind.memory`と`network.comind.void.memory`のような親子namespace、同じroot domain、似た名前だけではまとめません。明示的な手動判断（operator decision）または別governanceの確認は既定規則より優先します。

collectionを観測した時点で暫定projectを作る現行運用は、調査の入口として許容します。このprojectはcollection/namespaceの調査単位であり、authority domain自体をserviceまたはprojectとして採用したものではありません。真のcollectionに騙りや未使用collectionが少数混ざることより、別projectを誤って同居させること、atprotoと無関係なdomainをprojectへ引き込むことを優先して防ぎます。

#### Project site

`project_site`は、projectを説明・提示する代表的なWeb presenceです。documentation、specification、promotion、landing pageを含み得ますが、任意のdocs subpage、blog記事、support、store pageを網羅しません。通常は一つのcurrent primary site系統を期待し、移転前URL、明示的alternate、localization等が必要な場合だけ複数を扱います。

service surfaceとは別の構成要素です。`standard.site`のようにserviceを持たないprojectでもconfirmed `project_site`を持てます。反対に、landing pageとserviceが同じURLに同居する場合はservice componentをWebsite表示へ利用でき、同じ文字列を必ず`project_site`として二重登録しません。どちらのroleを採用するかはページの主な役割と運用上の必要性で決めます。

confirmed `project_site`は、許可された直接relationからrepository、service、account等のcandidateを作る起点になれます。candidate、検索hit、READMEから導出しただけのsiteは起点にせず、信頼判定の起点にできるかは採用理由を含む通常のanchor policyに従います。

#### Service surfaceとnative app

一つのserviceに複数surfaceがあり得ますが、project recordには説明と追跡に十分な代表的surfaceを保持できればよく、網羅を要求しません。customer tenant、self-hosted instance、mirror、proxyは通常は関連観測であり、独自にbrand付けされ追跡対象となる場合だけ別projectになり得ます。

同じ正規化済みservice URLは通常一つのprojectの構成要素ですが、同じhostまたはdomainを複数projectが共有することはあります。native appはserviceとは別の構成要素です。bundle/package/extension identifier、store URL、backend serviceのどれをnode化するかは保留事項です。第三者clientが同じbackendまたはprotocolを利用するだけなら別projectです。

#### Domain

domainはservice host、handle、NSID authority、`did:web` identityなどで共有されるため、domain node自体をprojectの構成要素やactorとして扱いません。完全一致したhost、handle、authority間のrelationは信頼経路の中継に使えますが、subdomain、同じregistrable domain、兄弟hostは構造または調査情報として扱います。

#### Account、DID、handle

handleとDIDはgraph上では別node kindですが、双方向検証後は一つの論理的なATProto accountを表します。DIDは安定identifier、handleは変更可能な現在名です。handle resolverとDID documentの二つの主張が一致した場合だけcurrent identityを確認し、片方向だけのaliasはconfirmed identityにしません。DID/handle対応はaccount identityを確認しますが、projectへの帰属根拠が二つ得られたとは数えません。

project accountはproject自身を表す場合に限り構成要素になり得ます。developer/organization accountは複数projectで共有可能なactorです。accountのroleをidentity解決、repo ownership、schema publicationだけから推定しません。

### 直接関係と経路の合意済み原則

#### 関係とidentifierの状態を分ける

graph nodeはedgeのendpointまたはpathの中継点です。node自体にcandidate/confirmed分類が暗黙に付くわけではなく、edge classificationと`project_identifiers`のrole/classificationは別の主張です。protocolまたは一次情報が入力identifierに対して特定の相手identifierを直接返す場合、その直接relationを保存します。pathが存在するという理由で、恣意的な推移edgeを新しく作りません。

#### 候補生成と信頼評価を分ける

role確定済み候補を作れるのは、原則としてconfirmedなproject identifierから対象identifierへの、静的に許可された直接の帰属関係がある場合です。対象identifierからconfirmed identifierへの自己申告方向は根拠として保存できますが、それだけではcandidateを作りません。`uses`、`mentions`、名前一致、registrable domain一致、host suffix、任意README linkは帰属関係へ昇格しません。

この原則の対象は、既存projectへ別kindのidentifierを候補として追加する処理です。collectorがcollection観測から暫定projectを作る処理には、次節の収集入口ごとの登録規則を使います。

#### Sync時の既存projectへの取込を分ける

source syncが観測を既存projectへ割り当てる処理は、新しいidentifierを追加する場合に帰属と採用の判断も行います。次の四つを分け、取り込み時点でprojectとの対応を評価します。

1. 既に同じcurrent identifierがあるprojectの観測を更新する
2. 承認済みの収集入口から、そのidentifierを起点とする新規projectを作る
3. 既存projectへ、それまで無かった新しいrole付きidentifierを追加する
4. 弱い一致を既存projectのsuggestionまたは新規project候補として残す

1は同じresourceについての追加観測であり、project境界を広げません。2では、たとえばcollection inventoryにrecordが存在するcollectionを起点に暫定projectを作り、そのcollectionを登録起点として採用できます。これは「collectionを中心に調査するprojectを作った」というbootstrapであり、別の既存projectとの同一性を主張しません。

3は既存projectの構成範囲を広げるため、collectorのmatch scoreだけでconfirmedへ追加しません。許可済みの直接関係からrole付きcandidateを作り、通常の信頼・採用規則へ渡します。承認済みsourceにrole固有のbootstrap契約がある場合も、取込先projectを一意に選べる規則と、そのclaimを採用できる規則を別々に示します。

collection syncでは、同じ直属namespace、すなわち同じLexicon解決単位に属するfull NSIDを同じprojectへまとめる合意済み規則を適用できます。既存のcurrent confirmed `owned_nsid`から一意なtargetが決まり、block/defer、former、non-production、noise、`separate_project`と衝突しない場合は、新しいcollectionをそのprojectへ採用できます。新規project作成時と既存projectへの追加時のどちらも、collection sourceによる採用理由と登録起点の資格を持てます。ただし信頼判定ではfull NSIDごとに独立した根拠が増えたとは数えず、直属namespace単位の同じ論理的な起点として扱います。

完全一致以外のroot domain、registrable namespace、service host family、名前、owner/developer account、repository homepageは、それだけでtarget projectを確定しません。一意なsuggestion、candidate admissionに許可された直接relation、または新規project候補として残します。複数projectに同じcurrent identifierがあるなど、resource identityの段階でtargetが競合する場合は、どれかを選んだり第三のprojectを作ったりせずcollision reviewへ送ります。

4で弱い一致を持つ新規project候補を作ることは許容します。弱い一致は別projectにも重複recordにも成立するため、新規作成したこと自体を`separate_project`判断や後続マージの正の根拠にしません。syncは既存projectを自動マージせず、マージ規則は作成後に残った重複候補を独立に評価します。

全体policyが定めるのは、完全一致、既存projectへの構成要素追加、登録起点、曖昧一致、guardの扱いです。各collectorには、観測した関係の意味、provenance、role、どのmatch kindを返せるか、そのsourceを特定roleのbootstrapとして承認できるかを定めます。これによりcollectorごとに帰属判断の基準を揃え、誤分類が見つかった場合はその事例を基に共通規則を見直せます。

#### 自動採用は信頼グラフ全体の往路と復路を見る

自動採用はcandidateと信頼判定の起点の間に、静的に許可された強い往路と復路があることを要求し、中継nodeを許します。往路と復路で同じpredicateを使う必要はありません。たとえばserviceからcollectionへのOAuth permissionと、collectionからauthority domain、完全一致service hostへ戻るpathを組み合わせられます。

自動採用済みidentifierも、confirmed project identifierとして後続のcandidate生成に使え、信頼経路のendpointまたは中継点になれます。ただし、そこから行う後続の自動採用は、手動判断または承認済みの登録起点まで根拠を追跡できることを要求します。評価のたびに登録起点まで再探索するか、検証済みの起点と経路を再利用するかは実装上の選択ですが、循環による自己支持と起点喪失を許さず、同じ信頼判定結果になることを必要条件とします。

信頼グラフに保存する各edgeは、subjectからobjectへの意味上の保証を表します。一つの観測が両方向を保証する場合は、同じprovenanceを持つ二本の方向付きedgeへ展開し、往路と復路の両方に利用できます。一方向の保証から検索用の逆引きを作る場合は、逆向きedgeを信頼グラフへ保存しません。両方向で意味が異なる場合は、handle resolverの`handle_resolves_did`とDID documentの`did_document_claims_handle`のように別predicateで表します。これらはaccount identityを双方向確認できますが、project attributionの根拠が二つ得られたことにはなりません。

candidate、graph-only node、導出識別子は、それだけでは信頼判定の起点にしません。自動採用済みidentifierやconfirmedなdeveloper/organization/publisherも、元の根拠から切り離された独立の起点にはしません。実測した入次数・出次数によるscoreは導入せず、predicate、方向、subject role、object role、中継可能性を静的規則として定義します。

#### 完全一致と階層関係を分ける

service URLの正確なhost、domainとhandleの完全一致は強いpathの一部にできます。subdomain、同じregistrable domain、兄弟hostは構造または調査情報であり、強いpathには使いません。shared hosting、tenant、organization配下の別projectを巻き込まないことを優先します。

#### 資源同一性と時間関係を分ける

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

##### 同じ登録プロジェクトとして継続する場合

名称、repository、service surface、domain、handle、NSID、実装が変わっても、説明・追跡対象としての提供物、管理上の連続性、利用者に対する位置付けが継続しているなら、同じproject recordの更新として扱えます。全構成要素が同一である必要はありません。旧identifierを`former`へ移し、新identifierをcurrentとして独立に採用し、変化が裏付けられた場合だけ両者のtransitionを記録します。

提供終了したservice URLは、過去に正当な構成要素だったなら`former`で保持します。移転先が確認できる場合は新しいidentifierをcurrentとして追加します。旧URLが終了したことと、新URLが同じprojectへ属することは別々に判断し、前者だけから後者を自動採用しません。

##### 別の登録プロジェクトとして扱う場合

旧projectのcodeやLexiconを引き継いでいても、別の提供物、管理、対象範囲、brandとして独立に追跡する方が説明しやすい場合は別project recordにします。この場合の「後継」「派生」「移管」はproject間の関係であり、一つのproject内のidentifier transitionへ押し込みません。共有repository、同じowner、redirect、同一contentのいずれか一つだけでは、同じprojectか別projectかを確定しません。

Basker→InfoEnon、DateSky→Nomareは、時間関係を検討する既知事例ですが、この文書ではまだ同一projectとも別projectとも確定しません。調査時には次の三通りを区別します。

| 調査結果 | project recordの扱い | identifierの扱い |
| --- | --- | --- |
| 同じ提供物の改名・移転と確認できる | 同じprojectを継続 | 旧identifierを`former`、新identifierをcurrentとし、裏付けがあればtransitionを記録 |
| 別の提供物が旧projectを引き継いだと確認できる | 別projectを維持 | 各projectのidentifierを独立に採用し、将来のproject間successor/derivation関係として扱う |
| 公開情報だけでは区別できない | 現状を維持してreviewを残す | redirect、名前一致、同一contentだけからtransitionやmergeを作らない |

##### identifier transitionの意味

transitionは、二つのidentifierが並存する事実ではなく、「旧identifierから新identifierへのrenameまたはreplacementが確定した」という説明・監査用の関係です。同じproject record内の変化だけを対象とし、次の用途へは直接使いません。

- 新identifierのcandidate生成または自動採用
- 旧identifierのTrust復活
- project recordの自動マージ
- 前後のresourceが同一物だという一律の推定

repositoryの不変provider IDはresource identityを直接支持できますが、service URLのreplacementは通常、異なるresource間のproject内交代です。このため、transitionを記録しても前後のidentifierをaliasとして一つへ正規化しません。transitionの種類、根拠参照、分かる場合の発効時点を保持し、根拠参照なしには確定しません。

raw redirectや同一content表示は観測であり、それだけでcanonical alias、同じservice、同じproject、確定transitionとはしません。同じcontentはalpha版、mirror、copy、共通templateでも生じます。redirect、公式announcement、旧・新surfaceの相互参照、providerの不変IDなどを個別の直接事実として保持し、意味が十分明らかな場合またはoperator review後にだけtransitionへ確定します。

現行schema version 3の`project_noncurrent_identifiers`と`project_identifier_transitions`は、同一project内のformer/non-productionと確定transitionを保持する器です。project間のsuccessor、買収、分割、統合、派生関係はまだ表現せず、必要性と実例のreview後に別途設計します。この時間整理のために、新しいidentifier kindまたはroleは追加しません。

#### Predicateの静的定義とrelation instanceを分ける

接続マトリクスで定義するのは、predicateの意味、方向、許可するendpoint kind、多重度、candidate admission・信頼経路・merge・表示への用途です。個々のrelation instanceには、実際のsubject/object、成立状態、source、method、locator、observed_at、provenanceと限定contextを記録します。

あるrelation instanceを特定の判断へ使えるかは、predicateの静的policy、instanceの状態とprovenance、endpointのrole/classificationの組合せで決めます。各rowへ用途別の可否を重複保存することや、relationの成立状態だけからTrust利用可否を決めることは前提にしません。

#### Predicateの用途を考える補助分類

- 正式な参照・直接保証: protocolが入力identifierに対して特定の相手を返す、またはprojectが制御するsurfaceがSource、official account、official service等として相手を明示する関係。Admissionまたは強いTrustの候補になる
- 共有可能な正しい参照: `uses_collection`、dependency、mirror、provider owner、developer/organization actorなど、relation自体は事実でもprojectへの排他的帰属を意味しない関係。説明・調査または限定したTrustに使う
- mention/noise: 任意README link、example、test、vendor、検索hit、名前一致、共通hostなど。観測sourceとしては残せるが、Admission、auto-confirm、マージには使わない

「一次情報に書かれている」ことだけで正式な帰属参照にはなりません。そのlinkがsource、official account、利用、依存、単なるmentionのどれを意味するかをpredicateで区別します。

この三分類は接続マトリクスの用途を確認するための説明上の区分です。保存するrelationの種類と実際の利用可否は、predicateごとの静的規則で定めます。

### Identifier kind接続マトリクス

行をsubject kind、列をobject kindとします。ここでは、帰属候補の生成、信頼評価、またはpath endpointのresource identity確認に使う正の接続関係を扱います。projectの構成要素同士だけでなく、DID、handle、domainのような情報の発信元、identity表現、中継点も含みます。一方、project同一性を否定する材料、マージを抑止する材料、説明・診断・調査だけに使う観測は外に分けます。`現行`は名前として現在の信頼グラフまたは`project_relations`に存在するpredicate、`仮称`はrelationの意味を区別するために置いた設計上の名前です。`現行`は、そのセルのendpoint kindと用途がすべてproduction接続済みであることまでは意味しません。セル内で状態が異なる名前はグループを分けて表示します。`なし`は、現在のproject判断に使う直接relationを設けないセルです。

| subject \ object | `site_url` | `service_url` | `repo_url` | `handle` | `did` | `domain` | `collection` |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `site_url` | 仮称: `site_canonical`、`site_redirects_to`、`official_alternate_site` | 仮称: `site_links_service`。projectが利用先としてserviceを明示 | 現行relation: `project_repository`。公式repositoryの直接表示 | 仮称: `official_account`。明示されたaccount role | 仮称: `official_account`。明示されたaccount role | 仮称: `site_exact_domain` | 仮称: `defines_schema`。仕様siteがmatching schemaを直接定義する場合だけ |
| `service_url` | 仮称: `service_links_site`。serviceが代表project siteを明示 | 現行: `service_canonical`、`redirects_to`。仮称: `official_alternate_surface` | 現行relation: `project_repository`。Trust edge: `service_links_repo` | 現行: `official_account`。明示されたaccount role | 現行: `official_account`。明示されたaccount role | 現行: `service_exact_domain` | 現行: `requests_permission` / `uses_collection` / `defines_schema`。`defines_schema`は明示的な定義claimに限定 |
| `repo_url` | 仮称: repositoryから代表project siteへの直接relation | 現行: `repository_homepage`観測。公式service関係の意味判定とTrust edge `repo_links_service`は別policy | 仮称: `same_repository_identity` | 仮称: provider ownerまたは明示的account link。GitHubではprovider accountの中継が必要 | 仮称: Tangled owner DID、または検証済みprovider account link。現行`did_links_repo`の逆引きだけでは生成しない | なし | 現行: `defines_schema` / `uses_collection` |
| `handle` | 仮称: accountからの明示的なproject site claim。Admissionには使わない | 仮称: accountからの明示的なservice claim。Admissionには使わない | 仮称: accountのrepo link/ownership。Admissionには使わない | なし。変更履歴は時間関係 | 現行: `handle_resolves_did` | 現行: `handle_exact_domain` | なし。DIDを中継する |
| `did` | 仮称: accountからの明示的なproject site claim。Admissionには使わない | 現行: `operates_service`（`project_relations`）。明示claimに限定 | 現行: `did_links_repo`（Tangled、auto-confirm対象外）などのrepo link/ownership。Admissionには使わない | 現行: `did_document_claims_handle` | なし。follow等は保留 | 仮称: `did_web_identity_domain`。PDS endpointはproject関係に使わない | 現行: `did_publishes_schema`。仮称: `has_record_in`、`earliest_observed_creator` |
| `domain` | 仮称: `domain_exact_site` | 現行: `domain_exact_service` | なし | 現行: `domain_exact_handle` | 仮称: `_lexicon`の`lexicon_authority_resolves_to_did`、`did:web` document公開 | なし | 現行: `domain_publishes_lexicon_record`は複合事実。設計上はauthority/DID/schemaへ分解 |
| `collection` | 仮称: 代表project siteの明示参照だけ。authority domain導出だけでは生成しない | 一般的な直接relationなし。authority domainを中継 | 仮称: canonical project repositoryの明示参照だけ。検索hitは不可 | なし。DIDを中継 | 仮称: authority解決がpublisher DIDを返す`collection_resolves_to_did`。通常record事実の逆引きだけでは生成しない | 現行: `collection_authority_domain` | 仮称: `same_lexicon_authority` |

#### マトリクスの読み方

- `site_url`はprojectを代表するsite系統のidentifierであり、任意のWeb URLやdocs subpageではありません。`service_url`はservice全体ではなくservice surfaceのidentifierです。
- landing pageとserviceが同じURLでも、常に二つのkindへ重複登録しません。`project_site`が独立した構成要素として必要か、serviceをWebsite表示へ利用すれば足りるかをroleごとに判断します。
- handleとDIDは別nodeですが同じ論理的accountを表し得るため、account relationの保存先は観測sourceに応じて分かれます。
- `domain_publishes_lexicon_record`は現行実装のpredicateです。DNSによるpublisher DID委譲と、そのDID repoにmatching schema recordがあることを一つに畳んでいるため、新設計では別事実として扱う案です。
- 同じ観測から両方向のedgeを生成する場合、両edgeに同じsource、locator、methodなどのprovenanceを保持します。由来の共通性は往路・復路への利用を妨げません。
- `collection`は現行どおりrecord collectionを主対象とします。permission-set、XRPC、record以外のLexiconへ一般化する場合は`nsid` nodeとsubtypeを将来検討します。

#### マトリクス外の関係と観測

以下の関係・観測は、系譜、マージ抑止、説明、診断などの用途に応じたpolicyで扱います。

##### 系譜・複製・マージ抑止

`fork_of`はrepo間のcode lineageを表す候補relationですが、あるidentifierがprojectへ帰属することや、その帰属主張が信頼できることを支持しません。このため、帰属・信頼の正の接続マトリクスには含めません。

用途は、似たrepoを同じprojectとして自動マージしないための負の証拠です。ただし、provider上のforkという事実だけで別projectだと断定はしません。独自service surface、domain、collection、accountなど別projectとしてのidentityも観測できた場合は強い抑止にし、それらが不明な場合はmerge confidenceを下げてreviewへ回す材料にします。系譜の表示や調査には利用できますが、AdmissionとTrust pathには利用しません。

`mirror_of`、replica、対等なGitHub/Tangled hostingも、それだけではprojectへの帰属やTrustを支持しません。双方を公式project repositoryとして扱う場合は、各repoについてprojectからの直接参照を別々に確認します。repo間の複製・同期関係は、系譜表示、同期診断、マージreviewへ使う別relationとして保持できます。

現行実装には`fork_of` predicateはなく、providerから得たfork metadataとreview heuristicが近い情報を扱っています。predicateとして保存するか、マージ判断専用の事実として保存するかは未確定です。関係種別・保存・review操作の選択肢は[時間・派生関係の未決事項](../../notes/temporal-project-relations.md#改名継承派生で決めること)で保持します。

##### 説明・調査用の関係

- 現行`mentions_collection`は、repo内のdocs、example、test、検索hitなどでcollectionを観測した事実です。`defines_schema`や`uses_collection`へ昇格できない参照を失わないために保持できますが、Admission、Trust、Mergeには使いません。
- schema dependency/referenceは実装やLexiconの説明には役立ちますが、collection ownershipを支持しません。依存先と同じprojectであることも意味しません。
- 同一contentの表示はalpha版、mirror、copy、共通templateを区別できないためrelation edgeとして保存しません。必要なら取得時点の観測または比較結果として扱います。
- 任意のHomepage、Source、profile URLをrepo↔domainの汎用relationにはしません。意味を判別できたものだけをproject site、service、account、project repositoryなどのrelationへ型付けします。

##### 構造・基盤・診断

- handleがdomainのsubdomainであること、domain間の`subdomain_of`、同じregistrable domain、兄弟hostは構造情報です。完全一致とは区別し、Admissionまたは強いTrust pathに使いません。
- 親子namespaceまたは共通rootはNSID文字列の構造情報です。直属Lexicon authorityが同じ場合とは区別し、Admission、Trust、Mergeに使いません。
- accountのPDS/service endpointは利用中のidentity infrastructureを示すだけで、accountまたはprojectによるdomainの所有・運営を示しません。診断情報としてのみ扱います。
- DID間のfollow、mention、同じPDS、keyや過去handleの共有は、現時点ではidentityまたはproject Trustのrelationにしません。

##### 外部媒体に掲載された主張

開発者の個人site、account profile、Bluesky投稿などは、それ自体が特定projectを代表する`project_site`として採用されない限り、account→site、account→service、account→repoなどのclaimを観測したsource/locatorとして考えます。個人siteの一部でprojectを紹介しているだけならdeveloper resourceであり、project componentにはしません。

これらの媒体から帰属主張を抽出する方法、発言者DIDとの結び付け、特定projectについての主張か単なる紹介か、投稿の更新・削除・時間経過、developerまたはorganizationによる主張をどこまで根拠として認めるかは未決です。情報源別の取得・品質規則として設計し、意味を確定できた帰属主張だけを既存の有向relationへ入力する方針です。

### 関係別の静的規則

`Admission`はrole確定済みのcandidate claimを作れるか、`Trust`は強いpathまたは補助pathに使えるか、`Merge`はprojectの自動マージに使えるかを表します。ここには主マトリクスに残したrelationだけを置き、上記の観測・抑止材料はそれぞれの別規則で扱います。いずれもrelation単独の扱いです。`条件付き`の具体的条件は意味欄に記します。Relation欄の`現行` / `仮称`は接続マトリクスと同じ意味です。名前をまだ置いていない行は、保存するrelationの必要性またはpredicate名自体が未確定です。現案ではrelation単独で自動マージを許す行はありませんが、設計上の非対称性を確認できるよう`Merge`列は明示的に残します。

| Relation | 向き・多重度 | 意味と強さ | Admission | Trust | Merge | 観測・表示 |
| --- | --- | --- | --- | --- | --- | --- |
| 公式project site alternate（仮称: `official_alternate_site`） | site↔site、多対多。current primaryは通常一系統 | 同じprojectが公式に提示した代表siteのalternate。個別docs pageは含めない | 条件付き可 | 強い。別の復路が必要 | 不可 | 可 |
| canonical project site URL（仮称: `site_canonical`） | site↔site、resource identity上はcurrent 1対1 | same-site redirectやcanonical metadataをsite identityとして確認。移転は時間関係と分ける | identity条件を満たす場合可 | identity確認 | project mergeではない | URL履歴として可 |
| project site redirect（仮称: `site_redirects_to`） | site→site、時間込みで多対多 | status、chain、時刻付きHTTP観測。promotion campaignや外部loginへのredirectをcanonical扱いしない | confirmed siteから条件付き可 | 単独不可。別predicateの復路が必要 | 不可 | 可 |
| project siteがserviceを明示（仮称: `site_links_service`） | site→service、多対多 | 代表siteが利用先、app、login等としてprojectのservice surfaceを直接示す | 可 | 強い | 不可 | 可 |
| serviceが代表project siteを明示（仮称: `service_links_site`） | service→site、多対多 | service surfaceがabout、docs、project home等として代表siteを直接示す | 可 | 強い | 不可 | 可 |
| 公式alternate（仮称: `official_alternate_surface`） | service↔service、多対多 | 同じprojectが公式に提示した別surface。網羅不要 | 条件付き可 | 強い。別の復路が必要 | 不可 | 可 |
| canonical service URL（現行: `service_canonical`） | service↔service、currentは同一観測から両方向 | 同一hostのHTTP→HTTPSまたはsame-site redirectをcanonical identityとして扱う現行edge。raw redirectと区別する | identity条件を満たす場合可 | identity確認。現行条件の妥当性は時間関係と併せて再監査 | project mergeではない | URL履歴として可 |
| redirect（現行: `redirects_to`） | service→service、時間込みで多対多 | status、chain、時刻付きHTTP観測。同一exact host内の`/`→`/login`等は同じsurface内のpath差として扱い、外部login/support/payment等は除外 | confirmed surfaceから条件付き可 | 単独不可。別predicateの復路が必要 | 不可 | 可 |
| project siteが公式repoを明示（仮称: `project_repository`） | site→repo、多対多 | Source、Repository、issue、docs、specification等としてprojectの公式repositoryを直接示す | 可 | 強い | 不可 | 可 |
| repoが代表project siteを明示（仮称: `project_repository`） | repo→site、多対多 | description、README、manifest等でprojectとの対応範囲と代表siteが明確 | 可 | 強い | 不可 | 可 |
| serviceが公式repoを明示（relation: `project_repository`、Trust edge: `service_links_repo`） | service→repo、多対多 | Source、Repository、issue等としてprojectの公式repositoryを直接示す | 可 | 強い | 不可 | 可 |
| repoが公式serviceを明示（relation: `project_repository`または専用の直接観測、Trust edge: `repo_links_service`） | repo→service、多対多 | description、README、manifest等でprojectとの対応範囲が明確 | 可 | 強い | 不可 | 可 |
| project siteがaccountを明示（仮称: `official_account`） | site→handle/DID、多対多 | project/developer/operator等。role不明なら未分類relationとして保持 | roleを明示できる場合可 | role policy次第 | 不可 | 可 |
| serviceがaccountを明示（現行: `official_account`） | service→handle/DID、多対多 | project/developer/operator等。role不明なら未分類relationとして保持 | roleを明示できる場合可 | role policy次第 | 不可 | 可 |
| accountがproject siteをlink（仮称） | handle/DID→site、多対多 | accountによるproject site claim。profile/bio/post等からの抽出・品質評価は別policy | 原則不可 | project-scopedな明示claimなら独立candidateへの逆方向evidence | 不可 | 可 |
| accountがserviceをlink（現行: `operates_service`） | handle/DID→service、多対多 | accountによるservice claim。profile/bio/post等からの抽出・品質評価は別policy | 原則不可 | project-scopedな明示claimなら独立candidateへの逆方向evidence | 不可 | 可 |
| project site URLのexact host（仮称: `site_exact_domain` / `domain_exact_site`） | site↔domain、siteは多対一、domainは一対多 | path付きURLでも正確なhostを採る。同じ観測が両方向を保証する。shared tenant guardを適用 | domain candidateにはしない | 強い中継 | 不可 | 可 |
| service URLのexact host（現行: `service_exact_domain` / `domain_exact_service`） | service↔domain、serviceは多対一、domainは一対多 | path付きURLでも正確なhostを採る。同じ観測が両方向を保証する。shared tenant guardを適用 | domain candidateにはしない | 強い中継 | 不可 | 可 |
| OAuth permission（現行: `requests_permission`） | service→collection、多対多 | 利用とatproto relevanceを示すがownershipではない | owned NSID candidateにはしない | exact authority/service domain等と組み合わせて強い | 不可 | 可 |
| collection利用（現行: `uses_collection`） | service/repo→collection、多対多 | read/write/queryの実利用 | owned NSID candidateにはしない | 補助。role/context次第 | 不可 | 可 |
| collection定義（現行: `defines_schema`） | site、repoまたは明示source→collection、多対多 | matching schema idとproject-scoped contextを持つ。vendor/generated/test/exampleを除外 | 可 | 強い | 不可 | 可 |
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
| collectionがcanonical project repositoryを明示（仮称） | collection→repo、多対多 | protocol/schema/specが特定repoを公式repositoryとして直接返す場合だけ | 条件付き可 | 強い | 不可 | 可 |
| 同じ直属Lexicon authority（仮称: `same_lexicon_authority`） | collection↔collection、authority内で多対多 | 同じ解決単位。pairwise edgeは保存せず導出可能 | confirmed collectionから可 | 支持するが単独auto-confirm不可 | 自動不可、重複はreview | 可 |

#### schemaの一致を確認する代表経路

matching schemaの取得は一つのrelationではなく、DNS委譲とschema record実在という二つの直接事実からなるpathです。

```text
authority domain --lexicon_authority_resolves_to_did--> publisher DID
publisher DID --did_publishes_schema--> collection
```

このpathを評価に使っても、authority domainからcollectionへの新しい推移edgeは作りません。現行の`domain_publishes_lexicon_record`をこの二つへ分解するかは、保存契約の検討事項です。

#### OAuth scopeを使う代表経路

confirmed service surface `S`がcandidate collection `C`をOAuth scopeで直接要求し、collection authority domainがserviceのexact hostと一致する場合、次の別方向pathを評価できます。

```text
往路: S --requests_permission--> C
復路: C --collection_authority_domain--> D --domain_exact_service--> S
```

`C -> S`という推移edgeは保存しません。`requests_permission`はcollection ownershipを主張しないため、それだけでowned NSID candidateを生成しません。当面、collectionは`sync-collections`等のcollection観測経路から形成する想定であり、OAuthや一般的な利用関係から候補を広げません。別経路で形成済みのownership candidateについて、service側の利用とexact authorityの対応をTrust確認に使います。service surfaceは一つのserviceに複数存在できるため観測上は多対多ですが、一つのfull NSIDのcurrent owner projectは最大一つとします。

account→repoについても、profile link、投稿、provider ownershipだけではproject repository candidateを作りません。confirmed project accountが構造化されたproject-scopedなSource claimを直接返す関係は有用になり得ますが、現時点で正確に検出できる入力契約がないため、将来の再検討事項とします。

#### Accountが所有するrecordを使う代表経路

通常recordはDIDに正規化したaccountからcollectionへの関係として扱い、仮称`has_record_in`で表します。AT URIがhandle表記でも、解決後のDIDへ結びます。confirmed project accountによるrecord所有はproject側からcollectionへの直接の支持にできますが、任意のaccountが任意のcollectionでrecordを作れるため、単独でownership candidateを作りません。この観測が保証するのはDID→collection方向であり、collectionから所有recordのDIDを検索できることだけを理由に逆向きedgeは生成しません。

Lexicon schema recordの公開は`did_publishes_schema`として分けます。matching schema id、publisher DID、authority delegationを確認できるため通常recordより強いrelationですが、publisher DIDのaccount roleは自動推定しません。

## 現行の採用規則

### Trust graph

この節は通常導線で使われる`trust-graph-v2`の規則です。前半の合意済み判断モデル全体を実装済みとは扱いません。

`trust-graph-v2`のpage↔repository edgeは、`project_relations`の現在の観測から導出します。`repository_trust.project_edges()`が共通投影で、候補URLの確認（candidate probe）、URLの信頼評価、Explain、shadow比較が同じ投影を使います。`trust_graph.graph()`は判定用、`stored_graph()`は保存用です。page↔repository以外のedgeと評価履歴は`state_json.evidence.trust_graph`に残します。

- `project_repository`のwebsite/service→repo、repo→website/serviceを方向付きedgeへ投影し、confirmedだけを強い経路として扱います。
- Read the Docsなどの既知documentation hostはproject用subdomainもdocumentationに分類します。exact pathの観測とWebsite候補化は維持し、service候補へ変換しません。
- `repository_homepage`は単独ではedgeにせず、confirmedな逆向きのpage→repoとexact URLが一致する場合だけ`repository_homepage_pair`を導出します。
- HTMLの解釈、READMEリンク、LLMの同一project判断もsource/method/locator、observed_at、context付きrelationに保存します。LLMは許可された入力pairに対する`same_project`をconfirmed、`related`/`uncertain`をcandidateとして保存します。出力にないpairは前回判断を維持し、明示的な新判断だけが同じpairを置換します。
- 同一page↔repo観測のclassificationは時刻が新しい結果で置換します。source/method/locatorが異なる独立観測は保持します。取得失敗では以前のpage観測を削除しません。
- URLの現在の支持はrelationと現在の採用済み起点から再評価します。保存された評価やadoptionのsource_refは過去の判断の説明であり、現在の支持の代用にはしません。
- `relation_corroboration`も通常のtrust評価・guard・採用decision記録を通します。
- 旧page↔repo保存edgeは現在の判定には使いません。通常saveは未解決edgeを一括削除しません。移行照合ではREADME出典の`owner/repo`形式と完全URL形式を同一視します。完全一致する出典を優先し、別repositoryや別methodをまとめません。現在のrelationのclassificationを維持します。`migrate-repository-trust`のdry-runはcurrent relation、復元可能なLLM判断、要reviewに分類します。`--execute-plan <plan.json> --archive <archive.json>`でDBを再照合し、旧page↔repo edge全体を原文のままarchiveへ保存・fsync・再読込確認した後、保存graphから除去します。relationの復元は行わず、現在のrelationと対象外edgeを維持するため判定用graphは変わりません。新規archiveを必須とし、上書きを拒否します。project key、旧観測の全field・重複、分類付きplan、過去判断の理由・入力pair、plan digest、archive digestを保存します。`reconstructable_judgment`は過去判断の記録が復元可能という分類で、現役relationへ戻す指示ではありません。archiveを通常判定へ読み込む経路はありません。書込み失敗はDB変更前に停止し、DB側のrollback時には退避fileが残る場合があります。fileの存在だけで適用済みと判断せず、実行結果と再dry-runで確認します。

- node kind は `service_url` / `repo_url` / `handle` / `did` / `domain` / `collection`
- 既存の `account` identifier はUI/storage上のroleであり、trust graph nodeにはしません
- edge は `subject` / `predicate` / `object` / `source` / `locator` / `method` / `classification` / `eligible_for_auto_confirm` / `observed_at` / `context` を持ちます
- relationの`confirmed`はpredicateの直接事実が確定したことを表し、endpoint identifierのconfirmedやtrust edgeへの採用を意味しません。信頼経路ではpredicate、方向、roleを静的policyへ照合し、適格なedgeだけを使います
- 実装済みの実動作適用は `service_url` / `repo_url` の自動confirm、`collection` ownership判定、`did` / `handle` / `domain` のtrust評価・監査です
- `did` / `handle` / `domain` は単独identifierとして評価できますが、通常enrich導線で新しいconfirmed developer accountへ自動保存昇格する処理は未接続です
- 未clearの`confirm_identifier`はgraph外anchorです。sourceでoperator入力と採用済みsource syncを区別します
- `repo_url` decisionはactiveなconfirmed `project_repository`に対応する場合だけanchorにします。defer/blockされたidentifierはdecisionが残っていてもanchorにしません
- 移行由来の根拠不明confirmed identifierは監査対象であり、新規URL昇格のtrusted anchorにはしません

`seed` / `manual_seed` roleは正本DBから移行済みで、現行コードは生成・読込しません。現行の`add-seed`はfactual roleを直接confirmedへ投影し、その採用を`confirm_identifier`へ記録します。seed scalarは初期入力の由来・検索用で、anchor判定には使いません。

service URLのrootに対するblock/deferは、その配下の自動候補にも適用します。ただし、子pathそのものにactiveな`confirm_identifier` decisionがある場合はそのexact URLをguardの例外にします。operatorが後から同じexact URLをblock/deferすると、先のconfirm decisionをclearしてguardを優先します。

URL candidate は、trusted anchor との間に `eligible_for_auto_confirm=true` の強い有向経路が双方向にある場合だけ自動confirmできます。ただしserviceの採用経路はrepository nodeを起点にも中継にも使いません。page↔repositoryの相互参照は同一projectの帰属根拠であり、service roleの根拠ではないためです。repository採用と方向付き経路の表示には引き続き使い、既存service同士のcanonical経路による確認も維持します。新規homepage／README公開先のservice採用は公開先宣言または独立した運営関係と、Web／protocolの実在を検査するrole policyを通します。候補追加は取得成功や実在確認を要求しません。詳細は[Repository homepageと公開先の評価](#repository-homepageと公開先の評価)を参照してください。片方向の `repo -> service`、片方向の `service -> repo`、READMEの任意URL、profile link、lexicon JSONの存在、fork/upstream、host suffix match は単独では自動confirmに使いません。

project自身が提供するページ（first-party page）上のrepository linkは、linkの正確な位置（locator）、canonical repository root、可視文字、accessibility label、子要素label、最寄り見出し・block、ページ内の領域区分（landmark）、同一groupとpage内のrepository数を集めた限定文脈で評価します。`Open source`、`View source`、単独`Source`、`Repository`等の直接表示はGitHub、Tangled、Codeberg、GitLabで共通に扱います。provider名だけ、`Fair source`、mirror、dependency、外部project一覧は明示的な公式repository表示とは扱いません。可視文字のないlinkはpage内にrepositoryが一つだけで、listや外部参照文脈にない場合だけ静的に支持できます。

この静的判断がconfirmedになり、subject URLがcurrent confirmed `service`または`project_site`と完全一致する場合だけ、repository identifierをcandidateへ投影します。relationのconfirmedは方向付き関係の意味が確定したことを表し、identifierのconfirmedではありません。補助docs pageやcomponent外pageのrelationはcandidateに留め、identifier candidateを作りません。旧anchor text、owner、project名一致によるcandidate化・confirmはservice/resource page経路では併用しません。

candidate repositoryは通常のrepository enrich、project nameのidentity token、紹介文生成用のLLM evidenceには使いません。confirmedなpage→repository relationから作ったcandidateに限り、repositoryのhomepage/READMEにある逆向きservice linkだけを先に収集します。repo側の現行逆リンクによって相互trust pathが成立した時点でrepositoryをconfirmし、その後に初めて通常repo contentを適用します。LLM relation assessorによる自動昇格は接続していません。LLM investigation planningには、追加調査先を決めるためURLだけを渡してよいものとします。

`enrich`は全collector・LLM evidenceの適用と通常cleanupを終えた後、URL candidateの信頼評価を一度再照合します。これによりproject一件の調査後半で逆向きedgeが加わった場合も、同じ調査内でprojectへ集約する`service_url` / `repo_url`をconfirmできます。個々の`source_repository_record`が持つ観測時のconfidenceは変更しません。成功した同一hostのHTTPからHTTPSへのredirectでHTTPS側がconfirmedなら、HTTP側は独立candidateとして昇格させず削除します。`repair-trust-graph`はevaluationの再計算・保存を担い、identifierを昇格しません。

collection ownershipの適用順序と必要な根拠は[NSID ownership](#nsid-ownershipの優先順位)にまとめます。

identity identifier は、manual/operator anchor との間に相互 path がある場合だけ `trust-graph-v2` 評価でauto-confirm可能です。`handle -> did` の解決だけでは片方向なので不足で、`did_document_claims_handle` のようなDID document側の逆向きclaimが必要です。domainは exact handle と同一値の場合に限り `domain_exact_handle` / `handle_exact_domain` を使います。suffix一致やhosted handleはdomain ownership根拠にしません。

確認用 command:

- `project-registry explain --topic trust -p <project_key>`
- `project-registry audit-trust-graph`
- `project-registry repair-trust-graph`

`audit-trust-graph` は読み取り専用で、既存 confirmed identifier を次のように分類します。`repair-trust-graph --execute` は edge/evaluation の再計算を保存します。`--downgrade-unsupported` を付けた場合は、operator decision や相互 trust path で説明できない confirmed identifier を候補・派生相当へ降格します。

- `needs_reprobe`: 片方向の強い edge だけがあり、service/repo の再検証が必要
- `should_downgrade`: operator decision でも相互信頼でも説明できない
- `guarded_confirmed_identifier`: block/defer 済みなのに confirmed 相当で残っている

`repair-trust-graph` はデフォルトでは trust edge/evaluation の再計算結果だけを保存します。confirmed identifier の降格は `--execute --downgrade-unsupported` を明示した場合だけ行い、`needs_reprobe` は降格せず再検証候補として残します。

provider別repo viewとresource URL一覧は`provider_repo_url_values()` / `resource_url_values()`で導出します。rootと`www`は観測済みredirect/canonical/content relationがある場合だけaliasとして畳み、他subdomainは独立surfaceとして残します。

same-siteの`/docs`、`/documentation`、`/about`、`/blog`、`/faq`、`/help`配下の情報ページと、`/login`、`/signin`、`/signup`、`/register`などの認証入口はservice identityではなく`resource_url_records`へ残します。これは補助ページをconfirmed service URLへ過剰昇格させないための暫定heuristicで、path名だけを長期的な信頼根拠とはみなしません。service follow-up probeやdeep page probeの結果はservice statusの補助evidenceには使いますが、補助ページURL自体をconfirmed service URLへ昇格しません。`/app`はapp surfaceとして使われることがあるため、この補助ページ判定だけでは落としません。`block_identifier` / `defer_identifier`はservice identifierとそのservice観測・relationへ作用します。同じ値のWebsiteや補助pageは別の責務なので自動削除しません。

same-site familyは、PSLとknown/configured multi-tenant suffixを反映した`root_domain()`が一致するhostだけです。同じregistrable domainのsubdomain、または同じhosted suffix上の同一tenantはsame-siteですが、異なるregistrable domainで先頭labelだけが同じhostはsame-siteにしません。

### Repository homepageと公開先の評価

通常enrichでは、現在の採用とguardを通過したrepositoryのconfirmed `repository_homepage`を、パスを保持した観測先として使います。docsも取得対象ですが、account、package、funding、repository自体は対象外です。既存の観測先と正規化URLが一致すれば同じrunで取得を重複させません。

既にexact URLがserviceであればstatic laneを使います。それ以外は`service_verification`の`repository_homepage` laneへ本文、リンク、requested/final URLを既存budgetで保存します。page→repoには既存のbounded policyを使い、取得成功だけでserviceの採用・状態・Websiteを変更しません。redirectはwebsite間の観測として保存し、同一hostという理由でpathを同一視しません。取得失敗で前回のpage→repo relationは消さず、成功時は同じpage sourceの観測を置換します。保存とreplayにも同じ境界を適用します。

このlane自体はroleを確定しません。通常enrichは取得結果を`repository-homepage-role-v4`へ渡します。現在採用済みrepoのhomepageを起点に、成功した取得の到達URLで既存service / project siteを照合します。既存serviceは再利用します。既存project siteでも新しい公開先根拠と実在確認があればserviceを評価します。別URLのalias登録やWebsiteの置換はしません。

未知のroleは`project_site`と`service`の帰属主張として別々に評価します。採用済みrepoのhomepage指定と今回のページのbounded policyによる逆参照はproject site候補を支持しますが、代表siteの確定根拠とはしません。候補化はpage→repoのconfirmed relationやページの採用済みroleを前提にしません。候補はtrust anchorになりません。service確定には、現在の採用済みrepoによる公開先宣言、または対象project→到達URLのconfirmed `operates_service` relationを要求します。既存supportから生成されるprobe由来（methodが`*_probe`またはsourceが`service_*`）のrelationは独立した根拠にしません。広義の`project_repository`、単なるリンク解釈、表示されたloginだけでは運営帰属を確定しません。公開先宣言は`repository-analysis-v16`の`declares_service_url`観測です。READMEの本文付き候補URLをLLMが選び、`current_project`または`uncertain`ならservice候補へ追加します。候補化には取得成功・逆参照・操作画面を要求しません。LLMへ提示するURL候補は特定の公開・利用語句に依存せず、既存budget内のREADME本文から抽出します。Markdownの`<https://…>` autolinkはHTMLタグとして削除せず、本文中のURLとして保持します。否定・仮定・第三者・旧公開先・区分欠落はこの経路の候補にはしません。デモは一律除外せず、パスを保持します。自動確定は最新成功結果の`current_project`だけを使い、同じrepoへの今回の逆参照と追加確認を要求します。公開先の観測を`operates_service`へ読み替えません。新しい公開先は同じenrich内で解析後に取得・評価します。追加確認はbrowserまたはprotocolのいずれか1 URL／enrichで、未確認、次いで古い確認を優先します。失敗もattemptとして保存するため、一件の失敗がほかのhomepageの確認を恒久的に塞ぎません。browser結果は`repository_homepage_browser` laneへ保存し、未採用URLの観測でprojectの稼働状態を上げません。

最初のnavigationの到達URLがstatic取得の到達URLとexact一致し、表示状態が観測済みで、表示されたauth/public surfaceの材料があり、当該pageにnetwork/console errorがなく、上記の帰属根拠がある場合だけWeb serviceを採用します。`service_kind=api|pds`ではbrowserを要求せず、origin root URLだけを対象に`/xrpc/com.atproto.server.describeServer`を15秒timeoutで取得します。HTTP 200、同じendpointへの到達、DIDとavailableUserDomainsを持つ応答を要求します。パス・query・fragment付きの公開先は、このXRPC確認では未対応としてrequestせず候補に残します。originへ丸めて別の公開先を確定しません。保存済み結果の適用時も対象URLと確認endpointの対応を再確認します。plain text rootの独立行`Code: URL` / `Source code: URL`も、対象repoへの逆参照として使えます。応答は`repository_homepage_protocol` laneとassessmentに保存します。汎用API・AppView独自APIの確認は未対応で、HTTP 200・一般JSON・AppViewという自己紹介だけでは確定せずcandidateに残します。他projectのconfirmed URLに加え、採用済みrepoからの未採用homepage指定・公開先宣言も衝突検査に含めます。保存済みの1段のHTTP redirectによる一致も含み、host familyだけでは一致としません。同じprojectの複数repoはまとめ、別projectの指定が競合すれば採用順にかかわらず保留します。未採用repoやguard対象のhomepageはこの競合元にしません。未取得の将来の参照先まで予測する検査ではありません。非現行URLやguardも保留し、採用は共通の確認・adoption記録を通します。採用理由にrepo、homepage、運営relation、role別の評価理由、今回の逆参照、衝突元、browser evidenceのsource・時刻・特徴、policy versionを残します。リンク先で見つかったlogin、host family一致、staticの`live`だけでは採用しません。

project site候補化は、対象ページがprojectを代表する可能性を示します。操作画面の有無と、公式documentationを代表project siteとして扱えるかは、それぞれ追加の判断が必要です。project siteの自動確定は未接続です。`homepage_role_assessment`に入力事実・`role_assessments`・適用actionを保存します。replayでも現在の起点、そのrepoのhomepage指定と逆参照の対応、guard、既存role、運営relationを再評価して適用します。公開先宣言は記録時と現在のrepo・URL・revisionも照合します。旧v1〜v3のassessmentからの自動採用は実行しません。他projectとの衝突判定は調査時のDB snapshotを記録したもので、replayは現在DBへの再照会ではありません。既存serviceのbrowser verificationによる稼働判定は維持します。ページ単位の観測契約は[Investigation](investigation.md#browser観測)を参照してください。

### Service観測の適用と補助ページ

`service_probe_supports_project()`はHTTP取得の失敗・parked pageを支持とせず、現在の採用と適格な信頼経路を評価します。authority domainとのhost一致、ページのNSID/atproto言及、alias token、Bluesky profile linkだけではserviceの帰属を確定しません。projectを支持しないpageは、既知repositoryとの関係判定に必要なlinkを内部evidenceに残しますが、title/description、docs/blog等のresource、Bluesky profileを公開profileへ投影しません。以前の観測で投影されていた場合も、そのpageの再観測時に除去します。

到達観測は`service_verification`に保存し、project supportのない結果から`operates_service`を作りません。4xx/fetch errorは`failed_service_urls`へ、支持されHTTP成功したURLは`supported_service_urls`へ残します。URL候補の自動採用は[Trust graph](#trust-graph)と[公開先評価](#repository-homepageと公開先の評価)の規則を使います。

docs/blog、support/status/privacyPolicy/termsOfService等の同一site補助ページは`resource_url_records`へ残します。投稿・profile・listなど役割を判定できないcontent URLは、単に同一siteという理由では保存しません。代表補助ページをURL+roleでまとめ、project全体、role単位、host+role単位の保存上限を適用します。confirmed、直接の出所を持つもの、短い入口URL、既知roleを優先して絞ります。上限は追加・collector patch・guard・hydrate・projectのマージ・saveの各境界で再適用します。

### Developer accountの採用

- 現在の account まわりは二層です
  - `developer_accounts`
    - confirmed 扱い
  - `candidate_developer_accounts`
    - service page / follow-up / Bluesky search 由来の未確認候補
- service/search 由来の handle は、まず `candidate_developer_accounts` に入れます
- `bsky.app/profile/{subject}` は service URL として扱いません
  - `{subject}` が handle 構文なら `handle`、DID 構文なら `did` として account evidence に分けます
  - `profile`, `user` のような一般 path 語だけではなく、構造化された handle / DID を優先して account-scoped URL を判定します
- confirmed に上がる主な条件:
  - confirmed service host と handle ドメインが整合する
  - 例: `https://example.com` と `alice.example.com`
- `developer_accounts` だけを identity 補強に使います
  - `candidate_developer_accounts` は ownership や service support の強い根拠に使わない方針

### NSID ownershipの優先順位

`project_owned_lexicon_nsid()` は現在、次の順で `owned` を判定します。

1. confirmed `owned_nsid`そのもの
2. source syncが同じprojectへ割り当てた同一collection namespaceのNSID
3. `trust-graph-v2` で collection と confirmed developer DID の間に相互の強い有向経路がある
4. `trust-graph-v2` で collection と confirmed root serviceの完全一致domainとの間に、`_lexicon` DNS解決とschema record取得で裏付けられた相互の強い有向経路がある
5. 既存の`owned_nsid`候補について、独立して採用済みのLexiconと直属authority・検証済み発行DIDが一致し、以下の範囲付きpublication policyを満たす

意図は、「偽 NSID -> 既存サービス -> それっぽい account」という自己強化ループを止めることです。confirmed service host、authority domain、identity token、repo内lexicon JSONのいずれか単独では ownership を確定しません。service経路はactiveなtrusted anchor、root URLとauthority domainの完全一致、`_lexicon`解決、同じcollectionのschema record取得をすべて要求します。collection namespaceからの拡張は、`dev.idiolect.recommendation` から `dev.idiolect.adapter` のように親namespaceが完全一致する場合だけです。`network.comind.memory` から `network.comind.void.memory` のような親子namespaceや深さ違いは自動owned扱いしません。

repo内にlexicon JSONがあるだけの観測は、project側のrepository採用条件と上記のNSID ownership条件を両方満たした場合だけ`defines_schema`として保存し、それ以外は`mentions_collection`として保持します。repo seedはproject repositoryの根拠であり、そのrepoに含まれる全NSIDの所有根拠ではありません。`uses_collection`、`mentions_collection`、OAuth `requests_permission`はownershipを確定しません。OAuth `include:`の再帰解決とorganization/individual accountの委任関係は未接続です。domain、account、DID、NSID、repo、service間の保証関係は[接続マトリクス](#identifier-kind接続マトリクス)と[関係別の静的規則](#関係別の静的規則)で区別します。

#### 承認済みLexiconと同じ発行元による信頼の補強

`accepted-lexicon-publisher-v1`は、同じproject内で既に帰属候補になっているcollection Bの信頼判定に、採用済みcollection Aの発行観測を再利用します。A/Bそれぞれの直属authority（末尾のnameを除いたNSIDを逆順にしたdomain）が完全一致し、そのDNS委譲先DIDとmatching schema recordの公開を検証できる場合に限ります。同じprefixや同じpublisherだけでproject境界を決定したり、新しい候補を生成したりする規則ではありません。

Aはcurrent confirmedの`owned_nsid`で、同じprojectの有効・review不要な独立したadoptionが必要です。通常のtrusted anchorの採用条件を基礎とし、`trust_policy`由来のadoptionは起点から除きます。operatorとbootstrapを区別して根拠へ残し、既存bootstrapを人力承認済みとは表示しません。まだ保存していないprojectは、既存anchor評価と同じく有効なconfirm decisionを要求します。A/Bのblock・deferを優先し、旧構成要素や開発試験用としてcurrentから外れたidentifierは使いません。この規則だけで採用したBを、次の独立した起点にしません。

観測の検証条件と保存は[Lexiconとauthorityの観測](investigation.md#lexiconとauthorityの観測)を参照してください。古い未検証観測を昇格させず、新しい観測を優先します。新しい応答で型・identityが不一致になった場合や、同じauthorityの新しいDNS委譲先が異なる場合、古い観測で支持しません。通信失敗は委譲変更の証拠とはせず、最後の検証済み観測を保持します。観測時点は根拠に含めますが、このpolicy専用の有効期限や一括再取得は設けません。

`collection_trust.py`が対象project・role・authority・publisherに範囲を限定したedge集合を作り、共通の経路評価へ渡します。既存の直接関係`collection_authority_domain`と、DNS委譲＋schema公開を表す`domain_publishes_lexicon_record`を使い、A→Bという推論結果やschema公開の逆向きedgeを直接観測として保存しません。第三者は中継する根拠であり、DID全体の信頼起点やproject/developer accountへ昇格しません。別namespace・別projectへの信頼伝播は行いません。

通常のLexicon結果適用時に候補を再評価し、支持されたBをconfirmして`trust_policy:accepted_lexicon_publisher`の採用来歴を残します。A/Bの取得結果の適用順序に依存しません。Explainと採用decisionの`source_ref`には、起点Aとその採用来歴、scope、schema URI・hash・観測時点を含む双方向経路、policy versionを残します。起点の取消し後は新規支持に使いませんが、過去の採用を自動で一律に取り消す処理は追加しません。このpolicyだけによる採用はmerge用のcollection根拠から除外します。

同じrepositoryの同じディレクトリだけを使う経路は未接続です。現行の`defines_schema`保存条件がownership判定に依存するため、そのrelationをownershipの新しい起点にすると循環します。実際のrecord表示・照合や公式accountの通常record保有も、このpublication policyには含めません。

### Identifierの一時保留

`defer_identifier`は既定30日で、期限内の自動confirmを抑止します。URLはcandidateとして観測可能なまま残し、NSIDは専用candidate配列を増やさず`deferred_identifiers`へ保持します。期限切れ後は通常の評価へ戻します。紹介候補の期限付き保留は別の[評価規則](project-evaluation.md#紹介候補の表示順位保留)です。

### Repository候補の調査順序

projectの公式repository候補は次の順で探します。

1. first-party page のリンク
2. README / homepage
3. GitHub repository search
4. Bluesky actor search / post search / author feed

confirmed に入れるのは、seed 由来か first-party の強い根拠があるものだけです。GitHub repo を確認した場合は `source_repo_records[].updated_at` に upstream の `updated_at` も保持し、`review-stalled` で repo activity として使います。

## Merge・Split

`duplicate_candidates` と `merge_recommendation` は派生データです。必要なら再計算できます。

Webのmerge候補planは保守的です。曖昧な一致は、2件のprojectを並べて確認するマージレビュー（pair review）と個別操作で人が判断します。以下では、統合で削除する側をsource、存続させる側をtargetと呼びます。

merge/split/separateはoperation単位のtransactionで実行します。aliasと調査履歴の扱いは[Merge / Splitの履歴](project-record.md#merge--splitの履歴)を参照してください。

merge時に、一方のconfirmed identifierが他方のblockまたは有効なdeferに当たる場合は、どちらの判断も黙って上書きせず操作を拒否します。同じ`kind + value + role`が現行identifierと非現行identifierに分かれている場合も、先に状態を解消する必要があります。`force`はこれらのidentifier判断を無効化しません。block/deferの適用単位は現行の`kind + value`です。異なるroleの現行identifierを同じclaimへまとめたり、採用来歴から非現行identifierを再採用したりしません。

merge実行時の統合後`duplicate_candidates`は、削除予定sourceと置換前targetを比較集合から除外し、統合後targetを加えた同一snapshot上で再計算します。Web APIはその保存済み結果の先頭候補を`next_merge_review`として返し、Merge reviewは一覧全体の再計算を挟まず次のpairへ進みます。この導線は候補scoreやbatch mergeの判定基準を変更しません。

`review-queue` は表示のたびに `duplicate_candidates` / `merge_recommendation` を再計算しません。通常導線では保存済みの派生値を使い、再計算は `repair-db`、seed 追加、merge / split / block / separate など identifier を変える操作に寄せます。

また、`review-queue` の通常表示は保存済み DB を信頼した軽量 shape を使います。保存時・repair 時の full normalization は維持しますが、一覧表示では URL / repo / lexicon の重い再正規化を避けます。

`audit-source-config [domain]` はローカル設定 `config/domain-overrides.json` の `custom_multi_tenant_suffixes` 追加後基準で既存 project を読み取り監査します。discovery fetch 設定の `config/sources.json` とは保存責務を分離しています。service URL、NSID authority、authority domain を tenant 単位に分け、同一 project 内に複数 tenant が残る時だけ `split_suggestions` を返します。仮 tenant project を組み立てて `compute_duplicate_candidates()` を通すため、分割後に既存 project との merge candidate が成立するかも表示できます。

このコマンドはデフォルト interactive です。suffix追加時はcurrent service probeのsame-site派生判定も同じapplication serviceで再評価します。tenant group の番号を選んだ場合だけ、service URL / NSID / authority domain を 1 つの新 project へ原子的に切り出し、元 project に block、新旧 project に相互 `separate_project_keys` を入れます。split 適用後は suggestion を再構築し、同じ元 project に複数 tenant が残る間はその project を優先して再表示します。Enter で送った suggestion key はその実行中だけ skip set に残します。共有 repo は自動配賦せず ambiguous として表示します。`--execute --no-interactive` はsuffix設定とcurrent same-site判定を揃えますが、splitは自動適用しません。

`split-project --identifier-type domain --value <authority-domain>` は、指定 authority domain と同一 host の confirmed service URL、同じ authority domain から導出される NSID、authority domain identifier をまとめて新 project へ切り出します。repo URL は authority domain だけでは帰属を確定できないため自動移動しません。

### duplicate candidate の作り方

- まず各 project から duplicate profile を作ります
  - `project_repository_urls`
  - `github_repo_urls`
  - `candidate_project_repository_urls`
  - `service_urls`
    - `website_url` もここに含めます
  - `recorded_lexicon_namespaces`
  - `record_creators`
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
- これらの値ごとに inverted index を作り、どれか 1 つでも共有する相手だけを候補集合に入れます
- `github_repo_urls`は保存fieldではなく、`project_repository_urls`からGitHub providerだけを導出したduplicate比較用の内部keyです
- duplicate / merge 用の `authority_service_families` と `domain_identity_tokens` は、確認済みのservice/manual service、operator confirmed domain、manual/confirmed/trust-policy confirmed collectionから導いたauthorityだけを使います
  - `input_nsids` / `lexicons` / `authority_domains` / `service_url_candidates` のような候補・観測fieldだけでは、merge用のdomain/family根拠にしません
  - 同じlexiconを使っている、または同じlexicon authorityを観測しただけでは、medium以上のmerge candidateにしません
- ただし次は最初から除外します
  - 自分自身
  - `separate_project_keys` で相互 block された pair
- fork/template/statusphere 派生のような「別物だが似ている」関係は、現状では `separate_project` で merge 候補から外します。将来的な関係表現の選択肢は[時間・派生関係の未決事項](../../notes/temporal-project-relations.md#改名継承派生で決めること)に分離しています

### candidate の根拠

- `compute_duplicate_candidates()` は候補相手ごとに次の overlap を見ます
  - `project_repository_urls`
  - `github_repo_urls`
  - `candidate_project_repository_urls`
  - `service_urls`
  - `recorded_lexicon_namespaces`
  - `record_creators_and_namespace_lineage`
    - creator DID が重なり、かつ namespace が同一または親子
  - `authority_service_families`
  - `domain_identity_tokens`
  - `merge_aliases`
  - `collection_first_record_creator_dids`
    - 明示的に heuristic を有効化した時だけ
- `record_creators` 単独では merge 理由にしません
  - namespace lineage が同時に必要です
- `candidate_project_repository_urls` は confirmed repo より弱い根拠ですが、duplicate 候補の入口には使います

### score と confidence

- score は理由ごとの重み付き合計です
  - `project_repository_urls`: 8
  - `github_repo_urls`: 8
  - `service_urls`: 7
  - `record_creators_and_namespace_lineage`: 7
  - `recorded_lexicon_namespaces`: 6
  - `authority_service_families`: 6
  - `candidate_project_repository_urls`: 5
  - `domain_identity_tokens`: 5
  - `collection_first_record_creator_dids`: 6
  - `merge_aliases`: 3
- `merge_confidence` / `auto_merge_ready` は score だけではなく理由の組み合わせで決まります
  - `service_urls` があれば `high` かつ `auto_merge_ready=true`
  - confirmed repo overlap と alias/domain/family/namespace の複合なら `high`
  - candidate repo overlap でも alias/domain/family が乗れば `high`
  - creator DID + namespace lineage は `high`
  - domain token + alias だけなら `medium`
  - score だけ高くても、理由の質が弱ければ `medium` や `low` に留まります

### merge recommendation の作り方

- `merge_recommendation` は `duplicate_candidates` の先頭 1 件を採用したものです
- 返すのは:
  - `target_project_key`
  - `score`
  - `merge_confidence`
  - `auto_merge_ready`
  - `reasons`
- top 候補が `auto_merge_ready=false` で、2 位が同点の時だけ `ambiguous_with` を付けます
- recommendationは比較対象の最有力候補を示します。一括マージへの採用には、次節の追加条件も確認します
- pair指定の `inspect-merge-pair` / Web merge review は、指定された2 projectだけを比較します
  - 一覧用の全project duplicate indexや、source/targetそれぞれの現在の `merge_recommendation` は計算しません
  - 表示する `source_pair_candidate` / `target_pair_candidate` は、一覧用 `compute_duplicate_candidates()` と同じpair判定関数から作ります
- Web merge review は、両projectのmerge identity hostが同じregistrable domain配下の別tenantに見える場合、未登録の `custom_multi_tenant_suffixes` 候補を表示します
  - 例: `foo.example.com` と `bar.example.com` なら `example.com`
  - 追加操作は `config/domain-overrides.json` を更新するだけで、既存 `separate_project` decision は自動解除しません
- collection first-creator heuristic は通常のWeb merge reviewでは初期計算しません
  - Webでは `Load first-creator details` を押した時だけ `listReposByCollection` の先頭DIDを補足情報として表示します
  - 照会collectionには保存済みのcase-preservingなNSID identifierを使います。`listReposByCollection`が返さないcollectionや取得失敗は、空欄にせず`not_found` / `error`として表示します
  - この補足表示は primary の `source_pair_candidate` / `target_pair_candidate` には混ぜません
  - CLIからheuristicを判定へ含める導線はありません

### merge候補planの追加安全策

- Webのmerge候補planは`duplicate_candidates`をそのままactionへ適用しません
- 候補 pair ごとに mutual recommendation を要求します
  - A の top が B
  - B の top も A
- さらに safe overlap を別ロジックで見直します
  - `strict`
    - strict GitHub/Tangled repo overlap
    - operator-confirmed repo overlap
    - operator-confirmed service host overlap
  - `moderate`
    - strict 条件に加えて
    - confirmed service overlap
    - candidate repo + alias overlap
    - creator DID + namespace lineage
    - domain token + alias overlap
    - authority family + domain token
    - service root domain + alias overlap
    - website root domain + alias overlap
- safe overlap が無い pair は recommendation があっても batch では落とします

### merge 方向の決め方

- `choose_batch_merge_direction()` は「どちらを target に残すか」を `project_batch_merge_preference()` で決めます
- 優先順位は概ね次です
  - confirmed factual identifierの強さ
    - service surface > owned NSID > project repository
  - editorial richness
    - `website_url`
    - `service_urls`
    - `project_repository_urls`
    - `resource_url_records`
    - `service_status`
    - `introduced_url`
    - confirmed NSID 数
  - confirmed identifier の数
  - `introduced_url` の有無
- つまり、より first-party surface が強く、より編集済みで、より confirmed identifier が多い側を target に残しやすいです

### 統合する編集情報の優先

`merge_project_records()`は`project_editorial_preference()`を使い、確認済みserviceと説明用の情報が充実しているprojectの名前・説明文などを優先します。

主に見ているもの:

- confirmed service surfaceがあるか
- `website_url`
- `service_urls`
- `project_repository_urls`
- `resource_url_records`
- `service_status`
- `introduced_url`
- confirmed NSID 数

この優先は主に次の field に効きます。

- `name`
- `tagline`
- `summary`
- `website_url`

意図は、collection/lexicon project が後から service 本体の editorial 情報を上書きしないようにすることです。

この統合規則は既存recordの説明文を遡及修正する処理ではありません。既存説明の訂正はprofile更新または手動修正として扱います。

## Shadow比較の契約

shadow比較は、比較用policyの結果をreportへ出し、通常の採用・マージによるDB更新には適用しない経路です。以下の比較commandは現行コードに存在し、DBを`mode=ro`のread transactionで読みます。固定caseと実データのsnapshotは分けて解釈します。

| 比較 | 入力・判断単位 | 通常導線との境界 |
| --- | --- | --- |
| `compare-trust-policy` | schema v3のrole付き採用claimと`registration_origin=true`の採用来歴、許可された直接関係と経路 | operator、legacy、sourceを累積したprofileを比較する。根拠のない起点を旧confirmedやcollectionから補わない |
| `compare-merge-policy` | project pairの構成要素、明示的な別project判断、共有と反証、統合方向 | collection断片は非対称に評価する。`uses_collection`や名称markerだけでは統合しない。identifierごとのcurrent/former/non-production/noise処遇を完了する経路ではない |
| `compare-collector-relation-policy` | repository帰属、repo→URL直接観測、target roleを分けた現在のclaim | 観測と採用を一つの結論にせず、比較結果だけでWebsiteやidentifierを書き換えない |

信頼比較は有限graph上の経路を探索し、最短の説明経路を返します。固定の最大3 hopで信頼範囲を定義しません。全起点探索とtrace再利用の同値性を比較しますが、保存済みpathを現在の支持の代用にはしません。merge比較ではconfirmed構成がowned NSIDだけのfragmentについて、exact subset、または同じauthorityとnamespace/alias等の文脈を持つ包括projectを評価します。独立した構成要素や別project判断を同時に確認し、通常mergeのscoreとは区別します。

専用relation assessorは、例えば「このpageのlinkは、そのprojectの公式repositoryを指すか」という一つの関係を、渡された根拠だけで評価します。結果は支持（`SUPPORTED`）、不支持（`NOT_SUPPORTED`）、材料不足（`INSUFFICIENT`）です。回答が入力内の根拠を参照していても、その解釈が正しいとは限りません。この評価はproject全体を扱う通常triageとは別の比較用処理で、結果からidentifierを自動採用する経路は接続していません。
