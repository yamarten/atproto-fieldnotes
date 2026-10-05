# 文書管理方針

この文書は、正本文書の責務、配置、更新、統合・廃止の判断基準を定めます。

## 正本文書全体の責務

| 文書 | 担当 |
| --- | --- |
| [README](../README.md) | 概要、最短の開始手順、目的別の案内 |
| [AGENTS](../AGENTS.md) | エージェント向けの参照案内、必須検証と実データの境界 |
| [brief](brief.md) | 目的、対象・非目標、方向性、優先順位、成功の判断 |
| [TODO](developer/todo.md) | 未完了作業、確認待ち、条件付き候補、着手条件 |
| [利用ガイド](user/guide.md) | 初回準備から登録・調査・判断まで、基本用語 |
| [接続設定](user/configuration.md) | LLM・認証・収集対象・設定ファイル |
| [Python環境](user/python-environment.md) | 処理系の準備・検証・切替・復旧・削除 |
| [CLI](user/cli.md) / [Web UI](user/web-ui.md) | 入出力・副作用・操作・起動方法 |
| [architecture](developer/architecture.md) | moduleの責務・依存方向・処理の接続 |
| `developer/specs/` | 現行の機能・データ・判断の契約と制約 |
| `developer/workflows/` | 開発・検証・評価の手順 |
| `notes/` | 有効な保留と、未決事項の判断・再開に必要な材料 |

各文書は担当する内容が変わった時に更新します。プロジェクト全体の目的・統治は`docs/`直下、開発・保守は`docs/developer/`へ置きます。

## 開発仕様

| 文書 | 一緒に保守する内容 | 隣接する責務 |
| --- | --- | --- |
| [Project record](developer/specs/project-record.md) | データ表現、fieldの意味、保存・更新、履歴、整合性 | 値を決める規則は各機能仕様へ |
| [発見と取り込み](developer/specs/discovery-and-sync.md) | source設定、候補発見、既存projectへの照合、新規登録、watermark、取得上限 | 採用判断はproject境界、登録後の調査はinvestigationへ |
| [Investigation](developer/specs/investigation.md) | 調査pipeline、collector、観測、evidence、部分失敗、replay | 帰属・評価結果を決める規則、物理保存契約を再掲しない |
| [Project境界](developer/specs/project-boundaries.md) | resource帰属、relation解釈、信頼・採用、projectの統合・分離 | resource採用とproject mergeは別の判断。transactionと履歴はproject recordへ |
| [Project評価](developer/specs/project-evaluation.md) | 説明更新、分類、稼働状態、紹介候補・順位、活動・追跡状態 | 観測方法はinvestigation、fieldの保存はproject recordへ |
| [Background jobs](developer/specs/background-jobs.md) | request/event/result、中断・再開、checkpoint、定期実行 | 各jobが行う業務処理はその機能仕様へ |
| [App record export](developer/specs/app-record-export.md) | field投影、readiness、validation、manifest、出力 | registry内部保存形や公開目標を担当しない |
| [Lexicon観測](developer/specs/lexicon-publication-catalog.md) | Lexicon公開情報の取得・保存・検証・型別処理 | 通常のproject候補への照合と運用切替。共通判断規則はproject境界へ |

開発・検証の手順は[変更の受入](developer/workflows/change-acceptance.md)と[LLM model評価](developer/workflows/llm-model-evaluation.md)を正本にします。通常調査でLLMに何を任せるかと、LLM構成を変更する際に何を比較するかを分けます。

## 残す内容と配置

読者の問いに答える単位でまとめます。操作手順、現在の契約、誤解を防ぐ例、判断を支える理由・反例を残し、同じ詳細条件を複数箇所で管理しません。概要は流れと意味を説明し、詳細を読まなくても対象と用途が分かるようにします。module数や文章量だけで文書を分割しません。

削除を検討するときは、その段落がなくなると何を判断・実行できなくなるかを確かめます。必要な意味が他の箇所にあれば重複を削り、現在の利用・保守・再開に不要なら移動せず削除します。短くするために操作の途中を省いたり、基本説明までリンク先へ追い出したりしません。

現行仕様はコード・通常経路と照合します。合意済みでも未実現の設計、比較用・暫定経路はその範囲を明示します。独立した機能は用途・本体との入出力・権限境界を示し、統合・撤去時に担当正本も整理します。

## TODOとnotesの更新

