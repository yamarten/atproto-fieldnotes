# 登録プロジェクトのマージ規則設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。2026-08-13時点の原理整理、現行実装と正本DBのread-only監査、次に比較する規則案を保持します。この文書のreview前にマージ判定、DB schema、Web UIを変更しません。

## 結論

登録プロジェクトのマージは、二つの識別子が信頼できるかではなく、独立して形成された二つのproject recordが同じ説明・追跡単位を重複して表しているかを判断します。信頼グラフが確認した帰属関係は判断材料になりますが、信頼判定結果や経路をそのままマージ可否へ読み替えません。

現行の類似候補計算は、同じauthority family、domain由来token、名前、candidate repositoryなどから広く候補を探します。この用途は残せます。一方、これらの一致を加点して`auto_merge_ready`を決める方式は、共有可能なresourceや同じ運営主体の別projectを巻き込みます。候補発見とマージ判断を同じscoreで兼ねない設計へ変える必要があります。

初期案では、自動実行の件数を増やすことを目標にしません。まず候補を適切な量へ絞り、人が判断する画面に直接関係、構成要素のrole、共有可能性、反証を示します。自動実行を許す組合せは、既知事例と正本DB sampleで誤マージがないことを示せたものだけ後から追加します。

## マージが判断するもの

登録プロジェクトは、所有されたLexicon、主要実装、source repository、提供service、native appなどを一つの説明・追跡対象としてまとめる編集上の単位です。したがってマージは、外部世界に必ず存在する単一のproject IDを照合する処理ではありません。複数種類のresourceのまとまり、継続性、独立した提供物やブランドの有無を見て、二つの記録を今後も別々に説明・追跡する価値があるかを判断します。

次は同じ意味ではありません。

- 同じresourceまたはcanonical aliasを指す
- resourceが同じ登録プロジェクトに帰属するという主張を信頼できる
- 二つの登録プロジェクトを一つの編集単位へまとめる

同じprovider repository IDを指す二つのURLは同じrepositoryですが、そのrepositoryを共有するArabicaとOolongは別projectです。反対に、二つのproject recordが異なるservice URLとrepositoryをそれぞれ片方ずつしか持たなくても、相互の公式参照と同じ提供物としての継続性を確認できれば、重複recordである可能性があります。

## 三つの判断を分ける

### 1. 比較する組を見つける

候補発見は、全project pairを人が見る負担を減らすための検索です。取りこぼしを抑えるため、名前、root domain、authority、共有repository、同じaccount、同じcollection namespaceなどの弱い一致も入口にできます。候補になったことは、マージを支持する証拠ではありません。

候補の並び順には、同じ値の数を単純加算したscoreよりも、なぜ比較対象になったかを示す種類別の優先度を使います。たとえば、同じcurrent `owned_nsid`や同じ正規化済みservice URLの衝突は、名前だけの一致より先にreviewします。

### 2. 同じ登録プロジェクトか判断する

判断時は、共有されたresourceだけでなく、各recordに固有の構成要素と反証を同時に見ます。構成要素の未収集は通常あり得るため、「違うservice URLが保存されている」「片方にしかrepositoryがない」だけでは別projectと断定しません。一方、別のbrandで提供される独立service、別のcurrent owned namespace、専用project account、fork・派生・tenantなどを確認できた場合は、同じdeveloper、organization、domain、repositoryより強い反証になり得ます。

現段階では、静的規則の結果を次の人間向け区分で比較します。内部enum名は固定しません。

- マージを勧める: 重複recordと考える強い根拠があり、別projectを示す事実がない
- 要確認: 比較する価値はあるが、共有可能なresourceの一致、弱い補助情報、情報不足しかない
- 別扱いを守る: operatorの別プロジェクト指定、または独立projectを示す十分な事実がある
- 判定対象外: mirror、fork、後継など、通常の重複record判定とは別の関係を先に扱う必要がある

「マージを勧める」と自動実行は別です。初期接続では前者も人が確認し、自動実行候補は比較結果をreviewしてから限定します。

### 3. どちらのrecordを残すか決める

マージ可否と存続先は別判断です。存続先は、operatorが明示した向き、紹介文などの編集内容、currentな代表surface、現行identifier、調査履歴の扱いやすさを見て決めます。構成要素の数が多い側を機械的に正しいrecordとみなしません。

