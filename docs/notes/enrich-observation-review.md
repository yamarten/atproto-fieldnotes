# 通常enrichの改善評価：導入と初回確認

通常enrichの探索・採用規則を変えず、検索計画の実入力・出力・実行と候補の接続、目的別token、主要収集操作の時間、triage再生成の変化箇所を記録する実装を主checkoutへ統合済み。技術的な選定は子タスクのCodexが行い、利用者は保存・処理負担と導入初回のtriage再生成を確認したうえで、実装の受理と母艦への送信を承認した。次の確認は導入後の週次2回完走時の観測評価。検索計画再利用の採用や追加比較推論は含まない。

## 選定した記録と見送ったもの

| 項目 | 将来の判断・既存記録の不足 | 保存・実行コストと採否 |
| --- | --- | --- |
| planningのprompt/evidence、整形前JSON、seed・最終計画・実行検索語、実行条件 | 入力が同じ時の計画変動と再利用候補を調べる。初期収集後の実入力は開始snapshotから戻せず、旧logはactor/focus等も欠く | planning 96 KiB上限、既存履歴と圧縮。採用 |
| 検索前後の候補状態 | 既知候補の再発見、初期収集由来、検索で出現と最終採用を分ける。query→候補→失敗は既存bundleで足りるが、検索直前の既知状態は失われる | 対象候補だけのbool照合、48 KiB上限。既存bundleの再保存はしない |
| purpose別tokenと設定 | prompt縮約、出力上限、モデル比較の負荷を調べる。triage usageは旧llm_sourceに残る場合もあるが、planning/解析のusageは通常metricsで欠落 | endpoint報告整数のみ、個別16行＋全件集計。TTFT/prefill/decodeは推測しない |
| 主要収集操作の時間、検索retryのattempt/待機 | network以外も含めて重い操作を特定する。phase時間だけではsource/操作を分けられない | タイマーと小さな集計dict。検索retryだけ待機を直接観測。cache/rate limitは既存値を利用 |
| triage変更field・設定等の成分差 | input_or_policy_changedの中身を絞る。旧生成evidenceはあるが、全ての旧設定／収集失敗成分は残らない | 変化field名と非secret成分hash。current entryに約1 KiB。条件・適用は変更しない |
| 全情報源のHTTPごとのretry/cache/待機統一計測 | 現状の件数には固定値や概算が混ざる | 既存adapter全体の計測契約変更は今回の低負荷な準備を超える。操作時間で候補を絞った後に必要sourceだけ再検討 |
| 生検索結果・HTTP本文・全triage promptの追加複製 | 未採用検索結果の精査や任意の再現には有用だが量が大きい | 今回は保持候補、既存triage evidence、必要なplanning入力に限定。関連度filterで落とした全件の品質比較はできない |
| job全体の待ち時間・専用監視process・自動周期集計 | project totalだけではcommit待ちやjob全体を説明できない | 既存batch/checkpoint開始終了を使う。観測を二重実装せず、新規jobは作らない |

