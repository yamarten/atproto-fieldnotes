# 信頼判定結果と採用policyの設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。信頼判定結果と採用policyの評価観点、設計概要、既知事例による検証、運用負担の比較方法を保持します。設計合意前に実装へ反映しません。

## 目的

信頼グラフの接続規則は[登録プロジェクトへの帰属と信頼グラフの設計メモ](project-attribution-and-trust-design.md)で原則レベルのレビューを完了しました。本書では、そのgraphから得たpathをrole付き帰属主張の信頼判定へ変換し、採用policyが`confirmed`への変更を決めるまでを扱います。

設計案は抽象的な整合性だけで評価しません。既知の実例と現在の調査手順を基準に、誤採用を防げるか、妥当な採用を維持できるか、どの手動確認が不要になるか、どの確認が引き続き必要か、新しい定期監査を要求しないかを比較します。

## 要点

この案では、信頼グラフが観測済みの直接関係を保持・探索し、Trust policyがその経路からrole付き帰属claimの信頼判定結果を作り、採用policyがproject recordを変更します。candidate生成、信頼判定、採用、mergeは別々の判断として扱います。

- 評価対象はidentifier単体ではなく、登録プロジェクト、identifier、roleを組み合わせた帰属claimです。
- provenance付きdirect relationを正本とし、信頼グラフはそこから導出します。
- 自動採用には、同じroot anchorとclaimの間で意味上の往路と復路が必要です。二つの関係が同じ観測に由来していても構いません。
- 信頼判定結果は`SUPPORTED`、`INSUFFICIENT`、`CONFLICTING`、`NOT_EVALUATED`に分け、判定だけでは保存状態を変更しません。
- operator判断と承認済みbootstrap sourceだけを独立root anchorにし、自動採用済みclaimやdeveloper、organization、publisherを汎用anchorへ昇格させません。
- 通常運用では入力が変わったclaimだけを再評価し、定期的な全件監査は導入しません。

以下では、まずこの案を評価する条件を固定し、次に設計内容、現行との差分と実例、移行条件の順で示します。

## 評価方法

### 評価観点と受入条件

| 評価観点 | 受入条件 | 確認方法 |
| --- | --- | --- |
| 誤採用の防止 | 一方的な自己申告、共有repo、共有actor、root domain共有などから自動採用しない | 既知の誤判定・曖昧事例をfixture化する |
| 妥当な採用の維持 | service↔repo、handle↔DID、OAuth scopeとexact authorityなど、合意済みの有効経路を理由なく失わない | 現行結果との比較とsample reviewを行う |
| roleの分離 | project/service account、developer、organization、publisherごとに利用可能なrelationとattestation範囲を定義する | role別policy testを作る |
| 説明可能性 | 自動採用ごとに、評価対象claim、anchor、往路、復路、guard、判定理由を表示できる | decision traceを検査する |
| 決定性 | DB件数や探索順序に依存せず、同じ観測とpolicy versionから同じ結果を得る | 順序変更と再計算のtestを行う |
| 責務分離 | candidate生成、信頼評価、採用mutation、mergeが互いの代用にならない | APIと呼出経路を監査する |
| 運用安全性 | 新旧結果をDB更新なしで比較でき、既存DBへの反映前に全差分を確認できる | 複製DBでshadow evaluationを行う |
| 保守性 | predicateや例外分岐をcollectorへ分散させず、relation追加を中央policyで扱える | 重複規則と変更箇所を現行実装と比較する |
| 性能 | 通常のenrichまたはrepairでpath探索が実用時間内に収まり、組合せ爆発しない | 実DB複製で時間、edge数、探索量を計測する |
| 運用負担 | 省略できる手動確認、残る確認、新たに必要な確認とその発生条件を事例別に説明できる | 現行手順と新policyの作業差分を記録する |
| 過去判断との整合 | operator confirm、add seed、clear履歴を一律の正解とせず、入力経路別に新policyとの一致・不一致理由を説明できる | decision source別のretrospective comparisonを行う |

誤採用防止、説明可能性、決定性、運用安全性はhard gateです。既知の危険事例を一件でも自動採用する、理由を再現できない、自動評価の比較前にDB mutationを必要とする設計は採用しません。

### 既知事例を評価単位にする

syntheticな最小testだけでなく、正本DBまたは過去の調査で確認した実例をcase catalogとして保持します。一つのcaseは少なくとも次を記録します。

- 評価対象の登録プロジェクト、identifier、roleからなるclaim
- 入力となる直接観測とprovenance
- 期待するcandidate生成の有無
- 期待するTrust verdictと主要reason
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
| 判断説明 | 現在確認できる根拠 | 新しいdecision trace | 追加調査なしで理由を追えるか |
| 誤判定対応 | 修正、再取得、DB repairの手順 | override、recompute、migrationの手順 | 復旧が現行より難しくならないか |

