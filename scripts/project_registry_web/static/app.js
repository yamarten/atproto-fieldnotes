// Display labels never change API values, stored data, quoted text, or JSON.
const displayLabels = {
  "sync_state_missing": "取得状態の記録なし",
  "sync_state_invalid": "取得状態の記録が不正",
  "sync_not_completed": "取得未完了",
  "sync_completion_time_invalid": "取得完了時刻が不正",
  "observation_precedes_sync": "観測時刻が取得完了より前",
  "sync_too_old": "取得結果が古い",
  "post_sync": "取得完了後",
  "pre_evaluation_baseline": "比較開始点より前",
  "parked_domain_detected": "ドメイン販売・駐車ページを検出",
  "not_found": "対象なし",
  "server_error": "サーバーエラー",
  "login_surface_detected": "ログイン画面を検出",
  "invite_text_detected": "招待制の記述あり",
  "beta_text_detected": "ベータ版の記述あり",
  "coming_soon_detected": "公開予定の記述あり",
  "docs_only_surface_detected": "ドキュメントのみの画面",
  "app_shell_detected": "アプリ画面の構成を検出",
  "mixed_launch_signals_detected": "公開状態の根拠が混在",
  "redirect_detected": "転送を検出",
  "redirect_http_to_https": "HTTPからHTTPSへ転送",
  "redirect_https_to_http": "HTTPSからHTTPへ転送",
  "redirect_same_host": "同じホストへ転送",
  "redirect_same_site_family": "同系列サイトへ転送",
  "redirect_cross_host": "別ホストへ転送",
  "verified_authority_service_surface": "管理ドメインのサービス画面を確認",
  "browser_checked": "ブラウザー確認済み",
  "rendered_surface": "描画後の利用画面",
  "service_probe": "サービス確認",
  "probe": "接続確認",
  "fetch": "取得",
  "triage": "説明・分類の評価",
  "repository_analysis": "リポジトリ解析",
  "project_repository_urls": "プロジェクトのリポジトリURL",
  "github_repo_urls": "GitHubリポジトリURL",
  "candidate_project_repository_urls": "リポジトリ候補URL",
  "service_urls": "サービスURL",
  "website_urls": "公式サイトURL",
  "recorded_lexicon_namespaces": "記録済みLexicon名前空間",
  "record_creators": "レコード作成者",
  "authority_service_families": "管理ドメインとサービスの対応",
  "domain_identity_tokens": "ドメインの識別情報",
  "merge_aliases": "マージ済みの別名",
  "namespace_lineage": "名前空間の階層の一致",
  "repository_readme": "リポジトリのREADME",
  "service_html": "サービスのHTML",
  "service_description": "サービスの説明文",
  "not_observed": "未観測",
  "repository_homepage": "リポジトリの公開先",
  "four_successful_weekly_observations": "比較可能な週次観測が4回に到達",
  "reviewed_publisher_delete_or_identity_change": "確認済み発行者の削除・識別情報の変化",
  "non_record_lexicon_usage_request": "レコード以外のLexicon利用の調査依頼",
  "candidate value is required": "候補の値を入力してください",
  "seed is required": "シードを入力してください",
  "API token is required": "APIトークンを入力してください",
  "Resource not found": "対象が見つかりません",
  "project not found": "登録プロジェクトが見つかりません",
  "job not found": "ジョブが見つかりません",
  "investigation run not found": "調査実行記録が見つかりません",
  "introduced_url must be an http(s) URL": "紹介済み投稿のURLはhttpまたはhttpsで指定してください",
  "features must contain at most 6 short strings": "特徴は短い文で6件まで指定してください",
  "schedule time must be HH:MM": "定期実行の時刻はHH:MM形式で指定してください",
  "schedule week_of_month must be between 0 and 5": "定期実行の週は毎週または第1〜第5週を指定してください",
  "schedule weekday must be between 0 and 6": "定期実行の曜日を選択してください",
  "identifier value could not be normalized": "識別子の値を確認してください。正規化できませんでした",
  "review target is stale or does not belong to this project": "レビュー対象が変更されたか、別プロジェクトに属しています。詳細を再読込してください",
  "guarded identifier cannot be added as a seed": "除外・保留中の識別子はシードへ追加できません",
  "project and other project resolve to the same project": "同じ登録プロジェクト同士は指定できません",
  "service_url is not saved on the selected project": "このサービスURLは選択したプロジェクトに保存されていません",
  "enrich checkpoint is not available": "調査の再開地点が見つかりません",
  "enrich checkpoint is already complete": "この再開地点の調査は完了しています",
  "only bulk enrich jobs can be resumed": "再開できるのは一括調査のみです",
  "server reload is already in progress": "サーバーは再起動中です",
  "invalid checkpoint run id": "再開地点の実行IDが不正です",
  "checkpoint run id mismatch": "再開地点の実行IDが一致しません",
  "Failed to fetch": "サーバーへ接続できません。接続状態を確認してください",
  "Load failed": "読込みに失敗しました。接続状態を確認してください",
  "confirm-candidate": "識別子を採用",
  "reject-candidate": "識別子を除外",
  "block-identifier": "識別子を除外",
  "defer-identifier": "識別子を30日保留",
  "split-project": "識別子を別プロジェクトへ分割",
  "clear-block-identifier": "識別子の除外を解除",
  "clear-defer-identifier": "識別子の保留を解除",
  "defer-promotion": "紹介候補を保留にする",
  "clear-promotion-deferral": "紹介候補の保留を解除",
  "dismiss-review-target": "レビュー対象から除外",
  "defer-review-target": "レビュー対象を30日保留",
  "clear-review-target": "レビュー対象の手動判断を解除",
  "clear-separate": "別プロジェクト指定を解除",
  "service_observation": "サービス観測",
  "enrich_all": "通常追跡中の全件調査",
  "sync_introduced": "紹介済み投稿の取り込み",
  "sync_sources_then_enrich": "外部情報の取り込み・新規分の調査",
  "enrich_all_states": "全追跡状態の調査・見直し",
  "lexicon_publication_weekly": "Lexicon公開状況の観測",
  "active": "通常追跡",
  "stable": "安定稼働",
  "stalled": "活動停滞",
  "out_of_scope": "調査範囲外",
  "archived": "アーカイブ",
  "ignore": "追跡対象外",
  "beta": "ベータ公開",
  "live": "利用可能",
  "dead": "提供終了",
  "private": "非公開",
  "unreleased": "未公開",
  "unverified": "未確認",
  "invite_only": "招待制",
  "unknown": "不明",
  "none": "なし",
  "core": "中核的な利用",
  "substantial": "実質的な利用",
  "optional": "任意の利用",
  "auth_only": "認証のみ",
  "high": "高い",
  "medium": "中程度",
  "low": "低い",
  "exceptional": "特に高い",
  "confirmed": "採用済み",
  "candidate": "候補",
  "derived": "導出",
  "heuristic": "推測",
  "introduced": "紹介済み",
  "candidates": "候補あり",
  "ready": "準備済み",
  "primary": "代表",
  "account": "アカウント",
  "did": "DID",
  "domain": "ドメイン",
  "handle": "ハンドル",
  "nsid": "NSID",
  "repo_url": "リポジトリURL",
  "service_url": "サービスURL",
  "site_url": "公式サイトURL",
  "project_key": "プロジェクトキー",
  "repo": "リポジトリ",
  "repository": "リポジトリ",
  "service": "サービス",
  "website": "公式サイト",
  "webApp": "Webアプリ",
  "distribution": "配布先",
  "appStore": "App Store",
  "playStore": "Google Play",
  "fdroid": "F-Droid",
  "docs": "ドキュメント",
  "support": "サポート",
  "changelog": "変更履歴",
  "status": "稼働状況",
  "privacyPolicy": "プライバシーポリシー",
  "termsOfService": "利用規約",
  "webManifest": "Webマニフェスト",
  "project": "登録プロジェクト",
  "collection": "コレクション",
  "oauth_client": "OAuthクライアント",
  "owned_nsid": "帰属NSID",
  "project_site": "公式サイト",
  "project_repository": "プロジェクトのリポジトリ",
  "source_repository_record": "リポジトリの登録情報",
  "developer_account": "開発者アカウント",
  "developer_handle": "開発者ハンドル",
  "developer_did": "開発者DID",
  "project_account": "公式アカウント",
  "project_handle": "公式ハンドル",
  "authority_domain": "管理ドメイン",
  "owned": "帰属",
  "developer": "開発者",
  "project site": "公式サイト",
  "project handle": "公式ハンドル",
  "authority": "管理ドメイン",
  "source": "情報源",
  "identity": "アカウント・ドメイン",
  "schema": "スキーマ",
  "assess_atproto_usage": "atproto利用範囲の判断",
  "atproto_usage": "atproto利用範囲",
  "confirm_identifier": "識別子の採用",
  "block_identifier": "識別子の除外",
  "defer_identifier": "識別子の保留",
  "defer_promotion": "紹介候補の保留",
  "lower_promotion_priority": "紹介候補の優先度を下げる",
  "promotion_priority": "紹介候補の優先度",
  "defer_review_target": "個別のレビュー対象を保留",
  "dismiss_review": "要レビューの種類を除外",
  "dismiss_review_target": "個別のレビュー対象を除外",
  "separate_project": "別プロジェクト指定",
  "review_kind": "要レビューの種類",
  "review_target": "レビュー対象",
  "queued": "実行待ち",
  "running": "実行中",
  "cancelling": "停止要求中",
  "completed": "完了",
  "cancelled": "中断",
  "failed": "失敗",
  "retry_wait": "再試行待ち",
  "complete": "完了",
  "partial": "一部失敗",
  "success": "成功",
  "ok": "成功",
  "problems": "問題あり",
  "not_started": "未開始",
  "observed": "観測済み",
  "observed_not_adopted": "観測済み・未採用",
  "supported": "根拠あり",
  "needs_review": "要レビュー",
  "pending": "待機中",
  "recommended": "推奨",
  "blocked": "制限あり",
  "suggested": "候補",
  "available": "利用可能",
  "unavailable": "取得できません",
  "expired": "期限切れ",
  "cleared": "解除済み",
  "deferred": "保留",
  "skipped": "スキップ",
  "waiting": "待機中",
  "enabled": "有効",
  "disabled": "無効",
  "review due": "レビュー時期",
  "monitoring": "観測中",
  "bulk": "一括",
  "operation": "処理",
  "enrich": "調査",
  "sync": "外部情報の取り込み",
  "review": "追跡状態の見直し",
  "verify_service": "匿名ブラウザー確認",
  "monitor_services": "サービス観測",
  "lexicon_publication_observation": "Lexicon公開状況の観測",
  "service_status": "サービス状態",
  "ownership": "帰属",
  "contribution": "atproto寄与度",
  "merge_candidate": "マージ候補",
  "trust": "信頼の根拠",
  "no_confirmed_owned_nsid": "採用済みの帰属NSIDなし",
  "unsupported_confirmed": "現在の根拠で支持されない採用済み識別子",
  "saved service verification state": "保存済みのサービス確認状態",
  "confirmed identifiers plus saved ownership relations": "採用済み識別子と保存済みの帰属関係",
  "rule-based atproto contribution evaluation": "規則に基づくatproto寄与度の評価",
  "current duplicate candidate and merge recommendation evaluation": "現在の重複候補とマージ推奨の評価",
  "trust-graph-v1 mutual directed trust paths for URL, collection, DID, handle, and domain identifiers": "URL・コレクション・DID・ハンドル・ドメインの相互信頼経路（trust-graph-v1）",
  "no confirmed service/repository/lexicon": "採用済みのサービス・リポジトリ・Lexiconがない",
  "service_status is still unknown": "サービス状態が不明",
  "summary missing": "説明が未登録",
  "tagline missing": "短い説明が未登録",
  "fewer than 3 features": "特徴が3件未満",
  "category missing": "分類が未登録",
  "LLM review missing": "LLMによる評価が未実施",
  "editorial_refresh": "説明の更新案あり",
  "service_observation_changed": "サービスの観測に変化あり",
  "llm_manual_review_recommended": "LLMが手動レビューを推奨",
  "service_candidate_needs_confirmation": "サービス候補の確認が必要",
  "candidate_repo_needs_confirmation": "リポジトリ候補の確認が必要",
  "service_now_usable_but_needs_editorial_review": "サービス利用可能・紹介情報の確認が必要",
  "more_evidence_than_current_classification": "現在の分類を見直す材料あり",
  "stale_metadata_with_recent_activity": "最近の活動あり・登録情報が古い",
  "service_status_unverified_but_promising": "有望だがサービス状態は未確認",
  "category_recheck_needed": "分類の再確認が必要",
  "external_lexicon_dependency_needs_review": "外部Lexiconへの依存の確認が必要",
  "high_information_density": "調査材料が豊富",
  "many_identity_candidates": "アカウント・ドメインの候補が多数",
  "service_status_unverified": "サービス状態が未確認",
  "browser_surface_not_confirmed": "ブラウザーで利用面を確認できず",
  "browser_password_form_detected": "パスワード入力欄を検出",
  "browser_identity_form_detected": "アカウント入力欄を検出",
  "browser_oauth_entry_detected": "OAuth認証の入口を検出",
  "browser_public_app_surface_detected": "公開されたアプリ画面を検出",
  "browser_rendered_surface": "ブラウザー表示を確認",
  "browser_rendered_error_detected": "ブラウザー表示でエラーを検出",
  "static": "HTML取得",
  "browser": "ブラウザー",
  "service_static": "サービスHTML取得",
  "service_browser": "サービスのブラウザー確認",
  "deep": "詳細調査",
  "html": "HTML本文",
  "readme": "README",
  "page_description": "ページ説明",
  "record": "レコード",
  "create": "作成",
  "update": "更新",
  "delete": "削除",
  "read": "読取り",
  "write": "書込み",
  "rpc": "RPC呼出し",
  "blob": "Blob",
  "include": "権限セット",
  "transition": "移行用権限",
  "atproto": "atproto",
  "rate_limited": "通信回数の制限",
  "timeout": "タイムアウト",
  "network_error": "通信エラー",
  "parse_error": "解析エラー",
  "http_error": "HTTPエラー",
  "Service observation": "サービス観測",
  "Enrich all": "通常追跡中の全件調査",
  "Sync introduced": "紹介済み投稿の取り込み",
  "Sync sources + enrich": "外部情報の取り込み・新規分の調査",
  "All-state maintenance": "全追跡状態の調査・見直し",
  "Lexicon publication observation": "Lexicon公開状況の観測",
  "Observe up to 20 confirmed service URLs last checked at least 30 days ago, including stable/stalled/out-of-scope projects. No automatic tracking changes.": "最終確認から30日以上経過した採用済みサービスURLを最大20件観測します。安定稼働・活動停滞・調査範囲外も対象です。追跡状態は変更しません。",
  "Run normal enrich --all for active projects.": "通常追跡中のプロジェクトを一括調査します。",
  "Run introduced-post sync with SQLite updates.": "紹介済み投稿を取り込み、SQLiteを更新します。",
  "Run non-introduced sync sources, then enrich newly created projects only.": "紹介済み投稿以外の外部情報を取り込み、新規登録分だけを調査します。",
  "Run enrich --all across every tracking state, then apply stalled review updates.": "すべての追跡状態を対象に一括調査し、活動停滞・安定稼働への見直しを適用します。",
  "Update the fixed sidecar and evaluate bounded weekly review signals without changing the registry.": "専用の観測DBを更新し、週次のレビュー材料を確認します。登録プロジェクトのDBは変更しません。",
  "observe due services": "確認時期を迎えたサービスを観測",
  "enrich --all": "通常追跡中の全件調査",
  "enrich --all --all-tracking-states": "全追跡状態の調査",
  "review stalled --execute": "活動停滞・安定稼働への変更を適用",
  "sync introduced": "紹介済み投稿の取り込み",
  "sync github": "GitHubから取り込み",
  "sync collections": "コレクション一覧から取り込み",
  "sync semble": "Sembleから取り込み",
  "sync tangled": "Tangledから取り込み",
  "enrich --all --new-only": "新規登録分の調査",
  "observe Lexicon publications": "Lexicon公開状況の観測",
  "queued by scheduler": "定期実行による実行待ち",
  "queued manually": "手動操作による実行待ち",
  "resume pending steps manually": "保留中の処理を手動で再開してください",
  "scheduled preset completed": "定期実行の処理が完了",
  "waiting for active background job": "実行中のジョブの終了待ち",
  "previous scheduled worker was interrupted; retrying step": "前回の処理が中断したため、同じ処理を再試行",
  "prerequisite_incomplete": "前提となる処理が未完了",
  "github": "GitHub",
  "semble": "Semble",
  "tangled": "Tangled",
  "collections": "コレクション一覧",
  "page": "ページ",
  "item": "件",
  "strict": "厳密",
  "moderate": "標準",
  "broad": "広範",
  "defines_schema": "スキーマを定義",
  "mentions_collection": "コレクションに言及",
  "official_account": "公式アカウント",
  "operates_service": "サービスを運営",
  "redirects_to": "転送先",
  "requests_permission": "権限を要求",
  "uses_collection": "コレクションを利用",
  "owns_namespace": "名前空間を所有",
  "serves_collection": "コレクションを提供",
  "links_to_service": "サービスへリンク",
  "links_to_repository": "リポジトリへリンク",
  "links_to": "リンク先",
  "same_as": "同一対象",
  "publishes_lexicon": "Lexiconを公開",
  "malformed_schema_observation": "形式不正のスキーマ観測",
  "known_schema_overlap": "既知のスキーマとの一致",
  "existing_project_unique_candidate": "既存プロジェクトへの一意の帰属候補",
  "unattributed_schema_catalog_observation": "帰属未確定のスキーマ観測"
};
function displayLabel(value) {
  const text = String(value ?? "");
  if (Object.hasOwn(displayLabels, text)) return displayLabels[text];
  // Known machine-generated evidence prefixes; leave the identifier suffix intact.
  if (/^http_\d{3}$/.test(text)) return `HTTP ${text.slice(5)}`;
  const colon = text.indexOf(":");
  if (colon > 0 && Object.hasOwn(displayLabels, text.slice(0, colon))) {
    return `${displayLabels[text.slice(0, colon)]}: ${text.slice(colon + 1)}`;
  }
  return text;
}
function scheduleMessage(value) {
  const text = String(value || "");
  if (text.startsWith("running ")) return `実行中: ${displayLabel(text.slice(8))}`;
  const stopped = text.match(/^scheduled step ([^:]+): (.*); resume pending steps manually$/);
  if (stopped) return `${displayLabel(stopped[1])}: ${displayLabel(stopped[2])}。保留中の処理を手動で再開してください。`;
  if (text.startsWith("scheduled step could not start:")) return `処理を開始できませんでした。保留中の処理を手動で再開してください。（技術詳細: ${text.slice(31).replace(/; resume pending steps manually$/, "")}）`;
  return displayLabel(text);
}
const $ = (id) => document.getElementById(id);
// Security boundary for HTML templates: every external string must pass through
// esc(), link(), pill(), or json() before it reaches innerHTML/outerHTML.
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const list = (value) => Array.isArray(value) ? value : [];
const resetDetailScroll = () => { if ($("detail")) $("detail").scrollTop = 0; window.scrollTo({top:0, left:0}); };
const backToListButton = () => `<button class="mobile-back secondary" data-detail-back>一覧へ戻る</button>`;
function contextualBackButton() {
  if (!state.detailBackTarget) return "";
  const label = state.detailBackTarget.type === "merge" ? "マージ比較へ戻る" : "プロジェクト詳細へ戻る";
  return `<button class="context-back secondary" data-detail-return>${esc(label)}</button>`;
}
function bindBackToListButtons() {
  document.querySelectorAll("[data-detail-back]").forEach((button) => {
    button.onclick = () => { document.body.classList.remove("detail-open"); state.selected = ""; syncUrl(); };
  });
  document.querySelectorAll("[data-detail-return]").forEach((button) => {
    button.onclick = () => {
      const target = state.detailBackTarget;
      if (!target) return;
      if (target.type === "merge") loadMergeDetail(target.source, target.target, {backTarget: target.previous || null});
      if (target.type === "project") loadDetail(target.key);
    };
  });
}
const settingsKey = "projectRegistryWebV2Settings";
const defaults = { reviewLimit: 50, projectLimit: 50, mergeLimit: 50, mergeMode: "moderate", includePersonalTools: false, timeZone: "Asia/Tokyo" };
const filterOptionFallbacks = {
  tracking_states: ["active", "archived", "ignore", "out_of_scope", "stable", "stalled"],
  service_statuses: ["beta", "dead", "invite_only", "live", "none", "private", "unknown", "unreleased", "unverified"],
  contribution_levels: ["auth_only", "core", "optional", "substantial", "unknown"],
  identifier_kinds: ["account", "did", "domain", "handle", "nsid", "repo_url", "service_url", "site_url"],
  identifier_classifications: ["candidate", "confirmed", "derived", "heuristic"],
  relation_predicates: ["defines_schema", "mentions_collection", "official_account", "operates_service", "redirects_to", "requests_permission", "project_repository", "uses_collection"],
  relation_subject_types: ["account", "did", "domain", "handle", "nsid", "oauth_client", "project", "repo", "service", "website"],
  relation_object_types: ["account", "did", "domain", "handle", "nsid", "oauth_client", "project", "repo", "service", "website"],
  decision_kinds: ["block_identifier", "confirm_identifier", "defer_identifier", "defer_promotion", "defer_review_target", "dismiss_review", "dismiss_review_target", "lower_promotion_priority", "separate_project"],
  decision_target_types: ["account", "did", "domain", "handle", "nsid", "project_key", "promotion_priority", "repo_url", "review_kind", "review_target", "service_url"],
};
const decisionKindOptionLabels = {
  lower_promotion_priority: "紹介候補の優先度を下げる",
};
const decisionTargetOptionLabels = {
  promotion_priority: "紹介候補の優先度",
};
const state = {
  screen: "review",
  reviewView: "queue",
  reviewSignalKind: "",
  promotionView: "inbox",
  promotionReason: "",
  promotionOffset: 0,
  selected: "",
  detailTab: "overview",
  detailBackTarget: null,
  detailRevision: "",
  mergeSourceRevision: "",
  mergeTargetRevision: "",
  openJobDetails: new Set(),
  jobDetailCache: new Map(),
  jobsById: new Map(),
  listRequest: 0,
  detailRequest: 0,
  mergeAnalysisRequest: 0,
  operationTimer: null,
  settings: loadSettings(),
  server: null,
  projectSearch: {
    query: "",
    tracking: "",
    service: "",
    contribution: "",
    notes: "any",
    introduced: "any",
    candidates: "any",
    errors: "any",
    sort: "last_seen_desc",
    identifierKind: "", identifierValue: "", identifierClassification: "",
    relationPredicate: "", relationSubjectType: "", relationSubjectValue: "",
    relationObjectType: "", relationObjectValue: "", relationSource: "",
    decisionKind: "", decisionTargetType: "", decisionTargetValue: "", decisionStatus: "",
  },
};
let mutationQueue = Promise.resolve();

