const $ = (id) => document.getElementById(id);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const list = (value) => Array.isArray(value) ? value : [];
const settingsKey = "projectRegistryWebV2Settings";
const defaults = { reviewLimit: 50, projectLimit: 50, mergeLimit: 50, mergeMode: "moderate", includePersonalTools: false };
const state = {
  screen: "review",
  reviewView: "queue",
  selected: "",
  detailTab: "overview",
  detailRevision: "",
  mergeSourceRevision: "",
  mergeTargetRevision: "",
  openJobDetails: new Set(),
  jobDetailCache: new Map(),
  listRequest: 0,
  detailRequest: 0,
  operationTimer: null,
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
  if (!response.ok) {
    const error = new Error(payload.error?.message || response.statusText);
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
  state.selected = query.get("project") || "";
  const tab = query.get("tab");
  if (["overview", "identifiers", "relations", "investigation", "decisions", "raw"].includes(tab)) state.detailTab = tab;
  state.projectSearch = {
    query: query.get("q") || "",
    tracking: query.get("tracking") || "",
    service: query.get("service") || "",
    contribution: query.get("contribution") || "",
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
  if (state.screen === "projects") {
    const values = {
      q: state.projectSearch.query,
      tracking: state.projectSearch.tracking,
      service: state.projectSearch.service,
      contribution: state.projectSearch.contribution,
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
      <label>Identifier kind<input id="filter-identifier-kind" placeholder="nsid, account, repo_url" value="${esc(state.projectSearch.identifierKind)}"></label>
      <label>Identifier value<input id="filter-identifier-value" value="${esc(state.projectSearch.identifierValue)}"></label>
      <label>Identifier classification<input id="filter-identifier-classification" placeholder="confirmed, candidate" value="${esc(state.projectSearch.identifierClassification)}"></label>
      <label>Relation predicate<input id="filter-relation-predicate" placeholder="defines_schema" value="${esc(state.projectSearch.relationPredicate)}"></label>
      <label>Relation subject type<input id="filter-relation-subject-type" value="${esc(state.projectSearch.relationSubjectType)}"></label>
      <label>Relation subject value<input id="filter-relation-subject-value" value="${esc(state.projectSearch.relationSubjectValue)}"></label>
      <label>Relation object type<input id="filter-relation-object-type" value="${esc(state.projectSearch.relationObjectType)}"></label>
      <label>Relation object value<input id="filter-relation-object-value" value="${esc(state.projectSearch.relationObjectValue)}"></label>
      <label>Relation source<input id="filter-relation-source" value="${esc(state.projectSearch.relationSource)}"></label>
      <label>Decision kind<input id="filter-decision-kind" placeholder="block_identifier" value="${esc(state.projectSearch.decisionKind)}"></label>
      <label>Decision target type<input id="filter-decision-target-type" value="${esc(state.projectSearch.decisionTargetType)}"></label>
      <label>Decision target value<input id="filter-decision-target-value" value="${esc(state.projectSearch.decisionTargetValue)}"></label>
      <label>Decision status<select id="filter-decision-status">${selectOptions([["", "Any"], ["active", "Active"], ["expired", "Expired"], ["cleared", "Cleared"]], state.projectSearch.decisionStatus)}</select></label>
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

function captureProjectSearch() {
  state.projectSearch = {
    query: $("project-query")?.value || "",
    tracking: $("filter-tracking")?.value || "",
    service: $("filter-service")?.value || "",
    contribution: $("filter-contribution")?.value || "",
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
  document.querySelectorAll("[data-review-view]").forEach((button) => button.onclick = () => { state.reviewView = button.dataset.reviewView; state.selected = ""; syncUrl(); loadList(); });
  $("search-projects")?.addEventListener("click", () => { captureProjectSearch(); state.selected = ""; syncUrl(); loadList(); });
  $("project-query")?.addEventListener("keydown", (event) => { if (event.key === "Enter") { captureProjectSearch(); state.selected = ""; syncUrl(); loadList(); } });
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

async function loadDetail(key, {updateUrl = true} = {}) {
  const request = ++state.detailRequest;
  state.selected = key; document.body.classList.add("detail-open");
  if (updateUrl) syncUrl();
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
  if (state.server?.read_only) return "";
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
  const investigation = data.investigation;
  const recommendation = data.merge_recommendation || {};
  const overview = data.overview || {};
  state.detailRevision = data.revision || "";
  state.mergeTargetRevision = data.merge_target_revision || "";
  const hasPromotionHold = list(decisions.active).some((item) => item.kind === "defer_promotion");
  const manualSeed = overview.manual_seed;
  const tab = (key, label, body) => `<section class="detail-tab-panel" data-detail-panel="${key}" ${state.detailTab === key ? "" : "hidden"}>${body}</section>`;
  const overviewBody = `
    ${section("Profile", `<dl class="kv"><dt>Website</dt><dd>${project.website_url ? link(project.website_url) : "None"}</dd><dt>Repository</dt><dd>${overview.repository ? link(overview.repository) : "None"}</dd><dt>Account</dt><dd>${esc(overview.account || "None")}</dd><dt>Seed</dt><dd>${manualSeed ? esc(`${manualSeed.kinds.join(" + ")}: ${manualSeed.value}`) : "No saved manual seed"}</dd><dt>Tagline</dt><dd>${esc(profileValue(profile, "tagline")) || "None"}</dd><dt>Summary</dt><dd>${esc(profileValue(profile, "summary")) || "None"}</dd><dt>Notes</dt><dd>${esc(profileValue(profile, "notes")) || "None"}</dd><dt>Last seen</dt><dd>${esc(project.last_seen_at)}</dd><dt>Last enriched</dt><dd>${esc(project.last_enriched_at || "Never")}</dd></dl>`)}
    ${section("Evaluation", `<dl class="kv"><dt>atproto contribution</dt><dd>${pill(evaluation.atproto_contribution_level)} score ${esc(evaluation.atproto_contribution_score)}</dd><dt>Technical interest</dt><dd>${pill(evaluation.technical_interest_level)} score ${esc(evaluation.technical_interest_score)}</dd><dt>Service confidence</dt><dd>${esc(evaluation.service_status_confidence)}</dd><dt>Promotion</dt><dd>${evaluation.promotion_ready ? pill("ready", "confirmed") : list(evaluation.promotion_blockers).map((item) => pill(item, "candidate")).join(" ") || "Not ready"}</dd><dt>Technical summary</dt><dd>${esc(evaluation.technical_interest_summary || "None")}</dd></dl>`)}
    ${section("Active Guards", list(overview.active_guards).length ? renderDecisions({active: overview.active_guards, history: []}) : `<p class="muted">No active guards.</p>`)}
  `;
  $("detail").innerHTML = `
    <div class="detail-header">
      <button class="mobile-back secondary" id="detail-back">Back to list</button>
      <p class="eyebrow">${esc(project.project_key)}</p>
      <h2>${esc(project.name || project.project_key)}</h2>
      <div class="meta-row">${pill(project.tracking_state)}${pill(project.service_status)}${project.introduced_url ? pill("introduced", "confirmed") : ""}</div>
      ${state.server?.read_only ? `<p class="notice">Read-only mode. Changes and background job controls are disabled.</p>` : `<div class="actions"><select id="tracking-state">${(state.server?.tracking_states || []).map((value) => `<option ${value === project.tracking_state ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><button id="save-tracking">Set state</button><button id="add-project-seed">Add seed</button><button id="enrich-project" class="primary">Enrich</button>${hasPromotionHold ? "" : actionButton("Defer promotion 7d", "defer-promotion")}${recommendation.auto_merge_ready ? actionButton("Apply merge recommendation", "merge-recommendation", {}, "danger") : ""}</div>`}
    </div>
    <nav class="detail-tabs" aria-label="Project detail">
      ${[["overview","Overview"],["identifiers","Identifiers"],["relations","Relations"],["investigation","Investigation"],["decisions","Decisions"],["raw","Raw"]].map(([key,label]) => `<button class="secondary ${state.detailTab === key ? "active" : ""}" data-detail-tab="${key}">${label}</button>`).join("")}
    </nav>
    ${tab("overview", "Overview", overviewBody)}
    ${tab("identifiers", "Identifiers", section("Identifiers", identifiers.length ? renderIdentifiers(identifiers) : `<p class="muted">No identifiers.</p>`))}
    ${tab("relations", "Relations", section("Relations", relations.length ? relations.map((item) => `<div class="relation ${esc(item.classification)}"><div>${pill(item.classification, item.classification)} ${esc(item.summary)}</div><div class="muted">${esc(item.evidence_label)}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("") : `<p class="muted">No saved relations.</p>`))}
    ${tab("investigation", "Investigation", section("Latest Investigation", renderInvestigation(investigation)))}
    ${tab("decisions", "Decisions", section("Decisions / Guards", renderDecisions(decisions)))}
    ${tab("raw", "Raw", section("Raw Data", `<details open><summary>Project payload</summary>${json(data)}</details>`))}
  `;
  $("detail-back").onclick = () => { document.body.classList.remove("detail-open"); state.selected = ""; syncUrl(); };
  $("save-tracking")?.addEventListener("click", () => confirmMutation("Set tracking state", `Set ${project.name || project.project_key} to ${$("tracking-state").value}?`, `/api/projects/${encodeURIComponent(project.project_key)}/tracking-state`, {tracking_state: $("tracking-state").value}, false));
  $("enrich-project")?.addEventListener("click", () => startProjectEnrich(project.project_key));
  $("add-project-seed")?.addEventListener("click", () => addProjectSeed(project.project_key));
  document.querySelectorAll("[data-detail-tab]").forEach((button) => button.onclick = () => {
    state.detailTab = button.dataset.detailTab;
    syncUrl();
    document.querySelectorAll("[data-detail-tab]").forEach((item) => item.classList.toggle("active", item.dataset.detailTab === state.detailTab));
    document.querySelectorAll("[data-detail-panel]").forEach((item) => item.hidden = item.dataset.detailPanel !== state.detailTab);
  });
  bindDetailActions(project.project_key);
}

const identifierFamilies = [
  {key: "service", label: "Services"},
  {key: "repository", label: "Repositories"},
  {key: "schema", label: "NSIDs"},
  {key: "identity", label: "Identities"},
];

function uniqueValues(values) { return [...new Set(values.filter(Boolean))]; }

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
  const actions = `${item.actions?.confirm ? actionButton("Confirm", "confirm-candidate", {type:actionKind, value:item.value}, "small primary") : ""}${item.actions?.reject ? actionButton("Reject", "reject-candidate", {type:actionKind, value:item.value}, "small danger") : ""}${item.actions?.block ? actionButton("Block", "block-identifier", {type:actionKind, value:item.value}, "small danger") : ""}${item.actions?.defer ? actionButton("Defer 30d", "defer-identifier", {type:actionKind, value:item.value}) : ""}${item.actions?.split ? actionButton("Split", "split-project", {type:actionKind, value:item.value}) : ""}`;
  const showKinds = item.family === "identity" || item.family.startsWith("other:");
  const detail = uniqueValues([showKinds ? item.kinds.join(" + ") : "", item.is_primary ? "primary" : "", ...item.role_labels, ...item.providers]).join(" · ");
  return `<div class="identifier ${esc(effectiveClassification)}">
    <div class="identifier-main">
      <div class="identifier-value">${item.kinds.some((kind) => ["service_url", "repo_url"].includes(kind)) ? link(item.value) : esc(item.value)}</div>
      <div class="identifier-summary">${classifications.map((value) => pill(value, value)).join("")}<span class="identifier-roles">${esc(detail)}</span></div>
    </div>
    ${actions ? `<details class="identifier-actions"><summary>Actions</summary><div class="actions">${actions}</div></details>` : ""}
  </div>`;
}

function renderDecisions(decisions) {
  const active = list(decisions.active);
  const history = list(decisions.history);
  const current = active.length ? active.map((item) => `<div class="decision"><div>${pill(item.kind, "candidate")} ${esc(item.target_type)} ${esc(item.target_value)}</div><div class="muted">${esc(item.note || "No note")} ${item.expires_at ? `· expires ${esc(item.expires_at)}` : ""}</div><div class="actions">${item.kind === "block_identifier" ? actionButton("Clear block", "clear-block-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_identifier" ? actionButton("Clear defer", "clear-defer-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_promotion" ? actionButton("Clear hold", "clear-promotion-deferral") : ""}${item.kind === "separate_project" ? actionButton("Clear separate", "clear-separate", {other:item.target_value}) : ""}${["dismiss_review_target", "defer_review_target"].includes(item.kind) ? actionButton("Clear", "clear-review-target", {"target-type":item.target_type, "target-value":item.target_value, "review-action":item.kind.startsWith("defer") ? "defer" : "dismiss"}) : ""}</div></div>`).join("") : `<p class="muted">No active decisions.</p>`;
  return current + (history.length ? `<details><summary>Decision history (${history.length})</summary>${json(history)}</details>` : "");
}

function renderInvestigation(run) {
  if (!run) return `<p class="muted">No saved investigation run.</p>`;
  return `<dl class="kv"><dt>Run</dt><dd>${esc(run.run_id)}</dd><dt>Completed</dt><dd>${esc(run.completed_at)}</dd><dt>Records</dt><dd>${esc(run.record_count)}</dd><dt>Failures</dt><dd>${esc(run.failure_count)}</dd></dl>
    <div>${list(run.collectors).map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(item.source)}</strong></div><div class="muted">${esc(item.operation)} · ${esc(item.request_count || 0)} requests · ${esc(item.result_count || 0)} results${item.stop_reason ? ` · ${esc(item.stop_reason)}` : ""}</div></div>`).join("") || `<p class="muted">No collector summaries.</p>`}</div>
    ${run.failures?.length ? `<details><summary>Failures</summary>${run.failures.map((item) => `<div class="collector"><div>${pill(item.category, "candidate")} ${esc(item.source)} ${esc(item.operation)}</div><div class="muted">${esc(item.message)}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("")}</details>` : ""}`;
}

function reviewTargetActions(target) {
  if (!target) return "";
  const data = {"target-type":target.target_type, "target-value":target.target_value};
  return `${actionButton("Dismiss", "dismiss-review-target", data)}${actionButton("Defer 30d", "defer-review-target", data)}`;
}

async function addProjectSeed(projectKey) {
  const seed = prompt("NSID, repository URL, or service URL");
  if (!seed) return;
  try {
    const preview = await api(`/api/seeds/preview?seed=${encodeURIComponent(seed)}`);
    if (preview.exact_project_key && preview.exact_project_key !== projectKey) throw new Error(`Seed belongs to ${preview.exact_project_key}`);
    if (preview.exact_project_key === projectKey) { status("Seed is already attached to this project."); return; }
    confirmMutation("Add manual seed", `Add ${seed} to ${projectKey}?`, "/api/seeds", {seed, project_key:projectKey, expected_revision:state.detailRevision}, false);
  } catch (error) { status(error.message, true); }
}

function bindDetailActions(projectKey) {
  document.querySelectorAll("[data-action]").forEach((button) => button.onclick = () => {
    const action = button.dataset.action;
    const payload = {identifier_type: button.dataset.type || "", value: button.dataset.value || "", other_project_key: button.dataset.other || "", target_type: button.dataset.targetType || "", target_value: button.dataset.targetValue || "", review_action: button.dataset.reviewAction || ""};
    const needsNote = ["reject-candidate", "block-identifier", "defer-identifier", "split-project", "defer-promotion", "dismiss-review-target", "defer-review-target"].includes(action);
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
    mutationQueue = mutationQueue.then(async () => {
      try {
        if (path.startsWith("/api/projects/")) payload.expected_revision = state.detailRevision;
        if (path.endsWith("/merge-recommendation")) payload.expected_target_revision = state.mergeTargetRevision;
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
      } catch (error) {
        if (error.code === "stale_project" && state.selected) {
          await loadDetail(state.selected, {updateUrl: false});
          status("Project changed in another operation. Detail was reloaded; review the change before retrying.", true);
        } else {
          status(error.message, true);
        }
      }
    }, async () => {}).catch(() => {});
  };
}

async function loadMergeDetail(source, target) {
  const request = ++state.detailRequest;
  state.selected = source; document.body.classList.add("detail-open");
  syncUrl();
  try {
    const data = await api(`/api/merge-pair?source_project_key=${encodeURIComponent(source)}&target_project_key=${encodeURIComponent(target)}`);
    if (request !== state.detailRequest || state.selected !== source) return;
    state.mergeSourceRevision = data.source_revision || "";
    state.mergeTargetRevision = data.target_revision || "";
    $("detail").innerHTML = `<button class="mobile-back secondary" id="detail-back">Back to list</button><p class="eyebrow">Merge review</p><h2>${esc(source)} → ${esc(target)}</h2>${section("Comparison", json(data.comparison || data))}${section("Preview", json(data.merged_preview || {}))}${state.server?.read_only ? `<p class="notice">Read-only mode.</p>` : `<div class="actions"><button id="mark-separate">Keep separate</button><button id="execute-merge" class="danger">Merge projects</button></div>`}`;
    $("detail-back").onclick = () => { document.body.classList.remove("detail-open"); state.selected = ""; syncUrl(); };
    $("mark-separate")?.addEventListener("click", () => confirmMerge("Keep projects separate", "separate", source, target));
    $("execute-merge")?.addEventListener("click", () => confirmMerge("Merge projects", "merge", source, target));
  } catch (error) {
    if (request === state.detailRequest && state.selected === source) status(error.message, true);
  }
}

function confirmMerge(title, action, source, target) {
  confirmMutation(title, `${title}: ${source} → ${target}?`, "/api/merge-batch/action", {
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
  $("detail").innerHTML = `<button class="mobile-back secondary" id="detail-back">Back to list</button><p class="eyebrow">Projects</p><h2>New Seed</h2><div class="controls"><label>NSID, repository URL, or service URL<input id="seed-value" type="text"></label><div class="actions"><button id="preview-seed" class="primary">Preview</button></div></div><div id="seed-preview"></div>`;
  document.body.classList.add("detail-open");
  $("detail-back").onclick = () => { document.body.classList.remove("detail-open"); state.selected = ""; syncUrl(); };
  $("preview-seed").onclick = async () => {
    const value = $("seed-value").value.trim();
    try {
      const data = await api(`/api/seeds/preview?seed=${encodeURIComponent(value)}`);
      $("seed-preview").innerHTML = section("Preview", json(data)) + (data.can_create && !state.server?.read_only ? `<button id="create-seed" class="primary">Create project</button>` : data.can_create ? `<p class="notice">Read-only mode. Preview only.</p>` : `<p class="notice">This seed already belongs to ${esc(data.exact_project_key)}.</p>`);
      $("create-seed")?.addEventListener("click", () => confirmMutation("Create project", `Create a project for ${value}?`, "/api/seeds", {seed:value}, false));
    } catch (error) { status(error.message, true); }
  };
}

function renderOperations() {
  $("single-pane").innerHTML = `<p class="eyebrow">Background jobs</p><h2>Operations</h2><p class="notice">${state.server?.read_only ? "Read-only mode. Job controls are disabled." : "Operations run in a separate process. Only one job runs at a time. Enrich stops between projects; sync and review finish their current operation before a cancellation request takes effect."}</p><div class="operation-grid">
    <article class="operation-card operation-enrich"><h3>Enrich</h3><div class="controls">
      <label>Target<select id="enrich-target"><option value="all">Active and watchlist projects</option><option value="project">One project</option></select></label>
      <label id="enrich-project-label" hidden>Project key<input id="enrich-project-key" placeholder="proj:..."></label>
      <label><span><input id="enrich-deep" type="checkbox"> Deep investigation</span></label>
      <label><span><input id="enrich-new-only" type="checkbox"> Never enriched only</span></label>
      <label><span><input id="enrich-retry" type="checkbox"> Previous failures only</span></label>
      <details class="tracking-state-options"><summary>Additional tracking states</summary><div class="checkbox-grid">
        <label><span><input id="enrich-stable" type="checkbox"> stable</span></label>
        <label><span><input id="enrich-stalled" type="checkbox"> stalled</span></label>
        <label><span><input id="enrich-out-of-scope" type="checkbox"> out_of_scope</span></label>
        <label><span><input id="enrich-archived" type="checkbox"> archived</span></label>
        <label><span><input id="enrich-ignore" type="checkbox"> ignore</span></label>
      </div></details>
      <button id="start-enrich" class="primary">Start enrich job</button>
    </div><code>project-registry enrich --all</code></article>
    <article class="operation-card"><h3>Sync</h3><p>Fetch configured external sources. Dry-run still uses the network but does not update SQLite.</p><div class="controls">
      <label>Source<select id="sync-source"><option value="introduced">Introduced posts</option><option value="github">GitHub repository search</option><option value="collections">Collection inventory</option><option value="semble">Semble collections</option><option value="tangled">Tangled repositories</option></select></label>
      <label>Initial lookback days<input id="sync-lookback" type="number" min="1" max="3650" value="10"></label>
      <label><span><input id="sync-create" type="checkbox"> Create unmatched projects</span></label>
      <label><span><input id="sync-screen" type="checkbox"> Screen unmatched GitHub repos</span></label>
      <label><span><input id="sync-all-history" type="checkbox"> Ignore saved watermark</span></label>
      <label><span><input id="sync-execute" type="checkbox"> Apply changes to SQLite</span></label>
      <button id="start-sync" class="primary">Start sync job</button>
    </div><code>project-registry sync-&lt;source&gt; [--execute]</code></article>
    <article class="operation-card"><h3>Review Ops</h3><p>Recalculate tracking-state candidates from saved project data. Dry-run is network-free.</p><div class="controls">
      <label>Review<select id="review-kind"><option value="stalled">Stalled / stable activity</option><option value="out_of_scope">Out of survey scope</option><option value="ignore">Unrelated or identity-less noise</option></select></label>
      <label>Candidate limit<input id="review-limit" type="number" min="1" max="500" value="50"></label>
      <label id="review-inactivity-label">Inactivity days<input id="review-inactivity" type="number" min="1" max="3650" value="45"></label>
      <label id="review-freshness-label">Max enrich age days<input id="review-freshness" type="number" min="1" max="3650" value="30"></label>
      <label>Tracking note<input id="review-note" placeholder="Optional note for applied updates"></label>
      <label><span><input id="review-execute" type="checkbox"> Apply tracking-state changes</span></label>
      <button id="start-review" class="primary">Start review job</button>
    </div><code>project-registry review-&lt;kind&gt; [--execute]</code></article>
    <article class="operation-card"><h3>Investigation Replay</h3><p>Replay and benchmark saved evidence without network access.</p><code>project-registry replay-investigation -p proj:...</code><code>project-registry benchmark-investigation</code></article>
  </div>${section("Recent Jobs", `<div id="job-list"><p class="muted">Loading...</p></div>`)}`;
  $("enrich-target").onchange = () => { $("enrich-project-label").hidden = $("enrich-target").value !== "project"; };
  $("sync-source").onchange = updateSyncControls;
  $("review-kind").onchange = updateReviewControls;
  $("start-enrich").onclick = startEnrichJob;
  $("start-sync").onclick = startSyncJob;
  $("start-review").onclick = startReviewJob;
  updateSyncControls();
  updateReviewControls();
  if (state.server?.read_only) {
    ["start-enrich", "start-sync", "start-review"].forEach((id) => { if ($(id)) $(id).disabled = true; });
  }
  loadJobs();
}

function confirmOperation(title, message, callback, {write = true} = {}) {
  const dialog = $("confirm-dialog");
  $("confirm-context").textContent = write ? "Confirm database change" : "Confirm operation";
  $("confirm-submit").textContent = write ? "Apply change" : "Start job";
  $("confirm-submit").className = write ? "danger" : "primary";
  $("confirm-title").textContent = title;
  $("confirm-message").textContent = message;
  $("confirm-note-label").hidden = true;
  $("confirm-note").value = "";
  dialog.showModal();
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    try { await callback(); } catch (error) { status(error.message, true); }
  };
}

function startEnrichJob() {
  const oneProject = $("enrich-target").value === "project";
  const projectKey = $("enrich-project-key").value.trim();
  if (oneProject && !projectKey) { status("Project key is required.", true); return; }
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
  }, oneProject ? projectKey : "the selected project set");
}

function startProjectEnrich(projectKey) {
  startEnrich({project_key: projectKey, all_projects: false}, projectKey);
}

function startEnrich(payload, target) {
  confirmOperation("Start enrich job", `Start enrich for ${target}? This writes investigation results to SQLite.`, async () => {
    status("Starting enrich job...");
    await api("/api/jobs/enrich", {method:"POST", body:JSON.stringify(payload)});
    status("Enrich job started.");
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
  const effect = execute ? "This fetches external data and writes accepted changes to SQLite." : "This fetches external data and reports a dry-run without updating SQLite.";
  confirmOperation("Start sync job", `Start ${source} sync? ${effect}`, async () => {
    status("Starting sync job...");
    await api("/api/jobs/sync", {method:"POST", body:JSON.stringify(payload)});
    status("Sync job started.");
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
  const execute = $("review-execute").checked;
  const payload = {
    kind,
    execute,
    limit: Number($("review-limit").value || 100),
    inactivity_days: Number($("review-inactivity").value || 45),
    max_enrich_age_days: Number($("review-freshness").value || 30),
    note: $("review-note").value.trim(),
  };
  const effect = execute ? "This applies every listed candidate from this run to SQLite." : "This only reports candidates and does not update SQLite.";
  confirmOperation("Start review job", `Start ${kind} review? ${effect}`, async () => {
    status("Starting review job...");
    await api("/api/jobs/review", {method:"POST", body:JSON.stringify(payload)});
    status("Review job started.");
    await loadJobs();
  }, {write: execute});
}

function renderJobs(items) {
  if (!items.length) return `<p class="muted">No jobs have run in this server process.</p>`;
  return items.map((job) => {
    const progress = job.progress || {};
    const count = progress.total ? `${progress.current || 0} / ${progress.total}` : "";
    const target = job.request?.parameters?.project_key || (job.request?.parameters?.all_projects ? "bulk" : job.request?.parameters?.source || job.request?.parameters?.kind || "operation");
    return `<article class="job-card ${esc(job.status)}"><div class="card-title"><strong>${esc(job.operation)} · ${esc(target)}</strong>${pill(job.status, job.status)}</div>
      <div class="meta-row"><span>${esc(job.job_id)}</span><span>${esc(count)}</span><span>${esc(progress.elapsed || "")}</span></div>
      ${job.last_item ? `<p class="muted">Last: ${esc(job.last_item.project_key)} ${esc(job.last_item.name || "")}</p>` : ""}
      ${job.error ? `<p class="job-error">${esc(job.error.message || JSON.stringify(job.error))}</p>` : ""}
      <div class="actions">
        ${job.can_cancel && !state.server?.read_only ? `<button class="danger small" data-job-cancel="${esc(job.job_id)}">Request stop</button>` : ""}
        ${job.can_resume && !state.server?.read_only ? `<button class="small" data-job-resume="${esc(job.job_id)}">Resume</button>` : ""}
      </div><details class="job-details" data-job-details="${esc(job.job_id)}" ${state.openJobDetails.has(job.job_id) ? "open" : ""}>
        <summary>Details</summary><div id="job-detail-${esc(job.job_id.replaceAll(":", "-"))}">${state.jobDetailCache.has(job.job_id) ? json(state.jobDetailCache.get(job.job_id)) : `<p class="muted">Open to load details.</p>`}</div>
      </details></article>`;
  }).join("");
}

async function loadJobs() {
  if (state.screen !== "operations") return;
  if (state.operationTimer) clearTimeout(state.operationTimer);
  state.operationTimer = null;
  try {
    const data = await api("/api/jobs?limit=50");
    $("job-list").innerHTML = renderJobs(list(data.items));
    bindJobActions();
    await Promise.all([...state.openJobDetails].map((jobId) => refreshJobDetail(jobId)));
    if (list(data.items).some((job) => job.can_cancel || job.status === "cancelling")) {
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
  document.querySelectorAll("[data-job-cancel]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.jobCancel;
    confirmOperation("Stop background job", `Request cancellation for ${jobId}? The current operation may finish first.`, async () => {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, {method:"POST", body:"{}"});
      status("Stop requested."); await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-resume]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.jobResume;
    confirmOperation("Resume enrich job", `Resume the checkpoint used by ${jobId}?`, async () => {
      await api(`/api/jobs/${encodeURIComponent(jobId)}/resume`, {method:"POST", body:"{}"});
      status("Resume job started."); await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-details]").forEach((details) => details.ontoggle = async () => {
    const jobId = details.dataset.jobDetails;
    if (!details.open) {
      state.openJobDetails.delete(jobId);
      return;
    }
    state.openJobDetails.add(jobId);
    const target = $(`job-detail-${jobId.replaceAll(":", "-")}`);
    if (state.jobDetailCache.has(jobId)) target.innerHTML = json(state.jobDetailCache.get(jobId));
    await refreshJobDetail(jobId);
  });
}

async function renderSettings() {
  try { state.server = state.server || await api("/api/settings"); } catch (error) { status(error.message, true); }
  $("single-pane").innerHTML = `<p class="eyebrow">Local Web UI</p><h2>Settings</h2><div class="controls filter-grid">
    <label>Review limit<input id="setting-review" type="number" min="1" max="500" value="${esc(state.settings.reviewLimit)}"></label>
    <label>Project limit<input id="setting-project" type="number" min="1" max="500" value="${esc(state.settings.projectLimit)}"></label>
    <label>Merge limit<input id="setting-merge-limit" type="number" min="1" max="500" value="${esc(state.settings.mergeLimit)}"></label>
    <label>Merge mode<select id="setting-merge-mode"><option>strict</option><option>moderate</option><option>broad</option></select></label>
    <label class="settings-toggle"><span>Promotion queue</span><span><input id="setting-personal" type="checkbox" ${state.settings.includePersonalTools ? "checked" : ""}> Include personal tools</span></label>
  </div><div class="actions"><button id="save-settings" class="primary">Save settings</button><button id="clear-token" class="danger">Clear API token</button></div>
  ${section("Server", `<dl class="kv"><dt>API</dt><dd>v${esc(state.server?.api_version || "?")}</dd><dt>Schema</dt><dd>v${esc(state.server?.schema_version || "?")}</dd><dt>Mode</dt><dd>${state.server?.read_only ? "Read-only" : "Read/write"}</dd><dt>Database</dt><dd>${esc(state.server?.db_path || "Unavailable")}</dd><dt>Background jobs</dt><dd>${state.server?.operations_connected ? "Connected: enrich, sync, and review subprocess worker" : "Not connected"}</dd></dl>`)}
  `;
  $("setting-merge-mode").value = state.settings.mergeMode;
  $("save-settings").onclick = () => {
    state.settings = {reviewLimit:Number($("setting-review").value), projectLimit:Number($("setting-project").value), mergeLimit:Number($("setting-merge-limit").value), mergeMode:$("setting-merge-mode").value, includePersonalTools:$("setting-personal").checked};
    saveSettings(); status("Settings saved.");
  };
  $("clear-token").onclick = () => { localStorage.removeItem("projectRegistryToken"); token = ""; status("Token cleared. Reload to sign in again."); };
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
