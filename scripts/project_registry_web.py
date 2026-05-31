#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
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
    body.single-pane main {
      grid-template-columns: minmax(0, 920px);
      justify-content: center;
    }
    body.single-pane .detail {
      display: none;
    }
    .tabs, .toolbar, .actions, .row {
      display: flex;
      gap: 8px;
      align-items: center;
      flex-wrap: wrap;
    }
    .tabs, .toolbar {
      min-width: 0;
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
    .identifier-row-actions {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .identifier-list {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .action-card, .job-item {
      display: grid;
      gap: 8px;
      width: 100%;
      min-width: 0;
      padding: 8px;
      border-left: 3px solid var(--line);
      border-radius: 5px;
      background: var(--bg);
    }
    .action-card-main {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      align-items: start;
      min-width: 0;
    }
    .action-card-actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      justify-content: flex-end;
    }
    .job-list {
      display: grid;
      gap: 8px;
      min-width: 0;
    }
    .job-summary {
      display: flex;
      gap: 6px;
      align-items: center;
      flex-wrap: wrap;
      min-width: 0;
      cursor: pointer;
    }
    .job-summary .muted {
      font-size: 12px;
    }
    .job-item ul {
      margin: 0;
      padding-left: 18px;
    }
    .confirm-overlay {
      position: fixed;
      inset: 0;
      z-index: 20;
      display: grid;
      place-items: center;
      padding: 16px;
      background: rgba(0, 0, 0, .45);
    }
    .confirm-overlay[hidden] {
      display: none;
    }
    .confirm-dialog {
      display: grid;
      gap: 12px;
      width: min(100%, 520px);
      max-height: min(80dvh, 620px);
      overflow: auto;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      box-shadow: 0 12px 36px rgba(0, 0, 0, .28);
    }
    .confirm-message {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    button, select, input {
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      border-radius: 7px;
      min-height: 36px;
      min-width: 0;
      max-width: 100%;
      padding: 7px 10px;
      font: inherit;
    }
    select, input {
      width: 100%;
    }
    input[type="checkbox"] {
      width: auto;
      min-height: auto;
      flex: 0 0 auto;
    }
    label {
      min-width: 0;
      max-width: 100%;
    }
    .section > label {
      display: grid;
      gap: 4px;
    }
    .row label {
      display: grid;
      gap: 4px;
      flex: 1 1 150px;
    }
    label.row {
      display: flex;
      align-items: center;
      flex-wrap: nowrap;
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
    .search-panel {
      display: grid;
      gap: 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }
    .search-panel summary {
      cursor: pointer;
      color: var(--muted);
      font-weight: 700;
    }
    .search-filter-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
      gap: 8px;
      padding-top: 8px;
    }
    .search-filter-group {
      display: grid;
      gap: 4px;
      align-content: start;
      min-width: 0;
    }
    .search-filter-group > span {
      color: var(--muted);
      font-size: 12px;
    }
    .search-filter-options {
      display: flex;
      gap: 6px;
      flex-wrap: wrap;
    }
    .search-filter-options label {
      display: inline-flex;
      gap: 4px;
      align-items: center;
      font-size: 13px;
    }
    .search-chips {
      display: flex;
      gap: 5px;
      flex-wrap: wrap;
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
    .muted {
      color: var(--muted);
      min-width: 0;
      overflow-wrap: anywhere;
    }
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
    .status:empty {
      display: none;
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
      .action-card-main {
        grid-template-columns: 1fr;
      }
      .action-card-actions {
        justify-content: flex-start;
      }
      header {
        position: sticky;
        padding: 7px 10px;
      }
      h1 {
        display: flex;
        align-items: center;
        font-size: 16px;
        min-height: 32px;
        margin-bottom: 4px;
        padding-right: 82px;
      }
      .tabs {
        flex-wrap: nowrap;
        overflow-x: auto;
        overscroll-behavior-x: contain;
        scrollbar-width: none;
        margin: 0 -10px;
        padding: 0 10px 4px;
      }
      .tabs::-webkit-scrollbar {
        display: none;
      }
      .tabs button {
        flex: 0 0 auto;
        min-height: 32px;
        padding: 5px 9px;
      }
      .toolbar {
        display: block;
        height: 0;
        min-height: 0;
        padding: 0;
        overflow: visible;
      }
      .toolbar button,
      .toolbar select,
      .toolbar input {
        min-height: 32px;
        padding: 5px 8px;
      }
      #refresh {
        position: absolute;
        top: 7px;
        right: 10px;
        min-height: 32px;
        padding: 5px 9px;
      }
      body[data-tab="search"] .toolbar,
      body[data-tab="merge"] .toolbar {
        display: flex;
        gap: 6px;
        height: auto;
        padding-top: 6px;
        padding-right: 76px;
      }
      .status {
        min-height: 18px;
        padding-top: 4px;
      }
    }
  </style>
</head>
<body>
  <header>
    <h1>Project Registry</h1>
    <div class="tabs">
      <button id="tab-review" class="active" title="Show projects that need review. This does not change the DB.">Review</button>
      <button id="tab-review-ops" title="Run review-stalled or review-ignore dry-runs, with explicit execute available.">Review Ops</button>
      <button id="tab-promotion" title="Show candidates that may be ready for introduction. This does not change the DB.">Promotion</button>
      <button id="tab-merge" title="Review duplicate merge plans. Loading this list does not change the DB.">Merge</button>
      <button id="tab-sync" title="Run sync dry-runs or explicit execute jobs. Dry-run does not change the DB.">Sync</button>
      <button id="tab-jobs" title="Review recent background job logs kept in this browser session. This does not change the DB.">Jobs</button>
      <button id="tab-search" title="Search projects by name, seed, NSID, or URL. This does not change the DB.">Search</button>
      <button id="tab-new-seed" title="Preview and register a new NSID, repo URL, or service URL seed.">New Seed</button>
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
  <div id="confirm-modal" class="confirm-overlay" hidden>
    <div class="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="confirm-title">
      <h2 id="confirm-title">Confirm Action</h2>
      <div id="confirm-message" class="confirm-message"></div>
      <label id="confirm-input-label" hidden>Reason <input id="confirm-input" type="text" placeholder="Reason"></label>
      <div class="actions">
        <button id="confirm-cancel" title="Cancel this action. No SQLite changes are made.">Cancel</button>
        <button id="confirm-ok" class="primary" title="Proceed with this action.">Proceed</button>
      </div>
    </div>
  </div>
  <script>
    const params = new URLSearchParams(location.search);
    const tokenParam = params.get("token");
    if (tokenParam) {
      localStorage.setItem("projectRegistryToken", tokenParam);
      params.delete("token");
      const next = location.pathname + (params.toString() ? "?" + params.toString() : "") + location.hash;
      history.replaceState(null, "", next);
    }
    const initialSeedParam = params.get("seed") || "";
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
      llmApiStyle: "",
      llmReasoningEffort: "",
      syncKind: "introduced",
      syncQuery: "from:yamarten.bsky.social #ATプロトーク",
      syncLimit: 25,
      syncMaxPages: 4,
      syncPages: 1,
      syncPerPage: 20,
      syncLookbackDays: 10,
      syncCreateUnmatched: true,
      syncScreenUnmatched: false,
      syncAllHistory: false,
      reviewOpsKind: "stalled",
      reviewOpsLimit: 100,
      reviewOpsInactivityDays: 45,
      reviewOpsEnrichAgeDays: 30,
      reviewOpsIncludeStable: true,
      reviewOpsIncludeStalled: true,
      reviewOpsNoteReason: "",
      reviewOpsNote: "",
      enrichAllLimit: 25,
      enrichAllIncludeStable: false,
      enrichAllIncludeStalled: false,
      enrichAllIncludeArchived: false,
      enrichAllIncludeIgnore: false,
      enrichAllAllTrackingStates: false,
      enrichAllNewOnly: false,
      enrichAllRetryFailures: false,
      enrichAllDeepInvestigation: false
    };
    const defaultSearchFilters = () => ({ trackingStates: [], serviceStatuses: [], statuses: "", categories: "", introduced: "any", hasCandidates: "any", hasErrors: "any", reviewKinds: "", seenSince: "", enrichedBefore: "", sort: "" });
    const state = { tab: "review", selected: null, detailView: "project", projectItems: [], projectNavigation: false, mergeItems: [], mergeReturnIndex: null, jobs: new Map(), seedPreview: null, listCache: new Map(), listRequestSeq: 0, searchFilters: defaultSearchFilters(), settings: loadSettings() };
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
      document.body.dataset.tab = tab;
      document.body.classList.toggle("single-pane", tab === "jobs");
      for (const name of ["review", "review-ops", "promotion", "merge", "sync", "jobs", "search", "new-seed", "settings"]) {
        $("tab-" + name).classList.toggle("active", name === tab);
      }
      $("query").style.display = tab === "search" ? "" : "none";
      $("merge-mode").style.display = tab === "merge" ? "" : "none";
      if (tab === "review-ops") {
        loadReviewOpsScreen();
        return;
      }
      if (tab === "sync") {
        loadSyncScreen();
        return;
      }
      if (tab === "jobs") {
        loadJobsScreen();
        return;
      }
      if (tab === "settings") {
        loadSettingsScreen();
        return;
      }
      if (tab === "new-seed") {
        loadNewSeedScreen();
        return;
      }
      closeDetailPane(true);
      loadList();
    }

    function detailBackButton() {
      return `<div class="mobile-detail-bar"><button id="close-detail" title="Hide details and return to the list.">Back to list</button></div>`;
    }

    function detailNavigation(previousEnabled, nextEnabled) {
      return `<div class="actions detail-navigation">
        <button id="previous-detail" ${previousEnabled ? "" : "disabled"} title="Open the previous item in the current list. This does not change the DB.">Previous</button>
        <button id="next-detail" ${nextEnabled ? "" : "disabled"} title="Open the next item in the current list. This does not change the DB.">Next</button>
      </div>`;
    }

    function openDetailPane() {
      document.body.classList.add("detail-open");
      if (window.matchMedia("(max-width: 820px)").matches) {
        requestAnimationFrame(() => {
          const headerHeight = document.querySelector("header")?.getBoundingClientRect().height || 0;
          const detailTop = $("detail").getBoundingClientRect().top + window.scrollY;
          window.scrollTo({ top: Math.max(0, detailTop - headerHeight - 8), behavior: "smooth" });
        });
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

    function cardBody(item) {
      const signals = asList(item.signals).slice(0, 2).map((sig) =>
        `<div class="signal">${esc(sig.kind)} <span class="muted">p${esc(sig.priority || 0)}</span></div>`
      ).join("");
      const seedLabel = [item.seed_type, item.seed_value].filter(Boolean).join(": ");
      return `
        <h2>${esc(item.name || "(unnamed)")}</h2>
        <div class="row">
          <span class="pill">${esc(item.project_key)}</span>
          <span class="pill">${esc(item.tracking_state || "")}</span>
          <span class="pill">${esc(item.service_status || "")}</span>
        </div>
        ${seedLabel ? `<div class="muted">${esc(seedLabel)}</div>` : ""}
        ${item.website_url ? `<a href="${esc(item.website_url)}" target="_blank" rel="noreferrer">${esc(item.website_url)}</a>` : ""}
        <div class="signals">${signals}</div>`;
    }

    function card(item) {
      return `<button class="card" data-key="${esc(item.project_key)}" title="Open project details. This does not change the DB.">${cardBody(item)}</button>`;
    }

    function promotionCard(item) {
      return `<div class="card">
        ${cardBody(item)}
        <div class="action-card-actions">
          <button data-key="${esc(item.project_key)}" title="Open project details. This does not change the DB.">Open</button>
          <button data-hold-promotion="${esc(item.project_key)}" data-hold-reason="${esc(item.last_promotion_deferral_reason || "")}" title="Hide this project from the promotion queue for 7 days while recording a reason.">Hold 7d</button>
        </div>
      </div>`;
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

    function searchCheckboxes(name, values, selected) {
      const active = new Set(asList(selected));
      return values.map((value) => `<label><input type="checkbox" name="${esc(name)}" value="${esc(value)}" ${active.has(value) ? "checked" : ""}> ${esc(value)}</label>`).join("");
    }

    function searchFilterPanel() {
      const filters = state.searchFilters;
      const chips = [];
      for (const value of filters.trackingStates) chips.push(`state:${value}`);
      for (const value of filters.serviceStatuses) chips.push(`service:${value}`);
      for (const [key, value] of [["record-status", filters.statuses], ["category", filters.categories], ["review", filters.reviewKinds]]) {
        if (value) chips.push(`${key}:${value}`);
      }
      for (const [key, value] of [["introduced", filters.introduced], ["candidate", filters.hasCandidates], ["error", filters.hasErrors]]) {
        if (value !== "any") chips.push(`${key}:${value}`);
      }
      if (filters.seenSince) chips.push(`seen:>=${filters.seenSince}`);
      if (filters.enrichedBefore) chips.push(`enriched:<${filters.enrichedBefore}`);
      if (filters.sort) chips.push(`sort:${filters.sort}`);
      return `<div class="search-panel">
        <div class="actions">
          <button id="run-search" class="primary" title="Search using the text and filters below. This does not change the DB.">Search</button>
          <button id="clear-search" title="Clear the search text and filters. This does not change the DB.">Clear</button>
          ${chips.length ? `<button id="clear-search-filters" title="Clear structured filters but keep the free-text query. This does not change the DB.">Clear filters</button>` : ""}
        </div>
        ${chips.length ? `<div class="search-chips">${chips.map((chip) => `<span class="pill">${esc(chip)}</span>`).join("")}</div>` : ""}
        <details>
          <summary>Filters${chips.length ? ` (${chips.length})` : ""}</summary>
          <div class="search-filter-grid">
            <div class="search-filter-group"><span>Tracking state</span><div class="search-filter-options">${searchCheckboxes("search-tracking-state", ["active", "watchlist", "stable", "stalled", "archived", "ignore"], filters.trackingStates)}</div></div>
            <div class="search-filter-group"><span>Service status</span><div class="search-filter-options">${searchCheckboxes("search-service-status", ["unknown", "unverified", "none", "dead", "live", "private", "unreleased", "beta", "invite_only"], filters.serviceStatuses)}</div></div>
            <label class="search-filter-group"><span>Record status</span><input id="search-statuses" type="text" value="${esc(filters.statuses)}" placeholder="comma-separated"></label>
            <label class="search-filter-group"><span>Category</span><input id="search-categories" type="text" value="${esc(filters.categories)}" placeholder="comma-separated"></label>
            <label class="search-filter-group"><span>Introduced</span><select id="search-introduced">${searchChoiceOptions(filters.introduced)}</select></label>
            <label class="search-filter-group"><span>Candidates remaining</span><select id="search-has-candidates">${searchChoiceOptions(filters.hasCandidates)}</select></label>
            <label class="search-filter-group"><span>Errors</span><select id="search-has-errors">${searchChoiceOptions(filters.hasErrors)}</select></label>
            <label class="search-filter-group"><span>Review signal kind</span><input id="search-review-kinds" type="text" value="${esc(filters.reviewKinds)}" placeholder="comma-separated"></label>
            <label class="search-filter-group"><span>Last seen since</span><input id="search-seen-since" type="date" value="${esc(filters.seenSince)}"></label>
            <label class="search-filter-group"><span>Enriched before</span><input id="search-enriched-before" type="date" value="${esc(filters.enrichedBefore)}"></label>
            <label class="search-filter-group"><span>Sort</span><select id="search-sort">
              <option value="" ${filters.sort ? "" : "selected"}>auto</option>
              ${["relevance", "last_seen_desc", "last_enriched_asc", "name_asc"].map((value) => `<option value="${value}" ${filters.sort === value ? "selected" : ""}>${value}</option>`).join("")}
            </select></label>
          </div>
        </details>
        <div class="muted">Operators: state:, service:, status:, record-status:, category:, introduced:, candidate:, error:, review:, seen:&gt;=YYYY-MM-DD, enriched:&lt;YYYY-MM-DD, sort:</div>
      </div>`;
    }

    function searchChoiceOptions(selected) {
      return ["any", "yes", "no"].map((value) => `<option value="${value}" ${selected === value ? "selected" : ""}>${value}</option>`).join("");
    }

    function selectedSearchValues(name) {
      return Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map((input) => input.value);
    }

    function readSearchFilters() {
      if (!$("search-introduced")) return;
      state.searchFilters = {
        trackingStates: selectedSearchValues("search-tracking-state"),
        serviceStatuses: selectedSearchValues("search-service-status"),
        statuses: $("search-statuses").value.trim(),
        categories: $("search-categories").value.trim(),
        introduced: $("search-introduced").value,
        hasCandidates: $("search-has-candidates").value,
        hasErrors: $("search-has-errors").value,
        reviewKinds: $("search-review-kinds").value.trim(),
        seenSince: $("search-seen-since").value,
        enrichedBefore: $("search-enriched-before").value,
        sort: $("search-sort").value
      };
    }

    function parseSearchOperators(text) {
      const filters = Object.assign(defaultSearchFilters(), state.searchFilters);
      const terms = [];
      const setList = (key, value) => { filters[key] = value.split(",").map((item) => item.trim()).filter(Boolean); };
      for (const token of String(text || "").trim().split(/\s+/).filter(Boolean)) {
        const match = token.match(/^([^:]+):(.*)$/);
        if (!match) { terms.push(token); continue; }
        const key = match[1].toLowerCase();
        const value = match[2];
        if (key === "state") setList("trackingStates", value);
        else if (key === "service" || key === "status") setList("serviceStatuses", value);
        else if (key === "record-status") filters.statuses = value;
        else if (key === "category") filters.categories = value;
        else if (key === "introduced") filters.introduced = value;
        else if (key === "candidate" || key === "candidates") filters.hasCandidates = value;
        else if (key === "error" || key === "errors") filters.hasErrors = value;
        else if (key === "review") filters.reviewKinds = value;
        else if (key === "seen" && value.startsWith(">=")) filters.seenSince = value.slice(2);
        else if (key === "enriched" && value.startsWith("<")) filters.enrichedBefore = value.slice(1);
        else if (key === "sort") filters.sort = value;
        else { terms.push(token); continue; }
      }
      return { text: terms.join(" "), filters };
    }

    function runSearch() {
      readSearchFilters();
      const parsed = parseSearchOperators($("query").value);
      state.searchFilters = parsed.filters;
      $("query").value = parsed.text;
      clearListCache();
      loadList({ force: true });
    }

    function wireSearchFilters() {
      $("run-search").onclick = runSearch;
      $("clear-search").onclick = () => {
        $("query").value = "";
        state.searchFilters = defaultSearchFilters();
        clearListCache();
        loadList({ force: true });
      };
      const clearFilters = $("clear-search-filters");
      if (clearFilters) clearFilters.onclick = () => {
        state.searchFilters = defaultSearchFilters();
        clearListCache();
        loadList({ force: true });
      };
    }

    function searchApiPath() {
      const params = new URLSearchParams({ limit: "50" });
      const q = $("query").value.trim();
      const filters = state.searchFilters;
      if (q) params.set("q", q);
      if (filters.trackingStates.length) params.set("tracking_states", filters.trackingStates.join(","));
      if (filters.serviceStatuses.length) params.set("service_statuses", filters.serviceStatuses.join(","));
      for (const [key, value] of [["statuses", filters.statuses], ["categories", filters.categories], ["introduced", filters.introduced], ["has_candidates", filters.hasCandidates], ["has_errors", filters.hasErrors], ["review_kinds", filters.reviewKinds], ["seen_since", filters.seenSince], ["enriched_before", filters.enrichedBefore], ["sort", filters.sort]]) {
        if (value && value !== "any") params.set(key, value);
      }
      return "/api/projects?" + params.toString();
    }

    function listCacheKey() {
      if (state.tab === "review") return `review:${state.settings.reviewLimit || 50}`;
      if (state.tab === "promotion") return `promotion:${state.settings.promotionLimit || 50}:${state.settings.includePersonalTools ? "personal" : "default"}`;
      if (state.tab === "merge") return `merge:${$("merge-mode").value || state.settings.mergeMode || "moderate"}:${state.settings.mergeLimit || 50}`;
      if (state.tab === "search") return `search:${searchApiPath()}`;
      return `${state.tab}:default`;
    }

    function clearListCache() {
      state.listCache.clear();
    }

    function renderListFromData(data) {
      if (state.tab === "review") {
        const items = asList(data.items);
        state.projectItems = items;
        $("list").innerHTML = listCount("Review queue", data.review_count ?? items.length, `limit ${state.settings.reviewLimit || 50}`) + (items.map(card).join("") || `<div class="card muted">No review items.</div>`);
      } else if (state.tab === "promotion") {
        const items = asList(data.items);
        state.projectItems = items;
        $("list").innerHTML = listCount("Promotion queue", data.promotion_count ?? items.length, `limit ${state.settings.promotionLimit || 50}`) + (items.map(promotionCard).join("") || `<div class="card muted">No promotion items.</div>`);
      } else if (state.tab === "merge") {
        state.mergeItems = asList(data.items);
        const detail = `${data.planned_merges || 0} planned, ${data.skipped_count || 0} skipped`;
        $("list").innerHTML = listCount("Merge plans", data.returned_merges ?? state.mergeItems.length, detail) + (state.mergeItems.map(mergeCard).join("") || `<div class="card muted">No merge plans.</div>`);
      } else {
        const q = $("query").value.trim();
        const items = asList(data.items);
        state.projectItems = items;
        $("list").innerHTML = searchFilterPanel() + listCount("Projects", data.count ?? items.length, `${items.length} returned${q ? " / text search" : ""}`) + (items.map(card).join("") || `<div class="card muted">No projects.</div>`);
        wireSearchFilters();
      }
    }

    function listApiPath() {
      if (state.tab === "review") return `/api/review-queue?limit=${encodeURIComponent(state.settings.reviewLimit || 50)}`;
      if (state.tab === "promotion") {
        const personal = state.settings.includePersonalTools ? "&include_personal_tools=true" : "";
        return `/api/promotion-queue?limit=${encodeURIComponent(state.settings.promotionLimit || 50)}${personal}`;
      }
      if (state.tab === "merge") {
        const mode = encodeURIComponent($("merge-mode").value || state.settings.mergeMode || "moderate");
        return `/api/merge-batch?mode=${mode}&limit=${encodeURIComponent(state.settings.mergeLimit || 50)}`;
      }
      return searchApiPath();
    }

    async function loadList(options = {}) {
      const force = Boolean(options.force);
      const key = listCacheKey();
      const cached = !force ? state.listCache.get(key) : null;
      const requestId = ++state.listRequestSeq;
      if (cached) {
        renderListFromData(cached.data);
        status("Refreshing...");
      } else {
        status("Loading...");
      }
      try {
        const data = await api(listApiPath());
        if (requestId !== state.listRequestSeq || key !== listCacheKey()) return;
        state.listCache.set(key, { data, loadedAt: Date.now() });
        renderListFromData(data);
        status("");
      } catch (err) {
        status(cached ? `Refresh failed: ${err.message}` : err.message);
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
            <div class="muted">PID: ${esc(server.pid || "")}</div>
            <div class="muted">DB: ${esc(server.db_path || "")}</div>
            <div class="muted">Token configured: ${server.token_configured ? "yes" : "no"}</div>
          </div>
          <div class="section"><h3>Server Reload</h3>
            <div class="muted">Restarts or stops this web server process. SQLite data is not modified, but in-flight background jobs stop with the process.</div>
            <div class="actions">
              <button id="reload-server" class="danger" title="Restart the web server process with the same host, port, DB path, token source, and other command-line options. SQLite data is not modified.">Reload server</button>
              <button id="shutdown-server" class="danger" title="Stop the web server process after this response is sent. SQLite data is not modified.">Stop server</button>
            </div>
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
            <label>API key <input id="setting-llm-api-key" type="password" autocomplete="off" value="${esc(settings.llmApiKey)}" placeholder="${server.env.ATPROTO_SURVEY_LLM_API_KEY_SET ? "(env value set)" : "optional"}"></label>
            <label>API style
              <select id="setting-llm-api-style">
                ${[
                  ["", "(env/default)"],
                  ["auto", "auto probe"],
                  ["chat", "chat"],
                  ["responses", "responses"],
                  ["completions", "completions"]
                ].map(([style, label]) => `<option value="${style}" ${style === settings.llmApiStyle ? "selected" : ""}>${label}</option>`).join("")}
              </select>
            </label>
            <label>Reasoning effort
              <select id="setting-llm-reasoning">
                ${reasoningEffortPresets.map((effort) => `<option value="${effort}" ${effort === reasoningEffortPreset(settings.llmReasoningEffort) ? "selected" : ""}>${effort || "(env/default)"}</option>`).join("")}
                <option value="custom" ${reasoningEffortPreset(settings.llmReasoningEffort) === "custom" ? "selected" : ""}>custom...</option>
              </select>
            </label>
            <label>Reasoning custom <input id="setting-llm-reasoning-custom" type="text" value="${esc(reasoningEffortCustomValue(settings.llmReasoningEffort))}" placeholder="on, off, medium, provider-specific value"></label>
            <div class="muted">Browser API key override: ${settings.llmApiKey ? "set" : "not set"}. API key is stored in this browser's localStorage if entered here.</div>
          </div>
          <div class="actions">
            <button id="save-settings" class="primary" title="Save these options to this browser only.">Save settings</button>
            <button id="clear-llm-api-key" class="danger" title="Clear only the API key override stored in this browser. Other settings are preserved.">Clear API key</button>
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

    async function loadNewSeedScreen() {
      status("");
      $("list").innerHTML = `<div class="card"><h2>New Seed</h2><div class="muted">Preview duplicate matches before registering a seed.</div></div>`;
      const seedValue = state.seedPreview?.seed || initialSeedParam || "";
      $("detail").innerHTML = `
        ${detailBackButton()}
        <div><h2>New Seed</h2><div class="muted">Register an NSID, repository URL, or service URL. Exact matches open the existing project instead of creating a duplicate.</div></div>
        <div class="section"><h3>Seed</h3>
          <div class="row">
            <input id="new-seed-value" type="text" value="${esc(seedValue)}" placeholder="NSID, repo URL, service URL" title="Seed to preview and register.">
            <select id="new-seed-type" title="Optional seed type override.">
              <option value="">auto</option>
              <option value="nsid">nsid</option>
              <option value="repo_url">repo_url</option>
              <option value="service_url">service_url</option>
            </select>
          </div>
          <div class="actions">
            <button id="preview-new-seed" class="primary" title="Check exact and nearby project matches before writing anything.">Preview</button>
            <button id="register-new-seed" title="Register this seed if no exact match exists, or open the exact match.">Register</button>
          </div>
        </div>
        <div class="section"><h3>Preview</h3><div id="new-seed-preview" class="identifier-list"><span class="muted">No preview yet.</span></div></div>
      `;
      wireNewSeed();
      wireDetailBackButton();
      openDetailPane();
      if (seedValue) previewNewSeed();
    }

    function newSeedPayload() {
      const payload = { seed: $("new-seed-value").value.trim() };
      const type = $("new-seed-type").value;
      if (type) payload.type = type;
      return payload;
    }

    function renderSeedPreview(data) {
      const exact = data.existing_project;
      const possible = asList(data.possible_matches);
      const exactHtml = exact ? `
        <div class="row identifier-row identifier-confirmed">
          <span class="identifier-value">Exact match: ${esc(exact.project_key)} ${esc(exact.name || "")}</span>
          <button data-open-project="${esc(exact.project_key)}" title="Open the existing exact-match project. This does not change the DB.">Open</button>
        </div>
      ` : `<div class="muted">No exact match.</div>`;
      const possibleHtml = possible.length ? possible.map((item) => `
        <div class="row identifier-row identifier-candidate">
          <span class="identifier-value">${esc(item.project_key)} ${esc(item.name || "")} <span class="muted">${esc(item.seed_type || "")}: ${esc(item.seed_value || "")}</span></span>
          <button data-open-project="${esc(item.project_key)}" title="Open this possible duplicate project. This does not change the DB.">Open</button>
        </div>
      `).join("") : `<div class="muted">No nearby matches.</div>`;
      return `
        <div class="muted">Detected: ${esc(data.seed_type)} / ${esc(data.seed_value)}</div>
        ${exactHtml}
        <details ${possible.length ? "open" : ""}><summary>${possible.length} possible matches</summary><div class="identifier-list">${possibleHtml}</div></details>
      `;
    }

    async function previewNewSeed() {
      const payload = newSeedPayload();
      if (!payload.seed) return;
      status("Previewing seed...");
      const query = new URLSearchParams(payload);
      try {
        const data = await api(`/api/seeds/preview?${query.toString()}`);
        state.seedPreview = data;
        $("new-seed-preview").innerHTML = renderSeedPreview(data);
        document.querySelectorAll("[data-open-project]").forEach((el) => {
          el.onclick = () => loadDetail(el.dataset.openProject);
        });
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    function wireNewSeed() {
      $("preview-new-seed").onclick = () => previewNewSeed();
      $("register-new-seed").onclick = async () => {
        const payload = newSeedPayload();
        if (!payload.seed) return;
        if (!state.seedPreview || state.seedPreview.seed !== payload.seed) {
          await previewNewSeed();
        }
        const exact = state.seedPreview?.existing_project;
        if (exact) {
          loadDetail(exact.project_key);
          return;
        }
        const possibleCount = asList(state.seedPreview?.possible_matches).length;
        const warning = possibleCount ? `\n\n${possibleCount} possible matches were found. Review them before creating a new project.` : "";
        if (!(await confirmAction(`Register this seed as a project?\n\n${payload.seed}${warning}\n\nThis writes to SQLite.`))) return;
        status("Registering seed...");
        try {
          const data = await api("/api/seeds", { method: "POST", body: JSON.stringify(payload) });
          clearListCache();
          status(`Registered ${data.project_key}`);
          loadDetail(data.project_key);
        } catch (err) {
          status(err.message);
        }
      };
    }

    async function loadReviewOpsScreen() {
      status("Loading review tools...");
      $("list").innerHTML = `<div class="card"><h2>Review Ops</h2><div class="muted">Dry-run is the default. Execute writes to SQLite only when explicitly checked.</div></div>`;
      const settings = state.settings;
      $("detail").innerHTML = `
        ${detailBackButton()}
        <div><h2>Review Ops</h2><div class="muted">Review-stalled suggests tracking_state changes; review-ignore suggests low-value projects to ignore.</div></div>
        <div class="section"><h3>Command</h3>
          <label>Kind
            <select id="review-ops-kind">
              ${[
                ["stalled", "review-stalled"],
                ["ignore", "review-ignore"]
              ].map(([value, label]) => `<option value="${value}" ${value === settings.reviewOpsKind ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
          <label class="row"><input id="review-ops-execute" type="checkbox"> Execute writes to SQLite</label>
          <label>Max candidates <input id="review-ops-limit" type="number" min="1" max="500" value="${esc(settings.reviewOpsLimit)}"></label>
        </div>
        <div class="section"><h3>Review Stalled</h3>
          <div class="row" data-review-kind="stalled">
            <label>Inactivity days <input id="review-ops-inactivity-days" type="number" min="1" max="3650" value="${esc(settings.reviewOpsInactivityDays)}"></label>
            <label>Enrich age days <input id="review-ops-enrich-age-days" type="number" min="1" max="3650" value="${esc(settings.reviewOpsEnrichAgeDays)}"></label>
          </div>
        </div>
        <div class="section" data-review-kind="ignore"><h3>Review Ignore</h3>
          <label class="row"><input id="review-ops-include-stable" type="checkbox" ${settings.reviewOpsIncludeStable ? "checked" : ""}> Include stable</label>
          <label class="row"><input id="review-ops-include-stalled" type="checkbox" ${settings.reviewOpsIncludeStalled ? "checked" : ""}> Include stalled</label>
          <label>Note reason
            <select id="review-ops-note-reason">
              ${[
                ["", "(auto/default)"],
                ["atproto_unrelated", "atproto_unrelated"],
                ["out_of_scope", "out_of_scope"],
                ["personal_experiment", "personal_experiment"],
                ["derivative_utility", "derivative_utility"],
                ["fork_without_identity", "fork_without_identity"],
                ["other", "other"]
              ].map(([value, label]) => `<option value="${value}" ${value === settings.reviewOpsNoteReason ? "selected" : ""}>${label}</option>`).join("")}
            </select>
          </label>
          <input id="review-ops-note" type="text" value="${esc(settings.reviewOpsNote)}" placeholder="Optional note stored on updated projects">
        </div>
        <div class="actions">
          <button id="run-review-ops" class="primary" title="Start a review job. Dry-run only lists candidates; execute applies the suggested tracking_state changes.">Run review</button>
        </div>
        <div class="section"><h3>Result / Logs</h3><pre id="review-ops-result">No job yet.</pre></div>
      `;
      wireReviewOps();
      wireDetailBackButton();
      openDetailPane();
      status("");
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
            <label class="row" data-sync-kind="github-search"><input id="sync-screen-unmatched" type="checkbox" ${settings.syncScreenUnmatched ? "checked" : ""}> Screen unmatched GitHub repos</label>
            <label class="row" data-sync-kind="github-search semble-collections"><input id="sync-all-history" type="checkbox" ${settings.syncAllHistory ? "checked" : ""}> All history</label>
          </div>
          <div class="section"><h3>Inputs</h3>
            <label data-sync-kind="introduced">Introduced query <input id="sync-query" type="text" value="${esc(settings.syncQuery)}" placeholder="from:yamarten.bsky.social #ATプロトーク"></label>
            <label data-sync-kind="collections">Collection source URL <input id="sync-source-url" type="text" value="" placeholder="${esc(defaultCollectionUrl)}"></label>
            <label data-sync-kind="github-search">GitHub query <input id="sync-github-query" type="text" value="" placeholder="${esc(defaultGithubQuery)}"></label>
            <div class="row">
              <label data-sync-kind="introduced">Limit <input id="sync-limit" type="number" min="1" max="500" value="${esc(settings.syncLimit)}"></label>
              <label data-sync-kind="introduced">Max pages <input id="sync-max-pages" type="number" min="1" max="20" value="${esc(settings.syncMaxPages)}"></label>
            </div>
            <div class="row">
              <label data-sync-kind="github-search semble-collections">Pages <input id="sync-pages" type="number" min="1" max="20" value="${esc(settings.syncPages)}"></label>
              <label data-sync-kind="github-search semble-collections">Per page <input id="sync-per-page" type="number" min="1" max="100" value="${esc(settings.syncPerPage)}"></label>
              <label data-sync-kind="collections github-search semble-collections">Lookback days <input id="sync-lookback-days" type="number" min="0" max="3650" value="${esc(settings.syncLookbackDays)}"></label>
            </div>
          </div>
          <div class="actions">
            <button id="run-sync" class="primary" title="Start a sync background job. Dry-run does not write to SQLite; execute does.">Run sync</button>
          </div>
          <div class="section"><h3>Result</h3><pre id="sync-result">No job yet.</pre></div>
          <div class="section"><h3>Enrich All</h3>
            <div class="muted">Runs enrich over matching projects in one background job. Each project is committed separately.</div>
            <label>Max projects <input id="enrich-all-limit" type="number" min="1" max="500" value="${esc(settings.enrichAllLimit)}"></label>
            <label class="row"><input id="enrich-all-deep" type="checkbox" ${settings.enrichAllDeepInvestigation ? "checked" : ""}> Deep investigation</label>
            <label class="row"><input id="enrich-all-new-only" type="checkbox" ${settings.enrichAllNewOnly ? "checked" : ""}> New only</label>
            <label class="row"><input id="enrich-all-retry-failures" type="checkbox" ${settings.enrichAllRetryFailures ? "checked" : ""}> Retry failures only</label>
            <label class="row"><input id="enrich-all-all-states" type="checkbox" ${settings.enrichAllAllTrackingStates ? "checked" : ""}> All tracking states</label>
            <label class="row"><input id="enrich-all-include-stable" type="checkbox" ${settings.enrichAllIncludeStable ? "checked" : ""}> Include stable</label>
            <label class="row"><input id="enrich-all-include-stalled" type="checkbox" ${settings.enrichAllIncludeStalled ? "checked" : ""}> Include stalled</label>
            <label class="row"><input id="enrich-all-include-archived" type="checkbox" ${settings.enrichAllIncludeArchived ? "checked" : ""}> Include archived</label>
            <label class="row"><input id="enrich-all-include-ignore" type="checkbox" ${settings.enrichAllIncludeIgnore ? "checked" : ""}> Include ignore</label>
            <div class="actions">
              <button id="run-enrich-all" class="primary" title="Start an enrich --all style background job using the LLM settings from Settings.">Run enrich all</button>
            </div>
            <pre id="enrich-all-result">No job yet.</pre>
          </div>
        `;
        wireSync();
        wireDetailBackButton();
        openDetailPane();
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    function loadJobsScreen() {
      closeDetailPane(true);
      $("list").innerHTML = `
        <div class="card">
        <div><h2>Jobs</h2><div class="muted">Shows recent background jobs tracked by this tab. Server restart or browser reload clears this view.</div></div>
        <div class="section"><h3>Recent Jobs</h3><div id="jobs-summary">${renderJobsSummaryHtml()}</div></div>
        </div>
      `;
      status("");
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

    function readReviewOpsPayload() {
      const kind = $("review-ops-kind").value;
      const payload = {
        kind,
        execute: $("review-ops-execute").checked,
        limit: numberSetting("review-ops-limit", 100),
        inactivity_days: numberSetting("review-ops-inactivity-days", 45),
        enrich_age_days: numberSetting("review-ops-enrich-age-days", 30),
        include_stable: $("review-ops-include-stable").checked,
        include_stalled: $("review-ops-include-stalled").checked,
        note_reason: $("review-ops-note-reason").value,
        note: $("review-ops-note").value.trim()
      };
      saveSettings(Object.assign({}, state.settings, {
        reviewOpsKind: payload.kind,
        reviewOpsLimit: payload.limit,
        reviewOpsInactivityDays: payload.inactivity_days,
        reviewOpsEnrichAgeDays: payload.enrich_age_days,
        reviewOpsIncludeStable: payload.include_stable,
        reviewOpsIncludeStalled: payload.include_stalled,
        reviewOpsNoteReason: payload.note_reason,
        reviewOpsNote: payload.note
      }));
      return payload;
    }

    function readEnrichAllPayload() {
      const payload = {
        limit: numberSetting("enrich-all-limit", 25),
        deep_investigation: $("enrich-all-deep").checked,
        new_only: $("enrich-all-new-only").checked,
        retry_failures: $("enrich-all-retry-failures").checked,
        all_tracking_states: $("enrich-all-all-states").checked,
        include_stable: $("enrich-all-include-stable").checked,
        include_stalled: $("enrich-all-include-stalled").checked,
        include_archived: $("enrich-all-include-archived").checked,
        include_ignore: $("enrich-all-include-ignore").checked,
        ...enrichPayload($("enrich-all-deep").checked)
      };
      saveSettings(Object.assign({}, state.settings, {
        enrichAllLimit: payload.limit,
        enrichAllDeepInvestigation: payload.deep_investigation,
        enrichAllNewOnly: payload.new_only,
        enrichAllRetryFailures: payload.retry_failures,
        enrichAllAllTrackingStates: payload.all_tracking_states,
        enrichAllIncludeStable: payload.include_stable,
        enrichAllIncludeStalled: payload.include_stalled,
        enrichAllIncludeArchived: payload.include_archived,
        enrichAllIncludeIgnore: payload.include_ignore
      }));
      return payload;
    }

    function updateReviewOpsVisibility() {
      const kind = $("review-ops-kind").value;
      document.querySelectorAll("[data-review-kind]").forEach((el) => {
        el.hidden = !String(el.dataset.reviewKind || "").split(/\s+/).includes(kind);
      });
    }

    function updateSyncVisibility() {
      const kind = $("sync-kind").value;
      document.querySelectorAll("[data-sync-kind]").forEach((el) => {
        el.hidden = !String(el.dataset.syncKind || "").split(/\s+/).includes(kind);
      });
    }

    function wireSync() {
      $("sync-kind").onchange = updateSyncVisibility;
      updateSyncVisibility();
      $("run-sync").onclick = async () => {
        const payload = readSyncPayload();
        const mode = payload.execute ? "EXECUTE and write to SQLite" : "dry-run without DB writes";
        if (!(await confirmAction(`Run ${payload.kind} sync?\n\nMode: ${mode}\n\nThis may call external APIs and run in the background.`))) return;
        status("Starting sync...");
        try {
          const data = await api("/api/sync", { method: "POST", body: JSON.stringify(payload) });
          if (payload.execute) clearListCache();
          $("sync-result").textContent = JSON.stringify(data, null, 2);
          status(`Started job ${data.job_id}`);
          updateJobStatus(`job:${data.job_id}`, Object.assign({ kind: `sync-${payload.kind}` }, data));
          pollJobInto(data.job_id, "sync-result", null, (job) => updateJobStatus(`job:${data.job_id}`, job));
        } catch (err) {
          status(err.message);
        }
      };
      $("run-enrich-all").onclick = async () => {
        const payload = readEnrichAllPayload();
        const stateText = payload.all_tracking_states ? "all tracking states" : ["active", "watchlist"].concat(payload.include_stable ? ["stable"] : [], payload.include_stalled ? ["stalled"] : [], payload.include_archived ? ["archived"] : [], payload.include_ignore ? ["ignore"] : []).join(", ");
        if (!(await confirmAction(`Run enrich --all style job?\n\nTargets: ${stateText}\nMax projects: ${payload.limit}\nDeep investigation: ${payload.deep_investigation ? "yes" : "no"}\n\nThis writes enriched project records to SQLite.`))) return;
        status("Starting enrich all...");
        try {
          const data = await api("/api/enrich-all", { method: "POST", body: JSON.stringify(payload) });
          clearListCache();
          $("enrich-all-result").textContent = JSON.stringify(data, null, 2);
          status(`Started job ${data.job_id}`);
          updateJobStatus(`job:${data.job_id}`, Object.assign({ kind: "enrich-all" }, data));
          pollJobInto(data.job_id, "enrich-all-result", null, (job) => updateJobStatus(`job:${data.job_id}`, job));
        } catch (err) {
          status(err.message);
        }
      };
    }

    function wireReviewOps() {
      $("review-ops-kind").onchange = updateReviewOpsVisibility;
      updateReviewOpsVisibility();
      $("run-review-ops").onclick = async () => {
        const payload = readReviewOpsPayload();
        runReviewOpsJob(payload);
      };
    }

    async function runReviewOpsJob(payload) {
      const endpoint = payload.kind === "ignore" ? "/api/review-ignore" : "/api/review-stalled";
      const mode = payload.execute ? "EXECUTE and write suggested tracking_state changes" : "dry-run without DB writes";
      if (!(await confirmAction(`Run review-${payload.kind}?\n\nMode: ${mode}\nMax candidates: ${payload.limit}\n\nDry-run is for inspection. Execute updates SQLite.`))) return;
      status("Starting review job...");
      try {
        const data = await api(endpoint, { method: "POST", body: JSON.stringify(payload) });
        if (payload.execute) clearListCache();
        $("review-ops-result").textContent = JSON.stringify(data, null, 2);
        status(`Started job ${data.job_id}`);
        updateJobStatus(`job:${data.job_id}`, Object.assign({ kind: `review-${payload.kind}` }, data));
        pollJobInto(data.job_id, "review-ops-result", (job) => renderReviewOpsJob(job, payload), (job) => updateJobStatus(`job:${data.job_id}`, job));
      } catch (err) {
        status(err.message);
      }
    }

    function renderReviewOpsJob(job, payload) {
      const result = job.result || {};
      const mode = result.mode || (payload.execute ? "execute" : "dry_run");
      const candidates = asList(result.candidates);
      const updates = asList(result.updated_projects).concat(asList(result.updated_tracking_notes));
      const counts = result.suggested_state_counts || result.kind_counts || {};
      const countRows = Object.keys(counts).sort().map((key) => `<span class="pill">${esc(key)} ${esc(counts[key])}</span>`).join("");
      const candidateRows = candidates.slice(0, 80).map((item) => `
        <div class="row identifier-row">
          <span class="identifier-value">
            ${esc(item.project_key || "")} ${esc(item.name || "")}
            <span class="muted">${esc(item.tracking_state || "")} -> ${esc(item.suggested_tracking_state || "ignore")}</span>
            ${item.kind ? `<span class="muted">${esc(item.kind)}</span>` : ""}
          </span>
          ${item.project_key ? `<button data-open-project="${esc(item.project_key)}" title="Open this candidate project. This does not change the DB.">Open</button>` : ""}
        </div>
      `).join("");
      const updateRows = updates.slice(0, 80).map((item) => `
        <div class="row identifier-row identifier-confirmed">
          <span class="identifier-value">
            ${esc(item.project_key || "")}
            ${item.tracking_state ? `<span class="muted">state: ${esc(item.tracking_state)}</span>` : ""}
            ${item.tracking_note ? `<span class="muted">${esc(item.tracking_note)}</span>` : ""}
          </span>
          ${item.project_key ? `<button data-open-project="${esc(item.project_key)}" title="Open this updated project. This does not change the DB.">Open</button>` : ""}
        </div>
      `).join("");
      const canApply = mode === "dry_run" && !payload.execute && (Number(result.candidate_count || candidates.length || 0) > 0);
      $("review-ops-result").innerHTML = `
        <div class="identifier-list">
          <div class="row">
            <span class="pill">${esc(job.kind || payload.kind)}</span>
            <span class="pill">${esc(mode)}</span>
            <span class="pill">${esc(result.candidate_count ?? result.updated_count ?? candidates.length ?? 0)} items</span>
          </div>
          <div class="row">${countRows || `<span class="muted">No counts.</span>`}</div>
          ${canApply ? `<div class="actions"><button id="apply-review-ops-dryrun" class="danger" title="Run the same review operation with execute enabled.">Apply this dry-run</button></div>` : ""}
          <details open><summary>${mode === "dry_run" ? "Candidates" : "Updated projects"}</summary><div class="identifier-list">${mode === "dry_run" ? (candidateRows || `<span class="muted">No candidates.</span>`) : (updateRows || `<span class="muted">No updates.</span>`)}</div></details>
          <details><summary>Raw JSON</summary><pre>${esc(JSON.stringify(job, null, 2))}</pre></details>
        </div>
      `;
      document.querySelectorAll("[data-open-project]").forEach((el) => {
        el.onclick = () => loadDetail(el.dataset.openProject);
      });
      const applyButton = $("apply-review-ops-dryrun");
      if (applyButton) {
        applyButton.onclick = () => runReviewOpsJob(Object.assign({}, payload, { execute: true }));
      }
    }

    async function pollJobInto(jobId, elementId, renderDone = null, onJob = null) {
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
          if (onJob) onJob(job);
          if (renderDone && (job.status === "done" || job.status === "failed")) {
            renderDone(job);
          } else {
            $(elementId).textContent = JSON.stringify(job, null, 2);
          }
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
        llmApiStyle: $("setting-llm-api-style").value,
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
      $("reset-settings").onclick = async () => {
        if (await confirmAction("Reset browser settings? This clears only localStorage settings, not SQLite.")) {
          localStorage.removeItem("projectRegistrySettings");
          state.settings = loadSettings();
          status("Settings reset.");
          loadSettingsScreen();
        }
      };
      $("clear-llm-api-key").onclick = () => {
        const next = Object.assign({}, readSettingsForm(), { llmApiKey: "" });
        saveSettings(next);
        status("API key override cleared.");
        loadSettingsScreen();
      };
      $("reload-server").onclick = async () => {
        if (!(await confirmAction("Reload the web server process now?\n\nThis keeps the same command-line arguments and does not modify SQLite. In-flight background jobs in this process will stop."))) return;
        status("Requesting server reload...");
        try {
          const data = await api("/api/reload", { method: "POST", body: JSON.stringify({ confirm: true }) });
          status(`Server reload requested: ${data.reload_id}`);
          setTimeout(() => window.location.reload(), 1500);
        } catch (err) {
          status(err.message);
        }
      };
      $("shutdown-server").onclick = async () => {
        if (!(await confirmAction("Stop the web server process now?\n\nThis does not modify SQLite. In-flight background jobs in this process will stop. You will need to start the server again from the terminal."))) return;
        status("Requesting server shutdown...");
        try {
          const data = await api("/api/shutdown", { method: "POST", body: JSON.stringify({ confirm: true }) });
          status(`Server shutdown requested: ${data.shutdown_id}`);
        } catch (err) {
          status(err.message);
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
        ${detailNavigation(index > 0, index < state.mergeItems.length - 1)}
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
      $("previous-detail").onclick = () => loadMergeDetail(index - 1);
      $("next-detail").onclick = () => loadMergeDetail(index + 1);
      $("merge-plan").onclick = async () => {
        if (await confirmAction(`Merge source into target?\n\nSource: ${item.source_project_key} ${item.source_name || ""}\nTarget: ${item.target_project_key} ${item.target_name || ""}\n\nThis writes the merged target and deletes the source project from SQLite.`)) {
          mergeMutate("merge", body);
        }
      };
      $("block-plan").onclick = async () => {
        if (await confirmAction(`Block this merge pair?\n\n${item.source_project_key} and ${item.target_project_key}\n\nThis records separate_project_keys on both projects.`)) {
          mergeMutate("block", body);
        }
      };
      $("skip-plan").onclick = () => {
        state.mergeItems.splice(index, 1);
        $("list").innerHTML = state.mergeItems.map(mergeCard).join("") || `<div class="card muted">No merge plans.</div>`;
        $("detail").innerHTML = `<p class="muted">Skipped locally. No DB change.</p>`;
      };
      $("open-source-project").onclick = () => loadDetail(item.source_project_key, { mergeReturnIndex: index });
      $("open-target-project").onclick = () => loadDetail(item.target_project_key, { mergeReturnIndex: index });
    }

    async function mergeMutate(action, payload) {
      status("Saving...");
      try {
          const data = await api("/api/merge-batch/action", { method: "POST", body: JSON.stringify(Object.assign({ action }, payload)) });
          clearListCache();
          status(data.mode || "Saved.");
          await loadList({ force: true });
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
          <span class="identifier-row-actions">
            <button data-split-type="${esc(type)}" data-split-value="${esc(value)}" title="Move this identifier into a new project and block it from reattaching here.">Split</button>
            <button data-defer-type="${esc(type)}" data-defer-value="${esc(value)}" title="Temporarily downgrade this identifier to candidate evidence and prevent automatic confirmation for 30 days.">Defer 30d</button>
            <button class="danger" data-block-type="${esc(type)}" data-block-value="${esc(value)}" title="Remove this identifier from this project and block it from reattaching.">Block</button>
          </span>
        </div>
      `).join("");
    }

    function renderCandidateRows(values, type) {
      return uniqueValues(values).map((url) => `
        <div class="row identifier-row identifier-candidate">
          <span class="identifier-value"><a href="${esc(url)}" target="_blank" rel="noreferrer">${esc(url)}</a></span>
          <span class="identifier-row-actions">
            <button data-confirm-type="${esc(type)}" data-confirm-value="${esc(url)}" title="Move this URL from candidate evidence into confirmed/manual project data.">Confirm</button>
            <button data-defer-type="${esc(type)}" data-defer-value="${esc(url)}" title="Keep this URL as candidate evidence and prevent automatic confirmation for 30 days.">Defer 30d</button>
            <button class="danger" data-reject-type="${esc(type)}" data-reject-value="${esc(url)}" title="Block this URL from being reattached to this project.">Reject</button>
          </span>
        </div>
      `).join("");
    }

    function renderMergeRecommendation(recommendation) {
      const rec = recommendation || {};
      const target = rec.target_project_key || "";
      if (!target) return `<span class="muted">None</span>`;
      const reasons = esc(JSON.stringify(rec.reasons || {}, null, 2));
      return `
        <div class="identifier-list">
          <div class="action-card">
            <div class="action-card-main">
              <span class="identifier-value">${esc(target)} score=${esc(rec.score || 0)} confidence=${esc(rec.merge_confidence || "")} auto=${rec.auto_merge_ready ? "yes" : "no"}</span>
              <span class="action-card-actions">
                <button data-open-project="${esc(target)}" title="Open the recommended target project. This does not change the DB.">Open</button>
                <button class="danger" data-merge-recommendation="${esc(target)}" title="Merge this project into the recommended target when the recommendation is auto-merge-ready.">Merge</button>
              </span>
            </div>
          </div>
          <details><summary>Reasons</summary><pre>${reasons}</pre></details>
        </div>
      `;
    }

    function renderProjectGuards(project) {
      const blocks = asList(project.blocked_identifiers);
      const deferred = asList(project.deferred_identifiers);
      const separate = asList(project.separate_project_keys);
      if (!blocks.length && !deferred.length && !separate.length) return `<span class="muted">None</span>`;
      const blockRows = blocks.map((item) => `
        <div class="row identifier-row">
          <span class="identifier-value">${esc(item.type || "")}: ${esc(item.value || "")}${item.note ? ` <span class="muted">(${esc(item.note)})</span>` : ""}</span>
          <button class="danger" data-clear-block-type="${esc(item.type || "")}" data-clear-block-value="${esc(item.value || "")}" title="Remove this blocked identifier record. This does not reattach the identifier automatically.">Clear</button>
        </div>
      `).join("");
      const deferredRows = deferred.map((item) => `
        <div class="row identifier-row identifier-candidate">
          <span class="identifier-value">${esc(item.type || "")}: ${esc(item.value || "")}<span class="muted"> until ${esc(item.expires_at || "")}${item.note ? ` (${esc(item.note)})` : ""}</span></span>
          <button data-clear-defer-type="${esc(item.type || "")}" data-clear-defer-value="${esc(item.value || "")}" title="Remove this temporary defer guard. This does not confirm or reattach the identifier automatically.">Clear defer</button>
        </div>
      `).join("");
      const separateRows = separate.map((otherKey) => `
        <div class="row identifier-row">
          <span class="identifier-value">${esc(otherKey)}</span>
          <button data-open-project="${esc(otherKey)}" title="Open the separated project. This does not change the DB.">Open</button>
          <button class="danger" data-clear-separate-key="${esc(otherKey)}" title="Remove the separation block between these two projects.">Clear</button>
        </div>
      `).join("");
      return `
        <details>
          <summary>${blocks.length} blocked, ${deferred.length} deferred, ${separate.length} separated</summary>
          <div class="identifier-list">${blockRows || `<span class="muted">No blocked identifiers.</span>`}</div>
          <div class="identifier-list">${deferredRows || `<span class="muted">No deferred identifiers.</span>`}</div>
          <div class="identifier-list">${separateRows || `<span class="muted">No separated projects.</span>`}</div>
        </details>
      `;
    }

    function activePromotionDeferral(project) {
      const now = Date.now();
      return asList(project.promotion_deferrals).filter((item) => !item.cleared_at && Date.parse(item.expires_at || "") > now).slice(-1)[0] || null;
    }

    function latestPromotionDeferralReason(project) {
      return asList(project.promotion_deferrals).slice(-1)[0]?.reason || "";
    }

    function renderPromotionHold(project) {
      const active = activePromotionDeferral(project);
      return `
        <div class="section"><h3>Promotion Hold</h3>
          ${active ? `<div class="identifier-row identifier-candidate"><span class="identifier-value">${esc(active.reason || "")}<span class="muted"> until ${esc(active.expires_at || "")}</span></span></div>` : `<span class="muted">No active hold.</span>`}
          <input id="promotion-hold-reason" type="text" value="${esc(latestPromotionDeferralReason(project))}" placeholder="Reason for holding promotion" title="Reason recorded with a 7-day promotion hold.">
          <div class="actions">
            <button id="hold-promotion" title="Hide this project from the promotion queue for 7 days while recording the reason.">Hold 7d</button>
            ${active ? `<button id="clear-promotion-hold" title="Clear active promotion holds while preserving their history.">Clear hold</button>` : ""}
          </div>
        </div>`;
    }

    async function loadDetail(key, options = {}) {
      state.selected = key;
      state.detailView = options.view || "project";
      state.projectNavigation = Boolean(options.projectNavigation);
      state.mergeReturnIndex = Object.prototype.hasOwnProperty.call(options, "mergeReturnIndex") ? options.mergeReturnIndex : null;
      status("Loading project...");
      try {
        const data = await api("/api/projects/" + encodeURIComponent(key));
        const p = data.project;
        const review = data.review_item || {};
        const raw = esc(JSON.stringify(p, null, 2));
        const isReviewView = state.detailView === "review";
        const projectIndex = state.projectNavigation && state.mergeReturnIndex === null ? state.projectItems.findIndex((item) => item.project_key === p.project_key) : -1;
        $("detail").innerHTML = `
          ${detailBackButton()}
          ${projectIndex >= 0 ? detailNavigation(projectIndex > 0, projectIndex < state.projectItems.length - 1) : ""}
          ${state.mergeReturnIndex !== null ? `<div class="actions"><button id="back-to-merge-detail" title="Return to the merge detail you opened this project from. This does not change the DB.">Back to merge detail</button></div>` : ""}
          ${isReviewView ? `<div class="actions"><button id="open-project-detail" title="Open the full project detail view. This does not change the DB.">Open project detail</button></div>` : ""}
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
          <div class="section"><h3>Merge Recommendation</h3>${renderMergeRecommendation(p.merge_recommendation)}</div>
          <div class="section"><h3>Signals</h3><div class="signals">${asList(review.signals).map((sig) => `<div class="signal"><b>${esc(sig.kind)}</b> p${esc(sig.priority || 0)} <button data-dismiss="${esc(sig.kind)}" title="Store this review signal kind as dismissed for this project.">Dismiss</button><pre>${esc(JSON.stringify(sig.details || {}, null, 2))}</pre></div>`).join("") || `<span class="muted">None</span>`}</div></div>
          ${renderPromotionHold(p)}
          <div class="section"><h3>Identity</h3><div>${esc(p.seed_type || "")}: ${esc(p.seed_value || "")}</div>${renderEditableIdentifierRows(asList(p.input_nsids).concat(asList(p.resolved_nsids)), "nsid")}</div>
          <div class="section"><h3>Website / Service</h3>${renderEditableIdentifierRows([p.website_url].filter(Boolean).concat(asList(p.service_urls)), "service_url")}<div class="identifier-list">${renderCandidateRows(data.service_candidates, "service_url")}</div></div>
          <div class="section"><h3>Repositories</h3>${renderEditableIdentifierRows(asList(p.source_code_urls).concat(asList(p.github_repo_urls)).concat(asList(p.tangled_repo_urls)), "repo_url")}<div class="identifier-list">${renderCandidateRows(data.repo_candidates, "repo_url")}</div></div>
          <div class="section"><h3>Enrich Job</h3><pre id="enrich-job-status">${esc(enrichJobText(state.jobs.get(p.project_key)))}</pre></div>
          ${isReviewView ? "" : `<div class="section"><h3>Add Seed</h3><div class="row"><input id="seed-value" type="text" placeholder="NSID, repo URL, service URL" title="Seed to attach to this existing project."><button id="add-seed" title="Attach this seed to the current project and write it to SQLite.">Add</button></div></div>
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
              <button id="defer-identifier" title="Temporarily downgrade this identifier to candidate evidence and prevent automatic confirmation for 30 days.">Defer 30d</button>
              <button id="block-identifier" class="danger" title="Block this identifier on the current project and remove matching stored values.">Block identifier</button>
            </div>
          </div>`}
          <div class="section"><h3>Guards</h3>${renderProjectGuards(p)}</div>
          <div class="section"><h3>Raw JSON</h3><details><summary>Show</summary><pre>${raw}</pre></details></div>
        `;
        wireDetail(p.project_key, projectIndex);
        wireDetailBackButton();
        openDetailPane();
        status("");
      } catch (err) {
        status(err.message);
      }
    }

    function wireDetail(key, projectIndex = -1) {
      const previousDetail = $("previous-detail");
      if (previousDetail) {
        previousDetail.onclick = () => loadDetail(state.projectItems[projectIndex - 1]?.project_key, { view: state.detailView, projectNavigation: true });
      }
      const nextDetail = $("next-detail");
      if (nextDetail) {
        nextDetail.onclick = () => loadDetail(state.projectItems[projectIndex + 1]?.project_key, { view: state.detailView, projectNavigation: true });
      }
      $("save-state").onclick = async () => {
        const nextState = $("tracking-state").value;
        if (await confirmAction(`Set tracking_state for ${key} to ${nextState}? This writes to SQLite.`)) {
          mutate(`/api/projects/${encodeURIComponent(key)}/tracking-state`, { tracking_state: nextState });
        }
      };
      $("enrich").onclick = async () => {
        if (await confirmAction(`Start enrich for ${key}? This runs background checks and may update SQLite.`)) {
          startEnrichJob(key, false);
        }
      };
      $("deep-enrich").onclick = async () => {
        if (await confirmAction(`Start deep enrich for ${key}? This may run extra external checks and update SQLite.`)) {
          startEnrichJob(key, true);
        }
      };
      const addSeed = $("add-seed");
      if (addSeed) {
        addSeed.onclick = async () => {
          const seed = $("seed-value").value.trim();
          if (seed && await confirmAction(`Attach seed to ${key}?\n\n${seed}\n\nThis writes to SQLite.`)) {
            mutate("/api/seeds", { seed, project_key: key });
          }
        };
      }
      $("hold-promotion").onclick = async () => {
        const reason = $("promotion-hold-reason").value.trim();
        if (!reason) {
          status("Promotion hold reason is required.");
          return;
        }
        if (await confirmAction(`Hold promotion for ${key} for 7 days?\n\nReason: ${reason}\n\nThis hides the project from the promotion queue until the hold expires.`)) {
          mutate(`/api/projects/${encodeURIComponent(key)}/defer-promotion`, { reason, days: 7 });
        }
      };
      const clearPromotionHold = $("clear-promotion-hold");
      if (clearPromotionHold) {
        clearPromotionHold.onclick = async () => {
          if (await confirmAction(`Clear active promotion holds for ${key}?\n\nThis preserves hold history and makes the project eligible for the promotion queue again.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/clear-promotion-deferral`, {});
          }
        };
      }
      const splitProject = $("split-project");
      if (splitProject) {
        splitProject.onclick = async () => {
          const identifierType = $("edit-identifier-type").value;
          const value = $("edit-identifier-value").value.trim();
          const note = $("edit-note").value.trim();
          if (value && await confirmAction(`Split this identifier out of ${key}?\n\n${identifierType}: ${value}\n\nThis creates a new project, removes the identifier from the current project, and records a separation/block.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/split-project`, { identifier_type: identifierType, value, note });
          }
        };
      }
      const blockIdentifier = $("block-identifier");
      if (blockIdentifier) {
        blockIdentifier.onclick = async () => {
          const identifierType = $("edit-identifier-type").value;
          const value = $("edit-identifier-value").value.trim();
          const note = $("edit-note").value.trim();
          if (value && await confirmAction(`Block this identifier on ${key}?\n\n${identifierType}: ${value}\n\nThis removes matching values from the project and records a blocked identifier.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/block-identifier`, { identifier_type: identifierType, value, note });
          }
        };
      }
      const deferIdentifier = $("defer-identifier");
      if (deferIdentifier) {
        deferIdentifier.onclick = async () => {
          const identifierType = $("edit-identifier-type").value;
          const value = $("edit-identifier-value").value.trim();
          const note = $("edit-note").value.trim();
          if (value && await confirmAction(`Defer this identifier on ${key} for 30 days?\n\n${identifierType}: ${value}\n\nConfirmed URLs are downgraded to candidate evidence. Automatic confirmation is suppressed until the defer expires.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/defer-identifier`, { identifier_type: identifierType, value, note, days: 30 });
          }
        };
      }
      const openProjectDetail = $("open-project-detail");
      if (openProjectDetail) {
        openProjectDetail.onclick = () => loadDetail(key, { view: "project", projectNavigation: state.projectNavigation });
      }
      const backToMerge = $("back-to-merge-detail");
      if (backToMerge) {
        backToMerge.onclick = () => loadMergeDetail(state.mergeReturnIndex);
      }
      document.querySelectorAll("[data-dismiss]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Dismiss review signal for ${key}?\n\n${el.dataset.dismiss}\n\nThis stores the signal kind in dismissed_review_kinds.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/dismiss-review-kind`, { kind: el.dataset.dismiss });
          }
        };
      });
      document.querySelectorAll("[data-confirm-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Confirm this ${el.dataset.confirmType} for ${key}?\n\n${el.dataset.confirmValue}\n\nThis moves it into confirmed/manual project data.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/confirm-candidate`, { identifier_type: el.dataset.confirmType, value: el.dataset.confirmValue });
          }
        };
      });
      document.querySelectorAll("[data-reject-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Reject this ${el.dataset.rejectType} for ${key}?\n\n${el.dataset.rejectValue}\n\nThis records a blocked identifier so it should not be reattached automatically.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/reject-candidate`, { identifier_type: el.dataset.rejectType, value: el.dataset.rejectValue });
          }
        };
      });
      document.querySelectorAll("[data-split-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Split this identifier out of ${key}?\n\n${el.dataset.splitType}: ${el.dataset.splitValue}\n\nThis creates a new project, removes the identifier from the current project, and records a separation/block.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/split-project`, { identifier_type: el.dataset.splitType, value: el.dataset.splitValue });
          }
        };
      });
      document.querySelectorAll("[data-block-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Block this identifier on ${key}?\n\n${el.dataset.blockType}: ${el.dataset.blockValue}\n\nThis removes matching values from the project and records a blocked identifier.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/block-identifier`, { identifier_type: el.dataset.blockType, value: el.dataset.blockValue });
          }
        };
      });
      document.querySelectorAll("[data-defer-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Defer this identifier on ${key} for 30 days?\n\n${el.dataset.deferType}: ${el.dataset.deferValue}\n\nConfirmed URLs are downgraded to candidate evidence. Automatic confirmation is suppressed until the defer expires.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/defer-identifier`, { identifier_type: el.dataset.deferType, value: el.dataset.deferValue, days: 30 });
          }
        };
      });
      document.querySelectorAll("[data-clear-block-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Clear this blocked identifier on ${key}?\n\n${el.dataset.clearBlockType}: ${el.dataset.clearBlockValue}\n\nThis removes only the guard record and does not reattach the identifier.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/clear-block-identifier`, { identifier_type: el.dataset.clearBlockType, value: el.dataset.clearBlockValue });
          }
        };
      });
      document.querySelectorAll("[data-clear-defer-type]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Clear this temporary defer on ${key}?\n\n${el.dataset.clearDeferType}: ${el.dataset.clearDeferValue}\n\nThis removes only the defer guard and does not confirm or reattach the identifier.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/clear-defer-identifier`, { identifier_type: el.dataset.clearDeferType, value: el.dataset.clearDeferValue });
          }
        };
      });
      document.querySelectorAll("[data-clear-separate-key]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Clear the separation block between ${key} and ${el.dataset.clearSeparateKey}?\n\nThis may allow future merge recommendations to pair them again.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/clear-separate`, { other_project_key: el.dataset.clearSeparateKey });
          }
        };
      });
      document.querySelectorAll("[data-open-project]").forEach((el) => {
        el.onclick = () => loadDetail(el.dataset.openProject);
      });
      document.querySelectorAll("[data-merge-recommendation]").forEach((el) => {
        el.onclick = async () => {
          if (await confirmAction(`Merge ${key} into recommended target ${el.dataset.mergeRecommendation}?\n\nThis is allowed only when the recommendation is auto-merge-ready and writes to SQLite.`)) {
            mutate(`/api/projects/${encodeURIComponent(key)}/merge-recommendation`, {});
          }
        };
      });
    }

    function confirmAction(message) {
      const modal = $("confirm-modal");
      const messageBox = $("confirm-message");
      const inputLabel = $("confirm-input-label");
      const ok = $("confirm-ok");
      const cancel = $("confirm-cancel");
      inputLabel.hidden = true;
      messageBox.textContent = message;
      modal.hidden = false;
      ok.focus();
      return new Promise((resolve) => {
        const cleanup = (value) => {
          ok.onclick = null;
          cancel.onclick = null;
          modal.onclick = null;
          document.removeEventListener("keydown", onKeydown);
          modal.hidden = true;
          resolve(value);
        };
        const onKeydown = (event) => {
          if (event.key === "Escape") cleanup(false);
          if (event.key === "Enter") cleanup(true);
        };
        ok.onclick = () => cleanup(true);
        cancel.onclick = () => cleanup(false);
        modal.onclick = (event) => {
          if (event.target === modal) cleanup(false);
        };
        document.addEventListener("keydown", onKeydown);
      });
    }

    function requestTextAction(message, initialValue = "") {
      const modal = $("confirm-modal");
      const messageBox = $("confirm-message");
      const inputLabel = $("confirm-input-label");
      const input = $("confirm-input");
      const ok = $("confirm-ok");
      const cancel = $("confirm-cancel");
      messageBox.textContent = message;
      inputLabel.hidden = false;
      input.value = initialValue;
      modal.hidden = false;
      input.focus();
      return new Promise((resolve) => {
        const cleanup = (value) => {
          ok.onclick = null;
          cancel.onclick = null;
          modal.onclick = null;
          document.removeEventListener("keydown", onKeydown);
          modal.hidden = true;
          inputLabel.hidden = true;
          resolve(value);
        };
        const proceed = () => {
          const value = input.value.trim();
          if (!value) {
            input.focus();
            return;
          }
          cleanup(value);
        };
        const onKeydown = (event) => {
          if (event.key === "Escape") cleanup("");
          if (event.key === "Enter") proceed();
        };
        ok.onclick = proceed;
        cancel.onclick = () => cleanup("");
        modal.onclick = (event) => {
          if (event.target === modal) cleanup("");
        };
        document.addEventListener("keydown", onKeydown);
      });
    }

    function enrichJobText(job) {
      return job ? JSON.stringify(job, null, 2) : "No enrich job running.";
    }

    function renderJobsSummaryHtml() {
      const jobs = Array.from(state.jobs.values()).reverse().slice(0, 12);
      if (!jobs.length) return `<span class="muted">No jobs yet.</span>`;
      return `<div class="job-list">` + jobs.map((job) => {
        const logs = asList(job.logs).slice(-8).map((entry) => {
          const bits = [entry.at, entry.message, entry.project_key, entry.error].filter(Boolean).join(" ");
          return `<li>${esc(bits)}</li>`;
        }).join("");
        return `
          <details class="job-item">
            <summary class="job-summary">
              <span class="pill">${esc(job.status || "queued")}</span>
              <span class="pill">${esc(job.kind || "enrich")}</span>
              ${job.project_key ? `<span class="identifier-value">${esc(job.project_key)}</span>` : ""}
              ${job.job_id ? `<span class="muted">${esc(job.job_id)}</span>` : ""}
            </summary>
            ${logs ? `<ul>${logs}</ul>` : `<div class="muted">No logs yet.</div>`}
            <details><summary>Raw JSON</summary><pre>${esc(JSON.stringify(job, null, 2))}</pre></details>
          </details>
        `;
      }).join("") + `</div>`;
    }

    function updateJobStatus(key, job) {
      state.jobs.set(key, job);
      const target = $("enrich-job-status");
      if (state.selected === key && target) target.textContent = enrichJobText(job);
      const summary = $("jobs-summary");
      if (summary) summary.innerHTML = renderJobsSummaryHtml();
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
      updateJobStatus(key, { status: "starting", kind: "enrich", project_key: key, deep_investigation: deep });
      try {
        const data = await api(`/api/projects/${encodeURIComponent(key)}/enrich`, { method: "POST", body: JSON.stringify(enrichPayload(deep)) });
        clearListCache();
        status(`Started job ${data.job_id}`);
        updateJobStatus(key, Object.assign({ kind: "enrich", project_key: key, deep_investigation: deep }, data));
        pollEnrichJob(key, data.job_id);
      } catch (err) {
        status(err.message);
        updateJobStatus(key, { status: "failed_to_start", kind: "enrich", project_key: key, error: err.message });
      }
    }

    async function pollEnrichJob(key, jobId) {
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, 1200));
        try {
          const job = await api(`/api/jobs/${encodeURIComponent(jobId)}`);
          updateJobStatus(key, job);
          if (job.status === "done") {
            status("Enrich done.");
            if (state.selected === key) await loadDetail(key, { view: state.detailView, projectNavigation: state.projectNavigation, mergeReturnIndex: state.mergeReturnIndex });
            return;
          }
          if (job.status === "failed") {
            status(`Enrich failed: ${job.error || ""}`);
            return;
          }
        } catch (err) {
          status(err.message);
          updateJobStatus(key, { status: "poll_failed", kind: "enrich", project_key: key, job_id: jobId, error: err.message });
          return;
        }
      }
    }

    async function mutate(path, payload, reloadDetail = true) {
      status("Saving...");
      try {
        const data = await api(path, { method: "POST", body: JSON.stringify(payload || {}) });
        clearListCache();
        status(data.job_id ? `Started job ${data.job_id}` : "Saved.");
        await loadList({ force: true });
        if (reloadDetail && state.selected) await loadDetail(state.selected, { view: state.detailView, projectNavigation: state.projectNavigation, mergeReturnIndex: state.mergeReturnIndex });
      } catch (err) {
        status(err.message);
      }
    }

    $("list").onclick = async (event) => {
      const holdPromotion = event.target.closest("[data-hold-promotion]");
      if (holdPromotion) {
        const key = holdPromotion.dataset.holdPromotion;
        const reason = await requestTextAction(`Hold promotion for ${key} for 7 days?\n\nThis records a reason and temporarily hides the project from the promotion queue.`, holdPromotion.dataset.holdReason || "");
        if (reason) mutate(`/api/projects/${encodeURIComponent(key)}/defer-promotion`, { reason, days: 7 }, false);
        return;
      }
      const button = event.target.closest("[data-key]");
      if (button) loadDetail(button.dataset.key, { view: state.tab === "review" ? "review" : "project", projectNavigation: true });
      const mergeButton = event.target.closest("[data-merge-index]");
      if (mergeButton) loadMergeDetail(Number(mergeButton.dataset.mergeIndex));
    };
    $("tab-review").onclick = () => setTab("review");
    $("tab-review-ops").onclick = () => setTab("review-ops");
    $("tab-promotion").onclick = () => setTab("promotion");
    $("tab-merge").onclick = () => setTab("merge");
    $("tab-sync").onclick = () => setTab("sync");
    $("tab-jobs").onclick = () => setTab("jobs");
    $("tab-search").onclick = () => setTab("search");
    $("tab-new-seed").onclick = () => setTab("new-seed");
    $("tab-settings").onclick = () => setTab("settings");
    $("refresh").onclick = () => {
      clearListCache();
      loadList({ force: true });
    };
    $("merge-mode").value = state.settings.mergeMode || "moderate";
    $("merge-mode").onchange = () => {
      saveSettings(Object.assign({}, state.settings, { mergeMode: $("merge-mode").value || "moderate" }));
      if (state.tab === "merge") loadList();
    };
    $("query").onkeydown = (event) => { if (event.key === "Enter") runSearch(); };
    $("query").style.display = "none";
    $("merge-mode").style.display = "none";
    if (initialSeedParam) {
      setTab("new-seed");
    } else {
      loadList();
    }
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


