# LLM Model Evaluation Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。LLM の恒久的な位置づけは
> [../investigation-model.md](../investigation-model.md)、`enrich` の接続状況は
> [../registry-internals.md](../registry-internals.md) を参照してください。

## 目的

enrich で使う LLM を変更した時に、結果がどの程度変わるかを再利用可能な形で測る。
将来の新モデル移行判断では、単発の印象ではなく、固定 corpus、同一 prompt、同一 evidence、
同じ採点軸で比較する。現時点の主目的は、ローカル運用で全件 enrich に 30 時間以上かかる状態を
短縮し、メモリ逼迫を下げつつ、出力品質を維持または改善できるかを判断すること。

非目標:

- LLM 単独で ownership、confirmed identifier、service 到達確認を確定する
- benchmark のために通常 DB を直接更新する
- 最新モデル名を恒久仕様として固定する
- 外部 API 利用を前提に通常運用を再設計する

## 背景

ユーザー報告:

- 現在の主運用 LLM は `Qwen3.6 35B` の Q4 量子化
- メモリが逼迫している
- 全件 enrich には 30 時間以上かかっている
- 優先評価軸は次の 3 つ
  - 出力が良くなるか
  - 速くなるか
  - 省メモリになるか
- 外部 API は主線ではない
  - 低コストで劇的な改善が見込める場合だけ比較候補に入れる
- 最悪は現状維持でよい
  - したがって探索は、急いでQwen系の小型へ置き換えるより、現行baselineを維持しながら別系列も最初から比較対象に含める
- 第一段階は固定 evidence の軽い比較でよい
  - 将来は、全件ではなくても重い実運用相当の比較を行えるようにする
- 現行の通常時は 1 project あたりおおむね 30 秒程度
  - たまに数分止まることがある
  - GC、memory pressure、swap、runner 側の stall など runtime-level 要因の可能性があるが未確認

未検証:

- 上記の実測値はこの文書作成時点では code / log / benchmark から再確認していない
- `Qwen3.6 35B` の正確な model card、runner、quantization variant、context 設定、thinking 設定は未確認
- 30 時間超の内訳が LLM decode、prompt prefill、外部 collector、service probe、DB write のどれに寄っているかは未計測
- 数分単位の停止が GC、swap、runner 内部 queue、model reload、context/KV cache、外部 collector timeout のどれに由来するかは未計測

## 現在地

実装済み:

- `enrich` は LLM を 2 箇所で使う
  - investigation query planning
  - 最終 triage / editorial 補完
- LLM transport は command 方式または OpenAI-compatible endpoint 方式
- endpoint 方式は `chat/completions`、`responses`、`completions` の順序付き候補を扱える
- LLM meta は `transport`、`model`、`api_style`、`reasoning_effort`、`prompt_version`、
  `generated_at` を保存する
- 最終 triage の `llm_source.evidence` には prompt に渡した縮約 evidence を保存する
- LLM failure 時も deterministic plan や他 collector は継続する

未接続:

- 複数モデルを同じ evidence に対して一括実行する harness
- LLM 出力差分を structured report として保存する仕組み
- triage 直前の project snapshot 保存
- Anthropic / Gemini などの first-party API adapter
  - 現状で使う場合は command wrapper か OpenAI-compatible gateway が必要
- model/prompt/cost/latency の集計 dashboard

未検証:

- 現在の通常 DB で、モデル差が category、service_status、technical_interest、promotion readiness に
  どの程度影響するか
- 同一モデル・同一 prompt の繰り返し実行での揺れ
- reasoning effort の差が JSON 安定性や過剰推測に与える影響
- thinking / non-thinking 切替、出力 token 上限、prompt 縮約が全件 runtime に与える影響
- prompt tuning の必要量が model 系列ごとにどれくらい違うか

## 2026-07-01 preliminary measurement

実装済み / 実行済み:

- 通常DBを `mode=ro` で開き、保存済み `investigation_runs.output_project_json` の
  `llm_source.evidence` から固定 evidence prompt を再構成した
- 現行 `.envrc` の local endpoint / `qwen/qwen3.6-35b-a3b` / reasoning effort `none` で、
  短・中・長の3件を1回ずつ実行した
- report は `.tmp/llm-fixed-evidence-microbench.json` と
  `.tmp/llm-fixed-evidence-inventory.json` に保存した

動作確認済み:

- endpoint方式は現行環境では `responses` API として解決された
- 3件すべて JSON parse は成功した
- 直近500 run由来の候補 prompt 長:
  - min: 6,734 chars
  - median: 9,951 chars
  - max: 128,961 chars
- 全保存run由来の候補 prompt 長:
  - candidate count: 9,109
  - min: 6,567 chars
  - median: 8,449 chars
  - max: 161,798 chars
  - `<10k`: 6,380
  - `10k-19,999`: 1,848
  - `20k-49,999`: 631
  - `50k-99,999`: 191
  - `>=100k`: 59
- microbench結果:
  - short: 6,734 chars, 21.75 sec, parse ok
  - median: 9,951 chars, 12.18 sec, parse ok
  - long: 128,961 chars, 164.60 sec, parse ok
- warmup後の短・中 repeat:
  - short: 6,734 chars, 11.80 sec -> 7.79 sec, parse ok
  - median: 9,951 chars, 12.93 sec -> 6.54 sec, parse ok
- warmup後の long repeat:
  - same long prompt: 128,961 chars, 10.52 sec -> 9.18 sec, parse ok
  - max long prompt: 161,798 chars, 180.02 sec timeout -> 23.93 sec, parse ok

未検証 / 解釈:

- 初回の short 21.75 sec は model load / runner warmup / cache初期化の影響を含んだ可能性がある
- 同一promptの2回目が大きく短縮するため、KV / prefix cache の影響が強い可能性がある
- max long prompt は1回目がclient timeoutした後、2回目は23.93 secで成功した。server側でprefillや
  prefix cacheが残った可能性があるが、server内部状態は未確認
- prompt chars と token 数は同じではない
- 129k chars級で165秒かかったため、長大 evidence が数分停止の一因である可能性は高いが、
  GC / swap / runner stall と確定したわけではない
- current `projects.state_json` には `llm_source` はあるが、固定 evidence 比較に必要な
  `llm_source.evidence` は current state からは取れない。比較 corpus は保存 run 由来で作る

方針への影響:

- model比較の前に、prompt/evidence長の上限と縮約方針を検討する価値がある
- 重いタスク比較の subset は、`prompt_chars >= 50k` と `>=100k` の候補から作る
- 現行モデルのJSON安定性は、この3件だけでは問題なし。ただし品質評価と繰り返し実行は未実施
- 速度改善候補は model変更だけでなく、長大 evidence の cap / summarize / field selection を同時に見る
- benchmarkでは warm/cold、同一prompt repeat、別prompt連続実行を分けないと判断を誤る

## 2026-07-02 evidence compaction experiment

実装済み / 実行済み:

- 通常DBを `mode=ro` で開き、保存済み `investigation_runs.output_project_json` の
  `llm_source.evidence` を集計した
- `.tmp/llm_evidence_shrink_experiment.py` で、長大 evidence の field別サイズと縮約variantを検証した
- report は `.tmp/llm-evidence-shrink-*.json` に保存した

動作確認済み:

- `prompt_chars >= 50k` の保存runは250件
- 肥大の主因は `resource_url_records`
  - 250件すべてに存在
  - average size: 56,916 chars
  - median size: 47,283 chars
  - max size: 121,180 chars
- 次に大きい field は桁が落ちる
  - `candidate_developer_accounts`: average 1,572 chars、max 40,932 chars
  - `summary_candidates`: average 1,332 chars、max 8,029 chars
  - `tagline_candidates`: average 1,212 chars、max 7,908 chars
- 最大case `Hypercerts` の `resource_url_records` は579件、約110,858 chars
  - recordは主に `url`、`source`、`confidence`、`role`、`observed_at` の単純な繰り返し
- 最大caseでのprompt削減:
  - original: 161,798 chars
  - `cap-heavy`: 22,866 chars、85.9% reduction、parse ok
  - `resource-summary`: 13,381 chars、91.7% reduction、parse ok
  - `summary-heavy`: 13,210 chars、91.8% reduction、parse ok
  - `minimal`: 11,783 chars、92.7% reduction、parse ok
- 別project `porxie` での `resource-summary`:
  - original: 151,052 chars
  - `resource-summary`: 9,090 chars、94.0% reduction、parse ok

未検証 / 解釈:

- LLM実行は最大caseと別project各1件中心で、品質評価や回帰評価としては不足
- `resource-summary` は URL群の全列挙を捨て、host別countと代表URLへ畳むため、
  特定URL pathが分類に必要なcaseでは情報を落とす可能性がある
- ただし現在の長大caseでは、`resource_url_records` が legacy unknown URL の大量列挙になっており、
  LLM triageへ全件渡す価値は低い可能性が高い
- `candidate_developer_accounts` の最大40k級は別途個別に縮約が必要

方針への影響:

- 第一候補は、`resource_url_records` を host別summaryへ変換すること
  - total count
  - source / role / confidence counts
  - top hosts with count
  - hostごとの代表URL少数
- `resource_url_records` を単純に先頭N件へ切るより、host別summaryの方が分布情報を保ちやすい
- prompt長capは `resource_url_records` の後段ではなく、LLM evidence bundle生成時点に入れる
- 次に見るべき縮約対象は `candidate_developer_accounts`、`summary_candidates`、`tagline_candidates`
- モデル比較の前に、少なくとも `resource_url_records` summary化の実装を検討する価値がある

## 2026-07-02 resource URL bloat root cause

実装済み:

- `collect_first_party_links()` の `same_site` 分岐で、repo候補でも既知の補助ページroleでもない
  同一site URLを `resource_url_records` に保存しないようにした
- これにより、投稿、profile、listなどのcontent URLが `role="unknown"` のconfirmed
  resource recordとして蓄積される経路を止めた
- 同一siteの `docs` / `blog` / `support` など、roleを判定できる補助ページは引き続き保存する
- `resource_url_records` の正規化時に、URL+role単位のmergeと代表選抜budgetを適用する
  - project全体は最大32件
  - role別上限は docs 8、blog 4、support 4、status/privacy/terms 各2、unknown 3
  - host+role別上限は docs 4、blog 2、support 3、status/privacy/terms 各1、unknown 2
  - confirmed、直接source、root/入口URL、短いpathを優先し、tag/archive/post/profile系pathを低く扱う

動作確認済み:

- 通常DBをread-only集計した結果、肥大化は normalized child table ではなく
  `projects.state_json.evidence.resource_url_records` に集中していた
  - `project_identifiers` の `role='resource'` は0件
  - active stateの `resource_url_records` は79,505件
  - role別では `unknown` が62,625件で最多
- 最大caseは `Longform` (`proj:1e87756b829c`) で13,947件
  - `service_follow_up:https://longform.social/profile/rlsbb.ru.web.brid.gy` 由来だけで12,731件
  - 例は `https://longform.social/post/...`、`/profile/...`、`/posts` などの同一site content URL
- `.tmp/resource_url_bloat_dry_run.py` で、既存active DBを更新せずにcleanup候補の上限を見積もった
  - `role` が空または `unknown`: 62,625件
  - source URL と同一siteで、URL pathから補助ページroleを判定できないもの: 56,864件
  - 多いpath kindは `/post` 13,224件、`/blog` 4,480件、root 2,591件、
    `/books` 2,398件、`/specs` 1,686件
- 後続snapshotでは active state が79,509件 / unknown 62,626件に増えていた。
  同じread-only dry-runで現行の正規化budgetを適用した場合の `resource_url_records` は4,613件相当
- 対象unit test:
  - `PYTHONPATH=tests .venv/bin/python -m unittest test_project_registry_core.ProjectRegistryResourceUrlRecordTest -v`
  - 23 tests OK
- core test file:
  - `PYTHONPATH=tests .venv/bin/python -m unittest discover -s tests -p 'test_project_registry_core.py' -v`
  - 113 tests OK

未検証 / 未実施:

- 既存DBの `resource_url_records` はまだ削除・再構築していない
- このguard後に通常enrichを回した時のprompt長分布、wall-clock、LLM latency低下は未測定
- budget/ranking後の全件保存状態やLLM品質への影響は未測定
- 4,613件は通常DBを更新した結果ではなく、read-onlyで現行正規化を適用した概算
- role unknown の全recordが不要とは確定していない。削除する場合は、host/path/source/role/confidence
  ごとのdry-run reportを先に作る
- 56,864件のdry-run候補は削除対象の確定値ではない。保存時のlink textや
  `classify_official_link()` の当時の分類結果は復元していないため、`/blog`、`/about`、
  `/specs` のように残すべき補助ページ候補が混ざる