const params = new URLSearchParams(location.search);
if (params.get("token")) {
  localStorage.setItem("projectRegistryToken", params.get("token"));
  params.delete("token");
  history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : ""));
}
restoreStateFromUrl(params);
let token = localStorage.getItem("projectRegistryToken") || prompt("Project RegistryのAPIトークン") || "";
if (token) localStorage.setItem("projectRegistryToken", token);

function loadSettings() {
  try {
    const loaded = {...defaults, ...JSON.parse(localStorage.getItem(settingsKey) || "{}")};
    return {...loaded, timeZone: normalizeTimeZone(loaded.timeZone) || defaults.timeZone};
  }
  catch { return {...defaults}; }
}
function saveSettings() { localStorage.setItem(settingsKey, JSON.stringify(state.settings)); }
function status(message, error = false) { $("status").textContent = message || ""; $("status").style.color = error ? "var(--danger)" : ""; }
function safeUrl(value) { try { const url = new URL(String(value || "")); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; } catch { return ""; } }
function link(value) { const href = safeUrl(value); return href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(value)}</a>` : esc(value); }
function externalLink(href, label) { const safe = safeUrl(href); return safe ? `<a href="${esc(safe)}" target="_blank" rel="noreferrer">${esc(label)}</a>` : esc(label); }
function pill(value, kind = "") { return value ? `<span class="pill ${esc(kind)}">${esc(displayLabel(value))}</span>` : ""; }
function json(value) { return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`; }
function parseTimeZoneOffset(value) {
  const match = String(value || "").trim().match(/^(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?$/i);
  if (!match) return null;
  const hours = Number(match[2]);
  const minutes = Number(match[3] || 0);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes) || hours > 14 || minutes > 59) return null;
  const total = hours * 60 + minutes;
  if (total > 14 * 60) return null;
  return (match[1] === "-" ? -1 : 1) * total;
}
function offsetLabel(minutes) {
  const sign = minutes < 0 ? "-" : "+";
  const absolute = Math.abs(minutes);
  return `${sign}${String(Math.floor(absolute / 60)).padStart(2, "0")}:${String(absolute % 60).padStart(2, "0")}`;
}
function normalizeTimeZone(value) {
  const raw = String(value || "").trim();
  if (!raw) return "";
  const offset = parseTimeZoneOffset(raw);
  if (offset !== null) return offsetLabel(offset);
  try {
    new Intl.DateTimeFormat("en-US", {timeZone: raw}).format(new Date(0));
    return raw;
  } catch {
    return "";
  }
}
function formatTimestamp(value) {
  if (!value) return "";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return String(value);
  const timeZone = state.settings.timeZone || defaults.timeZone;
  const offset = parseTimeZoneOffset(timeZone);
  if (offset !== null) {
    const shifted = new Date(date.getTime() + offset * 60000);
    const pad = (number) => String(number).padStart(2, "0");
    return `${shifted.getUTCFullYear()}-${pad(shifted.getUTCMonth() + 1)}-${pad(shifted.getUTCDate())} ${pad(shifted.getUTCHours())}:${pad(shifted.getUTCMinutes())}:${pad(shifted.getUTCSeconds())} ${offsetLabel(offset)}`;
  }
  try {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", {
      timeZone,
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23", timeZoneName: "short",
    }).formatToParts(date).map((part) => [part.type, part.value]));
    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}${parts.timeZoneName ? ` ${parts.timeZoneName}` : ""}`;
  } catch {
    return date.toISOString();
  }
}
function showDialogWithoutInputFocus(dialog) {
  dialog.showModal();
  requestAnimationFrame(() => dialog.focus({preventScroll: true}));
}
function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(Number(seconds))) return "";
  const value = Math.max(0, Math.round(Number(seconds)));
  if (value < 60) return `${value}秒`;
  if (value < 3600) return `${Math.floor(value / 60)}分${value % 60}秒`;
  return `${Math.floor(value / 3600)}時間${Math.floor((value % 3600) / 60)}分`;
}

function formatMilliseconds(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(Number(milliseconds))) return "";
  const value = Math.max(0, Math.round(Number(milliseconds)));
  return value < 1000 ? `${value}ミリ秒` : formatDuration(value / 1000);
}

function apiErrorMessage(error = {}) {
  const message = String(error.message || "");
  const known = displayLabel(message);
  if (known !== message) return known;
  const prefixes = {
    "project not found:": "登録プロジェクトが見つかりません:",
    "unknown schedule timezone:": "定期実行のタイムゾーンが不正です:",
    "background job already active:": "別のジョブが実行中です:",
    "invalid tracking_state:": "追跡状態が不正です:",
  };
  for (const [prefix, label] of Object.entries(prefixes)) {
    if (message.startsWith(prefix)) return `${label} ${message.slice(prefix.length).trim()}`;
  }
  const codes = {
    unauthorized: "APIトークンを確認してください。",
    not_found: "対象が見つかりません。",
    validation_error: "入力内容を確認してください。",
    job_conflict: "別の処理と競合しました。実行中の処理を確認してください。",
    stale_project: "別の操作でプロジェクトが変更されました。詳細を再読込してから再操作してください。",
    database_busy: "データベースが使用中です。しばらく待ってから再操作してください。",
    database_error: "データベースの処理に失敗しました。",
    internal_error: "サーバーの処理に失敗しました。",
  };
  // Unrecognized diagnostics remain available verbatim; never guess their meaning.
  return `${codes[error.code] || "処理に失敗しました。"}${message ? `（技術詳細: ${message}）` : ""}`;
}

async function api(path, options = {}) {
  const headers = {"X-Project-Registry-Token": token, ...(options.headers || {})};
  if (options.body) headers["Content-Type"] = "application/json";
  let response;
  try { response = await fetch(path, {...options, headers}); }
  catch (error) { throw new Error(apiErrorMessage(error)); }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(apiErrorMessage(payload.error || {message: response.statusText}));
    error.code = payload.error?.code || "request_failed";
    throw error;
  }
  return payload;
}

function restoreStateFromUrl(query = new URLSearchParams(location.search)) {
  const screen = query.get("screen");
  if (["review", "projects", "operations", "settings"].includes(screen)) state.screen = screen;
  const view = query.get("view");
  if (["queue", "promotion", "merge"].includes(view)) state.reviewView = view;
  state.reviewSignalKind = query.get("review_kind") || "";
  state.promotionView = query.get("promotion_view") || "inbox";
  state.promotionReason = query.get("promotion_reason") || "";
  state.promotionOffset = Math.max(0, Number(query.get("promotion_offset")) || 0);
  state.selected = query.get("project") || "";
  const tab = query.get("tab");
  if (["overview", "identifiers", "relations", "investigation", "decisions", "raw"].includes(tab)) state.detailTab = tab;
  state.projectSearch = {
    query: query.get("q") || "",
    tracking: query.get("tracking") || "",
    service: query.get("service") || "",
    contribution: query.get("contribution") || "",
    notes: query.get("notes") || "any",
    introduced: query.get("introduced") || "any",
    candidates: query.get("candidates") || "any",
    errors: query.get("errors") || "any",
    sort: query.get("sort") || "last_seen_desc",
    identifierKind: query.get("identifier_kind") || "", identifierValue: query.get("identifier_value") || "",
    identifierClassification: query.get("identifier_classification") || "",
    relationPredicate: query.get("relation_predicate") || "", relationSubjectType: query.get("relation_subject_type") || "",
    relationSubjectValue: query.get("relation_subject_value") || "", relationObjectType: query.get("relation_object_type") || "",
    relationObjectValue: query.get("relation_object_value") || "", relationSource: query.get("relation_source") || "",
    decisionKind: query.get("decision_kind") || "", decisionTargetType: query.get("decision_target_type") || "",
    decisionTargetValue: query.get("decision_target_value") || "", decisionStatus: query.get("decision_status") || "",
  };
}

function syncUrl({replace = false} = {}) {
  const query = new URLSearchParams();
  if (state.screen !== "review") query.set("screen", state.screen);
  if (state.screen === "review" && state.reviewView !== "queue") query.set("view", state.reviewView);
  if (state.screen === "review" && state.reviewView === "promotion") {
    query.set("promotion_view", state.promotionView);
    if (state.promotionReason) query.set("promotion_reason", state.promotionReason);
    if (state.promotionOffset) query.set("promotion_offset", state.promotionOffset);
  }
  if (state.screen === "review" && state.reviewView === "queue" && state.reviewSignalKind) {
    query.set("review_kind", state.reviewSignalKind);
  }
  if (state.screen === "projects") {
    const values = {
      q: state.projectSearch.query,
      tracking: state.projectSearch.tracking,
      service: state.projectSearch.service,
      contribution: state.projectSearch.contribution,
      notes: state.projectSearch.notes !== "any" ? state.projectSearch.notes : "",
      introduced: state.projectSearch.introduced !== "any" ? state.projectSearch.introduced : "",
      candidates: state.projectSearch.candidates !== "any" ? state.projectSearch.candidates : "",
      errors: state.projectSearch.errors !== "any" ? state.projectSearch.errors : "",
      sort: state.projectSearch.sort !== "last_seen_desc" ? state.projectSearch.sort : "",
      identifier_kind: state.projectSearch.identifierKind, identifier_value: state.projectSearch.identifierValue,
      identifier_classification: state.projectSearch.identifierClassification,
      relation_predicate: state.projectSearch.relationPredicate, relation_subject_type: state.projectSearch.relationSubjectType,
      relation_subject_value: state.projectSearch.relationSubjectValue, relation_object_type: state.projectSearch.relationObjectType,
      relation_object_value: state.projectSearch.relationObjectValue, relation_source: state.projectSearch.relationSource,
      decision_kind: state.projectSearch.decisionKind, decision_target_type: state.projectSearch.decisionTargetType,
      decision_target_value: state.projectSearch.decisionTargetValue, decision_status: state.projectSearch.decisionStatus,
    };
    Object.entries(values).forEach(([key, value]) => { if (value) query.set(key, value); });
  }
  if (state.selected) query.set("project", state.selected);
  if (state.selected && state.detailTab !== "overview") query.set("tab", state.detailTab);
  const url = location.pathname + (query.toString() ? `?${query}` : "");
  (replace ? history.replaceState : history.pushState).call(history, null, "", url);
}

function setScreen(screen, {updateUrl = true, preserveSelection = false} = {}) {
  if (state.operationTimer) clearTimeout(state.operationTimer);
  state.operationTimer = null;
  state.listRequest += 1;
  state.detailRequest += 1;
  state.screen = screen;
  if (!preserveSelection) state.selected = "";
  document.body.dataset.screen = screen;
  document.body.classList.remove("detail-open");
  document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => button.classList.toggle("active", button.dataset.screen === screen));
  const workspace = $("workspace");
  const single = $("single-pane");
  if (["review", "projects"].includes(screen)) {
    workspace.hidden = false; single.hidden = true; loadList().then(() => {
      if (state.selected) loadDetail(state.selected, {updateUrl: false});
    });
  } else {
    workspace.hidden = true; single.hidden = false;
    screen === "operations" ? renderOperations() : renderSettings();
  }
  if (updateUrl) syncUrl();
}

function listControls() {
  if (state.screen === "review") {
    const explicitMerge = state.reviewView === "merge"
      ? renderExplicitMergePairForm()
      : "";
    const reviewFilter = state.reviewView === "queue"
      ? `<div class="toolbar"><label>要レビューの種類<input id="review-signal-kind" placeholder="例: candidate_repo_needs_confirmation" value="${esc(state.reviewSignalKind)}"></label><button id="apply-review-filter" class="primary">適用</button><button id="clear-review-filter" class="secondary">解除</button></div>`
      : "";
    const promotionFilter = state.reviewView === "promotion" ? `<div class="toolbar">
      <label>候補の扱い<select id="promotion-view">${selectOptions([["inbox", "確認対象"], ["technical", "技術候補"], ["waiting", "保留"], ["lowered", "低優先度・特集候補"], ["all", "すべて"]], state.promotionView)}</select></label>
      <label>理由を検索<input id="promotion-reason" type="search" value="${esc(state.promotionReason)}" placeholder="例: 認証のみ、公開待ち"></label>
      <button id="apply-promotion-filter">検索</button><button id="clear-promotion-filter" class="secondary">条件を解除</button>
      </div><div id="promotion-reasons" class="actions"></div>` : "";
    return `<div class="controls"><div class="subnav">
      <button data-review-view="queue" class="${state.reviewView === "queue" ? "active" : ""}">要レビュー一覧</button>
      <button data-review-view="promotion" class="${state.reviewView === "promotion" ? "active" : ""}">紹介候補一覧</button>
      <button data-review-view="merge" class="${state.reviewView === "merge" ? "active" : ""}">マージ</button>
      <button id="refresh-review-list" class="secondary">再読込</button>
    </div>${reviewFilter}${promotionFilter}${explicitMerge}</div>`;
  }
  const relationSources = filterOptionValues("relation_sources");
  return `<div class="controls">
    <div class="toolbar"><input id="project-query" type="search" placeholder="名前・説明・NSID・アカウント・URLを検索" value="${esc(state.projectSearch.query)}"><button id="search-projects" class="primary">検索</button><button id="new-seed">シードを登録</button></div>
    <details><summary>絞り込み</summary><p class="muted">複数選択可。未選択ならすべて対象。</p><div class="filter-grid">
      <label>追跡状態<select id="filter-tracking" multiple size="5">${multiEnumOptions(filterOptionValues("tracking_states"), state.projectSearch.tracking)}</select></label>
      <label>サービス状態<select id="filter-service" multiple size="5">${multiEnumOptions(filterOptionValues("service_statuses"), state.projectSearch.service)}</select></label>
      <label>atproto寄与度<select id="filter-contribution" multiple size="5">${multiEnumOptions(filterOptionValues("contribution_levels"), state.projectSearch.contribution)}</select></label>
      <label>メモ<select id="filter-notes">${selectOptions([["any", "すべて"], ["yes", "あり"], ["no", "なし"]], state.projectSearch.notes)}</select></label>
      <label>紹介済み<select id="filter-introduced">${selectOptions([["any", "すべて"], ["yes", "あり"], ["no", "なし"]], state.projectSearch.introduced)}</select></label>
      <label>候補<select id="filter-candidates">${selectOptions([["any", "すべて"], ["yes", "あり"], ["no", "なし"]], state.projectSearch.candidates)}</select></label>
      <label>エラー<select id="filter-errors">${selectOptions([["any", "すべて"], ["yes", "あり"], ["no", "なし"]], state.projectSearch.errors)}</select></label>
      <label>並び順<select id="filter-sort">${selectOptions([["last_seen_desc", "最終観測が新しい順"], ["first_seen_desc", "登録が新しい順"], ["first_seen_asc", "登録が古い順"], ["last_enriched_asc", "調査が古い順"], ["name_asc", "名前"]], state.projectSearch.sort)}</select></label>
      <label>識別子の種類<select id="filter-identifier-kind">${enumOptions(filterOptionValues("identifier_kinds"), state.projectSearch.identifierKind)}</select></label>
      <label>識別子の値<input id="filter-identifier-value" value="${esc(state.projectSearch.identifierValue)}"></label>
      <label>識別子の判定区分<select id="filter-identifier-classification">${enumOptions(filterOptionValues("identifier_classifications"), state.projectSearch.identifierClassification)}</select></label>
      <label>関係の種類<select id="filter-relation-predicate">${enumOptions(filterOptionValues("relation_predicates"), state.projectSearch.relationPredicate)}</select></label>
      <label>関係元の種類<select id="filter-relation-subject-type">${enumOptions(filterOptionValues("relation_subject_types"), state.projectSearch.relationSubjectType)}</select></label>
      <label>関係元の値<input id="filter-relation-subject-value" value="${esc(state.projectSearch.relationSubjectValue)}"></label>
      <label>関係先の種類<select id="filter-relation-object-type">${enumOptions(filterOptionValues("relation_object_types"), state.projectSearch.relationObjectType)}</select></label>
      <label>関係先の値<input id="filter-relation-object-value" value="${esc(state.projectSearch.relationObjectValue)}"></label>
      <label>関係の情報源<input id="filter-relation-source" list="filter-relation-sources" value="${esc(state.projectSearch.relationSource)}"></label>
      <datalist id="filter-relation-sources">${relationSources.map((value) => `<option value="${esc(value)}"></option>`).join("")}</datalist>
      <label>手動判断の種類<select id="filter-decision-kind">${enumOptions(filterOptionValues("decision_kinds"), state.projectSearch.decisionKind, decisionKindOptionLabels)}</select></label>
      <label>判断対象の種類<select id="filter-decision-target-type">${enumOptions(filterOptionValues("decision_target_types"), state.projectSearch.decisionTargetType, decisionTargetOptionLabels)}</select></label>
      <label>判断対象の値<input id="filter-decision-target-value" value="${esc(state.projectSearch.decisionTargetValue)}"></label>
      <label>手動判断の状態<select id="filter-decision-status">${selectOptions([["", "すべて"], ["active", "有効"], ["expired", "期限切れ"], ["cleared", "解除済み"]], state.projectSearch.decisionStatus)}</select></label>
    </div></details>
  </div>`;
}

async function loadList() {
  const request = ++state.listRequest;
  $("list-controls").innerHTML = listControls();
  $("list").innerHTML = `<p class="muted">読込中…</p>`;
  bindControls();
  try {
    let data;
    if (state.screen === "projects") data = await api(projectSearchPath());
    else if (state.reviewView === "promotion") data = await api(`/api/promotion-queue?${new URLSearchParams({limit:state.settings.reviewLimit, include_personal_tools:state.settings.includePersonalTools, view:state.promotionView, reason:state.promotionReason, offset:state.promotionOffset})}`);
    else if (state.reviewView === "merge") data = await api(`/api/merge-batch?mode=${encodeURIComponent(state.settings.mergeMode)}&limit=${state.settings.mergeLimit}`);
    else {
      const query = new URLSearchParams({limit: state.settings.reviewLimit});
      if (state.reviewSignalKind) query.set("kinds", state.reviewSignalKind);
      data = await api(`/api/review-queue?${query}`);
    }
    if (request !== state.listRequest) return;
    renderList(data);
  } catch (error) {
    if (request !== state.listRequest) return;
    status(error.message, true);
    $("list").innerHTML = `<p class="notice">${esc(error.message)}</p>`;
  }
}

function projectSearchPath() {
  const query = new URLSearchParams({limit: state.settings.projectLimit});
  const values = {
    q: state.projectSearch.query,
    tracking_states: state.projectSearch.tracking,
    service_statuses: state.projectSearch.service,
    contribution_levels: state.projectSearch.contribution,
    has_notes: state.projectSearch.notes,
    introduced: state.projectSearch.introduced,
    has_candidates: state.projectSearch.candidates,
    has_errors: state.projectSearch.errors,
    sort: state.projectSearch.sort,
    identifier_kind: state.projectSearch.identifierKind, identifier_value: state.projectSearch.identifierValue,
    identifier_classification: state.projectSearch.identifierClassification,
    relation_predicate: state.projectSearch.relationPredicate, relation_subject_type: state.projectSearch.relationSubjectType,
    relation_subject_value: state.projectSearch.relationSubjectValue, relation_object_type: state.projectSearch.relationObjectType,
    relation_object_value: state.projectSearch.relationObjectValue, relation_source: state.projectSearch.relationSource,
    decision_kind: state.projectSearch.decisionKind, decision_target_type: state.projectSearch.decisionTargetType,
    decision_target_value: state.projectSearch.decisionTargetValue, decision_status: state.projectSearch.decisionStatus,
  };
  Object.entries(values).forEach(([key, value]) => { if (value && value !== "any") query.set(key, value); });
  return `/api/projects?${query}`;
}

function selectOptions(options, selected) {
  return options.map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

function filterOptionValues(key) {
  const values = new Set();
  list(filterOptionFallbacks[key]).forEach((value) => values.add(String(value)));
  list(state.server?.filter_options?.[key]).forEach((value) => values.add(String(value)));
  return Array.from(values).sort();
}

function splitSelections(value) {
  return String(value || "").split(/[\s,]+/).filter(Boolean);
}

function enumOptions(values, selected, labels = {}) {
  const options = [...values];
  if (selected && !options.includes(selected)) options.push(selected);
  return selectOptions([["", "すべて"], ...options.map((value) => [value, labels[value] || displayLabel(value)])], selected);
}

function multiEnumOptions(values, selected) {
  const selectedValues = new Set(splitSelections(selected));
  const options = [...values];
  selectedValues.forEach((value) => { if (!options.includes(value)) options.push(value); });
  return options.map((value) => `<option value="${esc(value)}" ${selectedValues.has(value) ? "selected" : ""}>${esc(displayLabel(value))}</option>`).join("");
}

function selectedValues(id) {
  return Array.from($(id)?.selectedOptions || []).map((option) => option.value).join(",");
}

function captureProjectSearch() {
  state.projectSearch = {
    query: $("project-query")?.value || "",
    tracking: selectedValues("filter-tracking"),
    service: selectedValues("filter-service"),
    contribution: selectedValues("filter-contribution"),
    notes: $("filter-notes")?.value || "any",
    introduced: $("filter-introduced")?.value || "any",
    candidates: $("filter-candidates")?.value || "any",
    errors: $("filter-errors")?.value || "any",
    sort: $("filter-sort")?.value || "last_seen_desc",
    identifierKind: $("filter-identifier-kind")?.value || "", identifierValue: $("filter-identifier-value")?.value || "",
    identifierClassification: $("filter-identifier-classification")?.value || "",
    relationPredicate: $("filter-relation-predicate")?.value || "", relationSubjectType: $("filter-relation-subject-type")?.value || "",
    relationSubjectValue: $("filter-relation-subject-value")?.value || "", relationObjectType: $("filter-relation-object-type")?.value || "",
    relationObjectValue: $("filter-relation-object-value")?.value || "", relationSource: $("filter-relation-source")?.value || "",
    decisionKind: $("filter-decision-kind")?.value || "", decisionTargetType: $("filter-decision-target-type")?.value || "",
    decisionTargetValue: $("filter-decision-target-value")?.value || "", decisionStatus: $("filter-decision-status")?.value || "",
  };
}

function bindControls() {
  const applyPromotionFilter = () => {
    state.promotionView = $("promotion-view").value;
    state.promotionReason = $("promotion-reason").value.trim();
    state.promotionOffset = 0;
    state.selected = "";
    syncUrl(); loadList();
  };
  $("promotion-view")?.addEventListener("change", applyPromotionFilter);
  $("apply-promotion-filter")?.addEventListener("click", applyPromotionFilter);
  $("promotion-reason")?.addEventListener("keydown", (event) => { if (event.key === "Enter") applyPromotionFilter(); });
  $("clear-promotion-filter")?.addEventListener("click", () => {
    $("promotion-reason").value = ""; applyPromotionFilter();
  });
  document.querySelectorAll("[data-review-view]").forEach((button) => button.onclick = () => { state.reviewView = button.dataset.reviewView; state.selected = ""; syncUrl(); loadList(); });
  $("refresh-review-list")?.addEventListener("click", () => loadList());
  const applyReviewFilter = () => {
    state.reviewSignalKind = $("review-signal-kind")?.value.trim() || "";
    state.selected = "";
    syncUrl();
    loadList();
  };
  $("apply-review-filter")?.addEventListener("click", applyReviewFilter);
  $("review-signal-kind")?.addEventListener("keydown", (event) => { if (event.key === "Enter") applyReviewFilter(); });
  $("clear-review-filter")?.addEventListener("click", () => {
    state.reviewSignalKind = "";
    state.selected = "";
    syncUrl();
    loadList();
  });
  bindExplicitMergePairControls(document);
  $("search-projects")?.addEventListener("click", () => { captureProjectSearch(); state.selected = ""; syncUrl(); loadList(); });
  $("project-query")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { captureProjectSearch(); state.selected = ""; syncUrl(); loadList(); } });
  $("new-seed")?.addEventListener("click", renderNewSeed);
}

function renderExplicitMergePairForm({source = "", target = "", sourceFixed = false} = {}) {
  const sourceMode = sourceFixed ? "readonly" : "";
  const backProject = sourceFixed ? ` data-explicit-merge-back-project="${esc(source)}"` : "";
  return `<div class="explicit-merge-pair" data-explicit-merge-pair${backProject}>
    <label>マージ元のプロジェクト<input data-explicit-merge-source placeholder="proj:3930cb0bedfd" value="${esc(source)}" ${sourceMode}></label>
    <label>マージ先のプロジェクト<input data-explicit-merge-target placeholder="proj:aefc07410260" value="${esc(target)}"></label>
    <button data-compare-explicit-merge class="primary">2件を比較</button>
  </div>`;
}

function bindExplicitMergePairControls(root) {
  root.querySelectorAll("[data-compare-explicit-merge]").forEach((button) => {
    button.onclick = () => compareExplicitMergePair(button.closest("[data-explicit-merge-pair]"));
  });
  root.querySelectorAll("[data-explicit-merge-source], [data-explicit-merge-target]").forEach((input) => {
    input.onkeydown = (event) => {
      if (event.key === "Enter") compareExplicitMergePair(input.closest("[data-explicit-merge-pair]"));
    };
  });
}

function compareExplicitMergePair(form) {
  const source = form?.querySelector("[data-explicit-merge-source]")?.value.trim() || "";
  const target = form?.querySelector("[data-explicit-merge-target]")?.value.trim() || "";
  if (!source || !target) {
    status("マージ元とマージ先のプロジェクトキーを入力してください。", true);
    return;
  }
  if (source === target) {
    status("マージ元とマージ先には異なるプロジェクトを指定してください。", true);
    return;
  }
  status("");
  state.reviewView = "merge";
  const backProject = form?.dataset.explicitMergeBackProject || "";
  loadMergeDetail(source, target, {backTarget: backProject ? {type: "project", key: backProject} : null});
}

function normalizeQueueItem(item) {
  return item.project || item;
}

const promotionServiceLabels = {
  confirmed: "利用画面を確認済み",
  needs_validation: "利用画面の確認が必要",
  not_applicable: "利用画面の評価なし",
};

const promotionPriorityLabels = {
  normal: "通常の優先度",
  lowered: "低い優先度",
};

const promotionVisibilityLabels = {
  eligible: "紹介候補一覧の対象",
  deferred: "紹介候補一覧で一時保留",
  excluded: "紹介候補一覧の対象外",
};

const promotionScoreLabels = {
  service_evidence: "サービスの利用画面",
  lexicon_publication: "Lexiconの公開を確認済み",
  technical_interest: "技術的な学び",
  service_status: "サービス状態",
  service_confidence: "サービス状態の確信度",
  profile_completeness: "紹介情報の充足度",
  repository_evidence: "リポジトリを発見",
  lexicon_evidence: "Lexiconを発見",
  review_readiness: "要レビュー項目なし",
  atproto_usage: "主機能でのatproto利用",
  promotion_blockers: "紹介材料の不足",
  recent_activity: "最近の活動",
  recent_collection_activity: "最近のレコード活動",
  recent_repository_activity: "最近のリポジトリ活動",
};

const promotionValidationLabels = {
  low_service_status_confidence: "サービス状態の確信度が低い",
  usable_surface_unconfirmed: "利用できるサービス画面が未確認",
};

const promotionSignalLabels = {
  service_surface_confirmed: "利用できるサービス画面を確認済み",
  service_candidate_needs_validation: "サービス画面の追加確認が必要",
  lexicon_publisher_confirmed: "Lexiconの発行者を確認済み",
  technical_insight_candidate: "技術的な学びのある候補",
};

const atprotoUsageLabels = {
  main_data: "主機能のデータ・基盤", supporting: "補助的な利用",
  bluesky_client: "Bluesky専用操作", auth_only: "認証のみ", unknown: "利用範囲は不明",
};

function renderAtprotoUsage(usage = {}) {
  return `<p>${usage.source === "operator" ? `${pill(promotionLabel(atprotoUsageLabels, usage.role || "unknown"))} · 手動判断` : "未評価"}</p>
    ${usage.source === "operator" && usage.reason ? `<p>${esc(usage.reason)}</p>` : ""}
    ${list(usage.sources).length ? `<ul>${usage.sources.map((s) => `<li>${esc(displayLabel(s.type))}: ${esc(s.locator)}</li>`).join("")}</ul>` : ""}
    <details><summary>利用範囲の判断を記録</summary><p class="muted">OAuth宣言と関連記述を参考に記入してください。手動判断は自動調査で上書きされません。</p>
    <label>主機能での利用<select id="atproto-usage-role">${selectOptions(Object.entries(atprotoUsageLabels), usage.role || "unknown")}</select></label>
    <label>判断根拠<input id="atproto-usage-reason" value="${esc(usage.source === "operator" ? usage.reason : "")}" placeholder="主機能とatprotoが担う部分を記入"></label>
    <div class="actions"><button id="save-atproto-usage">判断を保存</button>${usage.source === "operator" ? `<button id="clear-atproto-usage" class="secondary">手動判断を解除</button>` : ""}</div></details>`;
}

function promotionLabel(labels, value) {
  return labels[value] || displayLabel(value || "情報なし");
}

function renderPromotionScore(breakdown = {}) {
  const components = Object.entries(breakdown.components || {}).filter(([, value]) => Number(value) !== 0);
  if (!components.length) return "";
  return `<details class="score-breakdown"><summary>紹介スコアの内訳</summary><dl class="kv">${components.map(([key, value]) => `<dt>${esc(promotionLabel(promotionScoreLabels, key))}</dt><dd>${Number(value) > 0 ? "+" : ""}${esc(value)}</dd>`).join("")}</dl></details>`;
}

function promotionCardDetails(item) {
  if (state.screen !== "review" || state.reviewView !== "promotion") return "";
  const breakdown = item.score_breakdown || {};
  const validationReasons = uniqueValues(
    list(item.signals).flatMap((signal) => list(signal.details?.validation_reasons))
  ).map((reason) => promotionLabel(promotionValidationLabels, reason));
  const serviceGroup = item.service_evidence_group || "not_applicable";
  const priorityGroup = item.priority_group || "normal";
  const visibility = item.candidate_visibility?.group || "eligible";
  const attention = item.attention?.group || "unreviewed";
  const technicalView = state.promotionView === "technical";
  return `<span class="meta-row">${pill(promotionLabel(promotionServiceLabels, serviceGroup), serviceGroup === "needs_validation" ? "candidate" : "")}${priorityGroup === "normal" ? "" : pill(promotionLabel(promotionPriorityLabels, priorityGroup), "candidate")}${pill(technicalView ? `技術評価 ${displayLabel(item.technical_interest_level)} · ${item.technical_interest_score}` : `スコア ${breakdown.total ?? item.sort_score ?? 0}`)}</span>
    <span class="meta-row">${pill(item.atproto_usage?.source === "operator" ? promotionLabel(atprotoUsageLabels, item.atproto_usage.role) : "atproto利用 未評価")}${pill(promotionLabel({unreviewed:"未確認", recorded:"判断記録あり", held:"保留中", changed:"根拠変化・再確認", due:"再確認時期"}, attention), ["changed", "due"].includes(attention) ? "candidate" : "")}${visibility === "eligible" ? "" : pill(promotionLabel(promotionVisibilityLabels, visibility), "candidate")}</span>
    ${item.atproto_usage?.source === "operator" && item.atproto_usage.reason ? `<span class="muted">${esc(item.atproto_usage.reason)}</span>` : ""}
    ${list(item.signals).some((s) => s.kind === "technical_insight_candidate") ? `<span>${esc(list(item.signals).find((s) => s.kind === "technical_insight_candidate").details.technical_interest_summary)}</span>` : ""}
    ${item.active_deferral ? `<span class="muted">保留: ${esc(item.active_deferral.reason || "理由なし")} · ${esc(formatTimestamp(item.active_deferral.expires_at))}まで</span>` : ""}
    ${!item.active_deferral && item.last_promotion_deferral_reason ? `<span class="muted">前回の保留理由: ${esc(item.last_promotion_deferral_reason)}</span>` : ""}
    ${item.priority_reason ? `<span class="muted">優先度のメモ: ${esc(item.priority_reason)}</span>` : ""}
    ${validationReasons.length ? `<span class="muted">追加確認: ${esc(validationReasons.join("、"))}</span>` : ""}`;
}

function renderList(data) {
  const items = list(data.items);
  const count = data.count ?? items.length;
  const isPromotion = state.screen === "review" && state.reviewView === "promotion";
  if (isPromotion) {
    $("promotion-reasons").innerHTML = list(data.reasons).length ? `<details><summary>理由別の候補数 (${data.reasons.length})</summary>${data.reasons.map((item) => `<button class="small secondary" data-promotion-reason="${esc(item.reason)}">${esc(item.reason)} (${esc(item.count)})</button>`).join("")}</details>` : "";
    document.querySelectorAll("[data-promotion-reason]").forEach((button) => button.onclick = () => {
      state.promotionReason = button.dataset.promotionReason; state.promotionOffset = 0; syncUrl(); loadList();
    });
  }
  $("list").innerHTML = `<div class="meta-row"><strong>${esc(count)}</strong> 件</div>` + (items.length ? items.map((item, index) => {
    const project = normalizeQueueItem(item);
    const key = project.project_key || item.project_key || item.source_project_key || "";
    const name = project.name || item.source_name || key;
    const mergeTarget = item.target_project_key;
    const signals = list(item.signals).map((signal) => {
      const kind = signal.kind || signal;
      if (state.reviewView === "promotion" && ["service_surface_confirmed", "service_candidate_needs_validation"].includes(kind)) return "";
      const label = state.reviewView === "promotion" ? promotionLabel(promotionSignalLabels, kind) : kind;
      return pill(label, "candidate");
    }).filter(Boolean).join(" ");
    return `<button class="card ${isPromotion ? "promotion-card" : ""} ${state.selected === key ? "selected" : ""}" data-project-key="${esc(key)}" ${mergeTarget ? `data-merge-target="${esc(mergeTarget)}"` : ""}>
      <span class="card-title"><strong>${esc(name)}</strong>${pill(project.tracking_state || item.recommendation?.confidence || "")}</span>
      <span class="meta-row">${pill(project.service_status || "")}${pill(project.atproto_contribution_level || "")}${project.introduced_url ? pill("introduced", "confirmed") : ""}${project.has_candidates ? pill("candidates", "candidate") : ""}</span>
      ${project.tagline ? `<span>${esc(project.tagline)}</span>` : ""}${signals ? `<span class="meta-row">${signals}</span>` : ""}
      ${promotionCardDetails(item)}
      ${mergeTarget ? `<span class="muted">→ ${esc(item.target_name || mergeTarget)}</span>` : ""}
    </button>`;
  }).join("") : `<div class="empty-state"><p>条件に一致する登録プロジェクトはありません。</p></div>`);
  if (isPromotion) {
    $("list").insertAdjacentHTML("beforeend", `<div id="promotion-pages" class="actions"><span>${esc(data.offset + items.length)} / ${esc(data.total)} 件</span><button id="promotion-prev" ${!data.offset ? "disabled" : ""}>前のページ</button><button id="promotion-next" ${!data.has_more ? "disabled" : ""}>次のページ</button></div>`);
    $("promotion-prev").onclick = () => { state.promotionOffset = Math.max(0, state.promotionOffset - state.settings.reviewLimit); syncUrl(); loadList(); };
    $("promotion-next").onclick = () => { state.promotionOffset += Number(state.settings.reviewLimit); syncUrl(); loadList(); };
  }
  document.querySelectorAll("[data-project-key]").forEach((card) => card.onclick = () => {
    const source = card.dataset.projectKey;
    const target = card.dataset.mergeTarget;
    target ? loadMergeDetail(source, target) : loadDetail(source);
  });
}

function openListCard(card) {
  if (!card) return false;
  const source = card.dataset.projectKey;
  const target = card.dataset.mergeTarget;
  target ? loadMergeDetail(source, target) : loadDetail(source);
  return true;
}

function advanceReviewList(sourceKey, {targetKey = "", statusPrefix = "今回は先送りしました。"} = {}) {
  if (state.screen !== "review") return false;
  if (!sourceKey) return false;
  const cards = Array.from(document.querySelectorAll("#list [data-project-key]"));
  const current = cards.find((card) => card.dataset.projectKey === sourceKey && (!targetKey || card.dataset.mergeTarget === targetKey))
    || (targetKey ? cards.find((card) => card.dataset.projectKey === targetKey && card.dataset.mergeTarget === sourceKey) : null)
    || cards.find((card) => card.dataset.projectKey === sourceKey)
    || cards.find((card) => card.classList.contains("selected"));
  if (!current) return false;
  const isContinuation = current.dataset.reviewContinuation === "true";
  const currentIndex = cards.indexOf(current);
  const next = currentIndex >= 0 ? cards[currentIndex + 1] : null;
  const previous = currentIndex >= 0 ? cards[currentIndex - 1] : null;
  current.remove();
  const remaining = Array.from(document.querySelectorAll("#list [data-project-key]")).length;
  const meta = $("list")?.querySelector(".meta-row strong");
  if (!isContinuation && meta && Number.isFinite(Number(meta.textContent))) meta.textContent = String(Math.max(0, Number(meta.textContent) - 1));
  if (next || previous) {
    status(`${statusPrefix} 次の項目へ進みました。残り${remaining}件。再読込で一覧を更新します。`);
    openListCard(next || previous);
  } else {
    state.selected = "";
    document.body.classList.remove("detail-open");
    syncUrl();
    $("detail").innerHTML = `<div class="empty-state"><p>表示中の要レビュー項目はすべて確認しました。</p><div class="actions"><button id="refresh-empty-review-list" class="primary">一覧を再読込</button></div></div>`;
    resetDetailScroll();
    $("refresh-empty-review-list")?.addEventListener("click", () => loadList());
    status(`${statusPrefix} 表示中の項目をすべて確認しました。再読込で一覧を更新します。`);
  }
  return true;
}

function insertMergeReviewContinuation(sourceKey, targetKey, anchorIndex) {
  const marker = document.createElement("button");
  marker.type = "button";
  marker.hidden = true;
  marker.dataset.projectKey = sourceKey;
  marker.dataset.mergeTarget = targetKey;
  marker.dataset.reviewContinuation = "true";
  const cards = Array.from(document.querySelectorAll("#list [data-project-key]"));
  const nextCard = cards[Math.min(Math.max(anchorIndex, 0), cards.length - 1)];
  if (nextCard) nextCard.before(marker);
  else $("list")?.append(marker);
}

function advanceReviewListAfterMutation(response, path) {
  if (state.screen === "review" && state.reviewView === "promotion") {
    state.promotionOffset = 0;
    if ($("promotion-pages")) $("promotion-pages").innerHTML = `<span class="muted">保存により候補が変わりました。再読込で先頭から再取得します。</span>`;
  }
  if (path === "/api/merge-batch/action" && response.mode === "execute") {
    return advanceMergeReviewAfterMerge(response);
  }
  const sourceKey = path === "/api/merge-batch/action"
    ? String(response.source_project_key || state.selected || "")
    : String(response.project_key || state.selected || "");
  const targetKey = path === "/api/merge-batch/action" ? String(response.target_project_key || "") : "";
  return advanceReviewList(sourceKey, {targetKey, statusPrefix: "保存しました。"});
}

function advanceMergeReviewAfterMerge(response) {
  if (state.screen !== "review" || state.reviewView !== "merge") return false;
  const sourceKey = String(response.source_project_key || "");
  const targetKey = String(response.target_project_key || "");
  const affectedKeys = new Set([sourceKey, targetKey].filter(Boolean));
  const cards = Array.from(document.querySelectorAll("#list [data-project-key]"));
  const affected = cards.filter((card) => (
    affectedKeys.has(String(card.dataset.projectKey || ""))
    || affectedKeys.has(String(card.dataset.mergeTarget || ""))
  ));
  const anchorIndex = affected.length ? cards.indexOf(affected[0]) : -1;
  const removedVisibleCount = affected.filter((card) => card.dataset.reviewContinuation !== "true").length;
  affected.forEach((card) => card.remove());
  const remainingCards = Array.from(document.querySelectorAll("#list [data-project-key]"));
  const meta = $("list")?.querySelector(".meta-row strong");
  if (meta && Number.isFinite(Number(meta.textContent))) {
    meta.textContent = String(Math.max(0, Number(meta.textContent) - removedVisibleCount));
  }

  const next = response.next_merge_review || {};
  const nextSource = String(next.source_project_key || "");
  const nextTarget = String(next.target_project_key || "");
  if (nextSource && nextTarget) {
    insertMergeReviewContinuation(nextSource, nextTarget, anchorIndex);
    status(`保存しました。 マージ後のプロジェクトの次候補を確認します（残り${response.remaining_merge_candidate_count || 0}件）。`);
    loadMergeDetail(nextSource, nextTarget);
    return true;
  }

  const nextCard = remainingCards[Math.min(Math.max(anchorIndex, 0), Math.max(remainingCards.length - 1, 0))];
  if (nextCard) {
    status(`保存しました。 次の項目へ進みました。残り${remainingCards.length}件。再読込で一覧を更新します。`);
    openListCard(nextCard);
  } else {
    state.selected = "";
    document.body.classList.remove("detail-open");
    syncUrl();
    $("detail").innerHTML = `<div class="empty-state"><p>表示中の要レビュー項目はすべて確認しました。</p><div class="actions"><button id="refresh-empty-review-list" class="primary">一覧を再読込</button></div></div>`;
    resetDetailScroll();
    $("refresh-empty-review-list")?.addEventListener("click", () => loadList());
    status("保存しました。 表示中の項目をすべて確認しました。再読込で一覧を更新します。");
  }
  return true;
}

function skipReviewItem(sourceKey = state.selected, targetKey = "") {
  if (!advanceReviewList(String(sourceKey || ""), {targetKey: String(targetKey || ""), statusPrefix: "今回は先送りしました。"})) {
    status("先送りする項目がありません。一覧を再読込してください。", true);
  }
}

async function loadDetail(key, {updateUrl = true, backTarget = null} = {}) {
  const request = ++state.detailRequest;
  state.selected = key; document.body.classList.add("detail-open");
  state.detailBackTarget = backTarget;
  if (updateUrl) syncUrl();
  $("detail").innerHTML = `<p class="muted">${esc(key)}を読込中…</p>`;
  resetDetailScroll();
  try {
    const data = await api(`/api/projects/${encodeURIComponent(key)}`);
    if (request === state.detailRequest && state.selected === key) renderDetail(data);
  } catch (error) {
    if (request !== state.detailRequest || state.selected !== key) return;
    status(error.message, true);
    $("detail").innerHTML = `<p class="notice">${esc(error.message)}</p>`;
  }
}

function profileValue(profile, key) { return profile?.core?.[key] ?? profile?.[key] ?? ""; }
function section(title, body) { return `<section class="detail-section"><h3>${esc(title)}</h3>${body}</section>`; }
function projectTimestampRows(project) {
  return `<dt>初回観測</dt><dd>${esc(formatTimestamp(project.first_seen_at) || "不明")}</dd><dt>最終観測</dt><dd>${esc(formatTimestamp(project.last_seen_at) || "不明")}</dd><dt>最終調査</dt><dd>${esc(formatTimestamp(project.last_enriched_at) || "未調査")}</dd>`;
}
function mergeIdentifierLabel(item, classification) {
  const entries = list(item.entries).filter((entry) => entry.classification === classification);
  const roleLabels = uniqueValues(entries.map((entry) => entry.role_label || entry.role));
  if (item.family === "identity" && roleLabels.includes("developer")) {
    const parts = uniqueValues(entries.filter((entry) => entry.role !== "developer_account").map((entry) => entry.role_label || entry.kind));
    return `開発者の識別情報${parts.length ? ` (${parts.map(displayLabel).join(" + ")})` : ""}`;
  }
  return roleLabels.map(displayLabel).join(" + ") || item.role_labels?.map(displayLabel).join(" + ") || item.roles?.map(displayLabel).join(" + ") || item.kinds?.map(displayLabel).join(" + ") || item.family || "";
}
function mergeIdentifierRows(identifiers, classification) {
  const rows = list(identifiers).filter((item) => list(item.classifications).includes(classification) || item.classification === classification);
  return rows.map((item) => `<div><span class="muted">${esc(mergeIdentifierLabel(item, classification))}</span> ${identifierValueLink(item)}</div>`).join("") || `<span class="muted">なし</span>`;
}
function actionButton(label, action, data = {}, style = "small") {
  const attrs = Object.entries(data).map(([key, value]) => `data-${key}="${esc(value)}"`).join(" ");
  return `<button class="${style}" data-action="${action}" ${attrs}>${esc(label)}</button>`;
}

function renderDetail(data) {
  const project = data.project;
  state.selected = project.project_key;
  syncUrl({replace: true});
  const profile = project.profile || {};
  const identifiers = list(data.identifiers);
  const relations = list(data.relations);
  const decisions = data.decisions || {active: [], history: []};
  const evaluation = data.evaluation || {};
  const promotionPolicy = data.promotion_policy || {};
  const investigation = data.investigation;
  const overview = data.overview || {};
  const serviceOwnership = data.service_ownership_review || {};
  const browserServiceUrls = uniqueValues([
    ...list(overview.confirmed_services),
    ...identifiers
      .filter((item) => item.family === "service" && ["confirmed", "candidate"].includes(item.classification))
      .map((item) => item.value),
  ]);
  const explanations = data.explanations?.topics || {};
  state.detailRevision = data.revision || "";
  state.mergeTargetRevision = data.merge_target_revision || "";
  const hasPromotionHold = Boolean(promotionPolicy.active_deferral);
  const promotionDefer = overview.promotion_defer || {};
  const promotionDeferDays = Number(promotionDefer.default_days || 30);
  const promotionDeferAction = actionButton(
    `${promotionDeferDays}日間保留`,
    "defer-promotion",
    {days: promotionDeferDays, "default-note": promotionDefer.last_reason || ""}
  );
  const priorityGroup = promotionPolicy.priority_group || "normal";
  const promotionPriorityActions = `
    ${priorityGroup === "lowered"
      ? actionButton("通常の優先度へ戻す", "clear-promotion-priority") + actionButton("理由を編集", "lower-promotion-priority", {"default-note":promotionPolicy.priority_reason || ""})
      : actionButton("優先度を下げる", "lower-promotion-priority")}`;
  const reviewSkipAction = state.screen === "review" ? `<button id="skip-review-item" class="small secondary">今回は先送り</button>` : "";
  const promotionActions = `<div class="promotion-actions"><strong>紹介候補の判断</strong>
    <div class="actions">${hasPromotionHold ? actionButton("保留を解除", "clear-promotion-deferral") : promotionDeferAction}${promotionPriorityActions}${reviewSkipAction}</div>
    ${promotionPolicy.priority_reason ? `<p>特集候補・優先度の理由: ${esc(promotionPolicy.priority_reason)}</p>` : ""}
    ${hasPromotionHold ? `<p>保留: ${esc(promotionPolicy.active_deferral.reason || "理由なし")} · ${esc(formatTimestamp(promotionPolicy.active_deferral.expires_at))}まで</p>` : ""}</div>`;
  const tab = (key, label, body) => `<section class="detail-tab-panel" data-detail-panel="${key}" ${state.detailTab === key ? "" : "hidden"}>${body}</section>`;
  const overviewBody = `
    ${section("紹介情報", `<dl class="kv"><dt>公式サイト</dt><dd>${project.website_url ? link(project.website_url) : "なし"}</dd><dt>採用済みサービス</dt><dd>${list(overview.confirmed_services).map(link).join("<br>") || "なし"}</dd><dt>リポジトリ</dt><dd>${list(overview.repositories).map(link).join("<br>") || "なし"}</dd><dt>アカウント</dt><dd>${esc(overview.account || "なし")}</dd><dt>紹介済み</dt><dd>${project.introduced_url ? link(project.introduced_url) : "なし"}</dd><dt>短い説明</dt><dd>${esc(profileValue(profile, "tagline")) || "なし"}</dd><dt>説明</dt><dd>${esc(profileValue(profile, "summary")) || "なし"}</dd><dt>メモ</dt><dd>${esc(profileValue(profile, "notes")) || "なし"}</dd><dt>システム履歴</dt><dd>${renderSystemNotes(project)}</dd>${projectTimestampRows(project)}</dl><div class="actions"><button id="edit-profile">編集</button></div>`)}
    ${project.features?.length ? section("特徴", `<ul>${project.features.map((value) => `<li>${esc(value)}</li>`).join("")}</ul>`) : ""}
    ${project.editorial?.proposal ? `<p class="notice">根拠付きの説明更新案があります。紹介情報の「編集」から確認できます。</p>` : ""}
    ${section("確認時点", renderServiceObservations(project.service_observations))}
    ${section("主機能でのatproto利用", renderAtprotoUsage(promotionPolicy.atproto_usage))}
    ${section("リンク・資料", renderProjectLinks(overview))}
    ${section("評価", `<dl class="kv"><dt>atproto寄与度</dt><dd>${pill(evaluation.atproto_contribution_level)} 点数 ${esc(evaluation.atproto_contribution_score)}</dd><dt>技術的な学び</dt><dd>${pill(evaluation.technical_interest_level)} 点数 ${esc(evaluation.technical_interest_score)}</dd><dt>サービス状態の確信度</dt><dd>${esc(displayLabel(evaluation.service_status_confidence))}</dd><dt>紹介候補一覧</dt><dd>${evaluation.promotion_ready ? pill("ready", "confirmed") : list(evaluation.promotion_blockers).map((item) => pill(item, "candidate")).join(" ") || "紹介材料が未充足"}</dd><dt>紹介候補一覧での扱い</dt><dd>${pill(promotionLabel(promotionVisibilityLabels, promotionPolicy.candidate_visibility?.group || "excluded"))}</dd><dt>サービスの利用画面</dt><dd>${pill(promotionLabel(promotionServiceLabels, promotionPolicy.service_evidence_group || "not_applicable"))}</dd><dt>紹介候補の優先度</dt><dd>${pill(promotionLabel(promotionPriorityLabels, priorityGroup))} ${esc(promotionPolicy.priority_reason || "")}</dd><dt>紹介スコア</dt><dd>${esc(promotionPolicy.score_breakdown?.total ?? 0)}</dd><dt>技術評価の説明</dt><dd>${esc(evaluation.technical_interest_summary || "なし")}</dd></dl>${renderPromotionScore(promotionPolicy.score_breakdown)}`)}
    ${section("有効な手動判断", list(overview.active_guards).length ? renderDecisions({active: overview.active_guards, history: []}) : `<p class="muted">有効な手動判断はありません。</p>`)}
    ${section("サービス・帰属", renderServiceOwnership(serviceOwnership, browserServiceUrls))}
    ${section("マージ候補", `${renderExplicitMergePairForm({source: project.project_key, sourceFixed: true})}<div id="merge-analysis-content"><p class="muted">マージ候補を計算中…</p></div>`)}
    ${section("判定理由", `<div id="explain-content">${renderExplanations(explanations)}</div>`)}
  `;
  $("detail").innerHTML = `
    <div class="detail-header">
      ${backToListButton()}
      ${contextualBackButton()}
      <p class="eyebrow project-key">${esc(project.project_key)}</p>
      <h2>${esc(project.name || project.project_key)}</h2>
      <div class="meta-row">${pill(project.tracking_state)}${pill(project.service_status)}${project.introduced_url ? pill("introduced", "confirmed") : ""}</div>
      <div class="actions"><select id="tracking-state" aria-label="追跡状態">${(state.server?.tracking_states || []).map((value) => `<option value="${esc(value)}" ${value === project.tracking_state ? "selected" : ""}>${esc(displayLabel(value))}</option>`).join("")}</select><button id="save-tracking">追跡状態を変更</button><button id="add-project-seed">シードを追加</button><button id="enrich-project" class="primary">調査</button><span id="merge-analysis-action"></span></div>
      ${promotionActions}
    </div>
    <nav class="detail-tabs" aria-label="プロジェクト詳細">
      ${[["overview","概要"],["identifiers","識別子"],["relations","関係"],["investigation","調査履歴"],["decisions","手動判断"],["raw","JSON"]].map(([key,label]) => `<button class="secondary ${state.detailTab === key ? "active" : ""}" data-detail-tab="${key}">${label}</button>`).join("")}
    </nav>
    ${tab("overview", "概要", overviewBody)}
    ${tab("identifiers", "識別子", section("識別子", identifiers.length ? renderIdentifiers(identifiers) : `<p class="muted">識別子はありません。</p>`))}
    ${tab("relations", "関係", section("関係", relations.length ? relations.map((item) => `<div class="relation ${esc(item.classification)}"><div>${pill(item.classification, item.classification)} ${esc(`${displayLabel(item.subject.type)}: ${item.subject.value} → ${displayLabel(item.predicate)} → ${displayLabel(item.object.type)}: ${item.object.value}`)}</div><div class="muted">${esc([displayLabel(item.source), item.locator, displayLabel(item.method)].filter(Boolean).join(" · "))}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("") : `<p class="muted">保存済みの関係はありません。</p>`))}
    ${tab("investigation", "調査履歴", section("最新の調査実行記録", `${renderInvestigation(investigation)}<div class="actions"><button id="load-investigation-history">調査履歴を読み込む</button></div><div id="investigation-history"></div>`))}
    ${tab("decisions", "手動判断", section("手動判断", renderDecisions(decisions)))}
    ${tab("raw", "元データ", section("元データ（JSON）", `<details open><summary>プロジェクトのJSON</summary>${json(data)}</details>`))}
    <div class="detail-footer">${backToListButton()}${contextualBackButton()}</div>
  `;
  bindBackToListButtons();
  $("save-tracking")?.addEventListener("click", () => confirmMutation("追跡状態を変更", `${project.name || project.project_key}の追跡状態を「${displayLabel($("tracking-state").value)}」へ変更しますか？`, `/api/projects/${encodeURIComponent(project.project_key)}/tracking-state`, {tracking_state: $("tracking-state").value}, false));
  $("enrich-project")?.addEventListener("click", () => startProjectEnrich(project.project_key));
  $("verify-service-browser")?.addEventListener("click", () => startBrowserServiceVerification(
    project.project_key,
    $("browser-service-url")?.value || "",
  ));
  $("add-project-seed")?.addEventListener("click", () => addProjectSeed(project.project_key));
  $("edit-profile")?.addEventListener("click", () => editProfile(project, profile));
  $("skip-review-item")?.addEventListener("click", () => skipReviewItem(project.project_key));
  $("load-investigation-history")?.addEventListener("click", () => loadInvestigationHistory(project.project_key));
  bindExplicitMergePairControls(document);
  document.querySelectorAll("[data-detail-tab]").forEach((button) => button.onclick = () => {
    state.detailTab = button.dataset.detailTab;
    syncUrl();
    document.querySelectorAll("[data-detail-tab]").forEach((item) => item.classList.toggle("active", item.dataset.detailTab === state.detailTab));
    document.querySelectorAll("[data-detail-panel]").forEach((item) => item.hidden = item.dataset.detailPanel !== state.detailTab);
  });
  bindDetailActions(project.project_key);
  loadMergeAnalysis(project.project_key, data.revision, explanations);
}

const identifierFamilies = [
  {key: "site", label: "公式サイト"},
  {key: "service", label: "サービス"},
  {key: "repository", label: "リポジトリ"},
  {key: "schema", label: "NSID"},
  {key: "identity", label: "アカウント・ドメイン"},
];

function uniqueValues(values) { return [...new Set(values.filter(Boolean))]; }

function pdslsIdentityUrl(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  if (normalized.startsWith("did:") || /^[a-z0-9][a-z0-9.-]*\.[a-z0-9.-]+$/i.test(normalized)) {
    const target = new URL("https://pds.ls/");
    target.pathname = `at://${normalized}`;
    return target.href;
  }
  return "";
}

function identifierValueLink(item) {
  const kinds = list(item.kinds);
  const value = String(item.value || "").trim();
  if (kinds.some((kind) => ["site_url", "service_url", "repo_url"].includes(kind))) return link(value);
  if (kinds.some((kind) => ["account", "did", "handle"].includes(kind))) {
    const href = pdslsIdentityUrl(value);
    return href ? externalLink(href, value) : esc(value);
  }
  return esc(value);
}

function renderSystemNotes(project) {
  const notes = list(project.system_notes).filter((item) => String(item || "").trim());
  if (!notes.length) return "なし";
  return `<details class="system-notes"><summary>${notes.length}件（原文）</summary><ol>${notes.map((item) => `<li>${esc(item)}</li>`).join("")}</ol></details>`;
}

function renderProjectLinks(overview) {
  const groups = [
    ["利用・インストール", ["website", "webApp", "distribution", "appStore", "playStore", "fdroid"]],
    ["プロジェクト情報", ["repository", "docs", "support"]],
    ["補足情報", ["changelog", "status", "privacyPolicy", "termsOfService", "webManifest"]],
  ];
  const records = [];
  list(overview.app_links).forEach((item) => records.push({url:item.uri, role:item.role, confidence:"confirmed"}));
  list(overview.resource_urls).forEach((item) => {
    if (!records.some((existing) => existing.url === item.url && existing.role === item.role)) records.push(item);
  });
  const known = new Set(groups.flatMap(([, roles]) => roles));
  groups.push(["その他", uniqueValues(records.map((item) => item.role).filter((role) => !known.has(role)))]);
  const body = groups.map(([label, roles]) => {
    const items = records.filter((item) => roles.includes(item.role)).sort((a, b) => {
      const confidence = (a.confidence === "confirmed" ? 0 : 1) - (b.confidence === "confirmed" ? 0 : 1);
      return confidence || roles.indexOf(a.role) - roles.indexOf(b.role) || String(a.url).localeCompare(String(b.url));
    });
    if (!items.length) return "";
    return `<div class="identifier-kind"><div class="identifier-kind-header"><h4>${esc(label)}</h4><span>${items.length}</span></div>${items.map((item) => `<div class="identifier ${esc(item.confidence || "candidate")}"><div>${link(item.url)}</div><div class="muted">${esc(displayLabel(item.role))} · ${pill(item.confidence || "candidate", item.confidence || "candidate")}</div></div>`).join("")}</div>`;
  }).join("");
  return body ? `<div class="identifier-groups">${body}</div>` : `<p class="muted">保存済みリンクはありません。</p>`;
}

function renderIdentifiers(items) {
  const grouped = list(items);
  const knownFamilies = new Set(identifierFamilies.map((family) => family.key));
  const families = [...identifierFamilies, ...uniqueValues(grouped.map((item) => item.family).filter((family) => !knownFamilies.has(family))).map((key) => ({key, label: grouped.find((item) => item.family === key)?.family_label || key}))];
  return `<div class="identifier-groups">${families.map(({key, label}) => {
    const values = grouped.filter((item) => item.family === key);
    if (!values.length) return "";
    return `<div class="identifier-kind"><div class="identifier-kind-header"><h4>${esc(label)}</h4><span>${values.length}</span></div>${values.map(renderIdentifier).join("")}</div>`;
  }).join("")}</div>`;
}

function renderIdentifier(item) {
  const classifications = item.classifications || [item.classification];
  const effectiveClassification = item.classification || classifications[0] || "";
  const actionKind = item.actions?.identifier_type || "";
  const actions = `${item.actions?.block ? actionButton("識別子を除外", "block-identifier", {type:actionKind, value:item.value}, "small danger") : ""}${item.actions?.defer ? actionButton("識別子を30日保留", "defer-identifier", {type:actionKind, value:item.value}) : ""}${item.actions?.split ? actionButton("識別子を別プロジェクトへ分割", "split-project", {type:actionKind, value:item.value}) : ""}`;
  const showKinds = item.family === "identity" || item.family.startsWith("other:");
  const entries = list(item.entries);
  const detail = uniqueValues([showKinds ? item.kinds.map(displayLabel).join(" + ") : "", item.is_primary ? "primary" : "", ...item.providers]).join(" · ");
  return `<div class="identifier ${esc(effectiveClassification)}">
    <div class="identifier-main">
      <div class="identifier-value">${identifierValueLink(item)}</div>
      ${detail ? `<div class="identifier-roles">${esc(detail)}</div>` : ""}
      <div class="identifier-entry-list">${entries.map((entry) => {
        const entryType = entry.actions?.identifier_type || "";
        const entryValue = entry.value || item.value;
        const entryActions = `${entry.actions?.confirm ? actionButton("この値を採用", "confirm-candidate", {type:entryType, value:entryValue}, "small primary") : ""}${entry.actions?.reject ? actionButton("この値を除外", "reject-candidate", {type:entryType, value:entryValue}, "small danger") : ""}${entry.actions?.block ? actionButton("この値を除外", "block-identifier", {type:entryType, value:entryValue}, "small danger") : ""}${entry.actions?.defer ? actionButton("この値を30日保留", "defer-identifier", {type:entryType, value:entryValue}, "small") : ""}${entry.actions?.split ? actionButton("この値を別プロジェクトへ分割", "split-project", {type:entryType, value:entryValue}, "small") : ""}`;
        const entryValueLabel = entryValue !== item.value ? entryValue : "";
        return `<div class="identifier-entry"><div>${pill(entry.classification, entry.classification)} <strong>${esc(displayLabel(entry.role_label || entry.role))}</strong>${entry.is_primary ? ` ${pill("primary")}` : ""}</div><div class="muted">${esc(uniqueValues([displayLabel(entry.kind), entryValueLabel, entry.provider, formatTimestamp(entry.observed_at)]).join(" · "))}</div>${entryActions ? `<div class="actions">${entryActions}</div>` : ""}</div>`;
      }).join("") || `<div class="identifier-summary">${classifications.map((value) => pill(value, value)).join("")}<span class="identifier-roles">${esc(item.role_labels.map(displayLabel).join(" · "))}</span></div>`}</div>
    </div>
    ${actions ? `<details class="identifier-actions"><summary>識別子全体への操作</summary><div class="actions"><p class="muted">この識別子の値全体に適用します。</p>${actions}</div></details>` : ""}
  </div>`;
}

function decisionNote(item) {
  // Translate only notes with a known generating action and provenance.
  if (item.kind === "confirm_identifier") {
    const labels = {
      trust_policy: {
        "service accepted by the configured trust policy": "設定された信頼規則でサービスを採用",
        "repository accepted by the configured trust policy": "設定された信頼規則でリポジトリを採用",
      },
      operator_add_seed: {"identifier added by operator": "利用者がシードとして識別子を追加"},
      web_ui: {"candidate confirmed by operator": "利用者が候補を採用"},
      "trust_policy:accepted_lexicon_publisher": {
        "collection accepted through authority-scoped publication evidence": "同じ管理範囲の公開根拠によりコレクションを採用",
      },
    };
    const source = item.metadata?.source || "";
    if (Object.hasOwn(labels, source) && Object.hasOwn(labels[source], item.note)) return labels[source][item.note];
  }
  return item.note || "メモなし";
}

function renderDecisions(decisions) {
  const active = list(decisions.active);
  const history = list(decisions.history);
  const current = active.length ? active.map((item) => {
    const summary = item.kind === "lower_promotion_priority"
      ? `${pill("紹介候補の優先度", "candidate")} 低い優先度`
      : `${pill(item.kind, "candidate")} ${esc(displayLabel(item.target_type))} ${esc(item.target_value)}`;
    return `<div class="decision"><div>${summary}</div><div class="muted">${esc(decisionNote(item))} ${item.expires_at ? `· 期限 ${esc(formatTimestamp(item.expires_at))}` : ""}</div><div class="actions">${item.kind === "block_identifier" ? actionButton("除外を解除", "clear-block-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_identifier" ? actionButton("保留を解除", "clear-defer-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_promotion" ? actionButton("保留を解除", "clear-promotion-deferral") : ""}${item.kind === "lower_promotion_priority" ? actionButton("通常の優先度へ戻す", "clear-promotion-priority") : ""}${item.kind === "separate_project" ? actionButton("別プロジェクト指定を解除", "clear-separate", {other:item.target_value}) : ""}${["dismiss_review_target", "defer_review_target"].includes(item.kind) ? actionButton("解除", "clear-review-target", {"target-type":item.target_type, "target-value":item.target_value, "review-action":item.kind.startsWith("defer") ? "defer" : "dismiss"}) : ""}</div></div>`;
  }).join("") : `<p class="muted">有効な手動判断はありません。</p>`;
  return current + (history.length ? `<details><summary>手動判断の履歴（JSON） (${history.length})</summary>${json(history)}</details>` : "");
}

function renderInvestigation(run) {
  if (!run) return `<p class="muted">保存済みの調査実行記録はありません。</p>`;
  return `<dl class="kv"><dt>調査実行記録</dt><dd>${esc(run.run_id)}</dd><dt>完了日時</dt><dd>${esc(formatTimestamp(run.completed_at))}</dd><dt>所要時間</dt><dd>${esc(formatMilliseconds(run.metrics?.total_ms) || "未記録")}</dd><dt>記録件数</dt><dd>${esc(run.record_count)}</dd><dt>失敗</dt><dd>${esc(run.failure_count)}</dd></dl>
    <div>${list(run.collectors).map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(displayLabel(item.source))}</strong></div><div class="muted">${esc(displayLabel(item.operation))} · ${esc(item.request_count || 0)} リクエスト · ${esc(item.result_count || 0)} 件${item.stop_reason ? ` · ${esc(displayLabel(item.stop_reason))}` : ""}</div></div>`).join("") || `<p class="muted">収集処理の概要はありません。</p>`}</div>
    ${run.failures?.length ? `<details><summary>失敗</summary>${run.failures.map((item) => `<div class="collector"><div>${pill(item.category, "candidate")} ${esc(displayLabel(item.source))} ${esc(displayLabel(item.operation))}</div><div class="muted">技術詳細（原文）: ${esc(item.message)}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("")}</details>` : ""}`;
}

function renderRunMetrics(metrics) {
  if (!metrics?.version) return `<p class="muted">この調査実行記録には所要時間が記録されていません。</p>`;
  const phaseLabels = {
    initial_collection_ms: "初期収集・反映",
    planning_ms: "調査計画",
    investigation_collection_ms: "追加収集・反映",
    triage_ms: "LLMによる評価・反映",
    evaluation_ms: "最終評価",
    run_history_save_ms: "調査履歴の変換・SQL保存",
    project_save_ms: "プロジェクトの変換・SQL保存",
  };
  const phases = Object.entries(phaseLabels).filter(([key]) => metrics.phases?.[key] !== undefined);
  const requests = list(metrics.llm_requests);
  return `<dl class="kv"><dt>合計</dt><dd>${esc(formatMilliseconds(metrics.total_ms))}</dd>${phases.map(([key, label]) => `<dt>${esc(label)}</dt><dd>${esc(formatMilliseconds(metrics.phases[key]))}</dd>`).join("")}</dl>${requests.length ? `<details><summary>LLMリクエスト (${requests.length})</summary>${requests.map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(displayLabel(item.purpose))}</strong> · ${esc(formatMilliseconds(item.duration_ms))}</div><div class="muted">${esc(item.prompt_bytes || 0)} 入力バイト · ${esc(item.response_bytes || 0)} 出力バイト · 制限時間 ${esc(item.timeout_seconds)}秒${item.error_category ? ` · ${esc(displayLabel(item.error_category))}` : ""}${item.model ? ` · ${esc(item.model)}` : ""}</div></div>`).join("")}</details>` : `<p class="muted">LLMリクエストの記録はありません。</p>`}`;
}

function renderOAuthClient(item) {
  const analysis = item.analysis || {};
  const status = {parsed: "解析済み", partial: "一部未解釈", empty: "scope が空", missing: "scope の記載なし", invalid_type: "scope の形式不正", too_large: "scope が上限超過", unavailable: "解析できませんでした"}[analysis.status] || analysis.status;
  return `<div class="collector"><div>${link(item.metadata_url)} · ${esc(formatTimestamp(item.observed_at))} ${pill(status)}</div>
    ${item.declared_client_uri ? `<div class="muted">申告されたサービスURL: ${link(item.declared_client_uri)}</div>` : ""}
    ${analysis.atproto_only ? `<p>認証用の atproto scope のみ。公開データの読取りは可能です。</p>` : ""}
    ${list(analysis.permissions).map((p) => {
      const target = list(p.collection || p.lxm || p.accept).join(", ") || p.nsid || p.attr || p.raw;
      return `<div class="relation"><strong>${esc(displayLabel(p.kind))}: ${esc(displayLabel(target))}</strong>${p.kind === "include" ? pill("権限セット・未解決", "candidate") : ""}<div class="muted">${p.action ? `操作: ${esc(p.action.map(displayLabel).join("、"))}` : ""}${p.aud ? ` · 宛先: ${esc(p.aud)}` : ""}${p.kind === "transition" ? "移行用の権限。これだけでは用途を特定できません。" : ""}</div></div>`;
    }).join("")}
    ${list(analysis.unrecognized).length ? `<p>未解釈: ${esc(analysis.unrecognized.join(" "))}</p>` : ""}
    ${analysis.error ? `<p>${esc(analysis.error)}</p>` : ""}
    <details><summary>元のscope${item.scope_truncated ? "（長さ上限で省略）" : ""}</summary><pre>${esc(item.scope ?? "記載なし／文字列以外")}</pre></details></div>`;
}

function renderUsageMaterial(material = {}) {
  const sources = list(material.sources);
  const coverage = {collected_html:"取得時のHTML本文から抽出", saved_html_excerpt:"保存済みHTMLの範囲のみ", saved_excerpt:"保存済み画面本文の範囲のみ", saved_readme_excerpt:"保存済みREADME抜粋の範囲のみ", saved_description:"保存済みページ説明文"};
  return `${renderCollectionMaterial(material.collections)}<details><summary>atproto利用の関連記述 (${sources.length})</summary>
    ${sources.length ? `<p class="muted">用語で抽出した原文（否定・予定・他サービスの記述を含む）。主機能での実利用は未判定です。</p>` : ""}
    ${sources.length ? sources.map((source) => `<div class="collector"><strong>${esc(displayLabel(source.kind))}</strong> ${link(source.url || source.locator)}${source.url ? `<div class="muted">${esc(source.locator)}</div>` : ""}<div class="muted">${esc(coverage[source.coverage] || source.coverage)}${source.observed_at ? ` · ${esc(formatTimestamp(source.observed_at))}` : " · 観測時刻不明"}${source.input_truncated || source.matches_omitted ? " · 検索範囲／表示の省略あり" : ""}</div>
      ${source.literal_collections?.length ? `<p>観測済みコレクションと同じ表記: ${esc(source.literal_collections.join(", "))}（意味上の対応は未判定）</p>` : ""}
      ${list(source.passages).map((p) => `<div class="relation"><div class="muted">本文位置 ${esc(p.start)}–${esc(p.end)} · 検索語: ${esc(list(p.matches).join(", "))}</div>${p.before_omitted ? `<div class="muted">前略</div>` : ""}<pre>${esc(p.text)}</pre>${p.after_omitted ? `<div class="muted">後略</div>` : ""}</div>`).join("")}</div>`).join("") : `<p>${material.status === "no_matches" ? "保存済みの材料に検索語が見つかりませんでした。atprotoを使わないという意味ではありません。" : material.status === "text_unavailable" ? "HTMLの観測はありますが、検索できる本文を取り出せませんでした。" : "検索できる本文の観測がありません。"}</p>`}
    ${material.sources_omitted ? `<p>ほか ${esc(material.sources_omitted)} 件の資料を省略しています。</p>` : ""}
    </details>`;
}

function renderCollectionMaterial(catalog = {}) {
  const rows = list(catalog.rows);
  return `<details><summary>コレクションごとの材料 (${rows.length})</summary>
    ${rows.length ? `<p class="muted">同名のコレクションに関する観測です。作成アプリ・所有・主機能との関係は未確認です。</p>` : ""}
    ${rows.map((row) => `<div class="collector"><h4>${esc(row.collection)}</h4>
      <strong>要求する操作（権限の宣言）</strong>${row.permissions.length ? row.permissions.map((p) => `<p>${esc(p.actions.map(displayLabel).join("、"))} · ${link(p.metadata_url)} · ${esc(p.observed_at ? formatTimestamp(p.observed_at) : "観測時刻不明")}</p>`).join("") : "<p>このコレクション名の個別のリポジトリ権限の宣言は未観測</p>"}
      <strong>Lexiconの定義</strong>${row.schemas.length ? row.schemas.map((s) => `<div class="relation"><p>${esc(s.description || s.record_description || "説明文は未保存")}</p>${s.description && s.record_description ? `<p>${esc(s.record_description)}</p>` : ""}<p>レコードキー: ${esc(s.key || "未保存")}</p>
        ${s.fields_status === "saved" ? `<ul>${s.fields.map((f) => `<li>${esc(f.name)}${f.type ? ` (${esc(f.type)})` : ""}${f.description ? ` — ${esc(f.description)}` : ""}</li>`).join("")}</ul>${s.fields_omitted || s.fields_compacted ? `<p>フィールド一覧の一部は省略されています。</p>` : ""}` : "<p>フィールド一覧は保存時に省略、または未保存</p>"}
        <p class="muted">${esc(s.observed_at ? formatTimestamp(s.observed_at) : "観測時刻不明")}</p></div>`).join("") : "<p>レコード型のスキーマは未観測</p>"}
      <strong>レコードの観測</strong>${row.record_observations.map((o) => `<p>観測時の集計: ${esc(o.count ?? "不明")} 件 / 直近 ${esc(o.recent_count ?? "不明")} 件 · ${esc(o.observed_at ? formatTimestamp(o.observed_at) : "観測時刻不明")}</p>`).join("")}
      ${row.record_samples.length ? `<p>保存済みサンプル ${row.record_samples.length} 件</p>${row.record_samples.map((r) => `<div class="relation"><p>${esc(r.did || "DID未保存")} / ${esc(r.rkey || "レコードキー未保存")}</p><p>観測フィールド: ${esc(r.fields.join(", ") || "なし／未保存")}${r.fields_omitted ? "（一部省略）" : ""}</p>${r.declared_type && r.declared_type !== row.collection ? `<p>レコード内の$typeはコレクション名と不一致: ${esc(r.declared_type)}</p>` : ""}</div>`).join("")}` : "<p>保存済みレコードのサンプルなし（レコードが存在しないという意味ではありません）</p>"}
      </div>`).join("") || "<p>個別のコレクションを照合できる観測がありません。</p>"}
    ${rows.length ? `<p class="muted">公開読取りに個別の権限宣言は不要です。ワイルドカード・権限セット・未解釈のscopeは「OAuth scope の宣言」で確認できます。</p>` : ""}
    ${catalog.omitted ? `<p>ほか ${esc(catalog.omitted)} コレクションを省略しています。</p>` : ""}</details>`;
}

function renderServiceOwnership(data, serviceUrls = []) {
  const service = list(data.service_verifications);
  const ownership = list(data.nsid_ownership);
  const permissions = list(data.oauth_permissions);
  const clients = list(data.oauth_clients);
  const historicalPermissions = permissions.filter((p) => !clients.some((c) => c.metadata_url === p.locator));
  const aliases = list(data.url_aliases);
  const targets = uniqueValues(serviceUrls);
  const browserControls = targets.length
    ? `<div class="controls"><label>匿名ブラウザーでの確認対象<select id="browser-service-url">${targets.map((url) => `<option value="${esc(url)}">${esc(url)}</option>`).join("")}</select></label><button id="verify-service-browser" class="primary">匿名ブラウザーで確認</button></div>`
    : `<p class="muted">ブラウザー確認には採用済みまたは候補のサービスURLが必要です。</p>`;
  return `<dl class="kv"><dt>保存済みサービス観測</dt><dd>${pill(data.current_service_status)} ${esc(displayLabel(data.current_service_status_confidence || "none"))}</dd><dt>確認履歴</dt><dd>${esc(displayLabel(data.service_verification_status || "not_observed"))}</dd><dt>帰属の根拠</dt><dd>${esc(displayLabel(data.ownership_signal || "unknown"))}</dd><dt>OAuthメタデータ</dt><dd>${esc(displayLabel(data.oauth_status || "not_observed"))}</dd><dt>URLの別名</dt><dd>${esc(displayLabel(data.url_alias_status || "not_observed"))}</dd></dl>${browserControls}
    ${service.length ? `<details><summary>サービス確認の比較 (${service.length})</summary>${service.map((item) => `<div class="collector">${pill(item.status, item.status)} ${item.application_status === "unapplied" ? pill("未反映（競合）", "candidate") : ""} ${esc(formatTimestamp(item.completed_at))} · ${esc(displayLabel(item.source))}</div>`).join("")}</details>` : ""}
    ${ownership.length ? `<details><summary>NSIDの帰属 (${ownership.length})</summary>${ownership.map((item) => `<div class="relation ${esc(item.classification)}"><strong>${esc(item.nsid)}</strong> ${pill(item.classification, item.classification)}<div class="muted">${esc(displayLabel(item.evidence_status))} · ${esc(item.evidence.map((entry) => `${displayLabel(entry.predicate)}: ${displayLabel(entry.source)}`).join(", ") || "保存済みの帰属関係なし")}</div></div>`).join("")}</details>` : ""}
    ${clients.length ? `<details><summary>OAuth scope の宣言 (${clients.length})</summary><p class="muted">要求権限の宣言です。実際の許可・利用・所有は未確認です。</p>${clients.map(renderOAuthClient).join("")}</details>` : ""}
    ${renderUsageMaterial(data.atproto_material)}
    ${historicalPermissions.length ? `<details><summary>OAuth権限・従来の保存情報 (${historicalPermissions.length})</summary>${historicalPermissions.map((item) => `<div class="relation ${esc(item.classification)}"><strong>${esc(item.nsid)}</strong> ${pill(item.method)}${item.include_unresolved ? pill("権限セット・未解決", "candidate") : ""}<div class="muted">${esc(displayLabel(item.source))}</div></div>`).join("")}</details>` : ""}
    ${aliases.length ? `<details><summary>URLの別名 (${aliases.length})</summary>${aliases.map((item) => `<div class="identifier ${esc(item.confidence || "candidate")}"><div>${link(item.alias)} → ${link(item.canonical)}</div><div class="muted">${esc(item.reason || "観測された別名")}</div><div class="actions">${actionButton("別名URLを除外", "block-identifier", {type:"service_url", value:item.alias}, "small danger")}${actionButton("別名URLを保留", "defer-identifier", {type:"service_url", value:item.alias})}</div></div>`).join("")}</details>` : ""}`;
}

function renderExplanation(topic, item) {
  return `<div data-explain-topic="${esc(topic)}"><details><summary>${esc(displayLabel(topic))}: ${esc(displayLabel(item.conclusion))}</summary><dl class="kv"><dt>判定規則</dt><dd>${esc(displayLabel(item.rule))}</dd><dt>支持する根拠</dt><dd>${list(item.supporting_signals).map((value) => pill(value, "confirmed")).join(" ") || "観測なし"}</dd><dt>反する根拠</dt><dd>${list(item.counter_signals).map((value) => pill(value, "candidate")).join(" ") || "観測なし"}</dd></dl>${item.sources?.length ? json(item.sources) : ""}</details></div>`;
}

function renderExplanations(topics) {
  const entries = Object.entries(topics || {});
  if (!entries.length) return `<p class="muted">判定理由はありません。</p>`;
  return entries.map(([topic, item]) => renderExplanation(topic, item)).join("");
}

async function loadMergeAnalysis(projectKey, revision, explanations) {
  const request = ++state.mergeAnalysisRequest;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectKey)}/merge-analysis`);
    if (request !== state.mergeAnalysisRequest || state.selected !== projectKey || state.detailRevision !== revision) return;
    if (data.revision !== revision) {
      $("merge-analysis-content").innerHTML = `<p class="muted">プロジェクトが変更されました。詳細を再読込してマージ候補を再計算してください。</p>`;
      return;
    }
    const recommendation = data.merge_recommendation || {};
    const candidates = list(data.duplicates);
    const target = candidates.find((item) => item.project_key === recommendation.target_project_key);
    const otherCandidates = candidates.filter((item) => item.project_key !== recommendation.target_project_key).slice(0, 5);
    state.mergeTargetRevision = data.merge_target_revision || "";
    $("merge-analysis-content").innerHTML = recommendation.target_project_key
      ? `<dl class="kv"><dt>候補</dt><dd>${esc(target?.name || recommendation.target_project_key)} <span class="muted">${esc(recommendation.target_project_key)}</span></dd><dt>確信度</dt><dd>${pill(recommendation.merge_confidence || "unknown")} 点数 ${esc(recommendation.score || 0)}</dd><dt>自動マージ</dt><dd>${recommendation.auto_merge_ready ? pill("ready", "confirmed") : "手動レビューが必要"}</dd></dl><div class="actions"><button class="small" data-view-project="${esc(recommendation.target_project_key)}">候補の詳細を見る</button><button class="small" data-review-merge="${esc(recommendation.target_project_key)}">マージ前に比較</button></div>${otherCandidates.length ? `<details><summary>その他の候補 (${candidates.length - 1})</summary>${otherCandidates.map((item) => `<div class="identifier"><div>${esc(item.name || item.project_key)} <span class="muted">${esc(item.project_key)}</span></div><div>${pill(item.merge_confidence || "unknown")} 点数 ${esc(item.score || 0)}</div><div class="actions"><button class="small" data-view-project="${esc(item.project_key)}">表示</button><button class="small" data-review-merge="${esc(item.project_key)}">比較</button></div></div>`).join("")}</details>` : ""}`
      : `<p class="muted">マージ候補はありません。</p>`;
    document.querySelectorAll("[data-view-project]").forEach((button) => button.onclick = () => loadDetail(button.dataset.viewProject));
    document.querySelectorAll("[data-review-merge]").forEach((button) => button.onclick = () => loadMergeDetail(projectKey, button.dataset.reviewMerge, {backTarget: {type: "project", key: projectKey}}));
    if (recommendation.target_project_key) {
      $("merge-analysis-action").innerHTML = `<button id="compare-merge-recommendation" class="secondary">マージ候補を比較</button>`;
      $("compare-merge-recommendation").onclick = () => loadMergeDetail(projectKey, recommendation.target_project_key, {backTarget: {type: "project", key: projectKey}});
    }
    const mergeExplanation = data.explanation || {};
    explanations.merge_candidate = mergeExplanation;
    const current = document.querySelector('[data-explain-topic="merge_candidate"]');
    if (current) current.outerHTML = renderExplanation("merge_candidate", mergeExplanation);
  } catch (error) {
    if (request !== state.mergeAnalysisRequest || state.selected !== projectKey) return;
    const target = $("merge-analysis-content");
    if (target) target.innerHTML = `<p class="notice">${esc(error.message)}</p>`;
  }
}

