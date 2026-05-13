# Thread Handoff

次の担当者向けの引き継ぎメモです。利用手順は [docs/project-registry.md](project-registry.md)、内部仕様は [docs/registry-internals.md](registry-internals.md) を参照してください。

## まず知っておくこと

- DB は `data/survey.db`
- 主要テーブルは `projects` だけ
- `projects` は現在
  - scalar columns
  - `record_json`
  - `ops_json`
- `record_json` は高信頼の project record
- `ops_json` は候補・調査・LLM・merge 制御などの運用情報
- `project_identifiers` はもう使わない

## 運用上の注意

- DB を直接更新する前に、ユーザーへ一声かける
  - `enrich` が裏で走っている可能性があるため
- 特に事前確認が必要な操作
  - `repair-db`
  - import の `--execute`
  - `merge --execute`
  - `merge-batch --execute`
  - DB への直接 SQL 更新
- GUI で DB を触った後は `repair-db` を回す

## 現在の主要レーン

- 新規候補
  - `collection_count_view`
  - `sync-introduced` の Bluesky 投稿検索
- project 調査
  - `scripts/project_registry.py`
- 補助 discovery
  - `scripts/discover.py`

`list.json` / `services.json` 系の一時 import は削除済みです。今後の前提にしません。

## `project_registry.py` の重要ポイント

- `add-seed`
  - `nsid`, `repo_url`, `service_url` を受ける
  - `pdsls.dev`, `pds.ls`, `at://...` も `nsid` seed として扱う
- `sync-introduced`
  - `from:yamarten.bsky.social #ATプロトーク` の Bluesky 検索を既定で使う
  - seed は増やさず、既存 project の `introduced_url` だけ更新する
- `sync-collections`
  - collection inventory API を直接 fetch する
  - 新規 collection import に加えて、既知 collection の `count/recent_count/max` も refresh する
- `sync-github-search`
  - `config/sources.json` の `github_repo_search` を使う
  - 既知 repo を除いた、最近更新された未知 repo seed を追加する
- `enrich`
  - 既定では `tracking_state=active|watchlist` のみ
  - `stable` と `stalled` は明示 include が必要
  - local LLM を使う場合、endpoint timeout は `ATPROTO_SURVEY_LLM_TIMEOUT`、既定 `300`
- `review-stalled`
  - `stalled` 降格だけでなく `stable` 提案と `active` への復帰提案も返す
  - repo activity と collection activity のどちらか一方でも最近動いていれば `stalled` に落とさない
- `repair-db`
  - scalar 重複除去
  - 派生フィールド再計算
  - collision 監査
  - invalid JSON の自動救済はしない
- `migrate-storage-layout`
  - `preview`
    - `record_json` / `ops_json` の group 構成を確認
  - `backfill`
    - `record_json` / `ops_json` / `introduced_url` を既存 DB に埋める
  - `retire-legacy-data`
    - 旧 `data_json` の中身を空に揃える
  - `cleanup-schema`
    - 旧 `status` / `category` / `primary_nsid` / `data_json` を物理削除する
  - `finalize-check`
    - 完全移行前に未分類の legacy field が残っていないか確認する

## 既知の前提

- 同一ドメイン配下でも曖昧なら自動吸収しない
  - 例: `Rito` 本体と派生サービスのようなケース
  - こういうものは新規 record に落として後で `merge`
- `merge-batch` は保守的
  - 危ない候補は通さない前提
- `set-separate`
  - 手動で「この 2 件は別物」を相互に記録する
  - `duplicate_candidates` と `merge_recommendation` からその pair を除外する
- `block-identifier`
  - 手動で「この project にこの `service_url` / `repo_url` / `nsid` は付けない」を記録する
  - `finalize` / `enrich` 後も同じ identifier が戻らないようにする
- `duplicate_candidates` と `merge_recommendation` は派生データ
  - 必要なら再計算できる

## ドキュメントの読み分け

- 利用者向け
  - [README.md](../README.md)
  - [docs/project-registry.md](project-registry.md)
- 設計・内部仕様
  - [docs/registry-internals.md](registry-internals.md)
  - [docs/project-record.md](project-record.md)
- 調査背景
  - [docs/manual-workflow.md](manual-workflow.md)
  - [docs/automation-plan.md](automation-plan.md)
  - [docs/automation-architecture.md](automation-architecture.md)

## コミット時に気をつけること

- `data/` 配下の JSON は生成物や棚卸しメモが混ざる
- `data/tldextract-cache/` は生成 cache
- ユーザーが明示しない限り、`data/*.json` を安易にコミットしない
- DB スキーマや運用ルールを変えたら、利用者向け文書と内部文書の両方を更新する
