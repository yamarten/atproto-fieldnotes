const $ = (id) => document.getElementById(id);
// Security boundary for HTML templates: every external string must pass through
// esc(), link(), pill(), or json() before it reaches innerHTML/outerHTML.
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[ch]));
const list = (value) => Array.isArray(value) ? value : [];
const resetDetailScroll = () => { if ($("detail")) $("detail").scrollTop = 0; window.scrollTo({top:0, left:0}); };
const backToListButton = () => `<button class="mobile-back secondary" data-detail-back>Back to list</button>`;
function contextualBackButton() {
  if (!state.detailBackTarget) return "";
  const label = state.detailBackTarget.type === "merge" ? "Back to merge review" : "Back to project detail";
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
  lower_promotion_priority: "Lower candidate priority [lower_promotion_priority]",
};
const decisionTargetOptionLabels = {
  promotion_priority: "Candidate priority [promotion_priority]",
};
const state = {
  screen: "review",
  reviewView: "queue",
  reviewSignalKind: "",
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
let token = localStorage.getItem("projectRegistryToken") || prompt("Project Registry token") || "";
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
function pill(value, kind = "") { return value ? `<span class="pill ${esc(kind)}">${esc(value)}</span>` : ""; }
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
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.floor(value / 60)}m ${value % 60}s`;
  return `${Math.floor(value / 3600)}h ${Math.floor((value % 3600) / 60)}m`;
}

function formatMilliseconds(milliseconds) {
  if (milliseconds === null || milliseconds === undefined || !Number.isFinite(Number(milliseconds))) return "";
  const value = Math.max(0, Math.round(Number(milliseconds)));
  return value < 1000 ? `${value}ms` : formatDuration(value / 1000);
}

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
  state.reviewSignalKind = query.get("review_kind") || "";
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
      ? `<div class="toolbar"><label>Signal kind<input id="review-signal-kind" placeholder="e.g. candidate_repo_needs_confirmation" value="${esc(state.reviewSignalKind)}"></label><button id="apply-review-filter" class="primary">Apply</button><button id="clear-review-filter" class="secondary">Clear</button></div>`
      : "";
    return `<div class="controls"><div class="subnav">
      <button data-review-view="queue" class="${state.reviewView === "queue" ? "active" : ""}">Review Queue</button>
      <button data-review-view="promotion" class="${state.reviewView === "promotion" ? "active" : ""}">Promotion</button>
      <button data-review-view="merge" class="${state.reviewView === "merge" ? "active" : ""}">Merge</button>
      <button id="refresh-review-list" class="secondary">Refresh</button>
    </div>${reviewFilter}${explicitMerge}</div>`;
  }
  const relationSources = filterOptionValues("relation_sources");
  return `<div class="controls">
    <div class="toolbar"><input id="project-query" type="search" placeholder="Search name, NSID, account, URL" value="${esc(state.projectSearch.query)}"><button id="search-projects" class="primary">Search</button><button id="new-seed">New Seed</button></div>
    <details><summary>Filters</summary><p class="muted">Multiple-choice filters accept any number of selections; no selection means Any.</p><div class="filter-grid">
      <label>Tracking states<select id="filter-tracking" multiple size="5">${multiEnumOptions(filterOptionValues("tracking_states"), state.projectSearch.tracking)}</select></label>
      <label>Service status<select id="filter-service" multiple size="5">${multiEnumOptions(filterOptionValues("service_statuses"), state.projectSearch.service)}</select></label>
      <label>Contribution<select id="filter-contribution" multiple size="5">${multiEnumOptions(filterOptionValues("contribution_levels"), state.projectSearch.contribution)}</select></label>
      <label>Notes<select id="filter-notes">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.notes)}</select></label>
      <label>Introduced<select id="filter-introduced">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.introduced)}</select></label>
      <label>Candidates<select id="filter-candidates">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.candidates)}</select></label>
      <label>Errors<select id="filter-errors">${selectOptions([["any", "Any"], ["yes", "Yes"], ["no", "No"]], state.projectSearch.errors)}</select></label>
      <label>Sort<select id="filter-sort">${selectOptions([["last_seen_desc", "Recently seen"], ["first_seen_desc", "Recently added"], ["first_seen_asc", "Oldest added"], ["last_enriched_asc", "Needs enrich"], ["name_asc", "Name"]], state.projectSearch.sort)}</select></label>
      <label>Identifier kind<select id="filter-identifier-kind">${enumOptions(filterOptionValues("identifier_kinds"), state.projectSearch.identifierKind)}</select></label>
      <label>Identifier value<input id="filter-identifier-value" value="${esc(state.projectSearch.identifierValue)}"></label>
      <label>Identifier classification<select id="filter-identifier-classification">${enumOptions(filterOptionValues("identifier_classifications"), state.projectSearch.identifierClassification)}</select></label>
      <label>Relation predicate<select id="filter-relation-predicate">${enumOptions(filterOptionValues("relation_predicates"), state.projectSearch.relationPredicate)}</select></label>
      <label>Relation subject type<select id="filter-relation-subject-type">${enumOptions(filterOptionValues("relation_subject_types"), state.projectSearch.relationSubjectType)}</select></label>
      <label>Relation subject value<input id="filter-relation-subject-value" value="${esc(state.projectSearch.relationSubjectValue)}"></label>
      <label>Relation object type<select id="filter-relation-object-type">${enumOptions(filterOptionValues("relation_object_types"), state.projectSearch.relationObjectType)}</select></label>
      <label>Relation object value<input id="filter-relation-object-value" value="${esc(state.projectSearch.relationObjectValue)}"></label>
      <label>Relation source<input id="filter-relation-source" list="filter-relation-sources" value="${esc(state.projectSearch.relationSource)}"></label>
      <datalist id="filter-relation-sources">${relationSources.map((value) => `<option value="${esc(value)}"></option>`).join("")}</datalist>
      <label>Decision kind<select id="filter-decision-kind">${enumOptions(filterOptionValues("decision_kinds"), state.projectSearch.decisionKind, decisionKindOptionLabels)}</select></label>
      <label>Decision target type<select id="filter-decision-target-type">${enumOptions(filterOptionValues("decision_target_types"), state.projectSearch.decisionTargetType, decisionTargetOptionLabels)}</select></label>
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
  return selectOptions([["", "Any"], ...options.map((value) => [value, labels[value] || value])], selected);
}

function multiEnumOptions(values, selected) {
  const selectedValues = new Set(splitSelections(selected));
  const options = [...values];
  selectedValues.forEach((value) => { if (!options.includes(value)) options.push(value); });
  return options.map((value) => `<option value="${esc(value)}" ${selectedValues.has(value) ? "selected" : ""}>${esc(value)}</option>`).join("");
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
    <label>Source project<input data-explicit-merge-source placeholder="proj:3930cb0bedfd" value="${esc(source)}" ${sourceMode}></label>
    <label>Target project<input data-explicit-merge-target placeholder="proj:aefc07410260" value="${esc(target)}"></label>
    <button data-compare-explicit-merge class="primary">Compare pair</button>
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
    status("Source and target project keys are required.", true);
    return;
  }
  if (source === target) {
    status("Source and target must be different projects.", true);
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
  confirmed: "Usable service screen confirmed",
  needs_validation: "Service screen needs validation",
  not_applicable: "No service-screen assessment",
};

const promotionPriorityLabels = {
  normal: "Normal priority",
  lowered: "Lower priority",
};

const promotionVisibilityLabels = {
  eligible: "Shown in candidate list",
  deferred: "Temporarily hidden from candidate list",
  excluded: "Not in candidate list",
};

const promotionScoreLabels = {
  service_evidence: "Usable service screen",
  lexicon_publication: "Verified Lexicon publication",
  technical_interest: "Technical interest",
  service_status: "Service status",
  service_confidence: "Service-status confidence",
  profile_completeness: "Profile completeness",
  repository_evidence: "Repository found",
  lexicon_evidence: "Lexicon found",
  review_readiness: "No manual-review flag",
  atproto_contribution: "atproto contribution",
  promotion_blockers: "Missing introduction material",
  recent_activity: "Recent activity",
  recent_collection_activity: "Recent record activity",
  recent_repository_activity: "Recent repository activity",
};

const promotionValidationLabels = {
  low_service_status_confidence: "service status has low confidence",
  usable_surface_unconfirmed: "no usable service screen was confirmed",
};

const promotionSignalLabels = {
  service_surface_confirmed: "Usable service screen confirmed",
  service_candidate_needs_validation: "Service screen needs validation",
  lexicon_publisher_confirmed: "Verified Lexicon publisher",
  technical_insight_candidate: "Technical insight candidate",
};

