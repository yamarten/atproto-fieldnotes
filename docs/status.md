# Project Registry Status

この文書は、通常導線で現在できること、既知の制約、直近の検証状況の正本です。
恒久的なデータ契約やheuristicの詳細は各仕様書、未着手の候補は
[roadmap.md](roadmap.md)、次スレッド固有の引き継ぎは
[thread-handoff.md](thread-handoff.md)を参照してください。

## 現在の通常導線

実装済み:

- schema version 3のSQLite registry実装
  - `projects`のscalar、`profile_json`、`state_json`
  - normalized `project_identifiers`、`project_relations`、`project_decisions`
  - 非現行identifier、identifier採用理由、確定transition、registry-wide exact exclusion
  - `investigation_runs`、`collector_runs`の調査履歴
  - version 2 DBは通常openで暗黙移行せず、review済みmanifestから新しいversion 3複製DBを作って検証後に切り替える
- 公開command入口
  - `bin/project-registry`
  - `bin/project-registry-web`
  - CLI/Web/test wrapperは現在checkoutのvenvを優先し、worktreeではGit共通checkoutのvenvも利用
- 内部実行境界
  - source sync本体は`source_sync.py`
  - 1 projectのenrich本体は`enrichment_pipeline.py`
  - interactive queue入出力は`commands/queue_interaction.py`
  - project state、保存、normalized exact lookup、project key allocationは`project_record.py`
  - collector/finalize orchestrationとmerge/queue policyは`registry.py`
- Web UIの日常運用
  - normalized tableを直接使うproject検索と構造化filter
    - 自由入力検索は `project_key`、`name`、`website_url`、identifier value、relation subject/object/locator、decision target/note の大小文字を無視した部分一致
    - summary、tagline、raw evidence、README/HTML全文、自然語の意図や概念は検索対象外
  - identifier、relation、collector failure、developer accountのreview action
  - project nameとnotesのpreview付き編集
  - bounded investigation run viewerとread-only replay
    - 新規enrich runのphase別wall timeとLLM request size/statusを表示
  - service verification比較、ownership、OAuth、URL aliasの確認
  - 保存済みstateに対するExplain
  - source/targetの主要属性とidentifierを並べるmerge review
  - identifierのrole/classification対応を行単位で表示
  - background jobの経過時間、progress比から算出したETA、手動details更新
  - Git checkoutではSettingsにcommit hashとdirty状態を表示
  - `Add seed`は明示されたNSID、path付きservice URL、repository URLを対応するconfirmed factual roleへ投影し、採用decisionを記録する。同じseedの再適用でlegacy rowの不足を補修可能
- background operation
  - enrich、sync、review、保存済みservice URLのanonymous browser verificationを別processで実行
  - operation別のprogressと協調停止
  - enrich checkpointの再発見と再開
  - bulk enrichはproject失敗をcheckpointへ残して後続を続行し、run全体をfailedとして失敗分をresume可能
  - review dry-run planの選択実行とfingerprint再検証
  - Operationsからallowlist済みjob presetを手動または定期発火
    - 通常`enrich --all`、全tracking state対象`enrich --all`後のstalled review一括反映、`sync introduced`、introduced以外のsync一括後`enrich --all --new-only`
    - presetごとに毎週または第N週の曜日、時刻、time zoneを設定可能
- external source設定
  - introduced/collection/GitHub/Semble/Tangledのenabled、URL、query、scope、request budgetは`config/sources.json`が正本
  - TangledはAPI keyを環境変数からだけ読み、Jetstream v2 snapshotをsealed sequence watermarkから増分処理する
  - application serviceでCLI/Web/worker共通に解決し、matching/classification policyはcode/testに固定
- Web serverの手動reload
  - Settingsから同じ起動引数を保ち、package entrypointでprocessを再起動
  - active background jobへ協調停止を要求し、timeout後はworkerを終了してbrowserを再接続
- macOS LaunchAgentによるWeb serverの自動起動
  - `project-registry-web-service`でログイン時に実運用bindの`0.0.0.0:8888`へ起動し、異常終了時に再起動
  - plistへsecretを保存せずworkspaceの`direnv`環境を継承。SIGTERM時はactive workerを終了
- Web診断とlocal access境界
  - HTTP request logはquery/bodyを除いたmethod、path、status、所要時間だけをstderrへ記録
  - unexpected HTTP exceptionはserver側へtracebackを残し、job stderrとは分離
  - scheduled workerの起動失敗はqueueを終端し、presetの最終status/messageへ保存
  - POST JSON bodyはmutation lock外でparseし、mutation処理だけを直列化
  - API tokenはconstant-timeで比較
  - UIのHTML templateは外部文字列を共通escape/link/JSON helperへ通す
- project detail
  - 初期detailはmerge候補計算を待たずに表示
  - merge候補は後続requestで計算し、完了次第表示
  - 計算結果はDBへ保存しない
- atomic project mutationとmerge lineage
  - merge、split、separateの複数project書込みを単一transactionで実行
  - merge済みsource keyをcanonical projectへ解決
  - source keyのinvestigation runをtarget detailへ束ね、保存snapshotは維持
