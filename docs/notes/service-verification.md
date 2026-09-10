# Service Verification Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。契約は
> [Investigation](../developer/specs/investigation.md)、具体値は
> [現行規則](../developer/specs/project-evaluation.md#service-statusの評価) を参照してください。

## 検証方式

CLI option名は`--lane`ですが、意味は確認範囲と実行コストが異なる3つの検証方式です。

実装済み:

- static: HTTP status、metadata、text、link、redirect/canonical を収集
- deep: about/docs/app/login/signup などの優先 page を追加収集
- browser: optional Playwright または fake driver で JS 描画後の anonymous surface を確認
- `verify-service -p KEY --lane static|deep|browser [--execute]`
- review queue からの明示 browser 実行
- Web project detailからの保存済みservice URLの選択・匿名確認・結果表示も後続実装で接続済み（[現行Webガイド](../user/web-ui.md)）。

browser方式は同一 site の login/signup link を最大2件追い、app shell、runtime/network error、
login surface 到達を記録します。認証情報入力、登録、投稿は行いません。

## 制約

- login page 到達は認証後機能の稼働を保証しない
- static/deep/browser の成功は project identity の一致と別問題
- marketing site、docs、app、status page が別 host の場合は統合が弱い
- `unverified` は dead/private の確定ではなく未確認を含む
- browser の runtime error は一時障害と恒久障害を区別しない

## 未接続

- 定期 browser verification と履歴比較
- browser 結果からの自動 dead/private 降格
- CAPTCHA、OAuth、invite code、地域制限を越えた確認

## 未検証

- 実 Playwright localhost smoke test
- 長時間動作する SPA の network idle 判定
- 同一 service の複数 surface を一つの availability 評価へまとめる閾値

## 将来目標

- status、surface type、到達段階、利用可能性を別軸にする
- 高コストな検証方式をreview queueの明示操作に限定する
- HTML/browser fixture で status heuristic を反復比較する
