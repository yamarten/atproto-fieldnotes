# Project Record Schema

当面は最小コアを優先する。

## 設計意図

この project registry の一次目的は、atproto project を継続的に発見し、紹介候補として整理することです。

特に重視するのは次です。

- 紹介する価値がある project を見落としにくくする
- 紹介判断と紹介文生成に必要な核データを安定して保持する
- merge / review / watchlist 運用を支える証拠を残す

app lexicon を参照しているのは、今すぐ完全互換を満たすためではありません。将来的に DB から app record を export できるように、app metadata として意味が安定しそうな情報の置き場を先に用意しておくためです。

ただし現時点での主目的は app export ではなく紹介フローです。したがって、app 情報は「確かなものだけ」を保守的に保持し、ゴミ情報で埋めるくらいなら空のままにします。

## 保存レイヤ

現在の保存先は次です。

- DB column
  - `project_key`
  - `name`
  - `website_url`
  - `service_status`
  - `tracking_state`
  - `introduced_url`
  - `first_seen_at`
  - `last_seen_at`
  - `last_enriched_at`
- `record_json`
  - `core`
  - `app`
- `ops_json`
  - `ops`
  - `evidence`

`record_json` は紹介判断や app export に使う高信頼データ、`ops_json` は運用制御と証拠です。

## 最小コア

優先して埋める対象は次の通り。

- `name`
  - DB column
- `website_url`
  - DB column
  - 代表 URL を 1 件
- `service_status`
  - DB column
- `tagline`
  - `record_json.core`
- `summary`
  - `record_json.core`
- `source_code_url`
  - `record_json.core`
  - 代表 repo を 1 件
- `introduced_url`
  - DB column
  - 紹介先 URL
  - 未紹介なら空
- `manual_seeds`
  - `record_json.core`
- `brand_did`
  - `record_json.core`
- `lexicon_namespaces`
  - `record_json.core`
- `tracking_state`
  - DB column
  - 継続調査の対象状態
  - 初期値は `active`
- `notes`
  - `record_json.core`

## `record_json.core`

紹介判断と紹介文生成に直接使う核です。

- `summary`
- `tagline`
- `manual_seeds`
- `brand_did`
- `source_code_url`
- `lexicon_namespaces`
- `notes`

`tagline_is_provisional` もここに含みます。

## `record_json.app`

app 関連情報のまとまりです。現在の app lexicon そのものに限定しません。

- `tags`
- `platforms`
- `links`
- `images`
- `langs`
- `lexicons.produces`
- `lexicons.consumes`
- `account_indicators`

app 情報は低優先度です。確かな情報が無ければ `app` は空 object のまま保持します。

`links[]` は role を確信できる公開リンクだけを置きます。初回の正式対応 role は次です。

- `website`
- `webApp`
- `sourceCode`
- `docs`
- `support`
- `status`
- `privacyPolicy`
- `termsOfService`

`website_url` と `source_code_url` は canonical が column / core にあり、`links[]` には export 用の構造化表現として重複保持できます。

## `ops_json.ops`

review / merge / 紹介運用の制御情報です。

- `status`
- `category`
- `app_category`
- `project_state`
- `ownership_signal`
- `tracking_note`
- `manual_review_recommended`
- `manual_review_priority`
- `manual_review_reason`
- `promotion_ready`
- `promotion_blockers`
- `last_promoted_at`
- `promotion_events`
- `promotion_deferrals`
- `duplicate_candidates`
- `merge_recommendation`
- `separate_project_keys`
- `blocked_identifiers`
- `investigation_queries`
- `investigation_log`
- `last_run_errors`
- `last_llm_review_at`
- `service_status_confidence`
- `service_status_evidence`
- `atproto_contribution_level`
- `atproto_contribution_score`
- `atproto_contribution_evidence`
- `technical_interest_level`
- `technical_interest_score`
- `technical_interest_reasons`
- `technical_interest_summary`

## `ops_json.evidence`

候補、観測結果、生データ、再計算の材料です。

- `summary_candidates`
- `name_candidates`
- `tagline_candidates`
- `features`
- `inspired_by`
- `developer_accounts`
- `bluesky_handles`
- `tangled_handles`
- `discovery_sources`
- `evidence_urls`
- `primary_nsid`
- `input_nsids`
- `resolved_nsids`
- `lexicon_resolution_entries`
- `lexicons`
- `lexicon_count`
- `lexicon_schema_samples`
- `recent_record_samples`
- `record_creators`
- `authority_domains`
- `external_lexicons`
- `website_url_candidates`
- `service_urls`
- `resource_urls`
- `resource_url_records`
- `source_code_urls`
- `service_url_candidates`
- `failed_service_urls`
- `supported_service_urls`
- `candidate_source_code_urls`
- `source_repo_records`
- `github_repo_urls`
- `tangled_repo_urls`
- `collection_observations`
- `classification_reason`
- `exclude_reason`
- `watch_reason`
- `confidence`
- `llm_source`
- `llm_output`

