# collector relation policyのshadow比較

> 作業用・比較用文書です。2026-08-20時点の正本DBをread-onlyで評価した結果と、そのreview後に実装・適用した変更を記録します。

## 結論

GitHub repository metadataのhomepageは、repository管理者がそのURLをrepositoryのWebsiteとして直接指定した観測です。repositoryとURLの関係を示す根拠としては利用価値がありますが、そのURLがservice surfaceであることまでは示しません。また、repository自体が現在のprojectへ正しく帰属しているかは別の判断です。

初回比較ではこの三つを一つのservice relationとして評価したため、GitHub homepage由来の大部分を`NEEDS_ASSESSMENT`とし、「候補範囲が広すぎる」と過度に否定的な結論を出していました。三つの判断を分離すると、confirmed source repositoryから未confirmed URLへ向かう257 claimのうち、公開URLとして不正な2件を除く255件はrepository metadataによる直接参照として成立します。service以外の用途を静的に区別できる63件を除く194件は、現在のconfirmed repositoryをproject構成要素として受け入れる限り、service候補として収集・probeする価値があります。

一方、repositoryの採用理由を現在の保存履歴から追跡すると、257件中251件は`legacy_manual_seed_migration`だけを持ちます。これは251件の帰属が疑わしいことを意味せず、schema version 3移行前の採用文脈が細かく残っていないことを示します。追跡可能な採用理由をcandidate生成の必須条件にすると190件の追加reviewが発生するため、現時点ではproduction gateの根拠にしません。採用由来は警告と層化sampleの優先度に利用し、実例から誤帰属率と手間を確認してから扱いを決めます。

したがって、GitHub homepageについて現時点で支持できる方針は、「直接参照として保持する」「target roleを別に判定する」「candidate生成とauto-confirmを分ける」です。GitHub homepage単独でserviceをconfirmedにはしませんが、現在のconfirmed repositoryから得たURLを調査candidateにする処理まで一律に止める根拠もありません。

保存上は、GitHub homepageを`source_repository`ではなく、repo→URLの`repository_homepage`という専用predicateで保持します。このrelationの`confirmed`は「GitHub metadataでそのhomepage指定を直接観測した」という事実の確定であり、target URLがserviceであることやproject identifierとしてconfirmedであることを意味しません。`repo_links_service` trust edgeもhomepageだけからは作らず、service候補、Website候補、docs等への投影を後段policyへ分けます。

## 分離した三つの判断

### 1. repositoryのproject帰属

repositoryが現在のprojectでconfirmed `source_repository`であることと、その採用理由を読みます。比較上は次を区別します。

- operator review、operator add-seed、信頼policyによる追跡可能な採用
- schema version 3移行時の`legacy_manual_seed_migration`だけを持つ採用
- activeな採用文脈が保存されていないもの

`legacy_manual_seed_migration`は負の根拠ではありません。移行以前の判断を再現できないため、必要に応じてreview sampleを優先するための区分です。

### 2. repositoryからURLへの直接参照

GitHub homepage fieldはrepository管理者が設定するrepository metadataなので、公開URLとして妥当ならrepo→URLの方向付き直接参照として扱います。この関係は、URLがserviceであることや、repositoryと現在のprojectが同一であることを同時には保証しません。

READMEの一般URLは同じ扱いにしません。現行保存にはlink textや周辺sectionがないため、意味を再構築できないものは`NOT_RECONSTRUCTIBLE`のままです。将来README linkをcandidate admissionへ利用する場合だけ、boundedな表示文脈を収集します。

### 3. target URLの役割

URLがservice surface、代表Website、documentation、package、funding、account/profile、別repositoryのどれかを別に判定します。代表Websiteは`projects.website_url`、service identifierは`project_identifiers`の`service` roleであり、現行保存契約でも別物です。

