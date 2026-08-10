# 信頼判定結果と採用policyの設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。信頼判定結果と採用policyの設計を始める前に、評価観点、既知事例による検証、運用負担の比較方法を固定します。設計合意前に実装へ反映しません。

## 目的

信頼グラフの接続規則は[登録プロジェクトへの帰属と信頼グラフの設計メモ](project-attribution-and-trust-design.md)で原則レベルのレビューを完了しました。本書では、そのgraphから得たpathをrole付き帰属主張の信頼判定へ変換し、採用policyが`confirmed`への変更を決めるまでを扱います。

設計案は抽象的な整合性だけで評価しません。既知の実例と現在の調査手順を基準に、誤採用を防げるか、妥当な採用を維持できるか、どの手動確認が不要になるか、どの確認が引き続き必要か、新しい定期監査を要求しないかを比較します。

## 評価観点と受入条件

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

誤採用防止、説明可能性、決定性、運用安全性はhard gateです。既知の危険事例を一件でも自動採用する、理由を再現できない、自動評価の比較前にDB mutationを必要とする設計は採用しません。

## 既知事例を評価単位にする

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

## 運用負担の比較

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

## マージ前に示す比較結果

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

大きな変更に見合うメリットは、自動採用数だけでは判断しません。具体的な誤帰属リスクを一群として除去する、判断経路を説明可能にする、混在している責務を実装上も分離する、または反復している手動確認を削減する効果を実例で示します。挙動改善が小さい一方で実装、migration、継続監査が大幅に複雑化する場合は全面変更を採用せず、効果のある範囲へ縮小します。

## 次に作る設計概要

上記の評価方法を前提に、次は次の内容を一つの概要として提示します。個別predicateを一項目ずつ合意する進め方には戻しません。

- graph探索、Trust policy、採用policyの責務境界
- role付き帰属claimとTrust verdictの形
- anchor、往路・復路、guard、operator decisionの合成
- role別の主要policy
- 保存、再評価、stale、conflictの扱い
- 現行実装からの移行単位
- 既知事例で見込む挙動と運用負担の変化