- 手動cleanup
  - investigation履歴は既定90日かつprojectごとに最新3 runを保持
- bounded service verification保存
  - current stateはsurfaceごとの最新結果を最大32件保持
  - project直下のservice status/confidence/evidenceは、最新のURL別・lane別resultからpure projectionし、過去scalarのmax/unionを引き継がない
  - `follow_up`とsupporting pageはscalar対象外。肯定的browser観測は同一URLのdocs-only/coming-soon分類より優先する
  - static/deep/authority resultはcollector時点のproject support判定を保存し、projectionでnetworkやtrust graph更新を行わない
  - HTML extract、links、URLsへUTF-8/JSON byte budgetと件数上限を適用
  - relation/evaluationは取得結果全体から先に計算し、保存payloadだけを縮約
- bounded investigation history保存
  - 新規runのinput/output/evaluation/bundle JSONはheader付きzlib BLOBで保存し、共通readerが旧TEXTと圧縮BLOBを復元する
  - Investigation viewer、replay、fixture export、LLM/repository evaluation export、storage cleanupは共通reader経由で圧縮済みrunを扱う
  - decode failureはrun IDと列名付きで停止する。圧縮済み4列へのSQLite JSON functionや`LIKE`による直接監査は対象外
  - record/failure本文は`bundle_json`へ一度だけ保存
  - `collector_runs`はsource/operation別のstatus/count projectionで、本文列は新規runでは空配列
  - GitHub API snapshotを使用fieldへ縮約し、同一repoの一run内再収集をしない
  - first-party serviceから直接リンクされたcandidate repoは相互trust確認だけを先に収集し、confirm後に通常repo contentを適用する。未confirm候補はproject nameや紹介文の材料にしない
  - service pageのrepoリンクは、`Source`等の明示的なsource表示だけを決定的な逆向きtrust evidenceにする。表示が曖昧なリンクは、既に当該projectのconfirmed/candidate repoである場合だけ、通常triage内の`same_project / related / uncertain`判定へ渡す。`same_project`でもrepo側の現行逆リンクがなければ相互trustは成立しない
- 既存保存payloadのmaintenance
  - `cleanup-storage-payloads`はdry-run既定で、project current、run snapshot、bundle、collector projectionを報告
  - `cleanup-workspace-temp`はdry-run既定で`.tmp`内の古いSQLite本体・WAL・SHMだけを列挙し、`--execute`時に削除する。全unit testは`.tmp`が5GiBを超えると警告する
  - `--execute`はrunを削除せず一transactionで縮約し、commit前にforeign key/integrity checkを行う
  - `compress-investigation-history`はdry-runで全history JSONをdecode検査して圧縮後bytesを報告し、`--execute`では未圧縮rowだけを一transactionで変換する
  - multi-tenant suffixをWebまたは`audit-source-config --execute`で追加すると、同じapplication serviceがcurrent same-site判定を再評価する
  - 保存済みservice evidenceをcurrent stateへ再適用する時は、requested/final URLからsame-site判定を現行policyで再計算する。investigation runの履歴payloadは変更しない
  - `repair-db` dry-runはcurrent service projectionの変更件数とbefore/after例を報告する
  - scalar列の`service_status`だけが変わる場合も`repair-db --execute`の保存対象にする
  - collection syncはfull NSIDを既存projectと照合してから親namespace単位で集約し、bucket内の全NSIDをconfirmed `owned_nsid`として保存する。observationは活動統計だけを保持し、member一覧やseed roleは保存しない
  - `profile_json.app.links`は保存時にcanonical identifierとconfirmed resourceから毎回再構築し、既存の派生linkを入力として引き継がない
  - `backfill-derived-identifiers`はflat runtime viewが失ったroleを不足rowと誤認するため削除。normalized identifierは無変更roundtripを維持する
  - exact identifier lookupは`handle` / `did`を含めnormalized indexを使用
  - 2026-07-11に正本DBへ適用済み。payload 3,064,663,886 bytes削減、identifier 5,681行補完、same-site stale probe 29件修復。identifier件数は当時の作業記録であり現行commandの契約ではない
  - 2026-07-13にVACUUM済み。DB fileは約4.4 GiBから約1.5 GiBへ縮小し、`freelist_count=0` / `integrity_check=ok`を確認済み
  - 2026-07-13にactive `evidence.resource_url_records`から旧形式由来の補助URL 247件 / 174 projectsを削除済み。過去LLM入力snapshot内の同source名は履歴として残す
  - 2026-07-13に正本DBのsame-site stale probe 4件を現行policyで更新し、`app.links`から正本にない4,868 link / 449 projectsを削除。再監査0件、foreign key違反0、integrity `ok`を確認後、一度きりCLI commandを削除
  - 2026-07-13に474 projectの旧machine note 1,071行を`system_notes`へ移動。runtime project同値、normalized child・履歴不変、再監査0件、foreign key違反0、integrity `ok`を確認し、読込時のpattern変換とcollector旧projection fallbackを削除
  - 2026-07-26の正本DB read-only比較では、history JSON圧縮は2,269,114,597から374,451,025 bytesへ1,894,663,572 bytes（83.5%）削減する。canonical family単位retentionは30日で196,902,979 bytesに留まり、21日では1,040,007,334 bytesと月単位履歴を削除するため採用しなかった
  - 正本DB複製22,717 runs / 90,868 JSON valuesで圧縮前後の全列hash、Investigation list/detail、replay、fixture、LLM/repository evaluation exportの同値を確認。件数不変、foreign key違反0、integrity `ok`
  - 2026-07-26に正本DBの22,717 runs / 90,868 JSON valuesへ圧縮を適用済み。再dry-runは候補0件、decode failure 0件、replay差分0、fixture export成功、foreign key違反0、integrity `ok`。VACUUMは行わず、461,618 page（約1.76 GiB）を今後の書き込みで再利用する
  - 2026-07-28にoperator review済みのpath付きservice URL 10件を正本DBへ再適用し、exact URLのconfirmed `service` roleとactive `confirm_identifier` decisionを補完した。project数4,442は不変、foreign key違反0、integrity `ok`
