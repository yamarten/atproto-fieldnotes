# Project Registry

手動で見つけたseedを登録し、project recordとして追跡するための操作ガイドです。

内部実装や昇格ロジック、`enrich` / `sync` / `merge` / `review-stalled` の判定材料、外部アクセス量の詳細は [registry-internals.md](registry-internals.md) に分けています。

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

Python 3.10以上が必要です。公開wrapperは`.venv/bin/python`を優先し、無ければ`python3`を使います。
選ばれたruntimeが3.10未満の場合は、module import前にversion errorで終了します。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PATH="$PWD/bin:$PATH"
```

この文書では公開CLI入口を`project-registry`と表記します。実体は[bin/project-registry](../bin/project-registry)だけです。PATHを変更しない場合は`bin/project-registry`と実行してください。

新しいDBを作る場合だけ、最初に明示的な初期化commandを実行します。

```bash
project-registry init-db
```

既定pathは`data/survey.db`です。別pathを作る場合は次のように指定します。

```bash
project-registry init-db --db-path data/survey.dev.db
```

`add-seed`、`show`などの通常command、Web、background workerはDBを暗黙作成しません。pathが存在しない、
またはschema version 2として初期化されていない場合はerrorで停止します。`init-db`は旧schemaのmigrationや
上書きを行いません。

`tldextract` で PSL を引きます。入力は repo 内の `vendor/public_suffix_list.dat` に固定し、ライブラリの cache は `data/tldextract-cache/` 配下に置きます。

PSL に載っていない hosted suffix は、ローカル設定 `config/domain-overrides.json` の `custom_multi_tenant_suffixes` に保存します。追加にはWeb merge reviewまたは次の監査コマンドを使います。`example.com` を追加すると、`foo.example.com` と `bar.example.com` は別 family 扱いになります。初期形は [config/domain-overrides.example.json](../config/domain-overrides.example.json) です。discovery source の設定は `config/sources.json` に残します。

既存 DB を追加後の基準で確認しながら追記する場合:

```bash
project-registry audit-source-config example.com
```

このコマンドは既定で interactive です。suffix が未登録なら `domain-overrides.json` への追記を確認し、保存済みcurrent service probeのsame-site判定を追加後のpolicyで再評価してから`split_suggestions`を表示します。各 suggestion には tenant ごとの service URL / NSID、曖昧な repo、仮分割後の merge candidate、既存 `split-project` の dry-run コマンドが含まれます。

JSON dry-run のみ:

```bash
project-registry audit-source-config example.com --no-interactive
```

確認なしで suffix 追記まで行う場合:

```bash
project-registry audit-source-config example.com --execute --no-interactive
```

interactive では tenant group の番号を選んだ場合だけ、その group の service URL / NSID を 1 つの新 project へまとめて切り出します。元 project に複数 tenant が残る場合は suggestion を再計算し、同じ project を続けて表示します。Enter で送った suggestion はその実行中だけ先送りします。元 project には block と `set-separate` 相当の相互分離を残します。`--execute --no-interactive` では split を自動適用しません。共有 repo、共有 lexicon、URL 移転などの例外があるため、分割は常に suggestion 確認後に行います。設定ファイルを直接編集した場合も、同じsuffixに`--execute --no-interactive`を実行してcurrent stateを揃えます。

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
project-registry add-seed https://codeberg.org/cartes/web
project-registry add-seed https://example.com
project-registry add-seed https://forge.example/dev/app --type repo_url
```

GitHub の `blob/tree` URL、`tangled.sh` の tree URL、Codeberg/GitLab/sourcehut系の既知forge URLは repo root に正規化します。未知のforgeや曖昧なURLは `--type repo_url` を明示します。Web UIの `Add seed` では `repo_url: https://forge.example/dev/app` のようにprefixを書くとtypeを上書きできます。

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

