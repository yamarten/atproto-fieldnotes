# Project Registry Roadmap

現在の実装状況は[status.md](status.md)、確定済みの保存形や責務分担は
[project-record.md](project-record.md)、[registry-internals.md](registry-internals.md)、
[investigation-model.md](investigation-model.md)に移しました。

この文書は、まだ本体仕様に取り込んでいない展望と TODO を置くためのメモです。

## 目的の優先順位

1. 紹介するかどうかを安定して判断できる
2. 紹介文を作るための核データを保てる
3. 運用上必要な review / merge / evidence を保てる
4. 将来 app record を生成したくなった時に、必要情報が足りない状態を避ける

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon 完全互換を今すぐ満たすこと
- 不確かな app metadata で `app` を埋めること
- export の都合だけで core や ops の設計を歪めること

紹介フローを主導に置き、その延長で app export 可能性を確保する、という順序を維持します。

## 直近の実装順

ここでは、通常運用開始後に順番を決めて進める未完了作業だけを扱います。
完了済みの日常運用安定化8項目はstatusとGit履歴へ移し、この文書では管理しません。
過去の広域棚卸しで残すべき候補は、このroadmapまたは役割別notesへ移しています。

### DB-level policy marker

DB-level migration/data policy markerは直近優先から外します。

- 現状は`schema_version=2`だけで、旧方式v2や試験生成v2とのpolicy世代差はDB単体では識別できない
- ただし正本DBが基本1つで、巻き戻しや複数policy世代DBの並行運用も想定していないため、現時点の実利は小さい
- 将来、複数DB、再migration、schema v2内でのdata policy世代分岐が必要になった場合に再検討する
- 実装する場合は、open時の自動付与ではなく、markerなし既存v2のread-only audit合格後にだけadditiveに付与する

process間lockまたはpreflightは、次の破壊的maintenanceを追加する時に再評価します。現状の個人ローカル運用ではoffline手順を維持します。
### 必要が生じた時に検討するもの

現状の運用を妨げてはいませんが、具体的な不便や保守上の問題が出た場合に検討します。

- relation / ownership graph
  - 興味はあるが直近優先ではない
  - まずはviewだけ。write actionは付けない
- fuzzy search
  - 通常検索で不足が明確になった場合に追加する
  - 単なるtypo toleranceではなく、実際のproject探索に役立つ検索条件を先に定める
- cache / source budget visibility
  - 表示だけなら調査時に便利
  - Webからbudgetを直接編集するのは別判断
- keyboard shortcut
  - 操作系は入れない
  - 詳細の前/次切り替え、detail tab jump程度に留める
- static JS / CSS / route table分割
  - 編集衝突が増えた時、または責務を追いにくくなった時点で分割する
- app record / app lexicon export
  - 今すぐ完全互換を目標にしないが、紹介用profileとconfirmed identifierからread-only export reportを作れる状態は将来候補として残す
  - exportのために不確かな`profile_json.app`を埋めず、まず不足fieldと根拠不足をvalidation reportとして出す
- security / safety整理
  - LAN、他利用者なし、必要に応じてTailscale越しという公開範囲は維持する
  - その前提で必要十分な対策に留める
- job event履歴永続化
  - checkpoint discoveryだけでは実行履歴を追えない場合に追加する
  - 保存量とretention policyを先に決める
- v2以降のincremental schema migration runner
  - 次のschema改版が具体化した時点で、backup/transaction/validation契約と一緒に実装する
- SQLite接続自体のread-only化
  - 現状のWeb `--read-only`はHTTP POST/job controlを拒否するapplication-level mode
  - 日常運用で使う必要が生じた場合にURI `mode=ro`または`PRAGMA query_only`を追加する

### 長期候補

直近の運用安定化には含めません。必要性や具体的な利用場面が見えたものから再検討します。

- evidence / ownership graphの本格表示
- Investigation notebook mode
- Assisted review
- LLM triage side panel
- multi-project compare
- review session mode
- bulk maintenance dashboard
- source health dashboard
- plugin-style collector configuration

