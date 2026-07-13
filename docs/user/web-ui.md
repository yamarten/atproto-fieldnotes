# Project Registry Web UIガイド

`project-registry-web`はschema version 2のSQLite DBを操作する、単一operator向けのローカルWeb UIです。日常的な登録、調査、review、project修正はWeb UIを主導線とし、低水準のauditやmaintenanceは[CLIガイド](cli.md)へ分けています。

## 起動

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

| option | 意味 |
| --- | --- |
| `--host` | bind先。既定はloopback。 |
| `--port` | port。既定は`8765`。 |
| `--db-path` | 接続するschema v2 SQLite DB。既定は`data/survey.db`。 |
| `--token` | API共有token。`ATPROTO_SURVEY_WEB_TOKEN`でも指定できる。 |
| `--reload` | Web実装変更時にserver processを再起動する。 |
| `--reload-interval` | reload確認間隔。 |

別DBは先にCLIで初期化します。

```bash
project-registry init-db --db-path data/survey.dev.db
project-registry-web --db-path data/survey.dev.db --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

## 画面構成

| 画面 | 主な用途 | DB変更 |
| --- | --- | --- |
| `Review` | review queue、promotion queue、任意pairを含むmerge review | actionを確認した時だけwrite。skipはwriteしない |
| `Projects` | project検索、detail表示、seed preview・追加 | 検索はread-only。明示した編集/actionだけwrite |
| `Operations` | enrich、設定済みsync、tracking classification review、scheduled preset | dry-runとexecuteを画面で区別 |
| `Settings` | queue limit、merge mode、表示time zone、token、server/DB情報 | UI設定はlocalStorage。server reloadはDBを変更しない |

検索条件、選択project、detail tabはURLに保存され、reload、back、forwardで復元します。tokenとUI設定はURLへ残しません。

## Projects

`Add seed`はNSID、repository URL、service URLをpreviewしてから登録します。自動判定を上書きする場合は`repo_url: https://forge.example/dev/app`のように入力します。既存project detailから追加したseedはそのprojectへ紐づきます。

Filtersで次を絞り込めます。

- tracking state、service status、atproto contribution、introduced有無、candidate/error有無
- identifierのkind、value、classification
- relationのpredicate、subject/object typeとvalue、source
- decisionのkind、target type/value、active・expired・cleared

tracking state、service status、atproto contributionは複数選択です。kind、classification、predicate、subject/object type、decision kind/target typeは選択肢から指定します。identifier・relation・decisionのvalueは部分一致、relation sourceは候補付き完全一致です。未選択のfilterは絞り込みません。

project detailは次のtabに分かれます。

| tab | 表示内容 |
| --- | --- |
| `Overview` | profile、service/ownership、保存済み結論のExplain、merge候補、notes |
| `Identifiers` | classificationとrole付きidentifier、候補へのaction |
| `Relations` | typed relation evidence、dismiss/defer action |
| `Investigation` | 最新run、collector結果/failure、履歴、保存evidence、replay |
| `Decisions` | active guardとoperator decision履歴 |
| `Raw` | schema v2 APIが返したproject payload |

`Overview`のExplainは保存済みstateを表示し、networkやLLMを使いません。全project比較が必要なmerge候補はdetail本体の後に別requestで計算し、DBへ保存しません。保存データがない項目は`not_observed`であり、否定結果ではありません。

detailから次を実行できます。

- 単体enrich、tracking state変更、manual seed追加
- candidate confirm/reject、identifier block/defer、relation dismiss/defer
- promotion hold、split、separation解除、merge review
- profileの`name`、`introduced_url`、人間向け`notes`のpreview・保存

変更は確認後にSQLiteへ保存します。profile editはpreview後にprojectが別processで変わっていた場合、保存を拒否して再読込を促します。`system_notes`は機械生成履歴で、profile editの対象ではありません。

identifierへのblock/defer/splitは同じvalueのrole単位ではなく、画面に示すidentifier identity全体へ作用します。defer解除は自動confirmではありません。

## Review

`Review Queue`は人の確認が必要な候補、`Promotion`は紹介候補、`Merge`は重複候補または直接入力した任意pairを表示します。

action保存後は表示中カードだけを一覧から外し、次へ進みます。一覧全体を自動再計算しないため、最新候補と件数を取り直す時は`Refresh`を使います。`Skip for now`はDBへ何も書かず、その表示中だけ候補を先送りします。

