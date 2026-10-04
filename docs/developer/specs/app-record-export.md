# App Record Export

この文書は、保存済みprojectを第三者がapp一覧へ掲載するための`community.lexicon.app.entry` JSONへ変換するexport機能の契約を定めます。現行実装は、DBを変更せず出力可否を調べるvalidation reportと、公開対象・作成時刻を明示した入力（publication manifest）からのentry JSON生成を提供します。生成したJSONのPDSへの送信は、用途に応じた別ツールが担当します。

処理は二段階です。まず保存済みの確定情報をtarget schemaへ投影し、不足情報と変換上の制約をreportします。その結果を確認して公開対象と作成時刻をmanifestで明示すると、検証を通ったentry JSONを生成できます。候補identifierの確定やregistryの補正は、この処理では行いません。

## Target schema

targetは`community.lexicon.app.entry`だけです。Lexicon Communityの[canonical repository](https://tangled.org/lexicon.community/lexicons)にあるrevision `c8552ebbf7f2d1cc13e14870f6908cdc29796008`をvalidation contract v1の基準にします。

このexportは第三者による掲載用のentry JSONを生成します。app自身のDIDが公開する`community.lexicon.app.profile`と、それを参照する`entry.profileDid`は、app側が必要に応じて管理します。

targetのrequired fieldは`name`、`links`、`createdAt`です。`createdAt`はentry作成時刻であり、projectの`first_seen_at`や外部projectの作成時刻ではありません。validation reportでは`publication_context_required`へ分け、export時にmanifestから供給します。

## 入力境界

使用する入力:

- `projects.name`、`projects.website_url`、`projects.tracking_state`などのcanonical scalar
- `profile_json.core.summary`
- `profile_json.app.tags`、`profile_json.app.platforms`
- 現行のrole付き関係として保持する`classification=confirmed`のidentifier
- confirmedな`resource_url_records`
- 未確定interop fieldの診断に限るconfirmed relation

次の入力は候補の存在を説明する診断専用の材料です。

- candidate、derived、heuristic identifier
- former、non-production identifier
- noise、block済みidentifier
- 現在の保存済みprofileへ反映されていないLLM出力
- 調査履歴snapshotだけに残る値

validationとJSON生成はread-onlyで動作し、confirmedへの昇格は既存のreview flowが担当します。

## Field projection

| 出力field | 入力元 | 扱い |
| --- | --- | --- |
| `name` | `projects.name` | required。targetのbyte/grapheme上限を検査 |
| `description` | `profile_json.core.summary` | optional。target上限を検査 |
| `links` | canonical Website、confirmed repository/service/resource | required。保存したlinkのroleを`community.lexicon.app.defs` tokenへ変換 |
| `tags` | `profile_json.app.tags` | optional。件数と文字列上限を検査 |
| `platforms` | `profile_json.app.platforms` | optional。承認済みの既知platform tokenだけへ変換 |
| `createdAt` | export時にmanifestで指定する`publication_context` | requiredだがDB不足とは分ける |
| `updatedAt` | 将来の更新時に指定するpublication context | 現行の初回entry生成では省略 |

`webApp`と`distribution`のlinkはURI自体を保持し、target roleを確定できないためroleを省略してlossを報告します。link数や文字列上限を超える値は`unconvertible`として示します。

`images`、`status`、`langs`、`lexicons`、`accountIndicators`はoptionalです。現行reportは、DBのどの情報からどう変換するかの契約が未設定であることを`specification_uncertain`として示します。entry JSON生成はrequired fieldを完了条件とし、これらのfieldは生成するJSONから省略します。

Python標準libraryで確実に判定できる範囲として、UTF-8 byte上限とUnicode code point数を検査します。code point数がgrapheme上限以下ならgrapheme上限も満たします。非ASCII文字列でcode point数だけが上限を超える場合は、正確なgrapheme判定が必要な`specification_uncertain`に分類します。

## Readiness

readinessは、保存済みprojectをentryとして出力するための情報充足・適格性の判定です。公開時に指定する作成時刻の不足と、DBにあるproject情報の不足を分けて返します。

| 値 | 意味 |
| --- | --- |
| `needs_publication_context` | 公開対象としての条件、`name`、`links`は揃い、export時に`createdAt`を供給すればよい |
| `missing_required` | `name`またはconfirmed sourceから作れる`links`がない |
| `insufficient_evidence` | required候補はあるがconfirmedではない。現行reportではmissing reasonにも併記 |
| `unconvertible` | target上限超過など、値を損失なく変換できない |
| `scope_uncertain` | `name_only`、category未設定、または`out_of_scope`で公開対象判断が必要 |
| `not_applicable` | `category=reject`または`tracking_state=ignore` |

publication contextを持たないreadiness reportでは`record_ready`はfalseです。`content_ready`は`needs_publication_context`だけでtrueになります。公開scopeをfield completenessより優先し、`reject` / `ignore`は`not_applicable`になります。最終的な生成可否はpublication manifestを加えたexport時に判定します。

## Validation reportと再現性

`validate-app-record-export`は保存済みDBだけを入力とし、SQLiteを`mode=ro`で開いて`PRAGMA query_only=ON`を設定します。出力は指定したreport JSONです。

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

report内容をDB snapshotとcontractだけで決めることで、同じ入力から同じreportとhashを生成します。件数はsnapshotの観測値です。reportはGit管理外に置く一時的な監査結果です。

## Publication manifest

export対象は、operatorがmanifest version 1で列挙したproject keyです。同じmanifestにpublication contextを置くため、同じDB snapshotとmanifestから同じ成果物を再生成できます。

```json
{
  "manifest_schema_version": 1,
  "project_keys": ["proj:000bc4aef27f"],
  "publication_context": {
    "createdAt": "2026-09-04T12:00:00Z"
  }
}
```

`project_keys`は空でない一意な完全project keyの配列です。`createdAt`は64文字以内の有効なRFC 3339 datetimeで、UTCの`Z`表記だけを受理します。時刻は全recordへ同じ値を付けます。offsetの自動変換や`first_seen_at`へのfallbackは行いません。

readinessが`needs_publication_context`のprojectだけを通常出力できます。`scope_uncertain`を公開する場合は、`project_keys`に加えて`allow_scope_uncertain_project_keys`にも同じkeyを明示します。このoverrideは公開scopeだけを解決し、required field不足や変換不能を許可しません。`not_applicable`はoverrideできません。

manifestに未知field、重複key、未選択keyのscope override、存在しないproject、または1件でも不適格なprojectがあればexport全体を失敗させ、成果物を書きません。

## Entry JSON生成

```bash
project-registry export-app-records \
  --db-path data/survey.db \
  --manifest .tmp/app-record-export/manifest.json \
  --output .tmp/app-record-export/records.json
```

成果物はproject key順の裸の`records`配列を持ちます。各要素はそのままPDS書込み処理へ渡せるrecordで、`$type`、`name`、`links`、`createdAt`を必ず持ち、検証済みのmapped optional fieldだけを含みます。要素ごとの`project_key`や`collection` wrapperは付けません。target collectionも成果物には記録しません。このコマンドは`community.lexicon.app.entry`だけを対象とし、target指定optionは持ちません。source projectとの対応が必要な場合は、正規化済み`publication_manifest.project_keys`と`records`の同じordinalを使います。source contract未設定fieldは省略します。成果物にはDB入力fingerprint、manifest、`content_hash`を含みます。

DBはread-onlyで、出力先とDBが同一pathなら拒否します。PDS用rkeyはtarget schemaの`tid`ですが、この機能はPDS送信もrkey生成も行いません。後段の送信ツールは固定collectionへ裸の`records`を送信します。
