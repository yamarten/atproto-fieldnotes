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
  closed candidateへ変更し、policy v14でclaim可能なmetadataをdescriptionだけに限定し、README由来NSIDへ
  reverse-domain authority条件を追加しました。未調整のhash抽出6件は機械検査6/6有効で、新しい悪化は確認
  しませんでした。package配布repositoryの役割、fixture Lexicon、拡張子付きfile名のNSID候補化は残件ですが、
  追加規則を増やさず観測対象に留めます。正本DBへの再解析・書き込みは行っていません。
- 外部sourceとbrowser laneを評価し、Sembleは維持、TangledはUFOsからJetstream v2 snapshotへ置換、
  anonymous browser verificationはWeb project detailへ接続しました。一時的な`source_observations`と未接続の
  Tangled timeline / community / bot adapterは撤去しました。根拠は
  [評価記録](notes/source-lane-evaluation-2026-08-30.md)を参照してください。
- 一時的な`Review > Merge Recommendations`はproject境界の候補処理を完了し、正本DBの現行推奨と未解除の
  専用保留が0件であることを確認して撤去しました。元設計にあったidentifierごとの処遇選択は実装されて
  いなかったため、その完了までは主張しません。撤去時のread-only監査結果は
  [マージ規則shadow比較](notes/project-merge-policy-shadow-comparison.md)へ閉じ、正本DBは変更していません。
  通常のMerge review、`separate_project`判断、merge lineage、shadow comparator / policyは維持しています。
- app record exportは、明示manifestで選んだprojectへUTC `createdAt`を付け、PDS処理へ渡せる裸の
  `community.lexicon.app.entry` record配列を生成するところまで接続しました。scope未確定keyは別allowlistが
  必要で、全件成功時だけ成果物を書きます。正本DBとPDSへは書きません。

## 次に必要な作業

1. post-sync週次評価変更commitをmasterへ統合し、全testとOperations導線を再確認する。
2. 統合後の実運用`data/lexicon-publication-catalog.db`をschema version 3へ移行する。migrationは旧runを残し、誤って
   蓄積した3成功とsticky理由をlegacy countへ退避して現catalog revisionでrebaselineする。必要なら理由付き
   `rebaseline-evaluation`を明示実行する。
3. Operationsで`lexicon_publication_weekly`（毎週日曜05:00 Asia/Tokyo）が有効であることと、土曜23:00の通常sync後に
   比較可能判定されることを確認する。最初の実runは正本registryの前後不変も確認する。
4. 開発文書の用語整理を行う。

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