- `resource-summary` 縮約は依然として有効な安全弁候補だが、根本原因の予防とは別物として扱う

方針への影響:

- まず過収集経路を止め、次に既存DB cleanupのdry-runで削除候補を確認する
- cleanup実装では、`/post`、`/profile`、record id風pathなど明らかなcontent URLと、
  docs/blog/spec/about系の補助ページを分ける
- 同種大量への一次対策は、保存時のbudget/rankingで行う。LLM evidence縮約は二段目の安全弁にする
- 縮約は、未知の肥大化や長期運用での上限超過に備える二段目の対策として設計する
- モデル比較では、cleanup前後のprompt長分布を分けて記録しないと、モデル差と入力肥大差が混ざる

## 評価軸

優先度:

1. 速度
   - full enrich の wall-clock
   - project 1 件あたりの LLM 秒数
   - prompt prefill と decode の内訳
   - prompt length bucket 別の latency / timeout 分布
   - timeout / retry / JSON 再試行率
2. 省メモリ
   - peak RSS / unified memory pressure / swap 発生
   - runner が落ちないこと
   - 他の collector / Web UI と併用できる余裕
3. 品質
   - JSON schema 適合
   - evidence にない URL / identifier を出さない
   - `category`、`service_status`、`technical_interest_level` の妥当性
   - tagline / summary / features の人手評価

品質が少し良くても、全件 runtime と memory pressure が現状から大きく改善しないなら、通常運用の
既定候補にはしない。逆に、品質が同等で 2-3 倍以上速い、または swap を避けられる候補は優先的に
試す。

prompt は model 変更ごとに多少の tuning が必要になる前提で扱う。ただし、model 比較の第一段階では
prompt 変更を混ぜない。まず同一 prompt で素の相性を測り、その後に上位候補だけ prompt variant を
比較する。

## Prompt tuning の扱い

model ごとの prompt tuning は必要になる可能性が高いが、最初から全候補へ深く行わない。
比較の原因を分離するため、段階を固定する。

1. `model-only` screening
   - prompt は現行baselineに固定する
   - model / quantization / runtime / reasoning setting だけを変える
   - JSON安定性、evidence外の創作、速度、メモリ、短い人手品質確認で明らかに弱い候補を落とす
2. `prompt-variant` screening
   - baseline model と上位2-3候補だけを対象にする
   - variant は小さく保つ
     - `baseline_current`
     - `strict_json`
     - `compact_output`
     - `no_reasoning`
     - `evidence_guard_strict`
   - model固有promptは、汎用variantで救えないが速度・メモリが有望な候補だけに限定する
3. tuned heavy subset
   - 採用候補の `model + quantization + runtime + prompt_variant` 組み合わせだけを重いsubsetで測る
   - ここで初めて実運用候補として比較する

report では必ず `prompt_variant` を保存する。最終判断の単位は model 単体ではなく
`model + quantization + runtime + prompt_variant + reasoning_effort + output_cap` とする。
ただし第一段階の足切りでは `prompt_variant=baseline_current` に固定する。

model固有promptの深掘りをしない条件:

- baseline promptでJSON/schemaが大きく崩れる
- evidence外URL/identifierの創作が多い
- 速度・メモリがbaselineから改善しない
- 日本語summary/taglineが明確に弱く、用途限定でも使いにくい

model固有promptを試す条件:

- baseline promptでは一部不安定だが、速度またはメモリが大きく改善する
- errorが形式面に寄っており、promptで修正できる見込みがある
- Qwen以外の系列で、既存promptの語法と相性が悪い可能性が高い
- query planning専用、final triage専用など用途限定なら有望

## 分けて測るもの

### 1. 固定 evidence に対する LLM 出力差

目的:

- model そのものの差を最小ノイズで見る
- network、collector、外部検索結果の変化を入れない

入力候補:

- 保存済み project の `llm_source.evidence`
- 保存済み investigation run の `output_project.llm_source.evidence`
- 将来追加する `triage_input_project` snapshot

測る項目:

- JSON parse 成功率
- enum 外値、必須 field 欠落、型不一致
- evidence にない URL / NSID / handle / DID の提案数
- `category`、`service_status`、`technical_interest_level`、`manual_review_*` の一致率
- tagline / summary / features の空欄率、長さ、明らかな形式違反
- 同一条件の repeated run variance
- latency、timeout、token/cost 相当値
- peak memory、swap、runner error