マージ時は、source project keyをaliasとして残し、identifierの採用起点と`origin_project_key`、手動判断、調査snapshotを失わない現行方針を維持します。時間関係を持つidentifierは、単にcurrent値が多い側へ上書きせず、former/currentと確定transitionを保った統合が必要です。

## 構成要素の共有可能性

同じ値の一致をどう扱うかは、identifier kindだけでなくprojectにおけるroleで決まります。ここでいう排他性はDBの一意制約ではなく、「同時点で別projectのcurrent構成要素として共有されるのが通常か」という判断上の性質です。

| role・対象 | 共有可能性の原則 | マージでの扱い |
| --- | --- | --- |
| current `owned_nsid` | 一つのprojectに帰属してほしい | 同じfull NSIDは優先review。ただし重複record、誤帰属、分割ミス、ownership移転を区別するまで自動マージしない |
| `service` surface | 同じ正規化済みURLは通常一つの提供物だが、共有landing pageや同居surfaceはあり得る | 強いreview入口。URL一致だけで自動マージしない |
| `project_account` | roleが正しければ通常一つのprojectを表す | 強いreview入口。organization/developer accountの誤分類を先に確認する |
| `source_repository` | 複数projectが共有できる | 候補発見と補助情報。単独ではマージを勧めない |
| website | organization、documentation、共有initiativeでも使える | 候補発見と表示。単独ではマージを勧めない |
| developer・organization・publisher account | 複数projectが共有できる | 説明・調査用。同じaccountだけではマージを支持しない |
| domain・authority domain | service、handle、NSID authority、organizationで共有できる | 候補発見と経路説明。同じroot domainやauthorityだけではマージを支持しない |

`confirmed`は帰属主張を採用・信頼した状態であり、resourceが他projectと共有不能であることを意味しません。マージ規則はclassificationではなくroleごとの共有可能性を参照します。

## signalごとの静的な用途

| 現行または今後のsignal | 候補発見 | マージ判断 | 理由 |
| --- | --- | --- | --- |
| 同じcurrent confirmed full `owned_nsid` | 優先 | 強い衝突として要確認 | current ownershipは排他的だが、誤帰属や移転でも衝突する |
| 同じcurrent confirmed service URL | 優先 | 強い衝突として要確認 | 同じsurfaceの重複である可能性が高いが、共有landing pageや誤ったservice分類がある |
| 同じ専用project account | 優先 | role確認後に強い衝突として要確認 | project accountなら有力だが、organization/developerとの境界判定が難しい |
| 同じ登録起点・同じ収集eventから二重作成された記録 | 優先 | DB上の重複生成を示す強い補助 | 外部のproject同一性そのものではないが、同じclaimを二つのrecordへ作った経緯を説明できる |
| 同じconfirmed source repository | 可 | 補助のみ | monorepo、共通runtime、複数project同居repoがある |
| provider上の同じrepository identity | 可 | repository URLの同一性確認だけ | repositoryのrename/transferであり、project同一性ではない |
| candidate repositoryの一致 | 可 | 正の根拠にしない | candidate自体が未採用で、dependencyやmentionも混ざり得る |
| serviceとrepositoryの直接相互参照 | 優先 | 構成の整合を示す補助 | project-scopedな公式参照なら有用だが、共有repoから複数serviceを参照する例を排除できない |
| 同じrecorded namespace | 可 | current confirmed full NSIDより弱い補助 | namespaceの粒度、共有Lexicon、観測値が混ざる |
| collectionだけを持つrecordと、同じauthority配下の包括record | 優先 | 非対称な吸収候補として人が確認 | 単独の提供物を表さないcollection断片がsyncでproject化された場合がある。ただし同じauthority配下の別projectや共有Lexiconもあるため自動実行しない |
| 同じcreator DIDとnamespace lineage | 可 | review用の補助 | organization、developer、permission-set、移転で別projectにも成立する |
| collection first creator | 必要なら可 | 通常は表示のみ | 最初にrecordを作ったaccountはownershipやproject境界を保証しない |
| 同じauthority family・domain token・root domain | 可 | 並び順と説明だけ | 一つのauthority配下に複数projectや実験collectionが存在する |
| 名前・aliasの一致 | 可 | 並び順と説明だけ | 同名project、一般名、過去名称がある |
| 信頼グラフの`SUPPORTED`相当 | 関連claimの探索に可 | verdict単独では使わない | 既に仮定したproject境界の中で帰属主張を支持した結果であり、二つの境界を同一にする判断ではない |
| `separate_project`手動判断 | 除外 | hard guard | operatorが別の編集単位として維持すると明示している |
| fork・派生・mirror・tenant・instance | 比較表示には可 | 通常mergeから外すかconfidenceを下げる | 類似性の理由であって重複recordの証拠ではない。fork metadataだけでは別project確定もしない |
| former/current transition、後継、買収、分割 | 可 | 時間関係reviewへ送る | 同じprojectの継続か別project間の関係かを先に判断する必要がある |

