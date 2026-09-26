# Enrichの先行取得: 比較と受理待ち

次の一件の初期収集を現在projectのLLM呼出し中に実行する方式を、Codexの技術判断として採用推奨する。固定応答では調査対象・候補・保存結果・調査履歴の意味を保ち、triage再利用hitでもplanningの待ち時間を利用できた。現状はこのbranchの実装・検証までで、human-review受理、母艦への集約送信、主checkout統合、実運用反映は未実施。実週次の短縮率は未測定であり、下の模擬時間の短縮率を本番予測には使わない。

## 技術判断

従来はprojectごとの全処理を逐次実行していた。採用案ではrepository analysis前までの既存収集を`collect_initial`へ切り出し、最初のLLM呼出しで次の一件だけを先行する。LLMが早く終われば先行収集をjoinしてから現在projectの通信へ戻る。DB・LLM・保存・checkpointはcaller threadに置き、取得threadがDBを共有する構造にはしない。新たな依存や汎用schedulerは追加しない。

project全体を複数threadで実行する案は採らない。現状はDBを参照するhomepage確認・duplicate計算、LLM後の追加取得、process-local cacheが交互に現れるため、LLM数の制限だけでは共有状態とAPI同時負荷を保てない。collectorごとに細かく中断・再開する案よりも、既存のまとまった区間と明示的なjoinで保証を小さく保つ。失うのは二回目以降のLLM待ち時間を最大限使う機会であり、最初のLLMが短いprojectでは効果が小さい。