`enrich --all`は1 projectが失敗しても、失敗情報をcheckpointへ記録して残りを続行します。1件でも失敗した
runは最終的にfailedとなり、CLIは終了コード`1`を返します。同じcheckpointをresumeすると完了済みprojectを
飛ばし、失敗projectを再試行します。最初の失敗で停止したい診断時は`--fail-fast`を指定します。

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

保存済みcurrent stateの結論根拠を確認する場合:

```bash
project-registry explain --project-key 2f2acb
project-registry explain --project-key 2f2acb --topic ownership --format json
```

`service_status`、ownership、atproto contribution、merge candidateについて、適用rule、支持/反証signal、保存source参照を表示します。このcommandは外部networkやLLMを呼びません。

## Web UI

schema version 2対応のローカルWeb UIを起動できます。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

別のDBを参照する場合:

```bash
project-registry init-db --db-path data/survey.dev.db
project-registry-web --db-path data/survey.dev.db --token "$ATPROTO_SURVEY_WEB_TOKEN"
```

project detailは`Overview / Identifiers / Relations / Investigation / Decisions / Raw`に分かれ、検索条件、選択project、detail tabはURLからreload/back/forward後に復元します。UI設定はURLへ保存しません。tokenは初回bootstrap URLから読み取った直後にURLから除去します。

### Access境界

Web UIは`127.0.0.1`上で単一operatorが使うことを前提にしています。公開server、共有端末、複数利用者向けのaccess controlは実装していません。

- 起動時に表示・自動openする初回URLだけはqueryにtokenを含みます。UIは読み取り後に`history.replaceState`でqueryを除去し、HTTP request logもqueryを記録しませんが、terminal出力、browser拡張、初回navigationを観測できる相手からtokenを守る仕組みではありません。
- tokenは同じoriginの`localStorage`へ保存します。同じbrowser profile・originを使えるscriptや利用者とは秘密を分離できません。共有端末では使わず、不要になったらSettingsの`Clear API token`で削除します。
- server自身はplain HTTPで、TLS終端やtoken rotationを提供しません。`--host`には非loopback addressも指定できますが、認証は共有tokenだけなので、既定の`127.0.0.1` bindを通常導線とします。
- localhost外から使う必要がある場合は、Web serverをloopbackへbindしたまま、operatorが管理するSSH tunnelやTLS付きreverse proxyを前段に置きます。`--host 0.0.0.0`などで直接公開するとtokenを含む通信もplain HTTPになるため、常用導線とは扱いません。正式に使う場合はproxy header、TLS、token更新、access logの契約を別途設計します。

stderrのHTTP logはmethod、queryを除いたrequest path、status、所要時間だけを記録します。token、request body、background worker stderrは混ぜません。予期しないHTTP exceptionのtracebackとbackground jobのstderrはserver側の診断情報であり、UIへはそれぞれ一般的なerror responseと明示的に開いたjob detailとして表示します。

`--reload`を付けると、Web server、application service、HTML/CSS/JavaScriptの変更時にserver processを再起動します。
Settingsの`Reload server`は同じcommand-line argumentでserver processを手動再起動します。SQLiteは変更せず、
active background jobへ協調停止を要求します。shutdown timeoutを超えたworkerは終了してから再起動します。
browserは新しいprocessが応答するまで待って自動再読込します。

Operationsはbulk enrichの`enrich-runs/` checkpointを再発見します。server再起動でprocess内job eventは失われますが、未完了checkpointはDiscovered Checkpointsから再開できます。Review Opsは最初にnetwork-free planを作り、候補を選択してからSQLite更新を確認します。syncはdry-runでも外部networkへ接続します。

project detailのInvestigation tabでは保存run一覧、collector結果、failure、bounded evidence、input/output/evaluationを表示できます。Replay with current rulesは外部networkやDB更新を行わず、保存evaluationと現行ruleの差分をその場で計算します。

OverviewのService / Ownershipでは保存済みservice verificationの履歴、NSID ownership relation、OAuth permission、URL aliasを確認できます。OAuth `include:`は未解決であることを明示します。データが保存されていない場合は`not_observed`と表示し、否定結果とは扱いません。URL aliasは同じidentifier decision経路でblock/deferできます。

