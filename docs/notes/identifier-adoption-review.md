# Identifier採用理由の移行review

> 作業用・検討用文書です。新しい保存契約への移行前に、採用理由を特定できない現行confirmed identifierを利用者とreviewした結果を記録します。このreviewは2026-08-14のschema version 3切替で正本DBへ適用済みです。

## Review方針

- identifierだけで判断せず、project key、他の構成要素、relationの向きと収集元を確認する。
- reviewで維持すると決めたcurrent claimには、実際のreviewを根拠とするoperator採用理由を作れる。
- 誤ったcurrent claimは外す。別projectでは有効なidentifierや正規化重複をregistry-wide noiseとして扱わない。
- blockは、移行後のadmission policyでも同じprojectへ繰り返し再提案される場合だけ追加する。collectorやadmission境界の誤りを恒久的なblockで覆い隠さない。
- この文書の判断をproduction schemaや正本DBへ適用するのは、保存契約とmigrationの実装が合意された後とする。

## 確定済み

### Waverly Social

- project: `proj:4be43afbf2a0`
- `https://mywaverly.com`: `former`
- 根拠: 利用者による裏取り。現行service `https://waverly.social`とは別に、過去に正しかったURLとして保持する。

### Repository batch 1

#### Writer

- project: `proj:38bea19f59c3`
- 維持: `https://github.com/stormlightlabs/writer`
- current claimから外す: `https://github.com/stormlightlabs/audiox`
- current claimから外す: `https://github.com/stormlightlabs/beacon`
- current claimから外す: `https://github.com/stormlightlabs/lazurite`
- current claimから外す: `https://github.com/stormlightlabs/lectito`
- current claimから外す: `https://github.com/stormlightlabs/parserst`
- block: なし
- 根拠: 除外側はStormlight Labsのorganization pageから収集された別projectのrepository。Writer固有ページから相互参照される`writer`だけをsource repositoryとして維持する。

#### AgentScan

- project: `proj:693d18882df9`
- 維持: `https://github.com/MatteoGabriele/agentscan`
- current claimから外す: `https://github.com/matteogabriele/agentscan`
- block: なし
- 根拠: GitHub上で大文字小文字だけが異なる同一repository。formerやnoiseではなく、provider-aware canonicalizationで重複解消する。

#### adonisjs-atproto-oauth

- project: `proj:772030abe789`
- current claimから外す: `https://github.com/adonisjs/core`
- block: なし
- 根拠: 当該pluginのsource repositoryは既に`https://github.com/ThisIsMissEm/adonisjs-atproto-oauth`として保存されており、`adonisjs/core`は利用基盤側の別repository。

#### atproto-crates

- project: `proj:3b99b02904d4`
- current claimから外す: `https://github.com/ngerakines/riblt-rs`
- block: なし
- 根拠: 個人サイトから収集された別repository。atproto-cratesに対応するTangled repository候補は別に保存されている。

このbatchでreview対象10 claimを確定しました。

### Repository batch 2

次の9 claimは、serviceからrepositoryへの参照とrepositoryから対応serviceへの参照を確認し、current confirmedとして維持します。

| Project | Project key | Source repository |
| --- | --- | --- |
| Airglow | `proj:51763715a069` | `https://tangled.org/exosphere.site/airglow` |
| Bounce | `proj:d1e3d414c13e` | `https://github.com/snarfed/bounce` |
| Dandelion | `proj:39e63508f04a` | `https://github.com/symbiota-coop/dandelion` |
| Sill | `proj:46480183f16a` | `https://github.com/TylerFisher/sill` |
| SkyPicker | `proj:8b362fad7eb4` | `https://github.com/breakzplatform/skypicker.site` |
| SkyScheduler | `proj:4a58d46d6645` | `https://github.com/SocksTheWolf/skyscheduler` |
| Zeitgeist | `proj:1f1dbf60e85d` | `https://github.com/seldo/zeitgeist` |
| at.fund | `proj:0d837566577c` | `https://github.com/andyschwab/at.fund` |
| lieslese | `proj:99ef077d463e` | `https://github.com/jonash54/lieslese` |

#### mastrojs/atproto

