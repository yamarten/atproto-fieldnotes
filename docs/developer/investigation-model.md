# Investigation Model

project 調査の責務境界と evidence の恒久的な契約を定める正本文書です。source ごとの具体的な
heuristic と request 上限は [registry-internals.md](registry-internals.md)、保存 field は
[project-record.md](project-record.md)、検討中の論点は [notes/](../notes/) を参照してください。

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

公開command入口は`bin/project-registry`と`bin/project-registry-web`です。調査CLIの`main()`は`project_registry_core.cli`、調査orchestrationの既存実装は`project_registry_core.registry`にあります。旧top-level Python APIと`scripts/`配下のCLI wrapperは削除済みです。

Web detailはproject単位のinvestigation run summaryをSQLite rowから一覧し、選択したrunだけbundleをdecodeします。run detailのevidence/failureは上限付きで、raw HTMLやREADME全文を保存・表示しません。Replayは保存runを現行ruleで要求時に再評価するread-only処理で、結果をDBへ永続化しません。

### Run observability

新規enrich runは、次のwall timeをmillisecondで保存します。

- initial collection + integration
- investigation planning
- investigation collection + integration
- LLM triage + application
- final evaluation
- investigation run historyのserialization + SQL
- current projectのnormalization、serialization + SQL

collectorのnetwork取得とevidence適用は現行pipelineで交互に行うため、前半2つのcollection区間をnetwork時間だけと
見なしません。transaction commit待ち時間も未計測です。`total_ms`は上記のenrich開始からproject SQL書込みまでで、
process job全体、複数projectの待ち時間、operator review時間ではありません。

planningとtriageの各LLM requestには、purpose、success/failure、error category、duration、UTF-8 prompt bytes、
response JSON bytes、設定された1 attempt当たりtimeout、model/API style等の非secret metadataを残します。prompt本文、
endpoint、command、API keyは保存しません。token数はprovider共通に取得できないため未接続です。`timeout`は
明示的なtimeout例外または既存clientが返すtimeout errorから分類し、transport failure、endpoint incompatible、
rate limit、その他HTTP error、invalid responseと分けます。endpoint互換性エラー以外では同じpromptを自動再送しません。

保存先は`evaluation_json._run_metrics`ですが、load時はsemantic `evaluation`と`metrics`に分けます。そのため
非決定的な時間値はreplay diffやfixture exportへ入りません。Webのlatest run、run list、run detailから確認できます。

### LLM evidence budget

planningとtriageへ渡す可変evidence JSONは、整形後UTF-8で24 KiBを上限とします。fieldごとの代表件数上限を先に
適用し、長いlistは先頭と末尾から均等に残します。それでも超える場合は低優先fieldから追加で縮約します。
`project_key`等のscalarはlist budgetで落としません。縮約した場合は`evidence_budget`へfield別の観測件数と採用件数を
入れます。planningとtriageは調査の異なる時点で同じbudget規則を使い、triageへ渡したevidenceは
`llm_source.evidence`にも保存します。

これはLLM入力契約であり、service HTMLやEvidenceBundleの保存budgetとは別です。prompt templateの固定文まで24 KiBに
含める契約ではありません。実requestの最終prompt bytesはrun metricsで別に観測します。

Service / ownership reviewも保存済みcurrent state、normalized relation、collector runだけを読みます。画面表示のためにnetwork collectorを再実行しません。保存根拠がない状態は`not_observed`であり、到達不能・非ownership・permissionなしの確定結果ではありません。

## Evidence classification

- `confirmed`: manual seed、first-party claim、検証済み identity path など強い帰属を持つ観測
- `candidate`: 関連性はあるが、帰属または意味の追加確認が必要な観測
- `derived`: 他の観測から決定的に導出した query、正規化結果、usage relation
- `heuristic`: score、文字列類似、community source など誤判定し得る推定

classification は project field の confidence と同義ではありません。integration が guard と project
文脈を適用し、confirmed field、candidate field、observation のどこへ反映するかを決めます。

## EvidenceBundle v2

通常readerが受理するのはEvidenceBundle v2だけです。旧v1 bundleの互換readerは提供しません。

- `EvidenceBundle`
  - `schema_version`, `project_key`, `collected_at`
  - 順序付き `records`, `failures`, `collector_results`
- `EvidenceRecord`
  - `kind`, `source`, `classification`, `observed_at`
  - JSON-compatible な `value` と query、score、repo path などの `context`
  - service verificationのHTML/link/URLはcollector内の判定後に保存budgetを適用し、観測総数とdigestを併記する
- `CollectorFailure`
  - `source`, `operation`, `target`, `category`, `message`, `retryable`
- `CollectorResult`
  - `success | partial | skipped | rate_limited | failed`
  - request/result 数、cursor、stop reason、retry 時刻、cache hit

### Collector history projection

replayに使うrecord/failure本文は`bundle_json`へ一度だけ保存します。`collector_runs`はWebと集計用のprojectionで、
同じrunの同じ`source + operation`を一行へまとめ、worst status、request/result/cache count、record/failure countを
`result_json`へ保存します。source別payloadを`records_json` / `failures_json`へ反復複製しません。