OverviewのExplainではCLIと同じ共通serviceを使い、保存済みのservice status、ownership、atproto contribution、merge candidateの根拠を表示します。画面表示のためのnetwork取得やLLM評価は行いません。project detail本体を先に表示し、全projectとの比較が必要なmerge candidateだけを直後に別リクエストで計算して、完了次第OverviewとExplainへ追記します。計算結果はDBへ保存しません。

画面構成:

- `Review`: review queue、promotion queue、merge plan
- `Projects`: project検索、schema v2 detail、seed preview・追加
- `Operations`: enrich、sync、review classificationのbackground jobと、未接続処理のCLI案内
- `Settings`: queue limit、merge mode、表示time zone、token、接続DB、Python version、Git checkoutのcommit確認

Web UIの時刻表示はSettingsのtime zoneで整形します。既定は`Asia/Tokyo`です。IANA time zone名または`+09:00`のような固定offsetを指定できます。DBに保存するtimestampは変更しません。

`Review`画面では、個別のreview action保存後に一覧全体を自動再計算しません。表示中のカードだけをローカル一覧から外して次の項目を開きます。`Skip for now`はDBへ何も書かず、表示中カードを一時的に飛ばして次へ進みます。merge reviewでも同じく、merge/separateを選ばず保留できます。新しい候補や正確な件数を取り直す場合は`Refresh`でqueueを再計算します。

project detailではprofileに加え、classification付きidentifier、typed relation、active decisionと履歴、評価結果、最新investigation runのcollector状態を表示します。新規enrich runでは、run historyからphase別時間とLLM requestのsize/statusも確認できます。旧runはtiming未記録と表示します。同じ値にconfirmed/candidateなど複数行がある場合もroleとclassificationの組を分けて表示します。`website` roleは独立identifierとして強調せず、同じservice URL groupのprimary markerとして表示します。DID/handle/accountは同じaccount metadataを持つ場合は1つのidentity groupに畳みます。candidate confirmは該当行から開始できますが、confirm/block/defer/splitのdecision identityは保存契約上`kind + value`であり、同じ値のrole単位ではありません。block/defer/splitは画面でも作用範囲を明示します。tracking state、candidate confirm/reject、block/defer、promotion hold、split、separate解除、mergeは明示確認後にSQLiteへ保存します。表示中projectの単体enrichもdetail headerから開始できます。Overviewの`Notes`は人間向けprofile notesで、merge/split/importなどの機械生成履歴はread-onlyの`System notes`として別表示します。

merge candidateからは候補projectの通常detailまたはmerge reviewへ移動できます。Project detailのMerge Candidateでは表示中projectをsourceにしてtarget project keyを直接指定できます。Mergeタブではsource/targetのproject keyを直接入力して、候補一覧に出ていない任意pairのmerge reviewも開けます。merge reviewはsource/targetのprofile、状態、seed、confirmed/candidate identifier、pair判定signalとmerged result previewを並べます。subdomain tenant差でmerge候補になっていそうなpairでは、`custom_multi_tenant_suffixes` 候補を表示し、その場で `domain-overrides.json` に追加できます。

relationと最新collector failureは個別対象としてdismissまたはdeferできます。判断は証拠行を変更せず`project_decisions`へ保存します。candidate developer accountはidentifierと同じdetail actionからconfirm/block/deferでき、既存projectへのmanual seed追加もdetail headerからpreview後に実行できます。

OverviewのProfileでは`name`、`introduced_url`、人間向け`notes`を編集できます。Previewでfield単位のbefore/afterを確認してから保存し、preview後に別processがprojectを変更した場合は保存を拒否してdetailを再読込します。tagline、summary、website URL、`system_notes`はこの編集画面の対象外です。

