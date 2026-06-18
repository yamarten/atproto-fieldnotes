# Project Registry Roadmap

確定済みの保存形や責務分担は [project-record.md](project-record.md)、
[registry-internals.md](registry-internals.md)、[investigation-model.md](investigation-model.md) に移しました。

この文書は、まだ本体仕様に取り込んでいない展望と TODO を置くためのメモです。

## 目的の優先順位

1. 紹介するかどうかを安定して判断できる
2. 紹介文を作るための核データを保てる
3. 運用上必要な review / merge / evidence を保てる
4. 将来 app record を生成したくなった時に、必要情報が足りない状態を避ける

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon 完全互換を今すぐ満たすこと
- 不確かな app metadata で `app` を埋めること
- export の都合だけで core や ops の設計を歪めること

紹介フローを主導に置き、その延長で app export 可能性を確保する、という順序を維持します。

## 直近の実装順

ここでは、通常運用へ移るまでに順番を決めて進める作業を扱います。周辺機能を含む広い候補一覧は、次の作業用notesに分けています。

- [web-ui-overhaul-backlog.md](notes/web-ui-overhaul-backlog.md)
- [core-overhaul-backlog.md](notes/core-overhaul-backlog.md)

### 通常DB置換前

現行migrationのworkspace v1 backup mirrorに対するdry-runは合格済みです。workspaceの旧方式v2ミラーとその調査履歴はテスト用として破棄し、本番移行へ引き継ぎません。通常DBを置換する前に次を完了します。

1. 本番v1でdry-runを実行する
   - project / identifier / decision / relation / investigation / collector件数を確認する
   - hard acceptance condition、SQLite integrity、foreign keyを確認する
2. offline置換手順を固定する
   - Web、enrich、CLI writerを全停止する
   - v1 backupの複製をmigration入力にし、唯一のbackup自体を置換しない
   - migration reportをファイル保存し、新旧DBのhashと一緒に保管する
3. 移行世代の誤認防止を決める
   - 現状は`schema_version=2`だけで、旧方式v2も`already_current`になる
   - trust policy markerを`registry_meta`へ残すか、置換手順だけで管理するかを通常置換前に確定する
4. 必要ならprocess間lockまたはpreflightを追加する
   - 現状の`migrate-db`は別processを自動停止・拒否しない
   - 個人ローカル運用ではoffline手順で代替可能だが、自動判定ではない

### 日常運用を安定させるまで

移行後のDBをWeb UIとCLIから継続的にreviewできる状態にするための作業です。

1. Search/queryをDB table直接利用へ寄せる（実装済み）
   - identifier / relation / decision tableの構造化filterを接続済み
   - project一覧検索は全project hydrateを行わず、project rowと`EXISTS` queryから候補を作る
   - fuzzy search、FTS、paginationは必要が生じた時の候補として残す
2. Review action flowを作る（実装済み）
   - relation / collector failureは対象fingerprint付きdismiss/defer decisionへ進める
   - candidate identifierとdeveloper accountはconfirm/block/defer可能
   - 選択中の既存projectへmanual seedをpreview後に追加可能
3. Profile最小編集を入れる（実装済み）
   - nameとnotesをWebから編集可能
   - 保存前diffとproject revisionによるstale guardを実装済み
   - tagline、summary、website_urlは必要になったら追加
4. Background jobの運用穴を埋める（実装済み）
   - 固定`enrich-runs/`からcheckpointを発見し、server再起動後もresume可能
   - sync source内部とreview candidate単位のprogress/cancel境界を接続済み
   - reviewはdry-run planのcandidateを選択し、fingerprint再検証後にexecuteする
5. Operationsの安全表示を整える（実装済み）
   - dry-runでもnetworkするsyncを開始確認に明示
   - operationごとのStop境界とcheckpoint/process jobの違いを表示
   - job eventはprocess内保持のまま。永続化は行わない
6. Investigation viewerを追加する（実装済み）
   - bundleをdecodeしないrun summary一覧と、選択時のrun detailを追加
   - collector status、failure、上限付きevidence、input/output/evaluationを表示
   - 現行ruleで要求時にread-only replayし、保存evaluationとの差分を表示
7. Service / ownership reviewを厚くする
   - service verification結果の比較
   - NSID ownershipの根拠表示
   - OAuth permission inspector
   - URL alias review
8. Explain系の土台を作る
   - why service_status
   - why ownership
   - why contribution level
   - why merge candidate
   - CLI commandでもWeb表示の裏側でも使えるようにする

### 必要が生じた時に検討するもの

現状の運用を妨げてはいませんが、具体的な不便や保守上の問題が出た場合に検討します。

- relation / ownership graph
  - 興味はあるが直近優先ではない
  - まずはviewだけ。write actionは付けない
- fuzzy search
  - 通常検索で不足が明確になった場合に追加する
  - 単なるtypo toleranceではなく、実際のproject探索に役立つ検索条件を先に定める
- cache / source budget visibility
  - 表示だけなら調査時に便利
  - Webからbudgetを直接編集するのは別判断
- keyboard shortcut
  - 操作系は入れない
  - 詳細の前/次切り替え、detail tab jump程度に留める
- static JS / CSS / route table分割
  - 編集衝突が増えた時、または責務を追いにくくなった時点で分割する
