# Project Registry Roadmap

確定済みの保存形や責務分担は [project-record.md](project-record.md)、
[registry-internals.md](registry-internals.md)、[investigation-model.md](investigation-model.md) に移しました。

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

## 将来の論点

### 調査 pipeline の残件

次の優先候補だけを roadmap として残します。接続済み範囲と恒久的な契約は
[investigation-model.md](investigation-model.md) を正本とします。

- 本番ミラー上のdefer/block/manual correctionから、実際の誤判定fixture候補を抽出する
- OAuth `include:` permission setを再帰解決し、循環と取得失敗をsource状態へ残す
- Tangled timeline/community search/bot adapterを必要性評価後にopt-in接続する
- UFOs rolling windowのgap検知後に、別sourceで補完する運用を決める
- browser service verificationの実Playwright smoke testと複数surface統合を追加する
- repo contents、PDS、Bluesky sessionを含む全collectorへbudget/cache policyを統一する
- investigation runのretention、pruning、export時sanitize policyを決める
- Web UIからrun/evidence diffを閲覧する必要性を再評価する

未決定:

- investigation runのretention
- 複数projectを跨ぐ永続circuit breaker
- legacy project fieldの廃止順
- Web UIからのevidence inspection導線

設計途中の詳細は次の作業用文書へ分離しています。

- ownership / identity: [identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- service verification: [service-verification.md](notes/service-verification.md)
- replay / fixture corpus: [evidence-replay.md](notes/evidence-replay.md)
- collector failure policy: [collector-reliability.md](notes/collector-reliability.md)

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

`review-out-of-scope` の derivative 判定は、Bluesky従属utilityとATProto技術ツールを
十分に分離できていません。現状は候補提示だけに使い、自動実行しません。将来は
`technical_interest_level`、owned surface、独自データモデルの有無を加味して、
紹介スコープ外と技術調査対象を分離します。

### ATProto 寄与度の独立評価

現行の level、score、evidence と rule-based v1 は [registry-internals.md](registry-internals.md) を正本とします。
未接続の候補は次です。

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

### provider 別 convenience field

次は evidence から再構成できるなら、将来的に縮退候補です。

- `github_repo_urls`
- `tangled_repo_urls`

### Web UI 詳細検索

現行のfilterとAPIは [project-registry.md](project-registry.md) を正本とします。将来候補は次です。

- SQLite FTS table の追加
- typo tolerance / edit distance
- OR group や否定条件を含む query builder
- 保存済み検索条件
- URL query への filter 状態の永続化
- offset / cursor pagination
- raw JSON 任意パス検索

### Web UI: core 追従候補

未接続 / 将来候補:

- Enrich All checkpoint の運用改善
  - サーバー再起動後に `data/enrich-runs/*.json` を一覧し、Jobs / Sync から選んで再開する導線は未実装
  - UI job state と checkpoint state の履歴表示をどう同期するかは未検討

原則 CLI に残す保守操作:

- `audit-source-config` の hosted suffix split 監査
  - 既存 project detail の単体 split とは別導線
  - tenant group と仮分割後 merge candidate を確認してから実行する
  - 複数 identifier を動かすため、通常のモバイル review 導線には出さない
- `audit-url-fields`
  - 原則 read-only report
- `cleanup-app-links`
  - dry-run を先に表示し、execute は明示確認を必須にする
  - Web UI には出さず CLI で扱う

## TODO

- `app.status` を separate field として導入する必要が出たら、`service_status` からの写像を定義する
- `app.links[]` の role を増やすなら、`appStore` / `playStore` / `fdroid` / `webManifest` / `demo` / `api` / `package` / `lexicon` / `collection` / `directory` の採用基準を定義する
- `github_repo_urls` / `tangled_repo_urls` の保存を止めるか、caller と export の整理後に判断する
- URL field migration
  - 保存 payload / API payload / export から compatibility view を落とせるか確認する
  - provider ごとの account/resource scope extractor と `owned_service / likely_owned_service / upstream_dependency / unknown` relation classifier を追加する
- `inspired_by` を app metadata として昇格させるか再判断する
- Web UI 詳細検索が遅くなった場合は scalar filter を SQLite `WHERE` に寄せ、必要なら FTS を検討する
- `atproto_contribution_level` の docs / README evidence と LLM 補助分類を追加する
- 調査強化
  - service status signal の粒度改善、LLM evidence の service/lexicon prompt 分岐、browser確認レーンを検討する
  - 詳細は [service-verification.md](notes/service-verification.md)
- 旧 field 互換の名残を整理する
  - 内部 shape にはまだ `primary_nsid`, `bluesky_handles`, `tangled_handles`, `introduced` など legacy 的な field が残る
  - `confirmed_nsids`, `resolved_nsids`, `input_nsids`, `recorded_lexicon_namespaces`, `lexicons` の record / ops 境界を明確にする
- Web UI で `candidate_developer_accounts` を確認・昇格する操作を設計する
