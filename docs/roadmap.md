# Project Registry Roadmap

確定済みの保存形や責務分担は [docs/project-record.md](project-record.md) と [docs/registry-internals.md](registry-internals.md) に移しました。

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

### Research pipeline Phase 0 後続

Phase 0 では GitHub repo と GitHub / Bluesky search だけを structured evidence 経路へ接続しました。次の優先候補は以下です。

- lexicon resolution / recent record sampling を非mutating collector化する
- service probe とfirst-party follow-upを分け、source別timeout/retryを設定する
- deep resource page収集を通常service probeから分離する
- `CollectorFailure.category` をrun-level backoff / circuit breakerへ接続する
- evidence fixture corpusへ実際の誤判定projectを追加し、heuristic version比較を行う
- raw evidenceを通常運用で保存する必要が出た時は、DB field追加より先にartifact retentionと機密情報除去を決める

未決定でありPhase 0には含めないもの:

- evidence artifactの保存場所とretention
- collector間の共通retry policy
- legacy project fieldの廃止順
- Web UIからのevidence inspection導線

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

`category=auth_only` とは別に、「ATProto が無くてもサービスの主要価値が成立するか」を
評価する rule-based v1 を実装済みです。`ops_json` に evidence 付きの寄与度を保存し、
review queue と promotion queue の優先度に使います。

- `atproto_contribution_level`
  - `core`
    - ATProto record、repository、lexicon、federated identity などが主要機能を構成する
  - `substantial`
    - ATProto が主要機能の一部を担い、除くとサービスの価値が明確に下がる
  - `optional`
    - ATProto integration はあるが、除いてもサービスの中心機能は成立する
  - `auth_only`
    - sign-in / identity provider としてのみ使う
  - `incidental`
    - link、share、cross-post など周辺機能に限る
  - `unknown`
- `atproto_contribution_score`
  - queue の sort 用。level の説明可能性を失わない範囲で数値化する
- `atproto_contribution_evidence`
  - owned / consumed NSID
  - record sample
  - repository / README の実装根拠
  - service docs の説明
  - sign-in 導線だけか、主要データモデルまで使うか

LLM は evidence の要約と暫定分類に使えますが、単独で高スコアを確定しません。
紹介対象では `core` / `substantial` を優先します。manual review は寄与度そのものが不明な時、
または確認結果によって keep / reject、merge、紹介可否、tracking 優先度が変わる時に寄せます。

実装済みの v1 は次の保守的な rule です。

- resolved NSID または lexicon schema sample があれば `core`
- recent record sample または record count を持つ collection observation があれば `substantial`
- `category=auth_only` で強い protocol surface が無ければ `auth_only`
- input NSID または external lexicon hint だけなら `optional`
- それ以外は `unknown`

未接続:

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

### provider 別 convenience field

次は evidence から再構成できるなら、将来的に縮退候補です。

- `github_repo_urls`
- `tangled_repo_urls`

### Web UI 詳細検索

v1.1 の filter UI と API 拡張は実装済みです。以下は実装時の判断と、後続で最適化する場合の境界を残すための履歴です。

現状の `Search` タブは自由語 1 本だけを受け取り、`project_search_candidates()` の score 順で project を返します。候補探索としては使えますが、調査対象の棚卸しや「未紹介かつ candidate URL が残っている active project」のような絞り込みには不足しています。

次の追加では、自由語検索と structured filter を AND 条件で組み合わせる方針にします。SQLite schema は増やさず、既存の `projects` scalar columns と JSON shape から判定します。

#### v1.1 の対象（実装済み）

API は既存の `GET /api/projects` を拡張します。

```text
GET /api/projects
  ?q=
  &tracking_states=
  &service_statuses=
  &contribution_levels=
  &technical_interest_levels=
  &statuses=
  &categories=
  &introduced=
  &has_candidates=
  &has_errors=
  &promotion_hold=
  &review_kinds=
  &seen_since=
  &enriched_before=
  &sort=
  &limit=
```

