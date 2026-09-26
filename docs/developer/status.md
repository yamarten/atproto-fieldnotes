# Project Registry Status

通常導線では、CLI/Webでの登録・検索・手動レビュー、外部情報源からの増分取り込み、enrichによる調査・評価、紹介候補の提示と追跡を利用できます。コードはローカルのschema v4 DBを対象とし、Webの処理実行用process（worker）と定期実行の設定（preset）で行います。

現行機能の適用にも境界があります。新しい判定規則による既存project全件の再評価、非現行identifierの専用編集UI、取り込み処理・追跡状態レビューのcheckpoint再開は揃っていません。Lexiconの独立観測や、判定結果をDB更新へ適用せず比較するshadow経路も通常の自動採用とは別に扱います。以下では機能ごとに、利用できる範囲と、その説明を支える確認・制約を示します。

競合保護は2026-09-14に正本DBをschema v4へ明示移行し、実運用Webへ反映しました。移行前に存在した全データテーブルの件数・既存列内容の一致、SQLite整合性検査、Webの設定・一覧・job・定期設定API応答を確認しています。その他の実運用確認範囲は各項目の記録に従います。実データ量での競合時性能や実外部取得・LLMを伴う長時間競合は未確認です。

## 登録・検索・手動編集

CLI/Webからseed登録、一覧・詳細検索、追跡状態の変更、identifierの採用・除外・期限付き保留（confirm/block/defer）、profile編集を利用できます。新しいコードの通常導線はschema v4 DBです。Websiteとservice identifier、resourceの役割（role）と採用状態（classification）を区別して表示します。非現行identifier・採用来歴・移転関係・再登録の抑止情報は保存可能ですが、旧構成要素（`former`）と開発・試験用（`non_production`）の専用編集UIは未接続です。identifierへの判断は`kind + value`全体へ作用し、同じ値のroleごとには分けられません。

登録プロジェクトのWeb検索は識別子・関係・判断と保存済みの`tagline`、`summary`、`features`を大小文字を無視した部分一致で探し、構造化filterと併用できます。raw evidenceやREADME全文、自然語の概念検索には対応しません。説明検索は一時DBのAPI testと開発DBの実ブラウザで確認し、正本5,054件のread-only計測では説明だけにある語が約250ms、既存識別子が約282ms、不一致が約244msでした。実運用Webへの反映は未確認です。保存・変更操作・マージ元keyの解決のtestと、schema v3切替後の利用者による典型操作・定期sync/enrichを確認した記録があります。これらの経路の確認は、現在の全データの正しさを保証するものではありません。保存契約は[project record](specs/project-record.md)を参照してください。

## 発見と取り込み

introduced（紹介済み投稿）、collection inventory（collectionの活動一覧）、Bluesky公式Lexicon、GitHub、Semble、Tangledの取り込みと、共通の外部情報源設定をCLI/Web/workerへ接続しています。情報源ごとに既存projectへの照合、未一致、取り込み済みの境界（watermark）、候補確認（dry-run）と保存（execute）を扱います。TangledはJetstream v2を使い、上限で区切った増分実行に対応します。[取り込み仕様](specs/discovery-and-sync.md)で情報源ごとの条件を定めます。

introducedの取得後にseedを作れなかった投稿も、投稿・URL別の理由とともに同じqueryの同期状態へ保存し、Webの「紹介投稿の要確認」から元投稿、対象外判断、登録projectへの紹介URL設定、取消・再照合を扱います。既存の自動照合とcandidate判定は維持し、取得未完走と要確認0件を区別します。正本DBのread-only確認では旧未一致保留が21 seedあり、既存検索設定で最新10投稿を限定取得した際はseedを作れない投稿が6件ありました。これは全期間の漏れ件数ではありません。temp DBの再同期・取消・複数一致とAPI test、開発DBの実ブラウザで主要導線を確認しました。母艦統合後の全unit testを経て、実行中job・workerのない状態で実運用Webへ反映しました。HTTPS経由で新build・同じDB・配信asset一致と、要確認APIが旧保留21件を返すことを確認しています。実運用のブラウザ表示はブラウザ側の接続ブロックにより未確認です。正本DBへの新しい保存処理・判断操作・過去投稿の再取得は実行していません。