ProjectsのFiltersではtracking state、service status、atproto contribution、introduced有無、candidate/error有無、identifierのkind/value/classification、relationのpredicate/subject/object/source、decisionのkind/target/active・expired・clearedを指定できます。tracking state、service status、atproto contributionは複数選択でき、未選択なら絞り込みません。kind、classification、predicate、subject/object type、decision kind/target typeは保存契約または接続DBにある値から選びます。identifier・relation・decisionのvalueは部分一致、relation sourceは候補付きの完全一致入力です。これらはnormalized tableを直接queryし、全projectをhydrateしません。

Operationsのenrich、sync、review classificationはWeb serverとは別processで動きます。enrichは単体projectまたはbulkを選び、deep investigation、未enrichのみ、前回失敗のみ、追加tracking stateを指定できます。syncは設定済みのintroduced/GitHub/collection inventory/Semble/Tangled sourceだけを選択でき、任意URLやqueryはCLIで扱います。syncのdry-runも外部通信しますがSQLiteは更新しません。reviewのdry-runはnetwork-freeで、executeは表示対象になった候補を一括適用します。

OperationsのScheduled Presetsでは、allowlist済みのjob presetを手動または定期発火できます。presetは通常の`enrich --all`、全tracking state対象の`enrich --all`後にstalled reviewを一括反映するall-state maintenance、`sync introduced`、introduced以外のsync一括後に`enrich --all --new-only`を行う複合presetです。各presetはenabled、毎週または第N週の曜日、時刻、time zoneを個別に設定できます。`week_of_month=0`は毎週、`1..5`は月次の第N週として扱います。設定とpending queueはDBと同じdirectoryの`job-schedules.json`へ保存します。予定時刻から24時間以内だけdueとし、それより古い未実行分は後追い発火しません。Web reloadやserver再起動時に実行中だったscheduled stepは完了扱いにせず、同じstepから再試行します。任意commandは実行できません。
Webからexecuteしたsyncは、sourceごとの件数、候補key / URL、成否、所要時間だけをbounded observationとして同じfileへ直近4回分保存します。`GET /api/job-schedules`の`source_observations`で取得でき、dry-run、full job result / event、本文payloadは含みません。

同じWeb serverから同時に実行できるbackground jobは1件です。CLIなど別processとの排他は行いません。enrichのStopは処理中projectの保存後に停止し、cancelled/failedのbulk runは同じcheckpointからResumeできます。sync/reviewの停止要求は現在のoperation完了後に反映され、checkpoint再開はありません。running jobはprogressの`current / total`が得られる場合だけ、開始後の平均速度から線形外挿したETAを表示します。Detailsは自動pollで内容とスクロール位置を戻さず、`Refresh details`で明示更新します。job一覧とeventはserver process内保持で、server再起動後は復元されません。

Settingsのcommit hashとdirty表示は起動時に一度だけ取得します。配布物など`.git`が無い環境ではUnavailableと表示し、実行中にcheckoutが変わった場合はserver reload後に更新します。

未接続:

- server再起動後のprocess job履歴復元（enrich checkpoint発見は接続済み）
- LLM設定、server shutdownの画面操作

APIは共有token必須です。起動時URLの`?token=...`を開くか、`Authorization: Bearer ...`または`X-Project-Registry-Token`を付けます。静的assetはtokenなしで配信します。

## 紹介済み候補の取り込み

Bluesky 検索から紹介済み判定だけを同期する:

```bash
project-registry sync-introduced
project-registry sync-introduced --execute
project-registry sync-introduced --query 'from:yamarten.bsky.social #ATプロトーク'
```

通常のquery、source URL、tracked collection、pages/per-page/limit/lookback budget、enabled状態は
`config/sources.json`から読みます。各CLI optionはそのrunだけ設定を上書きします。`enabled: false`のsourceは
CLI/Web/workerのいずれからもnetwork request前に拒否されます。matching、classification、auto-attach等の
判定policyはこのconfigでは変更できません。

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