## 将来の論点

### 調査 pipeline の残件

次の優先候補だけを roadmap として残します。接続済み範囲と恒久的な契約は
[investigation-model.md](investigation-model.md) を正本とします。

- 既存DBのdefer/block/manual correctionから、実際の誤判定fixture候補を抽出する
- OAuth `include:` permission setを再帰解決し、循環と取得失敗をsource状態へ残す
- Tangled timeline/community search/bot adapterを必要性評価後にopt-in接続する
- UFOs rolling windowのgap検知後に、別sourceで補完する運用を決める
- browser service verificationの実Playwright smoke testと複数surface統合を追加する
- repo contents、PDS、Bluesky sessionを含む全collectorへbudget/cache policyを統一する
- fixture export時のsanitize運用を実データ追加前に固定する
- investigation viewerのbounded evidenceで不足する具体例を収集する
- LLM model comparison harnessを追加し、品質・速度・メモリを、固定evidenceでのtriage差分と複製DBでのfull enrich差分に分けて測る
- `resource_url_records` の既存DB cleanup dry-runを追加し、role不明の同一site content URLを削除してよい範囲を確認する

未決定:

- 複数projectを跨ぐ永続circuit breaker

設計途中の詳細は次の作業用文書へ分離しています。trust graphの現行仕様は
[registry-internals.md](registry-internals.md)を正本とします。

