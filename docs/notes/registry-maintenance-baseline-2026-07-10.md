# Registry Maintenance Baseline: 2026-07-10

この文書は、保存量削減と観測性改善の判断材料にする作業用snapshotです。現行仕様や通常運用の正本では
ありません。計測対象は2026-07-10時点の正本DBの複製で、review対象commit `713c162`の値ではありません。
このsnapshotを基準にしたcleanupは2026-07-11に正本DBへ適用済みです。現在の実装・適用状態は
[status.md](../status.md)、次回計測条件は[roadmap.md](../roadmap.md)を参照してください。

## このnoteの寿命

- 使いどき: 2026-08-10到達または新規1,000 investigation runs時の保存量再計測で、cleanup前後と増加率を比較する
- 消しどき: 後継の比較reportが計測条件、2026-07-10 baseline、cleanup後baseline、増加率を引き継ぎ、roadmapの再検討判断が更新された時
- 役目を終えた後はbaseline reportを系列で残さず、必要な結論だけをstatus/roadmapへ反映して削除する

## 計測条件

- source: main checkoutの`data/survey.db`
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

baseline取得後の実装では、新規enrich runについてcollection + integration、planning、triage、
evaluation、run history save、project saveのwall timeと、LLM request bytes/status/timeout分類を接続しました。
transaction commit、token数、operator review時間は引き続き未接続です。過去15,086 runへは遡及できないため、
このsnapshotの未計測値をbackfillしたとは扱いません。

同snapshotのcurrent projectを現行shapeへhydrateしたLLM evidenceは、prompt budget追加前で最大36,149 bytes、
32 KiB超は1 projectでした。field別代表件数と整形後24 KiB上限を適用すると、全3,239 projectの最大は
24,489 bytes、budget metadataを持つprojectは41件です。最大evidence projectの現行template込みpromptはtriage
29,608 bytes、planning 25,133 bytesでした。これはnetwork/実LLMを使わないprompt生成確認であり、response品質や
実model latencyの検証ではありません。

同branchでservice verificationの新規保存budgetも接続しました。2026-07-10 snapshotの保存済みpayloadへ同じ規則を
read-only適用した概算は204,648,820 bytesから43,172,994 bytes（78.9%減）、12,458 resultから3,878 current
surface、最大project 3,560,797 bytesから698,441 bytesです。既存HTML extract自体が旧20,000文字上限後の値なので、
原responseに対する完全な予測ではありません。正本DBへのcleanupは未実施です。

collector history projectionとGitHub snapshot縮約も同snapshotへread-only投影しました。

- `collector_runs`の3 JSON payload合計: 1,660,002,737 bytesから26,491,762 bytes（98.4%減）
- collector row: 99,863から86,062。source/operation別projectionへの集約分
- `bundle_json`: 701,691,851 bytesから370,460,403 bytes（47.2%減）
- bundle内GitHub record: 525,548から263,652。同一run内の同一repo再収集を除いた値

この値はJSON payloadの投影であり、SQLite pageやVACUUM後file sizeの予測ではありません。GitHub payloadがp95付近の
10 runを旧/新bundleでnetwork-free replayし、evaluationと主要project fieldは10件とも不変でした。最大payload
outlierの一括semantic replayは既存trust graph処理が長時間化して完走していないため未検証です。正本DBへのcleanupは
実施していません。

実装した`cleanup-storage-payloads`でcurrent state、input/output snapshot、bundle、collector projectionをまとめて
全件dry-runした結果は次のとおりです。UTF-8 JSON payloadの合計で、SQLite file sizeやVACUUM後sizeではありません。

| scope | before bytes | after bytes | saved bytes |
| --- | ---: | ---: | ---: |
| project current state | 257,373,596 | 93,990,388 | 163,383,208 |
| investigation input/output | 2,001,407,211 | 1,232,656,211 | 768,751,000 |
| investigation bundle | 701,691,851 | 370,079,800 | 331,612,051 |
| collector projection | 1,660,002,737 | 26,488,540 | 1,633,514,197 |
| total | 4,620,475,395 | 1,723,214,939 | 2,897,260,456 |

対象は3,239 project、15,086 runです。project currentは1,889件、input/outputは計15,108 row、bundleは
10,767 runが変更対象でした。collector rowは99,863から86,062、同一run内の意味が同じGitHub recordは最新の
`observed_at`を保持して261,896件減る投影です。input/outputの保存済みevaluation field比較は15,086 runで差分0件、
GitHub payload p95付近10 runの完全network-free replayは10/10不変でした。

最大削減量上位5 runの完全replayは、既存relation normalize経路が各10秒で完走せず未検証です。これは全体dry-runや
JSON投影の失敗ではありませんが、最大outlierのsemantic replayが確認済みとは扱いません。正本DBへのexecuteは
実施していません。

### P3.2 category report拡張後の再計測

2026-07-11に同じimmutable cloneへ、resource URLと旧LLM evidenceのcleanupおよびcategory reportを追加した実装を
全件dry-runしました。

| scope | before bytes | after bytes | saved bytes |
| --- | ---: | ---: | ---: |
| project current state | 257,373,596 | 91,963,114 | 165,410,482 |
| investigation input/output | 2,001,407,211 | 1,067,280,055 | 934,127,156 |
| investigation bundle | 701,691,851 | 370,079,800 | 331,612,051 |
| collector projection | 1,660,002,737 | 26,488,540 | 1,633,514,197 |
| total | 4,620,475,395 | 1,555,811,509 | 3,064,663,886 |

追加categoryの投影:

- resource URL: 546,298→43,295 records、116,240,032→7,905,001 bytes（108,335,031 bytes減）
- LLM evidence: 190,859,626→131,791,227 bytes（59,068,399 bytes減）
- service verification: 1,303,760,672→371,622,626 bytes（932,138,046 bytes減）
- snapshot evaluation field差分: 15,086 run中0件

resource URLの削除record内訳はrole別で`unknown` 408,274、`docs` 69,950、`blog` 18,438、その他6,341です。
先頭path patternの上位は`/post` 106,798、`/blog` 40,984、`/docs` 24,296、`/` 12,551、`/guides` 12,541、
`/books` 12,278、`/specs` 11,166でした。これはpath名による削除listではなく、URL+role mergeと
project/role/host+role budgetで代表外になったrecordの監査内訳です。host/path上位50とlargest savings 10件はcommandの
`payload_categories.resource_urls`で取得できます。

この計測時点では正本DBを変更していません。上記は後続の正本適用前に確認したdry-run見込みです。

## 当時の判断

確認済みの優先順位:

1. 新規runのphase timingを蓄積し、長時間runの支配区間を確認する。
2. storage codeを通常運用へ適用し、正本DB cleanupはwriter停止、backup、承認後の別operationにする。
3. 最大outlierのreplay normalize性能をstorage cleanupと分けて改善する。
4. same-site過去誤帰属のauditを正本DB maintenance windowの候補に含める。

未決定:

- byte-aware retentionの上限と、保持する最新run数
- cleanupを正本DBへ適用する時期