外部情報源への取得件数・request数を限定した疎通・動作確認（bounded smoke）と、Tangledの実適用・照合補正を確認しています。Tangledの全snapshotの初回完走時間・API利用量、長時間運用の再試行・cache・取得上限の妥当性は未検証です。

Tangledの通常新規発見は、recordの`source`が宣言するfork元を使い、GitHubの通常検索`fork:false`に合わせてforkを除外します。既存URLへの一致・更新と明示登録は維持します。公式のfork保存契約、保存実例4件、独立forkの明示登録を含むtemp DB testで確認しています。欠落・不正・未知形式の`source`からcloneを検出する機能、親との差分から独立性を自動判定する機能はありません。masterへ統合済みで、次回起動するCLI・sync workerはこの実装を読み込みます。Webの再起動は不要です。統合後の本番syncでの除外実績と正本の既存候補の処遇は未確認・未実施です。[確認範囲と既存候補](../notes/tangled-fork-discovery-2026-09-20.md)を参照してください。

Tangled同期のowner確認はBluesky profileからDID・handleの双方向検証へ置換し、解決不能recordを保留して増分実行で再評価します。temp DBで保留とwatermarkの同時保存・復旧・rollbackを確認し、正本の同じwatermarkから1,000 eventのread-only smokeで、停止ownerの保留とprofile未登録ownerを含む正常候補の評価を確認しました。この修正の本番execute・Web反映・定期preset再開は未実施です。[統合・再開材料](../notes/tangled-owner-sync-2026-09-20.md)を参照してください。

## 調査・観測・Replay

通常enrichは収集処理（collector）の観測を統合し、規則による評価とLLM補助、現在状態と調査履歴の保存を行います。Webから失敗・保存上限内の根拠・処理段階ごとの時間・LLM requestの情報を確認し、保存した調査実行記録（investigation run）を読み取り専用で再評価（replay）できます。collectorの部分失敗と再試行分類、一時DBのenrich→save→fetch、保存した調査実行記録のreplayを確認した記録があります。HTML/READMEの履歴をすべて保存・表示する機能はありません。古い調査実行記録で計測値が欠けている場合は未記録として扱います。

将来改善の比較用に、通常enrichの検索計画の実入力・整形前後の出力、検索候補の既知／新出と最終採用、purpose別token、収集操作時間、triage変更fieldを圧縮履歴へ保存する経路を実装しています。欠落・秘匿除去・上限超過を区別し、read-onlyの開発用reportで集計できます。一時DBの通常保存・読戻し、replay/export非干渉、cleanup後の保持を確認しました。正本の旧履歴200件のread-only確認と保存量proxyの計測を経て利用者が受理し、主checkoutへ統合後の全unit testも成功しました。次回起動するCLI・enrich workerから記録し、実週次の蓄積・負荷は未確認です。Webの新しい履歴readerはserver reload後に反映されますが、今回再起動はしていません。検索計画再利用は有効化していません。[初回評価・統合境界](../notes/enrich-observation-review.md)、[集計手順](workflows/enrich-observation-review.md)を参照してください。

