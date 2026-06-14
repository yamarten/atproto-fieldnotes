# Project Record Schema

この文書はregistry schema version 2のproject保存モデルの正本です。

## 設計意図

registryの一次目的は、ATProto関連projectを継続的に発見し、紹介・追跡・再調査に必要な情報を保守的に保持することです。

- projectの現在状態と、観測されたidentifierを分離する
- collector由来のrelation evidenceを再評価可能な形で残す
- block/deferなどoperator判断を自動評価より優先する
- uncertainな値をproject profileへ無理に昇格させない

## Schema Version

現行schemaはversion 2です。`registry_meta.schema_version=2`を持たない旧DBは通常起動では読み込みません。

旧DBは次で一方向移行します。

```sh
scripts/project-registry migrate-db data/survey.db
```

移行成功時は旧DBを同じdirectoryのtimestamp付きbackupへ移し、新DBを元のpathへ置きます。失敗時は旧DBを変更しません。

## `projects`

`projects`はmaterialized current stateです。

- `project_key`
- `name`
- `website_url`
- `service_status`
- `tracking_state`
- `introduced_url`
- `profile_json`
- `state_json`
- `first_seen_at`
- `last_seen_at`
- `last_enriched_at`

`profile_json`は紹介・表示に使う編集済み情報を保持します。

- `core`: tagline、summary、notes
- `app`: tags、platforms、確定済みlink view

`state_json`は低頻度の評価結果、調査補助、queue状態を保持します。identifier、relation、operator decisionの正本は含めません。

## `project_identifiers`

NSID、service URL、repo URL、account、DID、authority domainを順序付きで保持します。

- `kind`, `value`, `role`
- `classification`: `confirmed | candidate | derived | heuristic`
- `is_primary`
- `provider`
- `metadata_json`
- `observed_at`

主なrole:

- NSID: `primary_nsid`, `input_nsid`, `owned_nsid`, `observed_lexicon`, `referenced_nsid`, `recorded_namespace`
- service: `website`, `service`
- repo: `source_repository`, `source_repository_record`
- identity: `developer_account`, `developer_did`, `brand_did`, `project_account`, `authority_domain`
- operator input: `manual_seed`

runtimeでは既存collector/evaluation向けのcanonical fieldへhydrateします。`website_url_candidates`、`github_repo_urls`、`tangled_repo_urls`などのalias fieldは生成しません。

## `project_relations`

typed relation evidenceを保持します。

- typed subject / predicate / object
- classification
- source
- locator / method
- context
- observed_at

主要predicateは`defines_schema`、`uses_collection`、`mentions_collection`、`official_account`、`operates_service`、`source_repository`、`requests_permission`、`redirects_to`です。

同一project内でsubject、predicate、object、source、locator、methodが同じrelationは重複保存しません。

## `project_decisions`

operator判断を履歴として保持します。

- `block_identifier`
- `defer_identifier`
- `defer_promotion`
- `dismiss_review`
- `separate_project`

target、note、created/expires/cleared timestamp、追加metadataを持ちます。block/defer/manual confirmationはcollector・evaluation結果より優先します。

## Canonical Fields

- primary website: `projects.website_url`
- confirmed service set: `project_identifiers(kind=service_url, classification=confirmed)`
- service candidates: 同kindの`candidate`
- primary source repo: `is_primary=1`のrepo identifier
- confirmed/candidate repos: repo identifierのclassification
- supporting pages: `state_json.evidence.resource_url_records`
- introduction state: `introduced_url`が空でないかで判定

削除済みruntime field:

- `website_url_candidates`
- `github_repo_urls`
- `tangled_repo_urls`
- `resource_urls`
- `introduced`

provider別repo viewやresource URL一覧はhelperで都度導出します。

## Investigation History

`investigation_runs`と`collector_runs`はproject current stateとは別に、入力snapshot、EvidenceBundle、source完了状態、評価snapshot、出力snapshotを保存します。詳細は[investigation-model.md](investigation-model.md)を参照してください。

## Non-goals

- app lexiconとの完全互換
- raw HTMLやREADME全文の永続化
- 旧schemaの通常読み込み互換
- Web UI向けpayload互換
