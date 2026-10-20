# 通常enrichの観測から改善案を評価する

この手順は、通常enrichの保存履歴から、検索計画の変動、目的別LLM負荷、収集の重い操作、triage再生成の材料を調べるためのものです。集計はread-onlyで、検索・LLM・正本DB更新を実行しません。方式の変更や比較実験の実行は別の判断です。[観測仕様](../specs/investigation.md#将来比較用の記録)と[保存契約](../specs/project-record.md#将来比較用観測の保存)が記録の意味を定めます。

## 評価を始める条件

初回は導入後の週次enrichが2回完走した時点で始めます。既存のjob/checkpoint記録で対象・完走・開始終了時刻を確認し、導入前のworkerや中断runを区別します。project履歴だけで週次jobの完走を認定しません。2回は初回確認の契機であり、品質の証明や必要sample数ではありません。評価の目的と再利用・保存負担の検討は[TODO](../todo.md#通常enrichの初回評価)を参照します。

## 集計

作業checkoutで実行します。共通Python wrapperは既存の依存環境を選びます。日時は保存済み`completed_at`と同じISO表記・offsetを使い、下限を含み上限を含まない文字列範囲です。以下の日付は置き換える例です。

```bash
mkdir -p .tmp/enrich-review
bin/_project-registry-python enrich-observation-report scripts/enrich_observation_report.py \
  --db-path data/survey.db \
  --since 2026-10-01T00:00:00+00:00 --until 2026-10-20T00:00:00+00:00 \
  --limit 20000 --details > .tmp/enrich-review/report.json
```

`mode=ro`・`query_only`・単一read transactionで圧縮履歴を読みます。decode失敗は停止し、壊れたrowを黙って分母から除きません。`limit_reached=true`なら対象期間を分けるか上限を増やし、週次対象全体を読めたかを確認します。結果のrun ID一覧・順序hash・日時範囲と既存job記録を合わせて、評価対象を固定します。集計の対象はalias familyの統合前後を自動連結せず、保存されたproject keyごとです。

1. `coverage`と`application_status`で新規観測の保存割合、欠落、秘匿除去、上限超過、未反映を確認する。完全sectionだけを比較に使う。旧履歴の欠落を0件・同一入力とみなさない。
2. `consecutive_pairs`でprojectごとの期間内連続run数、比較可能数、記録された設定が同じ組、さらに入力が同じ組を確認する。固定生成は`input`、旧LLM方式は`prompt`を照合し、異なる方式同士と入力の欠けた組は比較から除く。同じ条件・入力でも、plan全体の差と実行用検索語の差を分ける。reasonだけの違いを探索範囲の変化と数えない。source hashに無関係なcode変更が含まれることもあるため、差がある組の採否は元資料を確認する。
3. `llm_endpoint_reported`でpurpose別の件数・時間・token合計とtoken報告件数／欠落件数を確認する。`llm_retained_requests_by_settings`でmodel・推論設定等を分ける。個別16行の上限で省略があれば設定別groupは全requestを代表しない。cached input/reasoningはinput/outputの内数となり得るため合算しない。duration÷tokenをprefill/decode時間やTTFTとしない。
4. `operation_elapsed_including_wait_ms`で重い操作を探し、`collector_existing_counts`のstatus・cache・既存件数を併読する。操作時間は実測だが、collector request count全体は実測HTTP数ではない。検索のattempt・retry待機の詳細は個別runで確認する。
5. `triage_reuse_reasons`と`triage_changed_fields`で再生成の頻出理由・変化fieldを確認する。旧entry・失敗後・説明できないfingerprint変化を、材料変化なしの無駄な再推論と断定しない。候補の本文差を確認する場合は保存された入力・出力の`llm_source.evidence`を使う。これは前回生成との比較で、単なる直前runとの差ではない。
6. `candidate_occurrences`はqueryごとの延べ候補数。既知、初期収集で出現、検索時に出現と最終confirmedを分ける。同じURLの再採用、複数queryやauthor feedの重複を、新規発見件数にしない。個別例で前後・source・既存bundleを確認する。

個別runの保存入力、整形前後の計画、実行と候補を読むには次を使います。promptなどの非公開資料を含むため、そのまま公開・外部送信しません。

```bash
bin/_project-registry-python enrich-observation-report scripts/enrich_observation_report.py \
  --db-path data/survey.db \
  --run-id inv:対象ID > .tmp/enrich-review/run.json
```

## 効果と運用費の比較

- **Triage再利用**: `metrics.triage_reuse`のhit/miss理由、実triage request数・時間、収集失敗と新しい候補の扱いを確認します。追加最適化は実際のmiss理由から判断します。
- **先行取得**: 同程度の対象とtriage hit率を持つbatchの`execution.batch_elapsed_ms`、先行使用／未使用、join待ち、collector requestとrate limitを併読します。重なった処理時間を合算して短縮実績にしません。効果が小さい、無効化やquota消費が多い場合は`--no-prefetch`で比較します。実外部取得とLLMを重ねた時間・負荷、大きなprojectの追加メモリは未測定です。
- **検索計画**: 現行の固定生成と旧LLM方式を区別して読む。旧planning requestの時間を現行のLLM負荷に混ぜない。入力・計画・実行語の変化と、既知候補の再発見／新出／最終採用を分けます。観測導入前の旧履歴には当時の完全planning入力がなく、triage材料の一致だけで検索固定の同等性を保証できません。新旧計画を同時期・同予算で実行しない限り、旧計画が拾えたはずの材料は断定しません。
- **保存・計測負担**: 実際の圧縮増分、上限省略、write lock競合、長期容量を確認します。導入前の保存outputによるproxyは実planningやSQLite全体の増加量を測ったものではありません。HTTPごとの統一計測は未実装で、操作時間から必要なsourceを絞って再検討します。

制御方式の固定応答比較は[変更の受入](change-acceptance.md#enrichの逐次先行取得比較)を参照します。

## 判断と再開

同条件の連続runが少ない、欠落が多い、通常対象の分布を覆わない場合は通常運用を継続し、次の週次完走時に同じ集計を行います。追加の周期jobは作りません。同じmodel IDの内容、endpoint側既定設定、認証先、commandの環境は完全には固定できず、「同条件」は記録した条件の一致です。backend変更が分かっている期間を混ぜません。

材料が揃ったら、入力同一率、実検索語の変化、既知候補／追加候補の具体例、削減可能な実request時間、候補漏れの未確認範囲を示して採否を検討します。新旧検索計画を同じ時期・同じ予算で両方実行していない通常履歴だけでは、旧計画の取りこぼしや再利用後の品質同等性を断定しません。比較実験が必要なら対象・予算・評価基準を別途提示します。

判断に必要な具体例は、一時資料の削除や90日retention前に[LLM事例保存手順](llm-model-evaluation.md#作業中に得た事例の保存)で保存を検討します。未評価の出力を人手goldにせず、採用判断・集計条件・未確認範囲はnotesへ残します。必要な比較資料を一時worktreeだけに置いたまま終了しません。
