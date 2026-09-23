# 保存済みデータによるPromotion選定品質の評価

2026-09-23。現行Promotionには、紹介対象の価値より技術評価の閾値・利用面の確認・tracking stateが強く効く例があります。通常の確認対象448件の先頭16件はすべて技術候補で、独自Lexiconを持つJuntosは348位、Community Lexicon Calendarは179位でした。`stable`の候補11件も既定一覧から外れます。利用者は一般方針としてstableも含めることを了承しました。観測例のStreamplaceは紹介済み扱いでもよい例外と確認されたため、代表的な取りこぼし例には数えません。これは現行出力の食い違いを示しますが、原因を並び順だけに帰すことはできません。後続の点検と承認された保存データ比較では、古い技術評価の較正で入れ替わる候補がある一方、共有Lexicon等の後順位は残りました。技術優先を外して既存合計点だけにすると、根拠のある基盤候補が末尾へ落ちます。さらに、利用範囲の未評価を補うだけで現行規則のまま解消する対照もありました。評価・入力の不足と順位規則自体を区別する必要があり、その後の実LLM9件×2回ではCobaltの訂正だけが通常候補の出入りを変え、Konbiniの取りこぼしと共有仕様の評価揺れが残りました。単純な再評価だけを改善策として採用しません。追加の原文中心の分類案はreasoning noneでJuntosの誤分類が残り、順位への採用を見送りました。同じ5件・同じpromptのnone／low各2回比較では、lowでCobaltの技術閾値とJuntosの対象分類が改善し、Laplan・standard.siteの主要評価も保持しました。一方、Konbiniのhigh／notableは揺れ、根拠外の説明も残ります。所要時間の中央値は10.746秒から127.607秒となり、利用者はこの時間増から推論ONのデフォルト採用を除外しました。deep・個別確認への限定利用も追加比較しましたが、同じ2件では推論ONの優位は確認できず、再生成した要約への誤りの再混入が残りました。主張ごとの根拠不足を示す補助用途と文章の自動修正を分け、後者は採用していません。後続の実サーバー確認ではreasoningの対応値はoff／onだけであり、以前のlowは要求値であって「低い推論強度」を実証したものではありません。入力容量・出力契約の整合は修正候補として残しています。具体的な推薦方式の採用は未決です。全体の捕捉率やノイズ率を測った結果ではありません。

受理方式はhuman-reviewです。利用者は今回の対照で「共有Lexicon ＞ Juntos ＞ クライアント」を確認し、Juntos自体が特に高優先なのではなく、クライアント、特にBlueskyクライアントの優先度が低いと明確にしました。未紹介のstableも通常候補に含め、stalledは通常候補から外す一般方針も了承済みです。技術評価の較正と順位への利用を分ける追加比較を実施しました。仮説比較に続き、承認された実LLMの限定評価を実施しました。結果は利用者レビューと送信承認を経て母艦へ統合済みです。以下の事実・合意・Codexの推奨を区別します。推薦規則・prompt・正本DB・本番Webは変更していません。

## 目的・方法・境界

