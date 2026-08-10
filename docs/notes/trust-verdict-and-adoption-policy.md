# 信頼判定結果と採用規則の設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。信頼判定結果と採用規則の評価観点、設計概要、既知事例による検証、運用負担の比較方法を保持します。shadow evaluationの比較結果をレビューする前に正本挙動へ反映しません。

## 目的

信頼グラフの接続規則は[登録プロジェクトへの帰属と信頼グラフの設計メモ](project-attribution-and-trust-design.md)で原則レベルのレビューを完了しました。本書では、信頼グラフから得た経路をrole付き帰属主張の信頼判定へ変換し、採用規則が`confirmed`への変更を決めるまでを扱います。

設計案は抽象的な整合性だけで評価しません。既知の実例と現在の調査手順を基準に、誤採用を防げるか、妥当な採用を維持できるか、どの手動確認が不要になるか、どの確認が引き続き必要か、新しい定期監査を要求しないかを比較します。

## この文書で使う言葉

概念を説明する本文では日本語を使い、実装や保存形式と対応させる必要がある箇所だけ内部名を併記します。

| 用語 | この文書での意味 |
| --- | --- |
| 帰属主張（claim） | 「このidentifierは、このroleで、この登録プロジェクトに属する」という判断対象 |
| role | identifierが登録プロジェクトにとって何であるかを表す。identifier自体の種類（kind）とは別 |
| 直接関係（direct relation） | 一つの観測が直接示す関係。複数の関係から推移的に作った関係は含まない |
| 関係経路（path） | 複数の直接関係を向きに沿ってつないだもの |
| 信頼判定規則（Trust policy） | 信頼グラフの経路から帰属主張の支持状況を評価する規則。保存状態は変更しない |
| 信頼判定結果（Trust verdict） | 信頼判定規則が返す主張単位の評価結果 |
| 採用規則（adoption policy） | 信頼判定結果、採用を止める条件、手動判断を組み合わせ、`candidate` / `confirmed`を変更する規則 |
| 信頼判定の起点（root anchor） | 手動判断または承認済みの登録起点によって採用され、評価経路の出発点にしてよいrole付きidentifier |
| 採用を止める条件（guard） | block、deferなど、自動採用を禁止または保留する条件 |
| 判定経路（decision trace） | 自動判定に使った起点、往路、復路、条件、判定理由の記録 |

## 要点

この案では、信頼グラフが観測済みの直接関係を保持・探索し、信頼判定規則がその経路からrole付き帰属主張の信頼判定結果を作り、採用規則がproject recordを変更します。candidate生成、信頼判定、採用、mergeは別々の判断として扱います。

- 評価対象はidentifier単体ではなく、登録プロジェクト、identifier、roleを組み合わせた帰属主張です。
- 由来付きの直接関係を正本とし、信頼グラフはそこから導出します。
- 自動採用には、同じ信頼判定の起点と帰属主張の間で意味上の往路と復路が必要です。二つの関係が同じ観測に由来していても構いません。
- 信頼判定結果は`SUPPORTED`、`INSUFFICIENT`、`CONFLICTING`、`NOT_EVALUATED`に分け、判定だけでは保存状態を変更しません。
- 自動採用済みの帰属主張も候補生成や後続の信頼経路に利用できますが、その根拠は手動判断または承認済みの登録起点まで追跡できる状態を保ちます。
- 通常運用では入力が変わったclaimだけを再評価し、定期的な全件監査は導入しません。

以下では、まずこの案を評価する条件を固定し、次に設計内容、現行との差分と実例、移行条件の順で示します。

## 評価方法

### 評価観点と受入条件

