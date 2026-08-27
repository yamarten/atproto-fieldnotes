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

このroutingは旧assessorの処理量を監査する分類です。`direct_relations_already_support_pair` 58件は逆向きの保存relationもあるため旧routingが評価不要にした件数であり、往路のcandidate relationをconfirmedへ格上げできる件数ではありません。逆向きrelationは別方向の事実として保持し、往路relationの意味を変える根拠には使わない方針へ修正しました。

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

出力は`SUPPORTED`、`NOT_SUPPORTED`、`INSUFFICIENT`、短い理由、入力内の根拠IDだけです。根拠は機械的に真偽判定できる事実に限らず、link label、周辺文、README等の位置を特定できる限定材料を含みます。機械検査するのは回答が提示済み材料を参照したことまでで、その意味解釈はLLMが行います。`SUPPORTED`の根拠条件は、service側とrepository側の両方を要求する`both_endpoints`と、どちらか一方にexact predicateを直接述べる材料があればよい`direct_any`を比較しました。`NOT_SUPPORTED`はdocs、dependency、developer site等の別relationを示す根拠を必要とします。名前やownerが似ている、provider iconがある、repositoryのWebsiteがservice URLと一致するだけなら`INSUFFICIENT`です。

この契約が判断するのは方向付きrelationだけです。shared implementationが複数serviceの`source_repository`であることは許容し、project mergeは導きません。逆方向relation、candidate admission、identifier confirmも生成しません。routing、relation評価、採用、信頼判定を別段階として維持します。

固定corpus v1は12件で、期待値を`SUPPORTED` 4、`NOT_SUPPORTED` 4、`INSUFFICIENT` 4に分けました。明示的source link、bug/contribution link、shared implementation、複数projectを含むrepository内のdeep locator、docs source、dependency、developer site、provider iconとWebsiteだけの組合せ、名前・owner類似、generic provider linkと実装code、target material欠落、公式docsを含みます。時間関係は意味判定へ送らずscope外として入力時に除外します。これは設計境界を固定するfixtureであり、正本DBの個々のrelationを正解として転記したものではありません。

実行commandは次です。

```sh
bin/project-registry evaluate-relation-assessment-corpus \
  --corpus tests/fixtures/project_registry/relation-assessor-cases-v1.json \
  --support-evidence-mode direct_any \
  --output .tmp/relation-assessor-v1.json
```

reportはJSON schema、根拠ID、選択した`SUPPORTED`根拠条件を検査し、期待値との一致と、実際の旧判断が付いたcaseではその一致を別々に集計します。設計fixture 12件には架空の旧判断を置いていないため、旧方式との比較母数は0です。worktreeには`.envrc`がないため、元checkoutの`.envrc`からLM Studio設定を読み、Qwen 3.6 35B A3Bで実model比較しました。productionのtriage prompt、`llm_same_project` edge、DBは変更していません。

正本DBの現在snapshotから`ASSESS`へroutingされた16組を限定材料corpusへread-onlyで抽出するcommandも追加しました。

```sh
bin/project-registry export-relation-assessment-corpus \
  --db-path data/survey.db \
  --output .tmp/relation-assessor-current-20260824-v2.json
```

16組の現行判断はすべて`same_project`です。service側根拠は9組が1件、7組が2件あります。repository側の位置付き抜粋は13組に1〜6件ありますが、3組は0件でした。該当するのはSovereign Comms、Glean、gamesgamesgamesgames.gamesのpairです。materialが保存されていることと、relation assessorへ渡せるraw excerptがあることは同義ではありません。service側の観測にはexact hrefを明示するよう入力契約を修正しました。corpus hashは`c1d8d687f8bed3867e52a41d24c9d7f8369b444d64749a52ceab7644873c8c53`です。

初期promptでは固定12件の期待値一致が`both_endpoints` 7/12、片側許容8/12でした。exact href、根拠不足と否定の区別、dependency、docs、shared implementation、時間関係を整理すると、`direct_any` v3は12件×3回で36/36でした。generic provider linkと実装codeを追加した後は、時間関係をscope外へ戻し、判定順序を明示したv5が35/36、唯一揺れたdeveloper siteの根拠文を明確化したtargeted 5回は5/5でした。固定corpusだけを見る限り、両endpoint必須よりdirect assertionを要求する方が良好です。

