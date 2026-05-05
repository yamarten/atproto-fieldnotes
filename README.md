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
- [scripts/project_registry.py](scripts/project_registry.py): 手動 seed 登録、本格調査、GitHub/Bluesky 調査、LLM 審査、canonical project record への昇格
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

project seed を直接登録して、本線 project record として育てたい場合:

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

local LLM を使う場合:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

endpoint 直指定でも動きます:

```bash
python3 scripts/project_registry.py enrich \
  --seed sh.tangled.repo \
  --type nsid \
  --llm-endpoint http://127.0.0.1:1234/v1 \
  --llm-model qwen/qwen3.5-35b-a3b
```

`ATPROTO_SURVEY_LLM_COMMAND` も `ATPROTO_SURVEY_LLM_ENDPOINT` も無い場合、`enrich` は事実収集だけ行い、record は `draft` のまま残ります。

LLM が埋める `tagline` / `summary` / `features` / 判定理由は日本語出力を前提にしています。`tagline` は助詞を含まない短い名詞句として扱い、最大 30 文字に丸めます。

Bluesky の post 検索を安定させたい場合は app password も設定します。

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
```

merge recommendation を見るだけなら dry-run:

```bash
python3 scripts/project_registry.py merge \
  --project-key nsid:app.bsky.feed.post \
  --use-recommendation
```

そのまま recommendation を使って統合するなら:

```bash
python3 scripts/project_registry.py merge \
  --project-key nsid:app.bsky.feed.post \
  --use-recommendation \
  --execute
```

危ない候補を除いた一括マージ候補だけ見たい場合:

```bash
python3 scripts/project_registry.py merge-batch
```

実行する場合:

```bash
python3 scripts/project_registry.py merge-batch --execute
```

`applog.json` から紹介済み URL を取り込む場合:

```bash
python3 scripts/project_registry.py import-applog applog.json
python3 scripts/project_registry.py import-applog applog.json --execute
python3 scripts/project_registry.py import-applog applog.json --execute --create-unmatched
```

WhiteWind のリンク記事を既存 project へ寄せる場合:

```bash
python3 scripts/project_registry.py import-list list.json
python3 scripts/project_registry.py import-list list.json --execute
```

構造化されたサービス一覧から補完する場合:

```bash
python3 scripts/project_registry.py import-services services.json
python3 scripts/project_registry.py import-services services.json --execute --create-unmatched
```

PDSls の URL を seed 候補として使っている場合も、そのまま `add-seed` / `enrich --seed` に渡せます。`https://pdsls.dev/at://.../collection[/rkey]` や `at://did.../collection[/rkey]` は collection NSID に変換して扱います。

新規に作られる project の `project_key` は seed 由来ではなく、`proj:xxxxxxxxxxxx` 形式の opaque ID です。

CLI では `--project-key` に `proj:` を含む完全キーのほか、`proj:` を省略した先頭一致 prefix も使えます。例えば `2f2acb` や `proj:2f2acb` のように指定できます。

`enrich --all` は既定で `tracking_state=active|watchlist` の project だけを回します。`stable` / `archived` / `ignore` も回したい場合は `--include-stable` などを付けます。各 project の JSON には `promoted_now` が出ます。複数件を回した場合は最後に `promoted_now` 一覧の summary も出ます。後から確認したい場合は `show --format json` で `last_promoted_at` と `promotion_events` を見ます。

GUI で DB を直接編集した後は、`data_json` の正規化、派生フィールドの再計算、重複衝突の確認のためにこれを回します。`project_identifiers` テーブルはもう使っていません。

```bash
python3 scripts/project_registry.py repair-db
python3 scripts/project_registry.py repair-db --salvage-json --clean-notes
python3 scripts/project_registry.py set-tracking-state --project-key service:https-example-com --tracking-state stable --note "安定運営"
```

## 次にやること

- changed collection に対する追加調査を入れる
- GitHub / Tangled 由来の候補を `project_record` に統合する
- 手動 seed 以外から `project_registry.py add-seed` へ橋渡しする
- watchlist 再巡回を追加する