- project: `proj:301bb26c4eeb`
- 維持: `https://github.com/mastrojs/atproto`
- current service claimから外す: `https://mastrojs.github.io`
- current source repository claimから外す: `https://github.com/mastrojs/mastrojs.github.io`
- block: なし
- 根拠: `mastrojs.github.io`はMastro framework全体のsiteで、`mastrojs.github.io` repositoryはそのsiteのsource。atprotoライブラリ本体は独立した`mastrojs/atproto` repositoryとして既にconfirmed保存されている。siteとblog記事は説明用resourceとして残せるが、atprotoライブラリのservice surfaceではない。

ここまででreview対象21 claimを確定し、残りは87 claimです。

### Repository batch 3

次の10 claimは、対応siteへのrepository側の参照、service側からの参照、または保存済みNSIDのschema定義を確認し、current confirmedとして維持します。

| Project | Project key | Source repository |
| --- | --- | --- |
| Glean | `proj:49cde3c92469` | `https://tangled.org/julien.rbrt.fr/glean` |
| Juttu | `proj:fe8cff17b578` | `https://github.com/matteomarjanovic/juttu` |
| LeafletImporter | `proj:385f289b72e5` | `https://github.com/jorgeuriarte/LeafletImporter` |
| Longpost | `proj:f73daa4b9d5f` | `https://github.com/mohit2152sharma/longpost` |
| PDS MOOver | `proj:d350e5c5c483` | `https://tangled.org/pds.dad/pds-moover` |
| Rito Labeler Kit | `proj:ec46fbcaefff` | `https://github.com/usounds/RitoLabelerKit` |
| SkySweeper | `proj:b749205083ba` | `https://github.com/pojntfx/skysweeper` |
| atproto-backup | `proj:ba1bbc31458b` | `https://github.com/turtlepaw/atproto-backup` |
| pdscheck | `proj:8d1637eee84c` | `https://tangled.org/chaosgreml.in/pdscheck` |
| status | `proj:29c230bf298e` | `https://tangled.org/zzstoatzz.io/status` |

ここまででreview対象31 claimを確定し、残りは77 claimです。

### Repository batch 4

#### ATProtoFans / High Five

- 現project: `proj:d311bd72ebf4`（ATProtoFans）
- ATProtoFansのcurrent source repository claimから外す: `https://tangled.org/atprotofans.com/high-five`
- High FiveをATProtoFansとは別projectとして扱い、このrepositoryと対応するserviceをHigh Five側の構成要素として採用する。
- block: なし
- 根拠: ATProtoFansは支援基盤、High Fiveはその上で提供される個別アプリであり、運営主体やdomainを共有していても提供物としてのレイヤが異なる。

#### 維持

次の2 claimはcurrent source repositoryとして維持します。

| Project | Project key | Source repository |
| --- | --- | --- |
| Local PDS | `proj:6861095a42bb` | `https://tangled.org/niallbunting.com/localpds` |
| hatk | `proj:24e1a816c21d` | `https://github.com/bigmoves/hatk` |

#### finxol

- project: `proj:e08c37ca7a9a`
- current source repository claimから外す: `https://github.com/finxol/auth`
- block: なし
- 根拠: 汎用的なOpenAuth templateであり、保存済みの`io.finxol.test`を含むfinxol projectの実装とは確認できない。

#### flo-bit timelock

- project: `proj:74c0cbe0ee40`
- current source repository claimから外す: `https://github.com/flo-bit/flo-bit.github.io`
- block: なし
- 根拠: 開発者の個人site全体のsource repositoryであり、timelock collectionの実装repositoryではない。

#### freeq

- project: `proj:9ff0c359aeba`
- `https://github.com/chad/freeq`: `former`
- current source repositoryとして追加: `https://github.com/freeq-irc/freeq`
- 根拠: GitHub上の移転。移転先repositoryにはfreeq本体、Web client、AT Protocol認証などが収められている。

このbatchでreview対象6 claimを確定しました。ここまでで計37 claimを確定し、残りは71 claimです。

### Service URL batch 1

#### 維持

次の6 claimは、各projectのcurrent service surfaceとして維持します。

