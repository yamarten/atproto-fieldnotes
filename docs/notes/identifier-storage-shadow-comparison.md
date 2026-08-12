# Identifier保存方式のshadow比較

> 作業用・検討用文書です。2026-08-13時点の正本DBをread-onlyで比較し、review済み判断を複製DBへ適用した結果を記録します。production schema、正本DB、自動confirm、検索、syncの挙動は変更していません。

## 結論

現行identifier、旧identifier、non-production identifier、採用理由、時間関係、noiseを別々に保存する案は、temp DB上のmutation、正本DBの移行投影、review済み108 claimの複製DBへの適用を通過しました。旧identifierを検索可能なまま信頼判定の入力から外す、non-production identifierとnoiseの双方でcollectionの再作成を防ぐ、再有効化やmerge後も採用理由と元projectを失わない、という主要条件は両立できます。

正本DBのconfirmed構成要素10,184件のうち、10,183件は既存のdecision、collection inventory、confirmed relation、legacy manual seedの文脈から採用理由を投影できます。残るWaverly Socialの1件を含むreview対象108 claimはすべて処遇を確定し、複製DBでは採用理由のないconfirmed構成要素が0件になりました。ここでいう採用理由の投影とは、identifierを探し直したり現在の信頼ポリシーで再確認したりすることではありません。現行DBではconfirmedである理由がdecisionとして残っていないclaimがあるため、新しい保存契約へ移す際に、既存履歴または今回のoperator reviewを対応づけることです。

この結果はproduction schemaを決めるためのshadow materializationです。正本DBへ適用する前の次のゲートは、後述する保存責務と実データ差分が意図どおりかのreviewです。

Waverly Socialの`https://mywaverly.com`は、利用者による裏取りを根拠として`former`へ移しました。これは複製DBだけの結果で、正本DBにはまだ適用していません。

## 人間向けの見取り図

このprototypeが分けるのは「何が今の構成要素か」「以前または開発用として同じprojectに関係するものは何か」「なぜ構成要素として採用したか」「何から何へ移ったか」「projectへ属さないnoiseは何か」です。

| 情報 | prototypeでの置き場所 | 通常のTrust入力 | exact検索 | collection再発見の抑止 |
| --- | --- | --- | --- | --- |
| 現行identifier | `current_identifiers` | 対象roleだけ可 | 対象 | 対象 |
| 旧identifier | `noncurrent_identifiers`の`former` | 不可 | 現行より低い優先度で対象 | 対象 |
| 開発用identifier | `noncurrent_identifiers`の`non_production` | 不可 | 低い優先度で対象 | 対象 |
| 採用理由 | `identifier_adoptions` | 起点利用可否はsource・role別policyで判断 | identifierではない | 直接は使わない |
| 確定した移転 | `identifier_transitions` | 直接は使わない | 旧・新identifierの関連説明に使える | 直接は使わない |
| noise | `identifier_exclusions` | 不可 | projectへは解決しない | 対象 |

table名はprototype上の名前であり、production schema名の決定ではありません。`identifier_exclusions`はregistry-wideなnoise抑止を試したもので、projectごとのblock/deferを置き換えません。

## 固定caseの結果

8件すべて成功しました。

| case | 確認したこと |
| --- | --- |
| former exact search | 旧URLから現在のprojectを検索できるがTrust入力にはならない |
| non-production sync guard | 開発用NSIDはprojectとの関連を残したままsyncによる再作成を防ぎ、Trust入力にはならない |
| reactivation | 旧identifierをcurrentへ戻しても、過去と再採用時の二つの採用理由を保持する |
| noise exclusion | noiseはproject attributionを作らず、syncによる再作成だけを防ぐ |
| transition separation | 旧→新の確定関係を、旧・新それぞれの採用理由とは別に保持する |
| merge | current、former、採用理由、時間関係を存続projectへ移し、元projectを監査用に保持する |
| current/noncurrent排他 | 同じproject・kind・value・roleをcurrentとnoncurrentへ同時保存しない |
| trusted anchor境界 | current candidateは検索できるがtrusted anchorにはせず、confirmed claimも有効な採用理由を持つ場合だけanchorにする |

## 正本DBへの移行投影

入力は4,416 project、24,860 identifier、12,211 confirmed identifier、2,405 decisionです。構成要素向けroleに絞ると10,184 claimです。

review対象107 claimは`project key + kind + value + role`の集合hash `f7ce2f2f9736a2fc1665277d121a8762bdace544ce55f28becc415a7f59d6f73`で固定しました。Waverly Socialの1件は、投影で補完できない追加claimとしてmanifestへ明示しています。この固定により、正本DB側の対象集合が変わった場合はreview結果を黙って別のclaimへ適用せず、materializationを停止します。

| 採用理由の補完経路 | claim数 | project数 | 扱い |
| --- | ---: | ---: | --- |
| active confirm等から直接対応 | 2,262 | 1,764 | operatorまたは既知bootstrap sourceを保持する |
| collection inventoryから補完 | 7,814 | 3,088 | collection sync由来のbootstrapを補う。個々のTrust支持とはみなさない |
| confirmed relationから補完 | 94 | 83 | relationのpredicate、method、sourceを参照する移行専用bootstrapを補う |
| legacy manual seedと一致するwebsiteから補完 | 13 | 13 | project作成時の文脈を失ったlegacy bootstrapを補う |
| 現行claimとして補完不能 | 1 | 1 | Waverly Socialの`https://mywaverly.com`。review後は`former`へ移す |

