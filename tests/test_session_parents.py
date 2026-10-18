"""Recorded parent identity and optional metadata migration; invented logs only."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P
import test_claude_subagent_parents as C

PARENT = "12345678-1234-1234-1234-123456789abc"
CHILD = "87654321-1234-1234-1234-123456789abc"
OTHER = "abcdef12-1234-1234-1234-123456789abc"


class CodexParentTests(unittest.TestCase):
    def lines(self, **fields):
        meta = {"id": CHILD, "session_id": OTHER, "thread_source": "subagent", **fields}
        return [json.dumps({"type": "session_meta", "timestamp": "2026-10-18T10:00:00Z", "payload": meta}) + "\n",
            json.dumps({"type": "turn_context", "timestamp": "2026-10-18T10:00:05Z",
                "payload": {"turn_id": "child-turn", "model": "gpt-6-luna", "effort": "max"}}) + "\n",
            json.dumps({"type": "token_usage_record", "timestamp": "2026-10-18T10:00:06Z",
                "payload": {"turn_id": "child-turn", "response_id": "child-response",
                    "usage": {"input_tokens": 100, "output_tokens": 10}}}) + "\n"]

    def parse(self, **fields):
        agg = P._blank_agg("codex", "fixture.jsonl")
        P.parse_codex(agg, self.lines(**fields))
        with patch.object(P, "_codex_thread_names", return_value={}):
            P._finalize_session(agg, "codex", "fixture.jsonl")
        return agg

    def test_explicit_parent_is_not_root_session_or_fork_history(self):
        agg = self.parse(parent_thread_id=PARENT, forked_from_id=OTHER)
        self.assertEqual(agg["_session_id"], CHILD)
        self.assertEqual(agg["sessions"][0]["parent_id"], PARENT)
        self.assertEqual(agg["totals"]["in"], 100)
        no_parent = self.parse(forked_from_id=OTHER)
        self.assertNotIn("parent_id", no_parent["sessions"][0])

    def test_documented_nested_spawn_source_also_records_parent(self):
        agg = self.parse(thread_source=None, source={"subagent": {"thread_spawn": {
            "parent_thread_id": PARENT, "depth": 1}}})
        self.assertTrue(agg["subagent"])
        self.assertEqual(agg["sessions"][0]["parent_id"], PARENT)

    def test_invalid_conflicting_self_and_unmarked_links_are_not_retained(self):
        for fields in ({"parent_thread_id": CHILD}, {"parent_thread_id": "12345678"},
                {"parent_thread_id": False}, {"parent_thread_id": {"id": PARENT}},
                {"thread_source": "user", "parent_thread_id": PARENT},
                {"parent_thread_id": PARENT, "source": {"subagent": {"thread_spawn": {
                    "parent_thread_id": OTHER}}}}):
            with self.subTest(fields=fields):
                self.assertNotIn("parent_id", self.parse(**fields)["sessions"][0])

    def test_unchanged_available_log_backfills_and_failure_keeps_old_entry(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(P, "_codex_thread_names", return_value={}):
            path = Path(temp) / "fixture.jsonl"
            path.write_text("".join(self.lines(parent_thread_id=PARENT)))
            old = P.update_file(None, "codex", str(path), None, {})
            del old["parent_metadata_version"]
            old.pop("_parent_session_id", None)
            old["sessions"][0].pop("parent_id", None)
            before = copy.deepcopy(old)
            with patch.object(P, "parse_codex", side_effect=ValueError("fixture failure")):
                with self.assertRaises(ValueError):
                    P.update_file(old, "codex", str(path), None, {})
            self.assertEqual(old, before)
            fresh = P.update_file(old, "codex", str(path), None, {})
            self.assertEqual(fresh["sessions"][0]["parent_id"], PARENT)
            self.assertEqual(fresh["totals"], before["totals"])

    def test_version_59_keeps_every_archived_and_live_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.json"
            files = {"gone": {"archived": True}, "live": {"archived": False}}
            path.write_text(json.dumps({"version": 59, "files": files}))
            with patch.object(D, "CACHE_PATH", str(path)), patch.dict(D._state, {"files": {}}), \
                    patch.dict(D._dirty, {"v": False}):
                D.load_cache()
                self.assertEqual(D._state["files"], files)
                self.assertTrue(D._dirty["v"])
            self.assertTrue(D._peer_cache_ok(59))

    def test_payload_uses_full_native_parent_and_device_without_mutating_ledger(self):
        def item(identity, child=False, device=None):
            path = f"/fixture/rollout-2026-10-18T10-00-00-{identity}.jsonl"
            agg = C.aggregate(path, child, "codex", device)
            agg["_session_id"] = identity
            if child:
                agg["sessions"][0]["parent_id"] = PARENT
            return agg
        parent, child = item(PARENT), item(CHILD, True)
        before = copy.deepcopy([parent, child])
        harness = C.ClaudeSubagentParentTests()
        payload = harness.payload([parent, child])
        result = next(s for s in payload["sessions"] if s["subagent"])
        self.assertEqual(result["subagent_parent"], {"id": PARENT, "title": "Same invented title",
            "source": "codex", "device": "local"})
        self.assertEqual([parent, child], before)
        payload = harness.payload([child], peers=[("peer:remote:parent", item(PARENT, device="remote"))])
        self.assertNotIn("subagent_parent", next(s for s in payload["sessions"] if s["subagent"]))
        parent["_session_id"] = 42
        payload = harness.payload([parent, child])
        self.assertNotIn("subagent_parent", next(s for s in payload["sessions"] if s["subagent"]))


if __name__ == "__main__":
    unittest.main()