async function loadInvestigationHistory(projectKey) {
  const target = $("investigation-history");
  target.innerHTML = `<p class="muted">調査履歴を読込中…</p>`;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectKey)}/investigation-runs?limit=50`);
    target.innerHTML = list(data.items).length ? `<div>${data.items.map((run) => `<button class="card" data-investigation-run="${esc(run.run_id)}"><span class="card-title"><strong>${esc(formatTimestamp(run.completed_at))}</strong>${pill(run.application_status === "unapplied" ? "未反映（競合）" : run.problem_count ? "problems" : "complete", run.application_status === "unapplied" || run.problem_count ? "candidate" : "confirmed")}</span><span class="muted">${esc(run.evaluation_version)} · ${esc(run.collector_count)} 収集処理${run.total_ms ? ` · ${esc(formatMilliseconds(run.total_ms))}` : ""}</span></button>`).join("")}</div><div id="investigation-run-detail"></div>` : `<p class="muted">保存済みの調査履歴はありません。</p>`;
    target.querySelectorAll("[data-investigation-run]").forEach((button) => button.onclick = () => loadInvestigationRun(button.dataset.investigationRun));
  } catch (error) { status(error.message, true); }
}

async function loadInvestigationRun(runId) {
  const target = $("investigation-run-detail");
  target.innerHTML = `<p class="muted">調査実行記録を読込中…</p>`;
  try {
    const run = await api(`/api/investigation-runs/${encodeURIComponent(runId)}?evidence_limit=100`);
    target.innerHTML = section("調査実行記録の詳細", `<dl class="kv"><dt>調査実行記録</dt><dd>${esc(run.run_id)}</dd><dt>保存状態</dt><dd>${esc(run.application_status === "unapplied" ? "未反映（競合）" : "反映済み")}</dd>${run.conflict_reason ? `<dt>競合理由</dt><dd>${esc(run.conflict_reason)}</dd>` : ""}<dt>記録件数</dt><dd>${esc(run.record_count)}${run.records_truncated ? "（先頭100件を表示）" : ""}</dd><dt>失敗</dt><dd>${esc(run.failure_count)}</dd><dt>評価</dt><dd>${esc(run.evaluation_version)}</dd></dl>${section("所要時間の内訳", renderRunMetrics(run.metrics))}${section("収集処理", run.collectors.map((item) => `<div class="collector">${pill(item.status, item.status)} ${esc(displayLabel(item.source))} · ${esc(item.record_count)} 件の記録 · ${esc(item.failure_count)} 件の失敗</div>`).join("") || `<p class="muted">収集処理の記録はありません。</p>`)}<details><summary>根拠（JSON）</summary>${json(run.records)}</details><details><summary>失敗</summary>${json(run.failures)}</details><details><summary>入力・出力・評価（JSON）</summary>${json({input_project:run.input_project, output_project:run.output_project, evaluation:run.evaluation})}</details><div class="actions"><button id="replay-investigation-run">現在の規則で再評価</button></div><div id="investigation-replay-result"></div>`);
    $("replay-investigation-run").onclick = async () => {
      try {
        const replay = await api(`/api/investigation-runs/${encodeURIComponent(runId)}/replay`);
        $("investigation-replay-result").innerHTML = section("再評価の差分（JSON）", json(replay));
      } catch (error) { status(error.message, true); }
    };
  } catch (error) { status(error.message, true); }
}

function reviewTargetActions(target) {
  if (!target) return "";
  const data = {"target-type":target.target_type, "target-value":target.target_value};
  return `${actionButton("レビュー対象から除外", "dismiss-review-target", data)}${actionButton("30日保留", "defer-review-target", data)}`;
}

function renderMultiTenantSuffixSuggestions(suggestions) {
  const items = list(suggestions);
  if (!items.length) return "";
  return section("共用ホスティングの接尾辞候補", items.map((item) => `
    <div class="suffix-suggestion">
      <div class="suffix-suggestion-main">
        <div>${pill("suggested", "candidate")} <strong>${esc(item.suffix)}</strong></div>
        <div class="muted">マージ元の利用者: ${esc(list(item.source_tenants).join(", ") || "none")}</div>
        <div class="muted">マージ先の利用者: ${esc(list(item.target_tenants).join(", ") || "none")}</div>
        <div class="muted">ホスト: ${esc(list(item.source_hosts).join(", ") || "none")} → ${esc(list(item.target_hosts).join(", ") || "none")}</div>
      </div>
      <div class="actions"><button class="small" data-add-multi-tenant-suffix="${esc(item.suffix)}">接尾辞を追加</button></div>
    </div>
  `).join(""));
}

function renderProfileFields(profile) {
  return `<dl class="kv"><dt>短い説明</dt><dd>${esc(profile.tagline || "なし")}</dd><dt>説明</dt><dd>${esc(profile.summary || "なし")}</dd><dt>特徴</dt><dd>${list(profile.features).map(esc).join("<br>") || "なし"}</dd></dl>`;
}

function renderProfileChanges(changes) {
  const value = (item) => Array.isArray(item) ? item.map(esc).join("<br>") : esc(typeof item === "boolean" ? (item ? "自動反映する" : "更新案を確認する") : item || "なし");
  const labels = {tagline:"短い説明", summary:"説明", features:"特徴", name:"名前", notes:"メモ", introduced_url:"紹介済み投稿のURL", automatic_description:"説明の更新方法"};
  return changes.map((change) => `<h4>${esc(labels[change.field] || change.field)}</h4><dl class="kv"><dt>変更前</dt><dd>${value(change.before)}</dd><dt>変更後</dt><dd>${value(change.after)}</dd></dl>`).join("");
}

function renderServiceObservations(observations) {
  if (!list(observations).length) return `<p class="muted">保存済みの確認記録はありません。</p>`;
  return `<p class="muted">保存時点の観測です。現在の利用可否や認証後の機能は未確認です。</p>${list(observations).some((item) => item.fetch_error) ? `<p class="muted">取得失敗は提供終了を示しません。</p>` : ""}${list(observations).map((item) => `<div class="collector"><strong>${link(item.requested_url)}</strong> · ${esc(displayLabel(item.lane))}<dl class="kv"><dt>最新の試行</dt><dd>${esc(formatTimestamp(item.observed_at) || "時点未記録")} · ${esc(item.fetch_error ? "取得失敗" : item.surface_confirmed ? "利用面を確認" : "利用面は未確認")}${item.status_code ? ` (HTTP ${esc(item.status_code)})` : ""}</dd><dt>最後に利用面を確認</dt><dd>${esc(item.last_success?.observed_at ? formatTimestamp(item.last_success.observed_at) : "時点未記録／成功記録なし")}</dd>${item.content_changed ? `<dt>ページの変化</dt><dd>保存範囲の内容が変化しました。説明への影響は未確認です。</dd>` : ""}${item.surface_change ? `<dt>観測の変化</dt><dd>${esc(item.surface_change === "surface_reached" ? "利用面へ到達。説明や追跡方針の見直し材料です。" : "以前の利用面を今回確認できませんでした。")}</dd>` : ""}</dl></div>`).join("")}`;
}

function editProfile(project, profile) {
  const dialog = $("profile-dialog");
  const expectedRevision = state.detailRevision;
  $("profile-name").value = project.name || "";
  $("profile-tagline").value = profileValue(profile, "tagline");
  $("profile-summary").value = profileValue(profile, "summary");
  $("profile-features").value = list(project.features).join("\n");
  $("profile-automatic").checked = Boolean(project.editorial?.automatic);
  const proposal = project.editorial?.proposal;
  $("profile-proposal").innerHTML = proposal ? section("説明の更新案", `<p>${esc(proposal.reason)}</p><p class="muted">${esc(formatTimestamp(proposal.generated_at))} · ${esc(({correction:"誤記の訂正", changed_facts:"事実の変化", expansion:"説明の補充"})[proposal.kind] || "説明の更新")}</p>${renderProfileFields(proposal.profile)}${list(proposal.sources).map((source) => `<details><summary>${esc(source.locator)}</summary><p>${esc(source.content)}</p></details>`).join("")}<button id="profile-use-proposal" type="button">更新案を編集欄へ読み込む</button><button id="profile-dismiss-proposal" type="button">この更新案を見送る</button>`) : "";
  $("profile-dismiss-proposal")?.addEventListener("click", async () => {
    try {
      const response = await api(`/api/projects/${encodeURIComponent(project.project_key)}/dismiss-description-proposal`, {method:"POST", body:JSON.stringify({expected_revision:expectedRevision})});
      dialog.close();
      renderDetail(response.detail);
      await loadList();
    } catch (error) { status(error.message, true); }
  });
  $("profile-use-proposal")?.addEventListener("click", () => {
    $("profile-tagline").value = proposal.profile.tagline;
    $("profile-summary").value = proposal.profile.summary;
    $("profile-features").value = list(proposal.profile.features).join("\n");
    $("profile-automatic").checked = false;
    $("profile-apply").disabled = true;
    preview = null;
  });
  $("profile-introduced-url").value = project.introduced_url || "";
  $("profile-notes").value = profileValue(profile, "notes");
  $("profile-diff").innerHTML = "";
  $("profile-apply").disabled = true;
  let preview = null;
  const values = () => ({
    name:$("profile-name").value,
    introduced_url:$("profile-introduced-url").value,
    notes:$("profile-notes").value,
    description:{tagline:$("profile-tagline").value, summary:$("profile-summary").value,
      features:$("profile-features").value.split("\n").map((value) => value.trim()).filter(Boolean)},
    automatic_description:$("profile-automatic").checked,
    expected_revision:expectedRevision
  });
  for (const id of ["profile-name", "profile-tagline", "profile-summary", "profile-features", "profile-notes", "profile-introduced-url", "profile-automatic"]) {
    $(id).oninput = () => {
      preview = null;
      $("profile-apply").disabled = true;
      $("profile-diff").innerHTML = "";
      if (["profile-tagline", "profile-summary", "profile-features"].includes(id)) $("profile-automatic").checked = false;
    };
  }
  $("profile-preview").onclick = async () => {
    try {
      const response = await api(`/api/projects/${encodeURIComponent(project.project_key)}/profile-preview`, {method:"POST", body:JSON.stringify(values())});
      preview = response.result;
      $("profile-diff").innerHTML = preview.changed ? section("変更内容", renderProfileChanges(preview.changes)) : `<p class="muted">変更はありません。</p>`;
      $("profile-apply").disabled = !preview.changed;
    } catch (error) { status(error.message, true); }
  };
  $("profile-apply").onclick = async () => {
    if (!preview?.changed) return;
    try {
      const response = await api(`/api/projects/${encodeURIComponent(project.project_key)}/profile`, {method:"POST", body:JSON.stringify(values())});
      dialog.close();
      renderDetail(response.detail);
      await loadList();
      status("紹介情報を保存しました。");
    } catch (error) {
      if (error.code === "stale_project") {
        dialog.close();
        await loadDetail(project.project_key, {updateUrl:false});
      }
      status(error.message, true);
    }
  };
  showDialogWithoutInputFocus(dialog);
}

function parseSeedInput(value) {
  const raw = String(value || "").trim();
  const match = raw.match(/^(nsid|repo_url|service_url)\s*:\s*(.+)$/i) || raw.match(/^(nsid|repo_url|service_url)\s+(.+)$/i);
  if (!match) return {seed: raw, type: ""};
  return {seed: match[2].trim(), type: match[1].toLowerCase()};
}

async function addProjectSeed(projectKey) {
  const input = prompt("NSID・リポジトリURL・サービスURLを入力してください。種類を指定するには repo_url: https://... のように入力します。");
  const parsed = parseSeedInput(input);
  if (!parsed.seed) return;
  try {
    const typeQuery = parsed.type ? `&type=${encodeURIComponent(parsed.type)}` : "";
    const preview = await api(`/api/seeds/preview?seed=${encodeURIComponent(parsed.seed)}${typeQuery}`);
    if (preview.exact_project_key && preview.exact_project_key !== projectKey) throw new Error(`このシードは${preview.exact_project_key}に登録済みです。`);
    const action = preview.exact_project_key === projectKey ? "再適用" : "追加";
    confirmMutation("識別子を追加", `${projectKey}へ${displayLabel(preview.seed_type)}「${preview.seed_value}」を${action}しますか？`, "/api/seeds", {seed:parsed.seed, type:parsed.type, project_key:projectKey, expected_revision:state.detailRevision}, false);
  } catch (error) { status(error.message, true); }
}

function bindDetailActions(projectKey) {
  $("save-atproto-usage")?.addEventListener("click", () => {
    const reason = $("atproto-usage-reason").value.trim();
    if (!reason) { status("判断根拠を入力してください。", true); return; }
    confirmMutation("利用範囲の判断を保存", "この判断を推薦に反映します。サービスの稼働状態、技術的価値、候補の優先度は変えません。",
      `/api/projects/${encodeURIComponent(projectKey)}/atproto-usage`, {role:$("atproto-usage-role").value, reason}, false);
  });
  $("clear-atproto-usage")?.addEventListener("click", () => confirmMutation(
    "手動判断を解除", "利用範囲を未評価へ戻します。解除した判断は履歴に残ります。",
    `/api/projects/${encodeURIComponent(projectKey)}/atproto-usage`, {clear:true}, false));
  document.querySelectorAll("[data-action]").forEach((button) => button.onclick = () => {
    const action = button.dataset.action;
    const payload = {identifier_type: button.dataset.type || "", value: button.dataset.value || "", other_project_key: button.dataset.other || "", target_type: button.dataset.targetType || "", target_value: button.dataset.targetValue || "", review_action: button.dataset.reviewAction || "", days: button.dataset.days || ""};
    const needsNote = ["reject-candidate", "block-identifier", "defer-identifier", "split-project", "defer-promotion", "dismiss-review-target", "defer-review-target", "lower-promotion-priority"].includes(action);
    const title = action === "defer-promotion" ? `紹介候補を${payload.days}日間、保留にする` : action === "lower-promotion-priority"
      ? "紹介候補の優先度を下げる"
      : action === "clear-promotion-priority"
        ? "紹介候補の優先度を通常へ戻す"
        : displayLabel(action);
    const message = action === "defer-promotion"
      ? `この候補を${payload.days}日間、保留にします。期限または根拠の変化で再表示します。「保留」からいつでも解除できます。`
      : ["lower-promotion-priority", "clear-promotion-priority"].includes(action)
      ? `${projectKey}: ${title}。よろしいですか？`
      : `${payload.value || payload.other_project_key || projectKey}: ${title}。よろしいですか？`;
    confirmMutation(
      title,
      message,
      `/api/projects/${encodeURIComponent(projectKey)}/${action}`,
      payload,
      needsNote,
      button.dataset.defaultNote || "",
      action === "lower-promotion-priority" ? "特集の共通理由・優先度のメモ（任意・あとで検索できます）" : action === "defer-promotion" ? "保留の理由（任意）" : "理由・メモ",
    );
  });
}

function confirmMutation(title, message, path, payload, note, defaultNote = "", noteLabel = "理由・メモ") {
  const dialog = $("confirm-dialog");
  $("confirm-context").textContent = "データベースへの変更を確認";
  $("confirm-submit").textContent = "変更を適用";
  $("confirm-submit").className = "danger";
  if (path.startsWith("/api/projects/")) payload.expected_revision = state.detailRevision;
  if (path.endsWith("/merge-recommendation")) payload.expected_target_revision = state.mergeTargetRevision;
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-note-label").hidden = !note;
  $("confirm-note-text").textContent = noteLabel;
  $("confirm-note").value = note ? defaultNote : "";
  showDialogWithoutInputFocus(dialog);
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    if (note) payload.note = $("confirm-note").value.trim();
    mutationQueue = mutationQueue.then(async () => {
      try {
        status("保存中…");
        const response = await api(path, {method:"POST", body:JSON.stringify(payload)});
        if (advanceReviewListAfterMutation(response, path)) {
          return;
        }
        status("保存しました。");
        if (response.detail) {
          renderDetail(response.detail);
        } else if (response.project_key || response.target_project_key) {
          await loadDetail(response.target_project_key || response.project_key);
        } else {
          document.body.classList.remove("detail-open");
        }
        await loadList();
      } catch (error) {
        if (error.code === "stale_project" && state.selected) {
          await loadDetail(state.selected, {updateUrl: false});
          status("別の操作でプロジェクトが変更されました。詳細を再読込したので、変更を確認してから再操作してください。", true);
        } else {
          status(error.message, true);
        }
      }
    }, async () => {}).catch(() => {});
  };
}

async function loadMergeDetail(source, target, options = {}) {
  const request = ++state.detailRequest;
  state.selected = source; document.body.classList.add("detail-open");
  const includeFirstCreator = Boolean(options.includeFirstCreator);
  state.detailBackTarget = Object.prototype.hasOwnProperty.call(options, "backTarget")
    ? options.backTarget
    : includeFirstCreator ? state.detailBackTarget : null;
  syncUrl();
  if (!includeFirstCreator) resetDetailScroll();
  try {
    const data = await api(`/api/merge-pair?source_project_key=${encodeURIComponent(source)}&target_project_key=${encodeURIComponent(target)}${includeFirstCreator ? "&include_first_creator_details=1" : ""}`);
    if (request !== state.detailRequest || state.selected !== source) return;
    state.mergeSourceRevision = data.source_revision || "";
    state.mergeTargetRevision = data.target_revision || "";
    const snapshot = (project, side) => {
      const identifiers = list(project.identifiers);
      return `<article class="merge-project"><div class="card-title"><strong>${esc(project.name || project.project_key)}</strong>${pill(project.tracking_state)}</div><div class="muted">${esc(project.project_key)}</div><dl class="kv"><dt>公式サイト</dt><dd>${project.website_url ? link(project.website_url) : "なし"}</dd><dt>状態</dt><dd>${pill(project.service_status)} ${pill(project.atproto_contribution_level)}</dd><dt>シード</dt><dd>${esc(`${displayLabel(project.seed?.type || "")}: ${project.seed?.value || ""}`)}</dd>${projectTimestampRows(project)}</dl><div class="merge-identifiers"><strong>採用済み識別子</strong>${mergeIdentifierRows(identifiers, "confirmed")}<strong>候補</strong>${mergeIdentifierRows(identifiers, "candidate")}</div><button class="small" data-merge-view="${esc(side)}">詳細を開く</button></article>`;
    };
    const firstCreatorBody = data.first_creator_details_included
      ? `<dl class="kv"><dt>一致</dt><dd>${json(data.first_creator_overlap || {})}</dd><dt>マージ元の初回作成者DID</dt><dd>${json(data.source_collection_first_creator_dids || {})}</dd><dt>マージ先の初回作成者DID</dt><dd>${json(data.target_collection_first_creator_dids || {})}</dd><dt>マージ元の照会結果</dt><dd>${json(data.source_collection_first_creator_details || {})}</dd><dt>マージ先の照会結果</dt><dd>${json(data.target_collection_first_creator_details || {})}</dd></dl><p class="muted">補足情報です。この比較画面の主要な候補判定には混ぜません。listReposByCollectionから初回作成者が得られない場合、照会結果にnot_foundまたはerrorを表示します。</p>`
      : `<p class="muted">未取得です。listReposByCollectionへ照会するため、時間がかかる場合があります。</p><div class="actions"><button id="load-first-creator-heuristic" class="secondary">初回作成者の情報を取得</button></div>`;
    const mergeActions = `<div class="actions"><button id="skip-review-item" class="secondary">今回は先送り</button><button id="mark-separate">別プロジェクトとして保持</button><button id="execute-merge" class="danger">プロジェクトをマージ</button></div>`;
    $("detail").innerHTML = `${backToListButton()}${contextualBackButton()}<p class="eyebrow">マージ候補の比較</p><h2>${esc(data.source?.name || source)} → ${esc(data.target?.name || target)}</h2><div class="actions"><button id="reverse-merge-direction" class="secondary">マージ元とマージ先を入れ替える</button></div><div class="merge-comparison">${snapshot(data.source, "source")}${snapshot(data.target, "target")}</div>${section("候補と判断された理由", `<dl class="kv"><dt>マージの制限</dt><dd>${data.merge_blocked ? pill("blocked", "failed") : "別プロジェクト指定なし"}</dd><dt>厳密な一致</dt><dd>${json(data.strict_overlap || {})}</dd><dt>標準条件での一致</dt><dd>${json(data.moderate_overlap || {})}</dd><dt>マージ元の判定材料</dt><dd>${json(data.source_pair_candidate || {})}</dd><dt>マージ先の判定材料</dt><dd>${json(data.target_pair_candidate || {})}</dd></dl>`)}${renderMultiTenantSuffixSuggestions(data.suggested_multi_tenant_suffixes)}${section("初回作成者による推測", firstCreatorBody)}${section("マージ結果のプレビュー", `<dl class="kv"><dt>名前</dt><dd>${esc(data.merged_preview?.merged_name || "")}</dd><dt>公式サイト</dt><dd>${link(data.merged_preview?.merged_website_url || "")}</dd><dt>サービス状態</dt><dd>${pill(data.merged_preview?.merged_service_status || "unknown")}</dd><dt>リポジトリ</dt><dd>${list(data.merged_preview?.merged_project_repository_urls).map(link).join("<br>") || "なし"}</dd><dt>サービス</dt><dd>${list(data.merged_preview?.merged_service_urls).map(link).join("<br>") || "なし"}</dd></dl>`)}${mergeActions}<div class="detail-footer">${backToListButton()}${contextualBackButton()}</div>`;
    bindBackToListButtons();
    document.querySelectorAll("[data-merge-view]").forEach((button) => button.onclick = () => loadDetail(button.dataset.mergeView === "source" ? source : target, {backTarget: {type: "merge", source, target, previous: state.detailBackTarget}}));
    document.querySelectorAll("[data-add-multi-tenant-suffix]").forEach((button) => button.onclick = () => addMultiTenantSuffix(button.dataset.addMultiTenantSuffix, source, target));
    $("reverse-merge-direction")?.addEventListener("click", () => loadMergeDetail(target, source, {
      includeFirstCreator,
      backTarget: state.detailBackTarget,
    }));
    $("load-first-creator-heuristic")?.addEventListener("click", () => loadMergeDetail(source, target, {includeFirstCreator: true}));
    $("skip-review-item")?.addEventListener("click", () => skipReviewItem(source, target));
    $("mark-separate")?.addEventListener("click", () => confirmMerge("別プロジェクトとして保持", "separate", source, target));
    $("execute-merge")?.addEventListener("click", () => confirmMerge("プロジェクトをマージ", "merge", source, target));
  } catch (error) {
    if (request === state.detailRequest && state.selected === source) status(error.message, true);
  }
}

function addMultiTenantSuffix(suffix, source, target) {
  confirmOperation("共用ホスティングの接尾辞を追加", `共用ホスティングの接尾辞として${suffix}を追加しますか？ 今後のマージ候補計算で、ドメインの一致判定が変わります。`, async () => {
    const response = await api("/api/domain-overrides/multi-tenant-suffixes", {method:"POST", body:JSON.stringify({suffix})});
    const prefix = response.added ? `共用ホスティングの接尾辞${response.suffix}を追加しました。` : `共用ホスティングの接尾辞${response.suffix}は登録済みです。`;
    if (advanceReviewList(source, {targetKey: target, statusPrefix: prefix})) return;
    status(`${prefix} 一覧を再読込します。`);
    await loadList();
  });
}

function confirmMerge(title, action, source, target) {
  confirmMutation(title, `${title}: ${source} → ${target}。よろしいですか？`, "/api/merge-batch/action", {
    action,
    source_project_key: source,
    target_project_key: target,
    expected_source_revision: state.mergeSourceRevision,
    expected_target_revision: state.mergeTargetRevision,
  }, false);
}

function renderNewSeed() {
  state.detailRequest += 1;
  state.selected = "";
  $("detail").innerHTML = `${backToListButton()}<p class="eyebrow">登録プロジェクト</p><h2>シードを登録</h2><div class="controls"><label>NSID・リポジトリURL・サービスURL<input id="seed-value" type="text"></label><div class="actions"><button id="preview-seed" class="primary">プレビュー</button></div></div><div id="seed-preview"></div><div class="detail-footer">${backToListButton()}</div>`;
  document.body.classList.add("detail-open");
  bindBackToListButtons();
  $("preview-seed").onclick = async () => {
    const value = $("seed-value").value.trim();
    try {
      const data = await api(`/api/seeds/preview?seed=${encodeURIComponent(value)}`);
      $("seed-preview").innerHTML = section("プレビュー", json(data)) + (data.can_create ? `<button id="create-seed" class="primary">プロジェクトを登録</button>` : `<p class="notice">このシードは${esc(data.exact_project_key)}に登録済みです。</p>`);
      $("create-seed")?.addEventListener("click", () => confirmMutation("プロジェクトを登録", `${value}をシードとしてプロジェクトを登録しますか？`, "/api/seeds", {seed:value}, false));
    } catch (error) { status(error.message, true); }
  };
}

function renderOperations() {
  $("single-pane").innerHTML = `<p class="eyebrow">バックグラウンドジョブ</p><h2>処理・定期実行</h2><div class="operation-grid">
    <article class="operation-card operation-enrich"><h3>調査</h3><div class="controls">
      <label>対象<select id="enrich-target"><option value="all">通常追跡中のプロジェクト</option><option value="project">1件のプロジェクト</option></select></label>
      <label id="enrich-project-label" hidden>プロジェクトキー<input id="enrich-project-key" placeholder="proj:..."></label>
      <div class="check-options" role="group" aria-label="調査オプション">
        <label class="check-option"><input id="enrich-deep" type="checkbox"> 詳細調査</label>
        <label class="check-option"><input id="enrich-new-only" type="checkbox"> 未調査のみ</label>
        <label class="check-option"><input id="enrich-retry" type="checkbox"> 前回失敗分のみ</label>
      </div>
      <details class="tracking-state-options"><summary>追加する追跡状態</summary><div class="checkbox-grid">
        <label><span><input id="enrich-stable" type="checkbox"> 安定稼働</span></label>
        <label><span><input id="enrich-stalled" type="checkbox"> 活動停滞</span></label>
        <label><span><input id="enrich-out-of-scope" type="checkbox"> 調査範囲外</span></label>
        <label><span><input id="enrich-archived" type="checkbox"> アーカイブ</span></label>
        <label><span><input id="enrich-ignore" type="checkbox"> 追跡対象外</span></label>
      </div></details>
      <button id="start-enrich" class="primary">調査を開始</button>
    </div><code>project-registry enrich --all</code></article>
    <article class="operation-card"><h3>外部情報の取り込み</h3><p>候補の確認にも外部通信を行います。保存は下のチェックで指定します。</p><div class="controls">
      <label>情報源<select id="sync-source"><option value="introduced">紹介済み投稿</option><option value="github">GitHubのリポジトリ検索</option><option value="collections">コレクション一覧</option><option value="semble">Sembleのコレクション</option><option value="tangled">Tangledのリポジトリ</option></select></label>
      <label>初回に遡る日数<input id="sync-lookback" type="number" min="1" max="3650" value="10"></label>
      <div class="check-options" role="group" aria-label="取り込みオプション">
        <label class="check-option"><input id="sync-create" type="checkbox"> 未登録のプロジェクトを新規登録</label>
        <label class="check-option"><input id="sync-screen" type="checkbox"> 未登録のGitHubリポジトリを選別</label>
        <label class="check-option"><input id="sync-all-history" type="checkbox"> 保存済みの取得位置を使わずに取得</label>
        <label class="check-option"><input id="sync-execute" type="checkbox"> 変更をSQLiteへ保存</label>
      </div>
      <button id="start-sync" class="primary">取り込みを開始</button>
    </div><code>project-registry sync-&lt;source&gt; [--execute]</code></article>
    <article class="operation-card"><h3>追跡状態の見直し</h3><p>保存済みの情報から変更候補を確認します（通信なし）。</p><div class="controls">
      <label>レビュー<select id="review-kind"><option value="stalled">活動停滞・安定稼働</option><option value="out_of_scope">調査範囲外</option><option value="ignore">無関係・対象を特定できないもの</option></select></label>
      <label>候補の件数上限<input id="review-limit" type="number" min="1" max="500" value="50"></label>
      <label id="review-inactivity-label">活動なしとする日数<input id="review-inactivity" type="number" min="1" max="3650" value="45"></label>
      <label id="review-freshness-label">調査結果の有効日数<input id="review-freshness" type="number" min="1" max="3650" value="30"></label>
      <label>追跡状態のメモ<input id="review-note" placeholder="変更時のメモ（任意）"></label>
      <button id="start-review" class="primary">変更候補を確認</button>
    </div><code>project-registry review-&lt;kind&gt; [--execute]</code></article>
    <article class="operation-card"><h3>調査の再評価</h3><p>保存済みの根拠を使い、通信せず再評価・比較できます。</p><code>project-registry replay-investigation -p proj:...</code><code>project-registry benchmark-investigation</code></article>
  </div>${section("定期実行", `<div id="schedule-list"><p class="muted">読込中…</p></div>`)}${section("保存済みの再開地点", `<div id="checkpoint-list"><p class="muted">読込中…</p></div>`)}${section("最近のジョブ", `<div id="job-list"><p class="muted">読込中…</p></div>`)}`;
  $("enrich-target").onchange = () => { $("enrich-project-label").hidden = $("enrich-target").value !== "project"; };
  $("sync-source").onchange = updateSyncControls;
  $("review-kind").onchange = updateReviewControls;
  $("start-enrich").onclick = startEnrichJob;
  $("start-sync").onclick = startSyncJob;
  $("start-review").onclick = startReviewJob;
  updateSyncControls();
  updateReviewControls();
  loadJobs();
}

function confirmOperation(title, message, callback, {write = true} = {}) {
  const dialog = $("confirm-dialog");
  $("confirm-context").textContent = write ? "データベースへの変更を確認" : "操作を確認";
  $("confirm-submit").textContent = write ? "変更を適用" : "ジョブを開始";
  $("confirm-submit").className = write ? "danger" : "primary";
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-note-label").hidden = true;
  $("confirm-note").value = "";
  showDialogWithoutInputFocus(dialog);
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    try { await callback(); } catch (error) { status(error.message, true); }
  };
}

function startEnrichJob() {
  const oneProject = $("enrich-target").value === "project";
  const projectKey = $("enrich-project-key").value.trim();
  if (oneProject && !projectKey) { status("プロジェクトキーを入力してください。", true); return; }
  startEnrich({
    project_key: oneProject ? projectKey : "",
    all_projects: !oneProject,
    deep_investigation: $("enrich-deep").checked,
    new_only: !oneProject && $("enrich-new-only").checked,
    retry_failures: !oneProject && $("enrich-retry").checked,
    include_stable: !oneProject && $("enrich-stable").checked,
    include_stalled: !oneProject && $("enrich-stalled").checked,
    include_out_of_scope: !oneProject && $("enrich-out-of-scope").checked,
    include_archived: !oneProject && $("enrich-archived").checked,
    include_ignore: !oneProject && $("enrich-ignore").checked,
  }, oneProject ? projectKey : "選択したプロジェクト群");
}

function startProjectEnrich(projectKey) {
  startEnrich({project_key: projectKey, all_projects: false}, projectKey);
}

function startBrowserServiceVerification(projectKey, serviceUrl) {
  if (!serviceUrl) { status("保存済みのサービスURLを指定してください。", true); return; }
  confirmOperation("匿名ブラウザーでサービスを確認", `${serviceUrl}をログインせずに開き、画面から得た根拠をSQLiteへ保存しますか？`, async () => {
    status("匿名ブラウザー確認を開始中…");
    await api("/api/jobs/verify-service", {method:"POST", body:JSON.stringify({project_key:projectKey, service_url:serviceUrl})});
    status("ブラウザー確認を開始しました。「処理・定期実行」で進捗を確認できます。");
  });
}

function startEnrich(payload, target) {
  confirmOperation("調査を開始", `${target}の調査を開始しますか？ 調査結果をSQLiteへ保存します。`, async () => {
    status("調査を開始中…");
    await api("/api/jobs/enrich", {method:"POST", body:JSON.stringify(payload)});
    status("調査を開始しました。");
    if (state.screen === "operations") await loadJobs();
  });
}

function updateSyncControls() {
  const source = $("sync-source").value;
  $("sync-screen").disabled = source !== "github";
  if (source !== "github") $("sync-screen").checked = false;
  if (source === "introduced") $("sync-lookback").value = "7";
  else if (source === "semble") $("sync-lookback").value = "14";
  else $("sync-lookback").value = "10";
  if (source === "tangled") $("sync-create").checked = true;
}

function startSyncJob() {
  const source = $("sync-source").value;
  const execute = $("sync-execute").checked;
  const payload = {
    source,
    execute,
    create_unmatched: $("sync-create").checked,
    screen_unmatched: $("sync-screen").checked,
    all_history: $("sync-all-history").checked,
    lookback_days: Number($("sync-lookback").value || 10),
  };
  const effect = execute ? "外部情報を取得し、採用された変更をSQLiteへ保存します。" : "外部情報を取得し、取り込み候補を表示します。SQLiteは更新しません。";
  confirmOperation("取り込みを開始", `${source === "introduced" ? "紹介済み投稿" : displayLabel(source)}からの取り込みを開始しますか？ ${effect}`, async () => {
    status("取り込みを開始中…");
    await api("/api/jobs/sync", {method:"POST", body:JSON.stringify(payload)});
    status("取り込みを開始しました。");
    await loadJobs();
  }, {write: execute});
}

function updateReviewControls() {
  const stalled = $("review-kind").value === "stalled";
  $("review-inactivity-label").hidden = !stalled;
  $("review-freshness-label").hidden = !stalled;
}

function startReviewJob() {
  const kind = $("review-kind").value;
  const payload = {
    kind,
    execute:false,
    limit: Number($("review-limit").value || 100),
    inactivity_days: Number($("review-inactivity").value || 45),
    max_enrich_age_days: Number($("review-freshness").value || 30),
    note: $("review-note").value.trim(),
  };
  confirmOperation("変更候補を確認", `保存済み情報から「${displayLabel(kind)}」への変更候補を計算しますか？ SQLiteは更新しません。`, async () => {
    status("追跡状態の見直しを開始中…");
    await api("/api/jobs/review", {method:"POST", body:JSON.stringify(payload)});
    status("追跡状態の見直しを開始しました。");
    await loadJobs();
  }, {write: false});
}

function renderJobs(items) {
  if (!items.length) return `<p class="muted">このサーバープロセスで実行したジョブはありません。</p>`;
  return items.map((job) => {
    const progress = job.progress || {};
    const current = Number(progress.current || 0);
    const total = Number(progress.total || 0);
    const unit = String(progress.unit || "").trim();
    const count = total > 0
      ? `${current} / ${total}`
      : current > 0 && unit
        ? `${current} ${displayLabel(unit)}`
        : "";
    const target = job.request?.parameters?.project_key || (job.request?.parameters?.all_projects ? "bulk" : job.request?.parameters?.source || job.request?.parameters?.kind || "operation");
    const plan = job.operation === "review" && job.status === "completed" && !job.request?.execute ? job.result?.data?.summary : null;
    return `<article class="job-card ${esc(job.status)}"><div class="card-title"><strong>${esc(displayLabel(job.operation))} · ${esc(displayLabel(target))}</strong>${pill(job.status, job.status)}</div>
      <div class="meta-row"><span>${esc(job.job_id)}</span><span>${esc(count)}</span><span>${job.elapsed_seconds === null || job.elapsed_seconds === undefined ? "" : `経過 ${esc(formatDuration(job.elapsed_seconds))}`}</span><span>${job.eta_seconds === null || job.eta_seconds === undefined ? "" : `残り時間（推定） ${esc(formatDuration(job.eta_seconds))}`}</span></div>
      ${job.last_item ? `<p class="muted">直近: ${esc(job.last_item.project_key)} ${esc(job.last_item.name || "")}</p>` : ""}
      ${job.error ? `<p class="job-error">${esc(apiErrorMessage(job.error))}</p>` : ""}
      <div class="actions">
        ${job.can_cancel ? `<button class="danger small" data-job-cancel="${esc(job.job_id)}">停止を要求</button>` : ""}
        ${job.can_resume ? `<button class="small" data-job-resume="${esc(job.job_id)}">再開</button>` : ""}
        ${plan?.candidates?.length ? `<button class="small primary" data-review-plan="${esc(job.job_id)}">${esc(plan.candidates.length)}件の変更候補を確認</button>` : ""}
      </div><details class="job-details" data-job-details="${esc(job.job_id)}" ${state.openJobDetails.has(job.job_id) ? "open" : ""}>
        <summary>詳細（JSON）</summary>${plan?.candidates?.length ? "" : `<div class="actions"><button class="small" data-job-detail-refresh="${esc(job.job_id)}">詳細を再読込</button></div>`}<div id="job-detail-${esc(job.job_id.replaceAll(":", "-"))}">${plan?.candidates?.length ? renderReviewPlan(job) : state.jobDetailCache.has(job.job_id) ? json(state.jobDetailCache.get(job.job_id)) : `<p class="muted">開くと詳細を読み込みます。</p>`}</div>
      </details></article>`;
  }).join("");
}

function renderReviewPlan(job) {
  const summary = job.result?.data?.summary || {};
  return `<div class="review-plan">${list(summary.candidates).map((item) => `<label><span><input type="checkbox" data-plan-candidate="${esc(job.job_id)}" value="${esc(item.candidate_id)}" checked> ${esc(item.project_key)} ${esc(displayLabel(item.kind || item.suggested_tracking_state || ""))}</span></label>`).join("")}<div class="actions"><button class="danger" data-review-execute="${esc(job.job_id)}">選択した候補を適用</button></div></div>`;
}

function renderCheckpoints(items) {
  if (!items.length) return `<p class="muted">調査の再開地点はありません。</p>`;
  return items.map((item) => `<article class="job-card"><div class="card-title"><strong>${esc(item.run_id)}</strong>${pill(item.status, item.status)}</div><div class="meta-row"><span>${esc(item.completed_project_count)} / ${esc(item.selected_project_count)} · 競合 ${esc(item.conflicted_project_count || 0)} · 対象外 ${esc(item.skipped_project_count || 0)}</span><span>${esc(formatTimestamp(item.updated_at))}</span></div>${item.can_resume ? `<button class="small" data-checkpoint-resume="${esc(item.run_id)}">再開</button>` : ""}</article>`).join("");
}

function scheduleWeekdayOptions(selected) {
  return ["月", "火", "水", "木", "金", "土", "日"].map((label, index) => `<option value="${index}" ${Number(selected) === index ? "selected" : ""}>${label}</option>`).join("");
}

function scheduleWeekOptions(selected) {
  return [
    [0, "毎週"],
    [1, "第1週"],
    [2, "第2週"],
    [3, "第3週"],
    [4, "第4週"],
    [5, "第5週"],
  ].map(([value, label]) => `<option value="${value}" ${Number(selected) === value ? "selected" : ""}>${label}</option>`).join("");
}

function renderSchedules(data) {
  const items = list(data.items);
  if (!items.length) return `<p class="muted">定期実行の設定はありません。</p>`;
  const queue = list(data.queue);
  const queueHtml = queue.length ? `<p class="notice">実行待ち: ${esc(queue.map((item) => `${displayLabel(item.preset_id)}: ${item.current_job_id || "待機中"}`).join(", "))}</p>` : "";
  const observation = data.lexicon_publication_observation || {};
  const observationRuns = list(observation.items);
  const observationHtml = `<article class="operation-card">
    <div class="card-title"><strong>Lexicon取得後の評価</strong>${observation.review_due ? pill("review due", "warning") : pill("monitoring")}</div>
    <p class="muted">${esc(observation.valid_period_count || 0)} / 4 比較可能な週 · 比較開始点 ${esc(formatTimestamp(observation.evaluation_baseline_at))} · ${esc(observation.history_limit || 16)}件の履歴を保持</p>
    ${list(observation.review_reasons).length ? `<p class="notice">${esc(list(observation.review_reasons).map(displayLabel).join("、"))}</p>` : ""}
    ${observation.error ? `<p class="job-error">${esc(apiErrorMessage({message: observation.error}))}</p>` : ""}
    ${observationRuns.length ? `<details><summary>最近の観測履歴</summary>${observationRuns.map((run) => {
      const categories = Object.entries(run.categories || {}).filter(([, value]) => Number(value) > 0).map(([key, value]) => `${displayLabel(key)} ${value}`).join(", ");
      const deltas = Object.entries(run.category_deltas || {}).filter(([, value]) => Number(value) !== 0).map(([key, value]) => `${displayLabel(key)} ${Number(value) > 0 ? "+" : ""}${value}`).join(", ");
      const comparison = run.comparison_status === "available" ? `比較可能${run.valid_period_counted ? " · 週数に加算済み" : " · 同じ週の再実行"}` : `比較不可: ${displayLabel(run.comparison_reason || "unknown")}`;
      return `<p class="muted"><strong>${esc(displayLabel(run.status))}</strong> ${esc(formatTimestamp(run.completed_at))} · ${esc(run.evaluation_period || "期間なし")} · ${esc(comparison)} · 取得 ${esc(formatTimestamp(run.sync_completed_at))}${run.sync_age_seconds === null || run.sync_age_seconds === undefined ? "" : ` (${esc(formatDuration(run.sync_age_seconds))}前)`} · 取得位置 ${esc(run.starting_watermark_seq)} → ${esc(run.ending_watermark_seq)} · 変更 +${esc(run.new_count)}/~${esc(run.updated_count)}/-${esc(run.deleted_count)} · コレクション一覧 ${esc(displayLabel(run.inventory_status))} (${esc(run.inventory_collection_count)}) · 検証 ${esc(run.verification_success_count)}/${esc(run.verification_target_count)}${categories ? ` · 分類 ${esc(categories)}` : ""}${deltas ? ` · Δ ${esc(deltas)}` : ""}${run.review_due ? ` · レビュー: ${esc(list(run.review_reasons).map(displayLabel).join("、"))}` : ""}</p>`;
    }).join("")}</details>` : `<p class="muted">保存済みの観測履歴はありません。</p>`}
  </article>`;
  return `${queueHtml}${observationHtml}<div class="operation-grid">${items.map((item) => {
    const schedule = item.schedule || {};
    const steps = list(item.steps).map((step) => esc(displayLabel(step.label || step.operation))).join(" → ");
    const statusLine = [displayLabel(item.last_status), scheduleMessage(item.last_message), item.last_started_at ? `開始 ${formatTimestamp(item.last_started_at)}` : "", item.last_completed_at ? `完了 ${formatTimestamp(item.last_completed_at)}` : ""].filter(Boolean).join(" · ");
    return `<article class="operation-card schedule-card" data-schedule-card="${esc(item.preset_id)}">
      <div class="card-title"><strong>${esc(displayLabel(item.label))}</strong>${schedule.enabled ? pill("enabled", "confirmed") : pill("disabled")}</div>
      <p class="muted">${esc(displayLabel(item.description))}</p>
      <p class="muted">${steps}</p>
      <div class="controls">
        <label><span><input type="checkbox" data-schedule-field="enabled" data-preset-id="${esc(item.preset_id)}" ${schedule.enabled ? "checked" : ""}> 有効</span></label>
        <label>週<select data-schedule-field="week_of_month" data-preset-id="${esc(item.preset_id)}">${scheduleWeekOptions(schedule.week_of_month)}</select></label>
        <label>曜日<select data-schedule-field="weekday" data-preset-id="${esc(item.preset_id)}">${scheduleWeekdayOptions(schedule.weekday)}</select></label>
        <label>時刻<input type="time" data-schedule-field="time" data-preset-id="${esc(item.preset_id)}" value="${esc(schedule.time || "03:00")}"></label>
        <label>タイムゾーン<input data-schedule-field="timezone" data-preset-id="${esc(item.preset_id)}" value="${esc(schedule.timezone || "Asia/Tokyo")}"></label>
      </div>
      <p class="muted">次回: ${esc(formatTimestamp(item.next_due_at))}</p>
      ${statusLine ? `<p class="muted">${esc(statusLine)}</p>` : ""}
      ${list(item.pending_steps).length ? `<p>保留: ${list(item.pending_steps).map(step => esc(displayLabel(step.label))).join(" → ")}</p>` : ""}
      <div class="actions"><button class="small primary" data-schedule-run="${esc(item.preset_id)}">${list(item.pending_steps).length ? "保留中の処理を再開" : "今すぐ実行"}</button></div>
    </article>`;
  }).join("")}</div><div class="actions"><button id="save-schedules" class="primary">定期実行の設定を保存</button></div>`;
}

function collectScheduleUpdates() {
  const updates = new Map();
  document.querySelectorAll("[data-schedule-field]").forEach((input) => {
    const presetId = input.dataset.presetId;
    if (!presetId) return;
    const item = updates.get(presetId) || {preset_id: presetId};
    const field = input.dataset.scheduleField;
    if (field === "enabled") item[field] = input.checked;
    else if (field === "weekday" || field === "week_of_month") item[field] = Number(input.value);
    else item[field] = input.value;
    updates.set(presetId, item);
  });
  return [...updates.values()];
}

async function loadJobs() {
  if (state.screen !== "operations") return;
  if (state.operationTimer) clearTimeout(state.operationTimer);
  state.operationTimer = null;
  try {
    const [data, checkpoints, schedules] = await Promise.all([api("/api/jobs?limit=50"), api("/api/checkpoints?limit=100"), api("/api/job-schedules")]);
    state.jobsById = new Map(list(data.items).map((job) => [job.job_id, job]));
    const anchor = document.querySelector("[data-job-details][open]");
    const anchorTop = anchor?.getBoundingClientRect().top;
    const detailScroll = new Map([...document.querySelectorAll("[data-job-details][open]")].map((details) => {
      const pre = details.querySelector("pre");
      return [details.dataset.jobDetails, {top:pre?.scrollTop || 0, left:pre?.scrollLeft || 0}];
    }));
    $("job-list").innerHTML = renderJobs(list(data.items));
    $("checkpoint-list").innerHTML = renderCheckpoints(list(checkpoints.items));
    $("schedule-list").innerHTML = renderSchedules(schedules);
    bindJobActions();
    if (anchor && anchorTop !== undefined) {
      const replacement = document.querySelector(`[data-job-details="${CSS.escape(anchor.dataset.jobDetails)}"]`);
      if (replacement) window.scrollBy(0, replacement.getBoundingClientRect().top - anchorTop);
    }
    detailScroll.forEach((position, jobId) => {
      const pre = document.querySelector(`[data-job-details="${CSS.escape(jobId)}"] pre`);
      if (pre) { pre.scrollTop = position.top; pre.scrollLeft = position.left; }
    });
    if (list(data.items).some((job) => job.can_cancel || job.status === "cancelling") || list(schedules.queue).length) {
      state.operationTimer = setTimeout(loadJobs, 2000);
    }
  } catch (error) { status(error.message, true); }
}

async function refreshJobDetail(jobId) {
  try {
    const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
    const detail = {request:job.request, result:job.result, events:job.events, stderr:job.stderr};
    state.jobDetailCache.set(jobId, detail);
    const target = $(`job-detail-${jobId.replaceAll(":", "-")}`);
    if (target && state.openJobDetails.has(jobId)) target.innerHTML = json(detail);
  } catch (error) {
    state.openJobDetails.delete(jobId);
    status(error.message, true);
  }
}

function bindJobActions() {
  $("save-schedules")?.addEventListener("click", async () => {
    try {
      await api("/api/job-schedules", {method:"POST", body:JSON.stringify({items: collectScheduleUpdates()})});
      status("定期実行の設定を保存しました。");
      await loadJobs();
    } catch (error) { status(error.message, true); }
  });
  document.querySelectorAll("[data-schedule-run]").forEach((button) => button.onclick = () => {
    const presetId = button.dataset.scheduleRun;
    confirmOperation("定期実行の処理を開始", `「${displayLabel(presetId)}」を実行待ちへ追加しますか？ SQLiteへ保存する処理を含む場合があります。`, async () => {
      await api(`/api/job-schedules/${encodeURIComponent(presetId)}/run`, {method:"POST", body:"{}"});
      status("処理を実行待ちへ追加しました。");
      await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-detail-refresh]").forEach((button) => button.onclick = () => refreshJobDetail(button.dataset.jobDetailRefresh));
  document.querySelectorAll("[data-checkpoint-resume]").forEach((button) => button.onclick = () => confirmOperation("保存済みの再開地点から再開", `${button.dataset.checkpointResume}から再開しますか？`, async () => {
    await api(`/api/checkpoints/${encodeURIComponent(button.dataset.checkpointResume)}/resume`, {method:"POST", body:"{}"});
    status("再開処理を開始しました。"); await loadJobs();
  }));
  document.querySelectorAll("[data-review-plan]").forEach((button) => button.onclick = () => {
    const details = button.closest("article").querySelector("details"); details.open = true;
  });
  document.querySelectorAll("[data-review-execute]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.reviewExecute;
    const card = button.closest("article");
    const selected = [...card.querySelectorAll(`[data-plan-candidate="${jobId}"]:checked`)].map((item) => item.value);
    const sourceJob = state.jobsById.get(jobId);
    if (!sourceJob || !selected.length) { status("変更候補を1件以上選択してください。", true); return; }
    confirmOperation("追跡状態の変更を適用", `選択した${selected.length}件の変更候補を適用しますか？`, async () => {
      const summary = sourceJob.result.data.summary;
      await api("/api/jobs/review", {method:"POST", body:JSON.stringify({...sourceJob.request.parameters, execute:true, selected_candidate_ids:selected, plan_fingerprint:summary.plan_fingerprint})});
      status("追跡状態の変更を開始しました。"); await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-cancel]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.jobCancel;
    confirmOperation("バックグラウンドジョブを停止", `${jobId}の停止を要求しますか？ 処理の区切りまで実行してから停止する場合があります。`, async () => {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {method:"POST", body:"{}"});
      status("停止を要求しました。"); await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-resume]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.jobResume;
    confirmOperation("調査を再開", `${jobId}の再開地点から調査を再開しますか？`, async () => {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {method:"POST", body:"{}"});
      status("再開処理を開始しました。"); await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-details]").forEach((details) => details.ontoggle = async () => {
    const jobId = details.dataset.jobDetails;
    if (!details.open) {
      state.openJobDetails.delete(jobId);
      return;
    }
    state.openJobDetails.add(jobId);
    const sourceJob = state.jobsById.get(jobId);
    if (sourceJob?.operation === "review" && sourceJob?.result?.data?.summary?.candidates?.length) return;
    const target = $(`job-detail-${jobId.replaceAll(":", "-")}`);
    if (state.jobDetailCache.has(jobId)) target.innerHTML = json(state.jobDetailCache.get(jobId));
    await refreshJobDetail(jobId);
  });
}

async function renderSettings() {
  try { state.server = state.server || await api("/api/settings"); } catch (error) { status(error.message, true); }
  $("single-pane").innerHTML = `<p class="eyebrow">ローカルWeb UI</p><h2>設定</h2><div class="controls filter-grid">
    <label>要レビュー・紹介候補の件数上限<input id="setting-review" type="number" min="1" max="500" value="${esc(state.settings.reviewLimit)}"></label>
    <label>登録プロジェクトの件数上限<input id="setting-project" type="number" min="1" max="500" value="${esc(state.settings.projectLimit)}"></label>
    <label>マージ候補の件数上限<input id="setting-merge-limit" type="number" min="1" max="500" value="${esc(state.settings.mergeLimit)}"></label>
    <label>マージ候補の条件<select id="setting-merge-mode">${selectOptions([["strict", "厳密"], ["moderate", "標準"], ["broad", "広範"]], state.settings.mergeMode)}</select></label>
    <label>タイムゾーン<input id="setting-time-zone" value="${esc(state.settings.timeZone || defaults.timeZone)}" placeholder="Asia/Tokyo または +09:00"></label>
    <label class="settings-toggle"><span>紹介候補一覧</span><span><input id="setting-personal" type="checkbox" ${state.settings.includePersonalTools ? "checked" : ""}> 個人用ツールを含める</span></label>
  </div><div class="actions"><button id="save-settings" class="primary">設定を保存</button><button id="clear-token" class="danger">APIトークンを削除</button><button id="reload-server" class="danger">サーバーを再起動</button></div>
  ${section("サーバー", `<dl class="kv"><dt>API</dt><dd>v${esc(state.server?.api_version || "?")}</dd><dt>DBスキーマ</dt><dd>v${esc(state.server?.schema_version || "?")}</dd><dt>起動時コミット</dt><dd>${state.server?.build?.commit ? `<code>${esc(state.server.build.commit)}${state.server.build.dirty ? "（未コミット変更あり）" : ""}</code>` : "Git管理外のため取得できません"}</dd><dt>Python</dt><dd>${esc(state.server?.build?.python || "?")}</dd><dt>データベース</dt><dd>${esc(state.server?.db_path || "取得できません")}</dd><dt>バックグラウンドジョブ</dt><dd>${state.server?.operations_connected ? "接続済み（調査・取り込み・追跡状態の見直し）" : "未接続"}</dd></dl>`)}
  `;
  $("setting-merge-mode").value = state.settings.mergeMode;
  $("save-settings").onclick = () => {
    const timeZone = normalizeTimeZone($("setting-time-zone").value);
    if (!timeZone) { status("タイムゾーンが不正です。Asia/TokyoのようなIANA名、または+09:00のような時差を指定してください。", true); return; }
    state.settings = {reviewLimit:Number($("setting-review").value), projectLimit:Number($("setting-project").value), mergeLimit:Number($("setting-merge-limit").value), mergeMode:$("setting-merge-mode").value, includePersonalTools:$("setting-personal").checked, timeZone};
    saveSettings(); status("設定を保存しました。");
    renderSettings();
  };
  $("clear-token").onclick = () => { localStorage.removeItem("projectRegistryToken"); token = ""; status("APIトークンを削除しました。再読込すると再入力できます。"); };
  $("reload-server")?.addEventListener("click", confirmServerReload);
}

function confirmServerReload() {
  const dialog = $("confirm-dialog");
  $("confirm-context").textContent = "操作を確認";
  $("confirm-submit").textContent = "再起動";
  $("confirm-submit").className = "danger";
  $("confirm-title").textContent = "サーバーを再起動";
  $("confirm-message").textContent = "同じ起動設定でWebサーバーを再起動しますか？ 実行中のジョブには停止を要求し、制限時間を超えた処理は終了します。";
  $("confirm-note-label").hidden = true;
  showDialogWithoutInputFocus(dialog);
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    const previousInstance = state.server?.server_instance_id || "";
    try {
      status("サーバーの再起動を要求中…");
      await api("/api/reload", {method:"POST", body:JSON.stringify({confirm:true})});
      status("サーバーの再起動を待機中…");
      waitForServerReload(previousInstance, Date.now() + 30000);
    } catch (error) { status(error.message, true); }
  };
}

async function waitForServerReload(previousInstance, deadline) {
  try {
    const server = await api("/api/settings");
    if (server.server_instance_id && server.server_instance_id !== previousInstance) {
      location.reload();
      return;
    }
  } catch (_) {}
  if (Date.now() >= deadline) {
    status("サーバーの再起動を要求しましたが、30秒以内に応答が戻りませんでした。", true);
    return;
  }
  setTimeout(() => waitForServerReload(previousInstance, deadline), 500);
}

document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => button.addEventListener("click", () => setScreen(button.dataset.screen)));
$("refresh").addEventListener("click", () => setScreen(state.screen, {updateUrl: false, preserveSelection: true}));
window.addEventListener("popstate", () => {
  restoreStateFromUrl();
  setScreen(state.screen, {updateUrl: false, preserveSelection: true});
});
api("/api/settings").then((server) => {
  state.server = server;
  setScreen(state.screen, {updateUrl: false, preserveSelection: true});
}).catch((error) => status(error.message, true));
