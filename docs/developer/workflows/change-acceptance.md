# 変更の受入条件と確認手順

この文書は、実装・文書変更を完了扱いにする前の確認条件、標準環境、実施手順を定めます。

## 標準環境

| 用途 | DB | 待受アドレス（bind）/ port | 扱い |
| --- | --- | --- | --- |
| 自動test | testごとのtemp DB | testが割り当てるloopback port | 毎回新規。外部API、正本DB、実LLMへ接続しない |
| CLI/Webの手動確認 | `data/dev/survey.db` | Webは`127.0.0.1:8765` | 架空定義から生成。各worktreeで保持・変更・リセットしてよい |
| 実データのread-only確認 | `data/survey.db` | Web serverは起動しない | CLI、SQL、書き捨てスクリプトから直接読む |
| 書込みを伴う実データ依存の確認 | `.tmp/`内の正本DB複製 | 必要なら`127.0.0.1:8765` | 必要な場合だけ作り、task終了時に削除 |
| 実運用Web | `data/survey.db` | `0.0.0.0:8888` | LaunchAgent管理。変更確認のために再起動・操作しない |

`8765`は手動起動と実ブラウザ確認用、`8888`は実運用専用です。確認用serverは`0.0.0.0`へbindしません。`8765`が使用中なら別portへ逃がさず、既存の確認用serverか残存processを確認します。

## 変更種別ごとの確認

すべてのcode変更では、対象testに加えてcommit前に`bin/project-registry-test`を実行します。文書だけの変更では、link、記載したcommand、現行実装との整合性を確認し、code testを省略した場合はその旨を報告します。

追加条件は次のとおりです。

- Web UI: Python側の関連API test、`node --check scripts/project_registry_web/static/app.js`、変更した主要導線の実ブラウザ確認
- heuristic: unit testに加え、既知の実誤判定fixtureまたは人間によるsample review
- DB保存契約・cleanup: temp DBでの機能確認、正本DBに対するread-only audit、必要な場合だけ複製DBで行う書込み確認
- 外部情報源の取得契約: 通常testと分け、変更内容に応じて件数・page・request数を限定した疎通・動作確認（bounded smoke test）

実施できなかった確認は成功扱いにせず、未検証の範囲と理由を報告します。

## CLI/Webの共通ダミーDB

初回は次の入口で、架空データ入りの小さな開発用DBを作ります。実行した入口のworktree内に作り、カレントディレクトリから保存先を推測しません。

```bash
bin/project-registry dev-db
```

Gitで管理するのは[架空定義](../../../scripts/fixtures/dev-registry.json)と生成入口です。SQLite本体はgit管理外の`data/dev/survey.db`で、task間で保持して構いません。保存は既存のproject codecと調査履歴APIを使い、schemaをfixture側へ複製しません。未使用の`tests/helpers.py`の`seed_web_fixture`は共通入口にしません。自動unit testは引き続きtestごとのtemp DBを使い、この可変DBに依存しません。

| 登録プロジェクト（key末尾） | 代表的な確認導線 |
| --- | --- |
| Demo Field Notes（`0001`） | 紹介候補、採用済みサービスと判断、成功の調査履歴 |
| Demo Sketch Board（`0002`） | 要レビュー、未確定サービス候補、除外判断、候補relation、部分失敗の履歴と根拠 |
| Demo Garden Log（`0003`） | 紹介済みfilter、紹介候補からの除外 |
| Demo Archive Kit（`0004`） | `stalled`の絞り込み・追跡状態変更 |
| Demo Sprout（`0005`） | 未調査、詳細の不足情報・調査履歴なし |

keyは`proj:dddd00000001`〜`proj:dddd00000005`です。日時は固定の表示例であり、鮮度判定やqueueの全件数・順位を恒久的に固定するfixtureではありません。表示用の成功／失敗・利用可能性・採用判断はすべて作り物で、実在サービスの状態を示しません。紹介候補として表示されることと、紹介文が完成していることも区別します。

