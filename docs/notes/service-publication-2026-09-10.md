# Service publication evaluation — 2026-09-10

作業・検証記録。恒久仕様は[investigation model](../developer/investigation-model.md)。

## 結論と適用範囲

candidateは公開先候補の記述だけで追加し、取得成功・逆参照・表示確認を要求しない。
自動confirmは現在のprojectの公開先宣言と公開先側の対応repo参照、種類に応じた実在確認を組み合わせる。
運営者を確定したことにはせず、`operates_service`を生成しない。repoのhomepage指定だけでserviceを再採用する
旧trust経路は復活させない。変更はworktreeだけで、正本DBや実行中enrichには適用していない。

## 実装

- repository analysis v16に`declares_service_url`を追加。本文中の正確なURLだけをclosed candidateとして提示する。
  `current_project`と`uncertain`を候補へ反映し、否定・仮定・第三者・旧公開先・欠落は反映しない。
  最新成功解析の`current_project`だけを自動confirmの宣言根拠にする。
- service candidateの既存正規化は一般URLをrootへ丸めるため、公開先宣言に対応するURLはexact pathを保持する。
  temp DBのsave・load・prune・replayでもpathを維持する。
- 通常enrichはrepo解析後に新しい宣言URLを取得し、既に同runで取得済みなら再利用する。
  role-v4は既存project siteでもservice条件を再評価し、既存のsite採用は撤回しない。
- Webは初回到達pageの表示済みauth/public surfaceを確認する。API/PDSはorigin rootの
  `com.atproto.server.describeServer`への1 requestで応答形式とexact endpointを確認する。
  API rootのplain text `Code:` / `Source code:`行も対応repoへの逆参照として扱う。
  追加確認はWeb/protocol合計1 URL／enrich。失敗attemptも保存して他URLとの順序に反映する。
- 宣言だけの未採用URLも他projectとの競合検査に含める。replayは現在の採用repo、宣言のrevision・URL、
  applicability、guardを再確認し、新しい解析結果を古い保存結果で上書きしない。

## 意味上の検証

`tests/fixtures/project_registry/service-publication-v1.json`に保存済みREADMEの抜粋と構成した反例9件を固定した。
既定LLMで各2回、計18回の公開先候補抽出が期待値と一致した。デモ・Impro・パス付きAPI・SkyGiveは候補に入り、
forkの上流・第三者・否定・仮定・documentation専用は除外された。
これは小さな調整用corpusの結果で、一般精度やservice自動confirmの正解率ではない。
SkyGiveのREADMEは候補生成の根拠にだけ使い、公開pageの対応が未確認なので実serviceとしては確定していない。

補正済み18実recordを正本read-only snapshotからtemp DBへ移し、最新保存bundleを新コードでreplay・保存した。
対象service確定の再発0、Website変更0、別service集合の変更0。temp DBは終了時削除済み。
これは保存済み根拠の再評価であり、新しい外部取得を含む再enrichではない。

全856 unit test成功（`bin/project-registry-test`）。`git diff --check`も成功。

## 検証の限界

- Web/protocolの新条件、候補の保存、同runでの新規URL取得、fork逆参照、競合、replayはunit testで確認した。
- protocol実サーバーへの新規smokeは未実施。新たな追加enrichを保留する運用下で、固定応答による検証に限定した。
- describeServer非対応の汎用APIやColibri AppViewの独自APIは自動confirmできずcandidateに残る。
  HTTP 200や一般JSONを代替根拠にするfallbackは追加していない。
- 未取得の将来の他project宣言は競合検査できない。LLMによるfork／第三者の読み違いは一般的には残り得るため、
  公開先側の逆参照・guardを独立に要求する。


## 保存済み実データと限定Web確認による追加評価

保存済み7project／8repoのbounded materialを、そのまま既定LLMで評価した。短い固定例だけの評価とは分ける。
候補URLの提示前に特定語句を要求していたため、Colibriの`Visit us at ...`等が対象外になっていた。
語句制限を撤去し、URLの提示と公開先であることの意味判定を分離した。変更前後で同じ7projectを比較した。
また`clean_readme_text`がMarkdownの`<https://…>`をHTMLとして削除することを再現し、URLを保持するよう修正した。
Colibriの保存本文には`See the docs at . Use via .`という欠落があるが、元の記法までは保存データから断定していない。
失われたURLを既存homepage等から推測して本文へ補う処理は追加していない。

|対象|変更後の公開先抽出と保存データでの判断|
|---|---|
|svelte-atproto-client-oauth|パス付きデモURLを抽出。既存root候補に対しexact path候補を追加。逆参照あり、表示観測待ち|
|Impro|既存候補URLを公開先宣言として抽出。保存HTMLから逆参照を確認できず保留|
|SkyGive|既存候補URLを公開先宣言として抽出。保存HTMLから逆参照を確認できず保留|
|Colibri Social Documentation|colibri.socialの公開先宣言を抽出（変更前は0）。既存serviceを再利用。API URLは本文欠落のため宣言として抽出しない|
|altq.net / r4atproto / PDSls|今回の保存本文から公開先宣言を抽出せず。既存の候補を宣言なしで確定しない|

変更後の公開先宣言は4project、URL重複をまとめると4 URL。うちColibriは既存confirmed service、残り3 URLはcandidate。
正本read-only snapshotからtemp DBへ移し、候補追加・prune・save/load・replayで3候補を維持した。
新規confirm 0、Website・既存service集合は全件不変、foreign key違反0、integrity ok。temp DBは終了時削除した。
ImproやSkyGiveの保存HTMLは切り詰められており、この段階の逆参照未確認を実ページでの不存在とは扱わない。