このコマンドは repo URL、service URL、`pdsls`、`lexicon.garden` を seed に正規化します。`pdsls` の record URL は collection NSID に落とし、`lexicon.garden` の identity URL や `bsky.app` のプロフィール/投稿 URL も、そこから service URL / repo URL / NSID を導ける場合だけ展開します。材料が無いものだけスキップします。`Echosky: ...` のように注記の直後に複数 seed が並ぶ場合は、ラベルと seed が十分近い時だけ同一 project へ束ねます。

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
conservative です。registrable namespaceやservice host familyだけの弱い一致は既存 project に吸わず、
`suggested_project_key` として提案だけ残します。
full collectionから得た親namespaceが既存projectのNSID seed / recorded namespaceと一意に一致する場合は、
registrable namespaceへの集約前に同一projectとして扱います。
同じ集約bucket内に、既にseed化したcollectionと同じ3-label以上の親namespaceを持つmemberがある場合は、
そのmemberも同じprojectのNSID seedとして記録します。例えば `link.latr.saved.external` をseed化したprojectに
`link.latr.saved.item` が同じobservationで現れた場合、後者もidentifierとして残します。

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
`sync-collections --max-items N`は、inventory応答の先頭N件だけを照合・import対象にする検証用の上限です。API応答自体は全件取得します。応答を切り詰めたexecuteでは未処理entryを取りこぼさないようsync stateを進めません。
Semble card の非root service URLは project seed にせず、confirmed/manual root serviceが既存projectにある場合だけ evidence URL として添付します。rootがcandidateにしかない場合は自動添付せず、`suggested_entries`へ返します。

## merge

merge executeはtarget更新、source key alias作成、関連するseparate参照更新、source削除を
単一transactionで行います。途中失敗時は部分更新を残しません。merge後も旧source keyを
project指定へ使用でき、存続projectへ解決します。source keyで保存されたInvestigation runも
存続projectの履歴に表示しますが、当時のsnapshot/bundle内keyは書き換えません。

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

`active -> stalled` の降格判定は、`last_enriched_at` が `--max-enrich-age-days` 未満のprojectだけを対象にします。未enrichまたは調査結果が古いprojectは、停止を確定する材料が不足しているため候補から外します。`stalled -> active` と `active -> stable` はrecent activityを直接根拠にするため、この鮮度ゲートの対象外です。

`review-out-of-scope` は、atprotoとの関係を保持したまま現在の紹介・重点調査対象から外す候補を出します。

- `category=auth_only` で owned protocol surface がない
- owned protocol surfaceを持たない、低noveltyのBluesky従属ツール

`out_of_scope` は通常の enrich/review/promotionから外れますが、project自体は残り、syncによるidentifier照合、merge候補、明示enrich、手動での `active` 復帰が可能です。さらにowned NSID / collection surfaceが見つかったprojectは、`review-out-of-scope` に `suggested_tracking_state=active` として再登場します。Flashesのように後から独自NSIDやprotocol surfaceを持つ可能性を失わないための状態です。

`review-ignore` は inactivity review とは別で、ノイズ整理用です。現状の候補は次です。

- 独立した website / service / owned lexicon surface が見えない GitHub fork repo

新規のスコープ外候補については、両コマンドとも既定で `active / stable / stalled` を見て、すでに `introduced_url` があるもの、`archived`、`ignore` は候補にしません。`review-out-of-scope` だけは既存の `out_of_scope` も走査し、owned protocol surfaceが見つかった場合の `active` 復帰候補を出します。

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
保留期間は `--days` 未指定なら自動で決まります。初回の若い project は14日、通常初回は30日、過去に promotion defer した project は履歴回数に応じて45/60/90日へ延びます。明示的に固定したい場合だけ `--days` を指定します。

表示順は全対象 project を先にスコアリングしたグローバル順です。`--limit` はソート後に適用します。interactive でも同じ順序を使い、DB 順の部分集合だけを先に表示することはありません。

## URL field の棚卸し

canonical URL fieldの利用数と保存済みinvariant違反を読み取り専用で確認できます。

```bash
project-registry audit-url-fields
project-registry audit-url-fields --example-limit 5
```