既存のservice probeでservice surfaceを観測済みならその証拠を利用します。現在の代表Websiteとの一致は現行表示上の役割を示しますが、旧処理によって設定された可能性があるため、独立したproject帰属証拠とは数えません。用途不明のURLはcandidateとしてprobeでき、probe後もserviceでなければWebsiteやdocs等の補助情報として扱えます。

## 比較profile

candidate admissionについて二つのprofileを比較します。

- current-membership: 現在confirmed `source_repository`であることをproject帰属の前提として使う。静的にservice以外と分かるURLを除き、残りをcandidateとしてprobeする
- traceable-attribution: operatorまたは信頼policyまで採用理由を追跡できるrepositoryだけを起点にする。legacy migrationだけのrepositoryはreviewへ送る

どちらもcandidate生成の比較であり、auto-confirm基準ではありません。auto-confirmには後段の静的な信頼policyによる往路と復路が必要です。名前一致は二profileの入力にせず、sample reviewの抽出だけに利用します。

service→repository方向では、リンク先がrepository URLであり、表示文言が`Source code`や`Repository`のようにsource repositoryを明示する場合、直接参照としてcandidate admissionへ利用します。実データにある`repo ↗`の`↗`は外部リンクを示す表示上の装飾で、規則上は`repo`として扱います。provider iconだけのリンク、保存文脈のない旧リンク、funding phrase等は別に扱います。

## 実装と再現方法

read-only補助commandを使用します。

```sh
bin/project-registry compare-collector-relation-policy \
  --db-path data/survey.db \
  --output .tmp/collector-relation-policy-v2-storage-20260820.json \
  --example-limit 10000
```

SQLiteは`mode=ro`と`PRAGMA query_only=ON`の単一read transactionで開きます。temp DB testでは実行前後のDB byte列が一致することを確認します。reportのcontent hashは`2fe42e5161c4f70f5652976bc0c6b1c7c58ec5518b55421eaf0d9274c5902eac`です。

固定case 12件はすべて成功しました。明示的source link、外部リンク記号を伴う表示文言、GitHub Sponsorsの`open source`誤認、provider icon、保存文脈欠落、deep path、provider root、service homepage、相互参照、docs、funding、package pageを含みます。三軸分離については、profile URLをserviceへしないこと、legacy採用と追跡可能な採用でcandidate profileが分かれること、代表Websiteをserviceへ読み替えないことをunit testで確認しました。production接続後の全unit testは648件に成功しました。

## 入力の全体像

| 入力 | 件数 |
| --- | ---: |
| project | 4,528 |
| `source_repository` observation | 9,246 |
| service→repo first-party link | 621 |
| repo→URL GitHub homepage | 423 |
| repo→URL README link | 8,202 |

README linkは6,078 rowで保存文脈が不足しています。これらを一括でLLMへ渡す設計にはせず、今回の三軸比較はGitHub homepageを中心に行います。

## GitHub homepageの三軸内訳

confirmed repositoryから未confirmed URLへ向かう257 claimを評価しました。

### repositoryのproject帰属

| 保存済みの採用文脈 | 件数 |
| --- | ---: |
| operatorまたは信頼policyまで追跡可能 | 4 |
| `legacy_manual_seed_migration`のみ | 251 |
| active adoption contextなし | 2 |

この表は正誤の内訳ではありません。traceable-attribution profileをそのまま採用すると、静的にservice以外と分かる63件を除いた194件のうち、4件だけをcandidate化し、190件を追加reviewへ送ります。現状の保存契約に対しては費用が大きく、得られる誤採用削減量はまだ測れていません。

### repositoryからURLへの直接参照

| 判定 | 件数 |
| --- | ---: |
| GitHub homepage metadataによる直接参照 | 255 |
| 公開URLとして不正 | 2 |

ここで成立するのはrepo→URLの参照です。URLのservice roleやprojectへの自動採用ではありません。

### target URLの役割