filter の意味:

- `q`
  - 現在の自由語検索を維持する
  - `project_key`, name, seed, NSID, website, service URL, repo URL, merge alias を検索する
- `tracking_states`
  - 複数指定可
  - `active`, `watchlist`, `stable`, `stalled`, `out_of_scope`, `archived`, `ignore`
- `service_statuses`
  - 複数指定可
  - 現行 `service_status` enum を使う
- `contribution_levels`
  - 複数指定可
  - `atproto_contribution_level` に一致
- `technical_interest_levels`
  - 複数指定可
  - `technical_interest_level` に一致
- `statuses`
  - 複数指定可
  - project record の `status`
- `categories`
  - 複数指定可
  - `category` と `app_category` のいずれかに一致
- `introduced`
  - `yes`, `no`, `any`
  - v1.1 では `introduced_url` の有無で判定する
- `has_candidates`
  - `yes`, `no`, `any`
  - v1.1 では service / website / repo candidate のいずれかが残っているか
- `has_errors`
  - `yes`, `no`, `any`
  - `last_run_errors` の有無
- `promotion_hold`
  - `yes`, `no`, `any`
  - active な `promotion_deferrals` の有無
- `review_kinds`
  - 複数指定可
  - `build_review_queue_item()` が返す signal kind のいずれかに一致
- `seen_since`
  - ISO date
  - `last_seen_at >= value`
- `enriched_before`
  - ISO date
  - 未 enrich、または `last_enriched_at < value`
- `sort`
  - `relevance`, `last_seen_desc`, `last_enriched_asc`, `name_asc`
  - `q` がある時の既定は `relevance`、無い時の既定は `last_seen_desc`
- `limit`
  - v1.1 は既存と同様に上限付き件数指定
  - offset pagination は後段

UI は `Search` タブ内に次を置きます。

- 常時表示:
  - 自由語入力
  - Search / Clear
  - 結果件数
- 折り畳み `Filters`:
  - tracking state
  - service status
  - ATProto contribution
  - technical interest
  - introduced
  - candidates remaining
  - errors
  - promotion hold
  - category
  - review signal kind
  - last seen / last enriched
  - sort
- filter 適用中:
  - compact な chip 表示
  - `Clear filters`

モバイルでは filter panel を初期状態で閉じます。project 結果の `Previous` / `Next` 導線は現在の検索結果順を使います。

自由語欄の `key:value` 演算子も実装済みです。browser 側で解釈し、API にはこの節の query parameter として送ります。API 自体に別 DSL は追加していません。

#### 実装上の分離

- 自由語 candidate score:
  - `project_search_candidates()` の既存 score を再利用する
- structured filter:
  - 新しい pure function `project_matches_search_filters(project, filters)` に分ける
- result projection:
  - `project_summary()` を維持する
- review signal filter:
  - 必要な時だけ `build_review_queue_item()` を呼ぶ

初期実装は全 project の in-memory filter でよいものとします。個人用ローカル DB で遅さが問題になった時点で、scalar filter を SQLite `WHERE` に寄せ、必要なら別途 FTS を検討します。

#### v1.1 ではやらないこと

- SQLite FTS table の追加
- typo tolerance / edit distance
- OR group や否定条件を含む query builder
- 保存済み検索条件
- URL query への filter 状態の永続化
- offset / cursor pagination
- raw JSON 任意パス検索

### Web UI: core 追従候補

2026-06-01 時点で core 側の追加機能を Web UI と照合したメモです。

通常導線へ反映済み:

- sync の `create_unmatched` 既定値は Web UI でも `true`
- promotion queue のグローバル sort、`technical_insight_candidate`、review queue の contribution score 補正は、既存 queue builder を再利用しているため Web UI の一覧順と signal に反映される
- title filter、authority service URL 保持、lexicon scope 分類は enrich / queue の core 処理として反映される

通常導線へ追加済み:

