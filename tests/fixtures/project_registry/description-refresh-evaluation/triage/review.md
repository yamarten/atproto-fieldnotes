# 説明更新の品質レビュー

推奨は今回のprompt案を不採用とすることです。技術的検証は終了しましたが、人が訂正する手間を減らせる品質には届いていません。追加の説明専用比較を経ても目標に届かず、ユーザーはこの結果でのタスク完了を了承しました。保持物と確定状態は親ディレクトリのREADMEを参照してください。

Aは開始HEADの現行prompt、Bは既存規則を整理した候補です。同一model・通常triage構築・suffix・transport・適用・保存codecで比較しました。外部collectorを含むenrich全体ではありません。本文は架空の固定素材であり、実在project全体の精度や人の訂正時間の測定ではありません。

旧4素材を調整用とし、Aは終了・機能変更の各1回、Bは4素材各1回と終了・機能変更の追加反復を実施。B固定後の未使用4素材は各1回です。合計12生成。旧誤記・旧障害のA比較と未使用素材のA比較は今回実施していません。過去8出力は別時点・旧構成の参考です。

全1020 unit testと対象15 testが成功しました。12出力×5保護条件の60条件を一時DBに保存・読戻しし、説明・編集状態・trackingを照合。来歴不明（過去LLM出力との完全一致を含む）、人編集、保存hash不一致を保護。JSONであること・根拠IDが存在することを品質合格にはしていません。

## 費用と反復


```json
{
  "baseline": {
    "count": 2,
    "seconds": 24.327,
    "median_seconds": 12.163499999999999,
    "min_seconds": 9.038,
    "max_seconds": 15.289,
    "input_tokens": 7528,
    "output_tokens": 1046
  },
  "candidate": {
    "count": 6,
    "seconds": 55.965,
    "median_seconds": 9.440999999999999,
    "min_seconds": 6.695,
    "max_seconds": 13.23,
    "input_tokens": 24402,
    "output_tokens": 3130
  },
  "holdout": {
    "count": 4,
    "seconds": 36.986,
    "median_seconds": 9.365,
    "min_seconds": 8.289,
    "max_seconds": 9.967,
    "input_tokens": 16228,
    "output_tokens": 1938
  },
  "all": {
    "count": 12,
    "seconds": 117.278,
    "median_seconds": 9.392,
    "min_seconds": 6.695,
    "max_seconds": 15.289,
    "input_tokens": 48158,
    "output_tokens": 6114
  }
}
```

Qwen 3.6 35B A3B、MLX 4bit、Responses、temperature 0.2、reasoning要求none（適用はendpoint未報告、usageのreasoningは全件0）、各2048 token／180秒。出力424〜548 token。API報告のloaded contextは183296。runtime細版・template・parallel・memory／電力／金銭費用は未測定。

Aの初回15.289秒、次9.038秒。B調整用の初回13.230秒、残り6.695〜9.732秒。同素材2反復目は7.426秒／6.695秒でcached tokenが増加しています。速度改善の因果効果とはしません。難例のB入力はAより329 token増。

## 原文と説明全文

各欄のrawはmodelの出力、automatic保存後は通常codecで読み戻した文面です。差がなければ省略を明記します。保護条件の保存後文面は全件、各節の更新前と同一です。生の全JSON・evidence・実送信promptは同じディレクトリにあります。

### closure — Reading Loom

旧素材・調整用

**入力原文（全文）**

> Reading Loom was a book reading journal service. Users stored book records and reading notes in their own AT Protocol PDS using the custom org.readingloom.book Lexicon. Official closure announcement: the hosted reading journal service permanently ended on 2026-08-31. The Lexicon definitions remain publicly available for developers. This announcement does not describe any replacement hosted service.

