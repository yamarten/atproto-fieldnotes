# Repository帰属判断の未決事項

[TODOの信頼・帰属policy](../developer/todo.md#信頼帰属policyの追加接続)で、公式repositoryや代表サイトの採用規則を広げる必要があるか判断するための事例集です。公式repoの見逃しを減らしつつ、依存先や別projectのrepoを誤採用しないことが目的です。未決の問いは、page自体がどのprojectに属するかと、そのpage上のlinkが公式repoを意味するかの二つです。観測時点は2026-08-24〜2026-09-08で、現在の公開page・DBは再確認していません。

同型の手動負担が蓄積するか、既存の判断境界に不足が確認された時に、複数の事例に適用できる規則を検討します。

## Linkの意味を決めきれない例

| 対象 | 記録時点の材料 | 未決の問い |
| --- | --- | --- |
| Airport | repositoryを示す数値badge | 明示source表示や単独provider link以外の視覚的表示を、どの一般的な根拠で支持するか |
| socialweb.computer | 複数repositoryの説明が併存 | 各linkがproject自身の公式repositoryか、外部projectへの参照か |
| obsidian-pds-sync | plugin名だけのlink | 名前の一致以外に、公式repositoryという意味を何で確定するか |
| Written World | 個人portfolio内の紹介とrepository link | 紹介箇所のproject帰属とlinkの意味をどう分けるか |
| didplc.directory | `go-didplc read replica`、`source of truth this mirrors` | 公式利用repositoryと外部dependency・mirror先の境界 |

最初の4種は当時のレビューでは正しいリンクと考えられたものの、一般化可能な直接表示が足りず保留しました。didplc.directoryは2026-08-27の範囲を限定した同一入力への3回評価で`SUPPORTED` 2回、`INSUFFICIENT` 1回でした。この結果を材料に、対象リポジトリがそのprojectで果たす役割を人が確認して採用を判断します。

同じ評価のEventsは`Fair source`表示だけで3回とも`INSUFFICIENT`でした。これは、repositoryの用途を判断するにはlicense表現に加えて根拠が必要な例です。`Open source`や`View source`等の明示表示は静的規則へ反映済みです。

## Page自体の帰属が先に必要な例

2026-08-27の再収集では、曖昧なリンクの多くが、URLの完全一致で特定した採用済み構成要素の外にありました。Tangledの共通ページやat-storeの商品ページのリンクは、表示が強くても対象project自身の構成要素からのリンクとは限りません。project名・既存プロフィールからページの帰属をLLMの関係判定処理（assessor）に補完させると、評価したい帰属を入力側で正しいものとして扱うことになります。

再検討する場合は、pageをproject componentとして採用する判断と、採用済みcomponent上のlinkの意味判定を分けます。配下docsを起点にする場合も、そのdocs rootの採用が必要かを現行policyと照合します。

2026-09-08のhomepage観測には、indigo／Ozone等の`atproto.com`、UFOs／Slingshotの`microcosm.blue`、複数Ruby関連projectの`ruby.sdk.blue`という共有URLがありました。共有URLには、異なるprojectが意図的に同じ案内面を使う場合も含まれます。代表project siteを自動採用する条件を検討する時に、pageが単一projectを代表するのか、project群を案内するのかを確認します。

## 再開時に持ち寄る材料

現在の構成要素の採用状態・利用者の判断、観測日時、正確なURLとリポジトリの識別情報、リンク表示と周辺に限定したHTML構造、対象リポジトリの役割を揃えます。過去の保留が後の手動判断で解消していれば閉じます。表示の変化と判定規則の問題は、観測時点ごとの入力と結果を照合して切り分けます。一般化した規則を提案する場合は、同じ表示が外部projectや依存先を指す反例も確認します。

## この資料の役割が終わる条件

既存規則・手動判断で扱えると分かった例、再収集できず規則の検討にも使わない例は外します。採用規則を広げるか現状を維持するか判断し、必要な代表例と理由を仕様・testへ引き継いだ時に削除します。例をすべて自動採用できるようにすることは完了条件ではありません。