confirmed relationからの補完にはREADME link等も含まれます。この補完規則を将来の信頼判定やcandidate admissionへ流用してはいけません。これは既にconfirmedであるclaimについて、過去のcollector文脈を移行理由として記録するだけです。

対象decision 2,353件のrole対応は2,293件が一意、59件が現行claimなし、1件が複数roleでした。複数roleの一件は`rpg.actor`の`developer_account` / `project_account`ですが、2026-08-04に期限切れになったdeferなので現行採用へ影響しません。roleを一意に決められないdecisionと、対応する現行claimがないdecisionには`target_role`を補いません。

## Review済み複製DBへの適用結果

`tests/fixtures/project_registry/identifier-adoption-review-v1.json`をreview結果の機械可読manifestとし、正本DBをSQLite `mode=ro`で開いて新しい複製DBへ適用しました。正本DBとproduction schemaは変更していません。

| 処遇 | claim数 | 結果 |
| --- | ---: | --- |
| 現行を維持してoperator reviewを採用理由にする | 59 | currentを維持 |
| serviceではなくWebsiteとして残す | 23 | service claimを削除。Websiteは維持 |
| 無関係なclaimを外す | 12 | currentから削除 |
| 正規化前のaliasを捨てる | 5 | HTTPやprovider表記違いをcurrent/formerへ残さない |
| 旧identifierへ移す | 7 | 既存6件とWaverly 1件を`former`へ移動 |
| non-productionへ移す | 1 | Soleiaの開発用URLを移動 |
| 別projectへ移す | 1 | High Fiveのrepositoryを新projectへ移動 |
| current identifierを追加する | 6 | reviewで確定したservice/repositoryを追加 |
| project属性を更新する | 7 | 名称またはtracking stateを更新 |

High FiveとMalfestioの二つの新projectを作成したためproject数は4,416件から4,418件になりました。High Fiveはserviceとrepository、Malfestioは二つのNSID、service、repositoryを持ちます。元のATProtoFansとWriterには、それぞれに属する構成要素を残しました。

Websiteだけを残したproject、無関係なserviceを外したproject、non-production serviceだけになったprojectのうち、他にconfirmed serviceがない22 projectは`service_status=none`に揃えました。このうちscalar値が実際に変わったのは14 projectです。終了済みserviceを`former`へ移したprojectの`dead`は、過去にservice surfaceを持っていた状態を表すため維持しました。

`source_repository_record` 1,455件と`authority_domain` 3,998件は、構成要素identifierではなく観測記録として`state_json.evidence`へ移しました。これはshadow上の保存候補であり、productionの観測保存契約はまだ確定していません。

active deferの対象としてcandidateのまま残っていた7 claimはcurrent identifierから除きました。confirmed claimとactive block/deferの競合はありませんでした。

適用後は4,418 project、19,358 current identifier、10,144 confirmed構成要素、8 non-current identifier、10,277 adoption rowです。current identifierが大きく減る主因は、上記5,453件の観測用rowをidentifier tableから移したことです。

検証結果は次のとおりです。

- SQLite integrity check: `ok`
- foreign key error: 0
- 同一claimのcurrent/non-current重複: 0
- 有効な採用理由を持たないconfirmed構成要素: 0
- 固定prototype case: 8/8成功

## sample review

- Writerでは、一つのprojectに複数repositoryが保存されています。6件はservice pageからのfirst-party linkという既存relationを移行理由として保持できます。これらを同じrepositoryへ正規化したり、別projectへ自動分割したりはしません。
- Baskerの`https://basker.bio`はconfirmed relationを持ちませんが、legacy manual seedのprojectであり、保存済み`website_url`と一致するため移行時の採用理由を補えます。Basker→InfoEnonという時間関係をこの一致から推測はしません。
- service probe由来の55件は、probeが帰属を独立に証明したという意味ではありません。既存projectのserviceとしてprobe対象にした、というbootstrap provenanceの補完に限定します。
- Waverly Socialには`https://waverly.social`と`https://mywaverly.com`の二つのconfirmed serviceがあります。前者は保存済み`website_url`と一致します。後者は利用者の裏取りにより`former`と決まり、新しい現行claimの採用理由を補う対象から外します。

## 実装境界と次のゲート

read-only補助コマンドは次の形です。

```bash
project-registry compare-identifier-storage \
  --db-path PATH \
  --output PATH \
  --example-limit 20
```

通常の比較はDBをSQLite `mode=ro`、`query_only=ON`の一つのread transactionで開き、指定JSON以外を書きません。review済みmaterializationを再現する場合は、既存ファイルを上書きしない新しい出力先を指定します。

```bash
project-registry compare-identifier-storage \
  --db-path PATH \
  --output REPORT.json \
  --materialized-db NEW-SHADOW.db \
  --review-manifest tests/fixtures/project_registry/identifier-adoption-review-v1.json
```

完全reportと複製DBは`.tmp/`へ生成し、commitしません。

利用者が保存責務と実データ差分をreviewするまで、production schema、正本DB、自動confirm、candidate保存、検索、sync、Web UIは変更しません。合意後は、このshadow schemaをそのままproductionへコピーするのではなく、既存`project_identifiers`、`project_decisions`、merge、exact lookup、collection syncの責務へ最小のschemaとmutationを割り当てます。特に、観測用rowの正本、非現行identifierの通常検索表示、confirm時のdefer解除をproduction契約として詰める必要があります。