**更新前（全文）**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ]
}
```

**A（現行） 反復0 — raw全文**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のATProtocolPDSに保存する。2026年8月31日をもってホストされた読書ジャーナルサービスは永久に終了した。Lexicon定義は開発者向けに公開されているが、置き換えとなるホストサービスは存在しない。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービスが2026年8月31日に終了し、ホスト版は存在しないがLexicon定義は公開されているという事実を反映",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "読書記録サービス",
  "summary": "読書記録とメモを共有するサービス。独自Lexiconで書籍・読書メモを表現し、利用者のPDSに保存していた。2026年8月31日をもってホストされた読書ジャーナルサービスを永久に終了した。Lexicon定義は開発者向けに公開されているが、代替ホストサービスは存在しない。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービスが2026年8月31日に終了し、現在ホストされていない事実を反映し、過去形および終了事由に修正した。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**B（候補） 反復1 — raw全文**


```json
{
  "tagline": "読書記録サービス",
  "summary": "読書記録とメモを共有するサービス。独自Lexiconで書籍・読書メモを表現し、利用者のPDSに保存していた。2026年8月31日をもってホストされた読書ジャーナルサービスは永久に終了した。Lexicon定義は開発者向けに公開されているが、置き換えとなるホストサービスは存在しない。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービスが2026年8月31日に終了し、ホスト版は存在しないことを反映。Lexiconの公開状態は維持。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: 現行A・改善Bの2反復とも不適合。終了後もtaglineとsummary冒頭、featuresが現行サービスとして読める。根拠は代替サービスへの言及がないだけだが、全出力が不存在を断定。BでPDS保存だけ過去形になっても全体の整合は改善していない。

<details><summary>2026-09-15の旧構成・参考出力2反復（今回のAとは区別）</summary>

反復0


```json
{
  "tagline": "読書記録共有サービス",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のATProtocolPDSに保存する。公式発表により、ホストされた読書ジャーナルサービスは2026年8月31日に永久に終了した。Lexicon定義は開発者向けに公開されたままだが、置き換えとなるホストサービスは存在しない。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービス終了の事実と、Lexicon定義のみが公開されていることを反映。公式発表に基づくサービス状態の更新。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

反復1


```json
{
  "tagline": "読書記録共有サービス",
  "summary": "読書記録と読書メモを独自Lexiconで表現し、利用者のAT Protocol PDSに保存する読書ジャーナルサービス。2026年8月31日をもってホストされたサービスは永久に終了した。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービスが2026年8月31日に終了し、現在非活性であることを反映。Lexicon定義は公開維持。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

</details>

### feature_change — Reading Loom

旧素材・調整用

**入力原文（全文）**

> Reading Loom originally provided a reading journal with book records stored in each user's AT Protocol PDS using the custom org.readingloom.book Lexicon. Since 2026-09-01 the hosted service provides book search only; new journal entries and sharing are no longer available. The former reading journal Lexicon definitions remain publicly available for developers. No claim is made that book search uses that Lexicon.

**更新前（全文）**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ]
}
```

**A（現行） 反復0 — raw全文**


```json
{
  "tagline": "読書記録共有",
  "summary": "元々は独自Lexiconで読書記録をPDSに保存するサービスだったが、現在は書籍検索のみを提供し、読書メモの投稿や共有機能は停止している。",
  "features": [
    "書籍検索",
    "読書メモ投稿機能（停止）",
    "読書記録共有機能（停止）",
    "独自Lexicon定義の公開"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービス提供内容が読書メモの保存・共有から書籍検索のみに変更され、旧機能は利用不可となった。",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "読書検索サービス",
  "summary": "読書ジャーナル機能は終了し、現在は書籍検索のみを提供するサービス。旧読書メモ用Lexicon定義は公開されているが、検索機能との関連は示されていない。",
  "features": [
    "書籍検索機能",
    "読書ジャーナル機能終了",
    "旧Lexicon定義の公開維持"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "2026-09-01以降、読書ジャーナルの新規投稿・共有機能が終了し、書籍検索機能のみが提供されている。旧Lexicon定義は公開維持されているが、検索機能には使用されていない。",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**B（候補） 反復1 — raw全文**


```json
{
  "tagline": "読書検索サービス",
  "summary": "読書ジャーナル機能を終了し、書籍検索サービスを提供する。旧読書メモ用Lexicon定義は公開されているが、検索機能では使用していない。",
  "features": [
    "書籍検索",
    "読書ジャーナル機能終了",
    "旧Lexicon定義の公開"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "読書ジャーナル機能（書籍・メモのPDS保存）が2026-09-01に終了し、書籍検索機能のみが提供されている事実を反映。旧Lexicon定義の公開状態と検索機能での非使用を明記。",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: Aはsummaryで過去／現在を区別できるが旧taglineが残る。Bはtaglineを更新する一方、旧機能の書籍・読書メモのPDS保存という技術的特徴が説明3項目から消えた。B反復0はsummaryで関係未確認とするのに変更理由ではLexicon非使用を断定。反復1ではsummaryにも非使用断定が現れる。改善と悪化が併存し、不適合。

<details><summary>2026-09-15の旧構成・参考出力2反復（今回のAとは区別）</summary>

反復0


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のATProtocolPDSに保存する。2026年9月以降は書籍検索のみを提供し、日記機能は終了。",
  "features": [
    "書籍検索",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存（旧機能）"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービス提供内容の変更（日記機能終了、検索機能のみ）とLexiconの公開状態の事実修正",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

反復1


```json
{
  "tagline": "読書記録共有",
  "summary": "元は独自Lexiconで読書記録をPDSに保存するサービスだったが、現在は書籍検索のみを提供。読書記録機能は終了し、Lexicon定義は開発者に公開されている。",
  "features": [
    "書籍検索",
    "独自Lexicon定義の公開"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービス提供内容が読書記録の共有から書籍検索のみに変更され、記録機能は終了した。",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

</details>

### original_error — Reading Loom

旧素材・調整用

**入力原文（全文）**

> Reading Loom is a set of Lexicon definitions for representing books and reading notes in AT Protocol PDS records. Correction from the maintainers: Reading Loom has never operated a hosted reading journal service and has never implemented sharing, search, or an end-user application. Descriptions of it as a reading-sharing service were incorrect from the start. Developers may use these definitions in their own applications.

**更新前（全文）**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ]
}
```

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "読書メモ用スキーマ定義",
  "summary": "書籍および読書メモのデータ構造を定義するLexicon定義セット。ホストされたサービスやエンドユーザー向けアプリケーションは提供せず、開発者が自身のアプリケーションで利用することを目的としている。",
  "features": [
    "書籍データスキーマ定義",
    "読書メモデータスキーマ定義",
    "PDSレコード用AT Protocolスキーマ"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "既存の「読書記録共有サービス」という記述は、維持者からの修正により「ホストサービスや共有機能は存在せず、スキーマ定義のみ」であることが判明したため、誤った記述を削除し、実際の性質（スキーマ提供）に修正した。",
    "sources": [
      "2e283591ea2e016fd797"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: Bの文面とcorrectionはこの素材で適合。元の誤記を過去の実績として残さず、書籍・読書メモの定義とPDS record用途を保持。今回の現行A出力はなく、9月15日の2出力も適合だったため改善効果とは数えない。

<details><summary>2026-09-15の旧構成・参考出力2反復（今回のAとは区別）</summary>

反復0


```json
{
  "tagline": "読書メモ用Lexicon定義",
  "summary": "書籍および読書メモのデータをATProtocolPDSレコードで表現するためのLexicon定義セット。ホストされたエンドユーザーサービスや共有機能は提供せず、開発者が自身のアプリケーションでこれらの定義を使用することを目的としている。",
  "features": [
    "書籍データのLexicon定義",
    "読書メモのLexicon定義",
    "PDSレコード保存形式の提供"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "元のプロファイルは「読書記録を共有するサービス」と誤記していたが、公式修正によりホスティングサービスや共有機能は存在せず、Lexicon定義の提供のみが実体であることが確定した。",
    "sources": [
      "2e283591ea2e016fd797"
    ]
  }
}
```

反復1


```json
{
  "tagline": "読書メモ用Lexicon",
  "summary": "書籍および読書メモのデータをATProtocolPDSレコードで表現するためのLexicon定義セット。ホストされたエンドユーザーサービスや共有機能は提供していない。",
  "features": [
    "書籍情報のLexicon定義",
    "読書メモのLexicon定義",
    "PDSレコード形式の仕様提供"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "既存の概要は「読書記録を共有するサービス」と誤記していたが、公式修正により「ホストサービスや共有機能は存在せず、開発者が利用するLexicon定義のみ」であることが確認された。",
    "sources": [
      "2e283591ea2e016fd797"
    ]
  }
}
```

</details>

### temporary_failure — Reading Loom

旧素材・調整用

**入力原文（全文）**

> 503 Service Unavailable. Temporary upstream timeout.

**更新前（全文）**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ]
}
```

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録の共有",
    "独自Lexiconによる書籍・読書メモの表現",
    "利用者のPDSへのデータ保存"
  ],
  "description_change": {
    "kind": "initial",
    "reason": "既存のプロファイルが空のため、利用可能な証拠から初期化。",
    "sources": []
  }
}
```

**automatic保存後（全文）**


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ]
}
```

保護対象では更新前を維持。更新案は作られなかった。

**Agent評価**: Bは非空current_profileを受け取っているのにinitialとし、空だったとの誤った理由を返した。featuresも言い換えている。既存の適用規則が変更を拒否し、保存後の説明は維持。9月15日も種別誤りが1/2反復で存在した。今回の現行A出力はなく悪化率は比較不能。

<details><summary>2026-09-15の旧構成・参考出力2反復（今回のAとは区別）</summary>

反復0


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ],
  "description_change": {
    "kind": "initial",
    "reason": "初期プロファイルの情報を保持。証拠が不足しているため変更なし。",
    "sources": []
  }
}
```

反復1


```json
{
  "tagline": "読書記録共有",
  "summary": "読書記録を共有するサービス。独自Lexiconで書籍と読書メモを表し、利用者のAT Protocol PDSに保存する。",
  "features": [
    "読書記録共有",
    "独自Lexiconによる書籍・読書メモ表現",
    "利用者のPDSへの保存"
  ],
  "description_change": {
    "kind": "insufficient_evidence",
    "reason": "説明ソースが存在しないため、既存のプロファイルを更新する根拠がありません。",
    "sources": []
  }
}
```

</details>

### partial_closure — Map Weave

未使用確認用

**入力原文（全文）**

> Map Weave運営からのお知らせ（2026-09-20）。イベントの新規登録と共有は9月18日に終了しました。従来のイベント機能は独自Lexiconでイベントを表現し、利用者のPDSへ保存していました。散策ルートの閲覧は引き続き利用できます。ルートは運営サーバーの地図データから表示します。イベントLexiconの定義ファイルは開発者向けに引き続き公開しています。

**更新前（全文）**


```json
{
  "tagline": "散策ルート・イベント共有",
  "summary": "散策ルートの閲覧とイベント共有を提供する地図サービス。イベントを独自Lexiconで表し、利用者のPDSに保存する。",
  "features": [
    "散策ルート閲覧",
    "イベント共有",
    "独自LexiconによるイベントのPDS保存"
  ]
}
```

**出力を見る前の期待**: changed_facts: ルート閲覧は現行、イベント共有・PDS保存は旧機能。ルートにPDS保存を転用しない。旧技術と定義公開を保持。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "散策ルート閲覧",
  "summary": "散策ルートの閲覧を提供する地図サービス。イベント共有機能は終了し、独自LexiconによるPDS保存も停止。ルート表示は運営サーバーの地図データを使用。",
  "features": [
    "散策ルートの閲覧",
    "運営サーバーの地図データ表示"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "運営告知により、イベントの新規登録・共有および独自Lexiconを用いたPDS保存機能が2026年9月18日に終了した。散策ルートの閲覧機能は継続している。",
    "sources": [
      "0d2f246360ed32d41818"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: Bは現行のルート閲覧と旧イベント機能を区別し、ルートをPDS保存とはしなかった。ただし公開を継続しているイベントLexicon定義が全3項目から消え、featuresには過去の技術的特徴もない。全終了にはしていないが、情報保持が不適合。Aとの直接比較・反復は未実施。

### feature_addition — Audio Pin

未使用確認用

**入力原文（全文）**

> Audio Pin release notes, 2026-09-20. Audio Pin continues to save podcast links and playback positions as bookmark records in each user's AT Protocol PDS using its custom Lexicon. This release adds local OPML import of podcast subscriptions. Imported subscriptions stay in this browser; only bookmarks are PDS records. Automatic transcription is a proposal from a community contributor for a future release, not part of this release.

**更新前（全文）**


```json
{
  "tagline": "音声ブックマーク",
  "summary": "音声番組のリンクと再生位置を保存するサービス。独自Lexiconでブックマークを表し、利用者のPDSに保存する。",
  "features": [
    "音声番組リンク保存",
    "再生位置ブックマーク",
    "独自LexiconによるPDS保存"
  ]
}
```

**出力を見る前の期待**: expansion/changed_facts: 従来のブックマークとPDS保存を維持し、ブラウザ内だけのOPML購読importを追加。購読をPDS保存や文字起こし提供としない。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "音声ブックマーク",
  "summary": "音声番組のリンクと再生位置を保存するサービス。独自Lexiconでブックマークを表し、利用者のPDSに保存する。OPMLインポートはローカル処理のみで、PDSへの保存はブックマークに限定される。",
  "features": [
    "音声番組リンクの保存",
    "再生位置のブックマーク",
    "独自LexiconによるPDS保存",
    "ローカルOPMLインポート"
  ],
  "description_change": {
    "kind": "expansion",
    "reason": "OPMLインポートがローカルブラウザ内処理であり、PDSへの保存対象はブックマークのみであることを明記し、自動トランスクリプションが未実装の提案であることを補足した。",
    "sources": [
      "038bef164c3fa9321e1d"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: Bの説明3項目は概ね適合。従来のブックマークとPDS保存を保持し、ローカルOPML importを追加。将来提案の文字起こしを提供済みにはしなかった。ただしimport対象が購読であることは明示せず、変更理由は本文にない文字起こしの補足を行ったと述べる。人の軽微な訂正が残る。Aとの直接比較・反復は未実施。

### maintainer_correction — Relay Lens

未使用確認用

**入力原文（全文）**

> Relay Lens maintainer correction, 2026-09-20. Relay Lens is an operator command-line utility that verifies signatures and record CIDs in downloaded AT Protocol repository CAR files. It prints an integrity report locally. Previous descriptions of Relay Lens as a hosted relay-latency dashboard were mistaken: it has never included a web dashboard, relay latency measurement, or network availability monitoring. Signature verification is implemented and remains its central function.

**更新前（全文）**


```json
{
  "tagline": "Relay監視ダッシュボード",
  "summary": "Relayの遅延を可視化するWebダッシュボード。署名付きリポジトリを検証してネットワークの稼働状況を監視する。",
  "features": [
    "Relay遅延監視",
    "Webダッシュボード",
    "署名付きリポジトリ検証"
  ]
}
```

**出力を見る前の期待**: correction: operator CLI、署名・record CID検証とlocal report。監視dashboardを過去の実績として残さず、実装済み検証を維持。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "署名検証CLI",
  "summary": "ダウンロードしたATプロトコルリポジトリCARファイルの署名とレコードCIDを検証し、ローカルで整合性レポートを出力するオペレーター用コマンドラインユーティリティ。",
  "features": [
    "署名付きリポジトリ検証",
    "CARファイルの整合性レポート出力"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "既存のWebダッシュボードやRelay遅延監視という記述は誤りであり、実態は署名検証CLIであるため、誤った主張を削除して正しい機能のみを記述した。",
    "sources": [
      "a77a3ffef88a10b051f5"
    ]
  }
}
```