制約:

- 現状の保存 run には triage 直前の project snapshot がないため、LLM 出力を現在状態へ適用した
  あとの差分は厳密には再現しづらい。

### 2. LLM 出力を project へ適用した後の状態差

目的:

- operator が実際に見る保存状態や queue への影響を見る

必要な追加:

- triage 直前の base project snapshot
- `apply_llm_output()` 後の project diff
- `finalize_project_shape()`、`apply_promotion_state()`、merge/duplicate 計算後の evaluation diff

測る項目:

- materialized field の差分
  - `summary`、`tagline`、`features`、`category`、`service_status`
  - `technical_interest_*`
  - candidate source/service URL
  - manual review signal
- `promotion_ready`、`promotion_blockers`、review queue signal の差分
- operator decision や manual correction と衝突する提案

### 3. full enrich A/B

目的:

- query planning の差が外部検索や最終保存結果まで含めてどの程度効くかを見る

実行方針:

- 通常 DB は直接使わず `.tmp/` 以下の複製 DB で実行する
- 外部 API の状態変化を含むため、結果は「運用時の実差」として扱い、model 単体差とは混同しない
- 同じ project subset、同じ tracking state、同じ prompt version、同じ reasoning effort を manifest に残す

初期 subset:

- まず 10 件で smoke test
- 次に 30-50 件で候補を絞る
- 100 件程度で通常運用候補を比較する
- 全件 shadow run は、50 件比較で明確に勝った候補だけに行う

### 4. 重いタスク / runtime stall 比較

目的:

- 通常の固定 evidence では見えない長時間運用時の stall、GC、swap、runner 側の詰まりを測る
- 全件 enrich ではなく、重い project subset で再現性のある負荷を作る

入力候補:

- evidence が大きい project
- service / repo / lexicon / recent record sample が多い project
- 過去 run で LLM 秒数または wall-clock が長かった project
- 数分停止が観測された project

測る項目:

- project 単位の wall-clock
- LLM request 単位の prefill / decode / total
- prompt token / output token
- prompt length bucket 別の latency / timeout 分布
- peak RSS、swap、memory pressure
- model load / unload の有無
- runner process の継続時間と crash / restart
- 外部 collector timeout と LLM stall の切り分け

制約:

- GC や runner stall は現時点では仮説であり、観測されるまでは原因として確定しない
- macOS / runner / model format / quantization / context length の組み合わせ依存が強いため、
  model 品質比較とは別 report にする

## Corpus 案

最初は 30-50 件、移行判断前は 100 件程度を目安にする。

含める軸:

- seed 種別
  - NSID seed
  - service URL seed
  - repo URL seed
  - account/handle seed
- evidence の濃さ
  - service-heavy
  - repo-heavy
  - lexicon-only
  - name-only / thin evidence
  - conflicting identity
- category
  - project
  - lexicon
  - infra_tool
  - library
  - auth_only
  - personal_tool
  - reject / out_of_scope
- 過去に誤判定・manual correction があったもの
- 日本語/英語が混在するもの
- service status が不安定なもの

gold label は一つに固定しすぎない。operator decision、defer/block 履歴、fixture の期待評価、
人手レビューのコメントを分けて保持する。

## 候補モデルの考え方

候補は個別モデル名より役割で選ぶ。

- baseline
  - 現在日常運用で使っている `Qwen3.6 35B Q4`
  - 乗り換え判断では必ず比較軸に残す
- same-family smaller
  - Qwen 系の 14B / 8B / 4B 級
  - 品質低下が許容範囲なら、速度とメモリ改善の第一候補
- same-family MoE
  - Qwen3-30B-A3B など、総parameterは大きいが active parameter が小さい MoE
  - runner / quantization / kernel 対応次第で速度差が大きく出るため、実測を必須にする
- local fast
  - 安価で速い model
  - bulk enrich の throughput と JSON 安定性を見る
- local strong
  - 現実的に常用できる範囲で強い model
  - editorial 品質、曖昧分類、technical_interest の差を見る
- external low-cost reference
  - 主線ではない
  - 50 件以下の固定 evidence benchmark で、品質または速度が劇的に良いかだけを見る
  - 通常運用採用には privacy、cost、rate limit、再現性を別途評価する

