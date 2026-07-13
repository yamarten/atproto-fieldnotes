# Collector Reliability Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。契約は
> [investigation-model.md](../developer/investigation-model.md) を参照してください。

## 現在地

実装済み:

- source/operation 単位の `CollectorResult`
- `success | partial | skipped | rate_limited | failed`
- request/result 数、cursor、stop reason、retry 時刻、cache hit の保存欄
- timeout、429、5xx、network error の retry。最大3 attempt
- `Retry-After`、exponential backoff、source-local rate-limit stop
- GitHub search/repo と service probe の process-local TTL cache
- GitHub/Bluesky search の request budget
- failure source 以外を継続し、run 後も「証拠なし」と「未実行/失敗」を区別

403 は retry せず forbidden として扱います。retryable flag は最終 failure の性質であり、必ず次 run を
自動 scheduling することを意味しません。

## 調査履歴

- 2026-06-19: UFOsの`sh.tangled.repo` response 42件中39件で`record.topics`が欠落または`null`だった。
  `atproto_relevant()`が`None`を反復してsync全体を失敗させていたため、list以外を空topicsとして扱うよう修正した。
- 2026-06-19: `sync-tangled-repos --create-unmatched --execute`の初回作成時、seed保存後もpartial runtime dictを
  返して同じrun内の次saveをguardが拒否していた。`ensure_project_for_seed()`は保存後にDBからcomplete viewを再loadして返す。

## Source 別の残件

- GitHub: secondary rate limit と通常 403 の詳細分類、repo contents request の budget 統合
- Bluesky: session create/refresh の retry と cursor 打ち切り状態
- service: follow-up/deep 全体の per-host budget、cache freshness
- Tangled/PDS: handle/DID/PDS 解決 cache と pagination 上限
- LLM: endpoint 単位の backoff と circuit breaker

## Source result envelope

初期案の source 固有 envelope は、v2 では共通 `CollectorResult` と順序付き `EvidenceRecord` に分離して
表現しています。integration が `repos` など source 固有 top-level field を知る構造にはしていません。

```json
{
  "source": "github_repo",
  "status": "success",
  "request_count": 3,
  "result_count": 1,
  "stop_reason": ""
}
```

## 未検証

- 実 API で適切な TTL、budget、backoff 上限
- process-local cache と保存 run の freshness 表示
- partial pagination の再開時重複排除
- 複数 project を跨ぐ run-level circuit breaker

## 将来目標

- source policy を config から調整可能にする
- retryable failure の次回実行時刻を review/enrich priority に使う
- operator 対応が必要な error だけを `last_run_errors` へ要約する
