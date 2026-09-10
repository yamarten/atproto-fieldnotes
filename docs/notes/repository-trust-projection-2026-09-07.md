# Page↔repository edgeのrelation導出への変更

2026-09-07の調査・実装・移行検証の記録です。恒久仕様は
[現行規則](../developer/specs/project-boundaries.md#trust-graph)、設計原則は
[登録プロジェクトへの帰属と信頼](../developer/specs/project-boundaries.md)を参照してください。

## 結論と診断

page↔repositoryの保存edgeは現在のrelationと別に強いclassificationを保持でき、古い根拠が再利用される
不整合がありました。relationを正本とし、判定用edgeを共通投影で導出する方式へ変更しました。
独立したHTML/README/LLM観測もrelationに保存します。account/NSID policyの拡張は行っていません。
この記録の初回検証時は正本DBへの移行・運用反映は未実施でした。2026-09-10に導出コードの実運用反映と
旧保存edge全11,485本の永続archiveへの退避を完了しました。適用結果は末尾「2026-09-10 最終反映・完了」を参照してください。
最終方針は、過去判断を現役relationへ復元せず、旧edge全体と判断記録を履歴へ退避するものです。
以下の復元実験・退避単独modeは検討履歴であり、現行の実行方法は末尾「全旧edge退避への統一」を参照してください。

実例はlexrpc（`proj:229e919decf3`）の`https://lexrpc.readthedocs.io`から
`https://github.com/bluesky-social/atproto`への関係です。current relationはcandidateなのに、
同じsource/method/時刻の保存edgeがconfirmed/eligibleのままでした。旧syncはclassificationの最大値と
eligibleのORを使うため弱い新判断で強い旧判断を置換できませんでした。再現した問題は候補repoの追加調査へ
誤った根拠を渡せることです。この事例から最終的なidentifierの誤採用まで確認したわけではありません。

当初の「source名を変えても支持された」という反例は、predicateに与えた意味を保ったままsourceだけを
変えており、意味上の誤判定の証明ではありません。sourceの一律allowlist追加は採用せず、実際に確認できた
relationとedgeの不整合を解消しました。

## 実装境界

- `repository_trust.project_edges()`でpage↔repoおよびhomepage pairを導出し、保存graphとの混合をやめた。
- 同一観測は新しい時刻の判断へ置換。source/method/locatorが異なる根拠は独立して保持する。
- LLMの許可されたpair、判断、理由、時刻をrelationへ保存する。未評価pairを否定と扱わず、明示的な
  `related`/`uncertain`だけが同じpairの旧confirmedを置換する。
- failed page fetchは旧観測を削除しない。現在のURL支持は現在の起点・relationで再評価する。
- `relation_corroboration`も共通trust評価・guard・adoption記録を通す。
- Explainと二つのshadow比較を共通投影へ接続。URLのproduction比較は保存済みverdictを現在評価と混同しない。

## 既存データと移行

複製検証に使った正本DB snapshot（4752 project、10665本の旧page↔repo edge）の分類は次のとおりです。

|分類|本数|処理|
|---|---:|---|
|現在のrelationで説明できる|5106|relationを維持し旧mirrorを除去|
|保存済み判断から復元できる|245|204件のLLM relationを復元し、そこから導出できるhomepage pair 41本も整理|
|要review|5314|旧edgeを保持するが現在の信頼経路には使わない|

最新LLM結果からpairが消えていた29本は、29本とも過去runの入力pair、same_project判断、generated_atと
edge時刻の一致を確認できました。復元時の履歴検索は時刻に隣接する最大2 runに限定します。時刻一致や
入力pairを確認できないLLM edge 9本は要reviewです。HTML/READMEの古い全観測を履歴から網羅的に復元する
処理は実装していません。保存上限のある最新本文・リンク一覧だけで当時の判定を再現したとは扱いません。

要reviewの内訳はREADME 3029、HTML 1720、旧first_party_link 369、bounded link 93、旧identity match 82、
homepage pair 12、LLM 9です。これらを誤りと断定していません。移行前の旧edgeだけが持っていた支持は
新しい判定へ引き継がれないため、既存DBに対して直ちに同等の判定結果になるとは主張しません。

`migrate-repository-trust --db-path <db> --output <plan.json>`でdry-runを生成します。
review後、同じDBに`--execute-plan <plan.json> --output <result.json>`で適用します。
実行は書込みtransaction内でplanを再計算・照合し、計画差分があれば中止します。
正本への実行には、別途承認、backup、他writerの停止が必要です。

## 検証

- `bin/project-registry-test`: 全808件成功。新規回帰テスト11件を含む。

- 新規回帰テスト: stale confirmed/current candidate、同一観測の格下げと古い入力の再到着、独立source保持、
  homepage pairの前向き依存、LLM未評価・明示的related、failed fetch、stale verdict、旧edge隔離、
  lexrpcの候補probe経路、temp DB移行、圧縮run履歴からの復元と入力pair欠落時の保留。
- 実データ複製: SQLite backup APIで`.tmp`に複製したDBだけに移行を実行。
  5351本を整理、204 relationを追加、未解決5314本を保持。再dry-runで未解決分だけになることを確認。
- `project_identifiers`、`project_identifier_adoptions`、`project_decisions`、`investigation_runs`、
  `collector_runs`は移行前後で全行ハッシュ一致。foreign key違反0、integrity_check `ok`。
- read-only shadow比較: 4752 project / 21346 claim、固定ケース12/12、評価方式間の不一致0。
  現在relationの投影と対象外edgeのdedupe後は66079本、shadowで許可されたedgeは6131本。
  これは新旧policyの同等性の証明ではなく、未復元edgeを使わない投影の結果。
- 正本DB書込み0、外部network/LLM実行0。Web UIは変更していないためbrowser検証は対象外。

最終dry-runでは正本DBが検証snapshotから更新されており、10646本中、current relation 5095本、
復元可能243本、要review 5308本となりました。これは検証snapshotと別時点の計画です。
最終plan digestは`8377e034f7b074a0dd8d457ef385896f1f2389c8849de781750600ccc8bec7bf`。
移行実行前には新しい計画をreviewし、古い件数を適用対象と取り違えないでください。

## 初回検証時に残っていた運用作業

以下は初回検証時の残件です。正本DBへの適用は末尾の最終反映記録で完了しています。
当時は要review分の扱いと、正本DBへの適用判断が残っていました。通常repairによる無条件の格下げや旧edge一括削除は行いません。

## 2026-09-10: 旧保存edgeの整理再開

最新正本DBのread-only dry-runは旧edge 11,516本、current relation 7,837本、復元可能200本、
要review 3,479本だった。以前のsnapshotとの差を移行による削除件数と混同しない。

要reviewを現在relationのpair・source・methodへ照合し、READMEの出典が`owner/repo`と完全URLに
分かれたための照合漏れを確認した。実例はskywatchedの同じ時刻のREADME観測。
移行処理だけでこの表記差を吸収し、既存の完全一致sourceを優先するよう修正した。
現在relationを旧edgeの強いclassificationへ引き上げず、別sourceや別methodの観測も混同しない。
新たに説明できた211本を含め、別snapshotの複製DBで以下を検証した。

|分類|本数|複製DBでの処理|
|---|---:|---|
|現在relationで説明できる|8,048|保存edgeだけを除去|
|保存済み判断から復元可能|200|184 relationを追加し、派生homepage pair 16本と合わせて保存edgeを除去|
|要review|3,268|保存graphに保持|

8,248本の除去後、再dry-runは要review 3,268本だけになった。
`project_identifiers`、`project_identifier_adoptions`、`project_decisions`、`investigation_runs`、
`collector_runs`は前後の全行hash一致。foreign key違反0、integrity `ok`。複製DBは終了時削除。
全862 unit testとdiff check成功。正本DBへの書込み、外部取得、enrich停止、master統合は行っていない。

残りの内訳: HTML 1,687、README 945、bounded link 310、旧first_party_link 299、
旧identity match 11、LLM 9、homepage pair 7。
古いHTMLの`GitHub`表示だけのconfirmedを再び有効なrelationとして復元することはしていない。
最新本文の省略や別sourceの同じpairだけでは、古い独立観測の復元・失効を断定しない。

この時点で未解決分の退避か保持をユーザーへ確認した。その後の合意と検証は次節を参照。
確認用の完全planは`.tmp/legacy-edge-reviewed-plan-20260910.json`、未解決分の全edge一覧は
`.tmp/legacy-edge-unresolved-20260910.json`、検証結果は`.tmp/legacy-edge-verification-20260910.json`。
これらは作業用であり、永続archiveとして扱わない。正本は検証中も別writerにより更新されているため、
実適用時にはwriter停止・backup後に再計画して照合する。

### 未解決分の退避単独mode

ユーザーは判定不変を条件に、未解決分を履歴へ退避して保存graphから除く方針を承認した。
relation復元は判定入力を増やす処理なので、退避単独modeと分離した。
`--execute-plan`に`--archive-unresolved <new archive.json>`を併用すると、未解決分だけを退避・除去し、
説明できる旧edgeや復元可能な判断には触れない。書込みtransaction内でplanを再照合した後、
原文の追加field・重複を含めて新規fileへ保存し、fsync・再読込検査の成功後だけDBを変更する。
archiveは通常の判定に読み込まず、後から必要な観測を調べるための記録として扱う。

正本の別時点snapshotは4,722 project、旧edge 11,489本（current relation 8,083、復元可能190、未解決3,216）。
前節との差はDB更新を挟んだ別snapshotによるもので、このtaskの正本への書込みによる差ではない。
公開CLIで複製DBのdry-run→退避を実行し、以下を確認した。

- 原文3,216本を退避し、archiveの内容は対象の保存観測と完全一致。relation復元0。
- 全4,722 projectの`trust_graph.graph()`が前後で完全一致。
- `project_relations`、`project_identifiers`、`project_identifier_adoptions`、`project_decisions`、
  `investigation_runs`、`collector_runs`の全行hash一致。
- 再dry-runに未解決edgeなし。説明可能8,083本と復元可能190本は維持。
- foreign key違反0、integrity `ok`。複製DBは終了時削除。
- 原文保存、書込み失敗時のDB不変、上書き拒否、古い計画の拒否をunit testで確認。
  CLI補完を更新後、全865 unit testとdiff check成功。

結果は`.tmp/legacy-archive-verification.json`、確認用archiveは`.tmp/legacy-unresolved-archive-reviewed.json`。
これらは検証用。正本DBの永続退避は未実施で、実行時は`data/migration-archives/`等へ新規保存する。
worktreeの変更をまとめて反映する方針を維持し、master統合・enrich停止・正本DB書込みは行っていない。
未解決分の退避そのものは判定不変だが、後続のrelation復元の影響まで不変とは主張しない。

### relation復元による通常判定の差分

同じ件数の新しいsnapshot（旧edge 11,489本）で通常移行を複製DBに適用し、保存されるURLが不変である
こととは別に、復元前後の通常判定を比較した。復元対象は132 projectの176 relationと派生homepage pair 14本。
復元対象projectの保存URLと前後graphに登場するURLの和集合、1,764 nodeを評価した。

- `trust_auto_confirm_identifier`: 自動確定可否の変化0。
- `reconcile_trust_supported_url_candidates`: service / repoの採用結果の変化0。
- `candidate_repo_trust_probe_urls`: 10 projectで各1 candidate repoが追加調査対象へ入る。
  AT Todo、Seams、Curio、SkyScheduler、cocore.dev、Flashes、onrepeat.fm、Blacksky、checkin、Oolong。
- 登録URL・採用判断・調査履歴の全行hash不変、foreign key違反0、integrity `ok`。複製DBは削除済み。

追加調査対象の判断理由を確認したところ、sourceへの明示リンクを挙げるものに加え、名前一致・同一project
という推測だけの理由がある。SkySchedulerには既に採用済みrepoとの大文字小文字違いも含まれる。
10件が誤ったrepoと判定したわけではないが、保存された判断の再現可能性だけで現在のconfirmed根拠としての
妥当性を再認定したとは扱わない。通常処理と同じ入力pair・時刻照合は、当時の判断が実在したことの検証。

別途、現在の最新LLM出力と176 pairをread-only照合し、same_project 127、最新出力にpairなし49、
related / uncertainとの明示的矛盾0だった。未評価49件を否定と読み替えない。
また、この照合は新しいpage/repoの取得や意味上の正解確認ではない。

推奨案は、復元可能な過去判断も原文・入力pair・理由を履歴として保持し、現役relationへの復元は行わないこと。
これなら現行判定を変えずに旧edgeを整理できる。一部だけを現役へ復元する場合は、その判断の妥当性を
追加確認する必要がある。復元可能分の退避は、未解決分だけを退避する既存合意の対象拡大なので未合意。
この方針変更の実装・正本適用はまだ行っていない。

比較結果は`.tmp/relation-restoration-impact.json`、最新判断の照合は
`.tmp/relation-restoration-latest-review.json`。DB操作は複製だけで、外部通信・LLM実行は0。
この追記では通常コードを変更しておらず、全unit testは前回865件成功から再実行していない。

### 全旧edge退避への統一

ユーザーが復元可能分も含む履歴退避を承認したため、旧page↔repo edge全体の退避に統一した。
`migrate-repository-trust --db-path <db> --output <plan.json>`で分類付き計画を生成し、
`--execute-plan <plan.json> --archive <new archive.json> --output <result.json>`で実行する。
実行には新規archiveが必須。未解決分だけのflagと、現役relationへ復元する実行経路は撤去した。
過去判断のinput pair・理由・時刻・出典runを含む計画全体と、保存edgeの原文をarchiveへ残す。
既に現在relationで説明できるedgeも退避に含めるため、削除した原文はすべて追跡できる。

公開CLIを使い、正本の複製DBで全11,489本の退避を検証した。
内訳は現在relation 8,083本、判断記録を復元できる190本、未解決3,216本。
archive内の原文・計画は保存内容と完全一致、再dry-runの残件0、現役relationへの復元0。
全4,722 projectの判定用graph、およびrelation・登録URL・採用判断・調査履歴の全行hashは前後一致した。
foreign key違反0、integrity `ok`。複製DBは削除済み。全865 unit test、補完のzsh構文検査、diff check成功。

検証結果は`.tmp/legacy-complete-verification.json`、確認用archiveは`.tmp/legacy-complete-archive-reviewed.json`。
旧edge整理の実装・検証に残件はなく、正本への実行と実行後確認が未完了。
実行時には稼働中writerを止め、backup後の新しい計画を照合し、永続archiveへ保存する。
この検証ではmaster統合・正本DB書込み・enrich停止を行っていない。


## 2026-09-10 最終反映・完了

`7c5a1c2`までを正本checkoutへfast-forwardし、全865 unit test成功。
木曜enrich `enrich-20260909T224730Z-44374263`を承認に従ってキャンセルし、Web停止・writer不在確認後に実行した。
直前backupは`data/backups/final-publication-rollout-20260910T0225Z/`。
最新planはcurrent_relation 8,092、reconstructable_judgment 187、needs_review 3,206。
全11,485本を`data/migration-archives/repository-trust-20260910T0225Z/archive.json`へ退避し、
relation復元0、再計画残件0。raw edgeとreviewed planの完全一致、全4,722 projectの判定用graph、
project_relations / project_identifiers / project_identifier_adoptions / project_decisions /
investigation_runs / collector_runsのhash不変、foreign key違反0、integrity `ok`を確認した。
同directoryにplan・result・verification JSONを保存している。以前の複製DBとの件数差はenrich進行後のsnapshot差。

Webを反映済みコードで再起動し、health成功、project一覧・詳細と定期設定APIのHTTP 200、job一覧とqueueが空で
あることを確認。定期設定を維持し、キャンセルrunは再開していない。公開先判定は以後の通常enrichで利用する。
既存全件の新policy再解析や、新たな本番enrichでの自動採用確認は行っていない。意味上の動作確認は上記の
実公開先評価とtemp DB保存・replay、通常pipelineのunit testに基づく。

今回の目的は採用済みrepo→公開先候補→根拠の揃うserviceの自動採用までで完了。
汎用API・AppView独自API、primary project_site自動採用、広いpolicy/LLM接続は未完了の別課題としてroadmapに残す。
