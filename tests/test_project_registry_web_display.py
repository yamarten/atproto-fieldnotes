"""Exercise the display boundary without a browser or external services."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required for Web display checks")
class WebDisplayTest(unittest.TestCase):
    def run_javascript(self, checks):
        path = Path(__file__).resolve().parents[1] / "scripts/project_registry_web/static/app.js"
        # Run the real render helpers; omit only page startup and supply browser storage.
        source = path.read_text().rsplit('\ndocument.querySelectorAll(".primary-nav', 1)[0]
        harness = r'''
const assert = require("node:assert/strict");
const location = {search:""};
const localStorage = {getItem: () => null};
const prompt = () => "";
const document = {};
'''
        result = subprocess.run([shutil.which("node"), "-"], input=harness + source + checks,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_list_count_escapes_external_text(self):
        self.run_javascript(r'''
const container = {innerHTML: ""};
document.getElementById = (id) => {
  assert.equal(id, "list");
  return container;
};
document.querySelectorAll = () => [];
state.screen = "projects";
renderList({items: [], count: '<img src=x onerror=alert(1)>'});
assert.ok(!container.innerHTML.includes('<img'));
assert.ok(container.innerHTML.includes('&lt;img src=x onerror=alert(1)&gt;'));
renderList({items: [], count: 12});
assert.match(container.innerHTML, /<strong>12<\/strong> 件/);
''')

    def test_search_controls_render_server_options_fallbacks_and_selection(self):
        self.run_javascript(r'''
state.screen = "projects";
state.projectSearch.tracking = "active,stalled";
state.projectSearch.identifierKind = "nsid";
state.projectSearch.relationSource = 'external"<source>';
state.server = {filter_options: {tracking_states: ["future_state"], relation_sources: ['external"<source>']}};
const html = listControls();
const tracking = html.match(/<select id="filter-tracking"([^>]*)>([\s\S]*?)<\/select>/);
assert.match(tracking[1], /\bmultiple\b/);
assert.match(tracking[2], /value="active" selected>通常追跡/);
assert.match(tracking[2], /value="stalled" selected>活動停滞/);
assert.match(tracking[2], /value="future_state"/);
const identifiers = html.match(/<select id="filter-identifier-kind"[^>]*>([\s\S]*?)<\/select>/)[1];
assert.match(identifiers, /value="nsid" selected>NSID/);
assert.match(html, /id="filter-relation-source" list="filter-relation-sources" value="external&quot;&lt;source&gt;"/);
assert.match(html, /<datalist id="filter-relation-sources"><option value="external&quot;&lt;source&gt;"><\/option><\/datalist>/);
''')

    def test_operation_and_settings_controls_render_and_bind_actions(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"", value:"", listeners:{},
    addEventListener(type, fn) { this.listeners[type] = fn; }});
  return nodes.get(id);
};
loadJobs = () => {};
renderOperations();
const html = nodes.get("single-pane").innerHTML;
for (const [id, label, handler] of [
  ["start-enrich", "調査を開始", startEnrichJob],
  ["start-sync", "取り込みを開始", startSyncJob],
  ["start-review", "変更候補を確認", startReviewJob],
]) {
  assert.match(html, new RegExp(`<button id="${id}"[^>]*>${label}</button>`));
  assert.equal(nodes.get(id).onclick, handler);
}
state.server = {operations_connected:true};
renderSettings().then(() => {
  assert.match(nodes.get("single-pane").innerHTML, /id="reload-server"[^>]*>サーバーを再起動<\/button>/);
  assert.equal(nodes.get("reload-server").listeners.click, confirmServerReload);
}).catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_context_help_escapes_content_and_survives_schedule_refresh(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"", textContent:"", style:{},
    matches() { return Boolean(this.open); }, hidePopover() { this.open = false; },
    showPopover(options) { this.open = true; this.source = options.source; },
    getBoundingClientRect: () => ({width:380, height:120})});
  return nodes.get(id);
};
document.querySelector = () => null;
document.querySelectorAll = () => [];
const window = {innerWidth:1280, innerHeight:720};
const unsafe = '<img src=x onerror=alert(1)>';
const html = renderSchedules({items:[{preset_id:"example", label:'A"&B', description:unsafe,
  steps:[{label:unsafe}]}]});
assert.match(html, /aria-label="A&quot;&amp;Bの処理内容の説明"/);
assert.ok(!html.includes(unsafe));
assert.match(html, /<template><p>&lt;img/);
assert.ok(!html.includes("<details"));
const attributes = {};
const button = {dataset:{helpTitle:"Example"}, isConnected:false,
  nextElementSibling:{innerHTML:"<p>Explanation</p>"},
  setAttribute(name, value) { attributes[name] = value; }};
showContextHelp(button);
const popup = nodes.get("context-help-popover");
const replacement = {...button, isConnected:true,
  getBoundingClientRect: () => ({left:100, top:100, bottom:132}), focus() {}};
document.querySelectorAll = selector => selector === ".context-help-trigger" ? [replacement] : [];
assert.equal(popup.open, true);
assert.equal(attributes["aria-expanded"], "true");
assert.equal(nodes.get("context-help-title").textContent, "Example");
bindJobActions = () => {};
state.screen = "operations";
api = async path => path === "/api/job-schedules" ? {items:[{preset_id:"example", label:"Example"}]} : {items:[]};
loadJobs().then(() => {
  assert.equal(popup.open, true);
  assert.equal(popup.source, replacement);
  assert.equal(nodes.get("context-help-body").innerHTML, "<p>Explanation</p>");
  assert.match(nodes.get("schedule-list").innerHTML, /data-schedule-field="enabled"/);
}).catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_detail_and_metrics_render_data_and_bind_project_enrichment(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"", listeners:{},
    addEventListener(type, fn) { this.listeners[type] = fn; }});
  return nodes.get(id);
};
document.querySelectorAll = () => [];
syncUrl = () => {};
loadMergeAnalysis = () => {};
const starts = [];
startEnrich = payload => starts.push(payload);
const detail = {project:{project_key:"proj:display", name:"Example"},
  overview:{confirmed_services:["https://example.com/app"]}};
renderDetail(detail);
const html = nodes.get("detail").innerHTML;
for (const identity of [{has_identity:true, components:[{value:"org.example.post", registration_origin:true}]},
                        {has_identity:false, components:[]}]) {
  renderDetail({...detail, identity});
  assert.equal(nodes.get("detail").innerHTML.split('data-detail-panel="identifiers"')[0],
               html.split('data-detail-panel="identifiers"')[0]);
}
assert.match(html, /最新の調査実行記録/);
assert.ok(!html.includes('このプロジェクトが表す対象'));
assert.match(html, /<dt>採用済みサービス<\/dt><dd><a[^>]*href="https:\/\/example.com\/app"/);
assert.match(html, /id="enrich-project"/);
nodes.get("enrich-project").listeners.click();
assert.deepEqual(starts, [{project_key:"proj:display", all_projects:false}]);
const metrics = renderRunMetrics({version:3, total_ms:42, phases:{run_history_save_ms:12, project_save_ms:30}});
assert.match(metrics, /<dt>調査履歴の変換・SQL保存<\/dt><dd>12ミリ秒<\/dd>/);
assert.match(metrics, /<dt>プロジェクトの変換・SQL保存<\/dt><dd>30ミリ秒<\/dd>/);
''')

    def test_identity_links_use_pdsls_and_preserve_at_uri(self):
        self.run_javascript(r'''
for (const value of ['alice.example', 'did:plc:abc123', 'did:web:example.com']) {
  const href = `https://pds.ls/at://${value}`;
  assert.equal(pdslsIdentityUrl(` ${value} `), href);
  assert.ok(identifierValueLink({kinds: ['did'], value}).includes(`href="${href}"`));
}
for (const value of ['', 'not an identity', '<img src=x onerror=alert(1)>']) {
  assert.equal(pdslsIdentityUrl(value), '');
  const rendered = identifierValueLink({kinds: ['account'], value});
  assert.ok(!rendered.includes('<a'));
  assert.ok(!rendered.includes('<img'));
}
''')

    def test_merge_continuation_skip_keeps_visible_count_and_opens_next(self):
        self.run_javascript(r'''
let cards = [];
const window = {scrollTo() {}};
const nodes = new Map();
const opened = [];
function node(id) {
  return {innerHTML: "", textContent: "", style: {}, dataset: {}, listeners: {},
    classList: {contains: () => id === "unrelated", add() {}, remove() {}},
    addEventListener(type, fn) { this.listeners[type] = fn; },
    remove() { cards.splice(cards.indexOf(this), 1); },
    before(other) { cards.splice(cards.indexOf(this), 0, other); },
  };
}
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, node(id));
  return nodes.get(id);
};
document.createElement = () => node("continuation");
document.querySelectorAll = selector => selector === "#list [data-project-key]" ? [...cards] : [];
document.body = node("body");
const meta = {textContent: "2"};
document.getElementById("list").querySelector = () => meta;
for (const key of ["unrelated", "next"]) {
  const card = node(key); card.dataset.projectKey = key; cards.push(card);
}
state.screen = "review";
syncUrl = () => {};
api = async () => ({source: {project_key:"target"}, target: {project_key:"source"}});
loadDetail = key => opened.push(key);
insertMergeReviewContinuation("source", "target", 1);
assert.equal(cards[1].hidden, true);
// Reversing the displayed merge direction must still find its continuation.
loadMergeDetail("target", "source").then(() => {
  assert.match(document.getElementById("detail").innerHTML, /id="skip-review-item"/);
  document.getElementById("skip-review-item").listeners.click();
  assert.deepEqual(cards.map(card => card.dataset.projectKey), ["unrelated", "next"]);
  assert.deepEqual(opened, ["next"]);
  assert.equal(meta.textContent, "2");
  assert.match(document.getElementById("status").textContent, /今回は先送り.*次の項目/);
  // A visible card, unlike the hidden continuation, does reduce the count.
  skipReviewItem("unrelated");
  assert.equal(meta.textContent, "1");
}).catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_labels_preserve_machine_values_raw_data_and_escaping(self):
        self.run_javascript(r'''
for (const [group, values] of Object.entries(filterOptionFallbacks)) {
  for (const value of values) assert.notEqual(displayLabel(value), value, `${group}: ${value}`);
}
assert.match(enumOptions(["active", "stalled"], "stalled"), /value="stalled" selected>活動停滞<\/option>/);
assert.match(multiEnumOptions(["active", "stalled"], "active,stalled"), /value="active" selected>通常追跡/);
assert.match(pill("candidate", "candidate"), /class="pill candidate">候補/);
assert.match(renderExplanation("merge_candidate", {conclusion:"none", rule:"", supporting_signals:[], counter_signals:[]}), /data-explain-topic="merge_candidate"/);
assert.equal(displayLabel("unrecognized_future_value"), "unrecognized_future_value");
assert.equal(displayLabel("owned_nsid:app.example.post"), "帰属NSID: app.example.post");
assert.equal(json({status:"active", summary:"Keep original <text>"}), '<pre>{\n  &quot;status&quot;: &quot;active&quot;,\n  &quot;summary&quot;: &quot;Keep original &lt;text&gt;&quot;\n}</pre>');
const unsafe = '<img src=x onerror=alert(1)>';
assert.ok(!pill(unsafe).includes('<img'));
assert.ok(!enumOptions([unsafe], unsafe).includes('<img'));
assert.equal(scheduleMessage("running enrich --all"), "実行中: 通常追跡中の全件調査");
assert.equal(scheduleMessage("scheduled step failed: sync github; resume pending steps manually"), "失敗: GitHubから取り込み。保留中の処理を手動で再開してください。");
assert.match(apiErrorMessage({code:"stale_project", message:"revision mismatch"}), /再読込/);
assert.match(apiErrorMessage({code:"validation_error", message:"future diagnostic"}), /入力内容.*future diagnostic/);
const originalNote = "service accepted by the configured trust policy";
assert.equal(decisionNote({kind:"block_identifier", note:originalNote}), originalNote);
assert.equal(decisionNote({kind:"confirm_identifier", note:originalNote, metadata:{source:"trust_policy"}}), "設定された信頼規則でサービスを採用");
const oauth = renderOAuthClient({metadata_url:"https://example.test/client.json", scope:"atproto repo:app.example.post?action=delete", analysis:{status:"parsed", permissions:[{kind:"repo", collection:["app.example.post"], action:["delete"]}]}});
assert.match(oauth, /操作: 削除/);
assert.match(oauth, /atproto repo:app.example.post\?action=delete/);
state.screen = "review";
state.reviewView = "promotion";
state.promotionView = "inbox";
const score = {total: 12, components: {service_evidence: 14, promotion_blockers: -2, recent_activity: 0}};
const candidate = {score_breakdown: score, service_evidence_group: "confirmed", priority_group: "normal",
  candidate_visibility: {group:"eligible"}, attention: {group:"unreviewed"},
  atproto_usage: {source:"none", role:"unknown", reason:"routine explanation"}};
const compact = promotionCardDetails(candidate);
assert.match(compact, /スコア 12/);
assert.match(compact, /atproto利用 未評価/);
assert.ok(!compact.includes("routine explanation"));
assert.ok(!compact.includes("通常の優先度"));
assert.ok(!compact.includes("紹介候補一覧の対象"));
assert.match(renderPromotionScore(score), /紹介材料の不足<\/dt><dd>-2/);
assert.match(renderPromotionScore(score), /<dd>\+14/);
assert.ok(!renderPromotionScore(score).includes("最近の活動"));
const waiting = promotionCardDetails({...candidate, priority_group:"lowered", priority_reason:unsafe,
  candidate_visibility:{group:"deferred"}, attention:{group:"changed"},
  atproto_usage:{source:"operator", role:"unknown", reason:"調査中のため不明"},
  active_deferral:{reason:"公開待ち", expires_at:"2026-10-22T00:00:00Z"}});
assert.match(waiting, /調査中のため不明/);
assert.match(waiting, /公開待ち/);
assert.match(waiting, /根拠変化・再確認/);
assert.match(waiting, /低い優先度/);
assert.ok(!waiting.includes("<img"));
assert.match(waiting, /&lt;img/);
state.promotionView = "technical";
const technical = promotionCardDetails({...candidate, technical_interest_level:"high", technical_interest_score:80,
  signals:[{kind:"technical_insight_candidate", details:{technical_interest_summary:"独自の連携方式"}}]});
assert.match(technical, /技術評価 高い · 80/);
assert.match(technical, /独自の連携方式/);
assert.ok(!renderServiceObservations([]).includes("取得失敗"));
assert.match(renderServiceObservations([{fetch_error:"timeout"}]), /取得失敗は提供終了を示しません/);
''')
