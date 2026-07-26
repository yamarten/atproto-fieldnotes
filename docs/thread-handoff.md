# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- promotion queueのservice確認度、手動優先度、固定fixture評価と正本DB sample reviewまで完了しました。
- LLM評価reportはreasoning要求とruntime確認、prompt調整に使ったcase、同一入力の複数回差分を区別し、2 reportを比較できる形へ更新しました。
- categoryを主な利用者と利用形態で定義し直し、`auth_only`をcategoryから外して`atproto_auth_only`へ分離しました。前回LLM由来のscopeを次のpromptへ戻すfeedback loopも除去し、全492 testを確認済みです。正本DBの旧`category=auth_only` 35件も個別移行済みです。
- masterのrepository取得・material化・LLM解析を取り込み、repository解析用の固定corpus export・実行・比較も追加しました。最初の比較で根拠位置の不正確な出力が両modelに多かったため、prompt内だけの根拠番号、summaryの根拠必須化、tree pathのLLM根拠からの除外、host integrationの意味検査まで行いました。解析reportを固定triage caseへ一時的に重ねる評価経路も追加しています。
- 修正後の調整用5 repositoryではGemmaが保存規則3/5、Qwenが0/5、後段triageは両model 5/5成功・根拠外identifier 0件でした。通常`enrich`にも最大出力token数を渡す入口を追加し、repository解析を含む全LLM requestへ伝播することを確認しました。
- 最新DB複製では、一般の`/docs/`文書をdeployment設定とする誤抽出と、同じexcerptの複製で後段24 KiB上限を超える問題を確認して修正しました。既存の実結果5件はtriage投影1,930〜3,536文字に収まります。現行policy v8の固定`goat` material 10回はGemmaがJSON成功5・失敗5、Qwenが10/10成功でしたが、保存規則をすべて通る結果は両方0件でした。Gemmaの失敗は内部のJSON構文違反です。Qwenも全件に根拠不足の観測、6/10件にmaterial外collectionがあり、repository解析内容は改善対象です。本文を残さない診断をrun metricsと固定評価reportへ追加し、全537 testを通しています。正本DBは変更していません。
- 旧category契約でのQwen/Gemma一致数とgoat判定は次のmodel選定へ直接使いません。通常運用modelの変更は未決定です。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- 未使用21件の元profileなし／ありは実測済み。元profileなしではQwen/Gemmaともcategory 17/21だが、全検査はQwen 17/21、Gemma 21/21、根拠外identifierはQwen 2件、Gemma 0件。
- 重い5件×2回とrepository analysis追加前の複製DB通常`enrich` 8件は完了。Gemmaは固定入力でQwenの1.30〜1.36倍、複製DBのLLM時間は最初のprojectを除いて1.29倍だが、暫定停止目安1.5倍以内。重い入力の全検査はGemma 9/10、Qwen 7/10、複製DBではgoodfeedsとleaflet-mdのcategoryをGemmaだけが期待どおり保存し、identifier、profile本文、promotion判定の退行はなかった。この時点のproject-level triageでは少数試用条件を満たしたが、現行pipeline全体では後述のrepository解析失敗により停止した。手動footprintはQwen 20 GB、Gemma 16 GB。Agents A1はidle時6/6成功、footprint 9.7 GBだったが、処理が遅く、自動切替でTTL中のQwenを退避させたため、現runtimeのままbackground試用へ進めない。
- Gemmaは現構成の通常処理候補から外した。model、runtime、またはpromptを変えて再検討する時だけ、同一`goat` materialでJSON構文エラーと根拠不十分な観測が再発しないことを先に確認する。
- Qwenは既定modelとして維持するが、固定`goat`で除外された観測とmaterial外collectionを一般化して減らせるかはrepository解析promptまたはmaterial契約の別課題。特定repository名や値に限定した規則は追加しない。
- その条件を通った場合だけ、別の未調整repositoryを加えて固定caseと最新DB複製を再実行し、既知のgoat、atproto.dart、AT Backup、marque.atと、固定corpusにないatplay.games、Postcards Social、publicdomainrelay.comの7件を正本試用候補として再利用する。Afrilingua AIは重複候補の判断後へ回す。
- 正本DBへのGemma試用は未承認・未実施。既定modelと定期jobはQwenのまま。
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
