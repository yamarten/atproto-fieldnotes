# Service publication evaluation — 2026-09-10

作業・検証記録。恒久仕様は[investigation model](../developer/investigation-model.md)。

## 結論と適用範囲

candidateは公開先候補の記述だけで追加し、取得成功・逆参照・表示確認を要求しない。
自動confirmは現在のprojectの公開先宣言と公開先側の対応repo参照、種類に応じた実在確認を組み合わせる。
運営者を確定したことにはせず、`operates_service`を生成しない。repoのhomepage指定だけでserviceを再採用する
旧trust経路は復活させない。変更はworktreeだけで、正本DBや実行中enrichには適用していない。

## 実装

- repository analysis v16に`declares_service_url`を追加。本文中の正確なURLだけをclosed candidateとして提示する。
  `current_project`と`uncertain`を候補へ反映し、否定・仮定・第三者・旧公開先・欠落は反映しない。
  最新成功解析の`current_project`だけを自動confirmの宣言根拠にする。
- service candidateの既存正規化は一般URLをrootへ丸めるため、公開先宣言に対応するURLはexact pathを保持する。
  temp DBのsave・load・prune・replayでもpathを維持する。
- 通常enrichはrepo解析後に新しい宣言URLを取得し、既に同runで取得済みなら再利用する。
  role-v4は既存project siteでもservice条件を再評価し、既存のsite採用は撤回しない。
- Webは初回到達pageの表示済みauth/public surfaceを確認する。API/PDSはbase配下の
  `com.atproto.server.describeServer`への1 requestで応答形式とexact endpointを確認する。
  API rootのplain text `Code:` / `Source code:`行も対応repoへの逆参照として扱う。
  追加確認はWeb/protocol合計1 URL／enrich。失敗attemptも保存して他URLとの順序に反映する。
- 宣言だけの未採用URLも他projectとの競合検査に含める。replayは現在の採用repo、宣言のrevision・URL、
  applicability、guardを再確認し、新しい解析結果を古い保存結果で上書きしない。

## 意味上の検証

`tests/fixtures/project_registry/service-publication-v1.json`に保存済みREADMEの抜粋と構成した反例9件を固定した。
既定LLMで各2回、計18回の公開先候補抽出が期待値と一致した。デモ・Impro・パス付きAPI・SkyGiveは候補に入り、
forkの上流・第三者・否定・仮定・documentation専用は除外された。
これは小さな調整用corpusの結果で、一般精度やservice自動confirmの正解率ではない。
SkyGiveのREADMEは候補生成の根拠にだけ使い、公開pageの対応が未確認なので実serviceとしては確定していない。

補正済み18実recordを正本read-only snapshotからtemp DBへ移し、最新保存bundleを新コードでreplay・保存した。
対象service確定の再発0、Website変更0、別service集合の変更0。temp DBは終了時削除済み。
これは保存済み根拠の再評価であり、新しい外部取得を含む再enrichではない。

全856 unit test成功（`bin/project-registry-test`）。`git diff --check`も成功。

## 検証の限界

- Web/protocolの新条件、候補の保存、同runでの新規URL取得、fork逆参照、競合、replayはunit testで確認した。
- protocol実サーバーへの新規smokeは未実施。新たな追加enrichを保留する運用下で、固定応答による検証に限定した。
- describeServer非対応の汎用APIやColibri AppViewの独自APIは自動confirmできずcandidateに残る。
  HTTP 200や一般JSONを代替根拠にするfallbackは追加していない。
- 未取得の将来の他project宣言は競合検査できない。LLMによるfork／第三者の読み違いは一般的には残り得るため、
  公開先側の逆参照・guardを独立に要求する。
