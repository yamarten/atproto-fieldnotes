# Architecture

このツールは、外部sourceから候補を登録し、projectごとに調査した根拠を評価して、紹介・追跡に使う現在状態をSQLiteへ保つ仕組みです。CLIとWebは同じapplication serviceを呼び、外部取得とLLMは主に調査時に使います。一覧・詳細の表示とExplainは保存済み情報を読みます。

## 全体の流れ

1. **発見・登録** — source別のsyncが候補を既存projectへ照合し、対象commandでは未知の候補を新規登録する。
2. **調査・判断** — enrichが観測を収集し、resourceの帰属と、説明・分類・稼働状態などをそれぞれ評価する。観測したlinkやLLMの提案が、そのまま採用済みidentifierになるわけではない。
3. **保存・利用** — projectの現在状態と調査履歴を分けて保存する。CLI/Webで検索・review・手動編集し、次の調査でもoperatorのconfirm・block・deferを尊重する。

この流れの契約は次の仕様に分かれます。

| 確認したいこと | 仕様 |
| --- | --- |
| 何を発見し、既存projectへどう取り込むか | [発見と取り込み](specs/discovery-and-sync.md) |
| 何を調べ、どの根拠を残すか | [Investigation](specs/investigation.md) |
| 何を同じprojectの構成要素と認め、いつ統合・分離するか | [Project境界](specs/project-boundaries.md) |
| 根拠から説明・紹介候補・追跡状態をどう決めるか | [Project評価](specs/project-evaluation.md) |
| 現在状態・関係・手動判断・調査履歴をどう保持するか | [Project record](specs/project-record.md) |

個人がローカルの`data/survey.db`を使う運用を前提とします。同時編集は自分のCLI、Web、worker間の衝突を想定します。Web server内ではmutationを直列化しますが、別processとの競合はSQLiteのlockに委ねます。

## 実行経路

CLIの`commands/`とWeb APIは入力をapplication serviceへ渡し、結果をそれぞれtext/JSONとHTTP responseへ変換します。長時間処理では、CLIはserviceを呼び、Webは別processの`worker.py`を介して同じserviceを実行します。業務処理は進捗eventと終了resultを返し、enrichの保存・checkpoint・再開もapplication serviceが担当します。中断可能な境界と再開の保証は[Background jobs](specs/background-jobs.md)で定めます。

独立したLexicon観測は専用sidecar DBへ保存し、registryは照合用に読むだけです。App record exportは保存済みprojectから公開用JSONを生成します。いずれも通常のenrichとは別の処理です。

### 実装の依存境界

- `application/sync.py`は`source_sync.py`を呼び、source別の取得、watermark、既存projectへの接続を実行する
- `application/enrichment.py`は`enrichment_pipeline.py`を呼び、1 projectのcollector、LLM、保存前のcurrent state更新を実行する
- `commands/queue_interaction.py`はreview/promotion queueのinteractive入出力を担当し、queue policy自体は`registry.py`に残す
- `project_record.py`はproject shape、保存payload、fetch/list/save、normalized exact lookup、project key allocationを所有する
- `registry.py`はcollector/evaluation orchestration、finalize、merge/queue policyを所有する。repositoryのprovider別取得や解析素材抽出は所有しない

`source_sync.py`と`enrichment_pipeline.py`はproject stateや保存を重複実装せず、`project_record.py`を直接利用します。`project_record.py`から`registry.py`への逆依存と、旧内部symbolを維持するためのre-exportは置きません。

### CLI・Webとapplication service

`enrich`のproject選択、保存、checkpoint、再開は`project_registry_core.application.enrichment`へ接続済みです。CLIは`project_registry_core.commands.enrichment`で引数を`OperationRequest`へ変換し、serviceのevent dataをJSON linesへ変換します。

