# Background Job Model

長時間処理を一つのrequestとして受け取り、進捗eventを返し、最後に完了・再試行待ち・中断・失敗のresultを返す契約です。CLIとWeb workerは同じapplication serviceを使い、project選択・保存・再開の意味を揃えます。

この文書のoperationは一つの処理要求、Web jobはその要求をworkerで実行・表示する単位です。一括enrichは複数projectを処理し、checkpointで同じ一括実行を再開できます。project一件ごとの調査履歴であるinvestigation runは[保存仕様](project-record.md#investigation-history)で扱います。以下のreview処理は、`review-stalled`、`review-out-of-scope`、`review-ignore`による追跡状態の見直しを指します。

停止は処理境界で協調的に行い、完了したDB更新は保持します。enrichはcheckpointを使って未完了projectから再開できますが、sync/reviewのcheckpoint再開は未接続です。Webのjob一覧・eventはprocess内の表示用情報であり、再開の正本はcheckpointです。

## 目的

- `argparse`、標準出力、HTTP requestから長時間処理本体を分離する
- CLIとWebでproject選択、保存、checkpoint、再開の意味を一致させる
- 進捗、部分完了、失敗、中断を機械可読なeventとして扱う
- API keyなどの認証情報をcheckpointやeventへ保存しない

非目標:

- collector request中の強制停止
- 一般のjob event履歴をSQLiteへ永続化すること。Lexicon publication観測の件数・成否などの集計は、専用のsidecar DBを正本にする
- 複数の書き込みjobを同時実行すること
- 任意commandや任意外部URLをWebから実行すること

review処理で追跡状態を更新する場合は、先にdry-run planを作り、candidate IDを選択してから実行します。execute workerはplanの変更検出値（fingerprint）とcandidate IDを再計算し、候補が変化していればDB更新前に拒否します。

## 共通契約

`project_registry_core.jobs`のschema version 1を使用します。

### `OperationRequest`

利用者または定期presetから渡す1回分の処理要求です。

- `operation_id`: 処理要求を識別するID。一括enrichではcheckpointの`run_id`にも使う
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
- `item_skipped`
- `item_conflicted`
- `retry_wait`
- `cancelled`
- `failed`
- `completed`

`sequence`は1 operation内で単調増加します。eventは通知であり、project current stateやcheckpointの正本ではありません。

一括enrich（bulk enrich）の`progress.current`はcheckpoint全体での処理対象の通し番号で、各projectの処理開始時に通知します。完了までの推定残り時間（ETA）には、現在のworker processで時間を観測済みの件数`eta_observed_count`と、そのprocessで未処理の件数`eta_remaining_count`を使います。再開前に完了した件数と再開後の経過時間を混ぜません。

`progress.total`は処理開始時点で総数が分かる場合だけ整数を入れ、未知の場合は`null`にします。`sync-introduced`は取得済みpage数を`current`、`unit=page`として通知しますが、`max_pages`はpaginationの安全上限であって総page数ではないため`total`へ入れません。Web UIはこの場合、比率やETAではなく取得済みpage数だけを表示します。

### ETA

bulk enrichは現在のworker processで時間を観測した処理件数と残件数からETAを計算し、resume前の完了件数を混ぜません。その他のjobは直近の`current / total`を開始時からの平均速度へ線形外挿します。総数がないoperationでは表示しません。`sync-introduced`は未知の総page数を`null`とし、安全上限を進捗総数にしません。

### `OperationResult`

処理全体の終了状態は`completed`、`retry_wait`、`cancelled`、`failed`です。想定内のproject競合だけが残る場合は`retry_wait`を返し、一般障害の`failed`と区別します。取消要求があれば`cancelled`、一般障害と競合が混在すれば`failed`を優先します。競合はerrorによる全件失敗として扱わず、`conflicted_projects`へkey・理由・未反映run IDを含めます。SQLiteのbusy timeoutや保存障害は一般障害です。

bulk enrichは一般障害を記録して既定で後続projectを処理し、最終結果を`failed`にします。`--fail-fast`は一般障害の後続処理を停止しますが、想定内の競合は一件を再試行待ちとして続行します。追跡状態レビューも判定snapshotを保存時に検査し、競合した候補を残して独立した後続候補を続行します。syncとservice確認のworkerも保存競合を`retry_wait`として返します。syncの競合では処理中sourceを終了し、未commit変更をrollbackします。既に保存したprojectを巻き戻さず、競合を超えてwatermarkを進めません。

## 中断

`CancellationToken`は協調的な停止要求です。

- project開始前に要求済みなら次projectを開始しない
- project処理中に要求された場合は、そのprojectの保存とcheckpoint更新後に停止する
- collectorや外部requestを途中で破棄しない。停止までの上限は各sourceのtimeoutに依存する
- 完了済みprojectは再開時に再実行しない

一括enrichの先行取得は一件までです。停止要求後に新たな先行取得を開始せず、既に動いている初期収集は回収します。現在projectだけを通常どおり保存・完了とし、未処理の先行成果は初期収集だけの未反映履歴へ残して停止します。checkpointに先行projectを完了として追加せず、再開時に最新状態から調査します。先行収集の詳細と停止までの待ちは[investigation](investigation.md#一括調査の先行取得)を参照してください。

anonymous browser verificationはproject境界で停止します。syncは進行中HTTP requestを強制中断せず、introducedはpage、GitHub/Sembleはqueryまたはitem、Tangledはevent、collection inventoryはfetch/import境界でcancelを確認します。reviewは次candidateの前で停止します。境界までに完了したDB更新は取り消しません。sync/review自体のcheckpoint再開は未接続です。

CLIの最初のCtrl+Cはこの停止要求へ変換します。checkpointを保存した後、終了コード`130`で終了します。

bulk enrichは1 projectの例外時にその接続の未commit変更をrollbackし、checkpointの`failed_projects`へproject key、例外種別/message、時刻を保存して後続projectを既定で続行します。後続が完了しても一括実行全体は`failed`で、CLIは終了コード`1`、Web jobもfailedとして表示します。結果には失敗一覧とresume commandを含めます。resumeは`completed_project_keys`だけを除外するため、失敗projectと未処理projectを再試行します。障害診断で最初の失敗位置を保ちたい場合はCLIの`--fail-fast`を使います。

project処理中に対象keyがmergeされてaliasになった場合や更新番号が変わった場合は、古いsnapshotの保存を拒否します。現在状態と反映済み履歴のtransactionをrollbackした後、調査成果を`unapplied`として別transactionへ保存します。保存契約・保持期間は[project record](project-record.md#investigation-history)を参照してください。

## Enrich Checkpoint

`enrich --all`は既定で`data/enrich-runs/<run_id>.json`を使います。開始時の`selected_project_keys`は対象の順序を固定しますが、処理snapshotは固定しません。各件開始時に最新のcanonical projectを読み直し、tracking state、`new_only`、`retry_failures`の条件を再確認します。条件から外れた対象や削除された対象は理由付きの`skipped_projects`へ記録し、調査成功件数には含めません。同じcanonical projectの処理済み対象を重複実行しません。

- project保存後に`completed_project_keys`を更新する
- 想定内の競合を`conflicted_projects`へ記録し、完了扱いにしない。過去の未解決attemptのrun IDも保持する
- 一般障害を`failed_projects`、存在しないkeyを`missing_project_keys`、処理中keyを`current_project_key`へ記録する
- 再開時は保存済みoptionを使い、成功・対象外として処理済みのkeyを除外する
- 再開は最新状態から外部取得・LLMを再実行する。保存済みの古い成果の差分再適用はしない
- LLM API keyの値は保存せず、再開processから受け取る

競合だけが残った一括実行は`retry_wait`で終了します。CLIは終了コード`2`とresume commandを返し、Webは再試行待ちとResumeを表示します。単体enrich・`--no-checkpoint`は競合一覧と未反映履歴を残しますがcheckpointによる再開はなく、同じ対象を明示的に再実行します。自動再試行や再試行時刻の予約はしません。

checkpointは再開の正本です。event履歴やWeb表示状態の正本ではありません。project commitとcheckpoint file更新は一つのtransactionではないため、その間でprocessを失った場合のexactly-once実行は保証しません。

先行取得を使う場合も各件開始時の最新読込みと選択条件の検査は維持します。先行snapshotのcanonical key・revisionが一致すればその収集結果を使い、異なれば未反映履歴へ退避して最新状態から再取得します。LLM処理・保存中の変更は従来の保存時revision検査で拒否します。先行収集の例外は現在projectのLLM失敗に転嫁せず、その先行projectの通常処理順で一般障害として扱います。

cancel・fail-fast・snapshot変更・対象外化等で使えなかった先行成果は、`prefetched_runs`へproject key・run ID・理由を記録します。これらは完了・失敗・競合project件数には加算しません。後続の再調査成功またはcheckpoint対象外処理で未反映履歴を解消します。途中停止した元processの計測値は`execution`に残り、再開後の値は新processの実績へ置換します。先行有無はcheckpointの`options.prefetch`へ保存し、再開時にも保持します。

## Scheduled preset

`service_observation`は`monitor_services` workerを通して、前回browser試行から30日以上経過したconfirmed service URLを最大20件確認します。対象・取消し・競合の扱いは[観測仕様](investigation.md#browser観測)を参照してください。初期設定は無効、毎週土曜03:00 Asia/Tokyoです。この値は有効化前の初期値であり、実データ量での妥当性を確認済みの運用頻度ではありません。既存scheduleを自動有効化・変更しません。

`Operations`のscheduled preset（定期実行する処理の組合せ）は、通常enrich、全tracking stateのenrich後の`review-stalled`一括反映、introduced sync、introduced以外のsync後の新規登録projectだけのenrichを実行します。presetごとに毎週または第N週の曜日、時刻、time zoneを設定します。Webからexecuteしたsyncは、sourceごとの件数、候補key / URL、成否、所要時間を、保存量を制限して直近4回分保存します。

想定内の競合では、独立した後続stepを続行します。`sync_sources_then_enrich`の各sourceは独立し、最後の`new_only` enrichは保存済みの未調査projectを処理します。`enrich_all_states`の`review-stalled`は先行enrichの完了を必須とし、未完了なら`deferred`として保存します。一般障害（`failed`）、明示中断（`cancelled`）、worker起動失敗では、そのpresetの後続stepを停止します。workerの異常終了もjobが`failed`になれば同じ扱いです。

未完了stepと依存待ちstepは`job-schedules.json`の`pending_steps`へ保持し、preset全体を`retry_wait`にして他presetを妨げません。停止時は失敗・中断・起動失敗した現在stepを先頭に、先に競合・依存待ちで保留したstep、残りの未実行stepの順で保存します。job自体の`failed` / `cancelled`は変更せず、presetの`last_steps`にも結果を残し、`last_message`へ停止理由を保存します。定期実行は次の週・月も含め未解決の同presetを自動再試行せず、利用者が`Resume pending steps`で再開します。手動実行は保留があればそのstepだけ、なければ`Run now`としてpreset全体を開始します。

enrichは結果または元requestで特定できる実在するcheckpointのpathとrun IDを保持して再開します。checkpoint作成前に失敗した初回stepは、同じ選択条件で新規実行します。起動失敗時はstepに既にある再開指定を保持します。sync/reviewとcheckpointを持たない観測stepは、そのstepを最新状態から再実行します。成功済みstepは繰り返しません。ただしsync再試行が新しいprojectを作り得るため、その後は`new_only` enrichを行います。元checkpointの再開もある場合は、その対象を処理した後に未調査projectを新たに選択します。再開後に再び停止した場合も、その時点の未完了stepだけへ保留を更新します。保留step・結果・停止理由はserver再起動後も維持します。

Lexicon publication週次presetの固定sidecar、source、budget、比較可能period、再評価gateは[sidecar catalog仕様](lexicon-publication-catalog.md#週次観測preset)を正本とします。job eventは汎用runner、観測集計と評価stateはsidecarがそれぞれ保持します。

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
- 同じWeb serverからのbackground jobは同時に1件へ制限する。CLIなど別processのwriter排他は提供しない
- Lexicon publication観測のSIGINTは進行中requestを強制終了せず、snapshot、inventory、publisherの境界で反映する。

syncのdry-runも外部APIへ接続しますが、SQLiteのprojectと`sync_state`は更新しません。reviewのdry-runは保存済みprojectだけを読み、networkへ接続しません。reviewのexecuteはplanから選択した候補を一括適用するため、Web UIは開始前に明示確認を要求します。

job一覧は永続化していません。server再起動後にprocess job一覧とeventは復元しません。未完了のenrich checkpointは固定rootから再発見し、OperationsのDiscovered Checkpointsから再開できます。

## Web serverの診断・再起動

Settingsのreloadは同じ起動引数を保ってpackage entrypointから再起動します。active jobへ協調停止を要求し、timeout後はworkerを終了してbrowserを再接続します。macOS LaunchAgentはworkspaceのdirenv環境を継承し、plistへsecretを保存しません。SIGTERM時はactive workerを終了します。

HTTP request logはquery/bodyを除くmethod、path、status、所要時間をstderrへ記録します。unexpected HTTP exceptionのtracebackはserver側へ残し、job stderrとは分離します。scheduled worker起動失敗は実行queueから外し、未完了stepをpresetの手動再開待ちとして保存します。POST JSON bodyはmutation lock外でparseし、mutation処理だけを直列化します。API tokenはconstant-timeで比較し、HTMLは外部文字列を共通escape/link/JSON helperへ通します。
