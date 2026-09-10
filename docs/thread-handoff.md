# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能は[status.md](status.md)、恒久仕様は
各仕様書、着手条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 現在地

- 「採用済みrepositoryから公開先候補を得て、根拠が揃うserviceを通常enrichで自動採用する」
  範囲は2026-09-10に実運用反映・旧edge退避・反映後確認まで完了。詳細は[status](status.md)を参照。
  汎用API・AppView独自API、primary project_site自動採用、広いpolicy接続は[roadmap](roadmap.md)の別課題。
- 火曜の全tracking state run `enrich-20260908T111813Z-a27f8067`は4,731件完了・1件失敗で終了。
  後続の木曜run `enrich-20260909T224730Z-44374263`はユーザー承認で2026-09-10 11:24 JSTにキャンセルし、
  再開していない。Webは再起動済み、定期設定は維持、queueは空。scheduled presetの`failed`表示は
  キャンセルしたstepによるもので、同じrunを自動再開する指示ではない。
  旧coverage失敗604件とPDSlss（`proj:efcd972d8d51`）の追加enrichは引き続き保留。
  604件の一覧は`data/backups/homepage-role-review-20260909T110921Z/coverage-failures-before-resume.json`。
  両runで確認されたmerge済み旧project keyの失敗は別の運用課題として残る。
- 最終反映直前のDB・checkpoint・定期設定backupは
  `data/backups/final-publication-rollout-20260910T0225Z/`。
  全旧edgeの永続archiveとplan・結果・検証reportは
  `data/migration-archives/repository-trust-20260910T0225Z/`。archiveを一時ファイルとして削除しない。

## 次に必要な作業

1. Lexicon週次観測で最初の比較可能なrunが成立した時、通常sync成功後48時間以内、segment完了、fresh inventory成功、
   同じAsia/Tokyo週を重複加算しないことを確認する。統合・sidecar v3移行・日曜05:00の有効化は完了済み。
   保存済み最初のrunは通常sync失敗による比較不能で、有効period数は0。確認snapshotは[status](status.md)を参照。
   初回run前後の正本registry不変は保存済みstateだけでは確認できていないため、次の実runで前後確認する。

文書整備と開発文書の用語整理は[roadmap](roadmap.md)の別論点として扱います。

このlocal Web UIへのreview linkは`http://127.0.0.1:8888/?project=...`を使います。

## 運用上の注意

- 2026-08-27開始の通常enrichは2,897 / 2,897 project、失敗0で2026-08-28に完了しました。同じ実行時間帯に
  定期`enrich_all`が別runとして開始し、19 project処理後に中断されています。重複が中断原因かは未確認ですが、
  CLI等の別processを含むwriter排他は未接続です。
- DBへ直接影響する操作の前は別のWeb / enrich / CLI writerが動いていないことを確認してください。
- merge前に開始した処理がalias元projectを再作成する経路は保存境界で拒否します。異なるcanonical projectへの
  一般的なprocess間writer排他は未接続のため、上記の運用上の注意は継続します。
- bounded page-to-repository policyの最終補正直前backupは
  `data/backups/project-repository-final-20260828T200038+0900/survey.db`です。
- 移行・補正前backupが上記を含め5世代、各約2.4 GiB残っています。通常運用の確認後、保持する世代を決めて
  不要分を削除してください。
- history JSON圧縮は正本DBへ適用済みです。VACUUMは行っておらず、解放pageは今後の書込みで再利用します。

## 未接続

- sync / reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLI等の別processを含むwriter排他
- DB-level migration / data policy marker

より広い制約と再検討条件は[status.md](status.md)と[roadmap.md](roadmap.md)を参照してください。
