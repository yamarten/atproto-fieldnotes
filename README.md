# atproto survey automation

新しく生まれたatprotoプロジェクトを発見し、追跡するための作業用リポジトリです。

主対象は「新規lexiconを持つ独立プロジェクト」です。ライブラリやauth-onlyサービスは原則除外し、infraツールは別カテゴリで保持します。

## このリポジトリでやること

- 候補を収集する
  - `collection_count_view`
  - Bluesky 投稿検索
- seedからproject recordを育てる
  - 事実収集
  - LLMによる要約と分類
  - 継続調査
- 紹介済みかどうか、継続調査対象かどうかを管理する

## 環境準備

隔離した Python 環境を使う前提にします。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`tldextract` を使って PSL 判定を行います。入力は repo 内の [vendor/public_suffix_list.dat](vendor/public_suffix_list.dat) に固定し、ライブラリの cache は [data/tldextract-cache](data/tldextract-cache) 配下に置きます。外部の絶対パスには依存しません。

PSL に無い hosted suffix を手動で足したい場合は、[config/sources.json](config/sources.json) の `custom_multi_tenant_suffixes` に追加します。ここに `example.com` を入れると、`foo.example.com` と `bar.example.com` を別 service family として扱います。

登録済み project への影響も確認する場合は、監査コマンドから追加できます。

```bash
python3 scripts/project_registry.py audit-source-config example.com
```

既定は interactive です。追加確認後に `sources.json` を更新し、新しい family 基準で同一 project に複数 tenant が混在していないかを表示します。tenant group の番号を選んだ場合だけ、その group の identifier をまとめて新 project へ切り出します。JSON dry-run だけを見る場合は `--no-interactive`、確認なしで suffix 追記まで行う場合は `--execute --no-interactive` を使います。non-interactive では split を適用しません。

## テスト

テストは標準ライブラリの `unittest` で実行します。外部 API、実 DB、実 LLM endpoint には触れず、必要な SQLite DB は temp directory に作ります。

```bash
python3 -m unittest discover -s tests -v
```

## zsh補完

wrapper の [bin/project-registry](bin/project-registry) と、zsh completion の [completions/_project-registry](completions/_project-registry) を追加しています。

普段 `project-registry` で呼びたい場合:

```bash
export PATH="$PWD/bin:$PATH"
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

これで:

```bash
project-registry <TAB>
project-registry enrich --llm-api-style <TAB>
```

のように subcommand と主要オプションを補完できます。

`scripts/` をそのまま `PATH` に入れると Python ファイルまでコマンド候補に出るので、`PATH` には `bin/` だけを足す前提にしています。`python3 scripts/project_registry.py ...` をそのまま使う場合でも、completion 定義には `project_registry.py` を紐付けているので、zsh 上では同じ候補が出ます。

## Web UI

モバイル端末から review 作業を回したい時は、追加依存なしの小さな Web UI を使えます。API は共有トークン必須です。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
python3 scripts/project_registry_web.py --host 127.0.0.1 --port 8765
```

LAN 内の端末から触る場合:

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
python3 scripts/project_registry_web.py --host 0.0.0.0 --port 8765
```

worktree から起動して普段使いの DB を見る場合は、DB path を明示します。

```bash
python3 scripts/project_registry_web.py \
  --host 127.0.0.1 \
  --port 8765 \
  --db-path data/survey.db \
  --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

開発中はブラウザ許可を何度も出さずに済むよう、コピーDBと固定ポートを使います。

```bash
python3 scripts/project_registry_web.py \
  --host 127.0.0.1 \
  --port 8769 \
  --db-path data/survey.dev.db \
  --token codex-dev-token \
  --reload
```

`--reload` を付けると、`scripts/project_registry_web.py` または `scripts/project_registry.py` の変更時に同じポートのままサーバプロセスが再起動します。開発中に `lsof` / `kill` でポートを空け直す必要を減らせます。

本番向けに長時間起動している場合は、`Settings` タブで現在の PID を確認できます。同じ画面の `Reload server` は同じ引数のままサーバプロセスを再起動し、`Stop server` はサーバプロセスを停止します。どちらも SQLite を変更しませんが、実行中の background job は止まります。

起動時に表示される `?token=...` 付き URL を開くと、ブラウザの localStorage に token を保存します。以後の `/api/*` は `Authorization: Bearer ...` または `X-Project-Registry-Token` で認証します。

