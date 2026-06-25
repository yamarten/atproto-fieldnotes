# Trust Graph Confirm Policy

> 作業用の設計メモです。現行実装の正本ではありません。
> 目的は、自動 confirmed 昇格を identifier kind ごとの ad hoc rule から、
> identifier 間の有向 trust graph 評価へ寄せることです。

## 方針

将来の設計目標:

- identifier kind ごとの個別昇格条件を増やさない。
- `service_url`、`repo_url`、`account`、`did`、`nsid` を同じ trust graph 上の node として扱う。
- evidence から identifier 間の有向 trust edge を作る。
- auto-confirm は、manual/operator anchor または既存 confirmed identifier と candidate identifier の間に、許可された相互 trust path が成立した場合だけ行う。
- 片方向 link、自称、usage/compatibility mention は candidate / review / investigation material に留める。
- 片方向 edge を逆向きの confirmed 根拠として使わない。
- cryptographic/atproto record など片方向でも強い検証になる根拠は、通常 link evidence と混ぜず、明示的な例外 edge type として扱う。
- confirmed identifier ごとに、成立した trust path を dry-run / explain で出せるようにする。

## Anchor

実装済み:

- manual seed は operator anchor として confirmed identifier にできる。
- block / defer は自動 confirm より優先する。

将来の設計目標:

- operator anchor と自動確認済み identifier を区別して保存・説明する。
- anchor から広がる auto-confirm は、相互 trust path が成立した candidate identifier だけに限定する。

## 使わない根拠

confirmed identifier への自動昇格に使わない:

- `repo -> service` だけ
- `service -> repo` だけ
- `service -> NSID` の本文言及だけ
- `repo -> NSID` の lexicon JSON 存在だけ
- authority domain と service host の一致だけ
- HTTP 200 / live surface だけ
- service page 上の atproto marker、alias token、authority domain の自称だけ
- Bluesky profile link だけ
- OAuth `repo:` permission だけ
- source usage / compatibility mention だけ

## 例

自動 confirm 可能な形:

- confirmed/manual `repo_url` と candidate `service_url` の間で、`repo -> service` と `service -> repo` の両方が独立 source から成立する。
- confirmed `did` が atproto repo record として対象 `nsid` の schema を公開しており、その `did` が project の confirmed developer DID として受け入れ済み。

candidate / review に留める形:

- service page が既存 repo に link しているが、repo 側から同じ service family への参照がない。
- repo README が service URL を指しているが、service 側から repo への first-party link がない。
- GreenGale のような互換 service が WhiteWind の NSID を文書で言及している。

## 未接続

- trust edge の schema
- policy table
- trust graph evaluator
- dry-run auditor
- existing heuristic から trust graph への migration path
- confirmed identifier ごとの trust path explain UI/CLI
