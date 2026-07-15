# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 未完了のmigrationやmaintenance window待ちはありません。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- 最初の作業は[roadmapの current service observation projection](roadmap.md)です。probeごとの最新結果は`lane + requested_url`単位で残りますが、project直下のservice status / confidence / evidenceには過去値のmax / unionが残り得ます。
- 最新のURL別・lane別観測からcurrent scalarを作るpure projectionと、lane間のprecedence・矛盾規則を実装します。temp DBの新旧観測fixtureでcurrent scalarだけが変わり、investigation historyが不変であることを確認します。
- 正本DBへ反映する前に複製に対するdry-run / auditを行い、適用は別途承認を得ます。その後はroadmapの順にeditorial policy、queue API / Web UI、executable evaluationへ進めます。
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
