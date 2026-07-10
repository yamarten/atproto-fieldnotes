# Registry Maintenance Handoff

この文書は`codex/registry-maintenance` branchの次作業だけを扱います。通常運用側の引き継ぎは
[../thread-handoff.md](../thread-handoff.md)であり、この文書で置き換えません。

通常運用へ適用済みの内容は`docs/status.md`へ、maintenance seriesを越えて残す候補は`docs/roadmap.md`へ移し、
完了項目や検証履歴をここへ累積しません。この文書はmaintenance series完了時に削除します。

## 現在地

- 実行計画は[registry-maintenance-plan.md](registry-maintenance-plan.md)へ整理済みです。
- branch固有の検証結果は[registry-maintenance-status.md](registry-maintenance-status.md)を参照してください。
- DB open transaction、DB path/create、Python runtime/wrapper契約は実装・temp DB検証済みです。正本DBは
  変更していません。
- 通常運用側ではbug fixや小規模UI改善を継続できます。各作業単位の着手前と適用前に最新実装との差分を
  確認します。

## 次に必要な作業

1. 使用実績を再確認したうえで、壊れている`split-project --new-project-key` optionを原則削除する。
2. same-site first-label fallbackを削除し、主要consumerの回帰testを追加する。
3. Web `--read-only` modeをUI/API/test/docsから削除する。audit/replay/dry-runは維持する。

## DB操作メモ

code・文書のmaintenance作業から正本DBを直接変更しません。DBへ直接影響するcommandは別のmaintenance
operationとして、Web/enrich/CLI writerの停止、backupまたは複製、dry-run承認、integrity checkを含めて
実行します。

保存量baselineは、通常運用中のDBへ重い集計を直接かけず、整合したSQLite snapshotを使います。
