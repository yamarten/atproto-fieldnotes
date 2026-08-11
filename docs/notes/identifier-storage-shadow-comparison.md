# Identifier保存方式のshadow比較

> 作業用・検討用文書です。2026-08-12時点の正本DBをread-onlyで比較した結果を記録します。production schema、既存DB、自動confirm、検索、syncの挙動は変更していません。

## 結論

現行identifier、旧identifier、non-production identifier、採用理由、時間関係、noiseを別々に保存する案は、temp DB上のmutationと正本DBの移行投影を通過しました。旧identifierを検索可能なままTrust入力から外す、non-production identifierとnoiseの双方でcollectionの再作成を防ぐ、再有効化やmerge後も採用理由と元projectを失わない、という主要条件は両立できます。

正本DBのconfirmed構成要素10,187件のうち、10,186件は既存のdecision、collection inventory、confirmed relation、legacy manual seedの文脈から採用理由を復元できました。残る1件はWaverly Socialの`https://mywaverly.com`で、現行サービスの別名、旧identifier、noiseのどれかを既存DBだけでは判定できません。

したがって、物理schemaの実装計画へ進む前に必要な利用者判断は二つです。

1. 既存confirmed relationに裏づけられた95件と、legacy manual seedかつ`website_url`と一致する13件を、移行専用の`bootstrap`採用理由として一括復元してよいか。
2. Waverly Socialの`https://mywaverly.com`をcurrent alias、former、noiseのどれとして扱うか。これは一件のデータ判断であり、保存方式全体の可否とは分けられます。

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

入力は4,412 project、24,792 identifier、12,213 confirmed identifier、2,409 decisionです。構成要素向けroleに絞ると10,187 claimです。

同じ正本DBを連続して二回評価したreportのcontent hashは、いずれも`b9a5aff8e519a51febed620a7f28f2ac210335386fe13cb1513a8439559a103b`でした。

| 復元経路 | claim数 | project数 | 扱い |
| --- | ---: | ---: | --- |
| active confirm等から直接復元 | 2,264 | 1,765 | operatorまたは既知bootstrap sourceを保持する |
| collection inventoryから復元 | 7,814 | 3,084 | collection sync由来のbootstrapとして復元する。個々のTrust支持とはみなさない |
| confirmed relationから復元 | 95 | 84 | relationのpredicate、method、sourceを参照する移行専用bootstrapとして復元する |
| legacy manual seedと一致するwebsiteから復元 | 13 | 13 | project作成時の文脈を失ったlegacy bootstrapとして復元する |
| 復元不能 | 1 | 1 | Waverly Socialの`https://mywaverly.com` |

95件の内訳は、service probe由来55件、HTTP redirect由来4件、repositoryへのfirst-party link由来14件、GitHub homepage由来14件、README link由来7件、Tangled PDS record由来1件です。README linkを含むため、この復元規則を将来のTrust判定やcandidate admissionへ流用してはいけません。これは既にconfirmedであるclaimについて、過去のcollector文脈を移行理由として記録するだけです。

旧decision 2,357件のrole対応は2,296件が一意、60件が現行claimなし、1件が複数roleでした。複数roleの一件は`rpg.actor`の`developer_account` / `project_account`ですが、2026-08-04に期限切れになったdeferなので現行採用へ影響しません。現行claimなし60件のうち45件はactiveなblock/deferであり、未採用値を抑止する判断として自然です。残りは期限切れdefer 8件とcleared confirm 7件です。

## sample review

- Writerでは、一つのprojectに複数repositoryが保存されています。6件はservice pageからのfirst-party linkという既存relationを移行理由として保持できます。これらを同じrepositoryへ正規化したり、別projectへ自動分割したりはしません。
- Baskerの`https://basker.bio`はconfirmed relationを持ちませんが、legacy manual seedのprojectであり、保存済み`website_url`と一致するため移行文脈を復元できます。Basker→InfoEnonという時間関係をこの一致から推測はしません。
- service probe由来の55件は、probeが帰属を独立に証明したという意味ではありません。既存projectのserviceとしてprobe対象にした、というbootstrap provenanceの復元に限定します。
- Waverly Socialには`https://waverly.social`と`https://mywaverly.com`の二つのconfirmed serviceがあります。前者は保存済み`website_url`と一致しますが、後者にはconfirmed relation、redirect、個別decisionがありません。保存方式はどの結論にも対応できますが、データ上のstandingは人が決める必要があります。

## 実装境界と次のゲート

read-only補助コマンドは次の形です。

```bash
project-registry compare-identifier-storage \
  --db-path PATH \
  --output PATH \
  --example-limit 20
```

DBはSQLite `mode=ro`、`query_only=ON`の一つのread transactionで開き、指定JSON以外を書きません。完全reportは`.tmp/identifier-storage-shadow-comparison.json`に生成し、commitしません。

利用者が上の二点をreviewするまで、production schema、migration、自動confirm、candidate保存、検索、sync、Web UIは変更しません。合意後は、このprototypeをそのままproductionへコピーするのではなく、既存`project_identifiers`、`project_decisions`、merge、exact lookup、collection syncの責務へ最小のschemaとmutationを割り当てる実装計画を作ります。
