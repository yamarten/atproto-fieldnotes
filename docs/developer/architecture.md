# Architecture

このツールは、外部情報源（source）から候補を登録し、登録プロジェクト（以下project）ごとに調査した根拠を評価して、紹介・追跡に使う現在状態をSQLiteへ保つ仕組みです。CLIとWebは同じapplication serviceを呼び、外部取得とLLMは主に調査時に使います。一覧・詳細の表示とExplain（判定理由の表示）は保存済み情報を読みます。

application serviceは、検索や登録・調査などの処理をCLIとWebから共通に呼ぶ入口です。以下では、保存情報の取得をquery、変更操作をmutation、処理を実行する利用者をoperatorと呼びます。projectの識別子・関係・手動判断を個別のtableへ分けた保存形をnormalized row、それらを調査処理用のdictへ組み立てることをhydrateと表記します。保存形とdictの対応は[Project record](specs/project-record.md)で定めます。

## 全体の流れ

1. **発見・登録** — source別のsyncが候補を既存projectへ照合し、対象commandでは未知の候補を新規登録する。
2. **調査・判断** — enrichが観測を収集し、resourceの帰属と、説明・分類・稼働状態などをそれぞれ評価する。観測したlinkやLLMの提案が、そのまま採用済みidentifierになるわけではない。
3. **保存・利用** — projectの現在状態と調査履歴を分けて保存する。CLI/Webで検索・要レビュー項目の確認・手動編集を行い、次の調査でもoperatorのconfirm（採用）・block（除外）・defer（期限付き保留）を尊重する。

この流れの契約は次の仕様に分かれます。

| 確認したいこと | 仕様 |
| --- | --- |
| 何を発見し、既存projectへどう取り込むか | [発見と取り込み](specs/discovery-and-sync.md) |
| 何を調べ、どの根拠を残すか | [Investigation](specs/investigation.md) |
| 何を同じprojectの構成要素と認め、いつ統合・分離するか | [Project境界](specs/project-boundaries.md) |
| 根拠から説明・紹介候補・追跡状態をどう決めるか | [Project評価](specs/project-evaluation.md) |
| 現在状態・関係・手動判断・調査履歴をどう保持するか | [Project record](specs/project-record.md) |

個人がローカルの`data/survey.db`を使う運用を前提とします。同時編集は自分のCLI、Web、worker間の衝突を想定します。Web server内ではmutationを直列化しますが、別processとの競合はproject単位の更新番号と保存transaction内の検査で検出します。

## 実行経路

