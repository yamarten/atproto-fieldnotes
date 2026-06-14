# Project Record Schema

この文書はregistry schema version 2のproject保存モデルと、各データの意味を定める正本です。調査pipelineの責務は[investigation-model.md](investigation-model.md)、具体的な判定規則は[registry-internals.md](registry-internals.md)を参照してください。

## 設計意図

registryの一次目的は、atproto関連projectを継続的に発見し、紹介・追跡・再調査に必要な情報を保守的に保持することです。

- projectの現在状態と、外部sourceから観測したidentifierを分離する
- collector由来のrelation evidenceを、後から再評価できる形で残す
- block/deferなどoperator判断を自動評価より優先する
- 不確かな値を紹介用profileへ無理に昇格させない
- 一覧表示に必要な値と、調査・運用上の詳細を分ける

app lexiconとの完全互換や即時exportは主目的ではありません。`profile_json.app`は将来再利用しやすい確定情報の置き場ですが、不確かな情報で埋めるより空のままにします。

## Schema Version

現行schemaはversion 2です。`registry_meta.schema_version=2`を持たない旧DBは通常起動では読み込みません。

```sh
project-registry migrate-db data/survey.db
```

移行成功時は旧DBを同じdirectoryのtimestamp付きbackupへ移し、新DBを元pathへ置きます。失敗時は旧DBを変更しません。

## 保存責務

### `projects`

projectの現在状態を表すmaterialized recordです。

- `project_key`: `proj:...`形式の内部識別子
- `name`: 表示用の代表名
- `website_url`: 代表公開URL。未確定なら空
- `service_status`: 公開・到達状態
- `tracking_state`: 継続調査上の扱い
- `introduced_url`: 紹介済みであることの根拠URL。service identityには使わない
- `profile_json`: 紹介・表示に使う編集済み情報
- `state_json`: 評価結果、候補、調査補助、queue状態
- `first_seen_at`, `last_seen_at`, `last_enriched_at`: registry内での観測・調査時刻

scalar columnは一覧、filter、運用判定で頻繁に使う値だけに限定します。

### `profile_json`

人が読むproject profileとして比較的安定した情報です。

`core`:

- `tagline`: 短い紹介文
- `tagline_is_provisional`: 暫定文であることを示すflag
- `summary`: projectの説明
- `notes`: operatorが保持する編集メモ

`app`:

- `tags`: 確定した分類tag
- `platforms`: 対応platform
- `links`: canonical identifierとconfirmed resourceから再構築できる公開link view

`links`で現在使うroleは`website`, `webApp`, `sourceCode`, `docs`, `support`, `status`, `privacyPolicy`, `termsOfService`です。identifierの正本ではなく表示・将来export用のviewなので、`cleanup-app-links`で再構築できます。

### `state_json`

現在の評価・調査状態を`ops`と`evidence`に分けて保持します。専用tableへ移したidentifier、relation、operator decisionは重複保存しません。

`ops`の主な内容:

- editorial/classification: `status`, `category`, `app_category`, `project_state`, `ownership_signal`
- review: `tracking_note`, `manual_review_recommended`, `manual_review_kind`, `manual_review_priority`, `manual_review_reason`
- promotion: `promotion_ready`, `promotion_blockers`, `last_promoted_at`, `promotion_events`
- merge: `duplicate_candidates`, `merge_recommendation`
- service評価: `service_status_confidence`, `service_status_evidence`
- atproto寄与度: `atproto_contribution_level`, `atproto_contribution_score`, `atproto_contribution_evidence`
- lexicon位置付け: `lexicon_scope`, `lexicon_scope_evidence`
- 技術的関心度: `technical_interest_level`, `technical_interest_score`, `technical_interest_reasons`, `technical_interest_summary`
- activity/error: `collection_observations`, `last_run_errors`, `last_llm_review_at`

`evidence`の主な内容:

- discovery/tracing: `discovery_sources`, `evidence_urls`, `investigation_queries`, `investigation_log`
- lexicon調査detail: `lexicon_resolution_entries`, `lexicon_count`, `lexicon_schema_samples`, `recent_record_samples`, `record_creators`
- 内容候補: `features`, `inspired_by`, `name_candidates`, `summary_candidates`, `tagline_candidates`
- URL observation: `resource_url_records`, `failed_service_urls`, `supported_service_urls`, `url_aliases`
- repo observation: `source_repo_records`
- service深掘り: `service_verification_results`
- LLM補助: `llm_source`, `llm_output`, `classification_reason`, `exclude_reason`, `watch_reason`, `confidence`

候補を保存することと、profileへ採用することは別です。たとえば`summary_candidates`は再評価材料であり、`profile_json.core.summary`と同じ確度を意味しません。

## `project_identifiers`

projectに結び付くNSID、service URL、repo URL、account、DID、authority domainを順序付きで保持します。

- `kind`: identifierの種類
- `value`: 正規化済みの値
- `role`: project内での役割
- `classification`: `confirmed | candidate | derived | heuristic`
- `is_primary`: 代表値か
- `provider`: GitHub、Tangled、Blueskyなどのprovider hint
- `metadata_json`: repo観測やaccount detailなど、そのidentifier固有の補助情報
- `observed_at`: 観測時刻