2026-06-30 時点で公式 docs を見る限り、Qwen3 は dense の 4B / 8B / 14B / 32B と MoE の
30B-A3B / 235B-A22B を持ち、hybrid thinking / non-thinking を切り替えられる。Qwen の速度
benchmark には Qwen3-4B / 8B / 14B / 32B / 30B-A3B の速度とメモリの表がある。ただし公式値は
H20 GPU、Transformers、SGLang などの指定環境での結果であり、この repository のローカル runner
でそのまま再現できる事実ではない。

現時点の仮説:

- 出力品質を大きく落とさず速くする第一候補は、同系統の 14B / 8B 級または non-thinking 設定
- メモリ逼迫を下げる第一候補は、より小さい model、低い context、KV cache 量子化、出力 token 上限
- 35B Q4 のまま劇的に速くするには、runner / quantization / prompt 長 / thinking 設定の見直しが必要
- 外部 API は、品質上限の参照または局所的な fallback としてだけ評価する

## 実験前の候補棚卸し

通常 enrich 実行中でも、候補の大枠は絞れる。現状維持が許容できるため、最初から Qwen 系だけに
閉じず、別系列も同じ benchmark に入れる。実験結果で変わるのは主に優先順位と採用可否であり、
候補カテゴリ自体は大きく変わらない。

最初から同じ土俵に載せる候補:

- `Qwen3-14B` / `Qwen3-14B` 相当の instruct / non-thinking
  - baseline からの速度・メモリ改善が見込める
  - 品質低下が許容範囲なら bulk enrich の第一候補
- `Qwen3-8B`
  - 速度・省メモリの強い候補
  - 最終紹介文品質より、query planning や低リスク triage で用途限定採用する可能性がある
- `Qwen3-30B-A3B` / `Qwen3-30B-A3B-Instruct-2507`
  - MoE なので active parameter は小さいが、全weightは読み込む
  - Qwen公式benchmarkでは serving framework によって性能差が大きく、Transformers の GPTQ-INT4 は
    MoE kernel unsupported とされる
  - 現在の runner で速くなるかは未検証。実験なしでは第一候補にしない
- `Qwen3-4B` / `Qwen3-4B-Instruct-2507`
  - 省メモリと速度の下限確認用
  - 品質が足りれば query planning 専用や retry/fallback 用に使える
- Gemma 3 `12B` / `27B`、または Gemma 4 `4B` / `26B A4B` / `31B`
  - Qwen以外のローカル候補
  - 公式model overview、runner対応、GGUF/QAT availability を確認してから比較対象に入れる
  - 日本語混在の editorial 品質、JSON安定性、Qwen promptとの相性は未検証
- Phi-4 / Phi-4-mini 系
  - 小型・省メモリ候補
  - 日本語editorialと保守的分類が足りるかを確認する
- Mistral Small / Ministral 系
  - Qwen/Gemma以外の中小型候補
  - license、ローカルrunner導線、日本語品質、JSON安定性を確認する
- Llama 3.x / 4 系の小型または中型候補
  - tooling と quantization の選択肢は広い
  - 小型の品質、大型のメモリ消費を確認する

後回しにする候補:

- reasoning / thinking 専用 model
  - enrich の分類・要約には過剰になりやすく、速度目標と衝突する可能性が高い
- 32B dense 以上の local model
  - 現状の 35B Q4 メモリ逼迫を解く主目的から外れやすい
  - ただし品質上限のローカル参照として、余裕があれば固定evidence benchmarkだけに入れてよい
- 235B級や大規模MoE
  - ローカル通常運用候補ではない
- 外部API
  - 主線ではない。劇的な品質差または非常に低い運用コストが見込める場合だけ、固定evidenceで参照比較する

### Qwen 以外の候補で起きるトレードオフ

Qwen 系の枠外へ出ても、速度・メモリ・品質のトレードオフ自体は消えない。変わるのは
「何を失いやすいか」と「どの runner / quantization で得をしやすいか」。

- Gemma 系
  - 公式に QAT / GGUF / llama.cpp / MLX 導線があり、ローカル運用の候補として見やすい
  - Gemma 4 では小型、dense、MoE が用意され、公式docsも高parameter・高bitほど能力は上がるが
    memory / processing / power cost も上がると明記している
  - MoE は active parameter が小さくても全weightをmemoryへ載せる必要があるため、メモリ問題の万能解ではない
  - Qwen promptとの相性、日本語混在の自然さ、JSON厳守は未検証
