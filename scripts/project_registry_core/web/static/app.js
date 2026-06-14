const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const list = (value) => Array.isArray(value) ? value : [];
const settingsKey = "projectRegistryWebV2Settings";
const defaults = { reviewLimit: 50, projectLimit: 50, mergeLimit: 50, mergeMode: "moderate", includePersonalTools: false };
const state = {
  screen: "review",
  reviewView: "queue",
  selected: "",
  listRequest: 0,
  detailRequest: 0,
  settings: loadSettings(),
  server: null,
  projectSearch: {
    query: "",
    tracking: "",
    service: "",
    contribution: "",
    candidates: "any",
    errors: "any",
    sort: "last_seen_desc",
  },
};

const params = new URLSearchParams(location.search);
if (params.get("token")) {
  localStorage.setItem("projectRegistryToken", params.get("token"));
  params.delete("token");
  history.replaceState(null, "", location.pathname + (params.toString() ? `?${params}` : ""));
}
let token = localStorage.getItem("projectRegistryToken") || prompt("Project Registry token") || "";
if (token) localStorage.setItem("projectRegistryToken", token);

function loadSettings() {
  try { return {...defaults, ...JSON.parse(localStorage.getItem(settingsKey) || "{}")}; }
  catch { return {...defaults}; }
}
function saveSettings() { localStorage.setItem(settingsKey, JSON.stringify(state.settings)); }
function status(message, error = false) { $("status").textContent = message || ""; $("status").style.color = error ? "var(--danger)" : ""; }
function safeUrl(value) { try { const url = new URL(String(value || "")); return ["http:", "https:"].includes(url.protocol) ? url.href : ""; } catch { return ""; } }
function link(value) { const href = safeUrl(value); return href ? `<a href="${esc(href)}" target="_blank" rel="noreferrer">${esc(value)}</a>` : esc(value); }
function pill(value, kind = "") { return value ? `<span class="pill ${esc(kind)}">${esc(value)}</span>` : ""; }
function json(value) { return `<pre>${esc(JSON.stringify(value, null, 2))}</pre>`; }

async function api(path, options = {}) {
  const headers = {"X-Project-Registry-Token": token, ...(options.headers || {})};
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, {...options, headers});
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error?.message || response.statusText);
  return payload;
}

function setScreen(screen) {
  state.listRequest += 1;
  state.detailRequest += 1;
  state.screen = screen;
  state.selected = "";
  document.body.dataset.screen = screen;
  document.body.classList.remove("detail-open");
  document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => button.classList.toggle("active", button.dataset.screen === screen));
  const workspace = $("workspace");
  const single = $("single-pane");
  if (["review", "projects"].includes(screen)) {
    workspace.hidden = false; single.hidden = true; loadList();
  } else {
    workspace.hidden = true; single.hidden = false;
    screen === "operations" ? renderOperations() : renderSettings();
  }
}

function listControls() {
  if (state.screen === "review") {
    return `<div class="controls"><div class="subnav">
      <button data-review-view="queue" class="${state.reviewView === "queue" ? "active" : ""}">Review Queue</button>
      <button data-review-view="promotion" class="${state.reviewView === "promotion" ? "active" : ""}">Promotion</button>
      <button data-review-view="merge" class="${state.reviewView === "merge" ? "active" : ""}">Merge</button>
    </div></div>`;
  }
  return `<div class="controls">
    <div class="toolbar"><input id="project-query" type="search" placeholder="Search name, NSID, account, URL" value="${esc(state.projectSearch.query)}"><button id="search-projects" class="primary">Search</button><button id="new-seed">New Seed</button></div>
    <details><summary>Filters</summary><div class="filter-grid">
      <label>Tracking states<input id="filter-tracking" placeholder="active, watchlist" value="${esc(state.projectSearch.tracking)}"></label>
      <label>Service status<input id="filter-service" placeholder="live, unknown" value="${esc(state.projectSearch.service)}"></label>
      <label>Contribution<input id="filter-contribution" placeholder="core, substantial" value="${esc(state.projectSearch.contribution)}"></label>
      <label>Candidates<select id="filter-candidates">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.candidates)}</select></label>
      <label>Errors<select id="filter-errors">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.errors)}</select></label>
      <label>Sort<select id="filter-sort">${selectOptions([["last_seen_desc", "Recently seen"], ["last_enriched_asc", "Needs enrich"], ["name_asc", "Name"]], state.projectSearch.sort)}</select></label>
    </div></details>
  </div>`;
}

