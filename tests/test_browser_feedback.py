from __future__ import annotations

import json
import shutil
import subprocess
import threading
import types
import unittest
import urllib.parse

from helpers import ROOT, TempRegistryDbTestCase, registry
from test_project_registry_web import _ProjectRegistryWebFixtures, FakeJobManager
from project_registry_core import operator_feedback, project_record
from project_registry_core.application import browser_entry, feedback, merge, presentation


class BrowserFeedbackTest(_ProjectRegistryWebFixtures, TempRegistryDbTestCase):
    key = "proj:111111111111"

    def setUp(self):
        super().setUp()
        self.seed_fixture()
        self.server = types.SimpleNamespace(token=self.token, db_path=self.db_path,
            open_db=lambda: registry.open_db(), jobs=FakeJobManager(), mutation_lock=threading.Lock())

    def resolve(self, url):
        return self.request("/api/browser-entry?" + urllib.parse.urlencode({"url": url}))

    def test_resolution_strips_query_and_fragment_and_requires_authentication(self):
        status, result, _ = self.resolve("https://github.com/example/app/blob/main/README.md?token=secret#part")
        self.assertEqual(status, 200)
        self.assertEqual(result["project_key"], self.key)
        self.assertNotIn("secret", json.dumps(result))
        self.assertEqual(result["seed"], "https://github.com/example/app")
        status, result, _ = self.request("/api/browser-entry?url=https://github.com/example/app", token="bad")
        self.assertEqual(status, 401)
        for url in ("file:///private/data", "https://name:secret@example.com", "https://example.com:bad"):
            self.assertEqual(self.resolve(url)[0], 400)

    def test_service_candidate_is_chosen_and_sibling_paths_do_not_match(self):
        _, result, _ = self.resolve("https://app.example.com/settings")
        self.assertEqual(result["status"], "choose")
        self.assertEqual(result["project_key"], "")
        conn = registry.open_db()
        project = project_record.fetch_project(conn, self.key)
        project["service_urls"] = ["https://shared.example/tools/a"]
        project_record.save_project(conn, project)
        before = conn.total_changes
        result = browser_entry.resolve_page(conn, "https://shared.example/tools/a/profile")
        self.assertEqual(result["project_key"], self.key)
        self.assertEqual(conn.total_changes, before)
        for url in ("https://shared.example/tools/ab", "https://shared.example/tools/b", "https://shared.example"):
            self.assertEqual(browser_entry.resolve_page(conn, url)["status"], "unmatched")
        conn.close()

    def test_shared_hosts_and_multiple_projects_never_pick_first(self):
        conn = registry.open_db()
        project = project_record.fetch_project(conn, self.key)
        project["service_urls"] = ["https://github.com", "https://shared.example/a"]
        project_record.save_project(conn, project)
        other = project_record.fetch_project(conn, "proj:222222222222")
        other["service_urls"] = ["https://shared.example/a"]
        project_record.save_project(conn, other)
        self.assertEqual(browser_entry.resolve_page(conn, "https://github.com/other/app")["status"], "unmatched")
        result = browser_entry.resolve_page(conn, "https://shared.example/a/post")
        self.assertEqual(result["status"], "choose")
        self.assertEqual(len(result["items"]), 2)
        self.assertFalse(result["project_key"])
        conn.close()

    def test_introduction_plan_roundtrip_search_edit_clear_and_stale_revision(self):
        path = f"/api/projects/{self.key}/introduction-plan"
        _, before, _ = self.request(f"/api/projects/{self.key}")
        status, saved, _ = self.request(path, method="POST", payload={"note": "独自Lexiconを紹介したい"})
        self.assertEqual(status, 200)
        self.assertEqual(saved["detail"]["operator_feedback"]["introduction_plan"]["reason"], "独自Lexiconを紹介したい")
        self.assertEqual(before["promotion_policy"], saved["detail"]["promotion_policy"])
        self.assertTrue(any(item["kind"] == "plan_introduction" for item in saved["detail"]["decisions"]["active"]))
        _, result, _ = self.request("/api/introduction-plans?" + urllib.parse.urlencode({"q": "独自Lexicon"}))
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["project_key"], self.key)
        status, result, _ = self.request(path, method="POST", payload={"note": "stale", "expected_revision": before["revision"]})
        self.assertEqual(status, 409)
        self.request(path, method="POST", payload={"note": "紹介文を書く"})
        self.request(path, method="POST", payload={"clear": True})
        _, result, _ = self.request("/api/introduction-plans")
        self.assertEqual(result["total"], 0)
        conn = registry.open_db()
        project = project_record.fetch_project(conn, self.key)
        self.assertEqual(len(project["introduction_plans"]), 2)
        self.assertTrue(all(item.get("cleared_at") for item in project["introduction_plans"]))
        conn.close()

    def test_plans_include_ineligible_and_introduced_projects_and_paginate(self):
        conn = registry.open_db()
        for key in (self.key, "proj:222222222222"):
            feedback.save_feedback(conn, project_key=key, kind="introduction-plan", reason="特集")
        project = project_record.fetch_project(conn, self.key)
        project["tracking_state"] = "ignore"
        project["introduced_url"] = "https://bsky.app/profile/example.test/post/1"
        project_record.save_project(conn, project)
        first = browser_entry.introduction_plans(conn, query="特集", limit=1)
        second = browser_entry.introduction_plans(conn, query="特集", limit=1, offset=1)
        self.assertEqual(first["total"], 2)
        self.assertNotEqual(first["items"][0]["project_key"], second["items"][0]["project_key"])
        conn.close()

    def test_manual_service_usage_persists_as_relation_without_ownership(self):
        path = f"/api/projects/{self.key}/service-usage"
        payload = {"service_url": "https://app.example.com", "nsid": "org.other.record",
                   "source_url": "https://app.example.com/docs?token=secret#part", "note": "投稿の保存先を確認"}
        status, result, _ = self.request(path, method="POST", payload=payload)
        self.assertEqual(status, 200)
        usage = result["detail"]["operator_feedback"]["service_usage"][0]
        self.assertNotIn("secret", json.dumps(usage))
        relations = result["detail"]["relations"]
        self.assertTrue(any(item["predicate"] == "uses_collection" and item["classification"] == "confirmed" for item in relations))
        conn = registry.open_db()
        project = project_record.fetch_project(conn, self.key)
        self.assertNotIn("org.other.record", project["resolved_nsids"])
        self.assertNotIn("org.other.record", project["input_nsids"])
        # A normal project update may replace automatic relations. Operator
        # assertions are reconstructed from decisions when normalizing/saving.
        project["relation_evidence"] = []
        project = registry.finalize_project_shape(project)
        project_record.save_project(conn, project)
        reloaded = project_record.fetch_project(conn, self.key)
        self.assertTrue(any(item["source"] == operator_feedback.USAGE_SOURCE for item in reloaded["relation_evidence"]))
        conn.close()
        self.request(path, method="POST", payload={**payload, "note": "実際の投稿も確認"})
        _, result, _ = self.request(path, method="POST", payload={**payload, "clear": True})
        self.assertEqual(result["detail"]["operator_feedback"]["service_usage"], [])
        self.assertFalse(any(item["source"] == operator_feedback.USAGE_SOURCE for item in result["detail"]["relations"]))
        self.assertEqual(len([item for item in result["detail"]["decisions"]["history"] if item["kind"] == "confirm_service_usage"]), 2)

    def test_usage_requires_saved_service_valid_nsid_reason_and_current_revision(self):
        path = f"/api/projects/{self.key}/service-usage"
        payload = {"service_url": "https://app.example.com", "nsid": "org.other.record", "note": "確認"}
        for change in ({"service_url": "https://unrelated.example"}, {"nsid": "jwt-token"}, {"note": ""},
                       {"source_url": "file:///private/data"}):
            self.assertEqual(self.request(path, method="POST", payload={**payload, **change})[0], 400)
        _, old, _ = self.request(f"/api/projects/{self.key}")
        self.request(path, method="POST", payload=payload)
        self.assertEqual(self.request(path, method="POST", payload={**payload, "clear": True, "expected_revision": old["revision"]})[0], 409)

    def test_clearing_usage_keeps_automatic_evidence_and_works_after_service_removal(self):
        conn = registry.open_db()
        feedback.save_feedback(conn, project_key=self.key, kind="service-usage", service_url="https://app.example.com",
                               nsid="org.other.record", reason="確認")
        project = project_record.fetch_project(conn, self.key)
        automatic = dict(next(item for item in project["relation_evidence"] if item["source"] == operator_feedback.USAGE_SOURCE))
        automatic["source"] = "automatic_observation"
        project["relation_evidence"].append(automatic)
        project["service_url_candidates"] = []
        project_record.save_project(conn, project)
        feedback.save_feedback(conn, project_key=self.key, kind="service-usage", service_url="https://app.example.com",
                               nsid="org.other.record", clear=True)
        reloaded = project_record.fetch_project(conn, self.key)
        self.assertTrue(any(item["source"] == "automatic_observation" for item in reloaded["relation_evidence"]))
        self.assertFalse(any(item["source"] == operator_feedback.USAGE_SOURCE for item in reloaded["relation_evidence"]))
        conn.close()

    def test_candidate_confirmation_keeps_operator_reason(self):
        status, result, _ = self.request(f"/api/projects/{self.key}/confirm-candidate", method="POST",
            payload={"identifier_type": "service_url", "value": "https://app.example.com", "note": "OAuthから公式サービスと確認"})
        self.assertEqual(status, 200)
        self.assertTrue(any(item["kind"] == "confirm_identifier" and item["note"] == "OAuthから公式サービスと確認"
                            for item in result["detail"]["decisions"]["active"]))

    def test_merge_preserves_plans_reasons_and_service_usage_history(self):
        conn = registry.open_db()
        target = "proj:222222222222"
        feedback.save_feedback(conn, project_key=self.key, kind="introduction-plan", reason="元の紹介理由")
        feedback.save_feedback(conn, project_key=target, kind="introduction-plan", reason="存続先の理由")
        feedback.save_feedback(conn, project_key=self.key, kind="service-usage", service_url="https://app.example.com",
                               nsid="org.other.record", reason="利用確認")
        merge.merge_projects(conn, project_key=self.key, target_project_key=target, execute=True)
        result = project_record.fetch_project(conn, target)
        self.assertEqual(operator_feedback.active_plan(result)["reason"], "存続先の理由")
        self.assertEqual({item["reason"] for item in result["introduction_plans"]}, {"元の紹介理由", "存続先の理由"})
        self.assertEqual(len(result["service_usage_decisions"]), 1)
        self.assertEqual(browser_entry.introduction_plans(conn)["total"], 1)
        conn.close()


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class FirefoxRegistryExtensionTest(unittest.TestCase):
    def test_toolbar_handoff_uses_only_clicked_url_and_local_configuration(self):
        folder = ROOT / "extras/firefox-registry"
        source = (folder / "entry.js").read_text() + (folder / "background.js").read_text()
        harness = '''
const assert = require("node:assert/strict");
let clicked, settings = {}, options = 0, opened = [];
const browser = {
 action:{onClicked:{addListener(fn){clicked=fn;}}},
 storage:{local:{async get(){return settings;}}},
 runtime:{async openOptionsPage(){options++;}, getURL(path){return "moz-extension://test/"+path;}},
 tabs:{async create(item){opened.push(item.url);}}
};
'''
        checks = '''
(async () => {
 await clicked({url:"https://service.example/path?code=secret#token"});
 assert.equal(options,1); assert.equal(opened.length,0);
 settings.registryUrl="http://127.0.0.1:8765";
 await clicked({url:"https://service.example/path?code=secret#token"});
 const target = new URL(opened.pop());
 assert.equal(target.origin,"http://127.0.0.1:8765");
 assert.equal(target.searchParams.get("screen"),"browser");
 assert.equal(target.searchParams.get("url"),"https://service.example/path");
 assert.ok(!target.href.includes("secret"));
 await clicked({url:"about:preferences"});
 assert.ok(opened.pop().startsWith("moz-extension://test/options.html?error="));
 for(const value of ["https://token:secret@example.com", "https://example.com/?token=secret", "https://example.com/sub", "javascript:alert(1)"]) {
  assert.throws(()=>registryBaseUrl(value));
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run([shutil.which("node"), "-"], input=harness + source + checks,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((folder / "manifest.json").read_text())
        self.assertEqual(set(manifest["permissions"]), {"activeTab", "storage"})
        self.assertNotIn("host_permissions", manifest)