- ownership / identity: [identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- service verification: [service-verification.md](notes/service-verification.md)
- replay / fixture corpus: [evidence-replay.md](notes/evidence-replay.md)
- collector failure policy: [collector-reliability.md](notes/collector-reliability.md)
- LLM model comparison: [llm-model-evaluation.md](notes/llm-model-evaluation.md)

### ステータス系

現行の canonical は `service_status` です。

将来的には、次の別軸を分けたくなる可能性があります。

- `paused`
- `unmaintained`

候補:

- `service_status`
  - 到達性、公開状態、招待制、死活
  - `unreleased` まではこの軸に含める
- `app.status`
  - app record 向けの公開・保守状態

現時点では分けません。必要になった時点で `app.status` を追加します。

### app metadata の拡張

`app` は今は低優先度です。確かな情報だけを入れ、空 object を許します。

将来、次を追加で整備する余地があります。

- `images[]`
- `langs[]`
- `lexicons.produces[]`
- `lexicons.consumes[]`
- `account_indicators[]`

`links[]` の初期実装は入っていますが、現時点では次の最小 role に限定しています。

- `website`
- `webApp`
- `sourceCode`
- `docs`
- `support`
- `status`
- `privacyPolicy`
- `termsOfService`

次は必要になった時だけ増やします。

### 境界項目の再評価

現時点では evidence として扱っているが、将来 app metadata に昇格するかもしれない項目です。
ただし今は、紹介判断や要約補助に使う材料としての意味が強く、app 側へ急いで寄せる必要はありません。

- `inspired_by`
- `features`
- `developer_accounts`

`review-out-of-scope` の derivative 判定は、Bluesky従属utilityとatproto技術ツールを
十分に分離できていません。現状は候補提示だけに使い、自動実行しません。将来は
`technical_interest_level`、owned surface、独自データモデルの有無を加味して、
紹介スコープ外と技術調査対象を分離します。

### service URL と補助ページの分類

現行の補助ページ判定は、confirmed service URLへの過剰昇格を防ぐための暫定heuristicです。
path名の名指しだけでは信頼性が低いため、将来的には次の材料を組み合わせて分類します。

- top pageからのリンク関係、anchor text、rel/canonical、redirect/canonical URL
- page title、meta description、本文のdocs/login/support/statusらしさ
- service root、app shell、auth entry、docs/support/status pageの役割分離
- 手動seed、operator decision、過去probe結果との整合

非目標として、pathがあるURLを一律に落とすことや、`/about`などの名前だけで恒久的に分類を確定することは避けます。

### atproto寄与度の独立評価

現行の level、score、evidence と rule-based v1 は [registry-internals.md](registry-internals.md) を正本とします。
未接続の候補は次です。

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

### Web UI 詳細検索

Web UIのschema v2検索は接続済みです。検索拡張候補は次です。

- SQLite FTS table の追加
- typo tolerance / edit distance
- 自然語・概念検索
  - 例: 「ブログサービス」で blogging / publishing / CMS / article posting 系projectを探す
  - 単純な部分一致ではなく、project category / feature tag / summary evidence の整備とquery expansionを前提にする
- OR group や否定条件を含む query builder
- 保存済み検索条件
- URL query への filter 状態の永続化
- offset / cursor pagination
- raw JSON 任意パス検索

### Web UI: 次の接続候補

未接続 / 将来候補:

- sync/reviewのcheckpoint再開
  - 現状はsource/candidate境界のprogressと停止まで
- server再起動後のprocess job一覧・event復元
  - enrich checkpointの再発見と再開は接続済み
  - job eventを永続化する場合は保存量とretention policyを先に決める
- `assist-db-edit`の共通background job化
- evidence表示上限の調整
  - run一覧、bounded evidence、replay差分は接続済み
  - raw HTMLやREADME全文の表示は非目標を維持する
- role単位のidentifier decision
  - 現行confirm/block/defer/splitは`kind + value`単位で、同じ値を持つ全roleへ作用する
  - 実例でrole単位の拒否が必要になった時に、decision targetとruntime guardを拡張する
- Webからのprofile編集範囲拡大
  - 現状の通常編集は`name`とoperator向け`notes`だけ
  - 必要になったら`tagline`、`summary`、`website_url`、`profile_json.app.links`などをfield単位preview、revision check、根拠表示付きで追加する
  - identifier/relation/decisionの正本を直接JSON編集させず、既存review actionや専用mutationを優先する
- PWA/local app化の再評価
  - オフラインDB操作は非目標のまま、静的asset cache、standalone表示、ホーム画面起動、軽いreload耐性に実利があるかを見る
  - Firefox中心の運用ではinstallabilityの恩恵が限定的なので、manifest/service workerの小規模追加で済む場合だけ候補にする
  - build stepや複雑なcache invalidationが必要なら優先しない

原則 CLI に残す保守操作:

- `audit-source-config` の hosted suffix split 監査
  - 既存 project detail の単体 split とは別導線
  - tenant group と仮分割後 merge candidate を確認してから実行する
  - 複数 identifier を動かすため、通常のモバイル review 導線には出さない
- `audit-url-fields`
  - 原則 read-only report
- 判定feedback後の既存DB cleanup audit
  - confirmed service URLに補助ページやdefer/block配下URLが残っていないか確認する
  - repoとNSIDの両方がacceptedではない `defines_schema` relationと、それに連動したowned NSIDを棚卸しする
  - dry-runで影響projectとidentifier/relation差分を出し、backup後にrepairまたは個別cleanupを実行する
- trust graph残件
  - `trust-graph-v1` の本体方針、URL相互trust、collection ownership、identity trust評価、audit/repair/explainは接続済み。現行仕様は[registry-internals.md](registry-internals.md)を正本にする
  - `did` / `handle` / `domain` のtrust評価結果を、通常enrich導線で新しいconfirmed developer accountへ自動保存昇格する処理は未接続
  - accepted GitHub repoからReadTheDocsなどのdocs siteへ明示的にlinkしている場合、README内の任意URLとは分けて`repo_links_service`へ昇格できる条件を検討する
- `cleanup-app-links`
  - dry-run を先に表示し、execute は明示確認を必須にする
  - Web UI には出さず CLI で扱う

確認済みの小規模不具合:

- `split-project --new-project-key`は新規keyを既存projectとして解決してから存在を拒否するため、明示key指定が実質使えない
