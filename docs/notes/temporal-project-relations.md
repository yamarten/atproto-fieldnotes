# Identifierと登録プロジェクトの時間関係の設計メモ

> 作業用・検討用文書です。現行仕様の正本ではありません。改名、移転、後継を信頼グラフやmergeへ暗黙に混ぜず、何が同じまま時間だけ進んだのかを表すための原則案です。predicate名と物理schemaは未確定です。

## 結論

時間経過で起きる変化を、次の三つへ分けます。

1. 同じ登録プロジェクトに属する同じ資源のidentifierが変わった
2. 同じ登録プロジェクトの名称や構成が変わった
3. ある登録プロジェクトを、別の登録プロジェクトが引き継いだ

1と2では同じproject keyを維持します。3では二つのproject recordを維持し、前身・後継関係を記録します。fork、派生project、mirror、並行するhosting、同時稼働するalpha版は、時間が関係して見えてもこの三分類へ自動的に入れません。

信頼グラフは「現在の帰属主張が、現在の直接関係によって支持されるか」を判断します。過去の帰属、移転、前身・後継は別の時間関係policyが扱い、過去に正しかったidentifierを現在の信頼判定の起点へ自動的に戻しません。

## 最初に分けるもの

### URL正規化と同時alias

HTTPからHTTPSへの変更、providerが定義する同じrepository URLの表記差、同時に有効なroot/`www`など、時点によらず同じ資源を指すと確認できるものは正規化またはaliasです。これは時間関係ではありません。

同じcontentを表示したことだけではaliasにしません。共通template、copy、mirror、alpha版でも同一contentは生じます。root/`www`も、同一contentだけでなく明示的なcanonical情報または同一siteとしての十分な根拠がある場合に限ります。

### 同じ資源の移転

repositoryのrename/transfer、handle変更、serviceの旧domainから新domainへの移転など、同じ資源のcurrent identifierが時間とともに変わった場合です。旧identifierと新identifierは同じroleで同じ登録プロジェクトに属しますが、有効だった期間が異なります。

方向は旧identifierから新identifierへの「置き換え」を表します。具体的なpredicate名は未確定ですが、単なる`redirects_to`観測と、確認済みの資源移転を同じpredicateにしません。

### 同じ登録プロジェクトの変化

登録プロジェクトの改名、説明の更新、構成要素の追加・終了は、project keyを変えません。名称履歴と各identifierの有効期間を残し、旧名や終了したserviceを現在の構成要素として表示しません。

構成要素の網羅性は要求しません。観測済みsurfaceの一部が終了したことを記録できればよく、未観測のAPI endpoint等を完全に列挙してから変化を確定する必要はありません。

### 別の登録プロジェクトへの継承

前身の成果や利用者を新しいprojectが引き継いでも、同じprojectと扱うとは限りません。前身と後継を別project recordとして保つ場合は、project間の方向付き関係として記録し、identifierや採用理由を一括移動しません。

Basker→InfoEnon、DateSky→Nomare型の事例は、まずこの候補として人が「同一projectの改名」か「別projectへの継承」かを判断します。名前の類似、redirect、同じ開発者、collectionの類似だけでどちらかへ自動分類しません。

### 時間関係へ入れないもの

- GitHub版とTangled版のように、どちらがmirrorとも明示されない並行hosting
- 同時に稼働する複数service surface
- alpha版と正式版が並行している状態
- fork、template由来、派生project
- tenant、instance、第三者mirror
- 一時的なredirect、login pageへのredirect、同一domain内のrouting

これらは並行関係、派生関係、配備形態、または単なる観測です。後で片方が終了し、正式な置き換えが確認された時点で、その二つの有効期間だけを時間関係として追加できます。

## 観測と確定した時間関係

HTTP redirect、HTML canonical、provider metadata、DID document、告知文、同じcontent、取得不能はすべて観測です。一つの観測から直ちに資源移転やproject継承を確定しません。

