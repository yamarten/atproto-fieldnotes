#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import uuid
from concurrent.futures import ThreadPoolExecutor
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import project_registry as registry


DEFAULT_REVIEW_STATES = {"active", "watchlist", "stable", "stalled"}
DEFAULT_PROMOTION_STATES = {"active", "watchlist"}
MERGE_BATCH_MODES = {"strict", "moderate", "broad"}
SYNC_KINDS = {"introduced", "collections", "github-search", "semble-collections"}


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Project Registry</title>
  <style>
    :root {
      color-scheme: light dark;
      --bg: #f7f7f4;
      --panel: #ffffff;
      --text: #1d2525;
      --muted: #66706f;
      --line: #d9dedb;
      --accent: #176c5f;
      --accent-text: #ffffff;
      --danger: #a83232;
      --confirmed-line: #2f756c;
      --confirmed-bg: #edf7f3;
      --candidate-line: #b7791f;
      --candidate-bg: #fff7e6;
      --shadow: 0 1px 3px rgba(20, 30, 30, .12);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #101414;
        --panel: #192020;
        --text: #eef4f2;
        --muted: #a5b0ad;
        --line: #2c3836;
        --accent: #4ab5a2;
        --accent-text: #06100e;
        --danger: #ef8a8a;
        --confirmed-line: #4ab5a2;
        --confirmed-bg: #142826;
        --candidate-line: #d69a3d;
        --candidate-bg: #2b2415;
        --shadow: none;
      }
    }
    * { box-sizing: border-box; }
    html {
      height: 100%;
    }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-size: 15px;
      line-height: 1.45;
      min-height: 100dvh;
      height: 100dvh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }
    header {
      flex: 0 0 auto;
      position: sticky;
      top: 0;
      z-index: 5;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      padding: 10px 12px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 18px;
      line-height: 1.2;
    }
    main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(320px, 45vw);
      gap: 12px;
      padding: 12px;
      max-width: 1280px;
      width: 100%;
      flex: 1 1 auto;
      min-height: 0;
      margin: 0 auto;
      overflow: hidden;
    }
    .tabs, .toolbar, .actions, .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .identifier-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      align-items: flex-start;
      width: 100%;
      padding: 3px 4px;
      border-left: 3px solid var(--line);
      border-radius: 5px;
      background: var(--bg);
    }
    .identifier-confirmed {
      border-left-color: var(--confirmed-line);
      background: var(--confirmed-bg);
    }
    .identifier-candidate {
      border-left-color: var(--candidate-line);
      background: var(--candidate-bg);
    }
    .identifier-value {
      min-width: 0;
      padding-top: 6px;
      overflow-wrap: anywhere;
    }
    .identifier-value a {
      display: inline;
    }
    .identifier-row > button {
      flex: 0 0 auto;
    }
    .identifier-list {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    button, select, input {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 7px;
      min-height: 36px;
      padding: 7px 10px;
      font: inherit;
    }
    button {
      cursor: pointer;
      box-shadow: var(--shadow);
    }
    button.primary {
      background: var(--accent);
      color: var(--accent-text);
      border-color: var(--accent);
    }
    button.danger { color: var(--danger); }
    button.active {
      border-color: var(--accent);
      color: var(--accent);
      font-weight: 700;
    }
    input[type="search"], input[type="text"] {
      width: min(100%, 360px);
      flex: 1 1 180px;
    }
    .list {
      display: grid;
      gap: 10px;
      min-width: 0;
      min-height: 0;
      align-content: start;
      grid-auto-rows: max-content;
      overflow: auto;
      overscroll-behavior: contain;
    }
    .list-count {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
      min-height: 28px;
    }
    .card, .detail {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      box-shadow: var(--shadow);
      min-width: 0;
    }
    .card {
      display: grid;
      gap: 7px;
      text-align: left;
      width: 100%;
    }
    .card > a {
      justify-self: start;
      max-width: 100%;
    }
    .card h2, .detail h2 {
      margin: 0;
      font-size: 17px;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }
    .muted { color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 2px 7px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 12px;
      max-width: 100%;
    }
    .signals {
      display: grid;
      gap: 6px;
    }
    .signal {
      border-left: 3px solid var(--accent);
      padding-left: 8px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .detail {
      min-height: 0;
      overflow: auto;
      overscroll-behavior: contain;
      display: grid;
      gap: 12px;
      align-content: start;
    }
    .section {
      display: grid;
      gap: 6px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }
    .section h3 {
      margin: 0;
      font-size: 13px;
      text-transform: uppercase;
      letter-spacing: .03em;
      color: var(--muted);
    }
    a { color: var(--accent); overflow-wrap: anywhere; }
    pre {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      background: var(--bg);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      max-height: 420px;
      overflow: auto;
      font-size: 12px;
    }
    .status {
      min-height: 24px;
      color: var(--muted);
      padding-top: 8px;
    }
    .mobile-detail-bar {
      display: none;
    }
    @media (max-width: 820px) {
      body {
        display: block;
        height: auto;
        min-height: 100dvh;
        overflow: auto;
      }
      main {
        display: flex;
        flex-direction: column;
        height: auto;
        min-height: 0;
        padding: 10px;
        overflow: visible;
      }
      .list {
        height: auto;
        overflow: visible;
        margin-bottom: 12px;
      }
      .detail {
        display: none;
        order: -1;
        max-height: none;
        overflow: visible;
        margin-bottom: 12px;
      }
      body.detail-open .detail {
        display: grid;
      }
      .mobile-detail-bar {
        display: flex;
        justify-content: flex-start;
      }
      .mobile-detail-bar button {
        width: 100%;
      }
      header { padding: 9px 10px; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Project Registry</h1>
    <div class="tabs">
      <button id="tab-review" class="active" title="Show projects that need review. This does not change the DB.">Review</button>
      <button id="tab-promotion" title="Show candidates that may be ready for introduction. This does not change the DB.">Promotion</button>
      <button id="tab-merge" title="Review duplicate merge plans. Loading this list does not change the DB.">Merge</button>
      <button id="tab-sync" title="Run sync dry-runs or explicit execute jobs. Dry-run does not change the DB.">Sync</button>
      <button id="tab-search" title="Search projects by name, seed, NSID, or URL. This does not change the DB.">Search</button>
      <button id="tab-settings" title="Configure Web UI options saved in this browser.">Settings</button>
    </div>
    <div class="toolbar">
      <input id="query" type="search" placeholder="Search project, seed, URL">
      <select id="merge-mode" title="Safety level for merge-batch planning. moderate matches CLI interactive default.">
        <option value="moderate">moderate</option>
        <option value="strict">strict</option>
        <option value="broad">broad</option>
      </select>
      <button id="refresh" class="primary" title="Reload the current list from SQLite. This does not change the DB.">Refresh</button>
    </div>
    <div id="status" class="status"></div>
  </header>
  <main>
    <section id="list" class="list"></section>
    <aside id="detail" class="detail">
      <p class="muted">Select a project.</p>
    </aside>
  </main>
  <script>
    const params = new URLSearchParams(location.search);
    const tokenParam = params.get("token");
    if (tokenParam) {
      localStorage.setItem("projectRegistryToken", tokenParam);
      params.delete("token");
      const next = location.pathname + (params.toString() ? "?" + params.toString() : "") + location.hash;
      history.replaceState(null, "", next);
    }
    let token = localStorage.getItem("projectRegistryToken") || prompt("Project Registry token");
    if (token) localStorage.setItem("projectRegistryToken", token);

    const defaultSettings = {
      reviewLimit: 50,
      promotionLimit: 50,
      mergeLimit: 50,
      mergeMode: "moderate",
      includePersonalTools: false,
      llmEndpoint: "",
      llmModel: "",
      llmApiKey: "",
      llmApiStyle: "auto",
      llmReasoningEffort: "",
      syncKind: "introduced",
      syncQuery: "from:yamarten.bsky.social #ATプロトーク",
      syncLimit: 25,
      syncMaxPages: 4,
      syncPages: 1,
      syncPerPage: 20,
      syncLookbackDays: 10,
      syncCreateUnmatched: false,
      syncScreenUnmatched: false,
      syncAllHistory: false
    };
    const state = { tab: "review", selected: null, mergeItems: [], settings: loadSettings() };
    const reasoningEffortPresets = ["", "none", "minimal", "low", "medium", "high", "on", "off"];
    const $ = (id) => document.getElementById(id);
    const status = (text) => { $("status").textContent = text || ""; };
    const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[ch]));
    const asList = (value) => Array.isArray(value) ? value : [];
    const uniqueValues = (values) => {
      const seen = new Set();
      const result = [];
      for (const value of asList(values)) {
        const text = String(value || "").trim();
        if (!text || seen.has(text)) continue;
        seen.add(text);
        result.push(text);
      }
      return result;
    };

    async function api(path, options = {}) {
      const headers = Object.assign({ "X-Project-Registry-Token": token || "" }, options.headers || {});
      if (options.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
      const res = await fetch(path, Object.assign({}, options, { headers }));
      const text = await res.text();
      const data = text ? JSON.parse(text) : {};
      if (!res.ok) throw new Error(data.error || res.statusText);
      return data;
    }

    function setTab(tab) {
      state.tab = tab;
      for (const name of ["review", "promotion", "merge", "sync", "search", "settings"]) {
        $("tab-" + name).classList.toggle("active", name === tab);
      }
      $("query").style.display = tab === "search" ? "" : "none";
      $("merge-mode").style.display = tab === "merge" ? "" : "none";
      if (tab === "sync") {
        loadSyncScreen();
        return;
      }
      if (tab === "settings") {
        loadSettingsScreen();
        return;
      }
      closeDetailPane(true);
      loadList();
    }

    function detailBackButton() {
      return `<div class="mobile-detail-bar"><button id="close-detail" title="Hide details and return to the list.">Back to list</button></div>`;
    }

    function openDetailPane() {
      document.body.classList.add("detail-open");
      if (window.matchMedia("(max-width: 820px)").matches) {
        $("detail").scrollIntoView({ block: "start" });
      }
    }

    function closeDetailPane(reset = false) {
      document.body.classList.remove("detail-open");
      if (reset) $("detail").innerHTML = `<p class="muted">Select a project.</p>`;
    }

    function wireDetailBackButton() {
      const button = $("close-detail");
      if (button) button.onclick = () => closeDetailPane(false);
    }

    function loadSettings() {
      try {
        return Object.assign({}, defaultSettings, JSON.parse(localStorage.getItem("projectRegistrySettings") || "{}"));
      } catch {
        return Object.assign({}, defaultSettings);
      }
    }

    function saveSettings(nextSettings) {
      state.settings = Object.assign({}, defaultSettings, nextSettings || {});
      localStorage.setItem("projectRegistrySettings", JSON.stringify(state.settings));
      $("merge-mode").value = state.settings.mergeMode || "moderate";
    }

    function reasoningEffortPreset(value) {
      return reasoningEffortPresets.includes(value || "") ? (value || "") : "custom";
    }

    function reasoningEffortCustomValue(value) {
      return reasoningEffortPresets.includes(value || "") ? "" : (value || "");
    }

    function card(item) {
      const signals = asList(item.signals).slice(0, 2).map((sig) =>
        `<div class="signal">${esc(sig.kind)} <span class="muted">p${esc(sig.priority || 0)}</span></div>`
      ).join("");
      return `<button class="card" data-key="${esc(item.project_key)}" title="Open project details. This does not change the DB.">
        <h2>${esc(item.name || "(unnamed)")}</h2>
        <div class="row">
          <span class="pill">${esc(item.project_key)}</span>
          <span class="pill">${esc(item.tracking_state || "")}</span>
          <span class="pill">${esc(item.service_status || "")}</span>
        </div>
        <div class="muted">${esc(item.seed_type || "")}: ${esc(item.seed_value || "")}</div>
        ${item.website_url ? `<a href="${esc(item.website_url)}" target="_blank" rel="noreferrer">${esc(item.website_url)}</a>` : ""}
        <div class="signals">${signals}</div>
      </button>`;
    }

    function mergeCard(item, index) {
      const reasonKeys = Object.keys(item.reasons || {}).slice(0, 3);
      return `<button class="card" data-merge-index="${index}" title="Open this merge plan. This does not change the DB.">
        <h2>${esc(item.source_name || item.source_project_key)} -> ${esc(item.target_name || item.target_project_key)}</h2>
        <div class="row">
          <span class="pill">${esc(item.source_project_key)}</span>
          <span class="pill">${esc(item.target_project_key)}</span>
          <span class="pill">score ${esc(item.recommendation?.score || 0)}</span>
        </div>
        <div class="muted">${esc(item.source_seed_type || "")}: ${esc(item.source_seed_value || "")}</div>
        <div class="muted">${esc(item.target_seed_type || "")}: ${esc(item.target_seed_value || "")}</div>
        <div class="signals">${reasonKeys.map((key) => `<div class="signal">${esc(key)}</div>`).join("")}</div>
      </button>`;
    }

    function listCount(label, shown, detail = "") {
      return `<div class="list-count"><span>${esc(label)}</span><span>${esc(shown)} shown${detail ? ` / ${esc(detail)}` : ""}</span></div>`;
    }

    async function loadList() {
      status("Loading...");
      $("list").innerHTML = "";
      try {
        let data;
        if (state.tab === "review") {
          data = await api(`/api/review-queue?limit=${encodeURIComponent(state.settings.reviewLimit || 50)}`);
          const items = asList(data.items);
          $("list").innerHTML = listCount("Review queue", data.review_count ?? items.length, `limit ${state.settings.reviewLimit || 50}`) + (items.map(card).join("") || `<div class="card muted">No review items.</div>`);
        } else if (state.tab === "promotion") {
          const personal = state.settings.includePersonalTools ? "&include_personal_tools=true" : "";
          data = await api(`/api/promotion-queue?limit=${encodeURIComponent(state.settings.promotionLimit || 50)}${personal}`);
          const items = asList(data.items);
          $("list").innerHTML = listCount("Promotion queue", data.promotion_count ?? items.length, `limit ${state.settings.promotionLimit || 50}`) + (items.map(card).join("") || `<div class="card muted">No promotion items.</div>`);
        } else if (state.tab === "merge") {
          const mode = encodeURIComponent($("merge-mode").value || state.settings.mergeMode || "moderate");
          data = await api(`/api/merge-batch?mode=${mode}&limit=${encodeURIComponent(state.settings.mergeLimit || 50)}`);
          state.mergeItems = asList(data.items);
          const detail = `${data.planned_merges || 0} planned, ${data.skipped_count || 0} skipped`;
          $("list").innerHTML = listCount("Merge plans", data.returned_merges ?? state.mergeItems.length, detail) + (state.mergeItems.map(mergeCard).join("") || `<div class="card muted">No merge plans.</div>`);
        } else {
          const q = encodeURIComponent($("query").value.trim());
          data = await api("/api/projects?limit=50" + (q ? "&q=" + q : ""));
          const items = asList(data.items);
          $("list").innerHTML = listCount("Projects", data.count ?? items.length, q ? "search result" : "limit 50") + (items.map(card).join("") || `<div class="card muted">No projects.</div>`);
        }
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    async function loadSettingsScreen() {
      status("Loading settings...");
      $("list").innerHTML = "";
      try {
        const server = await api("/api/settings");
        const settings = state.settings;
        $("detail").innerHTML = `
          ${detailBackButton()}
          <div><h2>Settings</h2><div class="muted">Saved in this browser. Server environment values are shown as defaults/reference.</div></div>
          <div class="section"><h3>Server</h3>
            <div class="muted">DB: ${esc(server.db_path || "")}</div>
            <div class="muted">Token configured: ${server.token_configured ? "yes" : "no"}</div>
          </div>
          <div class="section"><h3>Queues</h3>
            <label>Review limit <input id="setting-review-limit" type="number" min="1" max="500" value="${esc(settings.reviewLimit)}"></label>
            <label>Promotion limit <input id="setting-promotion-limit" type="number" min="1" max="500" value="${esc(settings.promotionLimit)}"></label>
            <label>Merge limit <input id="setting-merge-limit" type="number" min="1" max="500" value="${esc(settings.mergeLimit)}"></label>
            <label>Merge mode
              <select id="setting-merge-mode">
                ${["moderate", "strict", "broad"].map((mode) => `<option value="${mode}" ${mode === settings.mergeMode ? "selected" : ""}>${mode}</option>`).join("")}
              </select>
            </label>
            <label class="row"><input id="setting-personal-tools" type="checkbox" ${settings.includePersonalTools ? "checked" : ""}> Include personal tools in promotion queue</label>
          </div>
          <div class="section"><h3>LLM for Enrich</h3>
            <label>Endpoint <input id="setting-llm-endpoint" type="text" value="${esc(settings.llmEndpoint)}" placeholder="${esc(server.env.ATPROTO_SURVEY_LLM_ENDPOINT || "http://127.0.0.1:1234/v1")}"></label>
            <label>Model <input id="setting-llm-model" type="text" value="${esc(settings.llmModel)}" placeholder="${esc(server.env.ATPROTO_SURVEY_LLM_MODEL || "model name")}"></label>
            <label>API key <input id="setting-llm-api-key" type="password" value="${esc(settings.llmApiKey)}" placeholder="${server.env.ATPROTO_SURVEY_LLM_API_KEY_SET ? "(env value set)" : "optional"}"></label>
            <label>API style
              <select id="setting-llm-api-style">
                ${["auto", "chat", "responses", "completions"].map((style) => `<option value="${style}" ${style === settings.llmApiStyle ? "selected" : ""}>${style}</option>`).join("")}
              </select>
            </label>
            <label>Reasoning effort
              <select id="setting-llm-reasoning">
                ${reasoningEffortPresets.map((effort) => `<option value="${effort}" ${effort === reasoningEffortPreset(settings.llmReasoningEffort) ? "selected" : ""}>${effort || "(env/default)"}</option>`).join("")}
                <option value="custom" ${reasoningEffortPreset(settings.llmReasoningEffort) === "custom" ? "selected" : ""}>custom...</option>
              </select>
            </label>
            <label>Reasoning custom <input id="setting-llm-reasoning-custom" type="text" value="${esc(reasoningEffortCustomValue(settings.llmReasoningEffort))}" placeholder="on, off, medium, provider-specific value"></label>
            <div class="muted">API key is stored in this browser's localStorage if entered here.</div>
          </div>
          <div class="actions">
            <button id="save-settings" class="primary" title="Save these options to this browser only.">Save settings</button>
            <button id="reset-settings" class="danger" title="Clear browser settings and return to defaults/env values.">Reset</button>
          </div>
        `;
        $("list").innerHTML = `<div class="card"><h2>Current</h2><div class="muted">Review ${esc(settings.reviewLimit)}, Promotion ${esc(settings.promotionLimit)}, Merge ${esc(settings.mergeMode)} / ${esc(settings.mergeLimit)}</div><div class="muted">LLM model: ${esc(settings.llmModel || server.env.ATPROTO_SURVEY_LLM_MODEL || "(env/default)")}</div></div>`;
        wireSettings();
        wireDetailBackButton();
        openDetailPane();
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    async function loadSyncScreen() {
      status("Loading sync settings...");
      $("list").innerHTML = `<div class="card"><h2>Sync</h2><div class="muted">Dry-run is the default. Execute writes to SQLite only when explicitly checked.</div></div>`;
      try {
        const server = await api("/api/settings");
        const settings = state.settings;
        const defaultCollectionUrl = server.sync_defaults?.collection_inventory_url || "";
        const defaultGithubQuery = asList(server.sync_defaults?.github_queries)[0] || "";
        $("detail").innerHTML = `
          ${detailBackButton()}
          <div><h2>Sync</h2><div class="muted">Runs in a background job. Large syncs may take time and may use external APIs.</div></div>
          <div class="section"><h3>Command</h3>
            <label>Kind
              <select id="sync-kind">
                ${[
                  ["introduced", "sync-introduced"],
                  ["collections", "sync-collections"],
                  ["github-search", "sync-github-search"],
                  ["semble-collections", "sync-semble-collections"]
                ].map(([value, label]) => `<option value="${value}" ${value === settings.syncKind ? "selected" : ""}>${label}</option>`).join("")}
              </select>
            </label>
            <label class="row"><input id="sync-execute" type="checkbox"> Execute writes to SQLite</label>
            <label class="row"><input id="sync-create-unmatched" type="checkbox" ${settings.syncCreateUnmatched ? "checked" : ""}> Create unmatched projects</label>
            <label class="row"><input id="sync-screen-unmatched" type="checkbox" ${settings.syncScreenUnmatched ? "checked" : ""}> Screen unmatched GitHub repos</label>
            <label class="row"><input id="sync-all-history" type="checkbox" ${settings.syncAllHistory ? "checked" : ""}> All history</label>
          </div>
          <div class="section"><h3>Inputs</h3>
            <label>Introduced query <input id="sync-query" type="text" value="${esc(settings.syncQuery)}" placeholder="from:yamarten.bsky.social #ATプロトーク"></label>
            <label>Collection source URL <input id="sync-source-url" type="text" value="" placeholder="${esc(defaultCollectionUrl)}"></label>
            <label>GitHub query <input id="sync-github-query" type="text" value="" placeholder="${esc(defaultGithubQuery)}"></label>
            <div class="row">
              <label>Limit <input id="sync-limit" type="number" min="1" max="500" value="${esc(settings.syncLimit)}"></label>
              <label>Max pages <input id="sync-max-pages" type="number" min="1" max="20" value="${esc(settings.syncMaxPages)}"></label>
            </div>
            <div class="row">
              <label>Pages <input id="sync-pages" type="number" min="1" max="20" value="${esc(settings.syncPages)}"></label>
              <label>Per page <input id="sync-per-page" type="number" min="1" max="100" value="${esc(settings.syncPerPage)}"></label>
              <label>Lookback days <input id="sync-lookback-days" type="number" min="0" max="3650" value="${esc(settings.syncLookbackDays)}"></label>
            </div>
          </div>
          <div class="actions">
            <button id="run-sync" class="primary" title="Start a sync background job. Dry-run does not write to SQLite; execute does.">Run sync</button>
          </div>
          <div class="section"><h3>Result</h3><pre id="sync-result">No job yet.</pre></div>
        `;
        wireSync();
        wireDetailBackButton();
        openDetailPane();
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    function readSyncPayload() {
      const payload = {
        kind: $("sync-kind").value,
        execute: $("sync-execute").checked,
        create_unmatched: $("sync-create-unmatched").checked,
        screen_unmatched: $("sync-screen-unmatched").checked,
        all_history: $("sync-all-history").checked,
        query: $("sync-query").value.trim(),
        source_url: $("sync-source-url").value.trim(),
        github_query: $("sync-github-query").value.trim(),
        limit: numberSetting("sync-limit", 25),
        max_pages: numberSetting("sync-max-pages", 4),
        pages: numberSetting("sync-pages", 1),
        per_page: numberSetting("sync-per-page", 20),
        lookback_days: numberSetting("sync-lookback-days", 10)
      };
      saveSettings(Object.assign({}, state.settings, {
        syncKind: payload.kind,
        syncQuery: payload.query || state.settings.syncQuery,
        syncLimit: payload.limit,
        syncMaxPages: payload.max_pages,
        syncPages: payload.pages,
        syncPerPage: payload.per_page,
        syncLookbackDays: payload.lookback_days,
        syncCreateUnmatched: payload.create_unmatched,
        syncScreenUnmatched: payload.screen_unmatched,
        syncAllHistory: payload.all_history
      }));
      return payload;
    }

    function wireSync() {
      $("run-sync").onclick = async () => {
        const payload = readSyncPayload();
        const mode = payload.execute ? "EXECUTE and write to SQLite" : "dry-run without DB writes";
        if (!confirmAction(`Run ${payload.kind} sync?\n\nMode: ${mode}\n\nThis may call external APIs and run in the background.`)) return;
        status("Starting sync...");
        try {
          const data = await api("/api/sync", { method: "POST", body: JSON.stringify(payload) });
          $("sync-result").textContent = JSON.stringify(data, null, 2);
          status(`Started job ${data.job_id}`);
          pollJobInto(data.job_id, "sync-result");
        } catch (err) {
          status(err.message);
        }
      };
    }

    async function pollJobInto(jobId, elementId) {
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
          $(elementId).textContent = JSON.stringify(job, null, 2);
          if (job.status === "done" || job.status === "failed") {
            status(job.status === "done" ? "Job done." : `Job failed: ${job.error || ""}`);
            return;
          }
        } catch (err) {
          status(err.message);
          return;
        }
      }
    }

    function readSettingsForm() {
      const reasoningPreset = $("setting-llm-reasoning").value;
      const reasoningCustom = $("setting-llm-reasoning-custom").value.trim();
      return {
        reviewLimit: numberSetting("setting-review-limit", 50),
        promotionLimit: numberSetting("setting-promotion-limit", 50),
        mergeLimit: numberSetting("setting-merge-limit", 50),
        mergeMode: $("setting-merge-mode").value || "moderate",
        includePersonalTools: $("setting-personal-tools").checked,
        llmEndpoint: $("setting-llm-endpoint").value.trim(),
        llmModel: $("setting-llm-model").value.trim(),
        llmApiKey: $("setting-llm-api-key").value,
        llmApiStyle: $("setting-llm-api-style").value || "auto",
        llmReasoningEffort: reasoningPreset === "custom" ? reasoningCustom : (reasoningCustom || reasoningPreset)
      };
    }

    function numberSetting(id, fallback) {
      const value = Number($(id).value);
      if (!Number.isFinite(value)) return fallback;
      return Math.max(1, Math.min(500, Math.round(value)));
    }

    function wireSettings() {
      $("save-settings").onclick = () => {
        saveSettings(readSettingsForm());
        status("Settings saved.");
        loadSettingsScreen();
      };
      $("reset-settings").onclick = () => {
        if (confirmAction("Reset browser settings? This clears only localStorage settings, not SQLite.")) {
          localStorage.removeItem("projectRegistrySettings");
          state.settings = loadSettings();
          status("Settings reset.");
          loadSettingsScreen();
        }
      };
    }

    async function loadMergeDetail(index) {
      const item = state.mergeItems[index];
      if (!item) return;
      state.selected = null;
      status("Loading merge details...");
      let detail = {};
      try {
        detail = await api(`/api/merge-pair?source_project_key=${encodeURIComponent(item.source_project_key)}&target_project_key=${encodeURIComponent(item.target_project_key)}`);
        status("");
      } catch (err) {
        status(err.message);
      }
      const fullItem = Object.assign({}, item, detail);
      const comparison = esc(JSON.stringify(fullItem.comparison || {}, null, 2));
      const reasons = esc(JSON.stringify(item.reasons || {}, null, 2));
      $("detail").innerHTML = `
        ${detailBackButton()}
        <div>
          <h2>${esc(item.source_name || item.source_project_key)} -> ${esc(item.target_name || item.target_project_key)}</h2>
          <div class="row">
            <span class="pill">${esc(item.source_project_key)}</span>
            <span class="pill">${esc(item.target_project_key)}</span>
            <span class="pill">${esc($("merge-mode").value || "moderate")}</span>
          </div>
        </div>
        <div class="actions">
          <button id="merge-plan" class="primary" title="Merge source into target, save the merged target, and delete the source project.">Merge</button>
          <button id="block-plan" title="Mark the two projects as separate so future merge recommendations avoid this pair.">Block pair</button>
          <button id="ignore-source" class="danger" title="Set the source project tracking_state to ignore.">Ignore source</button>
          <button id="ignore-target" class="danger" title="Set the target project tracking_state to ignore.">Ignore target</button>
          <button id="skip-plan" title="Hide this plan in the current browser list only. This does not change the DB.">Skip</button>
        </div>
        <div class="section"><h3>Source</h3>${projectMini(fullItem.source)}<div class="actions"><button id="open-source-project" title="Open the source project detail view. This does not change the DB.">Open source</button></div></div>
        <div class="section"><h3>Target</h3>${projectMini(fullItem.target)}<div class="actions"><button id="open-target-project" title="Open the target project detail view. This does not change the DB.">Open target</button></div></div>
        <div class="section"><h3>Merged Preview</h3><div>${esc(fullItem.merged_preview?.merged_name || "")}</div><div class="muted">${esc(fullItem.merged_preview?.merged_website_url || "")}</div><div>${renderLinks(fullItem.merged_preview?.merged_source_code_urls || [])}</div></div>
        <div class="section"><h3>Reasons</h3><pre>${reasons}</pre></div>
        <div class="section"><h3>Comparison</h3><details open><summary>Show</summary><pre>${comparison}</pre></details></div>
      `;
      wireMergeDetail(item, index);
      wireDetailBackButton();
      openDetailPane();
    }

    function projectMini(project) {
      if (!project) return `<span class="muted">Missing</span>`;
      return `<div><b>${esc(project.name || "(unnamed)")}</b></div>
        <div class="row">
          <span class="pill">${esc(project.project_key || "")}</span>
          <span class="pill">${esc(project.tracking_state || "")}</span>
          <span class="pill">${esc(project.service_status || "")}</span>
        </div>
        <div class="muted">${esc(project.seed_type || "")}: ${esc(project.seed_value || "")}</div>
        ${project.website_url ? `<a href="${esc(project.website_url)}" target="_blank" rel="noreferrer">${esc(project.website_url)}</a>` : ""}`;
    }

    function wireMergeDetail(item, index) {
      const body = { source_project_key: item.source_project_key, target_project_key: item.target_project_key };
      $("merge-plan").onclick = () => {
        if (confirmAction(`Merge source into target?\n\nSource: ${item.source_project_key} ${item.source_name || ""}\nTarget: ${item.target_project_key} ${item.target_name || ""}\n\nThis writes the merged target and deletes the source project from SQLite.`)) {
          mergeMutate("merge", body);
        }
      };
      $("block-plan").onclick = () => {
        if (confirmAction(`Block this merge pair?\n\n${item.source_project_key} and ${item.target_project_key}\n\nThis records separate_project_keys on both projects.`)) {
          mergeMutate("block", body);
        }
      };
      $("ignore-source").onclick = () => {
        if (confirmAction(`Set source project to ignore?\n\n${item.source_project_key} ${item.source_name || ""}\n\nThis writes tracking_state=ignore.`)) {
          mergeMutate("ignore_source", body);
        }
      };
      $("ignore-target").onclick = () => {
        if (confirmAction(`Set target project to ignore?\n\n${item.target_project_key} ${item.target_name || ""}\n\nThis writes tracking_state=ignore.`)) {
          mergeMutate("ignore_target", body);
        }
      };
      $("skip-plan").onclick = () => {
        state.mergeItems.splice(index, 1);
        $("list").innerHTML = state.mergeItems.map(mergeCard).join("") || `<div class="card muted">No merge plans.</div>`;
        $("detail").innerHTML = `<p class="muted">Skipped locally. No DB change.</p>`;
      };
      $("open-source-project").onclick = () => loadDetail(item.source_project_key);
      $("open-target-project").onclick = () => loadDetail(item.target_project_key);
    }

    async function mergeMutate(action, payload) {
      status("Saving...");
      try {
        const data = await api("/api/merge-batch/action", { method: "POST", body: JSON.stringify(Object.assign({ action }, payload)) });
        status(data.mode || "Saved.");
        await loadList();
        $("detail").innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
      } catch (err) {
        status(err.message);
      }
    }

    function renderLinks(values) {
      return uniqueValues(values).map((url) => `<div><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></div>`).join("") || `<span class="muted">None</span>`;
    }

    function renderIdentifierValue(value, type) {
      if (type === "nsid") return `<span class="identifier-value">${esc(value)}</span>`;
      return `<span class="identifier-value"><a href="${esc(value)}" target="_blank" rel="noreferrer">${esc(value)}</a></span>`;
    }

    function renderEditableIdentifierRows(values, type) {
      const rows = uniqueValues(values);
      if (!rows.length) return `<span class="muted">None</span>`;
      return rows.map((value) => `
        <div class="row identifier-row identifier-confirmed">
          ${renderIdentifierValue(value, type)}
          <button data-split-type="${esc(type)}" data-split-value="${esc(value)}" title="Move this identifier into a new project and block it from reattaching here.">Split</button>
          <button class="danger" data-block-type="${esc(type)}" data-block-value="${esc(value)}" title="Remove this identifier from this project and block it from reattaching.">Block</button>
        </div>
      `).join("");
    }

    function renderCandidateRows(values, type) {
      return uniqueValues(values).map((url) => `
        <div class="row identifier-row identifier-candidate">
          <span class="identifier-value"><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></span>
          <button data-confirm-type="${esc(type)}" data-confirm-value="${esc(url)}" title="Move this URL from candidate evidence into confirmed/manual project data.">Confirm</button>
          <button class="danger" data-reject-type="${esc(type)}" data-reject-value="${esc(url)}" title="Block this URL from being reattached to this project.">Reject</button>
        </div>
      `).join("");
    }

    async function loadDetail(key) {
      state.selected = key;
      status("Loading project...");
      try {
        const data = await api("/api/projects/" + encodeURIComponent(key));
        const p = data.project;
        const review = data.review_item || {};
        const raw = esc(JSON.stringify(p, null, 2));
        $("detail").innerHTML = `
          ${detailBackButton()}
          <div>
            <h2>${esc(p.name || "(unnamed)")}</h2>
            <div class="row">
              <span class="pill">${esc(p.project_key)}</span>
              <span class="pill">${esc(p.tracking_state || "")}</span>
              <span class="pill">${esc(p.status || "")}</span>
              <span class="pill">${esc(p.service_status || "")}</span>
            </div>
          </div>
          <div class="actions">
            <select id="tracking-state">
              ${["active", "watchlist", "stable", "stalled", "archived", "ignore"].map((s) => `<option value="${s}" ${s === p.tracking_state ? "selected" : ""}>${s}</option>`).join("")}
            </select>
            <button id="save-state" title="Write the selected tracking_state to SQLite for this project.">Set state</button>
            <button id="enrich" title="Start background enrich for this project. This may update SQLite after external checks finish.">Enrich</button>
            <button id="deep-enrich" title="Start background deep enrich for this project. This may make more external checks and update SQLite.">Deep</button>
          </div>
          <div class="section"><h3>Summary</h3><div>${esc(p.tagline || "")}</div><div class="muted">${esc(p.summary || "")}</div></div>
          <div class="section"><h3>Signals</h3><div class="signals">${asList(review.signals).map((sig) => `<div class="signal"><b>${esc(sig.kind)}</b> p${esc(sig.priority || 0)} <button data-dismiss="${esc(sig.kind)}" title="Store this review signal kind as dismissed for this project.">Dismiss</button><pre>${esc(JSON.stringify(sig.details || {}, null, 2))}</pre></div>`).join("") || `<span class="muted">None</span>`}</div></div>
          <div class="section"><h3>Identity</h3><div>${esc(p.seed_type || "")}: ${esc(p.seed_value || "")}</div>${renderEditableIdentifierRows(asList(p.input_nsids).concat(asList(p.resolved_nsids)), "nsid")}</div>
          <div class="section"><h3>Website / Service</h3>${renderEditableIdentifierRows([p.website_url].filter(Boolean).concat(asList(p.service_urls)), "service_url")}<div class="identifier-list">${renderCandidateRows(data.service_candidates, "service_url")}</div></div>
          <div class="section"><h3>Repositories</h3>${renderEditableIdentifierRows(asList(p.source_code_urls).concat(asList(p.github_repo_urls)).concat(asList(p.tangled_repo_urls)), "repo_url")}<div class="identifier-list">${renderCandidateRows(data.repo_candidates, "repo_url")}</div></div>
          <div class="section"><h3>Enrich Job</h3><pre id="enrich-job-status">No enrich job running.</pre></div>
          <div class="section"><h3>Add Seed</h3><div class="row"><input id="seed-value" type="text" placeholder="NSID, repo URL, service URL" title="Seed to attach to this existing project."><button id="add-seed" title="Attach this seed to the current project and write it to SQLite.">Add</button></div></div>
          <div class="section"><h3>Project Edits</h3>
            <div class="row">
              <select id="edit-identifier-type" title="Identifier type to edit.">
                <option value="nsid">nsid</option>
                <option value="repo_url">repo_url</option>
                <option value="service_url">service_url</option>
              </select>
              <input id="edit-identifier-value" type="text" placeholder="Identifier value" title="Existing identifier value for split/block.">
            </div>
            <input id="edit-note" type="text" placeholder="Optional note" title="Optional note stored with the edit.">
            <div class="actions">
              <button id="split-project" title="Move this identifier into a new project, remove it from the current project, and block it from reattaching.">Split</button>
              <button id="block-identifier" class="danger" title="Block this identifier on the current project and remove matching stored values.">Block identifier</button>
            </div>
          </div>
          <div class="section"><h3>Raw JSON</h3><details><summary>Show</summary><pre>${raw}</pre></details></div>
        `;
        wireDetail(p.project_key);
        wireDetailBackButton();
        openDetailPane();
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    function wireDetail(key) {
      $("save-state").onclick = () => {
        const nextState = $("tracking-state").value;
        if (confirmAction(`Set tracking_state for ${key} to ${nextState}? This writes to SQLite.`)) {
          mutate(`/api/projects/${encodeURIComponent(key)}/tracking-state`, { tracking_state: nextState });
        }
      };
      $("enrich").onclick = () => {
        if (confirmAction(`Start enrich for ${key}? This runs background checks and may update SQLite.`)) {
          startEnrichJob(key, false);
        }
      };
      $("deep-enrich").onclick = () => {
        if (confirmAction(`Start deep enrich for ${key}? This may run extra external checks and update SQLite.`)) {
          startEnrichJob(key, true);
        }
      };
      $("add-seed").onclick = () => {
        const seed = $("seed-value").value.trim();
        if (seed && confirmAction(`Attach seed to ${key}?\n\n${seed}\n\nThis writes to SQLite.`)) {
          mutate("/api/seeds", { seed, project_key: key });
        }
      };
      $("split-project").onclick = () => {
        const identifierType = $("edit-identifier-type").value;
        const value = $("edit-identifier-value").value.trim();
        const note = $("edit-note").value.trim();
        if (value && confirmAction(`Split this identifier out of ${key}?\n\n${identifierType}: ${value}\n\nThis creates a new project, removes the identifier from the current project, and records a separation/block.`)) {
          mutate(`/api/projects/${encodeURIComponent(key)}/split-project`, { identifier_type: identifierType, value, note });
        }
      };
      $("block-identifier").onclick = () => {
        const identifierType = $("edit-identifier-type").value;
        const value = $("edit-identifier-value").value.trim();
        const note = $("edit-note").value.trim();
        if (value && confirmAction(`Block this identifier on ${key}?\n\n${identifierType}: ${value}\n\nThis removes matching values from the project and records a blocked identifier.`)) {
          mutate(`/api/projects/${encodeURIComponent(key)}/block-identifier`, { identifier_type: identifierType, value, note });
        }
      };
      document.querySelectorAll("[data-dismiss]").forEach((el) => {
        el.onclick = () => {
          if (confirmAction(`Dismiss review signal for ${key}?\n\n${el.dataset.dismiss}\n\nThis stores the signal kind in dismissed_review_kinds.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/dismiss-review-kind`, { kind: el.dataset.dismiss });
          }
        };
      });
      document.querySelectorAll("[data-confirm-type]").forEach((el) => {
        el.onclick = () => {
          if (confirmAction(`Confirm this ${el.dataset.confirmType} for ${key}?\n\n${el.dataset.confirmValue}\n\nThis moves it into confirmed/manual project data.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/confirm-candidate`, { identifier_type: el.dataset.confirmType, value: el.dataset.confirmValue });
          }
        };
      });
      document.querySelectorAll("[data-reject-type]").forEach((el) => {
        el.onclick = () => {
          if (confirmAction(`Reject this ${el.dataset.rejectType} for ${key}?\n\n${el.dataset.rejectValue}\n\nThis records a blocked identifier so it should not be reattached automatically.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/reject-candidate`, { identifier_type: el.dataset.rejectType, value: el.dataset.rejectValue });
          }
        };
      });
      document.querySelectorAll("[data-split-type]").forEach((el) => {
        el.onclick = () => {
          if (confirmAction(`Split this identifier out of ${key}?\n\n${el.dataset.splitType}: ${el.dataset.splitValue}\n\nThis creates a new project, removes the identifier from the current project, and records a separation/block.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/split-project`, { identifier_type: el.dataset.splitType, value: el.dataset.splitValue });
          }
        };
      });
      document.querySelectorAll("[data-block-type]").forEach((el) => {
        el.onclick = () => {
          if (confirmAction(`Block this identifier on ${key}?\n\n${el.dataset.blockType}: ${el.dataset.blockValue}\n\nThis removes matching values from the project and records a blocked identifier.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/block-identifier`, { identifier_type: el.dataset.blockType, value: el.dataset.blockValue });
          }
        };
      });
    }

    function confirmAction(message) {
      return window.confirm(message);
    }

    function enrichPayload(deep) {
      const settings = state.settings;
      return {
        deep_investigation: deep,
        llm_endpoint: settings.llmEndpoint || "",
        llm_model: settings.llmModel || "",
        llm_api_key: settings.llmApiKey || "",
        llm_api_style: settings.llmApiStyle || "",
        llm_reasoning_effort: settings.llmReasoningEffort || ""
      };
    }

    async function startEnrichJob(key, deep) {
      status("Starting enrich...");
      const target = $("enrich-job-status");
      if (target) target.textContent = "Starting enrich job...";
      try {
        const data = await api(`/api/projects/${encodeURIComponent(key)}/enrich`, { method: "POST", body: JSON.stringify(enrichPayload(deep)) });
        status(`Started job ${data.job_id}`);
        if (target) target.textContent = JSON.stringify(data, null, 2);
        pollEnrichJob(key, data.job_id);
      } catch (err) {
        status(err.message);
        if (target) target.textContent = err.message;
      }
    }

    async function pollEnrichJob(key, jobId) {
      const target = $("enrich-job-status");
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
          if (target) target.textContent = JSON.stringify(job, null, 2);
          if (job.status === "done") {
            status("Enrich done.");
            if (state.selected === key) await loadDetail(key);
            return;
          }
          if (job.status === "failed") {
            status(`Enrich failed: ${job.error || ""}`);
            return;
          }
        } catch (err) {
          status(err.message);
          if (target) target.textContent = err.message;
          return;
        }
      }
    }

    async function mutate(path, payload, reloadDetail = true) {
      status("Saving...");
      try {
        const data = await api(path, { method: "POST", body: JSON.stringify(payload || {}) });
        status(data.job_id ? `Started job ${data.job_id}` : "Saved.");
        await loadList();
        if (reloadDetail && state.selected) await loadDetail(state.selected);
      } catch (err) {
        status(err.message);
      }
    }

    $("list").onclick = (event) => {
      const button = event.target.closest("[data-key]");
      if (button) loadDetail(button.dataset.key);
      const mergeButton = event.target.closest("[data-merge-index]");
      if (mergeButton) loadMergeDetail(Number(mergeButton.dataset.mergeIndex));
    };
    $("tab-review").onclick = () => setTab("review");
    $("tab-promotion").onclick = () => setTab("promotion");
    $("tab-merge").onclick = () => setTab("merge");
    $("tab-sync").onclick = () => setTab("sync");
    $("tab-search").onclick = () => setTab("search");
    $("tab-settings").onclick = () => setTab("settings");
    $("refresh").onclick = loadList;
    $("merge-mode").value = state.settings.mergeMode || "moderate";
    $("merge-mode").onchange = () => {
      saveSettings(Object.assign({}, state.settings, { mergeMode: $("merge-mode").value || "moderate" }));
      if (state.tab === "merge") loadList();
    };
    $("query").onkeydown = (event) => { if (event.key === "Enter") loadList(); };
    $("query").style.display = "none";
    $("merge-mode").style.display = "none";
    loadList();
  </script>
</body>
</html>
"""


def configure_registry_db(db_path: str | None) -> None:
    if not db_path:
        return
    registry.DB_PATH = Path(db_path).expanduser().resolve()


def open_registry_db() -> sqlite3.Connection:
    registry.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return registry.open_db()


def parse_int(value: str | None, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    try:
        parsed = int(value or default)
    except ValueError:
        parsed = default
    return max(minimum, min(maximum, parsed))


def parse_state_set(values: list[str], default: set[str]) -> set[str]:
    if not values:
        return set(default)
    states: set[str] = set()
    for value in values:
        for item in value.replace(",", " ").split():
            normalized = registry.normalize_tracking_state(item)
            if normalized in registry.TRACKING_STATES:
                states.add(normalized)
    return states or set(default)


def project_summary(project: dict[str, Any]) -> dict[str, Any]:
    return {
        "project_key": project.get("project_key", ""),
        "name": project.get("name", ""),
        "tracking_state": project.get("tracking_state", "active"),
        "status": project.get("status", ""),
        "service_status": project.get("service_status", ""),
        "seed_type": project.get("seed_type", ""),
        "seed_value": project.get("seed_value", ""),
        "website_url": project.get("website_url", ""),
        "tagline": project.get("tagline", ""),
        "summary": project.get("summary", ""),
        "last_seen_at": project.get("last_seen_at", ""),
        "last_enriched_at": project.get("last_enriched_at", ""),
    }


def project_detail_payload(conn: sqlite3.Connection, project_key: str) -> dict[str, Any]:
    resolved_key = registry.resolve_project_key_input(conn, project_key)
    project = registry.fetch_project(conn, resolved_key)
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    duplicates = registry.compute_duplicate_candidates(conn, project)
    project["duplicate_candidates"] = duplicates
    project["merge_recommendation"] = registry.compute_merge_recommendation(dict(project, duplicate_candidates=duplicates))
    project = registry.apply_promotion_state(project)
    return {
        "project": project,
        "review_item": registry.build_review_queue_item(conn, project["project_key"]),
        "service_candidates": registry.project_service_hint_urls(project),
        "repo_candidates": pending_repo_candidate_urls(project),
    }


def pending_repo_candidate_urls(project: dict[str, Any]) -> list[str]:
    confirmed = {
        registry.canonical_repo_url(str(item or "")) or registry.normalize_url(str(item or ""))
        for item in list(project.get("source_code_urls", []))
        + list(project.get("github_repo_urls", []))
        + list(project.get("tangled_repo_urls", []))
        if (registry.canonical_repo_url(str(item or "")) or registry.normalize_url(str(item or "")))
    }
    for item in project.get("manual_seeds", []):
        if not isinstance(item, dict) or str(item.get("type") or "") != "repo_url":
            continue
        normalized = registry.canonical_repo_url(str(item.get("value") or "")) or registry.normalize_url(str(item.get("value") or ""))
        if normalized:
            confirmed.add(normalized)
    candidates = registry.normalize_repo_collection(project.get("candidate_source_code_urls", []))
    return registry.unique_list([url for url in candidates if url and url not in confirmed])


def block_identifier_on_project(
    conn: sqlite3.Connection,
    project_key: str,
    identifier_type: str,
    value: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    if identifier_type not in {"service_url", "repo_url", "nsid"}:
        raise ValueError("identifier_type must be service_url, repo_url, or nsid")
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    normalized_value = registry.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized_value:
        raise ValueError("identifier value could not be normalized")

    blocked = registry.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
    key = (identifier_type, normalized_value)
    if not any((str(item.get("type") or ""), str(item.get("value") or "")) == key for item in blocked):
        blocked.append(
            {
                "type": identifier_type,
                "value": normalized_value,
                "note": note or "blocked from web ui",
                "added_at": registry.iso_now(),
            }
        )
    project["blocked_identifiers"] = blocked
    registry.remove_identifier_from_project(project, identifier_type, normalized_value)
    project = registry.finalize_project_shape(project)
    project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = registry.compute_merge_recommendation(project)
    project = registry.apply_promotion_state(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def split_project_identifier(
    conn: sqlite3.Connection,
    project_key: str,
    identifier_type: str,
    value: str,
    *,
    note: str = "",
    new_project_key: str = "",
) -> dict[str, Any]:
    if identifier_type not in {"service_url", "repo_url", "nsid"}:
        raise ValueError("identifier_type must be service_url, repo_url, or nsid")
    source = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if source is None:
        raise KeyError(project_key)
    source = registry.ensure_project_shape(source)
    identifier_value = registry.canonical_seed_value(identifier_type, value)
    if not identifier_value:
        raise ValueError("identifier value is required")
    if registry.find_project_key_by_identifier(conn, identifier_type, identifier_value) != source["project_key"]:
        raise ValueError("identifier does not currently belong to the selected source project")

    if new_project_key:
        target_key = registry.resolve_project_key_input(conn, new_project_key)
    else:
        target_key = registry.generate_project_key({project["project_key"] for project in registry.all_projects(conn)})
    if target_key == source["project_key"]:
        raise ValueError("new project key resolves to the same source project")
    if registry.fetch_project(conn, target_key) is not None:
        raise ValueError(f"target project already exists: {target_key}")

    target = registry.default_project_record(target_key, identifier_type, identifier_value)
    target["tracking_state"] = source.get("tracking_state", "active")
    target["status"] = source.get("status", "draft")
    target["discovery_sources"] = registry.unique_list(list(source.get("discovery_sources", [])) + ["split_from_project"])
    target["tracking_note"] = f"split from {source['project_key']}"
    registry.add_manual_seed(target, identifier_type, identifier_value, source="split")
    target = registry.finalize_project_shape(target)

    blocked = registry.normalize_blocked_identifiers(list(source.get("blocked_identifiers", [])))
    block_key = (identifier_type, identifier_value)
    if not any((str(item.get("type") or ""), str(item.get("value") or "")) == block_key for item in blocked):
        blocked.append(
            {
                "type": identifier_type,
                "value": identifier_value,
                "note": note or "split out to another project",
                "added_at": registry.iso_now(),
            }
        )
    source["blocked_identifiers"] = blocked
    registry.remove_identifier_from_project(source, identifier_type, identifier_value)
    source["separate_project_keys"] = registry.unique_list(list(source.get("separate_project_keys", [])) + [target["project_key"]])
    target["separate_project_keys"] = registry.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])

    note_line = f"split out {identifier_type}:{identifier_value} -> {target['project_key']}"
    if note:
        note_line = f"{note_line} ({note})"
    existing_notes = str(source.get("notes") or "").strip()
    if note_line not in existing_notes.splitlines():
        source["notes"] = (existing_notes + ("\n" if existing_notes else "") + note_line).strip()

    source = registry.finalize_project_shape(source)
    source["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, source)
    source["merge_recommendation"] = registry.compute_merge_recommendation(source)
    target["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, target)
    target["merge_recommendation"] = registry.compute_merge_recommendation(target)

    registry.save_project(conn, source, enriched=bool(source.get("last_enriched_at")))
    registry.save_project(conn, target, enriched=False)
    conn.commit()

    return {
        "mode": "split",
        "source_project_key": source["project_key"],
        "target_project_key": target["project_key"],
        "identifier_type": identifier_type,
        "identifier_value": identifier_value,
        "source": project_summary(source),
        "target": project_summary(target),
        "source_blocked_identifiers": source.get("blocked_identifiers", []),
        "source_separate_project_keys": source.get("separate_project_keys", []),
        "target_separate_project_keys": target.get("separate_project_keys", []),
    }


def reject_candidate(conn: sqlite3.Connection, project_key: str, identifier_type: str, value: str, note: str = "") -> dict[str, Any]:
    if identifier_type not in {"service_url", "repo_url", "nsid"}:
        raise ValueError("identifier_type must be service_url, repo_url, or nsid")
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    normalized_value = registry.normalize_blocked_identifier_value(identifier_type, value)
    if not normalized_value:
        raise ValueError("identifier value could not be normalized")
    blocked = registry.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
    key = (identifier_type, normalized_value)
    if not any((str(item.get("type") or ""), str(item.get("value") or "")) == key for item in blocked):
        blocked.append(
            {
                "type": identifier_type,
                "value": normalized_value,
                "note": note or "rejected from web ui",
                "added_at": registry.iso_now(),
            }
        )
    project["blocked_identifiers"] = blocked
    project = registry.finalize_project_shape(project)
    project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = registry.compute_merge_recommendation(project)
    project = registry.apply_promotion_state(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def merge_batch_plan_payload(
    conn: sqlite3.Connection,
    *,
    mode: str,
    limit: int,
    bootstrap_collection_creator_heuristic: bool = False,
) -> dict[str, Any]:
    if mode not in MERGE_BATCH_MODES:
        raise ValueError("mode must be strict, moderate, or broad")
    plans, skipped = registry.build_batch_merge_plan(
        conn,
        mode=mode,
        bootstrap_collection_creator_heuristic=bootstrap_collection_creator_heuristic,
    )
    limited_plans = plans[:limit]
    items = [merge_batch_plan_item(conn, plan, include_details=False) for plan in limited_plans]
    return {
        "mode": "dry_run",
        "merge_mode": mode,
        "bootstrap_collection_creator_heuristic": bootstrap_collection_creator_heuristic,
        "planned_merges": len(plans),
        "returned_merges": len(items),
        "skipped_count": len(skipped),
        "skipped": [],
        "items": items,
    }


def merge_batch_plan_item(conn: sqlite3.Connection, plan: dict[str, Any], *, include_details: bool = True) -> dict[str, Any]:
    source = registry.fetch_project(conn, str(plan.get("source_project_key") or ""))
    target = registry.fetch_project(conn, str(plan.get("target_project_key") or ""))
    if source is None or target is None:
        return dict(plan, source=None, target=None, comparison={}, merged_preview={})
    source = registry.ensure_project_shape(source)
    target = registry.ensure_project_shape(target)
    payload = {
        **plan,
        "source_seed_type": source.get("seed_type", ""),
        "source_seed_value": source.get("seed_value", ""),
        "target_seed_type": target.get("seed_type", ""),
        "target_seed_value": target.get("seed_value", ""),
        "source": project_summary(source),
        "target": project_summary(target),
    }
    if include_details:
        merged = registry.merge_project_records(target, source)
        payload["comparison"] = registry.build_project_comparison_payload(source, target)
        payload["merged_preview"] = {
            "merged_name": merged.get("name", ""),
            "merged_website_url": merged.get("website_url", ""),
            "merged_service_status": merged.get("service_status", ""),
            "merged_source_code_urls": merged.get("source_code_urls", []),
            "merged_tagline": merged.get("tagline", ""),
        }
    return payload


def merge_pair_detail_payload(conn: sqlite3.Connection, source_project_key: str, target_project_key: str) -> dict[str, Any]:
    source_key = registry.resolve_project_key_input(conn, source_project_key)
    target_key = registry.resolve_project_key_input(conn, target_project_key)
    return merge_batch_plan_item(
        conn,
        {
            "source_project_key": source_key,
            "target_project_key": target_key,
            "source_name": "",
            "target_name": "",
            "reasons": {},
            "recommendation": {},
        },
        include_details=True,
    )


def merge_batch_action(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "").strip()
    if action not in {"merge", "block", "ignore_source", "ignore_target"}:
        raise ValueError("action must be merge, block, ignore_source, or ignore_target")
    source_key = registry.resolve_project_key_input(conn, str(payload.get("source_project_key") or ""))
    target_key = registry.resolve_project_key_input(conn, str(payload.get("target_project_key") or ""))
    if source_key == target_key:
        raise ValueError("source and target resolve to the same project")
    source = registry.fetch_project(conn, source_key)
    target = registry.fetch_project(conn, target_key)
    if source is None or target is None:
        raise KeyError(source_key if source is None else target_key)
    source = registry.ensure_project_shape(source)
    target = registry.ensure_project_shape(target)

    if action == "merge":
        if registry.merge_is_blocked(source, target):
            raise ValueError("merge is manually blocked for this project pair")
        merged = registry.merge_project_records(target, source)
        merged["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, merged)
        merged["merge_recommendation"] = registry.compute_merge_recommendation(merged)
        merged = registry.apply_promotion_state(merged)
        registry.save_project(conn, merged, enriched=bool(merged.get("last_enriched_at")))
        registry.delete_project(conn, source["project_key"])
        conn.commit()
        return {
            "mode": "merged",
            "source_project_key": source["project_key"],
            "target_project_key": target["project_key"],
            "source_name": source.get("name", ""),
            "target_name": target.get("name", ""),
            "merged": project_summary(merged),
        }

    if action == "block":
        source["separate_project_keys"] = registry.unique_list(list(source.get("separate_project_keys", [])) + [target["project_key"]])
        target["separate_project_keys"] = registry.unique_list(list(target.get("separate_project_keys", [])) + [source["project_key"]])
        source["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, source)
        source["merge_recommendation"] = registry.compute_merge_recommendation(source)
        target["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, target)
        target["merge_recommendation"] = registry.compute_merge_recommendation(target)
        registry.save_project(conn, source, enriched=bool(source.get("last_enriched_at")))
        registry.save_project(conn, target, enriched=bool(target.get("last_enriched_at")))
        conn.commit()
        return {
            "mode": "blocked",
            "source_project_key": source["project_key"],
            "target_project_key": target["project_key"],
        }

    ignored = source if action == "ignore_source" else target
    other = target if action == "ignore_source" else source
    ignored["tracking_state"] = "ignore"
    if not str(ignored.get("tracking_note") or "").strip():
        ignored["tracking_note"] = f"ignored during web merge review against {other['project_key']}"
    registry.save_project(conn, ignored, enriched=bool(ignored.get("last_enriched_at")))
    conn.commit()
    return {
        "mode": action,
        "ignored_project_key": ignored["project_key"],
        "other_project_key": other["project_key"],
        "ignored": project_summary(ignored),
    }


def public_settings_payload(token_configured: bool) -> dict[str, Any]:
    config = registry.load_config()
    github_cfg = config.get("github_repo_search", {}) if isinstance(config.get("github_repo_search", {}), dict) else {}
    return {
        "db_path": str(registry.DB_PATH),
        "token_configured": token_configured,
        "sync_defaults": {
            "introduced_query": "from:yamarten.bsky.social #ATプロトーク",
            "collection_inventory_url": str(config.get("collection_inventory", {}).get("url") or "")
            if isinstance(config.get("collection_inventory", {}), dict) else "",
            "github_queries": registry.clean_string_list(github_cfg.get("queries") or [], limit=12),
            "github_pages": int(github_cfg.get("pages") or 4),
            "github_per_page": int(github_cfg.get("per_page") or 50),
            "semble_pages": 1,
            "semble_per_page": 20,
        },
        "env": {
            "ATPROTO_SURVEY_LLM_ENDPOINT": os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "").strip(),
            "ATPROTO_SURVEY_LLM_MODEL": os.getenv("ATPROTO_SURVEY_LLM_MODEL", "").strip(),
            "ATPROTO_SURVEY_LLM_API_STYLE": os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto").strip() or "auto",
            "ATPROTO_SURVEY_LLM_REASONING_EFFORT": os.getenv("ATPROTO_SURVEY_LLM_REASONING_EFFORT", "").strip(),
            "ATPROTO_SURVEY_LLM_COMMAND_SET": bool(os.getenv("ATPROTO_SURVEY_LLM_COMMAND", "").strip()),
            "ATPROTO_SURVEY_LLM_API_KEY_SET": bool(os.getenv("ATPROTO_SURVEY_LLM_API_KEY", "").strip()),
            "ATPROTO_SURVEY_LLM_TIMEOUT": os.getenv("ATPROTO_SURVEY_LLM_TIMEOUT", "300").strip() or "300",
        },
    }


def llm_options_from_payload(payload: dict[str, Any]) -> dict[str, str | None]:
    def optional_string(key: str) -> str | None:
        value = str(payload.get(key) or "").strip()
        return value or None

    return {
        "llm_endpoint": optional_string("llm_endpoint"),
        "llm_model": optional_string("llm_model"),
        "llm_api_key": optional_string("llm_api_key"),
        "llm_api_style": optional_string("llm_api_style"),
        "llm_reasoning_effort": optional_string("llm_reasoning_effort"),
    }


def sync_payload_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("kind") or "").strip()
    if kind not in SYNC_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(sorted(SYNC_KINDS))}")
    return {
        "kind": kind,
        "execute": bool(payload.get("execute")),
        "create_unmatched": bool(payload.get("create_unmatched")),
        "screen_unmatched": bool(payload.get("screen_unmatched")),
        "all_history": bool(payload.get("all_history")),
        "query": str(payload.get("query") or "").strip(),
        "source_url": str(payload.get("source_url") or "").strip(),
        "github_query": str(payload.get("github_query") or "").strip(),
        "limit": max(1, min(500, int(payload.get("limit") or 25))),
        "max_pages": max(1, min(20, int(payload.get("max_pages") or 4))),
        "pages": max(1, min(20, int(payload.get("pages") or 1))),
        "per_page": max(1, min(100, int(payload.get("per_page") or 20))),
        "lookback_days": max(0, min(3650, int(payload.get("lookback_days") or 10))),
    }


def run_sync_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload["kind"])
    config = registry.load_config()
    if kind == "introduced":
        return registry.sync_introduced_entries(
            conn,
            query=str(payload.get("query") or "from:yamarten.bsky.social #ATプロトーク"),
            execute=bool(payload.get("execute")),
            limit=int(payload.get("limit") or 25),
            max_pages=int(payload.get("max_pages") or 4),
        )
    if kind == "collections":
        collection_cfg = config.get("collection_inventory", {}) if isinstance(config.get("collection_inventory", {}), dict) else {}
        source_url = str(payload.get("source_url") or collection_cfg.get("url") or "").strip()
        if not source_url:
            raise ValueError("collection inventory source URL is not configured")
        return registry.sync_collection_inventory(
            conn,
            source_url=source_url,
            execute=bool(payload.get("execute")),
            create_unmatched=bool(payload.get("create_unmatched")),
            lookback_days=int(payload.get("lookback_days") or 10),
        )
    if kind == "github-search":
        github_cfg = config.get("github_repo_search", {}) if isinstance(config.get("github_repo_search", {}), dict) else {}
        query = str(payload.get("github_query") or "").strip()
        queries = registry.clean_string_list([query] if query else github_cfg.get("queries") or [], limit=12)
        if not queries:
            raise ValueError("github repo search queries are not configured")
        return registry.sync_github_search(
            conn,
            queries=queries,
            execute=bool(payload.get("execute")),
            create_unmatched=bool(payload.get("create_unmatched")),
            pages=int(payload.get("pages") or github_cfg.get("pages") or 4),
            per_page=int(payload.get("per_page") or github_cfg.get("per_page") or 50),
            lookback_days=None if payload.get("all_history") else int(payload.get("lookback_days") or 10),
            screen_unmatched=bool(payload.get("screen_unmatched")),
        )
    if kind == "semble-collections":
        return registry.sync_semble_collections(
            conn,
            execute=bool(payload.get("execute")),
            create_unmatched=bool(payload.get("create_unmatched")),
            pages=int(payload.get("pages") or 1),
            per_page=int(payload.get("per_page") or 20),
            lookback_days=None if payload.get("all_history") else int(payload.get("lookback_days") or 14),
        )
    raise ValueError(f"unsupported sync kind: {kind}")


class ProjectRegistryWebServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], token: str):
        super().__init__(server_address, ProjectRegistryHandler)
        self.token = token
        self.jobs: dict[str, dict[str, Any]] = {}
        self.jobs_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="registry-web")

    def start_enrich_job(self, project_key: str, *, deep_investigation: bool, llm_options: dict[str, str | None] | None = None) -> str:
        job_id = uuid.uuid4().hex
        llm_options = dict(llm_options or {})
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "enrich",
                "project_key": project_key,
                "deep_investigation": deep_investigation,
                "llm_options": {key: ("***" if key == "llm_api_key" and value else value) for key, value in llm_options.items()},
                "created_at": registry.iso_now(),
            }
        self.executor.submit(self._run_enrich_job, job_id, project_key, deep_investigation, llm_options)
        return job_id

    def _run_enrich_job(self, job_id: str, project_key: str, deep_investigation: bool, llm_options: dict[str, str | None] | None = None) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        try:
            conn = open_registry_db()
            try:
                resolved_key = registry.resolve_project_key_input(conn, project_key)
                project = registry.fetch_project(conn, resolved_key)
                if project is None:
                    raise KeyError(project_key)
                enriched = registry.enrich_project(
                    conn,
                    project,
                    deep_investigation=deep_investigation,
                    **dict(llm_options or {}),
                )
                registry.save_project(conn, enriched, enriched=True)
                conn.commit()
                result = project_summary(enriched)
            finally:
                conn.close()
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "finished_at": registry.iso_now(),
                        "result": result,
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised via HTTP behavior
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "finished_at": registry.iso_now(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    }
                )

    def start_sync_job(self, payload: dict[str, Any]) -> str:
        sync_payload = sync_payload_from_request(payload)
        job_id = uuid.uuid4().hex
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "sync",
                "sync_kind": sync_payload["kind"],
                "execute": bool(sync_payload.get("execute")),
                "payload": sync_payload,
                "created_at": registry.iso_now(),
            }
        self.executor.submit(self._run_sync_job, job_id, sync_payload)
        return job_id

    def _run_sync_job(self, job_id: str, payload: dict[str, Any]) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        try:
            conn = open_registry_db()
            try:
                result = run_sync_payload(conn, payload)
            finally:
                conn.close()
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "finished_at": registry.iso_now(),
                        "result": result,
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised via HTTP behavior
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "finished_at": registry.iso_now(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    }
                )


class ProjectRegistryHandler(BaseHTTPRequestHandler):
    server: ProjectRegistryWebServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path in {"/", "/index.html"}:
                self.send_text(INDEX_HTML, content_type="text/html; charset=utf-8")
                return
            if parsed.path.startswith("/api/"):
                if not self.authorized():
                    self.send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                    return
                self.handle_api_get(parsed)
                return
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        except sqlite3.OperationalError as exc:
            self.handle_sqlite_error(exc)
        except KeyError as exc:
            self.send_json({"error": f"project not found: {exc}"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        try:
            if not parsed.path.startswith("/api/"):
                self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
                return
            if not self.authorized():
                self.send_json({"error": "unauthorized"}, status=HTTPStatus.UNAUTHORIZED)
                return
            payload = self.read_json_body()
            self.handle_api_post(parsed, payload)
        except sqlite3.OperationalError as exc:
            self.handle_sqlite_error(exc)
        except KeyError as exc:
            self.send_json({"error": f"project not found: {exc}"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        header_token = self.headers.get("X-Project-Registry-Token", "")
        token = ""
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
        elif header_token:
            token = header_token.strip()
        return bool(token) and token == self.server.token

    def handle_api_get(self, parsed: urllib.parse.ParseResult) -> None:
        query = urllib.parse.parse_qs(parsed.query)
        path = parsed.path
        if path == "/api/review-queue":
            limit = parse_int(query.get("limit", ["50"])[0], 50)
            states = parse_state_set(query.get("tracking_states", []), DEFAULT_REVIEW_STATES)
            kinds = parse_kind_set(query.get("kinds", []))
            conn = open_registry_db()
            try:
                items = registry.build_review_queue(conn, tracking_states=states, limit=limit, kinds=kinds)
                self.send_json(queue_payload("review", states, kinds, items))
            finally:
                conn.close()
            return
        if path == "/api/promotion-queue":
            limit = parse_int(query.get("limit", ["50"])[0], 50)
            states = parse_state_set(query.get("tracking_states", []), DEFAULT_PROMOTION_STATES)
            kinds = parse_kind_set(query.get("kinds", []))
            include_personal_tools = str(query.get("include_personal_tools", [""])[0]).lower() in {"1", "true", "yes"}
            conn = open_registry_db()
            try:
                items = registry.build_showcase_queue(
                    conn,
                    tracking_states=states,
                    limit=limit,
                    kinds=kinds,
                    include_personal_tools=include_personal_tools,
                )
                self.send_json(queue_payload("promotion", states, kinds, items))
            finally:
                conn.close()
            return
        if path == "/api/settings":
            self.send_json(public_settings_payload(bool(self.server.token)))
            return
        if path == "/api/merge-batch":
            limit = parse_int(query.get("limit", ["50"])[0], 50)
            mode = str(query.get("mode", ["moderate"])[0] or "moderate").strip()
            bootstrap = str(query.get("bootstrap_collection_creator_heuristic", [""])[0]).lower() in {"1", "true", "yes"}
            conn = open_registry_db()
            try:
                self.send_json(
                    merge_batch_plan_payload(
                        conn,
                        mode=mode,
                        limit=limit,
                        bootstrap_collection_creator_heuristic=bootstrap,
                    )
                )
            finally:
                conn.close()
            return
        if path == "/api/merge-pair":
            source_key = str(query.get("source_project_key", [""])[0] or "").strip()
            target_key = str(query.get("target_project_key", [""])[0] or "").strip()
            if not source_key or not target_key:
                raise ValueError("source_project_key and target_project_key are required")
            conn = open_registry_db()
            try:
                self.send_json(merge_pair_detail_payload(conn, source_key, target_key))
            finally:
                conn.close()
            return
        if path == "/api/projects":
            limit = parse_int(query.get("limit", ["50"])[0], 50)
            search = str(query.get("q", [""])[0] or "").strip()
            states = parse_state_set(query.get("tracking_states", []), set())
            conn = open_registry_db()
            try:
                if search:
                    items = registry.project_search_candidates(conn, search, limit=limit)
                else:
                    projects = registry.list_projects(conn, tracking_states=states or None)[:limit]
                    items = [project_summary(project) for project in projects]
                self.send_json({"items": items, "count": len(items)})
            finally:
                conn.close()
            return
        if path.startswith("/api/projects/"):
            project_key = urllib.parse.unquote(path.removeprefix("/api/projects/")).strip("/")
            conn = open_registry_db()
            try:
                self.send_json(project_detail_payload(conn, project_key))
            finally:
                conn.close()
            return
        if path.startswith("/api/jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/jobs/")).strip("/")
            with self.server.jobs_lock:
                job = dict(self.server.jobs.get(job_id) or {})
            if not job:
                self.send_json({"error": "job not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self.send_json(job)
            return
        self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)

    def handle_api_post(self, parsed: urllib.parse.ParseResult, payload: dict[str, Any]) -> None:
        path = parsed.path
        if path == "/api/seeds":
            seed = str(payload.get("seed") or "").strip()
            if not seed:
                raise ValueError("seed is required")
            seed_type = registry.detect_seed_type(seed, str(payload.get("type") or "") or None)
            conn = open_registry_db()
            try:
                project = registry.ensure_project_for_seed(
                    conn,
                    seed_type,
                    seed,
                    project_key=str(payload.get("project_key") or "").strip() or None,
                )
                conn.commit()
                self.send_json(
                    {
                        "project_key": project["project_key"],
                        "seed_type": seed_type,
                        "seed_value": registry.canonical_seed_value(seed_type, seed),
                        "name": project.get("name", ""),
                    }
                )
            finally:
                conn.close()
            return
        if path == "/api/merge-batch/action":
            conn = open_registry_db()
            try:
                self.send_json(merge_batch_action(conn, payload))
            finally:
                conn.close()
            return
        if path == "/api/sync":
            job_id = self.server.start_sync_job(payload)
            self.send_json({"job_id": job_id, "status": "queued"}, status=HTTPStatus.ACCEPTED)
            return
        if not path.startswith("/api/projects/"):
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        tail = path.removeprefix("/api/projects/")
        parts = [urllib.parse.unquote(part) for part in tail.split("/") if part]
        if len(parts) != 2:
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            return
        project_key, action = parts
        if action == "enrich":
            job_id = self.server.start_enrich_job(
                project_key,
                deep_investigation=bool(payload.get("deep_investigation")),
                llm_options=llm_options_from_payload(payload),
            )
            self.send_json({"job_id": job_id, "status": "queued"}, status=HTTPStatus.ACCEPTED)
            return
        conn = open_registry_db()
        try:
            if action == "tracking-state":
                tracking_state = registry.normalize_tracking_state(str(payload.get("tracking_state") or ""))
                if tracking_state not in registry.TRACKING_STATES:
                    raise ValueError("invalid tracking_state")
                project = registry.update_project_tracking_state_value(
                    conn,
                    registry.resolve_project_key_input(conn, project_key),
                    tracking_state,
                    note=str(payload.get("note") or "").strip(),
                )
                if project is None:
                    raise KeyError(project_key)
                self.send_json({"project": project_summary(project)})
                return
            if action == "dismiss-review-kind":
                kind = str(payload.get("kind") or "").strip()
                if not kind:
                    raise ValueError("kind is required")
                project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
                if project is None:
                    raise KeyError(project_key)
                project = registry.ensure_project_shape(project)
                project["dismissed_review_kinds"] = registry.unique_list(list(project.get("dismissed_review_kinds", [])) + [kind])
                registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                conn.commit()
                self.send_json({"project": project_summary(project), "dismissed_review_kind": kind})
                return
            if action == "confirm-candidate":
                project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
                if project is None:
                    raise KeyError(project_key)
                project = registry.ensure_project_shape(project)
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                if identifier_type == "service_url":
                    value = value or next(iter(registry.project_service_hint_urls(project)), "")
                    confirmed = registry.confirm_service_seed(project, value, source="web_ui")
                    changed_value = confirmed or value
                elif identifier_type == "repo_url":
                    value = value or next(iter(registry.project_repo_hint_urls(project)), "")
                    confirmed = registry.confirm_repo_seed(project, value, source="web_ui")
                    changed_value = confirmed or value
                else:
                    raise ValueError("identifier_type must be service_url or repo_url")
                if not changed_value:
                    raise ValueError("candidate value is required")
                project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
                project["merge_recommendation"] = registry.compute_merge_recommendation(project)
                project = registry.apply_promotion_state(project)
                registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
                conn.commit()
                self.send_json({"project": project_summary(project), "confirmed": {"type": identifier_type, "value": changed_value}})
                return
            if action == "reject-candidate":
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                project = reject_candidate(
                    conn,
                    project_key,
                    identifier_type,
                    value,
                    note=str(payload.get("note") or "").strip(),
                )
                self.send_json({"project": project_summary(project), "rejected": {"type": identifier_type, "value": value}})
                return
            if action == "block-identifier":
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                project = block_identifier_on_project(
                    conn,
                    project_key,
                    identifier_type,
                    value,
                    note=str(payload.get("note") or "").strip(),
                )
                self.send_json({"project": project_summary(project), "blocked": {"type": identifier_type, "value": value}})
                return
            if action == "split-project":
                result = split_project_identifier(
                    conn,
                    project_key,
                    str(payload.get("identifier_type") or "").strip(),
                    str(payload.get("value") or "").strip(),
                    note=str(payload.get("note") or "").strip(),
                    new_project_key=str(payload.get("new_project_key") or "").strip(),
                )
                self.send_json(result)
                return
            self.send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
        finally:
            conn.close()

    def read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def handle_sqlite_error(self, exc: sqlite3.OperationalError) -> None:
        if "locked" in str(exc).lower():
            self.send_json({"error": str(exc)}, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return
        self.send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)

    def send_text(self, text: str, *, content_type: str = "text/plain; charset=utf-8", status: HTTPStatus = HTTPStatus.OK) -> None:
        body = text.encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def parse_kind_set(values: list[str]) -> set[str] | None:
    kinds: set[str] = set()
    for value in values:
        kinds.update(item for item in value.replace(",", " ").split() if item)
    return kinds or None


def queue_payload(queue_name: str, tracking_states: set[str], kinds: set[str] | None, items: list[dict[str, Any]]) -> dict[str, Any]:
    kind_counts: dict[str, int] = {}
    for item in items:
        for signal in item.get("signals", []):
            if not isinstance(signal, dict):
                continue
            kind = str(signal.get("kind") or "")
            kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
    return {
        "queue_name": queue_name,
        "tracking_states": sorted(tracking_states),
        "kinds": sorted(kinds) if kinds else [],
        f"{queue_name}_count": len(items),
        "kind_counts": kind_counts,
        "items": items,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve a small mobile web UI for project_registry review workflows.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host. Use 0.0.0.0 for LAN access.")
    parser.add_argument("--port", type=int, default=8765, help="Bind port. Default: 8765.")
    parser.add_argument("--db-path", help="SQLite DB path. Defaults to the repository data/survey.db.")
    parser.add_argument("--token", help="Shared token required for /api/* requests. Can also be set with ATPROTO_SURVEY_WEB_TOKEN.")
    parser.add_argument("--reload", action="store_true", help="Restart this server process when project_registry_web.py or project_registry.py changes.")
    parser.add_argument("--reload-interval", type=float, default=1.0, help="Seconds between reload file checks. Default: 1.0.")
    return parser


def start_reload_watcher(paths: list[Path], interval: float) -> None:
    watched = {path: path.stat().st_mtime_ns for path in paths if path.exists()}
    if not watched:
        return

    def watch() -> None:
        while True:
            time.sleep(max(0.2, float(interval or 1.0)))
            for path, previous_mtime in list(watched.items()):
                try:
                    current_mtime = path.stat().st_mtime_ns
                except OSError:
                    continue
                if current_mtime != previous_mtime:
                    print(f"Reloading after change: {path}", flush=True)
                    os.execv(sys.executable, [sys.executable, *sys.argv])

    threading.Thread(target=watch, name="registry-web-reload", daemon=True).start()


def main() -> int:
    args = build_parser().parse_args()
    token = str(args.token or os.environ.get("ATPROTO_SURVEY_WEB_TOKEN") or "").strip()
    if not token:
        print("error: --token or ATPROTO_SURVEY_WEB_TOKEN is required", flush=True)
        return 2
    configure_registry_db(args.db_path)
    server = ProjectRegistryWebServer((str(args.host), int(args.port)), token)
    if args.reload:
        start_reload_watcher(
            [
                Path(__file__).resolve(),
                Path(registry.__file__).resolve(),
            ],
            float(args.reload_interval or 1.0),
        )
    url = f"http://{args.host}:{args.port}/?token={urllib.parse.quote(token)}"
    print(f"Serving project registry web UI at {url}", flush=True)
    print(f"Using DB: {registry.DB_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.executor.shutdown(wait=False, cancel_futures=True)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
