# Project Registry CLIガイド

`project-registry`の現行公開コマンドについて、利用者に見える入力、出力、外部アクセス、DB変更をまとめます。正確なoption名と既定値は`project-registry COMMAND --help`を正本とし、この文書は挙動と使い分けを説明します。

日常フローだけを知りたい場合は[利用ガイド](guide.md)、画面操作は[Web UIガイド](web-ui.md)を参照してください。

## 共通の入力と出力

- projectは`--project-key`または既知seedで指定する。project keyは一意なprefixでもよい。
- seed typeは`nsid`、`repo_url`、`service_url`。値が曖昧な場合は`--type`を指定する。
- tracking stateは`active`、`stable`、`stalled`、`out_of_scope`、`archived`、`ignore`。
- 多くのコマンドはstdoutへJSONを出す。`show`と`explain`はtext/JSON、`enrich`はJSON Lines。
- 引数errorは終了コード`2`、処理失敗は通常`1`。bulk enrichの協調停止は`130`。

`init-db`以外のCLIコマンドはDB path optionを持たず、`data/survey.db`を使います。`init-db --db-path`で作った別DBは、主に`project-registry-web --db-path`で参照する検証用です。

tracking stateの利用上の意味は次の通りです。

| state | 意味 |
| --- | --- |
| `active` | 通常の調査・review対象。 |
| `stable` | 稼働は安定しており、調査頻度を下げる。 |
| `stalled` | 立ち消え気味だが、archivedと確定するほどではない。 |
| `out_of_scope` | atprotoとの関係は保持するが、現在の紹介・重点調査対象外。 |
| `archived` | 終了・保管扱い。 |
| `ignore` | survey対象として追わないnoise。 |

### 副作用の表記

| 表記 | 意味 |
| --- | --- |
| なし | DBを書かず、外部networkも使わない。 |
| network | DBは書かないが外部sourceへ接続する。 |
| 常時write | 実行するとDBまたは指定fileを変更する。 |
| `--execute` | 既定はdry-runで、指定時だけDBまたは設定を変更する。 |
| interactive | 確認した項目だけ変更する。 |

## 登録・調査・表示

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `init-db` | `--db-path`。既定は`data/survey.db` | 作成したschema情報のJSON | 新規DBを作成。既存DBのmigrationや上書きはしない |
| `add-seed` | seed、任意の`--type`、既存projectの`--project-key` | 登録先projectのJSON | 常時write |
| `enrich` | `--project-key`、`--seed`、`--all`のいずれか。調査深度、対象state、LLM、checkpoint option | 進捗、failure、結果のJSON Lines | network、LLM、常時write |
| `show` | project、seed、または`--all`。任意のtracking filter | textまたはJSON | なし |
| `explain` | project key、topic、format | 保存済み結論の根拠をtextまたはJSON | なし |

`show --seed`は登録済みseed値からprojectを解決します。seedをproject keyとして解釈しません。曖昧なURLは`--type`を指定してください。

`explain`のtopicは`all`、`service_status`、`ownership`、`contribution`、`merge_candidate`、`trust`です。保存済みstateと共通評価serviceだけを使い、networkやLLMを呼びません。

### bulk enrichとcheckpoint

```bash
project-registry enrich --all
project-registry enrich --all --new-only
project-registry enrich --all --retry-failures
project-registry enrich --all --run-id weekly-20260714
project-registry enrich --all --resume-run weekly-20260714
```

`--all`は既定でresumable checkpointを`data/enrich-runs/`へ作ります。開始時の対象project key、完了済みkey、failure、再開optionを保存しますが、LLM API keyは保存しません。

1 projectが失敗しても既定では残りを続行し、run全体は最終的にfailed、終了コード`1`になります。`--fail-fast`は最初の失敗後に止めます。Ctrl+Cは処理中projectの完了・保存後に止まり、再開commandを出して終了コード`130`を返します。即時停止ではないため、停止までの時間は外部requestのtimeoutに依存します。

既定対象外のtracking stateは`--include-*`、明示列挙は`--tracking-states`、全stateは`--all-tracking-states`で含めます。

## reviewとpromotion

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `review-queue` | tracking state、signal kind、limit、`--interactive` | 次に人が確認すべき候補のJSON | 既定なし。interactiveで選んだactionはwrite |
| `promotion-queue` | tracking state、promotion kind、limit、personal toolの包含、`--interactive` | 未紹介候補のJSON | 既定なし。interactiveで選んだactionはwrite |
| `review-stalled` | activity日数、enrich鮮度、`--execute`または`--interactive` | tracking state変更候補のJSON | 既定なし。execute/interactiveでwrite |
| `review-ignore` | state、limit、note/reason、実行方式 | noise候補のJSON | 既定なし。execute/interactiveでwrite |
| `review-out-of-scope` | state、limit、note/reason、実行方式 | scope外または復帰候補のJSON | 既定なし。execute/interactiveでwrite |

