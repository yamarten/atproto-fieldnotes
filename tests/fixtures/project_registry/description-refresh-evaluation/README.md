# 説明更新の比較素材

2026-09-23の限定評価で使った架空素材と実LLM出力です。改善案は不採用で、ユーザーは追加試行後のタスク完了を了承しました。**モデルの失敗出力は正解labelではありません。** 実在project全体の精度を表すcorpusでもありません。

- `triage/`: 現行2生成と共通prompt整理案10生成。通常triageの構築・Qwen suffix・transport・適用・保存を通した結果。
- `description-only/`: 説明だけに絞る実験6生成。入力選択・prompt・出力schema・suffixなしは実験専用。通常transport・説明field正規化・editorial適用・保存codecを使用し、通常enrichには未接続。
- 各`review.md`: 原文・更新前・生出力・保存後全文とAgent評価。ユーザーが結果を受理したことと、各文章を正解として認定したことは区別する。
- 入力JSON、prompt全文、suffix、各出力JSON、attempts、metrics、statisticsを保持。`recorded-runner.py.txt`は実行時のスクリプト記録であり、配置変更後に直接実行する入口ではない。公開用に設定参照先のpathだけ相対化している。認証値自体は含まない。

通常promptとversionは開始HEAD `f7eff882fe18d846f7ac8c2efbdeabc5da363a69`の状態へ戻しています。候補promptはこの素材内だけに保持します。出力JSONのgit状態は実行時の記録であり、最終commitの状態ではありません。本文内の日付も構成素材の条件です。

保存結果の再検証はrepository rootから次を実行します。通信せず、`.tmp/`に作った一時DBを通常codecで読み書きし、成功時に削除します。triageは60条件、説明専用は30条件です。

```bash
PYTHONPATH=scripts bin/_project-registry-python verification tests/fixtures/project_registry/description-refresh-evaluation/triage/verify_saved_outputs.py
PYTHONPATH=scripts bin/_project-registry-python verification tests/fixtures/project_registry/description-refresh-evaluation/description-only/verify_saved_outputs.py
```

機械検証は保存・保護の再現であり、意味上の品質合格ではありません。生成を再開する場合は、新しい費用枠と評価設計を先に定め、元のrunner記録・実送信prompt・model metadataから構成を確認してください。JSON内の期待文やAgent評価を自動的にgoldへ昇格しません。今回の素材は全件が既知素材となったため、新方式の確認には別の未使用素材を用意します。

所有者は母艦taskです。次に説明更新方式・model・受入条件を見直す時、必要な比較素材を選び直し、代替する評価が保存できた時点で保持範囲を再検討します。母艦にある旧`.tmp/temporal-validation-20260915/`の実観測資料は本corpusとは別であり、今回変更・削除していません。
