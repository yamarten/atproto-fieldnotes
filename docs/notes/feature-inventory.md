# Project Registry 機能棚卸し

この文書は、機能を維持・凍結・削除する判断のための時点付き作業ノートです。現行仕様の正本ではありません。

対象はcommit `a09d3c6`です。実装規模はPython約30,160行、Web frontend約1,590行、test約9,420行です。
この棚卸しはコード量だけでなく、projectの主目的、通常導線への接続、削除時に戻る手作業、誤判定・DB破損の
危険を評価します。利用頻度をrepositoryだけで確認できない機能は、重要度を確定せず条件付きとしています。

## 結論

現在の機能は、大きく次の4種類に分かれます。

1. **中核**: project record、identity、operator decision、調査、review。削除するとprojectの目的を失う。
2. **安全性**: conservative merge、trust、transaction、保存budget、replay。コード量はあるが削除すると誤判定や復旧費が増える。
3. **運用補助**: Web、worker、checkpoint、scheduler。手作業でも代替できるため、実利用頻度で維持を判断できる。
4. **source固有・maintenance**: 外部source adapterや一度きりのcleanup。最も削除候補にしやすい。

旧DB互換・移行痕跡・旧payload fallbackと役割を終えたcleanup/repair commandは削除済みです。
次に削除を検討しやすいのは、`assist-db-edit`、未使用のsource lane、未使用ならbrowser verificationです。schedulerは
現運用で利用中のため削除候補から外します。逆に、normalized storage、operator decision、merge/split、
evidence分類、保存budgetを削ってもコードは減りますが、projectの価値と安全性を同時に失います。

## 評価基準

### 重要度

- **S**: 主目的そのもの。無くすと別のprojectになる。
- **A**: 通常運用または重大な誤判定・データ事故の防止に重要。
- **B**: 有用な補助。手動代替は可能だが、継続的な負担が戻る。
- **C**: source、利用形態、maintenance時期に依存する条件付き機能。
- **D**: 現時点で削除候補。ただし実利用の確認後に決める。

### 削除による単純化効果

- **大**: 複数module、UI、test、運用契約または依存をまとめて削減できる。
- **中**: command familyまたは独立moduleを削減できる。
- **小**: adapterや画面要素が減るが、coreの複雑さはほぼ残る。
- **逆効果**: 行数は減っても、手作業、誤判定、復旧費が増え、全体として単純にならない。

効果は厳密な削除行数ではありません。共有helperが多いため、機能を一つ消しても記載どおりの行数がそのまま減るとは
限りません。

## 1. Registryと保存

| 機能 | 接続状況 | 重要度 | 削除効果 | 削除時に失うもの | 所見 |
|---|---|---:|---:|---|---|
| schema v2 SQLite registry | 通常CLI/Webの正本 | S | 逆効果 | 全project dataと通常導線 | 維持。SQLite選択も個人local運用に合う |
| project scalar / `profile_json` / `state_json` | 全project表示・評価・保存 | S | 逆効果 | project record | 維持。ただし`state_json`へ新しい表現を増やさない |
| normalized identifier / relation / decision | 検索、guard、merge、review | S | 逆効果 | identityとoperator判断の正本 | 維持。flat runtime viewとの二重性は将来の整理対象 |
| project key alias / merge lineage | merge後の参照と履歴集約 | A | 逆効果 | 古いkeyからの追跡と履歴 | 維持 |
| investigation / collector run history | detail、replay、診断 | A | 大 | 調査の再説明と回帰材料 | 全削除しない。保存単価とretentionを管理する |
| bounded service verification保存 | current stateと履歴 | A | 逆効果 | DB増加抑止 | 機能というより必要な保存制約。維持 |
| bounded LLM evidence | planning / triage | A | 逆効果 | prompt肥大防止 | LLM自体を削除しない限り維持 |
| bounded history projection | 新規investigation run | A | 逆効果 | DB増加抑止 | 維持 |
| runtime flat project view / codec | 全domain logic | A | 大 | 現行heuristicとの接続 | 長期負債だが即時削除不可。typed APIへの段階移行だけ検討 |

