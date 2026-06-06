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

既存 DB を追加後の基準で確認しながら追記する場合:

```bash
python3 scripts/project_registry.py audit-source-config example.com
```

このコマンドは既定で interactive です。suffix が未登録なら `sources.json` への追記を確認し、追加後の family 基準で `split_suggestions` を表示します。各 suggestion には tenant ごとの service URL / NSID、曖昧な repo、仮分割後の merge candidate、既存 `split-project` の dry-run コマンドが含まれます。

JSON dry-run のみ:

```bash
python3 scripts/project_registry.py audit-source-config example.com --no-interactive
```

確認なしで suffix 追記まで行う場合:

```bash
python3 scripts/project_registry.py audit-source-config example.com --execute --no-interactive
```

interactive では tenant group の番号を選んだ場合だけ、その group の service URL / NSID を 1 つの新 project へまとめて切り出します。元 project には block と `set-separate` 相当の相互分離を残します。`--execute --no-interactive` では split を自動適用しません。共有 repo、共有 lexicon、URL 移転などの例外があるため、分割は常に suggestion 確認後に行います。

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

## Web UI

モバイル端末などから review 作業を進めたい場合は、標準ライブラリだけで動く Web UI を起動できます。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
python3 scripts/project_registry_web.py --host 127.0.0.1 --port 8765
```

LAN 内の別端末から使う場合は `--host 0.0.0.0` で bind します。`/api/*` は共有トークン必須なので、起動時に表示される `?token=...` 付き URL を最初に開くか、`Authorization: Bearer ...` / `X-Project-Registry-Token` を付けてアクセスします。

worktree から起動しつつ普段使いの DB を参照したい場合は `--db-path` を明示します。

```bash
python3 scripts/project_registry_web.py \
  --host 127.0.0.1 \
  --port 8765 \
  --db-path data/survey.db \
  --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

開発中は、ブラウザ側の許可を繰り返さないようコピーDBと固定ポートを使います。

```bash
python3 scripts/project_registry_web.py \
  --host 127.0.0.1 \
  --port 8769 \
  --db-path data/survey.dev.db \
  --token codex-dev-token \
  --reload
```

`--reload` は `scripts/project_registry_web.py` / `scripts/project_registry.py` の変更を検知して、同じポートのままサーバプロセスを再起動します。開発中にポート解放のためだけに `lsof` / `kill` を使う必要を減らせます。

長時間起動する本番向けプロセスでは、`Settings` タブで現在の PID を確認できます。`Reload server` は `POST /api/reload` により同じコマンドライン引数でプロセスを `execv` し直し、`Stop server` は `POST /api/shutdown` により HTTP server を停止します。どちらも DB は変更しません。ただし、このプロセス内で走っている background job は継続しません。

v1 でできること:

- `review-queue` / `promotion-queue` の確認
- `review-stalled` / `review-ignore` 相当の dry-run と明示 execute
- project 詳細と raw JSON の確認
- `tracking_state` の単体更新
- review signal の dismissal
- service / repo candidate の承認・拒否
- 既存 project への seed 追加
- 新規 seed の preview / duplicate 確認 / 登録
- 単体 `enrich` / deep enrich の background job 起動
- `enrich --all` 相当の background job 起動
- `merge-batch --interactive` 相当の merge plan 確認と pair 単位の実行
- project 詳細からの任意 identifier block / split
- project 詳細からの block / separate guard clear
- project 詳細からの auto-ready merge recommendation 実行

### project 検索

`Search` タブは自由語と構造化 filter を AND 条件で組み合わせます。現在の実装は SQLite の全文検索ではなく、全 project を読み込んで filter を掛け、自由語がある場合だけ `project_search_candidates()` で候補を採点する軽量検索です。

現在検索対象にしているもの:

- `project_key`
- `seed_value`
- `name`
- merge alias
- `primary_nsid`
- `input_nsids`
- `resolved_nsids`
- `website_url`
- `service_urls`
- `source_code_urls`

exact match、prefix match、substring match、alias match を加点し、score の高い順、同点なら `project_key` 順で返します。

`Filters` では tracking state、service status、ATProto contribution level、technical interest level、record status、category、紹介済みか、candidate URL が残っているか、run error があるか、active な promotion hold があるか、review signal kind、last seen、last enriched、sort を指定できます。filter panel はモバイルで場所を取りすぎないよう初期状態では閉じています。

自由語欄では短い演算子も使えます。演算子は browser 側で構造化 filter に変換され、API には通常の query parameter として送られます。未認識の `key:value` は URL などを壊さないよう自由語として扱います。

```text
state:active,watchlist introduced:no candidate:yes whisper
status:unverified error:yes
contribution:core,substantial technical:high,exceptional promotion-hold:no
review:service_candidate_needs_confirmation
seen:>=2026-05-01 enriched:<2026-05-31
```

API は `GET /api/projects` の `q`, `tracking_states`, `service_statuses`, `contribution_levels`, `technical_interest_levels`, `statuses`, `categories`, `introduced`, `has_candidates`, `has_errors`, `promotion_hold`, `review_kinds`, `seen_since`, `enriched_before`, `sort`, `limit` を受け付けます。

実装済みですが、大規模 DB 向けの SQL 最適化、FTS、OR 条件、保存済み検索条件、pagination は未接続です。後続候補は [docs/roadmap.md](roadmap.md) の「Web UI 詳細検索」を参照してください。

`Merge` タブでは `build_batch_merge_plan()` の結果を `moderate` / `strict` / `broad` で表示し、1件ずつ次の操作を選べます。

- `Merge`: source を target へ merge し、source project を削除する
- `Block pair`: 双方の `separate_project_keys` に相手を記録する
- `Ignore source`: source の `tracking_state` を `ignore` にする
- `Ignore target`: target の `tracking_state` を `ignore` にする
- `Skip`: 画面上だけで候補を隠す。DB は変更しない

`Merge` / `Block pair` / `Ignore source` / `Ignore target` は SQLite に書き込みます。実 DB で使う前に、`data/survey.dev.db` のようなコピー DB で操作感を確認する前提です。

`Review Ops` タブでは、review 系の CLI 相当処理を background job として開始できます。

- `review-stalled`: inactivity / enrich age 条件から `active` / `watchlist` / `stalled` の tracking_state 更新候補を出す
- `review-ignore`: 独自性の薄い fork や派生 Bluesky utility の `ignore` 候補を出す

既定は dry-run です。SQLite へ書き込む場合は `Execute writes to SQLite` を明示的に有効にし、確認ダイアログを通します。job JSON の `logs` には、候補選択、更新、失敗理由の時系列を残します。現状のログは in-memory job に紐づく表示で、サーバ再起動後の履歴永続化は未接続です。

`Jobs` タブでは、この browser tab で開始した background job の直近ログと raw JSON を確認できます。実行中 job は polling で更新されますが、展開中の job 詳細と `Raw JSON` は開いた状態を保持します。

`New Seed` タブでは、NSID / repo URL / service URL を preview してから登録できます。`/?seed=https%3A%2F%2Fexample.com` のように URL query で seed を渡すと、この画面を直接開きます。exact duplicate がある場合は既存 project を開き、近い候補がある場合は登録前に候補を表示します。

project 詳細の `Project Edits` では、`nsid` / `repo_url` / `service_url` と値を指定して次の操作を実行できます。

- `Split`: 指定 identifier を新規 project へ移し、元 project から除去して `blocked_identifiers` と `separate_project_keys` を記録する
- `Defer 30d`: 指定 identifier を一時保留する。confirmed URL は candidate に降格し、期限内の自動 confirm を抑制する
- `Block identifier`: 指定 identifier を元 project から除去し、`blocked_identifiers` に記録する

補助情報として `blocked_identifiers`、`deferred_identifiers`、`separate_project_keys` は `Guards` に折り畳み表示します。confirmed / candidate row からも `Defer 30d` を選べます。`Clear` / `Clear defer` は guard record だけを外す操作で、identifier を自動で再付与したり confirm したり project を merge したりはしません。`Merge Recommendation` は auto-ready な recommendation だけを project 詳細から実行できます。non-auto-ready recommendation や force merge は未接続です。

project 詳細の `Research` では、`atproto_contribution` と `technical_interest` の level / score を常時表示し、evidence、reasons、summary、`promotion_blockers`、`resource_url_records` を折り畳み表示します。これは既存 payload の閲覧導線で、Web UI から調査結果を編集する操作は未接続です。

`Sync` タブでは、次の CLI 相当の処理を background job として開始できます。

- `sync-introduced`
- `sync-collections`
- `sync-github-search`
- `sync-semble-collections`

既定は dry-run です。未知候補の project 作成はチェック済みで、必要なら外せます。SQLite へ書き込む場合は `Execute writes to SQLite` を明示的に有効にし、確認ダイアログを通します。外部 API を呼び、実行時間も DB 更新範囲も大きくなり得るため、まず `data/survey.dev.db` のようなコピー DB で dry-run / execute 結果を確認してください。

同じ `Sync` タブの `Enrich All` では、`enrich --all` 相当の処理を background job として開始できます。既定の対象 tracking_state は CLI と同じ `active` / `watchlist` です。`stable` / `stalled` / `archived` / `ignore` を含める option、`new only`、`retry failures only`、deep investigation、最大件数を指定できます。`Max projects` は空欄なら無制限で、数字を入れた時だけ対象数を制限します。各 project は個別に commit され、job JSON に進捗、直近の成功/失敗、`logs` が表示されます。

v1 では、`repair-db` と bulk tracking-state execute は実行しません。これらは引き続き CLI で dry-run を確認してから扱います。

将来の設計目標として、`assist-db-edit` を Web UI からも扱えるようにする余地があります。現時点では未接続ですが、自然文から merge / set-separate / block / tracking-state などの候補コマンドを提案するだけでなく、将来的には project の問題調査や根拠確認の入口として厚くする候補です。

`Settings` タブでは、Web UI の操作設定をブラウザ localStorage に保存できます。

- `review-queue` / review ops / `promotion-queue` / `merge-batch` の表示件数
- merge mode
- promotion queue に personal tools を含めるか
- 単体 `enrich` / deep enrich に渡す LLM endpoint / model / API key / API style / reasoning effort

LLM 設定を空にした場合は、従来通り `ATPROTO_SURVEY_LLM_*` 環境変数を使います。API key を Settings に入力した場合、そのブラウザの localStorage に保存されます。

実 DB に書き込む前は、別の `enrich` や SQLite ブラウザアプリが同じ DB を触っていないか確認してください。DB lock 時は Web API が `503` の JSON error を返します。

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
python3 scripts/project_registry.py sync-collections --execute
python3 scripts/project_registry.py sync-collections --source-url https://collectiondata.usounds.work/collection_count_view
```

このコマンドは API 全件を取得しますが、既定では既知collectionを再取り込みしません。`min` が直近 `10` 日以内の未知collectionだけを対象にします。
一方で、既知 collection の activity 情報自体は毎回見直します。`count` / `recent_count` / `max` を既存 project の `collection_observations` に反映するので、最近 record が流れている stalled project を active に戻す判断にも使えます。

未知候補は既定で新規 project にします。棚卸しだけ行い DB 候補を増やしたくない場合は
`--no-create-unmatched` を付けます。また、sync/import 時の既存 project への自動ひも付けは
conservative です。`exact_namespace` 以外の弱い一致は既存 project に吸わず、
`suggested_project_key` として提案だけ残します。

週次運用向けに、必要ならlookbackを調整できます:

```bash
python3 scripts/project_registry.py sync-collections --execute --lookback-days 10
python3 scripts/project_registry.py sync-collections --execute --lookback-days 14
```

必要なら取得したJSON payloadを保存できます:

```bash
python3 scripts/project_registry.py sync-collections --save-path data/collection_inventory.latest.json
```

## GitHub repository searchの同期

設定済み query、または指定 query から未知 repo を取り込む:

```bash
python3 scripts/project_registry.py sync-github-search
python3 scripts/project_registry.py sync-github-search --execute
python3 scripts/project_registry.py sync-github-search --query 'atproto OR "AT protocol" OR atprotocol'
```

このコマンドは既知 repo を再取り込みしません。`updated_at` が直近 `--lookback-days` 日以内の未知 repo だけを対象にします。query には既定で `fork:false` と `archived:false` を足します。

GitHub sync でも `homepage_family` のような弱い一致では既存 project に自動で吸わず、suggestion 扱いにします。

```bash
python3 scripts/project_registry.py sync-github-search --execute --lookback-days 10
python3 scripts/project_registry.py sync-github-search --pages 3 --per-page 100
```

1回だけ広く取得したい場合は、lookback を外して dry-run し、未マッチ repo に対して repo metadata / README / tree / lexicon hint を見た保存前スクリーニングをかけます。

```bash
python3 scripts/project_registry.py sync-github-search \
  --all-history \
  --screen-unmatched \
  --pages 10 \
  --per-page 100
```

この時、`screened_out_entries` に落ちたものは DB に作りません。現状は GitHub 上で fork と判定され、独立した homepage / service candidate / owned lexicon surface が見えない repo もここで落とします。問題なければ `--execute` を付けて本実行します。

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
python3 scripts/project_registry.py review-ignore
python3 scripts/project_registry.py review-ignore --execute
```

このコマンドは 3 種類の提案を返します。

- `suggested_tracking_state=stalled`
  - repo と collection の両方が長く止まっている、または `dead/private` が見えている
- `suggested_tracking_state=stable`
  - collection は最近動いているが repo 側は止まっている
- `suggested_tracking_state=active`
  - いま `stalled` だが、repo または collection の最近 activity が見えた

repo 更新日時と collection activity のどちらか一方でも最近動いていれば、少なくとも `stalled` には落としません。

`review-ignore` は inactivity review とは別で、ノイズ整理用です。現状の候補は次です。

- 独立した website / service / owned lexicon surface が見えない GitHub fork repo
- owned protocol surface を持たない、低 novelty の Bluesky 従属ツール

このコマンドは既定で `active / watchlist / stable / stalled` を見ます。すでに `introduced_url` があるもの、`archived`、`ignore` は候補にしません。

## 紹介候補キュー

未紹介で、人が見る価値のありそうな project を一覧化します。

```bash
python3 scripts/project_registry.py promotion-queue
python3 scripts/project_registry.py promotion-queue --kinds service_ready_for_introduction
python3 scripts/project_registry.py promotion-queue --include-personal-tools
python3 scripts/project_registry.py promotion-queue --kinds lexicon_ready_for_introduction --interactive
```

signal は今のところ 3 種類です。

- `service_ready_for_introduction`
  - 既定では `category=project` で、`service_status` が `live` / `beta` / `invite_only`
- `lexicon_ready_for_introduction`
  - `category=lexicon` で、NSID ヒントと公開先 (`website_url` / repo / resource) があり、紹介用メタデータが大きく欠けていない
- `technical_insight_candidate`
  - 実用性とは別に、新しい設計パターン、プロトコル不足の露呈、相互運用実験、特徴的なアーキテクチャ、参照実装としての価値が傑出している
  - LLM の暫定評価が `technical_interest_level=high` / `exceptional` で、具体的な理由と説明があるものだけを出す

`category=personal_tool` は既定では除外します。含めたい場合だけ `--include-personal-tools` を付けます。

`--interactive` を付けると `review-queue` と同じく 1 件ずつ見られます。`suggested_commands` には `show` や `enrich --deep-investigation` を出します。

interactive では `h` で理由付きの一時保留にできます。既定では 7 日間 `promotion-queue` から除外し、期限切れ後に自動復活します。期限切れ履歴は残るため、同じ project を再保留する時は Enter だけで直近理由を再利用できます。

明示的に設定、再設定、解除する場合:

```bash
python3 scripts/project_registry.py defer-promotion \
  --project-key proj:xxxxxxxxxxxx \
  --reason '中核機能であるXXがまだ実装されていない'

# 直近理由を再利用して再保留
python3 scripts/project_registry.py defer-promotion --project-key proj:xxxxxxxxxxxx

# active な保留だけ解除。履歴は残す
python3 scripts/project_registry.py defer-promotion --project-key proj:xxxxxxxxxxxx --clear
```

`promotion_deferrals` の理由は現状では運用メモです。理由から復活条件を自動判定する処理は未接続です。Web UI では promotion queue の `Hold 7d`、project detail の期限・理由・再保留・`Clear hold` を使えます。解除しても履歴は残ります。

表示順は全対象 project を先にスコアリングしたグローバル順です。`--limit` はソート後に適用します。interactive でも同じ順序を使い、DB 順の部分集合だけを先に表示することはありません。

## URL field の棚卸し

URL field の利用数、alias 重複、保存済み invariant 違反を読み取り専用で確認できます。

```bash
python3 scripts/project_registry.py audit-url-fields
python3 scripts/project_registry.py audit-url-fields --example-limit 5
```

このコマンドは DB を更新しません。`website_url_candidates`、`github_repo_urls`、`tangled_repo_urls` などの compatibility view を縮退させる前に、実 DB の分布と caller 移行の影響を確認するために使います。

保存済み `record_json.app.links` を正本から再構築する場合:

```bash
python3 scripts/project_registry.py cleanup-app-links
python3 scripts/project_registry.py cleanup-app-links --execute
```

既定は dry-run です。全削除ではなく、`website_url`、`source_code_url(s)`、`service_urls`、confirmed な `resource_url_records` から再構築します。

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

恒久 block ではなく、一定期間だけ様子を見る場合は `defer-identifier` を使います:

```bash
python3 scripts/project_registry.py defer-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com \
  --days 30
```

URL は confirmed から candidate に降格し、NSID は `deferred_identifiers` に保持されます。期限内は自動 confirmed 化を抑制します。解除する場合は同じ引数に `--clear` を付けます。解除しても自動 confirm はしません。

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

## storage layout

現在の `projects` テーブルは次を保持します。

- scalar columns
- `record_json`
- `ops_json`

`record_json` は高信頼の project record、`ops_json` は候補・調査・LLM・merge 制御などの運用情報です。迷うものは `record_json` に昇格させず、まず `ops_json` に残します。

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
