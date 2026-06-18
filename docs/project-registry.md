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
export PATH="$PWD/bin:$PATH"
```

この文書では公開CLI入口を`project-registry`と表記します。実体は[bin/project-registry](../bin/project-registry)だけです。PATHを変更しない場合は`bin/project-registry`と実行してください。

`tldextract` で PSL を引きます。入力は repo 内の `vendor/public_suffix_list.dat` に固定し、ライブラリの cache は `data/tldextract-cache/` 配下に置きます。

PSL に載っていない hosted suffix を追加したい場合は、[config/domain-overrides.json](../config/domain-overrides.json) の `custom_multi_tenant_suffixes` に suffix を足します。`example.com` を追加すると、`foo.example.com` と `bar.example.com` は別 family 扱いになります。discovery source の設定は `config/sources.json` に残します。

既存 DB を追加後の基準で確認しながら追記する場合:

```bash
project-registry audit-source-config example.com
```

このコマンドは既定で interactive です。suffix が未登録なら `domain-overrides.json` への追記を確認し、追加後の family 基準で `split_suggestions` を表示します。各 suggestion には tenant ごとの service URL / NSID、曖昧な repo、仮分割後の merge candidate、既存 `split-project` の dry-run コマンドが含まれます。

JSON dry-run のみ:

```bash
project-registry audit-source-config example.com --no-interactive
```

確認なしで suffix 追記まで行う場合:

```bash
project-registry audit-source-config example.com --execute --no-interactive
```

interactive では tenant group の番号を選んだ場合だけ、その group の service URL / NSID を 1 つの新 project へまとめて切り出します。元 project に複数 tenant が残る場合は suggestion を再計算し、同じ project を続けて表示します。Enter で送った suggestion はその実行中だけ先送りします。元 project には block と `set-separate` 相当の相互分離を残します。`--execute --no-interactive` では split を自動適用しません。共有 repo、共有 lexicon、URL 移転などの例外があるため、分割は常に suggestion 確認後に行います。

## zsh補完

CLIとWeb UI用のwrapper、zsh completionを使えます。

- [bin/project-registry](../bin/project-registry) / [completions/_project-registry](../completions/_project-registry)
- [bin/project-registry-web](../bin/project-registry-web) / [completions/_project-registry-web](../completions/_project-registry-web)

```bash
fpath=("$PWD/completions" $fpath)
autoload -Uz compinit && compinit
```

これで`project-registry`のsubcommandと主要optionを補完できます。`scripts/`を`PATH`に足すとPython file自体まで候補に出るため、`PATH`には`bin/`だけを足す前提です。

## seed登録

```bash
project-registry add-seed sh.tangled.repo
project-registry add-seed https://github.com/example/project
project-registry add-seed https://github.com/example/project/blob/main/lexicons/app/example/post.json
project-registry add-seed https://tangled.sh/@example/app/tree/main/lexdocs/example
project-registry add-seed https://example.com
```

GitHub の `blob/tree` URL や `tangled.sh` の tree URL は repo root に正規化します。

既存projectへ追加する場合:

```bash
project-registry add-seed https://example.com --project-key 2f2acb
```

`project_key` は `proj:xxxxxxxxxxxx` 形式ですが、CLIでは `proj:` を省略した prefix でも指定できます。

## 調査

1件だけ:

```bash
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry enrich --project-key 2f2acb
```

全件:

```bash
project-registry enrich --all
project-registry enrich --all --new-only
```

`enrich --all` は resumable checkpoint を自動作成します。保存先は既定で `data/enrich-runs/<run_id>.json` です。開始時の対象 project key、完了済み key、現在処理中の key、失敗情報、再開に使う実行 option を保存します。ただし LLM API key の値は保存しません。

```bash
project-registry enrich --all --run-id weekly-20260606
project-registry enrich --all --resume-run weekly-20260606
project-registry enrich --all --no-checkpoint
```

Ctrl+Cは処理中projectの完了後に停止する要求として扱います。保存とcheckpoint更新後、Python tracebackは出さず、JSON linesに`interrupted: true`、`run_id`、`checkpoint_path`、`resume_command`を出して終了コード`130`で終わります。外部request中の即時停止ではないため、停止までの時間は各sourceのtimeoutに依存します。再開時はcheckpointに保存された最初の対象リストを使い、完了済みprojectを飛ばします。

`stable` / `stalled` / `out_of_scope` / `archived` / `ignore` も回したい場合:

```bash
project-registry enrich --all --include-stable
project-registry enrich --all --include-stalled
project-registry enrich --all --include-out-of-scope
project-registry enrich --all --include-archived
project-registry enrich --all --include-ignore
```

## 表示

```bash
project-registry show --project-key 2f2acb
project-registry show --seed sh.tangled.repo --type nsid
project-registry show --all --format json
```

## Web UI

schema version 2対応のローカルWeb UIを起動できます。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

別のDBを参照する場合:

```bash
project-registry-web --db-path data/survey.dev.db --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