主なroleと意味:

- NSID
  - `primary_nsid`: runtimeで代表として使うowned NSID
  - `input_nsid`: seedや調査入力として得たが、ownership確定前のNSID
  - `owned_nsid`: ownership評価を通過したNSID
  - `observed_lexicon`: project周辺で観測したlexicon
  - `referenced_nsid`: projectが利用・参照する外部NSID
  - `recorded_namespace`: collection観測から導出したnamespace
- service
  - `website`: 代表公開URL
  - `service`: confirmedまたはcandidateのservice surface
- repository
  - `source_repository`: projectのsource候補またはconfirmed repo
  - `source_repository_record`: metadata、README、treeなどを取得したrepo observation
- identity
  - `developer_account`: 開発者・運営者account
  - `developer_did`: accountに対応するDID
  - `brand_did`: project自身のDID
  - `project_account`: project名義のaccount候補
  - `authority_domain`: NSIDやidentity pathから導出したdomain
- `manual_seed`: operatorが明示登録した入力。自動収集値より強く扱う

load時はcollector/evaluationが使うcanonical project dictへhydrateします。ただし削除済みalias fieldは生成しません。

## `project_relations`

「何が何をどのように保証・利用しているか」を保持するtyped relation evidenceです。

- `subject_type`, `subject_value`
- `predicate`
- `object_type`, `object_value`
- `classification`
- `source`: GitHub、service page、OAuth metadataなどの根拠source
- `locator`: URLやrepo path
- `method`: lexicon JSON、source scan、HTTP redirectなどの抽出方法
- `context_json`: operation、score、bounded extractなどの補助情報
- `observed_at`

主要predicate:

- `defines_schema`: repoやaccountがschemaを定義する
- `uses_collection`: read/write/queryなどでcollectionを利用する
- `mentions_collection`: docs、example、testなどで言及するだけ
- `official_account`: siteやrepoがaccountを公式として示す
- `operates_service`: identityがserviceを運営する
- `source_repository`: app/siteとrepoがsource関係にある
- `requests_permission`: OAuth metadataがrepo permissionを要求する
- `redirects_to`: URL間の実観測redirect/canonical関係

同じproject内でsubject、predicate、object、source、locator、methodが同じrelationは重複保存しません。relationの意味とownershipへの使い方は[investigation-model.md](investigation-model.md)を参照してください。

## `project_decisions`

operator判断を履歴として保持し、自動収集・評価より優先します。

- `block_identifier`: 誤ったidentifierの再付着を恒久的に抑止する
- `defer_identifier`: identifierの確定を期限付きで保留する
- `defer_promotion`: promotion queueへの再出現を期限付きで保留する
- `dismiss_review`: 特定review signalをdismissする
- `separate_project`: 2 projectを別物としてmerge候補から除外する

各rowはtarget、note、created/expires/cleared timestamp、追加metadataを持ちます。clear後も履歴を残すdecisionがあります。

## Canonical View

- primary website: `projects.website_url`
- confirmed/candidate service: `project_identifiers(kind=service_url)`のclassification
- primary source repo: repo identifierの`is_primary=1`
- confirmed/candidate repo: `project_identifiers(kind=repo_url)`のclassification
- supporting page: `state_json.evidence.resource_url_records`
- introduction state: `introduced_url`が空でないか

削除済みruntime field:

- `website_url_candidates`
- `github_repo_urls`
- `tangled_repo_urls`
- `resource_urls`
- `introduced`

provider別repo一覧とresource URL一覧はcanonical dataからhelperで導出します。

## Status Values

`tracking_state`:

- `active`: 通常の継続調査対象
- `watchlist`: 情報不足、未公開などで追跡を続ける
- `stable`: 安定しており通常の全件enrich頻度を下げる
- `stalled`: 立ち消え気味だがarchivedとは確定しない
- `out_of_scope`: atprotoとの関係はあるが現在の紹介・重点調査対象外
- `archived`: 開発終了または追跡完了
- `ignore`: 無関係・誤登録・再評価価値の低いnoise

`service_status`:

- `unknown`: まだ判断材料がない
- `unverified`: 調査したが到達性や公開状態を確定できない
- `none`: service surfaceを持たない
- `dead`: 消滅、parked、404/410など
- `live`: 一般利用可能なsurfaceを確認した
- `private`: 認証・権限制限などで一般公開性が低い
- `unreleased`: coming soonなど公開前
- `beta`: beta公開
- `invite_only`: 招待・waitlist制

`service_status_confidence`は`none | low | medium | high`です。status値と根拠の強さを別に保持します。

## Investigation History

`investigation_runs`と`collector_runs`はcurrent stateとは別に、入力snapshot、EvidenceBundle、source完了状態、評価snapshot、出力snapshotを保存します。これは履歴とreplay用であり、project rowの代替ではありません。

## Non-goals

- app lexiconとの完全互換
- raw HTMLやREADME全文の永続化
- 旧schemaの通常読み込み互換
- Web UI向けpayload互換