- security / safety整理
  - LAN、他利用者なし、必要に応じてTailscale越しという公開範囲は維持する
  - その前提で必要十分な対策に留める
- job event履歴永続化
  - checkpoint discoveryだけでは実行履歴を追えない場合に追加する
  - 保存量とretention policyを先に決める

### 長期候補

直近の運用安定化には含めません。必要性や具体的な利用場面が見えたものから再検討します。

- evidence / ownership graphの本格表示
- Investigation notebook mode
- Assisted review
- LLM triage side panel
- multi-project compare
- review session mode
- bulk maintenance dashboard
- source health dashboard
- plugin-style collector configuration

## 将来の論点

### 調査 pipeline の残件

次の優先候補だけを roadmap として残します。接続済み範囲と恒久的な契約は
[investigation-model.md](investigation-model.md) を正本とします。

- 既存DBのdefer/block/manual correctionから、実際の誤判定fixture候補を抽出する
- OAuth `include:` permission setを再帰解決し、循環と取得失敗をsource状態へ残す
- Tangled timeline/community search/bot adapterを必要性評価後にopt-in接続する
- UFOs rolling windowのgap検知後に、別sourceで補完する運用を決める
- browser service verificationの実Playwright smoke testと複数surface統合を追加する
- repo contents、PDS、Bluesky sessionを含む全collectorへbudget/cache policyを統一する
- investigation runのretention、pruning、export時sanitize policyを決める
- Web UIからrun/evidence diffを閲覧する必要性を再評価する

未決定:

- investigation runのretention
- 複数projectを跨ぐ永続circuit breaker
- Web UIからのevidence inspection導線

設計途中の詳細は次の作業用文書へ分離しています。

- ownership / identity: [identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- service verification: [service-verification.md](notes/service-verification.md)
- replay / fixture corpus: [evidence-replay.md](notes/evidence-replay.md)
- collector failure policy: [collector-reliability.md](notes/collector-reliability.md)

### ステータス系

現行の canonical は `service_status` です。

将来的には、次の別軸を分けたくなる可能性があります。

- `paused`
- `unmaintained`

候補:

- `service_status`
  - 到達性、公開状態、招待制、死活
  - `unreleased` まではこの軸に含める
- `app.status`
  - app record 向けの公開・保守状態

現時点では分けません。必要になった時点で `app.status` を追加します。

### app metadata の拡張

`app` は今は低優先度です。確かな情報だけを入れ、空 object を許します。

将来、次を追加で整備する余地があります。

- `images[]`
- `langs[]`
- `lexicons.produces[]`
- `lexicons.consumes[]`
- `account_indicators[]`

`links[]` の初期実装は入っていますが、現時点では次の最小 role に限定しています。

- `website`
- `webApp`
- `sourceCode`
- `docs`
- `support`
- `status`
- `privacyPolicy`
- `termsOfService`

次は必要になった時だけ増やします。

### 境界項目の再評価

現時点では evidence として扱っているが、将来 app metadata に昇格するかもしれない項目です。
ただし今は、紹介判断や要約補助に使う材料としての意味が強く、app 側へ急いで寄せる必要はありません。

- `inspired_by`
- `features`
- `developer_accounts`

`review-out-of-scope` の derivative 判定は、Bluesky従属utilityとatproto技術ツールを
十分に分離できていません。現状は候補提示だけに使い、自動実行しません。将来は
`technical_interest_level`、owned surface、独自データモデルの有無を加味して、
紹介スコープ外と技術調査対象を分離します。

### atproto寄与度の独立評価

現行の level、score、evidence と rule-based v1 は [registry-internals.md](registry-internals.md) を正本とします。
未接続の候補は次です。

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

### Web UI 詳細検索

Web UIのschema v2検索は接続済みです。検索拡張候補は次です。

- SQLite FTS table の追加
- typo tolerance / edit distance
- OR group や否定条件を含む query builder
- 保存済み検索条件
- URL query への filter 状態の永続化
- offset / cursor pagination
- raw JSON 任意パス検索

### Web UI: 次の接続候補

未接続 / 将来候補:

- investigation run一覧、evidence全文、replay差分の専用画面
- relation/collector failureからreview actionへ移る導線
- candidate developer accountの確認・昇格操作
- Enrich jobのserver再起動対応
  - subprocess runner、停止API、process内job一覧、cancelled/failed runの再開は実装済み
  - サーバー再起動後に`enrich-runs/*.json`を発見し、Operationsから再開する導線は未実装
  - UI job stateとcheckpoint stateの履歴統合は未実装

原則 CLI に残す保守操作:

- `audit-source-config` の hosted suffix split 監査
  - 既存 project detail の単体 split とは別導線
  - tenant group と仮分割後 merge candidate を確認してから実行する
  - 複数 identifier を動かすため、通常のモバイル review 導線には出さない
- `audit-url-fields`
  - 原則 read-only report
- `cleanup-app-links`
  - dry-run を先に表示し、execute は明示確認を必須にする
  - Web UI には出さず CLI で扱う

## 周辺候補の参照先

Web UIとcoreの広い棚卸しは、[web-ui-overhaul-backlog.md](notes/web-ui-overhaul-backlog.md)と[core-overhaul-backlog.md](notes/core-overhaul-backlog.md)に置いています。具体的な着手条件と実装順が決まった項目は、このroadmapの該当箇所へ反映します。