1. identifier の一時保留（実装済み）
   - confirmed / candidate row に `Defer 30d`
   - Guards に `deferred_identifiers` の期限、note、`Clear defer`
   - 恒久 block と一時保留を視覚的に分ける
2. promotion の理由付き保留（実装済み）
   - project detail に active な期限、理由、`Hold 7d`、解除
3. project detail の調査情報（実装済み）
   - `atproto_contribution_level / score / evidence`
   - `technical_interest_level / score / reasons / summary`
   - `promotion_blockers`
   - `resource_url_records`
4. Search filter の追加（実装済み）
   - contribution level
   - technical interest level
   - active promotion deferral の有無

未接続 / 将来候補:

- Enrich All checkpoint の運用改善
  - CLI 側と Web background job 側の checkpoint helper 接続は実装済み
  - Web の停止は project 境界での cooperative stop として実装済み
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

調査情報の専用表示と Search filter は接続済みです。運用画面として列挙した audit / cleanup 操作は未接続で、引き続き CLI を使います。

## TODO

- `app.status` を separate field として導入する必要が出たら、`service_status` からの写像を定義する
- `app.links[]` の role を増やすなら、`appStore` / `playStore` / `fdroid` / `webManifest` / `demo` / `api` / `package` / `lexicon` / `collection` / `directory` の採用基準を定義する
- `github_repo_urls` / `tangled_repo_urls` は runtime では `source_code_urls` / confirmed `source_repo_records` から provider view として再構成する方針
  - 保存 field は compatibility view としてまだ残す
  - 最終的に保存を止めるかは、caller と export の整理後に判断する
- URL field migration
  - `audit-url-fields` の raw 保存監査を基準に進める
  - `website_url_candidates` は runtime で `service_url_candidates` の compatibility alias として同期済み
  - `github_repo_urls` / `tangled_repo_urls` は runtime で `source_code_urls` / confirmed `source_repo_records` から provider view として再構成済み
  - `resource_url_records` への実 DB migration は適用済み。one-shot migration CLI は削除済み
  - `resource_urls` は新規保存 payload から除外済み。runtime caller は `resource_url_values()` / `resource_url_record_values()` 経由で `resource_url_records` 正本へ寄せ済み
  - 次の候補は保存 payload / API payload / export から compatibility view を落とせるかの確認
  - `cleanup-app-links` は追加・実 DB 適用済み。URL heuristic 変更後の再構築用として残す
  - confirmed / candidate service URL の overlap を新規・更新導線で除く invariant は実装済み。実 DB に残る既存 overlap 件数は `audit-url-fields` で確認する。`repair-db --execute` は他の正規化も広く適用するため、実行前に dry-run の差分を確認する
  - `bsky.app/profile/{subject}` は handle / DID 構文で account-scoped と判定し、service URL から除外して account evidence に分ける処理を接続済み
  - 将来は provider ごとの account/resource scope extractor と `owned_service / likely_owned_service / upstream_dependency / unknown` relation classifier を追加する
- `inspired_by` を app metadata として昇格させるか再判断する
- Web UI 詳細検索が遅くなった場合は scalar filter を SQLite `WHERE` に寄せ、必要なら FTS を検討する
- `atproto_contribution_level` の docs / README evidence と LLM 補助分類を追加する
- 調査強化
  - deep investigation の優先ページ候補生成と page kind evidence は接続済み
  - 次は service status signal の粒度改善、LLM evidence の service/lexicon prompt 分岐、ブラウザ確認レーンを検討する
- 旧 field 互換の名残を整理する
  - 内部 shape にはまだ `primary_nsid`, `bluesky_handles`, `tangled_handles`, `introduced` など legacy 的な field が残る
  - `confirmed_nsids`, `resolved_nsids`, `input_nsids`, `recorded_lexicon_namespaces`, `lexicons` の record / ops 境界を明確にする
- `developer_accounts` / `candidate_developer_accounts`
  - confirmed / candidate の保存・merge 境界は整理済み
  - 次は Web UI で候補 account をどう確認・昇格するかの操作設計
