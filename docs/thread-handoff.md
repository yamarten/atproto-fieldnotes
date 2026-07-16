# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- current service observation projectionの正本DB適用まで完了しました。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- `repair-db`は正本へ適用済みで再dry-run 0件です。重複candidate primary 2,063 rowの削除とtrust確認済み64 NSIDの`owned_nsid`化も完了し、残りはReview Queueの`Signal kind`を`primary_identifier_needs_confirmation`にした時に出る61 project / 63 identifierです。全件解決後にcandidate primary専用処理を削除します。
- 2026-08-10到達または新規investigation 1,000 runsの早い方で、保存量baselineを再計測する。

## DB 操作メモ

- 2026-07-16のcandidate primary整理直前backupは`.tmp/repair-candidate-audit/survey-before-primary-cleanup.db`です。

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認してください。

2026-07-16 current service projection適用前backupは`data/survey.db.backup-20260716-202316-before-repair`です。2026-07-13にVACUUM済みで、
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
