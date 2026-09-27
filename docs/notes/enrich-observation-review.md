# 通常enrichの初回運用評価

triage再利用・一件先行取得・比較用観測は通常CLI／workerへ接続済みです。2026-09-26の導入後、新しいコードを読んだ週次enrichが2回完走した時点で初回評価します。対象job IDと期間、2回の完走は未確認です。統合や一時DBの検証を、実運用での観測開始・完走と扱いません。検索計画の再利用は採用していません。

## 評価開始の確認

既存job/checkpointでworkerの開始・対象・完走を照合します。導入前から動いていたworkerと中断runを除き、project履歴だけでjob完走を認定しません。観測導入でtransportのpolicy hashが変わるため、初回triageは再生成されます。同条件の2回目以降が比較対象です。

Webの新しい履歴readerはserver reload後に反映されますが、反映確認は未実施です。それまでは統合済みcheckoutの開発report・CLIで集計・replayを確認できます。triage再利用と先行取得は新規CLI／workerで有効となり、この目的だけのWeb再起動は不要です。

## 次に判断すること

[集計手順](../developer/workflows/enrich-observation-review.md)に従い、同条件の連続run、観測の完全性、対象分布を確かめます。不足すれば通常運用を続け、次の完走時に再確認します。2回は確認の契機であり品質の証明ではありません。

- **Triage再利用**: `metrics.triage_reuse`のhit/miss理由、実triage request数・時間、収集失敗と新しい候補の扱いを確認します。追加最適化は実際のmiss理由から判断します。過去履歴からの約17%短縮見積りは設定不変を仮定した値で、実績ではありません。
- **先行取得**: 同程度の対象とtriage hit率を持つbatchの`execution.batch_elapsed_ms`、先行使用／未使用、join待ち、collector requestとrate limitを併読します。重なった処理時間を合算して短縮実績にしません。効果が小さい、無効化やquota消費が多い場合は`--no-prefetch`で比較します。実外部取得とLLMを重ねた時間・負荷、大きなprojectの追加メモリは未測定です。2回完走は観測の初回評価条件であり、先行取得の受理条件ではありません。
- **検索計画**: 入力・計画・実行語の変化と、既知候補の再発見／新出／最終採用を分けます。旧履歴には当時の完全planning入力がなく、triage材料の一致だけで検索固定の同等性を保証できません。新旧計画を同時期・同予算で実行しない限り、旧計画が拾えたはずの材料は断定しません。
- **保存・計測負担**: 実際の圧縮増分、上限省略、write lock競合、長期容量を確認します。導入前の保存outputによるproxyは実planningやSQLite全体の増加量を測ったものではありません。HTTPごとの統一計測は未実装で、操作時間から必要なsourceを絞って再検討します。

必要な事例はretention前に[非公開事例保存](../developer/workflows/llm-model-evaluation.md#作業中に得た事例の保存)を検討します。新規jobや追加比較推論をこの確認のために自動実行する合意はありません。保存・意味の契約は[Investigation](../developer/specs/investigation.md#将来比較用の記録)、制御方式の固定応答比較は[変更の受入](../developer/workflows/change-acceptance.md#enrichの逐次先行取得比較)を参照してください。
