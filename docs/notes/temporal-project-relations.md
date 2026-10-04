# Project境界・時間関係の設計材料と個別判断の留保

[TODOのproject間の時間・派生関係](../developer/todo.md#project間の時間派生関係)で、改名・継承・fork等を表示・検索・紹介文へどう反映するか、既存のidentifier履歴や分離判断で足りるかを検討する資料です。最初の節に保存形とreview操作の選択肢を置きます。後続の事例は[TODOの個別projectの再検討](../developer/todo.md#個別projectの判断を再検討する時)から参照し、同じ対象を扱う時に、同一性の留保や未確定のidentifier処遇を見落とさないために使います。

2026-08-11〜2026-09-27の調査・reviewに残った材料であり、現在のDBや公開先を再確認した結果ではありません。設計は必要な表示・検索・説明が具体化した時に再検討し、個別例は通常のレビューで必要になった時に確認します。全事例の先行調査は行いません。

現行・非現行の識別子、採用理由、確定した移転関係（transition）、再登録の抑止情報（exclusion）は現行のschema v4で保存できます。ここで未決なのはproject間の関係表現と、個々の事例をどの関係・状態として扱うかです。

## 改名・継承・派生で決めること

| 未決事項 | 残っている材料・選択肢 | 再検討時に必要な確認 |
| --- | --- | --- |
| 同一projectの改名と別projectへの継承 | 2026-08-11時点のDateSky/Nomareは一つのrecordに旧・新repo、NSID、serviceを持ち、DateSkyからNomareへのredirectも保存されていた。Basker/InfoEnonは別recordでnamespace・目的が異なり、直接relationはなかった | 一次情報またはoperator判断でproject境界を決め、同一project内でも各resourceが置き換わったかを別々に確認する |
| project間の時間関係の保存 | predecessor/successor、ownership transfer等の関係種別、発効時刻、operator判断をどう表すかは未確定 | 説明・表示・検索に必要な情報を先に決める。一方のproject配下だけへ閉じる保存で足りるかも確認する |
| 派生関係の表現 | `fork_of`、`derived_from`、`based_on_template`、`implements_example`は候補名。relation predicateか専用fieldかも未確定 | providerのfork parent、READMEの派生元link、package/template markerのどれを根拠として保存するかを決める |
| 派生関係とreviewの接続 | merge候補から除外する案と、候補に残してconfidenceを下げる案。`keep separate`と関係作成を同時に行う案 | 独自の提供面を持つ派生とsource-only forkを分け、既存`separate_project`や`fork_without_independent_identity` reviewで不足する操作を確認する |

派生関係の判断には[系譜・複製・マージ抑止](../developer/specs/project-boundaries.md#系譜複製マージ抑止)の原則を使います。上記の候補名と操作案は検討中です。

## 未確定のURL・namespace sample

| 対象 | 記録時点の観測 | 未決の問い |
| --- | --- | --- |
| Drydown | `drydown.social`から`alpha.drydown.social`へのredirectとconfirmの移動があった | rootは旧identifierか同時aliasか。`alpha`という名前だけでは現行性を否定しない |
| Barazo | `staging.barazo.forum`が代表的な公開先として保存されていた | 正式な公開先か補助環境か。docsのroleは後続の2026-09-09補正で扱っており、その完了事項とは分ける |
| lexicons.bio | `bio.lexicons.temp`と`bio.lexicons.temp.v0-1`のrecordがあり、後者は2026-08-08にも観測された | 無印側は旧namespaceか、並行利用か |
| Musicsky / observ.ing | `temp` namespaceのrecordが観測された | 正式namespace、一時検証、終了済みのどれか。名前と直近観測だけでは決められない |
| Spindle / publicdomainrelay | `com.publicdomainrelay.temp.*`にはmarket、agent、compute等もあり、Spindle固有とは限らない。`com.publicdomainrelay.temp.tangled.spindle.repos`はSpindleを明示する | 個々のproject帰属と、Tangled側schemaからの置き換えの有無 |
| PopFeed / PopSky | 2026-08-13の採用reviewに同時URL・canonical・履歴の区別が残った | 現在の採用・後続のrole補正と照合し、同時aliasか旧URLかを確認する |

redirectの判断では、次の異なる状況を比較します。現在の状態と公式告知・stable resource ID・両側の帰属根拠を確かめ、resourceごとの移転を判断します。

- Pinksky→Pinkleap: [当時参照したprofile](https://bsky.app/profile/pinksky.app)に発展・改名の明示があり、同一project内の置き換え候補とした。resourceごとの置き換えは別途確認する。
- 公開先の併存: ATPcraftでは`dev.atpcraft.at`の公式案内とLive mapが併存していた。上表のBarazo・Drydownと同様、補助リソース、並行する公開先、代表URL、本番環境の切替をURLだけで決めない。
- 行先が安定しない観測: PhotoSky／PskyからPhotoSkyまたはZeensへの観測。移転確定より先に、観測時期と行先の変化を確かめる。

## 一時merge review後に残ったidentifier処遇

一時導線で確認したのはcollection断片を包括projectへ収めるかというproject境界までです。識別子ごとに、現行・旧構成要素（former）・開発試験用（non-production）・無関係な誤検出（noise）のどれとして扱うかは未決です。一時導線は終了しており、再検討は通常の編集経路で行います。

| 対象 | 未決の材料 |
| --- | --- |
| Matadisco | `cx.vmx.dev.tmp003.matadisco` |
| Offprint | `app.offprint.experimental.media` |
| Roomy Space | `space.roomy.stream.dev.local` |
| Skyrdle | `farm.smol.games.skyrdle.`配下の`beta1.score`、`beta.guesses`、`beta.score`、`beta3.score`〜`beta8.score` |
| StratosFund | `at.stratosfund.estimate.dev`、`at.stratosfund.stage.dev` |

上表の14 NSIDは2026-09-04時点では現行の採用済み（confirmed）識別子でした。実recordの観測もあるため、公式な用途、利用時期、移行・廃止根拠を照合して、誤採用・旧版・開発試験用のどれに当たるかを判断します。

FreeMixの`com.freemix.*`と`fm.freemix.*`、grainの`v0`・`filmStock`表記差・labeler、TZ2ATの`store.tz2at.*`と`xyz.tz2at.*`、GainForestのobservationsからDarwin Core系への移行も、並存と旧版の区別が残った材料です。Impact Indexerの提供終了はproject/serviceの時間状態として扱い、NSIDのnoise判定へ置き換えません。

ATlasは[公式commit](https://github.com/lawhiteley/ATlas/commit/2328ac267dd55c7e4937f36d0ec6695308a06c62)にLexicon定義と実装の`io.whiteley.luke.ATlas.pin`→`io.whiteley.ATlas.pin`変更があり、旧側former・新側currentを支持する材料があります。判断材料があることと、その処遇がDBへ反映済みであることは区別します。

マージ前の最新snapshotとの照合では、Backyardの`blue.backyard.actor.profile`・`blue.backyard.graph.block`・`blue.backyard.graph.follow`、GainForestの`app.gainforest.evaluator.evaluation`、Postgameの`at.postgame.list`がマージ先で候補（candidate）、Simocracyの`org.simocracy.floor.config`が未登録でした。消失の有無を判断する際は、snapshotの取得時点とマージ時点の間にある保存履歴・利用者の判断を照合します。

## 帰属が足りない例と、分離判断の反映確認

- teal.fmの`fm.teal.thirdparty.feed.play`: 外部userによるrecordの実在は確認されたが、teal.fmによる定義・正式採用・委譲は確認できなかった。[Piper](https://tangled.org/teal.fm/piper)の機能や別NSIDのalphaからの移行だけでは、このNSIDをその旧版と判断できない。当時のauthority、schema record、公式参照、実装履歴が必要で、統合とidentifier処遇を保留する。
- Lexicon Garden周辺の4 fragment: `garden.lexicon.deeply-mouse.*`等のauthorityから導出されたservice候補と名前があっても、schemaを索引するLexicon Gardenが発行・所有する証拠にはならない。由来とproject帰属は未確定。
- Newdin Read: [公式告知](https://bsky.app/profile/newdin.com/post/3lfpr5axjns2m)が`read.newdin.com`を専用の入口と利用手順を持つbotとして説明していたため、本体からの分離を支持した。
- Untangled: [公式repository](https://tangled.org/ptr.pet/untangled)、入口`untangled.wisp.place`、`place.wisp.untangled.v1.watch`の定義・利用から独立クライアントとしての分離を支持した。
- ATProtoFans / high-five: 別レイヤとして扱う既知のoperator判断があった。GroundMist Library / GroundMist Syncは保存された構成だけで包括関係・別projectを確定できなかった。

Newdin Read・Untangled・high-fiveの分離とteal.fmの保留について、当時のレビュー判断を、2つのprojectの組に対するDBの判断記録へ反映したことまでは記録から確認できません。当時の判断を引き継ぎ、必要時に現在の判断記録を先に確認します。

## Candidateとconfirmedの一致に残る同一性の留保

2026-09-27の比較候補表示の受理は、各pairのmerge承認ではありません。次は保存情報による未確定例で、確認済み同一projectの成功例にしません。実際の処遇には現在の両projectの構成範囲と判断記録を照合します。

- WhiteWind（`proj:fec4b7da44a2`）／WhiteBreeze（`proj:3446d362d145`）: `https://whtwnd.com`のservice一致に対し、利用者が同一視への懸念を表明。
- Cartridge同士: 一致repoのほかに`nulfrost/bloc`もあり、利用者が同一性を留保。
- tapfall（`proj:698b749f3e60`）／skyfall（`proj:b8d039b334d8`）: `https://tangled.org/mackuba.eu/tapfall`の候補混入か、同じ説明・追跡単位かが未確認。
- atproto-identity-rs（`proj:634f6bc6b106`）／atproto-plc（`proj:723b9cf13beb`）: `https://tangled.org/smokesignal.events/atproto-identity-rs`の一致だけで同じ作者の関連作品を統合しない。

Rito同士のrepo・service一致やBookHiveのミラー説明も、保存情報の比較材料まででmergeの正解ラベルではありません。

## この資料の役割が終わる条件

設計材料は、新しい関係表現を採用するか既存の保存形で足りるか判断し、必要な契約・理由・代表例を仕様へ反映した時に閉じます。個別の留保は、現在の資料で判断してDB等の適切な記録へ反映した時、または追う必要がなくなった時に一件ずつ外します。未確認の例をまとめて解決扱いにはせず、残る少数の確認事項はTODOへ集約できます。設計の決着と全例の処遇完了を互いの前提にせず、両方の役割がなくなれば本資料を削除します。