async function loadList() {
  const request = ++state.listRequest;
  $("list-controls").innerHTML = listControls();
  $("list").innerHTML = `<p class="muted">Loading...</p>`;
  bindControls();
  try {
    let data;
    if (state.screen === "projects") data = await api(projectSearchPath());
    else if (state.reviewView === "promotion") data = await api(`/api/promotion-queue?limit=${state.settings.reviewLimit}&include_personal_tools=${state.settings.includePersonalTools}`);
    else if (state.reviewView === "merge") data = await api(`/api/merge-batch?mode=${encodeURIComponent(state.settings.mergeMode)}&limit=${state.settings.mergeLimit}`);
    else data = await api(`/api/review-queue?limit=${state.settings.reviewLimit}`);
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
    has_candidates: state.projectSearch.candidates,
    has_errors: state.projectSearch.errors,
    sort: state.projectSearch.sort,
  };
  Object.entries(values).forEach(([key, value]) => { if (value && value !== "any") query.set(key, value); });
  return `/api/projects?${query}`;
}

function selectOptions(options, selected) {
  return options.map(([value, label]) => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(label)}</option>`).join("");
}

function captureProjectSearch() {
  state.projectSearch = {
    query: $("project-query")?.value || "",
    tracking: $("filter-tracking")?.value || "",
    service: $("filter-service")?.value || "",
    contribution: $("filter-contribution")?.value || "",
    candidates: $("filter-candidates")?.value || "any",
    errors: $("filter-errors")?.value || "any",
    sort: $("filter-sort")?.value || "last_seen_desc",
  };
}

function bindControls() {
  document.querySelectorAll("[data-review-view]").forEach((button) => button.onclick = () => { state.reviewView = button.dataset.reviewView; loadList(); });
  $("search-projects")?.addEventListener("click", () => { captureProjectSearch(); loadList(); });
  $("project-query")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { captureProjectSearch(); loadList(); } });
  $("new-seed")?.addEventListener("click", renderNewSeed);
}

function normalizeQueueItem(item) {
  return item.project || item;
}

function renderList(data) {
  const items = list(data.items);
  const count = data.count ?? items.length;
  $("list").innerHTML = `<div class="meta-row"><strong>${count}</strong> results</div>` + (items.length ? items.map((item, index) => {
    const project = normalizeQueueItem(item);
    const key = project.project_key || item.project_key || item.source_project_key || "";
    const name = project.name || item.source_name || key;
    const mergeTarget = item.target_project_key;
    const signals = list(item.signals).map((signal) => pill(signal.kind || signal, "candidate")).join(" ");
    return `<button class="card ${state.selected === key ? "selected" : ""}" data-project-key="${esc(key)}" ${mergeTarget ? `data-merge-target="${esc(mergeTarget)}"` : ""}>
      <span class="card-title"><strong>${esc(name)}</strong>${pill(project.tracking_state || item.recommendation?.confidence || "")}</span>
      <span class="meta-row">${pill(project.service_status || "")}${pill(project.atproto_contribution_level || "")}${project.has_candidates ? pill("candidates", "candidate") : ""}</span>
      ${project.tagline ? `<span>${esc(project.tagline)}</span>` : ""}${signals ? `<span class="meta-row">${signals}</span>` : ""}
      ${mergeTarget ? `<span class="muted">→ ${esc(item.target_name || mergeTarget)}</span>` : ""}
    </button>`;
  }).join("") : `<div class="empty-state"><p>No matching projects.</p></div>`);
  document.querySelectorAll("[data-project-key]").forEach((card) => card.onclick = () => {
    const source = card.dataset.projectKey;
    const target = card.dataset.mergeTarget;
    target ? loadMergeDetail(source, target) : loadDetail(source);
  });
}

async function loadDetail(key) {
  const request = ++state.detailRequest;
  state.selected = key; document.body.classList.add("detail-open");
  $("detail").innerHTML = `<p class="muted">Loading ${esc(key)}...</p>`;
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
function actionButton(label, action, data = {}, style = "small") {
  const attrs = Object.entries(data).map(([key, value]) => `data-${key}="${esc(value)}"`).join(" ");
  return `<button class="${style}" data-action="${action}" ${attrs}>${esc(label)}</button>`;
}

function renderDetail(data) {
  const project = data.project;
  const profile = project.profile || {};
  const identifiers = list(data.identifiers);
  const relations = list(data.relations);
  const decisions = data.decisions || {active: [], history: []};
  const evaluation = data.evaluation || {};
  const investigation = data.investigation;
  const recommendation = data.merge_recommendation || {};
  const hasPromotionHold = list(decisions.active).some((item) => item.kind === "defer_promotion");
  $("detail").innerHTML = `
    <div class="detail-header">
      <button class="mobile-back secondary" id="detail-back">Back to list</button>
      <p class="eyebrow">${esc(project.project_key)}</p>
      <h2>${esc(project.name || project.project_key)}</h2>
      <div class="meta-row">${pill(project.tracking_state)}${pill(project.service_status)}${project.introduced_url ? pill("introduced", "confirmed") : ""}</div>
      <div class="actions"><select id="tracking-state">${(state.server?.tracking_states || []).map((value) => `<option ${value === project.tracking_state ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><button id="save-tracking">Set state</button>${hasPromotionHold ? "" : actionButton("Defer promotion 7d", "defer-promotion")}${recommendation.auto_merge_ready ? actionButton("Apply merge recommendation", "merge-recommendation", {}, "danger") : ""}</div>
    </div>
    ${section("Profile", `<dl class="kv"><dt>Website</dt><dd>${project.website_url ? link(project.website_url) : "None"}</dd><dt>Tagline</dt><dd>${esc(profileValue(profile, "tagline")) || "None"}</dd><dt>Summary</dt><dd>${esc(profileValue(profile, "summary")) || "None"}</dd><dt>Notes</dt><dd>${esc(profileValue(profile, "notes")) || "None"}</dd><dt>Last seen</dt><dd>${esc(project.last_seen_at)}</dd><dt>Last enriched</dt><dd>${esc(project.last_enriched_at || "Never")}</dd></dl>`)}
    ${section("Identifiers", identifiers.length ? identifiers.map(renderIdentifier).join("") : `<p class="muted">No identifiers.</p>`)}
    ${section("Relations", relations.length ? relations.map((item) => `<div class="relation ${esc(item.classification)}"><div>${pill(item.classification, item.classification)} ${esc(item.subject.type)}:${esc(item.subject.value)} <strong>${esc(item.predicate)}</strong> ${esc(item.object.type)}:${esc(item.object.value)}</div><div class="muted">${esc(item.source)} ${item.locator ? `· ${esc(item.locator)}` : ""} ${item.method ? `· ${esc(item.method)}` : ""}</div></div>`).join("") : `<p class="muted">No saved relations.</p>`)}
    ${section("Decisions / Guards", renderDecisions(decisions))}
    ${section("Evaluation", `<dl class="kv"><dt>atproto contribution</dt><dd>${pill(evaluation.atproto_contribution_level)} score ${esc(evaluation.atproto_contribution_score)}</dd><dt>Technical interest</dt><dd>${pill(evaluation.technical_interest_level)} score ${esc(evaluation.technical_interest_score)}</dd><dt>Service confidence</dt><dd>${esc(evaluation.service_status_confidence)}</dd><dt>Promotion</dt><dd>${evaluation.promotion_ready ? pill("ready", "confirmed") : list(evaluation.promotion_blockers).map((item) => pill(item, "candidate")).join(" ") || "Not ready"}</dd><dt>Technical summary</dt><dd>${esc(evaluation.technical_interest_summary || "None")}</dd></dl>`)}
    ${section("Latest Investigation", renderInvestigation(investigation))}
    ${section("Raw Data", `<details><summary>Project payload</summary>${json(data)}</details>`)}
  `;
  $("detail-back").onclick = () => document.body.classList.remove("detail-open");
  $("save-tracking").onclick = () => confirmMutation("Set tracking state", `Set ${project.name || project.project_key} to ${$("tracking-state").value}?`, `/api/projects/${encodeURIComponent(project.project_key)}/tracking-state`, {tracking_state: $("tracking-state").value}, false);
  bindDetailActions(project.project_key);
}

function renderIdentifier(item) {
  const canCandidate = item.classification === "candidate" && ["service_url", "repo_url"].includes(item.kind);
  const canOperate = ["service_url", "repo_url", "nsid"].includes(item.kind);
  return `<div class="identifier ${esc(item.classification)}">
    <div class="identifier-value">${["service_url", "repo_url"].includes(item.kind) ? link(item.value) : esc(item.value)}</div>
    <div class="meta-row">${pill(item.classification, item.classification)}${pill(item.kind)}${pill(item.role)}${item.is_primary ? pill("primary", "confirmed") : ""}${pill(item.provider)}</div>
    <div class="actions">
      ${canCandidate ? actionButton("Confirm", "confirm-candidate", {type:item.kind, value:item.value}, "small primary") + actionButton("Reject", "reject-candidate", {type:item.kind, value:item.value}, "small danger") : ""}
      ${canOperate ? actionButton("Block", "block-identifier", {type:item.kind, value:item.value}, "small danger") + actionButton("Defer 30d", "defer-identifier", {type:item.kind, value:item.value}) + actionButton("Split", "split-project", {type:item.kind, value:item.value}) : ""}
    </div>
  </div>`;
}

function renderDecisions(decisions) {
  const active = list(decisions.active);
  const history = list(decisions.history);
  const current = active.length ? active.map((item) => `<div class="decision"><div>${pill(item.kind, "candidate")} ${esc(item.target_type)} ${esc(item.target_value)}</div><div class="muted">${esc(item.note || "No note")} ${item.expires_at ? `· expires ${esc(item.expires_at)}` : ""}</div><div class="actions">${item.kind === "block_identifier" ? actionButton("Clear block", "clear-block-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_identifier" ? actionButton("Clear defer", "clear-defer-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_promotion" ? actionButton("Clear hold", "clear-promotion-deferral") : ""}${item.kind === "separate_project" ? actionButton("Clear separate", "clear-separate", {other:item.target_value}) : ""}</div></div>`).join("") : `<p class="muted">No active decisions.</p>`;
  return current + (history.length ? `<details><summary>Decision history (${history.length})</summary>${json(history)}</details>` : "");
}

function renderInvestigation(run) {
  if (!run) return `<p class="muted">No saved investigation run.</p>`;
  return `<dl class="kv"><dt>Run</dt><dd>${esc(run.run_id)}</dd><dt>Completed</dt><dd>${esc(run.completed_at)}</dd><dt>Records</dt><dd>${esc(run.record_count)}</dd><dt>Failures</dt><dd>${esc(run.failure_count)}</dd></dl>
    <div>${list(run.collectors).map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(item.source)}</strong></div><div class="muted">${esc(item.operation)} · ${esc(item.request_count || 0)} requests · ${esc(item.result_count || 0)} results${item.stop_reason ? ` · ${esc(item.stop_reason)}` : ""}</div></div>`).join("") || `<p class="muted">No collector summaries.</p>`}</div>
    ${run.failures?.length ? `<details><summary>Failures</summary>${json(run.failures)}</details>` : ""}`;
}

function bindDetailActions(projectKey) {
  document.querySelectorAll("[data-action]").forEach((button) => button.onclick = () => {
    const action = button.dataset.action;
    const payload = {identifier_type: button.dataset.type || "", value: button.dataset.value || "", other_project_key: button.dataset.other || ""};
    const needsNote = ["reject-candidate", "block-identifier", "defer-identifier", "split-project", "defer-promotion"].includes(action);
    confirmMutation(action.replaceAll("-", " "), `${action.replaceAll("-", " ")} ${payload.value || payload.other_project_key || projectKey}?`, `/api/projects/${encodeURIComponent(projectKey)}/${action}`, payload, needsNote);
  });
}

function confirmMutation(title, message, path, payload, note) {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-note-label").hidden = !note;
  $("confirm-note").value = "";
  dialog.showModal();
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    if (note) payload.note = $("confirm-note").value.trim();
    try {
      status("Saving...");
      const response = await api(path, {method:"POST", body:JSON.stringify(payload)});
      status("Saved.");
      if (response.detail) {
        renderDetail(response.detail);
      } else if (response.project_key || response.target_project_key) {
        await loadDetail(response.target_project_key || response.project_key);
      } else {
        document.body.classList.remove("detail-open");
      }
      await loadList();
    } catch (error) { status(error.message, true); }
  };
}

async function loadMergeDetail(source, target) {
  const request = ++state.detailRequest;
  state.selected = source; document.body.classList.add("detail-open");
  try {
    const data = await api(`/api/merge-pair?source_project_key=${encodeURIComponent(source)}&target_project_key=${encodeURIComponent(target)}`);
    if (request !== state.detailRequest || state.selected !== source) return;
    $("detail").innerHTML = `<button class="mobile-back secondary" id="detail-back">Back to list</button><p class="eyebrow">Merge review</p><h2>${esc(source)} → ${esc(target)}</h2>${section("Comparison", json(data.comparison || data))}${section("Preview", json(data.merged_preview || {}))}<div class="actions"><button id="mark-separate">Keep separate</button><button id="execute-merge" class="danger">Merge projects</button></div>`;
    $("detail-back").onclick = () => document.body.classList.remove("detail-open");
    $("mark-separate").onclick = () => confirmMerge("Keep projects separate", "separate", source, target);
    $("execute-merge").onclick = () => confirmMerge("Merge projects", "merge", source, target);
  } catch (error) {
    if (request === state.detailRequest && state.selected === source) status(error.message, true);
  }
}

function confirmMerge(title, action, source, target) {
  confirmMutation(title, `${title}: ${source} → ${target}?`, "/api/merge-batch/action", {action, source_project_key:source, target_project_key:target}, false);
}

function renderNewSeed() {
  state.detailRequest += 1;
  state.selected = "";
  $("detail").innerHTML = `<button class="mobile-back secondary" id="detail-back">Back to list</button><p class="eyebrow">Projects</p><h2>New Seed</h2><div class="controls"><label>NSID, repository URL, or service URL<input id="seed-value" type="text"></label><div class="actions"><button id="preview-seed" class="primary">Preview</button></div></div><div id="seed-preview"></div>`;
  document.body.classList.add("detail-open");
  $("detail-back").onclick = () => document.body.classList.remove("detail-open");
  $("preview-seed").onclick = async () => {
    const value = $("seed-value").value.trim();
    try {
      const data = await api(`/api/seeds/preview?seed=${encodeURIComponent(value)}`);
      $("seed-preview").innerHTML = section("Preview", json(data)) + (data.can_create ? `<button id="create-seed" class="primary">Create project</button>` : `<p class="notice">This seed already belongs to ${esc(data.exact_project_key)}.</p>`);
      $("create-seed")?.addEventListener("click", () => confirmMutation("Create project", `Create a project for ${value}?`, "/api/seeds", {seed:value}, false));
    } catch (error) { status(error.message, true); }
  };
}

function renderOperations() {
  $("single-pane").innerHTML = `<p class="eyebrow">Deferred Web job service</p><h2>Operations</h2><p class="notice">Long-running operations are intentionally not connected to the Web UI yet. Use the CLI while progress, cancellation, and checkpoint behavior are moved behind a background job service.</p><div class="operation-grid">
    <article class="operation-card"><h3>Enrich</h3><p>Single-project and bulk investigation.</p><code>project-registry enrich -p proj:...</code><code>project-registry enrich --all</code></article>
    <article class="operation-card"><h3>Sync</h3><p>Introduced posts, collections, GitHub, Semble, and Tangled.</p><code>project-registry sync-github-search</code><code>project-registry sync-tangled-repos</code></article>
    <article class="operation-card"><h3>Review Ops</h3><p>Tracking-state classification dry-runs and explicit execution.</p><code>project-registry review-stalled</code><code>project-registry review-out-of-scope</code></article>
    <article class="operation-card"><h3>Investigation Replay</h3><p>Replay and benchmark saved evidence without network access.</p><code>project-registry replay-investigation -p proj:...</code><code>project-registry benchmark-investigation</code></article>
  </div>`;
}

async function renderSettings() {
  try { state.server = state.server || await api("/api/settings"); } catch (error) { status(error.message, true); }
  $("single-pane").innerHTML = `<p class="eyebrow">Local Web UI</p><h2>Settings</h2><div class="controls filter-grid">
    <label>Review limit<input id="setting-review" type="number" min="1" max="500" value="${esc(state.settings.reviewLimit)}"></label>
    <label>Project limit<input id="setting-project" type="number" min="1" max="500" value="${esc(state.settings.projectLimit)}"></label>
    <label>Merge limit<input id="setting-merge-limit" type="number" min="1" max="500" value="${esc(state.settings.mergeLimit)}"></label>
    <label>Merge mode<select id="setting-merge-mode"><option>strict</option><option>moderate</option><option>broad</option></select></label>
    <label><span>Promotion queue</span><span><input id="setting-personal" type="checkbox" ${state.settings.includePersonalTools ? "checked" : ""}> Include personal tools</span></label>
  </div><div class="actions"><button id="save-settings" class="primary">Save settings</button><button id="clear-token" class="danger">Clear API token</button></div>
  ${section("Server", `<dl class="kv"><dt>Schema</dt><dd>v${esc(state.server?.schema_version || "?")}</dd><dt>Database</dt><dd>${esc(state.server?.db_path || "Unavailable")}</dd><dt>Background jobs</dt><dd>Not connected</dd></dl>`)}
  `;
  $("setting-merge-mode").value = state.settings.mergeMode;
  $("save-settings").onclick = () => {
    state.settings = {reviewLimit:Number($("setting-review").value), projectLimit:Number($("setting-project").value), mergeLimit:Number($("setting-merge-limit").value), mergeMode:$("setting-merge-mode").value, includePersonalTools:$("setting-personal").checked};
    saveSettings(); status("Settings saved.");
  };
  $("clear-token").onclick = () => { localStorage.removeItem("projectRegistryToken"); token = ""; status("Token cleared. Reload to sign in again."); };
}

document.querySelectorAll(".primary-nav [data-screen]").forEach((button) => button.addEventListener("click", () => setScreen(button.dataset.screen)));
$("refresh").addEventListener("click", () => setScreen(state.screen));
api("/api/settings").then((server) => { state.server = server; loadList(); }).catch((error) => status(error.message, true));
