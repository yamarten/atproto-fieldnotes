# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 日常運用安定化の8項目は実装済みです。
- Web server reloadのpackage entrypoint修正まで`master`へ反映済みです。
- 判定feedback対応は実装・DB適用とも進行済みです。現在の確認済み範囲は[status.md](status.md)、trust graphの現行仕様は[registry-internals.md](registry-internals.md)、将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- `proj:29a36771674f`など、複数project collisionの個別split/merge判断を行う。

## DB 操作メモ

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認してください。

この作業を理由に専用worktreeを維持する必要はありません。次スレッドはmain worktreeから開始して構いません。DBへ直接影響するcommandは従来どおりWeb/enrich/CLI writerを停止し、必要ならその時点で新しいbackupまたは複製を用意して実行してください。

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