| 評価観点 | 受入条件 | 確認方法 |
| --- | --- | --- |
| 誤採用の防止 | 一方的な自己申告、共有repo、共有actor、root domain共有などから自動採用しない | 既知の誤判定・曖昧事例をfixture化する |
| 妥当な採用の維持 | service↔repo、handle↔DID、OAuth scopeとexact authorityなど、合意済みの有効経路を理由なく失わない | 現行結果との比較とsample reviewを行う |
| roleの分離 | project/service account、developer、organization、publisherごとに利用可能なrelationと、どの関係を裏付けられるかを定義する | role別policy testを作る |
| 説明可能性 | 自動採用ごとに、評価対象の帰属主張、信頼判定の起点、往路、復路、採用を止める条件、判定理由を表示できる | 判定経路を検査する |
| 決定性 | DB件数や探索順序に依存せず、同じ観測とpolicy versionから同じ結果を得る | 順序変更と再計算のtestを行う |
| 責務分離 | candidate生成、信頼評価、採用mutation、mergeが互いの代用にならない | APIと呼出経路を監査する |
| 運用安全性 | 新旧結果をDB更新なしで比較でき、既存DBへの反映前に全差分を確認できる | 複製DBでshadow evaluationを行う |
| 保守性 | predicateや例外分岐をcollectorへ分散させず、relation追加を中央policyで扱える | 重複規則と変更箇所を現行実装と比較する |
| 性能 | 通常のenrichまたはrepairでpath探索が実用時間内に収まり、組合せ爆発しない | 実DB複製で時間、edge数、探索量を計測する |
| 運用負担 | 省略できる手動確認、残る確認、新たに必要な確認とその発生条件を事例別に説明できる | 現行手順と新policyの作業差分を記録する |
| 過去判断との整合 | operator confirm、add seed、clear履歴を一律の正解とせず、入力経路別に新policyとの一致・不一致理由を説明できる | decision source別のretrospective comparisonを行う |

誤採用防止、説明可能性、決定性、運用安全性は必須条件です。既知の危険事例を一件でも自動採用する、理由を再現できない、自動評価の比較前にDBの変更を必要とする設計は採用しません。

### 既知事例を評価単位にする

syntheticな最小testだけでなく、正本DBまたは過去の調査で確認した実例をcase catalogとして保持します。一つのcaseは少なくとも次を記録します。

- 評価対象の登録プロジェクト、identifier、roleからなるclaim
- 入力となる直接観測とprovenance
- 期待するcandidate生成の有無
- 期待する信頼判定結果と主要な理由
- 期待する自動採用、保留、手動判断の別
- merge判断へ流用してよいか
- 現在行っている手動確認
- 新policyで不要になる確認
- 引き続き必要な確認と、その再実施条件
- 誤判定した場合の影響

初期catalogには、少なくとも次の性質を持つ実例を含めます。

- 正式なservice↔source repositoryの相互参照
- handle resolverとDID document claimの相互確認
- OAuth scopeとexact service/authority domainによるcollection支持
- Lexicon authority delegationとmatching schema publication
- ArabicaとOolongのような複数project同居repo
- BaskerからInfoenon、DateSkyからNomareのように時間関係を分離すべき事例
- 一つのorganization accountが複数projectに関与する事例
- project accountまたはdeveloper accountからの片方向link
- fork、mirror、対等な複数repository hosting
- exact domainではなくsubdomain、registrable domain、共通rootだけが一致する事例
- collection観測は妥当でも、別projectの混在またはatprotoと無関係なdomainの取り込みを避けるべき事例

caseの期待結果は現行実装を正解として複製せず、人間の調査判断を基準にします。現行と期待結果が違うcaseは、今回の変更で直す対象なのか、別policyまたは時間関係の課題として保留するのかを明記します。

### 運用負担の比較

各caseと主要なdecision classについて、次の差分を示します。

| 項目 | 現行 | 新policy | 受入時の見方 |
| --- | --- | --- | --- |
| 観測取得 | 人が追加取得しているsource | 自動取得または引き続き手動のsource | 新しい無制限crawlを要求しないか |
| 初回判断 | 現在の確認手順と判断材料 | 自動判定できる部分と残る手動判断 | 手動工程が実際に減るか |
| 再評価 | enrich、repair、手動再確認の契機 | stale、conflict、policy更新時の再評価 | 毎回の全件監査を導入しないか |
| review queue | 現在reviewへ出る条件 | 新たに保留またはconflictになる条件 | 件数と優先順位が運用可能か |
| 判断説明 | 現在確認できる根拠 | 新しい判定経路 | 追加調査なしで理由を追えるか |
| 誤判定対応 | 修正、再取得、DB repairの手順 | override、recompute、migrationの手順 | 復旧が現行より難しくならないか |

