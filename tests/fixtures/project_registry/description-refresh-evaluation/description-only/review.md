# 説明専用化の追加比較

説明の更新を分類等から切り離せば、古い説明や誤断定を減らせるかを試した2026-09-23の記録です。当時は、時制・tagline・一時障害の種別の改善に対し、旧技術の説明欠落が終了2反復と機能変更で残ることを理由に品質目標未達と評価しました。追加試行を打ち切り、通常prompt/versionは開始HEADへ戻しました。以下は当時の評価であり、欠落が紹介準備に及ぼす影響を踏まえた再評価と現在の採否は[比較資料README](../README.md)を参照してください。

同じQwen、Responses、temperature 0.2、reasoning要求none、2048 token／180秒。前回候補の説明更新規則をそのまま使い、入力をname/current_profile/description_sources、出力を説明3項目とdescription_changeへ限定。分類用suffixは付けません。複数要因が同時に変わるため、タスク分割だけの因果効果とはしません。

実験専用のprompt／入力選択→通常LLM transport→通常の説明field正規化とeditorial.apply_proposal→通常save/fetchです。通常triage構築全体やapply_llm_output、enrich全体へ接続した結果ではありません。部分payloadを通常triage全体の出力として扱うと他fieldへ影響するため、既存の説明適用関数を使いました。

既知素材3件＋終了1反復、新規未使用2件、計6生成。promptは固定し追加調整なし。5保護条件×6出力の30条件を保存・読戻しし、別一時DBでも通信なしに再現しました。


```json
{
  "generations": 6,
  "seconds": 22.414,
  "input_tokens": 6333,
  "output_tokens": 1106
}
```

単独生成時間は2.421〜5.999秒。終了初回の入力は前回現行3761 tokenに対し1084 tokenです。出力短縮には必要情報の欠落も含まれます。通常triageを残して専用呼出しを加える場合の総費用・速度は未測定です。sandbox権限によるtransport失敗1回は実生成6回と別記録です。

## closure

既知素材

**入力原文全文**

> Reading Loom was a book reading journal service. Users stored book records and reading notes in their own AT Protocol PDS using the custom org.readingloom.book Lexicon. Official closure announcement: the hosted reading journal service permanently ended on 2026-08-31. The Lexicon definitions remain publicly available for developers. This announcement does not describe any replacement hosted service.

**更新前全文**


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

**反復0・生出力全文**


```json
{
  "tagline": "読書メモLexicon公開",
  "summary": "読書メモのデータ形式（Lexicon）を公開するプロジェクト。ホストされた読書記録サービスは2026年8月31日に終了した。",
  "features": [
    "読書メモ用Lexiconの公開",
    "ホスト読書サービスの終了"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "ホストされた読書記録サービスが2026年8月31日に終了し、現在はLexicon定義のみが公開されているため、サービス終了とLexicon公開へ役割を変更した。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

automatic保存後は上記3項目と同一。

**反復1・生出力全文**


```json
{
  "tagline": "読書メモLexicon提供",
  "summary": "読書記録共有サービスは2026年8月31日に終了した。開発者は公開されたorg.readingloom.book Lexicon定義を利用できる。",
  "features": [
    "読書記録共有サービス終了",
    "org.readingloom.book Lexicon定義の公開"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "サービス提供が終了し、Lexicon定義の公開のみとなったため、役割と機能リストを更新した。",
    "sources": [
      "549a091b2607558ee63c"
    ]
  }
}
```

automatic保存後は上記3項目と同一。

**評価**: 2反復とも時制とtaglineは改善し、代替サービス不存在の直接断定も消えた。ただし、書籍・読書メモを独自Lexiconで表して利用者自身のPDSに保存したという旧技術の説明が消えた。単なる定義公開へ縮小しており不適合。変更理由の「定義のみ」も根拠の限定を超えた言い方が残る。

保護4条件の保存後は更新前と同一。

## feature_change

既知素材

**入力原文全文**

> Reading Loom originally provided a reading journal with book records stored in each user's AT Protocol PDS using the custom org.readingloom.book Lexicon. Since 2026-09-01 the hosted service provides book search only; new journal entries and sharing are no longer available. The former reading journal Lexicon definitions remain publicly available for developers. No claim is made that book search uses that Lexicon.

**更新前全文**


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

**反復0・生出力全文**


```json
{
  "tagline": "書籍検索サービス",
  "summary": "書籍を検索するサービス。2026年9月1日以降、読書メモの作成と共有機能は終了し、書籍検索のみを提供する。旧読書記録用Lexicon定義は公開されている。",
  "features": [
    "書籍検索",
    "読書メモ作成機能の終了",
    "読書メモ共有機能の終了",
    "旧Lexicon定義の公開"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "読書メモの作成・共有機能から書籍検索機能へサービス内容が変更された。",
    "sources": [
      "376a2222ad4ac127d137"
    ]
  }
}
```

automatic保存後は上記3項目と同一。

**評価**: tagline・現在検索／旧記録機能の区別は改善し、検索でのLexicon非使用も断定していない。ただし旧機能のPDS保存が説明3項目から落ち、情報保持が不適合。

保護4条件の保存後は更新前と同一。

## temporary_failure

既知素材

**入力原文全文**

> 503 Service Unavailable. Temporary upstream timeout.

**更新前全文**


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

**反復0・生出力全文**


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
    "reason": "更新用の説明ソース（description_sources）が空のため、既存の記述を変更する根拠がありません。既存のtagline、summary、featuresを維持します。",
    "sources": []
  }
}
```

