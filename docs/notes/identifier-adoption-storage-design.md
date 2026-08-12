# Identifierの採用理由と保存契約の設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。[信頼ポリシーのshadow比較](trust-policy-shadow-comparison.md)で見つかった保存上の曖昧さを解消する論理契約を記録します。production schema、既存DB、自動confirmの挙動はまだ変更しません。

## 結論

登録プロジェクトに属するidentifierと、そのidentifierを採用した理由、収集しただけの観測を分離します。

- `project_identifiers`は「このidentifierが、このroleで、この登録プロジェクトに現在属する」という現行の帰属主張を表します。旧identifierとnon-production identifierは、通常の構成要素経路へ混入しない別の肯定的関連として保存します。
- `confirmed`は少なくとも一つの有効な採用理由を持つ帰属主張、`candidate`はまだ採用理由を持たない帰属主張を表します。`confirmed`を信頼と無関係な単なる登録状態へ弱めません。
- 採用理由は、手動判断、登録起点、信頼判定規則による自動採用を区別し、一つの帰属主張に複数保持できます。
- recordの発見情報やauthority domainなど、帰属主張そのものではない観測は`project_identifiers`へ入れません。
- 直接関係の正本は`project_relations`へ一本化し、信頼グラフのedgeと判定経路はそこから再生成できる評価結果として扱います。
- block/deferとconfirmの優先関係は最新の明示的な手動判断で決めます。自動処理は有効なblock/deferを解除しません。

この論理契約は確定案とします。一方、採用理由をJSON列に置くか別テーブルへ置くかなどの物理配置は、[時間関係の設計](temporal-project-relations.md)と実例監査を終えてから確定します。改名、移転、後継を表す履歴と採用理由の履歴を別々に保存できることが物理設計の条件です。

## 分離する四つの情報

### 帰属主張

帰属主張は`project key + identifier kind + value + role`を単位にします。同じURLやDIDでもroleが異なれば別の主張です。手動判断、信頼判定、採用を止める条件も、この単位へ対応させます。

`project_identifiers`はrole、`candidate` / `confirmed`、primary指定、表示に必要な最小情報を持つ帰属主張の正本です。[Identifierと登録プロジェクトの時間関係の設計メモ](temporal-project-relations.md)で定義する旧identifierとnon-production identifierも肯定的なproject関連情報ですが、全identifierへ複数の有効期間を要求しません。収集元固有のrecord、導出途中のdomain、noiseを、identifierに似ているという理由だけで同じ表へ置きません。

### 採用理由

採用理由は、その帰属主張をなぜ`confirmed`として維持しているかを表します。種類は次の三つです。

| 種類 | 意味 | 例 |
| --- | --- | --- |
| `operator` | operatorが対象roleの帰属を明示的に採用した | Web confirm、operator add-seed |
| `bootstrap` | 承認済みの収集入口が登録プロジェクトまたは帰属主張を作った | collection syncで観測したcollection、project作成時のseed、canonical sourceからの取込 |
| `trust_policy` | 採用済みの起点との往路・復路を信頼判定規則が支持した | serviceとsource repositoryの正式な相互参照、OAuth利用とexact authorityによるcollection支持 |

「登録起点」は四つ目の種類ではなく、採用理由の属性です。operatorが新規projectを作ったseedにも、collection syncが新規projectを作ったcollectionにも`registration_origin: true`を付けられます。これにより、「手動だから起点」「自動だから信頼判定が必要」という誤った二分を避けます。

採用理由は少なくとも種類、source、作成時刻、登録起点か、元のdecision・collector run・信頼判定結果への参照を持ちます。信頼判定規則による理由にはpolicy versionと入力fingerprintも必要です。往路・復路そのものは再生成可能な判定結果へ置き、帰属主張へ複製しません。

論理的には次のような情報です。これは保存JSONの確定schemaではありません。

```json
{
  "kind": "operator | bootstrap | trust_policy",
  "source": "web_ui | sync_collections | ...",
  "registration_origin": true,
  "created_at": "...",
  "source_ref": "...",
  "policy_version": "...",
  "input_fingerprint": "...",
  "cleared_at": null,
  "review_required": false,
  "review_reason": null
}
```