「自動化できる」とだけ記載せず、どの入力が既にcollectorから得られるか、追加probeが必要か、初回だけの監査か継続的な監査かを区別します。新policyによって毎runまたは定期的な全件監査が必要になる場合は、誤採用リスクを大きく下げるなど、それを上回る利点を示せない限り採用しません。

### マージ前に示す比較結果

実装案をマージする前に、複製DB上のshadow evaluationから少なくとも次を報告します。

- 現行と新policyで結果が変わらないclaim数
- 新たに自動採用されるclaim数と代表sample
- 自動採用されなくなるclaim数と代表sample
- 自動採用済みidentifierを後続の候補生成・信頼判定へ利用できるclaim数と、追加reviewが必要になるclaim数
- conflict、保留、手動reviewへ移るclaim数と代表sample
- 既知case catalogの期待結果との一致
- 省略できる手動確認と、新たに必要になる確認
- 評価時間、最大path長、探索edge数
- 保存契約、policy version、DB migration、再計算の要否
- 増えた実装複雑性と、削除または集約できた既存規則
- 過去のoperator confirm、bootstrap seed、cleared decisionとの一致・不一致内訳

大きな変更に見合うメリットは、自動採用数だけでは判断しません。具体的な誤帰属リスクを一群として除去する、判断経路を説明可能にする、混在している責務を実装上も分離する、または反復している手動確認を削減する効果を実例で示します。挙動改善が小さい一方で実装、migration、継続監査が大幅に複雑化する場合は全面変更を採用せず、効果のある範囲へ縮小します。

## 設計概要

### 全体の流れ

処理を次の責務へ分けます。

```text
観測の収集
  → 意味を型付けした直接関係の保存
  → 帰属規則によるrole付きcandidate claimの生成
  → relationから導出した信頼グラフのpath探索
  → 信頼判定規則によるclaim verdict
  → 採用規則によるcandidate / confirmedの変更
```

信頼グラフはrelationを保持・探索する機構、信頼判定規則はpathの意味を判断する規則、採用規則はproject recordを変更する規則です。candidate生成、信頼判定結果、採用による変更、project mergeは相互に代用しません。

### 評価対象はrole付き帰属主張

評価単位はnodeまたは`kind + value`ではなく、`project_key + kind + normalized value + role`で識別する帰属claimです。たとえば、同じDIDでも`project_account`、developer、publisherでは別claimです。

`candidate` / `confirmed`はclaimの採用状態です。graph node自体にはこの分類を持たせません。`confirmed`は、そのrole付きclaimを登録プロジェクトの事実として採用しているという現在の意味を維持します。採用根拠は、手動判断、承認済みの登録起点、信頼判定規則のいずれかとしてclassificationとは別に記録します。

### 直接関係を正本にし、信頼グラフを導出する

目標とする構造では、由来付きの直接関係を`project_relations`の正本へ保存し、信頼グラフのedgeは接続マトリクスとroleごとの規則から導出します。`eligible_for_auto_confirm`をcollectorがedgeごとに決めず、predicate、向き、source/method、対象claimのroleから中央の規則が利用可否を決めます。

`mentions_collection`、fork、subdomainなど主マトリクス外のrelationも調査・表示用として同じ正本に保持できますが、graph builderがTrust pathから除外します。現在の`state_json.trust_graph.edges`は`project_relations`と同じ事実を重複保持してdriftし得るため、移行後はcanonical storeにせず、互換期間を終えたら削除します。Trust evaluationだけはpolicy versionと入力fingerprintを持つ再生成可能なcacheとして`state_json`へ置けます。

このtargetはSQL table追加を必須としません。`project_identifiers`のunique keyは既にroleを含み、`project_relations`にはprovenanceとcontextがあり、decisionのroleと採用根拠はmetadataで表せます。ただしJSON/metadataの保存契約変更と既存データの再投影は必要です。role検索や監査性能の実測後にindexまたはschema migrationの要否を決めます。

