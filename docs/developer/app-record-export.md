# App Record Export

この文書は、保存済みprojectを第三者listingの`community.lexicon.app.entry` JSONへ投影するexport機能の
契約を定めます。現行実装はread-only validation reportまでを提供し、将来のJSON生成も同じfield mappingと
evidence境界を使います。生成したJSONのPDSへの送信は、用途に応じた別ツールが担当します。

この契約は、export候補の根拠とreadinessの意味を実装・review間で揃えるための正本です。将来計画は
`docs/roadmap.md`、現在接続済みの範囲は`docs/status.md`で管理します。

## Target schema

targetは`community.lexicon.app.entry`だけです。Lexicon Communityの
[canonical repository](https://tangled.org/lexicon.community/lexicons)にあるrevision
`c8552ebbf7f2d1cc13e14870f6908cdc29796008`をvalidation contract v1の基準にします。

`community.lexicon.app.profile`はapp自身のDIDが公開するcanonical profileです。必要になる例が非常に稀で、
必要なappは小さなrecordを自身で作成できるため、registry側で生成・検証・保守する費用に見合いません。
このexportの出力fieldは第三者listingのentry JSONに限定します。profile recordと`entry.profileDid`は、app側が
必要に応じて管理する情報です。

targetのrequired fieldは`name`、`links`、`createdAt`です。`createdAt`はentry作成時刻であり、projectの
`first_seen_at`や外部projectの作成時刻ではありません。将来のexportが公開時に供給する値として
`publication_context_required`へ分けます。

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

`record_ready`はJSON生成が接続されるまでfalseです。`content_ready`は`needs_publication_context`だけで
trueになります。公開scopeをfield completenessより優先し、`reject` / `ignore`は`not_applicable`になります。

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

## Entry JSON生成

次段階では、公開対象として選んだprojectの`candidate_record`へpublication contextの`createdAt`を加え、固定した
schema revisionで検証済みのentry JSONを生成します。そのために必要なTODOは、公開対象のcategory / tracking
state、`scope_uncertain`の扱い、`createdAt`の付与規則、複数recordの出力形式を決めることです。

required fieldを満たすJSON生成までをこのexport機能の責務とします。上記のsource contract未設定fieldは省略し、
PDSへの送信は別ツールが担当します。
