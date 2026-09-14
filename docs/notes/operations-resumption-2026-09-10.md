# 2026-09-10の運用保留・再開情報

運用上の保留と、保持判断が未了のbackup・archiveをまとめます。実環境の情報は2026-09-10時点の記録に基づきます。作業再開時には実行中のプロセスと保存状態を確認します。

## 一括enrichの保留

- キャンセル済みの一括enrich `enrich-20260909T224730Z-44374263`の再開は指示されていない。
- PDSlss（`proj:efcd972d8d51`）は当時の一括enrich開始時に確定した対象に含まれず、追加enrichはユーザー指示で保留。
- 一括enrichで確認されたmerge済み旧project keyの失敗は未解決として残る。

## Lexicon週次観測の初回確認

最初の比較可能なLexicon観測実行で、[週単位のperiodの評価条件](../developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)に沿ってperiodが一度だけ加算されることを確認する。2026-09-10の保存済みsnapshotでは有効period数0。初回の観測実行前後で正本registryが変わらないことは保存状態だけでは未確認のため、次の実行で前後確認する。

## Backupとarchiveの保全

- 最終反映直前のDB・checkpoint・定期設定backup: `data/backups/final-publication-rollout-20260910T0225Z/`。
- 旧信頼グラフの全接続（edge）の永続archiveと計画・結果・検証report: `data/migration-archives/repository-trust-20260910T0225Z/`。archiveを一時ファイルとして削除しない。
- ページ上の限定された範囲から公式リポジトリを判断する規則の最終補正直前backup: `data/backups/project-repository-final-20260828T200038+0900/survey.db`。
- backupの保持世代は未決。削除する前に、通常運用の確認状況と現在残っているbackupの用途を確かめる。以下の所在について、現存するfileと保持用途を確認する。

| 作業 | 記録されたbackup所在 |
| --- | --- |
| project site導入 | `data/backups/project-site-20260825T011520+0900/` |
| project repository移行 | `data/backups/project-repository-20260825T022442+0900/survey.db` |
| リポジトリの手動レビュー反映 | `data/backups/project-repository-review-20260827T204144+0900/survey.db` |
| Tangled初回取り込みの補正 | `data/backups/tangled-jetstream-first-run-correction-20260830/` |
| homepage role補正・checkpoint | `data/backups/homepage-role-review-20260909T110921Z/`。旧604件の一覧は配下の`coverage-failures-before-resume.json` |
| Website role復元・checkpoint | `data/backups/website-role-restoration-20260909T124023Z/` |

DB操作前には別のWeb/enrich/CLIによる書き込み処理を確認する。マージ元の旧キーでprojectが再作成されることを防ぐ検査はあるが、異なる登録プロジェクトへの書き込みをプロセス間で排他する仕組みはない。このローカルWeb UIでレビューするためのリンクは`http://127.0.0.1:8888/?project=...`を使う。
