# Registry Maintenance Handoff

この文書は`codex/registry-maintenance` branchの次作業だけを扱います。通常運用側の引き継ぎは
[../thread-handoff.md](../thread-handoff.md)であり、この文書で置き換えません。

通常運用へ適用済みの内容は`docs/status.md`へ、maintenance seriesを越えて残す候補は`docs/roadmap.md`へ移し、
完了項目や検証履歴をここへ累積しません。この文書はmaintenance series完了時に削除します。

## 現在地

進捗の正本は[registry-maintenance-status.md](registry-maintenance-status.md#実行台帳)の33 IDです。
現在は完了12、一部完了7、未着手13、別operation待ち1です。未終端21 IDは下記の作業順にすべて含めます。
ここへ完了項目や検証履歴を複製しません。

通常運用側ではbug fixや小規模UI改善を継続できます。各作業単位の着手前と適用前にmasterとの差分を確認します。

## 次に必要な作業

1. 現在開いている分割を閉じる。
   - P6.1: 責務mapと混入節auditの完了条件を決める。
   - P6.3: `tldextract`とvendored PSL fallbackの正本を一つにする。
   - P6.6: provider別repo URL/naming helperを移すかroadmapへ延期する。
   - P6.7: 残るorchestration分割の実施範囲を決め、移動またはroadmapへ延期する。
2. 計測・保存scopeの部分完了を閉じる。
   - P2.2: 代表timing/prompt/operator時間を取得するか、測らない項目をroadmapへ移す。
   - P3.1: `resource_url_records` budgetの全生成経路を監査する。
   - P3.2: cleanup reportとplan上のcategory/role/host/path要件との差を決着する。
3. Webと長時間operationの未着手code auditを行う。
   - P2.5: Web診断、scheduler、安全境界。
   - P4.1、P4.2、P4.3、P4.4、P4.5: Semble、identifier lookup、bulk failure、LLM retry、source設定。
4. 調査品質corpusを作る。
   - P5.1、P5.2、P5.3: decision/bundle export、sanitize rule、operator correction fixture。
5. 外部状態または別承認が必要な確認を分離して実行する。
   - P2.4: bounded real-source smoke。
   - P3.3: 正本DB cleanupとsame-site過去誤帰属audit。実行前に別承認とmaintenance windowが必要。
   - P3.4: P3.3後の増加率に基づくhistory表現の判断gate。
6. 全IDが終端してからcloseoutする。
   - P7.1: 通常側正本文書へ反映し、maintenance用status/handoffを削除する。
   - P7.2: 文書auditを独立実施する。

実施しないitemは省略せず、再検討条件とともに`docs/roadmap.md`へ移して台帳を
`roadmapへ延期`にします。`一部完了`、`未着手`、`別operation待ち`のままseriesを閉じません。

## DB操作メモ

code・文書のmaintenance作業から正本DBを直接変更しません。DBへ直接影響するcommandは別のmaintenance
operationとして、Web/enrich/CLI writerの停止、backupまたは複製、dry-run承認、integrity checkを含めて
実行します。

保存量baselineは、通常運用中のDBへ重い集計を直接かけず、整合したSQLite snapshotを使います。
