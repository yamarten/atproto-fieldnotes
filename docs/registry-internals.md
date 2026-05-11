# Registry Internals

`project_registry.py` の内部仕様と運用メモです。利用者向けの操作手順は [docs/project-registry.md](project-registry.md) を参照してください。

## データの持ち方

`projects` テーブルは次の二層で持ちます。

- scalar columns
  - 安定した正本
  - `project_key`, `status`, `category`, `name`, `primary_nsid`, `website_url`, `service_status`, `tracking_state`, `first_seen_at`, `last_seen_at`, `last_enriched_at`
- `data_json`
  - それ以外の構造化・派生情報

`repair-db` は `data_json` から scalar 重複を落とし、派生フィールドを再計算します。

## project record の最小コア

優先して埋める項目:

- `name`
- `website_url`
- `service_urls`
- `tagline`
- `tagline_is_provisional`
- `service_status`
- `source_code_urls`
- `introduced`
- `introduced_url`
- `tracking_state`
- `tracking_note`

詳細な field 一覧は [docs/project-record.md](project-record.md) を参照してください。

## enrich の流れ

`enrich` は次の順で動きます。

1. seed由来の事実収集
2. service / repo / lexicon の確認済み情報を更新
3. lexicon repository単位で namespace 集約
4. GitHub / Bluesky 調査
5. evidence bundle 構築
6. local LLM による summary / category / status 補完
7. 昇格判定

lexicon enrich に成功した場合、LLM evidence には lexicon schema の縮約サンプルも入ります。さらに `ufos-api.microcosm.blue/records` から recent record sample を取得できた場合は、それも判断材料に含めます。

`enrich --all` は既定で `tracking_state=active|watchlist` のみ対象です。`stable` と `stalled` は、明示的に include したときだけ回します。

## 昇格ゲート

本線扱いにする条件:

- seed が1件以上ある
- 確認済み識別子が1件以上ある
  - `website_url`
  - confirmed repository URL
  - resolved lexicon
- `service_status != unknown`
- `summary` と `tagline` がある
- `features` が3件以上ある
- `category` がある

満たさなければ `draft` のまま残し、`promotion_blockers` に理由を残します。

## NSIDの扱い

- `input_nsids`
  - 手動seedとして投入された NSID
- `resolved_nsids`
  - lexicon解決に成功した NSID
- `lexicons`
  - canonical に採用した full NSID
- `recorded_lexicon_namespaces`
  - project 粒度で記録する namespace

collection inventory import は project 粒度で集約しますが、lexicon 解決用に代表の full NSID を別に保持します。
既知 collection を `sync-collections` した時も `collection_observations` の `count` / `recent_count` / `max` は更新します。`latest_collection_activity_at` 相当の用途では、これを「最近 record が流れた時刻」とみなします。

## 確認済みと候補の区別

- confirmed
  - `website_url`
  - `service_urls`
  - `source_code_urls`
  - `github_repo_urls`
  - `resolved_nsids`
- candidate
  - `website_url_candidates`
  - `service_url_candidates`
  - `candidate_source_code_urls`
  - `summary_candidates`
  - `tagline_candidates`
- failure / transient
  - `failed_service_urls`
  - `last_run_errors`

## source repo 調査

source repo は次の順で探します。

1. first-party page のリンク
2. README / homepage
3. GitHub repository search
4. Bluesky actor search / post search / author feed

confirmed に入れるのは、seed 由来か first-party の強い根拠があるものだけです。
GitHub repo を確認した場合は `source_repo_records[].updated_at` に upstream の `updated_at` も保持し、`review-stalled` で repo activity として使います。

## LLM

LLM は discovery の主役ではなく、整理の補助です。

- `summary`
- `tagline`
- `features`
- `category`
- `service_status`

を生成します。説明系フィールドは日本語で出させます。`tagline` は助詞を含まない短い名詞句を想定し、最大30文字です。

endpoint 方式の timeout は `ATPROTO_SURVEY_LLM_TIMEOUT` で指定し、既定は `300` 秒です。

## merge

`duplicate_candidates` と `merge_recommendation` は派生データです。必要なら再計算できます。

`merge-batch` は保守的です。曖昧な一致は自動実行せず、`merge` の dry-run / execute で人が判断する前提です。

## repair-db

`repair-db` の責務:

- `data_json` から scalar 重複を除去
- `finalize_project_shape()` を通して正規化
- `duplicate_candidates` / `merge_recommendation` の再計算
- collision 監査

invalid JSON の自動救済はもう持ちません。`data_json` は strict JSON 前提です。

## review-stalled

`review-stalled` は `active/watchlist -> stalled` の候補だけではなく、

- `active -> stable`
  - collection は最近動いているが repo 側は止まっている
- `stalled -> active`
  - repo または collection に最近 activity が戻った

も返します。

`service_status=unknown/none` は単独では落とす根拠にしません。repo activity と collection activity を優先し、`dead/private` だけを強いシグナルとして扱います。
