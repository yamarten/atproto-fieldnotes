# atproto survey automation

新しく生まれたatprotoのプロジェクト、サービス、ライブラリ、lexiconを発見し、調査・整理・追跡するための個人用ローカルツールです。

主対象は新規lexiconを持つ独立プロジェクトです。候補を外部情報源から収集し、シードを起点に登録プロジェクトを調査して、紹介状況と追跡状態をSQLiteに保存します。

## Quick start

Python 3.10以上の隔離環境を用意します。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
npm install
export PATH="$PWD/bin:$PATH"
project-registry init-db
```

Web UIを起動します。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
export ATPROTO_SURVEY_JETSTREAM_API_KEY='...'
project-registry-web --host 127.0.0.1 --port 8765
```

macOSでログイン時に自動起動する場合は、`.envrc`からtoken等を読み込むLaunchAgentを導入できます。LaunchAgentは実運用向けに`0.0.0.0:8888`へbindし、同じMacからは`http://127.0.0.1:8888/`で開けます。

```bash
chmod 600 .envrc
project-registry-web-service install
project-registry-web-service status
```

Web UIの`Projects`からシードを登録・調査し、`Review`で要レビュー項目や紹介候補を確認します。CLIで最小フローを行う場合は次の通りです。

```bash
project-registry add-seed sh.tangled.repo
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry show --seed sh.tangled.repo --type nsid
```

通常コマンドとWeb UIはDBを暗黙作成しません。既定DBは`data/survey.db`です。別DBを使う方法、シード形式、外部情報源とLLMの設定は[利用ガイド](docs/user/guide.md)を参照してください。

## 公開入口

- [bin/project-registry](bin/project-registry): 登録、調査、候補整理、外部情報の取り込み、保守のCLI
- [bin/project-registry-web](bin/project-registry-web): schema v3ローカルWeb UI
- [bin/project-registry-web-service](bin/project-registry-web-service): macOS LaunchAgentの導入・状態確認
- [completions/_project-registry](completions/_project-registry) / [completions/_project-registry-web](completions/_project-registry-web) / [completions/_project-registry-web-service](completions/_project-registry-web-service): zsh completion

`PATH`には`bin/`だけを追加する前提です。各CLIオプションは`project-registry COMMAND --help`で確認できます。

## テスト

```bash
bin/project-registry-test
```

testは外部API、正本DB、実LLM endpointへ接続せず、必要なSQLite DBをtemp directoryへ作ります。wrapperは現在のcheckoutの`.venv`を優先し、worktreeではGit共通directoryにある本家checkoutの`.venv`も利用します。

## 文書

利用者向け:

- [利用ガイド](docs/user/guide.md): 準備、シード、日常フロー、安全な使い分け
- [CLIガイド](docs/user/cli.md): 全公開コマンドの入力、出力、外部接続、DB変更
- [Web UIガイド](docs/user/web-ui.md): 画面、絞り込み、操作、バックグラウンドジョブ、アクセス境界

現在地と計画:

- [Project Brief](docs/brief.md): 目的、通常flow、主要判断、非目標
- [Status](docs/status.md): 現在の通常導線、既知の制約、検証状況
- [Roadmap](docs/roadmap.md): 未完了・将来候補
- [Thread Handoff](docs/thread-handoff.md): 次の作業に必要な最短の引き継ぎ

開発者向け:

- [Registry Internals](docs/developer/registry-internals.md): module構成、依存方向、現行heuristic
- [Project Record Schema](docs/developer/project-record.md): schema v3の保存契約
- [Investigation Model](docs/developer/investigation-model.md): collector、evidence、integration、evaluationの責務
- [Background Job Model](docs/developer/background-jobs.md): event、result、cancellation、checkpoint契約
- [変更の受入条件と確認手順](docs/developer/change-acceptance.md): 変更種別ごとの確認、標準環境、DB複製、実運用への反映境界
- [設計・調査メモ](docs/notes/): 比較、検討、履歴資料

## 実装

- [scripts/project_registry_core/](scripts/project_registry_core/): CLIとWebで共有するapplication/core
- [scripts/project_registry_web/](scripts/project_registry_web/): Web API、worker registry、静的UI
- [prompts/project-triage.md](prompts/project-triage.md): LLM調査prompt

調査結果の正本は`data/survey.db`です。現行保存形式はregistry schema version 3で、旧schemaは通常導線の対象外です。
