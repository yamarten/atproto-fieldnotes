# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 未完了のmigrationやmaintenance window待ちはありません。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- browser laneのlogin判定を「linkを辿れた」だけから、form / OAuth入口 / 描画後errorの構造化evidenceへ変える。認証情報の入力・submitは行わない。
- browser依存を用意したbounded runでJuntos / Echosky / Moat / gmstn.systemsを比較し、次に最新観測から`service_status_*`のcurrent stateを投影する規則を決める。
- その後、candidate visibilityとeditorial判断を分けた上で、atShare / Mathrのcontextual扱い、score breakdown、Web表示、hold / deferの操作単位を決める。固定sample 14件と紹介済みpositive reference 5件を評価に使う。
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