def parse_csv_set(values: list[str]) -> set[str]:
    items: set[str] = set()
    for value in values:
        for item in value.replace(",", " ").split():
            normalized = item.strip()
            if normalized:
                items.add(normalized)
    return items


def parse_choice(value: str, *, allowed: set[str], default: str = "any") -> str:
    normalized = str(value or default).strip().lower()
    if normalized not in allowed:
        raise ValueError(f"invalid filter value: {normalized}")
    return normalized


def parse_iso_date(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    try:
        return dt.date.fromisoformat(normalized).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid ISO date: {normalized}") from exc


def project_has_candidates(project: dict[str, Any]) -> bool:
    return any(
        registry.clean_string_list(project.get(field, []))
        for field in ("service_url_candidates", "website_url_candidates", "candidate_source_code_urls")
    )


def project_matches_search_filters(
    project: dict[str, Any],
    *,
    tracking_states: set[str],
    service_statuses: set[str],
    statuses: set[str],
    categories: set[str],
    introduced: str,
    has_candidates: str,
    has_errors: str,
    seen_since: str,
    enriched_before: str,
) -> bool:
    shaped = registry.ensure_project_shape(project)
    if tracking_states and str(shaped.get("tracking_state") or "") not in tracking_states:
        return False
    if service_statuses and str(shaped.get("service_status") or "") not in service_statuses:
        return False
    if statuses and str(shaped.get("status") or "") not in statuses:
        return False
    if categories and not categories.intersection({
        str(shaped.get("category") or ""),
        str(shaped.get("app_category") or ""),
    }):
        return False
    is_introduced = bool(str(shaped.get("introduced_url") or "").strip())
    if introduced != "any" and is_introduced != (introduced == "yes"):
        return False
    has_pending_candidates = project_has_candidates(shaped)
    if has_candidates != "any" and has_pending_candidates != (has_candidates == "yes"):
        return False
    has_run_errors = bool(shaped.get("last_run_errors"))
    if has_errors != "any" and has_run_errors != (has_errors == "yes"):
        return False
    if seen_since and str(shaped.get("last_seen_at") or "")[:10] < seen_since:
        return False
    last_enriched = str(shaped.get("last_enriched_at") or "")[:10]
    if enriched_before and last_enriched and last_enriched >= enriched_before:
        return False
    return True


def search_projects_payload(conn: sqlite3.Connection, query: dict[str, list[str]]) -> dict[str, Any]:
    limit = parse_int(query.get("limit", ["50"])[0], 50)
    search = str(query.get("q", [""])[0] or "").strip()
    tracking_states = parse_state_set(query.get("tracking_states", []), set())
    service_statuses = parse_csv_set(query.get("service_statuses", []))
    statuses = parse_csv_set(query.get("statuses", []))
    categories = parse_csv_set(query.get("categories", []))
    review_kinds = parse_csv_set(query.get("review_kinds", []))
    introduced = parse_choice(query.get("introduced", ["any"])[0], allowed={"yes", "no", "any"})
    has_candidates = parse_choice(query.get("has_candidates", ["any"])[0], allowed={"yes", "no", "any"})
    has_errors = parse_choice(query.get("has_errors", ["any"])[0], allowed={"yes", "no", "any"})
    seen_since = parse_iso_date(query.get("seen_since", [""])[0])
    enriched_before = parse_iso_date(query.get("enriched_before", [""])[0])
    default_sort = "relevance" if search else "last_seen_desc"
    sort = parse_choice(
        query.get("sort", [default_sort])[0],
        allowed={"relevance", "last_seen_desc", "last_enriched_asc", "name_asc"},
        default=default_sort,
    )
    projects = [
        registry.ensure_project_shape(project)
        for project in registry.list_projects(conn)
        if project_matches_search_filters(
            project,
            tracking_states=tracking_states,
            service_statuses=service_statuses,
            statuses=statuses,
            categories=categories,
            introduced=introduced,
            has_candidates=has_candidates,
            has_errors=has_errors,
            seen_since=seen_since,
            enriched_before=enriched_before,
        )
    ]
    if review_kinds:
        projects = [
            project
            for project in projects
            if registry.build_review_queue_item(conn, str(project.get("project_key") or ""), kinds=review_kinds)
        ]
    by_key = {str(project.get("project_key") or ""): project for project in projects}
    if search:
        ranked = registry.project_search_candidates(conn, search, limit=max(1, len(projects)), projects=projects)
        items = []
        for candidate in ranked:
            item = project_summary(by_key[candidate["project_key"]])
            item.update({"score": candidate.get("score", 0), "reasons": candidate.get("reasons", [])})
            items.append(item)
    else:
        items = [project_summary(project) for project in projects]
    if sort == "last_seen_desc":
        items.sort(key=lambda item: str(item.get("project_key") or ""))
        items.sort(key=lambda item: str(item.get("last_seen_at") or ""), reverse=True)
    elif sort == "last_enriched_asc":
        items.sort(key=lambda item: (str(item.get("last_enriched_at") or ""), str(item.get("project_key") or "")))
    elif sort == "name_asc":
        items.sort(key=lambda item: (str(item.get("name") or "").lower(), str(item.get("project_key") or "")))
    return {
        "items": items[:limit],
        "count": len(items),
        "returned_count": min(limit, len(items)),
        "q": search,
        "sort": sort,
    }


def llm_source_summary(project: dict[str, Any]) -> dict[str, Any]:
    source = project.get("llm_source")
    if not isinstance(source, dict):
        return {}
    return {
        key: source.get(key)
        for key in (
            "transport",
            "api_style",
            "requested_api_style",
            "resolved_endpoint",
            "model",
            "reasoning_effort",
            "prompt_version",
            "generated_at",
        )
        if source.get(key) not in {None, ""}
    }


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
        "last_run_errors": project.get("last_run_errors", []),
        "llm_source": llm_source_summary(project),
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


def clear_block_identifier_on_project(
    conn: sqlite3.Connection,
    project_key: str,
    identifier_type: str,
    value: str,
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
    project["blocked_identifiers"] = [
        item for item in registry.normalize_blocked_identifiers(list(project.get("blocked_identifiers", [])))
        if (str(item.get("type") or ""), str(item.get("value") or "")) != (identifier_type, normalized_value)
    ]
    project = registry.finalize_project_shape(project)
    project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = registry.compute_merge_recommendation(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def defer_identifier_on_project(
    conn: sqlite3.Connection,
    project_key: str,
    identifier_type: str,
    value: str,
    *,
    note: str = "",
    days: int = registry.DEFAULT_DEFER_IDENTIFIER_DAYS,
) -> dict[str, Any]:
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    registry.defer_identifier_on_project(project, identifier_type, value, note=note, days=days)
    project = registry.finalize_project_shape(project)
    project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = registry.compute_merge_recommendation(project)
    project = registry.apply_promotion_state(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def clear_deferred_identifier_on_project(
    conn: sqlite3.Connection,
    project_key: str,
    identifier_type: str,
    value: str,
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
    project["deferred_identifiers"] = [
        item for item in registry.normalize_deferred_identifiers(list(project.get("deferred_identifiers", [])))
        if (str(item.get("type") or ""), str(item.get("value") or "")) != (identifier_type, normalized_value)
    ]
    project["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, project)
    project["merge_recommendation"] = registry.compute_merge_recommendation(project)
    project = registry.apply_promotion_state(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def defer_promotion_on_project(
    conn: sqlite3.Connection,
    project_key: str,
    *,
    reason: str = "",
    days: int = registry.DEFAULT_PROMOTION_DEFER_DAYS,
) -> tuple[dict[str, Any], dict[str, str]]:
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    entry = registry.defer_promotion_on_project(project, reason=reason, days=days)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project, entry


def clear_promotion_deferrals_on_project(conn: sqlite3.Connection, project_key: str) -> dict[str, Any]:
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if project is None:
        raise KeyError(project_key)
    project = registry.ensure_project_shape(project)
    registry.clear_active_promotion_deferrals(project)
    registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
    conn.commit()
    return project


def clear_separate_project_pair(conn: sqlite3.Connection, project_key: str, other_project_key: str) -> dict[str, Any]:
    project = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    other = registry.fetch_project(conn, registry.resolve_project_key_input(conn, other_project_key))
    if project is None or other is None:
        raise KeyError(project_key if project is None else other_project_key)
    project = registry.ensure_project_shape(project)
    other = registry.ensure_project_shape(other)
    if project["project_key"] == other["project_key"]:
        raise ValueError("project and other project resolve to the same project")
    project["separate_project_keys"] = sorted(set(project.get("separate_project_keys", [])) - {other["project_key"]})
    other["separate_project_keys"] = sorted(set(other.get("separate_project_keys", [])) - {project["project_key"]})
    for item in (project, other):
        item["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, item)
        item["merge_recommendation"] = registry.compute_merge_recommendation(item)
        registry.save_project(conn, item, enriched=bool(item.get("last_enriched_at")))
    conn.commit()
    return {
        "mode": "clear",
        "project": project_summary(project),
        "other": project_summary(other),
        "project_separate_project_keys": project.get("separate_project_keys", []),
        "other_separate_project_keys": other.get("separate_project_keys", []),
    }


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


def merge_project_recommendation(conn: sqlite3.Connection, project_key: str) -> dict[str, Any]:
    source = registry.fetch_project(conn, registry.resolve_project_key_input(conn, project_key))
    if source is None:
        raise KeyError(project_key)
    source = registry.ensure_project_shape(source)
    source["duplicate_candidates"] = registry.compute_duplicate_candidates(conn, source)
    source["merge_recommendation"] = registry.compute_merge_recommendation(source)
    recommendation = source.get("merge_recommendation") or {}
    target_key = str(recommendation.get("target_project_key") or "")
    if not target_key:
        raise ValueError("no merge recommendation available for source project")
    if not recommendation.get("auto_merge_ready"):
        raise ValueError("merge recommendation is not auto-merge-ready")
    target = registry.fetch_project(conn, registry.resolve_project_key_input(conn, target_key))
    if target is None:
        raise KeyError(target_key)
    target = registry.ensure_project_shape(target)
    if source["project_key"] == target["project_key"]:
        raise ValueError("source and target resolve to the same project")
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
        "used_recommendation": True,
        "merge_recommendation": recommendation,
        "merged": project_summary(merged),
    }


def public_settings_payload(token_configured: bool) -> dict[str, Any]:
    config = registry.load_config()
    github_cfg = config.get("github_repo_search", {}) if isinstance(config.get("github_repo_search", {}), dict) else {}
    return {
        "pid": os.getpid(),
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


def seed_preview_payload(conn: sqlite3.Connection, seed: str, seed_type_override: str = "") -> dict[str, Any]:
    seed_type = registry.detect_seed_type(seed, seed_type_override or None)
    seed_value = registry.canonical_seed_value(seed_type, seed)
    if not seed_value:
        raise ValueError("seed value is required")
    existing_key = registry.find_project_key_by_identifier(conn, seed_type, seed_value)
    existing_project = registry.fetch_project(conn, existing_key) if existing_key else None
    possible_matches = registry.project_search_candidates(conn, seed_value, limit=8)
    if existing_key:
        possible_matches = [item for item in possible_matches if item.get("project_key") != existing_key]
    return {
        "seed": seed,
        "seed_type": seed_type,
        "seed_value": seed_value,
        "existing_project": project_summary(existing_project) if existing_project else None,
        "possible_matches": possible_matches,
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
        "create_unmatched": bool(payload.get("create_unmatched", True)),
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


def enrich_all_payload_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    states = {"active", "watchlist"}
    if bool(payload.get("include_stable")):
        states.add("stable")
    if bool(payload.get("include_stalled")):
        states.add("stalled")
    if bool(payload.get("include_archived")):
        states.add("archived")
    if bool(payload.get("include_ignore")):
        states.add("ignore")
    requested_states = payload.get("tracking_states")
    if isinstance(requested_states, list) and requested_states:
        cleaned = {registry.normalize_tracking_state(str(item or "")) for item in requested_states}
        states = {state for state in cleaned if state in registry.TRACKING_STATES} or states
    return {
        "all_tracking_states": bool(payload.get("all_tracking_states")),
        "tracking_states": None if bool(payload.get("all_tracking_states")) else sorted(states),
        "limit": max(1, min(500, int(payload.get("limit") or 25))),
        "new_only": bool(payload.get("new_only")),
        "retry_failures": bool(payload.get("retry_failures")),
        "deep_investigation": bool(payload.get("deep_investigation")),
        **llm_options_from_payload(payload),
    }


def select_enrich_all_projects(conn: sqlite3.Connection, payload: dict[str, Any]) -> list[dict[str, Any]]:
    tracking_states = None if payload.get("all_tracking_states") else set(payload.get("tracking_states") or [])
    projects = registry.list_projects(conn, tracking_states=tracking_states or None)
    if payload.get("new_only"):
        projects = [project for project in projects if not str(project.get("last_enriched_at") or "").strip()]
    if payload.get("retry_failures"):
        projects = [project for project in projects if project.get("last_run_errors")]
    projects.sort(key=registry.enrich_project_sort_key)
    return projects[: int(payload.get("limit") or 25)]


def review_stalled_payload_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "stalled",
        "execute": bool(payload.get("execute")),
        "limit": max(1, min(500, int(payload.get("limit") or 100))),
        "inactivity_days": max(1, min(3650, int(payload.get("inactivity_days") or 45))),
        "enrich_age_days": max(1, min(3650, int(payload.get("enrich_age_days") or 30))),
        "note": str(payload.get("note") or "").strip(),
    }


def review_ignore_payload_from_request(payload: dict[str, Any]) -> dict[str, Any]:
    tracking_states = {"active", "watchlist"}
    if bool(payload.get("include_stable", True)):
        tracking_states.add("stable")
    if bool(payload.get("include_stalled", True)):
        tracking_states.add("stalled")
    requested_states = payload.get("tracking_states")
    if isinstance(requested_states, list) and requested_states:
        cleaned = {registry.normalize_tracking_state(str(item or "")) for item in requested_states}
        tracking_states = {state for state in cleaned if state in registry.TRACKING_STATES} or tracking_states
    note_reason = str(payload.get("note_reason") or "").strip()
    if note_reason and note_reason not in registry.IGNORE_REVIEW_NOTE_REASONS:
        raise ValueError("invalid note_reason")
    return {
        "kind": "ignore",
        "execute": bool(payload.get("execute")),
        "limit": max(1, min(500, int(payload.get("limit") or 100))),
        "tracking_states": sorted(tracking_states),
        "note_reason": note_reason,
        "note": str(payload.get("note") or "").strip(),
    }


def run_review_stalled_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, log: Any | None = None) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    inactivity_days = int(payload.get("inactivity_days") or 45)
    enrich_age_days = int(payload.get("enrich_age_days") or 30)
    project_list = [
        registry.ensure_project_shape(project)
        for project in registry.list_projects(conn, tracking_states={"active", "watchlist", "stalled"})
    ]
    candidates = [
        entry
        for project in project_list
        if (entry := registry.stalled_review_entry(project, now=now, inactivity_days=inactivity_days, enrich_age_days=enrich_age_days))
    ]
    candidates.sort(key=lambda item: (str(item.get("suggested_tracking_state") or "") != "active", str(item.get("project_key") or "")))
    total_candidates = len(candidates)
    candidates = candidates[: int(payload.get("limit") or 100)]
    suggested_state_counts: dict[str, int] = {}
    for item in candidates:
        state = str(item.get("suggested_tracking_state") or "")
        suggested_state_counts[state] = int(suggested_state_counts.get(state, 0)) + 1
    if log:
        log("review-stalled candidates selected", candidate_count=len(candidates), total_candidates=total_candidates)

    if not payload.get("execute"):
        return {
            "mode": "dry_run",
            "inactivity_days": inactivity_days,
            "enrich_age_days": enrich_age_days,
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidates,
            "suggested_state_counts": suggested_state_counts,
            "candidates": candidates,
        }

    updated: list[str] = []
    updated_with_state: list[dict[str, str]] = []
    note = str(payload.get("note") or f"auto-updated after review-stalled inactivity_days={inactivity_days} enrich_age_days={enrich_age_days}")
    for item in candidates:
        project_key = str(item.get("project_key") or "")
        project = registry.fetch_project(conn, project_key)
        if not project:
            continue
        project = registry.ensure_project_shape(project)
        project["tracking_state"] = str(item.get("suggested_tracking_state") or project.get("tracking_state") or "active")
        if note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = note
        registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(project["project_key"])
        updated_with_state.append({"project_key": project["project_key"], "tracking_state": str(project.get("tracking_state") or "")})
        if log:
            log("updated stalled review candidate", project_key=project["project_key"], tracking_state=str(project.get("tracking_state") or ""))
    conn.commit()
    return {
        "mode": "execute",
        "inactivity_days": inactivity_days,
        "enrich_age_days": enrich_age_days,
        "updated_count": len(updated),
        "updated_project_keys": updated,
        "updated_projects": updated_with_state,
        "suggested_state_counts": suggested_state_counts,
    }


def run_review_ignore_payload(conn: sqlite3.Connection, payload: dict[str, Any], *, log: Any | None = None) -> dict[str, Any]:
    tracking_states = set(payload.get("tracking_states") or ["active", "watchlist", "stable", "stalled"])
    project_list = [registry.ensure_project_shape(project) for project in registry.list_projects(conn, tracking_states=tracking_states)]
    candidates = [entry for project in project_list if (entry := registry.ignore_review_entry(project))]
    candidates.sort(key=lambda item: (str(item.get("kind") or ""), str(item.get("project_key") or "")))
    total_candidates = len(candidates)
    candidates = candidates[: int(payload.get("limit") or 100)]
    kind_counts: dict[str, int] = {}
    for item in candidates:
        kind = str(item.get("kind") or "")
        kind_counts[kind] = int(kind_counts.get(kind, 0)) + 1
    if log:
        log("review-ignore candidates selected", candidate_count=len(candidates), total_candidates=total_candidates)

    if not payload.get("execute"):
        return {
            "mode": "dry_run",
            "tracking_states": sorted(tracking_states),
            "candidate_count": len(candidates),
            "total_candidate_count": total_candidates,
            "kind_counts": kind_counts,
            "candidates": candidates,
        }

    updated: list[str] = []
    noted_project_keys: list[dict[str, str]] = []
    static_note = str(payload.get("note") or "").strip()
    default_reason_key = str(payload.get("note_reason") or "").strip()
    for item in candidates:
        project_key = str(item.get("project_key") or "")
        project = registry.fetch_project(conn, project_key)
        if not project:
            continue
        project = registry.ensure_project_shape(project)
        project["tracking_state"] = "ignore"
        reason_key = default_reason_key or registry.default_ignore_review_note_reason(item)
        note = static_note or registry.build_ignore_review_note(item, reason_key)
        if static_note and default_reason_key:
            note = registry.build_ignore_review_note(item, reason_key, static_note)
        if note and not str(project.get("tracking_note") or "").strip():
            project["tracking_note"] = note
        registry.save_project(conn, project, enriched=bool(project.get("last_enriched_at")))
        updated.append(project["project_key"])
        noted_project_keys.append({"project_key": project["project_key"], "tracking_note": str(project.get("tracking_note") or "")})
        if log:
            log("updated ignored review candidate", project_key=project["project_key"], kind=str(item.get("kind") or ""))
    conn.commit()
    return {
        "mode": "execute",
        "tracking_states": sorted(tracking_states),
        "updated_count": len(updated),
        "updated_project_keys": updated,
        "updated_tracking_notes": noted_project_keys,
        "kind_counts": kind_counts,
    }


class ProjectRegistryWebServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], token: str):
        super().__init__(server_address, ProjectRegistryHandler)
        self.token = token
        self.jobs: dict[str, dict[str, Any]] = {}
        self.jobs_lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="registry-web")

    def request_process_reload(self, reason: str) -> str:
        reload_id = uuid.uuid4().hex

        def reload_soon() -> None:
            time.sleep(0.25)
            print(f"Reloading project registry web server ({reload_id}): {reason}", flush=True)
            os.execv(sys.executable, [sys.executable, *sys.argv])

        threading.Thread(target=reload_soon, name="registry-web-manual-reload", daemon=True).start()
        return reload_id

    def request_process_shutdown(self, reason: str) -> str:
        shutdown_id = uuid.uuid4().hex

        def shutdown_soon() -> None:
            time.sleep(0.25)
            print(f"Stopping project registry web server ({shutdown_id}): {reason}", flush=True)
            self.shutdown()

        threading.Thread(target=shutdown_soon, name="registry-web-manual-shutdown", daemon=True).start()
        return shutdown_id

    def append_job_log(self, job_id: str, message: str, **fields: Any) -> None:
        entry = {"at": registry.iso_now(), "message": message, **fields}
        with self.jobs_lock:
            job = self.jobs.get(job_id)
            if not job:
                return
            logs = list(job.get("logs") or [])
            logs.append(entry)
            job["logs"] = logs[-200:]

    def start_enrich_job(self, project_key: str, *, deep_investigation: bool, llm_options: dict[str, str | None] | None = None) -> str:
        job_id = uuid.uuid4().hex
        llm_options = dict(llm_options or {})
        effective_llm = {
            "command_set": bool(os.getenv("ATPROTO_SURVEY_LLM_COMMAND", "").strip()),
            "env_endpoint_set": bool(os.getenv("ATPROTO_SURVEY_LLM_ENDPOINT", "").strip()),
            "env_model": os.getenv("ATPROTO_SURVEY_LLM_MODEL", "").strip(),
            "env_api_style": os.getenv("ATPROTO_SURVEY_LLM_API_STYLE", "auto").strip() or "auto",
            "payload_endpoint_set": bool(llm_options.get("llm_endpoint")),
            "payload_model": llm_options.get("llm_model") or "",
            "payload_api_style": llm_options.get("llm_api_style") or "",
            "payload_reasoning_effort": llm_options.get("llm_reasoning_effort") or "",
        }
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "enrich",
                "project_key": project_key,
                "deep_investigation": deep_investigation,
                "llm_options": {key: ("***" if key == "llm_api_key" and value else value) for key, value in llm_options.items()},
                "effective_llm": effective_llm,
                "created_at": registry.iso_now(),
                "logs": [],
            }
        self.append_job_log(job_id, "queued enrich job", project_key=project_key, deep_investigation=deep_investigation, **effective_llm)
        self.executor.submit(self._run_enrich_job, job_id, project_key, deep_investigation, llm_options)
        return job_id

    def _run_enrich_job(self, job_id: str, project_key: str, deep_investigation: bool, llm_options: dict[str, str | None] | None = None) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        self.append_job_log(job_id, "started enrich job")
        try:
            conn = open_registry_db()
            try:
                resolved_key = registry.resolve_project_key_input(conn, project_key)
                project = registry.fetch_project(conn, resolved_key)
                if project is None:
                    raise KeyError(project_key)
                self.append_job_log(job_id, "loaded project", project_key=resolved_key, name=str(project.get("name") or ""))
                enriched = registry.enrich_project(
                    conn,
                    project,
                    deep_investigation=deep_investigation,
                    **dict(llm_options or {}),
                )
                registry.save_project(conn, enriched, enriched=True)
                conn.commit()
                result = project_summary(enriched)
                llm_source = enriched.get("llm_source") if isinstance(enriched.get("llm_source"), dict) else {}
                self.append_job_log(
                    job_id,
                    "saved enriched project",
                    project_key=str(enriched.get("project_key") or resolved_key),
                    llm_transport=str(llm_source.get("transport") or ""),
                    llm_api_style=str(llm_source.get("api_style") or ""),
                    llm_resolved_endpoint=str(llm_source.get("resolved_endpoint") or ""),
                    last_run_error_count=len(enriched.get("last_run_errors", []) or []),
                )
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
            self.append_job_log(job_id, "enrich job failed", error=str(exc))
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "finished_at": registry.iso_now(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    }
                )

    def start_enrich_all_job(self, payload: dict[str, Any]) -> str:
        enrich_payload = enrich_all_payload_from_request(payload)
        job_id = uuid.uuid4().hex
        public_payload = {
            key: ("***" if key == "llm_api_key" and value else value)
            for key, value in enrich_payload.items()
        }
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "kind": "enrich_all",
                "payload": public_payload,
                "created_at": registry.iso_now(),
                "logs": [],
            }
        self.append_job_log(job_id, "queued enrich-all job", limit=int(enrich_payload.get("limit") or 25))
        self.executor.submit(self._run_enrich_all_job, job_id, enrich_payload)
        return job_id

    def _run_enrich_all_job(self, job_id: str, payload: dict[str, Any]) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        self.append_job_log(job_id, "started enrich-all job")
        completed: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        promoted_now: list[dict[str, Any]] = []
        try:
            conn = open_registry_db()
            try:
                projects = select_enrich_all_projects(conn, payload)
                total = len(projects)
                with self.jobs_lock:
                    self.jobs[job_id]["total"] = total
                    self.jobs[job_id]["completed_count"] = 0
                    self.jobs[job_id]["failed_count"] = 0
                self.append_job_log(job_id, "selected projects", total=total)
                for index, project in enumerate(projects, start=1):
                    project_key = str(project.get("project_key") or "")
                    with self.jobs_lock:
                        self.jobs[job_id]["current"] = {
                            "index": index,
                            "total": total,
                            "project_key": project_key,
                            "name": str(project.get("name") or ""),
                            "started_at": registry.iso_now(),
                        }
                    self.append_job_log(job_id, "enriching project", index=index, total=total, project_key=project_key)
                    try:
                        enriched = registry.enrich_project(
                            conn,
                            project,
                            deep_investigation=bool(payload.get("deep_investigation")),
                            llm_endpoint=payload.get("llm_endpoint"),
                            llm_model=payload.get("llm_model"),
                            llm_api_key=payload.get("llm_api_key"),
                            llm_api_style=payload.get("llm_api_style"),
                            llm_reasoning_effort=payload.get("llm_reasoning_effort"),
                        )
                        registry.save_project(conn, enriched, enriched=True)
                        conn.commit()
                        item = {
                            "project_key": enriched.get("project_key", project_key),
                            "name": enriched.get("name", ""),
                            "status": enriched.get("status", ""),
                            "tracking_state": enriched.get("tracking_state", ""),
                            "service_status": enriched.get("service_status", ""),
                            "last_run_errors": enriched.get("last_run_errors", []),
                            "promoted_now": bool(enriched.get("promoted_now")),
                        }
                        completed.append(item)
                        if enriched.get("promoted_now"):
                            promoted_now.append(project_summary(enriched))
                        self.append_job_log(job_id, "enriched project", project_key=project_key)
                    except Exception as exc:
                        conn.rollback()
                        failed.append({"project_key": project_key, "name": str(project.get("name") or ""), "error": str(exc)})
                        self.append_job_log(job_id, "project enrich failed", project_key=project_key, error=str(exc))
                    with self.jobs_lock:
                        self.jobs[job_id]["completed_count"] = len(completed)
                        self.jobs[job_id]["failed_count"] = len(failed)
                        self.jobs[job_id]["recent_completed"] = completed[-20:]
                        self.jobs[job_id]["recent_failed"] = failed[-20:]
            finally:
                conn.close()
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "finished_at": registry.iso_now(),
                        "result": {
                            "mode": "execute",
                            "project_count": len(completed) + len(failed),
                            "completed_count": len(completed),
                            "failed_count": len(failed),
                            "promoted_now_count": len(promoted_now),
                            "promoted_now": promoted_now[-50:],
                            "completed": completed[-100:],
                            "failed": failed[-100:],
                        },
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised via HTTP behavior
            self.append_job_log(job_id, "enrich-all job failed", error=str(exc))
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "finished_at": registry.iso_now(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                        "completed_count": len(completed),
                        "failed_count": len(failed),
                        "recent_completed": completed[-20:],
                        "recent_failed": failed[-20:],
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
                "logs": [],
            }
        self.append_job_log(job_id, "queued sync job", sync_kind=sync_payload["kind"], execute=bool(sync_payload.get("execute")))
        self.executor.submit(self._run_sync_job, job_id, sync_payload)
        return job_id

    def _run_sync_job(self, job_id: str, payload: dict[str, Any]) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        self.append_job_log(job_id, "started sync job", sync_kind=str(payload.get("kind") or ""))
        try:
            conn = open_registry_db()
            try:
                result = run_sync_payload(conn, payload)
            finally:
                conn.close()
            self.append_job_log(job_id, "sync job finished", mode=str(result.get("mode") or ""), sync_kind=str(payload.get("kind") or ""))
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "finished_at": registry.iso_now(),
                        "result": result,
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised via HTTP behavior
            self.append_job_log(job_id, "sync job failed", error=str(exc))
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "failed",
                        "finished_at": registry.iso_now(),
                        "error": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    }
                )

    def start_review_job(self, review_kind: str, payload: dict[str, Any]) -> str:
        if review_kind == "stalled":
            review_payload = review_stalled_payload_from_request(payload)
        elif review_kind == "ignore":
            review_payload = review_ignore_payload_from_request(payload)
        else:
            raise ValueError("review_kind must be stalled or ignore")
        job_id = uuid.uuid4().hex
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "kind": f"review_{review_kind}",
                "execute": bool(review_payload.get("execute")),
                "payload": review_payload,
                "created_at": registry.iso_now(),
                "logs": [],
            }
        self.append_job_log(job_id, f"queued review-{review_kind} job", execute=bool(review_payload.get("execute")))
        self.executor.submit(self._run_review_job, job_id, review_kind, review_payload)
        return job_id

    def _run_review_job(self, job_id: str, review_kind: str, payload: dict[str, Any]) -> None:
        with self.jobs_lock:
            self.jobs[job_id]["status"] = "running"
            self.jobs[job_id]["started_at"] = registry.iso_now()
        self.append_job_log(job_id, f"started review-{review_kind} job")
        try:
            conn = open_registry_db()
            try:
                if review_kind == "stalled":
                    result = run_review_stalled_payload(conn, payload, log=lambda message, **fields: self.append_job_log(job_id, message, **fields))
                elif review_kind == "ignore":
                    result = run_review_ignore_payload(conn, payload, log=lambda message, **fields: self.append_job_log(job_id, message, **fields))
                else:
                    raise ValueError("review_kind must be stalled or ignore")
            finally:
                conn.close()
            self.append_job_log(job_id, f"review-{review_kind} job finished", mode=str(result.get("mode") or ""))
            with self.jobs_lock:
                self.jobs[job_id].update(
                    {
                        "status": "done",
                        "finished_at": registry.iso_now(),
                        "result": result,
                    }
                )
        except Exception as exc:  # pragma: no cover - exercised via HTTP behavior
            self.append_job_log(job_id, f"review-{review_kind} job failed", error=str(exc))
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
        if path == "/api/seeds/preview":
            seed = str(query.get("seed", [""])[0] or "").strip()
            if not seed:
                raise ValueError("seed is required")
            seed_type = str(query.get("type", [""])[0] or "").strip()
            conn = open_registry_db()
            try:
                self.send_json(seed_preview_payload(conn, seed, seed_type))
            finally:
                conn.close()
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
            conn = open_registry_db()
            try:
                self.send_json(search_projects_payload(conn, query))
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
        if path == "/api/enrich-all":
            job_id = self.server.start_enrich_all_job(payload)
            self.send_json({"job_id": job_id, "status": "queued"}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/reload":
            if payload.get("confirm") is not True:
                raise ValueError("confirm=true is required")
            reload_id = self.server.request_process_reload("manual API request")
            self.send_json({"reload_id": reload_id, "status": "reloading"}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/shutdown":
            if payload.get("confirm") is not True:
                raise ValueError("confirm=true is required")
            shutdown_id = self.server.request_process_shutdown("manual API request")
            self.send_json({"shutdown_id": shutdown_id, "status": "stopping"}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/review-stalled":
            job_id = self.server.start_review_job("stalled", payload)
            self.send_json({"job_id": job_id, "status": "queued"}, status=HTTPStatus.ACCEPTED)
            return
        if path == "/api/review-ignore":
            job_id = self.server.start_review_job("ignore", payload)
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
            if action == "clear-block-identifier":
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                project = clear_block_identifier_on_project(conn, project_key, identifier_type, value)
                self.send_json({"project": project_summary(project), "cleared": {"type": identifier_type, "value": value}})
                return
            if action == "defer-identifier":
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                project = defer_identifier_on_project(
                    conn,
                    project_key,
                    identifier_type,
                    value,
                    note=str(payload.get("note") or "").strip(),
                    days=parse_int(str(payload.get("days") or "30"), 30, minimum=1, maximum=365),
                )
                self.send_json({"project": project_summary(project), "deferred": {"type": identifier_type, "value": value}})
                return
            if action == "clear-defer-identifier":
                identifier_type = str(payload.get("identifier_type") or "").strip()
                value = str(payload.get("value") or "").strip()
                project = clear_deferred_identifier_on_project(conn, project_key, identifier_type, value)
                self.send_json({"project": project_summary(project), "cleared_defer": {"type": identifier_type, "value": value}})
                return
            if action == "defer-promotion":
                project, entry = defer_promotion_on_project(
                    conn,
                    project_key,
                    reason=str(payload.get("reason") or "").strip(),
                    days=parse_int(str(payload.get("days") or "7"), 7, minimum=1, maximum=365),
                )
                self.send_json({"project": project_summary(project), "promotion_deferral": entry})
                return
            if action == "clear-promotion-deferral":
                project = clear_promotion_deferrals_on_project(conn, project_key)
                self.send_json({"project": project_summary(project), "active_promotion_deferral": registry.active_promotion_deferral(project)})
                return
            if action == "clear-separate":
                other_key = str(payload.get("other_project_key") or "").strip()
                if not other_key:
                    raise ValueError("other_project_key is required")
                self.send_json(clear_separate_project_pair(conn, project_key, other_key))
                return
            if action == "merge-recommendation":
                self.send_json(merge_project_recommendation(conn, project_key))
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