現行の保存・意味の契約は[Investigation](../developer/specs/investigation.md#将来比較用の記録)と[Project record](../developer/specs/project-record.md#将来比較用観測の保存)、集計操作は[workflow](../developer/workflows/enrich-observation-review.md)を正本とする。

## 検証と保存量の目安

2026-09-26、正本DBを`mode=ro`・`query_only`で読み、直近200 run（200 project）を新規reportへ通した。全件で新規観測が未記録と表示され、token欠落を0 tokenへ変換しないことを確認した。同じ200件のoutput snapshotではtriage `llm_source.usage`が200件に存在した。これは過去のplanning usageの復元ではない。

同じ保存outputから現行planning/triage promptを再構築し、LLM計画はdeterministic seedで代用して記録処理を測定した。外部取得・LLMは実行していない。過去の実planning入力・出力ではなく保存量と処理負荷のproxyで、通常週次全体の代表性や品質の測定ではない。

| 計測対象 | 中央値 | p95 | 最大 |
| --- | ---: | ---: | ---: |
| 観測によるevaluation JSONの追加論理bytes | 20,032 | 55,953 | 57,713 |
| 同じcodecでの追加圧縮bytes | 3,806 | 8,512 | 10,243 |
| 観測整形・serialization・圧縮 | 0.869 ms | 2.370 ms | 2.660 ms |
| 候補照合・成分診断を含む観測準備 | 1.226 ms | 3.001 ms | 3.520 ms |

200件の圧縮増分合計は846,354 bytes、planning sectionは200件とも省略・秘匿除去なし。これにcurrentのtriage entry成分hashが1,063 bytes/件程度加わり、input/output historyにも既存codecで保存される。上表はこのcurrent/hashの全DB増加量やSQLite page/WAL割当を含まない。token metrics、実際のmodel出力、操作数で実増分は変わる。小さな一時SQLite tableへ200 evaluation blobを挿入・commitする10反復の中央値は既存0.393 ms／観測追加0.925 msだった。正本のwrite lock競合や長期運用の容量増加は未測定。

計測対象は新しい順に`inv:abeadcd91b11448ba63dd906beecf62a`〜`inv:32430d0aee854183b06e47c4b5f03152`。この順のID一覧を空白なしUTF-8 JSONにしたSHA-256は`8ffa084c5bf91f60655825e5fcc75a7268b98cd1d350af8d82ffb2d62b76482d`。集計境界・結果・制約は本notesに保持し、一時資料だけを母艦への根拠にしない。

`bin/project-registry-test`の全1,064 test成功（最終14.482秒）、`git diff --check`成功。自動testでは、通常enrich→save→fetch、triage hit継続、planning fallback、検索retry・失敗時の同じbundle、candidate前後照合、token欠落・不正JSONのusage、16行超の集計読戻し、保存上限と認証情報除去、replay・fixture export・既存cleanup後の観測保持を確認した。Web UI・外部取得契約は変更しておらず、実browser・実通信smoke・共有LLMでの追加推論は実施していない。ローカルのlock済みNode依存をofflineで配置してtestした。LLM内容の正誤は判定せず、個別caseを新しいgoldや調整用事例には追加していない。

## 初回評価と統合境界

子タスクは「調査改善に備えた評価記録の整備」`01a0dbd8-0242-7791-9d88-4170b8e97b9e`（local）、母艦は「統括スレッドを運用」`01a050d2-495b-7fc0-85f5-3fb214fd1c9f`。開始点`014a482f`のbranch `codex/enrich-observation-records`で作成した実装`9a901c31`と受理記録`6ada06a7`を、母艦が2026-09-26に`2dcf6274`・`efb74a6a`として主checkoutへ統合した。統合後の全1,064 test成功、reportの文書記載入口の起動とdiff checkを確認した。正本DBへの書込み、job開始/変更、Web再起動は行っていない。

次に起動するCLI・enrich workerは新しい記録処理を使う。既に起動済みのworkerは対象に数えない。Web process側の新しい履歴readerはserverのreload後に反映されるため、新規記録の集計・replay確認にはそれまでは統合済みcheckoutの開発script・CLIを使う。実運用Webの反映確認は未実施。

transport moduleの内容が変わるため、既存triage再利用のpolicy hashが変わる。統合後の初回は従来規則に従って再生成し、次回以降に新しい診断成分も比較できる。同じ設定・材料での2回目以降は再利用を維持する。これは30日期限や入力一致条件の緩和ではない。

母艦の統合後、新しいコードを読んだworkerによる週次enrichが2回完走した時点で、既存job/checkpoint記録と新しいreportを併読する。同条件の連続run、保存完全性、対象分布が足りなければ通常運用を継続し、次の完走時点で再確認する。2回で品質が証明される意味ではない。旧計画を同時期・同予算で走らせない限り、旧計画が拾えたはずの材料は断定しない。

導入後2回のjob ID／期間は未確認。母艦が既存job/checkpointで導入後workerと完走を照合した時点でここへ反映する。統合や一時DBの検証を実運用での観測開始と扱わない。必要な事例はretention前に既存の非公開事例storeへ選別保存し、汎用reportを無制限に蓄積する別基盤は作らない。
