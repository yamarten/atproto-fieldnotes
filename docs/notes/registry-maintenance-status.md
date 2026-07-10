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
- DB path / create契約。
  - 通常CLI、Web、background workerは既存のschema version 2 DBだけを開く。
  - missing pathや未初期化fileは空DBへfallbackせずerrorにする。
  - 新規作成は`project-registry init-db [--db-path PATH]`だけが明示的に行う。
- Python runtime / wrapper契約。
  - 最低versionはPython 3.10。
  - 公開wrapperは`.venv/bin/python`、次に`python3`を選び、import前にversionを検証する。
  - 非対応runtimeではsyntax/import errorへ進まず、実測versionを含む短いerrorと終了code 2を返す。
- `split-project`のexplicit key option削除。
  - 正常利用例・test・現行call siteが無く、成立しない`--new-project-key` / `-n`を削除した。
  - application APIからも未使用引数と到達不能な既存key検証分岐を削除した。
  - CLI、Web、source-configからのsplitは従来どおり新しい`project_key`を自動採番する。

作業中:

- なし。

未実装:

- [registry-maintenance-plan.md](registry-maintenance-plan.md)のその他のPhase 1以降。

DB適用:

- なし。正本DBは変更していません。

## 現行HEADの検証

動作確認済み:

- Python 3.10.20と3.11.15でunit test 310件が全件成功。
- 新規DBをcaller側のcommitなしでclose/reopenしてschema versionを確認する回帰test。
- 既存DBをopenした接続を保持したまま、別接続が`BEGIN IMMEDIATE`を取得できる回帰test。
- missing pathと未初期化fileを通常openが拒否し、fileを暗黙作成しない回帰test。
- custom pathを`init-db`で作成し、schema versionとinvestigation tableを確認する回帰test。
- Webがmissing DBを検出し、serverをbindする前に停止する回帰test。
- 二つの公開wrapperがfake Python 3.9.18をimport前に拒否する回帰test。
- repository-local `.venv`が無い状態で、system Python 3.14.6を使う二つの公開wrapperの`--help`。
- `split-project` parserにexplicit key属性が無く、自動採番splitとWeb splitが成功する回帰test。

未検証:

- 正本DBを使ったCLI/Web/workerの再起動。今回の変更はtemp DBだけで確認し、正本DBは開いていません。

## Baseline

2026-07-10にcommit `aa44d37`で動作確認済み:

- `/opt/homebrew/bin/python3.11` 3.11.15でunit test 301件が全件成功。
- Node.js 26.5.0で`app.js`のsyntax checkが成功。
- investigation benchmark 6 caseが全件成功、semantic regression 0件。
- repository-local `.venv`が無い状態で、system `python3` 3.14.6を使う2つの公開wrapperの`--help`が成功。

このbaselineは外部API、実LLM、正本DB、実browserを使っていません。公開wrapperは対応Pythonを選べた
この環境でのみ動作確認済みです。
