# Registry Maintenance Status

この文書は`codex/registry-maintenance` branch内の作業状況と検証結果だけを扱います。
通常運用で現在できることの正本は[../status.md](../status.md)です。このbranchの変更を通常運用へ適用するまでは、
ここに書かれた内容を実装済みの通常仕様として扱いません。

各checkpointの適用時に、通常運用へ入った内容とその検証結果だけを`docs/status.md`へ反映します。この文書は
maintenance series完了時に削除します。

## 現在地

実装済み:

- DB open transaction修正。
  - 新規schema初期化はschema versionを書いた接続内でcommitする。
  - 既存schemaのensureではschema versionの同値upsertを行わない。
  - `database.open_database()`から返る接続は、open処理由来のwrite transactionを保持しない。
- DB path / create契約。
  - 通常CLI、Web、background workerは既存のschema version 2 DBだけを開く。
  - missing pathや未初期化fileは空DBへfallbackせずerrorにする。
  - 新規作成は`project-registry init-db [--db-path PATH]`だけが明示的に行う。
- Python runtime / wrapper契約。
  - 最低versionはPython 3.10。
  - 公開wrapperは`.venv/bin/python`、次に`python3`を選び、import前にversionを検証する。
  - 非対応runtimeではsyntax/import errorへ進まず、実測versionを含む短いerrorと終了code 2を返す。
- `split-project`のexplicit key option削除。
  - 正常利用例・test・現行call siteが無く、成立しない`--new-project-key` / `-n`を削除した。
  - application APIからも未使用引数と到達不能な既存key検証分岐を削除した。
  - CLI、Web、source-configからのsplitは従来どおり新しい`project_key`を自動採番する。
- same-site first-label fallback削除。
  - same-siteはPSLとknown/configured multi-tenant suffixを反映した`root_domain()`一致だけで判定する。
  - 異なるregistrable domainで先頭labelだけが同じhostを、redirect、official/resource link、repo homepageの
    強いevidenceとして扱わない。
- Web `--read-only` mode削除。
  - CLI option、server state、POST拒否分岐、settings API field、UI分岐、scheduler接続、専用test/docsを削除した。
  - deprecated optionやpayload互換fieldは残していない。
  - investigation replay、audit、dry-run、SQLite snapshot集計は別機能として維持する。
- product brief。
  - 一人のoperatorによる紹介判断を目的とし、通常flow、対象境界、正本、主要判断、非目標を
    `docs/brief.md`へ分離した。
  - 最終成果物と成功指標の数値目標は、未決定のものを実装済み仕様として扱っていない。
- 2026-07-10静止DB baseline。
  - 正本DBのAPFS cloneだけを集計し、4.43 GiBのうち`investigation_runs` 2.54 GiB、
    `collector_runs` 1.58 GiB、`projects` 253 MiBであることを確認した。
  - current stateではservice verification、historyではGitHub/service collector payloadが保存量の主因だった。
  - phase別時間、正確なprompt量、operator review時間は現行保存形から測れないことを明示した。
- enrich run observability。
  - initial collection、planning、investigation collection、triage、evaluation、run history save、project saveの
    wall timeを新規runへ保存する。
  - planning/triage LLM requestごとにprompt/response bytes、所要時間、timeout/error分類を保存する。
  - prompt本文、endpoint、command、API keyは保存せず、replay evaluationから非決定的なmetricsを分離する。
  - Webのlatest run、run list、run detailへ接続した。
- service verification保存budget。
  - full responseから判定とrelationを作った後、HTML extract 12 KiB、links 128件/64 KiB、URLs 192件/32 KiBへ
    保存payloadを縮約する。
  - repo、Bluesky profile、補助page、同一host系を優先して代表linkを残し、観測総件数とfull HTML digestを保持する。
  - current stateは`lane + requested_url`ごとの最新結果、最大32 surfaceとし、時系列はrun historyへ分離する。
  - 2026-07-10 snapshotへのread-only概算は204.6 MBから43.2 MBで、正本DB cleanupは実施していない。
- collector history保存budget。
  - record/failure本文は`bundle_json`へ一度だけ保存し、`collector_runs`はsource/operation別のworst status/count
    projectionへ変更した。
  - GitHub snapshotをintegration使用fieldへ縮約し、semantic hint本文はtyped relationだけに保持する。
  - initial/provider viewで同じrepoを一runに二度collectしない。
  - snapshot投影ではcollector payload 1.66 GBから26.5 MB、bundle 701.7 MBから370.5 MB相当だった。
  - GitHub payload p95付近10 runのnetwork-free semantic replayは10/10不変。最大outlier replayは未完走。
- 既存payload cleanup command。
  - `cleanup-storage-payloads`はrunを削除せず、current service evidence、input/output snapshot、bundle、collector
    projectionを現行budgetへ揃える。dry-runが既定。
  - executeは一transactionでrewriteし、commit前にforeign key/integrity checkを行う。VACUUMは明示option。
  - 2026-07-10 clone全件では4.620 GBから1.723 GB、2.897 GB（62.7%）削減見込み。
  - snapshot evaluation fieldは15,086 runで差分0、p95付近10 runの完全replayは10/10不変。
  - 最大削減上位5 runの完全replayは各10秒でtimeoutし未検証。
