# v1 Migration Trust Policy

この文書はv1 DBからschema v2へ移行する際のclassification継承を検討する作業用文書です。現行仕様の正本ではありません。確定した契約は`project-record.md`と`registry-internals.md`へ反映します。

## 現在の状態

実装済み:

- normalized identifier / relation / decision rowの無変更load/saveをlosslessに維持するruntime state
- candidate primary NSID、candidate owned NSID、candidate developer DIDをconfirmed用flat fieldへ再昇格しないhydrate
- `confirm_identifier` decisionの保存
  - Web/APIのcandidate confirm
  - interactive review queueのservice/repo confirm
- v1 migrationの`v1-quarantine-1` trust policy
  - 旧confirmedを由来付きcandidateへ降格
  - `manual_seed`と`confirm_identifier` decisionだけをoperator根拠としてconfirmed維持
  - active block対象を除外し、active defer対象をcandidateとして維持
  - untrusted primary website/repo/NSIDを強いflat fieldへhydrateしない
- migration report
  - 旧confirmed、維持confirmed、candidate降格、block除外、defer維持、primary scalar clear
  - active block衝突、根拠不明legacy confirmed、normalized row roundtrip diff
  - 上記hard acceptance conditionが非0なら移行失敗

動作確認済み:

- temp SQLiteのv1 fixtureでquarantine、operator confirmation維持、backup、dry-run、再実行
- candidate primary NSID / developer DIDの無変更load/save roundtrip
- migration後のnormalized row roundtrip diff 0
- 2026-06-18にworkspaceのv1 backup mirror 2540 projectsを現行migrationでdry-run
  - identifier 16364件、decision 113件、relation 0件
  - SQLite integrity `ok`
  - active block衝突、根拠不明legacy confirmed、normalized roundtrip diffはいずれも0
  - activeなidentifier defer 80件、期限切れまたは不正なdefer 0件

未検証:

- 本番稼働中のv1 DBに対するdry-runと通常運用DB置換

通常DB置換前の残件:

- workspaceの旧方式v2ミラーはテスト用であり、その`investigation_runs` / `collector_runs`は本番移行へ引き継がない。本番v1に保存されている調査履歴は通常のmigration対象になる
- `migrate-db`はCLI/Web/enrichを跨ぐ排他lockを取らない。全processを停止したoffline状態で実行する
- `registry_meta`はschema versionしか持たず、`v1-quarantine-1`適用済みかをDB単体で識別できない。旧方式v2ミラーは`already_current`になるため、入力DBを手順で取り違えない
- migration reportはDBへ永続化しないため、通常置換時のJSON出力をv1 backupと一緒に保管する

## 移行provenanceの削除

通常運用が十分安定し、v1 backupとmigration reportだけで旧状態を追跡できると判断した後は、identifier rowを残したままmigration専用metadataを削除できます。

dry-run:

```bash
project-registry prune-migration-provenance
```

実行:

```bash
project-registry prune-migration-provenance --execute
```

削除対象:

- `migration_source`
- `legacy_field`
- `legacy_classification`
- `trust_policy`
- `downgrade_reason`
- `trust_basis`
- `legacy_record` wrapper

`legacy_record`の中にあるrepo/account/manual seed metadataは通常metadataへ平坦化して残します。repository recordの`confidence`は旧値ではなく現在のidentifier classificationへ合わせ、quarantine candidateを再昇格させません。`project_key`、kind、value、role、classification、primary、provider、observed_atは変更せず、`project_identifiers` row自体は削除しません。

実行前条件:

- 通常運用DBが現行migrationから作成済み
- migration reportのhard acceptance conditionがすべて0
- quarantine candidateのreviewまたは再調査が十分進んでいる
- v1 backupを別途保持している

## 問題

構造上のmigrationが成功しても、旧heuristicがconfirmed fieldへ昇格させた誤情報をそのままv2のconfirmed identifierとして保存すると、ownership、duplicate/merge、service判定、次回collectorの入力が誤情報を相互補強します。

現行migrationは旧fieldを次のように扱います。

- `resolved_nsids`を`owned_nsid / confirmed`
- `primary_nsid`を`primary_nsid / confirmed`
- `service_urls`と`website_url`をservice identifierの`confirmed`
- `source_code_urls`と`source_code_url`をrepository identifierの`confirmed`
- `developer_accounts`をdeveloper identityの`confirmed`

この変換は旧判定の由来を評価せず、現在のruleで再検証もしません。

## 確認済み

2026-06-18時点のミラーv2 DBでは、同じprojectのmanual seedと値が一致しないconfirmed identifierが次の件数あります。

- developer account: 455
- service URL: 299
- primary website: 248
- owned NSID: 169
- primary NSID: 38
- source repository: 21
- source repository record: 20

