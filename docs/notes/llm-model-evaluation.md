# LLM Model Evaluation

> 作業用・比較用文書です。LLMの恒久的な役割は
> [investigation-model.md](../developer/investigation-model.md)、通常導線の接続状況は
> [registry-internals.md](../developer/registry-internals.md)を正本とします。

## 目的

次の二つを扱います。

- 通常`enrich`で使うmodelを変更するか、確認結果に基づいて判断する
- 新しいmodelや新versionを、同じ手順と観点で比較できるようにする

model名や単一のscoreを採用条件にはしません。model、配布形式、runtime、chat template、prompt、
reasoning設定を一つの実行構成として記録し、その構成で確認します。

## 現在地

実装済み:

- category契約
  - 主な利用者と利用形態による定義を正本文書、production prompt、保存、評価検査へ反映した
  - `auth_only`をcategoryから外し、独立した`atproto_auth_only`へ分けた
- `export-llm-evaluation-corpus`
  - 指定DBをread-onlyで開き、保存済みLLM evidenceから固定caseを出力する
  - 現在のQwen由来profileをevidenceから外して別に保持し、元profileなし／ありを同じcaseから作れる
  - 前回LLM出力だけを根拠とする`lexicon_scope`も除き、前回分類を入力へ戻さない
- `evaluate-llm-corpus`
  - 一つの実行構成を固定caseへ適用し、各request完了時までの結果をreportへ書き出す
  - JSON/schema、enum、evidence外identifier、categoryとsummaryの不整合、日英混在文の空白、
    latency、同一入力を複数回実行した時のfield差を記録する
  - reasoningは要求値とruntimeで確認した状態を分ける
  - 期待categoryを人が確認したlabelは、prompt調整に使ったcaseと使っていないcaseを分けて集計する
- `compare-llm-evaluation-reports`
  - 同じcorpus、case、repeatで作ったbaselineとcandidateを比較する
  - 機械検査で確認できる退行と、文章確認や複製DBでのenrich比較など未実施の確認を分けて返す

現在の候補:

- Qwen 3.6 35Bは現在の通常運用baseline
- Gemma 4 26B A4B MLXは少数project試用へ進める第一候補
- Agents A1 4Bは、小さいmodelを常駐させる低優先度background処理の候補
- `tests/fixtures/llm_evaluation_prompt_gemma4_26b_category_gates_v6.txt`はGemmaの既知誤分類を
  見ながら調整した評価用suffixで、production promptには未反映

未完了:

- 通常処理候補の少数project試用
- Agents A1のidle時切替、memory、連続background処理確認
- peak memory、swap、prefill、decodeのrunner自動記録

したがって、現時点ではGemmaを通常運用modelとして採用済みとは扱いません。

## 2026-07-25の新category契約による再計測

### runtimeと少数選別

LM Studio 0.4.20+1の`/api/v1/models`で、選択variant、format、量子化、reasoning対応を確認した。
現在のgpt-oss 20Bは旧GGUFではなくMLX MXFP4で、runtimeが`low / medium / high`を許容し、既定値も
`low`と報告した。この構成の`low`は適用済みとして扱う。旧GGUFで`low`が無視された結果とは混ぜない。

調整用case 8件のproduction promptによる少数選別では、Granite 4 H Tinyは全検査1/8、gpt-oss MLXは
category 2/8と根拠外identifier 1件、Qwen 3.5 9Bは全検査5/8だったため、通常処理の次段階へ進めなかった。
Agents A1 4Bはcategory 5/8、全検査7/8だったが216.4秒かかり、通常処理の速度上の利点はなかった。
一方、model packageは4.48 GBなので、常駐background用途のmemory候補としては残す。

この少数選別中、元profileを除いたcorpusにも前回Qwen出力由来の`lexicon_scope=shared`が残っていることを
確認した。通常enrichとcorpus exportの両方で、`lexicon_scope_evidence`が`llm_output:*`だけの場合はscopeを
`unknown`へ戻すよう修正した。外部またはheuristic根拠を持つscopeは維持する。新corpus 3,635件では13件の
evidence hashが変わり、調整用56件と未使用21件のlabelをproject単位で対応づけ直した。汚染corpus上のcategory
scoreは採用判断へ使わない。

Qwen用category-gates v3とGemma用category-gates v6は、調整用caseで実際に確認した誤りだけを対象にした。
両modelとも、schema sampleだけでも対象領域が詳細で一貫している場合に、文書化された共有lexiconまたは
実装済みinfraへ昇格させる傾向が残った。強い指示を重ねても解消しなかったため、個別caseに合わせた追加調整は
止め、既知の不一致として未使用caseへ進んだ。

### prompt調整に使っていない21件

model出力を見る前に期待categoryを確認した21件を、clean corpusと固定suffixで各1回実行した。
Chat Completions、最大出力2,048 token、reasoningなしで、元profileなし／ありを分けた。

| 構成 | 元profile | request成功 | 全検査通過 | category一致 | 根拠外identifier | 累積時間 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、category-gates v3 | なし | 21/21 | 17/21 | 17/21 | 2件 | 250.4秒 |
| Gemma 4 26B、category-gates v6 | なし | 21/21 | 21/21 | 17/21 | 0件 | 330.8秒 |
| Qwen 3.6 35B、category-gates v3 | あり | 21/21 | 19/21 | 16/21 | 1件 | 255.5秒 |
| Gemma 4 26B、category-gates v6 | あり | 21/21 | 19/21 | 17/21 | 0件 | 338.0秒 |