一方、正本DBの実16組をv5で一回評価すると`SUPPORTED` 3、`INSUFFICIENT` 11、`NOT_SUPPORTED` 2で、現行`same_project`との一致は3/16でした。さらに支持3件のうちOzoneの`LICENSE-APACHE`とBridgeBeats docsの`LICENSE`はliteralなsource assertionではなく、誤支持です。Ozoneを追加3回評価しても2回支持・1回根拠不足に揺れました。完全reportは`.tmp/relation-assessor-current-direct-v5-20260824.json`、hashは`c3f27718a90ac9044cc590ff14d487c636a4f1e0d0692fa2d1c864f9331e30a1`です。

この結果から、自由な意味判定だけに`SUPPORTED`を任せる案はまだproductionへ接続できません。また、literalな「serviceのsource code」を問うと、実際には同じprojectのrepositoryと思われるgeneric provider、license、issue tracker等のlinkを多く`INSUFFICIENT`へ残します。次の設計判断は、candidate admissionで必要なのが厳密な`source_repository`なのか、それともsource、issue tracker、license等を含む「このserviceが当該projectのrepositoryを指した」という帰属関係なのかです。後者を採る場合も、観測済みexact linkとrepository subtypeを分け、auto-confirmやmergeへ同じ強さで流用しません。

## candidate relationの格上げ比較

relation評価を「一方向のcandidate relationをconfirmedへ格上げできるか」という目的に限定して、194 proposalを再分類しました。逆向きrelation、project名、現在のLLM判断は格上げ根拠に使いません。subject URLにservice構成要素roleがある109件が評価対象で、残る85件はlink観測を保持したまま対象外とします。

初回は既存`repository_link_candidates`と同じくservice/repo pairへ表示文脈を一つだけ残していましたが、実画面sample reviewで同じpairに`Code`と`Issues`、repo root iconと`Download`のような複数linkがあることを確認しました。raw `service_verification_results`から同じpairの全表示を束ね直すと、194件中104件、評価対象109件中61件が複数表示を持ちました。逆向きrelationを混ぜる問題とは別に、一方向の複数観測をpair単位で一つへ潰さないことが必要です。

| 処遇 | 件数 | 意味 |
| --- | ---: | --- |
| `CONFIRM_SOURCE_REPOSITORY` | 17 | `Code`等の明示sourceを含む14件、self-hosting表示だけで支持する2件、Lexicons表示1件。self-hosting表示4件中2件は同じpairに明示sourceもある |
| `DO_NOT_PROMOTE` | 4 | sourceを示す別表示がなくlicenseだけのOzone、releaseだけのnpmxとatproto-backup、repositoryでないGitHub Sponsors。atproto-backupはURL case違いでrepo root linkと別pairになっており要resource identity補正 |
| `REVIEW_SOURCE_REPOSITORY` | 74 | provider名、repository名、その他の曖昧な表示を持つが、明示sourceまではない。用途の併存を含めて確認する |
| `RECOLLECT_PRESENTATION` | 14 | 無題linkを含み、明示source表示がない。アイコン、CSS、周辺配置を再取得または人が確認する |
| `NOT_EVALUATED` | 85 | subjectにservice構成要素roleがない。project帰属を推測せず観測だけ残す |

`DO_NOT_PROMOTE`はrepositoryが別projectだという否定ではありません。たとえば`Issues`や`LICENSE`は同じprojectのrepositoryを指す場合が多いものの、そのリンクが直接表すのはissue窓口やlicenseです。source以外のproject repository relationを将来導入する場合の再分類対象で、観測自体を削除しません。明確にendpointが誤っていたのはGitHub Sponsorsの1件だけでした。

collectorはCSS、アイコン画像、周辺配置を保存していないため、`link_text=GitHub`が実画面でも文字表示なのか、視覚的にはアイコンだけで支援技術向け文字だけを持つのかは完全には復元できません。アイコンだけなら高確率でproject repositoryを指すという経験則を評価するには、今後のbounded observationへ表示形態またはDOM上の限定contextを追加します。