目的は、紹介したい対象がどこで見えなくなるか、上位を何が占めるかを追い、改善する場所と優先対象を選ぶことです。[brief](../brief.md)の暫定基準と[実用上の評価原則](../developer/workflows/llm-model-evaluation.md#実用上の評価原則)を使いました。説明の完全性、全件の採点、新規収集・DB訂正は対象外です。LLM生成は各節に記した了承済みの限定比較に限ります。変更対象はこのnotesとstatusの参照だけで、briefとroadmapは母艦が管理します。

- 基準コードは`84bcf4d0eee28d805a6a06893568fc86157cca39`。作業開始時はdetached HEAD、未commit変更なし。
- 正本`data/survey.db`を`mode=ro`、`PRAGMA query_only=ON`、明示read transactionで読みました。2026-09-23 03:32:52〜03:33:16 JSTに一覧を計算。GB単位の複製は作っていません。
- 全5,054件を通常の`project_record.list_projects`で復元し、通常の`build_showcase_queue_from_projects`で各viewを計算。Webと同じapplication `queue_payload`の既定条件による先頭50件と一致しました。最初のDB復元とAPI照合は同じread transaction内、その後の各viewは復元済みの同じ入力から計算しました。
- 既定条件は`active`、`inbox`、個人用ツールを含めない、理由検索なし、50件。利用者のbrowser localStorageの現在設定や画面操作は確認していません。`all`、全tracking stateのinbox、個人用を含めるinboxは診断用の別条件です。
- 順位を見る前から選べる対象としてmanual goldの紹介好例5件、判断例14件、保存された紹介済み501レコードを照合しました。追加で、保存説明から共有Lexicon・AppView関連・インフラの対象を探索し、通常上位50件の名前・説明・signalを読みました。重要な対照は計30レコードの保存原文・schema・identifier・状態を別の短いread transactionで確認しました。
- 紹介済み501レコードは108投稿URLにまとまります。レコード数を独立した紹介実績数とはみなしません。紹介実績は旧来の関心と複数レコードへの紐付けを含み、暫定基準の完全なgoldではありません。
- 既存LLMの説明・技術評価・分類は探索の手掛かりです。原文を確認したものも、人間による新たな価値判断ではありません。未紹介を負例にせず、現在値から過去の推薦精度を推定しません。

## 通常経路と診断条件

| 条件 | 件数 | 意味 |
| --- | ---: | --- |
| 確認対象・既定条件 | 448 | 普段の入口をコードの既定値で再現 |
| 技術候補・既定条件 | 16 | 通常先頭16件と同じ集合。専用view内の順序は技術scoreによる |
| 保留 | 0 | 現時点のactiveな保留なし。保留導線の一般的有効性は未評価 |
| 低優先度・特集候補 | 1 | QTE。「別途アカウント登録が必要」という判断 |
| すべて | 502 | 全tracking stateの現行signal候補と保存判断の集合。全登録projectではない |
| 全tracking stateのinbox（診断） | 501 | 既定より53件多い（stable 11、stalled 42）。低優先度は含まない |
| 個人用を含めるactive inbox（診断） | 503 | 既定に55件追加 |

全体のtrackingはactive 3,382、stable 328、stalled 1,276、ignore 48、out_of_scope 17、archived 3です。表のstalled 42件は全1,276件のうち、tracking stateの制限だけを外すとinboxに入る候補数です。signalがあり、紹介済み・保留・低優先度など他の条件を通る部分集合で、残り1,234件を低関心と判定した数字ではありません。候補signalを持つことと、通常viewで表示されることは別です。全件で主機能の利用範囲は手動未評価でした。

個人用を含めた診断ではScott Polhemus Personal Blogが25位、Hawlt Noteが37位に追加されました。個人用途を別扱いにする現行filterは機能しています。55件をノイズや誤除外と評価したわけではありません。技術signal自体はpersonal_toolを一律除外する規則ではないため、「個人用を含めない」が全経路に共通する除外とは扱いません。

## 見たい対象側からの照合

### 人が選んだ紹介好例

manual goldの`showcase_reference_cases`5件は、名前だけでなくURLと所有identifierを合わせて照合しました。同名の別レコードを代用していません。

| 対象・project key | 保存状態 | 紹介済み除外を無視したsignal診断 |
| --- | --- | --- |
| Bluesky `proj:31e73fb1f3eb` | 紹介済み・stable | service要確認。技術評価notable |
| rolld `proj:202bc97fd2de` | 紹介済み・active | service確認済み＋DNS経由Lexicon公開 |
| standard.site `proj:cf79163aecc6` | 紹介済み・stable | DNS経由Lexicon公開。技術評価notable |
| idiolect `proj:1c7a9313031b` | 紹介済み・stalled | signalなし。service URLなし、DNS解決記録なし、技術評価notable |
| meyasuat `proj:125e44eebd53` | 紹介済み・active | service確認済み |

5件とも通常非表示は紹介済みとして妥当です。inboxへの復帰は推奨しません。ただしidiolectは、紹介済み条件とtracking条件を外しても現在のsignalだけでは拾えません。保存READMEには署名付きレコード、schema間翻訳、コミュニティによるschema管理があり、manual goldはサービスなしでも技術紹介対象としています。単なる説明欠落ではなく、保存された`notable`と技術入口の`high`閾値の隔たりがあります。`resolved_nsids`とschema sampleはあっても、DNS公開signalに必要な解決記録は0件です。

紹介済み501件のうち、表示除外を無視してsignalを計算すると326件にsignal、175件にはありません。後者はproject 78、personal_tool 23、infra_tool 19、name_only 16、lexicon 15、library 15、reject 9件です。この数字は診断対象の規模であり、取りこぼし175件や捕捉率65%という意味ではありません。稼働終了、分類、断片、個人用途等を個別に判定していません。

### 既存の判断例と現在の値

manual goldの14判断例は固定fixtureとして全14件と期待順が現行関数で一致しました。しかしfixture内では低優先度の判断を明示適用しているため、この成功は現在DBにも同じ判断が保存されている保証ではありません。

| 対象 | 現在の到達性 | 診断 |
| --- | --- | --- |
| Echosky `proj:2f765b110642` | 通常49位、priority 7、269点 | 候補に残す期待に一致。実装不完全という過去文脈を、現在の確定した不具合にはしない |
| Juntos `proj:0edac60136a4` | 通常348位、priority 4、134点 | 候補には入るが既定50件では見えない。詳細は下記 |
| math.r `proj:edb76ad07967` | 通常25位、priority 7、422点 | fixtureはゲーム記録の共通パターンとして低優先度。現在DBの低優先度判断はなし。service＋DNS公開が加点される |
| gmstn.systems `proj:835b06c25a8a` | 通常342位 | fixtureはlanding-onlyとして低優先度。現在は低優先度判断なし、service要確認で後順位。現在の未確認状態から実装なしとは断定しない |
| Moat `proj:3efb7d27c3ef` | stalled、通常非表示。「すべて」7位 | fixtureのHTTP URLと現在のHTTPS URL・NSIDを照合。現在は技術high、CLI等の保存READMEあり。過去のlanding-only期待を無条件に再適用しない |
| atShare `proj:59a2515d44f6` | activeだが全viewで候補外 | 現在はlibrary、service URLなし、技術notable。READMEのWeb componentという内容と対応。低優先度判断の有無だけの問題ではない |
| Bluesky Comments／Hangar／hughlou／Lexicon Community／lastfm.blue／skycle.app／Skystack／ToolShed | 8件ともout_of_scope、通常非表示 | 過去の明示した対象外判断と一致。共有Lexiconへの関心だけを理由にLexicon Communityを自動復帰させない |

math.rの差は人の既知判断と現在の順位の不一致ですが、過去判断が消失したという原因までは確認していません。fixture作成時の文脈や、現在のleaderboard schemaを含む対象の変化を踏まえ、現在も低優先度かを再確認する対象です。

### 保存根拠から選んだ追加対象

| 対象・根拠 | どこで見えなくなるか | 評価の位置づけ |
| --- | --- | --- |
| Streamplace `proj:6674c3d29053`。公式repo READMEはAT Protocol上のlive video、Web・download・`place.stream.*`を記載 | stableなので通常外。「すべて」22位。紹介済み・低優先度・保留ではない | 利用者は紹介済み扱いでもよい例外と確認。代表的な未紹介対象の取りこぼしには数えない。stable除外の動作例として保持。DBの紹介済み状態は変更しておらず、現在の実稼働も再取得していない |
| Community Lexicon Calendar `proj:d35d6f719f84`。DNS公開と`event`／`rsvp`のschema | 通常179位、priority 7、205点。技術view外 | 共有LexiconをJuntosやクライアントより先に確認したい合意との不一致。schemaはイベントと参加意思を定義。個別の紹介可否は未確定 |
| Juntos。保存confirmed repoに`app.juntos.channel`の「利用者repoに保存するchannel record」、channel API、Live Demoの記述 | 通常348位。DNS公開signalなし、技術notable、serviceはHTTP 200だけ | サービスが使えるという証明は不足しているが、独自Lexiconと用途は保存根拠にある。クライアントより先に見たい対象だが、特別な高優先例ではない。関心と稼働確認を一つの優先度へ押し込める不利 |
| Konbini `proj:0db4f693c077`。保存READMEはFriends of Friends向け部分index AppView、Docker Compose、選択backfill | activeだがsignalなし。「すべて」にも出ない。service none、技術notable、DNS公開なし | 分散性に関わる実装として調べる余地。公開代替インスタンスの稼働まで確認した例ではない |
| Blacksky AppView `proj:2c69d06edf95`。保存READMEは`api.blacksky.community`を支えるforkとRust indexerへの置換を明記 | stalledに加えてsignalなし。trackingだけ外しても出ない | 代替AppViewの具体例。ただし別のBlackskyレコード`proj:cef4c9185f74`は紹介済み。同じ紹介単位か未確定で、未紹介対象の取りこぼしとはまだ数えない |

HappyViewも保存説明ではLexicon駆動AppViewとして見つかりましたが、GitHub側に紹介済みレコード、Tangled側に未紹介の複数レコードがあります。Glean、Leaflet、標準Lexicon等にも同名・関連レコードがありました。これらを別々の新規高関心対象として数えると取りこぼしを水増しします。通常ロジックへの固有名特例、今回のmergeや再分類は提案しません。

## 上位を占めるものと逆転

先頭50件のsignalは重複込みで技術16、service確認済み29、service要確認7、DNS公開21です。categoryはproject 43、infra_tool 4、library 3、lexicon 0でした。DNS公開21件を共有Lexicon21件と読み替えません。`category=lexicon`の最初は131位の`schema`です。signalがDNS公開だけの候補は113位のNeoDBからで、いずれも既定50件には届きません。

先頭16件は順にy-pds、Spindle、Cospan、Doom over AT Protocol、Cistern、freeq、Glimpse、Fortransky、device-atproto-link、OCaml版atproto、Laplan、edgebox、zkat、SSRF Probe、Cobalt、hydrantです。この集合全体を低関心・ノイズとは評価しません。LaplanのAPI合成やhydrantのindex基盤など、具体的な技術的仕組みを原文で確認できる内容も含みます。y-pdsの保存理由はWebRTC接続交換を述べますが、追加で確認した原文にはその仕組みがなく、根拠不足として分けました。Spindleには市場のbid／receipt／agent skill等のschemaが保存されていましたが、名称・schema集合だけから運用中のサービスを保証しません。

順位の主要因は`top_priority`を合計scoreより先に比較することです。現行通常448件はpriority 8が16件、7が313件、4が119件で、まずこの層に分かれます。

| 対照 | 保存根拠・現在の位置 | 判断 |
| --- | --- | --- |
| Fortransky 8位／Cobalt 15位 vs Coves 17位 | 前者は技術high・priority 8、96／94点。Covesは独自のcommunity recordとservice確認済み・priority 7、446点 | スコア94点が446点に勝つのは実装どおり。珍しい言語・端末のクライアントと独自Lexiconサービスのどちらを先に見るかという大きな価値差を、保存技術labelが支配する |
| 同じ前者2件 vs Calendar 179位／Juntos 348位 | CalendarはDNS公開160点が中心。Juntosはservice要確認100点が中心。技術notableには技術加点も入口もない | 利用者が確認した順は共有Lexicon ＞ Juntos ＞ クライアント。Juntosを高優先サービス一般の代表にしない。現行のクライアント優先はこの相対順と逆転しており、数点の小さな並替えとは異なる |
| math.r 25位 vs Echosky 49位／Juntos 348位 | math.rはservice200＋DNS公開160、Echoskyはservice200、Juntosはservice100。NSID hint等の少数点では逆転できない | 既存goldの相対評価と逆転。Lexiconがあることは識別・技術の存在証拠であり、それだけで独立紹介の関心を保証しない |
| Echosky 49位 vs 近傍のSifted 50位・atmo-events 51位 | 同じpriority 7、269／267／264点 | この小差だけでは欠陥と断定しない。今回、相互の人間優先度は確定していない |

Cobaltの保存READMEは「Wii U向けnative client」と同時に実機で未実行と述べ、進捗表には実機起動doneもあり内部に矛盾があります。FortranskyはFortran TUI、Rust・assemblyによるfirehose decode等を説明します。当初はこの対照を並び順の問題として主に評価しましたが、利用者の指摘を受け、high判定そのものの妥当性を先に切り分ける必要があると訂正しました。新規性を否定したりBlueskyクライアントを一律除外したりしません。

## 技術評価の基準・保存判定・順位を分ける追加診断

利用者から、上位へ固定する規則だけでなく、技術highの判断基準が不適切である可能性も指摘されました。これは当初依頼の「評価signalと並び順のどちらが効いたか」に含まれます。先の調査は順位への作用を確かめたものの、保存判定の較正を十分に調べず、順位変更を先に推奨していました。改善方式の採否を決めた段階ではありません。

[現行triage基準](../../prompts/project-triage.md)は、highを「具体的なprotocol・相互運用の仕組み、または再利用できる基盤実装の学習価値」、notableを「そうした仕組みのない通常のrecord保存・API操作・言語選択」とします。技術評価は紹介準備やサービス利用可否と独立し、クライアントでも具体的な学習価値があればhighを許す設計です。「珍しいからhigh」だけを許す現行基準ではありません。

Git履歴ではこの具体的rubricは`3636199f`（2026-09-22 16:29 JST）で追加されました。それ以前は具体的根拠を必要とすることと理由enumの指示はありましたが、highとnotableのこの区別はありませんでした。後続`603c0792`も確認しました。現在のpromptだけで古い保存判定の正誤やmodelの指示遵守を評価しません。

正本を通常権限の`mode=ro/query_only`、read transactionで8件だけ再読し、`last_llm_review_at`と保存`llm_output.technical_interest_level`を確認しました。8件とも現在のlevelと保存LLM出力が一致し、最終LLM評価はrubric追加前でした。Cobalt・hydrant・Konbiniは9月19日、Fortransky・Calendarは9月17日、Laplanは9月16日、standard.siteは8月11日、idiolectは8月10日（以上UTC）です。最終enrich日時は最終LLM評価日時と同じではありません。過去requestの全文promptと入力を復元したものではなく、後から蓄積した原文が当時LLMへ渡ったかは未確認です。

| 対照 | 保存原文・理由からの診断 | 残る判断 |
| --- | --- | --- |
| Cobalt: high | 理由はWii Uの2画面とhomebrew実装。読んだREADME抜粋にはprotocol上の工夫や再利用できる基盤の具体的lessonが示されず、現行rubricに対するhighの根拠は弱い | 過大評価の有力な疑い。抜粋外まで含めた不存在は断定しない。現在のmodelで同じ評価になるかは未検証 |
| Fortransky: high | 言語選択だけでなく、CAR・DAG-CBORのdecode、Rust bridge・assembly decoder・event変換の構成が保存される | 「Fortranだから」の過大評価か、再利用できるprotocol実装の学習価値かが境界。クライアントへの低関心だけで技術levelを下げない |
| Laplan: high | Petri netによる型の入出力関係とBFSによるAPI経路合成がREADMEに具体的にある | 現行rubricに沿う有力な正の対照。上位の技術候補すべてを誤判定とは扱わない |
| hydrant: high | 原文にfiltered indexing、XRPC、ordered stream、relay、libraryとしての利用例がある | 再利用可能な基盤としてhighの根拠はある。一方、保存理由の「標準XRPC以外」は原文の「com.atproto.*以外も扱うXRPC」とずれ、理由文の誤りとlevelの誤りを分ける |
| idiolect: notable | 保存READMEは署名付きrecord、schema・翻訳・コミュニティによる相互理解の設計を説明する | 現行rubricでは過小評価の疑いが強い。既存較正素材のhigh期待とも整合するが、その短い構成素材を実資料の人間goldとはしない |
| standard.site: notable | 既存較正素材は共有schemaによる相互運用をhighと期待する。今回保存repoにはJekyll連携の説明も混在する | プロジェクト本体と連携実装の根拠の境界を確かめる必要があり、較正素材の成功だけで現在レコードを評価済みにしない |
| Calendar: notable | 保存schemaはevent・RSVPの共通表現を示す | 共有Lexiconとしての紹介関心は合意済み。ただしschemaの存在だけで技術highとは限らず、相互運用の具体的仕組みの根拠は別に見る |
| Konbini: notable | 保存READMEは部分index AppView、選択backfill、複数upstreamの仕組みを示す | 再利用できる基盤実装として過小評価の可能性。開発初期であることと技術学習価値を分ける |

この時点の診断はCodexによる保存根拠との照合で、新しい人間goldやLLM再判定ではありません。現行基準は過大・過小の両方を減らす意図を既に持ちますが、基準自体が十分か、modelが実資料で守れるか、既存データへ適用すれば逆転がどれだけ減るかは未確認です。この段階では新規生成・正本再評価を行っていません。後続の限定生成は後節に分離します。

この切り分けを進めるGOを受け、以下のメモリ上の比較を実施しました。利用者がクライアントを低優先とすることを、技術評価を目的に合わせて下げる理由にはしません。

## 技術評価だけを変えた比較と順位変更の診断

2026-09-23 12:15:46〜12:16:08 JST。新しい単一read snapshotの5,054件を通常復元し、通常application先頭50件との一致を確認した後に接続を閉じました。原文確認は技術候補16件すべてと、見たい対象・対照8件の計24レコード、後述の利用範囲の診断で追加したSkySweeperの計25レコードです。以下はCodexの仮説を手で技術fieldへ入れた感度比較であり、現行LLMがこの判定を返すという予測、人間gold、採用済み訂正ではありません。実装や根拠の真偽を外部で検証したものでもありません。

前回03:32の448件と今回の448件では全体の順序は一致せず、同じ先頭50件内でもLibrary Sky等の小さな順序差がありました。先頭50件の集合、技術16件の順位、下表の主要対照の基準順位は同じでした。旧順位を条件間比較へ流用せず、すべて新snapshotのbaselineと対にしています。今回の基準は、全条件計算後に再計算して全件の順序・点数一致を確認しています。

### 16技術候補の原文点検

| 原文からの判断 | 対象 | 根拠・境界 |
| --- | --- | --- |
| highを支える具体的な仕組みがある（11件） | Spindle、Cospan、Doom over AT Protocol、Cistern、freeq、device-atproto-link、OCaml版atproto、Laplan、edgebox、zkat、hydrant | schemaの入札・受領関係、構造化VCS、PDS経由のゲーム入出力設計、暗号化メモの受渡し、DIDをIRC接続へ結ぶSASL、attestation chain、ランタイムに依存しないprotocol library、API経路合成、PDSとWasm実行の分離、署名MSTのZK検証、index／relay基盤。設計としての説明根拠であり、記載された実装の完成・正しさ・稼働は未検証 |
| high／notableの境界（2件） | Fortransky、Glimpse | 前者にはdecode pipeline、後者にはlocal DB・推論・ATProto objectの処理構成がある。単なる言語・製品の珍しさより具体的だが、再利用できる基盤のlessonに相当するかが境界 |
| highの根拠が弱い（1件） | Cobalt | 端末と2画面の構成を中心とした理由。今回読んだ原文にはprotocol／相互運用の仕組みが見えない |
| 今回の保存原文では裏取り不足（2件） | y-pds、SSRF Probe | y-pdsは共同編集・ATProto syncというmetadataと静的HTMLまでで、保存理由にあるSDP交換を確認できない。SSRF Probeには今回抽出したrepo原文・schema・支持するservice本文がない。過去履歴やrecord sampleを含む全根拠不存在とはせず、仮説比較でも据え置く |

この16件を一括して低評価にする根拠はありません。逆に、技術reasonの文面が具体的であることだけでhighを受理することもできません。理由enumの`unusual_architecture`だけでは、Cobaltと有力な基盤を区別できません。

### 比較条件

Aは保存値のままです。Bは根拠に比較的沿う較正仮説で、Cobaltをhigh→notable、Konbini・idiolect・standard.siteをnotable→highとしました。standard.siteはプロジェクト本体の保存service説明に共有出版schemaがあり、連携repoの検証artifact説明を分けて読むことでhighの仮説を置きました。Bではlevelとその理由文、levelから通常算出される技術scoreだけを変更しました。既存の理由enumは保持し、同一性、分類、紹介済み、service、DNS公開、判断履歴等は変えていません。

CはBに加えて境界2件のFortransky・Glimpseをnotableに下げます。DはBに加えてCalendarをhighに上げます。CとDは境界判断に対する感度を見る別仮説で、一括採用する案ではありません。Calendarの紹介関心が高いことは合意済みですが、技術highは合意済みではありません。定義の存在だけでhighにする判断は今回も避けます。

| 条件 | 通常候補数 | 先頭50件内の技術候補 | 候補集合の差 |
| --- | ---: | ---: | --- |
| A: 現行active | 448 | 16 | 基準 |
| B: 較正仮説、active | 448 | 16 | Cobaltが外れKonbiniが入る |
| C: B＋境界クライアント2件をnotable | 446 | 14 | BからFortransky・Glimpseが外れる |
| D: B＋Calendarをhigh | 448 | 17 | Bと同じ集合、Calendarの順位だけ変化 |
| A＋stable | 459 | 16 | stable 11件追加 |
| B＋stable | 459 | 16 | stable 11件追加と、Bの入替え |

| 対象 | A: 現行 | B: 較正 | C: 境界クライアント低下 | D: Calendar high |
| --- | ---: | ---: | ---: | ---: |
| Cobalt | 15 | 候補外 | 候補外 | 候補外 |
| Konbini | 候補外 | 8 | 7 | 9 |
| Fortransky | 8 | 9 | 候補外 | 10 |
| Glimpse | 7 | 7 | 候補外 | 8 |
| Community Lexicon Calendar | 179 | 179 | 177 | 3 |
| Juntos | 348 | 348 | 346 | 348 |
| SkySweeper | 52 | 52 | 50 | 53 |
| Coves | 17 | 17 | 15 | 18 |
| math.r | 25 | 25 | 23 | 26 |
| Laplan | 11 | 12 | 10 | 13 |
| hydrant | 16 | 16 | 14 | 17 |

idiolectとstandard.siteはhigh仮説で技術signalを得ますが、全条件で紹介済み除外を維持しました。診断上の入口は改善しても、通常一覧の新規有用対象が2件増えたとは数えません。Cobalt等は技術signalだけだったため、notable化では低優先度一覧へ移らず、候補から消えます。技術評価の訂正と、紹介の優先度を下げる操作は別の効果です。

stableを含める条件も独立させました。B＋stableではCalendar 183位、Juntos 355位で、候補数増によって後ろへ動きます。合意済みの表示範囲拡張は到達可能な対象を増やしますが、既存対象の優先順を改善するものではありません。Streamplaceはこの条件で17位ですが、既紹介扱いでもよい例外という利用者判断を維持し、主要な救済実績として数えません。

### 技術優先を単純に外せばよいか

Bの同じ448候補とscoreを使い、`top_priority`を外して既存合計score降順・project key順だけにする診断も行いました。これは並び替えだけの一時比較で、通常関数・設定は未変更です。

| 対象 | Bの現行並び順 | 合計scoreのみ |
| --- | ---: | ---: |
| Coves | 17 | 1 |
| math.r | 25 | 9 |
| Community Lexicon Calendar | 179 | 165 |
| Juntos | 348 | 335 |
| Konbini | 8 | 440 |
| Laplan | 12 | 444 |
| hydrant | 16 | 448 |

先頭50件の技術候補は16→2、category=lexiconは0のままでした。サービス200点やDNS公開160点に対し技術highは70点なので、priorityだけを外すと、根拠のあるインフラ・技術候補も末尾近くへ落ちます。この単純変更は今回の価値基準を満たす改善案として推奨しません。技術候補viewは残せても、通常一覧のこの差を改善済みとは扱えません。

### 未評価の利用範囲を補うだけで直る対照

技術high以外の原因を切り分けるため、既存の低優先クライアント対照SkySweeper（`proj:b749205083ba`）を追加確認しました。保存された公式ページ説明は「Blueskyの古い投稿を自動削除」で、技術評価は既にnotableです。それでもservice確認済みのpriority 7で52位となり、service未確認のJuntos（priority 4、348位）より前です。技術highの較正だけではこの逆転は変わりません。

ただしこれは直ちに順位規則の新設を要求するものでもありません。通常規則には既に`atproto_usage.role`による優先度調整がありますが、保存値は全件未評価でした。2件だけをread-onlyで取得し、メモリ上で通常の`atproto_usage.set_override`と候補builderへ通しました。保存やユーザーの判断登録はしていません。仮説のためにoperator経路を使ったもので、実際の人間判断ではありません。

| 対照条件 | SkySweeperのpriority／score | Juntosのpriority／score | 2件の順 |
| --- | --- | --- | --- |
| 両方unknown（保存値） | 7／264 | 4／134 | SkySweeperが先 |
| SkySweeperだけbluesky_client | 5／144 | 4／134 | SkySweeperが先 |
| 上記＋Juntosをmain_data | 5／144 | 6／194 | Juntosが先 |

Juntosの仮説は、保存confirmed repoがchannelを利用者repoに保存するrecordと説明することに基づきます。実稼働を確認した判定ではありません。比較はこの2件の通常関数による順序で、全体順位を再計算した結果ではありません。技術level、service確認度、紹介済み・trackingは維持し、元入力の不変を確認しました。

この例では必要な差を表現する順位規則が既にあり、入力がないことが効いています。一方、この利用範囲は現在手動判断だけで、以前のLLM実験は意味の誤りにより通常採用されていません。全件の手動判断をユーザーへ求める解決にせず、保存情報から自動で信頼できる入力を作れるかは別の未解決課題です。また、共有Lexiconは実装済み主機能のmain_dataと同一ではなく、このroleだけで紹介関心全体を表現できると保証しません。

### 比較から言えることと次の判断

- **較正で解消しうる部分はある。** 過大評価による上位占有と、過小評価による候補外を、順位規則を変更せず入れ替えられます。ただし今回の入替えは仮説の効果であり、現行LLMで再生成すれば同じになるという検証ではありません。
- **候補の較正だけでは残る問題がある。** 境界クライアントを2件下げても、Calendar・Juntosは2順位上がるだけです。Calendarを技術highと見れば3位になるため、その対象の後順位がどの層の問題かは技術判定にも依存します。ただし共有Lexiconへの紹介関心を反映するためだけにhighを付ける設計にはしません。
- **順位規則の変更が必要かはまだ一括で決められない。** SkySweeperとJuntosは既存role入力の補完で逆転が解消します。共有Lexiconのように、技術highや実装のmain_dataと別の紹介関心をどう扱うかは残ります。先に較正と既存入力の充足を評価し、その後に残る差に対して順位の対案を比較する順が妥当です。すべてのhighを弱める案や、すべてのschemaを上位へする案の支持にはなりません。

次の具体案を選ぶなら、まず現行rubricで実際に較正できるかと、保存情報から共有仕様・独立した用途・クライアント等をどこまで区別できるかを確認します。既存入力を補っても残る差について、技術的に有力な対象を保った順位の対案を比較します。現状の`category`や`lexicon_scope`をそのまま正解とせず、全件未評価の利用範囲を人が埋めることも前提にしません。具体的な配点、追加判定field、生成方式は未採択です。今回の比較は、方式の選択に必要な原因の切り分けであって、推薦方式全体の品質合格ではありません。

今回の検証では、通常applicationとbaseline先頭50件の一致、通常builderによる全候補比較、変更fieldの限定、選定した対象の元入力不変、最後のbaseline再計算による全順序・score一致を確認しました。query_only接続を閉じてから仮説を適用し、saveや外部通信・LLMを呼んでいません。raw出力は`.tmp/promotion-quality/counterfactual-results.json`、単純sort診断は`sort-diagnostic.json`にあり、比較中の確認用として未commitです。前者のSHA-256は`c37767bc3ac83ac7d94e0179eedaca41958a9a6ca1b372d923e0db18a1c6bd17`。24件の保存原文は`calibration-material.json`（`6ff8264363ddfec9645b8ffcac1327f1bca14a716362bc9a9ec67bfc4e163d48`）に限定保持しています。恒久的なCLI・機能は追加していません。

## 現行LLMによる実資料の反復評価

2026-09-23。利用者の続行指示を受け、現行triageと既存model suffixで10対象の入力を準備し、生成可能な9件を各2回評価しました。結論は、**現行model・promptでの単純な再評価だけを選定改善策として採用しない**です。Cobaltの過大評価は訂正できましたが、Konbiniの取りこぼしは残り、standard.siteのhigh／notableは同一入力で揺れました。入力準備・理由文・出力fieldの契約にも別の問題があり、LLM精度だけの問題にはまとめません。service加点についての途中状態の診断は、後続の最終処理確認で訂正しています。

### 実行条件と境界

正本から対象を`mode=ro/query_only`のread transactionで取得して接続を閉じ、通常の`build_triage_prompt`で固定入力を作りました。通常更新に合わせて既存profileを含めています。前回のCodexによる較正仮説や期待順位はpromptへ追加していません。repository解析結果は保存済みのものを使い、repository解析自体の再生成・公開ページの再取得はしていません。

設定済みQwen `qwen/qwen3.6-35b-a3b`、Responses、temperature 0.2、reasoning要求none、最大出力2,048 token、timeout 180秒、既存suffixを使用しました。各応答のusageはreasoning token 0でしたが、runtime設定の別途確認はしていません。model配布形式・量子化・runtime version・実効context／並列数は未確認です。入力は5,788〜9,163 token、出力は525〜689 tokenでした。18回すべてJSONを受信し、合計693.523秒、中央値36.732秒、最長66.882秒です。2回の反復は同一promptで、cache等を分離した速度比較ではありません。

最初の通常権限での接続はsandboxにより送信前に失敗しました。続く昇格要求は宛先の信頼性・送信許可の不足を理由に自動承認審査が拒否しました。利用者が設定済みLAN内サーバーを自分の信頼済みサーバーと確認し、指定9件の保存公開資料・説明・識別子を各2回送信することを明示承認した後に実行しました。認証情報を評価本文へ追加せず、正本DBは更新していません。

### 技術評価と通常一覧への効果

生成後に全5,054件を新しいread snapshotから復元しました。選定対象の入力資料と保存技術levelが生成前から変わっていないことを確認し、同じsnapshotのbaselineと比較しています。通常applicationの先頭50件と候補builderの一致を確認しました。通常`apply_llm_output`をメモリ上で適用したうえで、技術fieldだけを反映する条件と全出力適用の条件を分けています。

| 対象 | 保存level | 実LLMの2回 | 技術fieldだけを反映した通常順位 |
| --- | --- | --- | --- |
| Cobalt | high | notable／notable | 15位→候補外 |
| Konbini | notable | notable／notable | 候補外のまま |
| Community Lexicon Calendar | notable | notable／notable | 179位→178位 |
| Juntos | notable | notable／notable | 348位→347位 |
| Fortransky | high | high／high | 8位のまま |
| Laplan | high | high／high | 11位のまま |
| hydrant | high | high／high | 16位→15位 |
| standard.site | notable | high／notable | 紹介済み除外のまま |
| SkySweeper | notable | notable／notable | 52位→51位 |

両反復とも通常候補は448→447件で、追加0件・Cobaltの除外1件です。後順位対象の1順位上昇はCobaltが外れた効果です。Konbiniのpartial index、selective backfill、upstream構成、Fortranskyのdecode pipeline、LaplanのPetri net／BFSの原文は実際の入力に残っていました。Konbiniがnotableに留まったことを材料の未送信だけでは説明できません。ただしCodexのhigh仮説を人間goldとせず、これだけでmodelの誤答率を計算しません。

Fortranskyのhigh理由は、複数言語のbridgeとfirehoseのdecode構成です。珍しい言語選択と、再利用できるprotocol実装の学習価値の境界は残ります。Laplanは両回ともAPI呼出しの経路合成を説明し、highの根拠を保持しました。standard.siteは両回とも共有出版Lexiconという説明を維持しながら技術levelだけが変わりました。Calendarも`lexicon_scope`はunknown／sharedに揺れています。今回の9件・2反復から未見対象の安定性や全体精度は判断できません。

`apply_llm_output`直後の途中状態では、候補数と出入りは同じで、Juntosは329位、SkySweeperは60位でした。Juntosの差はLLMが`service_status`をunverified→liveとし、22点加えたものです。ただし後続監査で、通常enrichはさらに`finalize_project_shape`で観測から状態を再計算することを確認しました。この経路ではJuntosはunverified・134点・347位へ戻り、22点加算は残りません。途中状態を通常enrichの最終的な適用不備とした診断は撤回します。SkySweeperはmanual reviewの推奨によって4点下がり、最終処理後も60位です。9件の利用範囲のprojectionと手動判断履歴は不変でした。

### LLM精度と分ける問題

- **入力生成の容量制御**: idiolectは通常入力の生成で`LLM evidence cannot fit within configured byte budget`となり、送信できませんでした。現行promptに対するevidence枠18,674 byteに対し、削減対象をすべて取り除いても24,656 byteが残り、うちrepository解析2件が22,164 byteを占めます。`repository_analysis_results`が削減順にないためです。技術評価を再生成する前の問題であり、独自の縮約へ差し替えず未評価としました。
- **根拠と理由文**: hydrantは両回highでしたが、原文の「com.atproto.*以外のXRPCも扱う」を「標準スキーマに依存しない」等と説明しました。保存済み上流解析にも近い表現があり、triage単独で生じた誤りとは断定しません。Juntosの「分散型AI推論メッシュ」も既存profile等にあり、今回の出力はこれを保持しました。既存profileありの評価を、根拠原文だけからのmodel精度と混同しません。
- **機械検査の契約**: 現行promptは`source_code_urls`を要求しますが、評価器と通常適用は`project_repository_urls`を読むため、全18件に必須field欠落が出ます。これはmodelが要求を無視した18件とは数えません。一方、promptで要求している`confidence`の欠落は8件あり、別の出力契約違反です。unsupported identifierの1件は、原文にもあるhydrant URLへ日本語説明が隣接し、評価器が文末までURLとして拾ったものでした。機械検査の0件合格を意味精度0%とも、JSON成功を品質合格とも扱いません。field名不一致を補正する実装は今回していません。
- **正しい利用範囲でも残る順位**: Fortranskyをメモリ上だけでbluesky_clientとした2件対照では、highのpriority 8・96点は変わらず、Calendarのpriority 7・205点より前でした。前回のSkySweeper／Juntosは既存role補完で解消しましたが、すべての逆転がrole入力だけで直るわけではありません。これは仮説入力の診断で、手動判断を登録したものではありません。

### この比較での採否

現行promptで再生成すれば保存評価が一括して適切になるという案は採りません。また、今回の結果だけでmodel変更や技術優先の一律撤去も採りません。改善の次の比較は、(1) 入力容量・出力fieldの契約不整合を解消する設計、(2) 既存の生成要約への依存と原文に対する技術highの境界を分けた較正、(3) 妥当な技術highと共有Lexiconへの紹介関心を別に保つ順位、の順とします。service加点の診断は、上記のとおり最終処理まで確認して訂正しました。

利用範囲は現行triageの出力項目ではなく、以前不採用とした意味抽出を今回は再実行していません。LLMの「主機能」をさらに強く推定させればよいという結論にはせず、保存した仕様・公式説明から言える用途と、実装済みの利用・実稼働を別に扱う必要があります。追加LLM処理・自動role保存・全件手動判断は採用していません。

固定corpus、元の選定レコード、実出力、入力監査、通常適用の比較は`.tmp/promotion-quality/llm-calibration/`に保持しています。corpus file SHA-256は`4e7adb740e0aa624a7b5776531e576dfcf8fa05f97225d1efb95a159aea1b247`、実出力reportは`73b19e7513c4fff9bc41229b10c407410f865524cd2d7c6c5935608345ae340a`です。通常候補builder、適用関数、API baseline一致、選定元入力の不変、最後のbaseline全順位・score再計算一致を確認しました。コード・prompt・正本DBは変更していません。

## 入力設計と対象分類を分けた追加比較

続行GOを受け、入力容量・通常適用の最終段階・原文中心の判定案を比較しました。**入力容量制御と出力field名の整合は改善候補として残し、reasoning noneで新しい対象分類をLLMに付けさせて順位へ使う案は不採用**です。この分類案は短く速くしても、期待する対象間の順を壊す意味の誤りが残りました。既存の大きなtriageを小さな追加LLM呼出しに分割すれば解決するという結果ではありません。

### 適用段階の訂正

通常pipelineは`apply_llm_output`の後に`finalize_project_shape`、URL候補整合、`apply_promotion_state`を通ります。外部再取得や保存をせず、保存出力18件をこれらの通常関数へ通しました。Juntosは両回ともlive→unverifiedへ戻り、standard.siteのlive提案も観測に基づくnoneへ戻りました。通常候補一覧との比較をやり直し、Juntosは両回とも347位、Cobalt除外以外の候補出入りはないことを確認しました。LLMのlive提案は弱い根拠の出力ですが、この対照に対する現行の最終guardは機能しています。service適用規則の新設は今回の修正候補から外します。`finalized-analysis.json`にAPI baseline一致・元入力不変・baseline全順位再計算一致を記録しました。通信を伴うenrich全体やDB保存・再読込を再実行したものではありません。

### 入力容量の全件監査と比較

正本5,054件を単一read transactionで取得して接続を閉じ、保存された現在値から通常triage入力を組み立てました。20件でbyte上限超過が再現しました。内訳はactive 7、stable 11、stalled 2、紹介済み12・未紹介8です。これは現在の入力生成経路の監査であり、過去enrichの実失敗率や候補の取りこぼし数ではありません。

| 一時的に比較した入力案 | 元の失敗20件中、入力を生成できた数 | 保持／損失 |
| --- | ---: | --- |
| リポジトリ内の観測・機能・collectionを各6件へ制限 | 9 | 生成した説明・関係を省略 |
| 同じlistを各3件へ制限 | 17 | 各対象のrepoと原文抜粋は保持するが、説明・関係を省略 |
| 同じlistを各1件へ制限 | 17 | さらに省略しても残る3件は解消しない |
| JSONの整形空白を減らす | 17 | 17件ではrepository解析payloadをそのまま保持。既存の上位list縮約は残る |
| 整形空白の削減＋内部list各3件 | 17 | 残る3件は解消しない |

idiolectは整形空白を減らす条件でevidence 18,470 byteとなり、repository解析2件をそのまま保持して18,674 byte枠に収まりました。固定の3件上限を実装する前に、情報を保つ直列化を優先する方が妥当です。残った3件はBluesky（`proj:0d784726dfae`、別の紹介済みBlueskyと同一視しない）、Barazo、Cosmikで、いずれも選択されたrepository解析6件を持ちます。全体でのbyte予算配分が必要で、入れ子listの件数制限だけでは保証できません。

推奨する入力契約は、まず不要な整形空白を除き、それでも超える場合はrepoごとの原文・参照先を一まとまりとして容量を配分するものです。根拠を落としたclaimだけを残さず、省略対象・量をcoverageへ記録します。複数repo全体の配分方式と、省略後も必要な意味が残ることの検証は未了です。20件だけに作用する例外や、失敗を隠す別の通常経路は作っていません。今回の候補生成の改善件数とも数えません。

出力field名は、通常適用・文書が読む`project_repository_urls`へpromptも合わせる案を推奨しました。`source_code_urls`との二重契約や互換aliasは追加しない方針です。これは出力契約の整合であり、`confidence`欠落や分類・技術評価の意味を直すものではありません。この比較時点では変更と回帰検証は未実施でした。後続の実装状態と検証範囲は[現行status](../developer/status.md)を参照してください。

### 原文中心・対象分類を別にする実LLM比較

前回と同じ9件・同じ既存model・信頼済みサーバーで各2回実行しました。通常コードとpromptを変えず、短い実験用promptへ保存原文を渡しました。repositoryの原文抜粋、serviceのtitle／descriptionと既存parserによる保存HTML本文、schema sampleを使い、過去の生成profile・技術level・summary候補・repositoryの生成説明は渡していません。原文自体の切捨て済み部分は復元せず、related repositoryやstaleの扱いを明記しました。

対象分類は、既存categoryでは独立アプリとBlueskyクライアントを区別できず、既存利用範囲は実装の利用深度も含むため、実験に限って`shared_schema / protocol_component / independent_application / bluesky_client / integration / unknown`を尋ねました。恒久fieldの新設を採用したものではありません。技術highのrubricは現行本文をそのまま使い、分類と独立して判定させました。入力の選択と出力taskを同時に変えた案の比較であり、どちらだけが改善・悪化の原因かを分離した実験ではありません。

Responses、temperature 0.2、reasoning要求none、最大出力1,024 token、180秒timeout、model suffixなしです。promptは4,688〜10,920 byte。18件すべてJSON成功・参照IDは入力内に存在し、合計282.464秒、中央値15.071秒でした。前回の通常triageとは入力・出力の仕事量が違うため、同じ処理が高速化したとは扱いません。

| 対象 | 対象分類（両回） | 技術level（2回） | 原文との照合 |
| --- | --- | --- | --- |
| Cobalt | bluesky_client | notable／high | 対象は捉えるが、同じ原文で技術閾値が揺れる |
| Konbini | protocol_component | notable／notable | index・backfillを説明できても、新規性不足を低評価理由にする。現行rubricは新規性を必須にしていない |
| Calendar | shared_schema | notable／notable | 期待方向のlabelでも、「schemaのpropertiesがあるから独立実装間での交換意図が明確」という理由は根拠不足。generic schemaだけでは不十分という指示にも反する |
| Juntos | bluesky_client | notable／notable | 両回誤分類。初回の理由は「独立したアプリでありBlueskyコンテンツのfrontendではない」とlabelに矛盾。2回目は独自channelをBlueskyコンテンツと混同 |
| Fortransky | bluesky_client | high／high | clientを捉える。複数言語とdecode構成をhighとする境界は残る |
| Laplan | protocol_component | high／high | Petri net／BFSによるAPI経路合成を保持 |
| hydrant | protocol_component | high／high | 初回はfiltered index・XRPC・relayを原文に沿って説明。理由文すべての自動受理を認めるものではない |
| standard.site | shared_schema | high／high | 共有出版仕様と技術levelは今回安定。2反復・調整に用いた既知例のみ |
| SkySweeper | bluesky_client | notable／notable | 投稿削除utilityという対象を捉える |

一部説明は日本語指定にも従いませんでした。参照IDの一致、同じ分類を2回返すこと、JSON成功は意味の正しさを保証しません。今回の対象は既に検討に使った例で、未見資料での性能評価ではありません。新たな人間goldも作っていません。

### 順位案の採否

この対象分類を使い、共有仕様を先に、独立アプリ・基盤をクライアントより先にする場合を3対照で比較しました。同じgroup内は現行priority／scoreを使う診断です。CalendarはFortranskyより先になりますが、Juntosは両回ともクライアントへ誤分類されるため、Fortransky・SkySweeperより後ろのままです。全件順位の再計算やこのgroupの固定配点を採用したものではありません。期待するlabelだけをCodexが置けば逆転を解消できても、そのlabelを今回のLLMが作れないので、順位案の成功としません。

従って、このreasoning none条件で原文を短く整理し対象分類を追加する案を、自動順位の改善策としては採用しません。技術評価の新規性・学習価値の境界を調べることはこのtask内の論点ですが、promptへ同じ規則を足して既知例を繰り返すだけでは、未見対象への一般化を確認できません。LLM側の次の有力な比較は、今回露呈した「基準の遵守」「対象の取り違え」「labelと理由の整合」を固定条件にして、reasoning設定または別modelを比較することです。どのmodelに変えるか・通常運用へ追加するかは未決で、今回model変更は行っていません。入力容量の是正、合意済みのstable包含と、推薦方式全体の品質改善は別々の達成条件として扱います。

作業素材は`.tmp/promotion-quality/design-comparison/`です。実LLM出力reportのSHA-256は`7df3e7606fa199da9708a08d04464f7891464f28f5a66fed3d95f6d2f74e6116`。通常入力の全件監査、内部list制限と直列化の比較、18生成、対照の順序比較を保存しました。通常コード・prompt・正本DBは未変更です。

## reasoning設定の比較

**同じ条件の20回比較では、lowでCobaltとJuntosの判定が改善しましたが、技術閾値の揺れと説明の補完は残りました。利用者は処理時間からデフォルト採用を除外し、deep・個別確認に限定した検証候補としています。** 以下は比較条件を確立した経緯と完走結果です。当初は5件×none／low×各2回の20呼出しを予定しましたが、Cobaltのnone 1回・low 2回の計3回で中断しました。lowでは2回とも最終JSONを取得できず、応答構造も記録した2回目は、推論だけで出力予算のほぼ全量を使い、最終回答が存在しませんでした。残り17回を実施したことにはせず、この失敗を分類精度や最終JSONの構文精度の低さには数えません。

対象はCobalt、Konbini、Juntos、Laplan、standard.site。前節の保存原文・短い実験promptをhashで固定し、modelは同じQwen、Responses、temperature 0.2、suffixなし、timeout 180秒、最大出力2,048 tokenを両条件に指定しました。前節の1,024 token条件の結果をnone側の代用にはしていません。各対象の2巡目で条件順を反転する計画でしたが、初回lowの失敗後、予定済みのCobalt low 2回目を先に実行して診断しました。

| 実行 | 結果 | 秒 | 確認した範囲 |
| --- | --- | ---: | --- |
| Cobalt none 1回目 | bluesky_client／high、JSON成功 | 18.518 | 推論token 0、出力339 token。説明は英語で日本語指定に反する。Wii Uへの移植をhighとする揺れも残る |
| Cobalt low 1回目 | JSON解析失敗 | 85.086 | 失敗段階・本文byte数・hashだけを保存。応答envelopeを保持していないので2回目と同じ原因とは断定しない |
| Cobalt low 2回目 | 最終回答なし | 83.950 | 出力2,047 tokenすべてがreasoning。outputはreasoning itemのみでmessageなし |

2回目のサーバー応答は`reasoning.effort=low`、`max_output_tokens=2048`を返し、usageは推論2,047 tokenを報告しました。推論生成が動いたことは観測できましたが、runtime内部でlowという強度をどう適用したか、version・量子化・templateは未確認です。サーバーは`status=completed`、`incomplete_details=null`を返しており、上限による切断を明示していません。「最終回答前に出力予算が足りなくなった」という診断は、上限に近いtoken数と最終回答欠落からの推定です。2,048 tokenに揃えた今回の条件では意味の比較が成立しませんでした。

現行`parse_openai_compatible_response`はmessageがあれば優先しますが、ない場合はoutput全体からtextを抽出します。reasoningだけの合成応答を同じ関数へ渡して、推論文がJSON解析へ進むことを確認しました。今回の2回目はこの経路なので、`response_stage=model_json`という表示を「最終回答のJSON構文エラー」とは扱えません。応答の最終回答有無と出力上限の診断も修正候補ですが、コード変更はしていません。この失敗を、最終JSONの構文違反に対する5反復試験の対象と同一視しません。

続行GOにより、同じCobalt・同じpromptでlowの最大出力を8,192 token、timeoutを420秒にした予備確認を1回実施しました。128.113秒で最終JSONを取得し、出力3,050 tokenのうち2,839 tokenが推論でした。以前の2,048 token枠を超える推論を経て回答できたため、出力枠不足という診断を支持します。ただし失敗回の正確な停止理由を新たに確認したものではありません。

判定は`bluesky_client / notable`。日本語で、Wii U向けの標準的なXRPC・セッション管理・UIの実装例と説明し、具体的な追加のプロトコル機構を捏造してhighにしていません。S01〜S03の原文と照合し、分類・技術理由の対応、必須fieldの型・enum・参照IDを確認しました。実機動作を検証済みとは断定せず、検証結果を不足情報にしています。これは今回の1回における妥当な判定であり、noneでも以前notableが出ていること、8,192 token枠のnone対照がないことから、reasoningによる改善とはまだ断定しません。実行時commitは`3e666c59`、reportは同素材directoryの`cobalt-low-8192-preflight.json`、SHA-256は`50b07cb41a0e5bd7407dc738e55d7364603dfee67676ac5f47d72d6c5f8fff87`です。

この予備確認後、同じ5件でnone／low各2回、計20呼出しを、両条件とも最大出力8,192 token・timeout 420秒に揃えて実施しました。固定prompt・原文を維持し、各対象の2巡目で条件順を反転しました。予備確認を反復の代用にせず、20回を新規に完走しました。必要な設定変更と追加実行も利用者が了承しましたが、失敗がなかったため追加の変更・呼出しは行っていません。

作業素材は`.tmp/promotion-quality/reasoning-comparison/`。中断reportのSHA-256は`1ed604d46eda441d3de9d94a1417e16df122c33672a80affd51e1abd69f30353`、Cobalt low 2回目の診断は`b24a1a3b641c721317d6798a65c1323c9bc5a07fb141f7745e2ac4d579e3ee8b`です。失敗応答・推論の本文は保存せず、応答構造・usage・byte数・hashを保持しました。実行時commitは`cbcd23ad`、tracked変更なし。中断までの3呼出しは合計187.554秒で、条件別の性能比較には不足します。正本DB読取り・更新、新規収集、通常prompt変更は行っていません。

### 同条件20回の比較結果と採否

すべてJSON成功で、必須fieldの型・enum・参照IDの存在・必要な引用・prompt hashが一致しました。成功した最終回答20件すべてを原文と照合しました。次の表は各条件2回の結果です。人間goldの正解率ではなく、固定資料に対するCodexの診断です。

| 対象 | none | low | 原文との対応と診断 |
| --- | --- | --- | --- |
| Cobalt | client／highを2回 | client／notableを2回 | lowは通常のXRPC・session・UIの実装例と評価。noneは珍しいハードウェアや実装負荷でhighにし、具体的な機構の不足を認めながら昇格 |
| Konbini | 基盤／notableを2回 | 基盤／notable、基盤／high | low初回は既存仕様の再利用・新規性不足を減点。2回目はFirehose／Jetstreamの同期基盤としてhigh。基準遵守は安定していない |
| Juntos | client／notable、client／high | 独立アプリ／notableを2回 | lowは独自のcommunity/channel用途を捉える。noneはBlueskyのコンテンツ操作と誤同一視し、2回目は通常CRUDをhighにした |
| Laplan | 基盤／highを2回 | 基盤／highを2回 | Petri net／BFSによるAPI合成という主要機構を保持 |
| standard.site | 共有schema／highを2回 | 共有schema／highを2回 | 明示された共有出版仕様の目的を保持し、関連Jekyllプラグインそのものと取り違えない |

lowも説明の自動受理には至りません。Laplanの2回目は「エラー処理やリトライを宣言的に記述できる」と補いましたが、入力S02は従来コードの対比にそれらの語があるだけで、その機能を裏付けません。Cobaltの初回は「入力に根拠がない」を設計全体の不存在に広げた表現でした。standard.siteの2回目は入力中にある購読・推薦・themeへ言及する一方、その箇所のsource IDを引用していません。noneにもJuntosのBlueskyへのマッピングやstandard.siteの既存post型の限界など、入力にない補完がありました。日本語指定はnoneで6/10件が英語、lowでは全件の非空の説明fieldが日本語でした。Juntos lowのtechnical summaryは2回とも空ですが、notableでの空欄は今回の実験契約で許容しています。

| 条件 | 回数 | 中央値 | 最長 | 合計 |
| --- | ---: | ---: | ---: | ---: |
| none | 10 | 10.746秒 | 18.066秒 | 110.895秒 |
| low | 10 | 127.607秒 | 163.620秒 | 1,182.212秒 |

合計1,293.107秒、中央値の比は約11.9倍でした。noneは全10回で推論token 0、lowは2,573〜3,820 tokenを報告し、推論生成の有無は確認できました。lowの総出力は2,782〜4,112 tokenで、8,192 token枠の再調整は不要でした。この生成時点ではruntime内部のlow強度、version・量子化・templateは未確認でした。後続のcapability確認でoff／on型と分かったため、低強度と高強度の比較としては扱いません。cache済み入力token数は対象ごとに両条件・両巡で同じでしたが、2巡目は両条件とも速くなりました。巡別中央値はnone 13.448→8.672秒、low 138.818→86.327秒で、サーバー負荷等は制御していません。速度比を全件運用へ外挿せず、今回の観測値として扱います。

今回の採否は、**none条件の分類案は順位へ採用せず、推論ONはデフォルト採用から除外し、deep・個別確認に限定した検証候補へ残す**です。「分類案は設定によらず役に立たない」という結論にはせず、最初の不採用をnone条件へ限定します。一方、Konbiniの閾値は2回で揺れているので、技術highへの単純再評価だけを入口改善として採用できません。追加の反復だけで未見対象への一般化が分かるわけではないため、同じ5件への設定調整はここで区切ります。

ユーザーが指摘した技術highの判断側は、順位規則と同じく原因検討の範囲です。今回、同じrubric・同じ資料でも設定で判定が変わり、lowでも明記した「新規性を必須にしない」が安定して守られませんでした。したがって順位だけを原因にする診断は不十分です。ただし、技術評価を仮に訂正した比較でも共有Lexicon・Juntosの後順位が残った点は別に成立します。技術highの意味・根拠と、紹介する対象の相対的な優先を分けて検証する必要があります。技術highへ関心度まで押し込む、あるいは技術priorityを外すだけ、という案には進みません。

次段の品質検証には、promptを変えずに選ぶ未使用対象と、通常triage・適用・候補導線での効果確認が必要です。今回の短い実験taskは通常triageではなく、新fieldの`artifact_kind`も実験用です。5件は既知例で、全候補の捕捉率・ノイズ率・人の訂正時間を測っていません。分類が改善したことから実際の表示順位の改善を推定せず、正本DB・通常設定・promptは変更していません。

作業素材は`.tmp/promotion-quality/reasoning-comparison-8192/`。固定条件・順序・全20応答・安全な応答metadataを`report.json`、構造検査・巡別時間を`summary.json`、全件の原文照合を`semantic-review.json`に保存しました。report SHA-256は`34cb563869ff8cf264891680e16b7d6b77d4da74ad2e3fcda14221305d8ec067`、実行時commitは`262dfd76`です。推論本文は保存していません。

### 「最高reasoning」の対応可否と利用範囲

利用者は、推論ONの処理時間ではデフォルト利用は許容できず、deep・ピンポイント利用なら検討できると明示しました。これは限定利用の採用・実装命令ではありません。以後の採否では通常処理の全面切替を候補から外します。

最高強度を比較する前に、同じ信頼済みサーバーの`GET /api/v1/models`で使用中の`qwen/qwen3.6-35b-a3b`だけを読み取りました。現在のcapabilityは`reasoning.allowed_options=["off","on"]`、defaultはonでした。配布形式はMLX、量子化4bit、loaded context 183,296、parallel 4です。この読み取り時点の情報であり、過去の全生成時点に同じ構成だったことを証明するものではありません。認証情報と他modelの一覧は保存せず、選択したmodelの応答を`.tmp/promotion-quality/reasoning-capabilities.json`に保持しました。

[LM Studioの公式API仕様](https://lmstudio.ai/docs/developer/rest/list)ではこのfieldがmodelごとの対応reasoning値を表します。[Qwen公式chat template](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/raw/main/chat_template.jinja)も`enable_thinking`の真偽で生成開始を切り替え、low／highの強度分岐を持ちません。ただし実サーバーのtemplate本文や、Responses互換層内部のlow・high変換は取得していません。

従って前節のnone／lowは要求値として残し、実測した違いは推論token 0と推論ありの差として解釈します。lowを「低い強度」と呼んだ説明は訂正します。現構成には公開されたhigh段階がないので、highを要求して応答にその値が返るだけでは、強度を上げた比較は成立しません。最高強度での追加生成は実施していません。前回の20回で出力は最大4,112 tokenに収まっており、8,192 token上限をさらに上げるだけで精度が上がるという根拠もありません。

この構成での次の有力な比較は、推論ONを限定した問いに使うものです。例えばKonbiniの技術highの根拠と不足、Laplanの説明中の主張と原文の対応を個別に検証します。追加の根拠と反証を照合して不確実性を減らす効果を、時間・根拠外の補完・最終判断への影響で評価します。この案は次節で固定資料を用いて比較しました。追加資料の取得を含むdeep調査全体は実施しておらず、その効果は未検証です。現行`--deep-investigation`は追加ページ等の調査範囲を広げる既存機能であり、「最高reasoning」の別名ではありません。deepへの自動接続・新しい判定field・通常設定の変更は行っていません。

### 論点を絞った再確認とエスカレーションの前提

**Konbini・Laplanの限定再確認では、推論ONの優位を確認できませんでした。根拠不足の指摘には部分的な有用性がありますが、再生成した説明の自動採用は見送ります。** 利用者はこの方針での検証と、うまくいけば推論OFF自身によるエスカレーション判断の検証を希望しました。まず再確認先の品質を確かめ、安定した修正という前提を満たさなかったため、自己エスカレーション試験には進んでいません。

同じ保存原文から、KonbiniはAppView・backfill・上流設定の事実と、以前の「新規性・再利用可能な教訓がない」という評価理由を、LaplanはPetri net／BFSの機構と「エラー処理・リトライを宣言的に書ける」という説明を照合させました。modelへ正解ラベルを渡さず、主張別のsupported／contradicted／not_established、短い原文引用、最終technical levelと説明を求めました。技術rubricは変更していません。新規資料を追加せず、質問と出力taskを絞る案の比較です。

同じ問いで推論ON各2回に加え、推論OFF各2回も対照にしました。計8回、Responses、temperature 0.2、最大出力8,192 token、timeout 420秒です。ONは前回と同じ互換API要求値lowを用い、推論3,403〜4,512 token、OFFはnoneで推論0を確認しました。lowという強度段階を使った意味ではありません。全8回JSON成功で主張ID・参照IDを確認し、本文すべてを保存原文と照合しました。

| 確認対象 | 推論ON（2回） | 推論OFF（2回） | 診断 |
| --- | --- | --- | --- |
| Konbiniの機能記述 | supportedを2回 | supportedを2回 | 明記されたAppView・backfill・上流設定は保持 |
| Konbiniの技術評価理由 | 既存の低評価理由を支持、notableを2回 | 同じく支持、notableを2回 | 既存仕様の再利用、新規性不足、作者のjankという説明へ依存し、基準適用の問題は解消しない |
| LaplanのPetri net／BFS | supportedを2回 | supportedを2回 | 正しい機構まで一律に否定しない |
| Laplanのリトライ宣言機能 | not_establishedを2回 | not_establishedを2回 | 個別主張では根拠不足を検出。推論ONは必要なかった |
| Laplanの書き直した要約 | 2回ともリトライ自動化／置換の趣旨を再混入 | 初回は再混入、2回目は原文の機構に限定 | 個別判定と生成文章が整合せず、検出成功を修正成功とみなせない |

Konbiniのnotableというlabelそのものを、唯一の正解highへの不一致として採点したわけではありません。「新規性がなくても基盤実装を評価する」というrubricに対し、新規性不足や実装の粗さを理由とする従来の説明が是正されなかった点を診断しています。既存評価文も入力したので、その評価へ引っ張られた可能性は分離できません。

引用の完全一致はONで0/4応答、OFFで2/4応答でした。不一致は`_very_`の強調記号や`[Petri net](URL)`のリンク表記を落としたもので、原文にない内容の捏造とは分けます。一方、リトライ自動化の再混入は意味の誤りであり、この引用表記差と同列には扱いません。

所要時間はONの中央値95.259秒・合計400.660秒、OFFの中央値19.501秒・合計88.250秒でした。ONを先に4回、OFFを後に4回としたため、cache・サーバー負荷の影響を分離した速度比ではありません。既知2例で、独立した正解集合や未使用対象への一般化もありません。同じ原文と問いに対し、ONの追加時間で修正品質が上がった根拠は得られませんでした。

この結果から、推論ONを自動修正先とする接続は採用しません。**主張ごとの根拠不足を示す補助結果だけに用途を絞る**案は残ります。Laplanではこの指摘をOFFでも得られたので、限定再確認とthinkingを常に結び付けません。資料の追加取得を含むdeepの効果は別途未検証です。再生成要約を自動採用せず原文と指摘を提示する契約なら、後続のエスカレーション判断を試す余地があります。その新契約は次節で検証しましたが、自己判定の有効性はまだ確認していません。

作業素材は`.tmp/promotion-quality/focused-review/`。ONの`report.json` SHA-256は`e67a8e07bfc31cdeb1322446b2fc293e26d462051d9bde5786abfabfad6be8be`、OFFの`report-none.json`は`ce8c941b1f5913782388f15d37097118debc4b26b95b911c9133a5af6b189980`です。固定prompt・case・原文照合と引用検査は同directoryに保持しました。`.tmp/promotion-quality/self-escalation/`には準備した5件×2回の自己判定用入力だけがあり、生成は0回です。通常コード・prompt・設定・正本DBは変更していません。

### 主張別の検証だけに絞った推論OFFの追加比較

**人が短く切り出した主張の根拠確認には部分的に有用ですが、長い説明を自動で検査し、問題の有無を保証する用途には不足します。** 説明の再生成をやめても、部分的な裏付けで主張全体を支持すること、判定理由へ新しい根拠外の説明を混ぜることは残りました。主張の切り方による改善を確認したので、modelのreasoning設定だけで決まる問題ではありません。

利用者の続行指示に基づき、Konbini・Laplan・Cobalt・standard.siteを推論OFFで各2回、計8回確認しました。出力は主張別の判定・理由・引用だけに限定し、最終技術levelと書き直した要約を求めません。各対象に保存資料に沿う正の対照を1つと、以前の生成に含まれた問題のある主張を1つ置きました。正の対照はCodexが作成し、問題の主張は過去の生成から採ったものです。modelへ期待判定は渡していません。技術rubricの意味は維持し、そのうち要約の出力指示だけを除きました。Konbiniの評価理由は事実の正誤だけで採点できないため、唯一のhigh正解を置かず、理由と基準の対応を診断します。

全8回JSON・出力schema・主張ID・参照IDは正常で、原文に明記された正の対照は8/8でsupportedでした。一方、結果は以下の通りです。

| 対象の問題のある主張 | 各2回の結果 | 原文照合による診断 |
| --- | --- | --- |
| Laplanの宣言的なエラー処理・リトライ | not_establishedを2回 | 根拠不足を検出。書き直しを求めないため、要約への再混入という経路は除けた |
| Cobaltのcustom backend・多大な技術的労力を含む説明 | supportedを2回 | Wii U対応とWolfram-backedという部分的な根拠で、説明全体を通した |
| standard.siteの既存app.bsky.feed.postの扱いにくさを含む説明 | supportedを2回 | 初回はその比較の根拠がないと理由に明記しながら、全体を支持した |
| Konbiniの新規性・再利用可能な教訓がないという評価理由 | contradicted／supported | 初回はrubricの例である共有スキーマ・独立した署名付き主張を、Konbiniの機能であるかのように理由へ混入。2回目は1日で作ったという作者説明から教訓がないと推論。どちらも妥当な較正になっていない |

Cobalt・standard.siteについて、部分的な裏付けで通る原因を確かめるため、主張を短く分けて各2回、さらに4回実行しました。prompt・原文・正の対照は同じです。Cobaltは以前の文章からcustom backendの節とengineering effortの文をそのまま分離し、standard.siteは既存レコードへの批判部分だけを独立した日本語の文にしました。問題を見た後に追加した診断であり、独立した検証集合ではありません。

この分割後は、standard.siteの比較とCobaltの技術的労力は各2回ともnot_establishedとなりました。正の対照も4/4でsupportedを保持しました。ただしCobaltのcustom backendは依然2回ともsupportedで、`Wolfram-backed session and XRPC`というdirectory説明だけではcustom backendという性質まで確定できない問題は残ります。手動で論点を分離すると一部は改善しますが、全主張の抽出・分割を自動で正しく行えるかは未検証です。

全12回はResponses、temperature 0.2、最大出力8,192 token、timeout 420秒、要求reasoning noneで推論token 0でした。最初の8回は中央値15.596秒・合計123.306秒、分割後4回は中央値9.488秒・合計41.756秒です。入力長・出力長・cacheが異なるので、分割そのものの速度効果とは解釈しません。引用の完全一致は最初3/8応答、分割後2/4応答でした。LaplanのMarkdownリンク省略やCobaltの改行を空白にする差に加え、standard.siteの2回目にはschema引用からmemberを省略し、`main.type`をrecordからobjectへ変えた箇所があり、引用不一致をすべて表記差とは扱えません。

採否として、**人が指定した短い主張を原文と照らす補助用途は残し、supportedを自動承認に使うことや、技術評価理由の自動較正は見送ります。** 根拠不足を指摘する文章も検証対象です。今回の結果は「初回の推論OFFが自分の誤りを認識し、必要な対象だけを再確認へ回せる」ことを示しません。自己エスカレーション試験は引き続き未実施であり、この手動切り出し試験をその代わりとは扱いません。追加資料を取るdeepの効果も未検証です。

作業素材は`.tmp/promotion-quality/claim-only-review/`と`claim-only-atomic/`です。各`report.json`のSHA-256は順に`e2bbd13b0db53196e51fb31d73ccb3fdd15e1cc3c9a3138ac0399e65777b5c85`、`fc5fa2da7ea56ccc230f0dec1e8d15f02ef8d86c7ca39dc79e9d8062f2165914`。固定入力・期待の根拠・全応答の原文照合結果を同directoryに保持しました。通常コード・prompt・設定・正本DBは変更していません。

### Qwen3.8-27Bでの最終追加比較と当面の保留

**Qwen3.8-27Bは長い主張の検証を改善しましたが、自動検証・自己エスカレーションへ進めるには不足すると判断し、この方向の追加調整は当面保留にします。** 利用者は「おまけでQwen3.8-27Bを試し、それでも駄目なら当面諦める」と指示しました。精度の改善は認めつつ、既知の見逃し・指摘理由の誤り・追加時間を合わせて採否を判断します。全LLMの検証能力や、別の調査方式一般を否定する結論ではありません。

信頼済みサーバーのモデル一覧から`qwen/qwen3.8-27b`を確認しました。MLX・4bit、architecture `qwen3_5`、最大context 262,144、対応reasoningはoff／low／medium／xhigh／on、default xhighです。確認時は未ロードでした。これは前に比較した3.6のoff／on型とは異なります。今回の生成は推論OFFだけであり、3.8のlow・medium・xhighの効果は未検証です。

前節の4件×2回と、Cobalt・standard.siteの手動分割後2件×2回、計12回を再実行しました。prompt・原文・主張・期待条件は変更せず、各入力のhashが前回と同じことを照合しました。実験プロセス内だけモデルを切り替え、Responses、要求reasoning none、temperature 0.2、最大出力8,192 token、timeout 420秒を維持しました。全12回JSON・出力schema・主張ID・参照IDが正常で、返却モデル名は3.8、推論tokenは0でした。全本文を原文と照合しています。

| 対象 | 3.6の推論OFF | 3.8の推論OFF | 判断 |
| --- | --- | --- | --- |
| 原文に明記された正の対照 | 全12回でsupported | 全12回でsupported | 一律否定にはなっていない |
| Laplanのリトライ宣言 | 根拠不足を2回検出 | 同じく2回検出 | 維持 |
| Cobaltの長い説明 | 全体を2回支持 | 全体の根拠不足を2回検出 | 改善。ただしcustom backend自体を特定して止めたわけではない |
| standard.siteの比較を含む長い説明 | 全体を2回支持 | 既存レコードへの比較部分が未支持と2回指摘 | 改善 |
| Konbiniの旧評価理由 | contradicted／supportedで揺れ、理由にも誤り | not_establishedを2回 | 断定は減少。初回理由には短期間の開発から教訓不足を推測する問題が残る |
| 分割後のCobaltの技術的労力、standard.siteの比較 | 各2回とも根拠不足 | 各2回とも根拠不足 | 維持 |
| 分割後のCobaltのcustom backend | 2回とも支持 | 2回とも支持 | `Wolfram-backed session and XRPC`だけからcustom backendまで確定する見逃しが残る |

判定理由にも問題が残りました。standard.siteの分割後初回は、比較の根拠不足という判定自体は妥当ですが、理由の日本語では「長文出版向けに設計されていること」も資料に含まれないと否定する文になっています。これはS03／S08と自身の正の対照への説明に反します。意図を推測して読み替えず、指摘文を人が確認する必要がある例として扱います。引用の完全一致は5/12応答で、不一致はMarkdown表記の省略や改行の空白化でした。前回のschema引用の内容変更と区別します。

全12回の中央値は34.787秒、合計459.112秒、範囲21.345〜68.329秒でした。分割前8回は中央値36.242秒（3.6は15.596秒）、分割後4回は31.924秒（3.6は9.488秒）です。最初の58.165秒にはモデルロードが含まれた可能性があり、別計測していません。cache・負荷・実行順・出力長も統制していないため、モデル固有の速度比とは言えません。この環境では検証を1段追加する実測の負担として扱います。

既知4件の手動選定で、未使用対象への精度・主張の自動抽出・初回出力の自己エスカレーション判断は未検証です。長い主張の部分的な裏付けで全体を通す傾向は改善しましたが、残る誤りの確認を人が担い、1回あたり数十秒を追加する方式を通常運用へ導入する根拠は不足しています。利用者の打切り方針に従い、同じfixtureへのprompt調整や推論強度の追加探索には進みません。手動の補助確認まで否定せず、通常フローへ自動の再検証・修正・エスカレーションを追加する案を保留にします。再開は、モデル・取得資料・作業方式が実質的に変わり、以前の見逃しと利用負担を再評価できる場合とします。stableの候補条件や関心を反映する順位など、別の改善論点を取り下げる意味ではありません。

素材は`.tmp/promotion-quality/qwen38-capabilities.json`と`qwen38-claim-review/`。後者の`report.json` SHA-256は`dc22b941229e8b67bec596d724083b0f6d194df95c4d4e23eb727482e0714a3d`です。通常モデル設定・コード・prompt・正本DBは未変更です。変更は調査文書だけで、`git diff --check`を確認し、unit testは実行していません。

## 表示材料・原因の切り分け

- **保存情報不足**: Juntosのservice保存物は2026-09-16の静的HTML（433 byte、rootとscript中心）とHTTP 200です。READMEのLive Demoは自己説明であり利用確認を代替しません。確認が足りないことは正しく残す一方、channel schemaの関心まで失わせる必要はありません。
- **帰属・分類・紹介単位**: 同名の別repoやschema断片が存在します。Ionosphereのconfirmed repo `github.com/blaine/ionosphere`を持つ`proj:1190661ee687`は保存名がStreamplaceですが、README本文は会議動画archiveのIonosphereです。名前だけでStreamplaceの捕捉を数えると誤ります。これは少なくとも説明名と保存原文の不整合であり、今回DBを訂正していません。
- **評価signal**: idiolect／Konbiniのnotableには入口がなく、repo内schemaがあるだけではDNS公開signalにもなりません。DNS検証を緩めて同じconfirmed labelを付ける解決は、証拠の意味を壊します。
- **候補条件**: `active`のみという既定条件がstableの候補も除外します。Streamplaceはその動作例ですが、利用者確認後は取りこぼしの代表例には使いません。全tracking stateへの診断でstable 11件、stalled 42件が追加されましたが、すべて高関心という意味ではありません。紹介済み・対象外・ignore・保留は別の意図した条件です。
- **並び順**: 技術highのpriority 8、service確認／DNS公開の7、service未確認の4が、関心の連続的な差より強く作用します。利用範囲を手動`main_data`にしても未確認serviceはpriority 6なので、既存priority 7群の後ろという構造は残ります。全件手動入力を改善の前提にしません。
- **表示と利用者判断**: 一覧cardはtagline、signal、技術highなら技術説明を表示します。summaryとschemaの具体的内容は詳細側へ進んで読む必要があります。通常cardの「スコア」は合計scoreで、先に効くpriorityそのものを見せません。利用範囲未評価、未確認serviceの理由は表示されます。今回の到達性はapplication出力とUIコードで確認し、実browserでの見落とし頻度・訂正負担は測っていません。

## 改善するなら何からか

以下は合意した改善方向と未承認の実装候補です。今回の方針確認をDB更新や実装開始の承認とは扱いません。個別名を通常規則へ入れず、同じ対象集合で改善前後を比較します。

1. **未紹介のstableも通常の紹介候補に含める。** 一般方針は利用者了承済み、実装は未着手です。現snapshotで加わるstable候補は11件で、Germ Network、FedProxy、TOKIMEKI Poll等を含みます。Streamplaceは紹介済み扱いでもよい例外なので、未紹介対象を救済する正の代表例としては使いません。stalledは通常候補から外す方針を利用者が了承しました。紹介済み・明示対象外・保留・低優先度は維持する方向です。全53件を一括で復帰させる合意ではありません。
2. **新しいLexicon／共有仕様への関心と、稼働確認・技術的新規性を別に扱い、表示順へ反映する。** 技術評価の較正仮説だけでは残る後順位、既存合計scoreだけへ置換した場合の技術候補の埋没、既存role入力を補うことで解消する逆転を確認しました。先に較正と入力充足の可能性を評価し、残る差に対して順位の対案を比較します。今回合意した相対優先に照らした改善目標は、クライアントの珍しい実装だけで共有Lexicon等より先になる逆転の解消です。順位変更という手段は未採択です。Juntosを一律の高優先へ昇格することではありません。service確認は「触りやすさ」の材料として使い、未確認を低関心と同一視しません。Juntos、Calendar、CovesとFortransky、Cobalt、math.rを少数対照にします。合計点だけの微調整やDNS公開全件の一律最上位化は推奨しません。
3. **候補外の技術・共有仕様を救う入口の根拠を確認する。** idiolectは既紹介の診断対照、Konbiniは未紹介の検討対照です。repo由来schemaや代替AppViewの具体的機能を区別したまま使えるかを評価します。notable全件を候補にする案や、収集されたschemaを一律ownedとする案には進みません。Konbini・idiolectのlevelだけを上げる仮説では入口を得ました。Konbiniの実LLM評価はnoneでnotable、lowでもnotable／highに揺れており、単純な再評価による安定した救済は確認できていません。idiolectの生成と他の対象への一般化は未確認です。
4. **既存手動判断との不一致・紹介単位を限定確認する。** math.rを今も低優先度にするか、BlackskyのAppViewを別に紹介するかが候補です。既存goldの固定期待を現在DBへ機械適用せず、同名問題の全件cleanupへ範囲を広げません。

順位設計の価値判断は、今回の対照で「共有Lexicon ＞ Juntos ＞ クライアント」、特にBlueskyクライアントは低優先と利用者が確認しました。Juntosも特別に高優先ではありません。この合意を固定配点、クライアントの自動除外、すべての独自Lexiconサービスの高優先化へ昇格させません。特異なインフラ実装・代替AppViewとクライアントも一括りにしません。表示範囲は、未紹介のstableも通常対象へ含める一般方針を利用者が了承しました。Streamplaceは紹介済み扱いにしてもよい例外ですが、その個別DB操作は行っていません。stalledは通常候補から外す方針も利用者了承済みです。評価・対照・実装を機械的に別taskへ分けず、このtaskで合意後に続けられます。

## 検証・再開・受理

通常applicationの先頭50件との一致、全viewの集合と順位、14 manual gold fixtureと期待順の無回帰を確認しました。DB接続は最初WAL補助ファイル不在で通常権限では失敗したため、その計測を昇格して`mode=ro/query_only`のまま実行しました。次の30件詳細読取りは通常権限へ戻して成功しています。初回の保存データ診断では全unit test、実browser、外部通信、LLM、新規enrich、性能比較は実施しませんでした。後続の実LLM比較とメモリ上の適用比較は各節に記した条件・件数に限り、コード変更・新規enrich・公開ページの再取得・正本保存はありません。

読み取り計測と実験の作業素材は `/workspace/worktrees/ca10/atproto_survey/.tmp/promotion-quality/` にあります。生DB複製・認証情報は保持していないとの子task報告です。母艦管理の再開材料として、入力契約の修正または順位設計を次に検討する時まで一時保持し、その際に必要な再現素材を選別して残りの処分を判断します。通常の一時領域なので恒久的な再現保証とは扱いません。Git管理の評価メモは独立して判断根拠を保持します。文中の順位は観測時点のsnapshotであり、将来は現在DBと区別して通常経路から再計算します。

今回の選抜は既知例・紹介実績・上位・保存語彙に偏ります。未発見project、まだ材料のない高関心対象、全候補の人間goldがないため、全体の捕捉率・ノイズ率・方式全体の品質合格は判断できません。少数の大きな逆転と表示条件は示せますが、追加対象のすべてを紹介したいと確定したものでもありません。

調査成果はhuman-reviewで了承され、明示的な送信指示後に母艦で文書を統合しました。候補条件・順位・入力契約の実装修正は未承認の改善候補であり、今回の調査の未完作業とは扱いません。本番操作・推薦変更・正本DB更新は未実施です。