### 信頼判定の起点まで根拠を追跡する

信頼判定の起点にできるのは、role付きclaimとして明示的に採用された次のものだけです。

- operatorが採用したcomponentまたはproject identity claim
- source assignment自体が承認済みで、その入力をprojectのbootstrapとすることが契約化されたclaim

信頼判定規則で自動採用されたclaimも、confirmed project identifierとして候補生成に使え、後続の信頼経路のendpointまたは中継点にもなれます。したがって、自動採用されたidentifierが多いこと自体は、後続の自動化を止める理由になりません。

一方、自動採用されたclaimだけを、元の根拠から切り離された独立の起点にはしません。後続の自動採用には、手動判断または承認済みの登録起点まで遡れる判定経路を残します。これにより、自動採用済みclaim同士が循環して根拠なしに支持を維持することを防ぎます。

毎回元の起点から全経路を探索するか、自動採用claimに元の起点と判定経路を引き継いで再利用するかは未決です。shadow evaluationでは、自動採用済みidentifierを後続判断に使った既知事例を含め、追加されるprobe、手動review、探索量を比較します。現行より手動負担が大きく増える場合は、完全な再探索を前提にせず、根拠を追跡可能なまま再利用する方式を選びます。

developer、organization、publisherから得た情報は個別の関係を裏付ける根拠になり得ますが、そのidentifierがconfirmedであるだけでは信頼判定の起点にしません。対象claimのroleごとの規則が、その種類の根拠を明示的に許可する場合だけ利用します。publisher DIDをdeveloperまたはproject accountへ読み替えません。

### 経路の成立条件

自動採用に使う`SUPPORTED`は、同じ信頼判定の起点とcandidate claimのendpointの間に、roleごとの規則が許可した往路と復路の両方がある場合に成立します。往路と復路が同じ一つの観測に由来しても、観測が意味上両方向を保証するなら利用できます。必要なのは独立source二件ではなく、一方的な騙りでは説明できない意味上の双方向性です。

探索はglobal predicate allow-listによる任意BFSではなく、claim roleごとに許可したpredicate遷移とendpoint roleを使います。中継nodeは許しますが、path長と遷移列はpolicyごとにboundedにし、新しい長い組合せが必要な場合は明示的なpolicyとして追加します。pairwiseな推移edgeは保存せず、採用時のtraceに実際のdirect edge列を残します。実測次数や動的scoreは使いません。

### 信頼判定結果

信頼判定規則はclaimを変更せず、次の判定値と理由、利用した信頼判定の起点、往路・復路を返します。

| 判定値 | 意味 | 採用規則の通常動作 |
| --- | --- | --- |
| `SUPPORTED` | 規則で許可された双方向pathが同じ信頼判定の起点との間に成立 | 採用を止める条件がなければ自動採用可能 |
| `INSUFFICIENT` | 必要な観測を行ったが片方向だけ、または意味上の強さが不足 | 新規candidateは保持。以前`SUPPORTED`だったtrust-policy basisのclaimはreview候補 |
| `CONFLICTING` | 信頼できる観測または排他的claimが両立しない | 自動採用せずreviewへ送る |
| `NOT_EVALUATED` | roleが対象外、または必要なprobeがまだ行われていない | reasonに応じて何もしないかtargeted probeを行う |

`NOT_EVALUATED`の`role_not_applicable`と`observation_pending`は理由で区別します。前者はdeveloper情報など構成要素向け信頼判定規則の対象外、後者は取得可能な逆向き観測が未実施という意味です。`INSUFFICIENT`と`CONFLICTING`を「偽」の一種類へまとめません。

`INSUFFICIENT`を要reviewにするのは、以前`SUPPORTED`だったclaimが根拠を失った場合を基本とします。新規candidateや、もともと信頼経路を必須としない手動採用・登録起点まで一律にreviewへ送ると件数が膨らむため、最終条件はshadow evaluationの件数とsampleを見て決めます。

