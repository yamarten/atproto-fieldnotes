# Identifierのmerge後欠落と限定復元の保留

sourceのconfirm判断がmergeで統合されない一般不具合は再現・修正し、masterへ統合後も全unit testの成功を確認しました。一方、Substratum Passportのroot URLが実際に消えた操作は未確定です。今回のコード修正を、その過去事象の完全解明や正本復元として扱いません。正本への書込み・実運用serverへの反映は未実施です。

## 確認した範囲

2026-09-21 JSTの正本read-only監査で、`proj:235ece82b0b7`の`https://substratum.cloud`（`kind=service_url, role=service`）はidentifier行に無く、次のoperator採用来歴だけが未解除で残っていました。

| 採用ID | 元project | 採用時刻（UTC） | targetへのmerge時刻（UTC） |
| --- | --- | --- | --- |
| `adoption:969fb2ba0fafd1ff90fcb2d6` | `proj:d1c8e155e541` | 2026-09-20 11:01:58 | 2026-09-20 11:02:29 |
| `adoption:38b2cfcf99efcca273aa6680` | `proj:f6b33b2fd813` | 2026-09-20 11:03:19 | 2026-09-20 11:05:37 |

さらに`proj:1e3ed7925f53`が11:18:39 UTCにmergeされています。同URLのblock/defer・非現行identifier・transition・registry-wide exclusion・別projectの現行identifierはありません。`https://app.substratum.cloud`は別のcandidateであり、復元値への代用や統合はしません。

有効な採用来歴と同じ`kind + value + role`のconfirmed identifierが無い例は、operator採用148行・132projectを含む採用全種の監査で、この1値・2来歴だけでした。単なるconfirm decisionの欠落には旧materialization由来の例もあり、差集合全体を今回の不具合や復元対象とは扱いません。

架空DBの通常confirm→merge→fetchで、sourceのroot URLはidentifierが残ってconfirm判断だけ消え、`/docs`のような補助pageはconfirmによる保護を失ってidentifierも消えることを再現しました。target側のconfirmは保持されました。単純なconfirm一覧の結合だけでは、元の採用に加えて架空のtarget採用を生成するため、採用eventの移送と保存時照合も修正しています。現行の保存契約は[Project record](../developer/specs/project-record.md#merge--splitの履歴)を参照してください。

## 未確定の消失操作

alias familyの保存済み調査履歴49件には、9月20日のconfirm/merge直前直後の入力snapshotがありません。現在targetとsourceの過去snapshotを使ったメモリ上のmergeではroot URLを保持しました。通常保存は欠落したclaimの採用来歴を解除するため、後続merge後にも2来歴が未解除で残る事実は「最初のmergeで落ち、その後通常保存された」という説明と整合しません。当時の実行コード・別の直接更新・未保存の入力差を特定する証拠は不足しています。stale enrichが原因だったことも確認していません。

母艦で確認できた操作には該当時刻後の正本identifier直接修正・repair等は含まれませんが、ユーザーGUI・別タスク・定期workerを含む全操作の不存在を保証するものではありません。追加の当時snapshotや限定操作記録が見つかった場合に、消失時点を再調査します。過去の完全解明を一般不具合修正の前提にはしません。

## 正本の限定復元

元の2採用来歴は同じservice claimの復元根拠になります。現在の状態を再確認し、以下の手順を正本適用の別承認へ提示します。

1. read-onlyで対象のrevision、元2採用、同値のidentifier・否定判断・非現行状態・別所有者を再確認し、追加する1claimと今回の復元判断の差分を用意する。
2. 過去decisionの完全metadataは不明なので再現したことにしない。元採用IDを理由に記した今回の復元判断を新しい日時で記録し、元の採用ID・時刻・originはそのまま維持する。新規の採用eventを残す場合も、今回の復元操作として明示する。
3. 正本適用の承認後にbackupを取得し、競合のない短いtransactionとrevision検査で限定適用する。通常の`repair-db`による全件再評価は復元手段にしない。
4. 保存後の再読込でidentifier・判断・元2採用来歴と新規復元eventの対応を確認し、同型監査を再実行する。復元が済んだらこの保留とstatusの導線を更新する。

確認済みの一般不具合と限定復元の残件は分離します。実運用への反映、正本復元、当時の全操作追跡、実データに対する修正後のmergeは未実施です。
