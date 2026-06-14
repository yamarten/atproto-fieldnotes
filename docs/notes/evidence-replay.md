# Evidence Replay Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。契約は
> [../investigation-model.md](../investigation-model.md) を参照してください。

## 現在地

実装済み:

- `investigation_runs` / `collector_runs` への v2 bundle と評価 snapshot 保存
- EvidenceBundle v2 fixtureの読み込み
- v1 fixtureはv2へ変換済みで、通常readerの互換は削除済み
- `replay-investigation`, `benchmark-investigation`, `export-investigation-fixture`
- relation/evaluation を中心にした semantic diff
- 6 case の synthetic/regression corpus

動作確認済み:

- replay 中に collector と service follow-up network を呼ばないこと
- temp SQLite での run 保存、読み込み、再評価
- benchmark 6 case の期待評価

## Corpus 運用

1 case は `case_id`、誤判定の説明、入力 project、bundle、期待 evaluation を持ちます。fixture へ
取り込む際は token、private endpoint、不要な本文、個人情報を除去し、判定に必要な locator、hash、
bounded extract だけを残します。

未接続:

- 本番ミラー上の defer/block/manual correction を自動抽出して fixture 候補にするコマンド
- heuristic version 間で relation の追加・削除理由まで説明する report
- fixture update の review policy

## 将来目標

- 実際に修正した誤判定を corpus へ追加する
- intended fix、regression、unknown から確定への変化を別集計する
- source 収集 benchmark と evaluation benchmark を分ける
