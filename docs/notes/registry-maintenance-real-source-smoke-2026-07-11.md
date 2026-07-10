# Registry Maintenance Real-source Smoke 2026-07-11

P2.2、P2.4、P4.1の判断材料として行ったbounded smokeの記録です。実装済み通常仕様の正本ではありません。

## 安全条件

- 本家worktreeの`.envrc`は`direnv exec`したprocess内だけで読み、値を表示・保存していない
- worker syncは専用temp DBまたは2026-07-10 baselineの作業用APFS cloneへ`execute=false`で接続した
- 正本DBには接続していない
- GitHubは1 query / 1 page / 1 item、Sembleは固定2 collectionごとに1 page / 1 item、Bluesky introducedは
  1 page / 1 postへ制限した
- collection inventoryとTangledは`max_items=1`で処理対象を制限した。どちらも単一responseを全件取得するため、
  network response自体の件数上限ではない

## P2.4 worker smoke

| source | 結果 | 上限・再実行 | DB更新 |
|---|---|---|---|
| GitHub search | worker `completed`。all-history runで1 repoを取得し、`source_query` / `source_item` eventを確認 | 1 query / 1 page / 1 item。即時再実行は同じrepoとsummary | なし。`state_updated=false` |
| Semble | worker `completed`。2 collectionから各1 cardを取得し、通常seed 1件とevidence-only 1件へ分類 | 2 request / 2 cards。即時再実行は同じsummary | なし。`state_updated=false` |
| Bluesky introduced | 認証sessionとsearchが成功しworker `completed`。1 postから5 introduction entryを抽出 | 1 page / 1 post | なし。`state_updated=false` |
| collection inventory | worker `completed`。8,318 entryのresponseから1 entryだけを照合 | 1 request / 1 processed entry。`truncated=true` | なし。`state_updated=false` |
| Tangled | worker `completed`。42 entryのUFOs rolling windowから1 repoだけを照合 | 1 request / 1 processed entry。`truncated=true` | なし。`state_updated=false` |

空のtemp DBは実行前後とも`projects=0`、`sync_state=0`、`investigation_runs=0`、`collector_runs=0`でした。
collection inventoryとTangledを同条件で即時再実行したところ、件数と非更新性は同じでしたが先頭entryは変化しました。
前者のresponse順序と後者のrolling windowは固定snapshotではないため、bounded smokeの同一候補再現性は期待しません。

failure/cancelの責務分担:

- live smokeはcredential、endpoint、request/response、通常完了、再実行、DB非更新を確認する
- failure分類とcancel境界は、外部serviceへ意図的な障害を起こさずdeterministic testで確認する
- source途中失敗時に続行してpartial resultを返すかというpolicy変更はP4.3の対象であり、このsmokeの完了条件にはしない

## P2.2 LLM timing

`.envrc`のOpenAI互換endpointへ、本文を出力せずResponses APIで確認しました。

- 固定55-byte prompt:
  - 初回接続はtool側wall timeで約94秒。application metricsを取得できなかったためcold-start参考値
  - warm runは831 ms
- 2026-07-10 cloneの代表project:

| sample | purpose | prompt bytes | response bytes | duration |
|---|---|---:|---:|---:|
| payload median | planning | 3,767 | 920 | 7,159 ms |
| payload median | triage | 8,242 | 1,459 | 12,270 ms |
| payload p95 | planning | 25,123 | 1,329 | 8,638 ms |
| payload p95 | triage | 29,598 | 2,730 | 16,632 ms |

4 requestは全て`success`で、timeoutは180秒、transportはOpenAI互換Responses APIでした。代表10〜20 project、
collector/save込みend-to-end、operator review時間は未測定です。

## P4.1 Semble sample

2026-07-10 baselineの作業用clone（3,239 project）に同じ2 cardをdry-runしました。

- `https://atstore.fyi`は既存`AT Store`へexact service URLでmatch
- `https://track.social/login`はroot service `https://track.social`を持つ既存`Track`へevidence-onlyとしてmatch
- project、sync state、investigation/collector run件数は実行前後で不変

この2件ではweak誤attachを再現しませんでした。P4.1を閉じるには、現行matching call pathのcode auditと、
より広い実データsampleまたは「exact以外をattachしない」ことの確認が必要です。