元profileなしではcategory一致数が同じだったが、Gemmaは全21件でschemaとidentifier検査を通った。Qwenは
GoodfeedsとChartIDでevidenceにないNSIDを生成した。category不一致は両modelに共通して、Markdown同期CLIを
`library`、根拠の薄いhealth projectを`infra_tool`、schema sample中心のcorvusを`lexicon`とした。加えてQwenは
Bluesky CLI skillを`library`、GemmaはTitleGraphを`project`とした。

元profileありは両modelとも改善しなかった。Qwenはcategory一致が1件減り、Gemmaは一致数を維持したが全検査が
2件減った。profileなし／ありでcategoryが変わったcaseはQwen 1件、Gemma 4件だった。現在profileを渡すことは
既存文章の更新適性を確認する条件ではあるが、分類の安定化策とは扱わない。

この段階では、安全検査でGemma、速度でQwenが優位で、category一致は同点である。Gemmaを既定へ変更する判断は
まだ下さない。次は同一入力の複数回実行、文章比較、memory、複製DBの順に確認する。

速度は累積時間だけで判断しない。元profileなし21件では、Qwenは中央値12.6秒、Gemmaは15.2秒で、21件すべて
Qwenの方が短かった。最初の1件を除いてもQwen 237.1秒、Gemma 301.7秒なので、この組ではmodel読み込みだけで
生じた差ではない。prompt tokenはQwen 111,780、Gemma 116,265、completion tokenはそれぞれ10,415、10,884で、
生成量にも小差がある。reportはprefillとdecodeを分けていないためtoken毎の推論性能までは断定しない。

一方、旧category-gates v4の未調整17件でGemmaの累積時間が短かったのは、Qwenの最初の1件だけが131.5秒
かかったためだった。最初の1件を除くとQwen 226.9秒、Gemma 293.2秒で、16/17件はQwenの方が短い。
したがって旧結果も、処理中の速度はQwenが優位で、最初のrequestにmodel読み込み等の一時費用が混ざったものと
読み直す。以後は最初のrequest、最初を除いた累積時間、中央値、最長時間を分けて記録する。

### 未使用caseの反復とmemory

未使用21件のうち、単発でidentifierまたはcategory差が出た7件と正常なlibrary 1件を各2回実行した。

| 構成 | request成功 | 全検査通過 | categoryが変わったcase | identifier集合が変わったcase | 検査成否が変わったcase | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、category-gates v3 | 16/16 | 12/16 | 0/8 | 2/8 | 3/8 | 159.5秒 |
| Gemma 4 26B、category-gates v6 | 16/16 | 16/16 | 0/8 | 0/8 | 0/8 | 206.3秒 |

両modelともcategoryは2回で変わらなかった。QwenはGoodfeedsとTitleGraphでidentifier集合が変わり、3件で
検査成否も変わった。Gemmaは文章、features、manual review等の差は7件にあったが、安全検査に関係する差は
なかった。単発21件で見えたGemmaの安全面の優位は反復でも維持された。

LM Studio workerをmacOSの`footprint`で手動観測した。Gemmaは反復完了後にphysical footprint 16 GB、
process lifetime peak 19 GB、Qwenは通常1件完了後に20 GB、peak 22 GBだった。load packageはGemma
15.64 GB、Qwen 20.43 GBである。runnerへ接続した同一workloadの自動計測ではないため精密なpeak比較には
使わないが、今回の通常構成ではGemmaの常駐memoryが小さいという観測には使う。

Agents A1はpackage 4.48 GBだが、footprint確認用の通常API要求は2回ともHTTP 500となった。同時刻には23:13開始の
定期workerが動いており、LM Studio logの直接原因も予測開始時の`Model unloaded`と直後のinstance消失だった。
Agents A1固有の失敗またはresource不足とは判定せず、この2回は競合した測定として採用しない。定期jobが動いて
いない時に、明示loadを使わない同じ経路で切替信頼性とfootprintを再測定する。

### model名を伏せた文章確認

元profileなしの未使用8件について、QwenとGemmaのtagline、summary、featuresをmodel名を伏せて人が比較した。
caseごとにA/Bの順を入れ替え、主要機能、根拠外の断定、日本語、情報量をまとめて判定した。

- Gemmaが好ましい: 3件
- Qwenが好ましい: 2件
- 同等: 3件

Ozoneは両modelのtaglineとsummaryが完全に一致し、featuresだけが軽い言い換えだった。atproto.dartもほぼ同じ
内容へ収束した。一方、evidenceが名前とrepository URLだけのatproto-healthは、両modelともmonitoring・診断機能を
断定しており、同等は同等に妥当という意味ではない。今回の文章比較では、どちらか一方の明確な優位は確認できない。

### 現時点の判断表