function promotionLabel(labels, value) {
  return labels[value] || String(value || "Not available").replaceAll("_", " ");
}

function promotionCardDetails(item) {
  if (state.screen !== "review" || state.reviewView !== "promotion") return "";
  const breakdown = item.score_breakdown || {};
  const components = Object.entries(breakdown.components || {})
    .filter(([, value]) => Number(value) !== 0)
    .map(([key, value]) => `${promotionLabel(promotionScoreLabels, key)} ${Number(value) > 0 ? "+" : ""}${value}`);
  const validationReasons = uniqueValues(
    list(item.signals).flatMap((signal) => list(signal.details?.validation_reasons))
  ).map((reason) => promotionLabel(promotionValidationLabels, reason));
  const serviceGroup = item.service_evidence_group || "not_applicable";
  const priorityGroup = item.priority_group || "normal";
  return `<span class="meta-row">${pill(promotionLabel(promotionServiceLabels, serviceGroup), serviceGroup === "confirmed" ? "confirmed" : "candidate")}${pill(promotionLabel(promotionPriorityLabels, priorityGroup), priorityGroup === "lowered" ? "candidate" : "confirmed")}${pill(`Ranking score ${breakdown.total ?? item.sort_score ?? 0}`)}</span>
    ${item.priority_reason ? `<span class="muted">Priority note: ${esc(item.priority_reason)}</span>` : ""}
    ${validationReasons.length ? `<span class="muted">Needs validation: ${esc(validationReasons.join(", "))}</span>` : ""}
    ${components.length ? `<span class="muted">${esc(components.join(" · "))}</span>` : ""}`;
}

function renderList(data) {
  const items = list(data.items);
  const count = data.count ?? items.length;
  $("list").innerHTML = `<div class="meta-row"><strong>${esc(count)}</strong> results</div>` + (items.length ? items.map((item, index) => {
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
    return `<button class="card ${state.selected === key ? "selected" : ""}" data-project-key="${esc(key)}" ${mergeTarget ? `data-merge-target="${esc(mergeTarget)}"` : ""}>
      <span class="card-title"><strong>${esc(name)}</strong>${pill(project.tracking_state || item.recommendation?.confidence || "")}</span>
      <span class="meta-row">${pill(project.service_status || "")}${pill(project.atproto_contribution_level || "")}${project.introduced_url ? pill("introduced", "confirmed") : ""}${project.has_candidates ? pill("candidates", "candidate") : ""}</span>
      ${project.tagline ? `<span>${esc(project.tagline)}</span>` : ""}${signals ? `<span class="meta-row">${signals}</span>` : ""}
      ${promotionCardDetails(item)}
      ${mergeTarget ? `<span class="muted">→ ${esc(item.target_name || mergeTarget)}</span>` : ""}
    </button>`;
  }).join("") : `<div class="empty-state"><p>No matching projects.</p></div>`);
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

function advanceReviewList(sourceKey, {targetKey = "", statusPrefix = "Skipped for now."} = {}) {
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
    status(`${statusPrefix} Moved to next item. ${remaining} visible; refresh to recalculate.`);
    openListCard(next || previous);
  } else {
    state.selected = "";
    document.body.classList.remove("detail-open");
    syncUrl();
    $("detail").innerHTML = `<div class="empty-state"><p>No more visible review items.</p><div class="actions"><button id="refresh-empty-review-list" class="primary">Refresh list</button></div></div>`;
    resetDetailScroll();
    $("refresh-empty-review-list")?.addEventListener("click", () => loadList());
    status(`${statusPrefix} No more visible review items; refresh to recalculate.`);
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
  if (path === "/api/merge-batch/action" && response.mode === "execute") {
    return advanceMergeReviewAfterMerge(response);
  }
  const sourceKey = path === "/api/merge-batch/action"
    ? String(response.source_project_key || state.selected || "")
    : String(response.project_key || state.selected || "");
  const targetKey = path === "/api/merge-batch/action" ? String(response.target_project_key || "") : "";
  return advanceReviewList(sourceKey, {targetKey, statusPrefix: "Saved."});
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
    status(`Saved. Reviewing another candidate for the merged project (${response.remaining_merge_candidate_count || 0} remaining).`);
    loadMergeDetail(nextSource, nextTarget);
    return true;
  }

  const nextCard = remainingCards[Math.min(Math.max(anchorIndex, 0), Math.max(remainingCards.length - 1, 0))];
  if (nextCard) {
    status(`Saved. Moved to next item. ${remainingCards.length} visible; refresh to recalculate.`);
    openListCard(nextCard);
  } else {
    state.selected = "";
    document.body.classList.remove("detail-open");
    syncUrl();
    $("detail").innerHTML = `<div class="empty-state"><p>No more visible review items.</p><div class="actions"><button id="refresh-empty-review-list" class="primary">Refresh list</button></div></div>`;
    resetDetailScroll();
    $("refresh-empty-review-list")?.addEventListener("click", () => loadList());
    status("Saved. No more visible review items; refresh to recalculate.");
  }
  return true;
}

function skipReviewItem(sourceKey = state.selected, targetKey = "") {
  if (!advanceReviewList(String(sourceKey || ""), {targetKey: String(targetKey || ""), statusPrefix: "Skipped for now."})) {
    status("No visible review item to skip; refresh list.", true);
  }
}