OAuth metadataの解析は`oauth_metadata.py` → `oauth_scope_parser.mjs` → 公式OAuth scope SDKで行います。Nodeは取得済み文字列の解析に限定し、HTTPは既存collector、保存済み観測の適用は`integration.py`、表示はapplication presentationが担当します。詳細は[OAuth観測](specs/investigation.md#oauth-client-metadataの観測)を参照してください。

`usage_material.py`は個別repo宣言・record型Lexicon・record観測を同じcollection名で束ね、保存済み本文・README原文の表記と照合します。通常のservice collectorへ既知名を渡し、HTMLの切捨て前に選んだ候補をservice観測として保持します。通信・LLM・用途判定を担当しません。[関連記述](specs/investigation.md#atproto利用の関連記述)を参照してください。

CLIの`commands/`とWeb APIは入力をapplication serviceへ渡し、結果をそれぞれtext/JSONとHTTP responseへ変換します。長時間処理では、CLIはserviceを呼び、Webは別processの`worker.py`を介して同じserviceを実行します。業務処理は進捗eventと終了resultを返し、enrichの保存・checkpoint・再開もapplication serviceが担当します。中断可能な境界と再開の保証は[Background jobs](specs/background-jobs.md)で定めます。

独立したLexicon観測はregistry本体とは別の専用DB（sidecar DB）へ保存し、registryは照合用に読みます。App record exportは保存済みprojectから公開用JSONを生成します。いずれも通常のenrichとは別の処理です。

LLMの個別事例は[非公開事例の固定保存](specs/llm-case-store.md)で、registry DBと公開テストfixtureから独立したGit管理外のJSONに保存できます。入力・出力・実行条件の固定snapshotと後からの判断eventを分けます。登録・event追記・検査はCLIの`commands/llm_cases.py`から`llm_case_store.py`を呼び、通常enrichには接続しません。

### 実装の依存境界

- `application/sync.py`は`source_sync.py`を呼び、source別の取得、watermark、既存projectへの接続を実行する
- `application/enrichment.py`は`enrichment_pipeline.py`を呼び、1 projectのcollector、LLM、保存前のcurrent state更新を実行する
- `commands/queue_interaction.py`はreview/promotion queueのinteractive入出力を担当し、queue policy自体は`registry.py`に残す
- `project_record.py`はproject shape、保存payload、fetch/list/save、normalized exact lookup、project key allocationを所有する
- `registry.py`はcollectorの適用と評価の取りまとめ、保存前の整形・評価を行うfinalize、マージ候補・レビュー候補の選定規則を担当する

`source_sync.py`と`enrichment_pipeline.py`はproject stateや保存を重複実装せず、`project_record.py`を直接利用します。`project_record.py`から`registry.py`への逆依存と、旧内部symbolを維持するためのre-exportは置きません。

### CLI・Webとapplication service

`enrich`のproject選択、保存、checkpoint、再開は`project_registry_core.application.enrichment`へ接続済みです。CLIは`project_registry_core.commands.enrichment`で引数を`OperationRequest`へ変換し、serviceのevent dataをJSON linesへ変換します。

Webのproject detail payloadは`project`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`を分離します。

identifier groupは同値行をまとめつつ、`entries`にrole（project内の役割）・classification（identifierの採用状態）・provider（外部サービス）の対応を保持します。Websiteはprofile、service URLはidentifierとして別々に表示し、値が同じでもprimary markerは付けません。DID/handle/accountは同じaccount metadataを持つ場合に、表示上の1つのidentity groupへまとめます。

confirmはcandidate entryから開始でき、同じ値のblock/deferを解除してrole付きconfirm decision（採用判断）とadoption（採用来歴）を保存します。profile編集で明示したnameは`operator_edit`候補として保存し、自動生成候補より優先します。日常的なproject query、tracking、identifier decision、split/mergeは`project_registry_core.application`を共有入口として使います。旧構成要素（`former`）と開発・試験用（`non_production`）identifierの表示・編集UIは未接続です。

Explain serviceは`project_registry_core.application.explain`をCLIとWebで共有します。`service_status`、NSID ownership（collectionの所有）、atproto寄与度、マージ候補について、保存済みの現在状態とrelationの根拠だけから結論、適用rule、支持/反証signalを返します。Explain実行中はnetwork collectorやLLMを呼ばず、再評価結果もDBへ保存しません。

Webの初期detailは全project hydrateを行わず、merge candidateだけを`/api/projects/{key}/merge-analysis`で非同期計算します。同service内では重複候補（duplicate candidate）の計算を1回だけ行い、その結果をマージ推奨（merge recommendation）とExplainの両方へ渡します。マージ解析対象projectの変更検出値（revision）が計算中に変化した場合は結果を破棄し、計算済みviewはDBへ追加しません。

### 一覧・詳細の検索経路

Webのproject一覧は`projects`を起点にnormalized identifier、relation、decisionを`EXISTS` queryでfilterします。一覧候補作成時に全projectのchildをhydrateせず、detailは選択した1件のchildを取得します。自由入力はproject key、name、Website、identifier value、relation subject/object/locator、decision target/noteに加え、保存済みの`tagline`、`summary`、`features`各要素の大小文字を無視した部分一致です。`profile_json`や`state_json`全体、raw evidence、README/HTML全文は対象外です。

紹介候補一覧は全子行を含む完全runtime viewを使います。読込み境界で正規化と保存用baseline作成を済ませ、queueとsignalの計算は同じ正規化済み値を再利用します。任意dictからの公開builderは正規化を行いますが、DBからの一覧は重複させません。読み取りsnapshotと判断履歴による先行絞込みの契約は[評価仕様](specs/project-evaluation.md#紹介候補の表示順位保留)を参照してください。

## Module構成と依存方向

以下のpathは`scripts/`からの相対pathです。処理を変更するときの参照用に、担当moduleをまとめます。

### 入口・処理の制御

- `project_registry_core/application/`
  - project query、tracking、decision、merge/split、review queue、sync、investigation、maintenance、enrichのCLI/Web共通service
  - `argparse.Namespace`やstdoutへ依存せず、明示引数とresult payloadを使う
  - `presentation.py`はproject detail、queue、search payloadを組み立てるtransport非依存service
- `project_registry_core/commands/`
  - application serviceを呼び出し、CLI向けJSON/textへ変換するadapter
  - review/promotion queueのinteractive入出力は`commands/queue_interaction.py`、stalled/ignore/out-of-scopeは`commands/review.py`が担当する
- `project_registry_web/`
  - schema v2 Web API adapter、HTTP server、build不要のHTML/CSS/ES modules
  - 主要mutation/queryはapplication serviceを呼び、hydrated project dictを直接保存しない。補助read pathの直接依存は下記を参照
  - `jobs.py`は別process worker、直近event、停止・再開を管理する
  - JSON responseはDB schema versionと独立した`api_version: 2`を持つ
  - presenterがidentifierを種類・同値関係でまとめた表示、relationと調査履歴の要約、project revisionを生成する
  - mutationは`expected_revision`を必須とし、stale detailを`409 stale_project`で拒否する
  - server内のmutationはlockで直列化する
- `project_registry_core/jobs.py`
  - 長時間処理のrequest/event/resultと協調的cancellation契約
- `project_registry_core/worker.py`
  - stdinの`OperationRequest`をapplication serviceへ渡し、event/resultをJSON Linesで返す
- `project_registry_core/dev_database.py`
  - `scripts/fixtures/dev-registry.json`の架空定義を既存のproject codec・調査履歴APIで保存する
  - `dev-db`から各worktreeの固定pathへ生成し、生成元markerのあるDBだけをtransactionでリセットする。通常CLIのDB選択は`cli.py`の起動境界で行う
- `project_registry_core/registry.py`
  - collector/evaluation orchestration、finalize、merge/queue policyと未分離の補助処理
  - collector適用とfinalize/evaluationの補助処理。1 projectの実行順序は`enrichment_pipeline.py`、transaction・保存・checkpointは`application/enrichment.py`が担当する
  - `argparse.Namespace`を受け取る`cmd_*`は残っていない

### 観測・統合・評価

- `project_registry_core/evidence.py`
  - `EvidenceBundle` schema version 2
  - `EvidenceRecord`, relation、`CollectorFailure`, `CollectorResult`
- `project_registry_core/investigation.py`
  - deterministic / LLM query planning
  - provider共通repository snapshot collectorとGitHub / Codeberg adapter
  - GitHub repository search、Bluesky actor/post/feed collector
  - collector は project dict を変更せず evidence bundle を返す
  - 外部情報源ごとの再試行、request数の上限、rate limit到達時の停止
- `project_registry_core/integration.py`
  - GitHub / Bluesky / service / Tangled / collector snapshot evidence を既存 project field へ適用する
  - GitHub lexicon hint の owned / reference 判定は収集後に行う
- `project_registry_core/repository_sources.py`
  - GitHub / Codeberg / Tangledのmetadata、README、tree、個別file取得と短期cacheを所有する
  - provider固有のHTTP契約を閉じ込め、projectへの適用判断は行わない
- `project_registry_core/repository_material.py`
  - 取得済みtreeとfileからmanifest、configuration、Lexicon、限定source hintを決定的に抽出する
  - network取得、LLM判定、project mutationは行わない
- `project_registry_core/repository_analysis.py`
  - confirmed repositoryのmetadata、README、tree、manifest、configuration、Lexicon、限定source抜粋をprovider共通の解析素材へ縮約する
  - repository revisionと解析policy単位で結果を再利用し、成功結果と最新試行を分けてcurrent stateへ保存する
- `project_registry_core/service.py` / `tangled.py` / `relations.py`
  - service検証、Tangled PDS/UFOs、typed relation
  - service確認結果の縮約と、調査経路・対象URL（`lane + requested_url`）ごとの最新観測を最大32組保持する処理は`service.py`が担当する
- `project_registry_core/tangled_identity.py`
  - Tangled同期専用のDID document・handle双方向検証と、対象の解決不能状態／外部取得失敗の分類。既存のDID取得・HTTP transportを使い、projectの帰属や保留一覧の保存は`source_sync.py`へ委ねる
- `project_registry_core/evaluation.py`
  - ownership、atproto寄与度、service status の純粋評価本体
- `project_registry_core/collection_trust.py`
  - Lexicon公開応答の検証と、project・role・authority・publisherに範囲を限定したcollection信頼policy。限定した直接関係を`trust_graph.py`の共通経路評価へ渡し、取得・採用・DB保存は行わない
- `project_registry_core/editorial.py`
  - 維持する説明、内容を持つ根拠の集合、変更種別と参照検査、編集保護、最新の更新案を扱う。版履歴は作らない
  - 通常triageから呼び、Webのpreview／編集／見送りと同じ状態を使う
- `project_registry_core/atproto_usage.py`
  - 利用範囲の理由付き手動判断と解除・マージ規則を扱う。サービス確認と技術評価から独立したprojectionをCLI/Web/紹介候補へ渡し、LLMの総括は適用しない
- `project_registry_core/promotion_attention.py`
  - 紹介候補を再評価するための構造化snapshot・fingerprintと直近の明示操作を扱う。品質scoreや自動閲覧判定を持たず、`registry.py`の候補選定とapplicationの判断保存から利用する

### 保存と再評価

- `project_registry_core/concurrency.py`
  - project更新番号のtrigger定義、短いread/write transaction、保存時の競合検査を所有する
  - 外部取得・LLM中のwrite lockは保持しない。`project_record.py`が読込みsnapshotへ番号を付け、保存時に検査する


- `project_registry_core/project_record.py`
  - projectの既定shapeと保存前正規化、scalar/profile/state payloadへの投影を所有する
  - schema v4 rowのhydrate、fetch/list/save、normalized exact lookup、project key allocationを所有する
  - `database.py` / `runtime_view.py`と専門moduleを利用し、`registry.py`へ依存しない
- `project_registry_core/storage.py` / `replay.py`
  - investigation run保存、network-free replay、fixture benchmark

### 共通の正規化・外部接続

- `project_registry_core/domain_policy.py`
  - 正式依存`tldextract`へ同梱のPublic Suffix List（PSL）を入力し、登録可能domainとsame-siteを判定する。共有hostingのtenant境界には既知・設定済みの接尾辞も使う。依存または同梱PSLの欠落時はエラーとする
  - suffix設定の読書きもこのmoduleが所有する
- `project_registry_core/http_transport.py`
  - outbound URL検証、内部host遮断、redirect先再検証、bytes/JSON requestを所有する
  - collectorのtestではこのmoduleのHTTP呼出しを差し替える
- `project_registry_core/identifiers.py`
  - canonical URL/NSID、抽出URLの句読点除去、authority domain導出、generic値を除外するalias token正規化を所有する
  - domain/HTTP transportも同じURL正規化を使う
- `project_registry_core/repo_urls.py`
  - GitHub、Tangled、Codeberg、GitLab、SourceHutのrepo URL正規化とprovider/owner判定を所有する
  - `registry.py`はconsumerとしてmoduleを呼ぶ
- `project_registry_core/naming.py`
  - HTML titleの短縮、name candidate正規化、service/repo source labelのpure helperを所有する
  - project固有のcandidate採否・score・mutationは`registry.py`に残す
- `project_registry_core/llm_transport.py`
  - command/OpenAI互換endpointの呼出し、response parser、timeout/error分類、request metricsを所有する
  - prompt/evidence構築とprojectへの適用はtransport外に残し、testはcommand/endpointをfakeへ差し替える

### 依存境界の制約

現行実装で分離できている責務と、残る直接依存は次の通りです。

- `database.py`はschema、接続、normalized rowのsnapshotを所有する。通常open/save pathは`registry.py`をimportせず、maintenance専用のnormalized roundtrip verifierは`project_record.py`の`fetch_project` / `save_project`を遅延importする
- `application/`はCLI/Web共通serviceとして分離済みだが、多くのserviceがdomain/storage facadeとして`registry.py`とproject保存用の`project_record.py`を用途別にimportする。`commands/`も同じ境界を使う
- Web APIの主要mutation/queryはapplication serviceを通る一方、server bootstrap、検索、補助read pathには`registry.py`または`project_record.py`への直接依存が残る
- `database`、`runtime_view`、`evidence`、`investigation`、`integration`、`evaluation`、`service`、transport、identifier/repo URL/naming/domain policyの抽出moduleを`registry.py`経由へ戻すre-exportは置かない
- queue policyとcollector適用の補助処理は`registry.py`に残る。1 projectのenrich本体の分離は完了しているが、`enrichment_pipeline.py`やapplication serviceが`registry.py`から独立済みとは扱わない

resource pageの観測の統合は、既存の更新関数をprojectのcopy上で実行し、許可fieldの差分（patch）を返す暫定実装です。cacheと、失敗が続く取得を抑止するcircuit breakerは、processと外部情報源ごとに状態を持ちます。状態はprocess終了時に失われます。