このコマンドはDBを更新しません。削除済みalias fieldはinventoryへ出力されません。

保存済み`profile_json.app.links`はproject保存時に、`website_url`、`source_code_url(s)`、`service_urls`、confirmedな`resource_url_records`から再構築します。既存の派生linkは入力として引き継ぎません。

適用する場合:

```bash
project-registry review-stalled --execute
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
切り出し先の`project_key`は自動採番されます。

`--identifier-type domain` を指定した場合は、authority domain 単位の誤マージ救済として扱います。指定 domain の authority domain identifier、同じ authority domain から導出される NSID、同じ host の confirmed service URL をまとめて切り出します。repo URL は domain だけでは帰属判断しないため自動移動しません。

このコマンドは:

- 指定 identifier を新 project に seed として移す
- 元 project からその identifier を除去する
- 元 project に block を入れて再付着を防ぐ
- 元 project と新 project を `set-separate` 相当で相互分離する

という処理をまとめて行います。複数アプリが混ざっている場合は、必要な identifier ごとに数回に分けて切り出します。

## DBを直接編集した後

GUIやSQLで `projects` を直接編集した後、または保存済みデータを現行ロジックのshapeへ揃える時は、正規化と派生データ再計算のためにこれを回します。

```bash
project-registry repair-db
project-registry repair-db --execute
```

既定はdry-runです。`--execute` を付けた時だけ保存します。`repair-db` は特定バグだけを直す専用コマンドではなく、DB全体のcurrent project payloadを現行の保存shapeへ揃え、merge候補派生値を再計算します。代表的な出力カウンタは次の意味です。

- `normalized_project_count`: 読み込み時の互換shape補正だけでproject JSON payloadが変わるproject数。
- `pruned_project_count`: `finalize_project_shape()`、developer account/handle補正、atproto contribution保存値の補正により、保存対象payloadが変わるproject数。project削除数や削除row数ではありません。
- `collection_member_seed_repair_count`: collection observation由来のmember seedを補完した件数。
- `manual_repo_seed_repair_count`: activeなmanual repo seedがconfirmed source repositoryに残っていない旧shapeを補正する件数。
- `developer_account_repair_count`: DIDがdeveloper handle/accountのhandle欄に入っている旧shapeを補正する件数。payload内とidentifier row内の検出数を合算します。
- `refreshed_derived_project_count`: `duplicate_candidates` / `merge_recommendation` の再計算結果が保存済み値と異なるproject数。
- `collision_count`: 正規化後に同じconfirmed canonical identifierを複数projectが所有する衝突数。candidateとprovider別の派生viewは数えません。報告のみで、自動merge/separateは行いません。

`--execute` はこれらの該当変更をまとめて保存します。たとえばdeveloper handle補正のつもりで実行しても、同時にmanual repo seed補正やmerge候補再計算が保存されることがあります。適用前にwriterを止めてDB backupを作り、dry-runの件数を確認してください。

trust graph による confirmed identifier 判定を点検する場合:

```bash
project-registry audit-trust-graph
project-registry repair-trust-graph
project-registry repair-trust-graph --execute
```

`audit-trust-graph` は読み取り専用です。`repair-trust-graph` は `--execute` なしではdry-runで、通常は trust edge/evaluation の再計算結果だけを保存します。unsupported confirmed identifier の候補降格は `repair-trust-graph --execute --downgrade-unsupported` を明示した場合だけ行います。

## 保存payloadの縮約

既存runを残したまま、current service evidence、investigationのinput/output snapshotとbundle、collector projectionを
現行の保存budgetへ揃える場合:

```bash
project-registry cleanup-storage-payloads
```

既定はread-onlyのdry-runで、層別の変更件数、UTF-8 bytes、最大削減run、snapshotの保存済みevaluation field差分を
JSONで報告します。`payload_categories`ではresource URL、service verification、LLM evidenceを分け、resource URLは
削除対象のrole、host、先頭path patternと最大削減project/runも報告します。path名だけで削除せず、現行の
project/role/host+role代表budgetを既存payloadへ再適用します。bundleの完全replayを全runへ実行するcommandではありません。

適用前にWeb、worker、enrich、別CLIを止め、DB backupを作り、dry-run結果を確認します。適用は一つのwrite
transactionで行い、commit前にforeign key checkとintegrity checkを通します。

```bash
project-registry cleanup-storage-payloads --execute
```

この操作はprojectやrunを削除しません。SQLiteの空きpageを後続write用に残さず、DB file自体も縮める場合だけ、
追加の作業領域を確保して次を使います。payload rewriteのcommit後にVACUUMするため、VACUUM失敗時もrewriteは
rollbackされません。

```bash
project-registry cleanup-storage-payloads --execute --vacuum
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

