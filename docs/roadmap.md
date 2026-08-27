# Project Registry Roadmap

現在の実装状況は[status.md](status.md)、確定済みの保存形や責務分担は
[project-record.md](developer/project-record.md)、[registry-internals.md](developer/registry-internals.md)、
[investigation-model.md](developer/investigation-model.md)に移しました。

この文書は、まだ本体仕様に取り込んでいない展望と TODO を置くためのメモです。

## 目的の優先順位

1. 深掘りする価値がある候補を、背景と根拠とともに提示できる
2. 追加調査と短い紹介文の出発点になる核データを保てる
3. 運用上必要な review / merge / evidence を保てる
4. 公開手段の第一候補である app record export に、紹介用の情報を再利用できる状態を保つ

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon 完全互換を今すぐ満たすこと
- 不確かな app metadata で `app` を埋めること
- export の都合だけで core や ops の設計を歪めること

紹介フローを主導に置き、その延長で app export 可能性を確保する、という順序を維持します。

## 実施順の依存関係

- LLM model comparisonは、実装済みの固定evidence harnessでmodel-only screeningを行ってから、prompt tuningとfull enrich比較へ進む
- app record export は公開手段の第一候補だが、紹介用profileとconfirmed identifierのvalidation reportを先に作れる状態に留める

## 条件付き短期候補

### 開発文書の用語整理

- 機能削減の判断と削除が一段落した後、`docs/developer/`を読み直し、未定義の独自語、対象が曖昧な`run` / `review`、古くなった説明を整理する
- 全面翻訳や一括置換は行わず、コード・DB・enumと対応する用語は維持する。利用者向け語彙との境界は[編集方針メモ](notes/user-facing-terminology.md)を参照する

### 保存量と履歴

- DBのallocated bytesが10 GiBを超えるか、current DB maintenanceとの競合が実測された場合にhistory DB分離を検討する
- 最大payload runでrelation normalizeが二乗的に長時間化する経路を、storage cleanupとは分けて改善する

### 外部sourceとbrowser lane

Semble / Tangled source laneとanonymous browser verificationは即時削除せず、週次または通常運用4回の
固有成果を確認してから判断します。Web execute syncは`job-schedules.json`のsourceごと直近4回の
bounded observationを使い、browser verificationは保存済みinvestigation runのinput/output snapshotを使います。
汎用job履歴や追加DB tableは作らず、次だけを記録します。

- 他sourceでは得られなかった新規project候補数
- operatorが深掘り対象にした件数
- browser verificationによってservice statusやURL採否が変わった件数
- 実行時間、failure、manual correction数

anonymous browser verificationは定期jobに含まれないため、同じ観測期間中にstatic / deep結果が曖昧なserviceを
sampleとして明示実行します。browser runが0件なら削除判断の材料にはせず、観測期間を開始した扱いにしません。

評価はSemble / Tangledの各laneで4回の発火結果と、review可能なcompleted resultを少なくとも1件ずつ得た後に
行います。failure / cancelも信頼性と運用費の実績には数えますが、固有成果を判定するcompleted resultの代わりには
しません。anonymous browser verificationは前述のsampleを少なくとも1件確認してから評価します。

固有成果があれば維持します。固有成果がなく、人力での代替費も小さい場合は、laneとsource固有の
config / parser / test / 文書をまとめて削除します。sourceを削除する場合は、最後のwatermark / config /
historyを保持するかも同時に決めます。Tangled timeline / community / bot adapterは、具体的な有効化予定が
なければ、通常接続せずparserも削除します。

#### 観測終了時の片付け

`job-schedules.json`の`source_observations`は上記評価のための一時的な仕込みであり、恒久的なjob履歴契約には
しません。laneを維持する判断でも、評価結果を確定する作業と同じトピックで次を片付け、後続の機能拡張へ
持ち越しません。

- `project_registry_web.jobs`の観測用constant / helper / job完了hookとAPIの`source_observations` fieldを削除する
- 観測専用testと、現行機能として観測導線を記述した文書を削除または更新する
- 実運用の`job-schedules.json`から`source_observations`だけを除去し、scheduleとpending queueを維持したまま再読込を確認する
- 評価結果とlaneの維持・削除判断だけをroadmapまたはstatusへ残し、観測payloadや作業用一覧は正本文書へ残さない

