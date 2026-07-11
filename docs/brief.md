# Project Registry Brief

## 目的

atproto関連のproject、service、library、lexiconを継続的に発見・調査します。
[ATプロトーク](https://www.bluecast.app/user/@yamarten.bsky.social)で紹介するprojectの予備調査を主目的としながら、
ATmosphereに関する情報を再利用できるlocal registryへ蓄積します。

このprojectは、operatorが日常的に新しいprojectを探し、同じような予備調査や短い紹介文の作成を繰り返す負担を
減らすために始めました。発見と初期調査を機械化し、一度得た情報と判断を次の調査へ再利用します。

単なるURL一覧ではなく、projectのidentity、活動状況、関連resource、紹介状況、情報の根拠、operatorの判断を
一つのproject単位で追える状態を目指します。

## 利用者と成果物

利用者は、local registryを運用する一人のoperatorです。multi-user運用、共有DB、外部利用者向けAPIは
対象にしません。

紹介内容の主な読者はatproto開発者です。利用できるserviceを知りたい読者の関心と、atprotoの設計空間や
技術的に特徴のある使い方を知りたいoperatorの関心の両方を扱います。

主な成果物は、深掘りする価値がある候補の提案と、その判断に必要な背景を含むproject情報です。特にatprotoの
利用深度、serviceの利用可否、技術的新規性を重視します。この情報だけで最終的な紹介可否を決めることは目指さず、
追加調査や短い紹介文を作るための出発点とします。紹介文の公開はこのsystemの外で行い、紹介結果は以後の調査に
利用できるようregistryへ反映します。

努力目標として、蓄積した情報から公開可能なdirectoryを生成できる状態を目指します。絶対的な網羅性ではなく、
operatorが通常の人力調査で拾う範囲の情報を、機械化によって可能な限り取りこぼさないことを重視します。
公開手段の第一候補はapp record exportですが、公開仕様の都合だけでregistryの内部表現を決めません。
紹介活動を続けなくなった場合にも、それまでに蓄積した情報を別の形で利用できる状態を残します。

## 通常の運用flow

1. 機械化した収集と手動発見から候補を集める。
2. 既存projectとの同一性や関係を保守的に判定し、曖昧なものを人間の確認へ回す。
3. 深掘りする価値の判断に必要な情報と根拠を収集・更新する。
4. operatorが深掘りする候補、曖昧な関係、追跡を続けるかどうかを判断する。
5. 判断結果をregistryへ反映し、収集範囲、失敗、負担、保存量を継続的に見直す。

## 対象の境界

紹介候補になり得るもの:

- 独立したatproto projectやservice。
- 独自lexicon、実用service、またはatproto ecosystemへの明確な技術的貢献を持つもの。

調査対象:

- 紹介候補か未確定だが、atprotoとの関連を示す根拠があるもの。
- library、infra、auth integration、personal toolなど、別categoryで保持する価値があるもの。
- identityやownershipが曖昧で、人間による確認が必要なもの。

記録のみ:

- 現在は紹介しないが、将来の再評価や既存projectとの関係確認に必要なもの。
- 調査対象のうち、今後の動きが期待できないもの。

ignore:

- atprotoとの関連根拠がないもの。
- 独立identityや追加価値がないfork/noiseで、再調査の必要もないもの。
- operatorが明示的に追跡不要と判断したもの。

## 正本と責務境界

- projectのcanonical identity、分類、追跡状態、operatorの判断は、local registryに正本として保存します。
- 調査で得た情報は、根拠と確度が分かる形でregistryへ統合します。
- 外部情報や自動生成結果はoperatorの判断材料であり、それだけでoperatorの判断を上書きしません。
- 現在の状態と判断に必要な履歴は保持しますが、収集したraw dataを無制限には保存しません。

## 原則

- 情報は根拠と確度を区別して保持し、確度の低い情報を確定情報として扱いません。
- seedの発見では取りこぼしを減らすことを優先し、identity、ownership、project間の統合は保守的に扱います。
- 補助情報の不足より、異なるprojectや人物を誤って同一視することを重大な誤りとして扱います。
- 自動化は発見と調査を補助しますが、紹介可否や追跡不要の最終判断はoperatorが行います。
- 使われていない互換性や、未確定の将来仕様より、現在の運用と情報の明確さを優先します。

## 非目標

- public SaaSやmulti-user systemとして運用すること。
- 外部情報や自動生成結果を、検証せずregistryの正本とすること。
- raw dataを将来用途が未確定なまま無制限に保存すること。
- 曖昧なidentity、ownership、project間の統合、紹介可否を完全自動で確定すること。
- 旧仕様や未使用interfaceの後方互換を維持すること。
- 未決定の公開directoryやapp record仕様に合わせて、registryの内部表現を先に固定すること。

## 成功の判断

- operatorが深掘りする価値を判断できるだけの背景と根拠を、候補とともに提示できる。
- 人力で新しいprojectを探していた時に発見できたseedを継続的に取り込め、収集の失敗や不足を確認できる。
- atprotoの利用深度、serviceの利用可否、技術的新規性について、追加調査の起点となる情報を得られる。
- 曖昧な情報を確定情報と混同せず、operatorが判断を訂正できる。
- 調査に必要なoperator時間、処理時間、失敗、保存量を観測し、継続可能な範囲に保てる。
- directoryを公開する場合、紹介用に蓄積した情報を作り直さずに利用できる。

具体的な目標値は、data量、収集範囲、運用段階によって変わるため、このbriefでは固定しません。