v1 の対象は `review-queue` 中心です。project 詳細、raw JSON、`tracking_state` 変更、review signal dismissal、service/repo candidate の承認・拒否、既存 project への seed 追加、新規 seed 登録、単体 `enrich` / deep enrich を扱えます。

`New Seed` タブでは、NSID / repo URL / service URL を preview してから登録できます。`?seed=https%3A%2F%2Fexample.com` のように URL query で seed を渡すと、この画面を直接開きます。exact duplicate がある場合は既存 project を開き、近い候補がある場合は登録前に候補を表示します。

`Review Ops` タブでは、`review-stalled` / `review-ignore` 相当を background job として起動できます。既定は dry-run で、SQLite へ書き込む場合は `Execute writes to SQLite` を明示的に有効にして確認ダイアログを通します。job JSON には `logs` が含まれ、候補選択、更新、失敗理由の時系列を確認できます。

project 詳細の `Project Edits` では、任意の `nsid` / `repo_url` / `service_url` を指定して次の操作もできます。

- `Split`: 指定 identifier を新しい project に移し、元 project から除去して、再吸収されないよう block / separate を記録する
- `Block identifier`: 指定 identifier を元 project から除去し、今後戻らないよう `blocked_identifiers` に記録する

同じ project 詳細では、補助情報として `blocked_identifiers` / `separate_project_keys` を折り畳み表示し、不要になった guard を `Clear` できます。`merge_recommendation` が auto-ready の場合は、project 詳細から recommendation target への merge も実行できます。

`Merge` タブでは `merge-batch --interactive` に近い確認作業ができます。`build_batch_merge_plan()` の結果を `moderate` / `strict` / `broad` で表示し、1件ずつ `Merge`、`Block pair`、`Ignore source`、`Ignore target`、`Skip` を選べます。`Skip` はブラウザ上だけの非破壊操作ですが、それ以外は SQLite に書き込みます。実DBではなく、まず `data/survey.dev.db` のようなコピーDBで試す前提です。

`Sync` タブでは、`sync-introduced`、`sync-collections`、`sync-github-search`、`sync-semble-collections` 相当の background job を開始できます。既定は dry-run で、未知候補の project 作成はチェック済みです。SQLite へ書き込む場合は `Execute writes to SQLite` を明示的に有効にして確認ダイアログを通します。外部 API を呼ぶため、実 DB で execute する前にコピー DB で結果を確認してください。

同じ `Sync` タブの `Enrich All` では、`enrich --all` 相当の background job を開始できます。既定の対象は CLI と同じく `active` / `watchlist` で、必要に応じて `stable` / `stalled` / `archived` / `ignore` を含められます。`Max projects` は空欄なら無制限で、数字を入れた時だけ対象数を制限します。run id / checkpoint path を指定でき、空欄なら `data/enrich-runs/<run_id>.json` に保存します。Jobs では同じサーバーの in-memory job log と raw JSON を別ブラウザからも確認でき、実行中の `enrich_all` に停止要求を出せます。この停止は現在の project が終わった後に checkpoint を `interrupted` として保存し、`Resume run id` から再開できます。各 project は個別に commit され、job JSON に進捗、成功数、失敗数、直近の結果、checkpoint 情報、`elapsed` / `eta`、`logs` が表示されます。

`repair-db` と bulk tracking-state execute は Web UI からは実行しません。

`Settings` タブでは、ブラウザ側に保存する操作設定を変更できます。

- review / review ops / promotion / merge の表示件数
- merge mode
- promotion queue に personal tools を含めるか
- 単体 `enrich` / deep enrich に渡す LLM endpoint / model / API style / reasoning effort / API key

未入力の LLM 設定は、従来通り `ATPROTO_SURVEY_LLM_*` 環境変数へフォールバックします。Settings に入力した API key はそのブラウザの localStorage に保存されます。

実 DB を更新する前は、別の `enrich` や SQLite ブラウザアプリが同じ DB を触っていないか確認してください。DB lock が起きた場合、Web API は `503` の JSON error を返します。

seedを1件登録:

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://tangled.sh/@example/app/tree/main/lexdocs/example
python3 scripts/project_registry.py add-seed https://example.com
```

`add-seed` は `tangled.sh` や GitHub の `blob/tree` URL を repo root に正規化します。`https://lexicon.garden/lexicon/.../collection.nsid` や `pdsls` の `at://.../collection` URL も `nsid` seed として受け付けます。

