# 登録プロジェクトのマージ規則shadow比較

> 作業用・比較結果文書です。現行仕様の正本ではありません。2026-08-13の正本DBをread-only transactionで評価した結果と、次の設計判断に必要な材料を残します。完全なJSON reportは`.tmp/merge-policy-shadow-v3.json`に生成し、コミットしません。

## 結論

role、直接relation、手動の別project指定を分ける境界を維持したまま、collectionだけを持つrecordを包括的なproject recordへ吸収する候補を非対称に評価すると、現在の3,233 pair中110件を「マージを勧めるが人の確認が必要」と分類できました。うち109件は現行`auto_merge_ready`にも含まれ、1件は新しいauthority単位の探索で見つかりました。固定19事例はすべて期待どおりです。

この110件を自動マージしてよいとは判断していません。規則は「独立した提供物を表しにくいcollection断片」という現在の手動判断をreview理由へ落としたもので、存続recordの自動決定もしません。未確認service候補のhostと一致するproject名はマージ根拠から除外し、ATProtoFans / high-fiveやLexicon Garden周辺のrecordは「マージを勧める」から要確認へ戻しました。この種の違いを保存情報だけで決めつけず、人が確認して`separate_project`などの判断を残すことが初期用途です。

過去510マージでは、両側snapshotがある504件中28件を同じ規則で説明できました。GainForest、PopFeed、Skyrdle、TZ2ATなど、個別collection recordを包括recordへまとめた実例が含まれます。過去判断は正解データではありませんが、初版の0件から、実際の手動作業の一部を根拠付きで優先できるところまでは改善しました。

比較器はread-onlyの開発補助であり、自動マージ、候補保存、Web UI、schema、正本DBを変更しません。110件をtarget単位でreviewした結果、107件は同じ編集単位へ収める判断を支持でき、newdin ReadとUntangledの2件は独立した提供物として分離を維持する方が妥当と分かりました。teal.fmの1件は、collectionの実在だけではprojectへの帰属も過去の正式採用も確認できないため保留します。次の設計ゲートは、この理由区分をWebのreview動線へ接続する価値と、追加すべき表示・判断・guard記録の範囲を確認することです。

## 人間向けに言い換えた比較内容

現行方式は、名前、domain由来token、authority family、repositoryなどが似た記録を候補にし、複数の一致をscoreへ足して`auto_merge_ready`を付けます。shadow policyは、比較するpairの発見と、なぜ人へマージを勧めるかを分けます。

今回追加した問いは、「この二つは対等なprojectとして同一か」ではなく、「片方はcollectionだけを個別projectにしてしまった断片で、もう片方の包括projectへ収めるべきか」です。次の形を満たすpairだけを優先reviewします。

1. fragment側のcurrent confirmedな構成要素が`owned_nsid`だけである
2. fragmentのcollectionがtargetのcollectionの真部分集合であるか、targetにservice、source repository、project accountなど独立した構成要素がある
3. 後者の場合は同じregistrableなNSID authorityに属し、直属namespace、project alias、またはproject名の文脈も一致する
4. fragmentが共有Lexiconとして分類されておらず、activeな`separate_project`や時間・派生関係に反しない

`uses_collection`、`requests_permission`、`defines_schema`は表示用の構成参照として残しますが、それだけでは吸収候補を作りません。repositoryが別projectのcollectionを使うことは普通にあり、okazu-diary / GitHubなどを誤って勧めるためです。同じrepository、root domain、名前、authorityも一般のマージ根拠にはせず、collection-onlyという非対称な形の中だけで補助に使います。

「マージを勧める」場合も`automatic_execution_allowed`は常にfalseです。fragment/targetの向きは確認すべき構造を示すもので、どちらのrecordを残すかは別判断です。

## 実行条件

`project-registry compare-merge-policy`で正本DBをSQLite `mode=ro`の単一transactionとして読みました。実運用のmulti-tenant domain設定はDBと同じcheckoutにある`config/domain-overrides.json`を自動検出しています。reportは指定したJSONだけを書き出し、DBを変更しないことと同一入力での再現性をtemp DB testで確認しています。

正本DBは並行してenrichされ得るため、件数は実行開始時のsnapshotです。以下はcontent hash `abe10116bef3d7f4520726a59da482c7a538f3df9f2ac3f6101ff63526091dac`のreportを基準にします。

- project: 4,416件
- current identifier: 24,901件
- relation: 71,168件
- schema v3のidentifier adoption: 0件。入力DBはまだschema v2
- 固定case: 19件中19件合格
- 評価時間: 約5.8秒