DBを閲覧専用で開く場合:

```bash
project-registry-web --db-path data/survey.db --token "$ATPROTO_SURVEY_WEB_TOKEN" --read-only
```

read-only modeでは全てのWeb POST APIを拒否し、画面上のmutationとjob controlも無効になります。project detailは`Overview / Identifiers / Relations / Investigation / Decisions / Raw`に分かれ、検索条件、選択project、detail tabはURLからreload/back/forward後に復元します。tokenとUI設定はURLへ保存しません。

`--reload`を付けると、Web server、application service、HTML/CSS/JavaScriptの変更時にserver processを再起動します。

画面構成:

- `Review`: review queue、promotion queue、merge plan
- `Projects`: project検索、schema v2 detail、seed preview・追加
- `Operations`: enrich、sync、review classificationのbackground jobと、未接続処理のCLI案内
- `Settings`: queue limit、merge mode、token、接続DBの確認

project detailではprofileに加え、classification付きidentifier、typed relation、active decisionと履歴、評価結果、最新investigation runのcollector状態を表示します。tracking state、candidate confirm/reject、block/defer、promotion hold、split、separate解除、auto-ready mergeは明示確認後にSQLiteへ保存します。表示中projectの単体enrichもdetail headerから開始できます。

relationと最新collector failureは個別対象としてdismissまたはdeferできます。判断は証拠行を変更せず`project_decisions`へ保存します。candidate developer accountはidentifierと同じdetail actionからconfirm/block/deferでき、既存projectへのmanual seed追加もdetail headerからpreview後に実行できます。

OverviewのProfileでは`name`とoperator向け`notes`を編集できます。Previewでfield単位のbefore/afterを確認してから保存し、preview後に別processがprojectを変更した場合は保存を拒否してdetailを再読込します。tagline、summary、website URLはこの編集画面の対象外です。

ProjectsのFiltersではidentifierのkind/value/classification、relationのpredicate/subject/object/source、decisionのkind/target/active・expired・clearedを指定できます。これらはnormalized tableを直接queryし、全projectをhydrateしません。

Operationsのenrich、sync、review classificationはWeb serverとは別processで動きます。enrichは単体projectまたはbulkを選び、deep investigation、未enrichのみ、前回失敗のみ、追加tracking stateを指定できます。syncは設定済みのintroduced/GitHub/collection inventory/Semble/Tangled sourceだけを選択でき、任意URLやqueryはCLIで扱います。syncのdry-runも外部通信しますがSQLiteは更新しません。reviewのdry-runはnetwork-freeで、executeは表示対象になった候補を一括適用します。

同じWeb serverから同時に実行できるbackground jobは1件です。CLIなど別processとの排他は行いません。enrichのStopは処理中projectの保存後に停止し、cancelled/failedのbulk runは同じcheckpointからResumeできます。sync/reviewの停止要求は現在のoperation完了後に反映され、checkpoint再開はありません。job一覧とeventはserver process内保持で、server再起動後は復元されません。