laneを削除する場合は、これに加えて前述のsource固有config / parser / test / 文書とwatermark / historyの
扱いを同じ作業で確定します。Tangledの結果にproject keyを含める契約はlane本体の出力なので、laneを維持する間は
観測用仕込みの撤去対象に含めません。

### 内部構造

#### DID exact lookupのcandidate hydration除去

現在のDID exact lookupはnormalized indexで候補を絞った後、候補projectだけをhydrateし、current payloadに存在しない
normalized-only candidate DIDを除外します。全project scanではありませんが、normalized tableだけで判定できない暫定境界です。

次を満たした時にcandidate hydrationを削除します。

- normalized-only `developer_did`を、有効な独立identifierとして扱うかstale rowとして除去するかを保存契約で決める
- current payloadから表現できない有効rowを失わないrepairまたは表現を用意する
- 既知の5 DID collisionを含む実DB auditで、index-only判定と期待projectの差分が0になる
- `find_project_key_by_identifier(..., "did", ...)`がproject hydrateを呼ばない回帰testを追加する

#### queue policyの再設計と分割

現行queueには紹介準備やmanual reviewのsignalが混在しています。briefで主成果物を「深掘りする価値の判断」と
定めたため、次にqueue policyを変更する時は、atprotoの利用深度、serviceの利用可否、技術的新規性を独立して
確認できるsignalと並び順を先に決めます。その契約とsample reviewを用意してからqueue builderを分離し、file移動
だけを先行させません。

### 信頼・帰属の次期設計

判断原則、責務境界、接続マトリクス、relation別の静的用途は[登録プロジェクトへの帰属と信頼](developer/project-attribution-and-trust.md)を正本とします。identifierの採用理由、former、non-production、確定transition、noiseを分離するschema version 3は正本DBへ適用済みです。現行の`trust-graph-v1`は稼働していますが、正本文書のrole付き静的policy全体がproductionへ接続済みという意味ではありません。

実データ比較は[信頼ポリシーshadow比較](notes/trust-policy-shadow-comparison.md)、collector relationの比較は[collector relation policyのshadow比較](notes/collector-relation-policy-shadow-comparison.md)を参照します。残る作業は次のとおりです。

1. 直接関係の正本と信頼経路の導出を揃える
   - `project_relations`を由来付きの直接事実の正本とし、旧trust graph edge、検索用inverse、再生成可能な判定経路の保存分担を確定する
   - collectorが観測したlink、collector固有解析、構成要素間関係判定、semantic relationを混同せず、一般link、support、sponsor、任意README URL、mentionを強いrelationへ昇格しない
   - `site_url`を含む仮称predicate、account関係、Lexicon authority委譲とschema publicationの分解は、固定caseと正本DB sampleで効果を確認してから保存名とproduction接続を決める
   - accepted repositoryからReadTheDocsなどの代表project siteへ明示的にlinkしている場合、任意README URLとは分けて`project_repository`やproject site帰属へ利用できる条件を決める
2. role付き信頼判定規則をproductionへ接続する範囲を決める
   - candidate生成、信頼判定結果、identifier採用、block/defer、手動判断を別責務として維持する
   - `eligible_for_auto_confirm`を個々のcollector判断として増やさず、predicate、方向、role、source/methodを中央の静的policyへ照合する
   - candidate、former、non-production、graph-only node、導出identifier、採用理由を追跡できない旧identifierを、それだけでtrusted anchorにしない
   - `did` / `handle` / `domain`の信頼評価結果を通常enrich導線で新しいconfirmed accountへ保存する場合は、project、developer、organization、publisherのroleを推測しない入力契約を先に定める
   - 自動採用を切り替える場合は、既知case、既存confirm、追加review、経路長、rollback境界を再確認し、candidate admissionと同時に無条件で広げない
