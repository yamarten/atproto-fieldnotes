# Registry Maintenance Baseline: 2026-07-10

この文書は、保存量削減と観測性改善の判断材料にする作業用snapshotです。現行仕様や通常運用の正本では
ありません。計測対象は2026-07-10時点の正本DBの複製で、review対象commit `713c162`の値ではありません。

## 計測条件

- source: `data/survey.db`
- snapshot: APFS cloneを`.tmp/registry-baseline-2026-07-10-apfs.db`へ作成
- logical file size: 4,760,858,624 bytes（4.43 GiB）
- schema version: 2
- project count: 3,239
- `PRAGMA integrity_check`: `ok`

clone直前に`survey.db-wal` / `survey.db-shm`が無いことと、正本DBをopenしているprocessが無いことを確認しました。
通常Web server自体は停止していませんが、確認時点ではDB file descriptorを保持していませんでした。clone後の
集計はSQLite immutable URIまたは複製DBだけを使い、正本DBは変更していません。snapshotは一時成果物なので
commitしません。

物理bytesはSQLite `dbstat.pgsize`、JSON内訳は`length(CAST(value AS BLOB))`で計測しています。JSON配列の件数や
queue件数は複製DBを現行実装で読み取った値です。

## 保存量

### tableとindex

| object | 物理bytes | 概算 | 主な内容 |
| --- | ---: | ---: | --- |
| `investigation_runs` | 2,723,831,808 | 2.54 GiB | run入出力、evaluation、bundle |
| `collector_runs` | 1,701,408,768 | 1.58 GiB | collector result、records、failure |
| `projects` | 265,637,888 | 253 MiB | current profile/state |
| `project_relations` | 18,092,032 | 17.3 MiB | normalized relation本体 |
| relation unique index | 16,961,536 | 16.2 MiB | relation重複制約 |

上位3 tableだけでfileの約98.5%です。normalized identifier、decision、aliasとそのindexは支配要因では
ありません。

### JSON column

| 保存場所 | bytes |
| --- | ---: |
| `investigation_runs.input_project_json` | 866,478,389 |
| `investigation_runs.output_project_json` | 1,134,928,822 |
| `investigation_runs.evaluation_json` | 4,696,078 |
| `investigation_runs.bundle_json` | 701,691,851 |
| `collector_runs.result_json` | 22,874,677 |
| `collector_runs.records_json` | 1,627,057,612 |
| `collector_runs.failures_json` | 10,070,448 |
| `projects.profile_json` | 1,861,686 |
| `projects.state_json` | 257,373,596 |

履歴では同じrunについてproject入出力、bundle、collector recordsを別々に保持しています。ここから直ちに
「重複なので削除可能」とは判断しませんが、retentionを日数と件数だけで決めてもbyte上限にならないことは
確認できました。

## current project state

`state_json` 257,373,596 bytesの主な内訳です。

| `state.evidence` field | project数 | total bytes | 最大project bytes |
| --- | ---: | ---: | ---: |
| `service_verification_results` | 1,889 | 204,646,120 | 3,560,797 |
| `llm_source` | 3,239 | 15,360,231 | 111,226 |
| `trust_graph` | 3,139 | 6,036,867 | 325,666 |
| `investigation_log` | 3,239 | 5,025,399 | 8,340 |
| `llm_output` | 3,239 | 4,567,617 | 2,633 |
| `summary_candidates` | 3,239 | 3,091,997 | 8,042 |
| `recent_record_samples` | 1,594 | 1,768,009 | 8,792 |
| `tagline_candidates` | 3,239 | 1,733,522 | 7,681 |
| `resource_url_records` | 1,142 | 802,422 | 7,132 |

`service_verification_results`だけで`state_json`の約79.5%を占めます。12,458 resultに含まれる上位fieldは
次のとおりです。

| field | total bytes | 最大1 result bytes |
| --- | ---: | ---: |
| `html_extract` | 134,283,326 | 22,765 |
| `links` | 31,409,539 | 1,450,700 |
| `urls` | 24,591,842 | 178,768 |
| `content_sha256` | 665,984 | 64 |
| `description` | 620,583 | 681 |

HTML extract、links、urlsの3 fieldでservice verification保存量の約93.0%です。単一resultの`links`が
1.45 MBに達するcaseがあり、件数だけでなくbyte budgetが必要です。

`resource_url_records`は4,665 recordで、`unknown` 2,401、`docs` 1,032、`blog` 560、`support` 281、
`privacyPolicy` 207、`termsOfService` 141、`status` 43でした。既存budget適用後のcurrent snapshotでは
0.8 MBであり、今回の保存量対策の第一要因ではありません。

state sizeの上位projectは次です。

| project | state bytes |
| --- | ---: |
| Boost | 3,820,518 |
| Leaflet | 2,749,063 |
| Granary | 2,588,670 |
| Graze | 1,594,573 |
| Atsign | 1,532,723 |
| Apify | 1,521,407 |
| Feeds | 1,453,535 |
| Cosmik | 1,399,821 |
| atproto.dart | 1,377,151 |
| Framer | 1,362,457 |

## investigation historyと所要時間

- `investigation_runs`: 15,086 run、2026-06-18から2026-07-09
- run数のあるproject key: 3,468。merge前keyやalias由来の履歴を含むためcurrent project数より多い
- duration: 平均39.66秒、p50 31秒、p95 76秒、最大2,039秒
- 4 JSON column合計: 平均176,314文字、p50 79,152文字、p95 624,283文字、最大8,102,302文字

