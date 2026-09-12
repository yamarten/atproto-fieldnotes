# App Record Export

この文書は、保存済みprojectを第三者listingの`community.lexicon.app.entry` JSONへ投影するexport機能の
契約を定めます。現行実装はread-only validation reportと、明示したpublication manifestからのentry JSON生成を
提供します。生成したJSONのPDSへの送信は、用途に応じた別ツールが担当します。

処理は二段階です。まず保存済みの確定情報をtarget schemaへ投影し、不足情報と変換上の制約をreportします。
その結果を確認して公開対象と作成時刻をmanifestで明示すると、検証を通ったentry JSONを生成できます。
候補identifierの確定やregistryの補正は、この処理では行いません。

## Target schema

targetは`community.lexicon.app.entry`だけです。Lexicon Communityの
[canonical repository](https://tangled.org/lexicon.community/lexicons)にあるrevision
`c8552ebbf7f2d1cc13e14870f6908cdc29796008`をvalidation contract v1の基準にします。

`community.lexicon.app.profile`はapp自身のDIDが公開するcanonical profileです。必要になる例が非常に稀で、
必要なappは小さなrecordを自身で作成できるため、registry側で生成・検証・保守する費用に見合いません。
このexportの出力fieldは第三者listingのentry JSONに限定します。profile recordと`entry.profileDid`は、app側が
必要に応じて管理する情報です。

targetのrequired fieldは`name`、`links`、`createdAt`です。`createdAt`はentry作成時刻であり、projectの
`first_seen_at`や外部projectの作成時刻ではありません。validation reportでは
`publication_context_required`へ分け、export時にmanifestから供給します。

## 入力境界

使用する入力:

- `projects.name`、`projects.website_url`、`projects.tracking_state`などのcanonical scalar
- `profile_json.core.summary`
- `profile_json.app.tags`、`profile_json.app.platforms`
- currentかつ`classification=confirmed`のidentifier
- confirmedな`resource_url_records`
- 未確定interop fieldの診断に限るconfirmed relation

次の入力は候補の存在を説明する診断専用の材料です。

- candidate、derived、heuristic identifier
- former、non-production identifier
- noise、block済みidentifier
- current profileへ採用されていないLLM出力
- 調査履歴snapshotだけに残る値

validationとJSON生成はread-onlyで動作し、confirmedへの昇格は既存のreview flowが担当します。

## Field projection

| target field | source | 扱い |
| --- | --- | --- |
| `name` | `projects.name` | required。targetのbyte/grapheme上限を検査 |
| `description` | `profile_json.core.summary` | optional。target上限を検査 |
| `links` | canonical Website、confirmed repository/service/resource | required。source roleを`community.lexicon.app.defs` tokenへ変換 |
| `tags` | `profile_json.app.tags` | optional。件数と文字列上限を検査 |
| `platforms` | `profile_json.app.platforms` | optional。承認済みの既知platform tokenだけへ変換 |
| `createdAt` | 将来のpublication context | requiredだがDB不足とは分ける |
| `updatedAt` | 将来のpublication context | 初回entryでは省略 |

`webApp`と`distribution`のlinkはURI自体を保持し、target roleを確定できないためroleを省略してlossを
報告します。link数や文字列上限を超える値は`unconvertible`として示します。

`images`、`status`、`langs`、`lexicons`、`accountIndicators`はoptionalです。現行reportはsource contractが
設定されていないことを`specification_uncertain`として示します。entry JSON生成はrequired fieldを完了条件とし、
これらのfieldは生成するJSONから省略します。

Python標準libraryで確実に判定できる範囲として、UTF-8 byte上限とUnicode code point数を検査します。
code point数がgrapheme上限以下ならgrapheme上限も満たします。非ASCII文字列でcode point数だけが上限を
超える場合は、正確なgrapheme判定が必要な`specification_uncertain`に分類します。

## Readiness

| 値 | 意味 |
| --- | --- |
| `needs_publication_context` | 公開scope、`name`、`links`は揃い、将来exportが`createdAt`を供給すればよい |
| `missing_required` | `name`またはconfirmed sourceから作れる`links`がない |
| `insufficient_evidence` | required候補はあるがconfirmedではない。現行reportではmissing reasonにも併記 |
| `unconvertible` | target上限超過など、値を損失なく変換できない |
| `scope_uncertain` | `name_only`、category未設定、または`out_of_scope`で公開対象判断が必要 |
| `not_applicable` | `category=reject`または`tracking_state=ignore` |

publication contextを持たないreadiness reportでは`record_ready`はfalseです。`content_ready`は
`needs_publication_context`だけでtrueになります。公開scopeをfield completenessより優先し、`reject` / `ignore`は
`not_applicable`になります。最終的な生成可否はpublication manifestを加えたexport時に判定します。

## Validation reportと再現性

`validate-app-record-export`は保存済みDBだけを入力とし、SQLiteを`mode=ro`で開いて
`PRAGMA query_only=ON`を設定します。出力は指定したreport JSONです。

```bash
project-registry validate-app-record-export \
  --db-path data/survey.db \
  --output .tmp/app-record-readiness/report.json
```

reportはproject key順で、次を含みます。

- target NSID、schema revision、validation contract version
- 入力tableの関連fieldから計算した`input_fingerprint`
- readinessとfield statusの集計
- projectごとのcandidate record、scope、missing/uncertain/unconvertible、除外入力
- 各candidate fieldを支えるsource fieldまたはnormalized row
- report全体の`content_hash`

report内容をDB snapshotとcontractだけで決めることで、同じ入力から同じreportとhashを生成します。件数は
snapshotの観測値です。reportは`.tmp/`へ置く一時的な監査結果です。

## Publication manifest

export対象をcategoryやtracking stateから暗黙に一括選択しません。operatorがmanifest version 1でproject keyを
列挙します。同じmanifestにpublication contextを置くため、同じDB snapshotとmanifestから同じ成果物を再生成
できます。

```json
{
  "manifest_schema_version": 1,
  "project_keys": ["proj:000bc4aef27f"],
  "publication_context": {
    "createdAt": "2026-09-04T12:00:00Z"
  }
}
```

`project_keys`は空でない一意な完全project keyの配列です。`createdAt`は64文字以内の有効なRFC 3339 datetimeで、
UTCの`Z`表記だけを受理します。時刻は全recordへ同じ値を付けます。offsetの自動変換や`first_seen_at`へのfallbackは
行いません。

readinessが`needs_publication_context`のprojectだけを通常出力できます。`scope_uncertain`を公開する場合は、
`project_keys`に加えて`allow_scope_uncertain_project_keys`にも同じkeyを明示します。このoverrideは公開scopeだけを
解決し、required field不足や変換不能を許可しません。`not_applicable`はoverrideできません。

manifestに未知field、重複key、未選択keyのscope override、存在しないproject、または1件でも不適格なprojectが
あればexport全体を失敗させ、成果物を書きません。

## Entry JSON生成

```bash
project-registry export-app-records \
  --db-path data/survey.db \
  --manifest .tmp/app-record-export/manifest.json \
  --output .tmp/app-record-export/records.json
```

成果物はproject key順の裸の`records`配列を持ちます。各要素はそのままPDS書込み処理へ渡せるrecordで、
`$type`、`name`、`links`、`createdAt`を必ず持ち、検証済みのmapped optional fieldだけを含みます。要素ごとの
`project_key`や`collection` wrapperは付けません。target collectionも成果物には記録しません。このコマンドは
`community.lexicon.app.entry`だけを対象とし、target指定optionは持ちません。source projectとの対応が必要な場合は、
正規化済み`publication_manifest.project_keys`と`records`の同じordinalを使います。source contract未設定fieldは
省略します。成果物にはDB入力fingerprint、manifest、`content_hash`を含みます。

DBはread-onlyで、出力先とDBが同一pathなら拒否します。PDS用rkeyはtarget schemaの`tid`ですが、この機能は
PDS送信もrkey生成も行いません。後段の送信ツールは固定collectionへ裸の`records`を送信します。