3. collectorとsyncの境界を継続して監査する
   - 同じidentifierの観測更新、新規projectの登録起点、既存projectへの新identifier追加、弱いsuggestionを分ける
   - collectionは一意な直属namespace割当なら既存projectへの追加も登録起点にできるが、同じnamespace内の複数collectionを独立した複数根拠にしない
   - account role、publisher、provider owner、authority domainなど、構成要素ではないactor・中継点をroleなしでtrusted anchorへ昇格しない

次の領域は信頼・帰属の判断モデルを参照しますが、信頼判定結果をそのまま流用せず、独立したpolicyとproduction変更として扱います。

- merge policy
  - repo共有、developer/publisher一致、root domain共有、first creator、信頼グラフのpathをそのまま自動merge根拠にしない
  - confirmedな構成要素、独立surface、共有・排他性、operator判断を使う別の比較として設計する
  - 現行監査と規則案は[登録プロジェクトのマージ規則設計メモ](notes/project-merge-policy-design.md)を参照する
- project間の時間・派生関係
  - 後継、買収、分割、統合、ownership移転、mirror、instance、tenant、forkを、同一project内のidentifier transitionへ押し込まない
  - Basker→InfoEnon、DateSky→Nomareなど既知事例をreviewしてから保存形を決める。fork等の現時点の方針は[派生関係の設計メモ](notes/derivation-relations.md)を参照する
- node・roleの拡張
  - developer node、provider account、native appのbundle/package/store identifier、record以外を含む汎用NSIDは、既存DID・handle・relationでは表現できない実例が確認された場合だけ追加する

この一連の完了条件は、新policyが省ける手動確認と新たに必要になるreviewを既知事例で説明でき、固定caseと正本DB sample reviewを通り、現行の自動confirmから切り替える範囲とrollback境界が明示されていることです。

### データpolicyとmigration

#### DB-level policy marker

DB-level migration/data policy markerは直近優先から外します。

- 現状は`schema_version=3`だけで、同じschema内のdata policy世代差はDB単体では識別できない
- ただし正本DBが基本1つで、巻き戻しや複数policy世代DBの並行運用も想定していないため、現時点の実利は小さい
- 将来、複数DB、再migration、同じschema内でのdata policy世代分岐が必要になった場合に再検討する
- 実装する場合は、open時の自動付与ではなく、markerなし既存DBのread-only audit合格後にだけadditiveに付与する

process間lockまたはpreflightは、次の破壊的maintenanceを追加する時に再評価します。現状の個人ローカル運用ではoffline手順を維持します。

## 条件付き長期候補

現状の運用を妨げてはいませんが、具体的な不便や保守上の問題が出た場合に検討します。

- relation / ownership graph
  - 興味はあるが直近優先ではない
  - まずはviewだけ。write actionは付けない
- fuzzy search
  - 通常検索で不足が明確になった場合に追加する
  - 単なるtypo toleranceではなく、実際のproject探索に役立つ検索条件を先に定める
- cache / source budget visibility
  - 表示だけなら調査時に便利
  - Webからbudgetを直接編集するのは別判断
- keyboard shortcut
  - 操作系は入れない
  - 詳細の前/次切り替え、detail tab jump程度に留める
- static JS / CSS / route table分割
  - 編集衝突が増えた時、または責務を追いにくくなった時点で分割する
- app record / app lexicon export
  - 公開手段の第一候補とするが、実装時期やapp lexiconとの完全互換はまだ確定しない
  - 紹介用profileとconfirmed identifierからread-only export reportを作れる状態を将来候補として残す
  - exportのために不確かな`profile_json.app`を埋めず、まず不足fieldと根拠不足をvalidation reportとして出す
- security / safety整理
  - LAN、他利用者なし、必要に応じてTailscale越しという公開範囲は維持する
  - その前提で必要十分な対策に留める
- job event履歴永続化
  - checkpoint discoveryだけでは実行履歴を追えない場合に追加する
  - 保存量とretention policyを先に決める
- v2以降のincremental schema migration runner
  - 次のschema改版が具体化した時点で、backup/transaction/validation契約と一緒に実装する
- service verificationの後半強化
  - 認証後の利用可能性や複数surface統合が実際の誤判定原因になった時に、signalと匿名browser laneを拡張する
