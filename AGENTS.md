# AGENTS

このリポジトリで作業するエージェント向けの恒久メモです。直近の残件や調査中の heuristic はここに詳しく書かず、関連ドキュメントへの導線だけを置きます。

## 目的

- `atproto` 関連の project / service / library / lexicon を収集・整理する
- 主作業対象は `scripts/project_registry.py`
- 調査結果の正本は SQLite DB `data/survey.db`
- このリポジトリは個人用のローカル運用を前提にする
  - 他ユーザーや共有 DB は考慮しない
  - 同時編集の懸念は、自分の別プロセスや GUI 操作だけを想定する

## 重要な前提

- `projects` テーブルが主データ
- 現在の保存構造は:
  - scalar columns
  - `record_json`
  - `ops_json`

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
  - [docs/project-record.md](docs/project-record.md)
- 直近の残件・引き継ぎ:
  - [docs/thread-handoff.md](docs/thread-handoff.md)

## まず読む順番

1. この `AGENTS.md`
2. `docs/thread-handoff.md`
3. `docs/registry-internals.md`
4. 必要に応じて `README.md` / `docs/project-registry.md`