「自動化できる」とだけ記載せず、どの入力が既にcollectorから得られるか、追加probeが必要か、初回だけの監査か継続的な監査かを区別します。新policyによって毎runまたは定期的な全件監査が必要になる場合は、誤採用リスクを大きく下げるなど、それを上回る利点を示せない限り採用しません。

### マージ前に示す比較結果

実装案をマージする前に、複製DB上のshadow evaluationから少なくとも次を報告します。

- 現行と新policyで結果が変わらないclaim数
- 新たに自動採用されるclaim数と代表sample
- 自動採用されなくなるclaim数と代表sample
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
  → 意味を型付けしたdirect relationの保存
  → 帰属policyによるrole付きcandidate claimの生成
  → relationから導出した信頼グラフのpath探索
  → Trust policyによるclaim verdict
  → 採用policyによるcandidate / confirmedの変更
```

信頼グラフはrelationを保持・探索する機構、Trust policyはpathの意味を判断する規則、採用policyはproject recordを変更するapplication policyです。candidate生成、Trust verdict、採用mutation、project mergeは相互に代用しません。

### 評価対象はrole付きclaim

評価単位はnodeまたは`kind + value`ではなく、`project_key + kind + normalized value + role`で識別する帰属claimです。たとえば、同じDIDでも`project_account`、developer、publisherでは別claimです。

`candidate` / `confirmed`はclaimの採用状態です。graph node自体にはこの分類を持たせません。`confirmed`は、そのrole付きclaimを登録プロジェクトの事実として採用しているという現在の意味を維持します。採用根拠は、operator判断、承認済みbootstrap source、Trust policyのいずれかとしてclassificationとは別に記録します。

### relationを正本にし、graphを導出する

target構造では、provenance付きdirect relationを`project_relations`の正本へ保存し、信頼グラフのedgeは接続マトリクスとrole policyから導出します。`eligible_for_auto_confirm`をcollectorがedgeごとに決めず、predicate、向き、source/method、対象claimのroleから中央policyが利用可否を決めます。

`mentions_collection`、fork、subdomainなど主マトリクス外のrelationも調査・表示用として同じ正本に保持できますが、graph builderがTrust pathから除外します。現在の`state_json.trust_graph.edges`は`project_relations`と同じ事実を重複保持してdriftし得るため、移行後はcanonical storeにせず、互換期間を終えたら削除します。Trust evaluationだけはpolicy versionと入力fingerprintを持つ再生成可能なcacheとして`state_json`へ置けます。

このtargetはSQL table追加を必須としません。`project_identifiers`のunique keyは既にroleを含み、`project_relations`にはprovenanceとcontextがあり、decisionのroleと採用根拠はmetadataで表せます。ただしJSON/metadataの保存契約変更と既存データの再投影は必要です。role検索や監査性能の実測後にindexまたはschema migrationの要否を決めます。

### root anchorとattestorを分ける

root anchorにできるのは、role付きclaimとして明示的に採用された次のものだけです。

- operatorが採用したcomponentまたはproject identity claim
- source assignment自体が承認済みで、その入力をprojectのbootstrapとすることが契約化されたclaim

Trust policyで自動採用されたclaimは、新しい独立root anchorへ追加しません。中継nodeを許したpathは元のroot anchorからその先まで探索できるため、連鎖追加は不要です。これにより、auto-confirm済みclaim同士が循環して根拠なしにanchor poolを維持することを防ぎ、すべての自動採用をoperatorまたはbootstrap sourceまでtraceできます。

developer、organization、publisherは事実を証言し得るattestorですが、confirmedであるだけでは汎用root anchorにしません。対象claimのrole policyがそのattestationを明示的に許可する場合だけ利用します。publisher DIDをdeveloperまたはproject accountへ読み替えません。

### pathの成立条件

自動採用に使う`SUPPORTED` verdictは、同じroot anchorとcandidate claimのendpointの間に、role policyが許可した往路と復路の両方がある場合に成立します。往路と復路が同じ一つの観測に由来しても、観測が意味上両方向を保証するなら利用できます。必要なのは独立source二件ではなく、一方的な騙りでは説明できない意味上の双方向性です。

探索はglobal predicate allow-listによる任意BFSではなく、claim roleごとに許可したpredicate遷移とendpoint roleを使います。中継nodeは許しますが、path長と遷移列はpolicyごとにboundedにし、新しい長い組合せが必要な場合は明示的なpolicyとして追加します。pairwiseな推移edgeは保存せず、採用時のtraceに実際のdirect edge列を残します。実測次数や動的scoreは使いません。

### Trust verdict

Trust policyはclaimを変更せず、次のverdictとreason、利用したroot anchor、往路・復路を返します。

| Verdict | 意味 | 採用policyの通常動作 |
| --- | --- | --- |
| `SUPPORTED` | policyで許可された双方向pathが同じroot anchorとの間に成立 | guardがなければ自動採用可能 |
| `INSUFFICIENT` | 必要な観測を行ったが片方向だけ、または意味上の強さが不足 | candidateのまま保持 |
| `CONFLICTING` | 信頼できる観測または排他的claimが両立しない | 自動採用せずreviewへ送る |
| `NOT_EVALUATED` | roleが対象外、または必要なprobeがまだ行われていない | reasonに応じて何もしないかtargeted probeを行う |

`NOT_EVALUATED`の`role_not_applicable`と`observation_pending`はreasonで区別します。前者はdeveloper情報など構成要素向けTrust policyの対象外、後者は取得可能な逆向き観測が未実施という意味です。`INSUFFICIENT`と`CONFLICTING`を「偽」の一種類へまとめません。

### role別の主要policy

| Claim family | 主なrole | 自動採用 | root anchor | 備考 |
| --- | --- | --- | --- | --- |
| project component | `service`、`source_repository`、`owned_nsid` | 対象。role別の双方向pathとguardを要求 | operatorまたは承認済みbootstrap component | repo共有は許すため、repo一致だけでconflictまたはmergeにしない |
| project identity | `project_account`、`project_handle` | 対象。projectが制御するsurfaceからの直接claimとaccount identity確認を要求 | operatorまたは承認済みproject identity | organization accountと区別する |
| actor relation | developer、organization、publisher | component自動採用の対象外 | 汎用rootにはしない | relationの事実性はactor用policyまたはoperator/source assignmentで扱う |
| intermediary / observation | `authority_domain`、`source_repository_record`、graph-only node | 対象外 | 不可 | path中継またはprovenanceとしてのみ使う |

developer accountとproject/service accountは同じaccount一般の規則を共有させません。将来、個人siteやBluesky投稿からdeveloper attestationを取得する場合も、actor用policyでclaimを型付けしてから利用し、project componentのroot anchorへ自動昇格させません。

### 採用policyとoperator decision

採用policyは次の優先順で一つのclaimを処理します。

1. activeなblockがあれば採用しない
2. activeなdeferがあれば期限または明示解除まで保持する
3. roleを指定したoperator confirmがあればoperator basisで`confirmed`にする
4. 承認済みbootstrap source契約に一致すればsource basisで`confirmed`にする
5. Trust verdictが`SUPPORTED`で追加guardがなければtrust-policy basisで`confirmed`にする
6. `CONFLICTING`はreviewへ、`INSUFFICIENT`はcandidate維持、`NOT_EVALUATED`はreasonに応じてtargeted probeまたは対象外とする

operator confirmがactive blockを暗黙に上書きすることは避け、明示的にguardを解除してから採用します。decision targetも`kind + value`ではなくrole付きclaimへ合わせます。同じDIDのdeveloper claimをconfirmしたことがproject account claimのconfirmになってはいけません。

既に`confirmed`のclaimが後の再評価で`INSUFFICIENT`または`CONFLICTING`になっても、通常のenrichで即時降格しません。Trust policyによる自動採用claimは独立rootではないため、新しい採用の根拠にはならず、既存classificationはreviewまたは明示的なrepairまで保持します。`CONFLICTING`なclaimはbasisにかかわらずanchor利用を停止し、operatorがconflictを解消または明示的に再判断します。この方法なら一時的な取得失敗による連鎖降格を避けつつ、新しい誤採用の拡大を止められます。

### 再評価とstale

再評価は次の入力が変わったclaimだけを対象にします。

- 関連direct relationの追加、更新、削除
- root anchor、operator decision、block、deferの変更
- claim roleまたはclassificationの変更
- Trust policy versionの変更
- DNS、handle resolution、redirectなど時間依存relationのtargeted reprobe

観測時刻だけを理由に全relationを一律失効させず、relation種別ごとに再取得契機を定義します。通常runごとの全件監査は導入しません。policy version変更時とmigration時だけ全件shadow evaluationを行い、通常運用では入力fingerprintが変わったclaimを再計算します。

## 現行との差分と実例

### 現行実装のbaselineと構造差分

2026-08-10の正本DBを`immutable` read-onlyで確認したbaselineは次のとおりです。

- 4,415 projects、24,475 identifier claims
- 保存済みtrust edge 14,346件、evaluation 9,503件
- `auto_confirm=true` 1,908件のうち、1,756件は`candidate_is_trusted_anchor`、実際の`mutual_trust_path`は152件
- 現行auditのunsupported confirmed 433件のうち、developer handle 348件、developer DID 70件で、component claimはrepo 14件とservice 1件
- in-memory再構成後のedgeは15,217件で、15件が`missing_recomputed_edge`、4件が`needs_reprobe`、429件が`should_downgrade`

現行はnode単位のevaluation、roleを見ないanchor、collectorが保存する`eligible_for_auto_confirm`、`project_relations`から同期したedgeと直接保存edgeの重複を持ちます。`candidate_is_trusted_anchor`が大半を占めるため、評価件数は自動採用の実効範囲を表していません。また、developer roleへcomponent向けTrust auditを適用した結果がunsupportedの大半を占めます。

新設計による直接の改善見込みは次です。

- 1,756件のroot anchor自己評価をTrust verdictから除き、採用basisとして表示する
- developer 418件をcomponent降格候補ではなくactor relation policyの対象へ分離する
- 現行baselineで根拠確認が必要なcomponent claimを15件へ絞り、shadow evaluationで新たに生じる差分だけを追加reviewする
- canonical relation storeからgraphを導出し、`missing_recomputed_edge`のような二重保存driftをなくす
- 自動採用152件について、root anchor、role、往路・復路をclaim traceとして再検証する

これらは実装前のbaselineから見込む効果であり、最終的な削減件数ではありません。新policyのshadow evaluationで増減とsampleを再計測します。

### 過去decisionとのretrospective comparison

正本DBには`confirm_identifier` decision、少数の現行`manual_seeds`、merge後のproject key aliasと旧project keyのinvestigation runが残っています。2026-08-10のread-only確認では、`confirm_identifier`は合計2,286件、active 2,277件、cleared 9件です。source内訳は`legacy_manual_seed_migration` 2,264件、Web UIでのoperator confirm 10件、`operator_add_seed` 4件、canonical redirect 7件、collection sync 1件でした。現行`manual_seeds` entryは9件です。

実行済みmergeを表す`project_key_aliases`は506件、存続先は293 projectsで、timestampは2026-06-21から2026-08-09まで残っています。alias 506件すべてに旧project keyの`investigation_runs`があり、合計1,221 runsの`input_project_json` / `output_project_json` snapshotを利用できます。alias rowはmergeの実行modeや判断理由の詳細を持たないため、全件を独立した手動判断とは断定しませんが、pre-mergeのidentifier claimを再構成する材料としてconfirm履歴より広いcoverageがあります。

この履歴は有用ですが、2,264件のmigration decisionを2,264回の独立した人間判断とは数えません。add seedは登録プロジェクトを始めるbootstrap操作であり、Trust policyが後から同じclaimを`SUPPORTED`にできることを必須にしません。canonical redirectやsyncもoperator判断とは分けます。

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

merge replayでは、alias作成前の最後の利用可能snapshotからsource/target双方のrole付きclaimを復元し、現在のcanonical projectでの採用basisと新Trust verdictを比較します。これにより、過去に人またはmerge workflowが一つの編集単位へまとめた後、どのcomponent claimをTrust policyだけで維持できるか、どれをbootstrap/operator basisとして残すべきか、どのclaimが共有repoや改名・後継など別論点かを分類できます。自動化件数を増やす目的ではなく、過度に保守的なpolicyが正しい構成要素を大量に失わないかを見るcoverage testとして使います。

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

## 移行単位とマージ判断

実装する場合も一度に正本挙動を置き換えません。

1. 新しいclaim evaluatorをread-only shadow modeで追加し、旧graph edgeと`project_relations`のunionからverdictを作る
2. case catalogと正本DB複製で旧評価との差分、role別件数、運用負担を確認する
3. decisionをrole付きclaimへ対応させ、operator/bootstrap root anchorを明示する
4. direct relationを`project_relations`へ集約し、旧`trust_graph.edges`との一致を監査する
5. 自動採用mutationを新しい採用policyへ接続し、旧v1は比較可能な間だけ残す
6. 明示的なdry-run結果とbackupを確認した後にだけ既存DBを再計算する

全面変更をマージする条件は、hard gateを満たしたうえで、既知事例の誤帰属リスク削減、説明可能性、重複保存と責務混在の解消、反復する手動確認の削減をsampleと件数で示せることです。component claimの挙動改善が小さく、role/保存migrationや継続監査の負担が上回る場合は、role-aware audit、decision trace、canonical relation storeなど効果が確認できた部分だけへ縮小します。

## 設計合意後に必要な具体化

概要合意後、実装計画では次だけを具体化します。

- claim、verdict、decision traceのJSON contract
- roleごとのpath templateと最大長
- operator/bootstrap sourceのroot anchor allow-list
- conflictを構成する排他的claimとrelation
- 既存decisionへのrole付与と旧edgeのmigration手順
- case catalog fixtureとshadow evaluation reportの形式