async function loadDetail(key, {updateUrl = true, backTarget = null} = {}) {
  const request = ++state.detailRequest;
  state.selected = key; document.body.classList.add("detail-open");
  state.detailBackTarget = backTarget;
  if (updateUrl) syncUrl();
  $("detail").innerHTML = `<p class="muted">Loading ${esc(key)}...</p>`;
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
  return `<dt>First seen</dt><dd>${esc(formatTimestamp(project.first_seen_at) || "Unknown")}</dd><dt>Last seen</dt><dd>${esc(formatTimestamp(project.last_seen_at) || "Unknown")}</dd><dt>Last enriched</dt><dd>${esc(formatTimestamp(project.last_enriched_at) || "Never")}</dd>`;
}
function mergeIdentifierLabel(item, classification) {
  const entries = list(item.entries).filter((entry) => entry.classification === classification);
  const roleLabels = uniqueValues(entries.map((entry) => entry.role_label || entry.role));
  if (item.family === "identity" && roleLabels.includes("developer")) {
    const parts = uniqueValues(entries.filter((entry) => entry.role !== "developer_account").map((entry) => entry.role_label || entry.kind));
    return `developer identity${parts.length ? ` (${parts.join(" + ")})` : ""}`;
  }
  return roleLabels.join(" + ") || item.role_labels?.join(" + ") || item.roles?.join(" + ") || item.kinds?.join(" + ") || item.family || "";
}
function mergeIdentifierRows(identifiers, classification) {
  const rows = list(identifiers).filter((item) => list(item.classifications).includes(classification) || item.classification === classification);
  return rows.map((item) => `<div><span class="muted">${esc(mergeIdentifierLabel(item, classification))}</span> ${identifierValueLink(item)}</div>`).join("") || `<span class="muted">None</span>`;
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
  const hasPromotionHold = list(decisions.active).some((item) => item.kind === "defer_promotion");
  const promotionDefer = overview.promotion_defer || {};
  const promotionDeferDays = Number(promotionDefer.suggested_days || 30);
  const promotionDeferAction = actionButton(
    `Defer promotion ${promotionDeferDays}d`,
    "defer-promotion",
    {days: promotionDeferDays, "default-note": promotionDefer.last_reason || ""}
  );
  const priorityGroup = promotionPolicy.priority_group || "normal";
  const promotionPriorityActions = `<div class="actions">
    ${priorityGroup === "lowered"
      ? actionButton("Restore normal priority", "clear-promotion-priority")
      : actionButton("Lower priority", "lower-promotion-priority")}
  </div>`;
  const reviewSkipAction = state.screen === "review" ? `<button id="skip-review-item" class="secondary">Skip for now</button>` : "";
  const tab = (key, label, body) => `<section class="detail-tab-panel" data-detail-panel="${key}" ${state.detailTab === key ? "" : "hidden"}>${body}</section>`;
  const overviewBody = `
    ${section("Profile", `<dl class="kv"><dt>Website</dt><dd>${project.website_url ? link(project.website_url) : "None"}</dd><dt>Confirmed services</dt><dd>${list(overview.confirmed_services).map(link).join("<br>") || "None"}</dd><dt>Repositories</dt><dd>${list(overview.repositories).map(link).join("<br>") || "None"}</dd><dt>Account</dt><dd>${esc(overview.account || "None")}</dd><dt>Introduced</dt><dd>${project.introduced_url ? link(project.introduced_url) : "None"}</dd><dt>Tagline</dt><dd>${esc(profileValue(profile, "tagline")) || "None"}</dd><dt>Summary</dt><dd>${esc(profileValue(profile, "summary")) || "None"}</dd><dt>Notes</dt><dd>${esc(profileValue(profile, "notes")) || "None"}</dd><dt>System notes</dt><dd>${renderSystemNotes(project)}</dd>${projectTimestampRows(project)}</dl><div class="actions"><button id="edit-profile">Edit</button></div>`)}
    ${section("Links / Resources", renderProjectLinks(overview))}
    ${section("Evaluation", `<dl class="kv"><dt>atproto contribution</dt><dd>${pill(evaluation.atproto_contribution_level)} score ${esc(evaluation.atproto_contribution_score)}</dd><dt>Technical interest</dt><dd>${pill(evaluation.technical_interest_level)} score ${esc(evaluation.technical_interest_score)}</dd><dt>Service confidence</dt><dd>${esc(evaluation.service_status_confidence)}</dd><dt>Promotion</dt><dd>${evaluation.promotion_ready ? pill("ready", "confirmed") : list(evaluation.promotion_blockers).map((item) => pill(item, "candidate")).join(" ") || "Not ready"}</dd><dt>Candidate-list status</dt><dd>${pill(promotionLabel(promotionVisibilityLabels, promotionPolicy.candidate_visibility?.group || "excluded"))}</dd><dt>Usable service screen</dt><dd>${pill(promotionLabel(promotionServiceLabels, promotionPolicy.service_evidence_group || "not_applicable"))}</dd><dt>Candidate priority</dt><dd>${pill(promotionLabel(promotionPriorityLabels, priorityGroup))} ${esc(promotionPolicy.priority_reason || "")}</dd><dt>Ranking score</dt><dd>${esc(promotionPolicy.score_breakdown?.total ?? 0)}</dd><dt>Technical summary</dt><dd>${esc(evaluation.technical_interest_summary || "None")}</dd></dl>${promotionPriorityActions}`)}
    ${section("Active Guards", list(overview.active_guards).length ? renderDecisions({active: overview.active_guards, history: []}) : `<p class="muted">No active guards.</p>`)}
    ${section("Service / Ownership", renderServiceOwnership(serviceOwnership, browserServiceUrls))}
    ${section("Merge Candidate", `${renderExplicitMergePairForm({source: project.project_key, sourceFixed: true})}<div id="merge-analysis-content"><p class="muted">Calculating merge candidates...</p></div>`)}
    ${section("Explain", `<div id="explain-content">${renderExplanations(explanations)}</div>`)}
  `;
  $("detail").innerHTML = `
    <div class="detail-header">
      ${backToListButton()}
      ${contextualBackButton()}
      <p class="eyebrow project-key">${esc(project.project_key)}</p>
      <h2>${esc(project.name || project.project_key)}</h2>
      <div class="meta-row">${pill(project.tracking_state)}${pill(project.service_status)}${project.introduced_url ? pill("introduced", "confirmed") : ""}</div>
      <div class="actions"><select id="tracking-state">${(state.server?.tracking_states || []).map((value) => `<option ${value === project.tracking_state ? "selected" : ""}>${esc(value)}</option>`).join("")}</select><button id="save-tracking">Set state</button><button id="add-project-seed">Add seed</button><button id="enrich-project" class="primary">Enrich</button>${hasPromotionHold ? "" : promotionDeferAction}<span id="merge-analysis-action"></span>${reviewSkipAction}</div>
    </div>
    <nav class="detail-tabs" aria-label="Project detail">
      ${[["overview","Overview"],["identifiers","Identifiers"],["relations","Relations"],["investigation","Investigation"],["decisions","Decisions"],["raw","Raw"]].map(([key,label]) => `<button class="secondary ${state.detailTab === key ? "active" : ""}" data-detail-tab="${key}">${label}</button>`).join("")}
    </nav>
    ${tab("overview", "Overview", overviewBody)}
    ${tab("identifiers", "Identifiers", section("Identifiers", identifiers.length ? renderIdentifiers(identifiers) : `<p class="muted">No identifiers.</p>`))}
    ${tab("relations", "Relations", section("Relations", relations.length ? relations.map((item) => `<div class="relation ${esc(item.classification)}"><div>${pill(item.classification, item.classification)} ${esc(item.summary)}</div><div class="muted">${esc(item.evidence_label)}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("") : `<p class="muted">No saved relations.</p>`))}
    ${tab("investigation", "Investigation", section("Latest Investigation", `${renderInvestigation(investigation)}<div class="actions"><button id="load-investigation-history">Load run history</button></div><div id="investigation-history"></div>`))}
    ${tab("decisions", "Decisions", section("Decisions / Guards", renderDecisions(decisions)))}
    ${tab("raw", "Raw", section("Raw Data", `<details open><summary>Project payload</summary>${json(data)}</details>`))}
    <div class="detail-footer">${backToListButton()}${contextualBackButton()}</div>
  `;
  bindBackToListButtons();
  $("save-tracking")?.addEventListener("click", () => confirmMutation("Set tracking state", `Set ${project.name || project.project_key} to ${$("tracking-state").value}?`, `/api/projects/${encodeURIComponent(project.project_key)}/tracking-state`, {tracking_state: $("tracking-state").value}, false));
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
  {key: "site", label: "Project sites"},
  {key: "service", label: "Services"},
  {key: "repository", label: "Repositories"},
  {key: "schema", label: "NSIDs"},
  {key: "identity", label: "Identities"},
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
  if (!notes.length) return "None";
  return `<details class="system-notes"><summary>${notes.length} ${notes.length === 1 ? "entry" : "entries"}</summary><ol>${notes.map((item) => `<li>${esc(item)}</li>`).join("")}</ol></details>`;
}

function renderProjectLinks(overview) {
  const groups = [
    ["Use / install", ["website", "webApp", "distribution", "appStore", "playStore", "fdroid"]],
    ["Project information", ["repository", "docs", "support"]],
    ["Supporting information", ["changelog", "status", "privacyPolicy", "termsOfService", "webManifest"]],
  ];
  const records = [];
  list(overview.app_links).forEach((item) => records.push({url:item.uri, role:item.role, confidence:"confirmed"}));
  list(overview.resource_urls).forEach((item) => {
    if (!records.some((existing) => existing.url === item.url && existing.role === item.role)) records.push(item);
  });
  const known = new Set(groups.flatMap(([, roles]) => roles));
  groups.push(["Other", uniqueValues(records.map((item) => item.role).filter((role) => !known.has(role)))]);
  const body = groups.map(([label, roles]) => {
    const items = records.filter((item) => roles.includes(item.role)).sort((a, b) => {
      const confidence = (a.confidence === "confirmed" ? 0 : 1) - (b.confidence === "confirmed" ? 0 : 1);
      return confidence || roles.indexOf(a.role) - roles.indexOf(b.role) || String(a.url).localeCompare(String(b.url));
    });
    if (!items.length) return "";
    return `<div class="identifier-kind"><div class="identifier-kind-header"><h4>${esc(label)}</h4><span>${items.length}</span></div>${items.map((item) => `<div class="identifier ${esc(item.confidence || "candidate")}"><div>${link(item.url)}</div><div class="muted">${esc(item.role)} · ${pill(item.confidence || "candidate", item.confidence || "candidate")}</div></div>`).join("")}</div>`;
  }).join("");
  return body ? `<div class="identifier-groups">${body}</div>` : `<p class="muted">No saved links.</p>`;
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
  const actions = `${item.actions?.block ? actionButton("Block identifier", "block-identifier", {type:actionKind, value:item.value}, "small danger") : ""}${item.actions?.defer ? actionButton("Defer identifier 30d", "defer-identifier", {type:actionKind, value:item.value}) : ""}${item.actions?.split ? actionButton("Split identifier", "split-project", {type:actionKind, value:item.value}) : ""}`;
  const showKinds = item.family === "identity" || item.family.startsWith("other:");
  const entries = list(item.entries);
  const detail = uniqueValues([showKinds ? item.kinds.join(" + ") : "", item.is_primary ? "primary" : "", ...item.providers]).join(" · ");
  return `<div class="identifier ${esc(effectiveClassification)}">
    <div class="identifier-main">
      <div class="identifier-value">${identifierValueLink(item)}</div>
      ${detail ? `<div class="identifier-roles">${esc(detail)}</div>` : ""}
      <div class="identifier-entry-list">${entries.map((entry) => {
        const entryType = entry.actions?.identifier_type || "";
        const entryValue = entry.value || item.value;
        const entryActions = `${entry.actions?.confirm ? actionButton("Confirm value", "confirm-candidate", {type:entryType, value:entryValue}, "small primary") : ""}${entry.actions?.reject ? actionButton("Reject value", "reject-candidate", {type:entryType, value:entryValue}, "small danger") : ""}${entry.actions?.block ? actionButton("Block value", "block-identifier", {type:entryType, value:entryValue}, "small danger") : ""}${entry.actions?.defer ? actionButton("Defer value 30d", "defer-identifier", {type:entryType, value:entryValue}, "small") : ""}${entry.actions?.split ? actionButton("Split value", "split-project", {type:entryType, value:entryValue}, "small") : ""}`;
        const entryValueLabel = entryValue !== item.value ? entryValue : "";
        return `<div class="identifier-entry"><div>${pill(entry.classification, entry.classification)} <strong>${esc(entry.role_label || entry.role)}</strong>${entry.is_primary ? ` ${pill("primary")}` : ""}</div><div class="muted">${esc(uniqueValues([entry.kind, entryValueLabel, entry.provider, formatTimestamp(entry.observed_at)]).join(" · "))}</div>${entryActions ? `<div class="actions">${entryActions}</div>` : ""}</div>`;
      }).join("") || `<div class="identifier-summary">${classifications.map((value) => pill(value, value)).join("")}<span class="identifier-roles">${esc(item.role_labels.join(" · "))}</span></div>`}</div>
    </div>
    ${actions ? `<details class="identifier-actions"><summary>Identifier actions</summary><div class="actions"><p class="muted">These apply to this identifier value.</p>${actions}</div></details>` : ""}
  </div>`;
}

function renderDecisions(decisions) {
  const active = list(decisions.active);
  const history = list(decisions.history);
  const current = active.length ? active.map((item) => {
    const summary = item.kind === "lower_promotion_priority"
      ? `${pill("Candidate priority", "candidate")} Lower priority`
      : `${pill(item.kind, "candidate")} ${esc(item.target_type)} ${esc(item.target_value)}`;
    return `<div class="decision"><div>${summary}</div><div class="muted">${esc(item.note || "No note")} ${item.expires_at ? `· expires ${esc(formatTimestamp(item.expires_at))}` : ""}</div><div class="actions">${item.kind === "block_identifier" ? actionButton("Clear block", "clear-block-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_identifier" ? actionButton("Clear defer", "clear-defer-identifier", {type:item.target_type, value:item.target_value}) : ""}${item.kind === "defer_promotion" ? actionButton("Clear defer", "clear-promotion-deferral") : ""}${item.kind === "lower_promotion_priority" ? actionButton("Restore normal priority", "clear-promotion-priority") : ""}${item.kind === "separate_project" ? actionButton("Clear separate", "clear-separate", {other:item.target_value}) : ""}${["dismiss_review_target", "defer_review_target"].includes(item.kind) ? actionButton("Clear", "clear-review-target", {"target-type":item.target_type, "target-value":item.target_value, "review-action":item.kind.startsWith("defer") ? "defer" : "dismiss"}) : ""}</div></div>`;
  }).join("") : `<p class="muted">No active decisions.</p>`;
  return current + (history.length ? `<details><summary>Decision history (${history.length})</summary>${json(history)}</details>` : "");
}

function renderInvestigation(run) {
  if (!run) return `<p class="muted">No saved investigation run.</p>`;
  return `<dl class="kv"><dt>Run</dt><dd>${esc(run.run_id)}</dd><dt>Completed</dt><dd>${esc(formatTimestamp(run.completed_at))}</dd><dt>Duration</dt><dd>${esc(formatMilliseconds(run.metrics?.total_ms) || "Not recorded")}</dd><dt>Records</dt><dd>${esc(run.record_count)}</dd><dt>Failures</dt><dd>${esc(run.failure_count)}</dd></dl>
    <div>${list(run.collectors).map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(item.source)}</strong></div><div class="muted">${esc(item.operation)} · ${esc(item.request_count || 0)} requests · ${esc(item.result_count || 0)} results${item.stop_reason ? ` · ${esc(item.stop_reason)}` : ""}</div></div>`).join("") || `<p class="muted">No collector summaries.</p>`}</div>
    ${run.failures?.length ? `<details><summary>Failures</summary>${run.failures.map((item) => `<div class="collector"><div>${pill(item.category, "candidate")} ${esc(item.source)} ${esc(item.operation)}</div><div class="muted">${esc(item.message)}</div><div class="actions">${reviewTargetActions(item.review_target)}</div></div>`).join("")}</details>` : ""}`;
}