- bounded LLM evidence
  - planning/triageへ渡すevidence JSONは整形後UTF-8 24 KiB上限
  - field別件数上限で先頭・末尾の代表を残し、縮約時は観測件数と採用件数をprompt内へ明示
  - triageにはboundedな現在の保存済みprofileも渡し、根拠が実質的に改善した場合だけ説明を更新するよう指示
  - categoryは主な利用者と利用形態で分類する。一般利用者が直接使うものは`project`、組み込む部品は`library`、projectの背後で動くものまたはATProto自体を扱う実行物は`infra_tool`とする。CLIやplugin等の形式だけでは決めない
  - `category=lexicon`は、第三者実装または相互運用を意図した共有schemaであることを文書等から確認できる場合に限定する。NSID、schema sample、record sample、namespace名だけでは`lexicon`へ進めない
  - `auth_only`はcategoryから外した。独立appまたはserviceのATProto利用がaccount認証だけの場合は`atproto_auth_only`として保存し、強いprotocol surfaceが無ければ`atproto_contribution_level=auth_only`へ投影する。認証実装自体を提供するlibrary、plugin、proxy、reference codeには付けない
  - 最終prompt bytesはrun metricsで別に観測
- fixed evidence LLM evaluation
  - 新しいmodelまたはversionを比較する手順と採用判断は[LLM model評価](developer/llm-model-evaluation.md)を正本とする
  - `export-llm-evaluation-corpus`は指定DBをread-onlyで開き、projectごとの最新保存runから固定triage evidenceをexportする
  - corpus選抜はprompt長bucketと保存済みcategoryを分け、既存`category=library`を既定で最低8件優先する。保存済み出力は比較referenceでありgold labelとは扱わない
  - corpusは保存済みprofileをbase evidenceと分離して保持する。`evaluate-llm-corpus --current-profile-mode without|with`で新規project相当と既存profile更新相当を同じcaseから実行し、reportの入力条件を分ける
  - 前回LLM出力だけを根拠とする`lexicon_scope`は通常promptとcorpusのbase evidenceから除き、外部またはheuristic根拠を持つscopeだけを維持する
  - `evaluate-llm-corpus`は同一prompt、最大出力token数、reasoning要求で1 model構成を反復実行し、JSON/schema、evidence外identifier、category-summary整合性、reference差分、latency、同一入力のfield差をJSON reportへ保存する
  - 現行Qwenで確認した追加規則は`prompts/model-rules/qwen-3.6-35b-a3b.txt`に置き、通常`enrich`と定期jobは現行Qwenのmodel IDで自動選択する。CLIのsuffix指定、環境変数、model IDによる既定値の順に優先し、別modelにはQwen用規則を適用しない
  - reasoning要求値とruntime log等で確認した`applied / ignored / not_checked`を分ける。endpoint metadataは要求値を返すだけなので適用確認には使わない
  - 期待categoryを人が確認したlabelはevidence hash一致と`used_for_prompt_adjustment`を必須にし、prompt調整に使ったcaseと使っていないcaseを別集計する
  - `compare-llm-evaluation-reports`は同じcorpusとcase/repeat集合のbaseline/candidateを比較し、機械検査の退行と文章確認・複製DB・少数project試用等の未実施項目を分けて返す。通常運用modelは自動選定しない
  - repository analysisはtriageと別corpusにし、保存済みrunのrevision固定materialをread-onlyでexportする。評価reportはschema、根拠位置、materialにないNSID、保存前の正規化結果、時間を記録し、二構成を同じcase/repeatで比較する
  - `evaluate-llm-corpus --repository-analysis-report`はcompleteなrepository解析reportのrepeat 0を同じproject keyの固定triage caseへ一時的に重ね、正本DBと元corpusを書き換えずに二段階処理を確認する
  - OpenAI Responsesの`output`にreasoningとmessageが併存する場合はmessageだけをmodel出力として解析し、Harmony control tokenを含むtextではfinal channelだけをJSON候補にする
  - 長時間実行では開始時と各request完了時までの結果をreportへ書き出し、正常終了時だけ`status=complete`へ確定する
  - reportのpeak memory、swap、prefill/decode内訳はfieldのみ定義済みで、自動計測は未接続
