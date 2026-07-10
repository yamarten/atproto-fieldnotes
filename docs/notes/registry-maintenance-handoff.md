# Registry Maintenance Handoff

この文書は`codex/registry-maintenance` branchの次作業だけを扱います。通常運用側の引き継ぎは
[../thread-handoff.md](../thread-handoff.md)であり、この文書で置き換えません。

通常運用へ適用済みの内容は`docs/status.md`へ、maintenance seriesを越えて残す候補は`docs/roadmap.md`へ移し、
完了項目や検証履歴をここへ累積しません。この文書はmaintenance series完了時に削除します。

## 現在地

- 実行計画は[registry-maintenance-plan.md](registry-maintenance-plan.md)へ整理済みです。
- branch固有の検証結果は[registry-maintenance-status.md](registry-maintenance-status.md)を参照してください。
- Phase 1のDB open transaction、DB path/create、Python runtime/wrapper契約、`split-project` explicit key削除、
  same-site first-label fallback削除、Web `--read-only` mode削除は実装・temp DB検証済みです。正本DBは
  変更していません。
- product briefと2026-07-10静止DB baselineは作成済みです。snapshotは`.tmp/`に置き、正本DBは変更して
  いません。
- 新規enrich runのphase timingとLLM request metadataは保存、API、Web表示まで接続済みです。旧runへの
  backfill、transaction commit、operator review時間の計測は行っていません。
- service verificationのHTML/link/URL budgetとcurrent surface upsertは実装済みです。正本DB cleanupは
  実施していません。
- 通常運用側ではbug fixや小規模UI改善を継続できます。各作業単位の着手前と適用前に最新実装との差分を
  確認します。

## 次に必要な作業

1. collector historyのGitHub/service payloadへ新規保存budgetを実装する。
2. service verification cleanup dry-runをsemantic diff付きの正式reportへする。
3. 新規timingを代表sampleで取得し、長時間runの支配区間を確認する。
4. same-site過去誤帰属の正本DB auditは、保存量cleanupと同じmaintenance windowで行う。

## DB操作メモ

code・文書のmaintenance作業から正本DBを直接変更しません。DBへ直接影響するcommandは別のmaintenance
operationとして、Web/enrich/CLI writerの停止、backupまたは複製、dry-run承認、integrity checkを含めて
実行します。

保存量baselineは、通常運用中のDBへ重い集計を直接かけず、整合したSQLite snapshotを使います。