通常CLIも同じDBへ接続できます。共通`--db-path`はcommandの前へ置き、省略すると正本DBになります。

```bash
bin/project-registry --db-path data/dev/survey.db show --all
bin/project-registry --db-path data/dev/survey.db show -p proj:dddd00000002 --format json
bin/project-registry --db-path data/dev/survey.db promotion-queue
bin/project-registry --db-path data/dev/survey.db review-queue
bin/project-registry --db-path data/dev/survey.db set-tracking-state -p proj:dddd00000005 --tracking-state stalled
bin/project-registry --db-path data/dev/survey.db replay-investigation --run-id inv:dev-sketch-partial
```

queueが提案する次のCLIコマンドには接続中DBの絶対pathを含めます。`--db-path`はregistry接続だけを選び、設定・認証・外部アクセスを隔離するモードではありません。外部取得や実LLMを使わず生成でき、上記のread/edit/replayも外部取得を行いません。enrich・sync・service確認を実行したことにはならず、それらを確認する場合は別途project数・request数・時間等を限定します。一括enrichを別DBで確認する際は`--checkpoint-path`をそのDB用に明示するか`--no-checkpoint`を指定します。

### リセットと上書き拒否

既存DBはそのまま再利用します。初期状態へ戻す場合は、そのDBを使うCLI、Web server、workerをすべて停止してから実行します。

```bash
bin/project-registry dev-db --reset
```

保存先は固定で、`dev-db`へ任意pathや共通`--db-path`は渡せません。通常生成は既存fileを拒否し、リセットは生成元markerと現行schemaを持つDBだけを許可します。DB・親directoryのsymlink、DBやSQLite sidecarのhard link/symlinkも拒否します。正本DBや既存の空DBをこのpathへ移してもリセットできません。未生成なら`--reset`でも新規作成します。

リセットは同じDB file内の単一transactionで全tableのデータを再投入し、途中失敗はrollbackします。WALを解除してexclusive transactionを取り、SQLiteが使用中と判断する場合は待たずに拒否します。接続を閉じたまま待機中のserver／workerの検知は保証しないため、事前停止は必要です。リセット後の通常接続は既存仕様どおりWALを有効にします。DB外の`job-schedules.json`、checkpoint、browserのlocalStorageはリセットしません。

従来手動初期化した`data/survey.dev.db`は自動移行・削除しません。標準の確認入口をこのダミーDBへ切り替え、古いDBが不要なら用途と利用processを確認して別途片付けます。`data/dev/`は正本の定期設定やcheckpointがある`data/`と分けます。開発用の定期実行は有効にせず、確認後はserverを停止します。

## Web UIの実ブラウザ確認

確認用serverは実運用tokenや`direnv`を使わず、loopbackへ起動します。

```bash
bin/project-registry-web \
  --host 127.0.0.1 \
  --port 8765 \
  --db-path data/dev/survey.db \
  --token web-ui-dev
```

`http://127.0.0.1:8765/?token=web-ui-dev`を開き、変更した画面への移動、表示、主要操作を確認します。書込み操作は開発用DBだけに対して行います。画面幅や自動遷移を変えた場合は、対象となるdesktop / mobile幅と遷移前後も確認します。

unit testでしか再現できない内部状態はそのtestで確認し、実ブラウザでは利用者の操作経路を確認します。どこをunit test、どこを画面で確認したかを分けて報告します。確認後はserverを停止し、`data/dev/survey.db`は今後も使うなら残します。

## 正本DBの確認と複製

現在のproject、件数、保存状態、dry-run結果を読むだけなら、正本DBを直接使います。read-onlyと分かっているCLI、SQLiteの`mode=ro` URI、read-only接続を使う書き捨てスクリプトのためにGB単位の複製を作りません。調査用スクリプトは保存せず、必要な結果だけを報告します。

```bash
sqlite3 'file:data/survey.db?mode=ro' 'SELECT COUNT(*) FROM projects;'
```

