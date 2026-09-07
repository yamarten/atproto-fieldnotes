# Project Registry CLIガイド

`project-registry`の現行公開コマンドについて、利用者に見える入力、出力、外部アクセス、DB変更をまとめます。正確なオプション名と既定値は`project-registry COMMAND --help`を正本とし、この文書は挙動と使い分けを説明します。基本用語は[利用ガイド](guide.md#基本用語)を参照してください。

日常フローだけを知りたい場合は[利用ガイド](guide.md)、画面操作は[Web UIガイド](web-ui.md)を参照してください。

## 共通の入力と出力

- 登録プロジェクトは`--project-key`または既知のシードで指定する。プロジェクトキーは一意な接頭辞でもよい。
- シードの種類は`nsid`、`repo_url`、`service_url`。値が曖昧な場合は`--type`を指定する。
- 追跡状態（`tracking_state`）は`active`、`stable`、`stalled`、`out_of_scope`、`archived`、`ignore`。
- 多くのコマンドはstdoutへJSONを出す。`show`と`explain`はtext/JSON、`enrich`はJSON Lines。
- 引数エラーは終了コード`2`、処理失敗は通常`1`。一括`enrich`の協調停止は`130`。

通常のregistry操作は`data/survey.db`を使います。例外として`init-db`と、read-onlyの開発補助`validate-app-record-export`、`export-app-records`、`export-llm-evaluation-corpus`、`compare-trust-policy`、`compare-merge-policy`、`compare-identifier-storage`は`--db-path`を取ります。`init-db --db-path`で作った別DBは、主に`project-registry-web --db-path`で参照する検証用です。

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
| `add-seed` | シード、任意の`--type`、既存登録プロジェクトの`--project-key` | 登録プロジェクトと投影したroleのJSON | 常時書き込み。NSID/service/repositoryは対応roleへconfirmed保存し、採用判断を記録 |
| `enrich` | `--project-key`、`--seed`、`--all`のいずれか。調査深度、対象の追跡状態、LLM、チェックポイントのオプション | 進捗、失敗、結果のJSON Lines | 外部接続、LLM、常時書き込み |
| `show` | 登録プロジェクト、シード、または`--all`。任意の追跡状態フィルター | textまたはJSON | なし |
| `explain` | プロジェクトキー、topic、format | 保存済み結論の根拠をtextまたはJSON | なし |
| `validate-app-record-export` | schema v3 DBと`--output` | `community.lexicon.app.entry`向けreadiness report | DBはread-only。指定JSONだけを書き込み |
| `export-app-records` | schema v3 DB、publication manifest、`--output` | PDS処理へ渡せるentry record配列 | DBはread-only。全対象の検証成功後に指定JSONだけを書き込み |

`show --seed`は登録済みのシード値から登録プロジェクトを解決します。シードをプロジェクトキーとして解釈しません。曖昧なURLは`--type`を指定してください。

`add-seed`へ渡したサービスURLはパスを保持します。同じシードを同じ登録プロジェクトへ再適用してもよく、旧データに不足するconfirmed service/repository roleを補えます。operatorが明示したservice URLには、`/about`、`/docs`、`/login`などのpath名による補助ページ判定を適用しません。同じURLのblock/deferは最新の明示登録で解除しますが、親URLに対するguardは他候補向けに残します。

`explain`のtopicは`all`、`service_status`、`ownership`、`contribution`、`merge_candidate`、`trust`です。保存済み状態と共通評価処理だけを使い、ネットワークやLLMを呼びません。

`validate-app-record-export`は紹介用profile、canonical scalar、confirmedなcurrent identifier/resourceから
公開候補を作り、その他の候補を診断用の除外入力として残します。projectごとにrequired/optionalの不足、
根拠不足、変換不能、仕様未確定、公開scopeを分け、`createdAt`をpublication contextとして報告します。
通常は`.tmp/`へ出力してください。詳細は
[App Record Export](../developer/app-record-export.md)を参照してください。

`export-app-records`は公開対象のproject keyとUTCの`createdAt`をmanifestで明示します。categoryや追跡状態から
自動選択しません。`scope_uncertain`はmanifestの別allowlistへ同じkeyを重ねて指定した場合だけ許可し、required
field不足、変換不能、`not_applicable`は許可しません。1件でも不適格なら出力しません。
`records`は要素ごとのproject keyやcollection wrapperを持たない裸のrecord配列です。targetは
`community.lexicon.app.entry`固定のため、成果物のtarget metadataやtarget指定optionもありません。

```bash
project-registry export-app-records \
  --db-path data/survey.db \
  --manifest .tmp/app-record-export/manifest.json \
  --output .tmp/app-record-export/records.json
```

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

`--llm-max-output-tokens`は、1回のplanning、confirmed repository解析、triageの各LLM requestに同じ最大出力token数を指定します。
省略時はendpoint側の設定を使います。model比較では、固定corpus評価と通常`enrich`で同じ値を明示してください。

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
| `service_candidate_needs_confirmation` | サービス候補はあるが、confirmed serviceとして未採用。Websiteの有無とは独立。 |
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
| `lexicon_publisher_confirmed` | confirmed owned NSIDが、authorityの`_lexicon` DNS委譲先にあるmatching schema recordとして取得できた。categoryには依存しない。 |
| `technical_insight_candidate` | 実用性とは別に、設計・相互運用・参照実装として特に見る価値がある。 |

`category=personal_tool`は既定ではサービスとLexicon発行の紹介候補から除外し、`--include-personal-tools`で含めます。Lexicon発行はNSID hintやrepository内schemaだけでは成立せず、通常enrichでDNS委譲とschema record取得に成功した観測を必要とします。紹介保留は期限後に自動で一覧へ戻り、履歴は残ります。`defer-promotion`で日数を省略した場合は登録からの日数と過去の保留回数から期間を自動決定します。

## インポートと外部情報の取り込み

`sync-*`コマンドは、設定済みの外部情報源を走査して照合結果や新規登録候補を作ります。`--execute`なしでは取り込み候補を確認するだけで、DBへ反映しません。

| コマンド | 情報源と入力 | 出力 | 副作用 |
| --- | --- | --- | --- |
| `import-seed-list` | 見出しやメモを含められるプレーンテキストファイル | 解析、照合、作成候補のJSON | `--execute` |
| `sync-bluesky-lexicons` | 設定済みBluesky公式repositoryの`app.bsky.*` / `chat.bsky.*` record schema | Bluesky projectへ追加するcollectionのJSON | 外部接続。`--execute`で書き込み。`com.atproto.*`は対象外 |
| `sync-introduced` | Bluesky投稿検索。検索条件、ページ上限、遡る期間、増分実行 | 紹介URLとの照合結果のJSON | 外部接続。`--execute`でintroduced情報を書き込み |
| `sync-github-search` | GitHubリポジトリ検索。検索条件、ページ上限、事前選別 | リポジトリの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |
| `sync-collections` | collection一覧URL。遡る期間、保存先、項目上限 | collectionの照合・作成候補のJSON | 外部接続。`--execute`で書き込み。`--save-path`はファイル書き込み |
| `sync-semble-collections` | 設定済みSemble collection。ページ上限、遡る期間 | カード由来シードの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |
| `sync-tangled-repos` | Jetstream v2 snapshot。sequence、項目上限、増分実行 | Tangledリポジトリの照合・作成候補のJSON | 外部接続。`--execute`で書き込み |

外部情報の取り込みは`--execute`なしでも外部ネットワークへ接続します。dry-run、失敗、不完全、上限で打ち切った実行は成功時点を示すwatermarkを進めません。成功したexecuteだけが情報源別の増分状態を保存します。`--all-history`は保存済みwatermarkや通常の遡る期間を無視しますが、情報源自身が一定期間のデータしか提供しない場合は完全履歴にはなりません。`sync-collections --all-history`は既知collectionも再処理し、collection inventory由来の保存済みobservationを現在の全payloadに置き換え、collection identifierと活動統計を再構築します。置換対象は結果JSONの`collection_observation_rebuild_project_count`と`collection_observation_cleanup_count`で確認できます。

`--create-unmatched`は未知のシードから登録プロジェクトを作るかを制御します。GitHub、collection一覧、Semble、Tangledは既定onです。詳細な検索条件やURLの一時上書きはCLIで行い、Web UIは設定済みの外部情報源だけを実行します。

Tangled syncにはNode.js 22.15以上、`npm install`で導入する公式`@bsky/jetstream` SDK、
`ATPROTO_SURVEY_JETSTREAM_API_KEY`が必要です。API keyは環境変数からだけ読み、設定、結果、watermarkへ保存しません。
`--all-history`はJetstream archiveの先頭から開始し、通常実行は保存済みsequenceから再開します。項目上限で途中終了した
executeは最後に処理したsequenceを保存するため、次回実行で続きから処理できます。設定上の通常上限は1,000 eventで、
初回archiveも複数runに分けて進めます。

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
| `migrate-repository-trust` | 旧ページ・repository信頼根拠をrelationへ整理する計画。`--db-path`と`--output`必須 | 分類付きJSON計画 | `--execute-plan <確認済み計画.json>`。未解決分は保持 |
| `repair-trust-graph` | trust graph再計算。任意で根拠不足の採用済み識別子を降格 | 変更予定件数のJSON | `--execute` |
| `cleanup-storage-payloads` | 現在の根拠と調査履歴への保存上限再適用 | byte・件数差分のJSON | `--execute`、任意の`--vacuum` |
| `cleanup-workspace-temp` | `.tmp`内の古いSQLite複製とWAL/SHM | 削除候補、容量、保持件数のJSON | `--execute` |
| `compress-investigation-history` | 調査履歴JSONのdecode検査と圧縮投影 | 論理bytes・保存bytes・decode failureのJSON | `--execute`、任意の`--vacuum` |
| `prune-investigation-history` | 保持期間外の調査実行記録。ただし登録プロジェクトごとの最新件数を保持 | 削除候補のJSON | `--execute`、任意の`--vacuum` |
| `audit-source-config` | 独自multi-tenant suffixと既存登録プロジェクトの分割候補 | JSONまたは対話候補 | 既定interactive。確認/`--execute`で設定・状態を書き込み。分割は自動適用しない |

正本DBを変更する保守系コマンドはbackupを作り、Web UI、worker、`enrich`、別のCLIを止めてからdry-runとexecuteを分けて実行してください。`--vacuum`は書き込みtransaction後の別処理で、失敗しても先行する書き換えや削除はrollbackされません。

`cleanup-workspace-temp`は正本DBを開かず、workspaceの`.tmp`だけを再帰的に調べます。既定では各SQLite本体とsidecar一式のうち最新更新から24時間以上経過したものをdry-run表示し、`--execute`で削除します。sidecarの一つでも新しければ同じDB一式を保持し、JSON、ログ、review文書、symlinkは対象にしません。作業終了直後の複製も削除する場合は、他processが使用していないことを確認して`--older-than-hours 0 --execute`を使います。全unit testのwrapperは`.tmp`が5GiBを超えるとこのコマンドを案内します。

`compress-investigation-history`はdry-runでも全履歴JSONをdecodeしてJSONとして検証します。decode failureが1件でも
ある場合は`--execute`を拒否します。圧縮後の内容確認にはInvestigation viewer、replay、exportを使い、SQLiteの
JSON functionで4つのhistory列を直接検索しません。

`repair-trust-graph --execute`は通常trust edge/evaluationだけを保存します。採用済み（`confirmed`）識別子の降格には`--downgrade-unsupported`も必要です。

`audit-source-config`は既定interactiveです。suffix追加を確認した後、保存済みcurrent service probeを新policyで再評価し、split候補を表示します。選んだtenant groupだけを分割します。`--no-interactive`はJSON dry-run、`--execute --no-interactive`はsuffixとcurrent stateを更新しますがsplitは適用しません。

## 調査・開発補助

これらは日常の登録や候補整理には通常不要で、収集処理や評価処理の回帰確認に使います。

| コマンド | 入力と出力 | 副作用 |
| --- | --- | --- |
| `replay-investigation` | 調査実行記録IDまたは登録プロジェクトの最新記録を読み、保存済みの根拠を現行規則で再評価してJSON出力 | なし。ネットワークや収集処理も使わない |
| `benchmark-investigation` | fixture corpus pathを読み、semantic regressionをJSON出力 | なし |
| `export-investigation-fixture` | 保存済み調査実行記録をsanitizationし、stdoutまたは`--output`へ出力 | DB変更なし。`--output`はファイル書き込み |
| `export-llm-evaluation-corpus` | 指定DBの保存済みLLM evidenceから固定corpusを作り、`--output`へ出力 | DBはread-only。指定ファイルへ書き込み |
| `evaluate-llm-corpus` | 固定corpus、任意の`--labels`、LLM接続設定、繰り返し回数、最大出力token数を受け、構造化reportを`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-llm-evaluation-reports` | 同じcaseとrepeatで作ったbaseline/candidate reportを比較し、機械検査の差と未実施確認を`--output`へ出力 | DB・LLM接続なし。指定ファイルへ書き込み |
| `export-repository-analysis-corpus` | 指定DBの保存済みrepository materialから固定corpusを作り、`--output`へ出力 | DBはread-only。指定ファイルへ書き込み |
| `evaluate-repository-analysis-corpus` | 固定repository materialを一つのmodel構成で解析し、保存前検査を含むreportを`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-repository-analysis-reports` | 同じrepository caseとrepeatのbaseline/candidate reportを比較 | DB・LLM接続なし。指定ファイルへ書き込み |
| `export-relation-assessment-corpus` | 指定DBから現在`ASSESS`対象となる方向付きrelationと保存済み限定根拠を固定corpusへ抽出 | DBはread-only。指定ファイルへ書き込み |
| `export-recollection-relation-assessment-corpus` | bounded再収集reportのreview claimをpage帰属とrelation意味判定へ分け、exactなconfirmed component由来のcaseだけを固定corpusへ抽出 | DB変更なし。指定ファイルへ書き込み |
| `evaluate-relation-assessment-corpus` | 一つの方向付き`project_repository`提案と限定材料からなる固定corpusを評価する。`--support-evidence-mode both_endpoints|direct_any`で両endpoint参照と、一方の直接的根拠を許容する条件を比較し、期待値・旧判断との一致を`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-trust-policy` | 指定DBのidentifier、relation、判断履歴を現行v1とrole付きshadow policyで比較し、`--output`へ構造化reportを出力 | DBはread-only。指定ファイルへ書き込み |
| `compare-merge-policy` | 指定DBの現行マージ候補と、role・直接relation・hard guardを分けたshadow policyを比較し、`--output`へ構造化reportを出力。DBと同じcheckoutの`config/domain-overrides.json`を自動検出し、別配置なら`--domain-overrides`で指定する | DBはread-only。指定ファイルへ書き込み |
| `compare-identifier-storage` | 指定DBの現行identifierと判断履歴から、非現行identifier・採用理由・時間関係を分離した保存案への移行を投影し、`--output`へ構造化reportを出力。`--materialized-db`を指定するとreview manifestを新しい複製DBへ適用する | 入力DBはread-only。reportと、明示した場合だけ新規複製DBへ書き込み。既存複製DBは上書きしない |
| `verify-service` | 登録プロジェクトまたはシード、任意URL、`static|deep|browser` lane | 外部接続。`--execute`で評価と再評価可能な調査実行記録をDBへ保存 |

exportのsanitizationは機械的な補助です。実プロジェクトキー、時刻、handle/DID、自由記述が残り得るため、fixtureとしてcommitする前に必ず人が確認してください。

LLM評価corpusも保存済みevidence、実プロジェクトキー、公開identifier、自由記述を含みます。通常は`.tmp/`へ
出力し、そのままcommitしません。`reference_output`は保存済みmodel出力との比較用で、正解labelではありません。
`evaluate-llm-corpus`はJSON/schema、evidence外identifier、`library`とsummaryの役割不整合、日英混在文の空白警告、保存済み出力との差、
latencyを記録します。`--labels`を指定するとevidence hashを照合してexpected outputとの差とcategory一致数も記録します。
期待categoryを人が確認したlabelは、そのcaseをprompt調整に使ったかを`used_for_prompt_adjustment`で明示します。reportは調整に使ったcaseと使っていないcaseを別集計します。
`--prompt-suffix-file`は評価時だけ追加指示を末尾へ付け、`--prompt-variant`と内容hashをreportへ記録します。
corpusは保存済みprofileをevidenceと分けて保持します。`--current-profile-mode without`では渡さず新規project相当、
`with`では渡して既存profile更新相当として実行し、条件をreportへ記録します。既定は`without`です。
`--repeat 2`以上では、同じ入力のcategory、identifier、summary等が実行ごとに変わったcaseをreportへ記録します。

`--llm-reasoning-effort`はruntimeへの要求値です。要求しただけでは適用済みと扱いません。runtime logまたはmodel設定で確認した結果を
`--reasoning-observation applied|ignored|not_checked`へ、短い根拠を`--reasoning-observation-note`へ指定します。
`applied`または`ignored`ではnoteが必須です。
endpoint responseは通常、実際に適用したreasoning設定を返さないため、各resultのmetadataだけから適用済みとは判断できません。

`compare-llm-evaluation-reports`はcorpus hash、case/repeat集合、成功、schema、evidence外identifier、prompt調整に使っていない、期待categoryを人が確認したcaseの
category結果を比較します。これは通常運用モデルの自動選定commandではありません。文章の人間確認、複製DBで両modelを別々にenrichした結果の比較、少数projectでの試用など、
reportだけでは完了しない確認を`remaining_checks`へ残します。
peak memory、swap、prefill/decode内訳の自動取得は未接続です。

repository analysis用corpusは、保存済みrunからrevisionへ固定したmaterialを取り出します。実行reportはJSON/schema、
summaryと各claimの根拠位置、materialに存在しないNSID、保存前の正規化結果、所要時間を記録します。summary、capability、observationの
内容と、解析結果を受けた後段triageは人が別に確認します。triage用のmodel別suffixはこの解析には適用しません。
後段を確認する場合は、`evaluate-llm-corpus --repository-analysis-report REPORT`で、正規化済み解析結果を同じprojectの
固定triage caseへ一時的に追加します。正本DBと元corpusは変更しません。

`verify-service`の`static`はtop page、`deep`は同一siteの追加pageを確認します。`browser`はJavaScript描画後の匿名画面を開き、表示中のmenuを展開した事実、auth linkを辿った事実、passwordまたはhandle/email入力を持つform、OAuth入口、公開content itemを伴う作成action、描画後network/console errorを別々に記録します。linkや作成CTAだけではservice surface確認済みにしません。認証actionの実行、認証入力、form送信、登録、投稿は行いません。browser laneにはPlaywrightの任意依存が必要です。

## 現在の整理対象

CLIはWeb UIより低水準な操作や開発補助も公開しています。機能削減の検討中も、削除が確定して実装から外れるまでは、この文書と`--help`で現行の入出力を説明します。削除候補を通常フローへ積極的に案内する意図はありません。
