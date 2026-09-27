# Tangled forkの既存候補レビュー

通常の新規発見はrecordがfork元を宣言するrepoを除外しますが、既存URLへのexact一致更新と明示登録は維持します。次回sync workerで使われる実装で、Web再起動は不要です。統合後の本番syncの除外実績と既存候補の処遇は未確認です。[選別仕様](../developer/specs/discovery-and-sync.md#forkの新規発見と明示登録)を参照してください。

2026-09-20の正本read-only監査では、同日JSTに初登録され`tangled_jetstream`由来の165件中、採用repo URLと一致する保存recordを158件で確認しました。44件にfork元宣言（AT-URI 26、repository DID 18）、114件にsource欠落、7件はこの経路で未確認です。owner配下の別repoの観測は数えていません。

既存44件は新規除外規則の導入後も更新されます。一括ignoreの根拠ではなく、個別処遇にはfork元・独立した提供面・技術的追加価値のreviewが必要です。codeの完全一致や独立deploymentは未確認です。`repair-db`で新規発見規則を既存行の削除・ignoreへ変換しません。

[保存fixture](../../tests/fixtures/project_registry/tangled-fork-records.json)のveryroundbird.house、maelstrom.dev、callie.on-her.computer、ellioth.coのWitchsky系4件はfork宣言の確認例です。ellioth側READMEには親サイトへのlinkもあり、README URLの有無だけでは独立性を保証できません。通常syncでの除外件数・sequence進行を確認する余地は残りますが、そのための追加job・全履歴実行は指示されていません。
