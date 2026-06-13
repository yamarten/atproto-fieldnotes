# Service Verification Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。確定済みの pipeline 契約は
> [../investigation-model.md](../investigation-model.md)、現在の判定値は
> [../registry-internals.md](../registry-internals.md) を参照してください。

## 検証レーン

service検証はコストと確認できる内容が異なるため、次のレーンに分けて考えます。

1. static top-page probe
   - HTTP status、title、description、HTML text、linkを取得する
2. same-site follow-up
   - top pageから見つけたdocs / blog / aboutなどを追加確認する
3. deep page probe
   - `/about`, `/docs`, `/faq`, `/app`, `/login`, `/signup`などを優先して確認する
4. browser verification
   - JS app shell、実際のlogin/signup導線、招待制、利用可能画面を確認する

## 実装済み

- 通常 enrich のtop-page probeとsame-site follow-up
- `--deep-investigation`による最大6 pageの優先probe
- `service_status`, `service_status_confidence`, `service_status_evidence`の保存
- DNS / TLS / timeout / connection refusalの分類
- parked、404/410、login、invite、beta、coming soon、docs-only、app shellのsignal
- same-host HTTP→HTTPS と cross-host redirect の区別
- deep page kindを `deep_page:*` evidenceとして記録

主なrule-based status:

- parked、404/410: `dead`
- 401/403: `private`
- coming soon: `unreleased`
- invite / waitlist: `invite_only`
- beta: `beta`
- login surface / app shell: `live`
- docs-only: `none`
- 5xx / network error: `unverified`
- launch signalとapp surfaceの混在: `unverified`

## 制約

- static HTMLだけではclient-side rendered appの実体を確認できない
- login textがあっても認証後の機能が動くことは保証しない
- docs-only surfaceとservice本体が別hostの場合、単一URL probeでは判別しにくい
- `unverified`はdead/privateの確定ではなく、確認不足を含む
- deep page probeはservice URLのconfirmed化やmergeを直接決めない

## 未接続

- browserを使った実login/signup導線の確認レーン
- JavaScript実行後のapp shell / error stateの確認
- page種別ごとの細かいsignal schema
- deep investigation結果を既存DB全体へ洗い替える専用運用

## 未検証

- 実ブラウザ検証の再現性とfixture化方法
- CAPTCHA、OAuth、招待コード、地域制限があるserviceの完了条件
- 同じserviceのmarketing site、docs、app、status pageを一つの検証結果へ統合する方法

## 将来目標

- static probeとbrowser verificationを別collectorとして扱う
- URLごとに検証レーン、到達段階、最終確認時刻を残す
- 高コストなbrowser検証はreview queueからopt-in実行する
- statusの判定と「実際に利用可能か」の確認を別軸で表現する