| Project | Project key | Service URL |
| --- | --- | --- |
| $ATA Protocol | `proj:02d789f4311d` | `https://ata-protocol.vercel.app` |
| Pocket Feed | `proj:06310d1eabe8` | `https://pocketfeed.at` |
| Hangar | `proj:146740828299` | `https://hangar.blue` |
| Basker | `proj:1f77ac7acad4` | `https://basker.bio` |
| Bluesky MCP Server | `proj:2fcb88c72090` | `https://bluesky-mcp-server.shraj.workers.dev` |
| FedProxy | `proj:30bf7e01618f` | `https://fedproxy.com` |

`proj:2fcb88c72090`はserviceと`https://github.com/shade-solutions/bluesky-mcp-server`の帰属を維持し、誤ってredirect先から採られたproject名`GitHub`を`Bluesky MCP Server`相当へ修正します。

#### 説明用websiteでありserviceではないURL

次の4 claimはcurrent service claimから外します。いずれもprojectの説明に役立つ公式websiteまたはdocumentationとしては残せます。

| Project | Project key | URL | 判断 |
| --- | --- | --- | --- |
| Bluesky Comments | `proj:1195485f4e06` | `https://bluesky-comments-docs.mhamlin.workers.dev` | project固有のdocumentationであり、実行機能を提供するservice surfaceではない |
| ToolShed | `proj:19a20ea5b015` | `https://toolshed.sh` | HTTPS側は説明site。実機能は同じhostのSSH serviceだが、HTTPS URLを異なるprotocolのservice識別子として代用しない |
| idiolect | `proj:1c7a9313031b` | `https://idiolect.dev` | schema群の公式site兼authority domain。Fieldwork実装が同じprojectに属しても、このURL自体をFieldwork serviceとはみなさない |
| lexrpc | `proj:229e919decf3` | `https://lexrpc.readthedocs.io` | library固有の正式documentationだが、libraryの実行serviceではない |

このbatchでreview対象10 claimを確定しました。ここまでで計47 claimを確定し、残りは61 claimです。

### Service URL batch 2

#### 維持

次の4 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 根拠 |
| --- | --- | --- | --- |
| WhiteBreeze | `proj:3446d362d145` | `https://blog.hugeblank.dev` | WhiteBreezeで実際に配信されるsingle-user blog |
| Granary | `proj:3b0767e1cad9` | `https://granary.io` | 変換機能をその場で利用できるWeb service |
| hughlou | `proj:3e385fd4fd78` | `https://hughlou.com` | AT Protocolを使ったconference replayを提供するWeb service |
| internect.info | `proj:44f1ca9889fb` | `https://internect.info` | ATmosphere探索機能を提供するWeb service |

#### Moat

- project: `proj:3efb7d27c3ef`
- current service claimから外す: `http://moat.social`
- current serviceとして採用: `https://moat.social`
- 根拠: 同一hostのHTTPSへの正規化であり、HTTP側をformer identifierとして残す必要はない。

#### 説明用websiteでありserviceではないURL

次の4 claimはcurrent service claimから外します。対応するprojectを代表するwebsiteとして残すものと、単に説明用resourceとして残すものを区別できる保存方法は別途決めます。

| Project | Project key | URL | 判断 |
| --- | --- | --- | --- |
| atproto-wc | `proj:36c8f12e523a` | `https://atproto-wc.com` | Web Components libraryのdocumentation・demo site |
| Writer | `proj:38bea19f59c3` | `https://writer.stormlightlabs.org` | native appの紹介・配布siteであり、Web上でWriterの機能を提供するserviceではない |
| Lexicon Community | `proj:3dae36ac4ecf` | `https://lexicon.community` | shared lexicon projectの公式website兼schema directory。serviceではないが、projectを代表するWebsiteとして保持する |
| sovereign-comms | `proj:3f13748b6a3b` | `https://coryottenwess.com` | 開発者の個人websiteであり、sovereign-commsのserviceではない |

#### bluesky-feed-generator

- project: `proj:407fbcd42a42`
- current service claimから外す: `https://atproto.blue`
- 根拠: `MarshalX/bluesky-feed-generator`のGitHub homepageが`https://atproto.blue/en/latest/atproto_firehose/index.html`を指していたため、2026-08-11の収集でservice候補化され、static probe成功だけでconfirmedになった。`atproto.blue`は`MarshalX/atproto`のPython SDK documentationであり、bluesky-feed-generatorのserviceではない。

#### WriterからMalfestioを分離

