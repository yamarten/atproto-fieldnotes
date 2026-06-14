# Investigation Model

project 調査の責務境界と evidence の恒久的な契約を定める正本文書です。source ごとの具体的な
heuristic と request 上限は [registry-internals.md](registry-internals.md)、保存 field は
[project-record.md](project-record.md)、検討中の論点は [notes/](notes/) を参照してください。

## 目的

- collector の追加や変更で読む working set を小さくする
- 外部からの観測、project への統合、評価、保存を分離する
- source ごとの失敗を隔離し、得られた evidence は失わない
- 保存済み evidence から network なしで評価を反復する
- manual confirmation、defer、block を自動評価より優先する

## Pipeline

調査は `collector -> evidence -> integration -> evaluation -> save` の順で扱います。

1. collector
   - query planning を含め、外部 source を読み structured evidence を返す
   - 接続済み collector は共有 project dict を直接変更しない
2. evidence
   - 観測と source の完了状態を JSON-compatible な bundle に正規化する
   - relation は subject、predicate、object、locator、抽出方法を持つ
3. integration
   - evidence から既存 project field を変更できる唯一の層
   - project 文脈、manual guard、複数 source の corroboration を適用する
4. evaluation
   - ownership、atproto寄与度、service status などを rule-based に計算する
   - network、DB、LLM に依存しない関数を優先する
5. save
   - `projects` を現在状態の materialized record として保存する
   - 同じ SQLite に input/output snapshot、評価、bundle、source 状態も保存する

公開CLI入口は`bin/project-registry`で、`scripts/project-registry`はそこへ委譲するaliasです。`main()`は`project_registry_core.cli`、調査orchestrationの既存実装は`project_registry_core.registry`にあります。旧top-level Python APIは削除済みです。

## Evidence classification

- `confirmed`: manual seed、first-party claim、検証済み identity path など強い帰属を持つ観測
- `candidate`: 関連性はあるが、帰属または意味の追加確認が必要な観測
- `derived`: 他の観測から決定的に導出した query、正規化結果、usage relation
- `heuristic`: score、文字列類似、community source など誤判定し得る推定

classification は project field の confidence と同義ではありません。integration が guard と project
文脈を適用し、confirmed field、candidate field、observation のどこへ反映するかを決めます。

## EvidenceBundle v2

通常readerが受理するのはEvidenceBundle v2だけです。旧v1 fixtureはv2へ変換済みで、v1互換readerは削除しました。

- `EvidenceBundle`
  - `schema_version`, `project_key`, `collected_at`
  - 順序付き `records`, `failures`, `collector_results`
- `EvidenceRecord`
  - `kind`, `source`, `classification`, `observed_at`
  - JSON-compatible な `value` と query、score、repo path などの `context`
- `CollectorFailure`
  - `source`, `operation`, `target`, `category`, `message`, `retryable`
- `CollectorResult`
  - `success | partial | skipped | rate_limited | failed`
  - request/result 数、cursor、stop reason、retry 時刻、cache hit

主要 relation predicate は `defines_schema`, `uses_collection`, `mentions_collection`,
`official_account`, `operates_service`, `source_repository`, `requests_permission`, `redirects_to` です。
同じ README から複数抽出した claim は独立 source と数えません。

## 正本と保存

実装済み:

- `projects` は UI、検索、merge、review が読む現在状態の正本
- `investigation_runs` は input project snapshot、bundle、evaluation version、保存済み評価、output snapshot を保持
- `collector_runs` は source/operation ごとの状態、対応 record、failure を順序付きで保持
- full HTML や README 本文は保存せず、bounded extract、hash、URL、repo path など判断に必要な材料だけを残す
- raw evidence の自動削除は行わない

run 保存は project row の代替ではありません。再評価可能な調査成果と、その時点の materialized
record を対応づける履歴です。

## 調査状態

次を区別します。

- `success`: 予定範囲を完了した
- `partial`: usable record と failure の両方がある
- `skipped`: 設定不足、不要判定、budget 超過などで実行していない
- `rate_limited`: upstream 制約で打ち切った
- `failed`: usable result を得られなかった
- 証拠なし: 完了した collector が対象 evidence を返さなかった
- 未調査: collector 自体が実行されていない

「証拠なし」を否定的事実へ変換するには、対応 source が成功して必要範囲を走査したことが必要です。

## Relation と ownership

- `defines_schema` は ownership の強い候補になる
- `uses_collection` と OAuth の `repo:` permission は利用関係であり ownership を確定しない
- `mentions_collection` は docs、example、test、fixture、vendor、generated などの弱い言及
- confirmed repo と first-party app が独立 source から相互リンクした場合、`source_repository` を昇格できる
- NSID ownership は manual seed、公式 account の PDS schema record、confirmed repo の
  `defines_schema`、authority までの identity path を優先する
- defer / block された identifier は relation corroboration より優先して抑止する

## Replay と benchmark

実装済み:

- `replay-investigation`: 保存 run の integration / evaluation を network なしで再実行し semantic diff を出す
- `benchmark-investigation`: fixture corpus を一括評価し relation/evaluation の退行を報告する
- `export-investigation-fixture`: 保存 run を sanitize 対象の fixture 形式へ出力する
- corpus は at.fund、BioSky/observ-ing、spores.garden、Margin、Tangled、Passing Reads の6 caseを持つ

fixture は raw response archive ではありません。誤判定を再現するための最小 evidence と期待評価を
保存します。実 project の追加と sanitize 運用は継続課題です。

## LLM の位置づけ

- LLM は query planning、editorial 補助、曖昧な分類候補に使う
- identifier の新規提案は原則 candidate に留める
- ownership、高い寄与度、service の到達確認を LLM 単独で確定しない
- rule-based evaluation と manual guard を優先する
- LLM failure 時も deterministic plan と他 collector を継続する

## 接続状況

実装済み:

- GitHub repo/search、Bluesky actor/post/feed
- GitHub lexicon JSON と source usage の意味分類
- lexicon resolution、recent record sample、service static/deep、resource page の evidence 化
- OAuth client metadata の permission relation
- anonymous browser service verification
- confirmed developer account からの Tangled PDS record 探索
- UFOs rolling window の `sync-tangled-repos`
- root と `www` の観測済み redirect/canonical alias
- retry、Retry-After、TTL cache、source-local circuit breaker、request budget

未接続:

- Tangled timeline、community search、bot adapter の通常実行。parser はあるが config 既定無効
- OAuth `include:` permission set の再帰解決
- browser 認証後操作。登録、認証入力、投稿は設計上の非目標
- Web UI からの run/evidence inspection

未検証:

- 実 Playwright を用いた localhost smoke test
- 実 API での retry/cache/budget の長時間運用値
- UFOs rolling window 外を含む Tangled 完全履歴

関連 notes:

- [identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- [service-verification.md](notes/service-verification.md)
- [evidence-replay.md](notes/evidence-replay.md)
- [collector-reliability.md](notes/collector-reliability.md)
