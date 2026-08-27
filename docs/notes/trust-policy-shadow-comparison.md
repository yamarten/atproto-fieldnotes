# 信頼ポリシーshadow比較

> 作業用・比較結果文書です。現行仕様の正本ではありません。2026-08-17にschema version 3の正本DBをread-onlyで評価した結果と、次の設計判断に必要な材料を残します。完全なJSON reportは`.tmp/trust-policy-v5-20260817.json`に生成し、コミットしません。

## 結論

role付きの構成要素、方向付きの直接関係、登録起点を分けて信頼判定する構造は、schema version 3の保存データでも成立しました。固定12事例はすべて期待どおりで、二つの探索方式の判定差は0件です。登録起点から毎回たどる単純な方式の方が探索量も少ないため、判定経路を永続化して再利用する仕組みを先に導入する利点は確認できませんでした。

保存移行の目的も達成しています。比較器は有効な`project_identifier_adoptions`のうち`registration_origin=true`のrole付き採用理由だけを起点として使います。旧confirm decisionからroleを推測せず、全confirmed collectionを暗黙の起点にするfallbackもschema version 3では使いません。移行前に2件残っていた起点のrole曖昧性は0件になり、confirmedだが信頼経路を再構成できないclaimは387件から181件へ減りました。

ただし自動採用へ接続する段階ではありません。既存の直接関係から新規candidateを作る投影297件のうち265件はrepositoryからserviceへの関係で、GitHub Pagesのaccount root、package documentation、一般homepageなど、projectのservice surfaceとは限らないURLが多く含まれます。残る32件のserviceからrepositoryへの関係にもsponsor repositoryやbranch pathが含まれます。これは個別URLを除外する問題ではなく、collectorが正式なproject参照と一般linkを異なるpredicateまたは観測として保存する契約が未完成という問題です。

したがって次の設計ゲートは、探索方式やDB schemaではなく、Admissionに使える直接関係の意味をcollector横断で定義することです。relation収集を100%正確にすることは前提にせず、誤りを人が確認でき、確認結果を一般則の改善へ戻せるreview境界を併せて設計します。この比較は自動confirm、candidate保存、Web UI、正本DBを変更していません。

## 人間向けに言い換えた比較内容

「登録起点から毎回探索する方式」は、判定したいidentifierごとに、採用済みの入口まで行き帰りの根拠がつながるかを現在のgraphから調べ直します。report内の名前は`full-origin`です。

「根拠付き経路を再利用する方式」は、以前に支持したidentifierへ元の入口と経路を持たせ、それを足場に後続identifierを判定します。report内の名前は`trace-reuse`です。正しく無効化するには循環、起点喪失、relation消失への管理が必要です。

今回の正本DBでは両者の結論は同じで、単純に毎回調べる方式の方が少ないedge探索で済みました。そのため実装上は前者を既定案とし、入力fingerprint単位の再計算回避だけを将来の最適化候補にします。

## 実行条件

次のコマンドで正本DBをSQLite `mode=ro`の単一transactionとして読みました。指定したJSON以外は書き換えません。

```sh
bin/project-registry compare-trust-policy \
  --db-path data/survey.db \
  --output .tmp/trust-policy-v5-20260817.json \
  --example-limit 1000
```

- policy: `trust-policy-shadow-v5`
- report content hash: `68a1bacef478b3602c920f34e72f4680498c387c621366a6687593a604ceb40b`
- project: 4,538件
- current role付きidentifier claim: 19,823件
- identifier adoption: 全10,829件、有効10,709件、有効な登録起点10,217行
- deduplicated direct edge: 63,542件、うち静的policyがpathへ許可したもの8,110件
- graph nodeへ写さなかったrelation: 14,744件
- 固定case: 12件中12件合格

## Source profile別の結果

Source profileは、どの採用入口までを登録起点として認めたら現在のgraphをどこまで説明できるかを見る累積比較です。手動だから自動より正しいという順位付けではありません。

| Source profile | 論理的な登録起点 | SUPPORTED | INSUFFICIENT | CONFLICTING | NOT_EVALUATED |
| --- | ---: | ---: | ---: | ---: | ---: |
| operator | 15 | 0 | 12,654 | 8 | 7,161 |
| operator + legacy migration | 2,253 | 387 | 10,029 | 8 | 9,399 |
| all current sources | 10,210 | 253 | 2,206 | 8 | 17,356 |

`all current sources`はWebとadd-seed 15行、legacy migration 2,238行、collection inventory migration 7,810行、collection import 113行、collection sync 33行、canonical redirect 7行、manual seed 1行を含みます。有効な登録起点10,217行と論理的な起点10,210件の差は、同じrole付きclaimへ複数の採用理由が残る分です。理由を捨てず、graph上では同じ起点として一度だけ数えています。

移行前の比較器は旧decisionと暗黙collection inventoryから起点を復元していたため、同じrepo URLに`source_repository`と`source_repository_record`がある場合にroleを選べませんでした。v5はadoptionの`kind + value + role`をそのまま使い、曖昧な起点、現行claimと対応しない起点、review_requiredな起点はいずれも0件でした。

## 運用負担としての読み方

`all current sources`の内訳は次です。

