# Thread Handoff

この文書は次の作業スレッドに必要な差分だけを置きます。実装済み機能の一覧は
[status.md](status.md)、恒久仕様は各仕様書、将来候補は[roadmap.md](roadmap.md)を
参照してください。完了項目や過去の検証ログはここへ累積しません。

## 現在地

- 日常運用安定化の8項目は実装済みです。
- 通常DBは2026-06-18に現行`v1-quarantine-1` policyでschema v2へoffline移行済みです。
  - migration report: `data/migrate-log.json`
  - v1 backup: `data/survey.db.v1-backup-20260618-151007`
  - migration時のproject 2575件、identifier 16554件、decision 116件、relation 0件
  - integrity、hard acceptance condition、normalized roundtripは合格
- 2026-06-21に通常DBをread-onlyで再監査済みです。
  - integrity `ok`、foreign key違反0件
  - v1由来identifier 14950件は全件`v1-quarantine-1`
  - 根拠不明legacy confirmed、active block衝突、policy欠落はいずれも0
- Web server reloadのpackage entrypoint修正まで`master`へ反映済みです。

## 次に必要な作業

直近のDB課題はtrust/data policy markerだけです。これは再移行ではありません。

- 新規v1 migrationはacceptance check完了後にDB-level markerを書く
- markerなし既存v2はread-only audit合格時だけmarkerをadditiveに後付けする
- 現行通常DBは後付け条件を確認済みで、project/identifier/investigationを書き換えない
- marker実装前でも通常運用データの意味はrow-level`v1-quarantine-1` metadataで確認できる

この作業を理由に専用worktreeを維持する必要はありません。次スレッドはmain worktreeから開始して構いません。DBへ直接影響するcommandは従来どおりWeb/enrich/CLI writerを停止し、backupまたは複製を用意して実行してください。

## 未検証・未接続

未検証:

- Web workerから実外部sync sourceへのend-to-end接続

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
- CLIなど別processを含むwriter排他

より広い制約と確認済み範囲は[status.md](status.md)を参照してください。
