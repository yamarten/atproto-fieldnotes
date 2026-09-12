# Projectの説明・評価・追跡

調査で得た根拠から、projectをどう説明し、紹介候補として扱い、継続して追跡するかを決めます。
これらを一つの総合判定にまとめず、次の判断を分けて保持します。

| 判断 | 答える問い | 主な結果 |
| --- | --- | --- |
| 説明・分類 | 何をするprojectか | 名前、紹介文、category |
| 稼働・寄与度 | 利用できる状態か、atprotoに何を提供するか | service status、atproto寄与度 |
| 紹介候補 | 紹介の前提が揃っているか、どれを先にreviewするか | 昇格ゲート、queue、順位・保留 |
| 追跡 | 今後も調査を続けるか、どの頻度で扱うか | tracking state、再調査対象 |

たとえばserviceがliveであることだけでは紹介候補にせず、説明や寄与度などの条件も確認します。
LLMは説明・分類の補助に使い、観測に基づく稼働判定とoperatorの判断を優先します。
resourceをそのprojectに属するものとして採用する判断は、これらの評価と別の[帰属・採用規則](project-boundaries.md)です。

以下では説明の更新、稼働・寄与度、紹介候補、追跡の順に、各判断の細則を示します。

## 説明と分類

### LLM出力の適用境界

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

### 保存済みprofileの更新

LLM triageのevidenceには、現在保存されている`tagline` / `summary` / `features`を
`current_profile`として含めます。LLMは新しい根拠が以前の説明を実質的に改善する場合だけ内容を変え、
根拠が変わらない時は現在の文面を維持します。

前回の`llm_source.evidence`と今回のevidenceを比較し、新しいsupported service URL、confirmedな
docs / blog / support URL、confirmed repository、または確認済みwebsiteで新たに観測した実利用面を、
説明に使える強いsourceの追加とみなします。実利用面はlogin、app shell、browserで確認したformや
public app surfaceに限り、beta等の文言だけでは認めません。この時だけ非空のsummaryと暫定taglineを
新しいLLM提案へ更新します。同じsourceの再取得や文章差だけでは置き換えません。暫定でないtaglineは
強いsourceが増えても自動更新しません。

比較結果の専用fieldは保存しません。前回LLM入力は既存の`llm_source.evidence`に残り、今回の成功後は
同fieldが新しい比較基準になります。過去に更新機会を逃したprofileはこの規則だけでは遡及更新されないため、
内容を確認した上で個別に適用します。schema / serviceなど内容別の専用signalは設けません。

`llm_output.name`は履歴ではなく、直近に成功したLLM reviewの現在提案です。成功したreviewを適用する時は、
`name_candidates`に残る以前の`source=llm_output`候補を現在提案で置き換えます。collectorまたはLLM呼び出しが
失敗して新しいoutputを適用できなかった場合は、直前の現在提案を維持します。operator edit、service title、
resource title、repository名など、LLM以外をsourceとする名前候補はこの置換の対象外です。

`finalize_project_shape()`も保存済み`llm_output.name`とLLM名候補を同期してから代表名を再評価します。このため、
旧実装で複数のLLM名候補が蓄積したprojectは、通常の保存または`repair-db`で現在提案だけに補正されます。
過去のLLM出力を調べる必要がある場合は、current stateの候補配列ではなくinvestigation runを参照します。

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

LLM は discovery の主役ではなく、整理の補助です。