| 観測 | 単独で言えること | 単独では言えないこと |
| --- | --- | --- |
| same-host HTTP→HTTPS | URL表記の正規化候補 | projectの移転 |
| root/`www`のcanonicalまたはredirect | 同時aliasの候補 | cross-domain移転 |
| cross-host redirect | 旧URLが新URLを指した | 同じservice、現在も同じproject |
| providerのstable repository IDを維持したrename/transfer | 同じrepositoryのURLが変わった | project自体が同一か、owner変更後も帰属するか |
| DID documentの先頭`alsoKnownAs`変更 | その観測時点のcurrent handle | 旧handleが現在も同じDIDを指すこと |
| 同一content | 比較対象の取得内容が一致した | alias、mirror、移転、同じproject |
| 公式な移転・改名告知 | 主張主体が関係を明示した | 告知先もそれを認めていること、別projectか同一projectか |
| 取得不能または消失 | その観測時点で取得できなかった | 資源が終了したこと、別URLへ移転したこと |

自動的に「同じ資源の移転」と確定できるのは、providerのstable resource IDなど、入力identifierに対して同じ資源の新identifierを直接返す一次情報がある場合に限定します。このような一次情報が意味上の両方向を保証するなら、同じprovenanceを持つ二つの方向付き関係へ分解し、通常の信頼判定に利用できます。HTTPや公開文面から判断する場合は、旧側が新側を示す経路と、新側が同じ登録プロジェクトの構成要素であることを支持する経路の両方を要求します。二つの経路が同じ取得結果に由来しても構いませんが、単なる検索用inverseは数えません。

projectの改名か別projectへの継承かは自動確定しません。この判断はprojectという囲い自体を変え、mergeと同程度の影響を持つためです。

## Identifierの状態

帰属の真偽と、現在使われているかを別軸にします。

- `confirmed` / `candidate`: そのidentifierがそのroleで登録プロジェクトに属するという帰属主張の採用状態
- current / historical: その帰属が現在有効か、過去の一定期間に有効だったか

旧domainが過去に正式なserviceだった場合、移転後も帰属主張は`confirmed`のままhistoricalにできます。これを`candidate`へ戻すと「昔は正しかった」という情報が失われ、`confirmed`の意味も曖昧になります。

一つのroleにcurrent identifierが複数あって構いません。新identifierを追加しただけでは旧identifierをhistoricalにせず、明示的な置き換えまたは終了を確認した場合だけ期間を閉じます。再開や一時的な併用に備え、同じidentifierが複数の有効期間を持てる論理モデルにします。

historicalな帰属主張は通常のAdmission、信頼判定の起点、current構成要素の表示に使いません。履歴表示、旧URLからの検索、移転の説明、過去判断の再現には使えます。

## 採用理由との関係

採用理由は「この帰属主張が正しい理由」、有効期間は「いつcurrentだったか」を表すため、同じ情報へ統合しません。手動confirm、登録起点、静的な信頼判定は帰属主張を採用できても、別のcurrent identifierへ置き換わった時刻までは決めません。

時間関係policyは、確認済みの移転から新identifierのcandidateと有効期間の開始候補を作れます。ただし、それだけで自動confirmするかは通常の採用規則へ委ねます。新側が通常の信頼判定で`SUPPORTED`になるか、operatorまたは承認済みの収集入口が採用した場合にだけ`confirmed`にします。

置き換えを自動処理する場合は、旧identifierをcurrentな信頼判定の起点として新identifierを評価し、新identifierの採用と期間開始を保存してから、旧identifierの期間を閉じます。先に旧identifierをhistoricalへ移して起点を失ったり、新identifierの採用に失敗したまま旧identifierだけを終了させたりしません。

旧identifierの採用理由は移転後も履歴の根拠として残しますが、historicalになった時点で信頼判定の起点には使いません。移転先へ旧identifierのoperator採用理由をコピーしません。

この分離により、canonical redirectを検出した処理が旧confirmを新URLへ付け替える現行動作は変更対象になります。移転観測、新URLの採用、旧URLの期間終了を別々に記録します。

## 信頼判定、Admission、mergeでの用途

