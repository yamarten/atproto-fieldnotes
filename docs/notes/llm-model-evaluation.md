# Model評価の保留・再開材料

通常enrichの置換候補と、低優先度処理用の常駐候補について、試用を止めた理由と再開に必要な確認を記します。

以下の保留は2026-07月の比較に基づきます。試用再開には、現行のmodel・runtime・repository解析契約で改めて固定入力の反復評価が必要です。

## Gemmaによる通常enrichの置換試用

Gemma 4 26B A4B MLX 4bit QATは、project-level triageだけの初期評価では候補に残りましたが、repository解析を含む通常enrichの複製DB試行でJSON失敗が再現したため、正本DBでの置換試用を停止しました。

停止判断に使った`goat`の固定materialではGemmaのJSON成功は10回中5回、Qwen 3.6 35B A3Bは10回中10回でした。ただし根拠不足の観測の除外が両modelで発生し、保存規則をすべて通る結果は両方0回です。QwenはJSON応答の安定性を理由に維持しています。解析内容の品質は引き続き評価対象です。

比較時はLM Studio 0.4.20+1、Chat Completions、reasoningなし、最大出力2,048 tokenでした。runtimeが返したcontextはQwen 183,296、Gemma 226,304、parallelは双方4で、要求した32,768は反映されていませんでした。再開時には要求値と実効値を分けて確認します。

再評価はmodel、runtime、promptの変更後に行い、次を順に確認します。

1. 失敗した`goat`の保存済み入力と適用policyを確認し、固定入力の反復でJSON構文エラーの再発と根拠不足の観測を評価する。当時の入力を利用できない場合は、別caseとして評価し、旧caseの再現確認が残ることを記録する。
2. 調整に使っていないrepositoryと最新DB複製で、repository解析から後段triage・保存まで確認する。旧契約の結果と現行契約の結果を分ける。
3. 条件を通った場合だけ、[少数projectでの試用手順](../developer/workflows/llm-model-evaluation.md#10-正本dbの少数projectで試す)に従って対象・停止条件・baselineへの戻し方・backup保持期限を定め、正本DBの少数試用を別途承認の対象にする。

## Agents A1による低優先度処理

Agents A1 4B GGUF Q8_0は、小型modelを追加常駐させる候補でした。goodfeeds、Chive API、atproto.dartを各2回確認した範囲では品質の機械検査を通りましたが、速度上の利点はなく、自動切替でTTL中のQwenが退避しました。

当時の実効contextは262,144、parallelは4、手動観測footprintは約9.7 GBでした。明示load後に複数modelを同居できないと証明したものではありませんが、通常APIの自動切替でQwenを残せなかったことが試用を止めた理由です。

明示loadに依存せずQwenと同居できるruntime構成が得られた時に、任せる低優先度処理を決め、同じcontext・parallel・連続処理・同居条件で品質、処理時間、memoryを比較します。常駐コストは配布packageのサイズに加え、実際のmemory使用量と共存時の挙動で判断します。