| target role | 件数 | service candidateでの扱い |
| --- | ---: | --- |
| 観測済みservice surface | 32 | candidateとして継続 |
| 現在のservice candidate | 18 | candidateとして継続 |
| 現在の代表Website | 8 | Websiteとして保持し、service roleはprobeで判断 |
| 用途不明 | 136 | candidateとしてprobe |
| account/profile | 12 | service candidateから除外 |
| documentation | 9 | service candidateから除外し、補助情報として保持可能 |
| package/distribution | 17 | service candidateから除外し、補助情報として保持可能 |
| funding | 7 | service candidateから除外し、補助情報として保持可能 |
| repository | 16 | service candidateから除外し、repository関係として扱う |
| 不正URL | 2 | 除外 |

current-membership profileでは194件をcandidateとして残し、63件をservice candidateから除外します。traceable-attribution profileでは4件だけをcandidateとして残し、190件を追加review、63件を除外します。

project名とrepository名の単純な一致・包含は214件、不一致は43件でした。名前は帰属根拠にせず、43件を誤帰属、共有repo、別名、時間関係が混ざるreview sampleとして優先するためだけに使います。不一致43件のうち13件はBluesky projectへ第三者repositoryがまとめて採用されていた一群であり、少数の既知の誤帰属が集計を歪めていたことも確認しました。

## sample review

- atrrrのGitHub homepageは同じrepositoryから生成されたdocumentationを指します。repo→URLの帰属は明確ですがserviceではありません。
- skywatchedのhomepageはREADMEでもlive demoと明記され、同じprojectのserviceとして自然です。
- atfield-kitのhomepageは同名のCloudflare Pages deploymentを指し、同じprojectの公開surfaceとして自然です。
- indigoのhomepageは公式`atproto.com`を指します。repoとの関連は明確ですが、indigo固有のserviceではなくAT Protocol全体のWebsiteです。
- AppViewLiteのhomepageは作者のBluesky profileを指します。homepage設定自体はrepositoryに対して自然であり、問題はAppViewLite repositoryがBluesky projectへ誤帰属していたことです。
- Bluesky Comments、ToolShed、idiolect、Lexicon Community等は現在の代表Websiteとhomepageが一致しますが、serviceであるかはそれぞれ別に判断する必要があります。
- Nomareのcurrent repositoryと旧Datesky repositoryが同じURLを指す事例は時間関係を含むため、この比較だけでcurrent/formerを決めません。

これらから、homepageの信頼性、repositoryのproject帰属、target roleを一つの正誤へ潰すと、正しいdocumentationやWebsiteまで誤りとして数えることが分かります。

## 保存契約のshadow投影

現行DBにはGitHub homepage由来の`source_repository` rowが423件あります。endpointが同一のclaimへ正規化すると421件で、うち2件は同じclaimにcandidateとconfirmedのrowが重複しています。公開URLとして不正な2 claimを除く419件は、`repository_homepage`という直接観測relationへ投影できます。

| shadow上の処遇 | 件数 |
| --- | ---: |
| 現行`source_repository` row | 423 |
| endpoint単位のdistinct claim | 421 |
| `repository_homepage`としてconfirmedにできる直接観測 | 419 |
| 不正URLのためrun observationだけに残す | 2 |
| 重複している現行row | 2 |

これはSQL table追加を必要としません。`project_relations`のpredicateとpolicyを分ければ表現できます。上表はreviewに使った2026-08-20時点のsnapshotであり、その後の正本DBでは旧rowが1件増えています。

## production接続と複製DB検証

review後、collectorと保存処理を次の契約へ変更しました。

