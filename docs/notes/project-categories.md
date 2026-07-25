# Project Category Definitions

> category契約の見直し内容を記録する作業用文書です。現行実装、既存DB、LLM評価labelへの反映が
> 完了するまでは、現在の保存仕様を表す正本文書として扱いません。反映後の保存契約は
> [project-record.md](../developer/project-record.md)へ移します。

## 目的

`category`は、projectの成熟度、稼働状態、発見経路ではなく、主な利用者と利用形態を表します。
同じrepository配布でも、利用者が直接使うもの、開発者が組み込むもの、projectの背後で動かすものを
分けます。

停止、削除、archiveはcategoryを変える理由にしません。過去のcategoryを確認できる場合は維持します。
削除後に初めて発見し、名前以外を復元できない古い例では`name_only`を許容します。

## Category

### `project`

一般利用者が直接利用するservice、app、client、bot、利用者向け拡張機能などです。

CLIやpluginという配布形式だけでは`project`と決めません。一般利用者が直接使うCLIや拡張機能は
`project`ですが、開発者が組み込むpluginは`library`、ATProto自体を操作・検査するCLIや運用componentは
`infra_tool`です。

### `lexicon`

第三者による実装または相互運用を意図し、共有規約として公開・文書化されたlexiconです。

NSID、schema sample、record sample、namespace名だけでは`lexicon`にしません。service固有schemaは
そのserviceのcategoryを使い、実体を特定できなければ`name_only`にします。

### `library`

開発者が他のsoftwareへ組み込むSDK、package、framework、adapter、開発用pluginです。

repositoryから配布されることやsource codeがあることだけでは`library`にしません。単独で実行・配置して
ATProtoやserviceを支えるものは`infra_tool`、一般利用者が直接使うものは`project`です。

### `infra_tool`

一般利用者が直接消費する成果物ではなく、次のどちらかを主目的とする実行物またはserviceです。

- projectやserviceの背後で動き、その提供・保守・運用を支える
- ATProtoのprotocol、data、network、repository、運用環境自体を操作・検査・保存する

常時稼働するonline infrastructureに限定しません。PDS、relay、indexerに加え、`goat`、comail、
AT Backup、Airglowのような開発・調査・backup用toolも含みます。

`library`との境界は主な利用形態で決めます。他のsoftwareへ組み込む部品は`library`、実行、配置、操作して
ATProtoやserviceを支えるものは`infra_tool`です。

### `personal_tool`

想定利用者が作者本人、特定PDSのmember、または固定された小集団に限定されるtoolやserviceです。

利用者の少なさ、単機能、特定の趣味や分野に特化していることだけでは`personal_tool`にしません。
限定利用であることを示す根拠がない場合も、単に`personal_tool`へ寄せません。

### `name_only`

名前、NSID、過去の言及などを記録する価値はあるものの、対象の実体または主な役割を判断できない場合の
暫定categoryです。情報不足を埋めるために、名前やschema断片だけから具体的な役割を推測しません。

停止・削除済みという理由だけでは`name_only`に変更しません。ただし、過去情報を復元できない古い対象で
元のcategoryを確定できない場合は許容します。

### `reject`

登録しようとしている対象そのものが調査範囲外、無関係、または候補として成立しない場合に使います。
情報が少ないだけなら`name_only`です。

既存projectに別物のURLが混入した場合は、そのURLの関係を除外する問題であり、projectのcategoryを
`reject`にはしません。誤ったURLから作られた候補全体を`reject`する場合も、URL混入という原因は
categoryとは別に扱います。

## `auth_only`の扱い

`auth_only`はcategoryから削除します。ATProtoとの関係が認証だけであることは成果物の利用形態ではないため、
次のように提供形態で分類します。

- OAuth SDK、認証package、開発用plugin: `library`
- OAuthを利用する一般利用者向けappやservice: `project`
- 認証proxyや運用component: `infra_tool`

認証だけを利用するという情報は、categoryとは独立した`atproto_contribution_level=auth_only`で保持します。

## 判断順序

複数の説明が当てはまりそうな場合は、次の順に確認します。

1. 対象そのものが調査範囲外または誤登録なら`reject`
2. 実体や役割を判断できないなら`name_only`
3. 想定利用者が作者本人または固定集団に限定されるなら`personal_tool`
4. 文書化された共有schema自体が対象なら`lexicon`
5. 他のsoftwareへ組み込む部品なら`library`
6. projectの背後で動くか、ATProto自体を扱う実行物なら`infra_tool`
7. 一般利用者が直接利用するものなら`project`

配布形式だけで決めず、README、docs、service surface、利用例などから主な利用者と利用形態を確認します。
