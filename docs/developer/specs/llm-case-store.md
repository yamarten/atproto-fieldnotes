# 非公開LLM事例の固定保存

この機能は、triageとrepository analysisの個別事例を、後日の回帰評価・参照・学習可否の検討に備えて固定保存する。正本DBや現在のURLを再読込せず、登録時に渡された入力、出力、実行条件を一つのJSONへコピーし、後から付く判断・確認・調整利用は同じ事例IDの追記履歴へ保存する。既存の評価corpusと公開テストfixtureは変更しない。事例を保存しただけではgold labelにならず、評価・学習にも自動投入しない。

## 保存先と境界

`register-llm-case --store ABSOLUTE_PATH --file DRAFT.json`は、リポジトリ外の絶対パスに`case_id.json`を作る。`append-llm-case-event`は同じ保存先の`events/case_id/event_id.json`へ判断履歴を追加する。worktree、`data/`、`.tmp/`等のGit管理下は拒否する。保存ディレクトリは新規作成時に0700、ファイルは0600とし、既存ディレクトリがgroup/otherに開いていれば拒否する。同じIDは上書きせず失敗する。これらのファイルはGit・DB・履歴retention・`cleanup-workspace-temp`の対象外なので、恒久保存先のバックアップは利用者の通常の個人データ保全に含める。作業worktreeの終了や正本DBの履歴削除でも残る。

このMacでの共通の恒久保存先は`$HOME/Library/Application Support/atproto-survey/llm-cases`とします。全task・worktreeで同じ保存先を`--store`に明示し、CLIの暗黙の既定値とは扱いません。初回登録時にCLIが作成します。保存先への書込みに承認が必要なら取得し、worktree内や別の一時領域へ代替しません。バックアップの設定・復旧確認は別途必要で、保存先の確定だけでは長期保全済みとはしません。