- GitHub homepageは公開URLとして妥当ならconfirmed `repository_homepage` relationとして保存する
- account/profile、docs、package、funding、別repository、非公開・不正URLをservice candidateへ投影しない
- homepage単独では`repo_links_service` trust edgeを作らない
- confirmed service→repositoryの明示的source relationと、repository→同一exact URLのhomepage観測が揃った場合だけ、往路・復路の意味を持つ`repository_homepage_pair` edgeを作る
- repositoryを正常に再取得した時は、そのrepositoryの旧homepage relationとそれに由来するedgeを置換する。取得失敗やsnapshotのない限定検査では消さない
- 旧`source_repository/github_homepage` relationは一度だけ通常の`repair-db`正規化で専用predicateへ移した。正本DB適用後は通常load/save経路の移行処理を除去し、過去investigation runのreplay入口だけで旧EvidenceBundleを新形式へ読み替える

2026-08-20の正本DBからSQLite online backupで作った複製へ適用した結果、旧424 rowは、重複2 rowと公開URLとして不正な2 claimを除いてconfirmed `repository_homepage` 420 claimになりました。旧`github_homepage` trust edgeは785本から0本になり、exactな相互参照が揃う`repository_homepage_pair` edge 80本を77 projectで再構成しました。旧methodを含む保存済み評価は0件、foreign key違反は0件、`integrity_check`は`ok`です。

2026-08-21にWeb serverを停止し、`survey.before-repository-homepage-20260821.db`を作成してから同じ移行を正本DBへ適用しました。正本でもrelation、edge、評価、foreign key、integrityの結果は複製DBと一致しました。適用後は通常経路の一時移行処理を削除し、過去runのnetwork-free replayだけに旧EvidenceBundle互換を隔離しました。

専用predicateへ分ける理由は、同じ一つの観測から次の異なる用途を直結させないためです。

- `repository_homepage`: repositoryがそのURLをWebsite欄で直接示している事実
- service candidate: target roleがserviceであり得るURLをprobeするための投影
- Website candidate: 人間向けの代表Website欄へ載せる候補
- trust edge: targetがservice surfaceであり、project帰属にも使えると後段で判定された場合だけ作る関係

## Website欄へのshadow投影

confirmed source repositoryから得たhomepageを、service identifierとは別にWebsite候補として評価しました。これは代表Websiteを自動更新する処理ではありません。

| 処遇 | claim | project | 意味 |
| --- | ---: | ---: | --- |
| Website空欄・単一候補 | 182 | 182 | review候補を一つ提示できる |
| Website空欄・複数候補 | 9 | 4 | repositoryごとのdocs/deployment等が混在し、自動選択しない |
| 既存Websiteと一致 | 140 | 140 | 現行値を維持できるが、旧処理由来の可能性があり独立証拠にはしない |
| 既存Websiteと異なる | 26 | 24 | 上書きせず、URL表記差、別surface、旧URL、移転をreviewする |
| Website候補にしない | 37 | 22 | account/profile、funding、別repository、不正URL |

空欄・単一候補182件の内訳は、観測済みservice surface 27件、既存service candidate 15件、documentation 4件、package 17件、未分類119件です。単一であることは代表Websiteとして正しいことを意味しないため、一括自動設定は提案しません。候補表示や層化reviewの入力には利用できます。

既存Websiteと異なる26件には、`www`やHTTP/HTTPSの表記差、Arabicaのalpha surfaceと正式Website、Nomareと旧Datesky、repository固有のdocs/API、同一project内の別toolなどが混在します。homepage policyだけで一つへ畳まず、URL identity、surface role、時間関係をそれぞれの設計へ渡します。

## GitHub Searchの既存project取り込み

`sync-github-search`には、まだ登録されていないrepositoryを検索結果から既存projectへ取り込む経路があります。現行処理はrepository URLが既存identifierと完全一致すればそのprojectを使い、そうでなければGitHub homepageとservice URLの完全一致を探します。`homepage_exact`は自動attachし、同じ登録可能domainに属するだけの`homepage_family`はsuggestionへ留めます。自動attachされたrepositoryは単なるcandidateではなく、登録起点を持つconfirmed `source_repository`になります。