| 観点 | 現在の観測 | 判断への影響 |
| --- | --- | --- |
| category | 元profileなしは両model 17/21。ありはQwen 16/21、Gemma 17/21 | 決め手なし |
| schema・identifier | 単発と反復の両方でGemmaが安定。Qwenは根拠外identifierと再実行時の変化あり | Gemmaを次段階へ進める主な理由 |
| 文章 | 伏せた8件でGemma 3、Qwen 2、同等3 | 明確な差なし |
| 処理時間 | 21件は全件Qwenが短く、重い入力と複製DBでもGemmaは約1.3倍 | Qwen優位。ただし暫定停止目安1.5倍以内 |
| 常駐memory | 手動観測はQwen 20 GB、Gemma 16 GB | Gemma優位。ただし同一workloadの自動測定ではない |
| 元profile | どちらも分類を安定化せず、Gemmaも4件でcategoryが変化 | 移行時は保存結果を別途確認 |
| prompt保守 | 両modelで別suffixが必要 | suffixの複雑化を運用費として扱う |

Gemmaは安全性とmemory、Qwenは処理時間で優位であり、現時点では既定modelを変更しない。重い入力と複製DBで
安全面の優位が保存後にも残り、Gemmaの遅さも暫定停止目安内だったため、次は少数project試用へ進む。

### 重い入力5件

2026-07-26に、後述の重い5件を元profileなしで各2回実行した。

| 構成 | request成功 | 全検査通過 | 根拠外identifier | 1巡目 | 2巡目 | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、category-gates v3 | 10/10 | 7/10 | 0件 | 113.5秒 | 50.3秒 | 163.8秒 |
| Gemma 4 26B、category-gates v6 | 10/10 | 9/10 | 0件 | 156.6秒 | 65.5秒 | 222.1秒 |

Qwenはcocoreの2回で`confidence`を欠き、Scott Polhemus Personal Blog Mediaの2回目でも同じfieldを欠いた。
Gemmaは同blogの2回目だけ`technical_interest_level=low`というenum外値を返した。categoryはどちらも各caseの
2回で変わらなかった。identifier集合はQwenで2/5件、Gemmaで1/5件変わったが、根拠外identifierはなかった。

旧Qwenで131.5秒かかった最初のsifa-sdkは今回は28.4秒で、旧外れ値は再現しなかった。各caseの2回目は両model
とも大きく短縮しており、同じpromptに対するruntime cacheの影響がある。2巡目だけでもGemmaはQwenの1.30倍、
全体では1.36倍で、重い入力でもQwenの方が速かった。一方、暫定目安の1.5倍には達せず、Gemmaの検査通過数は
2件多かったため、速度だけを理由に複製DB比較を止めなかった。

### 新category契約による複製DB比較

同じ正本DB snapshotのcopy-on-writeコピーを2つ作り、後述の8 projectを通常の単体`enrich`へ同じ順で通した。
正本DBは更新していない。外部collectorは連続して再実行したため、その取得失敗と所要時間はmodel差から分けた。

| project | Qwen保存category | Gemma保存category | 確認結果 |
| --- | --- | --- | --- |
| goodfeeds | `lexicon` | `project` | 未使用caseの期待値は`project`で、Gemmaだけ一致 |
| ChartID | `lexicon` | `lexicon` | 一致 |
| TitleGraph | `lexicon` | `lexicon` | 一致 |
| leaflet-md | `library` | `infra_tool` | ATProto連携CLIという合意済みの境界ではGemmaが妥当 |
| corvus | `project` | `project` | 生出力は両方`lexicon`だが、稼働projectの保存規則で補正 |
| Ozone | `infra_tool` | `infra_tool` | 一致 |
| atproto.dart | `library` | `library` | 一致 |
| Chive API | `project` | `project` | 一致 |

保存後のservice URL、source code URL、lexicon集合、tagline、summary、featuresは8/8件で両modelが同一だった。
tagline、summary、featuresは両方ともsnapshotの元profileから変わらず、今回の保存結果は既存文章を守れたことは
示すが、Gemmaがprofileを改善できることまでは示さない。service statusとpromotion readyも8/8件で一致した。
manual reviewの種類はChartID、leaflet-md、corvus、technical interestはgoodfeedsとcorvusで異なったが、promotionを
止める向きの逆転はなかった。leaflet-mdだけGemmaがservice statusの人手確認を勧め、Qwenは勧めなかった。

LLM requestの累積時間はQwen 198.8秒、Gemma 236.2秒だった。最初のgoodfeedsではGemmaが既にload済みで、Qwenは
自動切替を含むため、公平性の低い最初のprojectを除くとQwen 161.7秒、Gemma 208.7秒でGemmaが1.29倍だった。
全処理時間はQwen 401.9秒、Gemma 359.7秒だが、Qwen側atproto.dartのcollectorだけが91.5秒かかり、Gemma側は
31.9秒だった影響が大きい。この全処理時間の逆転はmodel速度として使わない。

両コピーは実行後にintegrity `ok`、foreign key違反0を確認した。Gemmaは重い固定入力で根拠外identifierを生成せず、
複製DBでも保存identifierや人が確定した判断を失わず、期待に合うcategoryがQwenより2件多かった。
LLM処理時間も暫定停止目安の1.5倍以内だった。固定caseと複製DBの範囲では、期間と停止条件を決めた少数project
試用へ進める条件を満たした。