- editorial qualityの洗い替え
  - 保存済みprofileと前回以降に追加された強いsourceの比較、summaryと暫定taglineの更新は通常enrichへ取り込み済み
  - featuresや広い文章品質の問題が継続的に観測された場合、またはsummaryのoperator編集を追加する場合に、来歴の保持や専用編集導線を検討する
  - 手動field編集とは別に、project単位の自然言語による訂正指示と現在のprofile・evidenceをLLMへ渡し、name / tagline / summary / featuresの更新案を作るmodeを追加する
  - 更新案は保存前に差分previewし、採用時は訂正指示と生成元を来歴として残す。同種の個別訂正を通常triage promptの恒久ruleへ増やさない
- syncの追加tuning
  - collection全件取得、GitHub broad query、screen-unmatched精度が実測のrequest量・誤候補で問題になったsourceだけを対象にする
- typed aggregate / 用途別view
  - normalized tableの直接queryを増やした後もflat runtime viewが主要な性能・保守問題として残る場合に再検討する

## 長期構想

直近の運用安定化には含めません。必要性や具体的な利用場面が見えたものから再検討します。

- evidence / ownership graphの本格表示
- Investigation notebook mode
- Assisted review
- LLM triage side panel
- multi-project compare
- review session mode
- bulk maintenance dashboard
- source health dashboard
- plugin-style collector configuration

## 調査 pipeline の残件

次の優先候補だけを roadmap として残します。接続済み範囲と恒久的な契約は
[investigation-model.md](developer/investigation-model.md) を正本とします。

- OAuth `include:` permission setを再帰解決し、循環と取得失敗をsource状態へ残す
- Tangled timeline/community search/bot adapterを必要性評価後にopt-in接続する
- UFOs rolling windowのgap検知後に、別sourceで補完する運用を決める
- anonymous browser verificationの固有成果を確認できた場合だけ、実Playwright smoke testと複数surface統合を追加する
- repo contents、PDS、Bluesky sessionを含む全collectorへbudget/cache policyを統一する
- investigation viewerのbounded evidenceで不足する具体例を収集する
- 固定evidence harnessで上位modelの反復、文章、重い入力、memoryを比較する
  - corpus export、1 model構成のstructured report、同一入力の複数回差分、2 reportの機械比較は実装済み。peak memory / swap / prefill / decodeのrunner観測は未接続
  - 過去のgpt-oss GGUF実行は`low`が無視されたため再利用しない。現行gpt-oss MLXはruntime APIで`low`対応と既定値を確認済み
  - category契約を見直した旧56 labelは調整用、別の21件は未使用caseとして固定・実測済み。結果を見た後はsuffixを変更していない
  - 元profileなし／ありの生出力と、複製DBで保存規則を適用した結果は実測済み。既存profile本文は8/8件で両modelとも維持されたため、Gemmaによる文章改善は少数project試用で別に確認する
  - confirmed repositoryのREADME、manifest、configuration、Lexicon、必要時のsource抜粋をbounded materialとして解析し、結果をtriageへ渡す経路は実装済み。根拠位置の初回比較を受け、利用可能なlocatorの明示、summaryの根拠必須化、内容のないtree pathのLLM根拠からの除外も実装済み。修正後の調整用5件ではGemmaが保存規則3/5、Qwenが0/5で、変更後triageも両model 5/5成功・根拠外identifier 0件。未知repositoryへの一般化は少数試用で確認する
  - 少数選別では通常処理候補をQwenとGemmaに絞った。Agents A1はidle時の連続6 requestに成功しfootprint 9.7 GBだったが、Qwen/Gemmaより遅く、自動切替でTTL中のQwenを退避させた。自動load時のcontext・parallelを小さくでき、明示loadなしでQwenと同居できるruntime構成が得られる場合だけbackground候補として再検討する
  - QwenとGemmaの反復、model名を伏せた文章比較、重い5件、複製DBの8件比較は実測済み。project-level triageではGemmaが少数試用の条件を満たしたが、追加されたrepository analysisは固定`goat` material 10回でGemmaがJSON成功5・失敗5、Qwenが10/10成功だった。保存規則をすべて通る結果は両model 0件で、Qwenも全件に根拠不足の観測、6/10件にmaterial外collectionがあった。Gemmaは現構成の通常処理候補から外し、model、runtime、またはpromptの変更でこの入力を安定して通った時だけ未調整repositoryを加えて再評価する。正本試用へは進めずQwenを維持するが、Qwenのrepository解析内容も改善対象とする
  - 手動footprintはQwen 20 GB、Gemma 16 GB、Agents A1 9.7 GB。Agents A1の23時台の失敗は定期jobとの競合で、idle時には再現しなかった
  - model別promptは許容するが、別model用suffixの結果だけで足切りしない。suffixの差分維持が複雑になる場合は運用上の不利として扱う
  - 既定modelはQwen維持と決定した。既存`category=library`の再分類はmodel変更から切り離し、現行category契約とoperator確認に基づく別作業として扱う

