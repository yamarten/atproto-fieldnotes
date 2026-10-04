# 説明更新方式の比較：目的・結果と保存素材

[TODOの説明更新改善](../../../../docs/developer/todo.md#説明更新の品質とservice監視)で、projectの説明・分類をまとめて行う通常triageの改善と、説明専用処理への分離を比較するための資料です。再調査で終了・機能変更・誤記が分かった時に、保存済みの短い見出し・概要・特徴一覧（`tagline / summary / features`）を適切に更新し、紹介前の確認・訂正負担を減らすことが目的です。現行の更新・保護契約は[説明更新の仕様](../../../../docs/developer/specs/project-evaluation.md#保存済みprofileの更新)、実用上の比較基準は[評価原則](../../../../docs/developer/workflows/llm-model-evaluation.md#実用上の評価原則)を参照してください。

2026-09-23の限定評価では共通prompt整理案を不採用とし、説明専用案への置換も追加根拠待ちとして現行を暫定維持しました。専用案は時制や見出しを改善する一方、旧機能の技術情報を省くためです。ただし旧PDS設計の欠落だけでは不採用の根拠として不十分で、紹介準備に必要な補完・訂正が減るかは未確認です。追加試行は終了し、通常処理への接続や本番適用には進んでいません。

## 比較方法と分かったこと

同じQwen 3.6 35B A3Bで、既存説明と新しい根拠を与えた架空素材を比較しました。通常方式、通常promptの説明更新規則を整理した案、説明だけに入力・出力を絞る案の計18生成です。終了、機能変更、部分終了、誤記訂正、機能追加、一時的な取得失敗を扱いました。方式ごとの対象・反復数は揃っておらず、全素材での直接比較ではありません。専用案では入力・出力・suffixも変えているため、タスク分割だけの因果効果は分離できません。

| 素材 | 観測した違い | 採否判断に残る問い |
| --- | --- | --- |
| Reading Loomの終了 | 現行は旧機能の現在形と根拠のない「後継は存在しない」を残す。専用案は終了を明示するが、旧機能の利用者PDS保存が2反復とも欠落 | 利用可能性の誤認を減らす利点と、技術紹介のために情報を補う負担のどちらが大きいか |
| Reading Loomの機能変更 | 現行summaryは旧PDS設計と現在の検索を分けるがtaglineは旧機能。専用案はtaglineを直す一方、旧設計を省く | 見出し訂正と旧設計の補完が選定・紹介準備にどう影響するか |
| Workshop Atlasの部分終了 | 専用案は現行カタログとbrowser内CSV処理を保持するが、旧予約の利用者PDS保存を曖昧化し「代わりに」と置換を示唆 | 現在の用途理解と、過去の技術を紹介する時の補完を分けて評価できるか。現行との直接比較は未実施 |
| Patch Courierの誤記訂正 | 専用案は署名・CID検証libraryという用途を保持し、入力方法と将来download helperを省略 | 用途を変えない詳細の省略が実際に困るか。現行との直接比較は未実施 |

共通prompt整理案では根拠外断定が反復し、機能変更で旧技術の欠落と非使用断定も加わりました。一時障害の不正な変更種別・架空根拠IDは保存側で拒否されましたが、根拠IDが正しい不適切な文面は、自動反映を許可した条件では保存され、保護条件では更新案として残りました。形式・参照・編集保護の検査と、意味上の正しさを分けて評価する必要があります。

行動・負担の評価はCodexの推定であり、利用者の確認時間・訂正時間、実在project全体の精度、通常enrich全体の追加費用は測っていません。Webの根拠・変更前後previewから補えるとしても、通常の操作で欠落に気づき、補完できるかは未評価です。終了・変更の難例だけで選定全体への効果を判断せず、次の比較では通常のサービス・共有Lexicon・技術的特徴も含めます。

## 保存素材と再検証

**モデルの失敗出力は正解labelではありません。** 実在project全体の精度を表すcorpusでもありません。

- `triage/`: 現行2生成と共通prompt整理案10生成。通常triageの構築・Qwen suffix・transport・適用・保存を通した結果。
- `description-only/`: 説明だけに絞る実験6生成。入力選択・prompt・出力schema・suffixなしは実験専用。通常transport・説明field正規化・editorial適用・保存codecを使用し、通常enrichには未接続。
- 各`review.md`: 原文・更新前・生出力・保存後全文と当時のAgent評価。旧技術の欠落を理由とした当時の不採用評価と、その後の用途に照らした判断は区別し、採否の現在の位置づけは本READMEを参照する。ユーザーが結果を受理したことと、各文章を正解として認定したことも区別する。
- 入力JSON、prompt全文、suffix、各出力JSON、attempts、metrics、statisticsを保持。`recorded-runner.py.txt`は実行時のスクリプト記録であり、配置変更後に直接実行する入口ではない。公開用に設定参照先のpathだけ相対化している。認証値自体は含まない。

比較時の通常promptは`triage/baseline-prompt.md`、候補promptは`triage/candidate-prompt.md`に収録しています。候補promptは通常処理へ採用していません。出力JSONのgit状態は実行時の条件の記録で、再検証にそのcommitのcheckoutは必要ありません。本文内の日付も構成素材の条件です。

保存結果の再検証はrepository rootから次を実行します。通信せず、`.tmp/`に作った一時DBを通常codecで読み書きし、成功時に削除します。triageは60条件、説明専用は30条件です。

```bash
PYTHONPATH=scripts bin/_project-registry-python verification tests/fixtures/project_registry/description-refresh-evaluation/triage/verify_saved_outputs.py
PYTHONPATH=scripts bin/_project-registry-python verification tests/fixtures/project_registry/description-refresh-evaluation/description-only/verify_saved_outputs.py
```

機械検証は保存・保護の再現であり、意味上の品質合格ではありません。生成を再開する場合は、新しい費用枠と評価設計を先に定め、元のrunner記録・実送信prompt・model metadataから構成を確認してください。JSON内の期待文やAgent評価を自動的にgoldへ昇格しません。今回の素材は全件が既知素材となったため、新方式の確認には別の未使用素材を用意します。

## この資料の役割が終わる条件

TODOの方式選択を判断する間、現行・候補の失敗と交換条件を確認する材料として保持します。方式選択を閉じる、改善案を追わないと決める、または代表的な失敗を引き継いだ新しい比較へ置き換える時に、仕様・TODOへ必要な結論を反映し、不要になった素材と本資料を削除します。全素材の成功や実在projectの個別訂正完了は保持終了の条件ではありません。これより前の実観測による監査・8出力は本corpusに含みません。
