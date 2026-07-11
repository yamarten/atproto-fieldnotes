# Registry Maintenance Status

この文書は`codex/registry-maintenance` branch内の作業状況と検証結果だけを扱います。
通常運用で現在できることの正本は[../status.md](../status.md)です。このbranchの変更を通常運用へ適用するまでは、
ここに書かれた内容を実装済みの通常仕様として扱いません。

各checkpointの適用時に、通常運用へ入った内容とその検証結果だけを`docs/status.md`へ反映します。この文書は
maintenance series完了時に削除します。

## 実行台帳

状態とcloseout条件は[registry-maintenance-plan.md](registry-maintenance-plan.md#実行管理とcloseout-gate)を
正本とします。`一部完了`、`未着手`、`別operation待ち`は未終端です。series closeout時には全行を
`完了`または`roadmapへ延期`へします。

### Phase 0 / 1

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P0 | 完了 | `5951b42`。plan、branch status/handoff、roadmap、baselineを作成 | なし。以後のHEAD検証は本書の検証節を更新する |
| P1.1 | 完了 | `935f77a`。schema初期化commitとread openのwriter非block test | なし |
| P1.2 | 完了 | `99b5492`。Python 3.10最低version、公開wrapper test | なし |
| P1.3 | 完了 | `eb05251`。明示`init-db`、missing/uninitialized DB拒否test | なし |
| P1.4 | 完了 | `58c70f5`。未使用explicit split keyをCLI/applicationから削除 | なし |
| P1.5 | 完了 | `33dcccb`。first-label fallback削除とconsumer回帰test | 過去DB audit/repairはP3.3で追跡 |
| P1.6 | 完了 | `f0c3b47`。Web read-only option/state/UI/test/docsを削除 | なし |

### Phase 2

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P2.1 | 完了 | `61af6f6`。`docs/brief.md`を作成 | 数値目標は未決定と明記済みで、実装済み仕様にはしていない |
| P2.2 | 完了 | `61af6f6`の容量baselineに加え、[2026-07-11 smoke](registry-maintenance-real-source-smoke-2026-07-11.md)でprompt-size分位10 project、20実LLM requestを測定 | full enrich phase蓄積とoperator review時間は通常運用で測るroadmapへ分離 |
| P2.3 | 完了 | `be27ef4`。7 phaseとplanning/triage request metricsを保存・API・Webへ接続 | 代表sample取得はP2.2で追跡 |
| P2.4 | 完了 | [2026-07-11 smoke](registry-maintenance-real-source-smoke-2026-07-11.md)でGitHub、Semble、Bluesky introduced、collection inventory、Tangledのbounded worker dry-runと実LLM接続を確認。collection/Tangledへ`max_items`を追加 | live smokeは通常接続、failure/cancelはdeterministic test、partial-result policy変更はP4.3の責務とした |
| P2.5 | 未着手 | なし | request log、traceback、scheduler失敗、lock範囲、constant-time token比較、innerHTML、利用制約を個別に監査し、実装またはroadmap判断する |

### Phase 3

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P3.1 | 一部完了 | `540b1b4` service budget、`229b1c3` collector/history縮約、`42cddbc` LLM evidence budget | `resource_url_records`の既存budgetが全生成経路で適用されることを監査する |
| P3.2 | 一部完了 | `3243702`。`cleanup-storage-payloads`と2026-07-10 clone全件dry-run。4.620 GB→1.723 GB見込み | planにあるresource URL、role/host/path pattern、重複LLM evidenceのcategory別reportとの差を埋めるか、現行cleanup scopeを採用する判断を明記する |
| P3.3 | 別operation待ち | `3243702`でtransaction、temp DB execute、FK/integrity/rollbackを確認 | 正本writer停止、backup、dry-run承認、execute、semantic audit、Web/worker再起動。same-site過去誤帰属auditも同windowで判断する。正本DBは未変更 |
| P3.4 | 一部完了 | `229b1c3`でbundle/collector本文二重保存を解消 | cleanup適用後の増加率を測り、snapshot差分化、stub、圧縮、retention、history DB分離を採否またはroadmapへ終端する |

### Phase 4

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P4.1 | 完了 | [2026-07-11 smoke](registry-maintenance-real-source-smoke-2026-07-11.md)とcode auditでcandidate serviceの自動attachを確認・修正。40-card clone dry-runでexact 37件、candidate suggestion 2件、unmatched 1件 | normalized indexと残るlookup costはP4.2で追跡 |
| P4.2 | 未着手 | なし | exact lookupのnormalized index利用、allocator利用、fuzzy lookup分離を実装またはroadmap判断する |
| P4.3 | 未着手 | なし | bulk failure/cancel result policyを決め、実装・testするかroadmap判断する |
| P4.4 | 未着手 | `3d0b598`はtransport分離のみでretry policyを変更していない | failure分類、payload fallback、timeout後retry、command/endpoint timeoutを決定・実装するかroadmap判断する |
| P4.5 | 未着手 | source/config境界の文書化は`5951b42` | introduced既定query等の二重管理を監査し、operator可変値だけをconfig/requestへ移すか現状維持を決める |

### Phase 5

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P5.1 | 未着手 | 既存benchmark 6 caseはmanual gold setではない | current snapshotからdecision/bundle相関をread-only exportする |
| P5.2 | 未着手 | なし | fixture sanitize ruleと除外情報を決定・文書化する |
| P5.3 | 未着手 | なし | 少数のoperator correction fixtureで実際に退行を検出できることを確認する |

### Phase 6

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P6.1 | 完了 | `registry-internals.md`で現行import方向、抽出済み境界、未分離facadeを明記 | 現行仕様の正本へ置き、重複するnotes mapは作らないと判断 |
| P6.2 | 完了 | `5b50e10`、`e3906a3`、`9486346`。`registry.py`から`cmd_*`/`argparse`依存を除去 | なし |
| P6.3 | 完了 | `32486fb`のmodule分離に加え、requirements/documentationどおり`tldextract + vendored PSL`を正本化 | 自作PSL fallbackを削除し、依存/PSL欠落は明示失敗する |
| P6.4 | 完了 | `540b1b4`、`29888c4`。collector/budget/current projectionを`service.py`へ集約 | なし |
| P6.5 | 完了 | `8fccfa6` HTTP、`3d0b598` LLM transport seam。旧re-exportなし | 実source/実LLM確認はP2.4で追跡 |
| P6.6 | 完了 | `4e37b00`の`identifiers.py`に加え、provider repo URLを`repo_urls.py`、pure name helperを`naming.py`へ移動 | call siteを同時更新し、`registry.py`に旧symbolをre-exportしていない |
| P6.7 | roadmapへ延期 | 現行import方向とstateful clusterを監査 | source sync=P4.2後、enrich=P4.3/P4.4後、project save=P3.3後、queue=新policy/consumer発生時という再開条件をroadmapへ固定 |

### Phase 7

| ID | 状態 | 対応commit・確認 | 残件 / 終端条件 |
|---|---|---|---|
| P7.1 | 未着手 | なし | 全IDを終端し、通常側brief/status/roadmap/handoffへ反映してmaintenance用status/handoffを削除する |
| P7.2 | 未着手 | なし | README/project-registry重複、絶対path、stale test数、registry-internals節を独立auditする |

## DB適用

- 正本DBへの適用はありません。
- cleanupは2026-07-10 cloneのdry-runとtemp DB executeだけです。
- P3.3を実行するまでは、branchの保存契約と正本DBの既存payloadが異なる状態です。

## 現行HEADの検証

動作確認済み:

- `requirements.txt`を導入した隔離環境のPython 3.10.20と3.11.15でunit test 342件が全件成功。
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
- LLM transport分離後のcommand呼出し、OpenAI互換response shape、metrics、timeout分類の回帰test。
- URL/NSID/authority/alias helperのdirect回帰testと、既存consumer test。
- investigation benchmark 6 caseが全件成功、semantic regression 0件。
- JavaScript syntax checkが成功。
- temp DBの実browserでSettings、Operations、project detailを確認し、旧mode表示が無いこととmutation/job操作が
  通常表示されることを確認した。操作実行と正本DB更新は行っていない。
- timing付きrunを持つtemp DBの実browserで、latest duration、run list duration、7 phase、planning/triage別の
  prompt/response bytes、success/timeout分類を確認した。
- metricsがreplay evaluationとfixture exportへ混入せず、prompt/endpoint/commandが保存対象外になる回帰test。
- command型LLM transportが設定commandを呼ぶ回帰test。これに必要な`subprocess` import欠落も修正した。
- 空のtemp DBを使った実workerからGitHub、Semble、Bluesky introduced、collection inventory、Tangledへの
  bounded dry-runと、設定済み実LLM endpointへの接続。collection/Tangledの再実行を含めDB件数は不変。
- 3,239-project cloneへSemble 40 cardsをdry-runし、confirmed/manual match 37件、candidate suggestion 2件、
  unmatched 1件を確認。協調停止した初回runと完走runのどちらもDB件数は不変。

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