Writer projectに混在する`org.stormlightlabs.malfestio.*`、`malfestio.stormlightlabs.org`、`https://tangled.org/desertthunder.dev/malfestio`はMalfestio側の構成要素として分離します。WriterとMalfestioは同じ開発組織に属しても別の提供物です。

このbatchでreview対象10 claimを確定しました。ここまでで計57 claimを確定し、残りは51 claimです。

### Service URL batch 3

#### 維持

次の6 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 補足 |
| --- | --- | --- | --- |
| discal.dev | `proj:46dced126ba1` | `https://discal.dev` | Discord eventのcalendar同期とAT Protocolへの公開機能を提供する |
| bsky-md | `proj:4ae5b718f07a` | `https://bsky-md.vercel.app` | Bluesky contentをMarkdownで返すWeb APIを提供する |
| did-method-plc | `proj:4b18f1f28ed3` | `https://web.plc.directory` | 説明に加えてDID lookup UIを提供する |
| Waverly Social | `proj:4be43afbf2a0` | `https://waverly.social` | 現行service。`https://mywaverly.com`は別途formerと決定済み |
| Wallet Link | `proj:5140513d54c6` | `https://wallet-link.stellz.club` | wallet link機能を提供する。NSID由来の現project名は`Wallet Link`相当へ修正する |
| atplan | `proj:55e0d611c15f` | `https://atplan.io` | viewerを含むためservice surfaceとして維持する |

#### serviceではない公式website・documentation

次の4 claimはcurrent service claimから外します。

| Project | Project key | URL | 処遇 |
| --- | --- | --- | --- |
| Barazo | `proj:52743420e0c7` | `https://docs.barazo.forum` | documentation resourceとして保持する。現project名`Barazo Docs`は`Barazo`へ修正する |
| tsky | `proj:52e5592062dd` | `https://tsky.dev` | libraryの代表Website・documentationとして保持する |
| atShare | `proj:59a2515d44f6` | `https://atshare.social` | 埋め込み用Web Componentの紹介・demo・配布面。代表Websiteとして保持する |
| arroba | `proj:5c8ecd4e26e6` | `https://arroba.readthedocs.io` | libraryの代表Website・documentationとして保持する |

このbatchでreview対象10 claimを確定しました。ここまでで計67 claimを確定し、残りは41 claimです。

### Service URL batch 4

#### 維持

次の7 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 補足 |
| --- | --- | --- | --- |
| Skystack | `proj:665437c72357` | `https://web.skystack.xyz` | Substack URLの処理とaccount検索を提供する |
| at-work.social | `proj:685741c66c43` | `https://at-work.social` | ログイン後にprofile設定を利用できる。機能が限定的でも、landing pageからproject固有の実機能へ到達できるためserviceとして扱う |
| Notes | `proj:6b4a455e4168` | `http://close.community` | current serviceは`https://close.community`へ正規化し、HTTP側はformerとして残さない |
| Lanyards | `proj:71e516b8a03e` | `https://www.lanyards.app` | Bluesky loginを伴うapplicationを提供する |
| Bluemotion | `proj:781181e32647` | `https://www.bluemotion.app` | 動画hosting service。終了予定という情報だけではformerへ移さず、実際の終了を確認した時点で時間関係として扱う |
| Popfeed | `proj:7c19c3b13943` | `https://popsky.social` | login、投稿、評価、listなどの実機能を提供する。現行表示名に合わせてproject名をPopSkyからPopfeedへ修正する |
| Octosphere | `proj:937479943016` | `https://octosphere.social` | ORCID loginと研究成果の同期機能を提供する |

Popfeedには`https://popfeed.social`もcurrent serviceとして追加します。`https://popsky.social`も現時点で機能を提供しているため併存させ、canonical URLや改名前後としての扱いは時間関係を設計する段階で決めます。この追加は108件のreview対象外にある新規claimであり、確定件数には加えません。

#### serviceではないwebsite

次の3 claimはcurrent service claimから外します。

| Project | Project key | URL | 処遇 |
| --- | --- | --- | --- |
| desertthunder.dev | `proj:643bfa3ae425` | `https://desertthunder.dev` | 開発者個人のportfolio・blogとして保持する。blogがAT Protocol上のdocumentを利用していても、top pageを個別projectのserviceとはみなさない。このproject record自体の位置付けは別途reviewする |
| atproto.dart | `proj:8829cf936560` | `https://atprotodart.com` | SDKの代表Website・documentationとして保持する |
| poptart | `proj:8b0ec3c1e38b` | `https://poptart.xyz` | Dart・Flutter SDKの代表Websiteとして保持する |