このうち問題になるのは`homepage_exact`です。repo→URLのhomepage観測はrepository管理者による一方向の主張なので、URLが同じというだけでは、そのrepositoryを既存projectの構成要素と確定するには足りません。また、現行lookupはconfirmed serviceだけでなくcandidate serviceも一致先にし、同じ優先度の一致先が複数あってもproject key順の先頭を選びます。

正本DBの420 homepage claimを、各repositoryが未登録の状態で現在のidentifierへ再照合するcounterfactual comparisonでは次の結果になりました。過去のsync eventそのものの再生ではありません。

| 現在の再照合結果 | claim | shadowでの処遇 |
| --- | ---: | --- |
| exact一致なし | 239 | unmatched。うち37件は`homepage_family` suggestionを提示可能 |
| 一意なconfirmed service＋逆向きのconfirmed `source_repository` | 22 | auto-attach可能 |
| 一意なconfirmed serviceだがhomepage一方向だけ | 131 | review suggestion |
| 一意なcandidate service | 24 | review suggestion |
| 同順位のcandidate serviceが複数projectにある | 4 | ambiguous review |

変更前の規則ならexact一致181件をすべて自動attach対象にします。採用した規則では22件だけを自動attach可能とし、159件をreview suggestionへ落とします。現在のrelation保存projectと変更前lookupが選ぶprojectが異なるclaimも9件ありました。これには重複project、後で分離したproject、誤帰属候補が混在するため、9件をそのまま過去の誤attach件数とは数えません。

22件にはHangar、Zeitgeist、Dandelion、AgentScan、Spark Social等が含まれ、confirmed service→repoの直接参照とrepo→同一service URLのhomepage観測が揃っています。逆に候補一致にはLexicon Communityと`lexicon.community`、複数一致にはOpnShelf、Postgame、Improがあり、candidateやproject key順をproject選択の根拠にしない必要性が見えます。

過去のGitHub Search結果にはrepositoryごとの`match_kind`が保存されておらず、schema version 3移行前のseed由来も`legacy_manual_seed_migration`へ平坦化されています。そのため、過去に`homepage_exact`で実際に何件attachしたかや、現在の9件がその結果かは復元できません。今回の比較は現在の観測とidentifierに同じ規則を再適用した影響見積もりです。

この比較から採用した一般則は次の通りです。`homepage_exact`自体を共通規則へ昇格したのではなく、identifier一致はproject候補の照合、relation評価は共通policy、attachは用途別の操作として分けます。

1. repository URL自体が既存confirmed `source_repository`と一致する場合は、既存projectを継続して使う。
2. homepage完全一致は、一致先が一意なconfirmed serviceであり、同じserviceから同じrepositoryへのconfirmed `source_repository`が保存されている場合だけ自動attach可能とする。
3. 一方向のhomepage一致、candidate/non-current serviceへの一致、複数projectへの一致は自動attachせず、根拠を添えたreview suggestionにする。
4. `homepage_family`は現行通りsuggestionだけに使い、自動attachへ昇格しない。

ここで使う二方向のrelationは独立した証拠である必要はありません。目的は証拠数を稼ぐことではなく、一方の管理者だけが任意の相手を指した騙りを避けることです。`repository_homepage`とconfirmed `source_repository`が意味上の両方向をそれぞれ保証していれば足ります。

固定caseはhomepage attachとrelation assessorのroutingを加えた22件を確認します。2026-08-24の正本DB read-only reportは`.tmp/collector-relation-policy-v4-assessor-inventory-20260824.json`、content hashは`7451ca5b3ae374047d434b6e8a580e25c0479017e012ba1f2bd098ab7ede6733`です。production接続後もcomparator自体はDBを変更しません。

review後、GitHub Searchを次の責務分離でproductionへ接続しました。