automatic保存後は上記rawの3項目と同一。

保護対象では更新前を維持。同じ更新案を保持。

**Agent評価**: Bはこの素材で適合。署名・record CID検証のCLIとlocal reportへ訂正し、誤ったdashboardと監視を過去の実績として残さない。Aとの直接比較・反復は未実施。

### fetch_outage — Pocket Garden

未使用確認用

**入力原文（全文）**

> 504 Gateway Timeout. The upstream server did not respond in time.

**更新前（全文）**


```json
{
  "tagline": "栽培記録共有",
  "summary": "植物の成長記録を共有するサービス。独自Lexiconで観察日と写真を表し、利用者のPDSに保存する。",
  "features": [
    "植物成長記録",
    "観察日・写真の独自Lexicon",
    "利用者のPDS保存"
  ]
}
```

**出力を見る前の期待**: insufficient_evidence: 説明3項目をそのまま維持。終了・非公開・機能喪失を導かない。

**B（候補） 反復0 — raw全文**


```json
{
  "tagline": "栽培記録",
  "summary": "植物の成長記録を共有するサービス。独自Lexiconで観察日と写真を表し、利用者のPDSに保存する。現在はサービスが利用不可。",
  "features": [
    "植物成長記録",
    "観察日・写真の独自Lexicon",
    "利用者のPDS保存"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "サービスURLが504エラーで応答せず、稼働していないことを反映してステータスをinactiveとし、taglineを暫定から確定へ修正。",
    "sources": [
      "http_504"
    ]
  }
}
```

