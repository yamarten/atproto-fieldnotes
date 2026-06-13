# Research Pipeline Phase 0 Brief

## 目的

`scripts/project_registry.py` の総行数そのものではなく、調査機能を変更する際に読む必要がある working set を小さくします。collector が共有 project dict を直接更新する構造を段階的に減らし、収集済み evidence から integration / evaluation だけを反復できる境界を作ります。

## 主要判断

- 既存の CLI、top-level Python API、DB schema、`record_json` / `ops_json` 保存形は維持する
- structured evidence schema は version 1 とし、通常実行では一時オブジェクトとして扱う
- raw evidence の永続化は test fixture に限定し、通常 DB には既存 field へ統合した結果だけを保存する
- GitHub repository 調査と GitHub / Bluesky search を Phase 0 の collector 境界へ接続する
- ownership、ATProto寄与度、service status の評価本体を network / DB 非依存の関数にする
- `project_registry.py` は互換 facade と orchestration を担当する

## 全体像

Phase 0 で接続済みの調査経路は次です。

```text
query planning
  -> GitHub / Bluesky collectors
  -> EvidenceBundle v1
  -> integration
  -> evaluation / finalize
  -> existing save_project
```

GitHub repo collector は metadata、README URL、tree 由来 lexicon hint を snapshot evidence として返します。ownership 判定と project field への反映は integration 側で行います。

## 非目標

- legacy field の全面削除
- project record / ops storage の再設計
- raw evidence の DB 保存、retention、run artifact 管理
- lexicon resolution、service probe、resource page collector の全面移行
- Web UI の再構築
- heuristic の意味変更

## 将来の設計目標

- lexicon / service / resource page collector も同じ evidence 境界へ移す
- source 別 retry / rate-limit / circuit breaker policy を collector ごとに持つ
- 保存済み evidence corpus に対して複数 heuristic version を比較する
- raw evidence を通常運用で保存する場合は、DBとは別のartifact lifecycleを先に設計する
