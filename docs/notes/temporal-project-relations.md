# Identifierと登録プロジェクトの時間関係の設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。改名、移転、後継を信頼グラフやmergeへ暗黙に混ぜず、何が同じまま時間だけ進んだのかを表すための原則案です。以下の「現行実装との差分」「次の確認」は2026-08-11時点の記録です。非現行identifier・採用理由・確定transition・noiseの保存は後続のschema v3で実装・適用済みです。現行契約は[Project Record Schema](../developer/project-record.md)、移行検証は[Identifier保存方式のshadow比較](identifier-storage-shadow-comparison.md)を参照してください。project間の時間関係と未確定sampleは設計途中として残します。

## 結論

時間経過で起きる変化を、次の三つへ分けます。

1. 同じ登録プロジェクトに属する同じ資源のidentifierが変わった
2. 同じ登録プロジェクトの名称や構成が変わった
3. ある登録プロジェクトを、別の登録プロジェクトが引き継いだ

1と2では同じproject keyを維持します。3では二つのproject recordを維持し、前身・後継関係を記録します。fork、派生project、mirror、並行するhosting、同時稼働するalpha版は、時間が関係して見えてもこの三分類へ自動的に入れません。

信頼グラフは「現在の帰属主張が、現在の直接関係によって支持されるか」を判断します。過去の帰属、移転、前身・後継は別の時間関係policyが扱い、過去に正しかったidentifierを現在の信頼判定の起点へ自動的に戻しません。

時間上の状態を決める前に、そのidentifierがどの登録プロジェクトへ帰属するかを決めます。同じauthority配下にあること、同じrepositoryや実験環境から観測されたこと、recordが実在することは、どのprojectの構成要素かを単独では決めません。現行・旧・non-productionという区分は、帰属が確定したidentifierにだけ適用します。

## 最初に分けるもの

### URL正規化と同時alias

HTTPからHTTPSへの変更、providerが定義する同じrepository URLの表記差、同時に有効なroot/`www`など、時点によらず同じ資源を指すと確認できるものは正規化またはaliasです。これは時間関係ではありません。

同じcontentを表示したことだけではaliasにしません。共通template、copy、mirror、alpha版でも同一contentは生じます。root/`www`も、同一contentだけでなく明示的なcanonical情報または同一siteとしての十分な根拠がある場合に限ります。

### 同じ資源の移転

repositoryのrename/transfer、handle変更、serviceの旧domainから新domainへの移転など、同じ資源のcurrent identifierが時間とともに変わった場合です。旧identifierと新identifierは同じroleで同じ登録プロジェクトに属しますが、有効だった期間が異なります。

方向は旧identifierから新identifierへの「置き換え」を表します。具体的なpredicate名は未確定ですが、単なる`redirects_to`観測と、確認済みの資源移転を同じpredicateにしません。

collectionのNSID変更も、同じschema資源が名前だけを変えたと確認できる場合はこの型に含めます。たとえば`com.example.post`を`com.example.feed.post`へ移し、旧record型と新record型が同じ役割を引き継ぐと公式なschema定義、実装、移行処理またはoperator判断から確認できる場合です。authority domain自体の変更も同様ですが、名前の類似やschema内容の一致だけでは置き換えを確定しません。

旧schemaを複数の新schemaへ分割した場合、意味を変えて作り直した場合、互換期間として旧NSIDと新NSIDを並行利用する場合は、単純なidentifier置換ではありません。前二者は同じproject内のschema間の置き換え・移行関係、後者は複数の現行collectionとして扱い、旧側の終了が確認された時点でだけ旧identifierへ移します。この区別は完全自動化を要件とせず、機械判定できないものをreviewへ残せる保存枠を用意します。

### 同じ登録プロジェクトの変化

登録プロジェクトの改名、説明の更新、構成要素の追加・終了は、project keyを変えません。必要な名称履歴と旧identifierを残し、旧名や終了したserviceを現在の構成要素として表示しません。

構成要素の網羅性は要求しません。観測済みsurfaceの一部が終了したことを記録できればよく、未観測のAPI endpoint等を完全に列挙してから変化を確定する必要はありません。

### 別の登録プロジェクトへの継承