`review-stalled`はactivityと保存済み調査の鮮度から`active`、`stable`、`stalled`間の変更を提案します。`review-ignore`はatproto projectとして追わないnoise、`review-out-of-scope`は関係を保持したまま現在の調査対象外にする候補です。

`--interactive`では1件ずつ確認します。skipはその実行中だけ先送りし、DBへ書きません。`--execute`は表示対象の候補を一括適用します。

`review-queue --kinds`の値は、候補になった理由を表します。

| kind | 意味 |
| --- | --- |
| `service_candidate_needs_confirmation` | 独立service候補はあるが、websiteとして未確認。 |
| `candidate_repo_needs_confirmation` | repository候補はあるが、source codeとして未確認。 |
| `llm_manual_review_recommended` | LLM評価が具体的なmanual review種別と理由を返した。 |
| `service_now_usable_but_needs_editorial_review` | serviceは利用可能だが、summary/tagline/category等が不足。 |
| `more_evidence_than_current_classification` | identifier候補やNSIDが増え、現在の分類よりevidenceが多い。 |
| `stale_metadata_with_recent_activity` | 最近activityがある一方、説明またはservice statusが古い・未確定。 |
| `service_status_unverified_but_promising` | login/app/beta等のsignalがあるがstatusは未確認。 |
| `category_recheck_needed` | lexicon分類だがservice/repositoryの公開面も見つかった。 |
| `external_lexicon_dependency_needs_review` | owned NSIDと外部lexicon依存が併存し、ownership確認が必要。 |
| `high_information_density` | NSID、schema sample、record sampleが多く、人が見る情報量が大きい。 |
| `many_identity_candidates` | service、repository、manual seed候補が多く、identity整理が必要。 |

`promotion-queue --kinds`は次の3種類です。

| kind | 意味 |
| --- | --- |
| `service_ready_for_introduction` | 原則`category=project`で、service statusが`live`、`beta`、`invite_only`。 |
| `lexicon_ready_for_introduction` | lexiconにNSID hintと公開先があり、紹介metadataの大きな欠落がない。 |
| `technical_insight_candidate` | 実用性とは別に、設計・相互運用・参照実装として特に見る価値がある。 |

`category=personal_tool`は既定ではservice紹介候補から除外し、`--include-personal-tools`で含めます。promotion holdは期限後に自動でqueueへ戻り、履歴は残ります。`defer-promotion`で日数を省略した場合はproject ageと過去のhold回数から期間を自動決定します。

## importと外部sourceのsync

| コマンド | sourceと入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `import-seed-list` | 見出しやメモを含められるplain text file | parse、match、作成候補のJSON | `--execute` |
| `sync-introduced` | Bluesky post検索。query、page budget、lookback、incremental | 紹介URLとのmatch結果のJSON | network。`--execute`でintroduced情報をwrite |
| `sync-github-search` | GitHub repository検索。query、page budget、screening | repoのmatch・作成候補のJSON | network。`--execute`でwrite |
| `sync-collections` | collection inventory URL。lookback、保存先、item上限 | collectionのmatch・作成候補のJSON | network。`--execute`でwrite。`--save-path`はfile write |
| `sync-semble-collections` | 設定済みSemble collection。page budget、lookback | card由来seedのmatch・作成候補のJSON | network。`--execute`でwrite |
| `sync-tangled-repos` | UFOs rolling window。item上限、incremental | Tangled repoのmatch・作成候補のJSON | network。`--execute`でwrite |

syncは`--execute`なしでも外部networkへ接続します。dry-run、失敗、不完全、上限で打ち切ったrunは成功watermarkを進めません。成功したexecuteだけがsource別の増分stateを保存します。`--all-history`は保存watermarkや通常lookbackを無視しますが、source自身がrolling windowしか提供しない場合は完全履歴にはなりません。

`--create-unmatched`は未知seedからprojectを作るかを制御します。GitHub、collection inventory、Sembleは既定on、Tangledは既定offです。詳細なqueryやURLの一時上書きはCLIで行い、Web UIは設定済みsourceだけを実行します。

sourceのenabled、既定URL/query、scope、request budgetは`config/sources.json`から読み、CLI optionはそのrunだけ上書きします。disabled sourceはnetwork request前に拒否されます。matching/classification policyはconfigでは変更できません。

syncのJSONでは、既存projectへのmatch、作成候補/作成結果、未一致または弱い一致のsuggestion、screening除外、走査境界・完全性、watermark更新の有無を分けて確認してください。弱いhost family一致だけでは既存projectへ自動attachせず、suggestionとして返す経路があります。`--no-incremental`は保存watermarkを読み書きせず、`--all-history`がなければ初回lookbackを適用します。

