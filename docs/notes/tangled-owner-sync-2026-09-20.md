# Tangled owner解決の統合・再開材料

修正コードでは停止・profile未登録ownerが他候補の取り込みを恒久的に止めないことを、temp DBと正本read-only smokeで確認した。本番execute、定期presetの再開・有効化変更、Web reload/restartは行っていない。母艦でcommitを監査・統合し、実運用の境界を確認してから再開を判断する。

## 問題と採否

2026-09-19 23:05 JST開始の`sync_sources_then_enrich`は最初の`sync tangled`で失敗した。母艦が確認したjobは`job:3783c45cceab`、operationは`sync-20260919T140531Z-b549da87`。Bluesky `getProfile`の400がowner単位で扱われず、書込み前の解決段階で停止していた。

| 案 | 普段の増分取り込みへの影響 | 判断 |
| --- | --- | --- |
| profile取得を維持 | Tangledを利用できるidentityでもBluesky停止・profile未登録だけで同期できない | 不採用 |
| profile失敗を単純skip | 有効候補の同期は進むが、後で復旧しても発見済みrecordを失う | 不採用 |
| 標準identity確認＋保留catalog | profileの有無から独立して確認でき、解決不能recordも再評価できる。外部解決requestと保留の保存量は増える | 採用 |

repo URLの照合規則・project帰属policyは維持した。DID形式URLへの切替は、既存handle形式URLとの同値管理が必要なため今回含めない。owner DIDとrecordのrepoDidは区別し、ownerが同じという理由だけで別repoを統合しない。現行の詳細は[取り込み仕様](../developer/specs/discovery-and-sync.md#ownerのidentityと解決不能record)を正本とする。

## 正本read-only確認

2026-09-20、正本`data/survey.db`を絶対pathで`mode=ro` / `query_only`接続し、変更後の`sync_tangled_repos(execute=False, max_items=1000)`を実行した。APIキーは母艦の標準`direnv exec .`環境からのみ利用し、値は出力・保存していない。worktreeにはlockfileどおり`npm ci --ignore-scripts`でSDKを導入した。

- 開始watermark: `23491334`、`last_success_at=2026-09-12T14:05:25+00:00`
- 取得／選択: 1,000 event、関連owner: 39、`truncated=true`、`snapshot_error={}`
- 既存一致: 7、作成候補: 153、関連外: 837、owner解決保留: 3 record / 1 owner
- 作成候補件数はdry-runでの評価件数であり、実作成数やunique repo数ではない
- `state_updated=false`、終了watermarkも`23491334`。実行前後のsync state一致と接続の`total_changes=0`を確認

| 既知owner | record | 変更後の結果 |
| --- | --- | --- |
| `did:plc:u5bivxodft4ol76wydfyvwlg` | millipds、carsync、pdsls | DID documentのclaimは取得できるが逆引きが400 `InvalidRequest` / `Unable to resolve handle`。3件を`handle_unresolved`として保留候補にした |
| `did:plc:3c6vkaq7xf5kz3va3muptjh5` | atproto-basic-notifications、pdsls | `partially.dead.aylac.top`の双方向検証に成功し、通常候補として評価した |

resolverの`Unable to resolve handle`には上流DNS障害も含み得る。この応答を永久無効と断定せずrecordを保持する。未知の400、認証不良、通信例外、429、5xxは全体失敗とし、checkpointを進めない。

## 統合後に残る境界

- 全unit testと対象回帰testの結果は修正commitの記録を参照。保留の同時保存、表示上限を超えた保持、再取得、delete fold、同じownerの複数repo、作成無効時のwatermark維持、キャンセル、保存例外rollback、workerの原因表示をtemp DBで検証した
- 本番に保留catalogを作成した確認、実際の停止owner復旧後の自動取り込み、長期間の保留件数・API利用量は未検証
- 正本DB修復、watermark手動更新、全history再走査、個別projectの処遇判断は行っていない
- 母艦task: `01a050d2-495b-7fc0-85f5-3fb214fd1c9f`（local）。統合後の検証と定期preset再開の判断は母艦で行う。再開境界の保留が解消したら、このnotesの有効性を見直す
