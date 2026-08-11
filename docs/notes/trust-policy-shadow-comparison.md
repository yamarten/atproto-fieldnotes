# 信頼ポリシーshadow比較

> 2026-08-11時点の作業用比較結果です。現行仕様の正本ではありません。この比較だけではproductionの自動採用規則、保存契約、DBを変更しません。

## 結論

role付きclaimと静的な方向付きrelationで信頼判定を行う構造は成立し、固定case 10件はすべて期待結果と一致しました。full-originとtrace-reuseのverdict差は0件で、今回のDBではfull-originの方が探索edge数も時間も少なかったため、次の試作では登録起点から毎回探索する単純な方式を既定案にできます。自動採用済みclaimへ判定経路を引き継ぐ保存契約を先に導入する利点は確認できませんでした。

一方、productionへ接続できる段階ではありません。最大の阻害要因は、旧confirm decisionが同じrepo URLの`source_repository`と`source_repository_record`へ曖昧に対応することと、現行`service_links_repo`が設計上の「明示されたsource repository」より広い意味で保存されていることです。前者を解消せずにrole付き監査を有効化すると大量のrepo確認が再発し、後者を解消せずにcandidate admissionを有効化するとスポンサーlinkや補助URLまでsource/service候補へ入ります。

したがって次の設計ゲートは、探索方式の選択ではなく、decisionをrole付きclaimへ対応させる移行規則、collection inventoryを登録起点として明示するprovenance、Admissionへ使えるdirect relationの生成契約を決めることです。ここを直したshadow reportを再レビューするまで、自動confirm、candidate保存、Web UI、schema migrationへは進みません。

## 実行条件

2026-08-11 02:38 JSTに、正本DBをSQLite `mode=ro`の単一transactionで読み、次を実行しました。

```sh
bin/project-registry compare-trust-policy \
  --db-path data/survey.db \
  --output .tmp/trust-policy-shadow-report.json \
  --example-limit 40
```

- report schema: 1
- policy: `trust-policy-shadow-v1`
- report content hash: `34218a1a6f521c64dc29fe73e8e13ea7c2506bb69f1b83930a89827de32d7295`
- 4,415 projects、24,660 role付きidentifier claims
- 66,651 deduplicated direct edgesのうち、静的policyがpathへ許可したものは8,004件
- 保存形をgraph nodeへ写せなかったrelationは5,644件。主に`project`、`account`、`oauth_client`のように現行matrixの6 kindへ直接対応しないendpointであり、強いedgeへ推測変換していない
- edgeの正本由来は`project_relations`のみ53,962件、旧`trust_graph`のみ7,304件、両方に存在5,385件

正本DBは別processから更新され得るため、上記hashと件数をこの比較snapshotの識別子にします。完全なJSONは`.tmp/trust-policy-shadow-report.json`に置き、commitしません。

## Source profile別の結果

| Source profile | 登録起点 | 曖昧な起点decision | SUPPORTED | INSUFFICIENT | CONFLICTING | NOT_EVALUATED |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| operator | 13 | 1 | 0 | 12,316 | 11 | 12,333 |
| operator + legacy migration | 1,065 | 1,204 | 347 | 10,917 | 11 | 13,385 |
| all current sources | 8,894 | 1,204 | 210 | 3,225 | 11 | 21,214 |

operatorだけでは現在の登録状態を説明できません。Web UI confirmは10件、operator add-seedは4件しか残っておらず、現在の大半はlegacy migrationまたはcollector由来です。

`all current sources`では8,314件のconfirmed `owned_nsid`を`implicit_collection_inventory`という登録起点として扱いました。これは信頼グラフがownershipを証明したという意味ではなく、現行保存契約にcollection sync等のprovenanceが残っていないためのshadow上の明示的fallbackです。この扱いを入れない場合、confirmed根拠不足は8,927件となり、collection観測からprojectを作る現行入口を誤って監査対象にしてしまいます。productionでは同じ暗黙規則を固定せず、登録起点のsourceを保存する必要があります。

`all current sources`の210件の`SUPPORTED`は既存candidate 171件とconfirmed 39件です。candidate内訳はowned NSID 117件とproject handle 54件で、service候補とsource repository候補の自動採用は0件でした。保存済みrelationから直接生成できる新規candidateの投影は19件でした。

## 運用負担としての読み方

| 区分 | 件数 | 扱い |
| --- | ---: | --- |
| 登録起点として採用済み | 8,894 | Trustによる自己評価をせず、登録sourceを表示する |
| component向けTrust auditの対象外 | 12,320 | developer、source record、authority domain、project account URL等。component降格reviewから外せる |
| candidateを自動採用可能 | 171 | relation生成契約を確認した後なら反復confirmを省ける可能性がある |
| candidateのままreview不要 | 1,952 | 新規reviewを発生させない |
| confirmedだがTrust不足 | 1,273 | そのまま1,273件の手動監査とは数えず、保存契約・collector不足ごとに原因をまとめて直す |
| conflict | 11 | guardとの衝突としてreviewする。うち1件は手動登録起点との併存 |