- collectorはGitHub Website欄をconfirmed `repository_homepage`観測として作る
- identifier lookupは一致する既存project候補と、その一致がconfirmed、candidate、former、non-production、複数一致のどれかを返す
- 既存の共通信頼policyが、confirmed service→repoとrepo→同じserviceの両方向を評価する
- 共通policyが支持した場合だけ`trust_policy` adoptionとしてrepoをconfirmし、GitHub Search用manual seedや登録起点は作らない
- 支持しない一致は既存projectを変更せず、`suggested_project_keys`と理由を返す

これにより、collector固有なのはGitHub metadataの観測と候補照合までになり、帰属・confirm判断はrepository調査等と同じ信頼policyを使います。正本DBへの書込みや既存identifierの再分類は行っていません。

## 現行relation assessmentの監査

現行実装には、専用relation assessorを作る以前から、project-level triageの`repository_link_assessments`が存在します。candidate `service_links_repo` edgeをtriage入力へ混ぜ、LLMが`same_project`を返すとconfirmedかつauto-confirm利用可能な`llm_same_project` edgeへ昇格します。この判定はprojectのcategoryや説明文と同じrequestで行われ、保存入力は主にservice側のlink text、`aria-label`、title、page title、page descriptionです。candidate repositoryのbounded materialは通常confirm前に収集しないため、判定時の両endpoint比較は保証されません。

正本DBの現在snapshotでは、保存済みproposal 194組に対して`same_project` 177、`related` 14、`uncertain` 1、出力欠落2でした。現在proposalから外れた保存済みassessmentも38組あります。これは過去runの出力と現在のcandidate edgeが別の更新周期を持つためで、現行出力をそのまま固定corpusの正解にはできません。

一つの方向付き`service_url`→`repo_url` proposalとしてroutingし直すと、次の分布になります。

| routing | 組 | 意味 |
| --- | ---: | --- |
| `POLICY_DECIDED` | 63 | 明示的source link等の決定規則4、非repository URLの除外1、保存済みの直接相互relation 58。LLM判定は不要 |
| `NOT_EVALUATED` | 85 | subject URLにcurrentなservice構成要素roleがない。link観測は残せるが、構成要素間`source_repository`判定へは入れない |
| `NEEDS_PROBE` | 30 | service roleとlink観測はあるが、bounded repository materialがない |
| `ASSESS` | 16 | 現在snapshotにはservice観測とbounded repository materialがあるため、専用corpus候補にできる |

relationの意味判定ではconfirmed subjectだけに限定しません。candidate service→confirmed repoという関係も、candidate serviceの信頼を逆方向から評価する材料になり得るためです。ただしcandidate serviceから新しいrepo candidateを生成することとは別で、candidate admissionは引き続きconfirmed構成要素からの許可済み直接relationに限定します。ここで必要なのはconfirmed/candidateの区別ではなく、subjectにserviceという構成要素roleの主張があることです。

現行の`same_project` 177組のうち、73組は現在subjectにservice roleがなく、45組は保存されたtriage入力にbounded target materialがありません。理由文にも、保存入力にないREADME、Dockerfile、同一owner、repository descriptionを根拠として挙げた例があります。また63組では`llm_same_project`が同じpairの唯一のforward trust edgeで、少なくとも一方のidentifierに`trust_policy` adoptionがあります。この63組は別経路のpathを持つ可能性があるため即座に63 identifierがunsupportedになるという意味ではありませんが、既存edgeを一括削除せず、専用policyで再評価してからcleanupする必要があります。

この監査から、専用relation assessorは次の契約に分けるのが妥当です。

