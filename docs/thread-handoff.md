# Thread Handoff

## 現在の基準

- 実装済み: registry schema version 2
  - `projects`のscalar + `profile_json` + `state_json`
  - `project_identifiers`
  - `project_relations`
  - `project_decisions`
- 実装済み: `project-registry migrate-db OLD_DB_PATH`
  - 既定で移行・検証・backup・元path置換まで実行
  - `--dry-run`は一時DBを検証後に削除
  - v2 DBへの再実行はno-op
- 実装済み: 公開command入口は`bin/project-registry` / `bin/project-registry-web`へ一本化
- 削除済み: `scripts/project-registry`互換wrapper
- 削除済み: `scripts/project-registry-web`互換wrapper
- 削除済み: `scripts/project_registry.py` top-level Python API
- 削除済み: runtime compatibility field
  - `website_url_candidates`
  - `github_repo_urls`
  - `tangled_repo_urls`
  - `resource_urls`
  - `introduced`
- 実装済み: EvidenceBundleはschema v2のみを通常readerで受理
- 実装済み: `project_registry_core.application`
  - project query/tracking、decision、merge/split、review queue
  - sync、investigation replay/verify/export、maintenance
- 実装済み: 通常CLI adapterを`project_registry_core.commands`へ用途別分割
- 実装済み: enrich background job境界
  - `OperationRequest` / `OperationEvent` / `OperationResult` / `CancellationToken`
  - enrichのproject選択、保存、checkpoint、再開をapplication serviceへ移動
  - CLIはevent dataをJSON linesへ変換するadapter
- 実装済み: Web enrich subprocess runner
  - 同じWeb serverからの同時background jobは1件。CLIなど別processとの排他は未実装
  - 開始、一覧、詳細、処理中project後の停止、cancelled/failed bulk runの再開
  - Operations画面で設定、進捗、event/resultを表示
- 実装済み: schema v2 Web UI
  - `project_registry_web`を`project_registry_core`と並ぶ独立adapter packageとして配置
  - `Review / Projects / Operations / Settings`
  - project検索、review/promotion/merge queue、seed preview・追加
  - normalized identifier/relation/decision、評価、最新investigation概要の表示
  - tracking、candidate confirm/reject、block/defer、split、separate解除、merge
- 実装済み: v2 runtime/save境界
  - normalized child tableからflat runtime viewを作るcodecを一本化
  - 既存projectのpartial dict保存を拒否
  - runtime markerと削除済みcompatibility fieldの保存・snapshot・Web payload漏出を抑止
  - normalized identifier/relation/decision rowの無変更load/saveをlosslessに維持
- 実装済み: v1 classificationのconservative migration
  - `v1-quarantine-1` trust policyで旧confirmedを由来付きcandidateへ降格
  - `manual_seed`と`confirm_identifier` decisionだけをoperator根拠としてconfirmed維持
  - active block対象を除外し、active defer対象をcandidateとして維持
  - untrusted primary website/repo/NSIDをconfirmed用flat fieldへhydrateしない
  - migration reportに旧confirmed、維持、降格、除外、roundtrip diff、hard acceptance conditionを出す
- 実装済み: relation/collector状態契約
  - DB unique制約と同じdedupe key、classification昇格、最新時刻、context統合
  - 通常collectorは明示的な`CollectorResult`を返す
  - lexicon JSON単独の`defines_schema`はcandidate
- 実装済み: 調査履歴retention
  - `prune-investigation-history`はdry-run既定
  - 既定で90日以内の全runとprojectごとの最新3 runを保持
  - `--execute`でcollector runもcascade削除し、`--vacuum`でfile sizeを縮小可能
  - operator decisionとmigration provenanceは対象外
- 実装済み: v1 migration provenance cleanup
  - `prune-migration-provenance`はdry-run既定
  - identifier rowと意味列を維持し、移行専用metadataだけを削除
  - `legacy_record`内の通常metadataは平坦化して保持
- 実装済み: Web write安全策と状態復元
  - `api_version: 2`、server presenter、project revision、stale guard
  - server内mutation直列化、frontend mutation queue、`--read-only`
  - detail tabとURL stateのreload/back/forward復元
- 削除済み: `--enrich-age-days` option alias。`--max-enrich-age-days`のみ使用する

## 動作確認済み