## 正の根拠と反証の組合せ

一つの万能scoreへ加算する代わりに、次の順で判定します。

1. `separate_project`などのhard guardを確認する
2. fork、mirror、tenant、後継、ownership移転など、通常merge以外の関係を先に扱う必要があるか確認する
3. role上共有しにくいcurrent構成要素の衝突を探す
4. 両recordの構成要素を結ぶ直接関係と、それぞれに固有の独立surface・namespace・accountを並べる
5. 名前、root domain、creator、共有repositoryなどの補助情報を説明と並び順に使う
6. 情報不足を「反証なし」として正の根拠へ加算しない

共有しにくい構成要素が一つ一致しても、初期案では自動マージしません。共有しにくい構成要素の一致と、異なるroleの構成が直接関係で整合し、独立projectを示す事実がない組合せを、将来の「マージを勧める」候補としてshadow比較します。

一方、共有repositoryの一致と別々のserviceまたはowned namespaceが同時にある場合は、共有repositoryを正の加点にせず、Arabica/Oolong型の別project候補として優先reviewします。別serviceや別namespaceがあることだけでhard guardにはせず、明示的な関係やoperator判断を得た後に`separate_project`へ確定します。

### collection断片の吸収は非対称に扱う

collection syncでは、project全体ではなく個別NSIDまたは小さな兄弟namespaceを起点にproject recordが作られることがあります。このrecordがcurrent confirmedな`owned_nsid`しか持たず、同じauthority配下にservice、source repository、project accountなど独立した構成要素を持つ包括recordがある場合は、通常の「二つの完成したprojectが同一か」とは別に、collection断片を包括recordへ吸収する候補として優先reviewします。

この規則は、二つのrecordを対称な重複として扱いません。fragment側とtarget側を明示し、次のいずれかを要求します。

- fragmentのconfirmed `owned_nsid`がtargetのconfirmed `owned_nsid`の真部分集合である
- 両recordが同じregistrableなNSID authorityに属し、targetだけが独立した構成要素を持ち、さらに直属namespace、project alias、またはproject名の文脈が一致する

共有Lexiconとして分類済みのrecordはfragmentにしません。`uses_collection`、`requests_permission`、`defines_schema`は構成参照として表示できますが、別projectのcollectionにも成立するため、それだけでは吸収候補を作りません。同じauthorityや名前も一般のマージ根拠へ昇格したわけではなく、fragment側がcollectionだけという非対称な形の中でreview優先度を決める補助です。

この規則が示すtargetは調査の向きであり、存続recordを自動決定しません。たとえばcollection側が本来のproject名や説明を持ち、repository側が補助実装だけを表す可能性は残るためです。既知の別projectは`separate_project`でhard guardにし、規則に一致してもマージを勧めません。

## 時間関係との境界

同じprojectの改名・URL移転・repository移転によって二重recordができた場合は、最終的に一つへマージし、旧identifierをformerとして残せます。DateSkyからNomareへの変更が同じ提供物の継続ならこの扱いです。

別のprojectが前projectを引き継いだ、買収後に別productへ統合した、あるprojectから別projectが派生した場合は、二つのrecordを残してproject間の時間・派生関係を表す方が適切です。BaskerとInfoEnonがどちらに当たるかは、名前やrepositoryの一致ではなく、service、ownership、説明対象の継続性を実例reviewして決めます。

