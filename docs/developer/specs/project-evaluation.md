# Projectの説明・評価・追跡

調査で得た根拠から、projectをどう説明し、紹介候補として扱い、継続して追跡するかを決めます。次の判断をそれぞれ保持します。

| 判断 | 答える問い | 主な結果 |
| --- | --- | --- |
| 説明・分類 | 何をするprojectか | 名前、紹介文、category |
| 公開・観測・利用 | 公開条件、保存した調査材料、確認範囲、atprotoが担う役割 | service status、観測記録、理由付き利用範囲注記 |
| 技術評価 | 設計・実装上、何を深掘りしたいか | 暫定評価と理由、技術候補 |
| 紹介候補 | どれを先に確認するか、個別にどう扱うか | 紹介スコア、手動優先度・保留 |
| 追跡 | 今後も調査を続けるか、どの頻度で扱うか | tracking state、再調査対象 |

通常候補はserviceまたは採用済みLexiconの根拠から選び、技術面から探す入口は別に設けます。説明が未記入でも候補になり、serviceがliveであることや情報が揃っていることだけで紹介可否を確定しません。LLMは説明・分類の補助に使い、観測に基づく稼働判定とoperatorの判断を優先します。resourceの採用は別の[帰属・採用規則](project-boundaries.md)です。

以下では説明の更新、公開・観測・利用、紹介候補、追跡の順に、各判断の細則を示します。

## 説明と分類

### LLM出力の適用境界

project全体の根拠を入力に、説明・分類・調査上の要確認事項を補うLLM処理をtriageと呼びます。観測を収集するcollector、repository一件の内容を解析するrepository analysisとは入力・出力を分けます。

- LLM が直接埋める主な field:
  - `name`
  - `tagline`
  - `summary`
  - `features`
  - `inspired_by`
  - `category`
  - `classification_reason`
  - `exclude_reason`
  - `watch_reason`
  - `manual_review_*`
  - `confidence`
  - `service_status` の補助更新
  - `lexicon_scope`
  - `technical_interest_level` / `technical_interest_reasons` / `technical_interest_summary`
- ただし LLM 出力の identifier は保守的に扱います
  - `project_repository_urls`
    - 既に confirmed 済みなら confirmed 側に残す
    - 新規提案なら candidate に留める
  - `service_urls`
    - 既存の confirmed service と一致する時だけ confirmed 扱い
    - それ以外は candidate
  - `lexicons`
    - 既に `resolved_nsids` にあるものだけ採用

`service_status` についても、LLM は補助更新に留めます。最終的な保存値は rule-based probe の evidence を優先し、特に `unverified` は安易に `live/private` へ確定させません。