前身の成果や利用者を新しいprojectが引き継いでも、同じprojectと扱うとは限りません。前身と後継を別project recordとして保つ場合は、project間の方向付き関係として記録し、identifierや採用理由を一括移動しません。

Basker→InfoEnon、DateSky→Nomare型の事例は、まずこの候補として人が「同一projectの改名」か「別projectへの継承」かを判断します。名前の類似、redirect、同じ開発者、collectionの類似だけでどちらかへ自動分類しません。

### 時間関係へ入れないもの

- GitHub版とTangled版のように、どちらがmirrorとも明示されない並行hosting
- 同時に稼働する複数service surface
- alpha版と正式版が並行している状態
- fork、template由来、派生project
- tenant、instance、第三者mirror
- 一時的なredirect、login pageへのredirect、同一domain内のrouting

これらは並行関係、派生関係、配備形態、または単なる観測です。後で片方が終了し、正式な置き換えが確認された時点で、旧identifierと置き換え関係を履歴へ追加できます。

## 観測と確定した時間関係

HTTP redirect、HTML canonical、provider metadata、DID document、告知文、同じcontent、取得不能はすべて観測です。一つの観測から直ちに資源移転やproject継承を確定しません。

| 観測 | 単独で言えること | 単独では言えないこと |
| --- | --- | --- |
| same-host HTTP→HTTPS | URL表記の正規化候補 | projectの移転 |
| root/`www`のcanonicalまたはredirect | 同時aliasの候補 | cross-domain移転 |
| cross-host redirect | 旧URLが新URLを指した | 同じservice、現在も同じproject |
| providerのstable repository IDを維持したrename/transfer | 同じrepositoryのURLが変わった | project自体が同一か、owner変更後も帰属するか |
| DID documentの先頭`alsoKnownAs`変更 | その観測時点のcurrent handle | 旧handleが現在も同じDIDを指すこと |
| 同一content | 比較対象の取得内容が一致した | alias、mirror、移転、同じproject |
| collection recordの観測 | そのNSIDがその時点で利用された | schemaのproject帰属、production性、旧NSIDとの置き換え |
| 公式の案内面から補助URLへのlink | そのprojectが地図、status page等のresourceを案内した | URLがprojectを代表するidentifierであること、独立したserviceであること |
| 公式な移転・改名告知 | 主張主体が関係を明示した | 告知先もそれを認めていること、別projectか同一projectか |
| 取得不能または消失 | その観測時点で取得できなかった | 資源が終了したこと、別URLへ移転したこと |

自動的に「同じ資源の移転」と確定できるのは、providerのstable resource IDなど、入力identifierに対して同じ資源の新identifierを直接返す一次情報がある場合に限定します。このような一次情報が意味上の両方向を保証するなら、同じprovenanceを持つ二つの方向付き関係へ分解し、通常の信頼判定に利用できます。HTTPや公開文面から判断する場合は、旧側が新側を示す経路と、新側が同じ登録プロジェクトの構成要素であることを支持する経路の両方を要求します。二つの経路が同じ取得結果に由来しても構いませんが、単なる検索用inverseは数えません。

projectの改名か別projectへの継承かは自動確定しません。この判断はprojectという囲い自体を変え、mergeと同程度の影響を持つためです。

## Identifierの扱い

まず帰属主張を、特定projectへの帰属が確定したもの、帰属先が未確定の観測、無関係または誤検出に分けます。帰属未確定の観測は、recordの存在や取得履歴を保持してもproject identifierにはしません。公式ページからリンクされた地図、status page、補助APIのようにprojectとの関係は明らかでも、projectを代表するidentifierとして採用する必要がないresourceも観測のまま保持できます。

帰属が確定したidentifierには、汎用的なcurrent/historical期間を一律に持たせず、意味と検索用途が異なる次の三つを区別します。noise・誤検出はidentifierの状態ではなく、別の除外判断です。

