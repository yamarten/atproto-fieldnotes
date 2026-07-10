# Registry Maintenance Status

この文書は`codex/registry-maintenance` branch内の作業状況と検証結果だけを扱います。
通常運用で現在できることの正本は[../status.md](../status.md)です。このbranchの変更を通常運用へ適用するまでは、
ここに書かれた内容を実装済みの通常仕様として扱いません。

各checkpointの適用時に、通常運用へ入った内容とその検証結果だけを`docs/status.md`へ反映します。この文書は
maintenance series完了時に削除します。

## 現在地

実装済み:

- DB open transaction修正。
  - 新規schema初期化はschema versionを書いた接続内でcommitする。
  - 既存schemaのensureではschema versionの同値upsertを行わない。
  - `database.open_database()`から返る接続は、open処理由来のwrite transactionを保持しない。

作業中:

- なし。

未実装:

- DB pathと明示的createの契約。
- [registry-maintenance-plan.md](registry-maintenance-plan.md)のその他のPhase 1以降。

DB適用:

- なし。正本DBは変更していません。

## 現行HEADの検証

動作確認済み:

- Python 3.11.15でunit test 303件が全件成功。
- 新規DBをcaller側のcommitなしでclose/reopenしてschema versionを確認する回帰test。
- 既存DBをopenした接続を保持したまま、別接続が`BEGIN IMMEDIATE`を取得できる回帰test。

未検証:

- 正本DBを使ったWeb/workerの再起動。今回の変更はtemp DBだけで確認し、正本DBは開いていません。

## Baseline

2026-07-10にcommit `aa44d37`で動作確認済み:

- `/opt/homebrew/bin/python3.11` 3.11.15でunit test 301件が全件成功。
- Node.js 26.5.0で`app.js`のsyntax checkが成功。
- investigation benchmark 6 caseが全件成功、semantic regression 0件。
- repository-local `.venv`が無い状態で、system `python3` 3.14.6を使う2つの公開wrapperの`--help`が成功。

このbaselineは外部API、実LLM、正本DB、実browserを使っていません。公開wrapperは対応Pythonを選べた
この環境でのみ動作確認済みです。

未検証:

- Python 3.10での全test。
- 非対応Pythonをsystem `python3`が指す環境で、wrapperが明確なversion errorを返すこと。現行wrapperには
  version checkがありません。
