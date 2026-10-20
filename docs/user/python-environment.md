# Python環境の管理

導入後のPython更新、候補環境の検証、切替・復旧、旧世代の削除を扱います。初回導入は[利用ガイド](guide.md#環境準備)から始めてください。

Python本体・標準ライブラリ・venvを同じcheckoutの`.runtime/世代名/`に保持します。`.venv`は選択中の世代の`venv/`へのsymlinkです。世代を準備しても選択や起動中processは変わらず、`activate`はsymlinkだけを原子的に置き換えます。世代名は英数字・`_`・`-`を使い、既存名は再利用しません。失敗した準備も上書きせず別名でやり直します。

uvは導入・保守時だけ必要です。macOSでは`brew install uv`、他の環境では[公式導入手順](https://docs.astral.sh/uv/getting-started/installation/)を使います。Homebrewでuv自体を更新しても、アプリのPython本体は更新・削除されません。`prepare`はPythonをAstralのpython-build-standalone配布先、依存をPyPIから取得するため、通信・書込権限が必要です。ユーザー共通のuv Python保存先やPATH上のPythonを変更しません。

公開wrapperの選択順序は`PROJECT_REGISTRY_PYTHON_ENV`、このcheckoutの`.venv`、このcheckoutに`.venv`が存在しない場合だけ本家checkoutの`.venv`です。指定済み環境の破損・依存欠落・Homebrew等の外部Python参照はエラーで停止し、システムPythonへfallbackしません。activateは各checkoutだけに作用しますが、本家環境を借りるworktreeの次回起動には本家の選択変更が反映されます。独立検証するworktreeには自身の世代を準備してください。

wrapperは選択symlinkを解決した世代の絶対pathからPythonを起動します。Webのworkerと通常reloadはこの`sys.executable`を使うため、`.venv`を切り替えても稼働中Webは旧世代を使い続けます。activateは既存processを停止しません。通常起動でuvを呼ばず、アプリにPythonの自動更新機能はありません。

## 準備と検証

`prepare NAME [PYTHON_VERSION [REQUIREMENTS]]`は専用Python本体、venv、依存を作ります。version省略時は`.python-version`のminorに対応するuvの最新patchを選び、取得済み本体の実pathでvenvを作ります。[uvのminor symlinkによる自動patch切替](https://docs.astral.sh/uv/concepts/python-versions/#minor-version-directories)を避け、世代内のversionを維持します。利用可能versionはuvの版に依存するため、保守時に必要ならuvを更新します。minor変更は`.python-version`の変更とアプリ検証を伴う別の判断です。

新規導入は[利用ガイドの環境準備](guide.md#環境準備)の`initial`を使います。既存環境の移行やPythonだけの更新では依存を同時更新しないよう、現環境の固定一覧を渡します。以下はcheckoutのrootで行います。

```bash
mkdir -p .tmp/python-env
# 初回移行時: 既存venvの導入済みversionを読み取る（既存環境は変更しない）
uv pip freeze --python .venv/bin/python > .tmp/python-env/requirements.txt
bin/project-registry-python-env prepare candidate 3.14 .tmp/python-env/requirements.txt
# 既に専用世代を利用中なら、その世代のrequirements.txtを代わりに渡せる
export PROJECT_REGISTRY_PYTHON_ENV="$PWD/.runtime/candidate/venv"
bin/project-registry-python-env status
bin/project-registry-test
bin/project-registry --help
bin/project-registry-web-service --help
# 開発DB未作成の場合だけ生成（既存DBは再利用）
bin/project-registry dev-db
bin/project-registry --db-path data/dev/survey.db show --all
bin/project-registry-web --host 127.0.0.1 --port 8765 --db-path data/dev/survey.db --token web-ui-dev
```

検証用Webは実運用token・定期設定を使いません。画面・静的asset・設定・一覧APIと、外部通信のないreview dry-run等の実workerを確認して停止します。[確認環境の選び方](../developer/workflows/change-acceptance.md#確認環境と接続先)を参照し、使用中portの既存processを確認して接続先の取り違えを避けます。`status`でPython本体・標準ライブラリが候補世代の`python/`内、実行入口が候補世代の`venv/`内であることを確認します。各世代の`requirements.txt`は実際に導入したversion一覧で、再構築用に保持します。venv・Python本体は絶対pathを含むため別checkoutへコピー・移動せず、移行先で再準備します。

## ブラウザー観測の導入

匿名ブラウザー確認、`verify-service --lane browser`、`monitor-services --execute`には、Playwrightと対応するChromiumが必要です。通常の`enrich`も、リポジトリの公開先をサービスとして確認する条件を満たす場合にブラウザーを使います。基本導入の`requirements.txt`には含まれず、未導入でこの経路を使うと取得失敗として記録されます。架空デモの閲覧・編集には不要です。

リポジトリのルートで、新しい専用世代を準備します。以下の`browser`は未使用の世代名に置き換えられます。[requirements-browser.txt](../../requirements-browser.txt)は本体依存も含み、その宣言範囲から依存を解決します。既存世代は変更しません。

```bash
bin/project-registry-python-env prepare browser 3.14 requirements-browser.txt
export PROJECT_REGISTRY_PYTHON_ENV="$PWD/.runtime/browser/venv"
bin/project-registry-python-env status
bin/_project-registry-python browser-install -m playwright install chromium
```

`status`の`venv`が指定した候補世代であることを確認します。`browser-install`はwrapperのエラー表示用の名前で、後続の`-m playwright`を選択中のPythonで実行します。`prepare`が導入するのはPythonパッケージまでで、最後のコマンドが対応するChromium（headless shellを含む）を取得します。取得にはネットワーク接続が必要です。

ブラウザー本体はPython世代の外に置かれ、macOSの既定保存先は`~/Library/Caches/ms-playwright`です。同じOSユーザーのCLI・Web・workerから使います。`PLAYWRIGHT_BROWSERS_PATH`を独自設定する場合は導入時と実行時で同じ値を使い、LaunchAgentにもその環境変数を渡す必要があります。保存先とversionの対応は[Playwright公式のブラウザー管理](https://playwright.dev/python/docs/browsers#managing-browser-binaries)を参照してください。

候補指定を保ったまま、次の確認を行います。一時的なブラウザーを起動してメモリ上のHTMLを描画し、JavaScriptを実行して終了します。DBや外部サイトは使いません。

```bash
bin/_project-registry-python browser-check - <<'PY'
import sys
from importlib.metadata import version
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.set_content("<p id='result'>ready</p>")
        page.eval_on_selector("#result", "el => el.textContent = 'ok'")
        assert page.locator("#result").inner_text() == "ok"
        print(f"OK: Python={sys.executable}, Playwright={version('playwright')}, Chromium={browser.version}")
    finally:
        browser.close()
PY
```

`OK`と候補世代のPython実pathが出れば、その世代からChromiumを起動できます。`No module named playwright`なら候補指定と準備に渡したrequirementsを、実行ファイルがない旨のエラーなら同じ候補指定での`install chromium`と保存先を確認します。この確認は外部サービスへの到達や利用可否の検証ではありません。

確認後は`unset PROJECT_REGISTRY_PYTHON_ENV`で一時指定を解除します。採用する場合は[切替と復旧](#切替と復旧)の`candidate`を`browser`へ読み替え、常駐Webも候補世代で新規起動します。起動済みWebは環境変数の変更や通常reloadだけでは切り替わりません。後日Pythonを更新する際は、この世代の`requirements.txt`を使ってPlaywrightを引き継ぎ、新しい世代でもブラウザーの導入・起動確認を行います。共有するブラウザー保存先は、旧Python世代の整理に合わせて一括削除しません。

## 切替と復旧

検証後は`unset PROJECT_REGISTRY_PYTHON_ENV`し、候補を選択します。CLIだけなら既存処理の終了を待ちます。常駐Webがある場合は実行中job・workerとCLIを確認し、[WebのPython切替手順](web-ui.md#python環境の切替)で停止・再起動します。本番適用は実装受入とは別の承認段階です。

既存`.venv`が実directoryの場合、activateは上書きを拒否します。使用process停止後に`mv .venv .venv-legacy`で保持してから切り替えます。この退避は元venvのscript内絶対pathを書き換えないため、旧環境への復旧時は必ず元の`.venv`へ戻します。

```bash
unset PROJECT_REGISTRY_PYTHON_ENV
bin/project-registry-python-env activate candidate
bin/project-registry-python-env status
```

切替が失敗した場合は旧選択が残ります。新世代での確認が失敗した場合はprocessを停止し、`bin/project-registry-python-env activate OLD_NAME`で旧世代を選び直して起動します。旧世代は準備・切替で変更されません。

初回移行で退避した`.venv-legacy`は、旧環境の記録として保持できますが、現行wrapperは外部Pythonを拒否するため、そのまま起動には使えません。利用できる旧専用世代がない場合は、必要なPython versionを指定して新しい世代を準備し、候補指定で検証してから選び直します。旧Homebrew本体が既に消えている場合も、venvの退避だけでは復旧できません。

## 更新と不要環境の整理

保守時は新しい世代名で`prepare`→候補指定で検証→Web・worker・CLIの停止→activate→新規起動→HTTP/HTTPS確認の順に行います。`uv python upgrade`や`uv python uninstall`を既存世代に対して実行せず、導入済み依存もその場で書き換えません。uv cacheは`.tmp/uv-cache`へ置き、venvには依存をcopyするためcacheの削除は稼働環境に影響しません。

旧世代を消すのは、選択中でなく、その世代を使うWeb・worker・CLIがすべて終了し、新環境の運用確認と復旧用の保持期間を終えた後です。`status`は選択環境の情報で、旧processが終了した証拠にはなりません。`ps -axo pid,command`や対象serviceの状態を照合し、不明なprocessがあれば保持します。通常reloadでは旧世代が残ることにも注意してください。条件確認後、対象を限定して`.runtime/OLD_NAME/`全体を削除します。自動cleanupは行いません。

`.runtime/`と`.venv`はGit管理外です。worktreeをarchive・削除する前にもそのworktree内のPythonを使うprocessを停止します。失敗した未選択世代は利用processがないことを確認して削除できます。Python本体も失った場合は、保存したversionと`requirements.txt`から新名で再準備・検証します。DBの復旧とは別操作です。
