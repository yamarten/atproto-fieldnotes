# Identifierのmerge後欠落・限定復元と未解明部分

sourceのconfirm判断がmergeで統合されない一般不具合は再現・修正し、masterへ統合後も全unit testの成功を確認しました。2026-09-21に承認済みの実運用Web再起動とSubstratum Passportの1claimの限定復元を実施しました。一方、root URLが実際に消えた過去の操作は未確定で、通常fetch→save時の`lexicon_count`投影にも未修正の問題が残ります。このメモはその再開材料と復旧backupの所在を保持します。

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

## 限定復元の確認と保持するbackup

2026-09-21 01:25 UTCに実行中jobがないことを確認し、標準`bin/project-registry-web-service restart`で再起動しました。認証済みGETでbuild `7c64af149190`（修正統合済み・clean）、health正常、同じ正本DB、instanceの更新を確認しています。jobの中断・新規実行・定期設定変更はしていません。

同日01:27:05 UTC（10:27:05 JST）に、`https://substratum.cloud`の`service_url / service / confirmed`を1行追加しました。元の2採用ID・日時・originを参照する今回のconfirm判断と、新しい復元採用event `adoption:2529206b49196d7dbe568475`（source=`operator_review:identifier_recovery`）を各1行記録しています。過去decisionの再現とはしていません。対象revisionをtransaction内で再検査し、81→122へ更新、write transactionは約8msでした。

SQLite online backupによる一時DBへの実保存と、正本適用後の全table差分・対象外row hashの監査を実施しました。identifier・decision・adoption各1行の追加、既知のlegacy NSID confirm2件の同値metadata正規化、対象revision以外に変更はありません。元の全採用行、他role、project本体のprofile/state・時刻、他projectの全rowを保持しました。対象URLの採用来歴は元2件と今回1件の計3件で、いずれも未解除です。Web GETでもconfirmed serviceと今回の判断を確認しました。05:33 UTCの事後監査で同型の有効adoptionとconfirmed identifierの不整合は0件でした。

復元直前の一貫したbackupは`data/backups/identifier-recovery-20260921T012600Z/survey.db`に保持しています。取得時revisionは81、`PRAGMA quick_check`は`ok`でした。復元確認用の一時DBは削除し、このbackupは通常の一時cleanup対象にしません。保持用途を判断するまで削除しないでください。

当時の全操作追跡、実データでの修正後mergeは未実施です。通常経路の再発防止は架空DB・APIの回帰testで確認し、運用確認のための追加mergeや全件repairは行っていません。

## 未修正の周辺問題：lexicon_countの投影

一時DBへの初回保存で、対象の`state_json.evidence.lexicon_count`が12→0になる想定外差分を検出し、正本適用前に停止しました。現行fetchのruntimeは`resolved_nsids`に12件を持つ一方、`lexicons`が空で、通常saveが`lexicon_count=len(lexicons)`として再投影するためです。identifierのNSID行自体の消失とは区別します。

今回の限定復元では、母艦確認済みの既存finalize契約どおり、保存前にruntimeの`lexicons`を`unique_list(resolved_nsids)`へ同期しました。再検証で`lexicon_count=12`を含むstate全体の不変を確認してから正本へ適用しています。これは復旧手順の補正であり、一般codecは変更していません。通常fetch→変更→saveで派生件数を維持する契約は未修正です。再開時は、candidate NSIDとconfirmed NSIDを持つ一時DBで同経路を再現し、hydrate/saveの責務と無変更roundtripを検証してください。