- temp SQLiteでv1→v2 migration、backup、dry-run、再実行
- identifier/relation/decisionのsave→fetch
- relation重複排除
- merge後のblock/defer保持と優先
- investigation replay / benchmark
- CLI helpと既存command parser
- Web API認証、static asset、v2 detail、normalized mutation、partial collector failure、SQLite busy error
- temp SQLiteでv1 migration trust policy、operator confirmation維持、normalized row roundtrip diff 0
- temp SQLiteでcandidate primary NSID / developer DIDの無変更load/save保持
- temp SQLiteで調査履歴pruneのdry-run、最新件数保持、collector cascade削除
- temp SQLiteでmigration provenance削除後もidentifier意味列と通常metadataを保持
- in-app Browserでdesktop検索・mutation・merge確認とmobile detail遷移
- temp SQLiteでenrichのproject境界中断、checkpoint再開、API key非保存
- fake subprocess workerで完了、競合拒否、SIGINT停止、再開
- 実workerと空のtemp DBでnetwork-free protocol実行
- in-app Browserでtemp DBのbulk enrich起動と完了event/result表示
- 全通常テスト197件
- in-app Browserでschema v2 DBのreview-stalled dry-runを実行
  - DB更新なし、全候補839件のうちlimit 50件をresultへ返すことを確認
  - GitHub sync選択時だけscreening optionが有効になることを確認
  - 実外部sync sourceのWeb worker経由実行は未検証
- 2026-06-14にミラー`data/survey.db`を実移行
  - project 2540件、identifier 18388件、decision 113件、relation 0件
  - SQLite integrity / foreign key / canonical snapshot検証済み
  - 旧DBは`data/survey.db.v1-backup-20260614-120431`
  - 移行後の再実行は`already_current`
- 2026-06-16にv1 backupからのmigration dry-runを再監査
  - project 2540件、identifier 18388件、decision 113件、relation 0件
  - canonical snapshot、SQLite integrity、foreign keyを検証
  - manual seed欠落0件、invalid decision 0件、normalized field漏出0件
  - source DBと現在のv2ミラーは変更していない
- 2026-06-16に現在のv2ミラーをread-only監査
  - project/identifier/decision件数とintegrityは正常、manual seed欠落0件
  - 旧移行時の`llm_source` snapshot内に削除済みcompatibility aliasが2537 projects分残る
  - 通常運用へ切り替える時はこのv2ミラーを採用せず、v1 backupから現行migrationで再作成する
- 2026-06-18にv1 backupから現行`v1-quarantine-1` migrationをdry-run
  - project 2540件、identifier 16364件、decision 113件、relation 0件
  - SQLite integrity、canonical snapshot、normalized row無変更roundtripを検証
  - active block衝突0件、根拠不明legacy confirmed 0件、roundtrip diff 0件
  - 実データで見つかった`legacy_record` hydrateと`external_lexicons` roundtrip不整合は修正済み

## 未検証

- 通常運用DBの置換
  - 現在の旧方式v2ミラーにだけ存在するinvestigation 112件 / collector 676件の移送が必要
  - migration command自体には別processとの排他lockがない
- 今回追加したdetail tab、URL state、frontend mutation queue、read-only表示の実ブラウザ確認
  - API/component testとJavaScript syntax checkは通過
  - この作業環境ではlocalhost listenがsandboxに拒否され、in-app Browserは接続できなかった

## 未接続

- sync/reviewのbackground job接続は完了。source page/candidate単位progress、途中停止、checkpoint再開は未接続
- interactive reviewの候補選択式plan実行と`assist-db-edit`の共通job化
- server再起動後のjob一覧復元とcheckpoint discovery
- Tangled optional adapterの通常実行
- OAuth `include:` permission解決
- Webからのinvestigation run一覧、evidence全文、replay差分表示

## 互換性のためだけに残しているもの

- CLIの既存command名と主要option
- collector/evaluationが使うcanonical project dict field
- `service_url_candidate_values()`、`provider_repo_url_values()`、`resource_url_values()`などの導出helper

旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerは維持しない。

## 次候補

- syncのsource内部progress/cancellationとreviewの候補選択式planを追加する
- server再起動後も既存checkpointを発見・再開できる導線を追加する
- identifier tableを直接queryする検索・collision監査へ移し、全project hydrateを減らす
- service browser検証とidentity corroborationのcorpusを増やす

恒久仕様は[project-record.md](project-record.md)、調査契約は[investigation-model.md](investigation-model.md)、長時間処理契約は[background-jobs.md](background-jobs.md)、内部の具体値は[registry-internals.md](registry-internals.md)、将来課題は[roadmap.md](roadmap.md)を参照してください。