## 2. 候補発見と外部source sync

| 機能 | 接続状況 | 重要度 | 削除効果 | 手動代替・損失 | 所見 |
|---|---|---:|---:|---|---|
| manual seed追加 | CLI/Web通常導線 | S | 逆効果 | 発見済みprojectを登録できない | 維持 |
| mixed seed list import | CLI | B | 小 | 1件ずつ追加 | 利用頻度が低ければ統合候補だが削減効果は小さい |
| Bluesky introduced sync | CLI/Web worker/schedule | A | 中 | 紹介済み状態を手動更新 | 主成果物との接続が強いので維持 |
| collection inventory sync | CLI/Web worker/schedule | A | 中〜大 | collection発見を手動確認 | 発見coverageへの寄与を周期ごとに測る |
| GitHub repository search sync | CLI/Web worker/schedule | A | 大 | GitHub検索とscreeningを手動実施 | 有力source。候補採用率が低ければqueryを縮小し、機能全削除は後段 |
| Semble collection sync | CLI/Web worker/schedule | B | 中 | Sembleを手動確認 | source固有。2〜4周期のunique候補数で維持判断可能 |
| Tangled UFOs repo sync | CLI/Web worker/schedule | B | 中 | Tangled/UFOsを手動確認 | source固有。rolling windowのunique成果を測る |
| Tangled timeline/community/bot adapter | parserあり、通常実行は無効 | C | 小〜中 | optional discovery source | 有効化予定がなければ削除候補。現在は未接続なので優先して棚卸しできる |
| source config (`config/sources.json`) | 全syncのoperator設定正本 | A | 逆効果 | source値がcodeへ再分散 | 維持。generic plugin DSLへ拡張しない |
| incremental watermark / lookback | 各sync | A | 逆効果 | 毎回全件取得 | 維持 |
| unmatched screening / suggestions | GitHub、Semble、collection | A | 逆効果 | weak matchの誤attach増加 | conservative identity方針の一部として維持 |

source laneは、単純な「取得件数」ではなく次で判断します。

- 他sourceでは得られなかったprojectを何件見つけたか。
- operatorが深掘り対象にした割合。
- request時間、failure、手動修正を含む運用費。
- sourceを止めた場合、人力調査で同じ情報を拾えるか。

## 3. Enrichとevidence収集

| 機能 | 接続状況 | 重要度 | 削除効果 | 削除時に失うもの | 所見 |
|---|---|---:|---:|---|---|
| 1 project enrich orchestration | CLI/Web worker | S | 逆効果 | 初期調査と再調査 | 維持 |
| collector -> evidence -> integration -> evaluation | 通常enrich | S | 逆効果 | 根拠と評価の分離 | project固有の中核設計。維持 |
| GitHub repo/README/lexicon inspection | 通常enrich | A | 大 | source、schema、利用関係 | 維持。payload budgetは必須 |
| Bluesky actor/post/feed evidence | 通常enrich | B | 中 | developer/account/social evidence | uniqueな判断材料を測る余地あり |
| lexicon resolution / recent record sample | 通常enrich | A | 中 | schema実在性と利用状況 | atproto project調査の中核に近い |
| static/deep service verification | 通常enrich / CLI | A | 中〜大 | service利用可否 | briefの評価軸なので維持 |
| anonymous browser verification | opt-in lane | C | 中 | login/app shellなどJS後のsurface | uniqueな誤判定修正が無ければ削除候補 |
| OAuth client metadata inspection | 通常enrich | B | 中 | permission/use relation | auth-only/project利用深度への寄与をsampleで判断 |
| Tangled account/repo inspection | 対象projectで実行 | B | 中 | Tangled固有ownership/source evidence | Tangled source全体と一緒に判断 |
| retry/cache/circuit breaker/request budget | collector共通 | A | 逆効果 | API負荷・長時間停止の抑止 | 外部sourceを残す限り維持 |
| failure classification | history/Web review | A | 逆効果 | 未調査と否定的事実の区別 | 維持 |

