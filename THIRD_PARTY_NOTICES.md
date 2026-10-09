# Third-party materials

## 同梱データ

[vendor/public_suffix_list.dat](vendor/public_suffix_list.dat)は、[Public Suffix List](https://publicsuffix.org/list/)のデータです。このファイルは[Mozilla Public License 2.0](https://www.mozilla.org/MPL/2.0/)に従って配布します。冒頭のライセンス案内と、リスト内の既存の表記を保持しています。本体コードのライセンスを、このデータのライセンスとして扱わないでください。

## インストールする依存物

以下は本体から利用する直接依存です。パッケージ本体、Python runtime、ブラウザはこのリポジトリに同梱していません。

| 依存物 | ライセンス | 用途・設定元 |
| --- | --- | --- |
| [tldextract](https://github.com/john-kurkowski/tldextract) | BSD-3-Clause | ドメイン解析。[requirements.txt](requirements.txt) |
| [@atproto/oauth-scopes](https://github.com/bluesky-social/atproto/tree/main/packages/oauth/auth-scopes) | MIT | OAuth scope解析。[package.json](package.json) |
| [@bsky/jetstream](https://github.com/bluesky-social/bsky/tree/main/packages/jetstream) | MIT OR Apache-2.0 | Tangled discovery。[package.json](package.json) |
| [Playwright for Python](https://github.com/microsoft/playwright-python) | Apache-2.0 | 任意のブラウザ観測。[requirements-browser.txt](requirements-browser.txt) |

Nodeの間接依存と固定versionは[package-lock.json](package-lock.json)に記録しています。インストールした依存物やruntimeをまとめて再配布する場合は、それぞれに付属するライセンス・著作権・NOTICE等も含めて扱ってください。

## 独立した補助ツール

`extras/luna-client/`の公式SIWC DevKit由来の暗号化アダプタとSDK拡張patchには、[Sign-in with ChatGPT DevKit Noncommercial License v1.0](extras/luna-client/LICENSE.devkit)を適用します。本体のMIT Licenseとは分けて扱います。SDK本体・Electron・locked dependenciesは同梱せず、別途取得するSDK内のLICENSE・NOTICEを保持します。範囲と取得手順は[clientのREADME](extras/luna-client/README.md#ライセンス)を参照してください。

## 調査対象の情報

テストfixtureと文書には、公開されたプロジェクト名・識別子・URL・短い引用、観測から作った期待結果を含みます。引用や観測には出典を保持し、第三者の原文・名称等に本体のライセンスで新たな権利を与えるものではありません。DBに収集する資料や、別途共有するDB exportにも、本体のライセンスを一律に適用しません。