未接続:

- `assist-db-edit`
- server再起動後のjob履歴復元とcheckpoint発見
- investigation run一覧とreplay差分の専用画面
- LLM設定、server reload/shutdownの画面操作

APIは共有token必須です。起動時URLの`?token=...`を開くか、`Authorization: Bearer ...`または`X-Project-Registry-Token`を付けます。静的assetはtokenなしで配信します。

## 紹介済み候補の取り込み

Bluesky 検索から紹介済み判定だけを同期する:

```bash
project-registry sync-introduced
project-registry sync-introduced --execute
project-registry sync-introduced --query 'from:yamarten.bsky.social #ATプロトーク'
```

このコマンドは seed を追加しません。既存 project に一致したものだけ `introduced_url` を更新します。既定は増分同期で、初回は直近 7 日、以後は query ごとの前回成功地点から検索します。一致しなかった URL は `unmatched_entries` に出し、sync state に保留して次回も再照合します。

```bash
project-registry sync-introduced --all-history
project-registry sync-introduced --no-incremental --lookback-days 14
```

見出しや注記が混ざったテキスト seed list を取り込む:

```bash
project-registry import-seed-list tmpdata.txt
project-registry import-seed-list tmpdata.txt --execute
```

このコマンドは GitHub/Tangled/service URL/`pdsls`/`lexicon.garden` を seed に正規化します。`pdsls` の record URL は collection NSID に落とし、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。`Echosky: ...` のように注記の直後に複数 seed が並ぶ場合は、ラベルと seed が十分近い時だけ同一 project へ束ねます。

## collection inventory APIの同期

設定済みURL、または指定URLから直接取り込む:

```bash
project-registry sync-collections
project-registry sync-collections --execute
project-registry sync-collections --source-url https://collectiondata.usounds.work/collection_count_view
```

このコマンドは API 全件を取得しますが、既定では既知collectionを再取り込みしません。未知 collection は最新活動の `max` を使い、初回は直近 `10` 日、以後は source URL ごとの前回成功地点より新しいものだけを対象にします。
一方で、既知 collection の activity 情報自体は毎回見直します。`count` / `recent_count` / `max` を既存 project の `collection_observations` に反映するので、最近 record が流れている stalled project を active に戻す判断にも使えます。

未知候補は既定で新規 project にします。棚卸しだけ行い DB 候補を増やしたくない場合は
`--no-create-unmatched` を付けます。また、sync/import 時の既存 project への自動ひも付けは
conservative です。`exact_namespace` 以外の弱い一致は既存 project に吸わず、
`suggested_project_key` として提案だけ残します。

週次運用向けに、必要ならlookbackを調整できます:

```bash
project-registry sync-collections --execute --lookback-days 10
project-registry sync-collections --execute --lookback-days 14
```

必要なら取得したJSON payloadを保存できます:

```bash
project-registry sync-collections --save-path data/collection_inventory.latest.json
```

## GitHub repository searchの同期

設定済み query、または指定 query から未知 repo を取り込む:

```bash
project-registry sync-github-search
project-registry sync-github-search --execute
project-registry sync-github-search --query 'atproto OR "AT protocol" OR atprotocol'
```

このコマンドは既知 repo を再取り込みしません。query ごとに保存した前回成功地点まで `updated_at` 降順で走査します。state が無い初回だけ `--lookback-days` を使います。query には既定で `fork:false` と `archived:false` を足します。

GitHub sync でも `homepage_family` のような弱い一致では既存 project に自動で吸わず、suggestion 扱いにします。

```bash
project-registry sync-github-search --execute --lookback-days 10
project-registry sync-github-search --pages 3 --per-page 100
```

1回だけ広く取得したい場合は、lookback を外して dry-run し、未マッチ repo に対して repo metadata / README / tree / lexicon hint を見た保存前スクリーニングをかけます。