`cleared_at`がない採用理由を有効とします。`review_required`は、過去の自動採用理由が現在の観測では再現できないが、即時降格も妥当でない状態を表します。この状態は`confirmed`を維持しますが、後続の自動採用に使う信頼判定の起点からは外します。

### 観測

観測は「調査時に何が見えたか」であり、それだけでは登録プロジェクトへの帰属を意味しません。

- `source_repository_record`はidentifier roleから外し、source repositoryを発見・取得したrecordとそのprovenanceとして保持します。project componentとしてのrepository URLは`source_repository`だけです。
- `authority_domain`はcollectionの解決や信頼経路に使うgraph nodeまたは観測であり、登録プロジェクトのidentifierにはしません。
- 一般link、mention、OAuth利用、検索hit、record creatorなどは、許可された直接関係または調査用観測として保存しても、それだけで採用理由にはしません。

初期移行では新しい汎用観測テーブルを導入せず、既存の`state_json.evidence`と`project_relations`のprovenanceで表現できるものを使います。複数projectから共有・再利用する観測が増え、JSON内で同じ事実が重複することが確認できた時点で専用テーブルを検討します。

### 信頼判定結果

信頼判定結果は、現在の直接関係と静的policyから再計算できるcacheです。`SUPPORTED`、`INSUFFICIENT`、`CONFLICTING`、`NOT_EVALUATED`、評価対象のrole、起点、往路、復路、policy version、入力fingerprintを保持できます。

直接事実は`project_relations`だけを正本にします。旧`state_json.trust_graph.edges`は移行時にprovenanceを監査し、正本relationへ写せたものだけ残します。同じ事実を二つの保存場所へ書き続けません。信頼判定結果は削除・再生成できるため、帰属主張や手動判断の履歴の代用にしません。

## 状態遷移

### 基本不変条件

- 有効な採用理由が一つ以上あれば`confirmed`、一つもなければ`candidate`です。
- 一つの帰属主張に複数の採用理由があって構いません。手動採用済みのclaimが後から信頼判定でも支持された場合、どちらかを捨てません。
- `candidate`から直接relationを辿って`SUPPORTED`になっても、採用規則を実行するまでは`candidate`のままです。
- 登録起点であることは自己評価による`SUPPORTED`を意味しません。信頼判定の対象外であり、採用理由を表示します。
- 有効なblock/deferがあるclaimへ、自動処理は採用理由を追加しません。
- roleなしのdecisionを新規作成しません。block/deferだけは、operatorが意図的に全roleを対象にする場合に限りrole省略を許します。

### 手動confirm

手動confirmは対象roleを必須とし、同じ範囲にある過去のblock/deferを解除して`operator`採用理由を追加します。既存のbootstrapまたはtrust採用理由があっても保持します。

blockを解除するconfirmは、Web UIで警告を表示してoperatorの意図を確認する対象にします。deferを解除するconfirmは通常の再判断として扱います。どちらも最終的な保存規則は同じで、最新の明示的な手動判断が有効です。

### 手動block/defer

手動block/deferは対象範囲の有効な採用理由を終了させ、自動採用を止めます。既存の採用理由とdecisionは履歴として残しますが、古い採用理由を自動的に復活させません。

deferが期限切れになっても、それだけで過去のconfirmは復活しません。新しい収集結果または信頼判定が採用条件を満たせば、改めて採用理由を作れます。これにより、期限切れと同時に古い観測を無条件で再採用することを避けます。

### bootstrap

承認済みの収集入口は、project作成または既存projectへの追加時に`bootstrap`採用理由を作ります。collection syncではrecordの存在を入口として十分とし、その事実を信頼グラフで自己証明させません。保存するのは「このcollectionからprojectを作った、またはこのprojectへ追加した」という起点の文脈です。

bootstrap sourceは決め打ちの一種類にせず、少なくともcollection sync、legacy seed migration、canonical redirect、source syncを区別します。operator add-seedは`operator`採用理由として別に保持します。各sourceを信頼判定の起点に使えるかはrole別policyで決め、単に`bootstrap`であることだけでは許可しません。

### 信頼判定規則による採用と再評価

自動採用は、candidateが`SUPPORTED`で、有効なblock/deferがない場合だけ`trust_policy`採用理由を追加します。Admissionが作ったcandidateを即座に採用せず、candidate生成と信頼判定を別の処理として保ちます。