| 区分 | 意味 | 通常検索 | Admissionと信頼判定 |
| --- | --- | --- | --- |
| 現行identifier | 現在、登録プロジェクトを代表するidentifier | 表示する | roleと採用理由が許せば利用する |
| 旧identifier | 過去に登録プロジェクトを正式に代表した肯定的な履歴 | 現在のprojectへ誘導する | 自動再登録せず、信頼判定の起点にしない |
| non-production identifier | projectとの関係は正しいが、productionを代表する構成要素として登録しないもの | exact検索や調査画面ではprojectとの関係を示してよい | 自動登録せず、信頼判定の起点にしない |

旧identifierとnon-production identifierは肯定的なproject関連情報です。既存identifier rowに状態を追加して保持する案と、履歴・補助情報として別に保持する案のどちらも許容します。noiseは帰属情報ではなく除外判断なので、同じ保存分類へ入れません。

production以外をstaging、preview、development、temporary等へ細分化しません。URLやNSIDの名前だけから推測せず、明示的な観測または手動判断がある場合にだけnon-productionとして扱います。一時的なdeployment URLなど、調査に不要なものは観測だけに留めても構いません。

新identifierを追加しただけでは旧identifierへ移しません。明示的な置き換えまたは終了が確認された場合にだけ旧identifierとして残します。一つのroleに現行identifierが複数あって構いません。

## 採用理由との関係

採用理由は「この帰属主張が正しい理由」、旧identifierへの移動や置き換え関係は「以前はprojectを代表していた」という履歴を表すため、同じ情報へ統合しません。手動confirm、登録起点、静的な信頼判定は帰属主張を採用できても、別identifierへ置き換わったことまでは決めません。

時間関係policyは、確認済みの移転から新identifierのcandidateを作れます。ただし、それだけで自動confirmするかは通常の採用規則へ委ねます。新側が通常の信頼判定で`SUPPORTED`になるか、operatorまたは承認済みの収集入口が採用した場合にだけ`confirmed`にします。

置き換えを自動処理する場合は、旧identifierを信頼判定の起点として新identifierを評価し、新identifierを採用してから、旧identifierと置き換え関係を履歴へ移します。先に旧identifierを起点から外したり、新identifierの採用に失敗したまま旧identifierだけを終了させたりしません。

旧identifierの採用理由は移転後も履歴の根拠として残しますが、信頼判定の起点には使いません。移転先へ旧identifierのoperator採用理由をコピーしません。

この分離により、canonical redirectを検出した処理が旧confirmを新URLへ付け替える現行動作は変更対象になります。移転観測、新URLの採用、旧URLの履歴化を別々に記録します。

## 信頼判定、Admission、mergeでの用途

| 用途 | 時間関係の扱い |
| --- | --- |
| Candidate admission | 確認済みの資源移転先をcandidateとして提示できる。raw redirect、同一content、名前一致からは生成しない |
| 自動confirm | 時間関係単独では行わない。新identifierへの通常の往路・復路または別の採用理由を要求する |
| 信頼判定の起点 | 現行identifierで、採用理由が有効で、review対象でない帰属主張だけを使う |
| 関係経路 | 現在の観測が支持する直接関係を通常policyで使う。旧identifierとnon-production identifierは起点にしない |
| project merge | 確認済みの同一project内の資源移転は重複候補の説明に使えるが、自動merge根拠にはしない |
| 前身・後継 | 別projectを維持し、merge抑止と表示に使う。採用理由とidentifierは継承しない |
| fork・派生 | [派生関係の設計メモ](derivation-relations.md)に従い、Trustとmergeの正の根拠にしない |

テスト版から正式版への追従も同じです。正式版が同じprojectの新しいserviceだと確認できればcandidateを作れますが、テスト版はproductionを代表していた事実がなければ旧identifierではなくnon-production identifierです。単に同じcontentを返す二つのURLは記録対象にしません。

## 現行実装との差分