```bash
project-registry sync-github-search \
  --all-history \
  --screen-unmatched \
  --pages 10 \
  --per-page 100
```

この時、`screened_out_entries` に落ちたものは DB に作りません。現状は GitHub 上で fork と判定され、独立した homepage / service candidate / owned lexicon surface が見えない repo もここで落とします。問題なければ `--execute` を付けて本実行します。

## sync の増分 state

`sync-introduced`、`sync-collections`、`sync-github-search`、`sync-semble-collections` は既定で増分同期です。successful execute の watermark を SQLite の `sync_state` に保存します。

- dry-run、API error、ページ上限到達による不完全走査では state を更新しません
- `--no-create-unmatched` で未一致候補が残った場合も state を更新しません
- `--all-history` は保存済み watermark と初回 lookback を無視します。successful execute なら次回用 state を更新します
- `--no-incremental` は保存済み watermark の読み書きを止めます。`--all-history` が無ければ初回 lookback は適用します
- timestamp 境界は再取得を許す at-least-once 方式です。重複処理はあり得ますが、同一 timestamp の新規項目を落としにくくします

Semble は response の `sorting.sortBy=updatedAt` / `sortOrder=desc` を確認できた場合だけ、watermark より古い card で早期停止します。`sync-collections` は既知 project の activity refresh のため、増分化後も inventory API 全件を取得します。

## merge

recommendationを見るだけ:

```bash
project-registry merge \
  --project-key 2f2acb \
  --use-recommendation
```

seed 同士を直接指定する場合:

```bash
project-registry merge \
  --seed ink.henry.saunter.walk --type nsid \
  --target-seed https://saunter.henry.ink --target-type service_url
```

repo URL や service URL を target にしてもよいです:

```bash
project-registry merge \
  --seed https://github.com/example/app --type repo_url \
  --target-seed https://example.com --target-type service_url
```

実行:

```bash
project-registry merge \
  --project-key 2f2acb \
  --use-recommendation \
  --execute
```

一括候補の確認:

```bash
project-registry merge-batch
project-registry merge-batch -v
```

特定 pair だけ根拠を確認する:

```bash
project-registry inspect-merge-pair \
  --project-key proj:aaaa1111bbbb \
  --target-project-key proj:cccc2222dddd
```

seed 指定でも確認できます:

```bash
project-registry inspect-merge-pair \
  --seed app.diffdown.comment --type nsid \
  --target-seed com.diffdown.document --target-type nsid
```

立ち上げ時の暫定措置として、`listReposByCollection` の先頭 DID と alias 一致を使う heuristic を有効にする場合:

```bash
project-registry merge-batch \
  --mode moderate \
  --bootstrap-collection-creator-heuristic \
  -v
```

これは常用向けではありません。`com.atproto.sync.listReposByCollection` の先頭 DID を「最初の record 作成者」とみなす経験則に依存するため、まず dry-run で中身を確認してから使います。

pair 単位でその heuristic だけを確認したい場合:

```bash
project-registry inspect-merge-pair \
  --seed app.diffdown.comment --type nsid \
  --target-seed com.diffdown.document --target-type nsid \
  --bootstrap-collection-creator-heuristic
```

## tracking state

継続調査対象の切り替え:

```bash
project-registry set-tracking-state \
  --project-key 2f2acb \
  --tracking-state stable \
  --note "安定運営"
```

`stalled` は、立ち消え気味で追跡頻度を下げたいが `archived` にするほど確定ではないもの向けです。

候補の自動判定を見る:

```bash
project-registry review-stalled
project-registry review-stalled --inactivity-days 60 --max-enrich-age-days 45
project-registry review-out-of-scope
project-registry review-ignore
project-registry review-ignore --execute
```

このコマンドは 3 種類の提案を返します。

- `suggested_tracking_state=stalled`
  - repo と collection の両方が長く止まっている、または `dead/private` が見えている
- `suggested_tracking_state=stable`
  - collection は最近動いているが repo 側は止まっている
