# Project Registry

手動で見つけたseedを登録し、project recordとして追跡するための操作ガイドです。

内部実装や昇格ロジック、`enrich` / `sync` / `merge` / `review-stalled` の判定材料、外部アクセス量の詳細は [docs/registry-internals.md](registry-internals.md) に分けています。

## 扱うseed

- `nsid`
- `repo_url`
- `service_url`

次も `nsid` seed として受け付けます。

- `https://pdsls.dev/at://did.../collection[/rkey]`
- `https://pds.ls/at://did.../collection[/rkey]`
- `https://lexicon.garden/lexicon/did.../collection.nsid`
- `at://did.../collection[/rkey]`

## 基本フロー

1. seedを登録する
2. `enrich` で調査する
3. `show` で状態を見る
4. 必要なら `merge` や `set-tracking-state` を使う

## 環境準備

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

`tldextract` で PSL を引きます。入力は repo 内の `vendor/public_suffix_list.dat` に固定し、ライブラリの cache は `data/tldextract-cache/` 配下に置きます。

PSL に載っていない hosted suffix を追加したい場合は、[config/sources.json](../config/sources.json) の `custom_multi_tenant_suffixes` に suffix を足します。`example.com` を追加すると、`foo.example.com` と `bar.example.com` は別 family 扱いになります。

## zsh補完

wrapper の [bin/project-registry](../bin/project-registry) と zsh completion の [completions/_project-registry](../completions/_project-registry) を使えます。

```bash
export PATH="$PWD/bin:$PATH"
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

これで `project-registry` の subcommand と主要オプションが補完されます。`scripts/` を `PATH` に足すと Python ファイル自体までコマンド候補に出るので、`PATH` には `bin/` だけを足す前提にしています。completion 定義は `project_registry.py` にも紐付けているので、`python3 scripts/project_registry.py ...` を直接呼ぶ場合も zsh 上では同じ候補が出ます。

## seed登録

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://github.com/example/project
python3 scripts/project_registry.py add-seed https://github.com/example/project/blob/main/lexicons/app/example/post.json
python3 scripts/project_registry.py add-seed https://tangled.sh/@example/app/tree/main/lexdocs/example
python3 scripts/project_registry.py add-seed https://example.com
```

GitHub の `blob/tree` URL や `tangled.sh` の tree URL は repo root に正規化します。

既存projectへ追加する場合:

```bash
python3 scripts/project_registry.py add-seed https://example.com --project-key 2f2acb
```

`project_key` は `proj:xxxxxxxxxxxx` 形式ですが、CLIでは `proj:` を省略した prefix でも指定できます。

## 調査

