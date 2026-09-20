# AGENTS

このリポジトリで作業するエージェント向けの恒久メモです。直近の残件や調査中の heuristic はここに詳しく書かず、関連ドキュメントへの導線だけを置きます。

## 目的

- `atproto` 関連の project / service / library / lexicon を収集・整理する
- 公開command入口:
  - `bin/project-registry`: `project-registry`
  - `bin/project-registry-web`: `project-registry-web`
  - `bin/project-registry-web-service`: `project-registry-web-service`（macOSでのWeb自動起動管理）
- 主実装は `scripts/project_registry_core/`
  - `application/`: CLIとWebで共有するquery/mutation service
  - `commands/`: `argparse`と出力を担当するCLI adapter
  - `jobs.py`: 長時間処理のrequest/event/result/cancellation契約
  - `worker.py`: 長時間application serviceを別processで実行する内部entrypoint
- Web実装は`scripts/project_registry_web/`
  - schema v2 Web API、subprocess job registry、静的UI
- 詳細なmodule構成と依存方向は`docs/developer/architecture.md`を正本にする
- 調査結果の正本は SQLite DB `data/survey.db`
- このリポジトリは個人用のローカル運用を前提にする
  - 他ユーザーや共有 DB は考慮しない
  - 同時編集の懸念は、自分の別プロセスや GUI 操作だけを想定する

## 重要な前提

- `projects` テーブルが主データ
- 現在の保存構造はschema version 4:
  - `projects`: scalar + `profile_json` + `state_json`
  - `project_identifiers`
  - `project_relations`
  - `project_decisions`
- 調査履歴は同じSQLiteの `investigation_runs` / `collector_runs` に保存する

## DB 操作

- DB へ直接影響する操作の前は、別の `enrich` や GUI が走っていないか注意する
- 特に慎重に扱うもの:
  - `repair-db`
  - `merge --execute`
  - import / sync の `--execute`
  - 直接 SQL 更新
- heuristic を変えた後に既存データへ反映したい時は、まず `repair-db` で足りるかを考える

## 実装方針

- 強い根拠と弱い根拠を分ける
  - confirmed
  - candidate
  - derived / heuristic
- ownership / identity / merge は保守的に扱う
- 曖昧なものは自動確定より queue / interactive review へ寄せる
- 生成物や一時データは安易にコミットしない
- コミットは適宜行う
  - 基本は作業トピックが変わるタイミング
  - 1つの目的に紐づく変更をひとまとまりで切る
  - 直近の仮説修正や取り消しは、まだ外に出していない履歴なら `commit --amend` や rebase 相当で整える
  - 既に共有済み、または他作業が依存している履歴は無理に書き換えず、通常の追加修正コミットで扱う

## 変更の受入条件

- task開始時に、目的、変更対象、非対象、確認方法、文書更新先を明確にする
- code変更は対象testだけでなく、commit前に`bin/project-registry-test`で全unit testを実行する
- Web UI変更はPython側のAPI testとJavaScript syntax checkに加え、変更した主要導線を実browserで確認する
  - 確認環境、DB、bind、port、実運用への反映境界は`docs/developer/workflows/change-acceptance.md`に従う
- heuristic変更はunit testだけで完了扱いにせず、既知の実誤判定fixtureまたは人間によるsample reviewで意味上の差分を確認する
  - 特定のproject、URL、source、既知fixtureだけに作用する条件を通常ロジックへ追加しない
  - 新しい分岐を追加する前に、既存規則の修正または置換で同じ問題を解消できないか確認する
  - 一般化できない対応が必要な場合は、operator decision、設定、または期限付きの観測として分離し、適用範囲と廃止条件を示す
- DB保存契約やcleanupはtemp DB上で実装確認する。正本DBのread-only CLI・SQL・書き捨てスクリプトは直接使用し、書込みを伴う確認や保存前後の比較だけ複製DBを使う。正本DBへの適用は別途承認を得る
- external source契約を変える場合は、通常testと分けてbounded smoke testの要否を判断する
- 実施していない確認は未検証としてstatusまたは作業固有notesの適切な場所へ残す