## 現在の候補比較

| 区分 | 件数 |
| --- | ---: |
| 現行duplicate candidate | 3,144 |
| 現行`auto_merge_ready` | 2,663 |
| 現行strict batch | 0 |
| 現行moderate batch | 2,665 |
| 現行またはshadowで見つけた全pair | 3,233 |
| shadow: マージを勧める | 110 |
| shadow: 要確認 | 3,105 |
| shadow: 別扱いを守る | 18 |
| shadow: 時間・派生関係へ送る | 0 |

110件の内訳は、現行`auto_merge_ready`から109件、現行候補ではなかったpairから1件です。3件はfragmentのfull `owned_nsid`がtarget側の真部分集合で、darkworld.download、Prefect AT Protocol Integration、`io.prefect.flowRun.state`由来recordが該当します。残りはcollection-only recordと包括recordのauthority・文脈を組み合わせたものです。

18件の「別扱いを守る」はactiveな`separate_project`判断を持つpairです。現行側でも既にblockされているため、新たに人の作業を省いた件数ではありません。shadow discoveryが現行候補に71 pairを追加しましたが、そのうち70件は要確認のままであり、authority共有だけをマージ推奨へ上げてはいません。

現行v2の再現性も確認しました。通常のproject loaderで計算した候補3,144、`auto_merge_ready` 2,663、strict 0、moderate 2,665と、比較器内のv2 baselineが一致しました。custom multi-tenant suffixを読まずに比較すると件数が変わるため、比較器はDB checkoutの設定ファイルを自動検出し、そのpathとhashをreportへ残します。

## sample review

狙いどおりの代表例は次です。

- darkworld.download: fragmentの唯一のfull NSIDが包括recordにもあり、包括側はほかのcollectionも持つ
- Prefect: integrationまたは個別flowRun recordのfull NSIDが包括recordにもあり、包括側はrepositoryなどを持つ
- GainForest: Soundscape、Scout、Walletなどcollection-onlyの小recordがあり、GainForest本体は同じauthorityのcollection群とserviceを持つ
- Postgame: `at.postgame.list.*`のcollection-only recordとrepository recordが、兄弟seed、同名、repositoryからの利用関係を持つ
- TZ2AT、Aether OS、FreeMix: 多数の個別collection recordと、同じ名前文脈を持つ包括recordがある

人の判断を残すべき代表例もあります。

- ATProtoFans / high-five: authorityと名前の文脈は一致するが、high-fiveは別レイヤとして分ける方が妥当という既知判断がある。現在のDBにpair単位のguardがないため候補に出る
- GroundMist Library / GroundMist Sync: 保存された構成要素だけでは、どちらが包括projectか、別projectかを確定しにくい
- 共有Lexicon authority: categoryが`lexicon`と分かるrecordはfragmentから除外するが、未分類なら人が役割を確認する必要がある

この誤り方は、件数を増やすために許した自動誤マージではなく、reviewで解消する情報不足です。初期接続を行うなら、候補ごとにfragment/target、collection一覧、独立構成要素、authority、名前・alias、構成relation、hard guardを表示する必要があります。

### pair数ではなくtarget単位で見る

110 pairには44のtarget projectがあります。13 targetは複数fragmentを持ち、合計79 pairを占めます。大きいものはTZ2AT 21件、Aether OS 11件、GainForest 11件、Skyrdle 10件、FreeMix 5件、grain 5件です。残る31 targetは各1件です。

この分布では、pairを110回独立に調査するより、targetの構成と説明を一度確認し、その下にfragmentとNSIDを並べる方が判断を再利用できます。ただし一括承認は前提にしません。同じtarget群でも、productionの構成要素、旧版、開発用、別レイヤのprojectが混ざり得ます。

### 複数fragmentを持つtargetのレビュー

13 target・79 pairについて、targetとfragmentのsummary、current identifier、service、source repositoryを正本DBの同じsnapshotから確認しました。fragment側には独立したconfirmed serviceまたはsource repositoryがなく、現時点で別projectと判断できるものはありませんでした。したがって79 pairはいずれもproject recordとしてはtargetへ収める方向が妥当です。ただしこれは79件を無条件に一括マージしてよいという意味ではなく、移すNSIDの処遇と、保存時に失われる情報を別に確認する必要があります。