manual seedとの一致は正しさの証明ではありません。上記は最低限の「独立した移行根拠が見えない件数」です。

activeなdefer対象はcandidateとして残り、block対象はidentifier setから除去されていました。このoperator decision優先は維持できます。

ミラーv2 DBの全2540 projectを変更せずloadしてsaveしたところ、candidateの`developer_did` 92件中59件が消えました。classification昇格はこの試験では発生しませんでしたが、normalized rowの完全なroundtrip invariantがまだ成立していません。

## 採用方針

### 1. 現行migrationで通常DBを置換しない

現在の`migrate-db` dry-runは件数、canonical scalar、foreign key、SQLite integrityを検証しますが、旧confirmedの意味的妥当性は検証していません。以下の対策が入るまで、成功reportを通常運用可能の判定に使いません。

### 2. Runtime codecをlosslessにする

normalized identifier、relation、decisionをloadして無変更saveした時、rowのclassification、role、primary、provider、metadata、observed_atが変化しないことを必須invariantにします。

flat runtime fieldからnormalized rowを再推測するだけでは、同じ値に複数role/classificationがある場合の情報を失います。runtime側にtypedなnormalized stateを保持し、legacy collector用flat viewはそこから導出する方向へ寄せます。collectorがidentifierを変更する操作は、追加・昇格・降格・削除を明示した差分としてintegrationへ渡します。

### 3. 旧confirmedをquarantineする

classificationへ`legacy`は追加しません。現在の4分類を維持し、現在の根拠でconfirmedを再現できない旧claimはcandidateへ降格します。

- `manual_seed / confirmed`は「operatorが入力した事実」だけを保証する
- manual seedと同じ値であっても、owned NSID、source repository、service ownershipを自動的にconfirmedにはしない
- active block対象は移行しない
- active defer対象はcandidateのまま維持する
- explicitなoperator confirmation、または現行ruleで再生可能な独立evidenceがあるclaimだけconfirmedにする
- 旧classification、旧field、移行version、降格理由はidentifier metadataへ残す

metadata例:

```json
{
  "migration_source": "schema_v1",
  "legacy_field": "service_urls",
  "legacy_classification": "confirmed",
  "trust_policy": "v1-quarantine-1",
  "downgrade_reason": "no current corroboration"
}
```

untrustedな`website_url`はproject scalarに残すとmergeやservice評価の強い入力になり得ます。代表候補としてidentifier tableには保持しますが、`projects.website_url`はconfirmed根拠が再構成できる場合だけ設定します。同様にcandidate primary repo/NSIDをconfirmed用flat fieldへhydrateしません。

### 4. Operator confirmationを履歴化する

現在のcandidate confirmはidentifierの現在値を更新しますが、confirmation自体の履歴を残しません。`confirm_identifier` decisionまたは同等のoperator assertionを追加し、再migration・再評価後も自動判定より優先できるようにします。

### 5. 移行後の自動伝播を止める

quarantine candidateは次を満たすまでownership corroboration、automatic merge、別identifierのconfirmed昇格へ使いません。

- 保存済みevidenceを現行evaluationでreplayしてconfirmedになった
- 対応sourceを再収集して独立evidenceを得た
- operatorが明示confirmした

再調査順はblock/deferを持つ誤判定corpus、unanchored primary、identity、service、NSID、repositoryの順とします。migration直後に全件merge/finalizeを実行しません。

## Migration report

dry-run reportへ少なくとも次を追加します。

- 旧confirmed件数とkind/role別内訳
- current ruleでconfirmedを維持した件数と根拠別内訳
- candidateへ降格した件数
- blockにより除外した件数、deferを維持した件数
- scalar primaryを空にしてcandidateへ移した件数
- operator confirmationで維持した件数
- migration前後のnormalized semantic diff
- migration後の無変更load/save diff

normalized rowの無変更roundtrip、active blockとの衝突0件、根拠不明confirmed 0件をplacement前のhard acceptance conditionにします。

## 実装順

1. normalized rowのlossless runtime representationとroundtrip test
2. `confirm_identifier`の保存・優先規則
3. v1 decoderに由来付きclaimを導入
4. conservative trust policyとmigration report
5. 実DB由来correction fixtureでblock/defer/false-confirm回帰
6. v1 backupからdry-runし、sanitized fixtureで判定diffを確認
7. 合格後にだけ通常DBを置換
8. 安定運用後、必要ならmigration provenance metadataだけを手動削除

## 非目標

- 旧confirmedを一括削除すること
- manual seedをownership証明とみなすこと
- migration中に外部APIへ接続すること
- migrationだけで全projectの正しさを確定すること