operator decisionとproject current stateはこのcommandでは削除しません。

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
benchmarkは通常replay 6件とoperator correction 3件を実行します。exportは機械的なsecret/raw text除去を行いますが、
出力自体にもmanual review必須と記録されます。実project key、時刻、handle/DID、自由記述を含む出力をそのまま
fixtureとしてコミットしません。

`manual-gold-v1.json`はbenchmarkとは別に、operator reviewから固定したidentity / service / repository判断を保持します。
DBのdecision rowを参照せず、synthetic project refと公開identifierに対する期待actionを比較用入力として使います。

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

`--max-items N`を指定すると、rolling window応答の先頭N件だけを処理します。応答自体は全件取得し、切り詰めたexecuteではsync stateを進めません。

通常の`enrich`では、accepted source repo URLがTangledの場合にrepo root HTMLとREADME rawを取得し、README内URLと短いexcerptを候補evidenceへ反映します。これは完全なTangled履歴syncやrecursive tree取得ではありません。

```bash
project-registry sync-tangled-repos
project-registry sync-tangled-repos --execute
project-registry sync-tangled-repos --create-unmatched --execute
```

UFOsは完全履歴ではありません。出力の`boundary_reached`, `gap_possible`, `complete_history`を確認してください。

## storage layout

現行DBはschema version 2です。

- `projects`: scalar + `profile_json` + `state_json`
- `project_key_aliases`: merge済みsource keyからcanonical projectへの解決
- `project_identifiers`: NSID、service/repo URL、account、DID、domain
- `project_relations`: typed relation evidence
- `project_decisions`: block/defer/separateなどのoperator判断
- `investigation_runs` / `collector_runs`: replay可能な調査履歴

旧DB schemaは現行導線の対象外です。詳細は [project-record.md](project-record.md) を参照してください。

## 環境変数

command方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
export ATPROTO_SURVEY_LLM_TIMEOUT='300'
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
export ATPROTO_SURVEY_LLM_API_STYLE='auto'
```

`ATPROTO_SURVEY_LLM_ENDPOINT`で指定するLLM endpointは、operatorが管理するtrusted endpointとして扱います。project dataから見つかったservice / resource URLには内部宛てblockが掛かりますが、このLLM endpoint呼び出しだけはLAN / localhostのendpointも許可します。Web UIからのLLM設定は未接続です。

`ATPROTO_SURVEY_LLM_TIMEOUT`はcommand/endpoint共通の1 attempt上限です。auto API styleはendpointが
互換性エラーでrequestを拒否した場合だけ別payload/endpointを試し、timeoutやresponse JSON不良では再送しません。

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

Web UIの共有tokenを環境変数で渡す場合:

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
```

現状、環境変数として読むのは上記だけです。`DNS_RESOLVER_URL` や `UFOS_API` はコード定数で、環境変数オーバーライドはありません。

`thinking` / reasoning effort を切り替えたい場合は `ATPROTO_SURVEY_LLM_REASONING_EFFORT`、または CLI の `--llm-reasoning-effort` を使います。例えば:

```bash
project-registry enrich --all --llm-reasoning-effort none
project-registry enrich --all --new-only --llm-reasoning-effort high
```
