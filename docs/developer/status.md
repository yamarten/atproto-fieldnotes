# Project Registry Status

通常導線では、CLI/Webでの登録・検索・手動レビュー、外部情報源からの増分取り込み、enrichによる調査・評価、紹介候補の提示と追跡を利用できます。運用はローカルのschema v3 DBを中心に、Webの処理実行用process（worker）と定期実行の設定（preset）で行います。

現行機能の適用にも境界があります。新しい判定規則による既存project全件の再評価、非現行identifierの専用編集UI、取り込み処理・追跡状態レビューのcheckpoint再開は揃っていません。Lexiconの独立観測や、判定結果をDB更新へ適用せず比較するshadow経路も通常の自動採用とは別に扱います。以下では機能ごとに、利用できる範囲と、その説明を支える確認・制約を示します。

実運用については2026-09-10までの適用・確認記録に基づきます。それ以後の実環境や正本DBの状態は未確認です。

## 登録・検索・手動編集

CLI/Webからseed登録、一覧・詳細検索、追跡状態の変更、identifierの採用・除外・期限付き保留（confirm/block/defer）、profile編集を利用できます。通常導線はschema v3 DBです。Websiteとservice identifier、resourceの役割（role）と採用状態（classification）を区別して表示します。非現行identifier・採用来歴・移転関係・再登録の抑止情報は保存可能ですが、旧構成要素（`former`）と開発・試験用（`non_production`）の専用編集UIは未接続です。identifierへの判断は`kind + value`全体へ作用し、同じ値のroleごとには分けられません。

検索は正規化して保存したfieldの部分一致・構造化filterで、summaryやREADME全文、自然語の概念検索には対応しません。保存・変更操作・マージ元keyの解決のtestと、schema v3切替後の利用者による典型操作・定期sync/enrichを確認した記録があります。これらの経路の確認は、現在の全データの正しさを保証するものではありません。保存契約は[project record](specs/project-record.md)を参照してください。

## 発見と取り込み

introduced（紹介済み投稿）、collection inventory（collectionの活動一覧）、Bluesky公式Lexicon、GitHub、Semble、Tangledの取り込みと、共通の外部情報源設定をCLI/Web/workerへ接続しています。情報源ごとに既存projectへの照合、未一致、取り込み済みの境界（watermark）、候補確認（dry-run）と保存（execute）を扱います。TangledはJetstream v2を使い、上限で区切った増分実行に対応します。[取り込み仕様](specs/discovery-and-sync.md)で情報源ごとの条件を定めます。

外部情報源への取得件数・request数を限定した疎通・動作確認（bounded smoke）と、Tangledの実適用・照合補正を確認しています。Tangledの全snapshotの初回完走時間・API利用量、長時間運用の再試行・cache・取得上限の妥当性は未検証です。

## 調査・観測・Replay

通常enrichは収集処理（collector）の観測を統合し、規則による評価とLLM補助、現在状態と調査履歴の保存を行います。Webから失敗・保存上限内の根拠・処理段階ごとの時間・LLM requestの情報を確認し、保存した調査実行記録（investigation run）を読み取り専用で再評価（replay）できます。collectorの部分失敗と再試行分類、一時DBのenrich→save→fetch、保存した調査実行記録のreplayを確認した記録があります。HTML/READMEの履歴をすべて保存・表示する機能はありません。古い調査実行記録で計測値が欠けている場合は未記録として扱います。

