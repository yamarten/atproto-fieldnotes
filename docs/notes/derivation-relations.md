# 派生関係の設計メモ

この文書は未実装の設計メモです。現行の `project_relations`、trust graph、merge 判定へはまだ接続していません。

## 背景

Statusphere 派生、GitHub fork、template/example 由来の project は、名前、repo 構造、lexicon、説明文が派生元と似るため merge candidate になりやすいです。一方で、独自 service、domain、collection、account、運用実態がある場合は派生元と同一 project ではありません。

この種の関係は「同一性の根拠」ではなく、「系譜・派生・実装参照」の弱い関係として表す必要があります。誤って merge すると、別 project の confirmed/manual identifier が同じ project 内に入り、trust anchor として使われる危険があります。

## 方針

- fork/template/statusphere 派生は、merge の肯定根拠にしない
- 独自 identity / service / domain / collection がある派生は別 project として保持する
- 独自 surface がない source-only fork は `ignore` / out-of-scope review 候補にする
- 派生元と派生 project の関係は、必要なら弱い派生関係として保存する
- 派生関係は trust anchor にしない
- 派生関係がある pair は、merge candidate から除外するか、confidence を強制的に下げる

## 候補 Relation 名

候補名は未確定です。現行 relation に追加する場合は、保存契約と Web 表示を別途設計します。

- `fork_of`
  - GitHub などの provider metadata で fork parent が分かる場合
  - GitHub の `fork=true` だけを同一性根拠にしない
- `derived_from`
  - 派生元 project / tutorial / example app を元にしているが、GitHub fork metadata では表せない場合
- `based_on_template`
  - Statusphere など、template/example 由来と判断できる場合
- `implements_example`
  - tutorial/example の実装例として存在する project を表したい場合

## Merge への使い方

派生関係は merge の正の score に加算しません。使う場合は次のどちらかです。

- 強い抑止:
  - pair が派生関係で結ばれていれば duplicate candidate に出さない
  - 既に `separate_project` と同じ「誤 merge 防止」の意味で使う
- 弱い抑止:
  - candidate には出すが `merge_confidence=low` に抑える
  - 表示に「派生関係あり」を出し、通常の merge 理由とは分ける

実運用では、独自 surface が確認できた派生は強い抑止寄りが安全です。source-only fork は merge ではなく ignore/out-of-scope review へ送ります。

## Trust への使い方

派生関係は trust graph の `eligible_for_auto_confirm` にしません。

特に次は避けます。

- 派生元 repo が confirmed だから派生 repo/service/collection を confirmed にする
- template の NSID を使っているから派生 project の collection ownership を confirmed にする
- 派生 project と派生元 project を merge して、双方の manual seed を同じ trust anchor pool に入れる

## 未解決

- relation 名を project relation として持つか、別の派生関係専用 field にするか
- Web merge review で `keep separate` と派生関係作成を同時に行うか
- Statusphere 派生をどの evidence から検出するか
- GitHub fork metadata、README の派生元 link、package/template marker をどこまで保存するか
- 既存 `fork_without_independent_identity` review とどう統合するか