過去に信頼判定で採用したclaimが再評価で`INSUFFICIENT`になった場合、即座にcandidateへ戻しません。他の有効な採用理由があればtrustの理由だけを終了し、変化を監査へ出します。trustの理由しかなければ`review_required`にして`confirmed`を維持しますが、そのclaimを後続の信頼判定の起点には使いません。

`CONFLICTING`の場合も自動採用と起点利用を止め、手動reviewへ出します。operatorまたはbootstrapの採用理由があるclaimを信頼判定だけで自動削除しません。明示的なblock/deferがある場合は、前述の手動判断の規則が優先します。

再評価で再び`SUPPORTED`になれば`review_required`を解除できます。信頼判定規則の変更やrelationの消失だけで、operatorの採用理由や登録起点を消しません。

## Candidate admissionとの境界

Admissionは許可済みの直接関係から帰属主張を`candidate`として投影するだけで、採用理由を作りません。serviceから明示されたsource repository、repositoryから明示されたservice、明示的なschema定義など、接続マトリクスで許可した関係に限定します。

OAuth利用、accountからのservice/repository link、mention、共通host、一般README linkからcandidateを作りません。ただしcollectorの意味分類を現時点で100%にすることは移行条件にしません。人間のreviewで誤candidateを棄却し、その判断を将来の一般則とcollector改善へ戻せることを優先します。collector固有の例外を積み重ねて19件すべてを事前に無害化する方針は採りません。

## Website、service、補助pageの境界

公開URLは、同じ値を持ちうる三つの責務に分けます。値が同じでも意味は統合しません。

| 責務 | 現行の保存先 | 意味 | identifier・信頼判定での扱い |
| --- | --- | --- | --- |
| Website | `projects.website_url` | 人がprojectを知るための代表的な公式URL。紹介、documentation、native appの配布pageでもよい | Websiteであることだけではidentifierにも信頼の起点にもならない |
| service | `project_identifiers`の`service_url` / `service` | projectの機能を実際に提供するURL。Web app、API、PDSなど。一つのprojectに複数あってよい | confirmed service identifierとして信頼判定と帰属判定の対象にできる |
| 補助page | `state_json.evidence.resource_url_records` | documentation、support、配布先など、説明や調査に役立つURL | 表示・調査用であり、identifierや信頼の起点にはしない |

Websiteとserviceは包含関係ではありません。同じURLが両方でもよく、Websiteだけ、serviceだけでも構いません。例えば`at-work.social`は同じURLをWebsiteとserviceにでき、Lexicon CommunityやlibraryのdocumentationはWebsiteだけ、`pds.openfederation.net`のようなPDS endpointはserviceだけにできます。補助pageがWebsiteと同じ値なら、画面ではWebsiteを優先して重複表示を抑えます。

通常の正規化、収集、candidate confirmでは次を不変条件にします。

- Websiteをservice identifierへ自動追加しません。
- 最初のservice URLからWebsiteを自動設定しません。
- service candidateのconfirmによってWebsiteを書き換えません。
- Websiteと一致するserviceを`primary service`とはみなしません。serviceに代表指定は導入しません。
- serviceの稼働状況はservice URLから判断し、紹介・documentation用Websiteの応答をservice稼働の代用にしません。
- mergeではWebsiteの選択とservice identifierの和集合を別々に処理します。

operatorが明示的にservice URLを`add-seed`して新規projectを作り、Websiteが空の場合だけ、入力の手間を減らすため同じURLでWebsiteを初期化して構いません。これは二つの責務を同一視する規則ではなく、operator入力時だけの初期値です。後からWebsiteとserviceを独立に変更・削除できます。collection syncなどの自動入口やcandidate confirmには適用しません。

この分離のために新しいidentifier roleやSQL tableは追加しません。現行DBではWebsiteが設定された612 projectすべてで、同じURLがconfirmed serviceにも入っていますが、一律に分離して612件を再監査しません。既存の重なりは既定で維持し、[Identifier採用理由の移行review](identifier-adoption-review.md)でWebsiteのみ、補助page、former、non-production、調査対象外と判断した既知事例だけを移行対象にします。

## 既存DBの移行規則

物理schemaを決める前に、複製DB上で次の論理移行を再現できることを確認します。