Webのproject detail payloadは`project`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`を分離します。

identifier groupは同値行をまとめつつ、`entries`にrole/classification/providerの対応を保持します。Websiteはprofile、service URLはidentifierとして別々に表示し、値が同じでもprimary markerは付けません。DID/handle/accountは同じaccount metadataを持つ場合に1つのidentity groupへ畳みます。

confirmはcandidate entryから開始でき、同じ値のblock/deferを解除してrole付きconfirm decisionとadoptionを保存します。profile編集で明示したnameは`operator_edit`候補として保存し、自動生成候補より優先します。日常的なproject query、tracking、identifier decision、split/mergeは`project_registry_core.application`を共有入口として使います。former/non-productionの表示・編集UIは未接続です。

Explain serviceは`project_registry_core.application.explain`をCLIとWebで共有します。`service_status`、ownership、atproto contribution、merge candidateについて、保存済みcurrent stateとrelation sourceだけから結論、適用rule、支持/反証signalを返します。Explain実行中はnetwork collectorやLLMを呼ばず、replay結果もDBへ保存しません。

Webの初期detailは全project hydrateを行わず、merge candidateだけを`/api/projects/{key}/merge-analysis`で非同期計算します。同service内ではduplicate candidate計算を1回だけ行い、その結果をmerge recommendationとExplainの両方へ渡します。source revisionが計算中に変化した場合は結果を破棄し、計算済みviewはDBへ追加しません。

### 一覧・詳細の検索経路

Webのproject一覧は`projects`を起点にnormalized identifier、relation、decisionを`EXISTS` queryでfilterします。一覧候補作成時に全projectのchildをhydrateせず、detailは選択した1件のchildを取得します。自由入力はproject key、name、Website、identifier value、relation subject/object/locator、decision target/noteの大小文字を無視した部分一致です。summary、tagline、raw evidence、README/HTML全文は対象外です。

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
  - presenterがidentifier family、relation summary、investigation summary、project revisionを生成する
  - mutationは`expected_revision`を必須とし、stale detailを`409 stale_project`で拒否する
  - server内のmutationはlockで直列化する
- `project_registry_core/jobs.py`
  - 長時間処理のrequest/event/resultと協調的cancellation契約
- `project_registry_core/worker.py`
  - stdinの`OperationRequest`をapplication serviceへ渡し、event/resultをJSON Linesで返す
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
  - source別 retry、request budget、rate-limit stop
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
  - service verification evidenceの縮約と、`lane + requested_url`単位の最新32 surface projectionは`service.py`が所有する
- `project_registry_core/evaluation.py`
  - ownership、atproto寄与度、service status の純粋評価本体

### 保存と再評価

- `project_registry_core/project_record.py`
  - projectの既定shapeと保存前正規化、scalar/profile/state payloadへの投影を所有する
  - schema v3 rowのhydrate、fetch/list/save、normalized exact lookup、project key allocationを所有する
  - `database.py` / `runtime_view.py`と専門moduleを利用し、`registry.py`へ依存しない
- `project_registry_core/storage.py` / `replay.py`
  - investigation run保存、network-free replay、fixture benchmark

### 共通の正規化・外部接続

- `project_registry_core/domain_policy.py`
  - 正式依存`tldextract`へvendored PSLだけを入力し、known/configured multi-tenant suffix、registrable domain、same-site判定を所有する。依存またはvendored PSL欠落時に別の自作policyへfallbackしない
  - suffix設定の読書きもこのmoduleが所有する
- `project_registry_core/http_transport.py`
  - outbound URL検証、内部host遮断、redirect先再検証、bytes/JSON requestを所有する
  - collectorはこのmoduleをtest seamとして使う
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

現行の依存方向は完全分離後の目標図ではありません。確認済みの境界は次の通りです。

- `database.py`はschema、接続、normalized rowのsnapshotを所有する。通常open/save pathは`registry.py`をimportせず、maintenance専用のnormalized roundtrip verifierは`project_record.py`の`fetch_project` / `save_project`を遅延importする
- `application/`はCLI/Web共通serviceとして分離済みだが、多くのserviceがdomain/storage facadeとして`registry.py`とproject保存用の`project_record.py`を用途別にimportする。`commands/`も同じ境界を使う
- Web APIの主要mutation/queryはapplication serviceを通る一方、server bootstrap、検索、補助read pathには`registry.py`または`project_record.py`への直接依存が残る
- `database`、`runtime_view`、`evidence`、`investigation`、`integration`、`evaluation`、`service`、transport、identifier/repo URL/naming/domain policyの抽出moduleを`registry.py`経由へ戻すre-exportは置かない
- queue policyとcollector適用の補助処理は`registry.py`に残る。1 projectのenrich本体の分離は完了しているが、`enrichment_pipeline.py`やapplication serviceが`registry.py`から独立済みとは扱わない

resource pageの統合は既存mutatorをproject copy上で実行し、許可fieldのpatchを返す暫定実装です。cacheとcircuit breakerはprocess/source単位で、永続policyではありません。
