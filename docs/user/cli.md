# Project Registry CLIガイド

`project-registry`の現行公開コマンドについて、利用者に見える入力、出力、外部アクセス、DB変更をまとめます。正確なオプション名と既定値は`project-registry COMMAND --help`を正本とし、この文書は挙動と使い分けを説明します。基本用語は[利用ガイド](guide.md#基本用語)を参照してください。

日常フローだけを知りたい場合は[利用ガイド](guide.md)、画面操作は[Web UIガイド](web-ui.md)を参照してください。

## 共通の入力と出力

- 登録プロジェクトは`--project-key`または既知のシードで指定する。プロジェクトキーは一意な接頭辞でもよい。
- シードの種類は`nsid`、`repo_url`、`service_url`。値が曖昧な場合は`--type`を指定する。
- 追跡状態（`tracking_state`）は`active`、`stable`、`stalled`、`out_of_scope`、`archived`、`ignore`。
- 多くのコマンドはstdoutへJSONを出す。`show`と`explain`はtext/JSON、`enrich`はJSON Lines。
- 引数エラーは終了コード`2`、処理失敗は通常`1`。一括`enrich`の協調停止は`130`。

通常の登録・調査操作は既定で`data/survey.db`を使います。別DBを使う場合は`project-registry --db-path PATH COMMAND ...`の順で指定します。一覧・詳細・レビュー・登録・編集・調査は選択したregistry DBへ接続し、存在しないDBは暗黙作成しません。queueの提案コマンドも接続中のDBを引き継ぎます。この指定は設定ファイル・認証・外部アクセスを隔離するものではありません。一括enrichのcheckpointは`--checkpoint-path`で別途指定するか`--no-checkpoint`で無効にしてください。

`init-db`と一部の開発補助コマンドはcommandの後に置く専用`--db-path`を使います。たとえば`validate-app-record-export`、`export-app-records`、`export-llm-evaluation-corpus`、`compare-trust-policy`、`compare-merge-policy`、`compare-identifier-storage`は指定DBを読み取り専用で参照します。これらと共通`--db-path`の併用は引数エラーです。各コマンドの引数は`--help`で確認してください。

CLI/Webの手動確認には`project-registry dev-db`で架空データ5件を生成できます。保存先は各worktreeの`data/dev/survey.db`に固定し、既存fileは上書きしません。利用processを停止して`dev-db --reset`を指定すると、この入口で生成したDBだけを初期状態へ戻します。生成・リセットは外部APIやLLMを呼びません。代表データ、上書き拒否と停止の条件、CLI/Webの確認例は[共通ダミーDBの手順](../developer/workflows/change-acceptance.md#cliwebの共通ダミーdb)を参照してください。

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
| `dev-db` | 任意の`--reset`。対象pathは固定 | DB path・操作・データ件数のJSON | 架空のCLI/Web確認用DBを生成。明示リセットは生成元markerのある現行schema DBのみ |
| `add-seed` | シード、任意の`--type`、既存登録プロジェクトの`--project-key` | 登録プロジェクトと入力から決めた役割（`role`）のJSON | 常時書き込み。NSID・サービス・リポジトリを対応する役割で採用済み（`confirmed`）として保存し、採用判断を記録 |
| `enrich` | `--project-key`、`--seed`、`--all`のいずれか。調査深度、対象の追跡状態、LLM、チェックポイントのオプション | 進捗、失敗、結果のJSON Lines | 外部接続、LLM、常時書き込み |
| `show` | 登録プロジェクト、シード、または`--all`。任意の追跡状態フィルター | textまたはJSON | なし |
| `explain` | プロジェクトキー、topic、format | 保存済み結論の根拠をtextまたはJSON | なし |
| `validate-app-record-export` | schema v4 DBと`--output` | `community.lexicon.app.entry`として書き出す条件の充足状況（readiness report） | DBはread-only。指定JSONだけを書き込み |
| `export-app-records` | schema v4 DB、公開対象・時刻を指定するmanifest、`--output` | PDS処理へ渡せるentry record配列 | DBはread-only。全対象の検証成功後に指定JSONだけを書き込み |

`show --seed`は登録済みのシード値から登録プロジェクトを解決します。プロジェクトキーから調べる場合は`--project-key`、シードのURLが曖昧な場合は`--type`を指定してください。

`add-seed`へ渡したサービスURLはパスを保持します。同じシードを同じ登録プロジェクトへ再適用してもよく、旧データに不足する採用済みサービス・リポジトリの役割を補えます。利用者が明示したサービスURLは、`/about`、`/docs`、`/login`などのパス名だけを理由に補助ページ扱いにしません。同じURLの除外・保留は最新の明示登録で解除しますが、親URLに対する再付着防止・保留の判断は他候補向けに残します。

`explain`のtopicは`all`、`service_status`、`ownership`、`contribution`、`merge_candidate`、`trust`です。保存済み状態と共通評価処理だけを使い、ネットワークやLLMを呼びません。

`validate-app-record-export`は紹介用プロフィール、代表値として保存した項目、現行かつ採用済みの識別子・リソースから公開候補を作り、その他の候補を診断用の除外入力として残します。登録プロジェクトごとに必須・任意項目の不足、根拠不足、変換不能、仕様未確定、公開対象としての適否を分け、公開用の作成日時である`createdAt`の指定も案内します。通常は`.tmp/`へ出力してください。詳細は[App Record Export](../developer/specs/app-record-export.md)を参照してください。

`export-app-records`は、manifestに明示したプロジェクトキーを公開対象とし、UTCの`createdAt`もmanifestから使います。公開対象としての適否が未確定な`scope_uncertain`は、manifestの別の許可リストへ同じキーを重ねて指定した場合だけ許可します。必須項目の不足、変換不能、`not_applicable`は許可せず、1件でも不適格なら出力しません。

出力形式は`community.lexicon.app.entry`に固定されています。`records`配列の各要素にはrecord本体だけを入れます。

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

調査中に同じ登録プロジェクトが編集・統合・分割された場合は、古い保存を拒否し、調査成果を「未反映」の履歴として残します。他の対象は続行し、競合だけが残れば`retry_wait`、終了コード`2`と再開コマンドを返します。再開すると最新状態から外部取得・LLMも再実行するため、その分の処理時間がかかります。各件開始時に追跡状態などを確認し、対象外になったものは理由を記録して調査しません。`--retry-failures`はcollector失敗の選択条件で、保存競合の再開には`--resume-run`を使います。

1件の登録プロジェクトが失敗しても既定では残りを続行し、一括実行全体は最終的に`failed`、終了コード`1`になります。`--fail-fast`は最初の失敗後に止めます。Ctrl+Cは処理中の登録プロジェクトの完了・保存後に止まり、再開コマンドを出して終了コード`130`を返します。即時停止ではないため、停止までの時間は外部リクエストのタイムアウトに依存します。

既定対象外の追跡状態は`--include-*`、明示列挙は`--tracking-states`、全状態は`--all-tracking-states`で含めます。

`--llm-max-output-tokens`は、調査計画の補助（planning）、採用済みリポジトリの解析、登録プロジェクトの説明・分類の補助（triage）の各LLMリクエストに同じ最大出力トークン数を指定します。省略時は接続先の設定を使います。モデル比較では、固定した評価用入力の集合（corpus）を使う評価と通常`enrich`で同じ値を明示してください。

### schema v3からv4への移行

通常起動は旧DBを自動移行しません。先にWeb・CLI・workerの書込みを停止し、backupを確保してから実行します。正本DBへの適用は開発用DBの検証とは別作業です。

```bash
project-registry --db-path PATH migrate-db-v4
project-registry --db-path PATH migrate-db-v4 --execute
```

最初のcommandはversionと対象pathの確認だけです。`--execute`が更新番号・履歴の適用状態を追加し、既存データを同じDB内でv4へ移行します。旧binaryと新binaryを同じDBへ並行接続しないでください。

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
| `candidate_repo_needs_confirmation` | リポジトリ候補はあるが、ソースコードを保持するリポジトリとして未採用。 |
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
| `service_surface_confirmed` | 原則`category=project`で、サービス状態が`live`、`beta`、`invite_only`、確度（`confidence`）が`medium`以上で、ログイン画面やアプリ画面の枠組み（app shell）、ブラウザーでの到達などサービスを利用する画面を確認済み。紹介可否は利用者が判断する。 |
| `service_candidate_needs_validation` | service URLと稼働statusの候補はあるが、confidenceが低いか稼働面が未確認。上記より低い優先度で残す。 |
| `lexicon_publisher_confirmed` | confirmed owned NSIDが、authorityの`_lexicon` DNS委譲先にあるmatching schema recordとして取得できた。categoryには依存しない。 |
| `technical_insight_candidate` | 設計・相互運用・参照実装を学ぶうえで特に見る価値がある。 |

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

外部情報の取り込みは`--execute`なしでも外部ネットワークへ接続します。Tangledを除き、dry-run、失敗、不完全、上限で打ち切った実行は成功時点を示すwatermarkを進めません。成功したexecuteだけが情報源別の増分状態を保存します。Tangledの途中checkpoint条件は後述します。`--all-history`は保存済みwatermarkや通常の遡る期間を無視しますが、情報源自身が一定期間のデータしか提供しない場合は完全履歴にはなりません。`sync-collections --all-history`は既知collectionも再処理し、collection inventory由来の保存済みobservationを現在の全payloadに置き換え、collection identifierと活動統計を再構築します。置換対象は結果JSONの`collection_observation_rebuild_project_count`と`collection_observation_cleanup_count`で確認できます。

`--create-unmatched`は未知のシードから登録プロジェクトを作るかを制御します。GitHub、collection一覧、Semble、Tangledは既定onです。詳細な検索条件やURLの一時上書きはCLIで行い、Web UIは設定済みの外部情報源だけを実行します。

Tangledの取り込みにはNode.js 22.15以上、`npm install`で導入する公式`@bsky/jetstream` SDK、`ATPROTO_SURVEY_JETSTREAM_API_KEY`が必要です。APIキーは環境変数からだけ読み、設定、結果、取り込み済みの境界（watermark）へ保存しません。

`--all-history`はJetstream archiveの先頭から開始し、通常実行は保存済みの処理位置（sequence）から再開します。項目上限で途中終了したexecuteは最後に処理したsequenceを保存するため、次回実行で続きから処理できます。設定上の通常上限は1,000イベントで、初回archiveも複数回の取り込み実行に分けて進めます。

Tangledのowner確認はBlueskyプロフィールの公開状態に依存せず、DIDとhandleを双方向に確認します。解決できないownerのrecordは帰属を付けず保留し、他の候補を処理します。結果の`skipped_owner`（保留record数）、`unresolved_owners`（owner数）、`owner_errors`（理由）を確認してください。保留一覧は成功した増分executeで処理位置と一緒に保存し、次回以降に件数を限定して再評価します。`pending_owner_records`は処理後の保留候補数で、実際に保存したかは`state_updated`で確認できます。保留だけなら処理位置を進められますが、有効候補の未一致を`--no-create-unmatched`で残す場合は進めません。通信・認証・未知のAPIエラーは実行全体の失敗として扱います。[詳細な保存・再評価条件](../developer/specs/discovery-and-sync.md#ownerのidentityと解決不能record)を参照してください。

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
| `audit-url-fields` | URL項目の利用数と、保存時に満たすべき整合性条件への違反 | 問題と例のJSON | なし |
| `audit-trust-graph` | 採用済み（`confirmed`）識別子のtrust根拠 | 問題と例のJSON | なし |
| `repair-db` | 登録プロジェクトの保存データ正規化、派生値更新、衝突 | 変更予定件数のJSON | `--execute` |
| `migrate-repository-trust` | 旧ページ・repository信頼根拠の退避計画。`--db-path`と`--output`必須 | 分類付きJSON計画 | `--execute-plan <確認済み計画.json> --archive <退避.json>`で旧根拠全体を退避 |
| `repair-trust-graph` | trust graph再計算。任意で根拠不足の採用済み識別子を降格 | 変更予定件数のJSON | `--execute` |
| `cleanup-storage-payloads` | 現在の根拠と調査履歴への保存上限再適用 | byte・件数差分のJSON | `--execute`、任意の`--vacuum` |
| `cleanup-workspace-temp` | `.tmp`内の古いSQLite複製とWAL/SHM | 削除候補、容量、保持件数のJSON | `--execute` |
| `compress-investigation-history` | 調査履歴JSONを読み出せるかの検査と圧縮時の容量見積もり | 圧縮前後のバイト数・読み出し失敗のJSON | `--execute`、任意の`--vacuum` |
| `prune-investigation-history` | 保持期間外の調査実行記録。ただし登録プロジェクトごとの最新件数を保持 | 削除候補のJSON | `--execute`、任意の`--vacuum` |
| `audit-source-config` | 複数利用者が使うホストの独自接尾辞と、既存登録プロジェクトの分割候補 | JSONまたは対話候補 | 既定interactive。確認/`--execute`で設定・状態を書き込み。分割は自動適用しない |

正本DBを変更する保守系コマンドはbackupを作り、Web UI、worker、`enrich`、別のCLIを止めてからdry-runとexecuteを分けて実行してください。`--vacuum`は書き込みtransaction後の別処理で、失敗しても先行する書き換えや削除はrollbackされません。

`migrate-repository-trust`は旧根拠を分類した計画を出力します。`--execute-plan`と`--archive`を併用すると、旧根拠全体を退避し、現在保存している信頼グラフから除去します。過去の判断・理由・入力したページとリポジトリの組も退避ファイルへ残しますが、現役の関係として復元はしません。現在の判定は既存の関係から導出しているため、この退避で判定は変わりません。

退避先は`data/migration-archives/`など永続保存する場所を選び、既存ファイルやDB・計画・結果の出力先とは別のパスを指定します。元データの保存を確認してからDB側を除去し、既存の退避ファイルは上書きしません。失敗時に退避ファイルだけが残る場合があるため、退避後は新しい計画を生成し、旧根拠がDBに残っていないことまで確認します。

`cleanup-workspace-temp`は正本DBを開かず、ワークスペースの`.tmp`だけを再帰的に調べます。既定では各SQLite本体と付随するWAL/SHMファイル一式のうち最新更新から24時間以上経過したものをdry-run表示し、`--execute`で削除します。付随ファイルの一つでも新しければ同じDB一式を保持し、JSON、ログ、レビュー文書、シンボリックリンクは対象にしません。作業終了直後の複製も削除する場合は、他プロセスが使用していないことを確認して`--older-than-hours 0 --execute`を使います。全unit testの起動スクリプトは`.tmp`が5GiBを超えるとこのコマンドを案内します。

`compress-investigation-history`はdry-runでも全履歴JSONを読み出してJSONとして検証します。読み出し失敗が1件でもある場合は`--execute`を拒否します。圧縮後の内容確認には調査履歴画面、`replay-investigation`、`export-investigation-fixture`を使います。圧縮した履歴はSQLiteのJSON関数で直接検索できません。

`repair-trust-graph --execute`は通常、信頼グラフの接続と評価結果だけを保存します。採用済み（`confirmed`）識別子の降格には`--downgrade-unsupported`も必要です。

`audit-source-config`は既定で対話操作を行います。接尾辞の追加を確認した後、現在保存しているサービスの観測結果を新しいドメイン規則で再評価し、分割候補を表示します。ホストの利用者単位にまとめた候補群から、選んだ群だけを分割します。`--no-interactive`はJSONでの候補確認、`--execute --no-interactive`は接尾辞と現在状態の更新を行いますが、分割は適用しません。

## 調査・開発補助

これらのコマンドは、収集処理や評価処理を変更した際の比較・検証に使います。再利用する固定入力の集合をcorpus、再現用に固定したテストデータをfixtureと呼びます。DBへ適用せず新旧規則を比較する経路をshadowと呼びます。

| コマンド | 入力と出力 | 副作用 |
| --- | --- | --- |
| `replay-investigation` | 調査実行記録IDまたは登録プロジェクトの最新記録を読み、保存済みの根拠を現行規則で再評価してJSON出力 | なし。ネットワークや収集処理も使わない |
| `benchmark-investigation` | 固定したテスト事例の集合を指定パスから読み、再評価結果と期待結果の差をJSON出力 | なし |
| `export-investigation-fixture` | 保存済み調査実行記録をsanitizationし、stdoutまたは`--output`へ出力 | DB変更なし。`--output`はファイル書き込み |
| `export-llm-evaluation-corpus` | 指定DBの保存済みLLM evidenceから固定corpusを作り、`--output`へ出力 | DBはread-only。指定ファイルへ書き込み |
| `evaluate-llm-corpus` | 固定corpus、任意の`--labels`、LLM接続設定、繰り返し回数、最大出力token数を受け、構造化reportを`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-llm-evaluation-reports` | 同じcaseとrepeatで作ったbaseline/candidate reportを比較し、機械検査の差と未実施確認を`--output`へ出力 | DB・LLM接続なし。指定ファイルへ書き込み |
| `export-repository-analysis-corpus` | 指定DBの保存済みrepository materialから固定corpusを作り、`--output`へ出力 | DBはread-only。指定ファイルへ書き込み |
| `evaluate-repository-analysis-corpus` | 固定repository materialを一つのmodel構成で解析し、保存前検査を含むreportを`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-repository-analysis-reports` | 同じrepository caseとrepeatのbaseline/candidate reportを比較 | DB・LLM接続なし。指定ファイルへ書き込み |
| `export-relation-assessment-corpus` | 指定DBから現在`ASSESS`対象となる方向付きrelationと保存済み限定根拠を固定corpusへ抽出 | DBはread-only。指定ファイルへ書き込み |
| `export-recollection-relation-assessment-corpus` | 取得範囲を限定した再収集reportの要確認事項を、ページの帰属と関係の意味判定へ分け、URLが採用済み構成要素と完全一致する事例だけを固定corpusへ抽出 | DB変更なし。指定ファイルへ書き込み |
| `evaluate-relation-assessment-corpus` | 一つの方向付き`project_repository`提案と限定材料からなる固定corpusを評価する。`--support-evidence-mode both_endpoints\|direct_any`で関係の両側への参照を必要とする条件と、一方の直接的根拠を許容する条件を比較し、期待値・旧判断との一致を`--output`へ出力 | LLMへ接続。DB変更なし。指定ファイルへ書き込み |
| `compare-trust-policy` | 指定DBのidentifier、relation、判断履歴を現行v1とrole付きshadow policyで比較し、`--output`へ構造化reportを出力 | DBはread-only。指定ファイルへ書き込み |
| `compare-merge-policy` | 指定DBの現行マージ候補と、役割・直接的な関係・マージを禁止する条件を分けた比較用規則を照合し、`--output`へ構造化reportを出力。DBと同じcheckoutの`config/domain-overrides.json`を自動検出し、別配置なら`--domain-overrides`で指定する | DBはread-only。指定ファイルへ書き込み |
| `compare-identifier-storage` | 指定DBの現行identifierと判断履歴から、非現行identifier・採用理由・時間関係を分離した保存案への移行を投影し、`--output`へ構造化reportを出力。`--materialized-db`を指定するとreview manifestを新しい複製DBへ適用する | 入力DBはread-only。reportと、明示した場合だけ新規複製DBへ書き込み。既存複製DBは上書きしない |
| `verify-service` | 登録プロジェクトまたはシード、任意URL、調査方法（`static\|deep\|browser`） | 外部接続。`--execute`で評価と再評価可能な調査実行記録をDBへ保存 |
| `monitor-services` | `--min-age-days`（既定30）、`--limit`（既定20） | 既定は未確認・古いconfirmed service URLの一覧だけ。`--execute`で匿名browser確認と保存 |

書き出し時の機密情報除去（sanitization）は機械的な補助です。実プロジェクトキー、時刻、handle/DID、自由記述が残り得るため、fixtureとしてcommitする前に必ず人が確認してください。

LLM評価corpusも保存済みの根拠、実プロジェクトキー、公開識別子、自由記述を含みます。通常は`.tmp/`へ出力し、commit前に人が確認します。`reference_output`は保存済みモデル出力との比較用で、正解labelではありません。

`evaluate-llm-corpus`はJSON/schema、根拠にない識別子、`library`と紹介文の役割不整合、日英混在文の空白警告、保存済み出力との差、応答時間を記録します。`--labels`を指定すると根拠のハッシュ値を照合して期待結果との差とcategory一致数も記録します。

期待categoryを人が確認したlabelは、その事例をプロンプト調整に使ったかを`used_for_prompt_adjustment`で明示します。reportは調整に使った事例と使っていない事例を別集計します。

`--prompt-suffix-file`は評価時だけ追加指示を末尾へ付け、`--prompt-variant`と内容のハッシュ値をreportへ記録します。

corpusは保存済みプロフィールを根拠と分けて保持します。`--current-profile-mode without`ではプロフィールを渡さず新規登録プロジェクト相当、`with`では渡して既存プロフィール更新相当として実行し、条件をreportへ記録します。既定は`without`です。

`--repeat 2`以上では、同じ入力に対する分類、識別子、紹介文等が実行ごとに変わった事例をreportへ記録します。

`--llm-reasoning-effort`はモデル実行環境への要求値です。適用の有無は実行環境のログまたはモデル設定で確認し、結果を`--reasoning-observation applied|ignored|not_checked`へ、短い根拠を`--reasoning-observation-note`へ指定します。`applied`または`ignored`ではnoteが必須です。接続先の応答は通常、実際に適用したreasoning設定を返さないため、各結果のmetadataだけから適用済みとは判断できません。

`compare-llm-evaluation-reports`は入力集合のハッシュ値、事例と反復回の集合、成功、schema、根拠にない識別子を比較します。categoryの比較には、プロンプト調整に使っておらず、人が期待categoryを確認した事例を使います。結果は通常運用モデルを選ぶための判断材料です。文章の人間確認、複製DBで両モデルを別々に`enrich`した結果の比較、少数プロジェクトでの試用など、reportだけでは完了しない確認を`remaining_checks`へ残します。

メモリ使用量の最大値、swap、入力処理（prefill）・出力生成（decode）の時間内訳の自動取得は未接続です。

リポジトリ解析用corpusは、保存済み調査実行記録から特定のrevisionに固定した解析素材を取り出します。実行reportはJSON/schema、解析要約と各事実記述（claim）の根拠位置、素材に存在しないNSID、保存前の正規化結果、所要時間を記録します。要約・機能・観測の内容と、解析結果を受けた後段triageは人が別に確認します。triage用のモデル別追加規則はこの解析には適用しません。

後段を確認する場合は、`evaluate-llm-corpus --repository-analysis-report REPORT`で、正規化済み解析結果を同じ登録プロジェクトの固定triage事例へ一時的に追加します。正本DBと元corpusは変更しません。

`verify-service`の`static`はトップページ、`deep`は同一サイトの追加ページを確認します。`browser`はJavaScript描画後の匿名画面を開きます。メニューを展開した事実、認証ページへのリンクを辿った事実、パスワードまたはhandle/email入力を持つフォーム、OAuthの入口、公開コンテンツと作成操作が併存する画面、描画後のネットワーク・コンソールエラーを別々に記録します。

`monitor-services`は`active / stable / stalled / out_of_scope`の確認済みservice URLを対象にします。失敗も含めた最新のbrowser試行からの経過日数で選び、未確認を先にします。`ignore / archived`や未確認の候補URLは対象外です。結果の`due_count / remaining_due_count`で上限による積み残しを確認できます。`--min-age-days`は1〜365、`--limit`は1〜100です。実行は保存と外部接続を伴いますが、説明の生成・追跡状態変更は行いません。失敗・競合を含む実行は終了コード1を返します。

```bash
project-registry monitor-services --limit 20
project-registry monitor-services --limit 20 --execute
```

最後に確認できた利用面と最新の試行は別に保持します。確認結果は現在の利用を保証するものではなく、HTTPエラーだけで提供終了とは判断しません。単独のservice確認で全体調査の鮮度は更新しません。

リンクや作成を促すボタンだけでは、サービスを利用する画面の確認済み判定にはしません。認証の実行、認証入力、フォーム送信、登録、投稿は行いません。`browser`を使う場合はPlaywrightとブラウザーの導入が必要です。

## 現在の整理対象

CLIはWeb UIより低水準な操作や開発補助も公開しています。機能削減の検討中も、削除が確定して実装から外れるまでは、この文書と`--help`で現行の入出力を説明します。日常の操作は[利用ガイドの日常フロー](guide.md#日常フロー)から選んでください。