## 4. LLM機能

| 機能 | 接続状況 | 重要度 | 削除効果 | 手動代替・損失 | 所見 |
|---|---|---:|---:|---|---|
| query planning | enrich | B | 中 | deterministic planだけで調査 | model比較前に削除しない。実際の追加evidence量を測る |
| project triage / classification | enrich | B | 中 | rule-based評価とoperator判断 | gold setで品質差を測って維持判断 |
| tagline/summary/editorial補助 | enrich/profile候補 | B | 中 | 人が紹介文の下書きを作る | operator時間削減が確認できるなら価値が高い |
| technical interest評価 | enrich/promotion queue | B | 中 | 技術的新規性を人が読む | briefの評価軸だがLLM結果は暫定。gold set待ち |
| command transport | 環境変数で選択 | C | 小〜中 | local wrapper経由のLLM | endpointしか使わないなら削除候補 |
| OpenAI互換endpoint transport | 実運用確認済み | B | 中 | LLM連携全体 | LLMを残すなら本命として維持 |
| prompt metrics / timing | investigation run | B | 小 | 費用・遅延の診断 | baselineが安定した後も軽量なので維持しやすい |

LLM subsystemは削除効果が比較的大きい一方、紹介文作成や曖昧caseの人手を減らす可能性があります。
先にmanual gold setとoperator時間を測り、次のどれかを判断します。

- planningとtriageの両方を維持する。
- editorial補助だけ残す。
- deterministic collector/evaluationへ戻す。

## 5. Evaluation、identity、trust

| 機能 | 接続状況 | 重要度 | 削除効果 | 削除時に失うもの | 所見 |
|---|---|---:|---:|---|---|
| service status評価 | enrich/review/detail | A | 逆効果 | 利用可能性の判断 | briefの主評価軸。維持 |
| atproto contribution評価 | enrich/queue | A | 逆効果 | 利用深度の判断 | queue再設計の基礎。維持 |
| technical interest評価 | enrich/queue | B | 中 | 技術的新規性の候補 | rule/LLMの精度をgold setで確認 |
| identity normalization | 全seed/lookup/merge | S | 逆効果 | project同一性 | 維持 |
| conservative auto-confirm policy | enrich/trust | S | 逆効果 | 誤帰属抑止 | 維持 |
| trust graph | URL confirm、collection ownership、監査 | A | 大 | 強い/弱い根拠の経路 | コード量はあるが過去の誤confirm防止に直結。拡張は凍結 |
| duplicate candidate / merge recommendation | detail/merge/review | A | 中〜大 | 重複整理 | 維持。global計算の性能だけ改善対象 |
| multi-tenant suffix policy | merge/split/audit | A | 中 | hosted domainの誤merge防止 | 実データ由来の安全機能。維持 |
| first-creator heuristic | merge reviewの補助 | C | 小 | merge判断の補助情報 | primary signalではない。誤誘導や保守費が出たら削除候補 |

## 6. Operator decisionとproject mutation

| 機能 | 接続状況 | 重要度 | 削除効果 | 削除時に失うもの | 所見 |
|---|---|---:|---:|---|---|
| confirm/block/defer identifier | CLI/Web | S | 逆効果 | 誤判定訂正と再発防止 | project固有の中核価値 |
| tracking state変更 | CLI/Web | S | 逆効果 | active/stable/stalled等の運用 | 維持 |
| promotion deferral | CLI/Web queue | B | 小〜中 | queueの一時保留履歴 | queue運用で実際に使うなら維持 |
| merge | CLI/Web | S | 逆効果 | 重複projectの統合 | transaction/preview/aliasを含め維持 |
| split identifier | CLI/Web | S | 逆効果 | 誤結合の回復 | 維持 |
| separate project guard | CLI/Web | A | 逆効果 | 再merge提案の抑止 | 維持 |
| batch merge | CLI | C | 中 | 複数安全候補の一括処理 | 実行頻度と事故riskを確認。対話的mergeだけで足りるなら削除候補 |
| batch tracking state変更 | CLI | C | 小〜中 | 粗い一括整理 | 使用履歴が無ければ削除候補 |

