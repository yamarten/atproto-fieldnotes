"""Exercise the display boundary without a browser or external services."""
import ast
import inspect
import json
import shutil
import subprocess
import unittest

from helpers import ROOT


@unittest.skipUnless(shutil.which("node"), "Node.js is required for Web display checks")
class WebDisplayTest(unittest.TestCase):
    def test_review_mutation_keeps_unresolved_changes_visible(self):
        self.run_javascript(r'''
state.screen = "review"; state.reviewView = "queue";
assert.equal(advanceReviewListAfterMutation({detail:{review:{signals:[{kind:"editorial_refresh"}]}}},
  "/api/projects/test/acknowledge-review"), false);
assert.equal(advanceReviewListAfterMutation({project_key:"test"},
  "/api/projects/test/block-identifier"), false);
''')

    def test_partial_completion_and_account_correspondence_are_visible(self):
        self.run_javascript(r'''
const job = {job_id:"job:partial", operation:"observe_services", status:"completed",
 result:{data:{summary:{outcome:"partial"}}}};
assert.match(renderJobs([job]), /完了（一部失敗）/);
assert.match(renderSchedules({items:[{last_status:"completed",last_steps:[{outcome:"partial"}]}]}), /完了（一部失敗）/);
assert.ok(!renderJobs([{...job, status:"failed"}]).includes("完了（一部失敗）"));
assert.ok(!renderJobs([{...job, result:{}}]).includes("完了（一部失敗）"));
const account = {family:"identity", kinds:["account","handle","did"], value:"did:plc:one",
 classification:"candidate", providers:[], role_labels:[], entries:[
 {kind:"account", role:"developer_account", value:"did:plc:one", classification:"candidate",
  actions:{identifier_type:"account",confirm:true}},
 {kind:"handle", role:"developer_handle", value:"dev.example", classification:"candidate"}],
 account_display:{handles:["dev.example"],dids:["did:plc:one"],correspondence:"saved"}};
const html=renderIdentifier(account);
assert.match(html, /@dev.example/);
assert.match(html, /開発者アカウント/);
assert.ok(html.includes("at://did:plc:one"));
assert.ok(!html.split("<details>")[0].includes("developer_handle"));
assert.match(renderIdentifier({...account,account_display:{...account.account_display,correspondence:"ambiguous"}}), /対応が複数保存/);
assert.match(renderIdentifier({...account,account_display:{...account.account_display,correspondence:"unrecorded"}}), /対応は保存されていません/);
''')

    def test_disabled_source_skips_are_visible_in_job_and_persisted_schedule(self):
        self.run_javascript(r'''
const job = {job_id:"job:skip", operation:"sync", status:"completed",
  request:{parameters:{source:"lexicons"}}, result:{data:{summary:{skipped:true,reason:"source_disabled"}}}};
assert.match(renderJobs([job]), /情報源が無効のためスキップしました/);
assert.ok(!renderJobs([{...job,result:{}}]).includes("スキップしました"));
const html = renderSchedules({items:[{preset_id:"sync_sources_then_enrich",last_steps:[
  {label:"sync lexicons",status:"skipped",reason:"source_disabled"},
  {label:"<unsafe>",status:"skipped",reason:"source_disabled"}]}]});
assert.match(html, /情報源が無効のためスキップ:/);
assert.match(html, /&lt;unsafe&gt;/);
assert.ok(!html.includes("<unsafe>"));
''')

    def test_browser_entry_and_introduction_plan_views_restore_and_escape(self):
        self.run_javascript(r'''
restoreStateFromUrl(new URLSearchParams({screen:"browser",url:"https://example.com/<tag>",browser_q:"service"}));
assert.equal(state.screen,"browser");
assert.equal(state.browserQuery,"service");
assert.equal(browserPageUrl("https://example.com/path?token=secret#code"),"https://example.com/path");
assert.throws(()=>browserPageUrl("https://name:secret@example.com"));
assert.match(listControls(), /https:\/\/example.com\/&lt;tag&gt;/);
assert.match(listControls(), /id="browser-new-seed" disabled/);
restoreStateFromUrl(new URLSearchParams({screen:"review",view:"planned",planned_q:"<note>",planned_offset:"50"}));
assert.equal(state.reviewView,"planned");
assert.equal(state.plannedOffset,50);
assert.match(listControls(), /&lt;note&gt;/);
assert.equal(displayLabel("plan_introduction"),"紹介予定の取り置き");
assert.equal(displayLabel("confirm_service_usage"),"サービスのLexicon利用の確認");
''')

    def run_javascript(self, checks, search=""):
        path = ROOT / "scripts/project_registry_web/static/app.js"
        # Run the real render helpers; omit only page startup and supply browser storage.
        source = path.read_text().rsplit('\ndocument.querySelectorAll(".primary-nav', 1)[0]
        harness = r'''
const assert = require("node:assert/strict");
const location = {search:SEARCH};
const localStorage = {getItem: () => null};
const prompt = () => "";
const document = {};
'''.replace("SEARCH", json.dumps(search))
        result = subprocess.run([shutil.which("node"), "-"], input=harness + source + checks,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_default_promotion_and_explicit_screen_urls_roundtrip(self):
        self.run_javascript(r'''
assert.equal(state.screen, "review");
assert.equal(state.reviewView, "promotion");
let historyUrl;
global.history = {pushState(_,__,url){historyUrl=url;}};
location.pathname = "/";
for (const view of ["queue", "promotion", "planned", "merge", "introduced"]) {
  restoreStateFromUrl(new URLSearchParams({view}));
  assert.equal(state.screen, "review");
  assert.equal(state.reviewView, view);
  syncUrl();
  const saved = new URLSearchParams(historyUrl.split("?")[1]);
  assert.equal(saved.get("screen"), "review");
  restoreStateFromUrl(saved);
  assert.equal(state.reviewView, view);
}
restoreStateFromUrl(new URLSearchParams({review_kind:"lexicon_publication_changed"}));
assert.equal(state.screen, "review");
assert.equal(state.reviewSignalKind, "lexicon_publication_changed");
assert.equal(state.reviewView, "queue");
restoreStateFromUrl(new URLSearchParams({screen:"projects",view:"promotion"}));
assert.equal(state.screen, "projects");
syncUrl();
assert.equal(new URLSearchParams(historyUrl.split("?")[1]).get("screen"), "projects");
for (const key of ["q", "tracking", "identifier_value", "relation_predicate", "decision_status"]) {
  restoreStateFromUrl(new URLSearchParams({[key]:"example"}));
  assert.equal(state.screen, "projects");
}
restoreStateFromUrl(new URLSearchParams({screen:"review"}));
assert.equal(state.screen, "review");
assert.equal(state.reviewView, "queue");
restoreStateFromUrl(new URLSearchParams());
assert.equal(state.screen, "review");
assert.equal(state.reviewView, "promotion");
assert.equal(state.promotionView, "inbox");
syncUrl();
restoreStateFromUrl(new URLSearchParams(historyUrl.split("?")[1]));
assert.equal(state.screen, "review");
assert.equal(state.reviewView, "promotion");
''')

    def test_popup_navigation_excludes_credentials_and_preserves_embedded_history(self):
        self.run_javascript(r'''
let message, historyUrl;
global.window = {parent:{}};
global.history = {pushState(_,__,url){historyUrl=url;}};
location.pathname="/";
const port={postMessage(data){message=data;},close(){}};
const connection={origin:"moz-extension://test",source:null,ports:[port],data:{type:"registry-popup-connect"}};
connectPopup({...connection,origin:"moz-extension://other"});
assert.equal(message,undefined);
connectPopup({...connection,source:{}});
assert.equal(message,undefined);
connectPopup(connection);
state.selected="proj:chosen";
state.browserQuery="org.example";
state.detailTab="identifiers";
syncUrl();
assert.equal(new URLSearchParams(historyUrl.slice(2)).get("embed"),"popup");
assert.deepEqual(message, {type:"registry-popup-location",
 pageUrl:"https://example.org/",browserQuery:"org.example",project:"proj:chosen",tab:"identifiers"});
const nodes={"popup-login":{hidden:true},workspace:{hidden:false}};
document.getElementById=id=>nodes[id];
startApplication();
assert.equal(nodes["popup-login"].hidden,false);
assert.equal(nodes.workspace.hidden,true);
''', search="?screen=browser&embed=popup&popup_parent=moz-extension%3A%2F%2Ftest&url=https%3A%2F%2Fexample.org%2F")

    def test_seed_form_preview_edit_duplicate_save_failure_and_conflict(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"", value:"", textContent:"", hidden:true,
    isConnected:true, disabled:false, listeners:{}, style:{},
    addEventListener(event, fn) { this.listeners[event] = fn; }, querySelectorAll() { return []; }});
  return nodes.get(id);
};
const node = id => document.getElementById(id);
let calls=[], loaded=[], statuses=[], closed=0;
loadDetail = async (...args) => { loaded.push(args); };
loadList = async () => {};
status = (...args) => { statuses.push(args); };
const dialog = {close() { closed++; }};
const submit = () => node("seed-form").onsubmit({preventDefault(){}});
const preview = {seed_type:"repo_url", seed_value:"https://forge.example/a/b", candidates:[], exact_project_key:""};
(async () => {
  api = async (path, options) => { calls.push([path,options]); return options ? {project_key:"proj:target"} : preview; };
  state.detailRevision = "later-revision";
  bindSeedForm({projectKey:"proj:target", revision:"opened-revision", dialog});
  node("save-seed").onclick();
  assert.equal(calls.length,0); // no save without a preview
  node("seed-value").value = "repo_url: https://forge.example/a/b";
  await submit();
  assert.match(calls[0][0], /type=repo_url/);
  assert.equal(node("save-seed").hidden,false);
  node("seed-value").value = "https://different.example";
  node("seed-value").oninput();
  node("save-seed").onclick();
  assert.equal(calls.length,1); // editing revokes the previous preview
  assert.equal(node("save-seed").hidden,true);
  node("seed-value").value = "repo_url: https://forge.example/a/b";
  await submit();
  node("save-seed").onclick();
  node("save-seed").onclick();
  await mutationQueue;
  const writes = calls.filter(([, options]) => options);
  assert.equal(writes.length,1);
  assert.deepEqual(JSON.parse(writes[0][1].body), {seed:"https://forge.example/a/b", type:"repo_url", project_key:"proj:target", expected_revision:"opened-revision"});
  assert.equal(closed,1);
  assert.equal(loaded[0][0],"proj:target");
  assert.match(statuses.at(-1)[0], /識別子を保存/);

  bindSeedForm({projectKey:"proj:target", revision:"revision", dialog});
  api = async () => ({...preview, exact_project_key:"proj:other", exact:{name:"<Other>"}});
  await submit();
  assert.equal(node("save-seed").hidden,true);
  assert.match(node("seed-preview").innerHTML, /&lt;Other&gt;/);
  node("open-seed-project").onclick();
  assert.equal(loaded.at(-1)[0],"proj:other");

  // Changing adoption mode revokes a preview, and candidate mode is sent explicitly.
  api = async (path, options) => { calls.push([path,options]); return options ? {project_key:"proj:target"} : preview; };
  bindSeedForm({projectKey:"proj:target", revision:"revision", dialog});
  await submit();
  node("seed-classification").value = "candidate";
  node("seed-classification").onchange();
  assert.equal(node("save-seed").hidden,true);
  await submit();
  assert.match(node("seed-preview").innerHTML, /候補として追加/);
  node("save-seed").onclick();
  assert.equal(node("seed-classification").disabled,true);
  await mutationQueue;
  assert.equal(JSON.parse(calls.at(-1)[1].body).classification,"candidate");
  assert.equal(node("seed-classification").disabled,false);
  node("seed-classification").value = "confirmed";

  // A failed request retains the input and allows a deliberate retry.
  api = async (path, options) => { if (options) throw new Error("保存失敗"); return preview; };
  bindSeedForm({projectKey:"proj:target", revision:"revision", dialog});
  await submit(); node("save-seed").onclick(); await mutationQueue;
  assert.equal(node("seed-message").textContent,"保存失敗");
  assert.equal(node("seed-value").value,"repo_url: https://forge.example/a/b");
  assert.equal(node("save-seed").disabled,false);

  api = async (path, options) => { if (options) throw Object.assign(new Error("古い更新"), {code:"stale_project"}); return preview; };
  node("save-seed").onclick(); await mutationQueue;
  assert.deepEqual(loaded.at(-1),["proj:target",{updateUrl:false}]);
  assert.match(statuses.at(-1)[0], /再操作/);

  // A late response may not restore a save action for an edited value.
  let finish;
  api = () => new Promise(resolve => { finish=resolve; });
  bindSeedForm();
  const pending = submit();
  node("seed-value").value = "https://new.example";
  node("seed-value").oninput();
  finish(preview); await pending;
  assert.equal(node("save-seed").hidden,true);

  // New registration is independent of introduction planning.
  calls=[];
  api = async (path, options) => { calls.push([path,options]); return options ? {project_key:"proj:new"} : {...preview,seed_type:"service_url",seed_value:"https://new.example"}; };
  await submit(); node("save-seed").onclick(); await mutationQueue;
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{seed:"https://new.example",type:""});
  assert.equal(loaded.at(-1)[0],"proj:new");
  assert.match(statuses.at(-1)[0], /紹介予定に入れる/);
  assert.ok(calls.every(([path]) => !path.includes("introduction-plan")));
})().catch(error => { console.error(error); process.exitCode=1; });
''')

    def test_existing_seed_source_uses_page_context_and_requires_new_preview(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"",value:"",hidden:true,isConnected:true,
    disabled:false,addEventListener(){},querySelectorAll(){return [];},focus(){},
    showModal(){},close(){}});
  return nodes.get(id);
};
const node = id => document.getElementById(id);
status = () => {};
global.requestAnimationFrame = callback => callback();
state.screen="browser";
state.pageUrl="https://source.example/app?token=secret#fragment";
state.detailRevision="opened-revision";
addProjectSeed("proj:target", "Target");
assert.match(node("seed-dialog-content").innerHTML, /見ていたURLを使う/);
assert.ok(!node("seed-dialog-content").innerHTML.includes("secret"));
assert.equal(node("seed-value").value, ""); // deliberate choice, no auto-fill or save
const submit = () => node("seed-form").onsubmit({preventDefault(){}});
const preview = {seed_type:"service_url",seed_value:"https://source.example/app",candidates:[]};
let calls=[], finish;
loadDetail=async()=>{}; loadList=async()=>{};
(async () => {
  api = () => new Promise(resolve => { finish=resolve; });
  node("seed-value").value="https://manual.example/";
  const pending=submit();
  node("use-page-seed").onclick();
  assert.equal(node("seed-value").value,"https://source.example/app");
  assert.equal(node("preview-seed").disabled,false);
  finish(preview); await pending;
  assert.equal(node("save-seed").hidden,true); // late preview cannot save old input
  node("save-seed").onclick();
  api=async(path,options)=>{ calls.push([path,options]); return options ? {project_key:"proj:target"} : preview; };
  await submit();
  assert.match(calls[0][0], /seed=https%3A%2F%2Fsource.example%2Fapp/);
  node("use-page-seed").onclick();
  assert.equal(node("save-seed").hidden,true); // choosing source also revokes a ready preview
  await submit(); node("save-seed").onclick();
  assert.equal(node("use-page-seed").disabled,true);
  node("use-page-seed").onclick(); // no input changes while saving
  await mutationQueue;
  assert.deepEqual(JSON.parse(calls.at(-1)[1].body),{seed:"https://source.example/app",type:"",project_key:"proj:target",expected_revision:"opened-revision"});
  assert.equal(node("use-page-seed").disabled,false);
  assert.ok(!seedForm().includes("use-page-seed"));
  state.screen="projects";
  addProjectSeed("proj:target");
  assert.ok(!node("seed-dialog-content").innerHTML.includes("use-page-seed"));
  state.screen="browser"; state.pageUrl="javascript:alert(1)";
  addProjectSeed("proj:target");
  assert.ok(!node("seed-dialog-content").innerHTML.includes("use-page-seed"));
})().catch(error => { console.error(error); process.exitCode=1; });
''')

    def test_browser_matches_show_nsid_reason_and_escape_values(self):
        self.run_javascript(r'''
const container = {innerHTML: "", insertAdjacentHTML: (_, html) => {container.innerHTML = html + container.innerHTML;}};
document.getElementById = () => container;
document.querySelectorAll = () => [];
state.screen = "browser";
renderList({items:[{project_key:"proj:123",name:"Example",matches:[{value:"com.example.<record>",classification:"candidate",match:"nsid_domain"}]}],status:"choose"});
assert.match(container.innerHTML, /com.example.&lt;record&gt;/);
assert.match(container.innerHTML, /ドメインに対応するNSID/);
assert.match(container.innerHTML, /NSIDの一致だけではサイトの帰属は確定しません/);
assert.ok(!container.innerHTML.includes("URL一致"));
''')

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

    def test_identifier_actions_are_once_per_value_not_per_role(self):
        self.run_javascript(r'''
const actions = {identifier_type:"repo_url", confirm:true, reject:true};
const item = {family:"repository", value:"https://github.com/example/app", classification:"candidate", kinds:["repo_url"],
 entries:[{kind:"repo_url", value:"https://github.com/example/app", role:"project_repository", classification:"candidate", actions},
          {kind:"repo_url", value:"https://github.com/example/app", role:"source_repository_record", classification:"candidate", actions}],
 actions:{identifier_type:"repo_url", confirm:true, reject:true, block:true, defer:true, split:true}};
const html = renderIdentifier(item);
for (const action of ["confirm-candidate", "block-identifier", "defer-identifier", "split-project"])
  assert.equal((html.match(new RegExp(`data-action="${action}"`, "g")) || []).length, 1);
assert.ok(!html.includes('data-action="reject-candidate"'));
assert.ok(!renderRelation({classification:"candidate", subject:{type:"service_url",value:"a"}, object:{type:"nsid",value:"b"}, predicate:"uses_collection"}).includes('data-action='));
const changes = renderReviewChanges({signals:[{kind:"editorial_refresh"}, {kind:"lexicon_publication_changed",fingerprint:'x"<tag>'}]});
assert.equal((changes.match(/data-action="acknowledge-review"/g) || []).length, 1);
assert.ok(changes.includes('x&quot;&lt;tag&gt;'));
''')

    def test_review_selection_covers_generated_signals_and_preserves_values(self):
        from project_registry_core import project_review
        source = ast.parse(inspect.getsource(project_review.compute_review_signals))
        kinds = sorted({value.value for node in ast.walk(source) if isinstance(node, ast.Dict)
                        for key, value in zip(node.keys, node.values)
                        if isinstance(key, ast.Constant) and key.value == "kind"
                        and isinstance(value, ast.Constant) and isinstance(value.value, str)})
        self.assertTrue(kinds)
        self.run_javascript("const generatedKinds = " + json.dumps(kinds) + r'''
assert.deepEqual([...reviewSignalKinds].sort(), generatedKinds);
state.screen = "review";
state.reviewView = "queue";
state.reviewSignalKind = "lexicon_publication_changed";
const html = listControls();
assert.match(html, /<select id="review-signal-kind">/);
assert.match(html, /value="lexicon_publication_changed" selected>Lexicon発行の変化/);
for (const kind of generatedKinds) assert.notEqual(displayLabel(kind), kind);
assert.ok(!html.includes("placeholder="));
state.reviewSignalKind = 'future"<signal>';
assert.match(listControls(), /value="future&quot;&lt;signal&gt;" selected/);
''')

    def test_merge_comparison_emphasizes_only_matches_and_collapses_diagnostics(self):
        self.run_javascript(r'''
const nodes = new Map();
document.getElementById = id => {
  if (!nodes.has(id)) nodes.set(id, {innerHTML:"", listeners:{},
    addEventListener(type, listener) { this.listeners[type] = listener; }});
  return nodes.get(id);
};
document.querySelectorAll = () => [];
document.body = {classList:{add() {}}};
syncUrl = () => {};
global.window = {scrollTo() {}};
const common = "https://common.example";
const identifiers = [common, "https://distinct.example"].map(value => ({value, family:"service", kinds:["service_url"], classification:"confirmed"}));
const requests = [];
api = async path => {
  requests.push(path);
  return {source:{project_key:"a", identifiers}, target:{project_key:"b", identifiers},
  source_pair_candidate:{reasons:{candidate_confirmed_identifiers:[`service_url: ${common} (service)`]}, identifier_matches:[{kind:"service_url", role:"service", value:common, project_classification:"candidate", other_classification:"confirmed"}]},
  first_creator_details_included:path.includes("include_first_creator_details=1"),
  did_comparison:{publisher_publisher:["did:plc:publisher"], first_observer_first_observer:path.includes("include_first_creator_details=1") ? ["did:plc:shared"] : null},
  source_lexicon_publisher_dids:{"org.example.schema":"did:plc:publisher"},
  source_collection_first_creator_dids:{"org.example.source":"did:plc:source"},
  target_collection_first_creator_dids:{"org.example.target":"did:plc:target"},
  source_collection_first_creator_details:{sample:"not_found"},
  target_collection_first_creator_details:{sample:"error"}};
};
loadMergeDetail("a", "b").then(async () => {
  assert.ok(!requests[0].includes("include_first_creator_details"));
  assert.match(nodes.get("detail").innerHTML, /id="load-first-creator-heuristic"/);
  assert.ok(!nodes.get("detail").innerHTML.includes("did:plc:shared"));
  assert.match(nodes.get("detail").innerHTML, /did:plc:publisher/);
  assert.match(nodes.get("detail").innerHTML, /未照会/);
  await nodes.get("load-first-creator-heuristic").listeners.click();
  assert.ok(requests[1].includes("include_first_creator_details=1"));
  const html = nodes.get("detail").innerHTML;
  assert.match(html, /<strong class="matched-identifier"[^>]*><a[^>]*href="https:\/\/common.example\/"/);
  assert.equal((html.match(/class="matched-identifier"/g) || []).length, 2);
  assert.match(html, /<details class="detail-section"><summary>候補と判断された理由<\/summary>/);
  const creatorSection = html.match(/<section class="detail-section"><h3>発行者DIDの一致[\s\S]*?<\/section>/)[0];
  const diagnostics = [...creatorSection.matchAll(/<details\b([^>]*)><summary>(.*?)<\/summary>([\s\S]*?)<\/details>/g)];
  assert.deepEqual(diagnostics.map(item => item[2]), ["参照したDIDと診断"]);
  assert.ok(!/\bopen\b/.test(diagnostics[0][1]));
  assert.ok(!diagnostics[0][3].includes("<details"));
  for (const value of ["not_found", "error", "did:plc:source", "did:plc:target"]) assert.ok(diagnostics[0][3].includes(value));
  const visibleResults = creatorSection.replace(/<details\b[\s\S]*?<\/details>/g, "")
    .replace(/<div id="merge-did-[^"]+"[\s\S]*?<\/div><\/td>/g, "</td>");
  assert.ok(!visibleResults.includes("did:plc:"));
  assert.ok(!visibleResults.includes("did-match-legend"));
  assert.ok(!creatorSection.includes("数字で内訳"));
  assert.ok(!creatorSection.includes("現在"));
  assert.match(creatorSection, /Lexicon開発者/);
  assert.ok(!creatorSection.includes("開発者・考案者"));
  assert.match(creatorSection, /観測順の先頭に返るrecord発行者/);
  assert.match(creatorSection, /このrecord発行者がLexicon開発者/);
  assert.ok(!creatorSection.includes("bsky.network"));
  assert.ok(!creatorSection.includes("2026-10-17確認"));
  assert.ok(creatorSection.includes('href="https://github.com/bluesky-social/indigo/blob/3beb6e491b793834f6c66aed9963a255f56f1b6b/cmd/collectiondir/pebble.go#L330-L364"'));
  assert.match(creatorSection, /target="_blank" rel="noreferrer">collectiondirの公開実装<\/a>/);
  assert.match(visibleResults, /class="did-match-matrix"/);
  assert.equal((visibleResults.match(/class="did-match-count"/g) || []).length, 2);
  assert.ok(!creatorSection.includes('id="load-first-creator-heuristic"'));
  assert.match(html, /<template><p>Lexiconの発行者と、観測順の先頭に返るrecord発行者を比較/);
  assert.match(html, /not_found/);
  api = async () => ({source:{project_key:"a"}, target:{project_key:"b"}, merge_blocked:true});
  await loadMergeDetail("a", "b", {includeFirstCreator:true});
  assert.match(nodes.get("detail").innerHTML, /<p class="notice">別プロジェクト指定によりマージが制限/);
}).catch(error => { console.error(error); process.exitCode = 1; });
''')

    def test_merge_did_matrix_maps_each_direction_and_escapes_breakdown(self):
        self.run_javascript(r'''
const unsafe = '<img src=x onerror=alert(1)>';
const html = renderMergeDidComparison({
  did_comparison:{publisher_publisher:["did:plc:pub"],
    source_publisher_target_first_observer:["did:plc:cross", unsafe],
    source_first_observer_target_publisher:[], first_observer_first_observer:null},
  source_lexicon_publisher_dids:{"org.source.a":"did:plc:pub", "org.source.b":"did:plc:pub", "org.source.cross":"did:plc:cross", [unsafe]:unsafe},
  target_lexicon_publisher_dids:{"net.target.pub":"did:plc:pub"},
  target_collection_first_creator_dids:{"net.target.record":"did:plc:cross"},
  source_non_record_nsids:[unsafe],
  source_lexicon_publisher_details:{[unsafe]:[{did:unsafe, observed_at:"2026-10-13", status:"unverified"}]}
});
assert.match(html, /<th scope="col">元 ↓ ／ 先 →<\/th><th scope="col">Lexicon<\/th><th scope="col">record<\/th>/);
assert.deepEqual([...html.matchAll(/class="did-match-count" popovertarget="([^"]+)"[^>]*>(\d+)<\/button>/g)].map(x=>[x[1],x[2]]), [
  ["merge-did-publisher_publisher","1"],
  ["merge-did-source_publisher_target_first_observer","2"],
  ["merge-did-source_first_observer_target_publisher","0"]
]);
assert.match(html, /aria-label="未照会">—/);
assert.ok(!html.includes('id="merge-did-first_observer_first_observer"'));
const publisher = html.match(/id="merge-did-publisher_publisher"[\s\S]*?<\/div><\/td>/)[0];
for (const nsid of ["org.source.a","org.source.b","net.target.pub"]) assert.ok(publisher.includes(nsid));
assert.ok(!publisher.includes("org.source.cross"));
const cross = html.match(/id="merge-did-source_publisher_target_first_observer"[\s\S]*?<\/div><\/td>/)[0];
for (const nsid of ["org.source.cross","net.target.record"]) assert.ok(cross.includes(nsid));
assert.ok(!cross.includes("org.source.a"));
assert.match(html, /popover role="dialog" aria-label="Lexicon → recordの一致内訳"/);
assert.match(html, /popovertargetaction="hide" aria-label="内訳を閉じる"/);
assert.match(html, /一致するDIDはありません。/);
assert.ok(!html.includes(unsafe));
assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
''')

    def test_relation_display_separates_entities_and_escapes_values(self):
        self.run_javascript(r'''
const unsafe = '<img src=x onerror=alert(1)>';
const html = renderRelation({subject:{type:"repo", value:unsafe}, predicate:"defines_schema",
  object:{type:"collection", value:"org.example.post"}, classification:"candidate", source:unsafe});
assert.ok(!html.includes("<img"));
assert.match(html, /relation-entity/);
assert.match(html, /relation-edge">─ スキーマを定義 →/);
assert.match(html, /&lt;img/);
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
assert.ok(!html.includes("実行順:"));
const sequence = renderSchedules({items:[{preset_id:"multi", label:"複合処理", description:"処理を順番に実行します。",
  steps:[{label:unsafe}, {label:"enrich --all --new-only"}]}]});
assert.match(sequence, /実行順: &lt;img[^<]* → 未調査対象の調査/);
assert.ok(!sequence.includes(unsafe));
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
assert.match(html, /<dt>サービス<\/dt><dd><a[^>]*href="https:\/\/example.com\/app"/);
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

    def test_nsid_links_keep_collection_query_and_escape_untrusted_values(self):
        self.run_javascript(r'''
for (const classification of ['confirmed', 'candidate', 'rejected']) {
  const html = renderIdentifiers([{family:'schema', kinds:['nsid'], value:'app.beaconbits.beacon',
    classification, providers:[], role_labels:['owned_nsid'], entries:[]}]);
  assert.match(html, /href="https:\/\/ufos.microcosm.blue\/collection\/\?nsid=app.beaconbits.beacon" target="_blank" rel="noreferrer">app.beaconbits.beacon<\/a>/);
}
const value = 'org.example.<tag>"&nsid=other#fragment';
const html = identifierValueLink({kinds:['nsid'], value});
const target = new URL(html.match(/href="([^"]+)"/)[1]);
assert.equal(target.origin, 'https://ufos.microcosm.blue');
assert.deepEqual([...target.searchParams], [['nsid', value]]);
assert.equal(target.hash, '');
assert.ok(!html.includes('<tag>'));
assert.match(html, /org.example.&lt;tag&gt;&quot;&amp;nsid=other#fragment<\/a>/);
assert.equal(identifierValueLink({kinds:['nsid'], value:''}), '');
assert.ok(!identifierValueLink({kinds:['service_url'], value:'javascript:alert(1)'}).includes('<a'));
assert.match(identifierValueLink({kinds:['repo_url'], value:'https://example.com/repo'}), /href="https:\/\/example.com\/repo"/);
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
  assert.match(document.getElementById("status").textContent, /^次の項目へ進みました。残り/);
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
assert.equal(scheduleMessage("running enrich --all"), "実行中: 通常調査");
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
const score = {total: 14, components: {service_evidence: 14, recent_activity: 0}};
const candidate = {score_breakdown: score, service_evidence_group: "confirmed", priority_group: "normal",
  candidate_visibility: {group:"eligible"}, attention: {group:"unreviewed"},
  atproto_usage: {source:"none", role:"unknown", reason:"routine explanation"}};
const compact = promotionCardDetails(candidate);
assert.match(compact, /スコア 14/);
assert.ok(!compact.includes("atproto利用 未評価"));
assert.ok(!compact.includes(">未確認<"));
assert.ok(!compact.includes("routine explanation"));
assert.ok(!compact.includes("通常の優先度"));
assert.ok(!compact.includes("紹介候補一覧の対象"));
assert.ok(!renderPromotionScore(score).includes("紹介材料の不足"));
assert.match(promotionCardDetails({...candidate, description_missing:["summary", "tagline"]}), /未記入: 説明・概要/);
assert.equal(descriptionMissingText([]), "なし");
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
const recent = renderServiceObservations([{surface_confirmed:false, observed_at:"2026-10-12T00:00:00Z",
  last_success:{observed_at:"2026-10-01T00:00:00Z"}}]);
assert.match(recent, /利用画面は未確認/);
assert.match(recent, /最後に利用画面を確認/);
assert.match(recent, /2026-10-01/);
assert.match(renderServiceObservations([{surface_confirmed:true}]), /利用画面を確認/);
''')
