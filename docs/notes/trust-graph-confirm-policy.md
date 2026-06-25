# Trust Graph Confirm Policy

> 作業用の設計メモです。現行実装の正本ではありません。
> 目的は、自動 confirmed 昇格を identifier kind ごとの ad hoc rule から、
> identifier 間の有向 trust graph 評価へ寄せることです。

## 方針

将来の設計目標:

- identifier kind ごとの個別昇格条件を増やさない。
- `service_url`、`repo_url`、`handle`、`did`、`domain`、`collection` を同じ trust graph 上の node として扱う。
- evidence から identifier 間の有向 trust edge を作る。
- auto-confirm は、既存 confirmed identifier と candidate identifier の間に、許可された相互 trust path が成立した場合だけ行う。
- 片方向 link、自称、usage/compatibility mention は candidate / review / investigation material に留める。
- 片方向 edge を逆向きの confirmed 根拠として使わない。
- protocol evidence も原則として有向 edge の片側にすぎない。handle resolution、DID document、atproto record などは相互 trust path の部品として扱い、片方向だけでは auto-confirm しない。
- confirmed identifier ごとに、成立した trust path を dry-run / explain で出せるようにする。

## Graph 外の入力

実装済み:

- manual seed / explicit confirm decision は trust edge ではなく、graph 外の manual decision として confirmed identifier にできる。
- block / defer は自動 confirm より優先する。

将来の設計目標:

- manual decision と自動確認済み identifier を区別して保存・説明する。
- manual decision から広がる auto-confirm は、相互 trust path が成立した candidate identifier だけに限定する。
- canonicalization / same-identifier transform は trust edge と分けて扱う。
  - same-host `http -> https`
  - trailing slash
  - clearly equivalent canonical URL

## Identifier Node

既存の保存schemaにおける identifier kind / role は [../project-record.md](../project-record.md) が正本です。このメモでは trust graph 上の node として次の意味に絞って扱います。

| Node | 定義 | 注意点 |
| --- | --- | --- |
| `service_url` | project が運営・提供する web/app/service surface のURL | docs、about、login、status pageなどの補助pageは service identity そのものではない |
| `repo_url` | project の source repository または canonical source package repository | fork、mirror、example、dependency repo は別物として扱う |
| `handle` | atproto handle 文字列 | 人間可読で再割当可能。DIDとの相互確認なしでは identity proof にしない |
| `did` | atproto account / actor のDID | 安定したprotocol identifier。handle、projectとの接続は別途必要 |
| `domain` | DNS/HTTP authority domain | handleやcollection authorityと文字列が似ていても同一視しない |
| `collection` | atproto collection NSID | compatibility/use/mention と ownership を分ける。lexicon IDやAPI namespace全般まで含めるかは未確定 |

`account` の扱い:

- このメモの方針では、`account` は trust graph node にしない。
- atproto account identity の最小単位は `did` とし、`handle` は時間で変わり得る label として別 node にする。
- `developer_account`、`project_account`、`brand_did` などの保存上のroleは、project内での役割を表すmetadataであり、identityそのものではない。
- `source` はどのcollector/evidenceから観測したかを表すprovenanceであり、identityそのものではない。
- UIや保存上は account として表示してよいが、trust graph evaluation では `handle` / `did` / role metadata / source provenance を分けて説明する。

`handle` と `did` を分ける理由:

- `handle` は domain-based の可読名で、DIDへ解決できても、DID側がそのhandleを主張していなければ相互確認にならない。
- `did` は protocol identity だが、そのDIDがprojectの開発者・運営者・brandであることは別の edge や manual decision が必要。

`handle`、`domain`、`collection` を分ける理由:

- `handle` は domain構文を使うが、atproto identity resolution の対象。
- `domain` はDNS/HTTP control の対象。
- `collection` の authority 部分は名前空間であり、domain/service ownershipとは別に確認する必要がある。
- hosted handle domain は affiliation を意味しない。例えば `xxx.sprk.so` handle が解決できても、その account を Spark 関係者とは扱わない。

## Trust Edge 候補