## 7. Review、queue、説明

| 機能 | 接続状況 | 重要度 | 削除効果 | 手動代替・損失 | 所見 |
|---|---|---:|---:|---|---|
| project list/search/filter | CLI/Web | A | 逆効果 | DBを直接query | 維持 |
| project detail | Web | A | 逆効果 | JSON/CLIを個別確認 | 日常判断の主画面 |
| review queue | CLI/Web | S | 逆効果 | 全projectを人が走査 | 主成果物に近い。再設計対象であって削除対象ではない |
| promotion queue | CLI/Web | A | 中 | 紹介候補を手動検索 | 紹介workflowとの接続が強い |
| stalled review | CLI/Web worker | B | 中 | 更新頻度を手動調整 | scheduler all-state maintenanceとの利用状況で判断 |
| ignore review | CLI/Web worker | B | 中 | noiseを手動整理 | DB規模が増えるほど価値が上がる |
| out-of-scope review | CLI/Web worker | B | 中 | 紹介対象外を手動整理 | briefの対象境界と接続 |
| targeted identifier/relation/account review | Web detail | A | 中 | raw evidenceを読んでCLI操作 | 誤判定訂正の通常導線。維持 |
| Explain | CLI/Web | B | 小〜中 | 保存済みstateを人が解読 | trust/evaluationが複雑な間は維持 |
| service/ownership comparison | Web | A | 中 | evidenceを別々に確認 | conservative判断を支える |
| merge pair review | CLI/Web | A | 中 | source/targetを手で比較 | merge事故防止のため維持 |
| profile name/notes edit + preview | Web | B | 小 | CLI/直接編集 | 日常利用があるなら維持 |
| raw project tab | Web | C | 小 | CLI `show` | troubleshooting用途。通常利用が無ければ削除候補 |

## 8. Background operationとWeb運用

| 機能 | 接続状況 | 重要度 | 削除効果 | 手動代替・損失 | 所見 |
|---|---|---:|---:|---|---|
| subprocess worker | Web enrich/sync/review | A | 大 | CLIを別terminalで実行 | 長時間処理とWebを両立するため維持価値が高い |
| progress/event/result contract | CLI/Web/worker | A | 逆効果 | stdout解析と個別処理 | workerを残す限り維持 |
| cooperative cancellation | enrich/sync/review | A | 逆効果 | process強制終了 | partial save保護のため維持 |
| enrich checkpoint/resume | CLI/Web | A | 中 | 長時間runを最初から再実行 | 30時間級運用では重要 |
| in-memory job registry | Web | B | 中 | process/terminalを直接監視 | Web workerを残すなら必要 |
| scheduled presets | Web | C〜B | 大 | cron/手動CLI | 実際に定期運用するならB。使っていなければ大きな削除候補 |
| schedule persistence/restart retry | Web | C〜B | 中 | 再起動後に手動再開 | schedulerと一体で判断 |
| Web manual reload | Web Settings | C | 小〜中 | serverをterminalから再起動 | 使用頻度が低ければ削除候補 |
| request/job diagnostics | Web | B | 逆効果 | 再現困難な障害調査 | 少量で効果が高いので維持 |
| Settings build/DB/runtime表示 | Web | C | 小 | terminal確認 | 削除効果が小さいため後回し |
| API token/local access guard | Web | A | 逆効果 | local/LAN writeの保護 | Web writeを残す限り維持 |

## 9. Investigation history、replay、corpus

