# Evidence Replay Notes

> 作業用・検討用文書です。現行仕様の正本ではありません。evidenceの現在の契約は
> [../investigation-model.md](../investigation-model.md) を参照してください。

## 目的

- 外部APIや実DBを使わずにintegration / evaluationを反復する
- 誤判定projectを回帰fixtureへ追加する
- heuristic変更前後の判定差を説明可能にする
- 全件再enrichなしでownership、寄与度、service statusを比較する

## 動作確認済み

- `EvidenceBundle` schema version 1のJSON round-trip
- synthetic GitHub repo snapshot fixtureの読み込み
- fixture中のlexicon hintからownershipとATProto寄与度を再評価
- collector non-mutation、GitHub採用閾値、partial failureのunit test
- temp SQLiteでの `enrich_project -> save_project -> fetch_project`

現在のfixtureはsyntheticな最小例であり、実project corpusではありません。

## 未接続

- fixture directoryを走査する専用runner
- collector outputをsanitized fixtureとして保存する導線
- project入力、raw evidence、期待evaluationを一つのcaseとして管理するschema
- heuristic versionや実行commitの記録
- old/new evaluationのstructured diff
- operator向けCLI

## Corpus案

1 caseは少なくとも次を持つ想定です。

- `case_id`と誤判定の説明
- collector前のproject snapshot
- source別evidence bundle
- 期待するconfirmed / candidate / rejected relation
- ownership、ATProto寄与度、service statusの期待値
- 許容する未確定値

fixtureへ取り込む際はtoken、private endpoint、個人情報、不要な本文を除去し、判断に必要な最小材料へ縮約します。

## 比較結果

将来のrunnerは次を区別して報告する必要があります。

- intended fix
- unrelated change
- regression
- previously unknownから確定への変化
- evidence不足で比較不能

単純なJSON全体diffではcandidate順序やtimestamp noiseが大きいため、evaluation結果とrelation単位の
semantic diffを優先します。

## 未検証

- 実projectを何件集めれば主要heuristicの退行を検知できるか
- fixture更新を通常の調査運用へ組み込む負担
- raw responseを残さず縮約evidenceだけで再現できる誤判定の範囲

## 将来目標

- 実際に修正した誤判定を必ずcorpusへ追加する運用
- source収集とevaluationを独立してbenchmarkする
- heuristic変更時にcase別diffと集計を自動出力する
