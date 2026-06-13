# Collector Reliability Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。collectorとfailureの契約は
> [../investigation-model.md](../investigation-model.md) を参照してください。

## 現在地

実装済み:

- GitHub / Bluesky search collectorはsource failureをbundleへ分離する
- `CollectorFailure`はsource、operation、target、category、message、retryableを持つ
- timeout、403/429、5xx、network error、その他errorを分類する
- 一つのsourceが失敗しても、他sourceとLLM reviewは継続できる
- Bluesky sessionと同一run内のGitHub search resultにはprocess-local cacheがある

制約:

- retryableは分類だけで、retryを実行しない
- bundleにはsource単位の完了状態、cursor、打ち切り理由がない
- GitHub repo metadataとservice probeはcacheしない
- secondary repo走査で同一repoを一run中に再fetchする場合がある
- failureは最終的に`last_run_errors`へ文字列として統合されるため、保存後は構造が失われる

## Source別の既知課題

### GitHub

- live fetchの連続実行で403 / secondary rate limitに当たり得る
- repo enrichはmetadata、README、treeに加え、lexicon candidate fileを最大16件取得する
- run-level stopやretry-after利用は未接続

### Bluesky

- actor / post searchは認証情報に依存する
- session作成とtoken refreshのretry policyは未接続
- search cursorで全履歴を走査できる保証はない

### Service probe

- confirmed URLとcandidate URLでtimeoutは分けている
- follow-upやdeep pageが増えると一projectあたりのrequest数が大きくなる
- source単位のcircuit breakerやcacheは未接続

### LLM

- endpoint shape差分に対するchat / responses / completions fallbackがある
- timeoutとparse failureはproject errorとして残る
- endpoint単位のbackoffは未接続

## 完了状態案

将来はsource実行を少なくとも次に分けます。

- `success`: 予定した範囲を完了
- `partial`: 一部resultとfailureがある
- `skipped`: 設定不足、既存evidenceで不要、budget超過などで未実行
- `rate_limited`: upstream制約で中断
- `failed`: usable resultなしで失敗

併せて、request数、取得件数、cursor、打ち切り理由、retry-after、cache hitを残す候補があります。

## 未検証

- source別の適切なretry回数とbackoff上限
- project単位とrun単位のどちらでcircuit breakerを持つべきか
- cache freshnessが調査精度へ与える影響
- partial resultを保存・再開する場合の重複排除

## 将来目標

- sourceごとのtimeout / retry / rate-limit policyをcollector外から設定する
- rate limit時は失敗sourceだけを止め、run全体を継続する
- 人が対応すべきerrorだけを`last_run_errors`へ残す
- source完了状態をreplayとreview queueの優先度へ利用する
