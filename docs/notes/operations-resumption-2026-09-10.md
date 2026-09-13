# 2026-09-10の運用保留・再開情報

運用上の保留と、保持判断が未了のbackup・archiveをまとめます。実環境の情報は2026-09-10時点の記録に基づきます。作業再開時にはprocessと保存状態を確認します。

## 一括enrichの保留

- キャンセル済みrun `enrich-20260909T224730Z-44374263`の再開は指示されていない。
- PDSlss（`proj:efcd972d8d51`）は当時のrunの固定対象外で、追加enrichはユーザー指示で保留。
- 一括enrichで確認されたmerge済み旧project keyの失敗は未解決として残る。

## Lexicon週次観測の初回確認

最初の比較可能runで[periodの評価条件](../developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)に沿ってperiodが一度だけ加算されることを確認する。2026-09-10の保存済みsnapshotでは有効period数0。初回run前後の正本registry不変は保存済みstateだけでは未確認のため、次の実runで前後確認する。

## Backupとarchiveの保全

- 最終反映直前のDB・checkpoint・定期設定backup: `data/backups/final-publication-rollout-20260910T0225Z/`。
- 全旧edgeの永続archiveとplan・結果・検証report: `data/migration-archives/repository-trust-20260910T0225Z/`。archiveを一時ファイルとして削除しない。
- bounded page-to-repository policyの最終補正直前backup: `data/backups/project-repository-final-20260828T200038+0900/survey.db`。
- backupの保持世代は未決。削除する前に、通常運用の確認状況と現在残っているbackupの用途を確かめる。以下の所在について、現存するfileと保持用途を確認する。

| 作業 | 記録されたbackup所在 |
| --- | --- |
| project site導入 | `data/backups/project-site-20260825T011520+0900/` |
| project repository移行 | `data/backups/project-repository-20260825T022442+0900/survey.db` |
| repositoryのoperator review反映 | `data/backups/project-repository-review-20260827T204144+0900/survey.db` |
| Tangled初回run補正 | `data/backups/tangled-jetstream-first-run-correction-20260830/` |
| homepage role補正・checkpoint | `data/backups/homepage-role-review-20260909T110921Z/`。旧604件の一覧は配下の`coverage-failures-before-resume.json` |
| Website role復元・checkpoint | `data/backups/website-role-restoration-20260909T124023Z/` |

DB操作前には別のWeb/enrich/CLI writerを確認する。merge済みalias元keyの復活拒否は、異なるcanonical projectへの一般的なprocess間writer排他を提供しない。このlocal Web UIへのreview linkは`http://127.0.0.1:8888/?project=...`を使う。