serviceは静的HTTP取得（static probe）・追加ページ調査（deep probe）と匿名browser確認を利用できます。Webから保存済みservice URLを選んで確認する導線も接続済みです。匿名browser確認は、JavaScript描画後の画面やloginへの到達を確認する時に使え、稼働状態の判断を補う効果を確認しています。認証入力・登録・投稿は行わず、loginへの到達は認証後の機能の保証ではありません。検証用HTMLの実Chromium、保存・replay、限定した公開URLで確認していますが、実Playwrightによるlocalhostでの疎通・動作確認は未検証です。[観測仕様](specs/investigation.md#staticdeepbrowserの調査範囲)を参照してください。

## Repository解析と公開先の採用

confirmed repositoryのrevision固定解析、根拠位置と事前生成した候補集合（closed candidate）による出力検査、最新成功結果と失敗した試行の分離を通常enrichへ接続しています。GitHub/CodebergとTangledでは取得可能な材料が異なり、Tangledの完全なtree取得は未対応です。候補repositoryは相互参照による帰属の信頼確認に必要な限定取得に留め、confirm前に通常profileの根拠へ使いません。

README公開先の候補化と、帰属・逆参照・Web/protocol実在確認を通したservice採用を接続しています。`repository-analysis-v16` / `repository-homepage-role-v4`は実運用反映済みです。既存project全件の再解析・再採用は実施していません。代表project siteの自動確定、汎用API・AppView独自APIの確認は未接続です。

固定した解析素材・保存実例・限定した公開URLによる候補化、確認、一時DB保存・replayの記録があります。意味の確認はこれらの小規模な事例を対象としています。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、package配布repositoryの役割、fixture Lexiconの解釈には残る課題があります。[解析仕様](specs/investigation.md#repository-analysis)、[採用仕様](specs/project-boundaries.md#repository-homepageと公開先の評価)を参照してください。

## 帰属・信頼・Projectの統合と分離

現在のpage↔repositoryの関係（relation）から信頼グラフの接続（edge）を導出し、候補URLの確認、信頼評価、Explain（判定理由の表示）、shadow比較へ接続しています。serviceの採用には、公開先が担う役割を検証するrole policyを通します。旧保存edgeのarchive・除去は実運用反映済みで、保存済みの過去採用を新規評価だけで一律に取り消しません。適用時に判定用graph・現行relation・登録URL・採用判断・調査履歴が変わらないことと、DB整合性・Web API応答を確認しています。

通常のマージレビュー、分割（split）・別project指定（separate）、マージ元keyを存続先へ解決するaliasによる履歴参照を利用できます。merge/split/separateの原子的保存と、merge前に始まった古いenrichによるalias元keyの再作成拒否を確認しています。

roleごとの信頼規則を広げる比較、マージ規則の比較、一つの関係の意味を判定する専用relation assessorのshadow比較は、通常の自動採用・自動mergeとは別の経路です。一時的なcollection断片のマージレビュー導線は撤去済みで、そこで確認したのはproject境界までです。identifierごとの現行・旧構成要素・開発試験用・無関係な誤検出の選択は確認範囲に含めません。判断材料が残る例は[identifier処遇](../notes/temporal-project-relations.md#一時merge-review後に残ったidentifier処遇)と[repository帰属](../notes/repository-attribution-open-questions.md)へ分けています。

## 説明・紹介候補・追跡

LLMと根拠の変化に応じたprofile更新、atproto寄与度、service利用可否、技術的な学習価値（technical interest）を利用して候補を提示します。`Promotion`（紹介候補）では根拠確認、期限付き保留、優先度変更・解除を利用できます。未確認のservice候補も要確認として残します。活動・調査鮮度に基づく追跡状態の見直しも接続済みです。

人が期待判断を定めた固定事例（manual gold）を通常の紹介候補一覧生成へ通す評価と、紹介済みprojectを使う読み取り専用の分析があります。紹介実績分析は現在値に対する評価で、当時の推薦を復元せず、未紹介projectを負例にしません。既存profileのsummaryとcategoryの不整合は残り、全件の一括再分類は実施していません。[評価方法](specs/investigation.md#replay-と-benchmark)を参照してください。

## LLM構成の評価

記録上の既定modelはQwen 3.6 35B A3Bです。通常enrichと定期jobでmodelに応じた追加規則を使用します。固定evidenceでprofile有無を分け、project全体の説明・分類を補うtriageの評価と、repository解析からtriageへの二段階評価を利用できます。比較reportは機械検査・反復差・文章確認等を区別し、通常modelを自動変更しません。

Gemmaは比較時のrepository解析でJSON失敗が再現し、通常処理への置換試用を停止しています。Qwenを維持していますが、解析内容の品質は引き続き評価対象です。後続のclosed candidate契約での確認と旧契約の結果も区別します。Agents A1の低優先度処理への試用も、比較時の速度・Qwenとの自動切替時の共存条件から停止しています。低優先度modelの通常自動切替と、同じ処理を実行した時のmemory・swap・入力処理（prefill）・出力生成（decode）の自動計測は未接続です。[評価手順](workflows/llm-model-evaluation.md)、[候補固有の保留・再開材料](../notes/llm-model-evaluation.md)を参照してください。

## JobとWeb運用

enrich、sync、追跡状態を見直すreview、匿名browser確認を別processで実行し、進捗表示・処理境界での停止（協調停止）・presetの手動／定期実行に対応します。一括enrichは進行状況を保存したcheckpointから再開できます。Webの手動reloadとmacOS LaunchAgentによる自動起動も利用できます。実worker・一時DBの完了／中断／再開、Webからのjob実行、正本での一括enrich停止・再開の記録があります。

syncと追跡状態レビューのcheckpoint再開、server再起動後のjob一覧・進捗eventの復元は未接続です。Web内の変更操作用lockはCLI等の別processを排他しません。同時書込みはSQLiteのlockと待ち時間上限（busy timeout）に委ねます。調査処理の並行実行は観測されていますが、重複実行が更新消失や中断の原因だったかは未確認です。調査実行記録の時間計測はproject一件のSQL書込みまでで、commit待ち・job全体・人によるレビュー時間を含みません。[現在有効な保留](#有効な運用上の保留注意)を参照してください。

## 保存とMaintenance

schema v3の保存・正規化fieldの検索、履歴圧縮、保持規則による手動削除、派生値の修復を通常導線へ接続しています。通常のDB接続は既存schema v3を必須とし、旧DBを暗黙変換しません。汎用的に移行を実行する仕組みや、適用済みのデータ処理規則をDB全体に記録するmarkerはありません。履歴圧縮の複製DB同値確認・正本適用、merge/splitのtransaction確認の記録があります。これらは過去の適用確認で、現在のDB容量・全件整合性を再監査した結果ではありません。[Maintenance手順](workflows/change-acceptance.md#maintenance適用の確認)を参照してください。

CLI/Webの手動確認には`dev-db`で生成・リセットできる共通ダミーDBを利用できます。架空データからの生成、上書き拒否・lock競合・失敗時rollbackのtemp DB test、通常CLIのDB選択・編集・replayと実ブラウザでの一覧・紹介候補・識別子採用・調査履歴を確認しています。外部取得・実LLM・background jobの実行確認や、実データ固有の監査・件数・容量確認を代替しません。[確認手順と安全境界](workflows/change-acceptance.md#cliwebの共通ダミーdb)を参照してください。

## App record export

保存済みprojectのread-only validationと、明示manifestからのentry JSON生成を利用できます。不適格recordを含む出力を全件拒否する契約で、PDS送信とrkey生成は担当しません。生成・検査経路があることと、実際にdirectoryを公開・運用していることは区別します。[Export仕様](specs/app-record-export.md)を参照してください。

## Lexiconの独立観測

developer scriptと`Operations`の週次presetによる、registryとは別の専用DB（sidecar）への観測を接続済みです。registryへ自動反映しません。取得上限を設けた実取得・公開者（publisher）の確認・正本registry不変を確認した記録があります。

2026-09-10の保存済みstate確認では、sidecar schema v3、比較開始点のrevision 7、比較条件を満たした週（valid period）数0、`review_due=false`で、日曜05:00 Asia/Tokyoのpresetは有効でした。直前sync失敗により比較不能だった観測実行は有効periodに数えていません。最初の比較可能な観測実行前後でのregistry不変と、現在のWeb表示は未再検証です。初回のperiod加算・registry不変の確認は[作業メモ](../notes/operations-resumption-2026-09-10.md#lexicon週次観測の初回確認)に残します。[Sidecar仕様](specs/lexicon-publication-catalog.md)を参照してください。

## 有効な運用上の保留・注意

PDSlssの追加enrichは保留され、キャンセル済みの一括enrichの再開は指示されていません。移行時の永続archiveは一時ファイルとして削除しません。移行前backupの保持世代は未決です。対象の一括実行ID、path、解除前の確認事項は[運用保留・再開情報](../notes/operations-resumption-2026-09-10.md)を参照してください。