### role別の主要規則

| 帰属主張の種類 | 主なrole | 自動採用 | 信頼判定の起点 | 備考 |
| --- | --- | --- | --- | --- |
| project component | `service`、`source_repository`、`owned_nsid` | 対象。role別の双方向pathと採用を止める条件を要求 | 手動採用または承認済みの登録起点となったcomponent | repo共有は許すため、repo一致だけでconflictまたはmergeにしない |
| project identity | `project_account`、`project_handle` | 対象。projectが制御するsurfaceからの直接claimとaccount identity確認を要求 | 手動採用または承認済みのproject identity | organization accountと区別する |
| actor relation | developer、organization、publisher | component自動採用の対象外 | 汎用の起点にはしない | relationの事実性は関係者向けの規則またはsource assignmentで扱う |
| intermediary / observation | `authority_domain`、`source_repository_record`、graph-only node | 対象外 | 不可 | path中継またはprovenanceとしてのみ使う |

developer accountとproject/service accountは同じaccount一般の規則を共有させません。将来、個人siteやBluesky投稿からdeveloperによる主張を取得する場合も、関係者向けの規則でclaimを型付けしてから利用し、project componentの信頼判定の起点へ自動昇格させません。

### 採用規則と手動判断

採用規則は次の優先順で一つのclaimを処理します。

1. roleを指定したoperator confirmがあればoperator basisで`confirmed`にする
2. activeなblockがあれば自動採用しない
3. activeなdeferがあれば期限または明示解除まで自動採用を保留する
4. 承認済みbootstrap source契約に一致すればsource basisで`confirmed`にする
5. 信頼判定結果が`SUPPORTED`で、追加の採用を止める条件がなければtrust-policy basisで`confirmed`にする
6. `CONFLICTING`はreviewへ、`INSUFFICIENT`は新規candidateを維持し、以前`SUPPORTED`だったclaimはreview候補にする。`NOT_EVALUATED`はreasonに応じてtargeted probeまたは対象外とする

手動採用はactiveなblockまたはdeferより優先します。Web UIでは警告を表示し、採用を止める条件が残ったまま手動採用した事実を判定経路へ記録します。手動採用が後で解除された場合は、残っているblockまたはdeferが再び自動採用を止めます。decision targetも`kind + value`ではなくrole付きclaimへ合わせます。同じDIDのdeveloper claimをconfirmしたことがproject account claimのconfirmになってはいけません。

既に`confirmed`のclaimが後の再評価で`INSUFFICIENT`または`CONFLICTING`になっても、通常のenrichで即時降格しません。以前`SUPPORTED`だったtrust-policy basisのclaimが`INSUFFICIENT`へ変わった場合はreview候補にし、件数と理由をshadow evaluationで確認します。`CONFLICTING`なclaimは採用根拠にかかわらず信頼判定の起点としての利用を停止し、operatorが矛盾を解消または明示的に再判断します。この方法なら一時的な取得失敗による連鎖降格を避けつつ、新しい誤採用の拡大を止められます。

### 再評価と情報の鮮度

再評価は次の入力が変わったclaimだけを対象にします。

- 関連する直接関係の追加、更新、削除
- 信頼判定の起点、手動判断、block、deferの変更
- claim roleまたはclassificationの変更
- 信頼判定規則のversion変更
- DNS、handle resolution、redirectなど時間依存relationのtargeted reprobe

観測時刻だけを理由に全relationを一律失効させず、relation種別ごとに再取得契機を定義します。通常runごとの全件監査は導入しません。policy version変更時とmigration時だけ全件shadow evaluationを行い、通常運用では入力fingerprintが変わったclaimを再計算します。

## 現行との差分と実例

### 現行実装の基準値と構造差分

2026-08-10の正本DBを`immutable` read-onlyで確認したbaselineは次のとおりです。

