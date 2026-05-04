# Manual Candidates

手動で見つけた候補は `data/manual_candidates.jsonl` に 1 行 1 JSON で追加する。

追加用 CLI:

```bash
python3 scripts/add_manual_candidate.py \
  --name "example" \
  --url "https://example.com" \
  --summary "short summary" \
  --notes "why it matters"
```

直接 JSONL を編集してもよい。

```json
{"kind":"project","name":"example","url":"https://example.com","summary":"short summary","notes":"why it matters","status":"candidate"}
```

`discover.py` は次回実行時にこのファイルを自動で読み込み、DB とレポートへマージする。
