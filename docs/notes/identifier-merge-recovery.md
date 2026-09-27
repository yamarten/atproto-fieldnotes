# Identifier復旧後に残る問題とbackup

sourceのconfirm判断がmergeで失われる一般不具合は修正・実運用反映済みで、2026-09-21にSubstratum Passportの1claimを限定復元しました。過去のroot URL消失操作は未確定で、通常fetch→saveの`lexicon_count`投影も未修正です。

## 消失操作を再調査する材料

対象は`proj:235ece82b0b7`の`https://substratum.cloud`（`service_url / service`）。2026-09-21の監査時、identifierはなく次のoperator採用来歴が未解除でした。`https://app.substratum.cloud`は別candidateで代用しません。

| 採用ID | 元project | 採用／targetへのmerge（2026-09-20 UTC） |
| --- | --- | --- |
| `adoption:969fb2ba0fafd1ff90fcb2d6` | `proj:d1c8e155e541` | 11:01:58／11:02:29 |
| `adoption:38b2cfcf99efcca273aa6680` | `proj:f6b33b2fd813` | 11:03:19／11:05:37 |

`proj:1e3ed7925f53`も11:18:39 UTCにmergeされています。alias familyの49履歴には操作直前直後のsnapshotがなく、保存済みsnapshotでのmergeはroot URLを保持しました。一般不具合の再現ではroot URLは残ってconfirmだけ消え、補助pageは両方消えました。通常保存は欠落claimの採用来歴を解除するため、後続merge後も2来歴が有効な事実は「最初のmergeで消えて通常保存された」だけでは説明できません。

当時の実行コード・直接更新・未保存入力差の証拠が足りず、stale enrichも原因と確認していません。追加snapshotや限定操作記録が見つかった時に再調査します。過去の完全解明を修正の前提にはしません。実データでの修正後mergeは未確認です。

## 保持する復旧backup

復元直前backupは`data/backups/identifier-recovery-20260921T012600Z/survey.db`です。取得時revisionは81、`quick_check=ok`。保持用途を判断するまで削除せず、一時cleanup対象にしません。

同日01:27:05 UTCの限定復元ではidentifier・confirm判断・復元採用eventを各1行追加し、元2採用来歴を保持しました。新eventは`adoption:2529206b49196d7dbe568475`（`operator_review:identifier_recovery`）で、過去decisionの再現ではありません。対象revisionは81→122。対象外rowとstateの不変、Web GETを確認し、05:33 UTCの事後監査で同型の不整合は0件でした。これは当時の監査で、現在DB全件の保証ではありません。

## 未修正のlexicon_count投影

復元確認用の一時DBで通常fetch→saveを通すと、`state_json.evidence.lexicon_count`が12→0になりました。runtimeの`resolved_nsids`は12件ですが`lexicons`が空で、saveが`len(lexicons)`へ再投影するためです。NSID行の消失とは別です。

限定復元時は既存finalize契約に沿って`lexicons`を`unique_list(resolved_nsids)`へ同期し、state全体の不変を確認して適用しました。一般codecの修正ではありません。再開時はcandidate／confirmed NSIDを持つ一時DBで無変更roundtripとfetch→変更→saveを再現し、hydrate/saveの責務を確認します。
