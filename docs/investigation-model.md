# Investigation Model

project 調査の責務境界と evidence の扱いを定める正本文書です。source ごとの具体的な
heuristic と request 数は [registry-internals.md](registry-internals.md)、保存 field は
[project-record.md](project-record.md)、未確定の設計論点は [notes/](notes/) を参照してください。

## 目的

- collector の追加や変更で読む working set を小さくする
- 外部から収集した事実と、それを project へ反映する判断を分離する
- 保存済み fixture から integration / evaluation を反復できるようにする
- confirmed、candidate、derived / heuristic を混同しない
- source の失敗を他 source の結果から切り離す

## Pipeline

調査 pipeline は次の責務に分けます。

1. query planning
   - seed と既存 project 情報から次に調べる query を作る
   - deterministic plan を基準にし、LLM plan は補助とする
2. collector
   - 外部 source を読み、structured evidence と failure を返す
   - 接続済み collector は入力 project dict を変更しない
3. integration
   - evidence を既存 project field の confirmed / candidate / observation へ反映する
   - ownership や first-party 性のような project 文脈が必要な判断はここで行う
4. evaluation
   - integration 後の project から ownership、ATProto寄与度、service status などを計算する
   - network / DB に依存しない rule-based 関数を優先する
5. save
   - `finalize`、duplicate / merge、promotion の再計算後、既存の `save_project()` で保存する

`scripts/project_registry.py` は既存 CLI / top-level Python API の互換 facade と orchestration を
担当します。module 分割自体は公開 API の変更を意味しません。

## Evidence classification

`EvidenceRecord.classification` は次の意味で使います。

- `confirmed`
  - manual seed や first-party source など、project への帰属を強く確認できた観測
- `candidate`
  - 関連性はあるが、project への帰属や意味を追加確認する必要がある観測
- `derived`
  - confirmed / candidate の観測から決定的に導出した query、log、正規化結果
- `heuristic`
  - score や文字列類似など、誤判定し得る規則による推定

classification は保存先 field の confidence と同義ではありません。collector が返した
classification を integration が project 文脈で再評価し、confirmed field、candidate field、
observation のどこへ反映するかを決めます。

## Evidence contract

実装済みの schema version 1 は次を持ちます。

- `EvidenceBundle`
  - `schema_version`
  - `project_key`
  - `collected_at`
  - 順序付きの `records`
  - `failures`
- `EvidenceRecord`
  - `kind`, `source`, `classification`, `observed_at`
  - JSON-compatible な `value`
  - query、score、repo path などの `context`
- `CollectorFailure`
  - `source`, `operation`, `target`
  - `category`, `message`, `retryable`

実装済みの failure category は `timeout`, `rate_limit`, `remote_server_error`,
`network_error`, `collector_error` です。retryable は分類結果であり、実際の retry policy が
接続済みであることを意味しません。

## 正本と寿命

実装済み:

- 通常の enrich では evidence bundle は process 内の一時値として扱う
- integration 後の project row が保存済み調査結果の正本になる
- `record_json` は高信頼 record、`ops_json` は候補・観測・評価・運用情報を保持する
- raw evidence は通常 DB や run artifact へ保存しない

制約:

- project row だけでは collector の raw response を完全には再現できない
- heuristic の再評価は、保存済み project field または明示的な fixture に残った材料に限られる
- raw evidence の永続化を導入する場合は、保存場所、retention、機密情報除去を先に決める

## 調査状態の読み方

次を同一視しません。

- evidence がない
  - source が何も返さなかった場合と、source を実行していない場合の両方を含み得る
- failure がある
  - source の一部または全部を取得できなかった
- partial result
  - record と failure が同じ bundle に存在するなど、一部の結果だけ得られた
- confirmed evidence がない
  - candidate や heuristic まで存在しないことを意味しない

未接続:

- source 単位の `success / partial / skipped / rate_limited / failed` 完了状態
- cursor、打ち切り理由、再試行可能時刻の標準表現

したがって現状では「evidence がない」を否定的事実として扱いません。source 完了状態の設計は
[notes/collector-reliability.md](notes/collector-reliability.md) に置きます。

## Replay と fixture

動作確認済み:

- `EvidenceBundle` の JSON round-trip
- synthetic GitHub repo snapshot fixture から collector を動かさず ownership / ATProto寄与度を再評価
- collector non-mutation、partial failure、rate-limit classification の unit test

未接続:

- 実 project を継続的に追加する golden corpus
- fixture を指定して integration / evaluation を実行する operator 向け CLI
- heuristic version 間の判定 diff と regression 集計

詳細な検討は [notes/evidence-replay.md](notes/evidence-replay.md) を参照してください。

## LLM の位置づけ

- LLM は query planning と editorial / classification 補助に使う
- source identifier の新規提案は原則 candidate に留める
- lexicon は rule-based に解決済みのものだけを採用する
- service status は rule-based probe evidence を優先する
- ownership や高いATProto寄与度を LLM 単独で確定しない
- LLM failure があっても deterministic plan と他 collector を継続する

## 接続状況

実装済み:

- deterministic / LLM query planning
- GitHub repository metadata / README / tree / lexicon hint collector
- GitHub repository search collector
- Bluesky actor / post / author feed collector
- 上記 evidence の project integration
- ownership、ATProto寄与度、service status の純粋評価本体

未接続の legacy mutating 経路:

- lexicon resolution
- recent record sampling
- service top page probe と first-party follow-up
- deep resource page probe

将来目標:

- legacy collector を同じ evidence 境界へ移す
- relation evidence と source 完了状態を明示する
- source 別 retry / rate-limit / cache policy を接続する
- fixture corpus で heuristic の変更を比較可能にする

関連する検討文書:

- [notes/identity-ownership-evidence.md](notes/identity-ownership-evidence.md)
- [notes/service-verification.md](notes/service-verification.md)
- [notes/evidence-replay.md](notes/evidence-replay.md)
- [notes/collector-reliability.md](notes/collector-reliability.md)