landing pageとservice surfaceは外見だけで分けず、そのURLからproject固有の実機能へ到達できるかで判定します。このためprofile設定を提供する`at-work.social`はserviceに含め、紹介・documentationだけのatproto.dartやpoptartは含めません。

このbatchでreview対象10 claimを確定しました。ここまでで計77 claimを確定し、残りは31 claimです。

### Service URL batch 5

#### 維持

次の5 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 補足 |
| --- | --- | --- | --- |
| PuppyDataServer | `proj:9471adacba12` | `https://puppydataserver.com` | login・登録機能を提供するPDS |
| Corgi | `proj:9eed9f36ae4d` | `https://feed.corgi.network` | Bluesky接続、community feedへの参加・投票機能を提供する |
| statusphere | `proj:b6d460767ca8` | `https://statusphere.atmo.tools` | login可能なStatusphere実装 |
| lastfm.blue | `proj:b78137dc424a` | `https://lastfm.butialabs.com` | Last.fmの週間集計を自動投稿するserviceの到達先 |
| lastfm.blue | `proj:b78137dc424a` | `https://lastfm.blue` | 前者へredirectするbrand上の入口。異なるdomainのcurrent aliasとして併存させる |

#### former

- project: `proj:a24717b429de`（Kyron）
- `https://kyron.spidroid.com`: `former`
- 根拠: 現在はDNS解決不能だが、過去の正式なservice URLとして保存されたものであり、noiseとして捨てない。

#### serviceではないwebsite・documentation

次の4 claimはcurrent service claimから外します。

| Project | Project key | URL | 処遇 |
| --- | --- | --- | --- |
| Tiles | `proj:9656822699bd` | `https://www.tiles.run` | native appの紹介・配布Websiteとして保持する。共有chatの表示面`https://chat.tiles.run`は別のservice候補だが、このreviewだけでcandidateからconfirmedへ上げない |
| OpenFederation | `proj:ac6dbbacfa8c` | `https://www.openfederation.net` | 説明・記事用Websiteとして保持し、XRPC APIを提供する`https://pds.openfederation.net`をcurrent serviceとして追加する |
| agora | `proj:b7df7bb4429f` | `https://www.agoracitizen.network` | projectの紹介Websiteとして保持し、実際の操作面`https://www.agoracitizen.app`をcurrent serviceとして追加する |
| BridgeBeats | `proj:c292e0ca6655` | `https://docs.bridgebeats.link` | documentationとして保持する。実serviceの`https://bridgebeats.link`は既にconfirmedである |

OpenFederationとagoraの追加URLは108件のreview対象外にある新規claimであり、確定件数には加えません。

このbatchでreview対象10 claimを確定しました。ここまでで計87 claimを確定し、残りは21 claimです。

### Service URL batch 6

#### 維持

次の2 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 補足 |
| --- | --- | --- | --- |
| lexdns | `proj:cfd0029dff59` | `https://lex.desertthunder.dev` | NSIDからLexiconを解決するUIとREST APIを提供する |
| Library Sky | `proj:d12c2e3c47c3` | `https://library-sky.com` | Bluesky loginと読書記録機能を提供するWeb application |

#### former

| Project | Project key | Service URL | 根拠 |
| --- | --- | --- | --- |
| atweb-vue | `proj:cdedad4d69eb` | `https://atweb-vue.github.io` | 現在はGitHub Pagesの404だが、過去のAT Protocol Web toolとして登録されたURLでありnoiseとして捨てない |
| PubLib | `proj:dbf4c6597041` | `https://www.publib.social` | 現在はhosting側のApplication not foundだが、固有NSIDとproject accountを伴う過去の提供面として保持する |

#### serviceではないwebsite・documentation

次の4 claimはcurrent service claimから外します。