## 参照先

- 目的と方向性: [brief](docs/brief.md)
- 現在頼ってよい範囲: [status](docs/developer/status.md)
- 今後の目標: [roadmap](docs/roadmap.md)
- 文書全体の責務・更新先: [文書管理方針](docs/documentation-policy.md)
- 実装全体: [architecture](docs/developer/architecture.md)
- 変更・検証手順: [変更の受入](docs/developer/workflows/change-acceptance.md)
- 利用手順: [README](README.md)、[利用ガイド](docs/user/guide.md)、[CLI](docs/user/cli.md)、[Web UI](docs/user/web-ui.md)
- 未決事項・保留・再開材料: [notes](docs/notes/)

## 文書の更新

- 詳細な配置・分割・統合・廃止の判断は[文書管理方針](docs/documentation-policy.md)に従う。
- 現行仕様、未実現の設計、現在地、今後の目標、作業記録を区別する。notesの確定事項は実装・合意と照合して正本へ反映する。
- statusは機能ごとの現在の説明を置き換え、roadmapは到達目標を更新する。作業ごとの完了一覧・test件数を追記しない。
- 作業固有の有効な保留・再開情報はnotesに置き、必要な間だけstatusから参照する。
- 利用者向けの基本用語は[利用ガイド](docs/user/guide.md#基本用語)を正本とする。開発文書のコード・DB・enum対応語を一括置換しない。

## 承認・一時作業の方針

* 承認やユーザー作業を避けるために、過剰な回避実装をしない。
* 合意した作業範囲内で、現在の権限で実行できる読み取り・非破壊的な検証には追加確認を求めない。ワークスペース外という理由だけで昇格しない。実データの対象と操作への許可は別途必要とする。
* 新たなパッケージ導入、通信先、書込先、破壊的操作など、既存の作業許可・権限を超える場合は、目的と実行コマンドを示して必要な確認・昇格を求める。既存の承認済みprefixが使える場合は、その先頭のトークン列を保ち、作業場所は実行toolのworkdirで指定する。
* 一度昇格した処理でも、必要だった条件が解消した後の実行は通常権限へ戻す。DBの読み取り計測で使う接続・一時出力とWAL補助ファイルの扱いは、[変更の受入](docs/developer/workflows/change-acceptance.md#sandboxでの読み取り計測)に従う。
* 一時ファイル、検証用スクリプト、作業ログは原則として `.tmp/` 以下に置く。`/tmp` やワークスペース外は明示的な必要がある場合だけ使う。
* `.tmp/` 以下は成果物ではない。必要がなければ削除してよい。
* 正本DBの検証用複製とSQLiteのWAL/SHMは作業完了時に削除する。残す理由がなければ`project-registry cleanup-workspace-temp --older-than-hours 0 --execute`を使い、全test時の容量警告を放置しない。
* 依存パッケージを避けるための自作実装は、小さく明らかな処理に限る。パーサ、暗号、認証、HTTP、ファイルロック、日付時刻処理などは自作せず、必要な依存の追加を提案する。
* 即席スクリプトを実行する場合は、何を読み書きするかを簡単に説明してから実行する。

## 互換性方針

このプロジェクトは個人利用・非公開のため、外部向けの後方互換性は原則として重視しない。古い仕様・未使用API・旧設定形式を残すより、現在の実装を単純に保つことを優先する。互換レイヤ、fallback、deprecated alias、移行処理は、明示的に必要な場合だけ追加する。文書・コード問わず、現状に無関係な内容は積極的に削除・置換する。

## まず読む順番

1. この `AGENTS.md`
2. `docs/brief.md`と`docs/developer/status.md`。作業に関係する有効な保留・注意も確認する
3. 文書変更では`docs/documentation-policy.md`、実装変更では`docs/developer/architecture.md`
4. 対象機能の`docs/developer/specs/`と、必要な`docs/developer/workflows/`
5. 必要に応じて`docs/roadmap.md`、`docs/user/`、関連する`docs/notes/`
