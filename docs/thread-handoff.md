# Thread Handoff

次の担当者向けの引き継ぎメモです。利用手順は [docs/project-registry.md](project-registry.md)、内部仕様は [docs/registry-internals.md](registry-internals.md) を参照してください。

## まず知っておくこと

- DB は `data/survey.db`
- 主要テーブルは `projects` だけ
- `projects` は現在
  - scalar columns
  - `record_json`
  - `ops_json`
- `record_json` は高信頼の project record
- `ops_json` は候補・調査・LLM・merge 制御などの運用情報
- `project_identifiers` はもう使わない

## 運用上の注意

- DB を直接更新する前に、ユーザーへ一声かける
  - `enrich` が裏で走っている可能性があるため
- 特に事前確認が必要な操作
  - `repair-db`
  - import の `--execute`
  - `merge --execute`
  - `merge-batch --execute`
  - DB への直接 SQL 更新
- GUI で DB を触った後は `repair-db` を回す

## 現在の主要レーン

- 新規候補
  - `collection_count_view`
  - `sync-introduced` の Bluesky 投稿検索
- project 調査
  - `scripts/project_registry.py`
- 補助 discovery
  - `scripts/discover.py`

`list.json` / `services.json` 系の一時 import は削除済みです。今後の前提にしません。

## 直近の残件

- `NSID` ownership 判定は直近で一段強化した
  - authority domain 由来の service URL は、即 candidate 化せずまず probe だけする
  - probe が `supported` を通った時だけ `website_url_candidates` / `service_url_candidates` に残す
  - unsupported な authority probe からは `service_title` を `name` 候補へ入れない
  - `developer_accounts` は confirmed 専用、service/search 由来は `candidate_developer_accounts` に分離した
  - `project_owned_lexicon_nsid()` は次を優先して `owned` 判定する
    - confirmed 開発者アカウントが authority domain を持っていそう
    - confirmed 開発者 DID がその `NSID` の schema record を持っている
    - 既存の service/repo/token 整合は後段 fallback
  - ここでの confirmed account は、現状 service domain と handle ドメインが整合するものが中心
- まだ残っている ownership 課題
  - `repo/website` に出た `NSID` を `owned` と `reference` にもっと明確に分ける
  - 今後は
    - `uses collection` は強い `reference`
    - `defines schema` は強い `owned`
    として扱う方針
  - `atproto repository` 側の record usage は ownership というより reference 根拠
  - `listReposByCollection` heuristic は補助根拠に留める。単独で ownership を上げない
  - 保証関係の整理は [docs/registry-internals.md](registry-internals.md) の「4.2 何が何を保証しているか」を参照
  - 特に未実装なのは `repo/readme/profile -> confirmed account`
- `service_status` 強化は 4 段階のうち前半だけ入っています
  - 既に入っているもの:
    - evidence / confidence 付き保存
    - top page の軽量 signal 判定
      - `dns_failed`, `tls_failed`, `timeout`
      - parked domain
      - login surface
      - invite / beta / coming soon
      - docs-only surface
  - まだ残っているもの:
    - `--deep-investigation` で `/about`, `/docs`, `/login`, `/app` など優先ページを読んで `service_status` を補強する
    - browser/computer-use 相当の「実際にログイン導線まで試す」検証レーン
- `service_status=unverified` は「未確認」であって dead/private の確定ではない
  - 特に `coming soon` と app shell が混在するケースは安全側で `unverified`
- マージ時の主従判定は `project_editorial_preference()` を導入済み
  - `website_url`, `service_urls`, repo, `service_status`, `introduced_url` を見て service 本体を優先する
  - ただし既に保存済みの微妙な `tagline` / `summary` を自動で全面修正する仕組みはまだ無い
  - `proj:f4cead1df6f7` のような既存 project は、LLM 再 `enrich` か手動調整が別途必要
- LM Studio の `responses` 対応は parser 修正済み
  - `output_text` が空でも `output[].content[].text` を読める
  - ただし endpoint 側の shape 差分は今後も増える可能性があるので、再発時は `parse_openai_compatible_response()` を最初に疑う

## 中期の課題

- `service_status` の深掘り
  - `--deep-investigation` で `about/docs/login/app` を優先的に読む
  - top page だけでは判断できない service を `none/unverified/live` でより安定して分ける
  - 最終的には browser/computer-use を使った login 導線確認レーンを別コマンド化したい
- `tagline` / `summary` の再編集
  - 既存 project には collection/lexicon 主体の文面が残っているものがある
  - service 本体が優先されるよう merge 側は改善済みだが、保存済み文面の洗い替えはまだ
  - `service project` と `lexicon project` で LLM prompt を分ける余地がある
- GitHub / Bluesky rate limit 耐性
  - GitHub は query 数削減とキャッシュを入れているが、live fetch が続くとまだ 403 に当たる
  - Bluesky は authenticated search 前提になったので、認証情報未設定時の degraded path はかなり弱い
  - backoff / partial skip / run-level circuit breaker を追加する余地がある