1. collector固有処理は、一つのsourceで観測したlink表示とbounded endpoint snapshotを作る。
2. 共通routingはsubject/objectのrole、predicate、決定規則、既存の直接relationを確認し、`POLICY_DECIDED`、`NOT_EVALUATED`、`NEEDS_PROBE`、`ASSESS`へ分ける。
3. semantic assessorは`ASSESS`になった一つの方向付きpredicateだけを評価し、`SUPPORTED`、`NOT_SUPPORTED`、`INSUFFICIENT`と入力内の根拠位置を返す。
4. `related`は提案された`source_repository` predicateを支持する中間verdictにはせず、このpredicateについては`NOT_SUPPORTED`とする。関連情報として残す必要があれば別の観測またはpredicateで扱う。
5. assessorは新しいURL、逆方向relation、candidate、confirmを生成しない。採用と信頼経路は後段の静的policyが決める。

現在の`same_project`という質問も広すぎます。同じ登録project内に収まるrepositoryでも、docs source、schemaだけのrepository、shared implementation、monorepo内componentなどがあり、serviceのsource repositoryというpredicateを常に支持するわけではありません。専用評価では「このservice surfaceからこのrepositoryへの観測は、このrepositoryを当該serviceの`source_repository`として直接参照しているか」のようにpredicateを固定します。

`ASSESS` 16組は現在snapshotから固定corpus候補を作れます。`NEEDS_PROBE` 30組は、candidate repository全体を通常enrichへ入れず、metadata、homepage、READMEの関連箇所だけをbounded probeとして取得した後に同じcorpus形式へ追加します。現在materialがある16組も、以前のLLM採用後にrepositoryがconfirmされて通常解析された可能性があるため、当時の判断が両endpoint材料に基づいていたとは扱いません。

## homepage以外にも使う分離規則

homepageの調査で得た一般則は、collector relation全体でも次の順序を維持することです。

1. collectorが外部sourceで直接観測した方向付き事実を保持する。
2. その事実が表すpredicateを、target roleやproject帰属と混ぜずに確定する。
3. service、Website、docs、repository等の候補へ用途別policyで投影する。
4. identifierのconfirmとtrust edgeは、必要な逆方向または別経路の支持を後段で評価する。

GitHub homepageはこの分離を具体化する最初の代表例です。合意後は、service pageのrepository link、README link、GitHub searchの`homepage_exact` attachへ同じ考え方を適用し、構成要素間関係判定全体へ戻ります。なお`homepage_exact`による既存projectへのrepository自動attachはrepo→URLの一方向自己申告をproject選択にも使う別経路であり、このshadow変更には含めません。

## reviewで合意したこと

1. GitHub homepageは公開URLとして妥当なら、confirmedな直接観測relation `repository_homepage`として保持し、homepage由来の`source_repository`とtrust edgeを置き換える。
2. account/profile、docs、package、funding、別repositoryはservice candidateから除外するが、Websiteや補助情報としての利用を別に判断する。
3. 残る194件はcurrent confirmed repositoryを起点にservice candidateとしてprobeし、traceable adoptionの欠落はproduction gateではなくreview warningとして扱う。
4. Website空欄・単一候補182件は自動設定せず、将来の候補表示または層化reviewへ回す。既存値との差分26件と複数候補4 projectも自動上書きしない。
5. candidate生成では逆方向の支持を必須にせず、auto-confirmでのみ静的trust policyの往路と復路を要求する。
6. relation assessorの固定corpusは、名前不一致、公式の広域Website、個人・organization site、shared repo、deployment、time caseを含む層化sampleから作る。名前一致自体はpolicyへ使わない。

上記1〜5とGitHub Searchの責務分離はproduction codeへ反映しました。次はservice→repoの曖昧な表示、README、複数sourceを使うrelation assessor固定corpusです。Website候補のreview UIとDB schema変更は実施していません。

## 専用relation assessorのshadow契約

現行の`same_project`を置き換える候補として、`source_repository`だけを評価するread-only assessorを追加しました。入力は一つの`service_url`→`repo_url` proposal、service側のlink観測、repository側のbounded materialに限定します。project名、既存profile、現在のLLM assessmentは入力に含めません。これらを含めると、既に同じprojectへ登録されていることを根拠に同じprojectだと答える循環が生じるためです。

