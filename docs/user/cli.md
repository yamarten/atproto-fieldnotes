# Project Registry CLIガイド

`project-registry`の現行公開コマンドについて、利用者に見える入力、出力、外部アクセス、DB変更をまとめます。正確なオプション名と既定値は`project-registry COMMAND --help`を正本とし、この文書は挙動と使い分けを説明します。基本用語は[利用ガイド](guide.md#基本用語)を参照してください。

日常フローだけを知りたい場合は[利用ガイド](guide.md)、画面操作は[Web UIガイド](web-ui.md)を参照してください。

## 共通の入力と出力

- 登録プロジェクトは`--project-key`または既知のシードで指定する。プロジェクトキーは一意な接頭辞でもよい。
- シードの種類は`nsid`、`repo_url`、`service_url`。値が曖昧な場合は`--type`を指定する。
- 追跡状態（`tracking_state`）は`active`、`stable`、`stalled`、`out_of_scope`、`archived`、`ignore`。
- 多くのコマンドはstdoutへJSONを出す。`show`と`explain`はtext/JSON、`enrich`はJSON Lines。
- 引数エラーは終了コード`2`、処理失敗は通常`1`。一括`enrich`の協調停止は`130`。

`init-db`以外のCLIコマンドはDBのパスを指定するオプションを持たず、`data/survey.db`を使います。`init-db --db-path`で作った別DBは、主に`project-registry-web --db-path`で参照する検証用です。

追跡状態の利用上の意味は次の通りです。

| 保存値 | 意味 |
| --- | --- |
| `active` | 通常の調査・要レビュー項目の対象。 |
| `stable` | 稼働は安定しており、調査頻度を下げる。 |
| `stalled` | 立ち消え気味だが、archivedと確定するほどではない。 |
| `out_of_scope` | atprotoとの関係は保持するが、現在の紹介・重点調査対象外。 |
| `archived` | 終了・保管扱い。 |
| `ignore` | 調査対象として追わないノイズ。 |

### 副作用の表記

| 表記 | 意味 |
| --- | --- |
| なし | DBを書かず、外部ネットワークも使わない。 |
| 外部接続 | DBは書かないが外部情報源へ接続する。 |
| 常時書き込み | 実行するとDBまたは指定ファイルを変更する。 |
| `--execute` | 既定はdry-runで、指定時だけDBまたは設定を変更する。 |
| `--interactive` | 確認した項目だけ変更する。 |

## 登録・調査・表示

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `init-db` | `--db-path`。既定は`data/survey.db` | 作成したschema情報のJSON | 新規DBを作成。既存DBのmigrationや上書きはしない |
| `add-seed` | シード、任意の`--type`、既存登録プロジェクトの`--project-key` | 登録プロジェクトのJSON | 常時書き込み |
| `enrich` | `--project-key`、`--seed`、`--all`のいずれか。調査深度、対象の追跡状態、LLM、チェックポイントのオプション | 進捗、失敗、結果のJSON Lines | 外部接続、LLM、常時書き込み |
| `show` | 登録プロジェクト、シード、または`--all`。任意の追跡状態フィルター | textまたはJSON | なし |
| `explain` | プロジェクトキー、topic、format | 保存済み結論の根拠をtextまたはJSON | なし |

`show --seed`は登録済みのシード値から登録プロジェクトを解決します。シードをプロジェクトキーとして解釈しません。曖昧なURLは`--type`を指定してください。

`explain`のtopicは`all`、`service_status`、`ownership`、`contribution`、`merge_candidate`、`trust`です。保存済み状態と共通評価処理だけを使い、ネットワークやLLMを呼びません。

### 一括`enrich`とチェックポイント

```bash
project-registry enrich --all
project-registry enrich --all --new-only
project-registry enrich --all --retry-failures
project-registry enrich --all --run-id weekly-20260714
project-registry enrich --all --resume-run weekly-20260714
```

`--all`は既定で再開可能なチェックポイントを`data/enrich-runs/`へ作ります。開始時の対象プロジェクトキー、完了済みキー、失敗、再開オプションを保存しますが、LLM API keyは保存しません。

1件の登録プロジェクトが失敗しても既定では残りを続行し、一括実行全体は最終的に`failed`、終了コード`1`になります。`--fail-fast`は最初の失敗後に止めます。Ctrl+Cは処理中の登録プロジェクトの完了・保存後に止まり、再開コマンドを出して終了コード`130`を返します。即時停止ではないため、停止までの時間は外部リクエストのタイムアウトに依存します。

既定対象外の追跡状態は`--include-*`、明示列挙は`--tracking-states`、全状態は`--all-tracking-states`で含めます。

## 要レビュー一覧と紹介候補一覧

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `review-queue` | 追跡状態、理由（signal kind）、件数上限、`--interactive` | 要レビュー項目のJSON | 既定なし。対話操作で選んだ処理は書き込み |
| `promotion-queue` | 追跡状態、紹介候補の種類、件数上限、personal toolの包含、`--interactive` | 紹介候補のJSON | 既定なし。対話操作で選んだ処理は書き込み |
| `review-stalled` | 活動日数、`enrich`の鮮度、`--execute`または`--interactive` | 追跡状態の変更候補JSON | 既定なし。execute/interactiveで書き込み |
| `review-ignore` | 状態、件数上限、note/reason、実行方式 | 追跡対象外にする候補のJSON | 既定なし。execute/interactiveで書き込み |
| `review-out-of-scope` | 状態、件数上限、note/reason、実行方式 | 調査範囲外または復帰候補のJSON | 既定なし。execute/interactiveで書き込み |

`review-stalled`は活動状況と保存済み調査の鮮度から`active`、`stable`、`stalled`間の変更を提案します。`review-ignore`はatprotoの登録プロジェクトとして追わない候補、`review-out-of-scope`は関係を保持したまま現在の調査対象外にする候補です。

`--interactive`では1件ずつ確認します。skipはその実行中だけ先送りし、DBへ書きません。`--execute`は表示対象の候補を一括適用します。

`review-queue --kinds`の値は、要レビューとなった理由を表します。

| kind | 意味 |
| --- | --- |
| `service_candidate_needs_confirmation` | 独立サービス候補はあるが、websiteとして未採用。 |
| `candidate_repo_needs_confirmation` | リポジトリ候補はあるが、source codeとして未採用。 |
| `llm_manual_review_recommended` | LLM評価が、利用者による確認が必要な具体的な種類と理由を返した。 |
| `service_now_usable_but_needs_editorial_review` | サービスは利用可能だが、summary/tagline/category等が不足。 |
| `more_evidence_than_current_classification` | 識別子候補やNSIDが増え、現在の分類より根拠が多い。 |
| `stale_metadata_with_recent_activity` | 最近活動がある一方、説明またはサービス状態が古い・未確定。 |
| `service_status_unverified_but_promising` | login/app/beta等の兆候があるが状態は未確認。 |
| `category_recheck_needed` | lexicon分類だがサービスやリポジトリの公開情報も見つかった。 |
| `external_lexicon_dependency_needs_review` | 所有NSIDと外部lexicon依存が併存し、所有関係の確認が必要。 |
| `high_information_density` | NSID、schema sample、record sampleが多く、人が見る情報量が大きい。 |
| `many_identity_candidates` | サービス、リポジトリ、手動シードの候補が多く、同一性の整理が必要。 |

`promotion-queue --kinds`で選べる紹介候補は次の4種類です。

| kind | 意味 |
| --- | --- |
| `service_surface_confirmed` | 原則`category=project`で、サービス状態が`live`、`beta`、`invite_only`、confidenceがmedium以上で、login、app shell、browser到達などのservice surfaceを確認済み。紹介確定を意味しない。 |
| `service_candidate_needs_validation` | service URLと稼働statusの候補はあるが、confidenceが低いか稼働面が未確認。上記より低い優先度で残す。 |
| `lexicon_ready_for_introduction` | lexiconにNSID hintと公開先があり、紹介metadataの大きな欠落がない。 |
| `technical_insight_candidate` | 実用性とは別に、設計・相互運用・参照実装として特に見る価値がある。 |

`category=personal_tool`は既定ではサービスの紹介候補から除外し、`--include-personal-tools`で含めます。紹介保留は期限後に自動で一覧へ戻り、履歴は残ります。`defer-promotion`で日数を省略した場合は登録からの日数と過去の保留回数から期間を自動決定します。

## インポートと外部情報の取り込み

`sync-*`コマンドは、設定済みの外部情報源を走査して照合結果や新規登録候補を作ります。`--execute`なしでは取り込み候補を確認するだけで、DBへ反映しません。

| コマンド | 情報源と入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `import-seed-list` | 見出しやメモを含められるプレーンテキストファイル | 解析、照合、作成候補のJSON | `--execute` |
| `sync-introduced` | Bluesky投稿検索。検索条件、ページ上限、遡る期間、増分実行 | 紹介URLとの照合結果のJSON | 外部接続。`--execute`でintroduced情報を書き込み |
| `sync-github-search` | GitHubリポジトリ検索。検索条件、ページ上限、事前選別 | リポジトリの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |
| `sync-collections` | collection一覧URL。遡る期間、保存先、項目上限 | collectionの照合・作成候補のJSON | 外部接続。`--execute`で書き込み。`--save-path`はファイル書き込み |
| `sync-semble-collections` | 設定済みSemble collection。ページ上限、遡る期間 | カード由来シードの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |
| `sync-tangled-repos` | UFOsの取得期間。項目上限、増分実行 | Tangledリポジトリの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |

外部情報の取り込みは`--execute`なしでも外部ネットワークへ接続します。dry-run、失敗、不完全、上限で打ち切った実行は成功時点を示すwatermarkを進めません。成功したexecuteだけが情報源別の増分状態を保存します。`--all-history`は保存済みwatermarkや通常の遡る期間を無視しますが、情報源自身が一定期間のデータしか提供しない場合は完全履歴にはなりません。

`--create-unmatched`は未知のシードから登録プロジェクトを作るかを制御します。GitHub、collection一覧、Sembleは既定on、Tangledは既定offです。詳細な検索条件やURLの一時上書きはCLIで行い、Web UIは設定済みの外部情報源だけを実行します。

外部情報源の有効・無効、既定URL・検索条件、対象範囲、リクエスト上限は`config/sources.json`から読み、CLIオプションはその実行だけ上書きします。無効な情報源はネットワークリクエスト前に拒否されます。照合・分類方針は設定ファイルでは変更できません。

取り込み結果のJSONでは、既存の登録プロジェクトとの照合、作成候補・作成結果、未一致または弱い一致による提案、事前選別での除外、走査範囲と完全性、watermark更新の有無を分けて確認してください。ホストの弱い類似だけでは既存の登録プロジェクトへ自動追加せず、提案として返す経路があります。`--no-incremental`は保存済みwatermarkを読み書きせず、`--all-history`がなければ初回に遡る期間を適用します。

## 手動判断と登録プロジェクトの修正

| コマンド | 主な入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `set-tracking-state` | 登録プロジェクト、追跡状態、任意note | 保存した状態のJSON | 常時書き込み |
| `set-separate` | 2つの登録プロジェクト、任意の`--clear` | 別プロジェクト指定のJSON | 常時書き込み |
| `block-identifier` | 登録プロジェクト、識別子の種類・値、note、`--clear` | 除外判断のJSON | 常時書き込み |
| `defer-identifier` | 登録プロジェクト、識別子の種類・値、日数、note、`--clear` | 保留判断のJSON | 常時書き込み |
| `defer-promotion` | 登録プロジェクト、日数、理由、`--clear` | 紹介保留のJSON | 常時書き込み |
| `split-project` | 分割元の登録プロジェクト、識別子の種類・値、note | 移動対象と新規登録プロジェクトのプレビュー・結果 | `--execute` |
| `inspect-merge-pair` | マージ元とマージ先の登録プロジェクト | 2件の根拠とマージ結果プレビューのJSON | なし |
| `merge` | マージ元、明示したマージ先または推奨先、`--force` | マージ結果プレビュー・実行結果のJSON | `--execute` |

`set-separate`は2件をマージ候補の計算から除外します。`block-identifier`は次回の正規化や`enrich`で同じ識別子が再び付くのを防ぎます。`defer-identifier --clear`は保留を解除するだけで、識別子を自動採用しません。`defer-promotion --clear`も有効な紹介保留だけを解除し、履歴は残します。

`split-project --identifier-type domain`はauthority domain、そこから導出されるNSID、同じホストの採用済み（`confirmed`）サービスURLをまとめて切り出します。リポジトリURLは自動移動しません。実行時は分割元への再付着防止と、新旧の登録プロジェクトを別物とする手動判断も保存します。

`merge`は既定dry-runです。マージ元をマージ先へまとめる方向と結果のプレビューを確認してください。現在の推奨結果が自動適用可能でない場合、`--force`なしのexecuteは拒否します。

executeはマージ先の更新、マージ元キーのalias、別プロジェクト指定の参照更新、マージ元の削除を1 transactionで行います。マージ後も旧マージ元キーは存続する登録プロジェクトへ解決され、旧キーで保存された調査実行記録も存続側の履歴から参照できます。当時のsnapshot内プロジェクトキーは書き換えません。

## audit、repair、storage maintenance

| コマンド | 確認・変更対象 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `audit-url-fields` | URL field利用数と保存invariant違反 | anomalyと例のJSON | なし |
| `audit-trust-graph` | 採用済み（`confirmed`）識別子のtrust根拠 | 問題と例のJSON | なし |
| `repair-db` | 登録プロジェクトの保存データ正規化、派生値更新、衝突 | 変更予定件数のJSON | `--execute` |
| `repair-trust-graph` | trust graph再計算。任意で根拠不足の採用済み識別子を降格 | 変更予定件数のJSON | `--execute` |
| `cleanup-storage-payloads` | 現在の根拠と調査履歴への保存上限再適用 | byte・件数差分のJSON | `--execute`、任意の`--vacuum` |
| `prune-investigation-history` | 保持期間外の調査実行記録。ただし登録プロジェクトごとの最新件数を保持 | 削除候補のJSON | `--execute`、任意の`--vacuum` |
| `audit-source-config` | 独自multi-tenant suffixと既存登録プロジェクトの分割候補 | JSONまたは対話候補 | 既定interactive。確認/`--execute`で設定・状態を書き込み。分割は自動適用しない |

保守系コマンドは正本DBのバックアップを作り、Web UI、worker、`enrich`、別のCLIを止めてからdry-runとexecuteを分けて実行してください。`--vacuum`は書き込みtransaction後の別処理で、失敗しても先行する書き換えや削除はrollbackされません。

`repair-trust-graph --execute`は通常trust edge/evaluationだけを保存します。採用済み（`confirmed`）識別子の降格には`--downgrade-unsupported`も必要です。

`audit-source-config`は既定interactiveです。suffix追加を確認した後、保存済みcurrent service probeを新policyで再評価し、split候補を表示します。選んだtenant groupだけを分割します。`--no-interactive`はJSON dry-run、`--execute --no-interactive`はsuffixとcurrent stateを更新しますがsplitは適用しません。

## 調査・開発補助

これらは日常の登録や候補整理には通常不要で、収集処理や評価処理の回帰確認に使います。

| コマンド | 入力と出力 | 副作用 |
| --- | --- | --- |
| `replay-investigation` | 調査実行記録IDまたは登録プロジェクトの最新記録を読み、保存済みの根拠を現行規則で再評価してJSON出力 | なし。ネットワークや収集処理も使わない |
| `benchmark-investigation` | fixture corpus pathを読み、semantic regressionをJSON出力 | なし |
| `export-investigation-fixture` | 保存済み調査実行記録をsanitizationし、stdoutまたは`--output`へ出力 | DB変更なし。`--output`はファイル書き込み |
| `verify-service` | 登録プロジェクトまたはシード、任意URL、`static|deep|browser` lane | 外部接続。`--execute`で評価と再評価可能な調査実行記録をDBへ保存 |

exportのsanitizationは機械的な補助です。実プロジェクトキー、時刻、handle/DID、自由記述が残り得るため、fixtureとしてcommitする前に必ず人が確認してください。

`verify-service`の`static`はtop page、`deep`は同一siteの追加pageを確認します。`browser`はJavaScript描画後の匿名画面を開き、auth linkを辿った事実と、passwordまたはhandle/email入力を持つform、OAuth入口、描画後network/console errorを別々に記録します。linkを辿れただけではlogin surface確認済みにしません。認証入力、form送信、登録、投稿は行いません。browser laneにはPlaywrightの任意依存が必要です。

## 現在の整理対象

CLIはWeb UIより低水準な操作や開発補助も公開しています。機能削減の検討中も、削除が確定して実装から外れるまでは、この文書と`--help`で現行の入出力を説明します。削除候補を通常フローへ積極的に案内する意図はありません。
