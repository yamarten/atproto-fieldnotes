# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- identifierの採用理由、旧identifier、non-production、確定した時間関係、noiseを分離するschema version 3と通常mutationを実装し、review済み108 claimを適用した正本DBへ2026-08-14に切り替えました。High Five / ATProtoFansとMalfestio / Writerには相互のproject分離判断を保存し、Website、service URL、補助pageのruntime責務も分離済みです。結果は[Identifier保存方式のshadow比較](notes/identifier-storage-shadow-comparison.md)にあります。
- schema version 3切替後の典型操作、定期sources sync、enrichは問題なく完了しました。信頼・マージshadow比較もschema version 3の採用履歴を入力して再実行し、結果を[信頼ポリシーshadow比較](notes/trust-policy-shadow-comparison.md)と[マージ規則shadow比較](notes/project-merge-policy-shadow-comparison.md)へ反映しました。productionの自動採用・自動マージは未接続です。
- service/repository linkをcollector観測、repoのproject帰属、repo→URL直接参照、target roleへ分けるread-only比較を実装し、正本DBの結果を[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)へ記録しました。review後、GitHub homepageをconfirmed `repository_homepage`として保存し、service候補への静的投影と信頼edgeを分離するproduction codeを実装しました。2026-08-21に正本DBの旧424 rowを420 claimへ移行し、旧homepage edge 785本を除去してexact相互参照80本だけを再構成しました。通常経路の一時移行処理は削除済みです。
- repositoryのidentifier role、relation predicate、runtime view、Web/API表示を`project_repository` / repositoryへ揃え、2026-08-25に正本DBへ適用しました。case違いの同一repository 9組10行を統合し、confirmed 1,236件、candidate 225件になりました。意味が混在していた旧`source_repository` relation 9,219行は一括改名せず除去し、現在値に残る旧decision metadata、profile link、contribution evidence cacheも正規化済みです。履歴上のLLM入出力snapshotは観測当時の記録として書き換えていません。
- promotion queueのservice確認度、手動優先度、固定fixture評価と正本DB sample reviewまで完了しました。
- Qwen / Gemmaの通常処理model評価は完了しました。Gemmaはproject-level triageで一部優位でしたが、現行repository analysis policyの固定`goat` material 10回でJSON成功5・失敗5となったため通常処理候補から外し、既定modelと定期jobはQwenを維持します。正本DBでのGemma試用は実施していません。
- category契約、元profileなし／あり、prompt調整用／調整後確認用case、反復、文章、重い入力、memory、複製DB、repository解析と後段triageを分ける評価方法を実装・文書化しました。
- masterのrepository取得・material化・LLM解析を取り込み、根拠位置の検査、bounded excerpt、最大出力token数、本文を残さないJSON診断を追加しました。
- identifier roleはNSIDの`owned_nsid`とclassificationを中心に整理し、正本DBの移行とcase補正を適用済みです。一度きりのCLI移行・case補正コードは削除し、現行checkoutで全560 testを通しています。
- Qwenも固定`goat`の全件で根拠不足の観測が除外され、6/10件にmaterial外collectionがありました。既定model維持とは別に、repository解析内容は改善対象です。
- 現在の確認済み範囲は[status.md](status.md)、再検討条件を含む将来作業は[roadmap.md](roadmap.md)を参照してください。

## 次に必要な作業

この節には未完了で、次スレッドが実際に判断・実行する作業だけを置きます。

- Qwen repository解析で除外された観測とmaterial外collectionを一般化して減らせるか、promptまたはmaterial契約を見直す。特定repository名や値に限定した規則は追加しない。
- GitHub Searchの`homepage_exact`、`site_url` / `project_site`、`project_repository`のproduction保存契約と正本DB移行は完了しています。旧page→repo 499 claimのbounded再収集もread-onlyで完了し、exact confirmed component起点だけに絞るとcanonical confirmed relation 46、既存candidate支持5、未登録repository候補15となりました。次はこのcomponent境界と15候補をreviewし、合意後にだけrelation・candidate identifierのmaterializerを作ります。review 333、現在未観測104、取得失敗7は初期適用へ含めず、LLM自動昇格も接続しません。

このlocal Web UIへのreview linkは`http://127.0.0.1:8888/?project=...`を使います。
- マージ推奨110件を理由別に表示し、承認・保留・`separate_project`を残すreview動線の実装量と運用上の利益を比較する。自動マージは前提にしない。
- `sync-introduced`のpage安全上限を総作業量のように`1 / 100`表示する進捗契約を見直す。処理自体とwatermark更新は正常に完了している。

## DB 操作メモ

次にDBへ直接影響する操作を行う前に、別のWeb/enrich/CLI writerが走っていないことを確認し、必要ならその時点で新しいbackupまたは複製を用意してください。

history JSON圧縮は2026-07-26に正本DBへ適用済みです。VACUUMは行っておらず、解放pageは今後の書き込みで再利用します。file sizeを直ちに縮める必要が生じた場合だけ、別processを止めてVACUUMを判断してください。

schema version 3切替前のrollback用backupは`data/survey.schema-v2-before-v3-20260814.db`です。version 3の通常運用を確認して不要になった時点で削除してください。

GitHub homepage relation移行直前のbackupは`data/survey.before-repository-homepage-20260821.db`です。新predicate対応codeはmasterへ取り込み済みで、Web serverも再起動済みです。

`project_repository`移行直前のbackupは`data/backups/project-repository-20260825T022442+0900/survey.db`です。SHA-256は`ad56c6013128f4ba974798882285da507abaa2e0f1bb7c71acc1f4f213f6a2e0`です。

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