| 区分 | 件数 | 扱い |
| --- | ---: | --- |
| 登録起点として採用済み | 10,210 | Trustによる自己評価をせず、採用理由を表示する |
| component向けTrust auditの対象外 | 7,146 | developer、organization、project account等を構成要素の自動採用監査から外す |
| candidateを自動採用できる可能性 | 188 | relationの意味とsampleを確認してから接続する |
| candidateのまま追加review不要 | 2,025 | 現状を維持する |
| confirmedだがTrust不足 | 181 | 即時降格せず、登録起点漏れかrelation不足かを類型で監査する |
| conflict | 8 | active guardとの衝突として人が確認する |

自動採用可能と出たcandidate 188件は`owned_nsid` 127件と`project_handle` 61件です。collectionについては、真collectionに未使用や騙りが少数混ざることより、別projectを混ぜることとATProto外domainをproject化することを重く見る既存方針のままsample reviewします。

confirmedだがTrust不足の181件は`owned_nsid` 146件、service 25件、source repository 10件です。これは181件を直ちに人が再確認するqueueではありません。migration時の採用理由が登録起点でなかったもの、後から追加された構成要素、現行predicateでは往復を表現できないものを分け、一般則で減らせる範囲を先に調べます。

cleared confirmは10件あり、shadow policyが自動的に再採用するものは0件でした。既存clearを勝手に復活させないnegative checkは維持されています。

## Candidate admissionの安全性

新規candidate投影297件は、`repo_links_service`によるservice 265件と、`service_links_repo`によるsource repository 32件です。由来はGitHub repository metadata 199件、GitHub page探索65件、service page 31件、service probe 1件、Tangled README 1件でした。

妥当そうな正式homepageやsource repositoryも含みますが、同じ規則でGitHub account root、Swift Package Index、documentation path、sponsor repository、repository内のbranch pathも候補になります。ホスト名やURL patternのdeny listを増やすのではなく、collectorが「repository metadataにある任意homepage」と「そのprojectのserviceとして明示されたURL」、「page内の一般GitHub link」と「Source/Repositoryとして明示されたlink」を区別する必要があります。

OAuth permission、`uses_collection`、`mentions_collection`、redirect、handle resolverはAdmissionへ使っていません。既存起点から出る`mentions_collection` 33,596件、`uses_collection` 14,577件などは明示的に抑止されました。collectionは現状`sync-collections`が主入口なので、他経路からのcandidate生成を広げる優先度は低いままです。

## 探索方式と経路長

`all current sources`では、登録起点まで毎回調べる方式が25,134 edge探索、約19.1 ms、経路を再利用する方式が50,559 edge探索、約26.3 msでした。verdict差は全profile合計で0件です。

SUPPORTEDな往路・復路は長さ1が127本、長さ2が364本、長さ3が15本でした。これは最大長3をpolicyとして確定する根拠ではありませんが、現在の既知データでは任意に長い推移を必要としていないことを示します。固定最大長はまだ置かず、有限なproject内graphの最短経路を使います。

## 既知事例、merge履歴、時間関係

固定catalogでは、service/repository相互参照、OAuthとauthorityの復路、handle/DID、organization account、Arabica/Oolong型共有repository、fork/mirror、旧identifier、non-production surface、subdomain、共有Lexicon authorityを確認しました。共有repository、organization account、fork、旧identifierはpositive Trust pathやmerge根拠へ昇格しません。

merge aliasは520件あり、merge前snapshotから取り出したidentifierの987件は存続projectにもあり、127件はありませんでした。これはcoverage比較であり、過去mergeを正解データにも、欠落を自動regressionにも数えません。

redirect観測1,160件はURL形状と既存claimだけで分類し、移転とは決めませんでした。人が見る候補は22組です。Basker / InfoEnonやDateSky / Nomareのような改名・後継、fork、mirror、同内容surfaceは、通常の帰属・merge判定から分けた時間・派生関係として引き続き保留します。忘却を避けるため、時間関係の導入条件は[登録プロジェクトへの帰属と信頼](../developer/project-attribution-and-trust.md)と[登録プロジェクトのマージ規則設計](project-merge-policy-design.md)に残します。

## 次の設計ゲート

1. Admissionに使える正式参照と、表示・調査だけに使う一般linkの保存契約をcollector横断で定義する
2. 297件をrelation sourceとpredicateの類型ごとにsample reviewし、人の判断を保存して一般則へ戻せる最小のreview動線を決める
3. confirmedだが不足する181件を、登録起点ではない採用、relation未収集、時間変化、誤帰属へ類型化する
4. candidate 188件の自動採用が既存手動confirmとどこまで一致し、どの確認を省き、どの追加reviewを生むかを評価する
5. 以上が運用負担に見合う場合だけ、productionの自動採用とcandidate保存への接続計画を作る

## 検証

- trust comparator unit/temp DB test: 18件合格
- 固定case catalog: 12/12合格
- schema version 3のrole付き登録起点、非起点adoption、review_required、旧fallback境界をtestで固定
- 正本DB: read-only CLI実行成功
- 二つの探索方式: verdict差0件
- production behavior: 変更なし
- `bin/project-registry-test`: 632件合格

移行前の`trust-policy-shadow-v1`からv4までの詳細値はGit履歴に残します。現行判断ではv5のschema version 3比較を基準にします。