| target | fragment数 | project境界の暫定判断 | identifier側の注意 |
| --- | ---: | --- | --- |
| TZ2AT | 21 | 同じTezos観測・投影schema群として統合 | `store.tz2at.*`と`xyz.tz2at.*`の並存を通常構成と移行由来のどちらとして扱うかは未確認 |
| Aether OS | 11 | WebOS内のアプリ・機能schemaとして統合 | 各アプリの稼働状況はproject境界と分けて扱う |
| GainForest | 11 | 同じLexicon群・service・repositoryへ統合 | observationsからDarwin Core系への移行記述など、旧schemaの処遇は個別確認 |
| Skyrdle | 10 | 同じゲームへ統合 | `beta*`、`atmos`、`player`をcurrent、former、non-productionのどれで残すかを個別確認 |
| FreeMix | 5 | 同じ音楽serviceへ統合 | `com.freemix.*`と`fm.freemix.*`の並存・重複が移行由来かを確認 |
| grain | 5 | 同じ写真共有serviceへ統合 | `v0`、`filmStock`の表記差、labeler schemaの稼働状況を確認 |
| Offprint | 3 | 同じ出版serviceへ統合 | `experimental`はnon-production候補。actor/profileとblock/imageは通常構成候補 |
| StratosFund | 3 | 同じ資金調達serviceへ統合 | `.dev`はnon-production候補。production名との対応を確認 |
| Backyard | 2 | actor/profileとgraphを同じserviceの構成として統合 | 特段の時間markerなし |
| Impact Indexer | 2 | 同じschema群へ統合 | service終了はproject全体の時間状態であり、個々のNSIDの帰属とは分ける |
| Prefect | 2 | 既にtargetにもある同一NSIDの重複recordとして統合 | identifier移送ではなく重複除去になる |
| Simocracy | 2 | floorとsenateを同じplatformの構成として統合 | Senateに独立した提供面が後から確認された場合は再判定 |
| laugh.town | 2 | suggestionとresyncを同じserviceの構成として統合 | 特段の時間markerなし |

このreviewから、project境界だけなら13回のtarget確認で79 pairを処理できる見込みです。一方、identifierの処遇まで一括指定できるのはBackyardやlaugh.townのような単純な群に限られます。FreeMix、grain、Skyrdle、Offprint、StratosFundの26 pairは、project統合に同意してもNSIDごとの分類reviewを残します。Impact Indexerの提供終了はidentifierのnoise扱いではなく、projectまたはserviceの時間状態として扱う論点です。

### 単一fragment targetのレビュー

残る31 target・31 pairは、正本DBの同じsnapshotに2026年8月14日時点の公式資料とsource repositoryの確認を加えてreviewしました。28 pairは、包括recordと同じproduct名・authorityを持つ通常の機能schema、target側にも同じfull NSIDがある重複record、または公式資料とproject境界の原則から同じprojectの構成・移行と確認できたrecordであり、project境界としては統合が妥当です。該当するのはATlas、at.fund、Chive、cllctv.art、CollectiveSocial、Coves、darkworld.download、DeckBelcher、Flashes、GoodFeeds、Homeport、Hypercerts、Infonomenon、Lanyards、Layers、Leaflet、LinkRing、Matadisco、OpnShelf、Postgame、ProtoPro、Rito、Roomy Space、SkyBeMoreBlue、Skyreader、とまり木、Viewsift、Yotenです。MatadiscoとRoomy Spaceはproject境界に問題はありませんが、`tmp`、`dev`、`local`を含むNSIDをnon-productionとして整理するreviewが残ります。