保存量上位のproject historyは、Longform 31,817,952文字、Granary 24,659,427文字、Bluesky
16,267,323文字、atproto.dart 15,083,380文字、Apify 12,379,117文字でした。Granaryは6 runの平均
1,683.8秒、最大2,039秒で、通常分布からも外れています。

durationはrun全体のwall timeだけです。collector、integration/evaluation、LLM、save、operator reviewのphase別
時間は現行schema/eventに保存されていないため、代表project 10〜20件の内訳はこのsnapshotから復元できません。
上記project別値をphase timingの代用にはしません。

## collector history

`collector_runs`は99,863行です。保存payload上位sourceは次のとおりです。failure率はcollector runの
`status = failed`を単純集計した値で、期待されるnot-foundと障害を区別していません。

| source | runs | success | failed | skipped | raw failure率 | payload bytes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `github_repo` | 10,770 | 10,498 | 272 | 0 | 2.5% | 1,010,594,758 |
| `service_static` | 10,529 | 8,383 | 2,146 | 0 | 20.4% | 282,879,675 |
| `service_follow_up` | 2,643 | 2,614 | 29 | 0 | 1.1% | 264,936,492 |
| `lexicon_resolution` | 11,754 | 1,098 | 10,656 | 0 | 90.7% | 48,810,960 |
| `github_search` | 15,086 | 15,068 | 17 | 1 | 0.1% | 16,606,350 |
| `bsky_post_search` | 15,086 | 15,016 | 69 | 1 | 0.5% | 13,064,501 |
| `bsky_actor_search` | 15,086 | 15,074 | 11 | 1 | 0.1% | 12,163,399 |
| `bsky_author_feed` | 15,086 | 1,265 | 0 | 13,819 | 0.0% | 5,029,681 |

historyの新規増加を抑える際は、まず`github_repo`とservice系recordsの保持内容を調べる必要があります。
`lexicon_resolution`の90.7%は値だけではsource障害と断定できず、failure reasonの分類が別途必要です。

## 運用状態とqueue

tracking stateは`active` 2,479、`stalled` 389、`stable` 326、`ignore` 42、`archived` 2、
`out_of_scope` 1でした。全projectに`last_enriched_at`があり、2026-07-10 00:00 UTC基準のageは、1日未満
1,599件、1〜6日897件、7〜29日743件、30日以上0件です。

保存fieldでは`manual_review_recommended = true`が1,405件、`promotion_ready = true`が1,026件、
`last_run_errors`ありが1,912件、`duplicate_candidates`ありが495件でした。これらはqueue件数そのものでは
ありません。

現行queue builderを複製DBへ適用した結果はreview 1,110件、promotion 188件です。review signal上位は
`llm_manual_review_recommended` 568、`high_information_density` 315、`many_identity_candidates` 242、
`service_candidate_needs_confirmation` 198でした。promotion signalは`service_ready_for_introduction` 170、
`lexicon_ready_for_introduction` 14、`technical_insight_candidate` 5です。一つのprojectが複数signalを持つため、
signal件数の合計とqueue件数は一致しません。

## このbaselineで測れないもの

未接続または未検証:

- collector、integration/evaluation、LLM、saveのphase別wall time
- LLM requestの正確なprompt bytes/token数、応答時間、timeout種別
  - `llm_source.evidence`はLLMへ渡す縮約evidenceの保存形で、最終prompt文字列そのものではない
- operatorがqueueを開いてdecisionを保存するまでのreview時間
- 一回の増分・全件enrichに必要なoperator待ち時間と総wall time
- 紹介判断precisionとidentity/merge correction率

これらは推測値で埋めず、phase timing、LLM request metadata、operator session計測または固定sampleを接続してから
成功指標のbaselineにします。

baseline取得後のmaintenance branchでは、新規enrich runについてcollection + integration、planning、triage、
evaluation、run history save、project saveのwall timeと、LLM request bytes/status/timeout分類を接続しました。
transaction commit、token数、operator review時間は引き続き未接続です。過去15,086 runへは遡及できないため、
このsnapshotの未計測値をbackfillしたとは扱いません。

同branchでservice verificationの新規保存budgetも接続しました。2026-07-10 snapshotの保存済みpayloadへ同じ規則を
read-only適用した概算は204,648,820 bytesから43,172,994 bytes（78.9%減）、12,458 resultから3,878 current
surface、最大project 3,560,797 bytesから698,441 bytesです。既存HTML extract自体が旧20,000文字上限後の値なので、
原responseに対する完全な予測ではありません。正本DBへのcleanupは未実施です。

## 判断

確認済みの優先順位:

1. current stateの`service_verification_results.html_extract/links/urls` budgetを新規runで検証する。
2. historyでは`github_repo`とservice系collector recordsの保持内容を調べ、新規増加を止める。
3. 新規runのphase timingを蓄積し、長時間runと保存量outlierを同じproject/run単位で比較する。
4. 新規保存budgetを検証してから、正本DB cleanupを別maintenance operationとしてdry-runする。

未決定:

- current stateからraw fieldを完全に落とすか、bounded sampleを残すか
- investigation runとcollector runのreplayに必要な最小payload
- byte-aware retentionの上限と、保持する最新run数
- cleanupを正本DBへ適用する時期
