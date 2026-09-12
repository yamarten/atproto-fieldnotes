# Project Registry Status

通常導線では、CLI/Webでの登録・検索・手動review、sourceの増分取り込み、enrichによる調査・評価、紹介候補の提示と追跡を利用できます。運用はローカルのschema v3 DBを中心に、Web workerと定期presetで行います。

現行機能の適用にも境界があります。新しい判定規則による既存project全件の再評価、非現行identifierの専用編集UI、sync/reviewのcheckpoint再開は揃っていません。Lexiconの独立観測やshadow比較も通常の自動採用とは別に扱います。以下では機能ごとに、利用できる範囲と、その説明を支える確認・制約を示します。

実運用については2026-09-10までの適用・確認記録に基づきます。それ以後の実環境や正本DBの状態は未確認です。

## 登録・検索・手動編集

CLI/Webからseed登録、一覧・詳細検索、tracking変更、identifierのconfirm/block/defer、profile編集を利用できます。通常導線はschema v3 DBです。Websiteとservice identifier、resourceのroleとclassificationを区別して表示します。非現行identifier・採用来歴・transition・exclusionは保存可能ですが、former/non-productionの専用編集UIは未接続です。identifier decisionは`kind + value`全体へ作用し、同じ値のroleごとには分けられません。

検索はnormalized fieldの部分一致・構造化filterで、summaryやREADME全文、自然語の概念検索には対応しません。保存・mutation・aliasのtestと、schema v3切替後のoperatorによる典型操作・定期sync/enrichを確認した記録があります。現在の全データの正しさを保証するものではありません。[schema切替の記録](notes/identifier-storage-shadow-comparison.md)を参照してください。

## 発見と取り込み

introduced、collection inventory、Bluesky公式Lexicon、GitHub、Semble、Tangledの取り込みと、共通source設定をCLI/Web/workerへ接続しています。sourceごとに既存projectへの照合、未一致、watermark、dry-run/executeを扱います。TangledはJetstream v2を使い、上限で区切った増分実行に対応します。

外部sourceへのbounded smokeとTangledの実適用・照合補正を確認しています。ただし、過去のsmokeを現在の外部APIの保証とは扱いません。Tangled full snapshotの初回完走時間・API利用量、長時間運用のretry/cache/budgetは未検証です。[source評価](notes/source-lane-evaluation-2026-08-30.md)、[workerの外部source確認](notes/registry-maintenance-real-source-smoke-2026-07-11.md)を参照してください。

## 調査・観測・Replay

通常enrichはcollectorの観測を統合し、rule-based評価とLLM補助、current stateと調査履歴の保存を行います。Webから失敗・限定されたevidence・phase時間・LLM requestの情報を確認し、保存runをread-onlyでreplayできます。collectorの部分失敗と再試行分類、temp DBのenrich→save→fetch、保存runのreplayを確認した記録があります。履歴の全HTML/READMEを保存・表示する機能ではなく、古いrunのmetrics欠落は未記録です。

