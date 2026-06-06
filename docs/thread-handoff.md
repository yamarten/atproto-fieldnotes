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
- `deferred_identifiers` は CLI の `defer-identifier` で追加する一時保留 guard
  - URL は candidate に降格し、NSID は deferred category に保持する
  - 期限内は自動 confirmed 化を抑制する
  - Web UI では confirmed/candidate row の `Defer 30d`、`Guards` の期限表示と `Clear defer` を使える
- `promotion_deferrals` は CLI の `defer-promotion` または `promotion-queue --interactive` の `h` で追加する理由付き一時保留履歴
  - 既定 7 日。active な期限内 entry がある間だけ promotion queue から除外し、期限切れ後に自動復活する
  - 履歴は残し、再保留時に直近理由を再利用できる
  - Web UI では promotion queue の `Hold 7d`、project detail の期限・理由・再保留・`Clear hold` を使える
  - 理由からの復活条件自動判定は未接続

## 運用上の注意

- DB を直接更新する前に、ユーザーへ一声かける
  - `enrich` が裏で走っている可能性があるため
- 特に事前確認が必要な操作
  - `repair-db`
  - import の `--execute`
  - `merge --execute`
  - `merge-batch --execute`
  - DB への直接 SQL 更新
- `sync-collections` / `sync-github-search` / `sync-semble-collections` は未知候補の project 作成が既定 ON
  - 棚卸しだけ行う場合は `--no-create-unmatched`
- GUI で DB を触った後は `repair-db` を回す

## 現在の主要レーン

- 新規候補
  - `collection_count_view`
  - `sync-introduced` の Bluesky 投稿検索
- project 調査
  - `scripts/project_registry.py`
- テスト
  - `unittest` ベース
  - 実行: `python3 -m unittest discover -s tests -v`
  - `tests/test_project_registry_core.py` は name / service_status / LLM parser / merge scoring
  - `tests/test_project_registry_storage.py` は temp DB で scalar / `record_json` / `ops_json` / repair 系
  - `tests/test_project_registry_web.py` は Web UI API と temp DB endpoint

## 直近の残件

- `NSID` ownership 判定は直近で一段強化した
  - authority domain 由来の service URL は、即 candidate 化せずまず probe だけする
  - probe が `supported` を通った時だけ `website_url_candidates` / `service_url_candidates` に残す
  - supported URL は `supported_service_urls` に URL 単位で保存し、guess prune から除外する
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
- service URL の redirect handling は追加済み
  - `normalize_url()` は文字列正規化だけで、`http` / `https` や redirect を無条件同一視しない
  - service probe は `requested_url` / `final_url` / redirect flags を返す
  - same host の `http -> https` redirect だけ canonical service URL を `https` 側へ寄せる
  - same site family の redirect 先が project を支持し、`website_url` が未設定なら primary service URL として登録する
  - 既存 `website_url` がある same site family redirect と、project を支持する cross-host redirect は candidate に留める
  - cross-host redirect は investigation log に残すが、自動 confirmed にはしない
- マージ時の主従判定は `project_editorial_preference()` を導入済み
  - `website_url`, `service_urls`, repo, `service_status`, `introduced_url` を見て service 本体を優先する
  - ただし既に保存済みの微妙な `tagline` / `summary` を自動で全面修正する仕組みはまだ無い
  - `proj:f4cead1df6f7` のような既存 project は、LLM 再 `enrich` か手動調整が別途必要
- LM Studio の `responses` 対応は parser 修正済み
  - `output_text` が空でも `output[].content[].text` を読める
  - ただし endpoint 側の shape 差分は今後も増える可能性があるので、再発時は `parse_openai_compatible_response()` を最初に疑う

## 中期の課題

- テスト拡張
  - HTTP request mock を使った `sync-*` / `enrich_project` の統合テスト
  - CLI subprocess smoke test
  - fixture JSON corpus による回帰テスト
- `service_status` の深掘り
  - `--deep-investigation` で `about/docs/login/app` を優先的に読む
  - top page だけでは判断できない service を `none/unverified/live` でより安定して分ける
  - 最終的には browser/computer-use を使った login 導線確認レーンを別コマンド化したい
- `tagline` / `summary` の再編集
  - 既存 project には collection/lexicon 主体の文面が残っているものがある
  - service 本体が優先されるよう merge 側は改善済みだが、保存済み文面の洗い替えはまだ
  - NSID seed だけで共有 schema とみなさない `lexicon_scope=shared/service_specific/unknown` は追加済み
  - LLM prompt も service 未発見と共有 lexicon を区別するよう更新済み
  - 既存文面の洗い替えには LLM 再 `enrich` が必要
  - `service project` と `lexicon project` で prompt 自体を分ける余地は残る
