# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 日常運用安定化の8項目は実装済みです。
- project detailのmerge候補計算は初期表示から分離済みです。
  - 初期detailを先に表示する
  - merge候補は自動で後続計算し、完了次第表示する
  - 計算結果はDBへ保存しない
- workspaceの現行v2 DBは移行検証用mirrorです。通常運用DBとして採用しません。

## 次に必要な作業

本番v1 DBのoffline migrationが直近の未完了作業です。手順とacceptance conditionは
[v1-migration-trust.md](notes/v1-migration-trust.md)と
[roadmap.md](roadmap.md)の「通常DB置換前」を参照してください。

通常DB置換前に確認した複数project mutationのatomic化とmerge後のsource identity/history保持は
実装済みです。既存v2 DBへ`project_key_aliases`をadditiveに作るため、進行中のv1 migrationを
やり直す必要はありません。新コードへ切り替えるまではmerge/split/separateを書き込まないでください。

v2以降のincremental schema migration runnerとSQLite接続自体のread-only化は、必要が生じた時の
候補へ戻しています。通常DB置換前には、旧方式v2との誤認を防ぐ現行trust/data policy markerだけを扱います。

重要な前提:

- Web、enrich、CLI writerを停止してから実行する
- 本番v1の唯一のbackup自体をmigration入力として置換しない
- workspaceの旧方式v2 mirrorと、その調査履歴は引き継がない
- 本番v1に存在するinvestigation/collector rowは通常migrationのコピー対象
- `migrate-db`自体には別processとの排他lockがない

## 未検証・未接続

未検証:

- 本番v1 DBに対するdry-runと通常運用DBの置換
- Web workerから実外部sync sourceへのend-to-end接続

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLIなど別processを含むwriter排他

より広い制約と確認済み範囲は[status.md](status.md)を参照してください。
