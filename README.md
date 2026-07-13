# atproto survey automation

新しく生まれたatproto project、service、library、lexiconを発見し、調査・review・追跡するための個人用ローカルツールです。

主対象は新規lexiconを持つ独立projectです。候補を外部sourceから収集し、manual seedを調査してproject recordへ育て、紹介状況と継続調査stateをSQLiteに保存します。

## Quick start

Python 3.10以上の隔離環境を用意します。

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export PATH="$PWD/bin:$PATH"
project-registry init-db
```

Web UIを起動します。

```bash
export ATPROTO_SURVEY_WEB_TOKEN='long-random-token'
project-registry-web --host 127.0.0.1 --port 8765
```

Web UIの`Projects`からseedを登録・調査し、`Review`で候補を確認します。CLIで最小フローを行う場合は次の通りです。

```bash
project-registry add-seed sh.tangled.repo
project-registry enrich --seed sh.tangled.repo --type nsid
project-registry show --seed sh.tangled.repo --type nsid
```

通常コマンドとWeb UIはDBを暗黙作成しません。既定DBは`data/survey.db`です。別DBを使う方法、seed形式、外部sourceとLLMの設定は[利用ガイド](docs/user/guide.md)を参照してください。

## 公開入口

- [bin/project-registry](bin/project-registry): 登録、調査、review、sync、maintenanceのCLI
- [bin/project-registry-web](bin/project-registry-web): schema v2ローカルWeb UI
- [completions/_project-registry](completions/_project-registry) / [completions/_project-registry-web](completions/_project-registry-web): zsh completion

`PATH`には`bin/`だけを追加する前提です。各CLI optionは`project-registry COMMAND --help`で確認できます。

## テスト

```bash
bin/project-registry-test
```

testは外部API、正本DB、実LLM endpointへ接続せず、必要なSQLite DBをtemp directoryへ作ります。wrapperは現在のcheckoutの`.venv`を優先し、worktreeではGit共通directoryにある本家checkoutの`.venv`も利用します。

## 文書

利用者向け:

- [利用ガイド](docs/user/guide.md): setup、seed、日常フロー、安全な使い分け
- [CLIガイド](docs/user/cli.md): 全公開コマンドの入力、出力、network、DB変更
- [Web UIガイド](docs/user/web-ui.md): 画面、filter、action、background job、access境界

現在地と計画:

- [Project Brief](docs/brief.md): 目的、通常flow、主要判断、非目標
- [Status](docs/status.md): 現在の通常導線、既知の制約、検証状況
- [Roadmap](docs/roadmap.md): 未完了・将来候補
- [Thread Handoff](docs/thread-handoff.md): 次の作業に必要な最短の引き継ぎ

開発者向け:

- [Registry Internals](docs/developer/registry-internals.md): module構成、依存方向、現行heuristic
- [Project Record Schema](docs/developer/project-record.md): schema v2の保存契約
- [Investigation Model](docs/developer/investigation-model.md): collector、evidence、integration、evaluationの責務
- [Background Job Model](docs/developer/background-jobs.md): event、result、cancellation、checkpoint契約
- [設計・調査メモ](docs/notes/): 比較、検討、履歴資料

## 実装

- [scripts/project_registry_core/](scripts/project_registry_core/): CLIとWebで共有するapplication/core
- [scripts/project_registry_web/](scripts/project_registry_web/): Web API、worker registry、静的UI
- [prompts/project-triage.md](prompts/project-triage.md): LLM調査prompt

調査結果の正本は`data/survey.db`です。現行保存形式はregistry schema version 2で、旧schemaは通常導線の対象外です。