2026-07-25にcategoryの意味を見直し、[project-record.md](../developer/project-record.md#category)へ記録した上で、
`auth_only`の削除、`infra_tool`の範囲拡大、`personal_tool`の利用者範囲による判定を現行実装へ反映しました。
この文書にある旧category一致数は次のmodel選定根拠として直接使いません。過去の実行結果は速度、memory、
JSON/schemaの挙動を知る材料として残し、categoryは新しい契約でlabelを確認して計り直します。

## 2026-07-24の一次計測

LM Studio 0.4.20+1、MLX 4bit、Chat Completions、最大出力2048 token、
`reasoning_effort=none`で固定corpus 40件を各1回実行した。Qwen、GemmaともAPI応答の
`completion_tokens_details.reasoning_tokens=0`を確認した。

load APIへは両modelともcontext 32,768を要求したが、LM Studioが返した実際のload configは
Qwenがcontext 183,296、Gemmaが226,304で、いずれもparallel 4だった。要求値は反映されておらず、
context設定まで同一の比較ではない。今回のpromptはどちらの実値よりも十分小さいが、このruntime差は
未解消として残す。

| 構成 | request成功 | schema等の完全通過 | prompt調整に使っていない18件のcategory一致 | evidence外identifier | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、production prompt | 40/40 | 32/40 | 7/18 | 0件 | 512.5秒 |
| Gemma 4 26B、production prompt | 40/40 | 37/40 | 9/18 | 2件 | 545.9秒 |
| Gemma 4 26B、category-gates v3 | 40/40 | 40/40 | 9/18 | 0件 | 554.3秒 |

Gemmaのproduction promptで追加されたevidence外identifierは、入力NSIDの親namespace 1件と、
根拠にないservice URL 1件だった。category-gates v3では両方とも解消した。未調整18件の
category一致数はproduction promptとcategory-gates v3で同じだが、Bluemarkは改善し、Sequoiaは
`infra_tool`から`project`へ変わって不一致になった。

代表10件を各2回実行した結果は次の通り。

| 構成 | categoryが2回で変わったcase | identifier集合が変わったcase | 片方だけ検査結果が変わったcase | evidence外identifier | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、production prompt | 0/10 | 3/10 | 1/10 | 1件 | 237.0秒 |
| Gemma 4 26B、category-gates v3 | 0/10 | 0/10 | 2/10 | 0件 | 281.2秒 |

どちらもcategoryは安定した一方、schema等の検査は再実行時に揺れた。1回目の40件だけを根拠に
Gemmaを完全通過と扱わない。

今回の人によるcategory確認では、`lexicon`を文書化された共有Lexiconに限定した。一方、production
promptは「schema自体が追跡対象なら`lexicon`」としており、NSIDしかない候補にも広く適用できる。
両modelがNSID-only候補を繰り返し`lexicon`へ分類した主因は、この規則の差と考えられる。
今回の18件を見てpromptを修正すると、それらは以後prompt調整に未使用の評価caseとして使えない。
規則を直す場合は今回のcaseを調整用へ移し、別の未使用caseを用意してから再評価する。

### model名を伏せた文章確認

同じ8件について、QwenとGemma category-gates v3のtagline、summary、featuresをmodel名を伏せて
人が比較した。caseごとにA/Bの順を入れ替え、総合的に好ましい方を選んだ結果は次の通り。

- Qwenが好ましい: 4件
- Gemmaが好ましい: 2件
- 同等: 2件

個別観点の採点や自由記述は行っていないため、事実性、情報量、日本語の読みやすさを分けた評価ではない。
この範囲ではGemmaの文章品質がQwenより高いとは確認できず、Qwenがやや好まれた。

## 2026-07-25の未調整17件

`project-triage-v2`とGemma用category-gates v4の文言を決める際に参照していない17件を、
model出力を見る前に人が確認して固定した。LM Studioとreasoning条件は一次計測と同じ。

| 構成 | request成功 | schema等の完全通過 | category一致 | evidence外identifier | 累積時間 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Qwen 3.6 35B、`project-triage-v2` | 17/17 | 12/17 | 6/17 | 1件 | 231.4秒 |
| Gemma 4 26B、`project-triage-v2` | 17/17 | 13/17 | 6/17 | 0件 | 295.3秒 |
| Qwen 3.6 35B、`project-triage-v2` + category-gates v4 | 17/17 | 13/17 | 8/17 | 0件 | 358.4秒 |
| Gemma 4 26B、`project-triage-v2` + category-gates v4 | 17/17 | 16/17 | 11/17 | 0件 | 307.2秒 |

Gemmaは共通promptだけではQwenと同じ6/17で、category-gates v4が性能に必須だった。同じv4をQwenへ
付けると8/17へ改善したが、Gemmaはさらに3件多く、完全通過も3件多かった。累積時間はGemmaが51.2秒
短いが、Qwenの最初の1件に131.5秒の一時費用が含まれる。これを除く16件ではQwen 226.9秒、Gemma
293.2秒で、個別にも16件すべてQwenが短かった。Gemmaの不一致6件は、SDKを`project`、説明のないrelayを
`infra_tool`、OAuth pluginを`auth_only`、文書化されていないschema 3件を`lexicon`としたもの。
Qwenも文書化されていないschemaを`lexicon`へ寄せ、加えてCLIを`library`、個人blogを`project`、
汚染された同名説明を`reject`などとした。Gemmaの唯一の完全通過失敗は`status=live`というenum外の値だった。

### 複製DBの通常enrich比較

同じ正本DB snapshotを2つ作り、goat、Sifa ID、cocore、better-auth-atproto、CannaDB、Airglowを
QwenとGemmaで別々に通常の`enrich`へ通した。両方に同じcategory-gates v4を指定し、suffix本文のhashが
investigation runへ保存されたことを確認した。正本DBは更新していない。

| project | Qwen保存category | Gemma保存category | 判定 |
| --- | --- | --- | --- |
| goat | `project` | `infra_tool` | 旧契約ではQwenを妥当としたが、現契約ではATProto自体を扱うため`infra_tool` |
| Sifa ID | `project` | `project` | 一致。両modelの生出力は`lexicon`だが、稼働serviceの保存規則で補正 |
| cocore | `lexicon` | `lexicon` | 一致。ただし共有Lexiconの外部文書がない固定evidenceでは`name_only`が期待値 |
| better-auth-atproto | `auth_only` | `auth_only` | 一致。人の期待値`library`とは不一致 |
| CannaDB | `project` | `project` | 一致 |
| Airglow | `infra_tool` | `infra_tool` | 一致 |

保存後のtagline、summary、featuresは4/6件で同一だった。差はAirglowのsummaryの言い回しと、Gemmaが
cocoreのfeaturesを3件多く挙げたこと。source code URLとservice URLの集合は6/6件で一致した。
累積時間はQwen 280.9秒、Gemma 401.2秒で、Gemmaが120.3秒遅かった。Gemmaのgoatで調査計画だけに
75.3秒かかった単発値の影響が大きく、恒常的な43%差とはまだ扱わない。両コピーはintegrity `ok`、
foreign key違反0を確認し、比較後に削除した。

固定evidenceではGemmaが優位だった一方、通常enrichの6件では保存品質の優位を確認できなかった。
goatの当時の解釈も現category契約では採用しない。旧prompt構成の文章比較ではQwenがやや好まれているため、
この時点で通常運用modelをGemmaへ置き換えない。次へ進む場合は正本DBの少数projectで期間を区切った試用とし、
Qwenを戻せる状態を保つ。

## 過去の測定を採用根拠にしない理由

2026-07-20から2026-07-24までのscreeningでは、Gemma 26Bと評価用suffixの組み合わせが
固定40件でschema・identifier検査を通り、期待categoryを人が確認した21件と一致しました。しかし、
その21件はsuffixの調整にも使われています。調整後のmodel選定結果として同じ21件の一致数を使いません。

gpt-oss-20b GGUFでは`reasoning low`を要求しましたが、LM Studioは次を記録しました。

```text
No valid custom reasoning fields found in model 'comparison/gpt-oss-20b-gguf/gpt-oss-20b-MXFP4.gguf'.
Reasoning setting 'low' cannot be converted to any custom KVs.
```

旧reportの`reasoning_effort=low`は送信値の記録で、runtimeでの適用確認ではありません。
このGGUF実行を`reasoning low`適用済みのMLX/GGUF比較として使いません。MLX側も既存reportだけでは
適用を確認できないため、必要ならruntime logを確認した上で再実行します。

過去の個別reportは`.tmp/`に置く一時結果であり、現行仕様やmodel採用の正本ではありません。

## 実行構成の記録

reportでは少なくとも次を固定します。

- model IDと取得元
- 配布形式、量子化、model fileまたはpackageの識別情報
- runtime名、version、backend
- chat templateまたはruntime既定template
- context、parallel、最大出力token数
- API style
- prompt本体のhash、評価用suffixの名前とhash
- reasoning要求値
- reasoningをruntimeが適用したか、無視したか、未確認か
- corpus hash、case ID、repeat回数
- git commitとdirty状態

`model + backend/format + runtime + template + prompt`のどれかが変わった結果を、同じ構成の継続測定として
混ぜません。runtimeが最終的に適用したcontext等を確認できない場合は、その事実をreport外の説明で補わず、
未確認のまま残します。

## caseの扱い

期待categoryを人が確認したlabelは`used_for_prompt_adjustment`を必須とします。

- `true`
  - model別promptの修正理由または確認に使ったcase
  - prompt調整の回帰確認には使える
  - 調整後のmodel選定結果には使わない
- `false`
  - 当該promptの文言を決める時に参照していないcase
  - model選定時のcategory確認に使える

caseを`false`として実行した後、その出力を見てpromptを変更した場合は`true`へ変更します。
その後の確認には別の未使用caseを用意します。

旧`tests/fixtures/llm_evaluation_category_labels_v2.json`には56件あります。最初の39件はprompt調整時に
参照済みなので`true`です。最初の21件はcategory-gates v3、追加18件は共有Lexicon規則を
`project-triage-v2`とcategory-gates v4へ反映する際に参照しました。2026-07-25に新たなcorpusから
evidenceだけを確認した17件は当時`false`として固定しました。

新category契約で全56件を再確認したlabelは
`tests/fixtures/llm_evaluation_category_labels_v3.json`です。旧結果とcategory境界を見ながら契約とpromptを
変更したため、56件すべてを`used_for_prompt_adjustment=true`としました。prompt固定後のmodel選定には、
この56件とは重ならない21件を`tests/fixtures/llm_evaluation_category_labels_v3_holdout.json`へ固定しました。
21件はmodel出力を確認する前に人が期待categoryを確認し、`used_for_prompt_adjustment=false`としています。
今後この21件の結果を見てpromptを変更した場合は調整用へ移し、別の未使用caseを用意します。

候補の`bluesky-backup-webapp`はREADMEにPDS backup用途が明記されている一方、固定evidenceにはrepository URLと
名前しかありませんでした。GitHub collectorはREADME本文を取得しますが、保存するのはREADME内URLで、本文は
summary候補や固定evidenceへ渡しません。このcaseは正解を入力から導けないため17件から外しました。これは
model性能ではなくevidence不足の論点であり、GitHub README本文を通常のenrichへどう渡すかは未解決です。

## 確認の順序

### 1. category契約を実装する

production prompt、保存、評価検査、正本文書への反映は完了しました。正本DBの旧`category=auth_only` 35件は、
役割を個別に確認し、正本DBの複製へ次の移行案を適用しました。

| 移行先 | 件数 | 主な対象 |
| --- | ---: | --- |
| `infra_tool` | 17 | OAuth reference/demo、OIDC provider、外部serviceへ導入するplugin、atpassport |
| `library` | 9 | OAuth client、importして使う認証package、Better Auth plugin |
| `project` | 3 | ATAuth、atauth、独自recordを持つBeacon |
| `name_only` | 6 | NSIDまたは汎用READMEだけで役割を確定できない対象 |

この35件はいずれも、独立した別用途のappまたはserviceがATProtoをaccount loginにだけ利用することを
確認できた対象ではありません。そのため`atproto_auth_only`は全件falseとしました。複製DBでは旧category 0件、
integrity `ok`、foreign key違反0を確認し、category以外の変更は寄与度の再計算fieldだけでした。
承認後に同じ移行を正本DBへ適用し、別のread-only接続でも件数と検査結果を再確認しました。

[project-record.md](../developer/project-record.md#category)の契約を、先にprompt、検査、テストへ反映します。

- `auth_only`をcategoryから外し、独立appまたはserviceのATProto利用がaccount認証だけの場合は
  `atproto_auth_only`で保持する。認証実装自体を提供するlibraryや運用componentには付けない
- `project`は一般利用者が直接使うもの、`library`と`lexicon`は開発者が組み込む・実装するものとして分ける
- `infra_tool`はonline infrastructureに限定せず、projectの背後で動くものとATProto自体を扱う実行物を含める
- `personal_tool`は用途の狭さではなく、想定利用者が作者本人または固定集団に限定される場合に使う
- URL混入はcategoryではなくidentifier関係の欠陥として扱う
- 停止・削除だけではcategoryを変えず、過去情報を復元できない古い対象では`name_only`を許容する

既存`category=auth_only`の正本DB rowはread-onlyで内容を確認し、複製DB検証後に承認を得て正本DBへ適用しました。
適用前backupは`data/survey.db.backup-20260725-auth-category-migration`です。このbackupは移行直後に意味上の誤りが
見つかった場合のrollback専用で、長期保存しません。2026-07-28まで通常利用で問題がなければ削除します。
この段階が終わるまでmodelのcategory成績を再集計しません。

### 2. 評価caseと元profile条件を作り直す

正本DBの保存済みevidence 3,635件をread-onlyでexportし、全caseでQwen由来profileを入力evidenceから分離しました。
旧56 labelの新契約による再確認と、調整後確認用21件の固定は完了しました。

既存labelを新category契約で確認し、prompt調整に使うcaseと、調整後の確認に使うcaseを分け直します。
同じevidenceについて次の二条件を用意します。

- 元profileなし: 新規登録時の分類と文章生成を確認する。model自体の比較ではこちらを主に使う
- 元profileあり: 現在のQwen由来profileを維持または改善できるか確認する。現行DBからの移行適性に使う

どちらもmodelの生出力と保存規則を通した結果を分けます。元profileありの保存結果が似ていることだけを、
model出力が似ている根拠にはしません。

### 3. runtime設定

最初にmodelが指定を受け付けるか確認します。

- 同じ短い入力をreasoningなし、`low`、必要なら`high`で実行する
- request payloadだけでなくruntime logを確認する
- `applied`、`ignored`、`not_checked`をreportへ指定する
- final JSONへ到達する前に最大出力token数へ達する場合は、category比較へ進めない
- Responsesでreasoning itemとmessageが分かれる場合はmessageだけをJSONとして解析する

reasoningの指定が無視された構成は、指定が効いた構成と同条件として比較しません。指定できないこと自体が
そのmodel fileまたはruntime接続の制約なら、reasoning指定なしの別構成として記録します。

### 4. 少数caseで候補を選別する

共通promptと少数の調整用caseで、明らかに次段階へ進めない構成を先に分けます。旧category契約の一致数は
使わず、新しい契約、JSON/schema、identifier、速度、memoryで確認します。

- Qwen 3.6 35B: 現在の通常運用baseline
- Gemma 4 26B A4B MLX: 通常処理の第一候補
- Granite H-Tiny: 小memory・高速側の候補
- gpt-oss MLX: reasoningの適用を確認する別系列候補
- Agents A1 4B: 常駐background処理の候補
- Qwen 3.5 9B: 必要なら省memory側の比較対象

Agents A1は1件の応答速度だけで外しません。load後、処理中、長いprompt、idle時のmemory、別modelとの
同居によるmemory pressure、連続処理の処理量と失敗率を確認します。通常処理modelと同じ速度要件ではなく、
低優先度処理を小さいmemoryで継続できるかを判断します。

### 5. model別promptを調整する

modelごとの失敗傾向に対して、小さいsuffixを作って比較します。

- Gemma: categoryとidentifierの既知誤り
- Qwen: Qwenで再現したcategoryまたはidentifierの誤りだけ
- Granite: schema、enum、tagline長など、実際に多かった形式違反
- gpt-oss: reasoningとfinal messageの接続を確認した後、MLX等の成功する構成だけ

Gemma用suffixを他modelへそのまま適用して改善しなかったことを、そのmodelの限界とは扱いません。
一方、model固有suffixが長く複雑になり、production promptとの差分維持が難しい場合は運用上の不利として残します。

### 6. prompt調整に使っていないcase

promptを固定した後、調整に使っていない、期待categoryを人が確認したcaseを実行します。

確認項目:

- request成功
- schemaとenum
- evidence外URL、handle、DID、NSID
- categoryとreview結果の一致
- categoryとsummaryの整合
- service statusやtechnical interestの根拠外確定

一致数だけでなく、どのfieldがどう誤ったかを確認します。重大なidentifier追加やoperator判断の逆転は、
平均値で相殺しません。

### 7. 同じ入力の複数回実行

上位構成は同じcaseを複数回実行し、reportの`repeat_comparison`で次を確認します。

- categoryが変わらないか
- identifier集合が変わらないか
- schema成否が変わらないか
- summary、tagline、featuresがどの程度変わるか

文章の言い換えと、事実・category・identifierの変更を同じ差として扱いません。

### 8. 文章、重い入力、memory

Qwenとcandidateへ同じevidenceを渡し、model名を伏せてsummary、tagline、featuresを人が比較します。
少なくとも次を見ます。

- evidenceにある主要機能を落としていない
- schemaだけの説明へ不必要に縮退していない
- 日本語として読みやすい
- 断定の強さが根拠に合う
- current profileを維持すべき時に不要な書換えをしていない

短いcaseだけでなく、promptが長いcaseと以前数分かかったcaseを含め、中央値だけでなく最長時間と失敗も確認します。
通常処理候補ではpeak memoryとforeground利用への影響、常駐background候補ではidle時と連続処理時のmemoryも
確認します。

2026-07-26に次の重い入力を元profileなしで各2回実行した。case順はcorpus順になるため、最初のrequestは
処理中の速度集計から分離した。結果は「重い入力5件」に記録した。

| case | prompt bytes | 選定理由 |
| --- | ---: | --- |
| sifa-sdk (`proj:9dfd435e65fc:37c2a1bb156d`) | 34,307 | 最長級。旧Qwenで最初のrequestが131.5秒だったため、一時費用の再現確認 |
| Chive API (`proj:2617bd65b02d:71f51c5b1dbc`) | 34,302 | 未使用case中の最長。直近21件でも両modelの最長級 |
| game (`proj:ef465b04c876:5e621b6483db`) | 34,267 | labelに依存しない最長級case。長さによる失敗確認 |
| cocore (`proj:72ae05e894a4:0382d41c1c9d`) | 33,857 | 長い入力と根拠不足時の安全性を同時に確認 |
| Scott Polhemus Personal Blog Media (`proj:f55a50d13d8e:103099567e8b`) | 25,422 | 未使用caseで両modelとも遅め。`personal_tool`境界も確認 |

各modelについて、最初のrequest、最初を除いた中央値と累積時間、最長時間、成功・schema・identifier、promptと
completion tokenを記録する。model packageの小ささは常駐memoryには有利だが、そのまま処理時間の短さを意味しない。
現在のmodel IDもQwenはA3B、GemmaはA4Bを示しており、少なくともpackage全体の容量だけからGemmaの方が速いとは
予想しない。prefill/decode内訳が取れない間は、入力長と生成量のどちらが差を作ったかは未確認とする。

### 9. 複製DBでのenrich比較

正本DBへ書き込まず、同じproject集合をQwenとcandidateで実行します。

- project選択、source設定、prompt version、reasoning、最大出力token数を揃える
- collectorの外部状態差を避けるため、可能な範囲で同じ保存evidenceまたは近接した複製を使う
- category、service status、technical interest、manual review、profile更新、promotion blockerの差を確認する
- LLM以外のcollector失敗をmodel差として数えない

この確認で一括再分類や正本DB更新は行いません。

2026-07-26に複製を実行直前に2つ作り、次の8 projectを同じsnapshotから比較した。`goodfeeds`は同名projectが
あるためproject keyを固定した。結果は「新category契約による複製DB比較」に記録した。

| project | project key | 主な確認点 |
| --- | --- | --- |
| goodfeeds | `proj:58bb49f7d6fb` | Qwenで出た根拠外identifierが保存前検査で止まるか |
| ChartID | `proj:16fad81dda9d` | Qwenで出た根拠外NSIDと、元profileありの影響 |
| TitleGraph | `proj:0eebecf6994f` | Gemmaのcategory不一致と再実行時の文章差 |
| leaflet-md | `proj:a37ab47fe843` | `library` / `infra_tool`境界 |
| corvus | `proj:289b39e3d947` | schema sampleから`lexicon`へ過剰昇格しないか |
| Ozone | `proj:4a8e71e4fc29` | 両modelの文章が同じになる正常対照 |
| atproto.dart | `proj:8829cf936560` | `library`の正常対照 |
| Chive API | `proj:2617bd65b02d` | 長いevidenceでの通常enrich時間と保存結果 |

合格条件は、Gemmaで根拠外identifier、人が確定した判断やpromotion blockerの逆転、重大なcategory・status
誤判定が0件で、Qwenよりprofileの情報欠落を増やさないこととする。処理時間はcollector時間とLLM時間を分け、
最初のrequestを別記する。
Gemmaが安全検査で優位でも、8件の通常enrichでLLM処理時間が恒常的にQwenの1.5倍を超え、定期jobや手動
`enrich`の待ち時間として許容できないか、memory pressureによる失敗が出る場合は、既定変更ではなく現状維持
または対象を限定した役割別利用を検討する。1.5倍はこの8件で試用可否を見直すための暫定目安で、一般的な
性能基準とは扱わない。

### 10. 少数projectで試す

固定caseと複製DBでの比較を通った構成だけを、期間、対象project、停止条件を決めて試します。通常modelの
置換候補と、background処理だけを担当する候補を分けます。Qwenへ戻せる設定を維持し、試用結果を確認するまで
既定modelを変更しません。

## model構成を変更する判断

判断は次の三段階に分けます。

### 比較を続ける

次を満たした構成だけ、文章確認と複製DBでのenrich比較へ進めます。

- runtime設定の適用状態が分かっている
- requestとJSONが安定して完了する
- schema、enum、identifierでbaselineより重大な退行がない
- prompt調整に使っていない、期待categoryを人が確認したcaseでcategoryを確認できる
- 日常運用に耐えない時間またはmemory増加が見えていない

### 少数projectで試す

固定caseと複製DBでのenrich比較を通ったcandidateだけを、選んだ少数projectへ明示的に適用します。
Qwenとcandidateを同じ正本projectへ同時保存しません。比較出力はreportまたは複製DBに分け、candidateを
適用するprojectを明示します。

試用中はQwenを直ちに使える状態で残します。次のいずれかが起きた場合はcandidate適用を止めます。

- evidence外identifier、または人が確定した判断やpromotion blockerの逆転
- categoryやservice statusの重大な誤判定
- profileの情報欠落または不自然な大量書換え
- timeout、final JSON欠落、memory pressureの運用上の悪化

### 既定modelを変更する

試用対象の結果を人が確認し、上記の停止条件がなく、品質が同等以上で、速度またはmemoryに運用上の改善が
確認できた時だけ既定を変更します。変更後も、あらかじめ選んだproject群の`enrich`を完了して結果を確認するまで
Qwen設定をrollback用に残します。

同じ処理を常時二つのmodelへ重複実行する運用は既定にしません。一方、通常の調査と低優先度background処理を
別modelへ分ける構成は、常駐memory、処理量、品質、foregroundへの影響を確認できた場合に採用候補とします。
全面移行、Qwenとの期間限定試用、役割別併用、現状維持を明示的に比較して決めます。

## report比較の限界

`compare-llm-evaluation-reports`は次だけを機械比較します。

- corpus hashとcase/repeat集合
- 成功数
- schema通過数
- evidence外identifier件数
- prompt調整に使っていない、期待categoryを人が確認したcaseのcategory一致数
- 所要時間

文章品質、正本DBへ適用してよいか、通常運用modelを変更してよいかは自動決定しません。runtimeでのreasoning確認、
複数回実行、memory観測、文章確認、複製DBでのenrich比較、少数project試用が無ければ、その項目を未実施として返します。

## 新しいmodelまたはversionを試す時

1. 現行category契約と評価labelが一致していることを確認する
2. 元profileなし／ありの評価入力を用意する
3. model file、runtime、template、context、reasoning対応を確認する
4. production promptのまま少数caseを実行する
5. 既知の安全検査を通らない構成を外す
6. 必要な構成だけmodel別suffixを調整する
7. prompt調整に使っていない、期待categoryを人が確認したcaseを実行する
8. 同じ入力を複数回実行する
9. baselineとの文章比較、重い入力、memoryを確認する
10. 複製DBでQwenと候補modelを別々にenrichし、結果を比較する
11. 通常処理またはbackground処理として少数projectで試す
12. 全面移行、期間限定試用、役割別併用、現状維持を判断する

新しい誤りをfixtureへ追加する時は、再現に必要な最小evidenceと期待値を保存します。そのcaseをprompt修正に使った
場合は`used_for_prompt_adjustment=true`とし、次のmodel選定確認には別caseを使います。

## 文書への反映

- 実装済みの評価機能と確認済み範囲: `docs/status.md`
- 未完了の比較、複製DBでのenrich比較、試用: `docs/roadmap.md`
- 比較途中の条件、結果、解釈: 本文書
- 次スレッドが直ちに実行する作業: `docs/thread-handoff.md`

モデル変更後は、通常運用model、確認した範囲、rollback用設定を`docs/status.md`へ短く記録します。