- `suggested_tracking_state=active`
  - いま `stalled` だが、repo または collection の最近 activity が見えた

repo 更新日時と collection activity のどちらか一方でも最近動いていれば、少なくとも `stalled` には落としません。

`active/watchlist -> stalled` の降格判定は、`last_enriched_at` が `--max-enrich-age-days` 未満のprojectだけを対象にします。未enrichまたは調査結果が古いprojectは、停止を確定する材料が不足しているため候補から外します。`stalled -> active` と `active -> stable` はrecent activityを直接根拠にするため、この鮮度ゲートの対象外です。

`review-out-of-scope` は、atprotoとの関係を保持したまま現在の紹介・重点調査対象から外す候補を出します。

- `category=auth_only` で owned protocol surface がない
- owned protocol surfaceを持たない、低noveltyのBluesky従属ツール

`out_of_scope` は通常の enrich/review/promotionから外れますが、project自体は残り、syncによるidentifier照合、merge候補、明示enrich、手動での `active` 復帰が可能です。さらにowned NSID / collection surfaceが見つかったprojectは、`review-out-of-scope` に `suggested_tracking_state=active` として再登場します。Flashesのように後から独自NSIDやprotocol surfaceを持つ可能性を失わないための状態です。

`review-ignore` は inactivity review とは別で、ノイズ整理用です。現状の候補は次です。

- 独立した website / service / owned lexicon surface が見えない GitHub fork repo

新規のスコープ外候補については、両コマンドとも既定で `active / watchlist / stable / stalled` を見て、すでに `introduced_url` があるもの、`archived`、`ignore` は候補にしません。`review-out-of-scope` だけは既存の `out_of_scope` も走査し、owned protocol surfaceが見つかった場合の `active` 復帰候補を出します。

## 紹介候補キュー

未紹介で、人が見る価値のありそうな project を一覧化します。

```bash
project-registry promotion-queue
project-registry promotion-queue --kinds service_ready_for_introduction
project-registry promotion-queue --include-personal-tools
project-registry promotion-queue --kinds lexicon_ready_for_introduction --interactive
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
project-registry defer-promotion \
  --project-key proj:xxxxxxxxxxxx \
  --reason '中核機能であるXXがまだ実装されていない'

# 直近理由を再利用して再保留
project-registry defer-promotion --project-key proj:xxxxxxxxxxxx

# active な保留だけ解除。履歴は残す
project-registry defer-promotion --project-key proj:xxxxxxxxxxxx --clear
```

`promotion_deferrals`の理由は現状では運用メモです。理由から復活条件を自動判定する処理は未接続です。解除しても履歴は残ります。

表示順は全対象 project を先にスコアリングしたグローバル順です。`--limit` はソート後に適用します。interactive でも同じ順序を使い、DB 順の部分集合だけを先に表示することはありません。

## URL field の棚卸し

canonical URL fieldの利用数と保存済みinvariant違反を読み取り専用で確認できます。

```bash
project-registry audit-url-fields
project-registry audit-url-fields --example-limit 5
```

このコマンドはDBを更新しません。削除済みalias fieldはinventoryへ出力されません。

保存済み`profile_json.app.links`を正本から再構築する場合:

```bash
project-registry cleanup-app-links
project-registry cleanup-app-links --execute
```

既定は dry-run です。全削除ではなく、`website_url`、`source_code_url(s)`、`service_urls`、confirmed な `resource_url_records` から再構築します。

適用する場合:

```bash
project-registry review-stalled --execute
```

同じ creator DID や NSID prefix にぶら下がる project をまとめて切り替える場合:

```bash
project-registry set-tracking-state-batch \
  --record-did did:plc:ie77niyfcztulkf2oa7p4adz \
  --tracking-state ignore

project-registry set-tracking-state-batch \
  --record-did did:plc:ie77niyfcztulkf2oa7p4adz \
  --current-tracking-states active stalled \
  --tracking-state ignore \
  --note "bulk ignore noisy synthetic NSIDs" \
  --execute
```

