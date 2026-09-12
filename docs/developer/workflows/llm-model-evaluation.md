# LLM model評価

この文書は、通常`enrich`または低優先度処理で使うLLMを新しいmodelやversionへ変更する時の確認方法を定めます。

## 目的

model名や単一のscoreではなく、実際に使う構成と保存結果に基づいて次を判断します。

- 現在のmodelとの比較を続けるか
- 対象を限定して正本DBで試すか
- 既定modelを変更するか
- 通常`enrich`と低優先度処理でmodelを分けるか

## 用語

- baseline: 現在の通常運用で使っている構成
- candidate: baselineと比較する構成
- 実行構成: model、配布形式、量子化、runtime、backend、chat template、prompt、reasoning設定を合わせたもの。一つでも変われば別の構成として記録する
- 調整用case: model別promptの修正理由または修正後の確認に使ったcase
- prompt固定後の確認用case: そのpromptの文言を決める時に出力を見ていないcase。model選定時のcategory確認に使う
- 元profileなし: 新規projectに近い条件。保存済みLLM profileを入力へ含めない
- 元profileあり: 既存projectの更新に近い条件。現在のprofileを入力へ含める

調整用caseはlabelの`used_for_prompt_adjustment=true`、prompt固定後の確認用caseは`false`とします。確認用caseの出力を見てpromptを変更した場合、そのcaseは調整用へ移し、次の確認には別caseを用意します。

## 実行構成の記録

reportには少なくとも次を記録します。

- model IDと取得元
- 配布形式、量子化、model fileまたはpackageの識別情報
- runtime名、version、backend
- chat templateまたはruntime既定template
- context、parallel、最大出力token数
- API style
- prompt本体のhash、model別suffixの名前とhash
- reasoning要求値
- reasoningをruntimeが適用したか、無視したか、未確認か
- corpus hash、case ID、repeat回数
- git commitとdirty状態

suffixの内容hashは、UTF-8本文の前後の空白を除いてSHA-256を計算します。固定評価と通常`enrich`は同じ計算方法を使い、requestが失敗した場合も使用したhashをrun metricsへ残します。

requestへreasoning値を送っただけでは適用済みと扱いません。runtime logまたはmodel設定で確認し、`applied`、`ignored`、`not_checked`をreportへ記録します。指定できない構成は、指定なしの別構成として扱います。

## 評価データ

`export-llm-evaluation-corpus`は指定DBをread-onlyで開き、保存済みevidenceから固定caseを出力します。corpus選抜はprompt長bucketと保存済みcategoryを分け、既定で`category=library`を最低8件優先します。保存済み出力は比較referenceでありgold labelではありません。前回LLM出力だけを根拠とするfieldは、次のmodel入力へ戻しません。現在のprofileはbase evidenceから分けて保持し、`evaluate-llm-corpus --current-profile-mode without|with`で入力条件を切り替えます。

期待categoryを付ける前に、現在のcategory契約と入力evidenceだけで人が判断できるかを確認します。外部情報を見れば判断できても固定evidenceから導けないcaseは、modelのcategory成績には使いません。外部情報不足はcollector契約の問題として分けます。

通常`enrich`が複数の処理でLLMを使う場合、入力と出力schemaが異なる処理を一つの成績へまとめません。現在の`evaluate-llm-corpus`が固定しているのはproject-level triageです。repository analysisは、revisionへ固定したrepository material、出力内の根拠位置、観測、capability、collectionを別の固定caseで確認します。category用のmodel別suffixはrepository analysisへ適用しません。

比較で実際に使った現行Qwen向けsuffixは[`prompts/model-rules/qwen-3.6-35b-a3b.txt`](../../../prompts/model-rules/qwen-3.6-35b-a3b.txt)に置きます。現行Qwenのmodel IDでは、通常`enrich`と定期jobがこのファイルを自動選択します。CLIの`--llm-triage-prompt-suffix-file`、環境変数`ATPROTO_SURVEY_LLM_TRIAGE_PROMPT_SUFFIX_FILE`、model IDによる既定値の順に優先します。別modelにはQwen用規則を適用せず、そのmodelの調整用caseとprompt固定後の確認用caseを経てから別の追加規則を明示します。

