# Architecture

外部情報源から候補を登録し、登録プロジェクト（以下project）ごとに収集した根拠を評価して、紹介・追跡に使う現在状態をSQLiteへ保存します。CLIとWebは同じapplication serviceを呼びます。外部取得とLLMは主に調査時に使い、一覧・詳細・Explain（判定理由）は保存済み情報を読みます。

以下では、保存情報の取得をquery、変更操作をmutation、利用者をoperatorと呼びます。識別子・関係・手動判断を個別tableへ分けた保存形をnormalized row、それらを処理用dictへ組み立てることをhydrateと表記します。

## 全体の流れ

```mermaid
flowchart TD
    register[発見・登録<br/>候補の照合・取り込み] --> db
    db[(SQLite<br/>現在状態・手動判断<br/>調査履歴)] -->|調査対象・保存済み情報| investigate
    investigate[調査・判断<br/>根拠の収集と更新案の評価] -->|手動判断を尊重して保存| db
    db --> view[閲覧・レビュー<br/>CLI / Web]
    view -->|採用・除外・保留・編集| db
```

1. **発見・登録** — source別のsyncが候補を既存projectへ照合し、対象commandでは未知の候補を新規登録する。
2. **調査・判断** — enrichが根拠を収集し、resourceの帰属、説明、分類、稼働状態を評価する。観測したlinkやLLMの提案をそのまま採用するわけではない。
3. **保存・利用** — 現在状態と調査履歴を分けて保存し、CLI/Webで検索・レビュー・編集する。次の調査でもoperatorの採用・除外・保留を尊重する。

| 確認したいこと | 仕様 |
| --- | --- |
| 発見した候補の照合・取り込み | [発見と取り込み](specs/discovery-and-sync.md) |
| 調査pipeline・取得範囲・根拠 | [Investigation](specs/investigation.md) |
| resourceの帰属とprojectの統合・分離 | [Project境界](specs/project-boundaries.md) |
| 説明・紹介候補・追跡状態の決定 | [Project評価](specs/project-evaluation.md) |
| 現在状態・手動判断・履歴の保存 | [Project record](specs/project-record.md) |
| 長時間処理・中断・再開 | [Background jobs](specs/background-jobs.md) |

## 実行経路

公開shell入口は`bin/_project-registry-python`で専用Python世代を選び、実pathからCLI/Web/service/testを起動します。`bin/project-registry-python-env`と`scripts/project_registry_runtime.py`が保守時の世代準備・検査・選択を担当します。通常起動はuvやシステムPythonへ依存せず、Web workerとreloadは起動中の`sys.executable`を使います。更新には停止・新規起動が必要です。[環境の選択・切替・復旧](../user/python-environment.md)を参照してください。

CLIの`commands/`とWeb APIは入力をapplication serviceへ渡し、結果をtext/JSONまたはHTTP responseへ変換します。長時間処理ではCLIはserviceを直接呼び、Webは別processの`worker.py`を介します。serviceが進捗eventと終了resultを返し、enrichでは対象選択・保存・checkpoint・再開も担当します。

紹介候補・要レビュー一覧の取得とpayload組立ては`application/review.py`、判定・採点は`project_review.py`が担当します。通常一覧・batch・単件取得は同じreview項目生成を使い、全体の順位付けとbatchの走査位置は各入口で保持します。

追跡状態レビュー（stalled / ignore / out-of-scope）は`application/review.py`が候補抽出・並べ替えと状態・メモの保存を担当し、対話CLIと非対話serviceで共有します。候補選択時のprojectと更新番号を`ReviewSnapshot`で保持し、利用者の入力後もそのsnapshotで保存競合を検査します。対話入力・理由の選択・表示は`commands/review.py`、候補ID・計画照合・進捗・中断はserviceが担当します。CLIのDB接続はコマンド入口で開閉します。

[App record export](specs/app-record-export.md)は保存済みprojectから公開用JSONを生成します。[LLM事例保存](specs/llm-case-store.md)は明示登録した入力・出力・判断をGit管理外のJSONへ保存し、通常enrichには接続しません。

### CLI・Webとapplication service

`application/presentation.py`が検索・queue・詳細のpayloadを組み立てます。Web APIはDB schemaとは独立した`api_version: 2`を持ち、詳細は`project`、`identity`、`identifiers`、`relations`、`decisions`、`evaluation`、`investigation`に分かれます。`registration.py`が`identity`の調査対象と採用理由を現行identifier・adoptionから投影します。同値identifierをまとめた表示でも、`entries`にrole・classification・providerの対応を保持します。

