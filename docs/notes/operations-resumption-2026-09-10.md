# 2026-09-10の運用保留・再開情報

旧thread-handoffから引き継いだ、特定作業の保留と保全対象です。2026-09-10の記録を保持したもので、
現在のprocess、queue、設定、backup世代を再監査した結果ではありません。
機能の現在地は[status](../status.md)、恒久的な仕様・手順は[文書管理方針](../documentation-policy.md)から参照します。
保留を解消したら本記録に結果を残し、statusの有効な注意から参照を外します。

## 一括enrichの保留

- 火曜の全tracking state run `enrich-20260908T111813Z-a27f8067`は4,731件完了・1件失敗で終了。
- 木曜run `enrich-20260909T224730Z-44374263`はユーザー承認で2026-09-10 11:24 JSTにキャンセルし、再開していない。
  記録時点ではWeb再起動済み、定期設定維持、queueは空。scheduled presetの`failed`表示はキャンセルしたstepによる。
  この記録は同じrunを自動再開する指示ではない。
- 旧coverage失敗604件とPDSlss（`proj:efcd972d8d51`）の追加enrichはユーザー指示で保留。
  一覧は`data/backups/homepage-role-review-20260909T110921Z/coverage-failures-before-resume.json`。
- 両runで確認されたmerge済み旧project keyの失敗は別の運用課題として残る。

## Lexicon週次観測の初回確認

最初の比較可能runで[periodの評価条件](../developer/specs/lexicon-publication-catalog.md#post-sync週次評価と再評価gate)に
沿ってperiodが一度だけ加算されることを確認する。2026-09-10の保存済みsnapshotでは有効period数0。
初回run前後の正本registry不変は保存済みstateだけでは未確認のため、次の実runで前後確認する。

## Backupとarchiveの保全

- 最終反映直前のDB・checkpoint・定期設定backup: `data/backups/final-publication-rollout-20260910T0225Z/`。
- 全旧edgeの永続archiveとplan・結果・検証report: `data/migration-archives/repository-trust-20260910T0225Z/`。
  archiveを一時ファイルとして削除しない。
- bounded page-to-repository policyの最終補正直前backup:
  `data/backups/project-repository-final-20260828T200038+0900/survey.db`。
- 記録時点の移行・補正前backupは上記を含め5世代、各約2.4 GiB。
  通常運用の確認後、現在の世代・用途を確かめて保持世代を決め、不要分を削除する。

DB操作前には別のWeb/enrich/CLI writerを確認する。merge済みalias元keyの復活拒否は、
異なるcanonical projectへの一般的なprocess間writer排他を提供しない。
このlocal Web UIへのreview linkは`http://127.0.0.1:8888/?project=...`を使う。