| 機能 | 接続状況 | 重要度 | 削除効果 | 削除時に失うもの | 所見 |
|---|---|---:|---:|---|---|
| investigation run viewer | Web | B | 中 | CLI/SQLで履歴確認 | 誤判定の説明に使うなら維持 |
| network-free replay | CLI/Web | A | 中 | heuristic変更前後の比較 | 保存履歴の価値を回収する機能。維持 |
| benchmark corpus | CLI/test | A | 逆効果 | semantic regression検知 | case数は少ないが削除しない |
| operator correction fixture | benchmark/test | A | 逆効果 | known guard退行検知 | 拡張対象 |
| fixture export/sanitizer | CLI | B | 中 | corpus追加を手作業で再構築 | gold setを増やす間は維持 |
| bounded evidence display | Web | B | 逆効果 | DB/ブラウザ負荷増加 | viewerを残す限り必要 |

## 10. Maintenance、audit、開発補助

旧DBやbackupを通常機能として再open/再repairする導線は維持しません。障害調査で必要になった場合は、
恒久commandではなく `.tmp/` の使い捨てscriptや一時SQLで扱います。削除対象を判断する時は、旧互換・移行痕跡と、
現行仕様の安全弁としてのfallbackを混同しません。

| 機能 | 接続状況 | 重要度 | 削除効果 | 所見・削除条件 |
|---|---|---:|---:|---|
| `init-db` | CLI | A | 小 | 明示初期化の安全境界。維持 |
| `repair-db` | CLI | A | 中 | 現行state再評価の通常maintenance。維持 |
| `audit-url-fields` | CLI read-only | B | 小 | URL contract変更時に有用 |
| `audit/repair-trust-graph` | CLI | A | 中 | trust変更とDB監査に必要。trust graphと一体 |
| `audit-source-config` | CLI interactive | A | 中 | multi-tenant suffix変更時の誤merge防止 |
| `prune-investigation-history` | CLI | B | 小〜中 | history増加管理。維持 |
| `cleanup-storage-payloads` | CLI | B（当面） | 中〜大 | 2026-08-10または1,000 run再計測まで維持。その後は定常maintenanceか削除か判断 |
| `set-tracking-state-batch` | CLI | C | 小〜中 | 実運用で繰り返し使わなければ削除候補 |
| `merge-batch` | CLI | C | 中 | 安全な一括merge需要が無ければ対話的mergeへ統合候補 |
| `assist-db-edit` | CLI | D〜C | 中 | 最有力削除候補。定型command作成を実際に減らしているか確認する |
| wrapper / worktree runtime選択 | CLI/Web/test | A | 逆効果 | fresh checkoutの再現性。維持 |
| test suite | 開発受入条件 | A | 逆効果 | regression耐性。件数ではなく重複だけ整理する |

maintenance commandを削除する時は、単に「正本DBへ一度適用した」だけで決めません。次を満たす必要があります。

1. 新規write pathに再発防止がある。
2. 正本DB再dry-runが0件である。
3. backupや古いcloneを再利用する必要がない、または旧DB非対応を明示する。
4. status、利用文書、completion、testからcommandをまとめて削除する。

## 11. 将来候補・未接続

次は現在の機能規模へ含めません。実装を始めなければ削除効果もありません。

- app record / app lexicon export
- FTS、自然語検索、saved query
- relation / ownership graph UI
- PWA / native app / multi-user化
- job event SQLite永続化
- sync/review checkpoint resume
- OAuth `include:`再帰解決
- Tangled timeline/community/botの通常接続
- schema v3 / incremental migration runner
- LLM model comparison harness

棚卸し上の最優先は、これらを「便利そう」という理由だけで実装しないことです。

## 削除候補の優先順位

### 1. 旧互換・移行痕跡

| 候補 | 期待効果 | 判断に必要な確認 |
|---|---:|---|
| 未使用の旧payload補完関数 | 小〜中 | 現行コードから未接続で、保存契約やfixtureで不要か |
| 移行専用metadata / legacy命名 | 小〜中 | 現行DBや通常payloadに残っていないか。現行意味がある場合は名前か説明を直す |
| 役割終了済みrepair command | 中 | 再発防止、正本DB再dry-run 0件、旧DB非対応の明示 |

