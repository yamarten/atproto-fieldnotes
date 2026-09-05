# Background Job Model

この文書は、長時間operator workflowをCLI、Web worker、テストから同じ業務処理として実行するための恒久的な契約です。個別collectorのevidence契約は[investigation-model.md](investigation-model.md)を参照してください。

## 目的

- `argparse`、標準出力、HTTP requestから長時間処理本体を分離する
- CLIとWebでproject選択、保存、checkpoint、再開の意味を一致させる
- 進捗、部分完了、失敗、中断を機械可読なeventとして扱う
- API keyなどの認証情報をcheckpointやeventへ保存しない

非目標:

- collector request中の強制停止
- 一般のjob event履歴をSQLiteへ永続化すること。Lexicon publication観測のboundedな業務集計はsidecarを正本にする
- 複数の書き込みjobを同時実行すること
- 任意commandや任意外部URLをWebから実行すること

Web server起動時のjob eventはprocess内だけに保持します。一方、bulk enrich checkpointはDBと同じdirectoryの`enrich-runs/`から再発見し、未完了runをserver再起動後にresumeできます。APIはrun IDだけを受け取り、任意checkpoint pathは受け付けません。

停止境界はoperationごとに異なります。enrichとanonymous browser verificationはproject境界、reviewはcandidate境界です。syncは進行中HTTP requestを強制中断せず、introducedはpage、GitHub/Sembleはqueryまたはitem、Tangledはevent、collection inventoryはfetch/import境界でcancelを確認します。

review executeは先にdry-run planを作り、candidate IDを選択してから実行します。execute workerはplan fingerprintとcandidate IDを再計算し、候補が変化していればDB更新前に拒否します。

## 共通契約

`project_registry_core.jobs`のschema version 1を使用します。

### `OperationRequest`

1回のoperator workflow入力です。

- `operation_id`: runとcheckpointを識別するID
- `operation`: `enrich`などの処理種別
- `parameters`: adapterに依存しない入力値
- `execute`: DB更新を伴う要求か
- `requested_at`

`to_dict()`は既定で`*_api_key`、`token`、`password`を値ではなく設定有無へ置換します。

### `OperationEvent`

処理中に順序付きで通知するeventです。

- `started`
- `progress`
- `item_completed`
- `cancelled`
- `failed`
- `completed`

`sequence`は1 operation内で単調増加します。eventは通知であり、project current stateやcheckpointの正本ではありません。

bulk enrichの`progress.current`はcheckpoint全体での処理対象ordinalで、処理開始時に通知します。ETAにはこの通算値を使わず、現在のworker processで時間を観測済みの件数`eta_observed_count`と、そのattemptで未処理の件数`eta_remaining_count`を使います。resume前に完了した件数とresume後の経過時間を混ぜません。

`progress.total`は処理開始時点で総数が分かる場合だけ整数を入れ、未知の場合は`null`にします。
`sync-introduced`は取得済みpage数を`current`、`unit=page`として通知しますが、`max_pages`はpaginationの
安全上限であって総page数ではないため`total`へ入れません。Web UIはこの場合、比率やETAではなく取得済みpage数だけを表示します。

### `OperationResult`

処理全体の終了状態です。

- `completed`
- `cancelled`
- `failed`

現行enrich serviceは正常終了、協調的中断、失敗のいずれもstructured resultを返します。bulkではproject処理中の例外を`failed` eventとcheckpointへ記録して後続を処理しますが、run全体のresultも`failed`です。単体enrichと`--fail-fast`指定時は後続へ進みません。syncはsource内のquery/page/item境界、reviewはcandidate境界で`progress` eventを返します。

## 中断

`CancellationToken`は協調的な停止要求です。

- project開始前に要求済みなら次projectを開始しない
- project処理中に要求された場合は、そのprojectの保存とcheckpoint更新後に停止する
- collectorや外部requestを途中で破棄しない。停止までの上限は各sourceのtimeoutに依存する
- 完了済みprojectは再開時に再実行しない

syncは進行中のHTTP requestを中断せず次のquery/page/item境界で、reviewは次candidateの前で停止します。境界までに完了したDB更新は取り消しません。sync/review自体のcheckpoint再開は未接続です。

CLIの最初のCtrl+Cはこの停止要求へ変換します。checkpointを保存した後、終了コード`130`で終了します。

bulk enrichは1 projectの例外時にその接続の未commit変更をrollbackし、checkpointの`failed_projects`へ
project key、例外種別/message、時刻を保存して後続projectを既定で続行します。後続が完了してもrun全体は
`failed`で、CLIは終了コード`1`、Web jobもfailedとして表示します。結果には失敗一覧とresume commandを含めます。
resumeは`completed_project_keys`だけを除外するため、失敗projectと未処理projectを再試行します。
障害診断で最初の失敗位置を保ちたい場合はCLIの`--fail-fast`を使います。

project処理中に対象keyがmergeされてaliasになった場合もproject失敗として扱います。保存境界の
`StaleProjectWriteError`は古いsource snapshotをalias keyまたはcanonical targetへ保存せず、同じtransactionで
作成した未commitのinvestigation runとともにrollbackします。bulk checkpointには元のsource keyと例外を残し、
resume時にalias解決後のcanonical projectを現在stateから再実行します。これはmerge済みkeyの復活防止であり、
CLI等を含むwriter全体のprocess間排他を提供するものではありません。

## Enrich Checkpoint

`enrich --all`は既定で`data/enrich-runs/<run_id>.json`を使います。