serviceはstatic/deep probeと匿名browser確認を利用できます。Webから保存済みservice URLを選んで確認する導線も接続済みです。認証入力・登録・投稿は行わず、loginへの到達は認証後の機能の保証ではありません。検証用HTMLの実Chromium、保存・replay、限定した公開URLを確認していますが、実Playwrightによるlocalhost smokeは未検証です。[browserの実導線確認](notes/source-lane-evaluation-2026-08-30.md#anonymous-browser-verification)、[page観測の確認](notes/homepage-observation-2026-09-08.md#browser観測の追加検証と修正)を参照してください。

## Repository解析と公開先の採用

confirmed repositoryのrevision固定解析、根拠位置とclosed candidateによる出力検査、最新成功結果と失敗した試行の分離を通常enrichへ接続しています。GitHub/CodebergとTangledでは取得可能な材料が異なり、Tangledの完全なtree取得は未対応です。候補repositoryは相互trust確認のための限定取得に留め、confirm前に通常profileの根拠へ使いません。

README公開先の候補化と、帰属・逆参照・Web/protocol実在確認を通したservice採用を接続しています。`repository-analysis-v16` / `repository-homepage-role-v4`は2026-09-10に実運用反映済みです。既存project全件の再解析・再採用は実施していません。代表project siteの自動確定、汎用API・AppView独自APIの確認は未接続です。

固定material・保存実例・限定した公開URLによる候補化、確認、temp DB保存・replayの記録があります。小規模な意味確認を一般的な正解率とは扱いません。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、package配布repositoryの役割、fixture Lexiconの解釈には残る課題があります。[解析仕様](developer/specs/investigation.md#repository-analysis)、[採用仕様](developer/specs/project-boundaries.md#repository-homepageと公開先の評価)、[公開先検証・適用記録](notes/service-publication-2026-09-10.md)、[解析評価](notes/llm-model-evaluation.md#2026-08-31-closed-candidate契約)を参照してください。

## 帰属・信頼・Projectの統合と分離

現在のpage↔repository relationから信頼edgeを導出し、candidate probe、信頼評価、Explain、shadow比較へ接続しています。serviceの採用にはrepositoryを含む相互参照だけを使わず、公開先のrole policyを通します。旧保存edgeのarchive・除去は実運用反映済みで、保存済みの過去採用を新規評価だけで一律に取り消しません。[最終適用記録](notes/repository-trust-projection-2026-09-07.md#2026-09-10-最終反映完了)では、判定用graphと現行relation・登録URL・採用判断・調査履歴の不変、整合性検査、再起動後のWeb API応答を確認しています。

通常のMerge review、split/separate、merge aliasによる履歴参照を利用できます。merge/split/separateの原子的保存と、merge前に始まった古いenrichによるalias元keyの再作成拒否を確認しています。

広いrole-aware trust、merge policy、専用relation assessorのshadow比較は、通常の自動採用・自動mergeとは別の経路です。一時的なcollection断片のreview導線は撤去済みで、そこで扱ったのはproject境界までです。identifierごとのcurrent/former/non-production/noise選択まで確認済みとは扱いません。[通常接続の記録](notes/collector-relation-policy-shadow-comparison.md#初期production接続)、[merge reviewの確認範囲](notes/project-merge-policy-shadow-comparison.md#一時導線終了時の監査)を参照してください。

## 説明・紹介候補・追跡

LLMと根拠の変化に応じたprofile更新、atproto寄与度、service利用可否、technical interestを利用して候補を提示します。Promotionでは根拠確認、期限付き保留、優先度変更・解除を利用できます。未確認のservice候補も要validationとして残します。活動・調査鮮度に基づくtracking reviewも接続済みです。

manual goldを通常queue生成へ通す固定評価と、紹介済みprojectを使うread-only分析があります。紹介実績分析は現在値に対する評価で、当時の推薦を復元せず、未紹介projectを負例にしません。既存profileのsummaryとcategoryの不整合は残り、全件の一括再分類は実施していません。[評価方法](developer/specs/investigation.md#replay-と-benchmark)を参照してください。

## LLM構成の評価

記録上の既定modelはQwen 3.6 35B A3Bです。通常enrichと定期jobでmodelに応じた追加規則を使用します。固定evidenceでprofile有無を分けたtriage評価と、repository解析からtriageへの二段階評価を利用できます。比較reportは機械検査・反復差・文章確認等を区別し、通常modelを自動変更しません。

Gemmaは比較時のrepository解析でJSON失敗が再現し、通常処理への置換試用を停止しています。Qwen維持は解析内容が全面的に正しいという判断ではありません。後続のclosed candidate契約での確認と旧契約の結果も区別します。低優先度modelの通常自動切替、同一workloadのmemory・swap・prefill/decode自動計測は未接続です。[評価手順](developer/workflows/llm-model-evaluation.md)、[構成・比較・採用判断](notes/llm-model-evaluation.md)を参照してください。

## JobとWeb運用

enrich、sync、review、匿名browser確認を別processで実行し、進捗・協調停止・presetの手動／定期実行に対応します。bulk enrichはcheckpointから再開できます。Webの手動reloadとmacOS LaunchAgentによる自動起動も利用できます。実worker・temp DBの完了／中断／再開、Webからのjob実行、正本でのbulk enrich停止・再開の記録があります。

sync/reviewのcheckpoint再開と、server再起動後のprocess job一覧・event復元は未接続です。Web内のmutation lockはCLI等の別processを排他しません。同時writeはSQLiteのlockとbusy timeoutに委ねます。並行runの観測はありますが、重複実行が更新消失や中断の原因だったかは未確認です。run timingはproject SQL書込みまでで、commit待ち・job全体・operator review時間を含みません。[再開記録](notes/homepage-observation-2026-09-08.md#正本適用と一括enrichの再開2026-09-09)、[現在有効な保留](#有効な運用上の保留注意)を参照してください。

## 保存とMaintenance

schema v3の保存・normalized検索、履歴圧縮、保持policyによる手動削除、派生値のrepairを通常導線へ接続しています。通常openは既存schema v3を必須とし、旧DBを暗黙変換しません。汎用migration runnerやDB-level data policy markerはありません。履歴圧縮の複製DB同値確認・正本適用、merge/splitのtransaction確認の記録があります。これらは過去の適用確認で、現在のDB容量・全件整合性を再監査した結果ではありません。[Maintenance手順](developer/workflows/change-acceptance.md#maintenance適用の確認)を参照してください。

## App record export

保存済みprojectのread-only validationと、明示manifestからのentry JSON生成を利用できます。不適格recordを含む出力を全件拒否する契約で、PDS送信とrkey生成は担当しません。生成・検査経路があることと、実際にdirectoryを公開・運用していることは区別します。[Export仕様](developer/specs/app-record-export.md)を参照してください。

## Lexiconの独立観測

developer scriptとOperations週次presetによるsidecar観測を接続済みです。registryへ自動反映しません。boundedな実取得・publisher確認・正本registry不変を確認した記録があります。

2026-09-10の保存済みstate確認では、sidecar schema v3、baseline revision 7、有効period数0、`review_due=false`で、日曜05:00 Asia/Tokyoのpresetは有効でした。直前sync失敗により比較不能だったrunを有効periodには数えていません。このstate確認は最初の比較可能run前後のregistry不変や、現在のWeb表示の再検証ではありません。最初の比較可能runでのperiod加算・registry不変の確認は[作業メモ](notes/operations-resumption-2026-09-10.md#lexicon週次観測の初回確認)に残します。[Sidecar仕様](developer/specs/lexicon-publication-catalog.md)、[smoke記録](notes/jetstream-lexicon-publication-catalog-smoke-2026-09-05.md)、[初期運用評価](notes/lexicon-publication-catalog-initial-operations-2026-09-05.md)を参照してください。

## 有効な運用上の保留・注意

旧coverage失敗604件とPDSlssの追加enrichは保留され、キャンセル済みrunの再開は指示されていません。永続migration archiveは一時ファイルとして削除しません。移行前backupの保持世代は未決です。対象run、path、解除前の確認事項は[2026-09-10の運用保留・再開情報](notes/operations-resumption-2026-09-10.md)を参照してください。