repository analysisを含む二段階の確認では、`evaluate-llm-corpus --repository-analysis-report REPORT`を使います。completeなrepository analysis reportのrepeat 0にある正規化済み結果を、同じproject keyのtriage caseへ一時的に重ねます。正本DBと元corpusは変更しません。report hash、上流model、適用case数をtriage reportへ記録します。

LLMを使う処理を追加した時、またはそのpromptや後段の利用方法を変えた時は、その処理の出力と、後段のtriage・保存結果への影響を試用前に確認します。以前のtriage比較だけで新しい処理まで確認済みとは扱いません。

## 確認の順序

### 1. category契約を確認する

[project-record.md](../specs/project-record.md#category)を正本とし、production prompt、保存処理、評価検査、labelが同じ意味を使っていることを確認します。category契約を変えた場合、旧category一致数を新しい選定根拠へ直接使いません。

### 2. 元profileなし／ありを用意する

model自体の比較は元profileなしを主に使い、元profileありは現在のDBからの移行適性に使います。生出力と保存規則を通した結果を分け、保存後の文章が似たことだけをmodel出力が似た根拠にはしません。

### 3. runtime設定を確認する

短い入力でrequest成功、JSON到達、reasoning対応、最大出力到達の有無を確認します。runtimeが実際に適用したcontextやparallelを取得できる場合は、要求値と分けて記録します。

### 4. 少数caseで候補を絞る

共通production promptと少数の調整用caseで、request、JSON/schema、enum、根拠外identifier、category、速度、memoryを見ます。通常処理候補と低優先度処理候補は、同じ速度要件で足切りしません。

### 5. model別promptを調整する

実際に再現した失敗だけを対象に、小さいsuffixを作ります。他model用suffixをそのまま付けた結果だけでcandidateを外しません。一方、suffixが長くなりproduction promptとの差分維持が難しい場合は、運用上の不利として残します。

### 6. prompt固定後の確認用caseを実行する

次をcase単位で確認します。

- request成功
- JSON/schemaとenum
- evidenceにないURL、handle、DID、NSID
- categoryと人の期待値
- categoryとsummaryの整合
- service statusやtechnical interestの根拠外確定

重大なidentifier追加や、人が確定した判断の逆転は、平均値で相殺しません。

repository analysisでは、request成功とschemaに加えて、summaryとcapabilityが入力時に割り当てた根拠番号だけを参照すること、保存時に対応する根拠位置へ戻ること、利用者向け本文へ根拠番号を混ぜないことを確認します。観測はpredicate・basis・根拠を固定したcandidate ID、collection利用はcase-sensitive NSID・operation・根拠を固定したcandidate IDで検査し、候補外ID、根拠のない観測、documentation URLを保存しません。候補が空のcaseも固定corpusへ含めます。同じmaterialをbaselineとcandidateへ渡し、解析結果を含むtriageも比較します。

### 7. 同じ入力を複数回実行する

category、identifier集合、検査成否、tagline、summary、featuresの変化を確認します。文章の言い換えと、事実、category、identifierの変更を同じ差として扱いません。

一度でもfinal JSONの構文エラーが出た処理は、promptとmaterialを固定して少なくとも5回反復します。1回でも再発すれば少数試用へ進めません。失敗reportには応答本文を残さず、失敗段階、byte数、SHA-256、JSON構文エラー位置、開始・終了記号、code fenceの有無だけを記録し、出力上限による切断と内部の形式違反を区別します。

### 8. 文章、重い入力、memoryを確認する

文章はmodel名を伏せ、主要機能の欠落、根拠外の断定、日本語、情報量を人が比較します。入力が長いcaseと以前時間がかかったcaseを含め、最初のrequest、最初を除いた時間、中央値、最長時間を分けます。同じpromptの反復ではruntime cacheが効く可能性があるため、1巡目と2巡目も分けます。

通常処理候補は処理後のfootprintとforeground利用への影響、常駐候補はidle時、連続処理時、他modelとの同居を確認します。package容量だけを常駐memoryや処理速度の代わりにしません。

### 9. 複製DBで通常`enrich`を比較する

正本DBの同じsnapshotを2つ作り、baselineとcandidateを別々に通常`enrich`へ通します。

- project、source設定、prompt version、reasoning、最大出力token数を揃える
- category、service status、technical interest、manual review、profile、identifier、promotion blockerを比較する
- planning、repository analysis、triageのrequest数と時間を分け、collector時間とも分離する
- repository analysisが走る場合は同じrevisionとmaterial hashを使い、保存された観測、capability、collectionと後段のtriageを比較する
- 外部collectorの取得失敗や応答時間差をmodel差に数えない
- 正本DBは更新しない

DBコピーは比較直前に作り、結果を文書へ記録してintegrityとforeign keyを確認した後に削除します。

### 10. 正本DBの少数projectで試す

固定caseと複製DBを通ったcandidateだけを、対象、件数または期間、停止条件を決めて試します。これは評価用copyではなく正本DBへ通常`enrich`を書き込むため、実行前に別途承認を得ます。baselineを直ちに使える設定で残し、candidateを適用するprojectを明示します。

次が起きた場合は試用を止めます。

- evidenceにないidentifierの保存
- 人が確定した判断やpromotion blockerの逆転
- categoryやservice statusの重大な誤判定
- profileの情報欠落または不自然な大量書換え
- timeout、final JSON欠落、memory pressureの悪化

既知の正解を確認するprojectと、model調整に使っていないprojectを分けます。既知projectは保存経路の確認に使い、その成績を未知projectでの性能として数えません。

## model変更の判断

### 比較を続ける条件

- runtime設定の適用状態が分かっている
- requestとJSONが安定して完了する
- schema、enum、identifierでbaselineより重大な退行がない
- prompt固定後の確認用caseでcategoryを確認できる
- 日常運用に耐えない時間またはmemory増加が見えていない

### 少数試用へ進む条件

- 固定caseと複製DBの両方で保存上の重大な退行がない
- 保存結果へ影響する全LLM処理について、現在のpromptと出力schemaで重大な退行がない
- 品質上の改善理由が具体的にある
- 速度とmemoryが許容範囲にある
- 試用対象、停止条件、baselineへ戻す方法が決まっている

### 既定modelを変更する条件

少数試用を人が確認し、停止条件に該当せず、保存品質が同等以上で、速度またはmemoryを含む運用上の利点がある時だけ変更します。変更後も、あらかじめ選んだproject群を確認するまでbaseline設定を残します。

全面移行、期間限定試用、役割別利用、現状維持を明示的に比較します。同じ処理を常時二modelへ重複実行する運用は既定にしません。

## commandと限界

- `export-llm-evaluation-corpus`: 固定corpusを作る。DBはread-only
- `evaluate-llm-corpus`: 一つの実行構成を固定caseへ適用し、構造化reportを作る
- `compare-llm-evaluation-reports`: 同じcorpus、case、repeatの機械検査結果を比較する
- `export-repository-analysis-corpus`: 保存済みrunからrevision固定のrepository materialをread-onlyで取り出す
- `evaluate-repository-analysis-corpus`: 一つの実行構成でrepository解析と保存前検査を行う
- `compare-repository-analysis-reports`: 同じmaterialとrepeatの機械検査結果を比較する
- `export-relation-assessment-corpus`: 現在`ASSESS`へroutingされるpairと保存済みraw excerptをDBからread-onlyで固定する
- `export-recollection-relation-assessment-corpus`: bounded再収集reportのreview claimから、exactなconfirmed component由来の意味判定caseだけを固定する。page帰属が未確定のclaimはLLMへ渡さない
- `evaluate-relation-assessment-corpus`: project-level triageと分離した一つの方向付きpredicateを、入力内根拠だけで評価する

機械比較が扱うのは成功、schema、根拠外identifier、確認用caseのcategory一致、所要時間です。文章品質、正本DBへ適用してよいか、既定modelを変更してよいかは自動決定しません。peak memory、swap、prefill、decodeのrunner自動記録も未接続です。

長時間評価は開始時と各request完了時に途中reportを書き出し、正常終了時だけ`status=complete`にします。途中reportを完走済みの比較と混同しません。

## 文書への反映

評価後は、比較条件、数値、現在の解釈を[比較記録](../../notes/llm-model-evaluation.md)へ残します。既定modelを変更した場合は、[status](../../status.md)のmodel構成・確認範囲を更新し、詳細な設定とbaselineへ戻す方法を比較・適用記録に残します。
