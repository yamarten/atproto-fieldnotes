# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能は[status.md](status.md)、恒久仕様は
各仕様書、着手条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 現在地

- schema version 3、Website / service / project siteの分離、repositoryの`project_repository`への統一、
  bounded page-to-repository policyは正本DBへ適用済みです。広いrole-aware trust自動採用、LLM relation assessor、
  自動mergeは意図的にproductionへ接続していません。
- promotion queueはservice確認度、technical interest、manual priorityを分け、DNSとschema recordで確認できる
  Lexicon publisherも独立signalとして優先します。
- Qwen / Gemma比較後も既定modelと定期jobはQwenを維持します。repository解析は本文付きevidence blockと
  closed candidateへ変更し、固定`goat`と10 repository sample（問題2件はv12で再確認）で候補外collectionとclaim棄却を解消しました。
  正本DBへの再解析・書き込みは行っていません。
- 外部sourceとbrowser laneを評価し、Sembleは維持、TangledはUFOsからJetstream v2 snapshotへ置換、
  anonymous browser verificationはWeb project detailへ接続しました。一時的な`source_observations`と未接続の
  Tangled timeline / community / bot adapterは撤去しました。根拠は
  [評価記録](notes/source-lane-evaluation-2026-08-30.md)を参照してください。

## 次に必要な作業

1. マージ推奨110件を既存queueで処理するか、理由別表示と承認・保留・`separate_project`を持つ専用review導線を
   追加するか判断する。自動mergeは前提にしない。
2. `sync-introduced`のpage安全上限を総作業量のように`1 / 100`表示する進捗契約を修正する。

このlocal Web UIへのreview linkは`http://127.0.0.1:8888/?project=...`を使います。

## 運用上の注意

- 2026-08-27開始の通常enrichは2,897 / 2,897 project、失敗0で2026-08-28に完了しました。同じ実行時間帯に
  定期`enrich_all`が別runとして開始し、19 project処理後に中断されています。重複が中断原因かは未確認ですが、
  CLI等の別processを含むwriter排他は未接続です。
- DBへ直接影響する操作の前は別のWeb / enrich / CLI writerが動いていないことを確認してください。
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
