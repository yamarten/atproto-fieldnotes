# 開発TODO

未完了の作業と、条件が揃った時に再検討する案を管理します。目的・優先順位は[Brief](../brief.md#開発の優先順位)、現行機能と制約は[仕様一覧](../documentation-policy.md#開発仕様)を参照してください。列挙順は実施順や着手承認を意味しません。

現在は、帰属・根拠・手動判断の責務分離を維持し、取得済み情報を候補選定と予備調査の負担軽減へつなげる段階です。情報源の拡大より選定への接続を重視します。Promotionの利用者による整理を、この改善への着手条件にはしません。追加実験・実装と正本の一括再評価は別途判断します。

- [データと実処理の検証](#データと実処理の検証): 保存契約・処理経路に残る問題と、実効果の評価。
- [選定・説明の改善](#選定説明の改善): 日常の予備調査の負担を減らすための判断。
- [条件付きの実装候補](#条件付きの実装候補): 実例や必要性を確認してから採否を決める案。
- [長期の検討候補](#長期の検討候補): 具体的な用途・条件が揃った時に再開する案。

## データと実処理の検証

未確認の挙動を、コードの実装や反映だけで検証済みにしません。保存契約と通常の処理経路を確認し、実データへの適用は検証と分けて判断します。

| 残件 | 次の確認・判断と境界 |
| --- | --- |
| 通常保存の`lexicon_count`投影 | `resolved_nsids`に値があっても`lexicons`が空のままfetch→saveすると、`state_json.evidence.lexicon_count`が`len(lexicons)`へ再投影され0になる観測がある。NSID行の消失とは別。candidate／confirmed NSIDを持つ一時DBで無変更roundtripと変更後saveを再現し、hydrate/saveの責務を確認する |
| 同じ発行元のLexicon信頼補強 | 一時DBと小規模sampleの確認までで、全体の誤採用率と省力効果は未評価。[適用条件](specs/project-boundaries.md#承認済みlexiconと同じ発行元による信頼の補強)を踏まえ、一括適用の妥当性を判断する |
| マージ済みprojectの一括調査 | 一括enrich中にmerge済みの旧project keyで失敗した観測がある。対象確定後のmergeと再開時のalias解決を一時DBで再現し、現在の実装で同じ失敗が起きるか確認する |

### 通常enrichの初回評価

[集計手順](workflows/enrich-observation-review.md)に沿い、通常enrichの保存履歴からtriage再利用・先行取得の実効果と保存・メモリ負担を評価します。導入後の初回生成と同条件で再利用可能な実行を分け、worker世代・入力条件・完走の確認を比較の前提にします。検索計画の再利用は未採用です。

### 紹介候補の再発見導線

技術候補の別入口、期限付き保留、低優先度の理由検索と各viewの速度改善は実装済みです。通常利用で、見慣れた候補に埋もれず選べるか、保留操作の負担が許容できるか、理由検索で特集候補を回収できるかを確認します。明示操作が使われず占有が続く、情報不足の上位滞留、理由検索では足りない特集作成のいずれかがあれば[選定改善](#atproto寄与度と紹介候補の順位)を再検討します。

紹介候補一覧は完全runtime viewの復元が重く、2026-09-23の複製DBでは通常一覧約3秒・すべて約5秒でした。日常利用を妨げる場合に、現在のデータと同時収集条件で再計測してcacheや推薦projectionの必要性を判断します。詳細マージ解析は別経路です。

## 選定・説明の改善

### 既存評価を日常の選定判断へつなげる

有用候補が判断に必要な時点で届いたか、人がどの確認・訂正・再調査を必要としたか、取りこぼしの原因が情報源・取込条件・帰属・説明・順位のどこにあったかを確認します。情報源評価、manual gold、紹介実績、公開Lexicon観測で答えられる範囲を分け、改善判断に必要な情報が欠ける時だけ追加観測を検討します。評価期間・記録方法は未固定で、記録負担に見合う判断が得られることを条件にします。現在値から当時の推薦を推定せず、未紹介projectを負例にしません。

### atproto寄与度と紹介候補の順位

関心の高い共有Lexicon等が候補外・後順位になる問題は未解決です。合計score順と種類別の枠配分は不採用、配点調整は一区切りとし、自動再検証・修正・自己エスカレーションも利用者の方針で保留しています。新たな判別材料、model・資料・方式の実質的な変化、既存比較では扱えない運用問題が得られた時に、[順位比較と反例](../notes/promotion-selection-quality.md)から再検討します。

使えるserviceと技術的に学べる対象の両方を選べることが目標です。service以外の比率や独自NSIDの有無だけを改善指標にせず、有用候補の捕捉と不要reviewの増減を比較します。技術候補の入口は[通常利用での効果](#紹介候補の再発見導線)を確認します。

主機能でのatproto利用を自動で区別する目標も未達です。現行の理由付き手動判断を全件annotationへ拡大せず、[意味対応の反例と再開条件](../notes/feature-collection-semantics-evaluation.md)に沿って、実利用の裏付け・Bluesky用途・利用深度・既存寄与度の責務を整理します。プロフィールだけの利用やBluesky専用操作にも技術的価値があり得ます。手動roleを完成した観測schemaとすること、読取り中心の一律減点は未採択です。

未見・根拠変化は再確認の契機に留めます。長期非採用を推薦へ使う案は、表示・閲覧の裏付けを含め未検証です。Lower priorityは個別編集・特集候補保存に維持し、独立topic管理は通常優先度や複数テーマを横断して特集を組む必要が生じた時に再検討します。

### 説明更新の品質とservice監視

再調査で終了・機能変更・誤記が分かった時に説明を適切に更新し、紹介前の確認・訂正を減らすため、通常triage内の改善と説明専用処理への分離のどちらを採るかが未決です。2026-09-23の[比較方法・結果・限界](../../tests/fixtures/project_registry/description-refresh-evaluation/README.md)では共通prompt整理案を不採用とし、説明専用案も時制改善と情報欠落の交換条件、実際の訂正負担を判断できず現行を暫定維持しています。再開時は許容できる欠落と訂正負担、新方式を試す理由、未使用素材、費用上限を定めます。既存profileの編集来歴を推定で補わず、保護と人による更新案確認を維持します。

service監視は対象・頻度・login／閲覧面の検出範囲が未決です。2026-09-15の観測では期限到来674 URLに対し、週20件では月次全巡回に不足する規模でした。ただし月次全巡回自体は確定要件ではありません。対象限定と頻度・上限変更を比較し、有効化する場合は限定試用・再評価条件を先に決めます。長期失敗率、再観測noise、review負担、連続運転は未確認です。[観測資料](../notes/service-observation-review.md)を使い、全件再調査・定期有効化は別判断にします。

## 条件付きの実装候補

### 発見経路と未確定候補の入口

有用な対象を情報源・取込条件・帰属材料の不足で保持できない実例がある時に、未確定候補の入口を検討します。パス付きURLや独立したfork等について、既存情報源の補完も踏まえ、拾える価値と増える無関係な候補を比較します。

手動の`Add seed`は入力値をその役割で採用する意味を保ちます。未確定の発見は説明で扱えるか、別の入口が必要かを判断し、候補repoを人の判断材料として読むことと、確定profileへ採用することを分けます。

### Repository analysisの誤解釈を減らす

repositoryの役割とcollection利用を根拠どおり説明できる状態を目指します。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、package配布repositoryの役割、fixture Lexiconのdefine扱いは、通常運用で頻度または実害を確認した場合だけ再開します。再開時も固定sampleの追加を既定にせず、個別repositoryや既知NSIDに依存しない一般規則を優先します。

### 信頼・帰属policyの追加接続

判断原則と現行の採用規則は[Project境界](specs/project-boundaries.md)を参照します。接続済みのREADME公開先宣言によるservice採用について追加評価する場合は、既存の検証結果で判断できない実データ上の効果や残る手動確認を対象にします。全件の再解析・再採用は別判断です。

公式repositoryや代表サイトの採用規則を広げる必要があるかは、[Repository帰属の未決例](../notes/repository-attribution-open-questions.md)でpage自体の帰属とlinkの意味を分けて検討します。以下は未完了の別課題です。

- 汎用API・AppView独自APIの実在確認。endpoint・引数・応答契約の取得方法を決めてから接続する。現在のprotocol確認はorigin rootの標準AT Protocol describeServerに限定する。
- 代表サイト（`project_site`）の自動採用。サービスを利用する画面と説明資料の区別、代表URLの選択条件を検討する。
- 以下の広いrole-aware policyやLLM relation assessorの接続。公開先宣言による限定接続とは独立した目標として扱う。

役割に応じた信頼規則（role-aware trust policy）を広げる際は、[判断モデル](specs/project-boundaries.md#判断モデル)を保ち、減らせる手動確認と増える誤採用・reviewを固定事例と実データsampleで比較します。入力契約、現行自動採用からの切替範囲、rollback条件を示して採否を決めます。

残る設計論点は、account・publisher等のrole、collector観測と意味付きrelationの保存分担、同一identifierの観測更新・既存projectへの追加・新規登録の境界です。

#### collectionの実利用と既存の採用判断を信頼確認へ使う

serviceとcollectionの対応確認を省力化するため、次を検討します。同じ発行元のLexiconによる既存候補の信頼補強は[現行仕様](specs/project-boundaries.md#承認済みlexiconと同じ発行元による信頼の補強)です。

- **表示内容と公開recordの対応**: AT URI・DID・record key・field等で、転載や汎用viewerと区別して実利用を確認できるか。OAuth権限要求は実際の作成・表示とは分ける。
- **repositoryとcollectionの定義位置**: ownership評価に依存する現行`defines_schema`とは独立した観測を確保できたら、同じprefixの別collectionの帰属に使えるか。複数project同居・vendor・test・共有schema・反対の手動判断を区別し、prefixだけで所有を広げない。
- **project/service accountのrecord保持**: 追加の確認削減が見込める場合だけ扱う低優先度案。account roleとserviceへの対応を確認し、developer個人や第三者の利用を区別する。record保持だけでschema所有を証明しない。

人が判断した正例と共有viewer・第三者client等の反例を用意できた時に、既存観測で分かる範囲と追加取得の必要性を比較し、省ける確認と誤採用の危険から採否を決めます。[関係別の保証範囲](specs/project-boundaries.md#関係別の静的規則)を維持します。

### project間の時間・派生関係

後継、買収、分割、統合、所有権移転、mirror、instance、tenant、forkは、プロジェクト間の関係として扱い、同一プロジェクト内の識別子の移転やマージと区別します。Basker / InfoEnon、DateSky / Nomareなどの実例を確認し、表示・検索・紹介文で必要になった時だけ保存形を追加します。[関係表現とreview接続の選択肢](../notes/temporal-project-relations.md#改名継承派生で決めること)を使い、既存の保存形・分離判断で足りるか、新しい関係を保存するかを判断します。

### 個別projectの判断を再検討する時

通常のレビューで同じ対象に戻った時は、過去の仮説を正解や操作承認に読み替えず、現在の資料とDBの判断記録を確認します。紹介単位、同一性、identifier処遇はそれぞれの根拠で判断します。旧マージ推奨レビューの識別子も、名前の目印だけで一括補正せず、実在・利用時期・公式な移行根拠を確認できたものを通常の編集経路で扱います。

### service URLと補助pageの分類

補助pageをservice URLへ過剰採用する実例が出た時に、ページの役割・公式な案内・過去の観測・手動判断を照合して改善します。path名だけで分類せず、認証後の利用可能性は匿名browser検証から分離します。

### 調査pipeline

実際の欠落や運用費が確認された項目だけを追加します。

- OAuth `include:` permission setの再帰解決と循環・取得失敗の記録
- repo contents、PDS、Bluesky sessionを含むcollectorのbudget / cache policy統一
- 調査履歴画面の根拠表示が、保存上限のために不足する具体例の収集
- 失敗が続く情報源へのアクセスを一時停止する仕組み（circuit breaker）を、複数プロジェクトの調査をまたいで保持すること

### 内部構造

#### DID exact lookupのcandidate hydration除去

現在は正規化した検索用データで候補を絞った後、候補projectの保存データから処理用の形を組み立て直し（hydrate）、検索用データにだけ残る候補DIDを除外します。次を満たした時だけ、検索用データのみで結果を返す方式（index-only）へ変更します。

- normalized-only `developer_did`を有効identifierとして扱うかstale rowとして除去するか決める
- current payloadから表現できない有効rowを失わない保存形またはrepairを用意する
- 既知の5 DID collisionを含む正本DB auditで期待projectとの差分が0になる
- exact DID lookupがproject hydrateを呼ばない回帰testを追加する

#### relation normalizeの性能

保存データ量が最大の調査実行記録で、関係の正規化（relation normalize）がデータ量の二乗に応じて長時間化する経路を再現できた場合、保存データの整理とは別の変更として改善します。

## 長期の検討候補

### 検索

現行のnormalized検索で不足する具体例が出たら、自然語・概念検索を優先して検討します。「ブログサービス」からblogging／publishing／CMS等を探すquery expansion、category・feature・summary evidenceの利用、SQLite FTS、typo tolerance、OR／否定条件、保存済み検索・filterのURL保持・paginationが候補です。raw JSON任意path検索は通常探索での必要例が出た場合に限ります。

### Model構成の継続評価

年数回程度を目安にmodel構成を見直し、通常enrichの品質・安定性・処理時間・常駐コストに改善の余地があるか評価を続けたいと考えています。具体的な時期と候補は未定です。通常enrichの置換候補と低優先度処理用の候補を分け、既存の[評価手順](workflows/llm-model-evaluation.md)で比較します。個別modelの[試用停止・再開材料](../notes/llm-model-evaluation.md)を踏まえ、評価結果から既定modelを維持するか変更するかを判断します。

### 保存済み判断を使うLLM適応

人が確認した判断をLLMの品質改善へ再利用する案です。当面は[非公開事例保存](specs/llm-case-store.md)で入力・出力・実行条件と判断・調整利用の来歴を蓄積します。運用上の採否や過去model出力を、そのまま解析・説明の正解labelにはしません。全件自動収集・過去資料の大量移行・学習実行は行いません。

同種の誤りが通常のprompt・入力選択の改善で減らず、用途に合う人手確認済み事例と未使用の評価対象が揃った時に再検討します。学習対象・期待結果を定め、事例参照やprompt改善と比較して、品質・処理時間・メモリ・運用費に実利がある方法を選びます。正本DBへの自動確定は別判断です。

### 編集支援の拡張

既存の編集手段では訂正負担が大きい場合に、project単位の自然言語指示とprofile／evidenceによる更新案、name・tagline・summary・features・website・app links等への編集範囲拡張を検討します。人が根拠と意図を確認できるよう、field単位の差分preview、採用指示・生成元の来歴、revision checkを含めて判断します。

同種の個別訂正を通常triage promptの恒久ruleへ増やしません。identifier、relation、decisionの正本を直接JSON編集させず、既存review actionまたは専用mutationを使います。

### Web UI

- 日常フローと安全境界だけを案内する短いHelp導線
- 外部情報の取り込み・追跡状態レビューを、保存した進行状況（checkpoint）から再開すること
- サーバー再起動後のバックグラウンドジョブ一覧・進捗eventの復元
- 保存上限付きの根拠では判断材料が不足すると確認された場合の表示上限調整
- 同じ識別子の一つのroleを否定する操作で、残したい別roleまで失わないためのrole単位identifier decision。別々に採否判断したい実例がある場合に、操作の説明・表示で足りるか、保存契約を変える必要があるか検討する
- 詳細の前後移動とtab jumpに限ったkeyboard shortcut
- read-onlyのrelation / ownership graph

PWA / local app化は、Firefox中心の運用でもstandalone表示や軽いreload耐性に実利があり、manifestと単純なservice workerで済む場合だけ再評価します。オフラインDB操作、build step、複雑なcache invalidationは導入しません。

### 保存量とmigration

| 着手条件 | 検討する変更 |
| --- | --- |
| DB allocated bytesが10 GiBを超える、またはmaintenance競合を実測 | history DB分離 |
| checkpointだけではjob履歴が不足 | event永続化とretention policy |
| 次のschema改版が具体化 | incremental migration runner |
| 複数DBまたは同一schema内のdata policy世代差が必要 | DB-level policy marker |

process間writer競合の現行保護は[保存transaction](specs/project-record.md#更新番号と保存transaction)を参照します。

DB-level policy markerを追加する場合は、open時に無条件付与せず、markerなしDBのread-only audit合格後にadditive updateします。現状の単一正本DBとoffline maintenanceのためだけには追加しません。

### GitHub公開の準備

公開対象はmasterのsourceとその到達履歴に限定し、公開時期は未定・優先度は低いままです。合意した範囲の履歴監査・環境固有情報の整理、HEADで読める文書への整理、設定例・MITライセンス・第三者素材の表記は済んでいますが、公開準備全体の完了や安全性の保証ではありません。公開を判断する前に、次を詰めます。

- 公開時点のmasterと到達履歴に、credentialや公開対象外データが含まれていないか最終確認する。master外のbranch・tag・jj参照や物理objectの清掃は未実施であり、公開対象へ混ぜない。対象範囲を広げる場合は追加監査する
- 追加の履歴除去が必要になった場合だけ、影響範囲、履歴書換え、既存cloneやbackupの扱いを決める
- READMEのサンプルと利用ガイドを、新規環境の利用者がCodexなしで試せるか確認する。既存環境での画面・コマンド確認と、依存導入からの初回利用は区別する
- sample data・設定例を使った初回利用、依存物の帰属、securityとsupportの境界を公開repository向けに確認する

DB共有はsource公開とは別に扱います。共有先・用途が決まった時に新しい論理exportを作り、その時点の収集資料に含まれる認証情報候補や署名・招待URLと、履歴内の複製を確認します。正本DBの一律削除や汎用匿名化基盤は導入せず、調査履歴・判断は原則共有可能とし、recordの`visibility: private`だけでは一律除外しません。

ここでいうGitHub公開はsource repositoryの公開候補です。現在のWeb UIをインターネットへ公開することや、複数利用者向けサービスにすることは含みません。

### データmodelの拡張

developer node、provider account、native appのbundle / package / store identifierは、既存DID、handle、identifier role、relationで表現できない実例が確認された場合だけ追加します。

### 用途・着手条件の具体化待ち

cache／source budgetのread-only表示、plugin-style collector configuration、Investigation notebook、Assisted review、LLM triage side panel、multi-project compare、review session、bulk maintenance dashboard、source health dashboardは候補名までで、採用・実施順は未決です。通常利用のどの負担を減らすかを具体化してから判断します。

static JS／CSS／route tableの分割は保守上の問題が出た場合に、security／safetyはLAN・個人利用・必要時Tailscale越しの前提に応じて再評価します。
