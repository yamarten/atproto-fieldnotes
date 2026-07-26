# Registry Maintenance Growth Review: 2026-07-26

この文書は、2026-07-11のstorage cleanup後に増えた調査履歴の保存量を再計測し、次の保存方式を選ぶための
作業用reportです。現行仕様は[project-record.md](../developer/project-record.md)、確定済みの実装状態は
[status.md](../status.md)、次の作業は[roadmap.md](../roadmap.md)を正本とします。

## このnoteの寿命

- 使いどき: history圧縮またはbyte-aware retentionの設計、受入条件、適用前後の比較
- 消しどき: 採用方式を実装・検証し、必要な結論をstatusとroadmapへ反映した時
- 保存payloadやdry-run出力を時系列reportとして追加し続けない

## 結論

- 新規1,000 investigation runsの再計測条件は2026-07-14に成立し、計測時点では7,631 runs増えている
- cleanup後の論理payloadは1,555,811,509 bytesから2,403,287,582 bytesへ847,476,073 bytes増えた
- 2026-07-10からの16.12日を基準にした30日換算増加は約1.47 GiBで、roadmapの512 MiB条件を超える
- 現行`cleanup-storage-payloads`の再適用は87,334 bytes増える投影であり、再executeする理由はない
- 狭い`evaluation_snapshot`だけを見るとno-changeはpayloadの87.8%だが、categoryやprofile等は変化しているため、
  そのままstub条件には使えない
- top-level差分化は新規snapshotの13.7%削減に留まる。zlib試算は新規history payloadの83.1%を削減するが、
  SQLiteからJSONを直接監査できなくなる
- retentionは単純で効果もあるが、保存期間と履歴消失をoperator policyとして決める必要がある

正本DBはread-onlyで計測し、cleanup、prune、VACUUMは実行していません。

## 計測条件

- measured at: 2026-07-26 11:49 JST
- repository commit: `f9e293d`
- current DB:
  - projects: 3,423
  - investigation runs: 22,717
  - collector runs: 129,749
  - latest investigation: 2026-07-26 02:27 JST
- SQLite allocated bytes: 2,555,985,920 bytes（約2.38 GiB）
- freelist: 327,680 bytes
- `cleanup-storage-payloads --example-limit 5`をdry-runで実行

比較元は2026-07-10 snapshotへ2026-07-11 cleanup policyを投影した値です。元snapshotは15,086 runs、
cleanup前4,620,475,395 bytes、cleanup後1,555,811,509 bytes、削減3,064,663,886 bytesでした。
旧baseline noteの詳細はこのreportへ必要な値を引き継いだため削除しました。

## 保存量の増加

| scope | cleanup後baseline | current stored | growth | share |
| --- | ---: | ---: | ---: | ---: |
| project current | 91,963,114 | 108,655,569 | 16,692,455 | 2.0% |
| investigation input/output | 1,067,280,055 | 1,705,855,310 | 638,575,255 | 75.4% |
| investigation bundle | 370,079,800 | 548,831,520 | 178,751,720 | 21.1% |
| collector projection | 26,488,540 | 39,945,183 | 13,456,643 | 1.6% |
| total | 1,555,811,509 | 2,403,287,582 | 847,476,073 | 100.0% |

新規7,631 runsのhistory payloadは830,783,618 bytesで、1 run平均は約108,870 bytesです。current stateの
増加16,692,455 bytesはrun平均へ含めません。

現行cleanup policyのdry-run結果:

| scope | before | after | saved |
| --- | ---: | ---: | ---: |
| project current | 108,655,569 | 108,643,445 | 12,124 |
| investigation input/output | 1,705,855,310 | 1,705,815,186 | 40,124 |
| investigation bundle | 548,831,520 | 548,971,104 | -139,584 |
| collector projection | 39,945,183 | 39,945,181 | 2 |
| total | 2,403,287,582 | 2,403,374,916 | -87,334 |

bundleの増加は現行形式への再直列化によるもので、semantic improvementや削減ではありません。snapshotの
`evaluation_diff_run_count`は0でした。

## no-change runの確認

新規runのinput/outputを既存`evaluation_snapshot()`で比較した結果:

- no-change: 7,117 / 7,631 runs（93.3%）
- no-change runのpayload: 729,336,829 / 830,783,618 bytes（87.8%）
- week 29: 87.7%、week 30: 88.4%

ただし、no-change群でも次のfieldは更新されています。

- `category`: 849 runs
- `technical_interest_level`: 1,005 runs
- `summary`: 204 runs
- `features`: 184 runs
- `name`: 160 runs
- `tagline`: 150 runs
- `llm_output`: 7,049 runs
- `relation_evidence`: 3,397 runs

したがって、ownership / service中心の既存evaluationだけでfull snapshotをstub化すると、現在のprofile、technical
interest、LLM出力、relation履歴を失います。stubを採用する場合は、保存対象全体に対する別のsemantic contractが必要です。

## 方式比較

### input/output差分

top-level key単位でinputをfull保存し、outputを`set` / `unset`差分にした試算です。元snapshotを完全復元できますが、
大きなnested fieldが変わるとfield全体を保持します。

| cohort | current snapshot | input + output delta | saved |
| --- | ---: | ---: | ---: |
| all 22,717 runs | 1,705,855,310 | 1,458,772,084 | 247,083,226（14.5%） |
| new 7,631 runs | 638,575,255 | 551,061,057 | 87,514,198（13.7%） |

schemaとreaderを複雑化する割に、30日換算増加を十分には抑えません。nested JSON patch等は別形式になるため、
この試算から効果を推測しません。

### JSON圧縮

各JSON valueを独立にzlib level 6で圧縮したread-only試算です。

| cohort | raw | zlib | saved |
| --- | ---: | ---: | ---: |
| current全対象 | 2,403,287,582 | 413,869,293 | 1,989,418,289（82.8%） |
| new history | 830,783,618 | 140,547,007 | 690,236,611（83.1%） |

圧縮率は高い一方、SQLite JSON functionや直接SQL監査を失い、全reader、replay、export、maintenanceでdecodeが
必要になります。圧縮対象をhistory tableだけに限定するか、history DB分離と同時に扱うかを含めて設計が必要です。

### retention

既存`prune-investigation-history`をread-onlyで使い、各historical project keyの最新3 runsを必ず保持した上限試算です。
merge aliasをcanonical project単位へ束ねていないため、byte-aware retentionの最終値ではありません。

| retention | candidate runs | estimated payload bytes |
| --- | ---: | ---: |
| 30 days | 2,420 | 178,115,892 |
| 21 days | 10,134 | 963,253,165 |
| 14 days | 12,136 | 1,160,367,161 |
| latest 3 only | 12,155 | 1,166,170,839 |

30日保持は直近運用の調査材料を残せますが、現在時点の削減は小さく、同じ増加率なら継続的なpruneが必要です。
21日以下では削減効果が大きい一方、月単位の比較履歴を失います。

## 次の判断

次の実装は独立taskで一方式ずつ扱います。

1. history JSON圧縮のreader/replay/SQL監査への影響と実測read costを確認する
2. 直接監査を優先する場合は、canonical project単位のbyte-aware retentionを設計する
3. no-change stubは、profileとrelationを含むsemantic contractを先に定義できる場合だけ再検討する
4. top-level input/output差分化は現時点の候補から外す

正本DBへの適用は、採用方式のtemp DB test、正本DB複製dry-run、backup、writer停止を確認してから別途承認を得ます。