planning/triageの入力上限と縮約規則は
[LLM evidence budget](investigation.md#llm-evidence-budget)を正本とします。

unconfirmedな`candidate_project_repository_urls`はtriage evidenceから除外します。候補URLを調査対象に含める必要があるinvestigation planningにだけURLを渡し、candidate repositoryのdescription、名前、lexicon hint等はconfirm前にはevidenceとして保存・適用しません。

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

category値の意味は[Category](project-record.md#category)を正本とします。
CLIやpluginなどの配布形式だけでは決めません。一般利用者が直接使うCLIや拡張機能は`project`、開発者が
依存packageとしてcodeへ組み込むpluginは`library`、既存serviceや外部toolへ導入してATProto連携を追加する
pluginは`infra_tool`です。ATProto自体を扱うCLIや運用componentも`infra_tool`です。既存projectに別物の
URLが混入した場合はidentifier関係の欠陥であり、projectを`reject`にしません。

停止、削除、archiveはcategoryを変える理由にしません。過去のcategoryを確認できる場合は維持し、削除後に
初めて発見して元の役割を復元できない古い対象では`name_only`を許容します。

複数の説明が当てはまる場合は、対象外なら`reject`、役割不明なら`name_only`、固定利用者なら
`personal_tool`、文書化された共有schemaなら`lexicon`、組み込む部品なら`library`、projectの背後で動くか
ATProto自体を扱う実行物なら`infra_tool`、一般利用者が直接使うものなら`project`の順で確認します。

生成したsummaryとcategoryが矛盾する場合は不整合として扱います。`current_profile`は既存の編集文面であり、
categoryの根拠にはしません。観測から役割を決めた後に、現在の文面を維持するか判断します。

#### 紹介候補signal

技術的な学習価値は、実用性や紹介準備とは別軸で `technical_interest_*` に保持します。LLM は evidence に基づいて `none / notable / high / exceptional` を暫定評価し、理由を enum と短い説明で残します。`promotion-queue` の `technical_insight_candidate` は `high` / `exceptional` だけを対象にします。`notable` は将来の再評価材料として保存しますが、推薦 signal にはしません。

serviceの利用可否も独立軸として扱います。`category=project / infra_tool`かつ`service_status=live / beta / invite_only`でURLを持つもののうち、confidenceがmedium以上でlogin、app shell、browser到達、検証済みauthority surfaceなどを確認した候補は`service_surface_confirmed`とします。これはservice軸のevidenceであり、中核機能や編集上の紹介可否まで保証しません。`library`はdocs URL等をserviceと誤認しないよう、このsignalの対象に含めません。

`unverified`、HTTP 200だけ、docs / landing pageだけ、またはevidenceに矛盾がある候補も自動除外せず、`service_candidate_needs_validation`として約100点下の優先度で残します。backend障害とlanding-onlyは静的probeだけで安定して分けられないため、候補提示後のoperator reviewで扱います。これらのservice signalはatproto寄与度や`technical_insight_candidate`と独立です。

理由 enum:

- `novel_design_pattern`
- `protocol_gap_exposed`
- `interoperability_experiment`
- `unusual_architecture`
- `implementation_reference`

## 稼働状態とatproto寄与度

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

現在の扱い:

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

project直下の`service_status / service_status_confidence / service_status_evidence`は、保存済みの
`service_verification_results`を初期値なしで再評価するcurrent projectionです。過去scalarのmax / unionは入力にしません。

- current resultは`lane + requested_url`ごとに最新1件を使う
- supporting pageの`follow_up`と、static / deepのsupporting page URLはproject scalarへ入れない
- 同一URLのbase laneは`deep > static > authority`とし、肯定的browser observationをその上へ重ねる
- browserでloginまたは公開app surfaceを確認したURLは`live / high`とし、同じURLの`docs_only`、`coming_soon`、mixed launch分類を除く。`beta`と`invite_only`は両立するため維持する
- static / deep / authority resultはcollector時点の`supports_project`を保存して利用する。旧resultにこのfieldがない場合は、manual service host、確認済みservice host、またはそこへの確認済みsame-site redirectに限定して復元する
- 対象となるbase/browser observationがない場合は、LLM等が設定した既存scalarをprojectionで消さない

projectionはnetwork、browser、trust graph更新を行いません。`repair-db`は変更件数とbefore/after例をdry-runで報告し、investigation runのinput/output/bundle/evaluationは更新しません。


probeがprojectを支持し、検証済みauthorityの完全一致hostが正常応答してtitleまたはdescriptionを持つ場合は、
汎用classifierの`unverified`を`verified_authority_service_surface`付き`live`へ補助更新します。
`none`、`unreleased`、`beta`等の明示分類は上書きしません。authority hostの一致だけでidentifier採用はしません。

匿名browserのlogin到達は認証後の利用を保証せず、runtime/network errorだけでは一時障害と恒久停止を区別できません。
`unverified`は`dead`や`private`の確定結果ではありません。

#### Service statusの要レビュー条件

LLM の `manual_review_recommended=true` は保存しますが、`manual_review_kind=service_status_manual_check`
は単に `service_status_confidence=low` というだけでは review queue に載せません。

`llm_manual_review_recommended` signal を残すのは、手動確認の結果で次のいずれかが変わる時です。

- `promotion_status_resolution`
  - `draft` で、service status の解決が昇格判断に影響する
- `introduction_validation`
  - 未紹介の `project` / `personal_tool` で、usable service の confidence が `none` / `low`
- `conflicting_service_status_evidence`
  - `mixed_launch_signals_detected` があり、status evidence が矛盾している

採用した理由は signal の `details.action_basis` に残します。
`identity_resolution` など、service status 以外の LLM manual review kind は従来通りです。

### Atproto寄与度

`atproto_contribution_level` / `atproto_contribution_score` / `atproto_contribution_evidence` は
category と独立した derived field です。`ensure_project_shape()` と `save_project()` で
rule-based に再計算します。

- `core` / `90`
  - resolved NSID または lexicon schema sample がある
- `substantial` / `70`
  - recent record sample または record count を持つ collection observation がある
- `optional` / `40`
  - input NSID または external lexicon hint だけがある
- `auth_only` / `20`
  - `atproto_auth_only=true`で、上記の強いprotocol surfaceが無い
  - 旧`category=auth_only`は正本DBで個別移行済みであり、読み取り互換を持たない
- `unknown` / `0`
  - rule-based evidence が無い

`incidental` は level として予約済みですが、現時点で rule-based に確定する source は未接続です。
review queue は寄与度を sort score に足し、promotion queue も寄与度を補助点として使います。
曖昧ケースの補助分類は[roadmap](../../roadmap.md#atproto寄与度と紹介候補の順位)の検討対象です。
既存 row の保存値を洗い替える場合は `repair-db` を使います。queue 表示時は読み取り shape に
対しても再計算するため、DB へ書き戻す前でも sort には反映されます。

## 紹介候補

### 昇格ゲート

本線扱いにする条件:

- 確認済み識別子が1件以上ある
  - `service_urls`
  - confirmed repository URL
  - resolved lexicon
- `service_status != unknown`
- `summary` と `tagline` がある
- `features` が3件以上ある
- `category` がある
- `llm_source`がある

Website単独やseedの有無はこのゲートの条件にしません。
満たさなければ `draft` のまま残し、`promotion_blockers` に理由を残します。

### 紹介候補の表示・順位・保留

`promotion-queue` は通常表示と interactive の両方で、対象 project 全件から signal と `promotion_queue_sort_score()` を計算し、`priority_group`、`top_priority`、`sort_score`、`project_key` の順でグローバルソートしてから `--limit` を適用します。`priority_group=lowered`は通常候補の後ろへ送り、各group内では従来のsignalとscore順を維持します。紹介候補は上位から見ること自体が目的なので、interactive でも部分バッチ先行にはしません。

`lexicon_publisher_confirmed`はcategory横断の紹介候補signalです。confirmed `owned_nsid`、`_lexicon.<authority>` DNS委譲を経て取得した`lexicon_resolution_entries`、同じNSIDの`lexicon_schema_samples`が一致した場合だけ成立します。repository内schema、NSID hint、record利用実績だけでは成立しません。priorityは8、scoreの`lexicon_publication`は160で、confirmed live serviceの次の優先帯に置きます。`reject` / `name_only`は除外し、`personal_tool`は`--include-personal-tools`指定時だけ対象にします。

promotion policyは次を混ぜずに投影します。

- `candidate_visibility`: `eligible / deferred / excluded`。紹介済み、`out_of_scope / ignore`、activeな`defer_promotion`のようなproject全体の表示可否を表す
- `service_evidence_group`: `confirmed / needs_validation / not_applicable`。service signalだけの確認度を表し、単独紹介の可否は含めない
- `priority_group`: `normal / lowered`。`lowered`はnormalized decisionの明示判断だけから投影し、project名・説明文・service evidenceから自動推定しない。理由メモは任意
- `score_breakdown`: service、lexicon、technical interest、profile、activityなど既存scoreの加減点を成分別に返し、`total`を従来の`sort_score`として使う

したがって`defer_promotion`は個別service signalの評価を下げる操作ではなく、project全体を期限付きで非表示にする操作です。一方、serviceの`needs_validation`は候補を消さず、confirmedより100点低いsignalとして残します。

WebのPromotion cardはservice group、manual priority、validation理由、scoreの非ゼロ成分を表示します。詳細画面から優先度を下げる時は任意の理由メモを入力でき、保存後は現在cardを外して次候補へ進みます。`Refresh`後も候補自体は残りますが、通常候補より後ろへ並びます。

merge元とcanonical targetの両方にactiveなpromotion priority判断がある場合はtarget側を維持し、source側をclear済み履歴として残します。

`promotion_deferrals` に active な entry がある project は signal 計算前に除外します。未指定時の保留期間は `suggested_promotion_defer_days()` が決めます。初回の若い project は 14 日、通常初回は 30 日、過去の promotion defer 履歴がある場合は 45 / 60 / 90 日へ延ばします。`--days` または API payload の `days` が明示された場合はその値を優先します。過去履歴は消さないため、再保留では直近理由を既定値として再利用できます。理由から「未実装機能が実装された」などの復活条件を自動判定する処理は将来候補で、現状は期限だけを見ます。

## 追跡と再調査

### review-stalled

`review-stalled` は `active -> stalled` の候補だけではなく、

- `active -> stable`
  - collection は最近動いているが repo 側は止まっている
- `stalled -> active`
  - repo または collection に最近 activity が戻った

も返します。

`service_status=unknown/none` は単独では落とす根拠にしません。repo activity と collection activity を優先し、`dead/private` だけを強いシグナルとして扱います。

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
- これは「ソースコード更新は止まっているが、collection はまだ実運用されている」ケースを落とし過ぎないためです
#### `active -> stalled`

- まず recent repo activity がある project は何もしません
- そのうえで、collection も recent でない時だけ stalled 判定に進みます
- `last_enriched_at` が無い、または `max_enrich_age_days` 以上古い場合は、停止を判断する材料が古いため降格候補から外します
- strong signal (`hard_signal`) として扱うもの:
  - collection が `inactivity_days` 以上止まっていて、しかも `recent_count>0` の observation が無い
  - repo が `inactivity_days` 以上止まっている
  - `service_status` が `dead` または `private`
- weak signal として理由には出すが、単独では落とさないもの:
  - `service_status=unknown` または `none`
  - confirmed identity が 1 件以下
    - `website_url`
    - `project_repository_urls`
    - `resolved_nsids`
- `stalled` を提案するには:
  - `hard_signal` が少なくとも 1 つ必要
  - 理由が 2 件以上必要
- つまり、単に `unknown` で証拠が薄いだけでは `stalled` にしません

#### 調査鮮度ゲート

- `max_enrich_age_days` は停止の根拠ではなく、降格判定に使える調査結果の最大経過日数です
- 未enrichまたは期限超過の `active` は `stalled` 候補を返しません
- recent activityによる `stalled -> active` と `active -> stable` は、調査鮮度ゲートより先に判定します

#### review-stalled の対象範囲

- `review-stalled` が見る tracking state は:
  - `active`
  - `stalled`
- `stable` / `out_of_scope` / `archived` / `ignore` はこの自動判定の対象外です
- `--execute` を付けると、提案された `suggested_tracking_state` をそのまま保存します

### review-out-of-scope / review-ignore

`review-out-of-scope` は関連性を否定せず、現在のsurvey対象から外す候補を返します。

- `auth_only_without_owned_protocol_surface`
  - `atproto_auth_only=true`
  - owned protocol surfaceがない
- `derivative_bluesky_utility`
  - `service_is_derivative_bluesky_tool()` が真
  - `introduced_url` がない
  - owned protocol surfaceがない
  - `category=lexicon` ではない

accepted projectは `tracking_state=out_of_scope` になります。この状態は通常の enrich/review/promotion対象外ですが、merge候補計算からは除外せず、明示enrichと手動復帰を許します。将来のsyncで新しいNSID等が既存projectへ紐づく経路も維持します。

`out_of_scope` projectにowned protocol surfaceが見つかった場合、同じコマンドは `owned_protocol_surface_detected` / `suggested_tracking_state=active` を返します。`--execute` なしでは更新せず、復帰も確認可能なqueueとして扱います。sync/enrichの途中で自動復帰する処理は未接続です。

`review-ignore` は inactivity review とは別で、ノイズ整理の候補だけを出します。

現状の kind は次です。

- `fork_without_independent_identity`
  - confirmed な project repository が GitHub fork
  - `website_url` / `service_urls` / `introduced_url` が無い
  - owned protocol surface (`resolved_nsids`, candidate `input_nsids`, `collection_observations`) も無い
  - `service_status=live|beta|invite_only` なら除外

目的はatproto無関係、誤登録、独立identityのない複製など、通常の再評価を期待しない候補を `tracking_state=ignore` へ寄せることです。既定では dry-run で JSON を返し、`--execute` を付けたときだけ保存します。

### 調査対象の選択

`enrich --all`は既定で`tracking_state=active`だけを対象にします。
`stable`、`stalled`、`out_of_scope`は明示的にincludeした場合だけ調査します。
