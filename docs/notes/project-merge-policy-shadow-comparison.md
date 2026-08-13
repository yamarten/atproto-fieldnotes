# 登録プロジェクトのマージ規則shadow比較

> 作業用・比較結果文書です。現行仕様の正本ではありません。2026-08-13の正本DBをread-only transactionで評価した結果と、次の設計判断に必要な材料を残します。完全なJSON reportは`.tmp/merge-policy-shadow-comparison.json`に生成し、コミットしません。

## 結論

role、直接relation、手動の別project指定を分ける規則は固定12事例をすべて期待どおり分類し、既知の共有repositoryやorganization accountをマージ推奨へ昇格しませんでした。一方、正本DBでは「マージを勧める」に該当するpairが0件でした。したがって、規則の境界は維持できますが、現時点で現行マージ判定を置き換えて人の確認を省く実益は示せていません。

主因は、別々のproject recordに保存された構成要素を直接結ぶrelationがほとんどないことです。現行の`auto_merge_ready` 2,663組はすべて新規則では要確認に留まりました。この結果は、条件を緩めて自動件数を作る理由にはしません。collector個別の完全性より、共有しにくい構成要素の衝突、構成要素間の直接関係、別projectを示す事実を一般則として表示・収集できることを先に求めます。

比較器はread-onlyの開発補助として独立しており、自動マージ、候補保存、Web UI、schema、正本DBを変更しません。次の設計ゲートは、0件という結果を許容してreview支援から接続するか、直接relationの収集・保存を先に改善してから再比較するかの判断です。

## 人間向けに言い換えた比較内容

現行方式は、名前、domain由来token、authority family、repositoryなどが似た記録を候補にし、複数の一致をscoreへ足して`auto_merge_ready`を付けます。新規則は処理を三段階に分けます。

1. 名前や共有repositoryを含む広いsignalで、比較する二記録を見つける
2. 同じservice、同じfull NSID、同じ専用project accountのような共有しにくい構成要素の衝突を確認する
3. 異なるroleの構成要素を結ぶ直接relationがあり、`separate_project`や時間・派生関係との衝突がなければ、人へマージを勧める

「人へマージを勧める」場合も自動実行はしません。repository、名前、root domain、同じnamespaceだけの一致は比較の入口または表示材料であり、マージを支持する票として加算しません。

## 実行条件

`project-registry compare-merge-policy`で正本DBをSQLite `mode=ro`の単一transactionとして読みました。実運用のmulti-tenant domain設定はDBと同じcheckoutにある`config/domain-overrides.json`を自動検出しています。reportは指定したJSONだけを書き出し、DBのbyte列が変わらないことをtemp DB testで確認しています。

正本DBは並行してenrichされていたため、件数は各実行開始時のsnapshotです。以下はcontent hash `65c2c73aae59bc419ec7e908691977832131e803094d9704c7d65c8a5a13918e`のreportを基準にします。

- project: 4,416件
- current identifier: 24,890件
- relation: 71,161件
- schema v3のidentifier adoption: 0件。入力DBはまだschema v2
- 固定case: 12件中12件合格
- 評価時間: 約6秒

## 現在の候補比較

| 区分 | 件数 |
| --- | ---: |
| 現行duplicate candidate | 3,142 |
| 現行`auto_merge_ready` | 2,663 |
| 現行strict batch | 0 |
| 現行moderate batch | 2,665 |
| 現行またはshadowで見つけた全pair | 3,162 |
| 新規則: マージを勧める | 0 |
| 新規則: 要確認 | 3,144 |
| 新規則: 別扱いを守る | 18 |
| 新規則: 時間・派生関係へ送る | 0 |

18件の「別扱いを守る」はactiveな`separate_project`判断を持つpairです。現行側でも既にblockされているため、新たに人の作業を省いた件数ではありません。shadow discoveryだけが見つけた2 pairも要確認であり、現時点ではreview対象が減っていません。

現行v2の再現性も確認しました。通常のproject loaderで計算した候補3,142、`auto_merge_ready` 2,663、strict 0、moderate 2,665と、比較器内のv2 baselineが一致しました。custom multi-tenant suffixを読まずに比較すると件数が大きく変わるため、比較器はDB checkoutの設定ファイルを自動検出し、そのpathとhashをreportへ残します。

## 0件になった理由

正本の未マージpairで、同じcurrent confirmed full `owned_nsid`を持つ衝突は3組ありました。service URLまたは専用project accountの完全一致はありませんでした。3組はいずれも、衝突したNSIDと両recordの異なる構成要素を結ぶ直接relationが保存されていないため、要確認に留まりました。

