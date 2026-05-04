# Project Record Schema

自動化後に 1 プロジェクトごとに保持したい項目です。

## 識別

- `project_key`
  - 内部キー。`preferred_url` または `primary_nsid` から生成
- `status`
  - `candidate`, `tracked`, `published`, `invite_only`, `beta`, `inactive`, `rejected`
- `category`
  - `project`, `infra_tool`, `name_only`, `library`, `personal_tool`, `auth_only`

## 基本情報

- `name`
- `tagline`
- `summary`
- `features`
  - 箇条書き 3-6 個
- `inspired_by`
  - 例: `Twitter`, `GitHub`, `Notion`

## 発見根拠

- `discovery_sources`
  - 例: `github_search`, `tangled_repo`, `lexicon_schema`, `lexicon_garden`, `ufos`
- `first_seen_at`
- `last_seen_at`
- `evidence_urls`
  - 人が見返す URL

## atproto 関連

- `primary_nsid`
- `lexicons`
  - NSID 配列
- `lexicon_count`
- `record_creators`
  - DID / handle 配列
- `authority_domains`
  - NSID から抽出したドメイン配列

## アカウント/コード

- `website_url`
- `service_urls`
- `service_status`
  - `none`, `unknown`, `live`, `invite_only`, `beta`, `private`, `dead`
- `source_code_urls`
- `github_repo_urls`
- `tangled_repo_urls`
- `bluesky_handles`
- `tangled_handles`

## 判定補助

- `classification_reason`
- `exclude_reason`
- `watch_reason`
- `confidence`
  - 0.0-1.0

## 運用メモ

- `last_llm_review_at`
- `next_check_at`
- `notes`