- repository analysis
  - confirmed repositoryはrevisionまたはmaterial hashと解析policy単位のrepository analysisをcurrent stateへ保存し、triageで再利用する。GitHub / Codebergはmetadata、README、tree、主要manifest、configuration、Lexicon、必要時だけ最大4 fileのsource抜粋を対象とし、Tangledはmetadata / README / 限定treeを対象とする。candidate repositoryはconfirm前に通常解析しない
  - repository analysisは単一artifact分類をせず、非排他的な観測述語、basis、根拠付きsummary、capability、case-preserving collection利用を保存する。READMEは元の行番号を保つ最大6 block・合計6,000文字に分け、選択数と切り詰め状態をcoverageへ保存する。LLMへ渡すのは本文を持つevidence blockとrepository descriptionだけで、name、homepage、is_template、内容のないtree pathはclaimの根拠にしない。観測はpredicate・basis・根拠を固定したcandidate ID、collection利用はreverse-domain authorityを確認したNSID・operation・根拠を固定したcandidate IDだけを選ばせ、保存前にlocatorへ戻す。未対応providerとcoverage不足も状態として残す
  - 解析失敗時は前回成功結果と最新試行を分離し、根拠付きの前回結果だけをstaleとしてtriageへ渡す。旧形式の根拠なしsummaryは渡さない。timeout、rate limit、HTTP系など一時的な外部失敗だけ同じmaterial / policyの再試行を24時間抑制し、LLM設定・互換性・response・local commandの修正後は即時再実行できる
  - repository observationからcategoryやidentifierを直接補正しない。categoryはproject-level triageで決め、NSID seedやLexicon JSONの存在だけでは`lexicon`にしない
  - repository analysis由来の明示的なdocumentation site URLは`docs` roleのcandidate resourceとして保存し、confirmedへ直接昇格しない
  - repository claimは根拠位置だけを直接持ち、重複を除いた根拠本文は`evidence_excerpts`へ最大6件、各600文字で保存する。triage投影は旧形式のclaim内excerptも同じ形へ縮約する
  - run metrics v2はrepository analysisのように複数回発生するLLM requestをpurpose別に集計し、planning / triageの個別requestを上限内で優先保持する
  - `enrich --llm-max-output-tokens`はplanning、repository analysis、triageの全requestへ同じ最大出力token数を渡す。省略時はendpoint既定値を使う
- 保存済みprofileの更新
  - 前回LLM評価後にsupported service、confirmedなdocs / blog / support、confirmed repositoryが増えた時、または確認済みwebsiteでloginやapp shell等の実利用面を新たに観測した時だけ、非空summaryと暫定taglineを最新提案へ更新する
  - 同じsourceでの言い換えと暫定でないtaglineは自動更新せず、内容別の専用review signalや保存fieldは追加しない
  - 2026-07-18の正本DB read-only監査では、現在保存されている前回evidenceとの差による遡及更新は0件
- replay regression corpus
  - 通常replay 6件とoperator correctionのblock/defer/confirm 3件をnetworkなしで評価
  - fixture exportはsanitizer v2を通し、commit前のmanual縮約を必須とする
  - manual gold setはidentity / service / repository判断、promotion判断、紹介済みpositive referenceを固定し、実project key・時刻・期限は保持しない
  - browser calibrationは描画後page要約5件を固定入力にし、実URLの現在状態とは分離する
  - browser laneは表示中の匿名menu展開、auth link追従、password form、handle/email form、OAuth入口、公開content itemを伴う作成action、描画後network/console errorを別evidenceにし、link到達や作成CTAだけではservice surfaceを確定しない
- promotion queueの独立signal
  - `project` / `infra_tool`のservice surface確認済みと要validationを別signalにし、後者を約100点下に表示。`unverified`でも確認済みwebsite/serviceを持つprojectは一覧から除外せず、必ず要validationに留める。`library`はdocs URL等をserviceと誤認しないよう対象外
  - atproto寄与度とtechnical insightはservice利用可否とは別に保持・表示する
  - confirmed owned NSID、同じNSIDのschema sample、authority domainと一致するDNS Lexicon resolution、publisher DID上のexact schema recordが揃うprojectを`lexicon_publisher_confirmed`として優先する。repository内schemaやcandidate NSIDだけではこのsignalを出さない
  - candidate visibility、service evidence、manual priority、score breakdownを分けて返す。`defer_promotion`は期限付き非表示、`lower_promotion_priority`は任意理由付きで通常候補より後ろへ送る
  - Webから根拠確認、優先度変更、解除ができ、manual gold evaluatorは通常queue生成処理のpolicyと相対順位をnetworkなしで検証する
  - read-onlyのpromotion outcome分析は、紹介済みprojectから紹介済み除外だけを外して現行signalを再計算し、category別捕捉率とsignalなしの理由をJSONで報告する。未紹介projectを負例として扱わない
  - 2026-07-20の正本DB read-only監査では177候補。`infra_tool`接続で未紹介候補が9件増え、service確認済み4、要validation 5。紹介済み`infra_tool`は46件中23件をsignalで捕捉
  - category promptのread-only sampleでは、未紹介`library` 16件を`library` 8、`project` 2、`lexicon` 5、`personal_tool` 1へ分類した。一方、既存profileを持つplugin / template / prototypeではsummaryとcategoryの不整合が残り、正本DBの一括再分類は実施していない