したがって時間関係は本設計から忘却しない一方、通常merge scoreへ埋め込みません。マージ候補で時間的な変化が疑われた時に「同じproject内のtransition」「別projectへの後継・統合」「未確定」のいずれかをreviewできることを、後続の時間関係設計への要件とします。

## Sync時の既存project取込との境界

source syncが新しいidentifierを正しい既存projectへ取り込めれば、重複recordの発生自体を減らせます。ただし、弱い一致を使って既存projectの囲いを広げることは、誤マージと同じ結果を一つのrecord内で起こします。マージ候補を減らす目的でsyncのattach条件を緩めません。

全体原則は[帰属と信頼グラフの設計メモ](project-attribution-and-trust-design.md#sync時の既存projectへの取込を分ける)に置きます。完全一致した既存identifierの観測更新、承認済みsourceからの新規project作成、既存projectへの新identifier追加、弱いsuggestionを区別します。既存projectへの新identifier追加はproject attributionとして評価します。ただしcollection syncで同じ直属namespaceへ追加する場合は、新規project作成時と同じ登録起点の資格を認め、同じnamespace内で根拠数だけを増やさない扱いにします。

マージ側では、候補の作成経路も表示します。同じclaimがsyncのraceや旧matching規則によって二重projectへ作られたのか、弱いsuggestionを別project候補として意図的に残したのかでreview優先度が異なります。将来のshadow比較では、新しいsync取込規則なら作成されなかった重複候補と、別projectの可能性があるため作成を維持する候補を分けます。

## 信頼グラフへのフィードバック

マージ規則は信頼グラフと同じ直接事実を利用できますが、次の境界を保ちます。

- 信頼グラフは、role付き帰属主張がどの直接関係から支持されたかを返す
- マージ規則は、二つのrecordからrole付き構成要素、直接関係、provenance、current/former、手動判断を読む
- マージ規則は、信頼判定結果を加点せず、必要な場合は結果の根拠となった直接関係まで戻る
- graph pathで到達できるだけのnode、developer/organization actor、共有可能なrepositoryを、record境界の一致へ読み替えない
- `separate_project`、fork、派生、mirror、後継などの負・時間関係は、正の接続マトリクスとは別の入力として扱う

両設計で共有するべきpolicyは、identifier roleごとの共有可能性、current/formerの意味、直接relationの意味です。`SUPPORTED`などの信頼判定結果やmerge scoreを共有する必要はありません。

schema version 3の採用理由は、同じ登録起点や収集eventから二重recordが作られたかを説明する運用provenanceとして利用できます。ただし、同じsync sourceや同じoperatorが登録したというだけではproject同一性を支持しません。比較するのは、同じ具体的claimを同じ起点から二重採用した経緯がある場合です。

## 現行実装と正本DBの監査

2026-08-13に、schema version 2の正本DBをSQLite `mode=ro`で開き、現行masterの計算だけを実行しました。DB更新、外部取得、LLM実行は行っていません。

### 現在の候補

- project: 4,416件
- duplicate profileから作られた比較pair: 3,262組
- `separate_project`で除外されたpair: 18組
- 現行のduplicate candidate: 3,142組
- 現行の`auto_merge_ready=true`: 2,663組
- strict batch plan: 0組
- moderate batch plan: 470組

2,663組のほぼ全ては、同じ`authority_service_families`と`domain_identity_tokens`の組合せでした。代表例には、`publicdomainrelay.com`配下の複数collection project、同じ個人domain配下の別project、`example.com`由来projectが含まれます。moderate batch planにもPrefect本体とAT Protocol Integrationのように、同じrepository・名前・owner周辺の情報だけでは同じ編集単位といえないpairが含まれました。

`auto_merge_ready`は、それだけでバックグラウンドから自動実行される印ではありません。現行では推奨先を使うCLI executeのgateになり、Web reviewは人が実行を確認します。batch planはmutual top recommendationと別のsafe overlapを要求します。それでもmoderateでは470組が残るため、現在の名前は判断の強さを過大に見せ、実行条件としても広すぎます。

### 過去のマージ

`project_key_aliases`にある510件について、sourceとtargetの両方でマージ時刻以前の最新`investigation_runs.output_project_json`を読みました。504件は両側のsnapshotがあり、6件はtarget側snapshotがありませんでした。504件へ現行規則を当てた内訳は次のとおりです。

| 現行規則での区分 | 件数 | 504件中の割合 |
| --- | ---: | ---: |
| strict overlapあり | 18 | 3.6% |
| moderate overlapあり | 213 | 42.3% |
| broad candidateのみ | 144 | 28.6% |
| 現行signalなし | 129 | 25.6% |

過去マージは正解データではなく、snapshotもマージ操作直前のtransaction stateそのものではありません。ただし、現行strict条件だけでは手動で採用した大半を説明できず、moderateを許すと現在の誤候補が大量に入ることは確認できます。過去判断との一致件数を最大化するより、受理されたpairをどの直接事実なら短時間で再確認できるか、現在の未マージpairで何を誤って勧めるかを併せて評価する必要があります。

DateSky/Nomareは当時snapshot間で現行signalがなく、同時点の値一致ではなく改名・移転の経緯を人が判断した例です。Arabica/Oolongは共有repositoryだけで統合せず、現在は`separate_project`で保護されています。この二例は、弱い一致を増やすだけでは時間関係と共有resourceの両方を解けないことを示します。

現行`project_key_aliases`の510件は`reason=merge`だけを持ち、どのsignalを確認したか、operator判断か静的規則かを保持しません。そのため過去snapshotから理由を推定するしかありません。将来も手動マージへ説明文の入力を必須にはしませんが、自動規則を導入する場合は少なくともdecision source、policy version、成立したruleを監査可能に残す必要があります。保存先は比較後に決め、alias tableの変更を先行させません。

## 次のshadow比較

実装へ接続する前に、read-only comparatorで現行方式と本案を同じpairへ適用します。信頼policy comparatorとは判断対象が異なるためmoduleとreportを分けますが、roleと直接relationの読み取りは共有できます。

2026-08-13の実行結果は[登録プロジェクトのマージ規則shadow比較](project-merge-policy-shadow-comparison.md)に分離しています。

比較対象は次です。

- 現在の全候補と、strict/moderate batch plan
- 過去510件のマージ時点snapshot
- Arabica/Oolong、共有Lexicon authority、organization/developer共有、fork/mirror、tenant、同一service URL、同一owned NSID、DateSky/Nomare、Basker/InfoEnonなどの固定case
- 現在の「マージを勧める」「要確認」「別扱いを守る」各sample

reportでは次を示します。

- 候補数と、各候補を見つけた入口
- role上共有しにくい構成要素の衝突数
- 共有可能なresourceだけで見つかった候補数
- hard guard、時間・派生関係、独立surfaceによって抑止された候補数
- 人が省ける比較、引き続き必要な比較、新たに増える比較
- 過去マージを説明できる件数と、説明に不足するrelation・provenance
- 同じ登録起点から二重作成されたclaimと、それ以外の外部事実による候補の区別
- 現行syncで既存projectへattachされたclaim、新規projectになったclaim、新しい取込規則なら作成を避けられる重複候補の区別
- 代表的な正例、反例、判定不能例

採用基準は自動マージ件数の多さではありません。既知の別projectを誤って勧めず、現在手動で行っている重複確認の一部を根拠付きで短縮でき、判断不能なpairをその理由とともにreviewへ残せることを重視します。

## reviewが必要な判断

本案から実装へ進む前に、次を確認します。

1. 初期接続では「マージを勧める」も自動実行せず、人が確認する方針でよいか
2. 同じcurrent confirmed service URL、同じcurrent confirmed full `owned_nsid`、同じ専用project accountを、いずれも自動マージではなく優先review入口とするか
3. 共有しにくい構成要素の一致に加えて、異なるrole間の直接関係と反証なしを要求する組合せを、shadow比較する「マージを勧める」条件としてよいか
4. 同じprojectの改名・移転による重複と、別projectへの後継・統合を、通常merge reviewから時間関係reviewへ分岐させるか

合意後にだけcomparatorを実装し、正本DBのsample reviewを行います。その比較結果を見てから、現行scoreの置換、保存済み派生値の扱い、Web reviewの表示、schema変更の要否を決めます。