- GitHub / Bluesky rate limit 耐性
  - GitHub は query 数削減とキャッシュを入れているが、live fetch が続くとまだ 403 に当たる
  - Bluesky は authenticated search 前提になったので、認証情報未設定時の degraded path はかなり弱い
  - backoff / partial skip / run-level circuit breaker を追加する余地がある
- `enrich --all` の中断 / 再開
  - Web UI の `Enrich All` は in-memory background job として実装済み
  - `Max projects` 空欄時は対象件数を制限しない
  - 永続 checkpoint、途中キャンセル、再開は未接続
  - 3000 件規模を日常的に回すなら core 側で run state を保存し、Web はその状態を表示 / 操作する設計が候補
- `assist-db-edit` の強化
  - 現状 action は `merge`, `set_separate`, `block_identifier`, `set_tracking_state`
  - 将来的には `split-project` や seed 追加も自然文から提案できるとよい
- hosted suffix 追加後の split 監査
  - `audit-source-config [domain]` は実装済み
  - suffix 追記、tenant 混在の `split_suggestions`、仮分割後の merge candidate 表示まで接続済み
  - interactive で tenant group を選ぶと service URL / NSID を原子的に切り出す
  - 共有 repo / lexicon や URL 移転の例外があるため確認必須。non-interactive の自動 split は入れない
- 旧 discovery フローから取り込み済みでないアイディア
  - 手動候補を一旦積む inbox 的な導線
  - `first_seen_at` / `last_seen_at` / recent activity を使った差分ベースの priority 補正
  - 差分だけをざっと確認する report view
  - ただし独立 snapshot/state テーブル群は復活させない前提
- `sync-*` の増分最適化
  - `sync-collections` は import 自体は増分化済みだが fetch は全件
  - `sync-github-search` は query の broadness と screen 精度をもう少し詰めたい
  - `sync-semble-collections` は固定 2 collection 前提なので、必要なら設定化する
- review 系の改善
  - `review-queue` は signal を増やしたが、まだ「service は使えそうだが editorial quality が低い」ものの抽出が弱い
  - `review-stalled` は repo / collection activity を見ているが、`introduced_url` や最近の紹介状況も補助信号に使えるかもしれない
  - `llm_manual_review_recommended` のうち `service_status_manual_check` は絞り込み済み
    - 単なる `service_status_confidence=low` では queue に載せない
    - draft の昇格判断、未紹介 service の紹介前検証、矛盾した status evidence の解消に限る
    - 採用理由は `details.action_basis`
  - 他の LLM manual review kind は、レビュー後に変わる判断や完了条件の表現がまだ弱い
  - `atproto_contribution_level` / score / evidence の rule-based v1 は実装済み
    - resolved NSID / schema sample は `core`
    - record sample / collection observation は `substantial`
    - NSID hint だけなら `optional`
    - `category=auth_only` は強い protocol surface が無ければ `auth_only`
    - review / promotion queue の sort に反映済み
  - docs / README evidence と LLM 補助分類は未接続
  - 寄与度レビューも、確認結果によって keep / reject、merge、紹介可否、tracking 優先度が変わる場合に寄せる
  - 実用性と別軸の `technical_interest_level / score / reasons / summary` は追加済み
    - LLM の暫定評価で `high / exceptional` のものを `promotion-queue` の `technical_insight_candidate` に出す
    - 新規設計パターン、protocol gap、相互運用実験、特徴的 architecture、参照実装を区別する
- Web UI 詳細検索
  - `Search` は自由語 score 検索と tracking state / service status / contribution / technical interest / category / introduced / candidate / error / promotion hold / review signal / 日付 filter の AND 条件に対応済み
  - browser 側の `key:value` 演算子は canonical な API query parameter へ変換する。API に別 DSL は追加していない
  - schema 追加なしの in-memory filter なので、遅さが問題になったら scalar 条件を SQLite `WHERE` に寄せる

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
  - `review-queue` は高速化のため通常表示では再計算しない
  - 派生値を明示的に洗い替えたい時は `repair-db` や identifier 更新系操作に寄せる
- `review-queue` は通常表示で軽量 shape を使う
  - 保存済み DB を信頼し、一覧表示では full normalization を避ける
  - 保存時・repair 時の正規化は従来通り

## ドキュメントの読み分け

- 利用者向け
  - [README.md](../README.md)
  - [docs/project-registry.md](project-registry.md)
- 設計・内部仕様
  - [docs/registry-internals.md](registry-internals.md)
  - [docs/project-record.md](project-record.md)
- 将来課題
  - [docs/roadmap.md](roadmap.md)

## コミット時に気をつけること

- `data/` 配下の JSON は生成物や棚卸しメモが混ざる
- `data/tldextract-cache/` は生成 cache
- ユーザーが明示しない限り、`data/*.json` を安易にコミットしない
- DB スキーマや運用ルールを変えたら、利用者向け文書と内部文書の両方を更新する
