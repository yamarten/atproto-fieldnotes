# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- promotion queueのservice確認度、手動優先度、固定fixture評価と正本DB sample reviewまで完了しました。
- LLM評価reportはreasoning要求とruntime確認、prompt調整に使ったcase、同一入力の複数回差分を区別し、2 reportを比較できる形へ更新しました。
- categoryを主な利用者と利用形態で定義し直し、`auth_only`をcategoryから外して`atproto_auth_only`へ分離しました。prompt、保存、評価検査と、元profileなし／ありを明示する評価入力まで実装し、全490 testを確認済みです。正本DBの旧`category=auth_only` 35件は内容が混在するため未移行です。
- 旧category契約でのQwen/Gemma一致数とgoat判定は次のmodel選定へ直接使いません。通常運用modelの変更は未決定です。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- 旧`category=auth_only` 35件の移行先をevidenceから個別に確認し、正本DB更新案を作る。正本DBへは承認を得るまで書かない。
- 新category契約で旧56 labelを確認し直し、すべてprompt調整用としてv3 fixtureへ移行済み。正本DBの保存済みevidence 3,635件から、調整後確認用の別caseを選ぶ。
- その後、Qwen、Gemma、Granite、gpt-oss MLX、Agents A1等を少数caseから順に比較する。Agents A1は常駐background用途のmemoryと処理量も確認する。
- GitHub README本文が通常enrichのevidenceへ保存されないため、repository descriptionが空のprojectでartifact roleを判断できない。model比較とは分けて収集契約の修正要否を決める。
- gpt-ossを再評価する場合は、最初に同じ短い入力をreasoningなし/`low`で実行し、LM Studio logで設定の適用または無視を確認する。過去のGGUF結果は`low`適用済み比較へ再利用しない。
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
