# Project Registry

手動で見つけたseedを登録し、project recordとして追跡するための操作ガイドです。

内部実装や昇格ロジックの詳細は [docs/registry-internals.md](registry-internals.md) に分けています。

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

`applog.json` から紹介済みURLを取り込む:

```bash
python3 scripts/project_registry.py import-applog applog.json
python3 scripts/project_registry.py import-applog applog.json --execute
python3 scripts/project_registry.py import-applog applog.json --execute --create-unmatched
```

Bluesky 検索から紹介済み判定だけを同期する:

```bash
python3 scripts/project_registry.py sync-introduced
python3 scripts/project_registry.py sync-introduced --execute
python3 scripts/project_registry.py sync-introduced --query 'from:yamarten.bsky.social #ATプロトーク'
```

このコマンドは seed を追加しません。既存 project に一致したものだけ `introduced=true` と `introduced_url` を更新します。一致しなかった URL は `unmatched_entries` に出すだけです。

見出しや注記が混ざったテキスト seed list を取り込む:

```bash
python3 scripts/project_registry.py import-seed-list tmpdata.txt
python3 scripts/project_registry.py import-seed-list tmpdata.txt --execute
```

このコマンドは GitHub/Tangled/service URL/`pdsls`/`lexicon.garden` を seed に正規化します。`pdsls` の record URL は collection NSID に落とし、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。`Echosky: ...` のように注記の直後に複数 seed が並ぶ場合は、ラベルと seed が十分近い時だけ同一 project へ束ねます。

## 既知collection一覧の取り込み

`collection_count_view` 由来JSONを取り込む:

```bash
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json"
python3 scripts/project_registry.py import-collections "scripts/collectiondata.usounds.work collection_count_view.json" --execute --create-unmatched
```

このimportはproject粒度で集約して取り込みます。新規作成時は後段のlexicon解決用に代表のfull NSIDも保持します。

## collection inventory APIの同期

設定済みURL、または指定URLから直接取り込む:

```bash
python3 scripts/project_registry.py sync-collections
python3 scripts/project_registry.py sync-collections --execute --create-unmatched
python3 scripts/project_registry.py sync-collections --source-url https://collectiondata.usounds.work/collection_count_view
```

このコマンドは API 全件を取得しますが、既定では既知collectionを再取り込みしません。`min` が直近 `10` 日以内の未知collectionだけを対象にします。

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

## DBを直接編集した後

GUIやSQLで `projects` を直接編集した後は、正規化と派生データ再計算のためにこれを回します。

```bash
python3 scripts/project_registry.py repair-db
```

## LLM設定

command方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
```

endpoint方式:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
```

Bluesky post検索を使う場合:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
```