endpoint方式の通常enrichには、入力・実行条件・手動判断・収集失敗が同じ場合のtriage再利用を接続しています。検索計画と収集・規則評価は毎回続け、元の生成から30日未満に限って要約・分類の再推論を省きます。一時DBの通常経路、変更・失敗・復旧・期限の条件と、保存済み実例40件の同じ出力の適用差分0を確認しました。直近履歴からの約17%短縮は設定不変を仮定した見積りで、旧prompt変更を含む当時の削減実績ではありません。利用者の受理後に主checkoutへ統合し、全unit testを確認済みです。次回起動するCLI・enrich workerから利用でき、初回は照合値を作るため通常生成します。統合確認のための本番enrich・Web再起動は実施せず、実週次でのhit率と短縮実績は未確認です。[再利用仕様](specs/investigation.md#triageの再利用)、[運用効果の確認材料](../notes/enrich-triage-reuse.md)を参照してください。

serviceは静的HTTP取得（static probe）・追加ページ調査（deep probe）と匿名browser確認を利用できます。Webから保存済みservice URLを選んで確認する導線も接続済みです。匿名browser確認は、JavaScript描画後の画面やloginへの到達を確認する時に使え、稼働状態の判断を補う効果を確認しています。認証入力・登録・投稿は行わず、loginへの到達は認証後の機能の保証ではありません。検証用HTMLの実Chromium、保存・replay、限定した公開URLで確認していますが、実Playwrightによるlocalhostでの疎通・動作確認は未検証です。[観測仕様](specs/investigation.md#staticdeepbrowserの調査範囲)を参照してください。

## Repository解析と公開先の採用

confirmed repositoryのrevision固定解析、根拠位置と事前生成した候補集合（closed candidate）による出力検査、最新成功結果と失敗した試行の分離を通常enrichへ接続しています。GitHub/CodebergとTangledでは取得可能な材料が異なり、Tangledの完全なtree取得は未対応です。候補repositoryは相互参照による帰属の信頼確認に必要な限定取得に留め、confirm前に通常profileの根拠へ使いません。

README公開先の候補化と、帰属・逆参照・Web/protocol実在確認を通したservice採用を接続しています。`repository-analysis-v16` / `repository-homepage-role-v4`は実運用反映済みです。既存project全件の再解析・再採用は実施していません。代表project siteの自動確定、汎用API・AppView独自APIの確認は未接続です。

固定した解析素材・保存実例・限定した公開URLによる候補化、確認、一時DB保存・replayの記録があります。意味の確認はこれらの小規模な事例を対象としています。拡張子付きfile名のNSID候補化、曖昧なdescriptionの過解釈、package配布repositoryの役割、fixture Lexiconの解釈には残る課題があります。[解析仕様](specs/investigation.md#repository-analysis)、[採用仕様](specs/project-boundaries.md#repository-homepageと公開先の評価)を参照してください。

## 帰属・信頼・Projectの統合と分離

現在のpage↔repositoryの関係（relation）から信頼グラフの接続（edge）を導出し、候補URLの確認、信頼評価、Explain（判定理由の表示）、shadow比較へ接続しています。serviceの採用には、公開先が担う役割を検証するrole policyを通します。旧保存edgeのarchive・除去は実運用反映済みで、保存済みの過去採用を新規評価だけで一律に取り消しません。適用時に判定用graph・現行relation・登録URL・採用判断・調査履歴が変わらないことと、DB整合性・Web API応答を確認しています。

通常のマージレビュー、分割（split）・別project指定（separate）、マージ元keyを存続先へ解決するaliasによる履歴参照を利用できます。merge/split/separateの原子的保存と、merge前に始まった古いenrichによるalias元keyの再作成拒否を確認しています。merge時のsourceのconfirm判断・identifier metadata・採用来歴の保持、連続merge／再保存での採用originの維持、明示判断・非現行状態の衝突拒否を架空DBの通常入口とAPIで確認し、実運用Webへ反映済みです。Substratumの1claimは別承認の限定復元で元2採用来歴を保持して復旧し、正本の全table差分とWeb GETを確認しました。過去の消失操作と、通常保存で`lexicon_count`を0へ投影する周辺問題は未解明・未修正です。[復旧確認と残る問題](../notes/identifier-merge-recovery.md)を参照してください。

Lexiconの既存帰属候補は、同じprojectの独立した採用済みLexiconと直属authority・検証済み発行DIDが一致する場合に、通常の取得結果適用から信頼評価・採用へ接続しています。publisherのaccount採用、別namespaceへの信頼伝播、同じrepositoryディレクトリだけによる採用は行いません。範囲制約・取消し・取得順序の回帰testと一時DBでの保存／replayを確認しています。2026-09-14のSifaの公開4定義を通常のDNS・DID・PDS経路で検証し、既存3候補が採用済みLexiconを起点として支持される差分を確認しました。効果の確認はこの小規模sampleに限り、所要時間の削減や全体の誤採用率は未測定です。旧観測の暗黙昇格、正本DBへの一括適用、実運用serverへの反映はしていません。[適用条件](specs/project-boundaries.md#承認済みlexiconと同じ発行元による信頼の補強)を参照してください。

roleごとの信頼規則を広げる比較、マージ規則の比較、一つの関係の意味を判定する専用relation assessorのshadow比較は、通常の自動採用・自動mergeとは別の経路です。一時的なcollection断片のマージレビュー導線は撤去済みで、そこで確認したのはproject境界までです。identifierごとの現行・旧構成要素・開発試験用・無関係な誤検出の選択は確認範囲に含めません。判断材料が残る例は[identifier処遇](../notes/temporal-project-relations.md#一時merge-review後に残ったidentifier処遇)と[repository帰属](../notes/repository-attribution-open-questions.md)へ分けています。

## 説明・紹介候補・追跡

LLMと根拠の変化に応じたprofile更新、atproto寄与度、service利用可否、技術的な学習価値（technical interest）を利用して候補を提示します。説明は用途・技術的特徴と役立つ過去の情報を一つの文面に保ち、取得失敗・確認時点を分離する方針です。根拠と変更理由のある更新、人の編集・来歴不明の既存説明の保護、Webでの更新案採用／訂正／見送りを接続しています。通常triageの構築・適用と一時DBの読戻しで保存保護を確認していますが、終了・機能変更では旧機能の現在形、変更前のtagline、根拠にない不在の断定が残ります。2026-09-23の固定素材比較でも現行方式の誤りが再現し、共通prompt整理案には旧技術の説明欠落・非使用の断定、説明専用案には時制等の改善と旧PDS設計の欠落があり、通常実装へ採用せず素材を保持しています。後続の対話では、紹介したい対象の取りこぼしとノイズによる埋没を最優先とし、紹介対象になりそうな度合いとPromotionの表示順も評価する暫定方針を了承しました。この基準では説明の欠落だけで採否を決めず、現行運用を暫定維持し、説明専用案への置換は追加根拠が必要と再評価しています。現行・候補とも取りこぼし・ノイズ量と人の訂正負担は未測定です。通常prompt・推薦規則・正本DBは変更しておらず、根拠IDの適合だけで文面を無人採用できる品質とも評価していません。[品質比較と再開条件](../notes/description-refresh-quality.md)を参照してください。

2026-09-14の正本DBへの読み取り専用監査では、4,811件すべてが編集来歴未記録の保護対象でした。LLM実行記録の存在や文面の一致から作者・編集日時を推定して自動反映を許可しません。6 tracking stateから各2件の説明を読み、用途・技術的特徴と、時制・特徴一覧の不整合を確認しました。評価結果はhuman-reviewで了承されましたが、全件の説明内容の正確さ、個別訂正、無人適用の品質受理、正本への適用は確認していません。[品質レビューと再開条件](../notes/temporal-description-validation.md)を参照してください。

service確認は最新の試行と最後の利用面確認を分け、古いbrowser成功で新しい未確認結果を`live`に戻さず、単発HTTPエラーを終了・非公開の確定へ使いません。`monitor-services`と初期無効の`Service observation` presetは、confirmed serviceの低頻度確認と観測差のreview表示へ接続しています。固定入力のtest・開発DBの実preset/worker確認に加え、2026-09-15に6件の実公開URLを通常Playwright経路で観測し、一時DBへ保存しました。約29秒、249 request、取得失敗0 URLでしたが、利用面の確認は0 URLで、日本語loginボタンやhandle例だけを持つ入力欄の検出漏れがありました。取得成功は利用可否の保証ではありません。観測だけで説明・tracking state・最終enrich日時が変わらないことを確認しています。

正本の期限到来対象は674 URLで、すべてbrowser試行日時が未記録でした。週20 URLでは一巡に34回必要であり、30日以内に全対象を確認する運用には不足します。少数URLの負荷測定から全体の失敗率や周期実行の安定性は保証できません。頻度・上限・監視対象と検出範囲は[レビュー待ち](../notes/temporal-description-validation.md#service観測と頻度の判断)で、実運用の有効化・設定変更・正本への観測保存は行っていません。

紹介候補の「確認済み」とDeferは、理由任意・既定30日の保留へ統合しています。期限・根拠変化・解除で通常一覧へ戻り、確認対象・技術候補・保留・Lowered / 特集候補の一覧、理由検索と件数、ページ送りを利用できます。旧確認済みは期限と残存記録を保持して読込み時に保留へ変換し、次の通常保存で保存形を更新します。読込みだけではDBを更新しません。根拠情報のない旧Deferは期限の条件を維持し、Lower priorityと表示だけの先送りは独立です。一時DBで保存・旧記録変換・再表示・解除・merge・検索を検証し、開発DBのChromiumで理由なしの保留・解除・検索とPC／モバイル幅を確認しています。実運用ではjob・workerなしを確認してWeb停止中に統合し、新版起動後のbuild・同じDB・静的ファイル一致を照合しました。HTTPSの実Chromiumで保留一覧への一本化、30日間保留・理由任意の確認ダイアログを確認し、保存せずキャンセルしています。正本の一括移行や判断保存はしていません。全viewの重複復元・正規化を除去し、保留一覧も判断履歴で先行絞込みする変更はWorktreeで確認済みです。同じ実データ複製のHTTP中央値は通常5.66→3.30秒、技術候補5.64→3.28秒、保留9.22→0.18秒、すべて9.13→5.11秒でした。全project復元値・候補全件の旧実装との一致、更新とsnapshotの整合性、複製DBでの実画面操作を確認しています。この速度改善は母艦統合後の全unit test・JS構文確認を経て、本番Webへ反映済みです。実行中job・workerがない状態で再起動し、新build・同じDB・各一覧GETとHTTPS実Chromiumの技術候補・詳細・保留ダイアログを確認しました。判断は保存していません。残る完全復元時間、同時収集下と長期運用の評価は残ります。[計測条件と統合確認](../notes/promotion-queue-performance.md)を参照してください。[運用評価](../notes/promotion-rediscovery-review.md)を参照してください。活動・調査鮮度に基づく追跡状態の見直しも接続済みです。

技術候補は通常CLI/Web/applicationへ接続し、サービス確認度と独立して技術score順に取り出せます。利用範囲は理由付きの手動判断だけを紹介順位へ反映します。実資料で権限やrecordを主機能の利用と取り違え、短い文面の抽出でも保存や書込みを誤って生成したため、LLMによる利用範囲の総括を通常prompt・適用・保存経路から外しました。意味抽出用の追加LLM呼出しも通常enrichには入れていません。実運用Webへ反映し、HTTPSの実ブラウザで技術候補への切替・詳細の判断欄を確認しました。保存・解除は開発DBでの確認に限り、正本への判断保存や一括更新はしていません。[選定評価と反映境界](../notes/promotion-selection-validation.md)を参照してください。

OAuth scopeは公式SDKで解析し、通常の収集・保存・replay・Web詳細へ接続しました。対象collection、書込み操作、RPCの宛先、基本／移行用scope、未解釈tokenを区別します。`include`は未解決の権限セットとして保持し、まだ展開しません。宣言を実際の利用・所有・主機能への依存に昇格させず、この解析結果を利用範囲の自動評価や推薦順位へ直接反映しません。実資料の固定metadata、temp DBの保存・scope削減・merge・replay、開発DBの表示で確認しています。metadata発見率、権限セットの解決、説明文との対応付けは未検証・未実装の範囲です。依存SDKを含め実運用checkoutへ反映済みですが、今回新解析を正本へ保存するenrichは実行していません。[観測仕様](specs/investigation.md#oauth-client-metadataの観測)を参照してください。

人が期待判断を定めた固定事例（manual gold）を通常の紹介候補一覧生成へ通す評価と、紹介済みprojectを使う読み取り専用の分析があります。紹介実績分析は現在値に対する評価で、当時の推薦を復元せず、未紹介projectも負例にしません。2026-09-23の保存データ調査では、共有Lexicon等の後順位、技術評価の境界の揺れ、候補条件による非表示を確認しました。未紹介stableを通常候補に含め、stalledは外す方針をCLIとWeb APIの既定条件へ接続しました。保存データのread-only比較では候補448→459件、先頭50件の入替え4件を確認しました。temp DBのCLI/API testと全unit test、開発DBの実ブラウザでstableの表示を確認しています。本番Webへの反映と実運用での閲覧は未確認です。共有仕様への関心と技術的価値・稼働確認を分けた順位方式は未採択で、単純な再評価や合計score順だけでは解消しません。旧入力構築では5,054件中20件で容量超過がありました。現行コードはJSONの整形空白を除き、超過時にはrepository解析のclaimと根拠を一緒に縮約して省略を記録します。promptのrepository URL出力fieldも通常適用・評価と一致させました。正本DBの現在値5,054件をread-onlyで通常のtriage入力構築へ通した結果、容量超過は0件、repo解析の縮約は3件でした。Barazoでは選択された6 repo中2件の解析全体を省略し、そのURLを記録しています。保持したrepoのsummary原文抜粋とclaimの根拠位置は既知ケースで確認しました。合成入力・通常適用はtest済みですが、実LLM生成・実運用serverへの反映は未確認です。限定的なモデル・推論・主張検証の比較では改善とともに見逃し・根拠外の補完・追加時間が残り、自動再検証・修正・自己エスカレーションを通常フローへ追加する方向は利用者の方針により当面保留です。モデル・資料・作業方式が実質的に変わる場合に再検討します。全体の捕捉率・ノイズ率は未測定です。[評価方法](specs/investigation.md#replay-と-benchmark)、[選定品質の比較と再開材料](../notes/promotion-selection-quality.md)を参照してください。

主機能との対応を検討する材料として、個別repo宣言・record型Lexicon・record観測を同じcollection名で並べ、本文・ページ説明・READMEの関連記述と併せて表示します。service HTMLを切り詰める前に既知のcollection名も検索し、後から判明した名前は保存済みHTML内で補います。保存・replay、原文位置、Web表示を確認しています。固定資料ではGleanの購読・注釈、mathrの個人バックアップ用スコアとBluesky投稿を分けて確認できますが、Squireのactorサンプルからタスクの保存先は分かりません。11対象の固定資料では本文の追加発見はなく、collection名だけの記述を拾う経路は構成例で確認しています。追加通信・LLMはなく、実運用Webでもcollection別材料の表示を確認しました。正本への観測追加は行っていません。機能原文とschema説明の限定的な意味対応は既存ローカルLLMで評価しましたが、購読と注釈、表示設定と文書本体を同一視する誤りが残り、通常調査への追加は不採用と判断しました。人による再照合を省ける品質と、実在対象の未使用確認を含む評価は満たしていません。自動評価による負担軽減と寄与度の責務整理は残ります。[材料の仕様と限界](specs/investigation.md#atproto利用の関連記述)、[意味対応の品質レビュー](../notes/feature-collection-semantics-evaluation.md)を参照してください。

## LLM構成の評価

個別のtriage/repository analysis事例を非公開のGit管理外ディレクトリへ固定保存し、登録・判断追記・一覧・再読込・整合検査するCLIを利用できます。入力・出力・実行条件の当時記録と再構成／欠落を区別し、後から付く部分判断、人による確認、調整利用は同じ事例の追記履歴へ保持します。一覧は追記後の有効状態を表示します。保存した事例は評価corpusや学習へ自動投入されません。個人用の保存・参照はCodexの内容確認で進められ、人手確認を前提にしません。認証情報の混入防止と、公開・外部送信時の確認は維持し、人が確認した判断とAIの推定を区別します。合成事例で登録、伏字、判断追記と訂正、上書き拒否、再読込、digest検査を確認しました。共通storeには架空素材5件に加え、実在repositoryの保存済み解析結果2,016件と評価reportの成功出力を含む41組を未評価の事例として保全し、元資料との全件照合とCLI整合検査を行いました。保存済み結果は正解ではなく、当時の完全promptは残っていません。自動伏字の影響を受けた事例と、調整利用歴を確認できない事例を含むため、保全件数を独立した学習用事例数や未使用holdout数とみなしません。対応表と伏字箇所は共通storeの`audits/repository-analysis-20260830-inventory.json`に置いています。人によるsnapshot・判断確認と長期バックアップの復旧確認は未実施です。[保存契約](specs/llm-case-store.md)を参照してください。

記録上の既定modelはQwen 3.6 35B A3Bです。通常enrichと定期jobでmodelに応じた追加規則を使用します。固定evidenceでprofile有無を分け、project全体の説明・分類を補うtriageの評価と、repository解析からtriageへの二段階評価を利用できます。比較reportは機械検査・反復差・文章確認等を区別し、通常modelを自動変更しません。

Gemmaは比較時のrepository解析でJSON失敗が再現し、通常処理への置換試用を停止しています。Qwenを維持していますが、解析内容の品質は引き続き評価対象です。後続のclosed candidate契約での確認と旧契約の結果も区別します。Splash版Qwen 3.6 35B A3Bは固定入力でLLM requestが約16%短くなった一方、triageの保存前検査と分類の一貫性が悪化したため通常modelへの置換を見送っています。複製DBの通常enrich、紹介候補一覧、実使用memoryへの影響は未確認です。Agents A1の低優先度処理への試用も、比較時の速度・Qwenとの自動切替時の共存条件から停止しています。低優先度modelの通常自動切替と、同じ処理を実行した時のmemory・swap・入力処理（prefill）・出力生成（decode）の自動計測は未接続です。[評価手順](workflows/llm-model-evaluation.md)、[候補固有の保留・再開材料](../notes/llm-model-evaluation.md)を参照してください。

## JobとWeb運用

Web UIは日本語を基本とし、JSON等の馴染みのある技術ラベルは原表記も使います。API・CLI・DBの名前と保存値は維持し、識別子・引用・JSON・診断原文は改変しません。日本語化は実運用HTTPSで静的ファイルの一致まで確認済みです。静的ファイルはリクエストごとに読み込まれるためサーバー再起動は不要で、開いているページの再読込で反映されます。正本での更新操作は再検証していません。

候補カードの通常状態の強調と説明の常設を減らし、スコア内訳を詳細へ移しています。フォームと詳細欄の余白を整理し、CLIコマンドは常時表示します。開発DBで判断の保存・解除、説明と原文の表示、デスクトップ・モバイル幅、キーボード・タッチ操作を確認しています。母艦統合後に実運用HTTPSの静的ファイルとの一致を確認し、ページの再読込で利用できます。「今回は先送り」の高さも隣の判断ボタンと揃えています。表示方針と操作名は[Web UIガイド](../user/web-ui.md)を参照してください。

enrich、sync、追跡状態を見直すreview、匿名browser確認を別processで実行し、進捗表示・処理境界での停止（協調停止）・presetの手動／定期実行に対応します。一括enrichは進行状況を保存したcheckpointから再開できます。Webの手動reloadとmacOS LaunchAgentによる自動起動も利用できます。実worker・一時DBの完了／中断／再開、Webからのjob実行、正本での一括enrich停止・再開の記録があります。

実運用Webは[Tailscale Serve経由のHTTPS入口](../user/web-ui.md#tailscale経由のhttps)も利用できます。専用8445ポートから既存8888へ転送し、API token認証と直接HTTPの`0.0.0.0:8888`を維持しています。2026-09-22にこのMacから証明書検証付きHTTPS、静的asset、無認証・不正tokenの拒否、認証付き設定・一覧・job API、Chromiumでの一覧・詳細・Settingsと再読込を確認しました。HTTPと同じserver instance・DBへの接続、既存Serveの8443・8444設定不変、Funnel未使用も確認しています。アプリ再起動・DB更新・job開始は行っていません。別端末からの到達とアクセス許可、Mac再起動後の復帰、証明書自動更新は未検証です。

presetの一般障害・明示中断・worker起動失敗は、失敗したstepと未完了stepを手動再開待ちとして保存します。`Resume pending steps`は成功済みstepを繰り返さず、enrichでは利用可能なcheckpointを使います。保留の永続化、定期時刻での自動再試行抑止、再開後の依存レビューは一時DBと模擬workerのtestで確認しています。この変更の実運用Webへの反映と、実外部取得を伴う障害からの再開は未確認です。

syncと追跡状態レビューのcheckpoint再開、server再起動後のjob一覧・進捗eventの復元は未接続です。Web内の変更操作用lockはCLI等の別processを排他しません。同時書込みは短いSQLite transaction内でproject更新番号を検査し、古いsnapshotを拒否します。enrichは未反映成果とcheckpointを残して独立した後続を処理します。presetは依存stepを保留し、独立stepを続行できます。競合保護は一時DBの別process・別接続で、変更保持、保存境界の排他、rollback、writer稼働中の読込みを確認しています。開発DBの実Web/workerでは外部取得部分を固定入力へ置き換え、競合後の後続処理、保留再開、依存レビュー、未反映履歴と古い編集フォームの拒否を確認しました。実データ量での応答時間・外部取得/LLMを伴う長時間競合は未検証です。調査処理の並行実行は観測されていますが、重複実行が過去の更新消失や中断の原因だったかは未確認です。調査実行記録の時間計測はproject一件のSQL書込みまでで、commit待ち・job全体・人によるレビュー時間を含みません。[現在有効な保留](#有効な運用上の保留注意)を参照してください。

## 保存とMaintenance

schema v4の保存・正規化fieldの検索、履歴圧縮、保持規則による手動削除、派生値の修復を通常導線へ接続しています。通常接続はschema v4を必須とし、旧DBを暗黙変換しません。v3からv4への明示移行commandを用意しています。更新番号はproject本体・子行・aliasの直接更新でも進み、同値に戻した変更と削除も検出します。汎用的なmigration管理や、適用済みのデータ処理規則をDB全体に記録するmarkerはありません。履歴圧縮の複製DB同値確認・正本適用、merge/splitのtransaction確認の記録があります。これらは過去の適用確認で、現在のDB容量・全件整合性を再監査した結果ではありません。[Maintenance手順](workflows/change-acceptance.md#maintenance適用の確認)を参照してください。

CLI/Webの手動確認には`dev-db`で生成・リセットできる共通ダミーDBを利用できます。架空データからの生成、上書き拒否・lock競合・失敗時rollbackのtemp DB test、通常CLIのDB選択・編集・replayと実ブラウザでの一覧・紹介候補・識別子採用・調査履歴を確認しています。外部取得・実LLMや、実データ固有の監査・件数・容量確認を代替しません。v3からv4への履歴保持と移行失敗時rollbackは一時DBで確認しています。正本のv4移行でも既存データの一致と整合性を確認し、移行前backupは[運用保留・再開情報](../notes/operations-resumption-2026-09-10.md#backupとarchiveの保全)に記載しています。[確認手順と安全境界](workflows/change-acceptance.md#cliwebの共通ダミーdb)を参照してください。

## App record export

保存済みprojectのread-only validationと、明示manifestからのentry JSON生成を利用できます。不適格recordを含む出力を全件拒否する契約で、PDS送信とrkey生成は担当しません。生成・検査経路があることと、実際にdirectoryを公開・運用していることは区別します。[Export仕様](specs/app-record-export.md)を参照してください。

## Lexiconの独立観測

developer scriptと`Operations`の週次presetによる、registryとは別の専用DB（sidecar）への観測を接続済みです。registryへ自動反映しません。取得上限を設けた実取得・公開者（publisher）の確認・正本registry不変を確認した記録があります。

2026-09-10の保存済みstate確認では、sidecar schema v3、比較開始点のrevision 7、比較条件を満たした週（valid period）数0、`review_due=false`で、日曜05:00 Asia/Tokyoのpresetは有効でした。直前sync失敗により比較不能だった観測実行は有効periodに数えていません。最初の比較可能な観測実行前後でのregistry不変と、現在のWeb表示は未再検証です。初回のperiod加算・registry不変の確認は[作業メモ](../notes/operations-resumption-2026-09-10.md#lexicon週次観測の初回確認)に残します。[Sidecar仕様](specs/lexicon-publication-catalog.md)を参照してください。

## 有効な運用上の保留・注意

PDSlssの追加enrichは保留され、キャンセル済みの一括enrichの再開は指示されていません。移行時の永続archiveは一時ファイルとして削除しません。移行前backupの保持世代は未決です。対象の一括実行ID、path、解除前の確認事項は[運用保留・再開情報](../notes/operations-resumption-2026-09-10.md)を参照してください。