- `redirects_to`はraw HTTP redirectとHTML canonicalを同じpredicateで保持しています。観測と確定したalias・移転を分ける必要があります。
- same-site redirectから同じprovenanceの双方向`service_canonical` edgeを作り、条件次第で旧URLのconfirm decisionを新URLへ移しています。双方向の意味保証は利用できますが、帰属の採用と旧identifierへの履歴化を一つのmutationにしません。
- `url_aliases`はroot/`www`の同一contentもconfirmed aliasにします。同一content単独ではaliasにしない方針へ変更が必要です。
- DID documentの`alsoKnownAs`を複数handleとして収集しています。current handleは規格上の先頭値を使い、以前の観測値は履歴として分ける必要があります。
- `project_identifiers`は旧identifierとnon-production identifierを区別せず、これらを自動再登録から抑止する正本もありません。
- `investigation_runs`には過去snapshotがありますが、旧identifierを検索し、現在のprojectへ誘導する肯定履歴の正本ではありません。
- `project_key_aliases`は実行済みmerge後のkey解決だけを表し、projectの改名履歴や前身・後継は表しません。
- fork・派生は現状`separate_project`やreview理由へ寄せられ、時間関係とは別です。この境界は維持します。

## 保存契約への影響

全identifierに複数の有効期間を持たせることは初期要件にしません。論理的に必要なのは次の情報です。

- 現行の帰属主張: `project + kind + value + role`、classification、採用理由
- 旧identifier: 元の帰属主張、旧identifierになった時刻と理由、置き換え先
- non-production identifier: projectとの肯定的な関係、自動登録を抑止する判断と根拠
- noise・誤検出: identifier値に対する除外decision
- project間の時間関係: 前身project、後継project、関係種別、発効時刻、operator判断

旧identifierとnon-production identifierは、既存`project_identifiers`に状態を追加する案と、別の履歴・補助tableへ置く案を比較します。どちらを選んでも通常query、Admission、信頼判定は現行identifierだけを既定入力とし、旧identifierは通常検索から現在のprojectへ誘導でき、non-production identifierはexact検索または調査画面から確認できる必要があります。

noiseはprojectとの肯定的な関係を持たないため、同じidentifier tableへ入れません。collectorから見れば旧identifier、non-production identifier、noiseはいずれも再生成を止めるguardになりますが、guardは別々の保存事実・decisionから投影します。

資源間の置き換え関係を`project_relations`へ置くか専用tableへ置くかは未確定です。project間の前身・後継はregistry全体の関係なので、一方のproject配下だけへ保存しない形が必要です。

## 2026-08-11のread-only監査

正本DBを更新せず、現行identifier、decision、redirect relation、URL alias、collection syncの再取込条件を確認しました。この監査は名前からnon-productionを自動分類するものではなく、保存上の必要性と候補件数を測るものです。

### 名前では分類できない

identifier値に`dev`、`test`、`temp`、`staging`、`preview`、`alpha`、`beta`相当のsegmentを含むrowを候補抽出しました。`.dev`はNSID authorityの正規なTLDとして大量に使われ、`alpha`や`test`もprojectが正式に採用したcollection名に含まれます。文字列だけでnon-productionへ分類する規則は採用できません。

`test`、`temp`、`alpha`、`beta`等を含むidentifierに対するactive decisionは31件あり、すべてlegacy migration由来のconfirmで、block/deferは0件でした。これは31件が誤りという意味ではなく、現行保存に「帰属は正しいが自動登録対象外」という判断を表す場所がないことを示します。

ここでの判定軸はproject自体の完成度ではなく、そのidentifierが同じprojectの中で何を代表するかです。開発中またはalpha段階のprojectに公開面が一つしかなければ、その公開面は現行identifierになり得ます。逆に完成したprojectのsandboxや一時deploymentは、projectとの関係が正しくてもnon-production identifierになり得ます。別のproduction identifierが存在することを必須条件にはしませんが、名前だけでなく、公式説明、同時に存在する他のsurface、登録起点、利用実態を合わせて判断します。

### 代表例のsample review

