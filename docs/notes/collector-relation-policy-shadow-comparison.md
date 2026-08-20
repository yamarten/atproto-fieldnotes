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

上記1〜5はproduction codeと正本DBへ反映しました。6のrelation assessor固定corpusと、service→repo、README、GitHub searchの`homepage_exact` attachを含む構成要素間関係判定全体は次の設計・実装単位です。Website候補のreview UIとDB schema変更は実施していません。