追加した日本語の公開先と単なる依存先リンクを各2回評価し、4/4で期待値に一致した。
fixtureの初期9例×2回とは別の追加確認であり、fixtureは現在11例。
実データ比較中にJSON不正応答が1回発生した。評価スクリプトの出力上限を2000から4096へ広げ、未完了の対象から
再試行して完了した。原因を出力上限と断定せず、通常処理のretryや既定設定は変更していない。

### 公開2 URLでの実動作確認

保存データだけでは表示状態を検証できなかったため、OAuthデモとImproに限定して通常の静的取得・role判定を実行した。
必要条件が揃ったデモだけ通常browser laneへ進んだ。追加enrich runや正本DB保存は行っていない。

- OAuthデモ: 公開先宣言、対応repoへの逆参照、初回pageの表示済みsurfaceが揃い、`CONFIRM_SERVICE`。
  実browser failureなし。exact pathのままtemp DBへ保存し、実観測bundleのreplayでも確定を維持した。
- Impro: 今回の取得本文全体から対応repoへの逆参照を確認できず、`REVIEW_ATTRIBUTION`。browserは未実行。
  temp DBの保存とreplayでも非確定を維持。Webアプリの実在を否定する結果ではない。
- 両recordでWebsite不変、foreign key違反0、integrity ok。temp DBは終了時削除。

全858 unit testとdiff check成功。実運用checkoutと正本DBは未変更。
この評価でWebの実際の確定経路を1例確認したが、汎用API・AppViewの対応範囲は拡張していない。

### APIの確認範囲とURL境界

パス付き公開先へ`/xrpc/...`を継ぎ足す実装は誤りだった。
[XRPC仕様](https://atproto.com/specs/xrpc)ではendpointはorigin直下に置く。
確認対象をorigin root URLに限定し、パス・query・fragment・userinfo付きURLではrequestしない。
候補URLは削除もoriginへの置換もしない。保存済み観測の適用時も対象URLとendpointの対応を再確認し、
旧形式のパス付き成功観測からの確定を防ぐ。temp DB保存後の候補維持も確認した。

Colibriの[現在のREADME](https://github.com/colibri-social/appview/blob/main/README.md)を再取得し、
API公開先`https://api.colibri.social`がMarkdown autolinkで明記されていることを確認した。
前回の保存本文にはこのURLがなかった。現在のREADMEを使うLLM再解析は今回未実施。
[公開仕様](https://colibri.social/docs/specification/appview)を読み、2 endpointを各1回GETした結果:

- `com.atproto.server.describeServer`: HTTP 404。現行checkerは非確定。
- `social.colibri.server.describeServer`: HTTP 200、同じURLへ到達。
  `software=colibri-appview`、`flavor=vanilla`、`version=0.2.10`で、仕様記載と対応。

これは確認方式の未対応を示す実例であり、APIの不存在を示さない。
Colibri固有のNSID・software名の分岐は通常heuristicへ追加していない。
汎用化する場合は、採用repoと対応する公開仕様からquery endpoint、引数、期待応答の契約を扱い、
既存の公開先宣言・逆参照・衝突検査と組み合わせる必要がある。XRPCは匿名可否の統一的な列挙手段がなく、
一般JSON・認証エラー・APIという自己紹介だけを成功へ昇格させる案は採らない。
この契約の取得・検証は別の機能拡張として残す。API候補追加そのものは現在のまま有効。

取得記録は`.tmp/appview-api-sources.json`と`.tmp/appview-api-smoke.json`。
公開仕様・README各1 URLとAPIの2 GETだけで、正本DB・実行中enrichは変更していない。
全860 unit testと`git diff --check`成功。全test時の一時領域容量警告なし。


## 2026-09-10 最終反映・完了

`7c5a1c2`までを正本checkoutへfast-forwardし、全865 unit test成功。
木曜enrich `enrich-20260909T224730Z-44374263`を承認に従ってキャンセルし、Web停止・writer不在確認後に実行した。
直前backupは`data/backups/final-publication-rollout-20260910T0225Z/`。
最新planはcurrent_relation 8,092、reconstructable_judgment 187、needs_review 3,206。
全11,485本を`data/migration-archives/repository-trust-20260910T0225Z/archive.json`へ退避し、
relation復元0、再計画残件0。raw edgeとreviewed planの完全一致、全4,722 projectの判定用graph、
project_relations / project_identifiers / project_identifier_adoptions / project_decisions /
investigation_runs / collector_runsのhash不変、foreign key違反0、integrity `ok`を確認した。
同directoryにplan・result・verification JSONを保存している。以前の複製DBとの件数差はenrich進行後のsnapshot差。

Webを反映済みコードで再起動し、health成功、project一覧・詳細と定期設定APIのHTTP 200、job一覧とqueueが空で
あることを確認。定期設定を維持し、キャンセルrunは再開していない。公開先判定は以後の通常enrichで利用する。
既存全件の新policy再解析や、新たな本番enrichでの自動採用確認は行っていない。意味上の動作確認は上記の
実公開先評価とtemp DB保存・replay、通常pipelineのunit testに基づく。

今回の目的は採用済みrepo→公開先候補→根拠の揃うserviceの自動採用までで完了。
汎用API・AppView独自API、primary project_site自動採用、広いpolicy/LLM接続は未完了の別課題としてroadmapに残す。