`legacy_resource_urls` source名は2026-07-13にactive `evidence.resource_url_records`から削除済みです。
`llm_source.evidence`内の過去入力snapshotには履歴として残します。

### 2. 利用実績が無ければ削除しやすい

| 候補 | 期待効果 | 判断に必要な確認 |
|---|---:|---|
| `assist-db-edit` | 中 | 過去1〜2か月に何回使い、手動command作成を何分減らしたか |
| Tangled optional adapter parser | 小〜中 | 有効化予定または固有の候補sourceがあるか |
| batch tracking / batch merge | 中 | 繰り返し使うoperationか、一度きりか |

Web raw tabとWeb manual reloadは現運用で利用中のため、現時点の削除候補から外します。

### 3. 運用形態によって大きく削れる

| 候補 | 期待効果 | 判断に必要な確認 |
|---|---:|---|
| anonymous browser verification | 中 | static/deepでは得られない採否変更を何件生んだか |
| Semble source lane | 中 | 他sourceにない有用候補を何件得たか |
| Tangled source lane | 中 | 他sourceにないrepo/ownership evidenceを何件得たか |
| LLM planning/triage | 大 | gold set精度とoperator時間がdeterministic pathより改善するか |

scheduled presetsは現運用で利用中のため、削除候補から外します。

### 4. 役割終了後に削除できるmaintenance

- 将来、定常利用しないと決めた場合の`cleanup-storage-payloads`

`backfill-derived-identifiers`、`repair-same-site-evidence`、`cleanup-app-links`は役割を終了し、定常責務を保存契約または設定変更serviceへ移して削除しました。
旧machine noteの読込時変換とcollector旧projection fallbackも、正本DBの現行形式への移行後に削除しました。

## 削除しない方がよいもの

- normalized identifier / relation / decision
- operator confirm/block/deferとseparate guard
- merge/split transactionとalias lineage
- conservative identity/trust policy
- failure classification
- service/resource/LLM/historyの保存budget
- checkpoint/cancellation（bulk enrichを続ける限り）
- replay benchmarkと既知誤判定fixture

これらは行数を減らせますが、減った分以上に誤判定、DB事故、再調査、手動復旧が増えます。

## 実際の棚卸し手順

### Step 1: 4周期だけ利用実績を記録する

新しいtelemetry機能は作らず、週次または通常運用4回について次だけ記録します。

- 実行したcommand / Web operation。
- sourceごとの新規候補数と、他sourceにない候補数。
- operatorが深掘り対象にした件数。
- manual correction件数。
- operation時間と、手動代替した場合の概算時間。

### Step 2: 機能ごとに判定する

- **維持**: 中核または固有の成果が確認できた。
- **凍結**: 現在動くが、追加投資はしない。
- **統合**: 独立command/UIである必要はない。
- **sunset**: 役割終了条件を満たしたら削除する。
- **削除**: 利用なし、固有成果なし、安全上の役割なし。

### Step 3: 一度に一つのsubsystemを削除する

削除前後で次を確認します。

- `bin/project-registry-test`
- `benchmark-investigation`
- CLI/Web helpと関連文書
- 保存fieldやDB rowを孤立させないこと
- source削除の場合、最後のwatermark/config/historyを保持するか消すか

## 現時点の推奨

今すぐ大規模削除を始めるより、次の順が安全です。

1. `assist-db-edit`とCLI batch操作の削除可否を確認する。
2. Semble/Tangled/browser laneの固有成果を4周期測る。
3. `cleanup-storage-payloads`の再計測条件を待つ。
4. manual gold setを増やし、LLM planning/triageの有無を比較する。

この順なら、projectの中核と安全性を崩さず、削除効果の大きい補助subsystemから判断できます。