1件だけ:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
python3 scripts/project_registry.py enrich --project-key 2f2acb
```

全件:

```bash
python3 scripts/project_registry.py enrich --all
python3 scripts/project_registry.py enrich --all --new-only
```

`stable` / `stalled` / `archived` / `ignore` も回したい場合:

```bash
python3 scripts/project_registry.py enrich --all --include-stable
python3 scripts/project_registry.py enrich --all --include-stalled
python3 scripts/project_registry.py enrich --all --include-archived
python3 scripts/project_registry.py enrich --all --include-ignore
```

## 表示

```bash
python3 scripts/project_registry.py show --project-key 2f2acb
python3 scripts/project_registry.py show --seed sh.tangled.repo --type nsid
python3 scripts/project_registry.py show --all --format json
```

## 紹介済み候補の取り込み

Bluesky 検索から紹介済み判定だけを同期する:

```bash
python3 scripts/project_registry.py sync-introduced
python3 scripts/project_registry.py sync-introduced --execute
python3 scripts/project_registry.py sync-introduced --query 'from:yamarten.bsky.social #ATプロトーク'
```

このコマンドは seed を追加しません。既存 project に一致したものだけ `introduced_url` を更新します。一致しなかった URL は `unmatched_entries` に出すだけです。

見出しや注記が混ざったテキスト seed list を取り込む:

```bash
python3 scripts/project_registry.py import-seed-list tmpdata.txt
python3 scripts/project_registry.py import-seed-list tmpdata.txt --execute
```

このコマンドは GitHub/Tangled/service URL/`pdsls`/`lexicon.garden` を seed に正規化します。`pdsls` の record URL は collection NSID に落とし、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。`Echosky: ...` のように注記の直後に複数 seed が並ぶ場合は、ラベルと seed が十分近い時だけ同一 project へ束ねます。

## collection inventory APIの同期

設定済みURL、または指定URLから直接取り込む:

```bash
python3 scripts/project_registry.py sync-collections
python3 scripts/project_registry.py sync-collections --execute --create-unmatched
python3 scripts/project_registry.py sync-collections --source-url https://collectiondata.usounds.work/collection_count_view
```

このコマンドは API 全件を取得しますが、既定では既知collectionを再取り込みしません。`min` が直近 `10` 日以内の未知collectionだけを対象にします。
一方で、既知 collection の activity 情報自体は毎回見直します。`count` / `recent_count` / `max` を既存 project の `collection_observations` に反映するので、最近 record が流れている stalled project を active に戻す判断にも使えます。

また、sync/import 時の既存 project への自動ひも付けは conservative です。`exact_namespace` 以外の弱い一致は既存 project に吸わず、`suggested_project_key` として提案だけ残します。

週次運用向けに、必要ならlookbackを調整できます:

```bash
python3 scripts/project_registry.py sync-collections --execute --create-unmatched --lookback-days 10
python3 scripts/project_registry.py sync-collections --execute --create-unmatched --lookback-days 14
```

必要なら取得したJSON payloadを保存できます:

```bash
python3 scripts/project_registry.py sync-collections --save-path data/collection_inventory.latest.json
```

## GitHub repository searchの同期

設定済み query、または指定 query から未知 repo を取り込む:

```bash
python3 scripts/project_registry.py sync-github-search
python3 scripts/project_registry.py sync-github-search --execute --create-unmatched
python3 scripts/project_registry.py sync-github-search --query 'atproto OR "AT protocol" OR atprotocol'
```

このコマンドは既知 repo を再取り込みしません。`updated_at` が直近 `--lookback-days` 日以内の未知 repo だけを対象にします。

GitHub sync でも `homepage_family` のような弱い一致では既存 project に自動で吸わず、suggestion 扱いにします。

```bash
python3 scripts/project_registry.py sync-github-search --execute --create-unmatched --lookback-days 10
python3 scripts/project_registry.py sync-github-search --pages 3 --per-page 100
```

1回だけ広く取得したい場合は、lookback を外して dry-run し、未マッチ repo に対して repo metadata / README / tree / lexicon hint を見た保存前スクリーニングをかけます。

```bash
python3 scripts/project_registry.py sync-github-search \
  --all-history \
  --create-unmatched \
  --screen-unmatched \
  --pages 10 \
  --per-page 100
```

この時、`screened_out_entries` に落ちたものは DB に作りません。問題なければ `--execute` を付けて本実行します。

## merge

recommendationを見るだけ:

```bash
python3 scripts/project_registry.py merge \
  --project-key 2f2acb \
  --use-recommendation
```

seed 同士を直接指定する場合:

```bash
python3 scripts/project_registry.py merge \
  --seed ink.henry.saunter.walk --type nsid \
  --target-seed https://saunter.henry.ink --target-type service_url
```

repo URL や service URL を target にしてもよいです:

```bash
python3 scripts/project_registry.py merge \
  --seed https://github.com/example/app --type repo_url \
  --target-seed https://example.com --target-type service_url
```

実行:

```bash
python3 scripts/project_registry.py merge \
  --project-key 2f2acb \
  --use-recommendation \
  --execute
```

一括候補の確認:

```bash
python3 scripts/project_registry.py merge-batch
python3 scripts/project_registry.py merge-batch -v
```

特定 pair だけ根拠を確認する:

```bash
python3 scripts/project_registry.py inspect-merge-pair \
  --project-key proj:aaaa1111bbbb \
  --target-project-key proj:cccc2222dddd
```

seed 指定でも確認できます:

```bash
python3 scripts/project_registry.py inspect-merge-pair \
  --seed app.diffdown.comment --type nsid \
  --target-seed com.diffdown.document --target-type nsid
```

立ち上げ時の暫定措置として、`listReposByCollection` の先頭 DID と alias 一致を使う heuristic を有効にする場合:

```bash
python3 scripts/project_registry.py merge-batch \
  --mode moderate \
  --bootstrap-collection-creator-heuristic \
  -v