ATlasは[公式commit](https://github.com/lawhiteley/ATlas/commit/2328ac267dd55c7e4937f36d0ec6695308a06c62)で、同じLexicon定義と実装コードが`io.whiteley.luke.ATlas.pin`から`io.whiteley.ATlas.pin`へ同時に変更され、commit messageもLexiconの再公開と明記されています。したがってproject recordは統合し、旧NSIDはformer、現行NSIDはcurrentとして扱うのが妥当です。

Covesの[公式Aggregator PRD](https://tangled.org/bretton.dev/coves/blob/main/docs/aggregators/PRD_AGGREGATORS.md)は、AggregatorをCovesから独立したシステムではなく、Covesのcommunityへ投稿するactorとして定義しています。第三者が個別Aggregatorを実装・運用することはできますが、`social.coves.aggregator.service`はその個別実装ではなく、Coves側のauthorization・discovery機構を構成するLexiconです。したがってcollection fragmentはCovesへ統合します。

Homeport Graphは`graph.homeport.social`というservice surfaceの観測と`social.homeport.graph.subscribe`だけを持ち、独立した名称、source repository、運用主体は確認できません。同じprojectが複数service surfaceを持つこと自体はproject分離の根拠にならないため、Homeportの構成として統合します。

Hypercertsの[公式CLI](https://github.com/GainForest/hypercerts-cli)は、`org.hypercerts.helper.workScopeTag`をActivity、Measurement、Funding Receiptなどと同じdata modelとcommand setの一項目として実装し、Activityから参照する再利用可能なtaxonomy recordとして扱っています。`helper`は独立toolの名称ではないため、Hypercerts本体へ統合します。

newdinの[公式告知](https://bsky.app/profile/newdin.com/post/3lfpr5axjns2m)は、`read.newdin.com`をthreadへmentionすると展開結果を返すbotとして、専用の入口と利用手順を伴って案内しています。subdomainだけでなく本体と異なる独立した機能・利用単位が公式に示されているため、Newdin Readは別projectとして維持し、本体とのpairへ`separate_project`判断を置くのが妥当です。

teal.fmの`fm.teal.thirdparty.feed.play`は、2026年2月に外部userがLast.fm由来の再生履歴を記録した実在するcollectionです。ただし、ここから分かるのはNSIDが使われたことだけです。teal.fmによる定義・正式採用・委譲は確認できず、公開定義も回収できません。teal.fmの[公式Piper repository](https://tangled.org/teal.fm/piper)がSpotify・Last.fmの再生履歴を扱うことと、後に`fm.teal.alpha.feed.play`から`fm.teal.feed.play`へ移行したことは確認できますが、機能の類似だけで問題のNSIDをその過去版とは判定できません。したがって、projectの統合も、`current`、`former`、`non_production`、`noise`のどのidentifier処遇も保留します。判断には、当時のLexicon authority、schema record、公式の参照、または実装履歴のいずれかが追加で必要です。

Untangledは別です。[公式repository](https://tangled.org/ptr.pet/untangled)はTangledを利用する独立クライアントと明記し、`untangled.wisp.place`という入口と、問題の`place.wisp.untangled.v1.watch`を定義・利用する実装を持ちます。wisp.placeのsubdomainとauthorityを使っていても、名称、repository、利用目的、入口のすべてが分かれているため、Untangledは別projectとして維持し、wisp.placeとのpairへ`separate_project`判断を置くのが妥当です。

teal.fmとUntangledは、同じauthorityと名前文脈が統合を調べる入口にはなっても、結論を与えないことを示します。Untangledは独立したrepository・提供面・利用単位が分離根拠になります。teal.fmは判断に必要な正の帰属根拠も独立projectの根拠も足りないため、現状を維持してreviewを残します。

### identifierの処遇が別に必要

NSIDのsegmentに`temp`、`tmp`、`dev`、`test`、`e2e`、`beta`、`experimental`、`local`が現れるものを機械的な確定分類ではなくreview markerとして抽出すると、5 targetに属する13 pairが該当しました。Skyrdle 10件中8件のほか、Matadisco、Offprint、StratosFund、Roomy Spaceにあります。

これらは「別projectだからマージしない」とは限りません。Skyrdleのbeta collectionのように帰属先は明瞭でも、統合後はcurrent構成要素ではなくnon-productionとして残す方が自然な例があります。したがってfragment吸収reviewには、project境界の判断に加えて、移すidentifierをcurrent、former、non-production、noiseのどれで残すかという別軸が必要です。tokenだけで自動分類せず、人が直せる注意表示として使います。

### 初回sampleで確認した反例

Lexicon Garden周辺の4 fragmentでは、`garden.lexicon.deeply-mouse.*`などのauthorityから`https://lexicon.garden`がservice candidateになり、record名も`lexicon.garden`になっていました。一方、collectionからLexicon Garden projectへの直接relationやconfirmed serviceはありません。Lexicon Gardenがネットワーク上のLexicon schemaを集約・索引することと、`garden.lexicon.*`の発行・所有主体であることは別であり、前者から後者を推定できません。[Lexicon GardenのHelp](https://lexicon.garden/help)で確認できるのもbrowser/discovery platformとしての性質です。

ATProtoFans / high-fiveも同じくhost状のcandidate由来名だけでは吸収を勧めず、既知判断どおり別レイヤとして扱います。こちらは外部情報の不足ではなく、project境界について既に得たoperator判断をpairの`separate_project`として保存すべき例です。

比較器は、fragment名が未確認service candidateのhostそのものに一致する場合、その名前とaliasをマージ根拠から除外します。この一般則によりLexicon Gardenの4 pairとhigh-fiveは「マージを勧める」から要確認へ戻りました。4 pairを個別例外としてコードへ埋め込まず、NSIDの由来や最終処遇は未確定のまま残します。high-fiveの明示的な別project判断は別途operator decisionとして保存します。

## 初版の0件から変えた点

初版は、共有しにくい構成要素の衝突と、異なる構成要素を結ぶ直接relationの両方を要求しました。この規則では正本DBの推奨が0件で、過去504マージも0件でした。理由は、個別collection recordと包括recordを繋ぐcross-record relationがほとんど保存されていないことと、そもそもcollection断片の吸収が対等な重複判定ではなかったことです。

今回の規則は直接relation条件を一般に緩めたものではありません。collection-onlyというfragment形状を先に固定し、包括側との非対称性を評価する別ruleを追加しました。これにより、同じauthority配下の二つの完成したserviceや、repositoryを共有するArabica/Oolongは従来どおり要確認に留まります。

## 過去マージとの比較

過去510 aliasのうち、sourceとtarget両方のマージ以前snapshotを取得できた504件へ同じ比較を行いました。現行v2 baselineは既存監査と同じ内訳を再現しました。

| 現行v2で再現した区分 | 件数 |
| --- | ---: |
| strict | 18 |
| moderate | 213 |
| broad candidateのみ | 144 |
| signalなし | 129 |

| shadow verdict | 件数 |
| --- | ---: |
| collection fragmentの吸収候補 | 28 |
| 要確認 | 476 |
| target snapshot不足 | 6 |

28件にはGainForest、PopFeed、Skyrdle、TZ2AT、Roomy、Offprintなどが含まれます。これは手動マージ全体の再現率を最大化する規則ではありません。改名・移転、同じserviceの重複、候補由来名しか持たないrecord、保存情報が乏しいrecordなど、残る476件は引き続き要確認です。

過去snapshotはマージ操作直前のtransaction stateそのものではなく、operatorが見た外部根拠も保存していません。したがって28件は正解数ではなく、「同じ静的理由を現在の保存情報から再構成できる件数」です。将来自動規則を接続する場合は、decision source、policy version、成立したruleを監査可能に残す必要があります。

## schema移行後でないと評価できない項目

入力DBはschema v2なので、identifier adoptionと登録起点は0件です。ただし旧recordに残る作成時の`seed_type`と`seed_value`は、現在の構成要素から再計算せず比較に利用します。Postgameのように、作成後に主identifierが変わっても兄弟namespaceから作られた経緯を失わないためです。

schema v3移行後は、同じsync eventから同じclaimを二重projectへ作ったケース、既存projectへattachしたケース、新規projectを作ったケースを区別できます。これは外部projectの同一性を直接証明しませんが、DB内の重複生成を優先reviewする運用情報になります。

fork、mirror、successor、predecessor、ownership transferのtypedなproject間relationも現在は保存されていません。DateSky/NomareやBasker/InfoEnonの実データを時間関係として分類する作業は、通常merge scoreへ混ぜず、時間関係設計を差し込む段階まで保留します。

## 現時点の採否材料

比較から支持されるのは、候補発見とマージ判断を分け、collection断片の吸収を通常の対称な重複判定とは別reasonで扱う方針です。未確認service候補から得たhost状の名前を根拠から除外した上で、現在の手動マージで頻出する形を110件へ絞り、28件の過去マージでも同じ理由を再構成できました。

まだ支持されないのは、この110件を自動実行することと、fragment側を常にsourceとして消すことです。独立project、共有authority、未分類Lexicon、包括recordの取り違えが残ります。初期接続では人が確認し、承認または`separate_project`判断を残すreview支援に限定します。

110件のtarget単位reviewでは、107件が同じ編集単位への統合を支持し、newdin ReadとUntangledの2件は別projectの維持を支持しました。teal.fmの1件は帰属とidentifierの時間上の立場をともに保留します。次は、project境界の判断とidentifier処遇を分けたreview UIのメリットが、表示・判断・guard記録の実装量に見合うかを決めます。

## 検証

- 固定case catalog: 19/19合格
- comparator unit/temp DB test: 10件合格
- 正本DB: read-only CLI実行成功
- 現行v2候補件数: 通常loaderと比較器で一致
- 過去マージのv2区分: 既存監査の18/213/144/129と一致
- `uses_collection`単独ではfragment吸収を勧めないnegative caseを固定
- `bin/project-registry-test`: 626件合格
