# LLM model評価

この文書は、通常`enrich`または低優先度処理で使うLLMを新しいmodelやversionへ変更する時の確認方法を定めます。実用上の評価原則は、promptやLLM出力の利用方法を変える比較にも適用します。確認する経路は変更対象に合わせ、model移行用の全手順を文書だけの再評価へ機械的に課しません。

## 目的

実際に使う構成と保存結果に基づいて次を判断します。

- 現在のmodelとの比較を続けるか
- 対象を限定して正本DBで試すか
- 既定modelを変更するか
- 通常`enrich`と低優先度処理でmodelを分けるか

## 実用上の評価原則

最優先は、紹介したい対象の取りこぼしと、ノイズが多すぎて対象が埋もれることの両方を避けることです。機能説明の正確さも重要ですが、まず候補の選定・表示・人の判断への影響を見て、その次に紹介文の下敷きや後日の再利用における補完・訂正負担を評価します。本文の完全性や個別fieldの一致を、この目的の代わりにしません。無人公開できる完成文や、次の調査箇所を指示する能力は必須ではありません。

評価前に、紹介対象になりそうな度合い・優先順と出力の利用先を確かめます。紹介する／しないの二値だけでなく、Promotionで先に見たい程度や相対的な表示順も扱います。この度合いは校正された確率ではなく、情報不足とも区別します。Lexiconの存在と技術的な面白さ、実用性と触りやすさを同一視しません。2026-09-23の暫定合意による優先順と既存方式の再評価は[再評価資料](../../notes/description-refresh-quality.md#価値基準と利用場面)を参照してください。点数・閾値・推薦規則をこの原則だけで変更せず、利用者の価値基準の表現も運用に照らして見直せるものとします。

| 利用先 | 比較する効果と誤り |
| --- | --- |
| 自動確定・保存文面の自動上書き | 誤同一視、人の判断の逆転、紹介対象としての位置づけや重要な特徴の消失を確認する。既存の編集保護を維持し、参照IDの一致を意味の保証にしない |
| 順位・候補表示 | 同じ確認範囲で、より紹介対象になりそうな候補に出会えるかを見る。除外・過度な順位低下と、低優先候補が上位を占める埋没を両方確認する。低優先候補を直ちに対象外やノイズとせず、同程度の候補の小さな並替えと、大きく関心の異なる候補の逆転を分ける。候補数を増やすだけ、すべてunknown／保留にするだけでは改善としない |
| 根拠付き補助表示 | 対象の位置づけや特徴を理解する助けと、誤った手掛かりを照合する負担を比べる。細かい機能とcollectionの対応まで分からなくても、選定に十分な材料が得られればよい |
| 説明の更新案 | 紹介文を組み立てたり後で対象を思い出したりする助けを比べる。用途・時点・特徴の誤りと、用途を変えない詳細の省略や表記修正を同じ重さにしない。修正文字数だけでなく、誤りや欠落を発見する負担も含める |

生出力、適用検査後の保存結果、実際の一覧や表示への影響を分けます。誤った出力が保存側で拒否された場合、その訂正を利用者の負担軽減として数えません。説明だけを比較して、実際には確認していない推薦順位の改善・悪化を断定しません。検索対象外の説明を改善しても、検索で発見しやすくなる効果は別途接続・確認が必要です。

比較材料には関心の高い対象、低い対象、判断が揺れる中間的な対象とノイズになり得る対象を含め、現行方式にも同じ基準を適用します。未紹介を負例にせず、Agentの期待値や過去出力を人間goldへ昇格しません。終了・機能変更などの難例は失敗の仕方を確かめる材料で、通常候補全体の代表標本とは区別します。既存一覧では上位だけでなく、関心の高い既知対象が除外・過度に後順位になっていないかも確認し、材料が不足している対象をノイズと断定しません。

人へ網羅的な採点や日常の記録習慣を求めず、Codexが原文・出力・利用先の対照を選び、判断が変わる交換条件だけを確認します。全体の捕捉率・ノイズ率を述べるには対象集合と判断根拠が必要です。実作業時間がなければ負担は推定とし、少数の対話から省力率を計算しません。保存保護の成功と、選定品質・説明品質の合格も別々に報告します。

## 用語

- baseline: 現在の通常運用で使っている構成
- candidate: baselineと比較する構成
- 実行構成: model、配布形式、量子化、runtime、backend、chat template、prompt、reasoning設定を合わせたもの。一つでも変われば別の構成として記録する
- 調整用case: model別promptの修正理由または修正後の確認に使ったcase
- prompt固定後の確認用case: そのpromptの文言を決める時に出力を見ていないcase。model選定時のcategory確認に使う
- 元profileなし: 新規projectに近い条件。保存済みLLM profileを入力へ含めない
- 元profileあり: 既存projectの更新に近い条件。現在のprofileを入力へ含める
- triage: project全体の根拠から説明・分類・要確認事項を補うLLM処理。[説明・評価仕様](../specs/project-evaluation.md#llm出力の適用境界)で適用条件を定める
- corpus: 再利用できる固定入力などを集めた評価caseの集合。人が正しいと判断した期待値をgold labelと呼び、過去のmodel出力は比較用のreferenceとして区別する

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

model別の追加規則（prompt suffix）の内容hashは、UTF-8本文の前後の空白を除いてSHA-256を計算します。固定評価と通常`enrich`は同じ計算方法を使い、requestが失敗した場合も使用したhashを評価reportまたは調査実行記録のmetricsへ残します。

requestへreasoning値を送っただけでは適用済みと扱いません。runtime logまたはmodel設定で確認し、`applied`、`ignored`、`not_checked`をreportへ記録します。指定できない構成は、指定なしの別構成として扱います。

## 評価データ

### 作業中に得た事例の保存

model比較に限らず、通常の調査・不具合修正・利用者からの訂正で、triageまたはrepository analysisの誤判定、判断が分かれる例、有用な正例を得たら、作業者が保存を検討します。誤りだけを集めず、同種事例の重複を避けます。全件収集や追加実験は不要です。保存のためだけに元タスクを拡大せず、手元の入力が履歴削除や一時資料のcleanupで失われる前に扱います。

1. [非公開事例の保存仕様](../specs/llm-case-store.md#保存先と境界)にある共通保存先の一覧を確認し、同じ事例があれば新規複製ではなく判断eventの追記を選びます。
2. 当時の入力・出力・実行条件と出典をdraftへ写します。再構成・欠落は明記し、部分判断や未確認でも保存できます。Codexの推定を人間goldへ昇格させず、人による確認を待つためだけに保存を先延ばししません。
3. 認証情報等の混入を点検し、`register-llm-case`で登録後、Codexが`show-llm-case`と`verify-llm-cases`で保存内容と伏字による欠落を確認します。個人用・非公開の保存と参照は人手確認待ちにせず、公開資料の原文・project名・通常の作業記録を公開用の基準で削りません。Codexによる確認では`human_reviewed=true`や`snapshot_reviewed`を設定しません。人が実際に確認した部分だけ、その根拠とともに記録します。
4. promptや規則の調整に使ったら、同じ事例へ`adjustment_used`を追記します。後の訂正もeventとして残し、再利用時は初期snapshotではなく`effective`の判断・利用状態を確認します。

作業の引き継ぎには保存したcase IDと、残る確認事項があればそれを含めます。保存先への書込み権限がなければ必要な承認を求め、無断で別の保存先へ分散させません。保存できなかった場合は未保存と理由を明記し、必要な一時入力を残件が解消する前に削除しません。事例の登録は学習・評価への採用ではなく、公開fixtureへの転用や人手goldとしての利用には別の確認が必要です。

### 固定評価用corpus

`export-llm-evaluation-corpus`は指定DBをread-onlyで開き、保存済みevidenceから固定caseを出力します。corpus選抜ではpromptの長さごとの区分（bucket）と保存済みcategoryを分け、既定で`category=library`を最低8件優先します。保存済み出力は比較referenceとして保持します。前回LLM出力だけを根拠とするfieldは、次のmodel入力へ戻しません。現在のprofileは観測由来の根拠（base evidence）から分けて保持し、`evaluate-llm-corpus --current-profile-mode without|with`で入力条件を切り替えます。

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

短い入力でrequest成功、最終JSONの受信、reasoning対応、出力token上限への到達の有無を確認します。runtimeが実際に適用したcontext長や並列数（parallel）を取得できる場合は、要求値と分けて記録します。

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

通常処理候補は処理後のmemory使用量（footprint）と手元の他アプリ操作への影響、常駐候補は待機時・連続処理時・他modelとの同居時のmemory使用量と挙動を確認します。配布packageの容量、実行中のmemory使用量、処理速度はそれぞれ記録します。

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

固定caseと複製DBを通ったcandidateだけを、対象、件数または期間、停止条件を決めて試します。この試用は正本DBへ通常`enrich`を書き込むため、実行前に別途承認を得ます。baselineを直ちに使える設定で残し、candidateを適用するprojectを明示します。

次が起きた場合は試用を止めます。

- evidenceにないidentifierの保存
- 人が確定した判断やpromotion blockerの逆転
- categoryやservice statusの重大な誤判定
- profileの欠落・書換えにより紹介対象の位置づけや重要な特徴を失う、または説明を広範囲に作り直す必要が生じる
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
- `export-repository-analysis-corpus`: 保存済み調査実行記録から、repositoryのrevisionを固定した解析素材をread-onlyで取り出す
- `evaluate-repository-analysis-corpus`: 一つの実行構成でrepository解析と保存前検査を行う
- `compare-repository-analysis-reports`: 同じmaterialとrepeatの機械検査結果を比較する
- `export-relation-assessment-corpus`: 関係の意味判定が必要な`ASSESS`区分のpairと保存済みの本文抜粋をDBからread-onlyで固定する
- `export-recollection-relation-assessment-corpus`: 取得上限付きの再収集reportで要確認となった帰属主張から、confirmed構成要素と完全一致する起点に由来する意味判定caseだけを固定する。page帰属が未確定のclaimはLLMへ渡さない
- `evaluate-relation-assessment-corpus`: project-level triageと分離した一つの方向付きpredicateを、入力内根拠だけで評価する

機械比較が扱うのは成功、schema、根拠外identifier、確認用caseのcategory一致、所要時間です。文章品質と、正本DBへの試用・既定modelの変更可否は、人が結果を読んで判断します。最大memory使用量、swap、入力処理（prefill）、出力生成（decode）のrunnerによる自動記録は未接続です。

長時間評価は開始時と各request完了時に途中reportを書き出し、正常終了時だけ`status=complete`にします。途中reportを完走済みの比較と混同しません。

## 文書への反映

評価後は、[status](../status.md)の採用状態・確認範囲・制約を更新します。候補固有の未決事項や試用停止条件は[再開材料](../../notes/llm-model-evaluation.md)へ必要な間だけ残します。既定modelを変更する場合は設定とbaselineへ戻す方法を確認し、試用中に必要な切り戻し情報を完了記録とともに削除しません。