function renderRunMetrics(metrics) {
  if (!metrics?.version) return `<p class="muted">Timing was not recorded for this run.</p>`;
  const phaseLabels = {
    initial_collection_ms: "Initial collection + integration",
    planning_ms: "Investigation planning",
    investigation_collection_ms: "Investigation collection + integration",
    triage_ms: "LLM triage + application",
    evaluation_ms: "Final evaluation",
    run_history_save_ms: "Run history serialization + SQL",
    project_save_ms: "Project serialization + SQL",
  };
  const phases = Object.entries(phaseLabels).filter(([key]) => metrics.phases?.[key] !== undefined);
  const requests = list(metrics.llm_requests);
  return `<dl class="kv"><dt>Total</dt><dd>${esc(formatMilliseconds(metrics.total_ms))}</dd>${phases.map(([key, label]) => `<dt>${esc(label)}</dt><dd>${esc(formatMilliseconds(metrics.phases[key]))}</dd>`).join("")}</dl>${requests.length ? `<details><summary>LLM requests (${requests.length})</summary>${requests.map((item) => `<div class="collector"><div>${pill(item.status, item.status)} <strong>${esc(item.purpose)}</strong> · ${esc(formatMilliseconds(item.duration_ms))}</div><div class="muted">${esc(item.prompt_bytes || 0)} prompt bytes · ${esc(item.response_bytes || 0)} response bytes · timeout ${esc(item.timeout_seconds)}s${item.error_category ? ` · ${esc(item.error_category)}` : ""}${item.model ? ` · ${esc(item.model)}` : ""}</div></div>`).join("")}</details>` : `<p class="muted">No LLM request was observed.</p>`}`;
}

function renderServiceOwnership(data, serviceUrls = []) {
  const service = list(data.service_verifications);
  const ownership = list(data.nsid_ownership);
  const permissions = list(data.oauth_permissions);
  const aliases = list(data.url_aliases);
  const targets = uniqueValues(serviceUrls);
  const browserControls = targets.length
    ? `<div class="controls"><label>Anonymous browser target<select id="browser-service-url">${targets.map((url) => `<option value="${esc(url)}">${esc(url)}</option>`).join("")}</select></label><button id="verify-service-browser" class="primary">Verify in anonymous browser</button></div>`
    : `<p class="muted">Add a confirmed or candidate service URL before browser verification.</p>`;
  return `<dl class="kv"><dt>Current service</dt><dd>${pill(data.current_service_status)} ${esc(data.current_service_status_confidence || "none")}</dd><dt>Verification history</dt><dd>${esc(data.service_verification_status || "not_observed")}</dd><dt>Ownership signal</dt><dd>${esc(data.ownership_signal || "unknown")}</dd><dt>OAuth metadata</dt><dd>${esc(data.oauth_status || "not_observed")}</dd><dt>URL aliases</dt><dd>${esc(data.url_alias_status || "not_observed")}</dd></dl>${browserControls}
    ${service.length ? `<details><summary>Service verification comparison (${service.length})</summary>${service.map((item) => `<div class="collector">${pill(item.status, item.status)} ${esc(formatTimestamp(item.completed_at))} · ${esc(item.source)}</div>`).join("")}</details>` : ""}
    ${ownership.length ? `<details><summary>NSID ownership (${ownership.length})</summary>${ownership.map((item) => `<div class="relation ${esc(item.classification)}"><strong>${esc(item.nsid)}</strong> ${pill(item.classification, item.classification)}<div class="muted">${esc(item.evidence_status)} · ${esc(item.evidence.map((entry) => `${entry.predicate}:${entry.source}`).join(", ") || "no saved ownership relation")}</div></div>`).join("")}</details>` : ""}
    ${permissions.length ? `<details><summary>OAuth permissions (${permissions.length})</summary>${permissions.map((item) => `<div class="relation ${esc(item.classification)}"><strong>${esc(item.nsid)}</strong> ${pill(item.method)}${item.include_unresolved ? pill("include unresolved", "candidate") : ""}<div class="muted">${esc(item.source)}</div></div>`).join("")}</details>` : ""}
    ${aliases.length ? `<details><summary>URL aliases (${aliases.length})</summary>${aliases.map((item) => `<div class="identifier ${esc(item.confidence || "candidate")}"><div>${link(item.alias)} → ${link(item.canonical)}</div><div class="muted">${esc(item.reason || "observed alias")}</div><div class="actions">${actionButton("Block alias", "block-identifier", {type:"service_url", value:item.alias}, "small danger")}${actionButton("Defer alias", "defer-identifier", {type:"service_url", value:item.alias})}</div></div>`).join("")}</details>` : ""}`;
}

