# AGENTS

このリポジトリで作業するエージェント向けの恒久メモです。直近の残件や調査中の heuristic はここに詳しく書かず、関連ドキュメントへの導線だけを置きます。

## 目的

- `atproto` 関連の project / service / library / lexicon を収集・整理する
- 公開CLI入口は `bin/project-registry`（command名は`project-registry`）
- `scripts/project-registry`は既存呼び出しを公開wrapperへ委譲するalias
- 主実装は `scripts/project_registry_core/`
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

## 参照先

- 利用手順:
  - [README.md](README.md)
  - [docs/project-registry.md](docs/project-registry.md)
- 内部仕様:
  - [docs/registry-internals.md](docs/registry-internals.md)
  - [docs/investigation-model.md](docs/investigation-model.md)
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

## まず読む順番

1. この `AGENTS.md`
2. `docs/thread-handoff.md`
3. `docs/registry-internals.md`
4. `docs/investigation-model.md`
5. 必要に応じて `README.md` / `docs/project-registry.md` / `docs/notes/`
