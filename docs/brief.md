# Project Registry Brief

## 目的

atproto関連のproject、service、library、lexiconを継続的に発見・調査し、紹介対象かどうかを一人のoperatorが
根拠付きで判断できる状態を保ちます。単なるURL一覧ではなく、同一projectのidentity、ownership、service状況、
repository、lexicon、紹介済み状態と、人間の判断履歴をlocal registryへ統合します。

## 利用者と成果物

利用者は、このrepositoryと単一の正本DBをローカル運用する一人のoperatorです。multi-user運用、共有DB、
外部利用者向けAPIは対象にしません。

現在実装済みの成果物は、SQLite registryと、その内容を調査・reviewするCLI/Web UIです。promotion queueは
紹介候補を提示しますが、紹介文の公開や外部directoryへの配信は未接続です。

最終成果物を次のどこまでにするかは未決定です。

- 紹介候補と根拠のqueue。
- 紹介文draftに必要な核データ。
- 公開可能な網羅directoryまたはapp record export。

この選択が決まるまでは、未接続の公開導線を実装済み成果物として扱いません。

## 通常の運用flow

1. configured source、検索、外部directory、手動発見からseed候補を集める。
2. exact identifierと保守的なmatchingで既存projectへ接続し、曖昧な候補はreview対象にする。
3. collectorと必要に応じたLLMでservice、repository、lexicon、ownership、紹介材料をenrichする。
4. failure、candidate identifier、merge/split、tracking stateを人間がreviewする。
5. promotion queueで紹介候補を確認し、紹介済み情報をlocal registryへ反映する。
6. 定期的に保存量、失敗率、古いproject、調査時間をauditし、不要な履歴や弱い自動判定を見直す。

## 対象の境界

紹介対象:

- 独立したatproto projectやservice。
- 独自lexicon、実用service、またはatproto ecosystemへの明確な技術的貢献を持つもの。
- operatorが紹介に必要な根拠を確認できたもの。

調査対象:

- 紹介対象か未確定だが、atprotoとの関連を示すevidenceがあるもの。
- library、infra、auth integration、personal toolなど、別categoryで保持する価値があるもの。
- identityやownershipが曖昧で、人間による確認が必要なもの。

記録のみ:

- 現在は紹介しないが、将来の再評価や既存projectとの関係確認に必要なもの。
- `out_of_scope`、`archived`、`stalled`として保持する根拠があるもの。

ignore:

- atprotoとの関連根拠が無いもの。
- 独立identityや追加価値が無いfork/noiseで、再調査の必要もないもの。
- operatorが明示的に追跡不要と判断したもの。

自動heuristicだけで紹介対象やignoreを最終確定しません。

## 正本と責務境界

- 外部directory、検索結果、collection inventory、GitHub、Bluesky、Semble、Tangledはdiscovery upstreamです。
- canonical project、identifier classification、merge/split、block/defer、tracking state、紹介済み状態はlocal
  SQLite registryを正本とします。
- source固有payloadやLLM出力はevidenceであり、operator decisionを上書きする正本ではありません。
- DBへ保存したcurrent stateとreplay可能なhistoryを区別し、raw payloadを無制限に正本へ蓄積しません。

## 主要な設計判断

- confirmed、candidate、derived/heuristicを区別し、弱い根拠をconfirmedとして扱いません。
- identity、ownership、mergeは保守的に扱い、曖昧なものをqueueへ送ります。
- CLIとWebはapplication serviceを共有し、SQLiteを唯一の正本DBとします。
- 長時間operationは別processで実行し、progress、協調停止、必要なcheckpointを持たせます。
- 個人ローカル運用を前提にし、使われていない互換層やmodeを維持しません。
- 保存量と調査品質を計測してから、history形式やretentionを変更します。

## 非目標

- public SaaS、multi-user、OAuth/RBAC、共有DB対応。
- 外部directoryのpayloadや判断を、そのままlocal registryの正本にすること。
- raw HTML、README、collector payload、LLM prompt/outputを無制限に保存・表示すること。
- 曖昧なidentity、ownership、merge、紹介可否を完全自動で確定すること。
- 旧DB schema、旧内部Python API、旧Web payloadの後方互換維持。
- 現時点で未決定の公開directory/app record仕様に合わせてcore保存形を歪めること。

## 成功指標

指標名は次で固定し、数値目標はoperator判断が決まるまで未決定とします。

| 指標 | 現在の目標値 | 測る内容 |
| --- | --- | --- |
| 週当たりoperator時間 | 未決定 | sync、enrich、review、cleanupに使う人間の総時間 |
| 1件当たりreview時間 | 未決定 | candidateを確認し、判断を保存するまでの時間 |
| 増分enrich所要時間 | 未決定 | 通常の新規・失敗再試行対象を処理する時間 |
| 全件enrich所要時間 | 未決定 | 全tracking対象を一巡する時間 |
| 紹介判断precision | 未決定 | 紹介可能としたsampleのうち、人間確認で妥当な割合 |
| identity/merge correction率 | 未決定 | 自動候補のうちblock、split、separateで訂正した割合 |
| source failure率 | 未決定 | source別requestまたはcollector runの失敗割合 |
| DB増加量 | 未決定 | 週・run・project当たりの保存bytes |

数値を決める前に、静止DB baselineと代表operationのtimingを取得します。測定不能な指標は仮値で埋めず、
観測経路が未接続であることをstatusまたはroadmapへ残します。
