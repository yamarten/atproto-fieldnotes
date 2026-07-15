# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 未完了のmigrationやmaintenance window待ちはありません。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- 最初の作業は[roadmapの rendered auth surface evidence](roadmap.md)です。[scripts/project_registry_core/service.py](../scripts/project_registry_core/service.py)の`collect_browser_service_evidence()`は現在、loginらしいsame-site linkを辿ったpageが1件以上あるだけで`login_surface_reached=true`にします。
- browser navigationの返値とservice verification recordを拡張し、link追従、password / handle / email入力とsubmitの組み合わせ、OAuth入口、network / console errorを別evidenceにします。認証情報の入力、form submit、認証後だけ発生する障害は対象外です。
- [tests/test_project_registry_research_pipeline.py](../tests/test_project_registry_research_pipeline.py)に、linkだけのpage、password form、OAuth入口、描画後errorのunit testを追加します。その後`requirements-browser.txt`の依存を用意し、[manual gold fixture](../tests/fixtures/project_registry/manual-gold-v1.json)の4 caseにある`subject.service_url`を次の形でdry-runします: `bin/project-registry verify-service --seed <service_url> --type service_url --lane browser`
- browser evidenceの受入後は、roadmapの2〜5に従ってcurrent service observation projection、editorial policy、queue API / Web UI、executable evaluationの順に進めます。これらをbrowser evidenceと同じ変更に混ぜません。
- 2026-08-10到達または新規investigation 1,000 runsの早い方で、保存量baselineを再計測する。

## DB 操作メモ

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認してください。

2026-07-11 cleanup適用前backupは`data/survey.db.backup-20260711-170101`です。2026-07-13にVACUUM済みで、
`freelist_count=0` / `integrity_check=ok`を確認済みです。DBへ直接影響するcommandは従来どおりWeb/enrich/CLI writerを停止し、必要なら
その時点で新しいbackupまたは複製を用意して実行してください。

## 未検証・未接続

未検証:

- Web workerから実外部sync sourceへのend-to-end接続

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLIなど別processを含むwriter排他
- DB-level migration/data policy marker
  - 正本DBが単一で、移行後の修正も通常DBへ反映済みのため直近優先から外した
  - 将来、複数DBやpolicy世代分岐を扱う場合だけ再検討する

より広い制約と確認済み範囲は[status.md](status.md)を参照してください。
