# 紹介候補の選定品質：保留と再開材料

[TODOの紹介候補の順位改善](../developer/todo.md#atproto寄与度と紹介候補の順位)で、関心の高い共有Lexiconや特徴のある実装を取りこぼさず、選定時の不要な確認を減らす方式を判断するための比較資料です。保存済み候補の順位を変える案と、順位に使う分類・説明をLLMで再検証する案を評価しました。以下では同一DB snapshotによる順位比較と、限定した原文入力によるLLM比較を分け、採用を見送った理由と反例を残します。

保存済み情報による2026-09-23の比較では、合計score順や種類別の枠配分が現行より役立つ根拠を得られず、利用者は順位方式を採用せず調整をいったん区切る方針を了承しました。未紹介stableの包含だけは通常経路へ実装済みです。共有Lexicon等の後順位、技術評価の揺れ、候補外の問題は残ります。全体の捕捉率・ノイズ率や方式全体の品質合格を確認した結果ではありません。

自動再検証・修正・自己エスカレーションの追加も、限定的なモデル比較の後に利用者の打切り方針により当面保留です。モデル・取得資料・作業方式が実質的に変わり、以前の見逃しと利用負担を再評価できる場合に再開します。現行の候補条件は[評価仕様](../developer/specs/project-evaluation.md)、実運用反映の確認待ちは[TODO](../developer/todo.md#運用の確認適用待ち)、目標・再検討条件は[TODO](../developer/todo.md#atproto寄与度と紹介候補の順位)を参照してください。

## 維持する判断

利用者が確認した相対順は「共有Lexicon ＞ Juntos ＞ クライアント」、特にBlueskyクライアントは低優先です。Juntos自体が特に高優先という意味ではなく、固定配点・クライアント自動除外・独自Lexiconサービスの一律昇格へ広げません。技術的な学習価値と紹介関心を分け、関心に合わせて技術highの意味を変えません。

未紹介stableを通常候補へ含め、stalledは既定から外す方針は実装済みです。Streamplaceは紹介済み扱いでもよい例外と確認され、未紹介対象の取りこぼしの代表例に数えません。その判断によるDB更新は未実施です。

## 順位を再検討する時の対照

以下は当時5,054件の同一snapshotを通常application／候補builderで読んだ比較です。既定active・個人用除外の448候補と、active＋stableの459候補を区別します。順位は現在値ではなく、再開時に通常経路で再計算します。未紹介を負例にせず、紹介済み501レコードも108投稿URLに対応するため独立した501事例とは数えません。

| 対照 | 当時の材料と診断 |
| --- | --- |
| Calendar `proj:d35d6f719f84`／Juntos `proj:0edac60136a4` | 共有event・RSVP定義と独自channel record。activeでは179位／348位、stable追加後は183位／355位。schemaや用途への関心と利用可能性を混同しない |
| Cobalt／Fortransky vs Coves | 技術highのpriority 8がserviceの7に先行し、94点のCobaltが446点のCovesより上。珍しい端末と具体的なdecode機構を同じ理由で下げない |
| Konbini `proj:0db4f693c077`／idiolect `proj:1c7a9313031b` | 部分index AppView／署名recordと翻訳の材料があるがnotableでは技術入口なし。idiolectは紹介済みの診断対照で、新規救済数に数えない |
| SkySweeper `proj:b749205083ba` vs Juntos | 保存値では52位／348位。メモリ上だけで前者bluesky_client・後者main_dataを補うと2件の順は直る。入力不足も原因だが、全件手動判断を解決の前提にしない |
| Fortransky vs Calendar | 仮にbluesky_clientでも技術highのpriority 8が勝つ。利用範囲の補完だけでは全逆転を解消しない |
| Laplan／hydrant／zkat | API経路合成、index・relay、署名MSTのZK検証など具体的技術の根拠。技術候補全体をノイズ扱いしない |

技術levelの較正仮説をメモリ上で適用するとCobaltとKonbiniを入れ替えられましたが、Calendar・Juntosの後順位は残りました。現行LLMの実資料9件×2回ではCobalt訂正だけが通常候補の出入りを変え、Konbiniは候補外のまま、standard.siteのhigh／notableも揺れました。単純な再評価を安定した救済策にはできません。仮説labelはCodexの診断で、人間goldやDBへ採用した判断ではありません。

active＋stableの同じ459候補で合計score順だけにすると先頭50件の技術候補は16→2となり、Laplan・hydrant等が落ち、Lexiconは0のままでした。技術8または12枠・DNS公開の`category=lexicon`12枠という対案はCalendar等を50件内へ入れますが、汎用schema・サービス断片・紹介単位不明の行も入り、具体的な基盤と確認済みサービスを押し出します。`lexicon_scope=shared`にも同様の断片があり、独立した共有仕様の確定条件には使えません。両案でJuntosの355位とKonbiniの候補外は変わらず、枠案は採用しませんでした。

## 自動分類・検証を再開する時の反例

| 試した方式 | 採否に効いた限界 |
| --- | --- |
| 原文中心の短い対象分類、Qwen 3.6・推論OFF | Juntosを2回ともBluesky clientと誤分類し、一回は理由文とlabelも矛盾。参照ID一致・JSON成功では順位入力を保証できない |
| 同じ5件・prompt、OFF／ON各2回 | ONでJuntosとCobaltは改善するがKonbiniの閾値と根拠外補完は残る。中央値10.746→127.607秒で、利用者はデフォルトONを除外 |
| Konbini・Laplanの主張照合と要約再生成 | ON/OFFともLaplanのリトライ宣言の根拠不足を指摘しながら、再生成要約へ再混入。検出成功と修正成功は別 |
| Qwen 3.6・OFFの主張別確認 | 長い説明は一部の根拠で全体を支持。手動で短く分けると改善するが、Cobaltの`Wolfram-backed session and XRPC`からcustom backendまで確定する見逃しが残る |
| Qwen 3.8-27B・OFFの同入力12回 | 長い説明の検証は改善するがcustom backendの見逃し、指摘理由の誤りと追加時間が残る。利用者の「これでも駄目なら当面諦める」に従い保留 |

Qwen 3.6比較のAPI要求値はnone／lowですが、後のcapability確認はoff／on型でした。「低い推論強度」を実証した比較ではありません。最初の2,048 token枠ではreasoningだけで最終回答がなく、意味比較が成立せず、完走比較は両条件8,192 token・420秒です。キャッシュ・負荷は統制しておらず速度比を全件運用へ外挿しません。3.8の推論ON等は未検証です。

自己エスカレーション用入力は準備しましたが生成は0回です。手動切り出しの主張検証を、自分の誤りを見つけて再確認へ回せた結果と扱いません。人が指定した短い主張の補助確認は否定しませんが、supportedの自動承認、理由の自動較正、再生成要約の自動採用は見送っています。追加資料を取るdeep全体の効果も未検証です。[機能とcollectionの対応](feature-collection-semantics-evaluation.md)の不採用理由も併読します。

## 再開時に混同しない実装状態

旧入力構築の容量超過とrepository URL出力fieldの不一致は、後続実装で対応済みです。現行は整形空白削減と、claim・原文・参照先を一緒に扱うrepository解析縮約を行い、省略を記録します。旧失敗20件を未修正件数として引き継ぎません。根拠の省略による品質と実LLM生成は別途未検証です。

JuntosのLLM提案`live`は通常の最終処理で`unverified`へ戻りました。途中の`apply_llm_output`だけの加点を、通常enrichの最終保存不備と扱いません。一方、最終messageのないreasoning-only応答がJSON解析へ進んだ診断は別の確認対象として残ります。再開時に現行transportと照合します。

## 再確認する個別判断

[TODOの個別projectの再検討](../developer/todo.md#個別projectの判断を再検討する時)で同じ対象を扱う際の留意点です。順位方式の比較とは別に、過去の仮説を確定labelとして使わないために保持しています。

- math.r（`proj:edb76ad07967`）は過去fixtureで低優先、当時DBでは判断なし。消失原因は未確認で、今も低優先か再確認します。
- Blacksky AppView（`proj:2c69d06edf95`）と紹介済みBlacksky（`proj:cef4c9185f74`）は紹介単位が未確定。HappyViewには紹介済みGitHub行と未紹介Tangled行があり、同名Glean・Leaflet等も別々の高関心対象として数えません。
- Ionosphereのrepo `github.com/blaine/ionosphere`を持つ`proj:1190661ee687`は保存名がStreamplace、READMEはIonosphereでした。説明名と原文の不整合を訂正していません。
- y-pdsのSDP交換、SSRF Probeの技術highは当時読んだ原文では裏取り不足でした。全根拠の不存在とは断定せず、現在の資料から確認します。

当時の比較入力・全出力は、このcheckoutには収録していません。上記は診断と保留判断の要約で、完全な再実行の入口ではありません。既知例と上位・保存語彙に偏った比較なので、再開には現在の資料から対照を作り直し、未使用対象と通常導線での効果を確認します。新たな判別材料や具体的な運用問題がないまま、同じ例への配点・prompt調整を再開しません。

## この資料の役割が終わる条件

順位・自動検証の各案を採用または検討終了と判断し、必要な理由を仕様・TODOへ反映した時、または反例を引き継ぐ新しい比較に置き換えた時に、対応する比較記録を削除します。個別判断はDB等の適切な記録への反映か、再確認不要の判断で別々に閉じます。残る個別事項だけならその判断先へ集約し、全件の訂正を待つために本資料全体を保持しません。
