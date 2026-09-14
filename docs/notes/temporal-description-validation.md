# 説明更新とservice観測の品質レビュー

現行Qwenの説明更新は、人が根拠と文面を確認して採用・訂正する用途に限定するのが妥当です。固定素材の実LLM出力では、根拠IDが正しくても終了・機能変更の時制と文面全体の整合が崩れました。編集保護と保存は成立しています。service観測は取得・保存できましたが、loginの検出漏れと、週20件では674 URLを短期間で一巡できない処理能力の制約があります。

2026-09-15のhuman-reviewで、ユーザーは評価結果を了承し、このタスクを調査・評価までで区切って報告する意向を示しました。これは無人適用の品質受理や改善実装の承認ではありません。以下に再開に必要な素材・差分・判断待ちを残します。現行契約は[Project評価](../developer/specs/project-evaluation.md#保存済みprofileの更新)、[観測仕様](../developer/specs/investigation.md#browser観測)が正本です。roadmap更新は母艦で行います。

## 実行条件と確認境界

- コード: 開始HEAD `766da15fd304750f51537fa682945c08697d38b5`、branch `codex/temporal-description-validation`。本評価時はproductionコード・promptを変更していません。
- 正本読取り: `data/survey.db`、schema v4、SQLite URI `mode=ro` + `PRAGMA query_only=ON` + 明示read transaction。絶対pathで成功しました。過去の接続失敗原因は未確定ですが、現在の監査の阻害要因ではありません。正本の複製は作っていません。
- 書込み: このworktreeの `.tmp/temporal-validation/llm-application.db` と `services.db`。後者は正本の6件から説明・追跡・service観測fieldを選んで通常codecで作った試験DBで、正本の完全snapshotではありません。
- 実行process: このworktreeのコードを通常checkoutの共用 `.venv` Pythonから実行。Web server/workerの起動、bind/portの新設、本番reload/restartはしていません。本番serverと今後起動するworkerが同じコード時点であることも本評価では保証しません。
- 日時: 正本profile監査は2026-09-14 23:35 JST、実LLMは2026-09-15 00:09〜00:11 JST、公開URL観測は同日00:11〜00:12 JST。現在値と観測時点を区別します。
- 認証値は既存設定からprocess内だけで読み、入力・reportへ記録していません。公開URLへ認証入力・登録・投稿をしていません。正本由来のprofileはLLMへ送っていません。

## 実LLMの固定素材と評価

modelは既存設定の `qwen/qwen3.6-35b-a3b`、LM Studio API報告のpublisher `qwen`、MLX 4bitです。ローカルappのversionは `0.4.23+1`。接続は設定済みLAN endpointのport 1234、API styleは通常の `none` 指定時と同じResponses、temperature 0.2、reasoning要求は `none`、出力上限2,048 token、timeout 180秒です。APIのmodel最大contextは262,144ですが、実際のload時context・parallel・chat template・MLX runtime細版は未確認です。APIはreasoning適用を報告せず、usageのreasoning tokenは全件0でした。

`build_triage_prompt` → 現行Qwen suffix追加 → `run_llm_with_metrics` → `apply_llm_output` → 通常codecでsave/fetchを実行しました。collectorやrepository analysisを含むenrich全体の再実行ではありません。共通promptは `prompts/project-triage.md`、suffixは `prompts/model-rules/qwen-3.6-35b-a3b.txt`（hash `832e6fef543699438cc364220866d001a83466e3fd949c10b133e033de8a6490`）です。通常のevidence縮約を通し、本文を持つsourceは各1件、一時障害では0件。input全体は15,987〜16,550 bytes、API入力3,397〜3,552 tokenでした。

架空のReading Loomと予約domain `reading-loom.example`を使い、URL取得なしで公式pageを想定した英語素材を固定しました。旧profileは全case共通で、tagline「読書記録共有」、summary「読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。」、features「読書記録共有」「独自Lexiconによる書籍・読書メモ表現」「利用者のPDSへの保存」です。

| 素材 | 固定した事実・反例 | 期待する意味 |
| --- | --- | --- |
| 終了 | 2026-08-31にhosted読書journalを恒久終了。従来は独自 `org.readingloom.book` Lexiconで利用者PDSへ保存。定義は現在も公開。代替hosted serviceについては記述なし | 用途・技術を過去形で保持し、定義公開は現在の状態として示す。「記述なし」を「存在しない」にしない |
| 機能変更 | 2026-09-01以降は書籍検索だけ。新規journal記録と共有は終了。旧Lexicon定義は公開中。検索がそのLexiconを使うという記述なし | taglineを現機能へ合わせ、旧機能・旧設計と現在の検索を区別する |
| 元の誤記 | 開発者向けの書籍・読書メモLexicon定義のみ。hosted service・共有・検索・end-user appは一度も実装していないと公式訂正 | 誤った共有serviceを過去の実績にせず、定義の用途を説明する |
| 一時障害 | 最新static結果が503/upstream timeout。description sourceから除外され、他の機能根拠は空 | 元profileを維持し、終了・非公開に置換しない。変更種別は `insufficient_evidence` |

4素材を各2回、合計8生成で打ち切りました。固定LLM出力を返すfakeは使っていません。全8件がJSONで応答し、生成待ち合計84.511秒（初回27.218秒、以後6.065〜9.765秒）、入力計28,080／出力計4,260 tokenでした。各出力435〜576 tokenで上限到達はありません。反復ではcache利用があり、初回との差をmodel単体の速度差とは扱いません。model情報取得は認証なしでの拒否1回と認証付き成功1回を別に行っています。

### 文面のレビュー材料

下表は実出力と適用後の文面からの抜粋です。summaryだけでなくtagline・featuresも更新対象として評価しています。原文全文と入力は末尾の保持物から確認できます。

| 素材・反復 | 実出力と問題 | Agent評価 |
| --- | --- | --- |
| 終了 0 | summary冒頭「読書記録を共有するサービス」「PDSに保存する」の後に終了日を追記。さらに「置き換えとなるホストサービスは存在しない」。featuresは共有・表現・保存の現在形 | 不適合。時制混在と根拠外の不在断定。`changed_facts`とsource IDは正しい |
| 終了 1 | 「利用者のAT Protocol PDSに保存する読書ジャーナルサービス。2026年8月31日をもって…永久に終了した」。featuresは共有・表現・保存のまま。現在も定義公開中という説明はsummaryから欠落 | 不適合。用途・技術は保持したが過去／現在の区別が不足 |
| 機能変更 0 | tagline「読書記録共有」。summary冒頭は「共有するサービス」「PDSに保存する」のまま末尾に検索専用化。featuresのPDS保存にだけ「旧機能」を付記 | 不適合。現機能と旧機能が文面全体で整合しない |
| 機能変更 1 | summary「元は独自Lexiconで読書記録をPDSに保存するサービスだったが、現在は書籍検索のみを提供。読書記録機能は終了し、Lexicon定義は開発者に公開されている。」、featuresは検索／定義公開へ変更。taglineは「読書記録共有」のまま | summaryは改善、profile全体は不適合。taglineが終了した機能を示す |
| 誤記 0 | tagline「読書メモ用Lexicon定義」。summaryは書籍・読書メモのPDS record定義を説明し、hosted service／共有は提供せず開発者向けとする。featuresも定義と保存形式へ置換 | このsampleでは適合。`correction`、正しいsource ID。元の誤記を過去の事実にしない |
| 誤記 1 | tagline「読書メモ用Lexicon」。summaryは同じ用途を保持し「ホストされたエンドユーザーサービスや共有機能は提供していない」 | このsampleでは適合。反復0と同じ訂正方向 |
| 一時障害 0 | 旧profileをそのまま出力。種別は誤って `initial`、理由は根拠不足・変更なし、sourcesは空 | 文面保持は成立。変更種別は不適合 |
| 一時障害 1 | 旧profile維持、`insufficient_evidence`、sources空 | このsampleでは適合 |

終了・機能変更・誤記の6出力はいずれも正しいsource IDと変更理由を持ちました。全8出力をautomatic／来歴不明／手編集済み／保存hashと文面不一致の4条件、計32条件に適用しました。automaticでは上記の問題を含む提案文が実際に保存され、他の3条件では元profileを保持し、変更を支持した6出力について更新案を保持しました。一時障害の2出力では説明を維持しました。全32条件でtracking stateは維持され、通常codecでの保存・読み戻しまで確認しています。人による更新案採用の再確認は今回行わず、既存のWeb確認範囲と区別します。

一時障害の2出力はcategoryを `name_only` としました。今回の固定入力には旧profile以外の役割根拠がないため、説明の維持と分類の維持は同一ではありません。この結果だけで、通常enrichの分類劣化率や保存済み根拠を持つprojectでの挙動までは評価していません。

### 説明更新の判断待ち

終了・機能変更を含む無人の説明更新の品質は未受理で、既存profileの保護と人による採用・訂正を維持します。このタスクでは改善実装へ進まず、既存の共通promptにある時間経過・訂正の規則を次の内容へ明確化する案を再開候補として残します。project名やURL固有の例外は提案しません。

1. 変更後のtagline・summaryの全文章・featuresを一緒に点検し、終了した用途は過去形または旧機能と明示する。終了文を末尾へ足すだけで現在形の旧説明を残さない。現機能がある場合はtaglineもそれに合わせる。
2. 根拠に「記述なし」とあることを不存在の証明にしない。現在の機能へ過去の技術実装を移して断定せず、必要な技術的価値は旧機能の説明として保持する。
3. 非空profileに十分なdescription sourceがない場合は `initial` を選ばず `insufficient_evidence` とする。

改善する場合は今回の4素材を調整用とし、異なる用途の未使用素材（部分終了、機能追加、明示的な誤記訂正、一時障害）をprompt固定後の確認用にします。再評価予算案は調整用4×2 + 確認用4×1の最大12生成、各2,048 token／180秒です。元promptとの意味上の正例・反例の比較、適用・保存、対象testと全unit testを確認します。規則追加だけで品質が改善したと扱いません。**このprompt変更と追加呼出しは未承認・未実施です。**

## 既存profileの適用範囲

正本4,811件すべてにsummaryとLLM実行記録があり、taglineは4,809件、featuresは4,383件で非空でした。`editorial.mode`／`profile_hash`／`last_update`／proposalはいずれも全件未記録です。現行の `editorial.automatic` では全件が保護対象でした。現在の3説明fieldと最新LLM出力が完全一致するのは1,871件でしたが、残り2,940件の不一致も人による編集の証明ではありません。過去の正規化・更新処理等があり得るため、どちらからも作者や編集時刻を推定していません。

sampleは `project_key` 昇順で6 tracking stateから各2件、計12件を読みました。無作為sampleでも全件の正確性監査でもありません。

| Sample | 保持・注意する具体的内容 |
| --- | --- |
| Trailcast (`proj:000bc4aef27f`) | 旅行・イベント記録という用途と、独自Lexicon／利用者PDSという技術的説明。公開pageの文面とは照合できるが、全実装を検証したことにはしない |
| Firehose Network (`proj:00640cc2ac16`) | 複数地域のrelayという役割。Web上の案内を確認してもprotocol稼働確認を代替しない |
| SpaceHost (`proj:0629779c6c2c`) | summaryはPDS hostingの現在形、featuresは「サービス終了のお知らせ」「アーカイブ情報の公開」。本文と特徴一覧の役割・時制が混ざる候補。正しい終了時期や元の作者は未確認 |
| osprey-atproto (`proj:61fd840868b8`) | Ospreyとの統合・UDF／ルールという技術的価値とarchive情報。featuresに統合の重複あり。archiveを価値消失と同一視しない |
| filae.site、Aurora Climbers Guide Wall、publicdomainrelay.com、travel.music.happy | schema・record観測から用途や実装をどこまで述べているかが個別レビュー点。現在の説明文だけを新しい根拠として扱わない |
| atproto（Qt/C++）、atrrr、Bluesky Comments、$ATA Protocol | 言語・組込み先・URLへのコメント付与・atproto対象外という区別を保持する。今回外部仕様の真偽確認はしていない |

**判断待ち:** 全件を自動反映へ切り替えず、通常調査が生成した案を人が確認する範囲で進める提案です。最初に個別見直しするproject、正本への適用は未決・未承認です。

## Service観測と頻度の判断

正本dry-runでは674 URL（active 394、stable 253、stalled 17、out_of_scope 10）が期限到来でした。全件でbrowser試行日時が未記録です。archived／ignoreの各1 URLは選択対象外。通常選択処理は約0.24秒でした。これは674 URLを取得した時間ではありません。

通常の先頭20候補から、stable／active／stalledと用途を分けて6 URLを選定しました。公開Web UI向けだけに選び直してはいません。Playwright 1.61.0の通常Chromium navigation／分類を使い、event listenerを追加してrequest数と完了responseのbody bytesを計測しました。responseや時刻をfakeにしていません。上限は6 URL、同一siteのlogin/signup follow-upを含む最大18 navigation、各navigationの既定timeout 30秒です。既存socket fallback時は最大36試行になり得ますが、今回はfallbackもfollow-upも0でした。全subresourceのrequest数・転送量・process全体のwall timeの厳密な上限ではありません。

| URL | 秒 | request数 | request失敗 | 完了body bytes | 観測の意味 |
| --- | ---: | ---: | ---: | ---: | --- |
| [Trailcast](https://trailcast.shino3.net) | 3.32 | 37 | 0 | 745,755 | 日本語loginボタンとhandle入力の説明が見えるが、formとして抽出されず、現行英語語彙のoauth判定にも該当しない |
| [Firehose Network](https://firehose.network) | 3.60 | 12 | 1 | 149,573 | relay一覧と案内を取得。browserの投稿/login検出だけでprotocol serviceを確認できない |
| [RankThat](https://rankthat.net) | 3.21 | 4 | 0 | 335,144 | demo/mockである旨の公開説明を取得。buttonのLoginはlink follow-upの対象外 |
| [FreeMix](https://freemix.fm) | 2.91 | 12 | 3 | 161,920 | beta申込み案内を取得。試験DBの旧 `beta` は最新の未確認観測により `unverified`。終了とは判定しない |
| [Crate](https://app.crate.social) | 4.13 | 12 | 0 | 354,834 | `/login`へredirect。表示されたhandle formのplaceholderが `alice.bsky.social` で、現行のemail/username/handle等の入力判定が拾わない |
| [Cartes](https://cartes.app) | 11.61 | 172 | 0 | 2,852,677 | 地図・地域名を取得。現行の投稿/login条件に合わず、閲覧型利用面は確認扱いにならない |

合計28.79秒、6 navigation、249 request、完了body 4,599,903 bytes。collector失敗は0/6 URL、subresourceのrequest失敗は4/249、console/network errorのいずれかを持つpageは3/6でした。byte数は完了requestのresponse bodyで、header・失敗途中の転送を含む総通信量ではありません。subresource失敗をURLの終了や非公開へ昇格していません。

全6件を試験DBへ保存し、通常monitorの結果はcompleted、remaining dueは0でした。これは6件だけの試験DBの結果で、正本674件のbacklog解消を意味しません。説明3field・tracking state・`last_enriched_at`は保存前後で一致し、LLMは呼んでいません。全6件が `surface_confirmed=false` で、利用成功を保証していません。初回browser記録であり、前回からの内容差・利用面変化とreview負担の実測にはなっていません。

**判断案:**

- 週20 URLは取得負荷を抑えた巡回には使えても、674 URLを月内に確認する目的には不足します。未記録対象だけで34回必要です。30日は再選択を抑える間隔であり鮮度保証ではありません。
- 全674 URLを概ね30日周期で追う意図なら、少なくとも日平均約23 URLの能力が必要です。候補は毎日25 URL（30日で750 URL分）。今回の平均からの単純外挿は1回約120秒／約19 MBですが、失敗・重いsite・review負担を含む保証値ではありません。対象を限定して週20を維持する案もあり、人が判断します。
- loginや閲覧面の検出を広げる場合は合意が必要です。日本語／form外入力／handle例の正例と、検索欄・newsletter・単なる案内buttonの反例で評価し、特定site名の例外は作りません。今回の6 URLだけを成功扱いへ変える修正は提案しません。
- 長期失敗率、timeoutが多いbatch、再観測時の差分noise、人のreview件数・所要時間、実schedule連続運転は未検証です。有効化する場合は、観測のみの試用範囲と再評価日を先に決めます。

## 保持物・残件・再開条件

再開材料は母艦checkoutの `.tmp/temporal-validation-20260915/` へ一時保全し、元の子worktreeの `.tmp/temporal-validation/` と全ファイルの一致を確認しました。`audit.json`、`llm-inputs.json`、`llm-<case>-<repeat>.json`（入力evidence・実出力・model情報・各適用結果）、`model-metadata.json`、`services-report.json`（取得素材・run ID・計測）、実行スクリプトを含みます。一時的な再開資料で、productionコードでも自動採用済みfixtureでもありません。commitには含めません。所有者は母艦で、prompt改善または監視方針の再検討時に必要な素材を確認し、恒久fixtureへの採用または破棄を判断します。試験DB2個とWAL/SHMはcleanup済みで、正本複製は作成していません。

残件は、(1)共通prompt改善と追加12生成を再開するか、(2)監視対象・頻度・検出範囲、(3)実観測の恒久保持／破棄と再評価条件です。実験を不採用にする場合も生素材の処遇を人が判断します。監視有効化・設定変更・正本への書込み・全件再調査は未承認です。

文書のみの変更であり、code／prompt／UI変更を対象とするunit全件・API・JS・実browser UI受入は今回実施していません。実LLM・公開URL・通常保存codecという上記経路を確認しています。品質改善へ進む場合は対象testと全unit testを実行し、UIも変更する場合に該当UI受入を追加します。
