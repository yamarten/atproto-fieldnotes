# Project Registry Roadmap

確定済みの保存形や責務分担は [docs/project-record.md](project-record.md) と [docs/registry-internals.md](registry-internals.md) に移しました。

この文書は、まだ本体仕様に取り込んでいない展望と TODO を置くためのメモです。

## 目的の優先順位

1. 紹介するかどうかを安定して判断できる
2. 紹介文を作るための核データを保てる
3. 運用上必要な review / merge / evidence を保てる
4. 将来 app record を生成したくなった時に、必要情報が足りない状態を避ける

## 非目的

少なくとも現段階では、次は主目的にしません。

- app lexicon 完全互換を今すぐ満たすこと
- 不確かな app metadata で `app` を埋めること
- export の都合だけで core や ops の設計を歪めること

紹介フローを主導に置き、その延長で app export 可能性を確保する、という順序を維持します。

## 将来の論点

### ステータス系

現行の canonical は `service_status` です。

将来的には、次の別軸を分けたくなる可能性があります。

- `unreleased`
- `paused`
- `unmaintained`

候補:

- `service_status`
  - 到達性、公開状態、招待制、死活
- `app.status`
  - app record 向けの公開・保守状態

現時点では分けません。必要になった時点で `app.status` を追加します。

### app metadata の拡張

`app` は今は低優先度です。確かな情報だけを入れ、空 object を許します。

将来、次を追加で整備する余地があります。

- `links[]`
- `images[]`
- `langs[]`
- `lexicons.produces[]`
- `lexicons.consumes[]`
- `account_indicators[]`

### 境界項目の再評価

現時点では evidence として扱っているが、将来 app metadata に昇格するかもしれない項目です。

- `inspired_by`
- `features`
- `developer_accounts`

### provider 別 convenience field

次は evidence から再構成できるなら、将来的に縮退候補です。

- `github_repo_urls`
- `tangled_repo_urls`

## TODO

- `service_urls` / `resource_urls` を `app.links[]` とどう統合するか決める
- `service_status` に `unreleased` を追加するか判断する
- `app.status` を separate field として導入する必要が出たら、`service_status` からの写像を定義する
- `github_repo_urls` / `tangled_repo_urls` を provider 別 convenience field のまま残すか、evidence から導出に寄せるか決める
- `inspired_by` を app metadata として昇格させるか再判断する
