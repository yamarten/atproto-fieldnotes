# 週次enrichのtriage再利用：運用効果の確認

最終triageの成功出力を再利用する変更は、利用者の受理と母艦への送信承認を経て主checkoutへ統合済み。検索計画と外部収集は毎回継続する。次に確認するのは実運用のhit/miss理由と短縮効果。統合時に正本DBへの書込み、jobの開始・変更、Web再起動は行っていない。

## 実装と採否

再利用条件の正本は[Investigation仕様](../developer/specs/investigation.md#triageの再利用)。本文・根拠・実行条件・失敗状態を照合し、観測の記録時刻4種類だけを除く。30日の期限と、手動判断・説明編集・category・保存出力の変更検出を持つ。新たな取得と通常の適用・最終評価は省略せず、生成時の入力と日時を保持する。LLM失敗は次回再試行し、同じ収集失敗が続く場合は再取得とエラー記録を続けながらtriageだけを再利用する。

検索計画の再利用は今回は不採用。当時の完全なplanning入力とactor/focusを含む計画全体は保存されておらず、最終triage入力から同一性を推定しない。同じtriage材料の1,175組でも、repo検索語は1,088組、post検索語は1,160組で変化し、そのうち今回の調査ログに採用結果があるものは14組だった。これは新規発見14件や言い換えの効果を証明する値ではない。既存候補の再採用や初期収集との関係を分離できず、検索語固定の同等性も保証できないため、現行の探索機会を保つ。deterministic seedへの常時置換、通信/推論の並列化、対象削減、更新頻度変更は実装していない。

## 読み取り調査と短縮見積り

2026-09-26に正本`data/survey.db`をSQLite `mode=ro`と共通履歴codecで読んだ。DBは67,639 run、5,758 projectの履歴を保持していた。WAL補助ファイル欠落時の最初の接続だけ昇格し、その後の集計は通常権限で実行した。DB内容は更新していない。

`application_status='applied'`を`completed_at DESC`で選んだ直近2,000 runについて、input snapshotに残る前回生成時のevidenceと、output snapshotの今回生成時のevidenceを比較した。現行promptへの再構成値を当時の完全promptとは扱っていない。

| 確認項目 | 結果 |
| --- | ---: |
| evidence完全一致 | 1,073 / 2,000（53.65%） |
| 指定した観測時刻だけを除いて一致 | 1,175 / 2,000（58.75%） |
| 前後の収集失敗・保存エラー、適用状態のguard、30日未満も満たす候補 | 1,025 / 2,000（51.25%） |
| 2,000 runの保存済み`total_ms`合計 | 70,529,277ms（約19時間35分） |
| そのうちtriage request時間 | 24,969,192ms（約6時間56分） |
| 1,025候補のtriage request時間 | 11,962,824ms（約3時間19分、totalの16.96%） |

最後の行は、実行条件が同じで再利用用記録が既にあると仮定した候補時間の合計であり、短縮実績ではない。この2,000組はすべてprompt版がv2→v5へ変わっており、実装の条件では再生成が必要。さらに4,000 run遡った比較では、比較可能3,748組のうち記録上の実行設定が同じものが2,367組あったが、全組で`description_sources`が追加され、入力は同一ではなかった。版名一致だけでは再利用できないことも確認した。

候補判定には直前runのoutput/bundle、今回のinput/outputに保存された失敗と手動判断を使った。保存済みrun全体の失敗と今回実装のtriage直前の失敗集合が完全に同じとは限らず、当時のtemplate全文・新しい照合値もないため、厳密な履歴hit率を復元した値ではない。stall整理後の週次対象全体や今後の材料変化率を代表せず、commit待ち・job全体の待ち時間も含まない。初回導入、prompt/model/推論契約更新、期限切れ時は削減できない。

同じ収集失敗も含めて除外する案では候補450件・8.76%に留まったが、同じ材料で前回と同じ保存エラーが続くものが720件あった。Lexicon DID未発見やrecord samplingのHTTP 400などが含まれる。これらの取得は毎回再試行し、材料・失敗の変化を照合してtriage再利用を決める方式を採用した。エラー本文の時刻などは推測で正規化せず、変化すればmissにする。

集計境界のrun IDは新しい順に`inv:abeadcd91b11448ba63dd906beecf62a`〜`inv:6a65ea4e9c0447be90a0047ebcdb88fd`。同順のrun ID listを空白なし・UTF-8のJSONへ直列化したSHA-256は`885d6df173ce5b9e5c309715ad266e05b553a8a06ae6beadd2a33ce4e613793e`。通常enrichの保存ではplanning入力が欠落するため、これらからplanningの完全再現はできない。

## 確認範囲

- 一時DBの通常enrich→save→fetchで、安定後はplanningと収集を続けてtriage呼出しだけが1→0になることを確認。履歴の実request件数と再利用記録を区別した。
- 名前・根拠本文・URL・record日付・識別子判断・説明編集・category・model/endpoint/API style/推論設定/出力上限/追加prompt/推論規則・期限を変更した時の再生成、観測日時だけの再取得、失敗への移行・同じ失敗の継続・復旧、LLM失敗・空/不完全出力、再利用無効設定・command方式を確認した。
- 実履歴から40件（project / personal_tool / infra_tool / lexicon / name_only）を選び、同じ保存出力と、観測日時だけを7日進めた同じ材料を通常`apply_llm_output`と`finalize_project_shape`へ通した。上記2,000件を新しい順に走査し、材料が一致する最初の10件、その後は観測日時だけに差のある組から合計40件まで採った。再利用出力と固定した新規応答の適用結果は、来歴情報を除く全project fieldで差分0。これは実LLMの新規生成との品質比較ではなく、同じ出力を再利用する適用契約の検証。
- 上記40件の入力fingerprint構築とlookupの実測は中央値0.312ms、最大0.501ms（初回policy hash構築後）。省略したLLM時間は履歴値からの見積りであり、共有LLMでの追加計測はしていない。
- `bin/project-registry-test`全1,057 test成功（最終実行16.153秒）、`git diff --check`成功。初回はworktreeのNode依存欠落でOAuth関連が失敗し、lock済み依存を`npm ci --offline --ignore-scripts --no-audit --no-fund`で配置して再検証した。外部取得契約・Web UIは変更していないため、実通信smokeやbrowser操作は追加していない。

観測日時だけの差分例は`inv:1633f21dc363417da7a1a06eac033e66`（Lexicon解決日時）、`inv:196197a64f10456693ce51d74629149a`（説明根拠の観測日時）、`inv:fa71fef079ee4fefa693631053466548`（両方）。個別LLM出力の正誤は判定せず、人手goldや新しい調整用事例にはしていない。事例保存手順を確認し、今回の入力同一性・適用比較については追加のLLM case登録を行わなかった。

## 運用で残る確認

母艦は`01a050d2-495b-7fc0-85f5-3fb214fd1c9f`（local）、子タスクは`01a0d9ad-afd0-7d30-8653-b7e7c95c4703`。開始点`53cb70c9`のbranch `codex/enrich-inference-reuse`で作成した`25fe5de9`を、母艦が`2b1f25a0`として統合し、主checkoutでも全1,057 test成功を確認した。新しく起動するCLI・enrich workerは統合後の実装を読み込む。既に実行中のworkerは切り替えず、通常model設定・本番Webも変更していない。

統合後の初回enrichは照合値を作るため通常どおり生成する。次回以降の実運用runで`metrics.triage_reuse`のhit/miss理由、実triage request数と時間、収集失敗と新しい候補の扱いを確認する。実週次全体の短縮率は未検証で、追加最適化は実際のmiss理由から判断する。同じmodel IDの中身・接続先既定設定の変更は自動検知対象外であり、必要時の明示再生成を利用する。