実画面とGitHub repositoryをsample reviewすると、[at.fund](https://www.at.fund/)は`Report a bug`とは別に`aria-label=GitHub repository`のrepo root link、[Semble](https://semble.so/)は無題のGitHub/Tangled repo root linkと`Submit an Issue`、[Bounce](https://bounce.anew.social/)は同じrepoへの`Code`と`Issues`、[atproto-backup](https://atbackup.pages.dev/)は無題のrepo root linkとreleaseへの`Download`を併存していました。これらは弱い表示だけを一つ残すと誤って保留されます。一方、[npmx](https://npmx.dev/)は現在画面でrelease tagだけを直接示し、[provcheck](https://provcheck.app/)は現在画面にGitHub linkが見つからず、保存観測の鮮度も論点になります。確認したGitHub repositoryはいずれも空ではなく、at.fund、Semble、Bounce、provcheck、atproto-backup、npmxは各serviceまたはprojectの実装・文書を保持していました。

## project repositoryへの拡張とDOM context

reviewでは必要なrelationを、実装sourceに限定した`source_repository`ではなく、そのprojectが公式に利用するrepositoryと定義しました。ソースコード、docs、仕様、Lexicon、issue tracker、release、licenseの置き場を含みます。subjectもservice surfaceへ限定せず、独立した構成要素である`project_site`からの直接linkを利用します。この意味には`source_repository`という名前が合わないため、production保存契約を変更する段階で`project_repository`へ改名します。二つのpredicateへ分割はしません。

ただしrepository hostへのlinkだけでは、dependency、related project、awesome list掲載を区別できません。shadow専用のbounded HTML extractorを追加し、一つのlinkについてexact locator、canonical repository root、表示文字、`aria-label`、`title`、子画像・SVGのlabel、最寄り見出し、最寄りblockの短い本文、header/footer/nav等のlandmark、同一groupとpageのrepository数を抽出します。完全HTMLやDOMは保存しません。同じcanonical repositoryへの複数linkは一件へ上書きせず、全観測を保持してからpair verdictへ集約します。

固定16 caseでは、公式docs source、issue、release、license、Lexicon、header/footerの単独provider icon、open sourceとsiteを動かすcodeの直接説明を`CONFIRM_PROJECT_REPOSITORY`、awesome list、related project、dependencyを`DO_NOT_PROMOTE`、本文中のgeneric iconと意味不明なdeep release linkを`REVIEW_PROJECT_REPOSITORY`としました。実サイトではSembleのページに公式repoのfooter/issue linkと別projectであるZotero pluginのrelease linkが同居し、locatorの`/releases/`だけでは帰属を決められないことを確認しました。npmXのversion link、AT Backupのdownload、Bounceの`Code`と`Issues`は表示文脈を伴って公式repositoryを支持します。

current pageをboundedに再収集するread-only commandも追加しました。

```sh
bin/project-registry compare-project-repository-context \
  --db-path data/survey.db \
  --output .tmp/project-repository-context-v4-20260824.json \
  --page-limit 25 \
  --timeout 10
```

旧比較で表示再収集またはreviewだったものを優先した25 page、26 proposalでは、`CONFIRM_PROJECT_REPOSITORY` 20、`REVIEW_PROJECT_REPOSITORY` 6でした。確定候補には単独のglobal chrome repository link、issue/release/docs/license表示、service URLからrepositoryへの直接redirect、open-source blockを含みます。GitHub URLのowner/repository case違いは表示URLを変更せず同一identityとして集約します。残った6件は$ATA Protocol、Airport、Rito Labeler Kitの無題または視覚依存link、複数repoを説明するsocialweb.computer、plugin名だけを示すobsidian-pds-sync、個人portfolio内のWritten Worldでした。

視覚的に無標のlinkを追加で15 page確認しました。対象projectのrepositoryを可視文字なしで示していたのはSeams、Rito、tsky、Longpost、RelationalText、Linkatの6 pageで、6件とも公式repositoryでした。`aria-label=GitHub`や`aria-label=Tangled`を持つ場合もありますが、これは支援技術向け表示であり、画面上の可視文字とは区別します。残る9 pageの対象linkには`GitHub`、`src`、`View on GitHub`等の可視文字がありました。

一方、Poptartが遷移するpub.devには、package自身のrepositoryに加えて、可視文字のないpub.dev運営側のbug-report iconがありました。したがって、可視文字がないことだけでは採用しません。評価する仮説として、現在pageを再収集した結果について、対象がsupported repositoryで、可視文字がなく、page内の異なるrepository identityが一つだけで、list item内でもない場合を`visually_unlabeled_single_repository_link`としてshadow上の確定候補にします。複数repository、一覧、awesome/related/dependency等の外部参照文脈はreviewまたは不採用へ残します。過去保存で表示情報が欠落しているだけのrowは正の材料にせず、再収集対象のままです。

この規則を最初の26 proposalへ当てると、$ATA ProtocolとRito Labeler Kitは確定候補になり、`CONFIRM_PROJECT_REPOSITORY` 22、`REVIEW_PROJECT_REPOSITORY` 4となる見込みです。これは保存済みreportの再集計であり、25 pageを再取得した実測値ではありません。Airportの数値badge、複数repoを持つsocialweb.computer、plugin名だけのobsidian-pds-sync、個人portfolio内のWritten Worldは引き続きreviewです。

残る4種は正しいlinkと思われますが、そのためだけに静的規則を増やしません。誤りと判定したのではなく、一般化可能な直接表示が不足するためreviewへ残します。同型事例が蓄積するまではrelation assessorまたは人が扱います。

## production保存契約と移行範囲

### 推奨する意味の分離

広げたrelationは、実装sourceだけでなくdocs、仕様、Lexicon、issue tracker、release、licenseを含む公式repositoryを表します。従来の`source_repository`はrelation predicateとidentifier roleの両方でこの意味より狭いため、どちらも`project_repository`へ改名する案を推奨します。predicateだけ改名してidentifier roleと`source_code_urls`表示を残す案は、issue専用repo等をsource codeと呼ぶ不整合が残るため採りません。逆に`source_repository`を下位roleとして併存させる案も、現時点ではsource用途を分けて運用する必要がないため採りません。将来必要になれば、repositoryの用途をrelation provenanceまたは表示metadataから導出します。

保存と判断は次の段階に分けます。

1. service surfaceとproject siteのpage probeは、exact locator、canonical repository root、可視文字、accessibility label、子要素label、最寄り見出し・block、landmark、list内か、page/group内のrepository identity数をbounded observationとしてcurrent resultとrun historyへ保存する。個別docs subpage等の補助resourceも観測源にはできるが、構成要素へ採用されたこととは分ける。
2. 外部一覧、dependency、funding、provider/account root等を静的に除外し、公式repositoryらしいが意味を確定できないlinkだけをcandidate `project_repository` relationにする。直接表示で確定できるlinkはconfirmed relationにできる。
3. relationの`confirmed`は「この方向付きlinkが当該projectの公式repository関係を表す」というsemantic判断であり、repo identifier自体のconfirmedではない。relation classificationとidentifier classificationを分ける。
4. confirmed relationのsubjectがcurrent confirmed `service`または`project_site` componentなら、repoをcandidate `project_repository` identifierへ投影できる。candidate site、導出URL、個別docs subpage等の補助resourceはrelationの観測・判断材料にはできるが、candidate admissionの起点にはしない。`project_site`を信頼判定の起点にできるかは、classificationだけでなく採用理由を含む通常のanchor policyに従い、auto-confirmには登録起点までの許可された往路・復路を要求する。
5. repository側からpageを示す方向も、同じ`project_repository` predicateの反対方向として別relationにする。GitHub Website欄の`repository_homepage`は直接観測predicateのまま維持し、それだけでは`project_repository`にしない。READMEの一般linkも、bounded contextで公式project surfaceへの参照と判断できるまで`project_repository`へ昇格しない。
6. 同じ観測が意味上の両方向を保証する場合は、同じprovenanceから二本の方向付きrelationを作ってよい。ただしraw linkを機械的にinverse化せず、各方向が表す意味をpolicyで確認する。

初期production接続では静的に確定できるrelationだけをconfirmedへ昇格し、LLMによる自動昇格は接続しません。曖昧なcandidate relationは残してもidentifier candidateを生成せず、既存のrelation assessorまたは人のreview対象にします。現在の専用assessorは限定materialで`INSUFFICIENT`が多く、旧`llm_same_project`より入力契約は改善していますが、productionの自動判断器として採用できる品質はまだ示していません。

### project siteの導入境界

`project_site`は、projectを説明・提示する代表的なWeb presenceです。documentation、specification、promotion、landing pageを含み得ますが、利用・操作する`service`と、個別のdocs、support、blog、distribution page等の補助resourceは含めません。serviceを持たない仕様・documentation主体projectでも構成要素を持てるようにするためのroleであり、一般URLをすべて構成要素へ昇格する仕組みではありません。

2026-08-24の正本DBでは、`projects.website_url`が空でないprojectは612件です。URL正規化とcurrent/non-current identifier、別projectとの衝突を比較すると、596件は同じprojectのconfirmed `service`をWebsite表示へ再利用でき、1件はcandidate service、5件はnon-current service、1件は別projectのcomponentと衝突し、残る9件が独立したproject site候補でした。後者にはLexicon Community、Bluesky Comments、ToolShed、idiolect等が含まれます。この分布は、既存Website欄の大半が過去のservice初期値と重なる一方、独立したproject site候補も実在することを示します。Website値だけでは採用理由とroleを再構築できないため、612件を一括してconfirmed `project_site`へ移しません。

導入時は、同じexact URLのconfirmed serviceがWebsite表示を兼ねられるものは二重登録せず維持します。confirmed serviceを再利用できない既存Website 16件をreviewし、operator判断または許可された直接関係で帰属を確認できるものだけ`site_url` / `project_site`へ採用します。project siteの保存、表示、採用理由、serviceとの相互relationを先に実装してから、service以外を起点にする`project_repository` relationを接続します。補助resourceを例外的なtrusted anchorへする案は採りません。

read-only比較commandを追加しました。

```sh
bin/project-registry compare-project-site-migration \
  --db-path data/survey.db \
  --output .tmp/project-site-migration-shadow-v1-20260824.json \
  --example-limit 1000
```

既存Websiteの内訳は次のとおりです。

| shadow処遇 | 件数 | 初期移行での扱い |
| --- | ---: | --- |
| confirmed serviceをWebsite表示へ再利用 | 596 | `project_site`を二重作成しない |
| candidate serviceと一致 | 1 | serviceかproject siteかreview |
| non-current serviceと一致 | 5 | 旧serviceの検索・表示とcurrent Websiteを分けてreview |
| 別project componentと衝突 | 1 | 自動移行しない |
| 独立project site候補 | 9 | operatorまたは許可された帰属根拠で採用 |

Website空欄のprojectについて、confirmed repositoryの`repository_homepage`から得られるURLも別に投影しました。227 URL / 206 projectがあり、52件はaccount、funding、package、repository等でsite候補外、1件は同じprojectのconfirmed service、20件はcandidate service、4件は別project componentと衝突しました。相互のrepository参照を保存済みのものは2件、repo→URLの一方向leadは148件です。複数leadを持つprojectは6件で、最大13 URLでした。

これら227件は既存Websiteの移行対象ではありません。repository homepageは正しい直接観測でも、親framework、project群の公式site、個人site等を指せるため、一方向leadを`project_site`へ自動採用しません。相互参照2件も、起点repository自体のproject帰属がlegacy採用である可能性を残すためreviewです。初期導入を止める必須reviewは、既存Websiteの596件を除く16件に限定し、空欄へのleadは将来の候補queueとして扱います。

2026-08-25のoperator reviewでは、独立候補9件のうちBluesky Comments、clippr、idiolect、Lexicon Community、mastrojs/atproto、Tiles、ToolShed、Writer、Kyronを`project_site`としました。clipprはserviceとの境界を外部事実だけでは明確にできませんが、初期移行上は代表site側へ置きます。Kyronは同URLのcandidate serviceを外し、former serviceは履歴として維持します。desertthunder.devはトップURL自体を`service`とし、blogとTILがstandard.siteを利用していることを登録理由とするため、`project_site`へは移しません。

残る7件も同じreviewで確定しました。PopFeedの`https://popfeed.social`はPopFeed側のconfirmed serviceを維持し、PopSky側へ混入した同じservice identifierだけを外します。atweb-vue、Impact Indexer、PubLib、Skypushのformer URLとSoleiaのnon-production URLはnon-current identifierを維持したままcurrent Website欄を空にします。誤検出としてblockするのではなく、すでに保存された時間・環境上のstandingを保持します。

この16件を固定manifestとして新しいshadow DBへ投影した結果、Website 612件はcurrent Website 607件へ整理され、その全件がconfirmed service再利用598件またはconfirmed `project_site` 9件になりました。追加した`project_site`にはoperator adoptionとregistration originを保存し、Kyronのcandidate service 1件とPopSkyの衝突service 1件を除去しました。確認結果はSQLite integrity `ok`、foreign key違反0、current site/service exact URL重複0です。2026-08-25に同じmanifestを停止中の最新正本DBへ再適用して結果が一致することを確認し、production codeと正本DBへ反映しました。切替前DBは`data/backups/project-site-20260825T011520+0900/`へ退避しています。

review manifestとshadow materialization結果を含む完全reportのcontent hashは`25437d8b7d625f81bb39a6af94d1a5daa82a56f7968e89eb04e28a7312f934a8`です。SQLiteは`mode=ro`と`PRAGMA query_only=ON`で開き、正本DBを更新しません。投影先は新規shadow DBに限定します。

### 正本DBのread-only監査

project site移行後の2026-08-25正本DBに対し、identifier roleと旧relationを別々に投影するread-only comparatorを追加しました。

```sh
bin/project-registry compare-project-repository-migration \
  --db-path data/survey.db \
  --output .tmp/project-repository-migration-v1-20260825.json \
  --example-limit 20
```

role改名対象は次のとおりです。

| 保存先 | `source_repository`行 | 処遇 |
| --- | ---: | --- |
| `project_identifiers` | 1,471 | 1,238 confirmed、233 candidateをclassification維持で`project_repository`へ改名。GitHub case違いの同一identity 9組10行を統合し、1,461行になる |
| `project_identifier_adoptions` | 1,362 | roleだけ改名し、登録起点とprovenanceを維持 |
| `project_noncurrent_identifiers` | 1 | standingを維持してroleだけ改名 |
| `project_decisions` | 1,219 | target roleを改名 |

active adoptionを保存値どおりにまとめると、`trust_policy`だけを採用根拠に持つrowは[account.info](http://127.0.0.1:8888/?project=proj%3A34ec9661afca)と[AgentScan](http://127.0.0.1:8888/?project=proj%3A693d18882df9)の2件です。ただしAgentScanはGitHub URLのcase違いでoperator採用済みの同一repository rowを別に持つため、resource identityで統合するとtrust-onlyなのはaccount.infoだけです。account.infoはservice側の`open source` linkとREADME側のexact linkを持つため、新policyで再確認してから旧trust edgeを除去します。他のtrust-policy採用repoにはoperatorまたはbootstrap由来もあり、relation再構成中も構成要素として維持できます。

旧`source_repository` relation 9,219行は意味別に扱います。全行が次の5処遇へ分類でき、未分類行はありませんでした。

| 旧relation | 行数 | 新契約での処遇 |
| --- | ---: | --- |
| page→repo `first_party_link` confirmed | 634 | 一括改名しない。105行はprovider/account root等でrepository endpointではない。残る529行はcanonical化すると499 claimで、bounded DOM contextを再収集してcandidate/confirmed `project_repository`を再生成する |
| repo→URL `readme_link` candidate | 8,187 | 一般README mentionであり公式関係ではない。run historyとrepository materialを観測として維持し、旧predicate rowは`project_repository`へ移さない |
| repo→URL `readme_link` confirmed | 33 | candidate repo trust inspectionでexact URLを認めた旧判断。bounded README contextで再評価し、反対方向の`project_repository`へ移せるものだけ昇格する |
| DID→repo `pds_list_records` confirmed | 365 | Tangled accountがrepo recordを公開した直接観測であり、project帰属ではない。`tangled_repo_snapshot`と`did_links_repo` edgeを維持し、`project_repository`へ移さない |

page→repo 634行のうち、subjectが構成要素URLと完全一致するものは67行、構成要素配下のpageは26行、collectorのcurrent stateから取得ページを確認できるものはさらに539行で、現状態から取得元を再構築できないものは2行でした。この区分はrelationの強度ではなく再収集経路の有無だけを示します。valid 529行をcanonical identityへまとめた499 claimについて、current保存にはlist、周辺block、repository密度等の新しい判断材料が揃わないため、旧confirmed classificationを新predicateへそのまま引き継ぎません。

GitHubではURLの大文字小文字だけが異なる同一resourceが9 projectにあり、10行が重複していました。統合時はconfirmedをcandidateより優先し、表示URLは既存のconfirmedまたはprimary行を残します。このidentity統合は公式repositoryかどうかの再判断ではなく、同じrepositoryを二重表示・二重採用しないための保存上の正規化です。

### 実装と適用の順序

1. `site_url` / `project_site`の保存・runtime view・Web/API表示と採用理由を設計し、既存Website 612件のread-only移行比較でservice兼用、独立site候補、補助resource、要reviewを分ける。
2. role、predicate、runtime view、Web/API表示を`project_repository` / repositoryへ揃え、`source_code_urls`を正本語彙として残さない。repository analysis等、repo URL一般を入力にする処理は新しいrepository viewへ接続する。
3. production page parserをservice surfaceとproject siteの両方から利用できるbounded observationへ分け、再取得時にcandidate/confirmed `project_repository` relationを再生成する。任意最大長やLLM判断はここへ混ぜない。
4. temp DBでsite導入とrepositoryのidentifier role、adoption、decision、non-current rowの意味とprovenanceを保つことを確認する。repositoryではGitHub case違いの同一identity 9組を統合し、旧relationを上表どおり選別する。既存confirmed identifierはrelation不足を理由に降格しない。
5. 259 projectの旧first-party relationを対象にbounded再収集を行い、静的確定、candidate、除外、取得不能をreportする。取得不能は旧relationを自動支持せずreviewへ残す。
6. account.info、GitHub case違いのAgentScan等3組、固定事例、初回26 proposal、無標link追加sampleを確認し、旧`llm_same_project` edgeと旧`source_repository` relationを除去した後のtrust verdict差を比較する。
7. comparator実装、production code、DB適用を別コミットに分ける。正本DB適用はserverとwriterを止め、backup、単一transaction、foreign key、integrity、再dry-run、通常操作確認を行う。

`project_site`のproduction codeと正本DBへの導入は完了しています。`project_repository` comparatorのreview後、production code側はrole、runtime view、Web/API表示、静的policyを`project_repository`へ揃え、旧`source_code_urls`を通常runtime契約から除去しました。`source_repository_record`は引き続きrepo調査の観測記録であり、confirmed recordであってもproject repositoryのcomponent viewへ混ぜません。初期productionではLLM自動昇格を接続していません。2026-08-25に同じmaterializerを停止中の正本DBへ適用し、production codeと保存値を切り替えました。

新しいmaterializerを正本DBのread-only copyへ適用した結果、repo identifierは1,471行から1,461行になり、GitHubのcase違い9組10行を統合しました。adoption、decision、non-currentのroleと値も同じresource identityへ追従し、旧`source_repository` relation 9,219行は一括改名せず削除しました。削除内訳は`first_party_link` 634、`pds_list_records` 365、`readme_link` 8,220です。移行後のSQLite integrityは`ok`、foreign key違反は0、旧identifier/adoption/decision/non-current roleと旧predicateの残存はすべて0でした。shadow DBを現行Web UIで開き、confirmed repositoryが重複せず`Repositories` / `repository`として表示されることも確認しました。

正本DBへの最終適用ではrepository identifierはconfirmed 1,236件、candidate 225件の計1,461件になりました。旧`source_repository` relation 9,219行の削除に加え、現在値を表すdecision metadata 1,219件、profile app link 1,170件、contribution evidenceのrelation cache 1,054件を新しいrole・predicateへ正規化しました。履歴上のLLM入力・出力snapshotは観測当時の記録なので書き換えていません。foreign key違反0、SQLite integrityは`ok`で、旧identifier/adoption/decision/non-current role、旧relation predicate、現在値の旧metadata・link・cacheはすべて0件です。

最終適用reportは`.tmp/project-repository-production-v2.json`、content hashは`5efce5739bfbeeeaf9a7e18d08d66fc769c2bc7c2ff87fb86b32b04eaacde1bc`です。切替前DBは`data/backups/project-repository-20260825T022442+0900/survey.db`へ退避し、SHA-256は`ad56c6013128f4ba974798882285da507abaa2e0f1bb7c71acc1f4f213f6a2e0`です。

完全reportは`.tmp/project-repository-migration-v1-20260825.json`、content hashは`c105cb064f1bcc3e55dc029b8c494faec2bdc3ee7509c9989a75ebeb94df943e`です。SQLiteは`mode=ro`、`PRAGMA query_only=ON`、単一read transactionで開き、正本DBを更新していません。

完全reportは`.tmp/project-repository-context-v4-20260824.json`、content hashは`796eb81a7204575755cc53568e27361cadcbc85b6a0327f7be8d8f2adcbf3167`です。25 pageの逐次取得は一部応答待ちにより約12分かかったため、commandの既定page limitは10とし、以後のreportにはpage別・全体の経過時間を記録します。これは評価用commandの実行量の問題であり、relation判定結果には影響しません。

既存DBにはこの局所DOM contextがないため、過去rowへ推測で補完しません。production接続時はcurrent pageを再収集し、確定できないものだけreviewへ残します。ここまでの変更はread-only shadow module、比較command、fixtureだけであり、collector保存、relation、identifier、正本DBは変更していません。

完全reportは`.tmp/collector-relation-promotion-audit-v3-20260824.json`、content hashは`246e2d721edf36bc0ffea6f4463c0d34556cabee5446809a4bb9a010c50a7d31`です。これはread-only shadow比較であり、relation、identifier、trust edge、正本DB、production collectorを変更していません。

## 旧page relationのbounded再収集

正本DBのrole移行後も旧relationを一括改名せず再評価できるよう、移行前backupからcanonical claimを読み、現在DBの構成要素とrepository classificationを照合して公開ページだけを再取得するread-only commandを追加しました。二つのSQLite入力は`mode=ro`と`PRAGMA query_only=ON`のtransactionで開き、JSON report以外へは書き込みません。

```sh
bin/project-registry compare-project-repository-recollection \
  --legacy-db-path data/backups/project-repository-20260825T022442+0900/survey.db \
  --db-path data/survey.db \
  --output .tmp/project-repository-recollection-v7-20260826.json \
  --page-limit 1000 \
  --timeout 10 \
  --concurrency 6 \
  --retries 2
```

入力は旧first-party link 529行をresource identityでまとめた499 claim、273 page、234 projectです。現在のpage standingはconfirmed componentと完全一致66、同じcomponent配下26、component外407でした。repositoryは現在confirmed 79、candidate 22、未登録398です。

リンク表示だけを評価すると強く見えるclaimが多数ありますが、project scopedな`project_repository`へ確定するには、取得ページ自体が対象projectのconfirmed componentである必要があります。component外のTangled共通ページからTangled本体repoを指す例や、at-storeの商品ページからLeaflet repoを指す例が実際にありました。同じhostやcomponent配下だけではtenant、商品、投稿等の別projectを排除できないため、自動確定はcomponent URLそのものからの直接linkに限定します。配下docsを起点にしたい場合は、そのdocs rootを`project_site`として採用してから評価します。

2026-08-26のsnapshotでは、確定claim 46、canonical confirmed relation 46、review 333、外部参照として除外9、現在未観測104、取得失敗7でした。確定relationが支持するrepositoryは現在confirmed 26、candidate 5、未登録15です。既存identifierのclassificationは変更せず、LLM自動昇格も使っていません。公開ページの一時失敗により直前実行との間で確定45〜46、取得失敗6〜9の揺れがありましたが、新規候補15件とcomponent境界の結論は同じでした。

15件のoperator reviewでは、aTIProtoのcomponentからリンクされたskytip-simpleだけを別projectとして除外し、残る14 repositoryは対象projectのconfirmed identifierとして採用する判断になりました。skytip-simpleは`Projects implementing ...`と`feature your project here`を伴う外部実装一覧にあり、link内の`Self-hosted`だけを見てaTIProto自身のrepositoryと判定したのが原因でした。個別URLを除外せず、外部projectの一覧・募集を示す周辺文脈を自己ホスト表示より優先する一般則へ修正しました。

修正後の2026-08-27再収集ではskytip-simpleだけが規則変更によって`CONFIRM_PROJECT_REPOSITORY`から`DO_NOT_PROMOTE`へ変わりました。全体は確定43、review 334、除外10、現在未観測106、取得失敗6です。前回確定からほかに減ったHigh Fiveは取得失敗、atmo.rsvpは現在の表示変化によるreview化であり、外部一覧規則の影響ではありません。公開ページのsnapshotは変動するため、operator review済みのatmo.rsvpは自動判定結果へ戻さず、固定manifestの判断を維持します。再収集reportは`.tmp/project-repository-recollection-v8-20260827.json`、content hashは`ffd82e04a1e2095bb3e80f94bc387714d564f46423afb246d79959f60586cef2`です。

review済み14件は固定manifestにし、正本DBをread-onlyで複製して新しいshadow DBだけへconfirmed `project_repository` relation、confirmed `project_repository` identifier、operator adoptionを投影しました。relationのsubjectはexact componentを維持し、13件が`service`、Lexicon Communityの1件が`website`です。採用理由は登録起点ではなくoperator reviewとして保存します。project数は不変、relationとidentifierは各14件増加し、foreign key違反0、SQLite integrityは`ok`でした。

shadow作成時点ではenrichが正本DBを更新中でした。対象projectの通常保存は、今回の変更とは無関係な既存repository 5件の不足していたbootstrap adoptionも補完しようとしましたが、materializerはこの副作用を検出して複製側から除外します。最終差分はrelation、identifier、decision、operator adoptionが各14件、project数不変です。review、未観測、取得失敗のclaimと既存candidate 5件はこの初期適用に含めず、LLM自動昇格も接続しません。

2026-08-27の正本適用では、実行中enrichへ協調的な停止要求を送り、687 / 2897件のproject境界でcheckpointを保存してからWeb serviceを停止しました。停止後の最新DBからshadowを作り直して14件以外の差分がないことを確認し、切替前DBを`data/backups/project-repository-review-20260827T204144+0900/survey.db`へ保存しました。backupのSHA-256は`fa446d9e7e898005f13b9001bf144343a4e420ae2128f2b04695beb497173dc1`です。

最終materialization reportは`.tmp/project-repository-review-final-20260827.json`、content hashは`1a346b011486739d700ce17d025ecded005554d1398dc750e8c9e06ffbbeb907`です。切替後DBはrelation、identifier、decision、operator adoption各14件、project数4,598不変、foreign key違反0、SQLite integrity `ok`で、aTIProtoへのskytip-simple登録は0件です。Web APIでMatadiscoの新repositoryがconfirmed `project_repository`として表示されることも確認しました。中断したenrichはrun ID `enrich-20260827T060057Z-68598db3`として再発見され、687 / 2897件、`can_resume=true`です。