保存する契機と作業中の扱いは[事例保存の作業手順](../workflows/llm-model-evaluation.md#作業中に得た事例の保存)に従います。Codexが作業ごとのdraftを`.tmp/`に作り、登録後に保存した事例を確認する場合は次を使います。draftは保存成功と内容確認の後、不要なら削除して構いません。

```sh
bin/project-registry register-llm-case --store "$HOME/Library/Application Support/atproto-survey/llm-cases" --file .tmp/case-draft.json
bin/project-registry append-llm-case-event --store "$HOME/Library/Application Support/atproto-survey/llm-cases" --file .tmp/case-event.json
bin/project-registry show-llm-case --store "$HOME/Library/Application Support/atproto-survey/llm-cases" example-read-only-1
bin/project-registry verify-llm-cases --store "$HOME/Library/Application Support/atproto-survey/llm-cases"
```

## JSON契約

draftは次の形を使う。`payload`は実際のprompt、evidence、model出力、解析素材などをJSONとして含められる。`captured`は当時保存した内容、`reconstructed`は後から組み立てた内容、`missing`は不在を意味する。入力と実行条件を後から現在DBやURLから再構成した場合、`captured`にはしない。`missing`に`payload`は置かない。

```json
{
  "schema_version": 1,
  "case_id": "example-read-only-1",
  "task": "triage",
  "origin": {"kind": "evaluation", "source": "report name, attempt and source path"},
  "input": {"status": "captured", "payload": {"prompt": "...", "evidence": {}}},
  "output": {"status": "captured", "payload": {"summary": "..."}},
  "execution": {"status": "captured", "payload": {"model": "...", "prompt_sha256": "...", "code_commit": "..."}},
  "used_for_adjustment": false,
  "assessments": [{"claim": "write support exists", "verdict": "unsupported", "authority": "human", "reason": "saved input shows read only"}],
  "sanitization": {"human_reviewed": false}
}
```

`task`は`triage`または`repository_analysis`。`origin.kind`は`saved_run`、`evaluation`、`manual`、`reconstructed`。`source`にはrun ID、report/corpus名、attempt、入手元などを後から照合できる程度に書く。`execution.payload`には判明したmodel、runtime、prompt/template/hash、推論設定、コードrevision等を記録し、不明な条件は推測せず`missing`または`reconstructed`で示す。`assessments`は空でもよい。各項目は単一の主張と`verdict=supported|unsupported|uncertain`、`authority=human|codex|model|unreviewed`、理由を持つ。人が確認した部分判断も、全文の正解や他fieldの正しさは意味しない。`used_for_adjustment`は登録時点までにその事例をpromptや規則の調整に使ったかを表す。後から使った場合は同じ事例IDへ`adjustment_used` eventを追加する。入力・出力・実行条件を改変した新事例だけ別IDで登録する。

## 後から付く判断と確認

`append-llm-case-event --store ABSOLUTE_PATH --file EVENT.json`は、入力・出力・実行条件を複製せず、既存事例IDへ追記する。event draftには`schema_version=1`、`case_id`、一意な`event_id`、`kind`、`authority`、`source`、`reason`を必須とする。`source`は後から確認できる判断元、`reason`はその判断の理由。登録時にUTCの`recorded_at`を付ける。

- `kind=adjustment_used`: prompt・規則の調整へ初めて使ったときに追記する。調整前に保存した`false`が一覧で未使用と誤表示されないよう、利用状態は一度`true`になれば維持する。
- `kind=snapshot_reviewed`: 保存済みの伏字・機密・欠落を人が確認したときに追記する。`authority=human`だけを許す。後にsnapshotそのものを変える必要が生じた場合は新事例として登録し直す。
- `kind=assessment`: `assessment: {"claim":"...","verdict":"supported|unsupported|uncertain"}`を追加する。既存判断を訂正する場合は`supersedes`に現在有効な判断ID（初期判断は`base:0`等、追加判断はevent ID）を指定する。以前の判断は履歴へ残り、`effective.assessments`には訂正後だけが出る。

```json
{"schema_version":1,"case_id":"example-read-only-1","event_id":"prompt-tuning-1","kind":"adjustment_used","authority":"codex","source":"prompt trial 1","reason":"this case informed the revision"}
```

同じevent IDの上書き、存在しない事例・訂正元、複数の訂正先が同じ判断を置換する分岐は拒否する。`show-llm-case`は固定した`case`、追記された全`events`、現在有効な`effective`を分けて返す。`list-llm-cases`は`effective`の確認状態・調整利用・有効判断数とevent数を表示する。初期`false`だけを拾って未使用の評価用事例とみなさず、必ず`effective.used_for_adjustment`を参照する。

個人用・非公開の保存と参照では、人による事前確認や公開用の匿名化を必須にしない。公開資料の原文、project名、公開identifier、時刻、通常の作業記録は、判断の再現に必要な範囲で保持する。認証情報の混入は防ぐが、利用者へ毎件のプライバシー確認を求めない。公開・外部送信・公開fixtureへの転用時に、その範囲に応じた除去・最小化を改めて確認する。

現行の登録処理はsecret名のkey（`Set-Cookie`を含む）、Bearer token、文字列中の代表的な`api_key`等の値、URLのuserinfo/query/fragmentを自動で伏せる。伏せたJSON pathは`sanitization.auto_redacted_paths`に残し、その場合`human_reviewed`をfalseに戻す。既に`[redacted]`の値は再び変更したと数えず、伏字済みdraftを新IDで登録する場合も元の伏字pathを引き継ぐ。自動処理はすべての秘密を検出できず、逆に非秘密のURL要素も除去するため、Codexが登録後に`show-llm-case`で保存内容と必要な意味の欠落を確認する。伏字によって必要な入力が欠けた事例は、元の実行を厳密に再現できないと扱う。人が実際にsnapshotを確認した場合だけ、その事実を`snapshot_reviewed` eventで追記する。

事例とeventの各ファイルは正規化JSONのSHA-256を持つ。`show-llm-case`と`verify-llm-cases`は構造、digest、event参照を検査する。digestは誤変更の検出用で、署名や真正性証明ではない。`input.status`が`captured`でない事例は当時入力の厳密な再実行に使えない。`effective.human_reviewed`はsnapshot確認の来歴で、ローカル参照の許可や判断の正しさを表さない。人手goldとして使えるのは、人が実際に確認した判断の範囲だけであり、`authority`がhuman以外の判断を昇格させない。

既存`export-llm-evaluation-corpus`と`export-repository-analysis-corpus`は保存済み資料から固定比較用caseを選ぶ入口で、過去model出力はreferenceである。`export-investigation-fixture`はGitに置く最小fixtureを作るためprompt・本文等を落とす。非公開事例の原文保存には直接使わない。必要な時点でこれらの出力や当時のreportをdraftへ明示的に写し、実際に保存されていた入力と再構成入力を区別する。
