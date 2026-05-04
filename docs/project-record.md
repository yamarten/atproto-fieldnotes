# Project Record Schema

当面は最小コアを優先する。

## 最小コア

優先して埋める対象は次の通り。

- `name`
- `website_url`
  - 代表 URL を 1 件
- `service_urls`
  - confirmed な利用先 URL
  - `website_url` と実質同一でもよい
  - `website_url` があれば同じ URL を含める
- `tagline`
- `tagline_is_provisional`
  - `bool`
  - 自動生成や未確認の暫定値なら `true`
- `service_status`
- `source_code_urls`
  - confirmed なコード置き場
  - 1 件あれば十分
- `introduced`
  - `bool`
  - 初期値は `false`
- `introduced_url`
  - 紹介先 URL
  - 未紹介なら空

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
  - 存在は見えるが一般利用前提ではない
- `dead`
  - 死んでいる
- `none`
  - サービス URL を持たない

追加候補として迷うのは次。

- `unreleased`
  - 公開前や coming soon を `private` と分けたい場合
- `paused`
  - dead ではないが停止中を区別したい場合

当面は増やさず、必要になるまで上の 7 値で回す。

## 内部管理フィールド

公開コアとは別に、運用上これだけは残す。

- `project_key`
- `manual_seeds`
- `duplicate_candidates`
- `merge_recommendation`
- `notes`
- `last_enriched_at`

## まだ保留でよいもの

今は空でもよい、または後で詰めればよい項目。

- `app_category`
- `category`
- `summary`
- `features`
- `inspired_by`
- `developer_accounts`
- `tags`
- `platforms`
- `project_state`
- `brand_did`
- `ownership_signal`
- `bluesky_handles`
- `tangled_handles`
- `recorded_lexicon_namespaces`
- `duplicate_candidates`
