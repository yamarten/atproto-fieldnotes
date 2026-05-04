# Automation Architecture

## 方針

この調査は「候補発見」と「候補整理」を分けると安定する。

- 候補発見は deterministic にやる
- 判断が曖昧なところだけ local LLM を使う

無料寄りで組むなら、次の 4 層が妥当です。

## 1. Signal Harvest

### 優先する一次情報

- `https://collectiondata.usounds.work/collection_count_view`
  - collection 単位の inventory と activity snapshot として使う
- GitHub repository search
- Tangled repo discovery
  - UFOs Record samples API for `sh.tangled.repo`

### 補助情報

- Lexicon Garden
- ATProto Dashboard
- UFOs

補助情報は UI として便利だが、機械取得の主軸は `collection_count_view` と GitHub / Tangled の snapshot に寄せる。
atproto の sync / repo endpoint は changed collection の深掘りに使う。

## 2. Enrichment

候補ごとに以下を引く。

- lexicon record 本体
- record 作成者 DID
- repo / profile / website / GitHub URL
- NSID authority domain の HTTP 応答
- Bluesky / Tangled / GitHub 上の名称一致候補
- 開発者の直近投稿

ここで「プロジェクトの存在証拠」を蓄積する。

## 3. Classification

まずルールで粗く分ける。

- `infra_tool`
  - PDS, relay, indexer, dashboard, moderation, hosting
- `library`
  - SDK, helper, bindings, wrapper
- `auth_only`
  - OAuth / login / sign-in 主体
- `personal_tool`
  - 個人利用前提、汎用サービス性が低い
- `project`
  - 他人が使える独立プロジェクト

ルールで決まらないときだけ local LLM に渡す。

## 4. Watchlist

未公開・招待制・ベータは落とさず watch する。

- `next_check_at` を付ける
- 7 日 or 14 日おきに再確認
- 新しい投稿、README 更新、公開 URL 追加を検出する

## 推奨の無料構成

### 毎日バッチ

- Python 標準ライブラリの収集スクリプト
- SQLite 保存
- Markdown レポート生成
- `launchd` または `cron`

### 週次再評価

- watchlist だけ再巡回
- local LLM で summary を更新
- 手動追加した候補をマージ

### ローカル LLM の使いどころ

LLM を常時オンラインに置く必要はない。

- 候補の分類
- 概要抽出
- 特徴の要約
- 「Twitter/Notion/GitHub の何に近いか」の推定
- 除外理由の明文化

## データフロー

1. `collection_count_view` の full snapshot を取得
2. GitHub search snapshot を取得
3. Tangled repo snapshot を取得
4. source ごとに差分判定
5. changed collection / repo を深掘り
6. `project_key` 単位で統合
7. ルールベース分類
8. あいまい候補だけ LLM へ
9. レポート出力
10. watchlist 更新

## 実装優先順位

### Phase 1

- `collection_count_view` snapshot 収集
- GitHub search snapshot 収集
- Tangled repo snapshot 収集
- SQLite 永続化
- Markdown レポート

### Phase 2

- changed collection に対する `listReposByCollection`
- lexicon 解決
- NSID ドメイン probe
- Bluesky / GitHub 名前検索
- 除外ルール

### Phase 3

- local LLM による summary / classification
- watchlist 再調査
- 通知

## 注意点

- `com.atproto.lexicon.schema` は「新しい lexicon」であって「使えるプロジェクト」とは限らない
- 1 人の開発者が実験的に作った collection はノイズになりやすい
- repo, domain, profile, post の複数証拠を束ねて 1 project とみなすべき