```

これは常用向けではありません。`com.atproto.sync.listReposByCollection` の先頭 DID を「最初の record 作成者」とみなす経験則に依存するため、まず dry-run で中身を確認してから使います。

pair 単位でその heuristic だけを確認したい場合:

```bash
python3 scripts/project_registry.py inspect-merge-pair \
  --seed app.diffdown.comment --type nsid \
  --target-seed com.diffdown.document --target-type nsid \
  --bootstrap-collection-creator-heuristic
```

## tracking state

継続調査対象の切り替え:

```bash
python3 scripts/project_registry.py set-tracking-state \
  --project-key 2f2acb \
  --tracking-state stable \
  --note "安定運営"
```

`stalled` は、立ち消え気味で追跡頻度を下げたいが `archived` にするほど確定ではないもの向けです。

候補の自動判定を見る:

```bash
python3 scripts/project_registry.py review-stalled
python3 scripts/project_registry.py review-stalled --inactivity-days 60 --enrich-age-days 45
```

このコマンドは 3 種類の提案を返します。

- `suggested_tracking_state=stalled`
  - repo と collection の両方が長く止まっている、または `dead/private` が見えている
- `suggested_tracking_state=stable`
  - collection は最近動いているが repo 側は止まっている
- `suggested_tracking_state=active`
  - いま `stalled` だが、repo または collection の最近 activity が見えた

repo 更新日時と collection activity のどちらか一方でも最近動いていれば、少なくとも `stalled` には落としません。

## 紹介候補キュー

未紹介で、人が見る価値のありそうな project を一覧化します。

```bash
python3 scripts/project_registry.py promotion-queue
python3 scripts/project_registry.py promotion-queue --kinds service_ready_for_introduction
python3 scripts/project_registry.py promotion-queue --include-personal-tools
python3 scripts/project_registry.py promotion-queue --kinds lexicon_ready_for_introduction --interactive
```

signal は今のところ 2 種類です。

- `service_ready_for_introduction`
  - 既定では `category=project` で、`service_status` が `live` / `beta` / `invite_only`
- `lexicon_ready_for_introduction`
  - `category=lexicon` で、NSID ヒントと公開先 (`website_url` / repo / resource) があり、紹介用メタデータが大きく欠けていない

`category=personal_tool` は既定では除外します。含めたい場合だけ `--include-personal-tools` を付けます。

`--interactive` を付けると `review-queue` と同じく 1 件ずつ見られます。`suggested_commands` には `show` や `enrich --deep-investigation` を出します。

適用する場合:

```bash
python3 scripts/project_registry.py review-stalled --execute
```

同じ creator DID や NSID prefix にぶら下がる project をまとめて切り替える場合:

```bash
python3 scripts/project_registry.py set-tracking-state-batch \
  --record-did did:plc:ie77niyfcztulkf2oa7p4adz \
  --tracking-state ignore

python3 scripts/project_registry.py set-tracking-state-batch \
  --record-did did:plc:ie77niyfcztulkf2oa7p4adz \
  --current-tracking-states active stalled \
  --tracking-state ignore \
  --note "bulk ignore noisy synthetic NSIDs" \
  --execute
```

## merge recommendation の手動抑止

この 2 件は別物として扱いたい、というときは相互に block できます:

```bash
python3 scripts/project_registry.py set-separate \
  --project-key proj:aaaa1111bbbb \
  --other-project-key proj:cccc2222dddd
```

解除する場合:

```bash
python3 scripts/project_registry.py set-separate \
  --project-key proj:aaaa1111bbbb \
  --other-project-key proj:cccc2222dddd \
  --clear
```

これを入れると `duplicate_candidates` と `merge_recommendation` の計算自体からその pair を外すので、次の `enrich` でも復活しません。

## 誤った identifier の手動矯正

project 自体は合っているが、誤った `service_url` / `repo_url` / `nsid` が混ざった場合は、その identifier を project 単位で block できます:

```bash
python3 scripts/project_registry.py block-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com
```

解除する場合:

```bash
python3 scripts/project_registry.py block-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com \
  --clear