- 4,415 projects、24,475 identifier claims
- 保存済みtrust edge 14,346件、evaluation 9,503件
- `auto_confirm=true` 1,908件のうち、1,756件は`candidate_is_trusted_anchor`、実際の`mutual_trust_path`は152件
- 現行auditのunsupported confirmed 433件のうち、developer handle 348件、developer DID 70件で、component claimはrepo 14件とservice 1件
- in-memory再構成後のedgeは15,217件で、15件が`missing_recomputed_edge`、4件が`needs_reprobe`、429件が`should_downgrade`

現行はnode単位のevaluation、roleを見ないanchor、collectorが保存する`eligible_for_auto_confirm`、`project_relations`から同期したedgeと直接保存edgeの重複を持ちます。`candidate_is_trusted_anchor`が大半を占めるため、評価件数は自動採用の実効範囲を表していません。また、developer roleへcomponent向けTrust auditを適用した結果がunsupportedの大半を占めます。

新設計による直接の改善見込みは次です。

- 1,756件の信頼判定の起点に対する自己評価を信頼判定結果から除き、採用根拠として表示する
- developer 418件をcomponent降格候補ではなくactor relation policyの対象へ分離する
- 現行baselineで根拠確認が必要なcomponent claimを15件へ絞り、shadow evaluationで新たに生じる差分だけを追加reviewする
- canonical relation storeからgraphを導出し、`missing_recomputed_edge`のような二重保存driftをなくす
- 自動採用152件について、信頼判定の起点、role、往路・復路を判定経路として再検証する

これらは実装前のbaselineから見込む効果であり、最終的な削減件数ではありません。新policyのshadow evaluationで増減とsampleを再計測します。

### 過去の判断との事後比較

正本DBには`confirm_identifier` decision、少数の現行`manual_seeds`、merge後のproject key aliasと旧project keyのinvestigation runが残っています。2026-08-10のread-only確認では、`confirm_identifier`は合計2,286件、active 2,277件、cleared 9件です。source内訳は`legacy_manual_seed_migration` 2,264件、Web UIでのoperator confirm 10件、`operator_add_seed` 4件、canonical redirect 7件、collection sync 1件でした。現行`manual_seeds` entryは9件です。

実行済みmergeを表す`project_key_aliases`は506件、存続先は293 projectsで、timestampは2026-06-21から2026-08-09まで残っています。alias 506件すべてに旧project keyの`investigation_runs`があり、合計1,221 runsの`input_project_json` / `output_project_json` snapshotを利用できます。alias rowはmergeの実行modeや判断理由の詳細を持たないため、全件を独立した手動判断とは断定しませんが、pre-mergeのidentifier claimを再構成する材料としてconfirm履歴より広いcoverageがあります。

この履歴は有用ですが、2,264件のmigration decisionを2,264回の独立した人間判断とは数えません。add seedは登録プロジェクトを始める登録起点の操作であり、信頼判定規則が後から同じclaimを`SUPPORTED`にできることを必須にしません。canonical redirectやsyncもoperator判断とは分けます。

shadow evaluationではsource cohortごとに次を比較します。

| Cohort | 比較すること | 一致しない場合の読み方 |
| --- | --- | --- |
| Web UI operator confirm | 新policyが`SUPPORTED`、`INSUFFICIENT`、`CONFLICTING`のどれを返すか | `INSUFFICIENT`ならcollector不足かoperator固有知識かをsample reviewする。`CONFLICTING`はhard review |
| operator add seed | role付きbootstrap rootとして再現できるか | Trust path不足は不一致としない。role誤りまたはguard衝突だけreviewする |
| legacy seed migration | 元のseed roleと現在のclaimが一貫するか | 一致率を自動化効果へ数えず、migration integrityの監査に使う |
| canonical redirect / source sync | source契約で採用可能か | operator判断との一致ではなく、bootstrap/source allow-listの妥当性として評価する |
| cleared confirm | 新policyが自動的に同じclaimを再採用しないか | 再採用する場合はclear理由と現在のevidenceをreviewする |
| executed merge | merge前の両projectから存続projectへ移ったcomponent claimを新policyがどう評価するか | merge自体をTrustの正解にせず、operator/bootstrap basisで残すclaim、Trustで再現できるclaim、時間関係・共有resourceとして別扱いすべきclaimへ分ける |

