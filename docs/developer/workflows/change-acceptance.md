# 変更の検証方法と技術的な注意点

この文書は、変更の影響を確かめるための環境の選び方、検証方法、操作に伴う技術的な注意点を説明します。

## 確認環境と接続先

| 用途 | DB | 待受アドレス（bind）/ port | 扱い |
| --- | --- | --- | --- |
| 自動test | testごとのtemp DB | testが割り当てるloopback port | 毎回新規。外部API、正本DB、実LLMへ接続しない |
| CLI/Webの手動確認 | `data/dev/survey.db` | Webは`127.0.0.1:8765` | 架空定義から生成。各worktreeで再利用・編集・リセットできる |
| 実データのread-only確認 | `data/survey.db` | Web serverは起動しない | CLI、SQL、書き捨てスクリプトから直接読む |
| 書込みを伴う実データ依存の確認 | 正本DBの複製（例: `.tmp/change-acceptance/survey.db`） | 必要ならloopback | 実データ固有の状態を使った書込み検証。復旧用バックアップとは用途が異なる |
| 実運用Web | `data/survey.db` | service設定で指定（以下の例ではport `8888`） | reload・書込みは稼働中jobや実データへ影響する |

以下の確認例は`127.0.0.1:8765`を使います。loopbackへのbindで他端末からの接続を避け、実運用とはDB・token・portを分けます。port使用中は既存processのDB・用途を確認し、別のserverを確認対象と取り違えないことが必要です。実運用の起動設定は[Web UIガイド](../../user/web-ui.md#macosでの自動起動)を参照してください。

## 変更種別ごとの確認

`bin/project-registry-test`は全unit testの入口です。外部API・正本DB・実LLMに接続せず、testごとのtemp DBを使います。文書変更ではlink、記載したcommand、現行実装との整合性が確認対象です。

testの準備は対象の依存に合わせます。project dictだけを使うtestは`tests/helpers.py`の`ProjectRecordTestCase`、DBの保存・queryを使うtestは`TempRegistryDbTestCase`を使い、project不要のtestは`unittest.TestCase`を使います。内部関数名の存在だけでなく、担当moduleの入出力や通常経路での適用・保存結果を確認します。

enrichの収集から採用・保存までを確認するtestでは、`tests/enrichment_io.py`の`EnrichmentIO`でHTTP応答・browser・DNS・LLM backendを固定できます。取得cacheをtest間で隔離し、未登録のHTTP・browser・DNS呼出しはpipelineが例外を記録して継続してもtestを失敗させます。既定の検索結果は空、LLMは利用不能です。必要な成功応答をtestで明示し、planning・collector・適用処理を一括して差し替えないようにします。段階間の順序や特定の故障位置そのものを確認するtestでは、その境界の差し替えを使います。

変更が作用する経路ごとの確認方法は次のとおりです。

- Web UI: Python側の関連API test、`node --check scripts/project_registry_web/static/app.js`、変更した主要導線の実ブラウザ確認
- heuristic: unit testに加え、既知の実誤判定fixtureまたは人間によるsample review
- DB保存契約・cleanup: temp DBでの機能確認、正本DBに対するread-only audit、必要な場合だけ複製DBで行う書込み確認
- 外部情報源の取得契約: 通常testと分け、変更内容に応じて件数・page・request数を限定した疎通・動作確認（bounded smoke test）

unit test、実データのread-only確認、実外部取得を伴う確認は、それぞれ検証できる範囲が異なります。一つの成功から別の経路の成功を推定せず、未検証の範囲を区別します。

詳細・Explainの信頼計算を変更する場合、`bin/project-registry-test -p test_trust_explanation_performance.py`で25・100・200件のLexiconに対する正規化回数の増え方、個別評価との判定・経路の一致、guard・手動seed・公開型変更、要求しないtopicの計算省略を確認できます。実時間はDB・負荷に依存するためunit testの固定秒数にはせず、実データの比較では識別子・Lexicon・relation・decision数を記録し、詳細とマージ解析、直接呼出しとHTTP、初回と反復、profile有無を分けます。

単件マージの先行絞込みを変更する場合、`bin/project-registry-test -p test_duplicate_candidate_prefilter.py`でidentifierのkind・role・採用状態、repository・service・代表URL・別名・domain・confirm対象による一致、namespace入力の全件比較、snapshot・次回の更新反映・取消しを確認します。無関係な大型JSONを持つprojectを25件から100件へ増やしても、詳細な比較入力へ組み立てる対象が増えないことを検証します。`test_duplicate_project_read.py`と`test_merge_analysis_cancellation.py`は全件・batch比較との同等性、保存不可の境界、更新競合と実HTTP切断を担当します。初回性能の比較では新process／新接続と反復を区別し、OS cacheを制御していない計測をcold diskの実測と扱いません。

heuristicの検証では、既知fixtureで誤りが解消することに加え、同種例・別の例への退行を確認します。特定のproject、URL、sourceだけに作用する特例では、一般の判定規則が改善したことを確かめられません。判定規則と利用者判断を分ける設計方針は[brief](../../brief.md#原則と責務境界)を参照してください。LLM調査・評価で得た誤判定、訂正、判断が分かれる例、有用な正例の再利用先は[事例保存の手順](llm-model-evaluation.md#作業中に得た事例の保存)で説明します。

### testの検証力を確かめる選択肢

testが通ることに加え、何を誤ると検出できるかを確認したい場合は、変更内容と確認費用に応じて次の方法を選べます。

- 既存testを変更する際は、import・配置への追従、fixtureやmock境界の変更、期待する動作の変更を分けて確認する。期待値変更や検証削除は、仕様変更・誤った期待の訂正・別のtestへの保証の移管など、根拠と併せて評価する。更新頻度やassertの増減だけでは妥当性を判断できない。
- 不具合修正では、同じ回帰testが修正前のコードで対象の不具合を検出し、修正後に成功するか比較する。testのimport不整合や環境不足による失敗は、不具合の再現と区別する。比較できない場合は、その理由と確認できた範囲を報告する。
- 保存・手動判断・統合など重要な処理では、保存の省略、判断の無視、統合方向の逆転など、想定する故障を少数選んで一つずつ入れ、testが検出するか確認する。故障注入や旧版への切替は隔離した一時checkout・コピーで行い、実運用の配信元や正本DBには作用させない。正常版の成功を基準に、故障版の失敗が対象の契約によるものかを確かめ、検証後は一時変更を復元・整理する。

期待値は仕様や具体的な入力・保存結果から確認します。同じ判断関数を使う二経路の結果比較は、置換前後の同等性には有効ですが、共通の誤りを検出できるとは限りません。内部呼出しの検査は、重い全件読込みの回避など、必要な制約を守る場合に使い、偶然の関数名・呼出し順序だけを固定しないようにします。

これらは全変更に追加する一律の手順や、定期的な全件故障注入ではありません。選んだ事例の検出数を全体の品質点数や合否閾値に換算せず、確認した契約、検出できた故障、残る未検証範囲を判断材料にします。

## CLI/Webの共通ダミーDB

初回は次の入口で、架空データ入りの小さな開発用DBを作ります。実行した入口のworktree内に作り、カレントディレクトリから保存先を推測しません。

```bash
bin/project-registry dev-db
```

Gitで管理するのは[架空定義](../../../scripts/fixtures/dev-registry.json)と生成入口です。生成したDBファイルはGit管理外の`data/dev/survey.db`に保存され、繰り返す手動確認に再利用できます。生成処理は既存のproject codecと調査履歴APIでデータを保存し、fixtureはschemaを持ちません。自動unit testはtestごとのtemp DBを使い、この可変DBに依存しません。

| 登録プロジェクト（key末尾） | 代表的な確認導線 |
| --- | --- |
| Demo Field Notes（`0001`） | 紹介候補、採用済みサービスと判断、成功の調査履歴 |
| Demo Sketch Board（`0002`） | 要レビュー、未確定サービス候補、除外判断、候補relation、部分失敗の履歴と根拠 |
| Demo Garden Log（`0003`） | 紹介済みfilter、紹介候補からの除外 |
| Demo Archive Kit（`0004`） | `stalled`の絞り込み・追跡状態変更 |
| Demo Sprout（`0005`） | 未調査、詳細の不足情報・調査履歴なし |

keyは`proj:dddd00000001`〜`proj:dddd00000005`です。日時は固定の表示例であり、鮮度判定やqueueの全件数・順位を恒久的に固定するfixtureではありません。表示用の成功／失敗・利用可能性・採用判断はすべて作り物で、実在サービスの状態を示しません。紹介候補として表示されることと、紹介文が完成していることも区別します。

通常CLIも同じDBへ接続できます。共通`--db-path`はcommandの前へ置き、省略すると正本DBになります。

```bash
bin/project-registry --db-path data/dev/survey.db show --all
bin/project-registry --db-path data/dev/survey.db show -p proj:dddd00000002 --format json
bin/project-registry --db-path data/dev/survey.db promotion-queue
bin/project-registry --db-path data/dev/survey.db review-queue
bin/project-registry --db-path data/dev/survey.db set-tracking-state -p proj:dddd00000005 --tracking-state stalled
bin/project-registry --db-path data/dev/survey.db replay-investigation --run-id inv:dev-sketch-partial
```

queueが提案する次のCLIコマンドには接続中DBの絶対pathを含めます。`--db-path`はregistry接続だけを選び、設定・認証・外部アクセスを隔離するモードではありません。外部取得や実LLMを使わず生成でき、上記のread/edit/replayも外部取得を行いません。enrich・sync・service確認を実行したことにはならず、それらを確認する場合は別途project数・request数・時間等を限定します。一括enrichを別DBで確認する際は`--checkpoint-path`をそのDB用に明示するか`--no-checkpoint`を指定します。

### リセットと上書き拒否

既存DBはそのまま再利用します。初期状態へ戻す場合は、そのDBを使うCLI、Web server、workerをすべて停止してから実行します。

```bash
bin/project-registry dev-db --reset
```

保存先は固定で、`dev-db`へ任意pathや共通`--db-path`は渡せません。通常生成は既存fileを拒否し、リセットは生成元markerと現行schemaを持つDBだけを許可します。DB・親directoryのsymlink、DBやSQLite sidecarのhard link/symlinkも拒否します。正本DBや既存の空DBをこのpathへ移してもリセットできません。未生成なら`--reset`でも新規作成します。

リセットは同じDB file内の単一transactionで全tableのデータを再投入し、途中失敗はrollbackします。WALを解除してexclusive transactionを取り、SQLiteが使用中と判断する場合は待たずに拒否します。接続を閉じたまま待機中のserver／workerの検知は保証しないため、事前停止は必要です。リセット後の通常接続は既存仕様どおりWALを有効にします。DB外の`job-schedules.json`、checkpoint、browserのlocalStorageはリセットしません。

`data/dev/`は正本の定期設定やcheckpointがある`data/`と分けます。定期実行を有効にすると手動操作以外でもjobが動くため、通常の画面確認例では定期実行を使いません。

## Web UIの実ブラウザ確認

確認用serverは実運用tokenや`direnv`を使わず、loopbackへ起動します。

```bash
bin/project-registry-web \
  --host 127.0.0.1 \
  --port 8765 \
  --db-path data/dev/survey.db \
  --token web-ui-dev
```

`http://127.0.0.1:8765/?token=web-ui-dev`を開き、変更した画面への移動、表示、主要操作を確認します。書込み操作は開発用DBだけに対して行います。画面幅や自動遷移を変えた場合は、対象となるdesktop / mobile幅と遷移前後も確認します。

unit testでしか再現できない内部状態はそのtestで確認し、実ブラウザでは利用者の操作経路を確認します。実ブラウザの成功だけでは、内部の故障・取消し等を網羅した確認にはなりません。

README等の説明用画面も、このダミーDBで撮影します。`docs/images/`に画像を保存し、掲載文で架空データと明示します。画面の導線・ラベルを変更した時は本文と画像を併せて見直し、本番token・実データ・設定画面の環境固有情報を含めません。

## Enrichの逐次・先行取得比較

外部応答を固定して制御方式の意味と時間を比較する場合は、次をworktreeで実行します。正本DB・外部取得・共有LLMには接続しません。各sampleの一時DBは`.tmp/enrich-overlap/`内に作成され、sample終了時に自動削除されます。

```bash
mkdir -p .tmp/enrich-overlap
bin/_project-registry-python benchmark scripts/benchmark_enrich_overlap.py \
  --output .tmp/enrich-overlap/comparison.json
```

通常のapplication・pipeline・保存・checkpointを使い、応答を返すcollectorとLLM backendだけを固定します。6件×4条件×各方式3回を別processで測り、逐次と先行の実行順を交互にします。再利用なし、実際のtriage再利用hit、取得がLLMより遅い場合、待ち時間ゼロを分け、同じproject・候補・input/output・bundle・評価・観測のhashと固定応答の呼出し数を比較します。意味の比較から除くのはrun ID・elapsed計測とそのJSONサイズ・runtime保存用情報・promotionのclock値です。一般の内容差や候補順は除きません。

時間は設定した模擬待機を含むbatch実経過、メモリは各processのピークRSSです。ウォームアップを含むRSSと実LLM processのメモリを混同しません。HTTP実request数や実ネットワークのrate limitを測る実験ではなく、実週次の短縮率をこの値から断定しません。故障・取消し・alias/merge・別接続更新・CLI/worker経路は`test_enrich_prefetch.py`で確認します。実環境を比較する場合は対象・request数・時間を限定し、競合中のjobやモデル比較がないことを別途確認します。

## 正本DBの確認と複製

現在のproject、件数、保存状態を読む確認には、正本DBのread-only接続を使えます。以下の`mode=ro` URIはDB書込みを禁止します。CLIのdry-run等を使う場合も、名前だけでread-onlyと判断せず、そのcommandの接続・保存処理を確認する必要があります。

```bash
sqlite3 'file:data/survey.db?mode=ro' 'SELECT COUNT(*) FROM projects;'
```

通常のCLI/API、画面遷移、表示、入力は`data/dev/survey.db`で確認できます。保存済み調査履歴とbackground jobの実行状態は別物で、実workerの進捗・停止・再開の確認には別途制御したjobが必要です。実データ固有の誤判定・状態・件数・容量が必要な場合は、正本DBのread-only確認または複製を使い分けます。複製で確認する対象には次があります。

- migration、cleanup、merge、enrichなど、確認中にDBを書き換える処理
- 保存前後の差分や再実行時の冪等性
- 正本相当の件数やDB容量を使った書込み時間、lock、transaction
- read-onlyであることを確認できないcommandやスクリプト

稼働中のSQLiteはDB本体のほかにWALへcommit済みデータを保持するため、本体だけのfile copyでは同じ状態を再現できません。整合した複製にはSQLiteのonline backupを使います。以下は検証用DBを作る例で、正本の内容を書き換えるcommandではありません。

```bash
mkdir -p .tmp/change-acceptance
sqlite3 data/survey.db ".backup '.tmp/change-acceptance/survey.db'"
```

複製をWebで使う場合は、[実ブラウザ確認](#web-uiの実ブラウザ確認)の`--db-path`を複製先へ変更します。SQLiteは接続中にWAL/SHMを使うため、複製の削除・差替えには使用processの停止が必要です。再利用する小さな開発用DBと、実データを含む容量の大きい複製では、容量・非公開情報の扱いも異なります。

`.tmp/`内の古いSQLite複製は[cleanup-workspace-temp](../../user/cli.md#auditrepairstorage-maintenance)で確認・削除できます。全unit test起動時の容量警告が出た場合は、この清掃対象と保持中の資料を確認します。

### sandboxでの読み取り計測

Pythonは既存の`bin/_project-registry-python`が選ぶ環境を使い、SQLite接続は`mode=ro`を維持します。bytecode出力を止める必要がある場合は`PYTHONDONTWRITEBYTECODE=1`を指定します。計測結果や依存ライブラリのcacheへの書込みと、DB読取りに必要な権限は分けて確認します。

WAL形式では、`mode=ro`でも`-wal`・`-shm`が存在しない場合に補助ファイルの作成が必要になります。読取が失敗したら、エラーと補助ファイルの状態を確認します。一度の通常のSQLite接続で補助ファイルが揃えば、以後は書込権限なしで読める場合があります。

WALは未反映のcommit済みデータを持つため、単なるcacheとして扱いません。稼働中の正本に`immutable=1`やロック無効化を指定したり、journal modeを変更したり、補助ファイルを手作業で作成・削除したりして回避しません。

## 実運用への反映

### 復旧時点と検証用複製

書込み検証用の複製は、操作を試すためのDBです。変更前へ戻すための復旧用バックアップとは用途が異なり、検証で変更した複製を変更前状態の退避として扱うことはできません。SQLiteの退避にも[online backup](#正本dbの確認と複製)を使い、必要な復旧時点の内容を保てるかが確認点になります。

既存の復旧手段で戻せる時点と、操作直前の状態が必要かは環境・変更内容に依存します。必要な復旧時点を満たせるかが、追加退避を判断する材料です。正本変更と復旧はどちらも実データへの書込みです。

### HTTPS入口の運用確認

Tailscale Serveの実運用設定変更を明示的に依頼された場合は、[Web UIガイドの設定・解除手順](../../user/web-ui.md#tailscale経由のhttps)に従います。転送先は実運用の`127.0.0.1:8888`で、worktreeの確認用serverへ向けません。設定前後の`tailscale serve status --json`で既存入口が不変であること、専用HTTPSポートだけが追加・解除されたこと、Funnelが有効になっていないことを確認します。

追加後は証明書検証を有効にしたHTTPSで、ページと静的asset、無認証・不正tokenのAPI拒否、正しいtokenでの設定・一覧APIを確認します。tokenをコマンド引数・URL・ログ・画像・検証成果へ含めず、実運用環境からprocess内へ読み込みます。実ブラウザではtoken入力、一覧・詳細・Settingsの表示と再読込をread-onlyで確認し、DB更新・job開始・server reloadは行いません。HTTP入口と同じserver instance・DBを返すことも照合します。設定変更のみならアプリの再起動は不要です。

serverを動かす端末自身からの成功は、別端末からの到達・tailnetのアクセス許可を保証しません。別端末・端末再起動後・証明書の自動更新は別の確認対象です。

### アプリ変更の反映

Python本体の更新は通常reloadで切り替わりません。[Python環境の管理](../../user/python-environment.md)で独立世代を準備し、`PROJECT_REGISTRY_PYTHON_ENV`で候補を指定すると、実運用環境を切り替えずに全testと開発DBのCLI/Web/実workerを確認できます。実運用の世代切替には[停止・切替・新規起動](../../user/web-ui.md#python環境の切替)が必要で、APIのPython実pathと世代で切替結果を照合できます。

変更の検証と実運用serverへの反映は別作業です。Web UIの設定画面のreload操作は稼働中serverを再起動します。service commandと設定画面での操作方法は[Web UIガイド](../../user/web-ui.md#ジョブ履歴と再起動)を参照してください。

実運用serverのreloadは実行中workerを停止し、process内のjob一覧とeventを失います。保存済み調査履歴は残りますが、停止前の実行状態をそのまま復元するものではありません。反映前のactive jobの確認と、反映後の稼働コード・API・画面の確認が、影響と適用結果を判断する材料になります。

## Maintenance適用の確認

共有hostingのtenant境界を定める接尾辞（hosted suffix）の変更に伴う分割監査は`audit-source-config`、URL監査はread-onlyの`audit-url-fields`を使います。判定規則の変更を既存DBへ反映する場合は、まず`repair-db`で足りるかを確認し、補助pageやblock/defer等の採用抑止条件（guard）に該当するURLのservice採用、repo/NSIDの採用条件を満たさない`defines_schema`等を監査し、dry-runの全差分を確認します。`repair-db --execute`は特定補正だけを選ばず、同じsummaryに含まれる正規化・派生値更新も保存します。

適用中にenrich・CLI・Webから別の書込みが入ると、dry-run時点の差分と適用結果を同じ前提で比較できません。競合する書込みの有無、[必要な復旧時点](#復旧時点と検証用複製)、保存後の再監査が確認点です。確認済み範囲は、commandの成功だけでなく保存内容と再監査の結果で判断します。