## operator判断とproject修正

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `set-tracking-state` | project、tracking state、任意note | 保存したstateのJSON | 常時write |
| `set-separate` | 2 project、任意の`--clear` | separation guardのJSON | 常時write |
| `block-identifier` | project、identifier type/value、note、`--clear` | block decisionのJSON | 常時write |
| `defer-identifier` | project、identifier type/value、日数、note、`--clear` | defer decisionのJSON | 常時write |
| `defer-promotion` | project、日数、理由、`--clear` | promotion holdのJSON | 常時write |
| `split-project` | source project、identifier type/value、note | 移動対象と新projectのpreview/結果 | `--execute` |
| `inspect-merge-pair` | sourceとtarget project | pair evidenceとmerged previewのJSON | なし |
| `merge` | source、明示targetまたはrecommendation、`--force` | merged preview/結果のJSON | `--execute` |

`set-separate`はpairをmerge候補計算から除外します。`block-identifier`は次回normalize/enrichで同じidentifierが再付着するのを防ぎます。`defer-identifier --clear`はguardを外すだけで、identifierを自動confirmしません。`defer-promotion --clear`もactive holdだけを解除し、履歴は残します。

`split-project --identifier-type domain`はauthority domain、そこから導出されるNSID、同じhostのconfirmed service URLをまとめて切り出します。repo URLは自動移動しません。実行時は元projectへの再付着blockと新旧projectのseparation guardも保存します。

`merge`は既定dry-runです。sourceをtargetへ統合する方向をpreviewで確認してください。現在のrecommendationが自動適用可能でない場合、`--force`なしのexecuteは拒否します。

executeはtarget更新、source key alias、separation参照更新、source削除を1 transactionで行います。merge後も旧source keyは存続projectへ解決され、旧source keyで保存されたinvestigation runも存続projectの履歴から参照できます。当時のsnapshot内project keyは書き換えません。

## audit、repair、storage maintenance

| コマンド | 確認・変更対象 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `audit-url-fields` | URL field利用数と保存invariant違反 | anomalyと例のJSON | なし |
| `audit-trust-graph` | confirmed identifierのtrust根拠 | issueと例のJSON | なし |
| `repair-db` | project payload正規化、派生値refresh、collision | 変更予定件数のJSON | `--execute` |
| `repair-trust-graph` | trust graph再計算。任意でunsupported confirmation降格 | 変更予定件数のJSON | `--execute` |
| `cleanup-storage-payloads` | current evidenceとinvestigation payloadのbudget再適用 | byte・件数差分のJSON | `--execute`、任意の`--vacuum` |
| `prune-investigation-history` | 保持期間外run。ただしprojectごとの最新件数を保持 | 削除候補のJSON | `--execute`、任意の`--vacuum` |
| `audit-source-config` | custom multi-tenant suffixと既存projectのsplit候補 | JSONまたはinteractive候補 | 既定interactive。確認/`--execute`で設定・stateをwrite。splitは自動適用しない |

maintenance系は正本DBのbackupを作り、Web UI、worker、enrich、別CLIを止めてからdry-runとexecuteを分けて実行してください。`--vacuum`はwrite transaction後の別処理で、失敗しても先行するrewrite/deleteはrollbackされません。

`repair-trust-graph --execute`は通常trust edge/evaluationだけを保存します。confirmed identifierの降格には`--downgrade-unsupported`も必要です。

`audit-source-config`は既定interactiveです。suffix追加を確認した後、保存済みcurrent service probeを新policyで再評価し、split候補を表示します。選んだtenant groupだけを分割します。`--no-interactive`はJSON dry-run、`--execute --no-interactive`はsuffixとcurrent stateを更新しますがsplitは適用しません。

## 調査・開発補助

これらは日常の登録・reviewには通常不要で、collectorやevaluationの回帰確認に使います。

| コマンド | 入力と出力 | 副作用 |
| --- | --- | --- |
| `replay-investigation` | run IDまたはprojectの最新runを読み、保存済みevidenceを現行ruleで再評価してJSON出力 | なし。network/collectorも使わない |
| `benchmark-investigation` | fixture corpus pathを読み、semantic regressionをJSON出力 | なし |
| `export-investigation-fixture` | 保存runをsanitizationし、stdoutまたは`--output`へ出力 | DBなし。`--output`はfile write |
| `verify-service` | project/seed、任意URL、`static|deep|browser` lane | network。`--execute`で評価とreplay可能runをDBへ保存 |

exportのsanitizationは機械的な補助です。実project key、時刻、handle/DID、自由記述が残り得るため、fixtureとしてcommitする前に必ず人が確認してください。

`verify-service`の`static`はtop page、`deep`は同一siteの追加page、`browser`はJavaScript描画後の匿名画面とlogin/signup到達を確認します。認証入力、登録、投稿はしません。browser laneにはPlaywrightの任意依存が必要です。

## 現在の整理対象

CLIはWeb UIより低水準な操作や開発補助も公開しています。機能削減の検討中も、削除が確定して実装から外れるまでは、この文書と`--help`で現行の入出力を説明します。削除候補を通常フローへ積極的に案内する意図はありません。
