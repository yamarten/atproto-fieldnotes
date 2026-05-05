# Project Registry

一次ソース取得とは別に、手動で見つけた seed を登録して canonical project record に昇格させるための仕組みです。

現時点では schema を 2 層で考えるが、当面は最小コアを優先する。

- `app core`
  - `name`
  - `website_url`
  - `service_urls`
  - `tagline`
  - `tagline_is_provisional`
  - `service_status`
  - `source_code_urls`
  - `introduced`
  - `introduced_url`
  - `tracking_state`
  - `tracking_note`
- `registry / investigation`
  - このツール固有の調査・追跡フィールド

`introduced` は必須の bool として扱う。初期値は `false`。`tracking_state` は継続調査の対象管理に使い、初期値は `active`。`project_key` は内部 ID で、`proj:xxxxxxxxxxxx` 形式の opaque key を使う。

今の merge 自動化は「候補検出と recommendation」に加えて、dry-run 付きの merge 実行まで入っている。標準は dry-run。

## できること

- NSID / repository URL / service URL のいずれかを seed として登録
- 既知 seed と重ならなければ新規 project として記録
- 既知 project に `--project-key` で seed を追加
- 新規 project は seed 由来ではない opaque `project_key` を持つ
- 登録済み project を調査して、確認済み情報と候補情報を分離
- local LLM で `summary/tagline/features/category/service_status` を生成
- 条件を満たした record を `draft` から本線 status に昇格
- duplicate / merge 候補を機械判定する
- recommendation を使って project merge を dry-run / 実行する

## 確認済みと候補の区別

- `website_url`
  - 実際に HTTP probe して確認できた URL
- `website_url_candidates`
  - NSID authority や GitHub homepage などから推定した未確認 URL
- `service_urls`
  - 確認済み URL
  - `website_url` が確定していれば同じ URL を含める
- `service_url_candidates`
  - 未確認 URL
- `failed_service_urls`
  - probe したが正常応答しなかった URL
- `source_code_urls`
  - 確認済みのコード置き場
- `candidate_source_code_urls`
  - 調査段で見つけたが、まだ確定していないコード置き場候補
- `source_repo_records`
  - confirmed/candidate のコード置き場と、その発見 source を持つ内部レコード
  - `reason`, `evidence_kind`, `evidence_url` を含み、first-party link / README / GitHub search / Bluesky reference / LLM suggestion を区別する
- `summary` / `tagline`
  - LLM 審査を通して確定した値
- `tagline_is_provisional`
  - `true` のとき、その tagline は暫定値
- `summary_candidates` / `tagline_candidates`
  - lexicon description, GitHub description, meta description などの予備情報
- `llm_source`
  - model, prompt version, generated_at, evidence bundle
- `llm_output`
  - LLM の生出力 JSON
- `tracking_state`
  - `active`, `watchlist`, `stable`, `archived`, `ignore`
- `tracking_note`
  - 手動で付ける追跡方針メモ
- `notes`
  - 手動メモ用
- `last_run_errors`
  - 直近の `enrich` run で出たエラーだけを保持する

## NSID の区別

- `input_nsids`
  - 手動 seed として渡された NSID
- `resolved_nsids`
  - 実際に lexicon 解決できた NSID
- `lexicons`
  - canonical に採用した解決済み full NSID
- `recorded_lexicon_namespaces`
  - project 単位で記録する namespace
  - 1件だけなら `app.bsky.feed.post` -> `app.bsky`
  - 複数件あれば、同じ lexicon repository に属する解決済み NSID 群の共通 prefix を優先
  - 共通 prefix が弱すぎる場合は無理にまとめず、各 NSID の先頭 2 要素へ戻す

## enrich の流れ

`enrich` は次の順で動く。

1. seed 由来の事実収集
2. service / repo / lexicon の確認済み情報を更新
3. lexicon repository 単位で namespace 集約
4. LLM に調査クエリを提案させる
5. GitHub / Bluesky の公開検索を実行して候補 repo / account を集める
6. evidence bundle を作成
7. local LLM で project summary を生成
8. 昇格ゲートを判定
9. 通れば `tracked` / `published` / `invite_only` / `beta` / `inactive` / `rejected` へ、足りなければ `draft` のまま残す

`enrich --all` は既定で `tracking_state=active|watchlist` の project だけを対象にする。`stable` / `archived` / `ignore` は明示的に含めたときだけ回す。

## 昇格ゲート

`enrich` 後に以下を満たすと本線扱いにする。

