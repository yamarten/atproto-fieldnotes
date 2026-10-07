# Project境界・時間関係の設計材料

[TODOのproject間の時間・派生関係](../developer/todo.md#project間の時間派生関係)で、改名・継承・fork等を表示・検索・紹介文へどう反映するか、既存のidentifier履歴や分離判断で足りるかを検討する資料です。保存形とreview操作の選択肢、URL移転・namespaceの解釈で区別すべき代表例を残します。

2026-08-11〜2026-09-27の調査・reviewに残った材料であり、現在のDBや公開先を再確認した結果ではありません。設計は必要な表示・検索・説明が具体化した時に再検討します。事例は設計判断の対照であり、全件の処遇確認を始めるbacklogではありません。

現行・非現行の識別子、採用理由、確定した移転関係（transition）、再登録の抑止情報（exclusion）は現行schemaで保存できます。ここで未決なのはproject間の関係表現と、個々の事例をどの関係・状態として扱うかです。

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

## Identifier処遇とproject境界を分ける

collection断片を包括projectへ収める判断と、各identifierの現行・旧構成要素（former）・開発試験用（non-production）・無関係な誤検出（noise）の判断は別です。`temp`・`experimental`・`dev`等の名前だけで、実recordを持つNSIDをnoiseにしません。提供終了もproject/serviceの時間状態として扱い、NSIDのnoise判定へ置き換えません。

ATlasの[公式commit](https://github.com/lawhiteley/ATlas/commit/2328ac267dd55c7e4937f36d0ec6695308a06c62)にはLexicon定義と実装の`io.whiteley.luke.ATlas.pin`→`io.whiteley.ATlas.pin`変更があり、旧側former・新側currentを支持します。名前の似たnamespaceが並存するだけの例と、公式な移行根拠がある例を区別します。

## 所有・分離・同一性を判断する材料

第三者によるrecordの実在は、そのschemaをprojectが定義・正式採用・委譲した証拠ではありません。schemaを索引するserviceも、索引先schemaの発行・所有者とは限りません。authority、公式参照、定義・利用の実装を確かめます。

Newdin Readの[公式告知](https://bsky.app/profile/newdin.com/post/3lfpr5axjns2m)は、専用の入口と利用手順を持つbotを説明しています。Untangledは[公式repository](https://tangled.org/ptr.pet/untangled)、専用の入口、`place.wisp.untangled.v1.watch`の定義・利用を持ちます。こうした独立した提供面は分離の判断材料になります。一方、repositoryやserviceの一致だけでは、候補の混入と同じ説明・追跡単位であることを区別できません。

## この資料の役割が終わる条件

新しい関係表現を採用するか既存の保存形で足りるか判断し、必要な契約・理由・代表例を仕様へ反映した時に閉じます。個人DBでの全例の処遇完了を、設計判断や本資料の削除条件にしません。