- 開始時に`selected_project_keys`を固定する
- project保存後に`completed_project_keys`を更新する
- failure、missing project、current projectを記録する
- 再開時は保存済みoptionを使う
- LLM API keyの値は保存せず、再開するprocessから再度受け取る

checkpointは再開の正本です。event履歴やWeb表示状態の正本ではありません。

## 現在の接続状況

実装済み:

- `project_registry_core.jobs`のrequest/event/result/cancellation契約
- `project_registry_core.application.enrichment`のproject選択、実行、保存、checkpoint、再開
- `project_registry_core.commands.enrichment`のCLI adapter
- `project-registry enrich`の既存command・主要option
- `project_registry_core.worker`のJSON Lines worker protocol
- `project_registry_web.jobs`のprocess内job registry
- Web APIのenrich開始、一覧、詳細、停止、再開
- Web APIのsync/review開始、dry-run/execute指定、一覧、詳細、停止要求
- Operations画面のenrich、sync、review設定と直近event/result表示
- Operations画面のscheduled preset設定、手動発火、定期発火
  - 通常`enrich --all`、全tracking state対象`enrich --all`後の件数上限なしstalled review一括反映、`sync introduced`、introduced以外のsync一括後`enrich --all --new-only`
  - presetごとに毎週または第N週の曜日、時刻、time zoneを設定する
  - Webからexecuteしたsyncは、sourceごとの件数、候補key / URL、成否、所要時間のbounded observationを直近4回分保存する
  - 固定sidecarを更新するdisabled既定の週次`lexicon_publication_weekly`
    - registry DBと同じdirectoryからsidecarを導出し、任意path / URL / commandは受け付けない
    - 1 runで固定sealed tipの1 bounded segmentだけを処理し、未完了時は同じtipを次回再開する
    - 日曜05:00 Asia/Tokyoを既定とし、固定schedule stateで土曜23:00の`sync_sources_then_enrich`成功後48時間以内かを判定する
    - registryは`mode=ro` + `query_only`、sidecarの観測履歴は直近16回、評価baseline、異なる比較可能週、category集計と再評価gateを別stateで保持する
- 同じWeb serverから同時に実行するbackground jobを1件へ制限
- `project_registry_core.application.review`の非対話classification service

動作確認済み:

- temp SQLite上の複数project実行
- project境界での中断とcheckpoint再開
- checkpointとrequest serializationにAPI key値を残さないこと
- CLIがevent dataをJSON linesとして出力すること
- fake workerによるsubprocess完了、競合拒否、SIGINT停止、再開
- 実workerと空のtemp schema v3 DBによるnetwork-free protocol実行
- 実workerと空のtemp schema v3 DBによるreview dry-run
- temp DB Web UIから別process enrichを起動し、完了event/resultを表示すること
- Web APIのsync/review allowlist、dry-run/execute request変換
- in-app Browserからschema v3 DBのreview dry-runを起動し、candidate limitと完了resultを確認したこと

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job一覧復元。enrich checkpoint発見は接続済み
- job event履歴の永続化

未検証:

- Web workerから実際の外部sync sourceへ接続するend-to-end実行

将来の設計目標:

- CLIなど別processとread-only jobの競合方針を定義する
- Web以外のprocessを含む排他とsource別実行budgetを統一する

## Web Runner

Web serverはjobごとに`python -m project_registry_core.worker`を起動します。workerはstdinでDB pathと`OperationRequest`を受け、stdoutをprotocol専用JSON Linesとして使用します。domain codeがstdoutへ出した内容はstderrへ隔離します。

- job一覧はserver process内に保持する
- 完了jobは直近100件、eventはjobごとに直近200件、stderrは直近50行を保持する
- Web server終了・reload時はactive workerへ協調停止を要求し、timeout後にprocessを終了する
- custom DBのcheckpointはそのDBと同じdirectoryの`enrich-runs/`へ保存する
- scheduled preset設定とpending queueはDBと同じdirectoryの`job-schedules.json`へ保存する。予定時刻から24時間以内だけdueとし、それより古い未実行分は後追い発火しない。server再起動時に実行中だったscheduled stepは同じstepから再試行する
- Web APIはLLM API keyを受け取らず、workerはserver processの環境変数を継承する
- syncは設定済みsourceだけを選択でき、任意source URLや任意queryはWeb APIへ公開しない
- anonymous browser verificationはproject keyと、そのprojectに保存済みのconfirmed/candidate service URLだけを受け付ける
- 任意command、任意worker module、任意checkpoint pathはWeb APIへ公開しない
- Lexicon publication観測はJetstream 20,000 event / 300秒、inventory 60秒、publisher 12件を固定上限とする。
  SIGINTは進行中requestを強制終了せず、snapshot、inventory、publisherの境界で反映する
  通常sync state欠落/failed/未来時刻/48時間超、inventory failure、未完了segmentはvalid週へ加算しない。同一Asia/Tokyo週の
  manual runや再試行は一度だけ加算する
  publisher 12件boundの外側と期限切れ成功はverification pendingであり、実際に試行した最新failureとは別categoryにする

syncのdry-runも外部APIへ接続しますが、SQLiteのprojectと`sync_state`は更新しません。reviewのdry-runは保存済みprojectだけを読み、networkへ接続しません。reviewのexecuteはそのrunで列挙した候補を一括適用するため、Web UIは開始前に明示確認を要求します。

job一覧は永続化していません。server再起動後にprocess job一覧とeventは復元しません。
未完了のenrich checkpointは固定rootから再発見し、OperationsのDiscovered Checkpointsから再開できます。
