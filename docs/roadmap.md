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
  &statuses=
  &categories=
  &introduced=
  &has_candidates=
  &has_errors=
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
  - `active`, `watchlist`, `stable`, `stalled`, `archived`, `ignore`
- `service_statuses`
  - 複数指定可
  - 現行 `service_status` enum を使う
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
  - introduced
  - candidates remaining
  - errors
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

## TODO

- `app.status` を separate field として導入する必要が出たら、`service_status` からの写像を定義する
- `app.links[]` の role を増やすなら、`appStore` / `playStore` / `fdroid` / `webManifest` / `demo` / `api` / `package` / `lexicon` / `collection` / `directory` の採用基準を定義する
- `github_repo_urls` / `tangled_repo_urls` を provider 別 convenience field のまま残すか、evidence から導出に寄せるか決める
- `inspired_by` を app metadata として昇格させるか再判断する
- Web UI 詳細検索が遅くなった場合は scalar filter を SQLite `WHERE` に寄せ、必要なら FTS を検討する
