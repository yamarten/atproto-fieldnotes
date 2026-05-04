# Manual Workflow

現在の手動調査手順を、そのまま機械処理しやすい形に落としたメモです。

## 対象

主対象:

- 新規 lexicon を持つ atproto プロジェクト

副対象:

- microcosm や PDSls のようなインフラ/運用ツール

原則不要:

- 既存の大規模プロジェクトに強く依存するだけのもの
- ログインにだけ atproto を使うもの
- 開発者本人しか使わない前提のもの
- ライブラリ

ただし、除外対象でも「名前だけ記録」は許容する。

## 現在の調査手順

### 1. GitHub と Tangled で新規プロジェクト候補を拾う

- GitHub repository search
  - `https://github.com/search?q=atproto+OR+%22AT+protocol%22+OR+atprotocol&type=repositories&s=updated&o=desc&p=1`
- Tangled timeline
  - `https://tangled.org/timeline`
- 将来的な機械化では `sh.tangled.repo` の収集で代替したい

### 2. Lexicon Garden と ATProto Dashboard で新規 collection を拾う

- Lexicon Garden
  - `https://lexicon.garden/`
- ATProto Dashboard
  - `https://atpdashboard.usounds.work/`
- 将来的な機械化では以下で代替したい
  - `com.atproto.lexicon.schema` の取得と解決試行
  - UFOs などの collection index

### 3. 候補 NSID のドメインを HTTP で見る

- NSID の authority 部分からドメインを抜く
- ドメインにアクセスして、サイトの有無や紹介文を確認する

### 4. NSID をハンドルとして Bluesky / Tangled アカウントを見る

- プロジェクトや作者のアカウントが存在するか確認する
- プロフィール文やリンク先を確認する

### 5. Bluesky / GitHub で名前検索する

- プロジェクト名で検索
- 類似名や旧名も見る

### 6. 開発者の投稿を読む

- record 作成者の直近投稿を見る
- リリース状況、招待制、ベータ公開、デモ URL を探す

## 収集したい情報

- プロジェクト名
- プロジェクト概要
- 特徴
- 元になるサービス
- ソースコード置き場
- 利用方法
- 稼働中サービスのアドレスと状態
- 使用 lexicon 一覧

## 継続追跡したいもの

以下は 1 回で終わらず watch 対象にする。

- 未リリース
- 招待制
- ベータ
- ドメインや repo はあるが説明不足

## 判断の難しい点

- 既存クライアント依存の拡張か、独立したプロジェクトか
- 単なる認証利用か、atproto 固有のデータモデルを使っているか
- 個人用ツールか、他人も使えるプロジェクトか
- ライブラリか、サービス/ツールか