Web UIでconfirmされたclaimのうち、新policyが当時利用可能だった自動観測だけで`SUPPORTED`を再現できるものは、将来省略できる可能性がある手動操作として数えます。ただし現行DBはdecision時点の全graph snapshotを必ず保持しているわけではないため、現在のevidenceで過去の判断時点を完全再現できるとは限りません。取得時刻とinvestigation記録が揃うcaseだけを厳密なhistorical replayとし、それ以外は現在状態とのretrospective comparisonとして区別します。

過去confirmとの一致率は単独の成功指標にしません。自動採用が増えることより、operatorが採用しなかった危険caseを採用しないこと、cleared decisionを勝手に復活させないこと、一致しないcaseの理由がcollector不足、policy差、時間変化のどれか説明できることを優先します。

merge replayでは、alias作成前の最後の利用可能snapshotからsource/target双方のrole付きclaimを復元し、現在のcanonical projectでの採用根拠と新しい信頼判定結果を比較します。これにより、過去に人またはmerge workflowが一つの編集単位へまとめた後、どのcomponent claimを信頼判定規則だけで維持できるか、どれを登録起点または手動判断による採用として残すべきか、どのclaimが共有repoや改名・後継など別論点かを分類できます。自動化件数を増やす目的ではなく、過度に保守的な規則が正しい構成要素を大量に失わないかを見るcoverage testとして使います。

activeな`separate_project` decision 51件も、mergeしなかった境界事例として併用できます。ただしdecision row数はunique pair数と同じとは限らず、別projectであることが個々のidentifier claimを否定するわけでもないため、主にmerge非流用とcross-project contaminationのnegative caseに使います。

### 既知事例で期待する挙動と運用差分

| 事例 | 新policyの期待結果 | 省ける作業 | 残る作業・新しい作業 |
| --- | --- | --- | --- |
| serviceとsource repoが相互に公式参照 | component claimを`SUPPORTED`として自動採用 | 相互参照を人が再確認してconfirmする操作 | collectorが取得できない媒体だけtargeted probe |
| account→service/repoの片方向link | candidateを生成せず、既存candidateへの逆向き証拠に使う場合も単独では`INSUFFICIENT` | 毎回「片方向だけでは採用不可」と判断する作業 | 取得可能ならproject側の直接claimを一度probe |
| handle resolverとDID document claim | DIDとhandleが同じaccountであることを支持する。project accountの帰属にはproject-controlled surfaceとの双方向claimも要求 | DID/handle対応の目視確認 | account roleが曖昧な場合のrole判断 |
| OAuth scopeとexact authority/service domain | 既存collection candidateのTrustを`SUPPORTED`。OAuthからcandidateは作らない | scopeとauthorityを人が照合する作業 | collection自体は`sync-collections`等で観測する |
| Lexicon authority delegationとmatching schema | publisher経由のpathを説明し、publisher roleを自動変換しない | DNS、DID、schema recordの三箇所を手で追う作業 | 別project混在のconflictだけreview |
| ArabicaとOolongの複数project同居repo | repo共有をconflictまたはmerge根拠にせず、各projectへのclaimを独立評価 | repo一致によるduplicate候補の再確認 | project-scopedな直接claimを判別できない場合だけ手動判断 |
| organization accountが複数projectに関与 | organization actor relationとして保持しcomponent anchorにしない | generic account auditと誤採用修正 | dedicated project accountとのrole区別が必要な事例だけreview |
| fork、mirror、対等hosting | Trust pathから除外し、系譜・merge guard側で扱う | Trust候補としての再調査 | 独立project identityが曖昧な場合のmerge review |
| Basker→InfoEnon、DateSky→Nomare | Trustだけで同一性、後継、currentを決めない | Trust pathから時間関係を推測する作業 | 別の時間関係policyで一度整理する |
| subdomain、registrable domain、共通rootだけの一致 | 構造一致をTrust evidenceに数えず、他の直接根拠がなければ`INSUFFICIENT` | domain一致のたびにownershipを確認する作業 | exact relationなど別の直接根拠が得られた場合だけ再評価 |