未決定:

- 複数projectを跨ぐ永続circuit breaker

帰属と信頼の判断モデルは[登録プロジェクトへの帰属と信頼](developer/project-attribution-and-trust.md)、trust graphの現行実装は[registry-internals.md](developer/registry-internals.md)を正本とします。設計途中の詳細は次の作業用文書へ分離しています。

- ownership / identity: [identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- service verification: [service-verification.md](notes/service-verification.md)
- replay / fixture corpus: [evidence-replay.md](notes/evidence-replay.md)
- collector failure policy: [collector-reliability.md](notes/collector-reliability.md)
- LLM model評価手順: [llm-model-evaluation.md](developer/llm-model-evaluation.md)
- Qwen / Gemma比較結果: [llm-model-evaluation.md](notes/llm-model-evaluation.md)

## ステータス系

現行の canonical は `service_status` です。

将来的には、次の別軸を分けたくなる可能性があります。

- `paused`
- `unmaintained`

候補:

- `service_status`
  - 到達性、公開状態、招待制、死活
  - `unreleased` まではこの軸に含める
- `app.status`
  - app record 向けの公開・保守状態

現時点では分けません。必要になった時点で `app.status` を追加します。

## app metadata の拡張

`app` は今は低優先度です。確かな情報だけを入れ、空 object を許します。

将来、次を追加で整備する余地があります。

- `images[]`
- `langs[]`
- `lexicons.produces[]`
- `lexicons.consumes[]`
- `account_indicators[]`
- owned collectionの用途を示すtest/prototype qualifier
- collectionを定義する側と利用する側を分けるproduce/consume表現

`links[]`はopen unionとして保存し、現行UIは次のroleを認識します。

- `website`
- `webApp`
- `sourceCode`
- `docs`
- `support`
- `status`
- `privacyPolicy`
- `termsOfService`
- `webManifest`
- `changelog`
- `appStore`
- `playStore`
- `fdroid`
- `distribution`

実例リンク、開発意図・背景、リリース発表などは有用ですが、追加経路と重要度が未確定です。将来roleを増やす場合も、アプリ本体や配布先と同じ優先度には置きません。

## 境界項目の再評価

現時点では evidence として扱っているが、将来 app metadata に昇格するかもしれない項目です。
ただし今は、紹介判断や要約補助に使う材料としての意味が強く、app 側へ急いで寄せる必要はありません。

- `inspired_by`
- `features`
- `developer_accounts`

`review-out-of-scope` の derivative 判定は、Bluesky従属utilityとatproto技術ツールを
十分に分離できていません。現状は候補提示だけに使い、自動実行しません。将来は
`technical_interest_level`、owned surface、独自データモデルの有無を加味して、
紹介スコープ外と技術調査対象を分離します。

## service URL と補助ページの分類

現行の補助ページ判定は、confirmed service URLへの過剰昇格を防ぐための暫定heuristicです。
path名の名指しだけでは信頼性が低いため、将来的には次の材料を組み合わせて分類します。

- top pageからのリンク関係、anchor text、rel/canonical、redirect/canonical URL
- page title、meta description、本文のdocs/login/support/statusらしさ
- service root、app shell、auth entry、docs/support/status pageの役割分離
- 手動seed、operator decision、過去probe結果との整合
- Webページを持たないアプリをservice URL必須にせず表す契約と、ブラウザ拡張ストアなど未対応の配布先

非目標として、pathがあるURLを一律に落とすことや、`/about`などの名前だけで恒久的に分類を確定することは避けます。

## `com.atproto.*` ownership

Bluesky公式repositoryはAppView実装も含むため、repository URLだけを`com.atproto.*` collectionの所有project判定に使いません。現行`sync-bluesky-lexicons`は`app.bsky.*` / `chat.bsky.*`だけを対象にします。`com.atproto.*`を補完する場合は、protocol specification、schema配布主体、実装repositoryの関係を別途定義してから専用sourceとして追加します。

## atproto寄与度の独立評価

現行の level、score、evidence と rule-based v1 は [registry-internals.md](developer/registry-internals.md) を正本とします。
未接続の候補は次です。

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

## Web UI 詳細検索

Web UIのnormalized検索は接続済みです。検索拡張候補は次です。

- SQLite FTS table の追加
- typo tolerance / edit distance
- 自然語・概念検索
  - 例: 「ブログサービス」で blogging / publishing / CMS / article posting 系projectを探す
  - 単純な部分一致ではなく、project category / feature tag / summary evidence の整備とquery expansionを前提にする
- OR group や否定条件を含む query builder
- 保存済み検索条件
- URL query への filter 状態の永続化
- offset / cursor pagination
- raw JSON 任意パス検索

## Web UI: 次の接続候補

未接続 / 将来候補:

- 利用を圧迫しないHelp導線
  - `docs/user/web-ui.md`を利用者向け正本とし、画面内へ長い説明を重複させない
  - 独立したHelp画面では日常フロー、基本用語、DB書き込み・外部接続・dry-run、アクセス境界を短く案内する
  - 外部情報の取り込みは、dry-runでも外部接続することを明示する
  - フィルターや破壊的操作の直近には、意味が紛らわしい箇所だけtooltipまたは短い補足を置く
  - CLI固有の低水準操作をWeb Helpへ網羅せず、必要な場合だけCLIガイドへ導く
- sync/reviewのcheckpoint再開
  - 現状はsource/candidate境界のprogressと停止まで
- server再起動後のprocess job一覧・event復元
  - enrich checkpointの再発見と再開は接続済み
  - job eventを永続化する場合は保存量とretention policyを先に決める
- evidence表示上限の調整
  - run一覧、bounded evidence、replay差分は接続済み
  - raw HTMLやREADME全文の表示は非目標を維持する
- role単位のidentifier decision
  - 現行confirm/block/defer/splitは`kind + value`単位で、同じ値を持つ全roleへ作用する
  - 実例でrole単位の拒否が必要になった時に、decision targetとruntime guardを拡張する
- Webからのprofile編集範囲拡大
  - 現状の通常編集は`name`、`introduced_url`、operator向け`notes`
  - 必要になったら`tagline`、`summary`、`website_url`、`profile_json.app.links`などをfield単位preview、revision check、根拠表示付きで追加する
  - identifier/relation/decisionの正本を直接JSON編集させず、既存review actionや専用mutationを優先する
- PWA/local app化の再評価
  - オフラインDB操作は非目標のまま、静的asset cache、standalone表示、ホーム画面起動、軽いreload耐性に実利があるかを見る
  - Firefox中心の運用ではinstallabilityの恩恵が限定的なので、manifest/service workerの小規模追加で済む場合だけ候補にする
  - build stepや複雑なcache invalidationが必要なら優先しない

原則 CLI に残す保守操作:

- `audit-source-config` の hosted suffix split 監査
  - 既存 project detail の単体 split とは別導線
  - tenant group と仮分割後 merge candidate を確認してから実行する
  - 複数 identifier を動かすため、通常のモバイル review 導線には出さない
- `audit-url-fields`
  - 原則 read-only report
- 判定feedback後の既存DB cleanup audit
  - confirmed service URLに補助ページやdefer/block配下URLが残っていないか確認する
  - repoとNSIDの両方がacceptedではない `defines_schema` relationと、それに連動したowned NSIDを棚卸しする
  - dry-runで影響projectとidentifier/relation差分を出し、backup後にrepairまたは個別cleanupを実行する
