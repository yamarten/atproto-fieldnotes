# 紹介候補の選定評価と反映境界

2026-09-22。技術候補入口は実装済みですが、主機能でのatproto利用の自動判定は、実資料での評価を受けて見直し中です。先にOAuth宣言を正確に解析・保存・表示する基盤を実装しました。正本DB更新・実運用serverへの反映は行っていません。

## 確認した範囲

- 通常CLI/Web/applicationからの技術候補抽出、全件抽出後のページ送り、手動補正の保存・再調査時の保護・解除、Lower priority・Defer・確認済み・理由検索・根拠変化の回帰を確認しました。
- 全unit test 987件、Web API test、JavaScript構文検査が通過しました。実ブラウザでは開発DB、`127.0.0.1:8765`で技術候補への切替、補正の保存・再読込・解除、Lower priorityと理由検索を操作しました。desktop 1280px / mobile 390pxで確認し、mobileの横方向overflowはありません。検証serverは停止済みです。
- 正本5,054件の同じread snapshotのactive 3,382件を、変更前の選定関数（`64e26831`）と変更後へ通しました。通常確認対象は441→448件、先頭50件内の技術候補は1→16件、専用の技術候補は16件です。既存候補の脱落はありません。追加7件は説明文による派生Blueskyツール除外を外した結果です。利用範囲は全件未評価のままで、正本へ保存していません。
- 追加はGlimpSky、Bluesky Tracker、Skydeck、SkyTrace、favoritism、Skymarshal、thumbs.sky.booです。これらの推薦価値が確認されたという意味ではありません。限定的な利用の評価または個別Lower priorityで順位を調整できます。

## 実LLMの較正

`promotion-selection-v1.json`は、既存manual goldの好例5件とユーザーによるSquire・SkySweeperの指摘、schemaだけの反例から作った短い構成素材です。実ページsnapshotではありません。既存Qwen 3.6 35B A3B、通常Responses transport、reasoningなし、既存model suffix、最大2,048出力token・180秒で、外部ページを取得せず実行しました。

| 素材 | 期待する利用範囲 | 期待する技術評価 | 初回 | 基準明確化後 |
| --- | --- | --- | --- | --- |
| Bluesky | main_data | high | 技術評価がnotable | 両方一致 |
| rolld | main_data | notable | 一致 | 未再実行 |
| standard.site | unknown（仕様のみ） | high | main_data / notable | 両方一致 |
| idiolect | unknown（仕様のみ） | high | main_data / notable | 両方一致 |
| meyasuat | main_data | notable | 一致 | 未再実行 |
| Squire | supporting | notable | 一致 | 未再実行 |
| SkySweeper | bluesky_client | notable | 一致 | 未再実行 |
| schema-only | unknown | notable | 一致 | 未再実行 |

初回8件の後、追加承認された3件だけを各1回実行しました。修正はproject名やURLの例外ではなく、仕様と実装の区別、基盤実装・相互運用設計と通常CRUDの評価基準です。固定出力を通常経路へ通した8件の回帰testと、実LLMによるこの11回を区別します。

## 残る境界

### 実資料による追加比較と再設計

構成素材と別に、12対象の公開ページ・READMEをA、OAuth metadata追加をB、schema・record追加をCとして固定し、異なる25入力を同じローカルLLMへ各1回送りました。24 JSON成功・1失敗で、Cの正常応答11件はすべてmain_dataとなりました。Squireのactor情報を主データと解釈し、standard.siteの仕様と第三者の実装を混同し、Lopecodeやmathrでは要求scopeから主機能を推測しました。これは実験用promptの結果で、通常triageの正答率測定ではありません。比較の参照値もCodexの暫定値で、人間のgoldではありません。

追加情報を大きなLLM判定へまとめる方式は採用せず、(1) scope等の機械的な解析、(2) 公式の保存先・読書き・共有の説明の抽出、(3) collectionとtagline等の限定的な意味対応、の順に進めます。利用の深さとBlueskyクライアントかどうかを分離し、既存の寄与度との責務を整理する必要があります。読取り中心を一段下げる提案は、まだ確定した採点規則ではありません。

速度も評価対象です。実資料の25 LLM requestは合計548秒・中央値22.2秒・最大32.5秒でした。通常enrichへ同じ呼出しを追加する設計にはしていません。先頭抜粋だけではGlean等の保存先説明が落ちるため、説明材料の取り出し方も次の検討対象です。詳細な固定素材と出力は作業用の`.tmp/usage-evidence-evaluation/`にあります。

### OAuth解析の現在地

公式`@atproto/oauth-scopes@0.5.12`を追加し、独自の文字列切出しを置き換えました。操作queryをNSIDへ混ぜず、権限セット・wildcard・未解釈を区別し、scopeが減った場合も最新観測を維持します。`include`の解決、主機能との対応付け、新しい寄与度規則は含みません。従来のrelationは元scope全体を欠くため、既存DBの新形式への更新には再取得が必要です。

Glean（repo 4・RPC 7・blob）、mathr（repo 2・RPC 1）、Lopecode（repo 8・blob）の保存済み公開metadataは全tokenを解析できました。取得やLLMを含まないローカル解析を各5回計測し、Node起動込みの中央値は94〜95ms、最大134ms、cache利用時の中央値は0.007〜0.011msです。enrich全体の速度やmetadata発見率の測定ではありません。外部取得契約は変更しておらず、今回は新規HTTP smokeをせず取得済み材料を使いました。

開発DBのDemo Field Notesへ架空の4観測を追加し、localhost:8765の実ブラウザで認証scopeのみ・具体的操作・未解決include・移行用scope・未解釈token・元scopeを確認しました。parser欠落／timeoutとDB再適用はunit testで確認しています。最終の全unit testは997件通過、Web API testとJavaScript構文検査も通過しました。検証serverは停止済みです。正本DBへの書込み・実enrichの再実行はしていません。

### 取り込み前の注意

- 最終promptで全8件を再実行した結果ではありません。反復安定性、取得された長い実資料での精度、実運用全体の有用候補捕捉率は未確認です。
- 根拠IDの妥当性は意味の正しさを保証しません。schema-onlyの初回理由には入力にないDNS確認への言及がありました。unknownを維持していても理由文の無人受理はできません。
- 既存データは新しい利用範囲評価を持ちません。入口・順位は読み取り時に反映されますが、個別の利用範囲は通常triageまたは理由付き手動補正が必要です。一括再評価や正本への個別補正は今回の承認範囲外です。
- 技術評価の閾値を下げて候補数を増やしてはいません。既存のnotable評価が新たな基準でhighになるかは、今後の通常調査で確認します。
- parentがroadmapと取り込みを管理します。このtaskでhuman reviewを終えてから、承認された報告を母taskへ渡します。