merge reviewはsourceからtargetへ統合する向きで、両projectのprofile、state、seed、identifier、pair signal、merged previewを比較します。`Keep separate`はpairのseparation guardを保存します。`Merge projects`は確認後に統合します。候補一覧にないpairもproject keyを直接指定して比較できます。

PSLにないmulti-tenant suffixが原因らしいpairでは、suffix候補を表示し、その場で`domain-overrides.json`へ追加できます。suffix追加は今後のfamily判定を変えるため、preview内容を確認してください。

## Operations

background jobはWeb serverとは別processで動き、同じWeb serverでは同時に1件だけ実行できます。CLIなど別processとの排他は行いません。

### Enrich

単体またはbulkを選び、deep investigation、未enrichのみ、前回failureのみ、追加tracking stateを指定できます。bulk enrichはproject間で停止し、checkpointから再開できます。1 projectのfailureは記録して後続を処理し、run全体をfailedとして再開可能にします。

### Sync

設定済みのintroduced、GitHub、collection inventory、Semble、Tangled sourceを実行します。任意queryやURLはCLIで指定します。dry-runでも外部networkへ接続しますがSQLiteは更新しません。syncの停止要求は現在のsource page/item境界で反映され、checkpoint再開はありません。

### Review Ops

保存済みproject dataからstalled/stable、out-of-scope、ignore候補を再計算します。plan作成はnetwork-freeです。dry-runで候補を確認し、execute時は表示対象を一括適用します。

### Scheduled Presets

allowlist済みpresetだけを手動または定期発火できます。任意commandは実行できません。設定できるのは通常bulk enrich、全tracking stateのmaintenance、introduced sync、その他sync後のnew-only enrichです。

各presetはenabled、毎週または月の第N週、曜日、時刻、time zoneを持ちます。設定とpending queueはDBと同じdirectoryの`job-schedules.json`へ保存します。予定から24時間を超えた未実行分は後追い発火しません。server再起動時に実行中だったstepは完了扱いにせず、同じstepから再試行します。

### job履歴と再起動

job一覧とeventはserver process内だけに保持され、server再起動後は復元されません。bulk enrichの未完了checkpointは`Discovered Checkpoints`から再発見・再開できます。sync/reviewにはcheckpoint再開がありません。

Settingsの`Reload server`はWeb processを再起動し、SQLiteは変更しません。active jobへ協調停止を要求し、timeoutを超えたworkerは終了してから再起動します。browserは新processの応答後に自動再読込します。

## Settings

- queue/project/merge limit、merge mode、personal toolの包含
- 表示time zone。既定`Asia/Tokyo`、IANA名または`+09:00`形式
- API tokenの保存・削除
- 接続DB、Python version、起動時Git commit/dirty状態
- server reload

UI設定とtokenは同じoriginの`localStorage`に保存します。表示time zoneを変えてもDB timestampは変わりません。Git状態はserver起動時に一度取得するため、checkout変更後はreloadが必要です。

## Access境界

Web UIは`127.0.0.1`上で単一operatorが使う前提です。公開server、共有端末、複数利用者向けのaccess controlはありません。

- 起動時の初回URLだけqueryにtokenを含み、UIは読取直後にURLから除去します。ただしterminal、browser拡張、初回navigationを観測できる相手からtokenを守る仕組みではありません。
- tokenは同じoriginの`localStorage`へ保存されます。同じbrowser profile・originを使えるscriptや利用者とは秘密を分離できません。不要になったらSettingsの`Clear API token`で削除してください。
- serverはplain HTTPで、TLS終端やtoken rotationを提供しません。`--host 0.0.0.0`などで直接公開しないでください。
- localhost外から使う場合はloopbackへbindしたまま、管理下のSSH tunnelまたはTLS付きreverse proxyを前段に置きます。

API requestは共有token必須です。起動時URLのbootstrap token、`Authorization: Bearer ...`、`X-Project-Registry-Token`のいずれかを使います。静的assetはtokenなしで配信します。

## 現在未接続のもの

- server再起動後のprocess job履歴復元。bulk enrich checkpointの再発見は接続済み。
- Web UIからのLLM接続設定。
- Web UIからのserver shutdown。reloadは接続済み。

