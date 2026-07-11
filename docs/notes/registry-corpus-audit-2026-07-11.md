# Registry Corpus Audit (2026-07-11)

調査品質corpusを作るための時点付き集計メモです。現行仕様の正本ではありません。2026-07-10のimmutable DB
snapshotをread-onlyで集計し、正本DBは変更していません。

## このnoteの寿命

- 使いどき: operator correction fixtureやmanual gold setを追加する時に、decision分布、bundle相関、sanitize境界を確認する
- 消しどき: fixture manifestまたはmanual gold文書が各caseの由来、採否、sanitize確認を直接保持し、この集計を参照せずcorpusを更新できるようになった時
- 後継corpusへ移した判断はinvestigation modelへ反映し、この時点集計だけを履歴目的で残さない

## Decision / bundle相関

集計基準時刻は2026-07-11T00:00:00+00:00です。未clearかつ未expireのdecisionだけをactiveとしました。

- project: 3,239
- latest investigation runを持つproject key: 3,468
  - merge/delete前のhistorical project keyをrun historyが保持するため、現行project数より多い
- active decision: 83件 / 59 project

| kind | decision | project | latest runあり | targetがlatest bundle内に完全一致 |
|---|---:|---:|---:|---:|
| `block_identifier` | 14 | 10 | 14 | 5 |
| `confirm_identifier` | 2 | 1 | 2 | 2 |
| `defer_identifier` | 28 | 22 | 28 | 19 |
| `separate_project` | 39 | 33 | 39 | 0 |

bundle相関はtarget文字列の再帰的な完全一致であり、ownershipや因果関係を証明する指標ではありません。
block/defer/confirmはsingle-project replay fixture候補を選ぶ用途には使えます。separateのtargetは相手project keyで、
single-project bundleには通常現れないため、merge/split用のmanual gold setで扱います。

## 最初のcorpus

`operator-correction-benchmark-v1.json`へblock/defer/confirmを1件ずつ追加しました。実snapshotから問題の形を
選びましたが、fixtureはsynthetic project key、必要な公開identifier、最小project patchだけで再構成しています。

- tenant serviceのblock
- 無関係repositoryのdefer
- service candidateのoperator confirm維持

block caseはtest内でdecisionを除去すると1 regressionを検出します。既存6 replay caseと合わせ、通常の
`benchmark-investigation`は9 caseを実行します。これはprecision、merge/split、紹介可否、operator所要時間を
採点するmanual gold setの代替ではありません。

## Fixture sanitize rule

自動exportはsecret key、authorization/cookie/header、raw HTML/body/readme/text extract、prompt/command/endpoint、
local absolute pathを削除またはredactし、HTTP(S) URLのuserinfo/query/fragmentを除去します。自動exportには
manual review必須markerを付けます。

コミットするfixtureはさらに人が縮約し、次だけを残します。

- 誤判定の再現に必要な公開URL/NSIDと最小evidence
- synthetic project keyと固定の非実時刻
- 期待するevaluation field

run ID、実project key、取得時刻、handle/DID、自由記述、raw response、ローカルpath、認証情報は、再現に必須で
ない限り含めません。handle/DID等が将来caseの本質なら、公開性と必要性を個別確認してから追加します。
