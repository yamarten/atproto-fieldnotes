# Tangled fork選別の統合と既存候補

通常の新規発見から、Tangled recordがfork元を宣言しているrepoを除外する変更はmasterへ統合済みです。既存projectの処遇と統合後の本番syncでの除外実績は未確認・未実施です。現在の選別条件と明示登録経路は[取り込み仕様](../developer/specs/discovery-and-sync.md#forkの新規発見と明示登録)を参照してください。

## 確認範囲

2026-09-20に公式lexiconと`ForkRepo`、`models.Repo.AsRecord`の公開ソースを限定取得し、recordの`source`が親repository DIDまたは旧AT-URIを保存することを確認しました。外部clone URIを同じ意味とする根拠はないため、新規除外の対象にしません。取得契約そのものとrequest数は変更せず、選別のための追加network requestもありません。

正本DBを`mode=ro`、`query_only=ON`、`temp_store=MEMORY`で監査しました。9月20日JSTに初登録され`discovery_sources`に`tangled_jetstream`を持つ165件のうち、保存済みinvestigation bundleから採用済みrepo URLと一致するTangled recordを取得できたのは158件です。その44件にfork元宣言があり、26件がAT-URI、18件がrepository DIDでした。残る114件はsource欠落、7件はこの保存観測経路で確認できていません。owner配下の別repoの観測を対象project自身のrecordに数えていません。

`veryroundbird.house/witchsky.app`、`maelstrom.dev/witchsky.app-fork`、`callie.on-her.computer/witchsky.app`、`ellioth.co/witchsky.app`の4件は保存recordの投影を[fixture](../../tests/fixtures/project_registry/tangled-fork-records.json)に固定し、通常syncで新規作成されないことを確認しました。fork宣言は確認済みですが、codeの完全一致や個別forkの独立したdeploymentは未確認です。追加で取得したellioth側READMEには親サイト`witchsky.app`のリンクがあり、READMEのURLの存在だけを使うscreeningでは独立性を保証できません。GitHubの例外screeningをTangledの通常発見へ持ち込まない理由です。

temp DBのtestは、作成と除外の混在、独立forkの明示登録後のexact更新、判定不能source、owner保留と回復、delete/update folding、dry-run、all-history、作成無効時のsequence境界、キャンセル、保存例外のrollbackを扱います。独立forkの保持は架空fixtureでの明示登録契約の確認であり、現実の独立性の自動判定ではありません。Web UI変更はありません。

## 残る作業と反映境界

Webはsyncごとにcheckoutのscriptsを参照してworkerを新規起動するため、次回syncから変更を読み込みます。今回のためのWeb再起動・正本補正は不要です。通常syncで除外件数とsequence進行を確認する余地は残りますが、その確認目的でjobを追加実行していません。165件の削除・ignore・mergeも行っていません。

既存44件は新規除外規則があってもexact一致で更新されます。個別処遇を進める場合は、fork元と独立したサービス・技術的追加価値を候補ごとにreviewしてから判断してください。44件を一括ignoreする根拠ではありません。`repair-db`は新規発見条件の変更を既存projectの削除・ignoreへ変換する手段ではなく、今回のための正本適用は不要です。

選別済みrecordの定期再試行queueはありません。将来取り込みたいforkは明示登録できます。新しいupdateまたは明示的な履歴再走査でも再評価されますが、source宣言が残る限り通常新規発見から除外します。バックログ消化、event上限変更、全履歴再実行はこの変更に含めません。
