# atproto survey automation

新しく生まれた atproto プロジェクトを継続的に発見するための作業用リポジトリです。

主対象は「新規 lexicon を持つ独立プロジェクト」です。
ライブラリや auth-only サービスは原則除外し、infra ツールは別カテゴリで保持します。

## 現在の方針

- 候補収集は deterministic にやる
- 曖昧な分類と要約だけ local LLM を使う
- なるべく無料で回す
- 週次 snapshot + watchlist を前提にする

## 構成

- [docs/manual-workflow.md](docs/manual-workflow.md): 現在の手動調査手順
- [docs/automation-plan.md](docs/automation-plan.md): 全体方針
- [docs/automation-architecture.md](docs/automation-architecture.md): 推奨アーキテクチャ
- [docs/project-record.md](docs/project-record.md): 保持したい項目
- [docs/manual-candidates.md](docs/manual-candidates.md): 手動候補の追加方法
- [docs/project-registry.md](docs/project-registry.md): 手動 seed 登録と project 調査
- [prompts/project-triage.md](prompts/project-triage.md): local LLM 用プロンプト
- [scripts/discover.py](scripts/discover.py): collection / GitHub / Tangled の snapshot 取得と差分検出
- [scripts/add_manual_candidate.py](scripts/add_manual_candidate.py): 手動候補の JSONL 追記
- [scripts/project_registry.py](scripts/project_registry.py): 手動 seed 登録と project record 補完
- [config/sources.json](config/sources.json): 情報源設定

## いまの推奨構成

### 収集

- `https://collectiondata.usounds.work/collection_count_view`
- GitHub repository search
- Tangled repo discovery
  - UFOs Record samples API for `sh.tangled.repo`
- 補助的に Lexicon Garden / ATProto Dashboard / UFOs

### 保存

- SQLite

### 判定

- ルールベース
- local LLM
  - 例: `qwen/qwen3.5-35b-a3b`

### 実行

- macOS: `launchd`
- Linux: `cron`

## 叩き台スクリプト

```bash
python3 scripts/discover.py --report reports/latest.md
```

新規 collection だけ見たい場合:

```bash
python3 scripts/discover.py --only-new
```

builtin namespace も出したい場合:

```bash
python3 scripts/discover.py --include-builtin
```

手動で候補を足す場合:

```bash
python3 scripts/add_manual_candidate.py \
  --name "example" \
  --url "https://example.com" \
  --summary "short summary"
```

project seed を直接登録して調査したい場合:

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

## 次にやること

- changed collection に対する追加調査を入れる
- GitHub / Tangled 由来の候補を `project_record` に統合する
- `project_record` 形式で保存する
- watchlist 再巡回を追加する
- local LLM 連携を追加する
