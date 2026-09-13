# 変更の受入条件と確認手順

この文書は、実装・文書変更を完了扱いにする前の確認条件、標準環境、実施手順を定めます。

## 標準環境

| 用途 | DB | 待受アドレス（bind）/ port | 扱い |
| --- | --- | --- | --- |
| 自動test | testごとのtemp DB | testが割り当てるloopback port | 毎回新規。外部API、正本DB、実LLMへ接続しない |
| 実ブラウザ確認 | `data/survey.dev.db` | `127.0.0.1:8765` | task間で保持してよい開発用DB |
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

## Web UIの実ブラウザ確認

開発用DBがなければ一度だけ初期化します。

```bash
test -f data/survey.dev.db || bin/project-registry init-db --db-path data/survey.dev.db
```

このDBはgit管理外で、画面確認用の小さなデータをtask間で保持できます。既知の初期状態が必要な自動testでは使い回しません。

確認用serverは実運用tokenや`direnv`を使わず、loopbackへ起動します。

```bash
bin/project-registry-web \
  --host 127.0.0.1 \
  --port 8765 \
  --db-path data/survey.dev.db \
  --token web-ui-dev
```

`http://127.0.0.1:8765/?token=web-ui-dev`を開き、変更した画面への移動、表示、主要操作を確認します。書込み操作は開発用DBだけに対して行います。画面幅や自動遷移を変えた場合は、対象となるdesktop / mobile幅と遷移前後も確認します。

unit testでしか再現できない内部状態はそのtestで確認し、実ブラウザでは利用者の操作経路を確認します。どこをunit test、どこを画面で確認したかを分けて報告します。確認後はserverを停止し、`data/survey.dev.db`は今後も使うなら残します。

## 正本DBの確認と複製

現在のproject、件数、保存状態、dry-run結果を読むだけなら、正本DBを直接使います。read-onlyと分かっているCLI、SQLiteの`mode=ro` URI、read-only接続を使う書き捨てスクリプトのためにGB単位の複製を作りません。調査用スクリプトは保存せず、必要な結果だけを報告します。

```bash
sqlite3 'file:data/survey.db?mode=ro' 'SELECT COUNT(*) FROM projects;'
```

通常のAPI、画面遷移、表示、入力、background job UIの確認には`data/survey.dev.db`を使います。正本DBの複製は、次のいずれかが確認対象そのものである場合だけ作ります。

- migration、cleanup、merge、enrichなど、確認中にDBを書き換える処理
- 保存前後の差分や再実行時の冪等性
- 正本相当の件数やDB容量を使った書込み時間、lock、transaction
- read-onlyであることを確認できないcommandやスクリプト

複製前に、なぜ開発用DBや正本へのread-only確認では足りないかを短く示します。同じtask内では1つの複製を使い回し、確認工程ごとのコピーや、単なる退避目的のコピーを作りません。正本DBは単純なfile copyをせず、SQLiteのonline backupを使います。

```bash
mkdir -p .tmp/change-acceptance
sqlite3 data/survey.db ".backup '.tmp/change-acceptance/survey.db'"
```

複製をWebで使う場合も`127.0.0.1:8765`へ起動します。確認後は使用processを停止し、複製DBとWAL/SHMを削除します。再利用する小さな`data/survey.dev.db`と、容量の大きい正本DB複製を区別し、後者はtaskをまたいで残しません。

## 実運用への反映

変更の受入確認と実運用serverへの反映は別作業です。通常は利用者がWeb UIのSettingsにある`Reload server`を押します。明示的な反映依頼がない限り、変更を実装したagentは`project-registry-web-service restart`、reload API、正本DBへの変更を実行しません。

実運用serverのreloadは実行中workerを停止し、process内のjob一覧とeventを失います。明示的に反映する場合も、実行中jobがないことを確認し、影響を説明してから行います。

## Maintenance適用の確認

共有hostingのtenant境界を定める接尾辞（hosted suffix）の変更に伴う分割監査は`audit-source-config`、URL監査はread-onlyの`audit-url-fields`を使います。判定規則の変更を既存DBへ反映する場合は、補助pageやblock/defer等の採用抑止条件（guard）に該当するURLのservice採用、repo/NSIDの採用条件を満たさない`defines_schema`等を監査し、dry-runの全差分を確認します。`repair-db --execute`は特定補正だけを選ばず、同じsummaryに含まれる正規化・派生値更新も保存します。正本DBへの適用は承認とbackupを経て行い、保存・再監査の確認範囲を作業記録へ残します。