先行有無を比較可能にするためCLIへ`--no-prefetch`を追加した。通常の一括経路と定期workerは先行あり、単体enrichは逐次。checkpointに方式を保存する。snapshot変更時は古い成果を現在状態へ適用せず、一件分の初期収集を未反映履歴へ残して再取得する。仕様の正本は[investigation](../developer/specs/investigation.md#一括調査の先行取得)と[background jobs](../developer/specs/background-jobs.md#enrich-checkpoint)。

## 比較条件と結果

再現手順は[変更の受入](../developer/workflows/change-acceptance.md#enrichの逐次先行取得比較)、全sample値は[比較JSON](enrich-overlap-comparison.json)。2026-09-26、macOS arm64、Python 3.14.7で実施した。6件の架空service project、HTML相当256 KiB／件、同じ固定応答を使った通常applicationの一括処理を、方式ごとに独立したprocess・一時DBで各3回測った。実際の調査入力でLLM backendを測った実験ではない。RSSは同processのwarmup込みで、共有LLMのメモリは含まない。

| 条件 | 模擬取得待ち／LLM一回 | 逐次中央値 | 先行中央値 | 短縮率 | ピークRSS中央値・逐次→先行 |
| --- | --- | ---: | ---: | ---: | ---: |
| triage miss | 150ms／220ms | 3.887s | 3.019s | 22.33% | 66.39→66.28 MiB |
| triage hit | 150ms／220ms | 2.520s | 1.632s | 35.22% | 69.09→68.77 MiB |
| 遅い取得・triage hit | 250ms／30ms | 1.927s | 1.742s | 9.61% | 65.75→66.56 MiB |
| 待ち時間なし・triage hit | 0ms／0ms | 0.137s | 0.135s | 1.69% | 65.97→65.58 MiB |

全条件で意味を比較したhashが一致し、観測のplanning入力・出力、検索候補の既知／新出・最終採用も一致した。triage missではprobe／search／planning／triageが各6回、hitではtriageが0回で他は各6回。正常完走の先行は5件を開始・使用し、未使用0件、余分な固定応答呼出し0回。これは模擬collector/backendの呼出し数であり、HTTP実request数の測定ではない。LLM最大同時数1、project間collector同時実行なしを時系列で確認した。

手動更新による無効化では、3件中一件の初期収集を再取得し、probeが3→4回になるfixtureを確認した。取消しでは現在一件を完了、次の一件の初期収集だけを回収・未反映保存し、三件目を開始しなかった。取消し・fail-fast後の先行分は再開時に再取得するので、その分のAPI負荷は増える。取得中断は初期収集区間の境界で、外部requestの強制取消しは行わない。

待機がほぼない場合はthread・snapshot読込みの追加コストに見合う短縮が期待できない。待ちゼロの2ms差は、逐次134〜138ms・先行133〜136msの変動幅内であり高速化の根拠にしない。追加メモリは先行一件分で、processごとのRSS差にはallocator等の変動もある。負のRSS差をメモリ削減効果とは扱わず、0.81 MiB以内だったfixtureの中央値差を、大きなrepository／Lexicon群を持つprojectの上限保証には使わない。実APIの残quotaが少ない場合は取得順の変更で部分失敗の配分が変わり得る。並列request数を増やさなくても、処理時間短縮によって単位時間のrequest数は増える。

## 確認範囲と未測定

- 一時DB: 固定応答での逐次同値、triage hit/miss、遅い取得、partial failure/rate limit、先行収集の一般例外、fail-fast、現在snapshotの保存競合、手動更新、tracking変更、削除、alias/merge、重複canonicalの一度だけの処理、checkpointのcommit前非更新。
- 通常入口: CLI、実worker subprocess、実SIGINT後の中断・未反映保存・checkpoint再開、取得中の別接続からの手動更新。外部応答だけをfixtureへ置換。
- repository analysisからも先行windowを開き、LLMがcaller threadに留まることを確認。既存のLexicon・repository・triage・保存・Web API関連testは全unit testで確認する。
- 全unit test: `bin/project-registry-test`で1,082件成功（新規18件を含む）。初回にworktree未配置の既存Node依存と新CLI optionの補完漏れを検出し、lockfileどおりの`npm ci --offline --ignore-scripts`と補完同期で対処した。lockfile・依存versionは変更していない。
- 正本read-only: 最新applied履歴200件（2026-09-24 22:34:47〜09-25 00:35:36 UTC）には`total_ms`は200件あるが、今回の初期収集境界・triage再利用・追加観測は0件。現行再利用後の週次短縮をこの履歴から推計していない。
- 未測定: 実外部取得と実LLMを重ねたwall time・HTTP負荷・実rate limit・本番メモリ、実週次の短縮率。今回の変更は外部sourceのAPI・応答契約やLLM設定を変えず、boundedな外部smokeは行っていない。共有LLM・Splash比較のモデルや稼働processに変更はない。
- 本番DB書込み、本番job開始／変更／停止、Web再起動は実施していない。UIは変更していないため実browserの受入対象もない。品質判定の新しい実例は得ておらず、非公開LLM事例の追加は不要と判断した。

実週次の確認では、同程度の対象とtriage hit率を持つbatchの`execution.batch_elapsed_ms`、先行使用／未使用、join待ち、collector requestとrate limitを併読する。初期収集とLLM時間を単純に合算して短縮実績としない。効果が小さい、無効化・quota消費が多い場合は`--no-prefetch`で比較する。二週の観測蓄積はこの実装の着手条件・受理前提にしていない。

## 受理・集約と反映境界

- 実task名: enrichの通信と推論の重ね合わせ
- task ID: `01a0dc07-f8c3-7793-95a7-cba219ff69ec`、host `local`
- 母艦: 統括スレッドを運用、`01a050d2-495b-7fc0-85f5-3fb214fd1c9f`
- 開始点: `c9307e4d`、branch: `codex/enrich-io-overlap`
- 主要判断: ユーザーが一件程度の先行取得と品質・保存契約の維持をGOで承認。具体的な初期収集境界・join方式・技術的採用推奨は子タスクCodexの判断。受理方式はhuman-review。
- 変更commit: このbranchの実装commit。最終hashは受理依頼と集約文に記載する。
- 未commit変更・成果の処分: この作業の差分は実装commitへ含める。比較script・固定fixture・比較JSONはGit管理に残す。一時DB・書き捨て計測script・生ログは清掃し、正本DBの複製は作成していない。worktreeの既存依存`node_modules/`は再検証用にGit管理外で保持する。
- 集約可否: 内容は集約可能だが、受理と集約内容・送信承認を得るまでは母艦へ送らない。母艦が受理commitをcherry-pickし、統合検証と可視な記録を残した後にarchiveする。
- 本番反映: DB migrationは不要。主checkoutへの統合後に新規起動するCLI・workerが先行方式を読む。既存jobへ割込みは不要。Web serverのコードも変更する操作ではなくworker側の変更であり、この目的だけのWeb再起動は不要。本番jobを意図的に開始して確認する操作は別承認。