| 用途 | 時間関係の扱い |
| --- | --- |
| Candidate admission | 確認済みの資源移転先をcandidateとして提示できる。raw redirect、同一content、名前一致からは生成しない |
| 自動confirm | 時間関係単独では行わない。新identifierへの通常の往路・復路または別の採用理由を要求する |
| 信頼判定の起点 | currentで、採用理由が有効で、review対象でない帰属主張だけを使う |
| 関係経路 | currentな直接関係を通常policyで使う。historical relationは過去時点の再現以外に使わない |
| project merge | 確認済みの同一project内の資源移転は重複候補の説明に使えるが、自動merge根拠にはしない |
| 前身・後継 | 別projectを維持し、merge抑止と表示に使う。採用理由とidentifierは継承しない |
| fork・派生 | [派生関係の設計メモ](derivation-relations.md)に従い、Trustとmergeの正の根拠にしない |

テスト版から正式版への追従も同じです。正式版が同じprojectの新しいcurrent serviceだと確認できればcandidateと期間開始を作れますが、テスト版がまだ稼働しているならhistoricalにはしません。単に同じcontentを返す二つのURLは記録対象にしません。

## 現行実装との差分

- `redirects_to`はraw HTTP redirectとHTML canonicalを同じpredicateで保持しています。観測と確定したalias・移転を分ける必要があります。
- same-site redirectから同じprovenanceの双方向`service_canonical` edgeを作り、条件次第で旧URLのconfirm decisionを新URLへ移しています。双方向の意味保証は利用できますが、帰属の採用と期間終了を一つのmutationにしません。
- `url_aliases`はroot/`www`の同一contentもconfirmed aliasにします。同一content単独ではaliasにしない方針へ変更が必要です。
- DID documentの`alsoKnownAs`を複数handleとして収集しています。current handleは規格上の先頭値を使い、以前の観測値は履歴として分ける必要があります。
- `project_identifiers`と`project_relations`は`observed_at`を持ちますが、有効期間とcurrent/historicalの別を持ちません。
- `investigation_runs`には過去snapshotがありますが、currentな帰属から旧identifierの期間を照会する正本ではありません。
- `project_key_aliases`は実行済みmerge後のkey解決だけを表し、projectの改名履歴や前身・後継は表しません。
- fork・派生は現状`separate_project`やreview理由へ寄せられ、時間関係とは別です。この境界は維持します。

## 保存契約への影響

時間関係を入れると、`project_identifiers`を「現在行だけのtable」と決めるより、帰属主張と有効期間を分ける方が単純です。

- 帰属主張: `project + kind + value + role`、classification、採用理由
- 有効期間: 帰属主張への参照、開始時刻、終了時刻、期間を開閉した根拠
- 資源間の時間関係: 旧identifier、新identifier、関係種別、発効時刻、根拠
- project間の時間関係: 前身project、後継project、関係種別、発効時刻、operator判断

物理候補は、`project_identifiers`に帰属主張を一行だけ保持し、複数期間を別tableへ置く形です。currentは終了時刻のない期間から導出します。この形なら、同じidentifierの停止と再開、historical confirmed、採用理由の維持を表せます。

資源間の時間関係を`project_relations`へ置くか専用tableへ置くかは未確定です。`project_relations`の現在のunique keyには発効時刻がなく、同じ移転の再観測と複数期間を区別できないため、そのまま流用はしません。project間の前身・後継はregistry全体の関係なので、一方のproject配下だけへ保存しない形が必要です。

## 次の確認

1. 正本DBとmerge前snapshotから、旧URL・旧handle・rename後repoがどの程度残っているかをread-onlyで抽出する
2. Basker/InfoEnon、DateSky/Nomareを、同一projectの改名候補と別projectへの継承候補のどちらとして扱うか人が確認する
3. canonical redirectでconfirmを付け替えた履歴と、root/`www`同一content aliasの実例をsample reviewする
4. current/historicalを加えた論理移行で、既存confirmed件数と信頼判定の起点がどう変わるかshadow比較する
5. その結果から、identifier期間、採用理由、時間関係をJSON列と別tableのどちらへ置くか確定する

この確認が終わるまで、redirect処理、DID/handle収集、project merge、正本DB schemaは変更しません。
