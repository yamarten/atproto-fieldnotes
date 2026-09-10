# Architecture

実装全体のmodule責務、依存方向、CLI/Webと処理本体の接続を説明します。
機能ごとの判断・保存契約は[仕様一覧](../documentation-policy.md#開発仕様)、
通常導線・実運用で利用できる範囲は[status](../status.md)を参照してください。

## 運用前提

このリポジトリは個人用のローカル運用を前提にします。

- `data/survey.db` は単一利用者のローカル DB として扱う
- 他ユーザーや共有 DB の存在は考慮しない
- 同時編集の懸念は、自分の別プロセスや GUI 操作による衝突だけを想定する

## 実装の依存境界

- `application/sync.py`は`source_sync.py`を呼び、source別の取得、watermark、既存projectへの接続を実行する
- `application/enrichment.py`は`enrichment_pipeline.py`を呼び、1 projectのcollector、LLM、保存前のcurrent state更新を実行する
- `commands/queue_interaction.py`はreview/promotion queueのinteractive入出力を担当し、queue policy自体は`registry.py`に残す
- `project_record.py`はproject shape、保存payload、fetch/list/save、normalized exact lookup、project key allocationを所有する
- `registry.py`はcollector/evaluation orchestration、finalize、merge/queue policyを所有する。repositoryのprovider別取得や解析素材抽出は所有しない

`source_sync.py`と`enrichment_pipeline.py`はproject stateや保存を重複実装せず、`project_record.py`を直接利用します。
`project_record.py`から`registry.py`への逆依存と、旧内部symbolを維持するためのre-exportは置きません。

## Module構成と依存方向

各moduleの処理責務を示します。実運用への適用や検証は[status](../status.md)を参照してください。

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
- `project_registry_core/repository_analysis.py`
  - confirmed repositoryのmetadata、README、tree、manifest、configuration、Lexicon、限定source抜粋をprovider共通の解析素材へ縮約する
  - repository revisionと解析policy単位で結果を再利用し、成功結果と最新試行を分けてcurrent stateへ保存する
- `project_registry_core/repository_sources.py`
  - GitHub / Codeberg / Tangledのmetadata、README、tree、個別file取得と短期cacheを所有する
  - provider固有のHTTP契約を閉じ込め、projectへの適用判断は行わない
- `project_registry_core/repository_material.py`
  - 取得済みtreeとfileからmanifest、configuration、Lexicon、限定source hintを決定的に抽出する
  - network取得、LLM判定、project mutationは行わない
- `project_registry_core/evaluation.py`
  - ownership、atproto寄与度、service status の純粋評価本体
- `project_registry_core/storage.py` / `replay.py`
  - investigation run保存、network-free replay、fixture benchmark
- `project_registry_core/service.py` / `tangled.py` / `relations.py`
  - service検証、Tangled PDS/UFOs、typed relation
  - service verification evidenceの縮約と、`lane + requested_url`単位の最新32 surface projectionは
    `service.py`が所有する
- `project_registry_core/domain_policy.py`
  - 正式依存`tldextract`へvendored PSLだけを入力し、known/configured multi-tenant suffix、registrable domain、
    same-site判定を所有する。依存またはvendored PSL欠落時に別の自作policyへfallbackしない
  - suffix設定の読書きもこのmoduleが所有し、`registry.py`は旧symbolをre-exportしない
- `project_registry_core/http_transport.py`
  - outbound URL検証、内部host遮断、redirect先再検証、bytes/JSON requestを所有する
  - collectorはこのmoduleをtest seamとして使い、`registry.py`は旧transport symbolをre-exportしない
- `project_registry_core/identifiers.py`
  - canonical URL/NSID、抽出URLの句読点除去、authority domain導出、generic値を除外するalias token正規化を所有する
  - domain/HTTP transportも同じURL正規化を使い、`registry.py`は旧symbolをre-exportしない
- `project_registry_core/repo_urls.py`
  - GitHub、Tangled、Codeberg、GitLab、SourceHutのrepo URL正規化とprovider/owner判定を所有する
  - `registry.py`はconsumerとしてmoduleを呼び、旧repo helper symbolをre-exportしない
- `project_registry_core/naming.py`
  - HTML titleの短縮、name candidate正規化、service/repo source labelのpure helperを所有する
  - project固有のcandidate採否・score・mutationは`registry.py`に残す
- `project_registry_core/llm_transport.py`
  - command/OpenAI互換endpointの呼出し、response parser、timeout/error分類、request metricsを所有する
  - prompt/evidence構築とprojectへの適用はtransport外に残し、testはcommand/endpointをfakeへ差し替える
- `project_registry_core/project_record.py`
  - projectの既定shapeと保存前正規化、scalar/profile/state payloadへの投影を所有する
  - schema v3 rowのhydrate、fetch/list/save、normalized exact lookup、project key allocationを所有する
  - `database.py` / `runtime_view.py`と専門moduleを利用し、`registry.py`へ依存しない
- `project_registry_core/application/`
  - project query、tracking、decision、merge/split、review queue、sync、investigation、maintenance、enrichのCLI/Web共通service
  - `argparse.Namespace`やstdoutへ依存せず、明示引数とresult payloadを使う
  - `presentation.py`はproject detail、queue、search payloadを組み立てるtransport非依存service
- `project_registry_core/jobs.py`
  - 長時間処理のrequest/event/resultと協調的cancellation契約
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
- `project_registry_core/worker.py`
  - stdinの`OperationRequest`をapplication serviceへ渡し、event/resultをJSON Linesで返す
- `project_registry_core/registry.py`
  - collector/evaluation orchestration、finalize、merge/queue policyと未分離の補助処理
  - collector適用とfinalize/evaluationの補助処理。1 projectの実行順序は`enrichment_pipeline.py`、transaction・保存・checkpointは`application/enrichment.py`が担当する
  - `argparse.Namespace`を受け取る`cmd_*`は残っていない

現行の依存方向は完全分離後の目標図ではありません。確認済みの境界は次の通りです。

- `database.py`はschema、接続、normalized rowのsnapshotを所有する。通常open/save pathは`registry.py`をimportせず、
  maintenance専用のnormalized roundtrip verifierは`project_record.py`の`fetch_project` / `save_project`を遅延importする
- `application/`はCLI/Web共通serviceとして分離済みだが、多くのserviceがdomain/storage facadeとして
  `registry.py`とproject保存用の`project_record.py`を用途別にimportする。`commands/`も同じ境界を使う
- Web APIの主要mutation/queryはapplication serviceを通る一方、server bootstrap、検索、補助read pathには
  `registry.py`または`project_record.py`への直接依存が残る
- `database`、`runtime_view`、`evidence`、`investigation`、`integration`、`evaluation`、`service`、transport、
  identifier/repo URL/naming/domain policyの抽出moduleを`registry.py`経由へ戻すre-exportは置かない
- queue policyとcollector適用の補助処理は`registry.py`に残る。1 projectのenrich本体の分離は完了しているが、
  `enrichment_pipeline.py`やapplication serviceが`registry.py`から独立済みとは扱わない

resource pageの統合は既存mutatorをproject copy上で実行し、許可fieldのpatchを返す暫定実装です。
cacheとcircuit breakerはprocess/source単位で、永続policyではありません。

## CLI・Webとapplication service

`enrich`のproject選択、保存、checkpoint、再開は`project_registry_core.application.enrichment`へ接続済みです。CLIは`project_registry_core.commands.enrichment`で引数を`OperationRequest`へ変換し、serviceのevent dataをJSON linesへ変換します。

中断・失敗・再開、checkpointの保存契約は
[Background Job Model](specs/background-jobs.md#enrich-checkpoint)を参照してください。

Web UIはschema v3 repositoryへ接続済みです。project detail payloadは`project`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`を分離します。identifier groupは同値行をまとめつつ、`entries`にrole/classification/providerの対応を保持します。Websiteはprofile、service URLはidentifierとして別々に表示し、値が同じでもprimary markerは付けません。DID/handle/accountは同じaccount metadataを持つ場合に1つのidentity groupへ畳みます。confirmはcandidate entryから開始でき、同じ値のblock/deferを解除してrole付きconfirm decisionとadoptionを保存します。profile編集で明示したnameは`operator_edit`候補として保存し、自動生成候補より優先します。日常的なproject query、tracking、identifier decision、split/mergeは`project_registry_core.application`を共有入口として使います。former/non-productionの表示・編集UIは未接続です。

Web runnerの起動、排他範囲、progress/cancellation、checkpoint発見は
[Web Runner](specs/background-jobs.md#web-runner)を正本とします。
Investigation tabのbounded evidenceとread-only replayは
[Investigation](specs/investigation.md#pipeline)を参照してください。

Explain serviceは`project_registry_core.application.explain`をCLIとWebで共有します。`service_status`、ownership、atproto contribution、merge candidateについて、保存済みcurrent stateとrelation sourceだけから結論、適用rule、支持/反証signalを返します。Explain実行中はnetwork collectorやLLMを呼ばず、replay結果もDBへ保存しません。Webの初期detailは全project hydrateを行わず、merge candidateだけを`/api/projects/{key}/merge-analysis`で非同期計算します。同service内ではduplicate candidate計算を1回だけ行い、その結果をmerge recommendationとExplainの両方へ渡します。source revisionが計算中に変化した場合は結果を破棄し、計算済みviewはDBへ追加しません。

## 一覧・詳細の検索経路

Webのproject一覧は`projects`を起点にnormalized identifier、relation、decisionを`EXISTS` queryでfilterします。
一覧候補作成時に全projectのchildをhydrateせず、detailは選択した1件のchildを取得します。
自由入力はproject key、name、Website、identifier value、relation subject/object/locator、decision target/noteの
大小文字を無視した部分一致です。summary、tagline、raw evidence、README/HTML全文は対象外です。