confirmedだがTrust不足の内訳はsource repository 1,200件、service 73件です。repo 1,200件の大半はrelation不足そのものよりdecisionのrole曖昧性の影響を受けています。active confirmのうち1,204件は、同じ`repo_url`値に`source_repository`と`source_repository_record`の二つのclaimが存在し、現行decisionにはroleがないため、shadow evaluatorがどちらを登録起点とするか推測しませんでした。これは各repoを再確認する問題ではなく、`source_repository_record`を観測用claimとして分離したうえで旧decisionを`source_repository`へ移す契約を決める問題です。

cleared confirmは9件あり、shadow policyが自動的に再採用するものは0件でした。既存clearを勝手に復活させないというnegative checkは通っています。

## 探索方式の比較

`all current sources`ではfull-originが23,316 edge探索、約20.4 ms、trace-reuseが60,123 edge探索、約31.2 msでした。verdict差は全profile合計で0件です。trace-reuseは各project内で採用済みclaimを反復的にfrontierへ加えるため、このsnapshotでは探索量を減らさず、保存するtraceや無効化規則だけを増やします。

SUPPORTEDな往路・復路は長さ1が78本、長さ2が342本で、3以上はありませんでした。これは将来の最大長を2へ確定する根拠ではありませんが、現在の既知データでは恣意的な長い推移pathを必要としていないことを示します。

この結果から、次のshadow実装もfull-originを維持し、入力fingerprint単位の再計算だけを最適化候補とします。trace-reuse用の永続schemaは追加しません。

## Candidate admissionの安全性

投影された新規candidate 19件には、MatadiscoやSoapstoneのserviceから明示されたrepositoryのように妥当そうな例がある一方、`https://github.com/open-source/sponsors`をsource repositoryとする例、repository READMEから`/.well-known/did.json`をservice surfaceとする例、同じrepositoryのrootとsubdirectoryを別source repositoryとする例が含まれました。

これは静的matrixの`service_links_repo` / `repo_links_service`規則が過剰なのではなく、現行保存predicateがその意味をまだ保証していないことを示します。現在のconfirmed `source_repository` relationには、明示的なSource/Repository表記だけでなく一般linkやREADME URLが混在しています。Admissionを有効化する前に、collectorがproject-scopedな正式参照だけへ強いpredicateを付け、一般linkを別predicateまたは観測へ残す必要があります。19件をそのまま自動登録する案は採用しません。

OAuth permission、`uses_collection`、`mentions_collection`、redirect、handle resolverはAdmissionへ使っていません。adopted nodeから出る関係のうち、`mentions_collection` 1,148件、`uses_collection` 325件などは明示的に抑止されました。

## 既知事例と履歴の確認

固定catalogでは、service/repo相互参照、accountからの片方向claim、OAuthとauthorityの復路、handle/DID、organization account、Arabica/Oolong型共有repo、fork/mirror、Basker/InfoEnon・DateSky/Nomare型の時間関係、subdomain、共有Lexicon authorityの10件が期待どおりでした。時間関係、fork、organization actorはpositive Trust pathへ入りません。

正本DBのArabicaではcollection inventoryと旧service seedを登録起点として保持し、`social.arabica.alpha.*`のcandidate collection 7件をSUPPORTEDとしました。一方、GitHub/Tangled repoは直接の相互pathまたはrole付き起点を欠くためINSUFFICIENTです。Oolongのcandidate repoも独立にINSUFFICIENTであり、repo共有をmerge根拠へ流用していません。

Basker、InfoEnon、DateSky/Nomareでは、各時点のcollectionまたはserviceを登録起点として保持するだけで、旧名と新名、前身と後継をTrust pathから推定しません。時間関係を別policyへ残す方針と一致します。

merge alias 506件すべてでmerge前snapshotを読めました。抽出したcomponent identifierのうち953件は現在の存続projectにもあり、126件はありませんでした。欠落sampleにはdocs/subpageなど網羅不要なservice surfaceが多く、collectionやrepoも少数含まれます。この比較はcoverage確認であり、実行済みmergeをTrustの正解にも、欠落126件をregressionにも数えません。将来のmerge replayではresource identityと時間関係を分けて分類する必要があります。

## 次の判断材料

次のshadow比較へ進む前に、以下を設計します。1、2、4と保存責務の論理契約は[Identifierの採用理由と保存契約の設計メモ](identifier-adoption-storage-design.md)へ整理しました。物理schemaは時間関係の設計後に確定します。

1. roleなしの旧`confirm_identifier(repo_url)`を、観測用`source_repository_record`ではなくproject componentの`source_repository`へ対応させる移行契約
2. collection inventory、canonical redirect、source sync、operator add-seedを、暗黙のclassificationではなくrole付き登録起点として保存する契約
3. `service_links_repo` / `repo_links_service`を正式なsource/service参照へ限定し、一般link、support、sponsor、docs、arbitrary README URLを分離するcollector契約
4. 手動confirmとblock/deferが併存する1件について、最新の明示的な手動判断を有効にし、自動処理ではguardを解除しない規則
5. confirmed service 73件の不足が、登録起点migration、collector不足、時間変化のどれかをsample分類する追加監査

これらを反映したshadowでcandidate admission sampleが安全になり、repoの一括的な疑似reviewが解消した場合にだけ、保存schemaとproduction接続の実装計画を作ります。