`application/explain.py`はCLI/Web共通で、保存済み状態とrelationから結論、適用rule、支持・反証signalを返します。外部取得・LLM・DB更新は行いません。

Explainは`trust`または`all`が要求されたときだけ信頼説明を計算します。信頼説明では、identity・collectionの接続を必要になった時点でそれぞれ一度だけ一括準備し、同じ説明生成内でanchorと正規化済みgraph・policy別の経路索引、公開Lexiconと独立した採用の根拠を共有します。接続を追加した時点でgraphを作り直し、共有用のcontextはproject fieldに持たせず呼出し終了時に破棄します。説明用の評価は保存用evaluationを更新せず、調査・採用処理では従来どおり個別評価を保存します。requestをまたぐcacheや表示情報の省略は行いません。

同時編集は自分のCLI、Web、worker間を想定します。Web内のmutationはlockで直列化し、process間ではproject更新番号と保存transaction内の検査で競合を検出します。詳細からのmutationは`expected_revision`を必須とし、古い詳細を`409 stale_project`で拒否します。紹介投稿の要確認はitem IDを指定し、保存時に現在の判断とproject更新番号を検査します。

### 一覧・詳細の検索経路

Firefox拡張のポップアップ試作は、`embed=popup`のWeb UIをiframeへ埋め込みます。Web UIは初回認証用フォームと狭い画面向け表示を使います。拡張は接続先originを指定した`postMessage`でframeへ専用MessagePortを渡し、その通信口から受け取ったサイトURL・検索条件・project key・詳細tabだけで別タブのURLを組み立てます。token・storage・未保存入力は渡しません。Web UI側は`popup_parent`で指定された拡張のoriginを照合し、送信元が親frameかFirefoxが参照を公開しない場合だけ通信口へ応答します。開き方は拡張設定で従来のタブ表示へ戻せます。試用手順と制限は[拡張のREADME](../../extras/firefox-registry/README.md)に置きます。

`application/browser_entry.py`は訪問URLから保存済みidentifierを照合するread-only入口と、利用者が取り置いた紹介予定一覧を担当します。Firefox拡張はURLを`screen=browser&url=...`へ渡すだけで、認証・DB保存はWeb UI側に置きます。サービス・サイトURLの完全一致かパス区切りごとの親URL、対応forgeのrepository境界を使います。hostのlabelを逆順にしたnamespaceと区切り付きprefixが一致するconfirmed/candidateの`owned_nsid`も検索候補に含めます。hostは親domainへ広げず、NSIDの元のcaseを表示します。`/api/browser-entry?q=...`は通常のテキスト検索へこの照合を追加し、件数・limitは両者を合わせたproject単位で計算します。NSIDだけの一致、candidate URLだけの一致、複数project一致では自動選択しません。GitHub、Tangled、Codeberg、GitLab、SourceHut、Bluesky、pds.ls等の共用hostでは単純な親service/site URL一致を使いません。redirectの取得やLLM照合は行わず、不一致時は手動検索・シードpreviewへ進めます。