## ここからの進め方

この文書を原理原則と評価方法の設計チェックポイントとして締めた後は、次の順で進めます。正本DBの挙動を変える実装は、shadow evaluationの比較結果をレビューして設計を選ぶまで行いません。

### 1. 設計チェックポイントを確定する

本書と[登録プロジェクトへの帰属と信頼グラフの設計メモ](project-attribution-and-trust-design.md)を、現時点の合意と保留事項を保持する作業用文書として確定します。自動採用済みidentifierの根拠を再利用する方法、`INSUFFICIENT`のreview条件、`CONFLICTING`に対する手動判断の効力は、実測後に選ぶ論点として残します。

### 2. 既知事例を評価用catalogにする

ArabicaとOolongの共有repo、BaskerからInfoenonおよびDateSkyからNomareへの時間変化、organization accountの共有、serviceとrepoの相互参照、OAuth scopeとcollection、handleとDID、fork・mirror・対等hosting、subdomainや共通rootの事例を、期待結果と現在の手動確認を持つcaseにします。過去のconfirm、add seed、merge、separate-project履歴は正解そのものではなく、現行運用との一致・不一致を測る比較材料として使います。

### 3. 読み取り専用のshadow evaluatorを作る

既存DBとrelationから新しい信頼判定結果を計算し、DBを変更せずに現行結果と比較します。少なくとも、毎回登録起点まで経路を探索する案と、自動採用時の起点・判定経路を引き継いで再利用する案を比較します。自動採用の増減だけでなく、後続判断に利用できるidentifier、追加probe、review件数、代表sample、探索時間、経路長を報告します。

### 4. 比較結果から採用する設計と変更範囲を決める

case catalogの期待結果、誤採用、手動負担、説明可能性、性能、移行量をレビューし、根拠の再利用方法、`INSUFFICIENT`のreview条件、`CONFLICTING`の手動上書き、信頼判定結果の保存形式を確定します。全面変更の効果が小さい場合は、roleを考慮した監査、判定経路、Web UIの警告、直接関係の正本化など、効果を確認できた部分だけへ縮小します。

### 5. 本実装と移行を段階化する

実装する場合も一度に正本挙動を置き換えません。

1. role付き帰属主張、信頼判定結果、判定経路を実装する
2. decisionをrole付きclaimへ対応させ、手動判断と登録起点を明示する
3. 手動採用の優先順位とWeb UIの警告を実装する
4. 自動採用による変更を新しい採用規則へ接続し、旧v1と比較する
5. 直接関係を`project_relations`へ集約し、旧`trust_graph.edges`との一致を監査する
6. 明示的なdry-run結果とbackupを確認した後にだけ既存DBを再計算する

### 6. 検証して正本文書へ反映する

既知事例fixture、関連unit test、`bin/project-registry-test`、複製DBのshadow report、Web UIの主要導線、migration前後の保存を確認します。確定したproject定義、role、保存契約、predicate、評価規則だけをbriefと開発者向け正本文書へ反映し、正本DBへの適用は別途承認を得ます。

全面変更をマージする条件は、必須条件を満たしたうえで、既知事例の誤帰属リスク削減、説明可能性、重複保存と責務混在の解消、反復する手動確認の削減をsampleと件数で示せることです。component claimの挙動改善が小さく、roleや保存形式のmigration、継続監査の負担が上回る場合は全面変更を採用しません。

## 実装前に具体化するもの

shadow evaluation後に採用する設計を選んでから、次を実装契約として具体化します。

- 帰属主張、信頼判定結果、判定経路のJSON保存契約
- roleごとのpath templateと最大長
- 手動判断または承認済み登録起点を、信頼判定の起点として許可する条件
- conflictを構成する排他的claimとrelation
- 既存decisionへのrole付与と旧edgeのmigration手順
- case catalog fixtureとshadow evaluation reportの形式

この文書は上記の工程と判断地点までを定める設計チェックポイントとして締めます。評価結果から生じる具体的な実装判断は、shadow reportと実装計画へ分けて記録します。
