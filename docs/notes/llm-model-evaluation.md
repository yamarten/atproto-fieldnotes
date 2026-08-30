# Qwen / Gemma model比較

> 2026-07-25から2026-07-26に行った比較の作業記録です。再利用する評価手順は
> [LLM model評価](../developer/llm-model-evaluation.md)、categoryの意味は
> [project-record.md](../developer/project-record.md#category)を正本とします。

## 結論

Gemma 4 26B A4B MLXは、現在のQwen 3.6 35B A3Bより約30%遅い一方、JSON/schema、enum、identifierの
安定性と一部categoryで優位でした。文章品質には明確な差がなく、手動観測のfootprintはGemmaが約4 GB小さい
結果でした。

Gemmaはproject-level triageでは正本DBの少数projectで試す価値がある候補でしたが、通常`enrich`へ追加された
repository analysisを含む最新DB複製の確認で、同じ`goat` materialへのJSON応答が2回連続で失敗しました。
通常`enrich`全体をGemmaへ置き換える正本試用は停止し、既定modelはQwenのまま維持します。

repository materialの誤分類と後段への過大な入力も複製DB確認で見つけ、modelに依存しない共通処理として修正しました。
失敗後に同じmaterialでrepository analysis単体を10回反復すると、成功5回、JSON構文エラー5回でした。成功した5回も
同じ根拠不十分な観測が保存前に除外され、完全に有効な結果は0回です。Gemmaは現構成の通常処理候補から外し、
model、runtime、またはpromptの変更でこの問題を解消できた場合だけ再評価します。

同じ`goat` materialをQwenでも10回反復すると、JSONは10/10成功しました。ただし全10件で根拠不十分な観測が
除外され、6/10件ではmaterialにないcollectionも除外されたため、保存規則をすべて通る結果はQwenも0/10です。
Qwen維持はrepository解析内容が十分良いという判断ではなく、Gemmaでだけ再現した全結果欠落を避ける判断です。

Agents A1 4Bは、小さいmodelを常駐させる低優先度処理の候補として確認しました。品質は少数caseで保ちましたが、
処理が遅く、自動切替ではTTL中のQwenを退避させました。明示loadに依存せずQwenと同居できるruntime構成が得られる
までは試用へ進めません。

## 比較した構成

| 役割 | model | 配布形式 | 評価用suffix |
| --- | --- | --- | --- |
| 比較基準 | Qwen 3.6 35B A3B | MLX 4bit、20.43 GB | [Qwen用追加規則](../../prompts/model-rules/qwen-3.6-35b-a3b.txt) |
| 通常処理candidate | Gemma 4 26B A4B | MLX 4bit QAT、15.64 GB | 比較時のGemma用追加規則 |
| 低優先度処理candidate | Agents A1 4B | GGUF Q8_0、4.48 GB | なし |

runtimeはLM Studio 0.4.20+1、Chat Completions、最大出力2,048 token、reasoningなしです。QwenとGemmaは
API応答の`reasoning_tokens=0`を確認しました。LM Studioが返したload configはQwen context 183,296、Gemma
226,304、いずれもparallel 4で、要求した32,768は反映されませんでした。今回のpromptは実際のcontextより十分
小さいものの、contextまで同一の比較ではありません。

最初の固定corpusと複製DB実行は、project-level triageを主な対象とし、repository analysis追加前に行いました。
2026-07-26にmasterを取り込み、confirmed repositoryのbounded materialを別のLLM requestで解析してから、その
保存結果をtriageへ渡すようになりました。既存のschema・identifier・category・速度の比較値はtriageの観測として
残し、repository analysisの出力品質と追加時間、および変更後promptへの影響は後述の固定5件で追加確認しました。

## 評価前に直したこと

2026-07-25にcategory契約を見直し、次をproduction prompt、保存処理、評価検査、正本文書へ反映しました。

- `auth_only`をcategoryから外し、独立した`atproto_auth_only`へ分離
- `project`を一般利用者が直接使うものとして整理
- `library`と`lexicon`を開発者が組み込むものとして整理
- `infra_tool`をonline infrastructureに限定せず、projectの背後で動くものとATProto自体を扱う実行物へ拡張
- `personal_tool`を用途ではなく想定利用者が作者本人または固定集団に限られるものとして整理
- URL混入をcategoryではなくidentifierの欠陥として分離

正本DBの旧`category=auth_only` 35件は個別に確認して移行済みです。

corpus exportでは現在のQwen製profileをbase evidenceから分離しました。また、前回LLM出力だけを根拠とする
`lexicon_scope`が次のpromptへ戻る経路を除去しました。新corpusは3,635件で、旧56件を調整用、新たな21件を
prompt固定後の確認用としてlabelしました。全492 unit testを通しています。

## 候補の少数確認

共通production promptと調整用8件で候補を絞りました。

- Granite 4 H Tiny: 全検査1/8
- gpt-oss 20B MLX: category 2/8、根拠外identifier 1件
- Qwen 3.5 9B: 全検査5/8
- Agents A1 4B: category 5/8、全検査7/8、216.4秒

これらは通常`enrich`の次段階へ進めず、QwenとGemmaを比較対象に残しました。gpt-ossは現在のMLX MXFP4で
`low / medium / high`対応をruntime APIから確認していますが、品質上の理由で進めていません。

Qwen用とGemma用のcategory補助suffixは、各modelで再現した誤りだけを対象にしました。強い指示を
追加しても、schema sampleだけで文書化された共有Lexiconまたは実装済みinfraへ昇格させる傾向が残ったため、
個別caseに合わせた追加調整は止めました。

## prompt固定後の21件

model出力を見る前に期待categoryを確認した21件を、元profileなし／ありで各1回実行しました。

| 構成 | 元profile | request成功 | 全検査通過 | category一致 | 根拠外identifier | 累積時間 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Qwen | なし | 21/21 | 17/21 | 17/21 | 2件 | 250.4秒 |
| Gemma | なし | 21/21 | 21/21 | 17/21 | 0件 | 330.8秒 |
| Qwen | あり | 21/21 | 19/21 | 16/21 | 1件 | 255.5秒 |
| Gemma | あり | 21/21 | 19/21 | 17/21 | 0件 | 338.0秒 |

元profileなしのcategory一致は同数でした。QwenはgoodfeedsとChartIDでevidenceにないNSIDを生成しました。
category不一致は、Markdown同期CLI、根拠の薄いhealth project、schema sample中心のcorvusなどです。

元profileを渡しても両modelのcategoryは安定しませんでした。profileなし／ありでcategoryが変わったcaseはQwen
1件、Gemma 4件です。元profileありは移行適性を確認する条件であり、分類を安定させる方法とは扱いません。

速度は累積時間だけで判断しません。元profileなし21件の中央値はQwen 12.6秒、Gemma 15.2秒で、21件すべて
Qwenの方が短い結果でした。最初の1件を除いてもQwen 237.1秒、Gemma 301.7秒です。

## 反復と文章比較

21件のうち、単発でidentifierまたはcategory差が出た7件と正常なlibrary 1件を各2回実行しました。

| 構成 | 全検査通過 | categoryが変わったcase | identifier集合が変わったcase | 検査成否が変わったcase |
| --- | ---: | ---: | ---: | ---: |
| Qwen | 12/16 | 0/8 | 2/8 | 3/8 |
| Gemma | 16/16 | 0/8 | 0/8 | 0/8 |

QwenはgoodfeedsとTitleGraphでidentifier集合が変わりました。Gemmaも文章等の言い換えはありましたが、安全検査に
関係する差はありませんでした。

同じ8件のtagline、summary、featuresをmodel名を伏せて人が比較した結果は、Gemma 3件、Qwen 2件、同等3件です。
文章品質に明確な勝者はありませんでした。featuresは、Gemmaが助詞を含む短い説明文、Qwenが名詞を連結した
短いラベルへ寄る傾向がありました。GemmaはgoodfeedsとOzoneで項目を少なく、leaflet-mdでは1項目多く生成しており、
文章の長短は一方向ではありません。この違い自体は優劣へ使っていません。

## 重い入力5件

sifa-sdk、Chive API、game、cocore、Scott Polhemus Personal Blog Mediaを元profileなしで各2回実行しました。

| 構成 | request成功 | 全検査通過 | 根拠外identifier | 1巡目 | 2巡目 | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen | 10/10 | 7/10 | 0件 | 113.5秒 | 50.3秒 | 163.8秒 |
| Gemma | 10/10 | 9/10 | 0件 | 156.6秒 | 65.5秒 | 222.1秒 |

Qwenは`confidence`を3回欠き、Gemmaは`technical_interest_level=low`というenum外値を1回返しました。
旧測定でQwenの最初のsifa-sdkが131.5秒かかった値は今回は28.4秒で再現しませんでした。同じpromptの2回目は
両modelとも大きく短縮しており、runtime cacheの影響があります。2巡目だけでもGemmaはQwenの1.30倍、全体では
1.36倍でした。

## memory

LM Studio workerをmacOSの`footprint`で手動観測しました。

| model | 処理後footprint | process lifetime peak |
| --- | ---: | ---: |
| Qwen | 約20 GB | 約22 GB |
| Gemma | 約16 GB | 約19 GB |
| Agents A1 | 9,744 MB | 9,821 MB |

同一workloadをrunnerから自動測定した値ではないため、精密なpeak比較には使いません。Gemmaの通常構成がQwenより
小さいという観測には使います。

## 複製DBの通常`enrich`

正本DBの同じsnapshotを二つ作り、8 projectをQwenとGemmaで別々に通常`enrich`へ通しました。正本DBは更新して
いません。外部collectorの取得失敗と時間差はmodel差から分けました。

| project | Qwen保存category | Gemma保存category | 確認結果 |
| --- | --- | --- | --- |
| goodfeeds | `lexicon` | `project` | Gemmaだけ期待値に一致 |
| ChartID | `lexicon` | `lexicon` | 一致 |
| TitleGraph | `lexicon` | `lexicon` | 一致 |
| leaflet-md | `library` | `infra_tool` | 合意済みの境界ではGemmaが妥当 |
| corvus | `project` | `project` | 生出力は両方`lexicon`、稼働projectの保存規則で補正 |
| Ozone | `infra_tool` | `infra_tool` | 一致 |
| atproto.dart | `library` | `library` | 一致 |
| Chive API | `project` | `project` | 一致 |

保存後のservice URL、source code URL、lexicon集合、tagline、summary、features、service status、promotion readyは
8/8件で一致しました。profile本文は両modelともsnapshotのQwen製profileから変わりませんでした。既存文章を
守れたことは示しますが、Gemmaが文章を改善できることまでは示しません。

LLM request累積時間はQwen 198.8秒、Gemma 236.2秒です。最初のmodel切替を含むgoodfeedsを除くとQwen
161.7秒、Gemma 208.7秒で、Gemmaが1.29倍でした。全処理時間はQwenが長くなりましたが、atproto.dartの
collectorだけで約60秒の差があり、model速度には使いません。

両コピーは実行後にintegrity `ok`、foreign key違反0を確認して削除しました。Gemmaは固定入力で根拠外identifierを
生成せず、複製DBでも保存identifierや人が確定した判断を失いませんでした。期待に合うcategoryがQwenより2件多く、
処理時間は暫定停止目安の1.5倍以内だったため、この時点のproject-level triageでは少数試用へ進める条件を満たしました。

## Agents A1の再確認

定期jobが動いていない時にgoodfeeds、Chive API、atproto.dartを各2回実行し、6/6 request成功、全検査6/6、
根拠外identifier 0件、categoryも全回期待値に一致しました。1巡目96.3秒、2巡目66.7秒で、処理時間の利点は
ありませんでした。

自動設定はcontext 262,144、parallel 4です。Agents実行前はTTL中のQwenだけ、実行後はAgentsだけがload済みと
runtime APIが返しました。これは通常APIの自動切替がQwenを残さなかった観測であり、Agentsを明示的に常駐させた
上で別modelを追加できないことまでは示しません。ただし現在の9.7 GBというfootprintでは、小型modelを追加常駐
させる利点は限定的です。

Qwen 3.5 9Bのpackageは約6 GBですがfootprintは未測定です。低優先度処理を再検討する場合は、処理内容を決めた
上で、AgentsとQwen 9Bを同じcontext、parallel、連続処理、同居条件で比較する必要があります。

## repository analysisの確認

### 2026-08-31 closed candidate契約

Qwenの固定`goat` 10回で全結果がclaim棄却され、5件にmaterial外collectionがあった旧契約に対し、LLM入力を
本文付きevidence blockへ限定し、観測とcollection利用を事前生成したcandidate IDの選択へ変更しました。READMEは
元の行番号を保つ最大6 block・合計6,000文字とし、tree pathはLLMの根拠から外しました。collection candidateは
NSIDとoperationを一体で固定し、候補が空の時は出力schema自体を空配列にします。

固定`goat`のv11 10回はrequest成功9/10、保存規則通過9/10、候補外collection 0、claim棄却0でした。失敗1件は
応答内部の不正JSONです。調整用5件と未調整5件を各1回確認したv11は8/10有効で、未調整5件は5/5有効でした。
不合格2件はいずれもcollection candidateが空なのにQwenが連番IDを補ったもので、v12で空配列を明示した再確認は
2/2有効でした。途中評価で見つけた本文中の`E01`表記は検査正規表現とpromptを修正し、operationの自由選択は
operation込みcandidateへ置換しました。特定repository名や既知NSIDへの分岐は追加していません。正本DBは変更していません。

master取り込み後、保存済みrunから5 repositoryのrevision固定materialをread-onlyで取り出し、QwenとGemmaへ
各1回渡しました。この5件はmaster側の実装・調整に使われた可能性があり、今回の失敗を受けたprompt修正にも
使ったため、今後は互換性とprompt調整の確認用です。未知projectでの性能には数えません。

| 構成 | request成功 | 保存規則を全claimが通過 | 根拠位置に不備があるresult | materialにないcollection NSIDがあるresult |
| --- | ---: | ---: | ---: | ---: |
| Qwen | 5/5 | 1/5 | 2/5 | 1/5 |
| Gemma | 4/5 | 0/5 | 2/4 | 1/4 |

両modelとも、入力materialのJSON内の位置を新しく組み立てたり、ファイル一覧のpathだけを内容の根拠にしたりする
出力が多く、model差を決める前に共通契約の修正が必要と判断しました。Qwenでも完全に保存可能だったのは1件だけで、
Gemmaは1件が不正JSONで失敗しました。

この結果を受け、LLMへ利用可能な根拠位置をprompt内だけの短い番号で示し、番号だけを返させて保存前にlocatorへ
戻すよう変更し、summaryにも根拠を必須化しました。内容を持たないtree pathはLLMの根拠から外し、treeから安全に判断できる観測は決定的な抽出処理だけに残しました。
host integrationは、一般的なWeb frameworkの利用だけでは成立しないよう意味検査も追加しました。

修正後の同じ5件を各modelへ1回ずつ渡した結果は次の通りです。

| 構成 | request成功 | 保存規則を全claimが通過 | 根拠位置に不備があるresult | materialにないcollection NSIDがあるresult | 正規化でclaimを落としたresult | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen | 5/5 | 0/5 | 0/5 | 1/5 | 4/5 | 68.4秒 |
| Gemma | 5/5 | 3/5 | 0/5 | 0/5 | 2/5 | 67.1秒 |

QwenはSmoke Signalでmaterialにないcollectionを1件生成し、複数caseで`capabilities`をschema外の文字列として返しました。
Gemmaの残る不備は、rolldの重複または根拠との意味不一致の観測と、statusphereのexample判定のbasis不一致です。
Gemmaのstatusphere summaryはrepository metadataに根拠を持ちましたが、READMEの現行Next.js実装より古いExpressJS説明へ寄っており、
根拠付きであることと情報の新しさは別に確認する必要があります。

この5件は実装・prompt調整に使ったため、3/5という値を未知repositoryへの一般化性能には使いません。一方、両modelの
根拠位置不備を0件にした共通契約と、GemmaがQwenより保存可能な構造を多く返したことは、現行経路の互換性確認に使います。

正規化済みrepository解析を同じ5件のtriage入力へ重ねる二段階確認では、両modelとも5/5 request成功、schema有効、
根拠外identifier 0件でした。categoryはAT-Proto-SDK、Smoke Signal、rolld、statusphere-example-appの4件で両modelが
期待どおりでした。外部hostへ組み込むatmogusは`infra_tool`が期待ですが、Qwenは`library`、Gemmaは`name_only`でした。
入力materialが薄く、個別case向けの追加規則は作っていません。Qwenはstatusphereを`service_status=live`、
`technical_interest_level=high`まで進め、Gemmaの`unknown`、`notable`より断定が強すぎるため、後段の安全性ではGemmaが妥当でした。

通常`enrich`には固定評価で使った最大出力2,048 tokenを渡す入口がなかったため、`--llm-max-output-tokens`を追加しました。
この値はplanning、repository analysis、triageへ共通して渡します。最新DB複製の確認では2,048を明示しました。
正本DB試用を再検討する場合も同じ値を使い、固定評価と実行条件を揃えます。

## 最新DB複製で見つかった問題

確定した7件の試用対象を正本DBから作った複製へ通し始めましたが、confirmed repositoryを持つ既知caseの段階で
次の問題が見つかったため、正本DB試用と残りcaseの実行を止めました。

1. `atproto.dart`の`website/docs/*.md`や`_category_.json`が、pathだけを理由にdeployment configurationとして
   決定的に抽出されていました。設定ファイル候補から一般の`/docs/`文書を外し、実データの再実行で誤観測が消えたことを確認しました。
2. `goat`では同じREADME excerptがsummaryと8 capabilityへ複製され、repository解析結果が20,732文字になりました。
   その結果、後段planningとtriageは24 KiBの入力上限に収まらず、LLM request前に失敗しました。claimには根拠位置だけを残し、
   本文は重複を除いた`evidence_excerpts`へ分離しました。既存の実結果5件を新しい保存形へ通すと、保存結果は
   2,276〜3,922文字、triage投影は1,930〜3,536文字に収まりました。
3. 現行repository analysis policyで作り直した複製では、`goat`の解析が2回とも`invalid_response`になりました。
   planningとtriageは成功し、categoryは`infra_tool`になりましたが、repository解析結果は保存できませんでした。
   以前のpolicyでも同caseは初回失敗後の再試行で成功しており、常に失敗する相性ではない一方、通常運用へ進める
   安定性とは扱えません。
4. 失敗応答の本文を残さず、失敗段階、byte数、SHA-256、JSON構文エラー位置、開始・終了記号、code fenceの有無を
   run metricsと固定評価reportへ保存する診断を追加しました。通常`enrich`で同じ`goat`を再実行した1回はrepository analysis
   624 bytes、planning、triageがすべて成功し、categoryも`infra_tool`でした。
5. そのmaterialを固定したrepository analysis単体の10回反復では、最初の5回が成功3・失敗2、診断追加後の5回が成功2・
   失敗3でした。診断付きの失敗3件はすべて`{`で始まり`}`で終わる819〜1,613 bytesの応答で、JSON error位置は
   503〜623でした。末尾切断ではなく、完成形に見える応答内部の構文違反です。成功5件もREADMEだけを根拠に
   `declares_cli_entrypoint`を確定する観測が除外され、保存規則をすべて通る結果は0/10でした。
6. 同じmaterialと現行policy、Chat Completions、reasoningなし、最大出力2,048 tokenでQwenも5回ずつ2巡しました。
   JSONは10/10成功しましたが、全件で`declares_importable_package`と`defines_lexicons`が根拠不足として除外され、
   6/10件では1〜7個のmaterialにないcollectionも除外されました。保存規則をすべて通る結果は0/10です。
   request累積時間はQwen 147.2秒、Gemma 53.2秒でした。Qwen 1巡目の最初の27.0秒を除いても平均13.4秒、
   Gemmaは全10回平均5.3秒で、このrepository解析入力ではQwenの方が遅い結果です。

共通処理の修正と診断追加は全541 unit testを通しています。現行repository analysis policyで解析から後段triageまで成功する実DB caseは
`goat` 2回ですが、固定反復が上記の結果なので、調整後の固定5件や通常`enrich`の成功を現行policy全体の安定性へ読み替えません。

## 採用判断と再評価条件

| 観点 | 現在の判断 |
| --- | --- |
| category | 固定21件は同点。複製DBではGemmaが2件優位 |
| schema・identifier | Gemmaが優位 |
| repository analysis | 固定`goat`ではGemma JSON 5/10、Qwen 10/10。完全有効は両方0/10。Gemmaは根拠外collection 0件、Qwenは6/10 resultで検出 |
| 文章 | 明確な差なし |
| 処理時間 | Qwenが優位。Gemmaは約1.3倍 |
| 常駐memory | 手動観測ではGemmaが約4 GB小さい |
| prompt保守 | 両modelで別suffixが必要 |
| 正本試用 | Gemmaは固定反復でJSON成功5/10のため実施しない。Qwenを維持するがrepository解析品質は別途改善対象 |

Gemmaを再評価するのは、model、runtime、またはpromptの変更後に、同一`goat` materialの反復でJSON構文エラーが
再発せず、根拠不十分な観測を減らせた場合です。その後に別の未調整repositoryと最新DB複製を確認し、条件を通った
場合だけ正本DBの少数試用を検討します。試用時は対象、停止条件、baselineへ戻す方法、backupの保持期限を決めて
別途承認を得ます。既定modelと定期jobはQwenのままにします。

peak memory、swap、prefill、decodeのrunner自動記録は未接続で、将来の評価基盤改善として残します。

## 旧測定を採用根拠にしない理由

2026-07-24以前のcategory結果は、旧category契約、prompt調整に使ったcase、または前回LLM出力が入力へ戻る条件を
含むため、現在の採用根拠へ直接使いません。

旧gpt-oss 20B GGUFでは`reasoning low`を要求しましたが、LM Studioは有効なcustom reasoning fieldがなく、設定を
model KVへ変換できないと記録しました。requestへ`low`を送った記録を適用済みとは扱いません。

初期のGemma用suffixで実行した17件ではGemmaの累積時間が51.2秒短く見えましたが、Qwenの最初の1件だけが131.5秒
かかったためです。最初を除くとQwen 226.9秒、Gemma 293.2秒で、16件すべてQwenの方が短い結果でした。