- Phi 系
  - Microsoft は edge / on-device を狙う小型モデル群として位置づけている
  - Phi-4 は 14B、Phi-4-mini は多言語・function calling・instruction following を強化した小型候補
  - ただし Phi は元々英語最適化寄りで、新しめのモデルでも対応言語は20超程度とされるため、日本語editorial品質は要確認
- Mistral / Ministral / Small 系
  - 小型・中型の実用候補になり得る
  - ただし license、公式GGUF/MLX availability、Japanese editorial品質、JSON安定性を個別確認する必要がある
- Llama 系
  - runner対応と周辺 tooling は強い
  - 小型は速いが分類・日本語要約の品質が足りない可能性があり、大型は現状のメモリ課題と衝突しやすい

したがって、Qwen以外は「トレードオフがない候補」ではなく、「Qwenとは違う失敗モードを持つ候補」として扱う。
最初の比較では、同じ corpus で次を重点的に見る。

- JSON schema 違反
- evidence にない URL / identifier の創作
- 日本語 tagline / summary の自然さ
- category / service_status の保守性
- peak memory と swap
- runner 導入コスト

実験結果で調査方針が変わる点:

- 現行runの時間内訳で LLM が支配的でなければ、model変更より collector timeout/cache/budget の改善を優先する
- LLM が支配的で、prefill が重いなら prompt/evidence縮約、context設定、KV cacheを優先する
- LLM が支配的で、decode が重いなら小型model、output cap、non-thinkingを優先する
- memory pressure / swap が支配的なら、小型model、低context、別runner、QAT/GGUF/MLX形式の比較を優先する
- Qwen小型が十分でも、別系列の固定evidence比較は行う
- 別系列が明確に勝たなければ、導入コストの低いQwen内移行または現状維持を選ぶ
- 別系列が品質・速度・省メモリのいずれかで明確に勝つ場合だけ、full enrich shadow runへ進める

参照した公式ページ:

- Qwen key concepts: https://qwen.readthedocs.io/en/latest/getting_started/concepts.html
- Qwen speed benchmark: https://qwen.readthedocs.io/en/latest/getting_started/speed_benchmark.html
- Qwen quantization benchmark: https://qwen.readthedocs.io/en/latest/getting_started/quantization_benchmark.html
- Qwen3 GitHub README: https://github.com/QwenLM/Qwen3
- Gemma 4 model overview: https://ai.google.dev/gemma/docs/core

## 実験候補

### A. モデル差だけを見る固定 evidence benchmark

- 対象: 30-50 件
- 入力: `llm_source.evidence`
- 実行: 各 model 3 回
- 出力 token 上限を固定する
- thinking / non-thinking を明示する
- Qwen 系、Gemma 系、Phi 系、Mistral 系、Llama 系を最初から同じ表に載せる
- prompt length bucket を分ける
  - short: `<10k chars`
  - medium: `10k-20k chars`
  - long: `50k-100k chars`
  - extreme: `>=100k chars`
- 評価:
  - parse 成功率
  - hallucinated identifier / URL
  - category / service_status / technical_interest の差
  - prompt length bucket 別の latency、timeout、peak memory、swap

### B. 速度・メモリだけの micro benchmark

- 対象: 実際の prompt から短/中/長の 3 種類
- DB と collector を使わない
- runner を固定し、model / quant / context / output cap だけを変える
- prefill 秒、decode tokens/s、peak memory、swap を測る

### C. full enrich shadow run

- 対象: 10 件、30-50 件、100 件の段階実行
- `.tmp/` に DB 複製を置く
- 通常 DB へは書かない
- 比較は wall-clock、失敗率、保存 field diff、review signal diff

### D. 構成変更の切り分け

model 変更と混ぜず、別実験として測る。

- `ATPROTO_SURVEY_LLM_REASONING_EFFORT=none` 相当
- max output tokens の制限
- prompt evidence の縮約
- query planning と final triage で別 model を使う
- LLM 不要 project の skip 条件

### E. Prompt variant 比較

第一段階の model 比較では prompt を固定し、上位候補だけで prompt tuning を行う。

- 既存 prompt
- JSON schema をより強く短くした prompt
- evidence の長文部分を縮約した prompt
- category / service_status / technical_interest を分割した prompt
- summary/tagline だけ別 pass にする prompt

