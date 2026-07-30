# Project 帰属と Trust の設計計画

> 作業用・検討用文書です。現行仕様の正本ではありません。
> この文書は、Trust Graph の接続規則を決める前に必要な原理と作業順を忘れないための計画です。
> predicate 名、identifier role、保存形式、移行方法は未確定であり、レビュー前に実装へ反映しません。

## 目的

この project は、人が行っていた atproto 関連 project の調査を、収集と判断に分けて機械化します。
判断の中心には次の二つがあります。

- identifier が調査対象の project に属する構成要素か
- 収集した情報が、その project の説明や「何であるか」の判断に役立つか

Trust Graph は前者に関係しますが、project への帰属判断そのものと、帰属に関する主張が事実かを
一つの評価へ混ぜないことを基本方針とします。

## Project の暫定的な意味

ここでいう project は、厳密に同一な resource の集合ではありません。所有された Lexicon、主要実装、
source repository、提供 service などを、一つの説明・追跡対象としてまとめる編集上の単位です。

Lexicon、実装、repository、service は互いに別の resource ですが、関係が適切なら同じ project record の
構成要素になれます。一方、同じdeveloperが関与する、同じcollectionを利用する、同じrepositoryへ
言及する、といった事実だけでは同じ project record に収めません。

この定義は、将来の mirror、instance、monorepo、fork、template、派生 project の境界を判断する際にも
使います。これらの詳細は未決ですが、同じcode、同じowner、同じdomainのいずれか一つだけで
project同一性を確定しないことを出発点にします。

## 区別する判断

### Resource identity

二つのURL、DID、handleなどが、同じresourceまたはそのcanonical aliasを指すかを扱います。

### Project attribution

あるresourceを、このregistryで同じproject recordへ所属させるべき構成要素と判断するかを扱います。
resource同士が同一物である必要はありません。

### Trust

「このresourceはこのprojectの構成要素である」という主張が、騙りや偶然のlinkではなく、
信頼できるsourceと関係で裏付けられているかを扱います。

### Project merge

独立して形成された二つのproject recordが、同じ説明・追跡単位を二重に表しているかを扱います。
既存projectへ新しい構成要素を追加するconfirmationとは非対称性と前提が異なるため、
Trust Graphのpathをそのままmerge scoreへ流用しません。

### 説明用情報の評価

developer account、dependency、used collectionなど、projectの説明や調査には役立つが、通常は
project identifierにしない情報を扱います。これはTrust Graphの主目的ではありません。

## 収集対象の暫定分類

### 1. Project の構成要素

同じproject recordへ、特定roleで所属させる対象です。

- owned Lexicon / NSID
- sourceまたは主要実装repository
- operated service
- その他、一つの説明・追跡対象として扱うresource

### 2. 証言主体・関係者

帰属主張の裏付けや説明には使えますが、通常はproject identifierにしません。

- developer account
- organization
- publisher DID
- service operator

### 3. 関連 resource

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

identifier kindだけでは分類を決めません。同じrepo URLでも、source repository、dependency、mirrorでは
projectとの関係が異なります。

## Candidate と confirmed の意味

candidate identifierは、信頼済みのproject境界の内側ではありません。「このprojectの構成要素かもしれない」
という帰属仮説です。project record内に保存されていることだけを理由に、anchorまたは強い証言主体として
扱いません。

confirmed identifierは、projectの構成要素として妥当であり、その帰属主張がTrust policyで支持され、
block、defer、operator判断に反しないものです。

概念上の処理は次の順です。

1. 外部情報を収集する
2. relationの意味から、構成要素、関係者、関連resource、観測のどれかを判断する
3. 構成要素になり得る対象だけをproject identifier candidateにする
4. 帰属主張をTrust policyで検証する
5. guardとoperator判断を適用し、条件を満たすcandidateをconfirmedにする

candidate生成を厳密にする目的は、project境界とreview品質を保ち、後段で誤用される対象を減らすことです。
Trust上の主要な安全条件は、candidate、graph-only node、derived identifierをtrusted anchorにしないことと、
candidate自身の自己申告だけでconfirmationを成立させないことです。

## Identifier の性質を分ける

identifierについて、次の性質を同じ意味として扱いません。

- projectの構成要素か
- 人間向けにprojectを代表するか
- 他projectとの共有を許すか
- discoveryまたはlookupの入口に使えるか
- Trust評価のanchorに使えるか

### Repository

repositoryは一つのprojectに複数存在でき、一つのrepositoryが複数projectにまたがることもあります。
monorepo、共通実装、複数instance、mirror、forkがあるため、confirmed repositoryの一致だけでprojectを
自動mergeしません。

repo URLだけでは関係範囲を表せない実例が出た場合は、package、subdirectory、manifest、Lexicon pathなどを
relation contextとして保持することから検討します。接続マトリクス確定前に新しいidentifier kindを追加しません。

### NSID