| 事例 | 確認した事実 | 暫定分類 | 一般則への含意 |
| --- | --- | --- | --- |
| ATPcraft | `atpcraft.at`はoperator confirm済みで、HTML canonicalは`dev.atpcraft.at`を指す。公開中の[ATPcraft案内面](https://dev.atpcraft.at/)はactive developmentと説明し、`atpcraft.lenooby09.tech`をLive mapとして案内する | `atpcraft.at`は現行を維持し、`dev.atpcraft.at`は観測のまま。Live mapは公式な現行resourceだが、project identifierへ昇格させる必要はない | URLが公式かつ現行でも、補助resourceとprojectを代表するservice identifierは分ける。service surfaceの網羅性は要求しない |
| Drydown | `drydown.social`のconfirmがclearされ、redirect先の`alpha.drydown.social`が現行websiteとconfirmed serviceになっている。`social.drydown.*` collectionも観測される | `alpha.drydown.social`は現行を維持する。`drydown.social`は旧identifierか同時aliasか追加確認が必要 | redirectだけでは旧・新を確定できないが、`alpha`という名前も現行性を否定しない |
| Barazo | [公式documentation repository](https://github.com/singi-labs/barazo-docs)はprojectをalphaとし、`docs.barazo.forum`を公式docsとして示す。正本DBでは`staging.barazo.forum`が現行websiteである | `docs.barazo.forum`と`staging.barazo.forum`は現行を維持する。後者をnon-productionへ移すには、別の代表surfaceまたは公式なsandbox扱いを確認する | project全体がalphaであることと、そのproject内の補助環境であることを分ける |
| Arabica | `social.arabica.alpha` namespaceは2026-08-08時点でもrecordが観測され、同じprojectの現行collection群として利用されている | 現行 | `alpha` segmentだけではnon-productionにしない |
| Spindle / publicdomainrelay | `com.publicdomainrelay.temp.*`のrecordは2026-08-08時点でも多数観測されるが、market、agent、compute等はSpindle固有とは限らない。`com.publicdomainrelay.temp.tangled.spindle.repos`はSpindleを明示するが、Tangled側schemaからの移動かは未確認 | Spindleへの一括帰属を撤回し、project帰属reviewとする。Spindle固有NSIDの置き換え関係も一次情報またはoperator判断まで保留する | recordの存在、共通authority、`temp` segmentはいずれもproject境界を決めない。時間状態を付ける前に帰属を確定する |
| lexicons.bio | `bio.lexicons.temp`と`bio.lexicons.temp.v0-1`の双方に過去のrecordがあり、後者は2026-08-08時点でも利用される | `v0-1`は現行を維持し、無印側が旧identifierかは追加確認 | record activityは有用な観測だが、置き換え確定とは分ける |
| Musicsky / observ.ing | `temp` namespaceのrecordは観測されているが、直近観測だけでは正式namespace、一時検証、終了済みのどれかを確定できない | review対象 | 情報不足を名前で埋めず、人間の判断を将来の一般則へ戻す |

このsampleは100%の事前分類を目標にしません。確定できないものをreview対象として残せること、review結果をcollector個別例外ではなく「何を見ればproject帰属と、現行・旧・non-production・noiseを区別できるか」という一般則へ反映できることを重視します。

### redirectは移転より広い

`redirects_to`はconfirmedなHTTP redirect 996件とHTML canonical 543件を保持しています。login path、言語path、GitHub page、同一host内routing、productionからdev URLへのcanonical、別serviceへのcross-host redirectが混在し、raw relationを旧identifierまたは移転へ一括変換できません。

### Redirect観測のread-only分類

`trust-policy-shadow-v3`では、時間関係を確定せず、URL構造だけでraw redirectを次のように分類します。同じproject、同じsubject、同じobject、同じmethodの重複を除くと1121観測でした。

| 分類 | 件数 | この段階で言えること |
| --- | ---: | --- |
| URL正規化 | 37 | 同じhostとpathの表記差。identifier正規化候補であり、project移転ではない |
| root/`www` alias候補 | 252 | rootと`www`の直接関係。alias候補だが、raw relation自体を移転にしない |
| 同一host内routing | 213 | `/login`、language path、documentation version等。同じsurface内の遷移として時間関係から除外する |
| 同一site family内の別surface | 170 | rootとsubdomain等。別surface、alias、alpha版、移転のどれかはURL構造だけでは決めない |
| 別siteへのredirect | 449 | service移転、repository/package/docsへの案内、platform landing、別projectが混在する |

このうち、redirect元が現行confirmed serviceまたは過去にconfirmされたserviceで、同じprojectにredirect先claimがあるか、現行serviceから未登録の別surfaceへ向かうものは25組でした。HTTP redirectとHTML canonicalが同じ組を示す場合は人間の確認一件としてまとめています。redirect先が既に同じprojectのclaimであるものは3組、未登録のものは22組です。

25組は移転候補の正解集合ではありません。DateSky→Nomare、Drydown、Pinksky→Pinkleapのような改名・移転らしい例と、service URLからGitHub repositoryやpub.dev packageへ着地する例、BarazoやATPcraftのようにalpha・staging・developmentの公開面を含む例が残っています。これは意図した結果で、一般則だけで明らかなrouting 213件等を先に外し、残る小さい集合を人が分類して次の一般則へ戻します。HTML canonical単独、cross-site redirect単独、未登録targetであることのいずれも、旧identifier化や新identifier採用を自動確定しません。

25件のreview entryを人が再分類すると、既定HTTPS portの有無だけが違うPoptartの二本をまとめた24組になりました。

| 人間向けの分類 | raw観測 | 組 | 扱い |
| --- | ---: | ---: | --- |
| 明示的な改名・移転 | 1 | 1 | [Pinksky profile](https://bsky.app/profile/pinksky.app)がPinkleapへ発展したと明示するため、同じproject内の置き換え候補にできる |
| repository・packageへの案内 | 4 | 3 | Bluesky MCP Server、Okazu Diary、Poptart。service移転ではなく、project repositoryまたはpackage resourceへのlinkとして時間関係から除外する |
| 同一site family内の別surface | 6 | 6 | Barazo、If This Then、Scribe、Slices、ATPcraft、Drydown。別surface、canonical、production切替のどれかをURLだけでは決めない |
| 行先が安定していない観測 | 3 | 3 | PhotoSky／PskyからPhotoSkyまたはZeensへの観測。[PhotoSky](https://photosky.at/)が現在は独自の案内面を返すことも含め、確定した移転へ昇格しない |
| 別siteへの移転・改名候補 | 11 | 11 | `main.bsky.dev`→`bsky.app`、DateSky→Nomare、Pipup→Lemma、Provcheck、Eurosky、Thread Viewer、Cirrus、LastFM.blue、Denazen→Rhize、Flapnote→Yuunagi、Cabildo Abierto。個別確認対象には残すが、raw redirectから一括確定しない |

この分類から追加できる一般則は四つです。既定portと末尾slash等を正規化して人間の確認単位を重複させないこと、repository・packageとして型を判別できる着地先を時間関係から除外すること、同じsubjectから複数targetが観測された場合は移転より先に変化する観測として扱うこと、同一site family内のredirectを旧・新ではなく別surface候補として扱うことです。残るcross-site候補は、明示的な改名・移転告知、providerのstable resource ID、または旧側と新側の帰属を支持する直接関係が揃った場合にだけ確定します。

canonical redirectによるactive confirmは7件ありました。Drydownでは`https://drydown.social`のlegacy confirmをclearし、redirect先の`https://alpha.drydown.social`を自動confirmしています。ATPcraftではproduction URLのHTML canonicalが`https://dev.atpcraft.at`を指し、Barazo Docsでは`https://staging.barazo.forum`がconfirmed serviceとして残っています。これらが本当にnon-productionかは個別確認が必要ですが、URL名を無視してredirect先へconfirmを付け替える現行動作をshadow比較する代表例になります。

URL aliasは196 projectにあり、理由別ではconfirmed redirect 200件、同一contentだけによるconfirmed alias 3件でした。同一content由来3件は、合意済みの「content一致単独ではaliasにしない」規則による除外候補です。

### 改名・後継の既知事例

DateSky/Nomareは現在一つのproject recordに旧・新repository、旧・新NSID、旧serviceを持ち、`https://datesky.app`から`https://nomare.net`へのredirectも保存されています。同一projectの改名・移転候補として扱い、各repoとNSIDが本当に置き換えられたかは別々に確認するのが妥当です。

BaskerとInfoEnonは別project recordで、namespaceと提供目的も異なり、正本DBに両者の直接関係はありません。現時点ではmergeせず、一次情報で継承が確認できた場合だけ別project間の前身・後継関係を付ける候補です。

### collection syncの復活経路

collection inventoryのincremental filterは、現在のproject identifierまたはmanual seedに含まれるNSIDだけを既知としてskipします。project identifierからnon-production collectionを単に削除すると、次回以降のinventoryで再びeligibleになります。

既存projectへattachした後の`add_collection_owned_nsid()`はproject-scopedなblock/deferを確認しますが、unmatched collectionから新しいprojectを作る前に参照できるregistry-wideな肯定履歴・除外indexはありません。したがって、non-productionと旧identifierはprojectへの関連を保ったまま`known_collection_nsids()`へ含め、noiseは別の除外decisionからfilterする必要があります。

### 保存形の比較

production codeでは`project_identifiers`を9 moduleが直接参照し、SQLによるread・writeは20箇所あります。現在のhydrateは全rowを`resolved_nsids`、`service_urls`、`source_code_urls`等の現行runtime fieldへ戻し、exact lookup、split、source sync、merge、Trust shadowも全rowを現行構成要素として扱います。

| 観点 | `project_identifiers`へ状態列を追加 | 旧・non-productionを別保存 |
| --- | --- | --- |
| 通常表示とruntime | hydrateを含む全readで現行条件が必要 | 既存tableを現行専用のまま維持できる |
| TrustとAdmission | 起点、対象、candidate集計の全てで現行条件が必要 | 現行tableだけを読む既存経路は変更不要 |
| exact検索 | 一つのtableで検索できるが、現行と旧の優先順位が必要 | 二つのtableをunionし、現行を優先する明示的なqueryが必要 |
| collection sync抑止 | 通常構成要素から除外しつつknown集合には残す特別処理が必要 | current NSIDと別保存NSIDをknown集合でunionする |
| split・source sync | 旧rowを現在所属として誤操作しない条件が必要 | 現行tableだけを読むため自動的に拒否できる |
| merge | 一つのrow集合を移せるが、旧rowを現行表示へ混ぜない監査が必要 | 別保存rowと採用履歴を明示的に移し、元project keyを履歴へ残す必要がある |
| 移行と保守 | schema列追加は小さいが、既存20 SQL箇所とruntime viewの監査範囲が広い | tableと専用mutationは増えるが、変更箇所を検索、sync抑止、merge、履歴表示へ限定できる |

比較結果として、`project_identifiers`は現行の帰属主張だけを保持し、旧identifierとnon-production identifierは別保存する案を採ります。物理table名は未確定ですが、論理的には`project key + kind + value + role + standing`を持つ肯定的な非現行関連です。noiseは引き続き別の除外decisionであり、この保存へ入れません。

現行から旧・non-productionへの移動と再有効化は、二つのtableを個別に編集する一般SQLではなく、専用mutationで同一transaction内に行います。同じ`project key + kind + value + role`が現行と非現行の双方で有効にならないことをvalidationで保証します。旧identifierのexact検索は現在のprojectへ誘導し、non-production identifierはexact検索または調査画面だけに出します。

採用理由を`project_identifiers`のJSON列へ埋めると、identifierを別保存へ移すたびに履歴も移し替える必要があります。このため採用理由はidentifierの現行状態から独立した保存とし、旧identifier化した後も元のoperator、bootstrap、trust policyによる理由を監査できる形にします。同じ資源の置き換え関係とproject間の前身・後継関係も、採用理由へ埋め込みません。

## 次の確認

1. sample reviewで確定できなかったDrydown旧URL、Barazo staging、lexicons.bio旧namespace、Musicsky、observ.ing、publicdomainrelay配下collectionの帰属を、必要になった時点で一次情報またはoperator判断により確定する
2. 残ったcross-site候補11組は、保存schemaの決定に影響する事例が出た場合に一次情報またはoperator判断で確定する
3. 旧identifierとnon-production identifierを除外した入力で、既存confirmed件数と信頼判定の起点がどう変わるかshadow比較する
4. 非現行identifier、採用理由、時間関係を分けた論理schemaについて、mergeと再有効化を含むtemp DB prototypeを作る
5. prototypeでmigration dry-run、通常検索、exact検索、sync抑止、Trust shadowの差分を比較し、物理schemaと移行計画を確定する

この確認が終わるまで、redirect処理、DID/handle収集、project merge、正本DB schemaは変更しません。
