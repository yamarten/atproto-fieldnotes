# Tangled owner解決後の同期再開

owner確認はBluesky profile取得からDID・handleの双方向検証と解決不能recordの保留へ置換済みです。本番execute、保留catalogの作成、定期presetの再開・有効化、実運用反映は未確認・未実施です。再開前に現在のjob・preset・watermarkとコード反映を確認します。[取り込み仕様](../developer/specs/discovery-and-sync.md#ownerのidentityと解決不能record)が現行契約です。

## 失敗と再開の起点

2026-09-19 23:05 JST開始の`sync_sources_then_enrich`は最初の`sync tangled`で失敗しました。jobは`job:3783c45cceab`、operationは`sync-20260919T140531Z-b549da87`です。ownerの`getProfile` 400が書込み前の解決段階で全体を停止していました。

2026-09-20の正本read-only smokeはwatermark `23491334`、`last_success_at=2026-09-12T14:05:25+00:00`から1,000 eventで区切り、途中終了でした。停止ownerを保留し正常候補を評価でき、sync state不変・`total_changes=0`を確認しました。現在のwatermarkや実作成の確認ではありません。

| owner | 当時の確認対象と結果 |
| --- | --- |
| `did:plc:u5bivxodft4ol76wydfyvwlg` | millipds、carsync、pdslsの3 record。DID claimは取得できるが逆引きが400／Unable to resolve handleで`handle_unresolved`として保留候補 |
| `did:plc:3c6vkaq7xf5kz3va3muptjh5` | partially.dead.aylac.topの双方向確認に成功し、atproto-basic-notifications、pdslsを通常候補として評価 |

解決不能を永久無効と断定せず保持します。本番での保留保存、停止owner復旧後の取り込み、長期件数・API利用量は未検証です。watermark手動変更・全履歴再走査・正本修復は再開確認の代用にせず、個別projectの処遇とも分けます。
