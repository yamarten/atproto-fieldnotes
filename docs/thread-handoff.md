# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- promotion queueのservice確認度、手動優先度、固定fixture評価と正本DB sample reviewまで完了しました。
- LLM評価reportはreasoning要求とruntime確認、prompt調整に使ったcase、同一入力の複数回差分を区別し、2 reportを比較できる形へ更新しました。
- categoryを主な利用者と利用形態で定義し直し、`auth_only`をcategoryから外して`atproto_auth_only`へ分離しました。前回LLM由来のscopeを次のpromptへ戻すfeedback loopも除去し、全492 testを確認済みです。正本DBの旧`category=auth_only` 35件も個別移行済みです。
- 旧category契約でのQwen/Gemma一致数とgoat判定は次のmodel選定へ直接使いません。通常運用modelの変更は未決定です。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- 未使用21件の元profileなし／ありは実測済み。元profileなしではQwen/Gemmaともcategory 17/21だが、全検査はQwen 17/21、Gemma 21/21、根拠外identifierはQwen 2件、Gemma 0件。
- Qwen/Gemmaの未使用8件×2回とmodel名を伏せた文章比較は完了。文章比較はGemma 3、Qwen 2、同等3。次は重い入力、複製DB比較の順。手動footprintはQwen 20 GB、Gemma 16 GB。Agents A1のfootprint要求は23時台の定期jobと競合して2回`Model unloaded`となったため、idle時にbackground用途の切替信頼性とmemoryを再確認する。
- GitHub README本文が通常enrichのevidenceへ保存されないため、repository descriptionが空のprojectでartifact roleを判断できない。model比較とは分けて収集契約の修正要否を決める。
- 現行gpt-oss MLXはruntime APIで`low / medium / high`対応と既定`low`を確認済み。過去のGGUF結果は`low`適用済み比較へ再利用しない。
- category移行前backup `data/survey.db.backup-20260725-auth-category-migration`は移行直後のrollback専用。2026-07-28まで通常利用で問題がなければ削除する。
- 2026-08-10到達または新規investigation 1,000 runsの早い方で、保存量baselineを再計測する。

## DB 操作メモ

- 2026-07-16のcandidate primary整理直前backupは`.tmp/repair-candidate-audit/survey-before-primary-cleanup.db`です。
- 2026-07-17のrole統合適用直前backupは`data/survey.db.backup-20260717-105233-before-primary-role-cleanup`です。

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認してください。

2026-07-16 current service projection適用前backupは`data/survey.db.backup-20260716-202316-before-repair`です。2026-07-13にVACUUM済みで、
`freelist_count=0` / `integrity_check=ok`を確認済みです。DBへ直接影響するcommandは従来どおりWeb/enrich/CLI writerを停止し、必要なら
その時点で新しいbackupまたは複製を用意して実行してください。

## 未検証・未接続

未検証:

- Web workerから実外部sync sourceへのend-to-end接続

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLIなど別processを含むwriter排他
- DB-level migration/data policy marker
  - 正本DBが単一で、移行後の修正も通常DBへ反映済みのため直近優先から外した
  - 将来、複数DBやpolicy世代分岐を扱う場合だけ再検討する

より広い制約と確認済み範囲は[status.md](status.md)を参照してください。
