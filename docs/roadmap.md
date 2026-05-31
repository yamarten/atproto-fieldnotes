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

- `paused`
- `unmaintained`

候補:

- `service_status`
  - 到達性、公開状態、招待制、死活
  - `unreleased` まではこの軸に含める
- `app.status`
  - app record 向けの公開・保守状態

現時点では分けません。必要になった時点で `app.status` を追加します。

### app metadata の拡張

`app` は今は低優先度です。確かな情報だけを入れ、空 object を許します。

将来、次を追加で整備する余地があります。

- `images[]`
- `langs[]`
- `lexicons.produces[]`
- `lexicons.consumes[]`
- `account_indicators[]`

`links[]` の初期実装は入っていますが、現時点では次の最小 role に限定しています。

- `website`
- `webApp`
- `sourceCode`
- `docs`
- `support`
- `status`
- `privacyPolicy`
- `termsOfService`

次は必要になった時だけ増やします。

### 境界項目の再評価

現時点では evidence として扱っているが、将来 app metadata に昇格するかもしれない項目です。
ただし今は、紹介判断や要約補助に使う材料としての意味が強く、app 側へ急いで寄せる必要はありません。

- `inspired_by`
- `features`
- `developer_accounts`

### ATProto 寄与度の独立評価

`category=auth_only` とは別に、「ATProto が無くてもサービスの主要価値が成立するか」を
評価する rule-based v1 を実装済みです。`ops_json` に evidence 付きの寄与度を保存し、
review queue と promotion queue の優先度に使います。

- `atproto_contribution_level`
  - `core`
    - ATProto record、repository、lexicon、federated identity などが主要機能を構成する
  - `substantial`
    - ATProto が主要機能の一部を担い、除くとサービスの価値が明確に下がる
  - `optional`
    - ATProto integration はあるが、除いてもサービスの中心機能は成立する
  - `auth_only`
    - sign-in / identity provider としてのみ使う
  - `incidental`
    - link、share、cross-post など周辺機能に限る
  - `unknown`
- `atproto_contribution_score`
  - queue の sort 用。level の説明可能性を失わない範囲で数値化する
- `atproto_contribution_evidence`
  - owned / consumed NSID
  - record sample
  - repository / README の実装根拠
  - service docs の説明
  - sign-in 導線だけか、主要データモデルまで使うか

LLM は evidence の要約と暫定分類に使えますが、単独で高スコアを確定しません。
紹介対象では `core` / `substantial` を優先します。manual review は寄与度そのものが不明な時、
または確認結果によって keep / reject、merge、紹介可否、tracking 優先度が変わる時に寄せます。

実装済みの v1 は次の保守的な rule です。

- resolved NSID または lexicon schema sample があれば `core`
- recent record sample または record count を持つ collection observation があれば `substantial`
- `category=auth_only` で強い protocol surface が無ければ `auth_only`
- input NSID または external lexicon hint だけなら `optional`
- それ以外は `unknown`

未接続:

- `incidental` を確定できる evidence source
- docs / README を材料にした「主要機能か追加機能か」の細分化
- LLM による曖昧ケースの補助分類

### provider 別 convenience field

次は evidence から再構成できるなら、将来的に縮退候補です。

- `github_repo_urls`
- `tangled_repo_urls`

## TODO

- `app.status` を separate field として導入する必要が出たら、`service_status` からの写像を定義する
- `app.links[]` の role を増やすなら、`appStore` / `playStore` / `fdroid` / `webManifest` / `demo` / `api` / `package` / `lexicon` / `collection` / `directory` の採用基準を定義する
- `github_repo_urls` / `tangled_repo_urls` を provider 別 convenience field のまま残すか、evidence から導出に寄せるか決める
- URL field migration
  - `audit-url-fields` の raw 保存監査を基準に進める
  - `website_url_candidates` の caller を `service_url_candidates` へ寄せ、derive-only または削除にする
  - `github_repo_urls` / `tangled_repo_urls` の caller を `source_code_urls` / `source_repo_records` へ寄せ、provider view として導出にする
  - `resource_urls` は削除前に role / source / observed_at を持つ structured observation へ移す
- `inspired_by` を app metadata として昇格させるか再判断する
- `atproto_contribution_level` の docs / README evidence と LLM 補助分類を追加する