1. `source_repository_record`を`project_identifiers`から除き、record metadataとprovenanceを観測へ戻します。componentとして残すURLは`source_repository`です。
2. `authority_domain`を`project_identifiers`から除き、collection解決の観測と直接relationを残します。
3. identifier対象のdecisionへroleを対応させます。同じrepo URLの`source_repository`と`source_repository_record`に曖昧だった旧confirmは、record側を除いた後の`source_repository`へ移します。roleを一意に決められないdecisionは推測せずmigration reviewへ出します。
4. activeなWeb confirmとoperator add-seedから`operator`採用理由を作ります。legacy manual seed migrationは現在のoperator判断と同一視せず、移行由来を明示した`bootstrap`として扱います。
5. collection sync等で作られたconfirmed collectionには`bootstrap`採用理由と登録起点の文脈を復元します。復元できない場合は暗黙のTrust支持を作らず、`migration_inferred`としてsample reviewの対象にします。
6. 現行classificationを採用理由から再構成し、採用理由のないconfirmedをsource・role別に監査します。件数をそのまま個別review件数とはみなしません。
7. 旧trust edgeと`project_relations`をprovenance込みで重複排除し、正本relationへ写せないedgeは保存理由を分類します。

正本DBで確認済みの`https://skymarshal.blue`は、Web confirmの24秒後にdeferされています。最新の手動判断を採る規則ではdeferが有効であり、移行時に古いconfirm由来の採用理由を有効にしません。この一件を一般則の検証例にし、個別例外は作りません。

## 物理schemaを確定する条件

時間関係の実例と実装参照箇所を比較した結果、`project_identifiers`は現行claim専用のまま維持します。旧identifierとnon-production identifierは別保存し、通常のruntime、Trust、Admission、split、source syncへ暗黙に入りません。詳しい比較は[時間関係の設計メモ](temporal-project-relations.md#保存形の比較)に記録します。

採用理由も`project_identifiers`のJSON列には置かず、claim keyを参照する独立した履歴として保存する案を採ります。現行identifierを旧identifierへ移しても、過去にoperator、bootstrap、trust policyのどの理由で採用されていたかを同じ場所に残すためです。`project_decisions`には対象roleを追加し、判定cacheは削除・再生成できる場所に置きます。

論理的な保存責務は次の通りです。物理table名、column名、foreign keyはtemp DB prototype後に確定します。

| 保存責務 | 含むもの | 含まないもの |
| --- | --- | --- |
| 現行identifier | 現在の`project key + kind + value + role`、candidate/confirmed、primary | 旧、non-production、観測、noise |
| 非現行identifier | 同じprojectへの肯定的な関連、former/non-production、その判断理由 | 現行構成要素、noise |
| 採用理由 | operator/bootstrap/trust policy、登録起点、source参照、有効・終了・review状態 | identifierの時間状態、置き換え先 |
| 時間関係 | 同じ資源の旧→新、別project間の前身→後継 | 採用理由、raw redirect |
| decision | block/defer/confirm等の手動判断と対象role | collector観測、Trust判定cache |
| 観測・relation | record、redirect、直接関係とprovenance | 帰属や移転の確定結果 |

prototypeでは少なくとも次を確認します。

- 旧identifierを通常検索から現在のprojectへ誘導できるか
- non-production identifierとnoiseを混同せず、どちらもsyncによる自動復活を抑止できるか
- project改名・後継と、同一resourceのrename/transferを区別できるか
- 時点の異なる採用理由とblock/deferを正しい順序で再現できるか
- merge後も、どのprojectでどの理由により採用されていたかを監査できるか
- 信頼判定cacheを消しても、手動判断、登録起点、時間関係が残るか

temp DBのmutation、正本DBのread-only移行投影、主要事例のsample reviewは[Identifier保存方式のshadow比較](identifier-storage-shadow-comparison.md)まで完了しました。8件の固定caseはすべて成功し、confirmed構成要素10,187件のうち10,186件は既存データから採用理由を補完できます。Waverly Socialの`https://mywaverly.com`を含む移行review対象108 claimは、[Identifier採用理由の移行review](identifier-adoption-review.md)ですべて処遇を確定しました。production schemaと正本文書は未変更であり、次は合意済みのWebsite・service分離を含む実装計画とtemp DB migrationで影響を確認します。