- LLM transport failure境界
  - command/endpointは共通の1 attempt timeoutを使用
  - endpoint互換性エラーだけ別payload/endpointを試し、timeout・transport・429/5xx・invalid responseは再送しない

利用手順は[利用ガイド](user/guide.md)、CLIの公開面は[CLIガイド](user/cli.md)、Webの公開面は
[Web UIガイド](user/web-ui.md)、保存契約は[project-record.md](developer/project-record.md)、調査契約は
[investigation-model.md](developer/investigation-model.md)、background operation契約は
[background-jobs.md](developer/background-jobs.md)を正本とします。

## 既知の制約

未接続:

- sync/reviewのcheckpoint再開
- server再起動後のprocess job eventとjob一覧の復元
  - enrichの未完了checkpoint再発見は接続済み
- CLIなど別processを含むwriter排他
- 任意の将来schemaへ順次上げる汎用incremental migration runner
  - version 2から3はreview済みmanifestを使う新規複製DBの明示materializationに限定
- DB-level trust/data policy marker
  - `registry_meta`はschema versionしか持たない
  - 正本DBが単一のため、直近の実装優先から外した
  - 将来必要になった場合は、audit合格後のadditive updateとして後付けする
- Tangled repoの完全なtree取得。現状は公開HTMLとREADME raw取得に限定
- OAuth `include:` permission setの再帰解決

現行設計の非目標:

- raw HTMLやREADME全文のWeb表示
- job eventのSQLite永続化
- collector request途中の強制停止
- 旧DB schema、旧Python import API、旧Web payload、EvidenceBundle v1 readerの互換維持

安全上の制約:

- identifierのconfirm/block/defer/split decisionは現行保存契約どおり`kind + value`全体へ作用し、role単位ではない
- NSID factual roleは`owned_nsid`へ統一し、classificationでconfirmed/candidateを表す。`seed` / `manual_seed` / 観測・参照用NSID roleは正本DBから移行済みで、現行コードは生成・読込・移行を行わない
- candidate NSIDは、confirmed developer DIDとの相互trust pathに加え、operator/trust-policy confirmedなroot serviceの完全一致domainが`_lexicon` DNS経由で同じschema recordを公開する場合も自動confirmできる。path付き・未採用serviceやrepo内schema単独では昇格しない
- enrich後半でURLの相互trust pathが完成した場合も、全evidence適用後のreconciliationで同じrun中にaggregate identifierをconfirmする。同一hostのHTTPからconfirmed HTTPSへのredirect元はcandidateから除去する
- `sync-bluesky-lexicons`はBluesky公式repositoryの`app.bsky.*` / `chat.bsky.*` record schemaを一意な`https://bsky.app` projectへ追加する。`com.atproto.*`は対象外
- app links/resource roleは`webManifest`, `changelog`, `appStore`, `playStore`, `fdroid`, `distribution`を追加し、未知roleもopen unionとして保持する。Web Overviewは利用・インストール、project情報、補足情報、その他に分けて表示する
- bulk enrichのjob ETAは現在のworker processで時間を観測済みの件数と残件数から算出し、resume前の完了件数を混ぜない。その他のjobは直近の`current / total`を開始時からの平均速度へ線形外挿し、progress総数が無いoperationでは表示しない
- enrich run timingはproject SQL書込みまでで、transaction commit、process job全体、operator review時間を含まない

## 動作確認状況

2026-08-30時点で確認済み:

- 全unit test 729件
- JavaScript syntax check、Web API test、主要なlist/detail/job導線の実browser確認
- Semble / Tangled / anonymous browser laneを保存済みsampleで評価し、Sembleを維持、TangledをJetstream v2
  snapshotへ置換、anonymous browser verificationをWeb project detailへ接続した。Jetstreamは公式SDKで限定sequenceの
  plan/download/decodeを確認した。初回1,000 event dry-runは53秒、既存一致28、新規作成予定79、relevance除外893で、
  正本DBは不変だった。初回executeでowner DID単独の照合が同一ownerの別repositoryを集約する問題を確認したため、
  repository URLの一意一致だけに限定した。
  修正後に同じ範囲を再dry-runすると、現在の正本DBに対して既存一致69、新規作成予定38だった。
  初回runを巻き戻したtemp DBで修正版を再適用すると既存一致15、新規作成92、project純増92となり、
  foreign key violation 0、integrity check `ok`、一projectへの異なるrepo URL集約0を確認した。
  同じ補正をbackup作成後の正本DBへ適用し、project 4,772、watermark `6055342`、同じ検証結果を確認した。
  Webは一時schema version 3 DBで保存済みURL選択と確認モーダルを確認した。詳細は
  [外部sourceとanonymous browser laneの評価](notes/source-lane-evaluation-2026-08-30.md)を参照
- investigation replay benchmark 9 case、semantic regression 0件
- GitHub、Semble、Bluesky introduced、collection inventory、Tangledの
  [bounded real-source smoke](notes/registry-maintenance-real-source-smoke-2026-07-11.md)
