# Model評価の保留・再開材料

通常enrichの置換候補と、低優先度処理用の常駐候補について、試用を止めた理由と再開に必要な確認を記します。

GemmaとAgents A1の保留は2026-07月の比較に基づきます。試用再開には、現行のmodel・runtime・repository解析契約で改めて固定入力の反復評価が必要です。

## Qwen 3.6 35B A3B Splashによる通常enrichの置換

2026-09-25の固定入力比較では、`incoai/Qwen3.6-35B-A3B-Splash`の通常modelへの置換は見送ります。LLM request時間は短くなりましたが、triageの保存前検査と分類の一貫性が悪化し、紹介候補の判断・説明を良くする根拠は得られていません。正本DB、通常model設定、promptは変更していません。

比較したのは現行`qwen/qwen3.6-35b-a3b`（`lmstudio-community`のMLX 4bit、LM Studio 0.4.25+1、選択済みMLX runtime 1.11.0）とSplash（Inco AIのpacked Q4 targetとDFlash 2 draft、同じLM StudioのSplash runtime 0.0.5）です。Splashのtargetは`mlx-community`由来で、現行packageと重みの同一性は確認していません。両方ともResponses API、reasoning `none`要求、最大2,048出力token、共通production promptを使用し、triageには現行Qwen用suffixを明示しました。repository解析は現行`repository-analysis-v16`、triageは元profileなしです。現行modelのcontextは183,296・parallel 4、Splashは32,768・parallel 4を要求し、APIでcontext 32,768を確認しましたがparallel実効値は取得できませんでした。両modelとも出力のreasoning tokenは0で、runtimeが要求を適用したかは未確認です。選んだ入力は最大約9,000 tokenで、今回のSplash側context上限には達していません。

| 固定入力、各2回 | 現行MLX | Splash | 読み方 |
| --- | ---: | ---: | --- |
| repository解析、6件×2回 | 保存前検査12/12、83.96秒 | 保存前検査12/12、72.09秒 | 合計14%短縮。2巡目の合計は26.89秒と26.91秒でほぼ同じ |
| triage、7件×2回 | 保存前検査13/14、150.15秒 | 保存前検査10/14、124.83秒 | 合計17%短縮。ただしSplashはHighportの`confidence`を2回とも欠き、openclaw-blueskyを2回とも説明と矛盾する`library`へ分類 |
| repository解析結果を重ねた後段triage、共通の3件×1回 | 保存前検査3/3、92.17秒 | 保存前検査2/3、43.18秒 | Splashでopenclaw-blueskyの同じ矛盾が再発。1巡だけの時間差は速度の安定した推定には使わない |

repository解析では両構成とも根拠外ID・不正collection・保存前に除かれるclaimは0でした。観測・collectionの大部分は一致しましたが、文章の情報量はcaseと反復によって入れ替わり、Splashの一貫した品質向上は見つかっていません。triageのHighportは出力上限に達しておらず、Splash側の2回の欠落は切断によるものではありません。OzoneではSplashが入力根拠の乏しい状態でバックアップやPDS・Relayの運用機能まで説明しました。Chiveは両構成が陶器販売サイトとレシピrecordを同一対象とみなしており、schema検査を通っても意味上の正しさは保証されません。これらの保存済みcategoryは人間goldではありません。

固定入力の両処理を合わせるとrequest時間は234.11秒から196.93秒へ約16%短縮しました。ただし単一の短いcorpus、モデルごとの連続実行、cache、出力長の違いを含む構成比較であり、通常enrich全体の時間短縮率やSplash runtime単独の効果ではありません。Splashの配布packageは20.95 GBで現行20.43 GBより大きく、実使用memoryと他アプリへの影響は測定できていません。modelのロード時間も別に要し、role別切替には運用上の負担があります。

再開素材は共通非公開保存先`$HOME/Library/Application Support/atproto-survey/llm-cases/audits/splash-20260925/`に保全しています。`manifest.json`は21ファイルのhash、`enrich-metrics-500.json`は正本直近500 runのID・総時間・用途別request件数と時間を持ちます。通常enrichの約6.1%短縮という値は固定corpusの率を当てた仮試算で、実測ではありません。

Highport、openclaw-bluesky、Ozoneの両modelのrepeat 0は、共通storeの`splash-20260925-{highport,openclaw-bluesky,ozone}-{baseline,candidate}-r0`の6件に固定しています。人間goldはなく、render済みprompt本文も残っていないためevidenceとprompt hashから参照します。長期backupの復旧確認は未実施です。

通常enrichの複製DB比較、紹介候補一覧への影響、実使用memory、長い入力、reasoning実効、正本DBでの少数試用は未実施です。現時点の退行を踏まえ、複製DBと正本DBへの試用には進みません。再検討するなら、まず再現した`confidence`欠落とcategory矛盾を小さい候補固有suffixで直し、別の未使用caseで確認します。その上で現行と同じcontext・並列・連続処理・memory条件、repository解析を含む通常enrichの複製DB結果を比較します。

## Gemmaによる通常enrichの置換試用

Gemma 4 26B A4B MLX 4bit QATは、project-level triageだけの初期評価では候補に残りましたが、repository解析を含む通常enrichの複製DB試行でJSON失敗が再現したため、正本DBでの置換試用を停止しました。

停止判断に使った`goat`の固定materialではGemmaのJSON成功は10回中5回、Qwen 3.6 35B A3Bは10回中10回でした。ただし根拠不足の観測の除外が両modelで発生し、保存規則をすべて通る結果は両方0回です。QwenはJSON応答の安定性を理由に維持しています。解析内容の品質は引き続き評価対象です。

比較時はLM Studio 0.4.20+1、Chat Completions、reasoningなし、最大出力2,048 tokenでした。実行環境が返したコンテキスト長はQwen 183,296、Gemma 226,304、並列数は双方4で、要求したコンテキスト長32,768は反映されていませんでした。再開時には要求値と実効値を分けて確認します。

再評価はmodel、runtime、promptの変更後に行い、次を順に確認します。

1. 失敗した`goat`の保存済み入力と適用policyを確認し、固定入力の反復でJSON構文エラーの再発と根拠不足の観測を評価する。当時の入力を利用できない場合は、別caseとして評価し、旧caseの再現確認が残ることを記録する。
2. 調整に使っていないrepositoryと最新DB複製で、repository解析から後段triage・保存まで確認する。旧契約の結果と現行契約の結果を分ける。
3. 条件を通った場合だけ、[少数projectでの試用手順](../developer/workflows/llm-model-evaluation.md#10-正本dbの少数projectで試す)に従って対象・停止条件・baselineへの戻し方・backup保持期限を定め、正本DBの少数試用を別途承認の対象にする。

## Agents A1による低優先度処理

Agents A1 4B GGUF Q8_0は、小型modelを追加常駐させる候補でした。goodfeeds、Chive API、atproto.dartを各2回確認した範囲では品質の機械検査を通りましたが、速度上の利点はなく、自動切替でTTL中のQwenが退避しました。

当時の実効コンテキスト長は262,144、並列数は4、手動で観測したメモリ占有量は約9.7 GBでした。試用を止めた理由は、通常APIの自動切替でQwenを残せなかったことです。明示的にモデルを読み込んだ場合の共存可否は、この観測からは判断できません。

明示的なモデル読み込みに依存せずQwenと共存できる実行環境が得られた時に、任せる低優先度処理を決め、同じコンテキスト長・並列数・連続処理・共存条件で品質、処理時間、メモリ使用量を比較します。常駐コストは配布パッケージのサイズに加え、実際のメモリ使用量と共存時の挙動で判断します。