出力は`SUPPORTED`、`NOT_SUPPORTED`、`INSUFFICIENT`、短い理由、入力内の根拠IDだけです。根拠は機械的に真偽判定できる事実に限らず、link label、周辺文、README等の位置を特定できる限定材料を含みます。機械検査するのは回答が提示済み材料を参照したことまでで、その意味解釈はLLMが行います。`SUPPORTED`の根拠条件は、service側とrepository側の両方を要求する`both_endpoints`と、一方の直接的な材料で十分と判断することを許す`bounded_any`を比較し、まだ確定しません。`NOT_SUPPORTED`はdocs、dependency、developer site等の別relationを示す根拠を必要とします。名前やownerが似ている、provider iconがある、repositoryのWebsiteがservice URLと一致するだけなら`INSUFFICIENT`です。

この契約が判断するのは方向付きrelationだけです。shared implementationが複数serviceの`source_repository`であることは許容し、project mergeは導きません。逆方向relation、candidate admission、identifier confirmも生成しません。routing、relation評価、採用、信頼判定を別段階として維持します。

固定corpus v1は12件で、期待値を`SUPPORTED` 4、`NOT_SUPPORTED` 4、`INSUFFICIENT` 4に分けました。明示的source link、bug/contribution link、shared implementation、複数projectを含むrepository内のdeep locator、docs source、dependency、developer site、provider iconとWebsiteだけの組合せ、名前・owner類似、target material欠落、公式docs、改名・後継を含みます。これは設計境界を固定するfixtureであり、正本DBの個々のrelationを正解として転記したものではありません。

実行commandは次です。

```sh
bin/project-registry evaluate-relation-assessment-corpus \
  --corpus tests/fixtures/project_registry/relation-assessor-cases-v1.json \
  --support-evidence-mode both_endpoints \
  --output .tmp/relation-assessor-v1.json
```

reportはJSON schema、根拠ID、選択した`SUPPORTED`根拠条件を検査し、期待値との一致と、実際の旧判断が付いたcaseではその一致を別々に集計します。設計fixture 12件には架空の旧判断を置いていないため、旧方式との比較母数は0です。現在のshellにはLLM commandまたはendpointが設定されておらず、実model比較はまだ実行していません。productionのtriage prompt、`llm_same_project` edge、DBは変更していません。

正本DBの現在snapshotから`ASSESS`へroutingされた16組を限定材料corpusへread-onlyで抽出するcommandも追加しました。

```sh
bin/project-registry export-relation-assessment-corpus \
  --db-path data/survey.db \
  --output .tmp/relation-assessor-current-20260824.json
```

16組の現行判断はすべて`same_project`です。service側根拠は9組が1件、7組が2件あります。repository側の位置付き抜粋は13組に1〜6件ありますが、3組は0件でした。該当するのはSovereign Comms、Glean、gamesgamesgamesgames.gamesのpairです。materialが保存されていることと、relation assessorへ渡せるraw excerptがあることは同義ではありません。`both_endpoints`ではこの3組を内容にかかわらず支持できず、`bounded_any`ではservice側材料だけで支持するかをmodelが判断できます。この差は、精度だけでなく追加probeと人力reviewの量として比較します。出力hashは`f22166cd43a82182f11668db2ead2d6264a6c9290c624e2f0421ed450993d1f0`です。

次の比較では、`both_endpoints`と`bounded_any`を同じmodel・corpus・repeatで実行し、期待値一致だけでなく`INSUFFICIENT`の増減、根拠外参照、所要時間、人力reviewへ残る量を確認します。別relationを提案された`source_repository`について`NOT_SUPPORTED`と扱うことと、時間関係をこのpredicateだけで決めず`INSUFFICIENT`へ残すことは合意済みです。実model比較後に、正本DBの`ASSESS` 16組と追加probe対象へ採用する入力契約を決めます。