- seed が 1 件以上ある
- 確認済み識別子が 1 件以上ある
  - `website_url`
  - confirmed repository URL
  - resolved lexicon
- `service_status` が `unknown` ではない
- `summary` と `tagline` が埋まっている
- `features` が 3 件以上ある
- `category` が決まっている

満たさない場合は `status=draft` のまま、`promotion_blockers` と `watch_reason` に理由を残す。runtime error は `notes` ではなく `last_run_errors` に入る。

`category` の想定値には `lexicon` も含む。これは、主に lexicon / schema 定義を提供し、実装や運用はサードパーティに委ねるタイプの project を表す。例として `standard.site` のようなものをここへ入れる。

`draft -> 非draft` に初めて上がったときは、`promoted_now=true` がその run の出力に含まれる。あわせて `last_promoted_at` と `promotion_events` を project record に保存するので、後からでも昇格履歴を追える。

## LLM の接続

昇格には local LLM が必須。`enrich` は command 方式か endpoint 方式のどちらかを使う。

- `ATPROTO_SURVEY_LLM_COMMAND`
  - 任意。stdin で prompt を受け、stdout に JSON だけ返すコマンド
- `ATPROTO_SURVEY_LLM_ENDPOINT`
  - 任意。OpenAI 互換の `chat/completions` endpoint か、その base URL
- `ATPROTO_SURVEY_LLM_MODEL`
  - 任意。endpoint 方式で使う model 名。未指定なら `local-model`
- `ATPROTO_SURVEY_LLM_API_KEY`
  - 任意。endpoint 方式の Bearer token
- `ATPROTO_SURVEY_LLM_PROMPT_VERSION`
  - 任意。未指定時は `project-triage-v1`

command 方式:

```bash
export ATPROTO_SURVEY_LLM_COMMAND='your-local-llm-wrapper'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

endpoint 方式:

```bash
python3 scripts/project_registry.py enrich \
  --seed sh.tangled.repo \
  --type nsid \
  --llm-endpoint http://127.0.0.1:1234/v1 \
  --llm-model qwen/qwen3.5-35b-a3b
```

または:

```bash
export ATPROTO_SURVEY_LLM_ENDPOINT='http://127.0.0.1:1234/v1'
export ATPROTO_SURVEY_LLM_MODEL='qwen/qwen3.5-35b-a3b'
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

command が無く、endpoint も無い場合は、事実収集だけ行って `draft` のまま残す。

LLM には、人が読む説明系フィールドを日本語で出すよう指示している。enum、URL、handle、DID、NSID はそのまま保持する。`tagline` は助詞を含まない短い名詞句を想定し、最大 30 文字に丸める。

## 調査フェーズ

コード置き場の探索は、`enrich` の中で次の順に行う。

- LLM に検索クエリ案を出させる
- GitHub repository search を叩く
- Bluesky actor search / post search を叩く
- 見つかった actor の recent posts を少数だけ見る
- そこから見つかった repo URL は `candidate_source_code_urls` に積む

この段階では source repo を自動確定しない。confirmed に入るのは、seed 由来か、公式 service page / README などの強い根拠で見つかったものだけ。

`investigation_queries` と `investigation_log` は append-only ではなく、直近の `enrich` run の内容だけを保持する。

## first-party collector

service URL を probe したときは、検索 fallback より先に first-party の導線を優先して追う。

- service page の `<a href>` を相対 URL 解決つきで抽出
- `source` / `view source` / `repo` / `monorepo` などの導線を repo 根拠として記録
- `docs` / `blog` は同一サイト系なら follow-up probe する
- `bsky.app/profile/...` は `bluesky_handles` / `developer_accounts` に入れる

これで、検索 API でたまたま見つかった候補と、公式ページから辿れた repo を区別できる。

## Bluesky 認証

`app.bsky.feed.searchPosts` は認証が必要になることがあるので、調査フェーズでは app password を使えるようにしている。

- `BSKY_HANDLE`
- `BSKY_APP_PASSWORD`
- `BSKY_AUTH_HOST`
  - 任意。未指定時は `https://bsky.social`

例:

```bash
export BSKY_HANDLE='your-handle.bsky.social'
export BSKY_APP_PASSWORD='xxxx-xxxx-xxxx'
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

`searchPosts` はまず認証付きで試し、失敗した場合だけ public AppView に fallback する。

## 重複候補

自動マージはしない。代わりに次の一致材料から `duplicate_candidates` を計算する。

- 同一 GitHub repo
- 同一 confirmed service URL
- 同一 lexicon repository DID
- 同一 `recorded_lexicon_namespaces`

## 使うスクリプト

- [scripts/project_registry.py](../scripts/project_registry.py)

## seed 登録

```bash
python3 scripts/project_registry.py add-seed sh.tangled.repo
python3 scripts/project_registry.py add-seed https://pdsls.dev/at://did:plc:3wng2qnttvtg23546ar6bawo/ink.henry.annotate.mark
python3 scripts/project_registry.py add-seed https://github.com/example/project
python3 scripts/project_registry.py add-seed https://example.com
```

`pdsls.dev` / `pds.ls` の URL や direct `at://did.../collection[/rkey]` を渡した場合は、collection NSID を抜いて `nsid` seed として扱う。

既存 project に seed を足す場合:

```bash
python3 scripts/project_registry.py add-seed https://example.com --project-key github:example/project
```

## merge

recommendation を使った dry-run:

```bash
python3 scripts/project_registry.py merge \
  --project-key nsid:app.bsky.feed.post \
  --use-recommendation
```

recommendation をそのまま実行:

```bash
python3 scripts/project_registry.py merge \
  --project-key nsid:app.bsky.feed.post \
  --use-recommendation \
  --execute
```

target を明示して merge:

```bash
python3 scripts/project_registry.py merge \
  --project-key nsid:app.bsky.feed.post \
  --target-seed https://bsky.app \
  --execute
```

保守的な条件で一括マージ候補を見る:

```bash
python3 scripts/project_registry.py merge-batch
```

実際に流す:

```bash
python3 scripts/project_registry.py merge-batch --execute
```

この一括マージはかなり保守的で、少なくとも次のどれかが必要。

- 同一 `repo_did`
- 同一 manual `repo_url`
- 同一 manual `service_url` host

さらに recommendation が相互であることを要求する。`service_urls` や alias だけの一致では自動マージしない。

## applog import

`applog.json` のような連結 JSON から、root service URL / exact repo root を抽出して紹介済み情報を取り込む。

dry-run:

```bash
python3 scripts/project_registry.py import-applog applog.json
```

既存 record にだけ反映:

```bash
python3 scripts/project_registry.py import-applog applog.json --execute
```

未一致 seed も新規作成する:

```bash
python3 scripts/project_registry.py import-applog applog.json --execute --create-unmatched
```

`list.json` のような WhiteWind 記事本文から、Markdown link を既存 project へ寄せて紹介済み情報を補完する:

```bash
python3 scripts/project_registry.py import-list list.json
python3 scripts/project_registry.py import-list list.json --execute
```

`services.json` のような Markdown table から、`name / NSID / service URL / repo URL` を project に補完する:

```bash
python3 scripts/project_registry.py import-services services.json
python3 scripts/project_registry.py import-services services.json --execute --create-unmatched
```

GUI や SQL で直接 `projects` を触った後は、`data_json` の正規化、派生フィールドの再計算、重複衝突の確認のために次を回す。`project_identifiers` テーブルはもう使わない:

```bash
python3 scripts/project_registry.py repair-db
python3 scripts/project_registry.py repair-db --salvage-json --clean-notes
```

このコマンドは次を自動修復する。

- orphan identifiers
- stale identifiers
- missing identifiers
- `--salvage-json` を付けると、trailing comma のような軽微な JSON 崩れを直してから監査する
- `--clean-notes` を付けると、過去の自動エラー行だけを `notes` から落とす

複数 project が同じ identifier を要求している collision は、直さずレポートだけ出す。

## 調査と昇格

1 件だけ:

```bash
python3 scripts/project_registry.py enrich --seed sh.tangled.repo --type nsid
```

全部:

```bash
python3 scripts/project_registry.py enrich --all
python3 scripts/project_registry.py enrich --all --include-stable
```

## 確認

```bash
python3 scripts/project_registry.py show --all
python3 scripts/project_registry.py show --all --tracking-states active watchlist
python3 scripts/project_registry.py show --project-key github:example/project --format json
```

JSON には `promotion_ready`, `promotion_blockers`, `duplicate_candidates`, `llm_source`, `llm_output` も含まれる。

追跡対象から外したいとき:

```bash
python3 scripts/project_registry.py set-tracking-state \
  --project-key service:https-example-com \
  --tracking-state stable \
  --note "安定運営"
```

## 権限

この仕組み自体に OAuth scope は不要。

- NSID 調査: public atproto API
- GitHub repo 調査: public GitHub API
- service URL 調査: 通常の HTTP GET

`GITHUB_TOKEN` は rate limit 緩和用で任意。