## merge recommendation の手動抑止

この 2 件は別物として扱いたい、というときは相互に block できます:

```bash
project-registry set-separate \
  --project-key proj:aaaa1111bbbb \
  --other-project-key proj:cccc2222dddd
```

解除する場合:

```bash
project-registry set-separate \
  --project-key proj:aaaa1111bbbb \
  --other-project-key proj:cccc2222dddd \
  --clear
```

これを入れると `duplicate_candidates` と `merge_recommendation` の計算自体からその pair を外すので、次の `enrich` でも復活しません。

## 誤った identifier の手動矯正

project 自体は合っているが、誤った `service_url` / `repo_url` / `nsid` が混ざった場合は、その identifier を project 単位で block できます:

```bash
project-registry block-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com
```

解除する場合:

```bash
project-registry block-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com \
  --clear
```

この block は `finalize` / `enrich` のたびに適用されるので、次回の再調査でも同じ identifier はその project に戻りません。

恒久 block ではなく、一定期間だけ様子を見る場合は `defer-identifier` を使います:

```bash
project-registry defer-identifier \
  --project-key proj:aaaa1111bbbb \
  --identifier-type service_url \
  --value https://example.com \
  --days 30
```

URL は confirmed から candidate に降格し、NSID は `deferred_identifiers` に保持されます。期限内は自動 confirmed 化を抑制します。解除する場合は同じ引数に `--clear` を付けます。解除しても自動 confirm はしません。

## 誤マージの分割

誤って合体した project から、1 本の `nsid` / `service_url` / `repo_url` を新 project に切り出す場合:

```bash
project-registry split-project \
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
project-registry assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい'
project-registry assist-db-edit 'proj:aaaa と proj:bbbb をマージしたい'
project-registry assist-db-edit 'proj:xxxx から https://example.com をブロックしたい'
```

現状サポートする action は:

- `merge`
- `set_separate`
- `block_identifier`
- `set_tracking_state`

一意に project 解決できた場合だけ、そのまま反映もできます:

```bash
project-registry assist-db-edit 'proj:dff3271870e4とproj:5e69784458a3を別物として扱いたい' --execute
```

TTY 上で一意に解決できた場合は、`--execute` なしでもその場で確認プロンプトを出します。ローカル LLM が設定されていればそれで解釈します。未設定でも `proj:` / URL / NSID が明示されているケースは heuristic で解釈します。曖昧な場合は候補一覧と `suggested_command` だけ返し、実行しません。

## DBを直接編集した後

GUIやSQLで `projects` を直接編集した後は、正規化と派生データ再計算のためにこれを回します。

```bash
project-registry repair-db
project-registry repair-db --execute
```

## 調査履歴の削除

保存済み調査は既定で、完了から90日以内の全runとprojectごとの最新3 runを保持します。それより古い`investigation_runs`と対応する`collector_runs`を確認するには:

```bash
project-registry prune-investigation-history
```

削除を実行する場合:

```bash
project-registry prune-investigation-history --execute
```

保持期間と最低保持件数は変更できます。

```bash
project-registry prune-investigation-history --retention-days 180 --keep-latest-per-project 5
```

通常のDELETE後に空いたSQLite pageは後続writeで再利用されますが、DB file自体は縮みません。file sizeも縮める場合はWeb UI、enrich、別CLIを停止してから実行します。

```bash
project-registry prune-investigation-history --execute --vacuum
```

operator decision、migration provenance、project current stateはこのcommandでは削除しません。

### v1 migration provenanceの削除

v1から移行したidentifierには旧field、旧classification、降格理由などのprovenance metadataが残ります。通常運用が十分安定し、v1 backupを別途保持した後は、identifier rowを残したまま移行専用metadataだけを削除できます。

```bash
project-registry prune-migration-provenance
project-registry prune-migration-provenance --execute
```

