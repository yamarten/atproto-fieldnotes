# candidate / confirmed一致のマージ候補レビュー

現行構成要素の同じidentifierが一方candidate、他方confirmedの組を、採用前の比較候補として拾う変更です。個別ページ、moderate/broad一覧、比較画面で一致値と双方の採用状態を表示します。対象・点数・guardの契約は[Project境界](../developer/specs/project-boundaries.md#candidate-の根拠)を正本にします。

## 合意と反映境界

- ユーザーは2026-09-27、この一致だけでmoderateへ追加し、相互最上位条件を維持する案を採択しました。無条件の一括マージは追加しません。
- Codexの技術判断で既存`COMPONENT_ROLES`と現行identifier viewを再利用し、開発者の共有・非現行・block/deferを除外しています。追加点だけで自動マージ可能へ昇格させず、他の既存理由による可否を維持します。
- 開始点は`00ba607147f21d2da5697351b3e817558b7694b6`、branchは`codex/candidate-confirmed-merge`です。受理方式はhuman-reviewで、最終画面・候補品質の受理と母艦への送信は未承認です。
- 本番DB更新・実merge・Web再起動は行っていません。母艦への統合後、Web反映にはserver reloadが必要です。保存済みreview queueの派生候補全件の更新は含みません。個別ページとマージ一覧は読込み時に計算します。

## 実データでの差分とsample review

2026-09-27の正本5,142件を`mode=ro`の同じread transactionで読み、開始点と変更後の関数を同一snapshotへ適用しました。外部取得は行っていません。

- guard・別project指定を除いた一致は280組。識別子別ではrepo 177、service 139、NSID 2で、複数の一致を持つpairがあります。うち110組は従来理由では拾えなかった組です。
- moderateは538→581組（55追加、12除外）。加点で推薦順位が変わり、従来pairが相互最上位でなくなって除外される場合があります。
- 既存候補pairの`auto_merge_ready`の変化は0組。ただし、別の既存理由でreadyだったpairが新たにmoderateへ入る場合にも、その判定は維持します。
- 復元済みproject集合でのplan生成は1回ずつ9.79→10.57秒でした。DB読込みを含むHTTP時間や反復benchmarkではありません。

以下はmoderateへの追加例です。名前や値の一致は観測事実であり、mergeの正解ラベルではありません。最終的な候補品質のhuman-reviewは未実施です。

| pair | 一致値 | レビュー観点 |
| --- | --- | --- |
| WhiteWind (`proj:fec4b7da44a2`) / WhiteBreeze (`proj:3446d362d145`) | service `https://whtwnd.com` | 同じサービスを別名で登録したものか、構成範囲を確認する |
| Rito (`proj:63d8f13ca0ee`) / Rito (`proj:43095c7fc660`) | repo `https://github.com/usounds/Rito`、service `https://rito.blue` | 複数一致でも自動確定に昇格しない |
| tapfall (`proj:698b749f3e60`) / skyfall (`proj:b8d039b334d8`) | repo `https://tangled.org/mackuba.eu/tapfall` | 別作品の候補repo混入か、同じ説明・追跡単位かを確認する |
| atproto-identity-rs (`proj:634f6bc6b106`) / atproto-plc (`proj:723b9cf13beb`) | repo `https://tangled.org/smokesignal.events/atproto-identity-rs` | 同じ作者の関連作品を統合しないよう構成範囲を確認する |

## 検証と残る確認

一時DBのtestで全構成要素role、双方向の一致、異なるrole、developer、candidate同士、NSID大小文字、block・有効／期限切れdefer、former/non-production、別project指定、相互最上位条件、既存候補、自動マージ可否、照会前後のDB不変を確認しました。APIではmerge-analysis・merge-pair・strict/moderate/broad一覧と別project指定後の除外を確認しています。

開発DB・`127.0.0.1:8765`の実ブラウザで、最上位とその他の候補から比較、詳細へ戻る、方向反転、moderate一覧から比較を確認しました。通常幅と390px幅で一致値・採用状態の表示と比較導線を確認し、JavaScript構文検査と全unit testも成功しています。worktreeのNode依存は同一lockfileの主checkoutにある導入済み依存を一時参照して検証しました。実データpairの統合可否、実運用Webへの反映、候補品質と最終UIのユーザー受理は未確認です。外部source契約を変えていないため、外部通信のsmoke testは不要と判断しました。

母艦統合と受理を確認したら状態を更新し、再開材料がなくなったらこのnoteを削除します。
