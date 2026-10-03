# atproto survey automation

新しく生まれたatprotoのプロジェクト、サービス、ライブラリ、lexiconを発見し、調査・整理・追跡するための個人用ローカルツールです。

主対象は新規lexiconを持つ独立プロジェクトです。候補を外部情報源から収集し、シードを起点に登録プロジェクトを調査して、紹介状況と追跡状態をSQLiteに保存します。

## Quick start

uvとNode.js 22.15以上を用意します。Python本体はHomebrewとは別に、checkout内の専用世代へ取得します。既定はCPython 3.14の最新patchで、通常起動時には取得・更新しません。Node依存はTangledの取得とOAuth scopeの解析に使います。

```bash
bin/project-registry-python-env prepare initial
bin/project-registry-python-env activate initial
bin/project-registry-python-env status
npm install
export PATH="$PWD/bin:$PATH"
project-registry init-db
```

uvの導入、既存環境からの移行、検証後の更新・復旧は[Python環境の管理](docs/user/guide.md#python環境の管理)を参照してください。既存の`.venv`を直接上書きしません。

Web UIを起動します。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
export ATPROTO_SURVEY_JETSTREAM_API_KEY='...'
project-registry-web --host 127.0.0.1 --port 8765
```

macOSでログイン時に自動起動する場合は、`.envrc`からトークン等を読み込むLaunchAgentを導入できます。LaunchAgentは実運用向けに`0.0.0.0:8888`で接続を待ち受け、同じMacからは`http://127.0.0.1:8888/`で開けます。

先に[設定テンプレートから`.envrc`を準備](docs/user/guide.md#設定ファイルと認証情報)し、Web UI tokenを設定して`direnv allow`を実行します。

```bash
chmod 600 .envrc
project-registry-web-service install
project-registry-web-service status
```

Web UIの`登録プロジェクト`からシードを登録・調査し、`レビュー`で要レビュー項目や紹介候補を確認します。CLIで最小フローを行う場合は次の通りです。

```bash
project-registry add-seed sh.tangled.repo
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry show --seed sh.tangled.repo --type nsid
```

通常コマンドとWeb UIはDBを暗黙作成しません。既定DBは`data/survey.db`です。別DBを使う方法、シード形式、外部情報源とLLMの設定は[利用ガイド](docs/user/guide.md)を参照してください。

## 利用と共有の範囲

個人が自分のローカルDBを運用するためのツールです。複数利用者での共有DBや公開Webサービスとしての運用、外部向けAPIの互換性保証は提供しません。

調査DB、認証情報を設定する`.envrc`、実行ログ、非公開のLLM事例はGit管理外です。DBを共有する場合は、その都度用途に合わせた論理exportを用意し、収集資料に含まれる認証値やトークン等を確認します。共有用の秘匿処理付きDB exportは未実装です。同梱データと依存物の権利表記は[Third-party materials](THIRD_PARTY_NOTICES.md)を参照してください。

## 公開入口

- [bin/project-registry](bin/project-registry): 登録、調査、候補整理、外部情報の取り込み、保守のCLI
- [bin/project-registry-web](bin/project-registry-web): schema v4ローカルWeb UI
- [bin/project-registry-web-service](bin/project-registry-web-service): macOS LaunchAgentの導入・状態確認
- [bin/project-registry-python-env](bin/project-registry-python-env): Python世代の準備・選択・状態確認
- [completions/_project-registry](completions/_project-registry) / [completions/_project-registry-web](completions/_project-registry-web) / [completions/_project-registry-web-service](completions/_project-registry-web-service): zsh completion

`PATH`には`bin/`だけを追加する前提です。各CLIオプションは`project-registry COMMAND --help`で確認できます。

## テスト

```bash
bin/project-registry-test
```

テストは外部API、正本DB、実LLMの接続先へ接続せず、必要なSQLite DBを一時ディレクトリへ作ります。起動スクリプトは現在のcheckoutの`.venv`を優先し、それが存在しないworktreeでは本家の専用環境を利用します。候補環境の検証は`PROJECT_REGISTRY_PYTHON_ENV`で明示できます。システムPythonへのfallbackはありません。

## 文書

利用者向け:

- [利用ガイド](docs/user/guide.md): 準備、シード、日常フロー、安全な使い分け
- [CLIガイド](docs/user/cli.md): 全公開コマンドの入力、出力、外部接続、DB変更
- [Web UIガイド](docs/user/web-ui.md): 画面、絞り込み、操作、バックグラウンドジョブ、アクセス境界

目的・計画・文書方針:

- [Project Brief](docs/brief.md): 目的、対象・非目標、提供したい価値
- [Roadmap](docs/roadmap.md): 到達目標、着手条件、優先関係
- [文書管理方針](docs/documentation-policy.md): 正本文書全体の責務、配置、更新方法

開発者向け:

- [Status](docs/developer/status.md): 現在頼ってよい範囲、接続・適用と制約
- [Architecture](docs/developer/architecture.md): 全体の流れ、実行経路、moduleの責務・依存方向
- [仕様一覧](docs/documentation-policy.md#開発仕様): 発見、調査、帰属・採用、評価、保存などの担当文書
- [変更の受入条件と確認手順](docs/developer/workflows/change-acceptance.md): CLI/Web共通ダミーDBの生成・リセット、変更種別ごとの確認、DB複製、実運用への反映境界
- [設計・調査メモ](docs/notes/): 未決事項、保留、再開に必要な材料

## 実装

- [scripts/project_registry_core/](scripts/project_registry_core/): CLIとWebで共有する調査・評価・保存などの処理
- [scripts/project_registry_web/](scripts/project_registry_web/): Web API、バックグラウンドジョブの管理、静的UI
- [prompts/project-triage.md](prompts/project-triage.md): LLM調査prompt

調査結果の正本は`data/survey.db`です。現行保存形式はregistry schema version 4で、旧schemaは通常導線の対象外です。