補足:

- `features` と `inspired_by` は紹介判断や要約補助にも使いますが、現時点では app metadata 本体ではなく evidence として保持します
- `lexicon_namespaces` は `record_json.core` の canonical 値とは別に、evidence 側でも比較・調査の補助として保持されます
- `service_urls` / `source_code_urls` / `resource_url_records` は evidence に残しつつ、確信できるものだけ `record_json.app.links` に昇格させます

## URL field の責務

URL は role、confidence、observation state を分けて扱います。保存 migration は完了済みですが、runtime compatibility view はまだ残っています。正本と compatibility view を同一視しません。

### canonical

- `website_url`
  - project の代表公開 URL
- `source_code_url`
  - 代表 source repository
  - `source_code_urls` から選ぶ primary view
- `introduced_url`
  - 紹介済み判定の根拠 URL
  - service identity ではない

### confirmed set

- `service_urls`
  - 確認済み service surface
  - `website_url` を含む
- `source_code_urls`
  - 確認済み source repository

### candidate

- `service_url_candidates`
  - 未確認 service surface
- `candidate_source_code_urls`
  - 未確認 source repository

### observation

- `resource_url_records`
  - docs / about / blog など、調査中に取得した補助 page の正本
  - `url`, `role`, `source`, `observed_at`, `confidence`
- `evidence_urls`
  - 一般 evidence URL
- `failed_service_urls`
  - probe 失敗 URL
- `supported_service_urls`
  - heuristic probe が project と対応すると判断した URL

### derived compatibility view

- `website_url_candidates`
  - 現状は `service_url_candidates` と重複する legacy alias
- `github_repo_urls`
  - `source_code_urls` の GitHub provider view
- `tangled_repo_urls`
  - `source_code_urls` の Tangled provider view
- `resource_urls`
  - `resource_url_records[].url` から導出する runtime compatibility view
- `record_json.app.links`
  - export 用の role 付き view

`resource_url_records` の `confidence=confirmed` かつ role が export 対象のものだけを `record_json.app.links` に昇格します。既存 `resource_urls` は `role=unknown`, `source=legacy_resource_urls`, `confidence=legacy` として lossless に読み込みます。同じ URL の confirmed observation が得られたら legacy record は畳みます。通常導線は `resource_url_values()` / `resource_url_record_values()` 経由で `resource_url_records` を読み、`resource_urls` は runtime compatibility view として導出しますが、新規保存 payload には書きません。

derived compatibility view は保存 payload / API payload / export 互換のためまだ残っています。`resource_urls` は保存を止め、runtime caller は正本へ寄せました。他の view は保存中です。caller と外部表示の整理後に derive-only または削除へ移します。

`ops_json.ops` には lexicon の位置付けを表す派生値も保持します。

- `lexicon_scope`
  - `shared`: 第三者が実装・相互運用する共有 lexicon と判断できる
  - `service_specific`: 特定 service / app の名前空間と service 面が対応している
  - `unknown`: NSID はあるが、service 未発見と共有 lexicon をまだ区別できない
- `lexicon_scope_evidence`
  - 上記判定の根拠

## `tracking_state`

継続調査の対象を制御する enum。

- `active`
  - 通常の継続調査対象
- `watchlist`
  - 情報不足、招待制、未公開などで追跡したい
- `stable`
  - 安定運営。通常の `enrich --all` からは外す
- `out_of_scope`
  - atprotoとの関係はあるが、現在の紹介・重点調査スコープ外
  - Blueskyクライアント、認証用途のみ、従属utilityなどを想定する
  - registryには残し、sync/merge照合や将来の再評価対象には含める
  - 通常の `enrich --all`、review queue、promotion queueからは外す
- `archived`
  - 開発終了や追跡完了。通常の `enrich --all` からは外す
- `ignore`
  - atproto無関係、誤登録、独立identityのない複製などのノイズ
  - 通常の再評価を期待しない。通常の `enrich --all` からは外す

## `service_status`

現時点の基本 enum はこれで扱う。

- `unknown`
  - まだ判定できない
- `live`
  - 一般に利用可能
- `invite_only`
  - 招待制
- `beta`
  - ベータ公開
- `private`
  - 将来的にも広くユーザーを募る前提が弱い
  - 個人用、内輪用、組織内用など
- `unreleased`
  - 公開前、coming soon、まだ一般利用させる段階ではない
- `dead`
  - 死んでいる
- `none`
  - サービス URL を持たない

追加候補として迷うのは次。

- `paused`
  - dead ではないが停止中を区別したい場合

当面は `paused` を増やさず、上の 8 値で回す。

## 境界項目

- `inspired_by`
  - 現時点では app metadata ではなく、分類・要約補助の evidence として扱う
- `features`
  - 紹介文や tags の材料だが、現時点では evidence
- `tags`, `platforms`
  - app metadata として再利用しやすいので `record_json.app`
