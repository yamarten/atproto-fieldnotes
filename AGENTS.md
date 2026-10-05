# AGENTS

## 作業時の参照先

- 目的・設計方針は[brief](docs/brief.md)、関連する残件・着手条件は[TODO](docs/developer/todo.md)で確認する。
- 実装変更では[architecture](docs/developer/architecture.md)から対象機能の仕様を確認する。
- 文書変更では[文書管理方針](docs/documentation-policy.md)に従う。利用手順の入口は[README](README.md)。
- 検証方法と技術的な注意点は[変更の受入](docs/developer/workflows/change-acceptance.md)で確認する。LLM調査・評価・修正では[LLM model評価](docs/developer/workflows/llm-model-evaluation.md)も確認する。

## 検証と実データの境界

- code変更は対象testに加え、commit前に`bin/project-registry-test`で全unit testを実行する。
- Web UI変更は関連API test、JavaScript syntax check、変更した主要導線の実ブラウザ確認を行う。
- 正本DBは`data/survey.db`。書込み検証には開発用DBか必要な複製を使い、正本への変更と実運用serverへの反映は明示的な承認を得る。
