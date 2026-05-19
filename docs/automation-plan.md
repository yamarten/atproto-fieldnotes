# Automation Plan

## 目的

新しく生まれた atproto プロジェクトを継続的に発見し、特に新規 lexicon を持つプロジェクトを見逃しにくくする。

対象判定は完全自動にしない。
代わりに次の分担にする。

- 機械: 候補収集、証拠集め、重複統合、粗分類、watchlist 管理
- 人: 曖昧な候補の最終判断

## 推奨アーキテクチャ

- 収集
  - `collection_count_view`
  - `sync-introduced` で拾う Bluesky 投稿
- 保存
  - SQLite
- 要約/分類
  - ルールベース + local LLM
- 出力
  - Markdown report
  - 必要なら webhook

詳しくは [docs/automation-architecture.md](automation-architecture.md) を参照。

## 情報源の役割分担

### A. 新規 lexicon 発見

- `collection_count_view`

### B. 既知紹介済み候補の取り込み

- `sync-introduced` の Bluesky 投稿検索

### C. 候補の裏取り

- changed collection に対する `com.atproto.sync.listReposByCollection`
- NSID authority domain の HTTP probe
- Bluesky profile / author feed
- GitHub name search

## 最小構成

無料寄りで始めるならこれで十分です。

- Python 3
- SQLite
- `launchd` または `cron`
- local LLM
  - 例: `qwen/qwen3.5-35b-a3b`

## 運用単位

### Weekly

- `collection_count_view` snapshot の収集
- `sync-introduced` による紹介済み判定更新
- new / changed collection の report 更新
- watchlist 再確認
- LLM summary の再生成
- 除外ルールの見直し

## データモデル

保持すべき項目は [docs/project-record.md](project-record.md) に定義した。

保存形は紹介前処理を主目的に設計する。ただし将来 DB から app record を export できるよう、app lexicon を参考にした metadata の置き場も持つ。

最低限必要なのは以下。

- project 名
- 概要
- 特徴
- 元ネタのサービス
- ソースコード置き場
- 利用方法
- 動作 URL と状態
- lexicon 一覧

## LLM の役割

LLM は discovery の主役ではなく、整理の補助に使う。

- 候補の分類
- 一言 summary の抽出
- 主要 feature の抽出
- 除外理由の文章化
- watch 継続理由の文章化

プロンプトは [prompts/project-triage.md](../prompts/project-triage.md) に置いた。

## 実装優先順位

1. `collection_count_view` と Bluesky 投稿検索で候補収集
2. changed collection と changed repo に対して lexicon / repo / profile / domain の証拠集め
3. ルールベース分類
4. local LLM による summary と曖昧候補判定
5. watchlist 再巡回

## 注意点

- 新しい lexicon があっても、価値ある project とは限らない
- 逆に repo だけ先に出て、lexicon は後から来ることもある
- なので `repo`, `lexicon`, `profile`, `domain`, `post` を束ねて 1 件として扱う必要がある