TODOには残る問題、次の判断、着手・再検討条件を記します。完了項目は外し、部分達成したら残件に書き直します。列挙順を実施順や承認と扱わず、試さなかった条件をすべて恒久的な確認義務にしません。

短い確認待ちはTODOで扱い、反例・比較材料・未決の選択肢が必要な間だけnotesを保持します。有効な保留はTODOから案内し、観測時点と現在状態を区別します。個人DBの復旧履歴、job ID、backupの所在・保持条件、非公開資料の索引はGit管理外の運用記録へ置きます。共有文書に必要な判断理由や再現条件は本文に残し、個人環境のファイルを開かないと理解できない参照を作りません。確定した契約・再利用可能な手順は仕様・workflowへ反映して重複を削除します。

notesは冒頭で、参照元の未決の問い、検討・評価の目的、本文が提供する判断材料を説明します。実験記録なら比較方法・結論・限界も本文から分かるようにします。資料ごとに、どの判断が閉じるか、何へ引き継げば役割が終わるかを示します。判断を見送る・追わない結論も閉じ方に含め、全例の調査完了を削除条件にしません。評価素材にREADMEがあれば目的・結果・解釈をそこで扱い、notesとの重複を避けます。統合・削除時は参照元も更新します。

完了経緯や試行結果は原則として作業報告とGit履歴に留め、文書全体のarchiveや転送stubを作りません。次の判断に必要な材料は現行本文に残し、旧worktreeや一時fileの再発掘を前提にしません。運用データ・backup・恒久archiveの削除は文書削除とは別に判断します。

## 編集と確認

本文は意味のまとまりごとに一段落一行で書きます。リンクは必要な仕様・根拠へ案内し、参照先で何が分かるかを示します。現行文書は可能な限りHEADだけで理解・利用できるようにし、必要な外部参照や実行条件のrevisionは保持します。

移動・統合時は担当正本と参照を揃え、本文のない節を削除します。編集後は成果物単体で読み直し、手順・条件・例が意味を保つこと、リンク・見出し参照・commandが有効であることを確認します。参照更新だけが目的の文書では本文を変更しません。

## 利用者向け用語の編集

README、brief、利用者向け文書は、操作や判断を理解できる日本語を優先し、意味と呼称は[利用ガイドの基本用語](user/guide.md#基本用語)を正本とします。コマンド名、オプション、JSON field、enumはバッククォート付きで原名を示します。画面の操作手順には実際の日本語ラベルを用います。開発文書ではコード検索と実装上の区別を優先し、内部用語を無理に訳しません。

- 一般的な外部projectとDBの登録プロジェクトを区別する。開発文書ではproject row、dict、table等の形も維持する。
- `run`は調査実行記録、一括実行、収集処理、background jobで単位が異なるため対象を添える。
- `confirmed`は自動判定にも使われ、利用者操作の「採用」と完全な同義語にはしない。`classification`もidentifier、relation、evidenceのどれかを明示する。
- `review`はqueue、signal、target、job、一般的な確認行為を一括置換しない。利用者向け機能名は「要レビュー一覧」「紹介候補一覧」とし、後者を「昇格候補」とは呼ばない。
- `sync`は双方向同期を意味せず、dry-runは「取り込み候補の確認」と説明する。`merge`は登録プロジェクトのマージに使い、調査根拠のintegrationには使わない。
- `source`、`surface`、`family`、`current`、`bounded`、`guard`等は固定訳を増やさず、文脈に応じて具体的に説明する。

Web UIは日本語を基本とし、JSON等の馴染みのある技術ラベルは原表記も使います。内部のAPI・DB値とは表示層で分離し、原文引用、利用者の入力、URL、識別子、製品・プロトコルの固有名詞、JSONや診断記録は改変しません。未認識の診断を推測で翻訳せず、日本語の案内に技術詳細を添えます。多言語切替は設けません。画面の改名時は関連する利用手順を同期します。

操作画面では、現在の状態、判断材料、操作の効果を見通せることを優先します。説明は必要になる操作の近くへ置き、常設する必要がなければ削ります。補足を開く場合は内容を予想できる名前にし、説明と設定を区別します。キーボードとタッチでも到達できるようにし、重要な警告をhoverだけに隠しません。コマンドやJSON等の読み飛ばしやすい情報を面積だけの理由で隠さず、文字縮小より先に重複・余白・配置・強調を整理します。ボタンは自然な動作名にし、複数の意味を「・」で詰め込みません。
