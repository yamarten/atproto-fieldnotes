# Background Job Model

長時間処理を一つのrequestとして受け取り、進捗eventを返し、最後に完了・中断・失敗のresultを返す契約です。
CLIとWeb workerは同じapplication serviceを使い、project選択・保存・再開の意味を揃えます。

停止は処理境界で協調的に行い、完了したDB更新は保持します。enrichはcheckpointを使って未完了projectから
再開できますが、sync/reviewのcheckpoint再開は未接続です。Webのjob一覧・eventはprocess内の表示用情報であり、
再開の正本はcheckpointです。

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

### ETA

bulk enrichは現在のworker processで時間を観測した処理件数と残件数からETAを計算し、resume前の完了件数を混ぜません。
その他のjobは直近の`current / total`を開始時からの平均速度へ線形外挿します。総数がないoperationでは表示しません。
`sync-introduced`は未知の総page数を`null`とし、安全上限を進捗総数にしません。

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

anonymous browser verificationはproject境界で停止します。syncは進行中HTTP requestを強制中断せず、introducedはpage、GitHub/Sembleはqueryまたはitem、Tangledはevent、collection inventoryはfetch/import境界でcancelを確認します。reviewは次candidateの前で停止します。境界までに完了したDB更新は取り消しません。sync/review自体のcheckpoint再開は未接続です。

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

## Scheduled preset

Operationsのscheduled presetは、通常enrich、全tracking stateのenrich後のstalled review一括反映、
introduced sync、introduced以外のsync後のnew-only enrichを実行します。
presetごとに毎週または第N週の曜日、時刻、time zoneを設定します。
Webからexecuteしたsyncは、sourceごとの件数、候補key / URL、成否、所要時間のbounded observationを直近4回分保存します。

Lexicon publication週次presetの固定sidecar、source、budget、比較可能period、再評価gateは
[sidecar catalog仕様](lexicon-publication-catalog.md#週次観測preset)を正本とします。
job eventは汎用runner、観測集計と評価stateはsidecarがそれぞれ保持します。

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

syncのdry-runも外部APIへ接続しますが、SQLiteのprojectと`sync_state`は更新しません。reviewのdry-runは保存済みprojectだけを読み、networkへ接続しません。reviewのexecuteはそのrunで列挙した候補を一括適用するため、Web UIは開始前に明示確認を要求します。

job一覧は永続化していません。server再起動後にprocess job一覧とeventは復元しません。
未完了のenrich checkpointは固定rootから再発見し、OperationsのDiscovered Checkpointsから再開できます。

## Web serverの診断・再起動

Settingsのreloadは同じ起動引数を保ってpackage entrypointから再起動します。
active jobへ協調停止を要求し、timeout後はworkerを終了してbrowserを再接続します。
macOS LaunchAgentはworkspaceのdirenv環境を継承し、plistへsecretを保存しません。SIGTERM時はactive workerを終了します。

HTTP request logはquery/bodyを除くmethod、path、status、所要時間をstderrへ記録します。
unexpected HTTP exceptionのtracebackはserver側へ残し、job stderrとは分離します。
scheduled worker起動失敗はqueueを終端し、presetの最終status/messageへ保存します。
POST JSON bodyはmutation lock外でparseし、mutation処理だけを直列化します。
API tokenはconstant-timeで比較し、HTMLは外部文字列を共通escape/link/JSON helperへ通します。