- `assist-db-edit` の強化
  - 現状 action は `merge`, `set_separate`, `block_identifier`, `set_tracking_state`
  - 将来的には `split-project` や seed 追加も自然文から提案できるとよい
- `sync-*` の増分最適化
  - `sync-collections` は import 自体は増分化済みだが fetch は全件
  - `sync-github-search` は query の broadness と screen 精度をもう少し詰めたい
  - `sync-semble-collections` は固定 2 collection 前提なので、必要なら設定化する
- review 系の改善
  - `review-queue` は signal を増やしたが、まだ「service は使えそうだが editorial quality が低い」ものの抽出が弱い
  - `review-stalled` は repo / collection activity を見ているが、`introduced_url` や最近の紹介状況も補助信号に使えるかもしれない

## 低優先の整理

- 旧 field 互換の名残整理
  - 内部 shape にはまだ `primary_nsid`, `bluesky_handles`, `tangled_handles`, `introduced` など legacy 的な field が残っている
  - schema からは落としているが、コード内部では完全には消えていない
- `confirmed_nsids` と lexicon analysis 情報の境界整理
  - 今は `resolved_nsids`, `input_nsids`, `recorded_lexicon_namespaces`, `lexicons` が混在する箇所がまだある
  - 長期的には `record` と `ops` で役割をさらに明確にしたい
- `developer_accounts` の表現整理
  - 現在は
    - `developer_accounts`
    - `candidate_developer_accounts`
    の二層
  - confirmed/candidate の UI 露出はまだ薄い
  - repo/readme/profile 由来の confirmed 条件をもう少し増やせる
- `resource_urls` の運用整理
  - 現在は docs/blog/about をまとめて入れている
  - service 判定強化を進めるなら、`resource_urls` 内の種類も持ちたくなる可能性がある

## `project_registry.py` の重要ポイント

- `add-seed`
  - `nsid`, `repo_url`, `service_url` を受ける
  - `pdsls.dev`, `pds.ls`, `at://...` も `nsid` seed として扱う
- `sync-introduced`
  - `from:yamarten.bsky.social #ATプロトーク` の Bluesky 検索を既定で使う
  - seed は増やさず、既存 project の `introduced_url` だけ更新する
- `sync-collections`
  - collection inventory API を直接 fetch する
  - 新規 collection import に加えて、既知 collection の `count/recent_count/max` も refresh する
- `sync-github-search`
  - `config/sources.json` の `github_repo_search` を使う
  - 既知 repo を除いた、最近更新された未知 repo seed を追加する
- `enrich`
  - 既定では `tracking_state=active|watchlist` のみ
  - `stable` と `stalled` は明示 include が必要
  - local LLM を使う場合、endpoint timeout は `ATPROTO_SURVEY_LLM_TIMEOUT`、既定 `300`
- `review-stalled`
  - `stalled` 降格だけでなく `stable` 提案と `active` への復帰提案も返す
  - repo activity と collection activity のどちらか一方でも最近動いていれば `stalled` に落とさない
- `repair-db`
  - scalar 重複除去
  - 派生フィールド再計算
  - collision 監査
  - invalid JSON の自動救済はしない
- storage layout migration は完了済み
  - 現行 DB は `record_json` / `ops_json` / `introduced_url` を含む現在の schema を前提にする

## 既知の前提

- 同一ドメイン配下でも曖昧なら自動吸収しない
  - 例: `Rito` 本体と派生サービスのようなケース
  - こういうものは新規 record に落として後で `merge`
- `merge-batch` は保守的
  - 危ない候補は通さない前提
- `set-separate`
  - 手動で「この 2 件は別物」を相互に記録する
  - `duplicate_candidates` と `merge_recommendation` からその pair を除外する
- `block-identifier`
  - 手動で「この project にこの `service_url` / `repo_url` / `nsid` は付けない」を記録する
  - `finalize` / `enrich` 後も同じ identifier が戻らないようにする
- `duplicate_candidates` と `merge_recommendation` は派生データ
  - 必要なら再計算できる

## ドキュメントの読み分け

- 利用者向け
  - [README.md](../README.md)
  - [docs/project-registry.md](project-registry.md)
- 設計・内部仕様
  - [docs/registry-internals.md](registry-internals.md)
  - [docs/project-record.md](project-record.md)
- 調査背景
  - [docs/manual-workflow.md](manual-workflow.md)
  - [docs/automation-plan.md](automation-plan.md)
  - [docs/automation-architecture.md](automation-architecture.md)

## コミット時に気をつけること

- `data/` 配下の JSON は生成物や棚卸しメモが混ざる
- `data/tldextract-cache/` は生成 cache
- ユーザーが明示しない限り、`data/*.json` を安易にコミットしない
- DB スキーマや運用ルールを変えたら、利用者向け文書と内部文書の両方を更新する
