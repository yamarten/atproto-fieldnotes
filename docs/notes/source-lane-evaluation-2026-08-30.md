# 外部sourceとanonymous browser laneの評価（2026-08-30）

この文書はlane方針を決めた時点の観測と判断を残す作業記録です。現行仕様は`docs/status.md`と
`docs/developer/`以下を正本とします。

## 採択した方針

- Semble collection laneは維持する。
- Tangled repository laneは維持し、UFOs rolling windowからJetstream v2 snapshotへ取得方法を置き換える。
- anonymous browser verificationは維持し、CLI専用にせずWebのproject detailからbackground jobとして実行できるようにする。
- 評価用の`source_observations`は恒久job履歴にせず撤去する。
- 有効化予定のないTangled timeline / community search / bot adapterはconfig、parser、test、文書から撤去する。

この方針はhuman reviewで提示し、Jetstream API keyを`.envrc`の
`ATPROTO_SURVEY_JETSTREAM_API_KEY`として保持する方針を含め、ユーザー承認を得た。

## 確認済みの観測

### Semble

保存済み4回のexecute observationでは、合計43 projectを作成し13件を既存projectへ一致させた。
現在41 projectがSemble作成由来で、うち32件は評価時点でSembleだけをdiscovery sourceとしていた。
保存済みprojectのsampleでは、Semble固有projectからservice surface確認済みのpromotion候補も確認できた。
4回の処理時間は19、168、56、93秒で、source failureは記録されていなかった。

### Tangled / UFOs / Jetstream v2

UFOsを使った保存済み4回の観測は、各回42 recordのrolling windowから合計11件をunmatchedとして返し、
既存一致・作成は0件だった。各回38〜41件を現行relevance filterで除外し、保存済みwatermarkへ到達せず
`gap_possible=true`だった。また旧実装はowner DIDまたはrecord websiteだけで既存projectを照合し、計算済みの
Tangled repo URLとのexact matchを行っていなかった。このため0件一致はlane価値の根拠にならない。

同一の約21時間を比較すると、UFOsの42 recordはすべてJetstream v2に含まれた。一方、Jetstreamは
create 167、update 14、delete 10の191 eventを返し、record単位で畳み込んだ現存keyは177だった。
UFOsの同一窓カバー率は42 / 177、約23.7%である。現行relevance filterの通過数はUFOs 2、
Jetstream 8だった。Jetstream固有候補には`grain`、`grain-ios`、`tranquil-pds`等が含まれ、
既存DBとのDID・website・repo URL一致も確認できた。

API keyを使った`sh.tangled.repo`のsnapshot planはHTTP 200で、評価時点のsealed tipは
`25324347113`、planner表示は7,637 entriesだった。これはcreate/update/deleteを含むarchive entry数であり、
現存repository数ではない。公式SDKを使った限定sequence・最大1 eventのsmoke testでplan、download、decodeを確認した。

### anonymous browser verification

固定した5 sampleでは、JuntosとWhiteWindのservice statusが肯定的なbrowser surfaceにより変わり、
Echoskyはstatusを変えずconfidenceが強化された。Moatとgmstnは肯定的surfaceなしとして据え置かれた。
5件中2件でstatusが変わり、1件でconfidenceが強化されたため、静的HTTP確認に対する固有成果がある。

評価時点ではCLIの`verify-service --lane browser`と対話CLIからだけ利用でき、Web UI、通常enrich、定期jobには
接続されていなかった。日常導線がWebであるため、CLI専用のまま維持する案は採らなかった。

## 判断と不確実性

Sembleは固有project作成と既存一致の両方があり、実行時間とfailureも許容範囲なので維持する。

Tangledの少ない観測数はsource母集団ではなくUFOs rolling windowの取りこぼしを表していた。Jetstream v2は
archiveをsequence watermarkで再開でき、週次jobの36時間超の停止にも対応できるため置換する。初回のfull snapshotは
API利用量と時間が未確定である。通常runはevent上限を持ち、途中終了時は最後に処理したsequenceから再開する。
既存の旧UFOs watermarkは新しいscopeでは参照しない。

browser verificationは値があるが、認証入力、form送信、登録、投稿を行わない匿名確認に限定する。
Web APIは任意URLを受けず、選択projectに保存済みのconfirmed/candidate service URLだけを許可する。