confirmed `owned_nsid`はprojectの構成要素であり、ownership確認の強いanchorになり得ますが、人間向けに
projectを代表するとは限りません。一つのprojectは複数NSIDを所有できます。

current ownershipでは、同じfull NSIDは最大一つのprojectにだけ帰属するものとして扱います。他projectによる
利用、互換実装、参照はownershipではなくrelationです。二つのprojectが同じconfirmed `owned_nsid`を持つ場合、
直ちにmergeせず、重複record、誤帰属、分割ミス、ownership移転のreview対象にします。

repository内にLexicon JSONが存在することだけでは、そのprojectによるNSID ownershipを意味しません。

## Trust Graph の責務

Trust Graphは、project identifier candidateを発見・生成する機構ではなく、帰属主張をどのidentifierとsourceが
どの方向から支持しているかを説明可能にする機構として設計します。

graph nodeには次が混在し得ます。

- confirmedまたはcandidateのproject identifier
- 外部の証言主体
- protocol解決の中継点
- claimの対象

graph nodeになっただけではproject identifierになりません。developer DIDやauthority domainは、projectの
構成要素候補でなくても、証言主体または中継点として必要になる場合があります。

Trust Graphの直接出力は、将来的には`auto_confirm`というmutation判断ではなく、主張がどの程度支持されているか、
どの方向の確認が欠けているか、conflictまたはreprobeが必要かを示すTrust verdictとする方向で検討します。
最終confirmationは、project attribution、Trust verdict、guard、operator判断を組み合わせるapplication policyです。

## 設計作業の順序

### 1. Project境界と対象分類のレビュー

- projectの編集・追跡単位としての定義を確認する
- 構成要素、証言主体・関係者、関連resource、観測の境界を確認する
- repository、NSID、serviceなどの共有・排他性を整理する
- mirror、instance、monorepo、派生projectで自動判断しない境界を置く

### 2. 帰属マトリクス

各relationについて次を整理します。

- subjectのkindとrole
- relationの意味
- objectのkindと想定role
- 静的な多重度
- 対象分類
- project identifier candidateを生成するか
- 調査先または説明用情報としてだけ保持するか
- 他projectとの共有を許すか

この段階では、主張を信用できるかを決めません。

### 3. Trust関係マトリクス

帰属主張ごとに次を整理します。

- 誰が誰について主張したか
- relationの向き
- sourceを誰が制御できるか
- subjectがconfirmed、candidate、graph-only、derivedのどれか
- 反対方向または独立sourceの確認が必要か
- strong pathに使えるpredicateと中継node
- supported、self-claimed only、unsupported、conflicting、needs reprobeなどの結論

正確なTrust verdict名はこの段階で決めます。

### 4. Confirmation policy

- project構成要素として妥当である
- 帰属主張がTrust policyで支持される
- block、defer、operator判断に反しない

という条件の合成を定義します。candidate、graph-only node、derived identifierをanchorにしないこと、
roleを無視してDIDやdomainを昇格しないことをinvariantにします。

### 5. Merge policy

confirmedなproject構成、role、共有・排他性、独立surface、派生関係、operator判断を入力に、二つのproject recordを
統合するかを別途設計します。Trust Graphのauto-confirm path、developerやpublisherの一致、candidate identifierの
一致をそのまま正のmerge根拠にしません。

### 6. 現行実装との差分監査

- collectorごとのcandidate生成箇所
- 調査先とproject identifier candidateの混同
- Trust Graphが帰属判断やmutationまで担っている箇所
- roleを無視した`kind + value`評価
- candidate、graph-only node、derived identifierのanchor化経路
- 一つの観測から生成される逆向きedge
- candidate repoや弱いdomain signalを使うmerge判定
- repositoryとNSIDの共有・排他性の現行動作

を確認します。

### 7. 正本文書化と実装計画

設計合意後、短いproject定義は`docs/brief.md`、保存上の分類とroleは
`docs/developer/project-record.md`、観測・帰属・Trustの関係は
`docs/developer/investigation-model.md`、具体的なpredicateと評価規則は
`docs/developer/registry-internals.md`へ反映します。

その後に、predicate追加、role追加、保存契約変更、policy version更新、DB再計算の必要性を判断します。
実装を行う場合は、対象test、全unit test、既知fixtureまたはsample review、temp DB、正本DB複製のdry-run/auditを
変更内容に応じて実施します。正本DBへの適用は別途承認を得ます。

## 現時点で固定しないもの

- 個別predicate名
- Trust verdictの名前と段階
- 新しいidentifier role
- graph edgeと`project_relations`の保存分担
- repository subpath用の新identifier kind
- SQL schema変更またはDB migrationの要否
- mirror、instance、monorepo、ownership移転の最終分類
- merge scoreと自動実行条件

## 関連する既存メモ

- [Identity and Ownership Evidence Notes](identity-ownership-evidence.md)
- [派生関係の設計メモ](derivation-relations.md)

これらは現行仕様の正本ではありません。本計画との整合を確認し、確定した内容だけを各正本文書へ移します。