通常のCLI/API、画面遷移、表示、入力には`data/dev/survey.db`を使います。保存済み調査履歴とbackground jobの実行状態は別物で、実workerの進捗・停止・再開の確認には別途制御したjobが必要です。実データ固有の誤判定・状態・件数・容量が必要な場合は、正本DBのread-only確認または必要な複製を使います。正本DBの複製は、次のいずれかが確認対象そのものである場合だけ作ります。

- migration、cleanup、merge、enrichなど、確認中にDBを書き換える処理
- 保存前後の差分や再実行時の冪等性
- 正本相当の件数やDB容量を使った書込み時間、lock、transaction
- read-onlyであることを確認できないcommandやスクリプト

複製前に、なぜ開発用DBや正本へのread-only確認では足りないかを短く示します。同じtask内では1つの複製を使い回し、確認工程ごとのコピーや、単なる退避目的のコピーを作りません。正本DBは単純なfile copyをせず、SQLiteのonline backupを使います。

```bash
mkdir -p .tmp/change-acceptance
sqlite3 data/survey.db ".backup '.tmp/change-acceptance/survey.db'"
```

複製をWebで使う場合も`127.0.0.1:8765`へ起動します。確認後は使用processを停止し、複製DBとWAL/SHMを削除します。再利用する小さな`data/dev/survey.db`と、容量の大きい正本DB複製を区別し、後者はtaskをまたいで残しません。

### sandboxでの読み取り計測

正本の読み取りが許可された計測は、まず通常権限で実行します。Pythonは既存の`bin/_project-registry-python`が選ぶ環境を使い、SQLite接続は`mode=ro`を維持します。書き捨て計測や旧コードとの比較では、不要なbytecode出力を`PYTHONDONTWRITEBYTECODE=1`で止め、計測結果と依存ライブラリの書込みcacheを作業中のworktreeの`.tmp/`へ置きます。読取対象のDB・設定・vendoredデータと、書込先を区別します。準備処理と追加権限が必要な計測を一つのshell commandへまとめません。

WAL形式では、`mode=ro`でも`-wal`・`-shm`が存在しない場合に補助ファイルの作成が必要になります。読取が失敗したら、エラーと補助ファイルの状態を確認し、必要な接続だけを一時的な昇格対象にします。一度の通常のSQLite接続で補助ファイルが揃えば、以後は書込権限なしで読める場合があります。次の計測では通常権限へ戻して確認し、補助ファイルが削除された場合などは、その時点の状態から再判断します。昇格後に成功したことだけで、以後も昇格が必要とは扱いません。

承認省略のために`data/`やWAL補助ファイルへ恒久的な書込権限を追加しません。WALは未反映のcommit済みデータを持つため、単なるcacheとして扱いません。稼働中の正本に`immutable=1`やロック無効化を指定したり、journal modeを変更したり、補助ファイルを手作業で作成・削除したりして回避しません。通常権限での再実行が失敗した場合は、その経路を未解決として報告します。

## 実運用への反映

変更の受入確認と実運用serverへの反映は別作業です。通常は利用者がWeb UIのSettingsにある`Reload server`を押します。明示的な反映依頼がない限り、変更を実装したagentは`project-registry-web-service restart`、reload API、正本DBへの変更を実行しません。

実運用serverのreloadは実行中workerを停止し、process内のjob一覧とeventを失います。明示的に反映する場合も、実行中jobがないことを確認し、影響を説明してから行います。

## Maintenance適用の確認

共有hostingのtenant境界を定める接尾辞（hosted suffix）の変更に伴う分割監査は`audit-source-config`、URL監査はread-onlyの`audit-url-fields`を使います。判定規則の変更を既存DBへ反映する場合は、補助pageやblock/defer等の採用抑止条件（guard）に該当するURLのservice採用、repo/NSIDの採用条件を満たさない`defines_schema`等を監査し、dry-runの全差分を確認します。`repair-db --execute`は特定補正だけを選ばず、同じsummaryに含まれる正規化・派生値更新も保存します。正本DBへの適用は承認とbackupを経て行い、保存・再監査の確認範囲を作業記録へ残します。