automatic保存後は上記3項目と同一。

**評価**: 既存文面3項目を維持し、insufficient_evidenceと正しい理由・空sourcesを返した。この素材では適合。

保護4条件の保存後は更新前と同一。

## workshop_transition

未使用確認用

**入力原文全文**

> Workshop Atlas release announcement, 2026-09-22. Room booking ended on September 10. The former booking function represented reservations with a custom Lexicon and stored them in each user's AT Protocol PDS. Those schema definitions remain public. The tool catalogue continues; its entries come from our server database. Today we added CSV export of the catalogue, generated locally in the browser. This announcement does not discuss a successor booking service.

**更新前全文**


```json
{
  "tagline": "工作室予約・工具案内",
  "summary": "工作室の予約と工具カタログを提供するサービス。予約を独自Lexiconで表現し、利用者のPDSに保存する。",
  "features": [
    "工作室予約",
    "工具カタログ",
    "独自Lexiconによる予約のPDS保存"
  ]
}
```

**事前の期待**: 現行工具カタログ＋新CSV export、予約終了、旧PDS設計と現行定義公開を保持。catalogue/CSVをPDS保存とせず、後継不存在を断定しない。

**反復0・生出力全文**


```json
{
  "tagline": "工具カタログとCSVエクスポート",
  "summary": "工作室の工具カタログを提供するサービス。予約機能は終了したが、旧機能のLexiconとPDS保存のスキーマ定義は公開されたまま。現在はサーバーデータベースからカタログ情報を取得し、ブラウザ内でCSVエクスポート機能を追加した。",
  "features": [
    "工具カタログ",
    "ブラウザ内CSVエクスポート",
    "旧予約機能のLexiconとPDSスキーマ定義の公開維持"
  ],
  "description_change": {
    "kind": "changed_facts",
    "reason": "予約機能が終了し、代わりにブラウザ内でのCSVエクスポート機能が追加されたため。",
    "sources": [
      "bf2a5a4c2fef04514c4a"
    ]
  }
}
```

automatic保存後は上記3項目と同一。

**評価**: 予約終了・現行catalogue・新CSVと公開定義を保持し、PDSを新機能へ転用せず後継不存在も断定しなかった。旧設計を「LexiconとPDS保存のスキーマ定義」と曖昧に縮め、利用者自身のPDSへ予約を保存していた実装の説明にはなっていない。変更理由の「代わりに」も根拠にない置換関係を示唆。改善はあるが要訂正。

保護4条件の保存後は更新前と同一。

## patch_correction

未使用確認用

**入力原文全文**

> Patch Courier documentation correction, 2026-09-22. Patch Courier is a reusable library for validating the signatures and record CIDs of AT Protocol repository exports. It has never distributed software patches, run a hosted service, or recorded delivery histories. The old description confused it with a different product. Applications import the library and receive validation results. A network download helper is planned; validation currently accepts files provided by the caller.

**更新前全文**


```json
{
  "tagline": "パッチ自動配信サービス",
  "summary": "ソフトウェアの修正パッチを自動配信するサービス。AT Protocolの署名付き記録で配信履歴を検証する。",
  "features": [
    "パッチ自動配信",
    "署名付き配信履歴",
    "AT Protocol記録検証"
  ]
}
```

**事前の期待**: 訂正し、過去配信サービスという捏造履歴を残さない。署名/CID検証とimportして使うlibrary用途を保持。予定downloadを実装済みとしない。

**反復0・生出力全文**


```json
{
  "tagline": "AT Protocol署名検証ライブラリ",
  "summary": "AT Protocolレポジトリエクスポートの署名とレコードCIDを検証する再利用可能なライブラリ。ホストサービスやパッチ配信は行わない。",
  "features": [
    "署名検証",
    "レコードCID検証",
    "ライブラリ利用"
  ],
  "description_change": {
    "kind": "correction",
    "reason": "旧記述の「パッチ自動配信」および「配信履歴記録」は誤りであり、実際には署名とCIDの検証のみを行うライブラリであるため、これらの主張を削除し、正確な機能に修正した。",
    "sources": [
      "224eaf4b7217cca1dd7f"
    ]
  }
}
```

**automatic保存後全文**


```json
{
  "tagline": "ATProtocol署名検証ライブラリ",
  "summary": "AT Protocolレポジトリエクスポートの署名とレコードCIDを検証する再利用可能なライブラリ。ホストサービスやパッチ配信は行わない。",
  "features": [
    "署名検証",
    "レコードCID検証",
    "ライブラリ利用"
  ]
}
```

**評価**: 誤記を過去の実績にせず、署名・CID検証libraryという用途を保持。予定downloadを提供済みとはしない。この素材では適合。

保護4条件の保存後は更新前と同一。

## 状態

候補は不採用、品質目標は未達。正本と母艦は未変更。入力・prompt・全出力・適用結果・再検証scriptはこのディレクトリへ再評価素材として保持します。ユーザーはこの結果でのタスク完了を了承しました。別modelや推論設定の追加試行は今回行いません。
