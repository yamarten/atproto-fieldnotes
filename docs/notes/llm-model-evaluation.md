# Qwen / Gemma model比較

> 2026-07-25から2026-07-26に行った比較の作業記録です。再利用する評価手順は
> [LLM model評価](../developer/llm-model-evaluation.md)、categoryの意味は
> [project-record.md](../developer/project-record.md#category)を正本とします。

## 結論

Gemma 4 26B A4B MLXは、現在のQwen 3.6 35B A3Bより約30%遅い一方、JSON/schema、enum、identifierの
安定性と一部categoryで優位でした。文章品質には明確な差がなく、手動観測のfootprintはGemmaが約4 GB小さい
結果でした。

GemmaはQwenを直ちに置き換えるほど圧倒的ではありませんが、正本DBの少数projectで試す価値があります。
正本DBでの試用と既定modelの変更は未実施です。Qwenは通常運用の既定modelのままです。

Agents A1 4Bは、小さいmodelを常駐させる低優先度処理の候補として確認しました。品質は少数caseで保ちましたが、
処理が遅く、自動切替ではTTL中のQwenを退避させました。明示loadに依存せずQwenと同居できるruntime構成が得られる
までは試用へ進めません。

## 比較した構成

| 役割 | model | 配布形式 | 評価用suffix |
| --- | --- | --- | --- |
| baseline | Qwen 3.6 35B A3B | MLX 4bit、20.43 GB | category-gates v3 |
| 通常処理candidate | Gemma 4 26B A4B | MLX 4bit QAT、15.64 GB | category-gates v6 |
| 低優先度処理candidate | Agents A1 4B | GGUF Q8_0、4.48 GB | なし |

runtimeはLM Studio 0.4.20+1、Chat Completions、最大出力2,048 token、reasoningなしです。QwenとGemmaは
API応答の`reasoning_tokens=0`を確認しました。LM Studioが返したload configはQwen context 183,296、Gemma
226,304、いずれもparallel 4で、要求した32,768は反映されませんでした。今回のpromptは実際のcontextより十分
小さいものの、contextまで同一の比較ではありません。

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

Qwen用category-gates v3とGemma用category-gates v6は、各modelで再現した誤りだけを対象にしました。強い指示を
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
処理時間は暫定停止目安の1.5倍以内だったため、正本DBの少数試用へ進める条件を満たしました。

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

## 採用判断と残件

| 観点 | 現在の判断 |
| --- | --- |
| category | 固定21件は同点。複製DBではGemmaが2件優位 |
| schema・identifier | Gemmaが優位 |
| 文章 | 明確な差なし |
| 処理時間 | Qwenが優位。Gemmaは約1.3倍 |
| 常駐memory | 手動観測ではGemmaが約4 GB小さい |
| prompt保守 | 両modelで別suffixが必要 |

未完了:

- masterのGitHub evidence取得変更を取り込み、比較条件への影響を確認する
- 試用対象を、既知の保存確認用projectとmodel調整に使っていないprojectへ分けて選び直す
- 正本DBでGemmaを少数projectへ適用する
- peak memory、swap、prefill、decodeをrunnerから自動記録する

正本DBの試用は通常`enrich`を書き込む実運用試用です。対象、停止条件、backupの保持期限を決め、別途承認を得てから
行います。既定modelと定期jobは、試用結果を確認するまでQwenのままにします。

## 旧測定を採用根拠にしない理由

2026-07-24以前のcategory結果は、旧category契約、prompt調整に使ったcase、または前回LLM出力が入力へ戻る条件を
含むため、現在の採用根拠へ直接使いません。

旧gpt-oss 20B GGUFでは`reasoning low`を要求しましたが、LM Studioは有効なcustom reasoning fieldがなく、設定を
model KVへ変換できないと記録しました。requestへ`low`を送った記録を適用済みとは扱いません。

旧category-gates v4の17件ではGemmaの累積時間が51.2秒短く見えましたが、Qwenの最初の1件だけが131.5秒
かかったためです。最初を除くとQwen 226.9秒、Gemma 293.2秒で、16件すべてQwenの方が短い結果でした。