`application/feedback.py`が紹介予定とサービスのLexicon利用の明示保存・解除を担当します。`operator_feedback.py`がdecision履歴から現在の取り置きと利用relationを投影し、既存の完全runtime view・revision check・transactionを使って保存します。拡張からの閲覧・照合だけでは判断を記録しません。データ契約は[operator decision](specs/project-record.md#サイト閲覧後の手動判断)を参照してください。

通常一覧は`projects`とnormalized identifier・relation・decisionへの`EXISTS` queryで絞り込み、全projectをhydrateしません。詳細は選択した1件の子行を読みます。文字列検索は保存済みfieldの部分一致で、操作は[Web UIガイド](../user/web-ui.md#登録プロジェクト)を参照してください。

通常一覧の保留表示・filterは、期限内の保留または旧確認期限を持つprojectだけを同じread snapshot内でhydrateし、`promotion_attention.active_deferral`で根拠変化・解除・期限を評価します。SQLは候補の先行絞込みに使い、保留の有効性を別実装しません。decisionの期限filterは時差を含む日時として比較します。

紹介候補一覧は完全runtime viewを使い、読込み時の正規化結果をqueueとsignalで再利用します。先行絞込みとread snapshotの契約は[評価仕様](specs/project-evaluation.md#紹介候補の表示順位保留)に従います。

詳細のマージ候補は`/api/projects/{key}/merge-analysis`で別途計算し、重複候補の計算結果を推薦とExplainで共有します。単件の比較では、`list_duplicate_candidate_inputs`が名前・代表URL・identifierの小さな列とconfirm対象だけを読み、`project_merge.duplicate_candidate_keys`が一致し得るprojectを広めに選びます。guard・採用根拠による除外はこの段階では行わず、選択したprojectだけを`list_duplicate_projects`で従来の比較入力へ組み立て、既存の判定を適用します。先行絞込みとstate・identifier・decision・adoptionの読出しは同じread snapshotです。対象自身は比較集合から除き、対象profileは同じ計算内で再利用します。保存済みnamespaceによる一致・creatorとのlineage比較を含む入力では全件の比較集合を維持します。一覧の`/api/merge-batch`は`list_batch_merge_projects`で`ignore`以外の比較入力と方向選択用のservice status・紹介URL・補助URLを読み、全件のprofile・索引・key辞書を一度作って一覧全体で共有します。これらの部分的なviewは保存不可で、requestをまたぐprojectや候補のcacheは持ちません。呼出元が比較集合を渡す経路はその集合を使います。

画面遷移時はブラウザが詳細とマージ解析の通信を取り消し、request世代とproject/revisionを照合して古い結果を描画しません。`request_cancellation.py`が接続断を検知し、applicationの確認点とSQLite progress handlerで協調的に計算を止め、DB接続等を解放します。別タブへは作用せず、計算中にproject revisionが変わった結果も破棄します。中断は即時の強制終了ではなく、HTTPS中継経由では切断が上流へ伝わることが必要です。

## Module構成と依存方向

以下は`scripts/project_registry_core/`配下です。WebのHTTP・静的UI・process管理は`scripts/project_registry_web/`にあります。

### 配置の単位

以下の機能別packageは、`__init__.py`で内部moduleを一括importせず、呼出元が担当moduleを直接参照します。

| Package | 配置する処理と境界 |
| --- | --- |
| `identity/` | 汎用identifierの正規化、domain・tenant境界、観測済みURL alias。repository固有のURL規則は`repositories/` |
| `repositories/` | repository URL、provider別取得、素材抽出・解析、リンク文脈、repository帰属とhomepageのrole判定。取得と判定は別moduleを維持 |
| `publications/` | Lexicon公開情報のevent・schema解析、カタログ、検証保存、projectへの投影、型別consumer判定 |
| `transport/` | HTTP送受信、GitHub rate limit、LLMのcommand/endpoint接続、retry・cache。取得対象の選択やprojectへの適用は外側 |
| `model_evaluation/` | corpusによるLLM・repository解析・relation評価と非公開事例保存。明示的な評価commandから使い、通常enrichから呼ばない |
| `persistence/` | SQLite schema・接続、transaction・競合検査、normalized row・runtime view、調査履歴保存。projectの形・手動判断・fetch/list/saveの組立ては直下の`project_record.py` |
| `maintenance/` | 明示的な比較・移行と保存量の整理。通常のquery・調査・保存から呼ばない |
| `application/` / `commands/` | 利用操作の組立てとCLI入出力。機能別の処理とは別の境界 |

直下にはproject全体の状態・判断、共通の根拠・relation・信頼評価、調査の制御・観測、replay、実行入口を置きます。各moduleの責務と接続は以下の表に示します。

### 入口・処理の制御

| Module | 担当 |
| --- | --- |
| `commands/` | CLI引数・出力。queueの対話は`queue_interaction.py`、stalled/ignore/out-of-scopeは`review.py` |
| `application/` | CLI/Web共通service。`argparse.Namespace`やstdoutへ依存しない |
| `jobs.py` / `worker.py` | request/event/resultと協調的中断。workerはstdinの`OperationRequest`を実行しJSON Linesを返す |
| `application/sync.py` / `source_sync.py` | source別取得、watermark、既存projectへの接続。テキストシードの解析・参照先解決・取り込みも`source_sync.py`が担当 |
| `application/publications.py` / `publications/publication_registry.py` | [Lexicon公開情報](specs/lexicon-publication-catalog.md)の取得・検証、保存、project候補への照合 |
| `publications/publication_verification.py` | syncとenrichの検証結果保存。公開本文・観測時刻の照合と調査保存への接続 |
| `introduced_review.py` | 紹介投稿の対象・参考リンク分類、`project_merge`と共通の候補照合、手動紐付け・新規作成・取消。判断はqueryの`sync_state`、紹介URLはproject本体へ保存 |
| `application/enrichment.py` | enrichの対象選択・transaction・保存・checkpoint |
| `enrichment_pipeline.py` | 1 projectのcollector・LLM・保存前更新の実行順序。Lexicon・record sample・resource pageの収集結果をEvidenceBundleへ梱包する処理も保持 |
| `application/enrich_prefetch.py` | LLM区間中の次の一件の先行取得。DBとLLMはcaller threadに保ち、使用前にsnapshotを照合 |
| `registry.py` | collector適用・finalizeの補助処理 |
| `maintenance/` | 明示的な監査・修復・比較・移行・保存量整理。全件監査と修復は`registry_audit.py`、tenant分割候補は`source_config.py`。通常の調査・query・保存から呼ばない |
| `dev_database.py` | 架空定義から開発DBを生成・リセット。通常CLIのDB選択は`cli.py` |

### 観測・統合・評価

| Module | 担当 |
| --- | --- |
| `evidence.py` | `EvidenceBundle` v2、観測・relation・collector結果と失敗の型 |
| `investigation.py` | 検索語の固定生成とcollector。project dictを変更せずevidence bundleを返す |
| `repositories/repository_sources.py` | provider別metadata・README・tree・file取得と短期cache |
| `repositories/repository_material.py` | 取得済みfileからmanifest・configuration・Lexicon・source hintを抽出 |
| `repositories/repository_analysis.py` | confirmed repoの解析素材縮約、revision・policy単位の再利用、成功結果と最新試行の管理 |
| `service.py` / `tangled.py` / `relations.py` | service観測、Tangled PDS/UFOs、typed relation。Tangledの所有観測とproject固有のrepository候補判定を分離し、integrationは採用済みrepoだけから候補テキストを適用 |
| `html_link_context.py` / `account_links.py` / `developer_links.py` | 共有HTMLリンク文脈抽出と、websiteが案内する公式Bluesky account、開発・保守の役割を示すaccountの候補判定。developerの自己申告も根拠付き候補に限定する。repositoryの判定は`repositories/`が担当 |
| `tangled_identity.py` | 同期用DID・handle双方向検証と取得失敗の分類。帰属・保留保存は`source_sync.py` |
| `oauth_metadata.py` / `oauth_scope_parser.mjs` | 取得済みOAuth metadataを公式SDKで解析。Nodeで外部取得はしない |
| `usage_material.py` | collection名に対応する宣言・record観測・原文の関連記述を抽出。用途判定はしない |
| `integration.py` | 観測をprojectへ適用し、query・ログ・失敗を記録。取得済みLexicon schema・record sampleの縮約も担当。共通の正規化・開発者情報・信頼edgeは担当moduleを直接呼び、callbackは採用判断・probe／Lexicon適用に限定 |
| `project_review.py` | review signal・紹介候補の採点／保留／優先度・追跡状態の提案。DB・registry・applicationには依存しない |
| `project_merge.py` | 重複候補の比較・推薦・batch計画、未保存recordの合成、authority domainの分割対象識別子。比較集合のDB読出しは可能だが、外部取得・finalize・保存は担当しない |
| `evaluation.py` | ownership・atproto寄与度・service statusの純粋評価 |
| `collection_trust.py` / `trust_graph.py` | Lexicon公開応答の検証・限定したcollection信頼policyと共通経路評価。取得・採用・DB保存は分離 |
| `editorial.py` | 説明と更新案・根拠参照・編集保護。通常triageとWeb編集で共有 |
| `atproto_usage.py` | 利用範囲の理由付き手動判断・解除・マージ。サービス確認・技術評価から独立 |
| `promotion_attention.py` | 候補再確認用snapshotと直近の明示操作。品質scoreや自動閲覧判定は持たない |
| `triage_reuse.py` | 入力・実行条件・手動判断・期限を照合し、triage成功出力を再利用。他の調査・適用処理は省略しない |

### 保存と外部接続

| Module | 担当 |
| --- | --- |
| `persistence/database.py` / `persistence/runtime_view.py` | schema・接続・normalized rowのsnapshotと処理用view |
| `project_record.py` | project shape、正規化、保存payload、hydrate、fetch/list/save、exact lookup、key割当 |
| `persistence/concurrency.py` | 更新番号trigger・短いtransaction・競合検査。外部取得・LLM中にwrite lockを保持しない |
| `persistence/storage.py` / `replay.py` | 調査履歴保存、network-free replay、fixture benchmark |
| `enrich_observations.py` | 比較用snapshotと処理時間。集計入口は`scripts/enrich_observation_report.py` |
| `model_evaluation/llm_case_store.py` / `private_data.py` | 非公開事例保存と共通の認証情報除去 |
| `identity/domain_policy.py` | 同梱PSLとhosting suffix設定によるdomain・tenant境界。依存・PSL欠落はエラー |
| `transport/http_transport.py` | outbound URL検証、内部host遮断、redirect先再検証、HTTP呼出し |
| `transport/atproto.py` | Bluesky認証・session更新・投稿／profile取得、DNS・DID解決、Lexicon取得と応答解析。registry・project record・保存層に依存せず、採用判断は呼出元で行う |
| `identity/identifiers.py` / `identity/resource_identity.py` / `repositories/repo_urls.py` | URL・NSID・atproto account・repositoryの共通正規化と資源key、型選択、明示的なguard範囲、repo provider・owner判定。role別の採用判断は行わない |
| `naming.py` | title短縮、名前候補の保持・除外・採点・選定。識別情報は`project_record.py`から読み、取得・DB保存・registryへの依存を持たない |
| `transport/llm_transport.py` | command/互換endpoint呼出し、応答解析、timeout・error・metrics。prompt構築と適用は外側 |

### 依存境界の制約

- `source_sync.py`と`enrichment_pipeline.py`は保存を重複実装せず`project_record.py`を使う。`project_record.py`は`registry.py`へ依存しない。
- application・commands・pipelineにはdomain facadeとして`registry.py`への依存が残る。Web主要query/mutationはapplicationを通るが、bootstrap・検索・補助read pathには直接依存もある。
- `persistence/database.py`のopen/saveは`registry.py`や`project_record.py`をimportしない。
- 比較・migration用コピーの生成は`database.materialized_database_copy`がread-onlyの取得元接続、新規コピー先の排他作成、backup、commit・失敗時の削除を担当する。`maintenance/`内の各materializationは必要なschema世代と変換・検証規則を持ち、既存コピーや正本を上書きしない。
- 分離したmoduleを旧symbol互換のため`registry.py`からre-exportしない。collectorのtestはHTTP transport、LLMのtestはcommand/endpointをfakeへ差し替える。
- 名前と取り込みの照合に使うproject・seedの識別token、および候補textの追加は`project_record.py`が担当する。`integration.py`は名前候補を`naming.py`へ直接渡し、名前処理をregistryのcallbackで受け取らない。

`registry.finalize_project_shape`は採用抑止条件の適用、identityの整理、派生viewの更新を順に組み立てる。除外・保留の適用は候補の正規化前後に必要なため順序を維持する。アカウントの照合・補完は`integration.py`、repository viewの正規化は`project_record.py`、代表名の確定は`naming.py`が担当する。保存済みLLM根拠のアカウントは不適切な観測を除くが、project本体へのhandle補完や候補重複の除去を遡及適用しない。

`application/merge.py`が統合recordをfinalizeして、競合検査・識別子移送・alias更新・保存を同じtransactionで行う。`project_merge.py`はregistryやapplicationをimportしない。マージ比較の補助DID照合はapplicationが保存済みLexicon発行元とrecord側の明示照会で得た先頭DIDを区別して組み立て、候補判定へ混ぜない。識別子の採用・除外・保留照合と手動repoの参照は`project_record.py`に集約する。

authority domain・tenant単位の分割も`application/merge.py`が新旧projectの更新・finalize・競合検査・保存を担当する。tenant分割候補の対話表示・選択・再表示は`commands/maintenance.py`、候補生成は`maintenance/source_config.py`を使う。監査・修復はregistryの通常の採用・正規化規則を呼び、独自の採用規則を持たない。

resource pageの統合は既存更新関数をprojectのcopyへ適用し、許可fieldの差分を返す暫定実装です。cacheと連続失敗を抑止するcircuit breakerはprocess・情報源単位で、process終了時に状態を失います。