model と prompt を同時に変えると原因が分からなくなるため、report は `model-only`、`prompt-only`、
`model+prompt` を分ける。

## 最初に着手する実験手順

実験実装に入る時は、まず固定 evidence benchmark だけを作る。通常DBはread-onlyで使い、
結果は `.tmp/` か専用reportへ保存する。

### 0. Corpus manifest 作成

- 保存済み `investigation_runs.output_project_json.llm_source.evidence` から候補を抽出する
- corpus manifest には少なくとも次を持たせる
  - `case_id`
  - `project_key`
  - `project_name`
  - `source_run_id`
  - `prompt_chars`
  - `prompt_bucket`
  - `evidence_hash`
  - `evidence_field_sizes`
  - `known_manual_signal` があればその参照
- 初回 corpus は30-50件
  - short `<10k`: 10-15件
  - medium `10k-20k`: 8-12件
  - long `50k-100k`: 5-10件
  - extreme `>=100k`: 3-5件
  - thin/conflicting/manual-correction系を少数入れる

### 1. Baseline 再測定

- 現行modelを `baseline_current` promptで測る
- coldに近い1回目と、warm repeatを分けて記録する
- 同一prompt repeatと別prompt連続実行を分ける
- timeoutしたcaseは、server側cacheの可能性があるため、失敗後repeatを別eventとして記録する

### 2. Candidate model screening

- 同じ corpus / 同じ prompt / 同じ output cap / 同じ reasoning setting で比較する
- 各 model は原則1回、上位候補と不安定候補だけ3回repeatする
- 初回で見る足切り条件
  - parse失敗
  - schema違反
  - evidence外URL/identifier
  - latencyの大幅悪化
  - memory pressure / swap悪化

### 3. Prompt variant screening

- baseline model と上位2-3候補だけで実施する
- `strict_json`、`compact_output`、`evidence_guard_strict` のような小さいvariantから試す
- model固有promptは最後にし、report上も `model_specific:*` と明示する

### 4. Heavy subset

- cleanup / resource URL budget 前後の差が混ざらないよう、input snapshot と prompt hash を固定する
- 重いsubsetでは full enrich ではなく、まず固定 evidence LLM replay を使う
- その後に複製DBで30-50件の full enrich shadow runへ進む

この順であれば、次に必要な実装は次の3点に絞れる。

- corpus manifest exporter
- fixed evidence LLM runner
- structured comparison report writer

## 採用判断

最低条件:

- JSON parse 失敗や schema 違反が baseline より悪化しない
- evidence にない identifier / URL を提案する率が増えない
- operator が過去に block/defer した対象を強く戻さない
- `category` と `service_status` の重大な誤判定が増えない
- latency と timeout が日常 enrich に耐える
- peak memory と swap が baseline より悪化しない

乗り換え候補:

- 上記を満たした上で、summary/tagline/features の人手評価が改善する
- technical_interest の high/exceptional が、具体根拠のある project に寄る
- manual_review recommendation が review 負荷を増やすだけでなく、実際に拾う価値のある候補を増やす
- 品質同等なら、全件 runtime 見積もりが baseline の半分以下になる
- 品質がやや落ちても、candidate URL / identifier の安全性が保たれ、3 倍以上速いなら用途限定で採用候補にする

移行手順:

1. 固定 evidence benchmark で baseline と比較する
2. `.tmp/` DB で full enrich shadow run を行う
3. 差分 report を人手確認する
4. 少数 project で通常導線に試験適用する
5. 問題なければ環境変数または preset の既定を切り替える

## 実装候補

将来 command:

```bash
project-registry compare-llm-models --from-runs latest --models config/llm-models.json --repeat 3
project-registry compare-llm-models --project-key proj:... --stage triage --output .tmp/llm-eval/run.json
```

report には少なくとも次を保存する。

- benchmark run id、日時、git commit、dirty 状態
- prompt file hash、prompt version、evidence hash
- prompt variant id
- model spec、transport、api_style、reasoning_effort、temperature
- quantization、context length、output token cap、runner、runner version
- raw parsed payload、parse/schema error
- normalized field diff
- latency、prefill/decode 内訳、timeout、retry、token/cost 相当値
- peak memory、swap、OOM / runner crash
- stall marker、長時間停止区間、外部 collector 待ちとの切り分け
- reviewer note 欄

通常 DB への書込みはしない。full enrich A/B だけは複製 DB に対して実行する。