- 正本DB maintenance後のforeign key違反0、integrity `ok`
- 2026-07-13の正本DB複製でsame-site 4件と`app.links` 4,868件を適用後、再監査0件、normalized roundtrip差分0、履歴table不変、foreign key違反0、integrity `ok`
- 3,239 projectから作ったhandle/DIDを含む12,849 exact lookupで、従来判定との差分0
- 正本DBでKlearsky、Postgame、radio4000の重複project 3組をmergeし、confirmed canonical identifierのcollision 0件、foreign key違反0、integrity `ok`を確認
- 2026-07-16に正本DBへcurrent service projection 421件を適用。そのうちscalar-only 151件は保存判定修正後に反映し、projection再dry-run 0件、`projects=3,278` / `investigation_runs=20,472` / `collector_runs=116,967` / `project_decisions=178`の前後一致、foreign key違反0、integrity `ok`を確認
- 同じ`repair-db` dry-runの`pruned_project_count=103`はcandidate normalized rowのlossless保持による別残差で、current service projection未適用件数ではない
- 2026-07-17に正本DBの`primary_nsid`を`owned_nsid` / `input_nsid`へ、normalized `website` roleを`service`へ統合。644 project / 703旧role rowを処理し、再dry-run 0件、旧role 0件、foreign key違反0、integrity `ok`を確認
- 2026-07-28に正本DBへidentifier role移行を適用。4,440 projectのlegacy role 20,575件を整理し、NSID case 445値を現行collection inventory表記へ補正した。再dry-run変更0件、旧role 0件、foreign key違反0、integrity `ok`を確認し、一時case補正実装は適用後に削除した
- Website、service URL、補助pageのruntime責務を分離した。通常の正規化・候補採用・service redirectはWebsiteを補完せず、Websiteだけではidentifier、Trust起点、service稼働、service collisionにならない。明示的な`add-seed`だけはWebsiteが空なら同じservice URLを初期値にする
- identifier採用理由、旧identifier、non-production、確定transition、noiseを分離するschema version 3と通常mutationを実装した。review済み108 claimを適用した4,418 projectの正本DBへ2026-08-14に切り替え、High Five / ATProtoFansとMalfestio / Writerの相互分離判断、無変更save差分0、採用理由欠落0、current/non-current重複0、foreign key違反0、integrity `ok`、全629 test成功を確認した。結果は[Identifier保存方式のshadow比較](notes/identifier-storage-shadow-comparison.md)を参照
- schema version 3切替後にoperatorが一通りの典型操作を確認し、定期sources syncとenrichも完了した。2026-08-16の`sync-introduced`は1ページから3 post・24 entryを処理し、10 project更新・9 project追加、watermark更新まで完了している。表示上の`1 / 100`は処理件数ではなく取得page数と安全上限を並べたもので、完了jobの進捗表示としては誤解を招くが処理停止ではない
- schema version 3の正本DBで信頼・マージshadow比較を再実行した。信頼比較はrole付き登録起点10,210件を曖昧性0件で読み、固定12事例と二探索方式の同値性を確認した。マージ比較は3,492 pair中110件を人向け推奨、20件を別扱い維持とし、固定19事例を確認した。どちらもproductionの自動採用・自動マージは未接続で、全632 testに成功した。結果は[信頼ポリシーshadow比較](notes/trust-policy-shadow-comparison.md)と[マージ規則shadow比較](notes/project-merge-policy-shadow-comparison.md)を参照
- service/repository collectorの方向付き観測、repoのproject帰属、repo→URL直接参照、target roleを分けるread-only shadow comparatorを追加した。review後、GitHub homepageをconfirmed `repository_homepage`として保存し、service候補への静的投影と信頼edgeを分離するproduction codeを実装した。homepage単独では信頼edgeを作らず、confirmed service→repo relationとexact URLが一致する時だけ`repository_homepage_pair`を再構成する。GitHub Searchの`homepage_exact`も、collectorの観測・候補照合と共通信頼policyを分離し、一意なconfirmed serviceと逆向きのconfirmed `source_repository`が揃う場合だけ`trust_policy` adoptionとして既存projectへ取り込む。一方向、candidate / non-current、複数一致はsuggestionに留め、任意のprojectを選ばない。2026-08-21に正本DBの旧424 rowを420 claimへ移行し、旧edge 785本を除去して相互参照edge 80本を77 projectで再構成した。foreign key違反0、integrity `ok`。通常経路の一時移行処理は削除し、過去runのreplayだけに旧EvidenceBundle互換を隔離した。Website自動更新とrelation assessor全体は未実施。結果は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照
- 既存project-level triageに混在する`repository_link_assessments`をread-only監査した。現在proposal 194組は、静的policyまたは直接相互relationで処理できる63、service構成要素roleがなく対象外の85、bounded repo probeが必要な30、現在materialで専用評価可能な16に分かれた。現行`same_project` 177組のうち73組はcurrent service roleがなく、45組は保存入力にbounded target materialがない。project contextを入力せず、一つの方向付きrelationと限定根拠だけを評価するshadow assessor、3 verdictの保存前検査、12件の設計fixtureを追加した。Qwenによる固定corpusではdirect assertion方式が安定したが、実16組では3支持・11根拠不足・2不支持となり、LICENSE linkの誤支持も再現した。reviewでrelationの意味をsourceに限定しない公式`project_repository`へ広げ、docs、仕様、Lexicon、issue、release、licenseを含めること、service以外のproject構成要素からも採用することを合意した。serviceと一般URLを混ぜず、代表documentation、specification、promotion、landing pageを構成要素として扱う`site_url` / `project_site`も独立追加した。shadowのbounded HTML extractorは表示、子icon label、最寄り見出し・block、landmark、repository密度を保持する。正本DB監査では旧`source_repository` relation 9,219行のうちpage→repoは634行だけで、README mention 8,220行とTangled DID→repo 365行が混在していた。productionでは旧relationを一括改名せず除去し、page relationはbounded再収集から再生成する。初期段階ではLLM自動昇格を接続しない。結果は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照
- `project_site`導入用のread-only comparatorとreview済みshadow materializerを追加した。正本DBのWebsite 612件は、confirmed service再利用596、candidate service 1、non-current service 5、別project component衝突1、独立site候補9に分かれた。16件をoperator reviewし、project site 9、service confirm 1、別projectの誤ったservice除去1、non-currentを維持したWebsite解除5に確定した。production codeは`site_url` / `project_site`の保存、runtime view、merge、Web/API表示に対応し、2026-08-25に正本DBへ適用した。適用後はcurrent Website 607件がconfirmed service再利用598またはconfirmed project site 9となり、active site adoption 9、site/service exact重複0、foreign key違反0、integrity `ok`。切替前DBは`data/backups/project-site-20260825T011520+0900/`へ退避した。Website空欄へのrepository homepage 227 URL / 206 projectは初期移行へ含めない。結果は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照
- repository identifier role、relation predicate、runtime view、Web/API表示を`project_repository` / repositoryへ揃え、2026-08-25に正本DBへ適用した。case違いの同一repository 9組10行を統合し、repository identifierはconfirmed 1,236件、candidate 225件の計1,461件になった。意味が混在していた旧`source_repository` relation 9,219行は一括改名せず除去し、現在値を表すdecision metadata 1,219件、profile app link 1,170件、contribution evidenceのrelation cache 1,054件も正規化した。履歴上のLLM入出力snapshotは書き換えていない。旧role・predicate・現在値cacheの残存0、foreign key違反0、integrity `ok`、全702 test成功を確認した。切替前DBは`data/backups/project-repository-20260825T022442+0900/survey.db`へ退避した。旧page→repoから抽出したcanonical 499 claimのbounded再収集は未実施で、初期段階ではLLM自動昇格を接続しない。結果は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照
- 移行前backupの旧page→repo 499 claimを現在の273公開pageからbounded再収集するread-only comparatorを追加した。リンク表示だけでなく取得pageのproject帰属を分け、confirmed component URLそのものからの直接linkだけを自動確定対象にする。component外のTangled共通pageやcomponent配下の商品pageが別projectのrepoを指す実例を確認したため、component配下またはcomponent外のpageは強い表示でもreviewへ留める。2026-08-26のsnapshotはcanonical confirmed relation 46、review 333、除外9、現在未観測104、取得失敗7で、confirmed relationは既存confirmed repository 26、candidate 5、未登録15を支持した。15件のoperator reviewでは外部実装一覧にあるskytip-simpleを除外し、14件を採用した。外部project一覧の文脈をlink内の自己ホスト表示より優先する一般則と、review済み14件をrelation・identifier・operator adoptionへ投影するmaterializerを追加し、2026-08-27に正本DBへ適用した。relation、identifier、decision、operator adoption各14件、project数不変、foreign key違反0、integrity `ok`、Web API表示を確認した。その後、review 334件をpage帰属322件とexact component上のrelation assessment 12件へ分離するread-only corpus exporterを追加した。広義`project_repository`の固定caseはQwenで12/12一致し、実12件×3回では安定支持10、根拠不足1、判定揺れ1だった。`Open source`、`View source`、単独`Source`、GitHub・Tangled・Codeberg・GitLabの`Repository`表示をprovider-neutralなshadow静的規則へ追加し、provider名だけ、`Fair source`、mirrorは自動支持しない。LLM自動昇格は未接続。結果は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照
- page→repositoryのbounded静的policyをproduction collectorへ接続した。serviceとproject siteは同じpolicyを使い、exact current componentから静的に支持できるlinkだけをconfirmed `project_repository` relationにし、repository identifierはcandidateへ投影する。Tangledを含むprovider-neutralな明示表示、無標単独link、外部一覧・dependency・funding、component外pageを固定testで確認した。旧anchor text・owner・project名一致によるrepository candidate化とconfirmは停止し、auto-confirmはrepository側の逆向きtrust pathを引き続き要求する。LLM relation assessorは未接続、正本DBの一括再収集・書換えも未実施。通常enrichは2,897件、失敗・missing 0件で完了し、832 relation、207 projectを確認した。直接relationから投影したcandidateを最終cleanupが除去する不整合と、依存先の公式Lexicon参照を当該project repositoryとする誤判定を修正し、master rebase後の全720 test成功を確認した。対象3 projectを通常enrichで補正し、bounded relationはconfirmed 39、candidate 793、identifier投影漏れ0、foreign key違反0、integrity `ok`。Web serviceはmasterの新コードでHTTP 200を確認した
- project state/save境界を`project_record.py`へ分離し、旧`registry.py` symbolは再輸出していない。全423 testと、正本DBのread-only hydrate 100 project / exact lookup 577件のSQL照合を確認
- 旧category契約でのLLM評価では、未調整17件のcategory一致がQwen 8/17、Gemma 11/17、複製DB 6件の保存categoryは5件同一だった。category契約を見直したため、この一致数とgoatの旧判定は今回のmodel選定へ直接使っていない。速度、JSON/schema、identifier、文章比較の観測だけを履歴として残した
- 新category契約、`atproto_auth_only`の保存、LLM出力検査、旧`auth_only`categoryの再保存防止、元profile有無を分ける評価入力を実装し、全492 testを確認。正本DBの旧`category=auth_only` 35件は`infra_tool` 17、`library` 9、`project` 3、`name_only` 6へ移行し、旧category 0件、integrity `ok`、foreign key違反0を確認
- 新category契約で確認した旧56 labelは全件をprompt調整用とし、重複しない21件を調整後確認用として固定済み
- 未使用21件の元profileなし実測は、QwenとGemmaがともにcategory 17/21。全検査はQwen 17/21、Gemma 21/21、根拠外identifierはQwen 2件、Gemma 0件。元profileありではcategoryがQwen 16/21、Gemma 17/21だった。この段階ではGemmaを残したが、後続のrepository解析反復を含む最終判断ではQwen維持とした
- 問題が出た未使用case中心の8件×2回では、category変化は両model 0件。identifier集合／検査成否の変化はQwen 2件／3件、Gemma 0件／0件。手動footprintはQwen 20 GB、Gemma 16 GBだったが、runnerによる同一workload memory計測は未接続
- 元profileなし未使用8件のmodel名を伏せた文章比較は、Gemma 3件、Qwen 2件、同等3件。文章品質では一方の明確な優位を確認していない
- 調整用5 repositoryの現行解析policyでは、request成功は両model 5/5、根拠位置不備は両model 0件。全claimが保存規則を通ったresultはGemma 3/5、Qwen 0/5で、materialにないcollectionはGemma 0件、Qwen 1件。解析結果を重ねた後段triageも両model 5/5でschema有効、根拠外identifier 0件。調整用caseのため未知repositoryへの一般化成績には使わない
- 固定評価と通常`enrich`の最大出力条件を揃える入口を追加し、planning、repository analysis、triageへの伝播を確認
- 最新DB複製の`atproto.dart`で一般の`/docs/`文書がdeployment設定になる誤抽出を確認して修正。`goat`では重複excerptでrepository結果が20,732文字となり後段24 KiB上限を超えたため、保存とtriage投影を縮約した。既存の実結果5件はtriage投影1,930〜3,536文字に収まった
- 現行repository analysis policyの固定`goat` material 10回は、GemmaがJSON成功5・失敗5、Qwenが10件すべて成功。Gemmaの失敗は末尾切断ではなく応答内部のJSON構文違反。一方、保存規則をすべて通る結果は両model 0件で、Qwenは全件に根拠不足の観測、6/10件にmaterial外collectionがあった。利用不能な本文は保存せず、byte数、hash、JSON error位置等をrun metricsと固定評価reportへ残す。Gemmaは現構成の通常処理候補から外し、既定modelはQwenを維持するが、repository解析内容は別途改善対象。正本DBは未変更。全541 testを通過
- repository analysis policy v12で、LLM入力を本文付きevidence blockとclosed candidateへ変更した。固定`goat`のv11反復は9/10有効、候補外collection 0、claim棄却0で、残る1件は不正JSON。調整用5件はv11で3/5、空候補を明示したv12再確認で残り2/2が有効。変更判断に使っていない5件はv11で5/5有効だった。本文の根拠番号混入、操作の自由生成、空候補時のcandidate補完を個別に検出して契約へ反映した。正本DBは未変更。全734 testを通過
- 固定fixtureへ追加しないhash抽出12 projectのv12確認は機械検査12/12有効だったが、意味確認で言語修飾名とhostnameのcollection候補、nameだけからの要約、package配布repoの役割、既存fixture Lexiconを確認した。policy v14ではclaim可能なmetadataをdescriptionだけにし、README候補へ単一のreverse-domain authority条件を追加した。最新1,146 projectの保存materialでは旧候補208種のうち30種を除外し、確認した除外値は言語修飾名、hostname、handle、file名、property pathだった。reverse-domain条件を入れたv13で問題2 projectを再実行すると誤collectionは消え、正常な`app.bsky.feed.post`候補は残った。package配布repoとfixture Lexiconは未解決。全736 testを通過

未検証:

- 長時間運用時の実API retry/cache/budget値

この節は網羅的なテスト履歴ではなく、現在の信頼境界を示すsnapshotです。個々の過去の
実行結果はGit履歴と関連notesに残します。
