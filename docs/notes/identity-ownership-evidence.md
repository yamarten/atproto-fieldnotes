# Identity and Ownership Evidence Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。確定済みの pipeline 契約は
> [../investigation-model.md](../investigation-model.md)、現行 heuristic は
> [../registry-internals.md](../registry-internals.md) を参照してください。

## 論点

domain、account、DID、NSID、service、git repo の関係は同じ強さではありません。
ownership を文字列類似だけで自己強化しないため、どの観測が何を保証するかを分けます。

## 実装済み

`project_owned_lexicon_nsid()` は現在、次を優先します。

1. manual NSID seed そのもの
2. manual seed の親 namespace
3. confirmed developer account と authority domain の整合
4. confirmed developer DID が持つ lexicon schema record
5. confirmed service host と authority domain の整合
6. project identity token と namespace token の整合

builtin NSID は project 固有の根拠がなければ reference として扱います。GitHub repo collector は
lexicon-like JSON を見つけても、ownership が弱ければ project の `input_nsids` へ追加せず
`github_repo_lexicon_reference` log に留めます。

## 保証関係の整理

- `domain -> account`
  - `_lexicon.<authority_domain>` TXT から DID を解決できる場合は強い
- `account -> NSID`
  - account の PDS に schema record がある場合は強い ownership 根拠
- `NSID -> domain`
  - NSID から authority domain を導けるが、service ownership までは保証しない
- `domain -> service`
  - confirmed website / service URL の host は強い
- `service -> domain`
  - URLからの導出であり、ownership保証としては弱い
- `account -> domain`
  - handle suffix / root domain の整合は中程度
- `git repo -> domain`
  - README、homepage、first-party link はrepoとserviceを結ぶ補助根拠
- `service -> account`
  - 現状は confirmed service host とhandle domainの整合による推移的な確認
  - serviceがaccountを直接主張した証拠ではない

## `defines_schema` と `uses_collection`

将来のrelation evidenceでは、repo内のNSID出現を少なくとも次に分ける必要があります。

- `defines_schema`
  - lexicon schema JSONをproject自身が定義・配布している
  - ownershipの強い根拠
- `uses_collection`
  - `createRecord`、query、subscription、record sampleなどでcollectionを利用している
  - ATProto寄与度や依存関係の根拠だが、ownership根拠にはしない
- `mentions_collection`
  - README、docs、example、test fixtureなどの言及
  - candidate/referenceに留める

現行実装はこのrelationを汎用schemaとして保存していません。lexicon-like path、schema JSON、
investigation log、record sampleなどの個別fieldで近似しています。

## 未接続

- README / GitHub profile / project website から confirmed developer account への昇格
- first-party page上のBluesky profile linkを公式accountとして直接評価する処理
- repo内NSIDを `defines_schema / uses_collection / mentions_collection` に分類する解析
- atproto repository上のrecord usageをownershipから明示的に分離したrelation保存

## 未検証

- monorepo内で複数projectが別namespaceを定義するケース
- generated schema、vendored schema、test fixtureを `defines_schema` と誤認する割合
- organization accountと個人developer accountを同一主体として扱える条件

## 将来目標

- relation、subject、object、source URL / repo path、強度を持つevidenceへ整理する
- 複数sourceの一致でaccount / service / repoの帰属を昇格する
- ownership判定とATProto利用判定を別のevaluationとして比較可能にする
