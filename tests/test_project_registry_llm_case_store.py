from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from project_registry_core import cli
from project_registry_core.model_evaluation import llm_case_store
from project_registry_core import registry


class LlmCaseStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = self.root / "private-cases"
        self.draft = self.root / "draft.json"
        self.case = {
            "schema_version": 1,
            "case_id": "partial-claim-1",
            "task": "triage",
            "origin": {"kind": "evaluation", "source": "synthetic evaluation attempt 1"},
            "input": {"status": "captured", "payload": {"prompt": "Read https://user:pass@example.org/info?key=1#part Bearer abc123"}},
            "output": {"status": "captured", "payload": {"summary": "unsupported claim", "api_key": "secret-123", "Set-Cookie": "session=secret"}},
            "execution": {"status": "captured", "payload": {"model": "example", "prompt_sha256": "abc"}},
            "used_for_adjustment": True,
            "assessments": [{"claim": "repository has write support", "verdict": "unsupported",
                             "authority": "human", "reason": "fixed input only shows read operations"}],
            "sanitization": {"human_reviewed": True},
        }

    def write(self) -> None:
        self.draft.write_text(json.dumps(self.case), encoding="utf-8")

    def test_register_read_verify_and_secret_removal(self) -> None:
        self.write()
        self.assertEqual(cli.main(["register-llm-case", "--store", str(self.store), "--file", str(self.draft)]), 0)
        stored = llm_case_store.read(self.store, "partial-claim-1")
        self.assertEqual(stored["case"]["assessments"][0]["verdict"], "unsupported")
        self.assertEqual(stored["case"]["input"]["payload"]["prompt"],
                         "Read https://example.org/info Bearer [redacted]")
        self.assertEqual(stored["case"]["output"]["payload"]["api_key"], "[redacted]")
        self.assertEqual(stored["case"]["output"]["payload"]["Set-Cookie"], "[redacted]")
        self.assertFalse(stored["case"]["sanitization"]["human_reviewed"])
        self.assertEqual(cli.main(["verify-llm-cases", "--store", str(self.store)]), 0)
        self.assertEqual(len(llm_case_store.list_cases(self.store)), 1)
        self.assertEqual(cli.main(["show-llm-case", "--store", str(self.store), "partial-claim-1"]), 0)
        with self.assertRaises(FileExistsError):
            llm_case_store.register(self.store, self.draft)
        self.assertEqual((self.store / "partial-claim-1.json").stat().st_mode & 0o777, 0o600)

    def test_reviewed_redacted_draft_stays_reviewed(self) -> None:
        self.write()
        llm_case_store.register(self.store, self.draft)
        reviewed = llm_case_store.read(self.store, "partial-claim-1")["case"]
        reviewed["case_id"] = "partial-claim-reviewed"
        reviewed["sanitization"]["human_reviewed"] = True
        self.draft.write_text(json.dumps(reviewed), encoding="utf-8")
        result = llm_case_store.register(self.store, self.draft)
        self.assertTrue(result["human_reviewed"])
        self.assertIn("$.output.payload.Set-Cookie", result["auto_redacted_paths"])

    def test_events_update_effective_state_without_changing_snapshot(self) -> None:
        self.case["used_for_adjustment"] = False
        self.case["sanitization"]["human_reviewed"] = False
        self.write()
        llm_case_store.register(self.store, self.draft)
        original_input = llm_case_store.read(self.store, "partial-claim-1")["case"]["input"]
        event = {"schema_version": 1, "case_id": "partial-claim-1", "event_id": "prompt-tuning-1",
                 "kind": "adjustment_used", "authority": "codex", "source": "prompt trial 1",
                 "reason": "this case informed a prompt revision"}
        event_draft = self.root / "event.json"
        event_draft.write_text(json.dumps(event), encoding="utf-8")
        self.assertEqual(cli.main(["append-llm-case-event", "--store", str(self.store), "--file", str(event_draft)]), 0)
        with self.assertRaises(FileExistsError):
            llm_case_store.append_event(self.store, event_draft)
        event.update({"event_id": "human-review-1", "kind": "snapshot_reviewed",
                      "authority": "human", "source": "case review", "reason": "redacted snapshot checked"})
        event_draft.write_text(json.dumps(event), encoding="utf-8")
        llm_case_store.append_event(self.store, event_draft)
        event.update({"event_id": "later-claim-1", "kind": "assessment", "authority": "human",
                      "reason": "input only documents reading",
                      "assessment": {"claim": "writes are supported", "verdict": "unsupported"}})
        event_draft.write_text(json.dumps(event), encoding="utf-8")
        llm_case_store.append_event(self.store, event_draft)
        event.update({"event_id": "corrected-claim-1", "reason": "later human check found the input ambiguous",
                      "assessment": {"claim": "writes are supported", "verdict": "uncertain"},
                      "supersedes": "later-claim-1"})
        event_draft.write_text(json.dumps(event), encoding="utf-8")
        llm_case_store.append_event(self.store, event_draft)
        event.update({"event_id": "corrected-base-1", "reason": "original judgement narrowed after review",
                      "assessment": {"claim": "repository has write support", "verdict": "uncertain"},
                      "supersedes": "base:0"})
        event_draft.write_text(json.dumps(event), encoding="utf-8")
        llm_case_store.append_event(self.store, event_draft)
        result = llm_case_store.read(self.store, "partial-claim-1")
        self.assertEqual(result["case"]["input"], original_input)
        self.assertFalse(result["case"]["used_for_adjustment"])
        self.assertTrue(result["effective"]["used_for_adjustment"])
        self.assertTrue(result["effective"]["human_reviewed"])
        self.assertEqual(len(result["effective"]["assessments"]), 2)
        self.assertEqual({item["assessment_id"] for item in result["effective"]["assessments"]},
                         {"corrected-base-1", "corrected-claim-1"})
        self.assertEqual(result["effective"]["assessments"][-1]["verdict"], "uncertain")
        row = llm_case_store.list_cases(self.store)[0]
        self.assertTrue(row["used_for_adjustment"])
        self.assertEqual(row["event_count"], 5)
        self.assertEqual(cli.main(["verify-llm-cases", "--store", str(self.store)]), 0)
        event_path = self.store / "events" / "partial-claim-1" / "prompt-tuning-1.json"
        altered = json.loads(event_path.read_text(encoding="utf-8"))
        altered["event"]["reason"] = "changed"
        event_path.write_text(json.dumps(altered), encoding="utf-8")
        with self.assertRaises(ValueError):
            llm_case_store.read(self.store, "partial-claim-1")

    def test_missing_input_is_explicit_and_cannot_be_reconstructed_as_captured(self) -> None:
        self.case["input"] = {"status": "missing"}
        self.case["execution"] = {"status": "missing"}
        self.case["assessments"] = []
        self.case["sanitization"] = {"human_reviewed": False}
        self.write()
        llm_case_store.register(self.store, self.draft)
        self.assertEqual(llm_case_store.read(self.store, "partial-claim-1")["case"]["input"], {"status": "missing"})
        self.case["input"] = {"status": "captured"}
        self.write()
        with self.assertRaises(ValueError):
            llm_case_store.register(self.root / "another-store", self.draft)

    def test_reconstructed_repository_case_keeps_provenance_without_gold(self) -> None:
        self.case["case_id"] = "repo-reconstruction-1"
        self.case["task"] = "repository_analysis"
        self.case["origin"] = {"kind": "reconstructed", "source": "saved material, not the original request"}
        self.case["input"] = {"status": "reconstructed", "payload": {"readme": "read operation"}}
        self.case["output"] = {"status": "missing"}
        self.case["execution"] = {"status": "missing"}
        self.case["assessments"][0]["authority"] = "codex"
        self.case["used_for_adjustment"] = False
        self.write()
        llm_case_store.register(self.store, self.draft)
        stored = llm_case_store.read(self.store, "repo-reconstruction-1")["case"]
        self.assertEqual(stored["input"]["status"], "reconstructed")
        self.assertEqual(stored["assessments"][0]["authority"], "codex")
        self.assertFalse(stored["used_for_adjustment"])

    def test_integrity_and_repo_path_rejected(self) -> None:
        self.write()
        with self.assertRaises(ValueError):
            llm_case_store.register(registry.ROOT / "data" / "cases", self.draft)
        llm_case_store.register(self.store, self.draft)
        target = self.store / "partial-claim-1.json"
        envelope = json.loads(target.read_text(encoding="utf-8"))
        envelope["case"]["assessments"][0]["verdict"] = "supported"
        target.write_text(json.dumps(envelope), encoding="utf-8")
        with self.assertRaises(ValueError):
            llm_case_store.read(self.store, "partial-claim-1")


if __name__ == "__main__":
    unittest.main()