| Project | Project key | URL | 処遇 |
| --- | --- | --- | --- |
| BridgeBeats | `proj:c292e0ca6655` | `http://docs.bridgebeats.link` | HTTPSへredirectするdocumentationのHTTP入口。formerには残さない |
| mcp-server-bluesky-py | `proj:cff85554009c` | `https://khleel213.github.io` | MCP serverの紹介・導入案内を行う代表Websiteであり、稼働中のMCP service endpointではない |
| Skeeditor | `proj:d25310ba67bf` | `https://docs.skeeditor.link` | browser extensionの代表Website・documentationとして保持する |
| pdsx | `proj:d27b446ac7c1` | `https://pdsx.zzstoatzz.io` | CLI・MCP serverの代表Website・documentationとして保持する。hosted MCP endpoint候補`https://pdsx-by-zzstoatzz.fastmcp.app`とは分ける |

#### 調査対象外のproject

次の2件はAT Protocolと無関係であり、URLのroleだけを修正せずproject全体を調査対象外とします。

| Project | Project key | URL | 根拠 |
| --- | --- | --- | --- |
| Atlas-protocol-colosseum-solana | `proj:ca196e3df3c5` | `https://atlasfi.in` | Solana上の財務・ゼロ知識証明project |
| atlas_protocol | `proj:dbefc6b34927` | `https://atlas-protocol.readthedocs.io` | single-cell解析用の生命科学library |

このbatchでreview対象10 claimを確定しました。ここまでで計97 claimを確定し、残りは11 claimです。

### Service URL batch 7

#### 維持

次の4 claimはcurrent service surfaceとして維持します。

| Project | Project key | Service URL | 補足 |
| --- | --- | --- | --- |
| Substack to atproto migration tool | `proj:de6cfabddedc` | `https://substack-to-atproto-migration-tool.vercel.app` | browser内でSubstack投稿の移行処理を実行できる |
| atproto-loader | `proj:e5a0370c7fc8` | `https://phew.network` | Bluesky投稿内のJavaScriptを読み込み、表示・実行する機能面 |
| atmo.pics | `proj:e9014057b0eb` | `https://atmo.pics` | loginと画像共有機能を提供する |
| etzhayyim | `proj:fe7957f9be5e` | `https://etzhayyim.com` | project固有のlive observation、actor閲覧などを提供する。AT Protocol実処理を担う`aozora.app`とは別のservice surface |

#### former

| Project | Project key | Service URL | 根拠 |
| --- | --- | --- | --- |
| Skypush | `proj:ea9378ef5f52` | `https://skypush.tomo-x.win` | Bluesky上の紹介を経て登録された過去のservice URLだが、現在はDNS解決不能 |
| Impact Indexer | `proj:fd72f40ebe1e` | `https://www.impactindexer.org` | 現在は証明書切れに加えてVercel deploymentも消失している。NSIDと過去の実装痕跡があるためnoiseとして捨てない |

#### current serviceから外す

| Project | Project key | URL | 処遇 |
| --- | --- | --- | --- |
| Soleia | `proj:e5f7a5da4236` | `https://dev.soleia.app` | production serviceの旧URLではなく、停止した開発用URL。formerではなくnon-production観測として保持する |
| Trotsky | `proj:f9a4c3238ea0` | `https://trotsky.pirhoo.com` | libraryの代表Website・documentationとして保持する |
| Trotsky | `proj:f9a4c3238ea0` | `http://trotsky.pirhoo.com` | HTTPSへ正規化されるHTTP入口。formerには残さない |

#### 調査対象外のproject

- project: `proj:dcfb2e5c949f`（at_protocol）
- URL: `https://docs.atsign.com`
- 処遇: AtsignのatProtocolでありAT Protocolとは異なるため、名前一致で混入したproject全体を調査対象外とする。

このbatchでreview対象10 claimを確定しました。relationまたはlegacy project contextから採用理由を補完できる107 claimは、これですべてreview済みです。

shadow comparisonで補完不能だった残り1 claimは、冒頭で別途`former`と決定したWaverly Socialの`https://mywaverly.com`です。したがって、移行review対象108 claimはすべて処遇が確定しました。

## 保存契約への反映

`lexicon.community`のようにprojectを代表する公式WebsiteでありながらserviceではないURLを扱うため、Website、service、補助pageの責務を[Identifierの採用理由と保存契約の設計メモ](identifier-adoption-storage-design.md#websiteservice補助pageの境界)で分離しました。通常の保存・表示・信頼判定は分離済みです。この文書で確定した再分類は複製DBで検証済みですが、正本DBには未適用です。