GitHub repo collectorは同じrepoを一runで二度取得しません。snapshotの`repo`はintegrationで使うname、description、
homepage、fork parent、updated timestamp等だけを保存します。source semantic hint本文は別のtyped relationに展開済みなので
snapshotへ重複保存せず件数だけを残します。README URLは最大64件、JSON 16 KiB、1 URL 2 KiBです。relationと
candidate生成はbudget適用前の全取得結果から行います。

通常接続済みcollectorは`CollectorResult`を明示的に返します。record/failureからsource状態を推測する互換処理は現行導線では使いません。failure categoryはtimeout、rate limit、request budget、network error、remote 5xx、invalid response、browser error、collector errorなど、再試行・運用判断に必要な単位で固定します。

主要 relation predicate は `defines_schema`, `uses_collection`, `mentions_collection`,
`official_account`, `operates_service`, `source_repository`, `requests_permission`, `redirects_to` です。
同じ README から複数抽出した claim は独立 source と数えません。

## 正本と保存

実装済み:

- `projects` は UI、検索、merge、review が読む現在状態の正本
- `investigation_runs` は input project snapshot、bundle、evaluation version、保存済み評価、output snapshot を保持
- `collector_runs` は source/operation ごとの状態とrecord/failure件数をprojectionとして保持
- full HTML や README 本文は保存せず、bounded extract、hash、URL、repo path など判断に必要な材料だけを残す
- raw evidence の自動削除は行わない。手動retention commandで古いrun単位に削除できる

run 保存は project row の代替ではありません。再評価可能な調査成果と、その時点の materialized
record を対応づける履歴です。

既定retention policyは完了から90日以内の全runとprojectごとの最新3 runを保持します。`prune-investigation-history`はそれより古いrunをdry-runで列挙し、`--execute`時だけ対応するcollector runとまとめて削除します。fixtureへexportしたcaseはDB runとは独立して保持します。

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

- `defines_schema` は accepted repo と accepted NSID の間だけに保存する schema definition relation
- repositoryにlexicon JSONらしいファイルが存在するだけの場合は、`defines_schema` ではなく弱い `mentions_collection` として扱う
- `uses_collection` と OAuth の `repo:` permission は利用関係であり ownership を確定しない
- `mentions_collection` は docs、example、test、fixture、vendor、generated などの弱い言及
- confirmed repo と first-party app が独立 source から相互リンクした場合、`source_repository` を昇格できる
- NSID ownership は manual seed、公式 account の PDS schema record、authority までの identity path を優先する
- manual repo seedはsource repositoryの根拠であり、そのrepo内にある全NSIDのownership根拠ではない
- service到達確認は`service_verification`として保存するが、`operates_service` relationはproject support判定後にだけ保存する
- defer / block された identifier は relation corroboration より優先して抑止する

## Replay と benchmark

実装済み:

- `replay-investigation`: 保存 run の integration / evaluation を network なしで再実行し semantic diff を出す
- `benchmark-investigation`: fixture corpus を一括評価し relation/evaluation の退行を報告する
- `export-investigation-fixture`: 保存 run を sanitize 対象の fixture 形式へ出力する
- executable corpusは通常replay 6 caseに、operator correctionのblock/defer/confirm 3 caseを加えた9 caseを持つ
- `manual-gold-v1.json`はoperator reviewから固定したidentity / service / repository判断11 caseと、promotionのhold / exclude 9 caseを持つ

fixtureはraw response archiveではありません。誤判定を再現するための最小evidenceと期待評価を保存します。
自動exportはsecret、authorization/cookie/header、raw HTML/body/readme/text extract、prompt/command/endpoint、
local pathを削除またはredactし、URLのuserinfo/query/fragmentを除去します。export結果にはmanual review必須markerを
付けます。コミット前に実project/run key、時刻、handle/DID、自由記述を再現に必要な最小範囲へ人が縮約します。

manual gold setはDBのdecision rowを逐次mirrorしません。synthetic project ref、公開identifier、期待actionまたはpromotion disposition、根拠要約だけを
保持し、実project key、run ID、decision時刻、有効期限、operator noteを除外します。現時点ではqueue紹介可否、
atproto利用深度、技術的新規性の正解labelを持たないため、これらはqueue policyのsample reviewで追加します。

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
- Tangled repo URL のHTML/README inspection
- UFOs rolling window の `sync-tangled-repos`
- root と `www` の観測済み redirect/canonical alias
- retry、Retry-After、TTL cache、source-local circuit breaker、request budget

未接続:

- Tangled timeline、community search、bot adapter の通常実行。parser はあるが config 既定無効
- OAuth `include:` permission set の再帰解決
- browser 認証後操作。登録、認証入力、投稿は設計上の非目標
- raw HTMLやREADME全文を含む無制限のevidence inspection

未検証 / 制約:

- Tangled repo inspection は公開HTMLと `raw/{ref}/{path}` でREADMEを取得する。GitHub APIのような完全なrecursive tree APIとは扱わない

Web UIのrun summary、bounded evidence、input/output/evaluation、要求時のread-only replay差分は
接続済みです。

未検証:

- 実 Playwright を用いた localhost smoke test
- 実 API での retry/cache/budget の長時間運用値
- UFOs rolling window 外を含む Tangled 完全履歴

関連 notes:

- [identity-ownership-evidence.md](../notes/identity-ownership-evidence.md)
- [service-verification.md](../notes/service-verification.md)
- [evidence-replay.md](../notes/evidence-replay.md)
- [collector-reliability.md](../notes/collector-reliability.md)