**automatic保存後（全文）**


```json
{
  "tagline": "栽培記録共有",
  "summary": "植物の成長記録を共有するサービス。独自Lexiconで観察日と写真を表し、利用者のPDSに保存する。",
  "features": [
    "植物成長記録",
    "観察日・写真の独自Lexicon",
    "利用者のPDS保存"
  ]
}
```

保護対象では更新前を維持。更新案は作られなかった。

**Agent評価**: Bは一時504から『現在はサービスが利用不可』をsummaryに追記し、correctionと架空の根拠ID http_504を返した。出力は不適合。description_sourcesは空で、既存の適用検査が拒否し、保存後の説明3項目は維持。Aとの直接比較・反復は未実施。

## 評価時に提示した判断案（現状はREADME参照）

1. 今回の候補は不採用とし、通常promptは開始時点へ戻すことを推奨します。品質目標は未達のまま残し、改善完了とはしません。

2. 再評価に必要な固定入力、両prompt全文、suffix、12出力・適用結果、実行／再検証スクリプト、レビュー・計測を保持し、試験DBは削除します。母艦の旧素材は変更・削除しません。保持先の恒久化はレビューで決めます。

3. 再開するなら、同じmodelで説明更新だけを扱う入力／出力契約と、現在の多項目triageを比較する小規模評価を次候補にします。説明専用化が有効かは未検証で、追加生成・費用枠は新たに決める必要があります。注意書きの追加継続や自動採用保護の緩和は推奨しません。
