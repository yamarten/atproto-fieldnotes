# Project Registry

一次ソース取得とは別に、手動で見つけた seed を登録して project record を育てるための仕組みです。

## できること

- NSID / repository URL / service URL のいずれかを seed として登録
- 既知 seed と重ならなければ新規 project として記録
- 既知 project に `--project-key` で seed を追加
- 登録済み project を調査して、残りフィールドを自動補完

## 確認済みと候補の区別

- `website_url`
  - 実際に HTTP probe して確認できた URL
- `website_url_candidates`
  - NSID authority や GitHub homepage などから推定した未確認 URL
- `service_urls`
  - 確認済み URL
- `service_url_candidates`
  - 未確認 URL
- `failed_service_urls`
  - probe したが正常応答しなかった URL
- `summary` / `tagline`
  - 将来的に LLM や手動確認で確定した値
- `summary_candidates` / `tagline_candidates`
  - lexicon description, GitHub description, meta description などの予備情報

## NSID の区別

- `input_nsids`
  - 手動 seed として渡された NSID
- `resolved_nsids`
  - 実際に lexicon 解決できた NSID
- `recorded_lexicon_namespaces`
  - project 単位で記録する namespace
  - 1件だけなら `app.bsky.feed.post` -> `app.bsky`
  - 複数件あれば、同じ lexicon repository に属する解決済み NSID 群の共通 prefix を優先
  - 共通 prefix が弱すぎる場合は無理にまとめず、各 NSID の先頭 2 要素へ戻す

## 使うスクリプト

- [scripts/project_registry.py](../scripts/project_registry.py)

## seed 登録

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://github.com/example/project
python3 scripts/project_registry.py add-seed https://example.com
```

既存 project に seed をマージする場合:

```bash
python3 scripts/project_registry.py add-seed https://example.com --project-key github:example/project
```

## 調査

1 件だけ:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

全部:

```bash
python3 scripts/project_registry.py enrich --all
```

## 確認

```bash
python3 scripts/project_registry.py show --all
python3 scripts/project_registry.py show --project-key github:example/project --format json
```

## 権限

この仕組み自体に OAuth scope は不要。

- NSID 調査: public atproto API
- GitHub repo 調査: public GitHub API
- service URL 調査: 通常の HTTP GET

`GITHUB_TOKEN` は rate limit 緩和用で任意。