- LLM evidence prompt budget。
  - planning/triageの可変evidenceを整形後24 KiB、文字列4 KiB、field別代表件数で制限した。
  - listは先頭・末尾を残し、追加縮約時はfield別の観測件数と採用件数を`evidence_budget`へ入れる。
  - 2026-07-10 clone全3,239 projectへの投影は最大24,489 bytes、budget発動41件。実LLM callは未検証。
- CLI adapter分離。
  - stalled/ignore/out-of-scopeのinteractive review入出力を`registry.py`から`commands/review.py`へ移した。
  - merge-batchのinteractive action、undo、出力を`commands/merge.py`へ移した。
  - assist-db-editの候補表示、確認、mutation adapterを`commands/assist.py`へ移した。
  - 判定helperと保存契約は変更せず、`registry.py`から`cmd_*`と`argparse`依存を除去した。
- domain policy分離。
  - PSL、known/configured multi-tenant suffix、registrable domain、same-site判定とsuffix設定を
    `domain_policy.py`へ機械的に移した。
  - CLI/Webを含むcall siteを同時に移し、`registry.py`に旧symbolのre-exportは残していない。
  - このcommitでは判定policyと保存形式を変更していない。
- service verification責務整理。
  - evidence collectorと保存budgetに加え、`lane + requested_url`単位の最新32 surfaceを保持するcurrent
    projectionを`service.py`へ集約した。
  - generic evidence適用は`integration.py`、既存payloadのrewriteは`storage_cleanup.py`に残し、両方が同じ
    projection関数を利用する。保存契約は変更していない。
- HTTP transport分離。
  - outbound URL検証、内部host遮断、redirect先再検証、bytes/JSON requestを`http_transport.py`へ移した。
  - collector call siteとtest doubleを新moduleへ直結し、`registry.py`に旧symbolのre-exportは残していない。
  - request header、timeout、redirect上限、URL正規化の契約は変更していない。実外部sourceへのsmoke testは
    実施していない。

作業中:

- 新規timingの代表sample取得と、通常運用へ適用するcheckpointの整理。

未実装:

- [registry-maintenance-plan.md](registry-maintenance-plan.md)のその他のPhase 1以降。

DB適用:

- なし。正本DBは変更していません。cleanupはclone dry-runとtemp DB executeだけです。

## 現行HEADの検証

動作確認済み:

- Python 3.10.20と3.11.15でunit test 332件が全件成功。
- 新規DBをcaller側のcommitなしでclose/reopenしてschema versionを確認する回帰test。
- 既存DBをopenした接続を保持したまま、別接続が`BEGIN IMMEDIATE`を取得できる回帰test。
- missing pathと未初期化fileを通常openが拒否し、fileを暗黙作成しない回帰test。
- custom pathを`init-db`で作成し、schema versionとinvestigation tableを確認する回帰test。
- Webがmissing DBを検出し、serverをbindする前に停止する回帰test。
- 二つの公開wrapperがfake Python 3.9.18をimport前に拒否する回帰test。
- repository-local `.venv`が無い状態で、system Python 3.14.6を使う二つの公開wrapperの`--help`。
- `split-project` parserにexplicit key属性が無く、自動採番splitとWeb splitが成功する回帰test。
- same first-label / different registrable domainをdirect判定、redirect、official/resource link、repo homepageで
  拒否する回帰test。同一registrable domainのsubdomainはsame-siteとして維持する。
- domain policy分離後のsuffix設定ファイル更新、cache反映、Web API追加とsame-site判定の回帰test。
- HTTP transport分離後の内部host遮断、redirect先再検証、明示的なoperator endpoint許可、query送信の回帰test。
- investigation benchmark 6 caseが全件成功、semantic regression 0件。
- JavaScript syntax checkが成功。
- temp DBの実browserでSettings、Operations、project detailを確認し、旧mode表示が無いこととmutation/job操作が
  通常表示されることを確認した。操作実行と正本DB更新は行っていない。
- timing付きrunを持つtemp DBの実browserで、latest duration、run list duration、7 phase、planning/triage別の
  prompt/response bytes、success/timeout分類を確認した。
- metricsがreplay evaluationとfixture exportへ混入せず、prompt/endpoint/commandが保存対象外になる回帰test。
- command型LLM transportが設定commandを呼ぶ回帰test。これに必要な`subprocess` import欠落も修正した。

未検証:

- 正本DBを使ったCLI/Web/workerの再起動。今回の変更はtemp DBだけで確認し、正本DBは開いていません。
- first-label誤判定に由来するconfirmed identifierやrelationが正本DBに何件あるか。
- 2026-07-10 baseline以前のphase別時間。過去runへ遡及できないため。
- transaction commit、provider token数、operator review時間。観測経路が未接続のため。

## Baseline

2026-07-10にcommit `aa44d37`で動作確認済み:

- `/opt/homebrew/bin/python3.11` 3.11.15でunit test 301件が全件成功。
- Node.js 26.5.0で`app.js`のsyntax checkが成功。
- investigation benchmark 6 caseが全件成功、semantic regression 0件。
- repository-local `.venv`が無い状態で、system `python3` 3.14.6を使う2つの公開wrapperの`--help`が成功。

このbaselineは外部API、実LLM、正本DB、実browserを使っていません。公開wrapperは対応Pythonを選べた
この環境でのみ動作確認済みです。
