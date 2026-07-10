# AGENTS

このリポジトリで作業するエージェント向けの恒久メモです。直近の残件や調査中の heuristic はここに詳しく書かず、関連ドキュメントへの導線だけを置きます。

## 目的

- `atproto` 関連の project / service / library / lexicon を収集・整理する
- 公開command入口は `bin/project-registry` と `bin/project-registry-web`
  - command名はそれぞれ`project-registry` / `project-registry-web`
- 主実装は `scripts/project_registry_core/`
  - `application/`: CLIとWebで共有するquery/mutation service
  - `commands/`: `argparse`と出力を担当するCLI adapter
  - `web/`: schema v2 Web API、subprocess job registry、静的UI
  - `jobs.py`: 長時間処理のrequest/event/result/cancellation契約
  - `worker.py`: 長時間application serviceを別processで実行する内部entrypoint
  - `registry.py`: domain helperとcollector/evaluation orchestration
- 調査結果の正本は SQLite DB `data/survey.db`
- このリポジトリは個人用のローカル運用を前提にする
  - 他ユーザーや共有 DB は考慮しない
  - 同時編集の懸念は、自分の別プロセスや GUI 操作だけを想定する

## 重要な前提

- `projects` テーブルが主データ
- 現在の保存構造はschema version 2:
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
  - `merge-batch --execute`
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
- code変更は対象testだけでなく、commit前に全unit testを実行する
- Web UI変更はPython側のAPI testとJavaScript syntax checkに加え、変更した主要導線を実browserで確認する
- heuristic変更はunit testだけで完了扱いにせず、既知の実誤判定fixtureまたは人間によるsample reviewで意味上の差分を確認する
- DB保存契約やcleanupは、temp DB上の実装確認と、正本DBの複製に対するdry-run/auditを分ける。正本DBへの適用は別途承認を得る
- external source契約を変える場合は、通常testと分けてbounded smoke testの要否を判断する
- 実施していない確認は未検証としてstatus、roadmap、notes、handoffの適切な場所へ残す

## 参照先

- 利用手順:
  - [README.md](README.md)
  - [docs/project-registry.md](docs/project-registry.md)
- 内部仕様:
  - [docs/status.md](docs/status.md)
  - [docs/registry-internals.md](docs/registry-internals.md)
  - [docs/investigation-model.md](docs/investigation-model.md)
  - [docs/background-jobs.md](docs/background-jobs.md)
  - [docs/project-record.md](docs/project-record.md)
- 設計途中の比較・検討メモ:
  - [docs/notes/](docs/notes/)
- 直近の残件・引き継ぎ:
  - [docs/thread-handoff.md](docs/thread-handoff.md)

## 文書の配置

- `docs/` 直下は、利用手順、現行仕様、データモデル、roadmap、handoff など役割が確立した正本文書を置く
- 開発フェーズ名や一時的な作業単位だけを理由に、`docs/` 直下へ新規文書を増やさない
- 調査メモ、比較資料、設計途中の論点、作業記録など、会話だけでは失われるが現行仕様や roadmap に直接混ぜるには重い内容は `docs/notes/` に置いてよい
- `docs/notes/` の文書は作業用・履歴用であることを明確にし、確定した内容は適切な正本文書へ反映する
- 現状仕様、将来課題、調査途中の仮説を一つの文書に混ぜず、`registry-internals.md` / `roadmap.md` / `thread-handoff.md` / `docs/notes/` の責務に分ける
- `docs/thread-handoff.md` は次スレッドが読む最短の現在地と未完了作業だけを書く
  - 完了した実装内容、検証ログ、詳細な変更一覧は置かない
  - `次に必要な作業` には未完了で実行可能な作業だけを書く
  - 実装済み・動作確認済みのsnapshotは `docs/status.md` へ置く
  - 将来候補は `docs/roadmap.md`、設計途中の考え方は `docs/notes/` へ置く
  - DBへ直接影響した直近のbackupや注意点だけは、次作業の安全に必要な範囲で短く残す

## 承認・一時作業の方針

* 承認やユーザー作業を避けるために、過剰な回避実装をしない。
* パッケージ導入、ネットワークアクセス、ワークスペース外の読み書き、破壊的操作が必要な場合は、無理に迂回せず、目的と実行コマンドを示して確認を求める。
* 一時ファイル、検証用スクリプト、作業ログは原則として `.tmp/` 以下に置く。`/tmp` やワークスペース外は明示的な必要がある場合だけ使う。
* `.tmp/` 以下は成果物ではない。必要がなければ削除してよい。
* 依存パッケージを避けるための自作実装は、小さく明らかな処理に限る。パーサ、暗号、認証、HTTP、ファイルロック、日付時刻処理などは自作せず、必要な依存の追加を提案する。
* 即席スクリプトを実行する場合は、何を読み書きするかを簡単に説明してから実行する。

## 互換性方針

このプロジェクトは個人利用・非公開のため、外部向けの後方互換性は原則として重視しない。
古い仕様・未使用API・旧設定形式を残すより、現在の実装を単純に保つことを優先する。
互換レイヤ、fallback、deprecated alias、移行処理は、明示的に必要な場合だけ追加する。
文書・コード問わず、現状に無関係な内容は積極的に削除・置換する。

## まず読む順番

1. この `AGENTS.md`
2. `docs/thread-handoff.md`
3. `docs/status.md`
4. `docs/registry-internals.md`
5. `docs/investigation-model.md`
6. `docs/background-jobs.md`
7. 必要に応じて `README.md` / `docs/project-registry.md` / `docs/notes/`