```

この block は `finalize` / `enrich` のたびに適用されるので、次回の再調査でも同じ identifier はその project に戻りません。

## 誤マージの分割

誤って合体した project から、1 本の `nsid` / `service_url` / `repo_url` を新 project に切り出す場合:

```bash
python3 scripts/project_registry.py split-project \
  --project-key proj:aaaa1111bbbb \
  --identifier-type nsid \
  --value com.example.app.post
```

実行時は `--execute` を付けます。

このコマンドは:

- 指定 identifier を新 project に seed として移す
- 元 project からその identifier を除去する
- 元 project に block を入れて再付着を防ぐ
- 元 project と新 project を `set-separate` 相当で相互分離する

という処理をまとめて行います。複数アプリが混ざっている場合は、必要な identifier ごとに数回に分けて切り出します。

## 自然文からの DB 修正補助

短い自然文から、既存 CLI の具体コマンド案を出す:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい'
python3 scripts/project_registry.py assist-db-edit 'proj:aaaa と proj:bbbb をマージしたい'
python3 scripts/project_registry.py assist-db-edit 'proj:xxxx から https://example.com をブロックしたい'
```

現状サポートする action は:

- `merge`
- `set_separate`
- `block_identifier`
- `set_tracking_state`

一意に project 解決できた場合だけ、そのまま反映もできます:

```bash
python3 scripts/project_registry.py assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい' --execute
```

TTY 上で一意に解決できた場合は、`--execute` なしでもその場で確認プロンプトを出します。ローカル LLM が設定されていればそれで解釈します。未設定でも `proj:` / URL / NSID が明示されているケースは heuristic で解釈します。曖昧な場合は候補一覧と `suggested_command` だけ返し、実行しません。

## DBを直接編集した後

GUIやSQLで `projects` を直接編集した後は、正規化と派生データ再計算のためにこれを回します。

```bash
python3 scripts/project_registry.py repair-db
```

## storage layout の移行

現在の `projects` テーブルは次を保持します。

- scalar columns
- `record_json`
- `ops_json`

`record_json` は高信頼の project record、`ops_json` は候補・調査・LLM・merge 制御などの運用情報です。迷うものは `record_json` に昇格させず、まず `ops_json` に残します。

個別 project がどう分かれるか確認する:

```bash
python3 scripts/project_registry.py preview-storage-layout --project-key 2f2acb
python3 scripts/project_registry.py preview-storage-layout --seed sh.tangled.repo --type nsid
```

全体 preview:

```bash
python3 scripts/project_registry.py migrate-storage-layout --phase preview --limit 10
python3 scripts/project_registry.py migrate-storage-layout --phase finalize-check --limit 10
```

`finalize-check` は「未分類の legacy field」が残っていないかだけを確認します。`introduced` や `primary_nsid` のように意図的に drop する field は別枠です。

`record_json` / `ops_json` を DB に backfill する:

```bash
python3 scripts/project_registry.py migrate-storage-layout --phase backfill
python3 scripts/project_registry.py migrate-storage-layout --phase backfill --execute
```

旧 `data_json` の中身を空に揃え、retired column を schema から落とす:

```bash
python3 scripts/project_registry.py migrate-storage-layout --phase retire-legacy-data --execute
python3 scripts/project_registry.py migrate-storage-layout --phase cleanup-schema --execute
```

### `record_json`

- `core`
  - `summary`, `tagline`, `manual_seeds`, `brand_did`, `source_code_url`, `lexicon_namespaces`, `notes`
- `app`
  - app export 向けの確かな metadata
  - 現時点では `tags`, `platforms`, `links`
  - `links` は role を確信できる URL だけを保持する
  - 情報が無ければ空 object のまま保持する

### `ops_json`

- `ops`
  - review / merge / 紹介運用の制御情報
- `evidence`
  - URL 候補、repo 観測、NSID 解決、LLM 出力、分類補助の材料

## 環境変数

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

現状、環境変数として読むのは上記だけです。`DNS_RESOLVER_URL` や `UFOS_API` はコード定数で、環境変数オーバーライドはありません。

`thinking` / reasoning effort を切り替えたい場合は `ATPROTO_SURVEY_LLM_REASONING_EFFORT`、または CLI の `--llm-reasoning-effort` を使います。例えば:

```bash
python3 scripts/project_registry.py enrich --all --llm-reasoning-effort none
python3 scripts/project_registry.py enrich --all --new-only --llm-reasoning-effort high
```