dry-runが既定です。`legacy_record`内の通常repo/account/manual seed metadataは平坦化して残し、repository confidenceは現在のidentifier classificationへ合わせます。identifierのkind、value、role、classification、primary、provider、observed_atは変更しません。実行判断の詳細は[notes/v1-migration-trust.md](notes/v1-migration-trust.md)を参照してください。

## 高度な調査・開発用コマンド

以下は通常のseed登録・enrich・review操作には不要です。collectorやevaluationの開発、個別projectの深掘り、実験的sourceの確認に使います。

### 保存済み調査のreplay

保存済みEvidenceBundleからintegration/evaluationだけをnetworkなしで再実行します。heuristic変更の回帰確認とfixture作成が目的です。

```bash
project-registry replay-investigation --run-id inv:...
project-registry replay-investigation --project-key proj:...
project-registry benchmark-investigation
project-registry export-investigation-fixture --run-id inv:... --output /tmp/case.json
```

`benchmark-investigation`と`export-investigation-fixture`は開発・テストcorpus保守用で、通常のproject更新操作ではありません。

### serviceの個別診断

`verify-service`はservice確認の深さを`--lane`で指定します。ここでのlaneは実装上の検証方式を区別するoption名です。

- `static`: top pageのHTTP status、metadata、text、link、redirectを確認する
- `deep`: about/docs/app/login/signupなど同一site内の追加pageも確認する
- `browser`: PlaywrightでJavaScript描画後の匿名画面とlogin/signup到達を確認する

```bash
project-registry verify-service -p proj:... --lane static
project-registry verify-service -p proj:... --lane deep --execute
project-registry verify-service -p proj:... --lane browser --execute
```

`--execute`なしはDBを更新しません。browser方式は任意依存で、認証入力、登録、投稿は行いません。

```bash
python3 -m pip install -r requirements-browser.txt
python3 -m playwright install chromium
```

### Tangled UFOsの実験的sync

`sync-tangled-repos`はUFOsのrolling windowからTangled repo recordを探す補助経路です。通常の全履歴syncではなく、既定はdry-runかつunmatched project作成offです。

```bash
project-registry sync-tangled-repos
project-registry sync-tangled-repos --execute
project-registry sync-tangled-repos --create-unmatched --execute
```

UFOsは完全履歴ではありません。出力の`boundary_reached`, `gap_possible`, `complete_history`を確認してください。

## storage layout

現行DBはschema version 2です。

- `projects`: scalar + `profile_json` + `state_json`
- `project_identifiers`: NSID、service/repo URL、account、DID、domain
- `project_relations`: typed relation evidence
- `project_decisions`: block/defer/separateなどのoperator判断
- `investigation_runs` / `collector_runs`: replay可能な調査履歴

旧DBは`project-registry migrate-db OLD_DB_PATH`で移行します。`--dry-run`は一時DBを作って検証し、元DBを変更しません。

v1移行は旧confirmedをそのまま信用せず、根拠を再構成できないidentifierをmetadata付きcandidateへ降格します。`manual_seed`または`confirm_identifier` decisionがあるidentifierだけをoperator根拠としてconfirmed維持します。reportでは少なくとも次を確認します。

- `uncorroborated_legacy_confirmed_count`: 0であること
- `active_block_identifier_collision_count`: 0であること
- `normalized_roundtrip_diff_count`: 0であること
- `downgraded_to_candidate_counts`: 旧confirmedからcandidateへ隔離した内訳

詳細は [project-record.md](project-record.md) を参照してください。

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

`ATPROTO_SURVEY_LLM_ENDPOINT` と Settings で指定する LLM endpoint は、operator が管理する trusted endpoint として扱います。project data から見つかった service / resource URL には内部宛て block が掛かりますが、この LLM endpoint 呼び出しだけは LAN / localhost の endpoint も許可します。

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
project-registry enrich --all --llm-reasoning-effort none
project-registry enrich --all --new-only --llm-reasoning-effort high
```
