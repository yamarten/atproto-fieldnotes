# Background Job Model

この文書は、長時間operator workflowをCLI、将来のWeb worker、テストから同じ業務処理として実行するための恒久的な契約です。個別collectorのevidence契約は[investigation-model.md](investigation-model.md)を参照してください。

## 目的

- `argparse`、標準出力、HTTP requestから長時間処理本体を分離する
- CLIとWebでproject選択、保存、checkpoint、再開の意味を一致させる
- 進捗、部分完了、失敗、中断を機械可読なeventとして扱う
- API keyなどの認証情報をcheckpointやeventへ保存しない

非目標:

- collector request中の強制停止
- job履歴をSQLiteへ永続化すること
- 複数の書き込みjobを同時実行すること
- Webから長時間処理を開始・監視すること

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

### `OperationResult`

処理全体の終了状態です。

- `completed`
- `cancelled`
- `failed`

現行enrich serviceは正常終了と協調的中断でresultを返します。project処理中の例外は`failed` eventを通知して再送出し、CLIの既存error handlingへ渡します。将来のworkerはこの例外をjob-level failureへ変換します。

## 中断

`CancellationToken`は協調的な停止要求です。

- project開始前に要求済みなら次projectを開始しない
- project処理中に要求された場合は、そのprojectの保存とcheckpoint更新後に停止する
- collectorや外部requestを途中で破棄しない。停止までの上限は各sourceのtimeoutに依存する
- 完了済みprojectは再開時に再実行しない

CLIの最初のCtrl+Cはこの停止要求へ変換します。checkpointを保存した後、終了コード`130`で終了します。

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

動作確認済み:

- temp SQLite上の複数project実行
- project境界での中断とcheckpoint再開
- checkpointとrequest serializationにAPI key値を残さないこと
- CLIがevent dataをJSON linesとして出力すること

未接続:

- subprocess workerとjob registry
- Web APIからの開始、一覧、停止、再開
- sync、review classification、`assist-db-edit`の共通job化
- server再起動後の実行中job復元
- job event履歴の永続化

将来の設計目標:

- Web serverとは別processでjobを実行する
- 書き込みjobは原則1件に制限し、read-only jobとの競合方針を定義する
- syncとreview系にも同じevent・cancellation契約を適用する
- Webはapplication serviceを直接再実装せず、同じ`OperationRequest`をworkerへ渡す