次は candidate identifier 間、または confirmed identifier と candidate identifier の間に作る有向 trust edge の候補です。edge名はレビュー用の仮ラベルであり、永続化schemaの確定名ではありません。個別の採用可否と成立条件は未確定です。

| From | To | Edge候補 | 成立条件候補 |
| --- | --- | --- | --- |
| `service_url` | `repo_url` | `service_links_repo` | service page が first-party source/repository link として repo を示す |
| `repo_url` | `service_url` | `repo_links_service` | repo metadata homepage、package metadata、またはREADMEの明示的な公式site/source文脈が service URL または same service family を示す。README内の任意URLだけでは成立しない |
| `service_url` | `handle` | `service_links_handle` | service page が official account/profile link として handle を示す |
| `service_url` | `did` | `service_links_did` | service page が official account/profile link として DID を示す |
| `handle` | `did` | `handle_resolves_did` | handle resolution が DID を返す |
| `did` | `handle` | `did_document_claims_handle` | DID document が `alsoKnownAs` などで handle を示す。DID documentそのもの、またはDID document相当の正規解決結果だけを見る |
| `did` | `collection` | `did_publishes_schema` | DID の atproto repo に対象 collection の `com.atproto.lexicon.schema` record がある |
| `collection` | `domain` | `collection_authority_domain` | collection NSID から authority domain を導出できる |
| `domain` | `collection` | `domain_publishes_lexicon_record` | domain の `_lexicon` または同等のlexicon authority endpointが対象 collection を公開している |
| `domain` | `handle` | `domain_exact_handle` | handle文字列がdomainと完全一致する。hosted subdomainやsuffix一致は含めない |
| `domain` | `service_url` | `domain_serves_service` | domain root、canonical、same-site redirect などが service URL を示す |
| `service_url` | `domain` | `service_claims_domain` | 要検討。想定は service が well-known / signed metadata / canonical metadata で authority domain を示す場合だが、通常HTML本文や自称文言では成立しない |
| `repo_url` | `handle` | `repo_links_handle` | repo metadata / README が official handle を示す |
| `repo_url` | `did` | `repo_links_did` | repo metadata / README が official DID を示す |
| `did` | `repo_url` | `did_links_repo` | Tangled/PDS repo record など、DIDに紐づく protocol record が repo を示す |
| `repo_url` | `did` | `repo_claims_did` | repo-host account、signed metadata、READMEの明示的identity表示などが DID を示す |
| `service_url` | `service_url` | `service_canonical` | same-host redirect、canonical URL、same-site canonical などが別URLとの同一service関係を示す |

## Candidate / Review に留める根拠

次の evidence は trust edge の候補にはなるが、単独では auto-confirm に使わない。機械的に official / incidental / dependency / mirror / compatibility を区別しにくいため、相互 edge、source independence、手動確認、または追加 metadata が必要。

- `repo -> service` だけ
- `service -> repo` だけ
- `repo -> collection` の lexicon JSON 存在だけ
- authority domain と service host の一致だけ
- Bluesky profile link だけ
- actor/profile description 内の URL
- repo README 内の外部 URL
- repo fork/upstream関係
- repo mirror/canonical関係
- same root domain / same organization token
- package metadata の homepage/source URL
- collection/schema/doc metadataからsource repoを指しているように見える値

## 例

自動 confirm 可能な形:

- confirmed `repo_url` と candidate `service_url` の間で、`repo -> service` と `service -> repo` の両方が独立 source から成立する。
- confirmed `handle` と candidate `did` の間で、`handle -> did` と `did -> handle` の両方が成立する。

candidate / review に留める形:

- service page が既存 repo に link しているが、repo 側から同じ service family への参照がない。
- repo README が service URL を指しているが、service 側から repo への first-party link がない。
- GreenGale のような互換 service が WhiteWind の collection を文書で言及している。

## 未接続

- trust edge の schema
- policy table
- trust graph evaluator
- dry-run auditor
- existing heuristic から trust graph への migration path
- confirmed identifier ごとの trust path explain UI/CLI