function renderExplanation(topic, item) {
  return `<div data-explain-topic="${esc(topic)}"><details><summary>${esc(topic)}: ${esc(item.conclusion)}</summary><dl class="kv"><dt>Rule</dt><dd>${esc(item.rule)}</dd><dt>Supports</dt><dd>${list(item.supporting_signals).map((value) => pill(value, "confirmed")).join(" ") || "None observed"}</dd><dt>Counters</dt><dd>${list(item.counter_signals).map((value) => pill(value, "candidate")).join(" ") || "None observed"}</dd></dl>${item.sources?.length ? json(item.sources) : ""}</details></div>`;
}

function renderExplanations(topics) {
  const entries = Object.entries(topics || {});
  if (!entries.length) return `<p class="muted">No explanation available.</p>`;
  return entries.map(([topic, item]) => renderExplanation(topic, item)).join("");
}

async function loadMergeAnalysis(projectKey, revision, explanations) {
  const request = ++state.mergeAnalysisRequest;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectKey)}/merge-analysis`);
    if (request !== state.mergeAnalysisRequest || state.selected !== projectKey || state.detailRevision !== revision) return;
    if (data.revision !== revision) {
      $("merge-analysis-content").innerHTML = `<p class="muted">Project changed. Reload detail to recalculate merge candidates.</p>`;
      return;
    }
    const recommendation = data.merge_recommendation || {};
    const candidates = list(data.duplicates);
    const target = candidates.find((item) => item.project_key === recommendation.target_project_key);
    const otherCandidates = candidates.filter((item) => item.project_key !== recommendation.target_project_key).slice(0, 5);
    state.mergeTargetRevision = data.merge_target_revision || "";
    $("merge-analysis-content").innerHTML = recommendation.target_project_key
      ? `<dl class="kv"><dt>Candidate</dt><dd>${esc(target?.name || recommendation.target_project_key)} <span class="muted">${esc(recommendation.target_project_key)}</span></dd><dt>Confidence</dt><dd>${pill(recommendation.merge_confidence || "unknown")} score ${esc(recommendation.score || 0)}</dd><dt>Auto merge</dt><dd>${recommendation.auto_merge_ready ? pill("ready", "confirmed") : "Manual review required"}</dd></dl><div class="actions"><button class="small" data-view-project="${esc(recommendation.target_project_key)}">View candidate detail</button><button class="small" data-review-merge="${esc(recommendation.target_project_key)}">Compare before merge</button></div>${otherCandidates.length ? `<details><summary>Other candidates (${candidates.length - 1})</summary>${otherCandidates.map((item) => `<div class="identifier"><div>${esc(item.name || item.project_key)} <span class="muted">${esc(item.project_key)}</span></div><div>${pill(item.merge_confidence || "unknown")} score ${esc(item.score || 0)}</div><div class="actions"><button class="small" data-view-project="${esc(item.project_key)}">View</button><button class="small" data-review-merge="${esc(item.project_key)}">Compare</button></div></div>`).join("")}</details>` : ""}`
      : `<p class="muted">No merge candidate found.</p>`;
    document.querySelectorAll("[data-view-project]").forEach((button) => button.onclick = () => loadDetail(button.dataset.viewProject));
    document.querySelectorAll("[data-review-merge]").forEach((button) => button.onclick = () => loadMergeDetail(projectKey, button.dataset.reviewMerge, {backTarget: {type: "project", key: projectKey}}));
    if (recommendation.target_project_key) {
      $("merge-analysis-action").innerHTML = `<button id="compare-merge-recommendation" class="secondary">Compare merge candidate</button>`;
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
  target.innerHTML = `<p class="muted">Loading runs...</p>`;
  try {
    const data = await api(`/api/projects/${encodeURIComponent(projectKey)}/investigation-runs?limit=50`);
    target.innerHTML = list(data.items).length ? `<div>${data.items.map((run) => `<button class="card" data-investigation-run="${esc(run.run_id)}"><span class="card-title"><strong>${esc(formatTimestamp(run.completed_at))}</strong>${pill(run.problem_count ? "problems" : "complete", run.problem_count ? "candidate" : "confirmed")}</span><span class="muted">${esc(run.evaluation_version)} · ${esc(run.collector_count)} collectors${run.total_ms ? ` · ${esc(formatMilliseconds(run.total_ms))}` : ""}</span></button>`).join("")}</div><div id="investigation-run-detail"></div>` : `<p class="muted">No saved runs.</p>`;
    target.querySelectorAll("[data-investigation-run]").forEach((button) => button.onclick = () => loadInvestigationRun(button.dataset.investigationRun));
  } catch (error) { status(error.message, true); }
}

async function loadInvestigationRun(runId) {
  const target = $("investigation-run-detail");
  target.innerHTML = `<p class="muted">Loading run...</p>`;
  try {
    const run = await api(`/api/investigation-runs/${encodeURIComponent(runId)}?evidence_limit=100`);
    target.innerHTML = section("Run detail", `<dl class="kv"><dt>Run</dt><dd>${esc(run.run_id)}</dd><dt>Records</dt><dd>${esc(run.record_count)}${run.records_truncated ? " (first 100 shown)" : ""}</dd><dt>Failures</dt><dd>${esc(run.failure_count)}</dd><dt>Evaluation</dt><dd>${esc(run.evaluation_version)}</dd></dl>${section("Timing", renderRunMetrics(run.metrics))}${section("Collectors", run.collectors.map((item) => `<div class="collector">${pill(item.status, item.status)} ${esc(item.source)} · ${esc(item.record_count)} records · ${esc(item.failure_count)} failures</div>`).join("") || `<p class="muted">No collectors.</p>`)}<details><summary>Evidence</summary>${json(run.records)}</details><details><summary>Failures</summary>${json(run.failures)}</details><details><summary>Input / output / evaluation</summary>${json({input_project:run.input_project, output_project:run.output_project, evaluation:run.evaluation})}</details><div class="actions"><button id="replay-investigation-run">Replay with current rules</button></div><div id="investigation-replay-result"></div>`);
    $("replay-investigation-run").onclick = async () => {
      try {
        const replay = await api(`/api/investigation-runs/${encodeURIComponent(runId)}/replay`);
        $("investigation-replay-result").innerHTML = section("Replay diff", json(replay));
      } catch (error) { status(error.message, true); }
    };
  } catch (error) { status(error.message, true); }
}

function reviewTargetActions(target) {
  if (!target) return "";
  const data = {"target-type":target.target_type, "target-value":target.target_value};
  return `${actionButton("Dismiss", "dismiss-review-target", data)}${actionButton("Defer 30d", "defer-review-target", data)}`;
}

function renderMultiTenantSuffixSuggestions(suggestions) {
  const items = list(suggestions);
  if (!items.length) return "";
  return section("Multi-Tenant Suffix Candidate", items.map((item) => `
    <div class="suffix-suggestion">
      <div class="suffix-suggestion-main">
        <div>${pill("suggested", "candidate")} <strong>${esc(item.suffix)}</strong></div>
        <div class="muted">Source tenants: ${esc(list(item.source_tenants).join(", ") || "none")}</div>
        <div class="muted">Target tenants: ${esc(list(item.target_tenants).join(", ") || "none")}</div>
        <div class="muted">Hosts: ${esc(list(item.source_hosts).join(", ") || "none")} → ${esc(list(item.target_hosts).join(", ") || "none")}</div>
      </div>
      <div class="actions"><button class="small" data-add-multi-tenant-suffix="${esc(item.suffix)}">Add suffix</button></div>
    </div>
  `).join(""));
}

function editProfile(project, profile) {
  const dialog = $("profile-dialog");
  $("profile-name").value = project.name || "";
  $("profile-introduced-url").value = project.introduced_url || "";
  $("profile-notes").value = profileValue(profile, "notes");
  $("profile-diff").innerHTML = "";
  $("profile-apply").disabled = true;
  let preview = null;
  const values = () => ({
    name:$("profile-name").value,
    introduced_url:$("profile-introduced-url").value,
    notes:$("profile-notes").value,
    expected_revision:state.detailRevision
  });
  $("profile-preview").onclick = async () => {
    try {
      const response = await api(`/api/projects/${encodeURIComponent(project.project_key)}/profile-preview`, {method:"POST", body:JSON.stringify(values())});
      preview = response.result;
      $("profile-diff").innerHTML = preview.changed ? section("Changes", json(preview.changes)) : `<p class="muted">No changes.</p>`;
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
      status("Profile saved.");
    } catch (error) {
      if (error.code === "stale_project") await loadDetail(project.project_key, {updateUrl:false});
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
  const input = prompt("NSID, repository URL, or service URL. Use repo_url: https://... to override.");
  const parsed = parseSeedInput(input);
  if (!parsed.seed) return;
  try {
    const typeQuery = parsed.type ? `&type=${encodeURIComponent(parsed.type)}` : "";
    const preview = await api(`/api/seeds/preview?seed=${encodeURIComponent(parsed.seed)}${typeQuery}`);
    if (preview.exact_project_key && preview.exact_project_key !== projectKey) throw new Error(`Seed belongs to ${preview.exact_project_key}`);
    const action = preview.exact_project_key === projectKey ? "Reapply" : "Add";
    confirmMutation("Add identifier", `${action} ${preview.seed_type}: ${preview.seed_value} to ${projectKey}?`, "/api/seeds", {seed:parsed.seed, type:parsed.type, project_key:projectKey, expected_revision:state.detailRevision}, false);
  } catch (error) { status(error.message, true); }
}

function bindDetailActions(projectKey) {
  document.querySelectorAll("[data-action]").forEach((button) => button.onclick = () => {
    const action = button.dataset.action;
    const payload = {identifier_type: button.dataset.type || "", value: button.dataset.value || "", other_project_key: button.dataset.other || "", target_type: button.dataset.targetType || "", target_value: button.dataset.targetValue || "", review_action: button.dataset.reviewAction || "", days: button.dataset.days || ""};
    const needsNote = ["reject-candidate", "block-identifier", "defer-identifier", "split-project", "defer-promotion", "dismiss-review-target", "defer-review-target", "lower-promotion-priority"].includes(action);
    const title = action === "lower-promotion-priority"
      ? "Lower candidate priority"
      : action === "clear-promotion-priority"
        ? "Restore normal candidate priority"
        : action.replaceAll("-", " ");
    const message = ["lower-promotion-priority", "clear-promotion-priority"].includes(action)
      ? `${title} for ${projectKey}?`
      : `${title} ${payload.value || payload.other_project_key || projectKey}?`;
    confirmMutation(
      title,
      message,
      `/api/projects/${encodeURIComponent(projectKey)}/${action}`,
      payload,
      needsNote,
      button.dataset.defaultNote || "",
      action === "lower-promotion-priority" ? "Reason or note (optional)" : "Reason or note",
    );
  });
}

function confirmMutation(title, message, path, payload, note, defaultNote = "", noteLabel = "Reason or note") {
  const dialog = $("confirm-dialog");
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
        if (path.startsWith("/api/projects/")) payload.expected_revision = state.detailRevision;
        if (path.endsWith("/merge-recommendation")) payload.expected_target_revision = state.mergeTargetRevision;
        status("Saving...");
        const response = await api(path, {method:"POST", body:JSON.stringify(payload)});
        if (advanceReviewListAfterMutation(response, path)) {
          return;
        }
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
      return `<article class="merge-project"><div class="card-title"><strong>${esc(project.name || project.project_key)}</strong>${pill(project.tracking_state)}</div><div class="muted">${esc(project.project_key)}</div><dl class="kv"><dt>Website</dt><dd>${project.website_url ? link(project.website_url) : "None"}</dd><dt>Status</dt><dd>${pill(project.service_status)} ${pill(project.atproto_contribution_level)}</dd><dt>Seed</dt><dd>${esc(`${project.seed?.type || ""}: ${project.seed?.value || ""}`)}</dd>${projectTimestampRows(project)}</dl><div class="merge-identifiers"><strong>Confirmed identifiers</strong>${mergeIdentifierRows(identifiers, "confirmed")}<strong>Candidates</strong>${mergeIdentifierRows(identifiers, "candidate")}</div><button class="small" data-merge-view="${esc(side)}">Open full detail</button></article>`;
    };
    const firstCreatorBody = data.first_creator_details_included
      ? `<dl class="kv"><dt>Overlap</dt><dd>${json(data.first_creator_overlap || {})}</dd><dt>Source first DIDs</dt><dd>${json(data.source_collection_first_creator_dids || {})}</dd><dt>Target first DIDs</dt><dd>${json(data.target_collection_first_creator_dids || {})}</dd><dt>Source lookup details</dt><dd>${json(data.source_collection_first_creator_details || {})}</dd><dt>Target lookup details</dt><dd>${json(data.target_collection_first_creator_details || {})}</dd></dl><p class="muted">Supplemental only. This is not mixed into the primary pair signal in this view. Lookup details show not_found/error when listReposByCollection does not return a first creator for a collection.</p>`
      : `<p class="muted">Not loaded. This calls listReposByCollection and may take a moment.</p><div class="actions"><button id="load-first-creator-heuristic" class="secondary">Load first-creator details</button></div>`;
    const mergeActions = `<div class="actions"><button id="skip-review-item" class="secondary">Skip for now</button><button id="mark-separate">Keep separate</button><button id="execute-merge" class="danger">Merge projects</button></div>`;
    $("detail").innerHTML = `${backToListButton()}${contextualBackButton()}<p class="eyebrow">Merge review</p><h2>${esc(data.source?.name || source)} → ${esc(data.target?.name || target)}</h2><div class="actions"><button id="reverse-merge-direction" class="secondary">Reverse source and target</button></div><div class="merge-comparison">${snapshot(data.source, "source")}${snapshot(data.target, "target")}</div>${section("Why these match", `<dl class="kv"><dt>Blocked</dt><dd>${data.merge_blocked ? pill("blocked", "failed") : "No manual separation guard"}</dd><dt>Strict overlap</dt><dd>${json(data.strict_overlap || {})}</dd><dt>Moderate overlap</dt><dd>${json(data.moderate_overlap || {})}</dd><dt>Source signal</dt><dd>${json(data.source_pair_candidate || {})}</dd><dt>Target signal</dt><dd>${json(data.target_pair_candidate || {})}</dd></dl>`)}${renderMultiTenantSuffixSuggestions(data.suggested_multi_tenant_suffixes)}${section("First-creator heuristic", firstCreatorBody)}${section("Merged result preview", `<dl class="kv"><dt>Name</dt><dd>${esc(data.merged_preview?.merged_name || "")}</dd><dt>Website</dt><dd>${link(data.merged_preview?.merged_website_url || "")}</dd><dt>Service status</dt><dd>${pill(data.merged_preview?.merged_service_status || "unknown")}</dd><dt>Repositories</dt><dd>${list(data.merged_preview?.merged_project_repository_urls).map(link).join("<br>") || "None"}</dd><dt>Services</dt><dd>${list(data.merged_preview?.merged_service_urls).map(link).join("<br>") || "None"}</dd></dl>`)}${mergeActions}<div class="detail-footer">${backToListButton()}${contextualBackButton()}</div>`;
    bindBackToListButtons();
    document.querySelectorAll("[data-merge-view]").forEach((button) => button.onclick = () => loadDetail(button.dataset.mergeView === "source" ? source : target, {backTarget: {type: "merge", source, target, previous: state.detailBackTarget}}));
    document.querySelectorAll("[data-add-multi-tenant-suffix]").forEach((button) => button.onclick = () => addMultiTenantSuffix(button.dataset.addMultiTenantSuffix, source, target));
    $("reverse-merge-direction")?.addEventListener("click", () => loadMergeDetail(target, source, {
      includeFirstCreator,
      backTarget: state.detailBackTarget,
    }));
    $("load-first-creator-heuristic")?.addEventListener("click", () => loadMergeDetail(source, target, {includeFirstCreator: true}));
    $("skip-review-item")?.addEventListener("click", () => skipReviewItem(source, target));
    $("mark-separate")?.addEventListener("click", () => confirmMerge("Keep projects separate", "separate", source, target));
    $("execute-merge")?.addEventListener("click", () => confirmMerge("Merge projects", "merge", source, target));
  } catch (error) {
    if (request === state.detailRequest && state.selected === source) status(error.message, true);
  }
}

function addMultiTenantSuffix(suffix, source, target) {
  confirmOperation("Add multi-tenant suffix", `Add ${suffix} to custom_multi_tenant_suffixes? This changes domain family matching for future merge calculations.`, async () => {
    const response = await api("/api/domain-overrides/multi-tenant-suffixes", {method:"POST", body:JSON.stringify({suffix})});
    const prefix = response.added ? `Added multi-tenant suffix ${response.suffix}.` : `Multi-tenant suffix ${response.suffix} was already configured.`;
    if (advanceReviewList(source, {targetKey: target, statusPrefix: prefix})) return;
    status(`${prefix} Refreshing list.`);
    await loadList();
  });
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
  $("detail").innerHTML = `${backToListButton()}<p class="eyebrow">Projects</p><h2>New Seed</h2><div class="controls"><label>NSID, repository URL, or service URL<input id="seed-value" type="text"></label><div class="actions"><button id="preview-seed" class="primary">Preview</button></div></div><div id="seed-preview"></div><div class="detail-footer">${backToListButton()}</div>`;
  document.body.classList.add("detail-open");
  bindBackToListButtons();
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
  $("single-pane").innerHTML = `<p class="eyebrow">Background jobs</p><h2>Operations</h2><p class="notice">Operations run in a separate process. Only one job runs at a time. Enrich stops between projects, sync at source page/item boundaries, and review between candidates.</p><div class="operation-grid">
    <article class="operation-card operation-enrich"><h3>Enrich</h3><div class="controls">
      <label>Target<select id="enrich-target"><option value="all">Active projects</option><option value="project">One project</option></select></label>
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
      <button id="start-review" class="primary">Build review plan</button>
    </div><code>project-registry review-&lt;kind&gt; [--execute]</code></article>
    <article class="operation-card"><h3>Investigation Replay</h3><p>Replay and benchmark saved evidence without network access.</p><code>project-registry replay-investigation -p proj:...</code><code>project-registry benchmark-investigation</code></article>
  </div>${section("Scheduled Presets", `<div id="schedule-list"><p class="muted">Loading...</p></div>`)}${section("Discovered Checkpoints", `<div id="checkpoint-list"><p class="muted">Loading...</p></div>`)}${section("Recent Jobs", `<div id="job-list"><p class="muted">Loading...</p></div>`)}`;
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
  $("confirm-context").textContent = write ? "Confirm database change" : "Confirm operation";
  $("confirm-submit").textContent = write ? "Apply change" : "Start job";
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

function startBrowserServiceVerification(projectKey, serviceUrl) {
  if (!serviceUrl) { status("A saved service URL is required.", true); return; }
  confirmOperation("Verify service in anonymous browser", `Open ${serviceUrl} without signing in and save the rendered service evidence to SQLite?`, async () => {
    status("Starting anonymous browser verification...");
    await api("/api/jobs/verify-service", {method:"POST", body:JSON.stringify({project_key:projectKey, service_url:serviceUrl})});
    status("Browser verification started. Progress is available in Operations.");
  });
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
  const payload = {
    kind,
    execute:false,
    limit: Number($("review-limit").value || 100),
    inactivity_days: Number($("review-inactivity").value || 45),
    max_enrich_age_days: Number($("review-freshness").value || 30),
    note: $("review-note").value.trim(),
  };
  confirmOperation("Build review plan", `Calculate ${kind} candidates from saved data? This does not update SQLite.`, async () => {
    status("Starting review job...");
    await api("/api/jobs/review", {method:"POST", body:JSON.stringify(payload)});
    status("Review job started.");
    await loadJobs();
  }, {write: false});
}

function renderJobs(items) {
  if (!items.length) return `<p class="muted">No jobs have run in this server process.</p>`;
  return items.map((job) => {
    const progress = job.progress || {};
    const current = Number(progress.current || 0);
    const total = Number(progress.total || 0);
    const unit = String(progress.unit || "").trim();
    const count = total > 0
      ? `${current} / ${total}`
      : current > 0 && unit
        ? `${current} ${unit}${current === 1 ? "" : "s"}`
        : "";
    const target = job.request?.parameters?.project_key || (job.request?.parameters?.all_projects ? "bulk" : job.request?.parameters?.source || job.request?.parameters?.kind || "operation");
    const plan = job.operation === "review" && job.status === "completed" && !job.request?.execute ? job.result?.data?.summary : null;
    return `<article class="job-card ${esc(job.status)}"><div class="card-title"><strong>${esc(job.operation)} · ${esc(target)}</strong>${pill(job.status, job.status)}</div>
      <div class="meta-row"><span>${esc(job.job_id)}</span><span>${esc(count)}</span><span>${job.elapsed_seconds === null || job.elapsed_seconds === undefined ? "" : `Elapsed ${esc(formatDuration(job.elapsed_seconds))}`}</span><span>${job.eta_seconds === null || job.eta_seconds === undefined ? "" : `ETA ${esc(formatDuration(job.eta_seconds))}`}</span></div>
      ${job.last_item ? `<p class="muted">Last: ${esc(job.last_item.project_key)} ${esc(job.last_item.name || "")}</p>` : ""}
      ${job.error ? `<p class="job-error">${esc(job.error.message || JSON.stringify(job.error))}</p>` : ""}
      <div class="actions">
        ${job.can_cancel ? `<button class="danger small" data-job-cancel="${esc(job.job_id)}">Request stop</button>` : ""}
        ${job.can_resume ? `<button class="small" data-job-resume="${esc(job.job_id)}">Resume</button>` : ""}
        ${plan?.candidates?.length ? `<button class="small primary" data-review-plan="${esc(job.job_id)}">Review ${esc(plan.candidates.length)} candidates</button>` : ""}
      </div><details class="job-details" data-job-details="${esc(job.job_id)}" ${state.openJobDetails.has(job.job_id) ? "open" : ""}>
        <summary>Details</summary>${plan?.candidates?.length ? "" : `<div class="actions"><button class="small" data-job-detail-refresh="${esc(job.job_id)}">Refresh details</button></div>`}<div id="job-detail-${esc(job.job_id.replaceAll(":", "-"))}">${plan?.candidates?.length ? renderReviewPlan(job) : state.jobDetailCache.has(job.job_id) ? json(state.jobDetailCache.get(job.job_id)) : `<p class="muted">Open to load details.</p>`}</div>
      </details></article>`;
  }).join("");
}

function renderReviewPlan(job) {
  const summary = job.result?.data?.summary || {};
  return `<div class="review-plan">${list(summary.candidates).map((item) => `<label><span><input type="checkbox" data-plan-candidate="${esc(job.job_id)}" value="${esc(item.candidate_id)}" checked> ${esc(item.project_key)} ${esc(item.kind || item.suggested_tracking_state || "")}</span></label>`).join("")}<div class="actions"><button class="danger" data-review-execute="${esc(job.job_id)}">Apply selected</button></div></div>`;
}

function renderCheckpoints(items) {
  if (!items.length) return `<p class="muted">No enrich checkpoints found.</p>`;
  return items.map((item) => `<article class="job-card"><div class="card-title"><strong>${esc(item.run_id)}</strong>${pill(item.status, item.status)}</div><div class="meta-row"><span>${esc(item.completed_project_count)} / ${esc(item.selected_project_count)}</span><span>${esc(formatTimestamp(item.updated_at))}</span></div>${item.can_resume ? `<button class="small" data-checkpoint-resume="${esc(item.run_id)}">Resume</button>` : ""}</article>`).join("");
}

function scheduleWeekdayOptions(selected) {
  return ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, index) => `<option value="${index}" ${Number(selected) === index ? "selected" : ""}>${label}</option>`).join("");
}

function scheduleWeekOptions(selected) {
  return [
    [0, "Every week"],
    [1, "1st week"],
    [2, "2nd week"],
    [3, "3rd week"],
    [4, "4th week"],
    [5, "5th week"],
  ].map(([value, label]) => `<option value="${value}" ${Number(selected) === value ? "selected" : ""}>${label}</option>`).join("");
}

function renderSchedules(data) {
  const items = list(data.items);
  if (!items.length) return `<p class="muted">No schedule presets are available.</p>`;
  const queue = list(data.queue);
  const queueHtml = queue.length ? `<p class="notice">Queued scheduled work: ${esc(queue.map((item) => `${item.preset_id}:${item.current_job_id || "waiting"}`).join(", "))}</p>` : "";
  const observation = data.lexicon_publication_observation || {};
  const observationRuns = list(observation.items);
  const observationHtml = `<article class="operation-card">
    <div class="card-title"><strong>Lexicon observation gate</strong>${observation.review_due ? pill("review due", "warning") : pill("monitoring")}</div>
    <p class="muted">${esc(observation.successful_run_count || 0)} successful weekly runs · retains ${esc(observation.history_limit || 16)} runs</p>
    ${list(observation.review_reasons).length ? `<p class="notice">${esc(list(observation.review_reasons).join(", "))}</p>` : ""}
    ${observation.error ? `<p class="job-error">${esc(observation.error)}</p>` : ""}
    ${observationRuns.length ? `<details><summary>Recent observation history</summary>${observationRuns.map((run) => `<p class="muted"><strong>${esc(run.status)}</strong> ${esc(formatTimestamp(run.completed_at))} · watermark ${esc(run.starting_watermark_seq)} → ${esc(run.ending_watermark_seq)} · changes +${esc(run.new_count)}/~${esc(run.updated_count)}/-${esc(run.deleted_count)} · inventory ${esc(run.inventory_status)} (${esc(run.inventory_collection_count)}) · verification ${esc(run.verification_success_count)}/${esc(run.verification_target_count)}${run.review_due ? ` · review: ${esc(list(run.review_reasons).join(", "))}` : ""}</p>`).join("")}</details>` : `<p class="muted">No durable observation runs yet.</p>`}
  </article>`;
  return `${queueHtml}${observationHtml}<div class="operation-grid">${items.map((item) => {
    const schedule = item.schedule || {};
    const steps = list(item.steps).map((step) => esc(step.label || step.operation)).join(" → ");
    const statusLine = [item.last_status, item.last_message, item.last_started_at ? `started ${formatTimestamp(item.last_started_at)}` : "", item.last_completed_at ? `completed ${formatTimestamp(item.last_completed_at)}` : ""].filter(Boolean).join(" · ");
    return `<article class="operation-card schedule-card" data-schedule-card="${esc(item.preset_id)}">
      <div class="card-title"><strong>${esc(item.label)}</strong>${schedule.enabled ? pill("enabled", "confirmed") : pill("disabled")}</div>
      <p class="muted">${esc(item.description)}</p>
      <p class="muted">${steps}</p>
      <div class="controls">
        <label><span><input type="checkbox" data-schedule-field="enabled" data-preset-id="${esc(item.preset_id)}" ${schedule.enabled ? "checked" : ""}> Enabled</span></label>
        <label>Week<select data-schedule-field="week_of_month" data-preset-id="${esc(item.preset_id)}">${scheduleWeekOptions(schedule.week_of_month)}</select></label>
        <label>Weekday<select data-schedule-field="weekday" data-preset-id="${esc(item.preset_id)}">${scheduleWeekdayOptions(schedule.weekday)}</select></label>
        <label>Time<input type="time" data-schedule-field="time" data-preset-id="${esc(item.preset_id)}" value="${esc(schedule.time || "03:00")}"></label>
        <label>Time zone<input data-schedule-field="timezone" data-preset-id="${esc(item.preset_id)}" value="${esc(schedule.timezone || "Asia/Tokyo")}"></label>
      </div>
      <p class="muted">Next: ${esc(formatTimestamp(item.next_due_at))}</p>
      ${statusLine ? `<p class="muted">${esc(statusLine)}</p>` : ""}
      <div class="actions"><button class="small primary" data-schedule-run="${esc(item.preset_id)}">Run now</button></div>
    </article>`;
  }).join("")}</div><div class="actions"><button id="save-schedules" class="primary">Save schedules</button></div>`;
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
      status("Schedules saved.");
      await loadJobs();
    } catch (error) { status(error.message, true); }
  });
  document.querySelectorAll("[data-schedule-run]").forEach((button) => button.onclick = () => {
    const presetId = button.dataset.scheduleRun;
    confirmOperation("Run scheduled preset", `Queue ${presetId} now? This may write to SQLite.`, async () => {
      await api(`/api/job-schedules/${encodeURIComponent(presetId)}/run`, {method:"POST", body:"{}"});
      status("Scheduled preset queued.");
      await loadJobs();
    });
  });
  document.querySelectorAll("[data-job-detail-refresh]").forEach((button) => button.onclick = () => refreshJobDetail(button.dataset.jobDetailRefresh));
  document.querySelectorAll("[data-checkpoint-resume]").forEach((button) => button.onclick = () => confirmOperation("Resume discovered checkpoint", `Resume ${button.dataset.checkpointResume}?`, async () => {
    await api(`/api/checkpoints/${encodeURIComponent(button.dataset.checkpointResume)}/resume`, {method:"POST", body:"{}"});
    status("Resume job started."); await loadJobs();
  }));
  document.querySelectorAll("[data-review-plan]").forEach((button) => button.onclick = () => {
    const details = button.closest("article").querySelector("details"); details.open = true;
  });
  document.querySelectorAll("[data-review-execute]").forEach((button) => button.onclick = () => {
    const jobId = button.dataset.reviewExecute;
    const card = button.closest("article");
    const selected = [...card.querySelectorAll(`[data-plan-candidate="${jobId}"]:checked`)].map((item) => item.value);
    const sourceJob = state.jobsById.get(jobId);
    if (!sourceJob || !selected.length) { status("Select at least one review candidate.", true); return; }
    confirmOperation("Apply review plan", `Apply ${selected.length} selected candidates?`, async () => {
      const summary = sourceJob.result.data.summary;
      await api("/api/jobs/review", {method:"POST", body:JSON.stringify({...sourceJob.request.parameters, execute:true, selected_candidate_ids:selected, plan_fingerprint:summary.plan_fingerprint})});
      status("Review execution started."); await loadJobs();
    });
  });
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
    const sourceJob = state.jobsById.get(jobId);
    if (sourceJob?.operation === "review" && sourceJob?.result?.data?.summary?.candidates?.length) return;
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
    <label>Time zone<input id="setting-time-zone" value="${esc(state.settings.timeZone || defaults.timeZone)}" placeholder="Asia/Tokyo or +09:00"></label>
    <label class="settings-toggle"><span>Promotion queue</span><span><input id="setting-personal" type="checkbox" ${state.settings.includePersonalTools ? "checked" : ""}> Include personal tools</span></label>
  </div><div class="actions"><button id="save-settings" class="primary">Save settings</button><button id="clear-token" class="danger">Clear API token</button><button id="reload-server" class="danger">Reload server</button></div>
  ${section("Server", `<dl class="kv"><dt>API</dt><dd>v${esc(state.server?.api_version || "?")}</dd><dt>Schema</dt><dd>v${esc(state.server?.schema_version || "?")}</dd><dt>Commit</dt><dd>${state.server?.build?.commit ? `<code>${esc(state.server.build.commit)}${state.server.build.dirty ? " (dirty)" : ""}</code>` : "Unavailable outside a Git checkout"}</dd><dt>Python</dt><dd>${esc(state.server?.build?.python || "?")}</dd><dt>Database</dt><dd>${esc(state.server?.db_path || "Unavailable")}</dd><dt>Background jobs</dt><dd>${state.server?.operations_connected ? "Connected: enrich, sync, and review subprocess worker" : "Not connected"}</dd></dl>`)}
  `;
  $("setting-merge-mode").value = state.settings.mergeMode;
  $("save-settings").onclick = () => {
    const timeZone = normalizeTimeZone($("setting-time-zone").value);
    if (!timeZone) { status("Invalid time zone. Use an IANA name like Asia/Tokyo or an offset like +09:00.", true); return; }
    state.settings = {reviewLimit:Number($("setting-review").value), projectLimit:Number($("setting-project").value), mergeLimit:Number($("setting-merge-limit").value), mergeMode:$("setting-merge-mode").value, includePersonalTools:$("setting-personal").checked, timeZone};
    saveSettings(); status("Settings saved.");
    renderSettings();
  };
  $("clear-token").onclick = () => { localStorage.removeItem("projectRegistryToken"); token = ""; status("Token cleared. Reload to sign in again."); };
  $("reload-server")?.addEventListener("click", confirmServerReload);
}

function confirmServerReload() {
  const dialog = $("confirm-dialog");
  $("confirm-title").textContent = "Reload server";
  $("confirm-message").textContent = "Restart the Web server with the same command-line arguments? Active background jobs receive a stop request; jobs that exceed the shutdown timeout are terminated.";
  $("confirm-note-label").hidden = true;
  showDialogWithoutInputFocus(dialog);
  dialog.onclose = async () => {
    if (dialog.returnValue !== "confirm") return;
    const previousInstance = state.server?.server_instance_id || "";
    try {
      status("Requesting server reload...");
      await api("/api/reload", {method:"POST", body:JSON.stringify({confirm:true})});
      status("Waiting for server restart...");
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
    status("Server reload was requested, but the restarted server did not become ready within 30 seconds.", true);
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
