# Registry Maintenance Handoff

この文書は`codex/registry-maintenance` branchの次作業だけを扱います。通常運用側の引き継ぎは
[../thread-handoff.md](../thread-handoff.md)であり、この文書で置き換えません。

通常運用へ適用済みの内容は`docs/status.md`へ、maintenance seriesを越えて残す候補は`docs/roadmap.md`へ移し、
完了項目や検証履歴をここへ累積しません。この文書はmaintenance series完了時に削除します。

## 現在地

進捗の正本は[registry-maintenance-status.md](registry-maintenance-status.md#実行台帳)の33 IDです。
現在は完了29、roadmapへ延期2、未着手2です。未終端2 IDは下記の作業順にすべて含めます。
ここへ完了項目や検証履歴を複製しません。

通常運用側ではbug fixや小規模UI改善を継続できます。各作業単位の着手前と適用前にmasterとの差分を確認します。

## 次に必要な作業

1. P3.3の正本maintenanceとP3.4の保存量判断gateは終端した。
2. P7.1で通常側正本文書へ最終反映し、このmaintenance用status/handoffを削除する。
3. P7.2でREADME/project-registry重複、絶対path、stale test数、registry-internals節を独立auditする。
   - P7.1: 通常側正本文書へ反映し、maintenance用status/handoffを削除する。
   - P7.2: 文書auditを独立実施する。

実施しないitemは省略せず、再検討条件とともに`docs/roadmap.md`へ移して台帳を
`roadmapへ延期`にします。`一部完了`、`未着手`、`別operation待ち`のままseriesを閉じません。

## DB操作メモ

code・文書のmaintenance作業から正本DBを直接変更しません。DBへ直接影響するcommandは別のmaintenance
operationとして、Web/enrich/CLI writerの停止、backupまたは複製、dry-run承認、integrity checkを含めて
実行します。

直近のP3.3 operationでは`data/survey.db.backup-20260711-170101`を作成してから正本へ適用しました。
Webは`0.0.0.0:8888`で再起動済みです。VACUUMは行っていないため、SQLite fileは4.4 GiBのままですが、
解放pageは後続writeが再利用できます。

保存量baselineは、通常運用中のDBへ重い集計を直接かけず、整合したSQLite snapshotを使います。
