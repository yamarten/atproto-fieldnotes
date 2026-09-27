# candidate / confirmed一致のマージ候補レビュー

現行構成要素の同じidentifierが一方candidate、他方confirmedの組を、採用前の比較候補として拾う変更です。個別ページ、moderate/broad一覧、比較画面で一致値と双方の採用状態を表示します。対象・点数・guardの契約は[Project境界](../developer/specs/project-boundaries.md#candidate-の根拠)を正本にします。

## 合意と反映境界

- ユーザーは2026-09-27、この一致だけでmoderateへ追加し、相互最上位条件を維持する案を採択しました。無条件の一括マージは追加しません。
- Codexの技術判断で既存`COMPONENT_ROLES`と現行identifier viewを再利用し、開発者の共有・非現行・block/deferを除外しています。追加点だけで自動マージ可能へ昇格させず、他の既存理由による可否を維持します。
- 開始点は`00ba607147f21d2da5697351b3e817558b7694b6`、branchは`codex/candidate-confirmed-merge`です。受理方式はhuman-review。ユーザーは候補実例のレビュー後、Cartridgeの同一性への留保を残しつつ機能を受理し、母艦への送信を承認しました。各pairのmerge承認ではありません。子タスクcommit `0d9abd60`を母艦masterへ`e7b0e5c6`として統合しました。
- 本番DB更新・実merge・Web再起動は行っていません。Web反映には別途server reloadが必要です。保存済みreview queueの派生候補全件の更新は含みません。個別ページとマージ一覧は読込み時に計算します。

## 実データでの差分とsample review

2026-09-27の正本5,142件を`mode=ro`の同じread transactionで読み、開始点と変更後の関数を同一snapshotへ適用しました。外部取得は行っていません。

- guard・別project指定を除いた一致は280組。識別子別ではrepo 177、service 139、NSID 2で、複数の一致を持つpairがあります。うち110組は従来理由では拾えなかった組です。
- moderateは538→581組（55追加、12除外）。加点で推薦順位が変わり、従来pairが相互最上位でなくなって除外される場合があります。
- 既存候補pairの`auto_merge_ready`の変化は0組。ただし、別の既存理由でreadyだったpairが新たにmoderateへ入る場合にも、その判定は維持します。
- 復元済みproject集合でのplan生成は1回ずつ9.79→10.57秒でした。DB読込みを含むHTTP時間や反復benchmarkではありません。

以下はmoderateへの追加例です。名前や値の一致は観測事実であり、mergeの正解ラベルではありません。ユーザーは比較候補として表示する機能を受理しましたが、WhiteWind/WhiteBreezeを代表的な成功例として扱うことには懸念を示しています。

| pair | 一致値 | レビュー観点 |
| --- | --- | --- |
| WhiteWind (`proj:fec4b7da44a2`) / WhiteBreeze (`proj:3446d362d145`) | service `https://whtwnd.com` | ユーザーが同一視に懸念を示した組。構成範囲は未確認で、確認済みの成功例にしない |
| Rito (`proj:63d8f13ca0ee`) / Rito (`proj:43095c7fc660`) | repo `https://github.com/usounds/Rito`、service `https://rito.blue` | 保存説明もソーシャルブックマークで一致。ただし複数一致でもmergeの正解確定にはしない |
| tapfall (`proj:698b749f3e60`) / skyfall (`proj:b8d039b334d8`) | repo `https://tangled.org/mackuba.eu/tapfall` | 別作品の候補repo混入か、同じ説明・追跡単位かを確認する |
| atproto-identity-rs (`proj:634f6bc6b106`) / atproto-plc (`proj:723b9cf13beb`) | repo `https://tangled.org/smokesignal.events/atproto-identity-rs` | 同じ作者の関連作品を統合しないよう構成範囲を確認する |

追加確認ではBookHive同士にもservice一致と保存済みのミラー説明がありましたが、保存情報に基づく比較材料です。Cartridge同士は一致repo以外に`nulfrost/bloc`もあり、ユーザーから「結構怪しい」との留保があります。Cartridgeも確認済み同一projectの成功例として扱いません。実際のmerge可否は各pairの比較で判断します。

## 検証と残る確認

一時DBのtestで全構成要素role、双方向の一致、異なるrole、developer、candidate同士、NSID大小文字、block・有効／期限切れdefer、former/non-production、別project指定、相互最上位条件、既存候補、自動マージ可否、照会前後のDB不変を確認しました。APIではmerge-analysis・merge-pair・strict/moderate/broad一覧と別project指定後の除外を確認しています。

子タスクでは開発DB・`127.0.0.1:8765`の実ブラウザで、最上位とその他の候補から比較、詳細へ戻る、方向反転、moderate一覧から比較を確認しました。通常幅と390px幅で一致値・採用状態の表示と比較導線を確認し、JavaScript構文検査と全unit testも成功しています。worktreeのNode依存は同一lockfileの主checkoutにある導入済み依存を一時参照して検証し、参照を解除済みです。検証用Webも停止しています。実データpairの統合可否と実運用Webへの反映は未確認です。外部source契約を変えていないため、外部通信のsmoke testは不要と判断しました。

母艦では候補抽出・自動マージ判定からの追加点除外・表示経路と対応testを監査し、統合後の`bin/project-registry-test`全1,090件が成功しました（21.102秒）。JavaScript構文検査とdiff checkも成功しています。実データ差分とブラウザ確認は子タスクの記録を受け入れ、母艦では再実行していません。

子タスクは「Candidateとconfirmedの一致をマージ候補へ接続」（`01a0e1c0-6f7b-7ef3-a1ab-8a6044a92d8e`、host `local`）。実運用反映と候補品質の確認に必要な間はこのnoteを残し、再開材料がなくなったら削除します。
