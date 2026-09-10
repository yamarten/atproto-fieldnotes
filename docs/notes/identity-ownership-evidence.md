# Identity and Ownership Evidence Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。契約は
> [investigation-model.md](../developer/investigation-model.md)、具体値は
> [registry-internals.md](../developer/registry-internals.md) を参照してください。

このnoteの以下の実装状況・将来目標は初期検討時点の記録です。現行repository roleは
`project_repository`で、信頼graphによる評価も接続済みです。旧relationを一括改名したものではありません。現行の判断と接続範囲は
[帰属と信頼](../developer/project-attribution-and-trust.md)と[Registry Internals](../developer/registry-internals.md)を参照してください。

## 初期検討時の保証関係

実装済み:

- operator-confirmed `owned_nsid`、公式 account の PDS schema record、accepted repo と accepted NSID の `defines_schema` を優先
- `defines_schema`, `uses_collection`, `mentions_collection` を分離
- test、fixture、vendor、generated、docs/example 内 schema を直接 ownership に使わない
- OAuth client metadata の `repo:` を `requests_permission` として保存
- repo と first-party app の独立 source による相互 link で `source_repository` を corroborate
- defer / block を relation-based 自動昇格より優先

保証しないもの:

- `uses_collection` や OAuth permission だけでは NSID ownership を保証しない
- NSID から authority domain を導けても service ownership までは保証しない
- repo README 内の複数 link は同一 source なので、独立 corroboration と数えない
- Tangled owner DID が一致しても、その repo が対象 project 本体とは限らない

## 未接続

- OAuth `include:` permission set の再帰解決。現状は candidate
- organization account と個人 developer account の委任関係
- monorepo 内の package/project 単位 ownership
- website が account/DID を cryptographically claim する一般的経路

## 未検証

- source code の operation 検出における false positive/negative
- generated schema を除外したことで失う正当な配布 repo の割合
- repo/app mutual link の移転・fork・買収時の扱い

## 将来目標

- domain、account、DID、repo、service、NSID の identity path を説明可能な graph として比較する
- ownership と利用関係を別 evaluation として表示する
- manual correction を優先したまま、新しい corroboration rule を corpus で検証する