説明は紹介文の下書きになることを目標とします。現状では個別確認へ進む判断材料として使われることが多く、下書き品質を達成した状態ではありません。人の訂正を引き継ぎ、同じ手直しを繰り返さず、新しい事実で正しく更新したいという期待に対し、以下の編集保護は現行の対応です。更新停止自体を目的にはしません。[品質と方式の残件](../todo.md#説明更新の品質とservice監視)を参照してください。

### 保存済みprofileの更新

`tagline / summary / features`は、projectについて蓄積した理解を一つの説明として保ちます。最新の取得結果をそのまま説明にしません。用途、機能、技術的特徴を中心とし、終了が確認できた場合は過去形で残します。機能変更後も理解に役立つ特徴は「当初は」など時点を区別して織り込み、元から誤っていた記述は過去の正しい事実として残さず訂正します。取得失敗と確認日時は別の観測表示で扱います。説明の全文版履歴は新設しません。

説明更新の品質は3項目全体で確認します。終了の一文を末尾に加えても、taglineや本文冒頭・特徴一覧が終了した用途を現行機能として示していれば不整合です。部分終了では現行機能と旧機能を分け、過去の保存先・schema・実装を新機能へ根拠なく移しません。「記述なし」「未確認」を不存在や非使用の断定にせず、元からの誤記と実際の時間変化を区別します。現在も公開されている定義や、理解に役立つ過去の技術的特徴も保持対象です。これは意味上の受入条件であり、promptで要求することや参照検査の成功だけで達成済みとは扱いません。

triageへ現在の文面を`current_profile`、内容を持つ根拠を`description_sources`として渡します。根拠は最大8件で、confirmed repositoryの根拠付き・非stale解析、projectを支持する成功したservice page／匿名browser page、resolved Lexiconのschema sampleから作ります。URLや取得時刻が増えただけでは根拠の内容が変わったことにしません。同じURLの内容変更も入力にできます。候補repository、取得失敗、既存の説明文はこの根拠集合に入れません。

既存説明を変えるには`description_change.kind=correction / changed_facts / expansion`、具体的な変更理由、入力に存在する根拠IDを1〜4件必要とします。`wording_only / insufficient_evidence`、参照不正、理由欠落では維持します。本文が長くなったこと、source URLの追加、過去の成功だけでは更新しません。初回の空の説明は従来のproject evidenceから生成できます。特徴一覧の変更は追加専用ではなく置換とし、残すべき過去の特徴は時点を明記して提案に含めます。

新規に機械生成した説明は自動反映できます。人が編集した説明、来歴が未記録の既存説明、最後に記録した文面と異なる説明は保護し、根拠と理由付きの更新案1件を保持します。Editで採用・訂正・見送り、または明示的な自動反映の許可ができます。同じ根拠・同じ文面の見送り案は再提示しません。採用した案の理由と根拠は直近の更新来歴に残します。初回生成以外では、LLMが変更の意味と根拠の十分性を判定し、コードは種別・参照・編集保護を検査します。参照が存在することだけで内容の真偽を機械的に証明するものではありません。

更新案は`editorial_refresh`、利用面の到達変化や保存範囲のページ内容変化は`service_observation_changed`としてreviewへ接続します。ページの差分は説明変更や終了の確定ではなく、追加調査の材料です。`out_of_scope`はこの2種の変化を通常Web reviewへ提示し、他の一般signalは提示しません。観測・説明更新はtracking stateを自動変更せず、既存の追跡レビューと手動変更を別に扱います。browser確認だけではtriageを実行せず、保存根拠を次回の明示／通常enrichへ渡します。

成功したtriageの入力は`llm_source.evidence`へ保存します。既存データの自動作成／人の編集を推定して一括移行せず、内容と自動反映の許可を個別に確認します。保存形は[Project record](project-record.md#説明の編集制御と観測時点)を参照してください。

`llm_output.name`は、直近に成功したLLM triageによる名前の提案です。成功したtriageを適用する時は、`name_candidates`に残る以前の`source=llm_output`候補を最新の提案で置き換えます。collectorまたはLLM呼び出しが失敗して新しいoutputを適用できなかった場合は、直前の提案を維持します。operator edit、service title、resource title、repository名など、LLM以外に由来する名前候補はこの置換の対象外です。

`finalize_project_shape()`も保存済み`llm_output.name`とLLM名候補を同期してから代表名を再評価します。このため、旧実装で複数のLLM名候補が蓄積したprojectは、通常の保存または`repair-db`で現在提案だけに補正されます。過去のLLM出力を調べる必要がある場合は、current stateの候補配列ではなくinvestigation runを参照します。

### Name候補

- repo seed / GitHub repo metadata 由来の repo slug は、短い汎用語へ切り詰めずに全体を名前候補として使います
  - 例: `atproto-visualizers` は `atproto` ではなく `atproto-visualizers`
- `Bluesky` / `atproto` / `bsky` のような汎用プラットフォーム名は、service title / resource title / LLM / lexicon 由来だけなら project name として採用しません
- ただし `https://bsky.app` の title が `Bluesky` であるように、source URL の host が名前と強く対応する場合は候補として残します
- ただし repo 自体がその名前を明示している場合は、repo 由来候補として別扱いします
- `Error` / `Not Found` / `Forbidden` のような汎用エラーページ title は、service/resource title および既存 name から除外します
- service/resource title と既存 name は title cleanup を通して評価します
  - 例: `Relay.app: Turn plain English into reliable automations` は `Relay.app` として扱い、tagline 部分を project name に残しません
- NSID authority domain から推定した URL の title は、NSID 固有 token と対応しない場合は name 候補から除外します
  - 例: `com.hectorsector.book.status` 由来の `https://hectorsector.com` が `@hectorsector.com on Bluesky` を返しても、`book` / `status` と対応しないため project name には採用しません
  - 同じ値が既存 `name` や LLM output にコピーされている場合も、元の service title 候補と照合して除外します
- LLM などが full NSID そのものを name として返した場合は弱い候補として除外します
  - lexicon name 由来の tail 候補は別扱いで残します
- `Home` / `About` / `Docs` / `Login` のような短いページ役割名は、service/resource title 由来なら name 候補から除外します
  - 同じ値が既存 `name` や LLM output にコピーされている場合も、元の service/resource title 候補と照合して除外します
  - repo 由来など、ページ title ではない経路の同名候補はこの rule では除外しません
- service/resource title 由来の名前は、title の意味 token の大半が seed / repo / NSID / authority domain に対応する場合だけ採用します
  - 例: `Book Status` は `com.hectorsector.book.status` と対応するため残します
  - 例: `Browse Listings` は `listing` だけが対応し、`Browse` がページ操作名なので除外します
  - 汚染値が既存 `name` や LLM output にコピーされている場合も、元の service/resource title 候補と照合して除外します

### 分類と紹介候補signal

LLM triageは収集済みの根拠を整理し、説明と分類候補を補います。

triageの入力上限と縮約規則は[LLM evidence budget](investigation.md#llm-evidence-budget)を正本とします。

unconfirmedな`candidate_project_repository_urls`はtriage evidenceから除外します。検索語の固定生成にも候補URLを使いません。candidate repositoryのdescription、名前、lexicon hint等はconfirm前にはevidenceとして保存・適用しません。

説明系fieldは日本語で生成し、`tagline`は助詞を含まない短い名詞句、最大30文字とします。

NSID seed は、それだけでは共有 lexicon の根拠にしません。`lexicon_scope` は次の 3 値で保持します。

- `shared`
  - `standard.site` のように、第三者実装や相互運用を意図した共有 schema と判断できる
- `service_specific`
  - namespace と service / collection 面が同一 project を指している
- `unknown`
  - service がまだ見つかっていないだけか、共有 lexicon として設計されているかを区別できない

`category=lexicon` の project で `lexicon_scope=service_specific` になった場合は `category=project` へ戻します。`unknown` は無理に確定しません。LLM prompt にも、service URL 未発見を shared の根拠にしないことと、service の実態がある場合は schema 説明だけに縮退させないことを明記しています。

#### Categoryの選択

category値の意味は[Category](project-record.md#category)を正本とします。CLIやpluginなどの配布形式だけでは決めません。一般利用者が直接使うCLIや拡張機能は`project`、開発者が依存packageとしてcodeへ組み込むpluginは`library`、既存serviceや外部toolへ導入してATProto連携を追加するpluginは`infra_tool`です。ATProto自体を扱うCLIや運用componentも`infra_tool`です。既存projectに別物のURLが混入した場合はidentifier関係の欠陥であり、projectを`reject`にしません。

停止、削除、archiveはcategoryを変える理由にしません。過去のcategoryを確認できる場合は維持し、削除後に初めて発見して元の役割を復元できない古い対象では`name_only`を許容します。

複数の説明が当てはまる場合は、対象外なら`reject`、役割不明なら`name_only`、固定利用者なら`personal_tool`、文書化された共有schemaなら`lexicon`、組み込む部品なら`library`、projectの背後で動くかATProto自体を扱う実行物なら`infra_tool`、一般利用者が直接使うものなら`project`の順で確認します。

生成したsummaryとcategoryが矛盾する場合は不整合として扱います。`current_profile`は既存の編集文面であり、categoryの根拠にはしません。観測から役割を決めた後に、現在の文面を維持するか判断します。

#### 紹介候補signal

技術的な学習価値は、実用性や紹介準備とは別軸で `technical_interest_*` に保持します。LLM は evidence に基づいて `none / notable / high / exceptional` を暫定評価し、理由を enum と短い説明で残します。`promotion-queue` の `technical_insight_candidate` は `high` / `exceptional` だけを対象にします。`notable` は将来の再評価材料として保存しますが、推薦 signal にはしません。

サービスの公開条件と確認範囲を分けます。`category=project / infra_tool`でサービスURLを持ち、`live / beta / invite_only`かつログイン入口または公開閲覧画面の根拠がある候補は`service_surface_confirmed`です。名前のconfirmedは入口の観測を意味し、認証後の利用成功は保証しません。`library`はdocs URLをserviceと誤認しないよう対象外です。

`unverified / none`、HTTP 200だけ、app shell、authority hostのtitle、beta・招待の案内だけの候補も除外せず、`service_candidate_needs_validation`として残します。配点は[紹介候補の表示・順位・保留](#紹介候補の表示順位保留)のサービス成分に従い、自動priorityを通常順位に使いません。抽象的なconfidenceでは区別せず、匿名調査で確認した範囲を使います。全件のログインや機能試験は必須にしません。backend障害とlanding-onlyは静的probeだけでは安定して分けられません。

`service_assessment`は保存材料から読む投影で、`publication`（従来の公開状態）、`surface`（`login / public / app_shell / announcement / documentation / unconfirmed / failed / not_observed`）、URL・方法・日時別の`observations`を返します。確認済みサービスURLごとに最新のbase/browser観測を使い、取得失敗を古い入口確認で隠しません。対象観測がない旧データは保存済み評価の根拠を使い、方法を`saved_evaluation`、時刻を未記録と明示します。`usage_tested=false`は利用試験を判定していない意味です。confidenceはcollector・既存レビューの診断材料として保持しますが、利用者向けの確信度表示と紹介scoreの加点には使いません。

理由 enum:

- `novel_design_pattern`
- `protocol_gap_exposed`
- `interoperability_experiment`
- `unusual_architecture`
- `implementation_reference`

## 公開状態とatproto利用

### Service statusの評価

`service_status`はscalar column、confidenceとevidenceは`state_json`の評価detailに保持します。

- `service_status`
- `service_status_confidence`
- `service_status_evidence`

現在見ている主な signal:

- `http_<code>`
- `dns_failed`
- `dns_temporary_failure`
- `tls_failed`
- `timeout`
- `connection_refused`
- `parked_domain_detected`
- `login_surface_detected`
- `invite_text_detected`
- `beta_text_detected`
- `coming_soon_detected`
- `docs_only_surface_detected`
- `app_shell_detected`
- `mixed_launch_signals_detected`

単一probeの分類（projectの現在値は次節で別に投影）:

- parked domain, `404/410`
  - `dead`
- `401/403`
  - `private`
- coming soon / launching soon
  - `unreleased`
- invite / waitlist
  - `invite_only`
- beta 文言
  - `beta`
- login surface / app shell
  - `live`
- docs-only
  - `none`
- `5xx` や network 系失敗
  - `unverified`
- launch 文言と app surface が混在
  - `unverified`

#### Service statusの現在値

project直下の`service_status / service_status_confidence / service_status_evidence`は、保存済みの`service_verification_results`を初期値なしで再評価するcurrent projectionです。過去scalarのmax / unionは入力にしません。

- current resultは`lane + requested_url`ごとに最新1件を使う
- supporting pageの`follow_up`と、static / deepのsupporting page URLはproject scalarへ入れない
- 同一URLのbase laneは観測日時を優先し、同日時・時点未記録同士では`deep > static > authority`を使う
- browserでloginまたは公開app surfaceを確認したURLは、そのbrowserより新しいbase観測がない場合に`live / high`とする。同じURLの`docs_only`、`coming_soon`、mixed launch分類を除き、`beta / invite_only`は維持する
- より新しいbase観測で利用面が確認できない場合、古いbrowser成功で`live`へ戻さない。より新しいbrowser試行で利用面が確認できない場合も`unverified / low`とし、最後の成功時点は別に残す
- 確認済みservice URLのHTTPエラー・取得失敗・parked判定は現在値を`unverified / low`にする。単一probeの`dead / private`分類やLLMの同値提案だけで、提供終了・非公開を自動確定しない
- static / deep / authority resultはcollector時点の`supports_project`を保存して利用する。旧resultにこのfieldがない場合は、manual service host、確認済みservice host、またはそこへの確認済みsame-site redirectに限定して復元する
- 対象となるbase/browser observationがない場合は、LLM等が設定した既存scalarをprojectionで消さない

projectionはnetwork、browser、trust graph更新を行いません。`repair-db`は変更件数とbefore/after例をdry-runで報告し、investigation runのinput/output/bundle/evaluationは更新しません。


probeがprojectを支持し、検証済みauthorityの完全一致hostが正常応答してtitleまたはdescriptionを持つ場合は、汎用classifierの`unverified`を`verified_authority_service_surface`付き`live`へ補助更新します。`none`、`unreleased`、`beta`等の明示分類は上書きしません。authority hostの一致だけでidentifier採用はしません。

匿名browserのlogin到達は認証後の利用を保証せず、runtime/network errorだけでは一時障害と恒久停止を区別できません。`unverified`は`dead`や`private`の確定結果ではありません。

#### 要レビュー一覧の評価入力

要レビュー一覧・対話batch・単件項目は、詳細と同じ正規化済みの完全runtime viewから寄与度・review signal・順位を計算します。保存された評価値をそのまま表示するのではなく、識別子・関係根拠・手動判断を含む現在の保存材料に共通規則を適用します。関係根拠や認証専用フラグを省いて寄与度を再計算せず、公開Lexiconの変更通知とそのdismiss判断も保持します。一覧はproject本体・子行・更新番号を同じread snapshotで読み、各projectの正規化と保存用baseline作成は一度です。重複候補の再探索は行いません。

#### Service statusの要レビュー条件

LLM の `manual_review_recommended=true` は保存しますが、`manual_review_kind=service_status_manual_check`は単に `service_status_confidence=low` というだけでは review queue に載せません。

`llm_manual_review_recommended` signal を残すのは、手動確認の結果で次のいずれかが変わる時です。

- `service_status_resolution`
  - `service_status=unknown`で、利用できる状態かを解決する
- `introduction_validation`
  - 未紹介の `project` / `personal_tool` で、usable service の confidence が `none` / `low`
- `conflicting_service_status_evidence`
  - `mixed_launch_signals_detected` があり、status evidence が矛盾している

要レビュー一覧へ載せた理由はsignalの`details.action_basis`に残します。この絞り込みは`service_status_manual_check`に適用し、`identity_resolution`など他の`manual_review_kind`には適用しません。

### Atprotoの観測根拠

NSIDの採用状態とschema・record・利用relationの観測を、画面では「atprotoの調査材料」として扱います。採用済みの所有NSIDは外部解決の成功を意味せず、保存したサンプル件数は総数ではありません。詳細は件数、検索は代表する調査材料の種類を表示し、「寄与度」「中核的な利用」の総合評価と点数は表示しません。内部の`atproto_contribution_level / score / evidence`は既存codec・監査で使う派生表現で、`ensure_project_shape()`と`save_project()`で再計算します。scoreは紹介・要レビューの順位に使いません。

- `core` / `90`
  - 採用済みの所有NSID、lexicon schema sample、`defines_schema` relationのいずれかがある
- `substantial` / `70`
  - recent record sample、collection observation、`uses_collection` relationのいずれかがある
- `optional` / `40`
  - input NSID、external lexicon hint、`requests_permission / mentions_collection` relationのいずれかがあり、上位の材料や認証のみの判定がない
- `auth_only` / `20`
  - `atproto_auth_only=true`で、上記の強いprotocol surfaceが無い
  - 旧`category=auth_only`は正本DBで個別移行済みであり、読み取り互換を持たない
- `unknown` / `0`
  - rule-based evidence が無い

`incidental`は未接続です。この分類は実際の主機能利用、独立性、紹介価値を表しません。この派生scoreを紹介・要レビューの点数へ転用しません。所有NSIDの件数を使う紹介スコアは、後述の独立した配点です。

### 主機能でのatproto利用

`atproto_usage`はサービスの利用可能性・確認強度・技術的価値とは別の、理由付き手動注記です。未記録なら`unknown`です。手動・自動の利用範囲判断を紹介順位へ加減点しません。通常triageに利用範囲の総括を要求しません。

| role | 意味 |
| --- | --- |
| `main_data` | 主機能のデータ、またはprotocol基盤をatprotoで実装している |
| `supporting` | 主機能は別にあり、プロフィール等の補助部分だけに利用している根拠がある |
| `auth_only` | sign-inだけに利用している |
| `unknown` | 手動判断が未記録、または根拠不足という手動判断 |

OAuth宣言と関連記述は判断材料です。NSID、DNS確認、login画面、単独のschemaだけでは利用範囲を確定しません。実資料で要求権限と実際の保存を混同する誤りがあり、短い文面からの抽出でも再現したため、LLMによる自動確定は採用しません。手動判断は再調査でも維持し、明示解除すると`unknown`へ戻ります。未評価の既存projectに名前や過去の生成説明からroleを補完しません。

機能説明とcollection定義の意味の近さも、対象アプリが実際にそのcollectionを使う根拠にはしません。限定的な意味対応の実験でも、購読と注釈、表示設定と文書本体を同一視する誤りが残りました。引用の存在・連続一致は意味の正しさを保証せず、意味対応の自動出力を通常調査・順位へ追加していません。同じ話題や保存形式で表現できることを、機能の依存関係や主データの一致と扱いません。

roleは読取り・書込み・削除の操作別観測ではなく、repo宣言がないことから公開読取りの不存在を推定しません。旧`bluesky_client`の履歴は書き換えず、表示時に`role=unknown / focus=bluesky`へ投影し、理由を引き継ぎます。旧形式の`focus`は保存互換の注記で、`auto`を含め自動判別や順位変更を行いません。旧用途判断から利用の深さを補完しません。

### 紹介上の用途の扱い

他サービスへの依存と独立した用途の区別は選定上の関心ですが、Bluesky等の名称や説明文の用途表現から自動の低優先区分へ分類しません。通常候補は以下の配点で相対評価します。依存性そのものを自動判定した結果ではなく、手動の低優先・保留は別の明示操作として維持します。

技術評価は独立し、具体的なprotocol設計・相互運用・再利用できる実装上の学びを`high`以上の対象にします。通常のrecord保存・login・CRUD・言語の珍しさだけでは`notable`を超えません。補助的利用やBlueskyクライアントでも独立した学びがあれば技術候補になります。

## 紹介候補

利用者の「紹介予定」は、自動推薦と独立した理由付きの取り置きです。`plan_introduction`の保存・解除は紹介score、候補の表示可否、手動優先度、保留、紹介URLを変えず、専用一覧で有効な取り置きを回収できます。紹介済みや推薦対象外になっても自動解除しません。保存・merge・履歴は[サイト閲覧後の手動判断](project-record.md#サイト閲覧後の手動判断)を参照してください。

### 説明の未記入項目

説明（`summary`）と概要（`tagline`）の空欄は、詳細・紹介候補一覧に`description_missing`として表示します。情報充足の総合判定・派生status・昇格履歴は保持しません。どちらかに空白以外の文字があれば紹介スコアに一度だけ3点を加え、空欄だけで高優先度の編集レビュー要求を出しません。文章の質・正しさを採点するものではありません。

候補の表示可否は各入口のservice・Lexicon・技術signalと、追跡状態・紹介済み・保留等から判定します。識別子の帰属確認、稼働状態、operatorの保留判断はそれぞれの規則を使います。app record出力の適格性は別の[export検査](app-record-export.md)です。

選定の目的は、現在の内容の価値と、速報性・今後の発展への期待を含む「今紹介する価値」を拾うことです。以下は現行の選出・配点であり、この目的への適合を確認済みとはしません。新規登録順を人が見て補う負担の軽減、実際の登場時期とDB登録時期の区別は[選定の残件](../todo.md#atproto寄与度と紹介候補の順位)です。

### 紹介候補の表示・順位・保留

`promotion-queue`の通常候補はserviceまたは採用済みLexiconのsignalを持つ対象です。手動の`priority_group`、`sort_score`降順、`project_key`の順で全体を並べてから`--limit`を適用します。自動signalのpriorityや用途による別区分を並び順に使いません。技術だけを根拠に通常候補へ入れず、技術候補viewでは技術score順を使います。確認履歴は品質scoreを増減しません。

CLIとWebの通常候補は既定で`active`と`stable`を含み、`stalled`を含めません。紹介済み、明示した対象外、保留、低優先度の扱いは追跡状態とは別に評価します。CLIの`--tracking-states`とWeb APIの`tracking_states`で明示した場合は、その指定を使います。

採用済みの`owned_nsid:confirmed`があれば`lexicon_definition_confirmed`、うちDNS委譲先からのschema解決を有効な検証記録で確認できるものがあれば代わりに`lexicon_publisher_confirmed`を返します。`reject / name_only`を除きcategory横断で適用し、`personal_tool`は`--include-personal-tools`指定時だけ対象にします。候補NSID、利用・参照NSID、保留・除外中のNSIDは数えません。採用は帰属判断であり、新規登録起点の採用を含め、それ自体を外部解決の証明とは扱いません。

通常の紹介スコアは次の5成分の合計で、最大96点です。配点枠は軸間の重みを表し、品質合格率ではありません。説明と公式repoの補助点は合わせても6点で、解決済みLexicon3件分、または活動の隣接する期間区分の差に相当します。画面では「Lexicon」「サービス」「活動の新しさ」「説明」「公式リポジトリ」と点数を表示し、配点ルールはⓘから開きます。技術評価・手動の優先度は別項目として扱います。

| 成分 | 配点 |
| --- | --- |
| `lexicon` | 採用済みの異なるNSID数＋うち解決確認数。各1点、合計最大40点 |
| `service_evidence` | 確認済みsignalのうち、招待制は12点。それ以外は公開画面20点・ログイン入口16点。要確認signalは8点、signalなし0点 |
| `recent_activity` | 最新活動が3日以内30点、7日以内24点、14日以内18点、30日以内12点、60日以内6点、それ以外・未観測0点 |
| `description` | summaryかtaglineに記述があれば3点。両方あっても3点 |
| `repository` | 採用済み公式repoが1件以上あれば3点。候補・手動seedだけ・保留・除外中のrepoは対象外 |

サービスは確認範囲とアクセス条件を一つの成分で扱い、statusを別加点しません。公開画面・ログイン入口は保存済み観測から判定し、betaとliveを同じ条件で評価します。招待制は入口確認済みでも12点に抑え、招待案内だけなら要確認の8点です。認証後の主機能の利用成功や誰でも登録できることまでは証明しません。公式repoの加点は採用済みリンクが調査の入口になることへの補助点で、現在の到達性・build成功・コード品質を保証しません。

解決確認には`collection_trust.verified_publications`の保存済み有効記録を使い、最新の失敗・型変更・publisher変更・catalogの無効化を尊重します。schema本文サンプルの最大12件には依存しません。有効な検証記録がない旧形式・未解決のNSIDにも採用の1点は残し、追加1点を推測で補いません。NSID内のdefs数や同じNSIDの重複は数えません。

活動はrepository更新・collection活動の最新日時を一度だけ使います。collectionの`max`に加え、`recent_count>0`の観測日時も既存の活動根拠です。DB登録日・説明更新日をサービス登場日として加点せず、repoとrecord別の追加点も付けません。未観測を古い活動と断定せず0点とし、180日等の負点は設けません。技術評価、confidence、taglineの暫定フラグ、NSID hint、review状態、利用範囲・用途注記は別加点しません。レビュー推奨は豊富な資料等への追加確認にも使うため、その不存在を準備完了とみなしません。

`--view technical` / Webの「技術候補」は、eligible・通常優先度・未確認のうち`technical_insight_candidate`を持つ候補を全件から抽出し、技術scoreとproject keyで並べてからlimitを適用します。service・Lexiconの有無や通常スコアをこの並び順に使いません。閾値は`high / exceptional`と具体的な技術説明を維持します。通常候補へも載るには、別にserviceか採用済みLexiconの根拠が必要です。技術評価自体は詳細でも確認できます。

promotion policyは次を混ぜずに投影します。

- `candidate_visibility`: `eligible / deferred / excluded`。紹介済み、`out_of_scope / ignore`、activeな`defer_promotion`のようなproject全体の表示可否を表す
- `service_evidence_group`: `confirmed / needs_validation / not_applicable`。ログイン入口・公開閲覧の観測の有無を表し、機能の利用成功や紹介可否は含めない
- `service_assessment`: URL・方法・日時と確認範囲
- `priority_group`: `normal / lowered`。`lowered`はnormalized decisionの明示判断だけから投影し、project名・説明文・service evidenceから自動推定しない。理由メモは任意
- `score_breakdown`: 上記5成分と`total / maximum / lexicon_counts / activity_age_days`。`total`を`sort_score`として使う
- `attention`: 明示した確認記録から`unreviewed / recorded / held / changed / due`を投影する。未記録・判断記録あり・保留中・根拠変化・期限到来を区別する。有用性や実際の未閲覧を表さない

`defer_promotion`はproject全体を期限または根拠変化まで通常の候補表示から外します。一方、serviceの`needs_validation`は候補を消さず、サービス成分8点のsignalとして残します。

Webの`Promotion`は既定の「確認対象」でeligible・通常優先度の候補を表示します。「技術候補」「保留」「Lowered / 特集候補」「すべて」へ切り替えられ、理由の部分一致検索・同じ理由の件数・ページ送りでlimitより先も回収できます。専用一覧は通常のtracking state範囲外や推薦signalがなくなった保存候補も表示し、元の追跡・表示状態を示します。「すべて」は現行signal候補とこれらの保存候補の集合であり、全登録projectではありません。

`lowered`と`waiting`一覧は完全なproject復元の前に、それぞれ`lower_promotion_priority`と`defer_promotion`の判断履歴を持つprojectへ読込み対象を絞ります。保留へ読込み変換する旧確認期限も`waiting`の対象に含めます。解除済み・期限切れ履歴も選び、有効な判断・metadata・根拠変化の解釈は復元とpolicyへ委ねます。全viewでproject本体・子行・更新番号を同じread snapshot内で復元し、正規化済みの完全runtime viewから推薦を計算します。同じrequest内で本体を再読込みせず、shape正規化と保存用baseline作成は各projectで1回です。全tracking stateからの回収、全体の順位、理由検索前の`reasons`集計、検索後の`total`、最後のoffset/limit適用を維持します。応答や評価済みprojectをrequest間でcacheせず、再取得ごとに現在のDBと期限を評価します。

cardは主機能での利用範囲と理由・技術評価の理由・サービスの入口確認、手動優先度、確認記録、追加確認理由、紹介スコアを表示します。詳細の「調査材料と評価」で確認範囲と点数内訳を読み、詳細上部の同じ場所で保留・Lower priority・Skipを操作します。保存後は表示中のcardを外し、Refreshで先頭から再計算します。単なる画面取得・Refresh・SkipはDBを更新しません。

Lower priorityは単体で先に扱わない判断であり、同じ理由をまとめて特集する候補にも使います。理由の編集は旧decisionを解除済みとして残し、新decisionを追加します。通常優先度へ戻す操作も履歴を消しません。明示したloweringは根拠が変わっても自動解除せず、専用一覧で再評価できます。

設計比較では、既存Lower priority＋検索可能な理由を特集backlogとして採用しました。例えば「認証のみ」の対象を複数保存し、同じ理由で検索してまとめる運用は既存の1操作で足ります。独立topicなら通常優先度のまま複数テーマに属させられますが、今回の単独紹介を後に回す用途では管理項目が増えます。順位を保った複数テーマ分類が必要になれば分離を再検討します。

マージ元（source）と存続先（target）の両方に有効な紹介候補の優先度判断がある場合はtarget側を維持し、source側を解除済み履歴として残します。

`promotion_deferrals`の未解除・期限内entryは、fingerprintが無いか現在値と一致する間だけ保留として効きます。未指定時の保留期間は一律30日です。`--days`またはAPIの`days`を優先し、理由は省略・空文字を許可します。Webは前回理由を編集可能な初期値にしますが、空欄で確定すれば理由なしとして保存します。理由文の条件を機械的に理解して実装完了等を確定する処理はありません。

### 保留候補と根拠変化

保留は、見終えた候補や追加情報を待つ候補を通常一覧から一時的に外す可逆的な操作です。CLI・Webとも期限到来、根拠変化、明示解除で通常の選別対象へ戻します。他の除外条件やLower priorityは独立に維持します。例えば55件中先頭50件を保留すると、通常一覧は残る5件となり、50件は保留一覧から回収できます。長期表示だけからの自動減衰は、表示履歴がなく未利用と区別できないため採用しません。無操作では順序が変わらない代わりに、未閲覧の恒久判断を作りません。

fingerprintのversionは`promotion-attention-v1`です。category、service状態・collectorのconfidence・利用面の根拠、従来の観測根拠分類、technical interestのlevel/reasons、lexicon scope、確認済みservice/repository/NSID、resolved NSIDのschema内容、features、summary/tagline/featuresの有無を比較します。利用範囲・旧形式focusの手動注記の変更も再確認の契機にします。通常の候補は以前のfingerprintを維持し、一律の保留解除をしません。集合の順序、取得時刻、activity件数、HTML/README本文hash、summary/taglineの言い換えだけでは復活させません。featuresの文言変更は意味が同じでも再提示し得るため、精密な意味判定ではなく保守的な再確認条件です。未収集の機能変化、これらのfieldへ反映されない変化は検知できず、期限と手動復帰が補います。

保存するのは直近の判断材料・操作・累積操作件数であり、過去の順位や表示session全体ではありません。未確認・変化は有用性そのものではなく、既知の無変化候補による占有を避ける条件です。既存の品質signal・scoreを同じ再評価group内で維持します。保存契約は[確認記録](project-record.md#promotionの確認記録)を参照してください。

## 追跡と再調査

### review-stalled

`review-stalled`は、活動の変化に応じて次の追跡状態への変更候補を返します。

- `active -> stalled`
  - repositoryとcollectionの活動・調査鮮度・理由の組合せを、以下の条件で評価する
- `active -> stable`
  - collection は最近動いているが repo 側は止まっている
- `stalled -> active`
  - repo または collection に最近 activity が戻った

`service_status=unknown/none`だけでは`stalled`を提案しません。repositoryとcollectionの活動状況を優先し、service statusのうち`dead/private`を強いシグナルとして扱います。

#### activity の見方

- repo activity:
  - `source_repo_records[].updated_at` の最大値
- collection activity:
  - `collection_observations[].max` の最大値
  - ただし `recent_count > 0` の observation があれば、その `observed_at` も recent activity とみなします
- `activity_is_recent()` は `age < inactivity_days` の時だけ recent 扱いです

#### `stalled -> active`

- いま `tracking_state=stalled` の project は、次のどちらかが recent なら `active` に戻す候補です
  - recent repo activity
  - recent collection activity
- 片方だけ戻っていても `active` 候補にします

#### `active -> stable`

- 次の条件を満たす時だけ `stable` 候補です
  - いま `tracking_state=active`
  - repo は recent ではない
  - collection は recent
- これは「ソースコード更新は止まっているが、collectionはまだ実運用されている」projectを継続調査の対象に保つためです

#### `active -> stalled`

- まずrecent repo activityがあるprojectは`stalled`候補の対象外にします
- そのうえで、collection も recent でない時だけ stalled 判定に進みます
- `last_enriched_at` が無い、または `max_enrich_age_days` 以上古い場合は、停止を判断する材料が古いため降格候補から外します
- strong signal (`hard_signal`) として扱うもの:
  - collection が `inactivity_days` 以上止まっていて、しかも `recent_count>0` の observation が無い
  - repo が `inactivity_days` 以上止まっている
  - `service_status` が `dead` または `private`
- weak signalとして理由には出すが、単独では`stalled`を提案しないもの:
  - `service_status=unknown` または `none`
  - confirmed identity が 1 件以下
    - `website_url`
    - `project_repository_urls`
    - `resolved_nsids`
- `stalled` を提案するには:
  - `hard_signal` が少なくとも 1 つ必要
  - 理由が 2 件以上必要

#### 調査鮮度ゲート

- `max_enrich_age_days`は、`stalled`判定に使える調査結果の最大経過日数です
- 未enrichまたは期限超過の `active` は `stalled` 候補を返しません
- recent activityによる `stalled -> active` と `active -> stable` は、調査鮮度ゲートより先に判定します

#### review-stalled の対象範囲

- `review-stalled` が見る tracking state は:
  - `active`
  - `stalled`
- `stable` / `out_of_scope` / `archived` / `ignore` はこの自動判定の対象外です
- `--execute` を付けると、提案された `suggested_tracking_state` をそのまま保存します

### review-out-of-scope / review-ignore

`review-out-of-scope`は、atprotoとの関連を保持しつつ、現在の紹介・重点調査のスコープ外にする候補を返します。以下の`owned protocol surface`は、`project_has_owned_protocol_surface()`が見るNSID・NSID候補・namespace・collection活動観測の有無を指します。所有がconfirmedであることだけを条件にする語ではありません。

- `auth_only_without_owned_protocol_surface`
  - `atproto_auth_only=true`
  - owned protocol surfaceがない
- `derivative_bluesky_utility`
  - `service_is_derivative_bluesky_tool()` が真
  - `introduced_url` がない
  - owned protocol surfaceがない
  - `category=lexicon` ではない

スコープ外への変更案を適用したprojectは`tracking_state=out_of_scope`になります。この状態は通常のenrich・追跡状態レビュー・紹介候補一覧の対象外ですが、マージ候補計算には含め、明示enrichと手動復帰を許します。その後のsyncで新しいNSID等が既存projectへ紐づく経路も維持します。

`out_of_scope` projectにowned protocol surfaceが見つかった場合、同じコマンドは `owned_protocol_surface_detected` / `suggested_tracking_state=active` を返します。`--execute` なしでは更新せず、復帰も確認可能なqueueとして扱います。sync/enrichの途中で自動復帰する処理は未接続です。

`review-ignore`は、無関係・誤登録・独立した同一性を持たない複製など、無視するprojectの候補を返します。

現状の kind は次です。

- `fork_without_independent_identity`
  - confirmed な project repository が GitHub fork
  - `website_url` / `service_urls` / `introduced_url` が無い
  - owned protocol surface (`resolved_nsids`, candidate `input_nsids`, `collection_observations`) も無い
  - `service_status=live|beta|invite_only` なら除外

目的はatproto無関係、誤登録、独立identityのない複製など、通常の再評価を期待しない候補を `tracking_state=ignore` へ寄せることです。既定では dry-run で JSON を返し、`--execute` を付けたときだけ保存します。

### 調査対象の選択

`enrich --all`は既定で`tracking_state=active`だけを対象にします。`stable`、`stalled`、`out_of_scope`は明示的にincludeした場合だけ調査します。
