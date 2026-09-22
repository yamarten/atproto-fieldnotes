# 紹介候補の選定評価と反映境界

2026-09-22。技術候補入口と主機能でのatproto利用評価は実装済みで、人によるレビューと母taskへの取り込みを待っています。正本DB更新・実運用serverへの反映は行っていません。

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

- 最終promptで全8件を再実行した結果ではありません。反復安定性、取得された長い実資料での精度、実運用全体の有用候補捕捉率は未確認です。
- 根拠IDの妥当性は意味の正しさを保証しません。schema-onlyの初回理由には入力にないDNS確認への言及がありました。unknownを維持していても理由文の無人受理はできません。
- 既存データは新しい利用範囲評価を持ちません。入口・順位は読み取り時に反映されますが、個別の利用範囲は通常triageまたは理由付き手動補正が必要です。一括再評価や正本への個別補正は今回の承認範囲外です。
- 技術評価の閾値を下げて候補数を増やしてはいません。既存のnotable評価が新たな基準でhighになるかは、今後の通常調査で確認します。
- parentがroadmapと取り込みを管理します。このtaskでhuman reviewを終えてから、承認された報告を母taskへ渡します。