- Prefect AT Protocol Integration / Prefectはfull NSIDとcandidate repositoryが重なりますが、integrationと本体を同じ編集単位にする根拠にはなりません。
- `io.prefect.flowRun.state`由来record / Prefectもfull NSIDが重なりますが、collection単位で作られたrecordを統合すべきか、誤った帰属かを区別するrelationがありません。
- 二つのdarkworld.download recordもfull NSIDが重なります。重複recordらしく見えても、現状の保存情報だけでは同じ規則で安全に確定できません。

構成要素を跨ぐ直接relationが見つかったpairはJacquard / rpg.actorでした。Jacquard repositoryが`actor.rpg.*` schemaを定義する事実はありますが、両recordは共有しにくい構成要素を衝突させていません。repositoryが別projectの共有Lexiconを実装・定義する場合もあるため、これだけでマージを勧めない判定は妥当です。

この結果から、必要なのはrelation総数の増加ではなく、二つのrecordの構成を説明する正しい種類のrelationです。71,161 relationの大半は`mentions_collection` 38,035件と`uses_collection` 16,185件であり、いずれもマージの正の根拠には使いません。`official_account`や`operates_service`もaccount roleを正しく判別できなければ共有organization/developerを巻き込むため、単独ではマージ推奨にしません。

## 既知事例の確認

固定case catalogでは、次の境界を明示して12件すべて期待どおりになりました。

- 共有しにくいserviceの一致と、repoからcollectionへの直接relationが揃う人工例は「マージを勧める」
- serviceまたはfull NSIDの一致だけなら「要確認」
- Arabica/Oolong型の共有repositoryは「要確認」
- organization accountや専用project accountの一致だけなら「要確認」
- activeな`separate_project`は「別扱いを守る」
- forkと後継は通常の重複record判定ではなく「判定対象外」
- 同じ直属namespace、candidate repositoryと名前、同じroot domainだけなら「要確認」

固定caseの「マージを勧める」は規則が到達可能であることを確認するための人工例です。正本で0件だった事実を隠す成功件数としては扱いません。

## 過去マージとの比較

過去510 aliasのうち、sourceとtarget両方のマージ以前snapshotを取得できた504件へ同じ比較を行いました。現行v2 baselineは既存監査と同じ内訳を再現しました。

| 現行v2で再現した区分 | 件数 |
| --- | ---: |
| strict | 18 |
| moderate | 213 |
| broad candidateのみ | 144 |
| signalなし | 129 |

新規則では504件すべてが要確認で、「マージを勧める」は0件でした。過去マージを誤りと評価したわけではありません。過去snapshotは実行した人の判断理由を保存せず、現在求めるcross-recordの直接relationや時間的な継続性も十分に持ちません。この比較からは、過去判断の再自動化より、今後のmerge時に成立したruleと根拠を監査可能に残す必要性が確認できます。

## schema移行後でないと評価できない項目

入力DBはschema v2なので、identifier adoptionと登録起点は0件です。そのため、同じsync eventから同じclaimを二重projectへ作ったケース、既存projectへattachしたケース、新規projectを作ったケースを分ける比較はまだできません。これは外部projectの同一性を直接証明する情報ではありませんが、DB内の重複生成を優先reviewする有力な運用情報です。schema v3移行後に同じcomparatorを再実行します。

fork、mirror、successor、predecessor、ownership transferのtypedなproject間relationも現在は保存されていません。固定caseでは通常mergeから分岐できることだけを確認しました。DateSky/NomareやBasker/InfoEnonの実データを時間関係として分類する作業は、通常merge scoreへ混ぜず、時間関係設計を差し込む段階まで保留します。

## 現時点の採否材料

比較から支持されるのは、候補発見とマージ判断を分け、現行`auto_merge_ready`をそのまま自動実行条件として使わない方針です。共有repository、同じnamespace、名前、domainを加点して自動化する案は採りません。

まだ支持されないのは、新規則を現行UIや実行経路へ接続すれば人の確認を減らせる、という主張です。現在の保存情報では3,144 pairが要確認のままであり、推奨0件です。次は次のどちらか、または両方を小さく行って再評価します。

1. Web reviewで使うことを前提に、候補を「共有しにくい構成要素の衝突」「直接relationあり」「共有可能resourceのみ」「弱い類似」に分け、件数を減らさず優先順位と理由表示だけを改善する
2. collectorが既に観測しているservice/repository/collection/account間の直接事実を、projectを跨いで比較できる形で保存・投影し、正本の既知重複で「マージを勧める」まで到達できるか再比較する

どちらでも、最初は人による確認を維持します。推奨件数を作るために排他条件や直接relation条件を緩めません。

## 検証

- 固定case catalog: 12/12合格
- comparator unit/temp DB test: 7件合格
- 正本DB: read-only CLI実行成功
- 現行v2候補件数: 通常loaderと比較器で一致
- 過去マージのv2区分: 既存監査の18/213/144/129と一致
- `bin/project-registry-test`: 623件合格

