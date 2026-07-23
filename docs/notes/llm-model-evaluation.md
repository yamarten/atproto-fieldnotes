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

- `export-llm-evaluation-corpus`
  - 指定DBをread-onlyで開き、保存済みLLM evidenceから固定caseを出力する
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
- Gemma 4 26B A4B MLXは次の比較へ進める第一候補
- `tests/fixtures/llm_evaluation_prompt_gemma4_26b_category_gates_v3.txt`はGemmaの既知誤分類を
  見ながら調整した評価用suffixで、production promptには未反映

未完了:

- prompt調整に使っていない、期待categoryを人が確認したcaseでのGemma確認
- summary、tagline、featuresのQwen/Gemma比較
- 同じ入力を複数回実行した時の重要field差
- 重い入力を含む比較
- 複製DBでQwenとGemmaを別々にenrichした結果の比較
- 少数projectでのGemma試用
- peak memory、swap、prefill、decodeのrunner自動記録

したがって、現時点ではGemmaを通常運用modelとして採用済みとは扱いません。

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

現在の`tests/fixtures/llm_evaluation_category_labels_v2.json`にある21件は、すべてGemma用suffixの
調整時に参照済みなので`true`です。次のmodel選定確認には、既存corpusの未label caseまたは新たにexportした
caseをevidenceだけからreviewし、prompt修正前に期待categoryを固定します。

## 確認の順序

### 1. runtime設定

最初にmodelが指定を受け付けるか確認します。

- 同じ短い入力をreasoningなし、`low`、必要なら`high`で実行する
- request payloadだけでなくruntime logを確認する
- `applied`、`ignored`、`not_checked`をreportへ指定する
- final JSONへ到達する前に最大出力token数へ達する場合は、category比較へ進めない
- Responsesでreasoning itemとmessageが分かれる場合はmessageだけをJSONとして解析する

reasoningの指定が無視された構成は、指定が効いた構成と同条件として比較しません。指定できないこと自体が
そのmodel fileまたはruntime接続の制約なら、reasoning指定なしの別構成として記録します。

### 2. prompt調整

modelごとの失敗傾向に対して、小さいsuffixを作って比較します。

- Gemma: categoryとidentifierの既知誤り
- Qwen: Qwenで再現したcategoryまたはidentifierの誤りだけ
- Granite: schema、enum、tagline長など、実際に多かった形式違反
- gpt-oss: reasoningとfinal messageの接続を確認した後、MLX等の成功する構成だけ

Gemma用suffixを他modelへそのまま適用して改善しなかったことを、そのmodelの限界とは扱いません。
一方、model固有suffixが長く複雑になり、production promptとの差分維持が難しい場合は運用上の不利として残します。

### 3. prompt調整に使っていないcase

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

### 4. 同じ入力の複数回実行

上位構成は同じcaseを複数回実行し、reportの`repeat_comparison`で次を確認します。

- categoryが変わらないか
- identifier集合が変わらないか
- schema成否が変わらないか
- summary、tagline、featuresがどの程度変わるか

文章の言い換えと、事実・category・identifierの変更を同じ差として扱いません。

### 5. 文章と重い入力

Qwenとcandidateへ同じevidenceを渡し、model名を伏せてsummary、tagline、featuresを人が比較します。
少なくとも次を見ます。

- evidenceにある主要機能を落としていない
- schemaだけの説明へ不必要に縮退していない
- 日本語として読みやすい
- 断定の強さが根拠に合う
- current profileを維持すべき時に不要な書換えをしていない

短いcaseだけでなく、promptが長いcaseと以前数分かかったcaseを含め、中央値だけでなく最長時間と失敗も確認します。

### 6. 複製DBでのenrich比較

正本DBへ書き込まず、同じproject集合をQwenとcandidateで実行します。

- project選択、source設定、prompt version、reasoning、最大出力token数を揃える
- collectorの外部状態差を避けるため、可能な範囲で同じ保存evidenceまたは近接した複製を使う
- category、service status、technical interest、manual review、profile更新、promotion blockerの差を確認する
- LLM以外のcollector失敗をmodel差として数えない

この確認で一括再分類や正本DB更新は行いません。

## 通常運用modelを変更する判断

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

- evidence外identifierまたはoperator guardの逆転
- categoryやservice statusの重大な誤判定
- profileの情報欠落または不自然な大量書換え
- timeout、final JSON欠落、memory pressureの運用上の悪化

### 既定modelを変更する

試用対象の結果を人が確認し、上記の停止条件がなく、品質が同等以上で、速度またはmemoryに運用上の改善が
確認できた時だけ既定を変更します。変更後も、あらかじめ選んだproject群の`enrich`を完了して結果を確認するまで
Qwen設定をrollback用に残します。

常時二つのmodelを実行する運用は既定にしません。二重実行の時間と比較作業が継続的な価値を持つ具体例が
見つかった場合だけ再検討します。

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

1. model file、runtime、template、context、reasoning対応を確認する
2. production promptのまま少数caseを実行する
3. 既知の安全検査を通らない構成を外す
4. 必要な構成だけmodel別suffixを調整する
5. prompt調整に使っていない、期待categoryを人が確認したcaseを実行する
6. 同じ入力を複数回実行する
7. baselineとの文章比較と重い入力を確認する
8. 複製DBでQwenと候補modelを別々にenrichし、結果を比較する
9. 少数projectで試す
10. 通常運用modelを変更するか判断する

新しい誤りをfixtureへ追加する時は、再現に必要な最小evidenceと期待値を保存します。そのcaseをprompt修正に使った
場合は`used_for_prompt_adjustment=true`とし、次のmodel選定確認には別caseを使います。

## 文書への反映

- 実装済みの評価機能と確認済み範囲: `docs/status.md`
- 未完了の比較、複製DBでのenrich比較、試用: `docs/roadmap.md`
- 比較途中の条件、結果、解釈: 本文書
- 次スレッドが直ちに実行する作業: `docs/thread-handoff.md`

モデル変更後は、通常運用model、確認した範囲、rollback用設定を`docs/status.md`へ短く記録します。