1件調査:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

全件調査:

```bash
python3 scripts/project_registry.py enrich --all
python3 scripts/project_registry.py enrich --all --new-only
```

`enrich --all` は既定で `data/enrich-runs/<run_id>.json` に checkpoint を書きます。各 project は個別 commit され、Ctrl+C で止めても Python traceback にはせず、終了コード `130` と再開用 command を JSON lines に出します。

```bash
python3 scripts/project_registry.py enrich --all --run-id weekly-20260606
python3 scripts/project_registry.py enrich --all --resume-run weekly-20260606
```

再開時は最初に選ばれた project key のうち完了済みを飛ばします。LLM API key の値は checkpoint に保存しないため、必要な環境では再開時にも指定してください。

`stable` や、立ち消え気味で低頻度追跡に落とした `stalled` も回したい場合:

```bash
python3 scripts/project_registry.py enrich --all --include-stable
python3 scripts/project_registry.py enrich --all --include-stalled
```

Bluesky 検索から紹介済み判定を同期:

```bash
python3 scripts/project_registry.py sync-introduced
python3 scripts/project_registry.py sync-introduced --execute
```

`sync-introduced` は seed を追加せず、既存 project に対して `introduced_url` を付けるためだけに使います。

URL field の利用数、alias 重複、保存済み invariant 違反を読み取り専用で棚卸し:

```bash
python3 scripts/project_registry.py audit-url-fields
```

保存済み `record_json.app.links` を正本から再構築する場合:

```bash
python3 scripts/project_registry.py cleanup-app-links
python3 scripts/project_registry.py cleanup-app-links --execute
```

既定は dry-run です。URL heuristic を変更した後などに、stale な link を整理できます。

collection inventory APIから直接同期:

```bash
python3 scripts/project_registry.py sync-collections
python3 scripts/project_registry.py sync-collections --execute
python3 scripts/project_registry.py sync-collections --execute --lookback-days 10
```

`sync-collections` は API 全件を取得しますが、取り込み対象は既知collectionを除いた「`min` が直近 `--lookback-days` 日以内」のものだけです。
既知 collection についても、`count` / `recent_count` / `max` は activity refresh 用に見直します。これにより `latest_collection_activity_at` は「最後に新規 collection が生えた日」ではなく、既知 collection を含めた最近の record 活動に近い値になります。`tracking_state=stalled` の project でも、ここで最近の活動が見えれば `active` に戻せます。
未知候補は既定で project として作成します。棚卸しだけ行う場合は `--no-create-unmatched` を付けます。

GitHub repository search から repo seed を同期:

```bash
python3 scripts/project_registry.py sync-github-search
python3 scripts/project_registry.py sync-github-search --execute
```

`sync-github-search` は `config/sources.json` の `github_repo_search` を使い、既知 repo を除いた「`updated_at` が直近 `--lookback-days` 日以内」の未知 repo だけを対象にします。query には既定で `fork:false` と `archived:false` を足します。さらに `--screen-unmatched` を付けたときは、GitHub 上で fork と判定された repo のうち、独立した homepage / service surface / owned lexicon surface が見えないものを新規 project 候補から落とします。

1回だけ広く取りたい時は、まず dry-run で lookback を外し、未マッチ repo に対して README/tree/lexicon を見たスクリーニングをかけます。

```bash
python3 scripts/project_registry.py sync-github-search \
  --all-history \
  --screen-unmatched \
  --pages 10 \
  --per-page 100
```

問題なければ `--execute` を付けます。`--screen-unmatched` は atproto 関連シグナルが無い repo を DB に作らず、その場で `screened_out_entries` に落とします。

手元メモのような見出し付きテキストからまとめて seed を取り込む:

```bash
python3 scripts/project_registry.py import-seed-list tmpdata.txt
python3 scripts/project_registry.py import-seed-list tmpdata.txt --execute
```

`import-seed-list` は GitHub/Tangled/service URL/`pdsls`/`lexicon.garden` を seed に正規化します。`pdsls` の record URL からは collection NSID を拾い、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。

DB直接編集後の整合性回復:

```bash
python3 scripts/project_registry.py repair-db
python3 scripts/project_registry.py repair-db --execute
```

追跡頻度を見直す候補の確認:

```bash
python3 scripts/project_registry.py review-stalled
python3 scripts/project_registry.py review-stalled --inactivity-days 60 --enrich-age-days 45
python3 scripts/project_registry.py review-ignore
python3 scripts/project_registry.py review-ignore --execute
```

`review-stalled` は `active/watchlist` を `stalled` に落とす候補だけでなく、最近 activity が戻った `stalled` を `active` に戻す候補や、collection だけ動いているものを `stable` に落とす候補も出します。repo 更新日時と collection activity の両方を見ていて、どちらか一方でも最近動いていれば `active` のまま残します。

`review-ignore` は別系統で、独立した website / service / owned lexicon surface が見えない GitHub fork repo と、protocol surface を持たない低 novelty の Bluesky 従属ツールを `ignore` 候補として出します。まず dry-run で確認してから `--execute` を付ける前提です。

次に紹介する候補の確認:

```bash
python3 scripts/project_registry.py promotion-queue
python3 scripts/project_registry.py promotion-queue --kinds service_ready_for_introduction
python3 scripts/project_registry.py promotion-queue --include-personal-tools
python3 scripts/project_registry.py promotion-queue --kinds lexicon_ready_for_introduction --interactive
```

`promotion-queue` は、未紹介 (`introduced_url` なし) で

- 利用可能なサービス (`live` / `beta` / `invite_only`)
- 共有準備済みと見なせる `lexicon` project

を別 signal で一覧化します。`personal_tool` は既定では除外し、`--include-personal-tools` を付けた時だけ含めます。

誤って付いた `service_url` / `repo_url` / `nsid` を今後も戻さないようにしたい場合:

```bash
python3 scripts/project_registry.py block-identifier --project-key proj:xxxx --identifier-type service_url --value https://example.com
```

恒久 block ではなく 30 日だけ candidate 相当に降格して様子を見る場合:

```bash
python3 scripts/project_registry.py defer-identifier --project-key proj:xxxx --identifier-type service_url --value https://example.com --days 30
```

曖昧な自然文から DB 修正コマンド案を出したい場合:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい'
python3 scripts/project_registry.py assist-db-edit 'proj:aaaa と proj:bbbb をマージしたい'
```

一意に解決できた時だけ、そのまま反映することもできます:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい' --execute
```

TTY 上で一意に解決できた場合は、`--execute` なしでもその場で確認プロンプトを出します。ローカル LLM が設定されていればそれで解釈し、未設定でも `proj:` / URL / NSID が明示されているケースは heuristic である程度動きます。

## 環境変数

実装上の環境変数は次で全部です。

command方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
```

endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_API_KEY='...'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
export ATPROTO_SURVEY_LLM_PROMPT_VERSION='project-triage-v1'
export ATPROTO_SURVEY_LLM_REASONING_EFFORT='none'
```

Bluesky post検索を使う場合:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
export BSKY_AUTH_HOST='https://bsky.social'
```

GitHub検索の rate limit を避けたい場合:

```bash
export GITHUB_TOKEN='ghp_...'
```

`DNS_RESOLVER_URL` や `UFOS_API` などは現状コード定数で、環境変数では上書きしません。

`thinking` / reasoning effort を切り替えたい場合は `ATPROTO_SURVEY_LLM_REASONING_EFFORT`、または CLI の `--llm-reasoning-effort` を使います。例えば:

```bash
python3 scripts/project_registry.py enrich --all --llm-reasoning-effort none
python3 scripts/project_registry.py enrich --all --new-only --llm-reasoning-effort high
```

## 保存形式

`projects` テーブルは現在、次を保持します。

- scalar columns
  - 一覧や絞り込みで多用する値
- `record_json`
  - 高信頼の project record
- `ops_json`
  - 候補、調査、LLM 出力、merge 制御、activity などの運用情報

`data_json` はもう読み書きしません。移行完了後は schema からも外しています。`record_json` は「確信のあるものだけ」を入れ、判断が難しい URL / name / NSID は原則 `ops_json` の candidate 側へ退避します。

## 文書

利用者向け:

- [docs/project-registry.md](docs/project-registry.md)
- [docs/project-record.md](docs/project-record.md)

運用・設計向け:

- [docs/registry-internals.md](docs/registry-internals.md)
- [docs/roadmap.md](docs/roadmap.md)
- [docs/thread-handoff.md](docs/thread-handoff.md)

実装:

- [scripts/project_registry.py](scripts/project_registry.py)
- [prompts/project-triage.md](prompts/project-triage.md)
