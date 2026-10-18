"""Optional effort backfills preserve the durable ledger; synthetic inputs only."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


class ReasoningEffortMigrationTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="telemetry-effort-migration-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        for item in [patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}),
                     patch.dict(D._meta, D._meta.copy()),
                     patch.dict(D._dirty, {"v": False}),
                     patch.dict(D._grew, {"v": False, "saved": 0}),
                     patch.dict(D._gen, D._gen.copy()),
                     patch.dict(D._cache_error, {"v": None}),
                     patch.object(D, "CACHE_PATH", str(self.root / "cache.json")),
                     patch.object(P, "_copilot_project_map", return_value={}),
                     patch.object(D, "_peer_items", return_value=[]),
                     patch.object(D, "_local_id", return_value="fixture-device"),
                     patch.object(D, "_devices_meta", return_value=[]),
                     patch.object(D, "_context_files", return_value=[]),
                     patch.object(D, "_installed", return_value=[]),
                     patch.object(D, "_mcp_servers", return_value={})]:
            item.start()
            self.addCleanup(item.stop)

    def fixture(self):
        path = self.root / "session.jsonl"
        path.write_text(json.dumps({"uuid": "fixture-response", "type": "assistant",
            "timestamp": "2026-10-18T10:00:00Z", "effort": "max",
            "requestId": "fixture-request", "message": {"id": "fixture-message",
                "model": "claude-fable-5-1", "content": [],
                "usage": {"input_tokens": 100, "output_tokens": 20}}}) + "\n",
            encoding="utf-8")
        current = P.update_file(None, "claude", str(path), None, {})
        legacy = copy.deepcopy(current)
        legacy.pop("reasoning_effort", None)
        legacy.pop("reasoning_effort_version", None)
        legacy["sessions"][0].pop("reasoning_effort", None)
        for key in list(legacy["state"]):
            if "effort" in key:
                legacy["state"].pop(key)
        return path, current, legacy

    def test_v58_retains_all_entries_and_backfills_unchanged_live_file(self):
        path, expected, legacy = self.fixture()
        archived = copy.deepcopy(legacy)
        archived["archived"] = True
        other = P._blank_agg("gemini", "missing.jsonl")
        files = {str(path): legacy, "archived": archived, "other": other}
        Path(D.CACHE_PATH).write_text(json.dumps({"version": 58, "files": files}))
        with contextlib.redirect_stderr(io.StringIO()):
            D.load_cache()
        self.assertEqual(D._state["files"], files)
        updated = P.update_file(D._state["files"][str(path)], "claude", str(path), None, {})
        self.assertEqual(updated["reasoning_effort"], expected["reasoning_effort"])
        self.assertEqual(updated["totals"], legacy["totals"])
        self.assertEqual(updated["records"], legacy["records"])
        self.assertEqual(D._state["files"]["archived"], archived)
        self.assertEqual(D._state["files"]["other"], other)
        self.assertNotIn("reasoning_effort", archived["sessions"][0])
        self.assertIs(P.update_file(updated, "claude", str(path), None, {}), updated)

    def test_failed_backfill_retries_without_changing_signature_or_totals(self):
        path, expected, legacy = self.fixture()
        D._state["files"] = {str(path): legacy}
        original = copy.deepcopy(legacy)
        with patch.object(P, "discover", return_value=[("claude", str(path), None)]):
            with patch.object(P, "parse_claude", side_effect=ValueError("fixture failure")), \
                    contextlib.redirect_stderr(io.StringIO()):
                D.refresh()
                D.refresh()
            self.assertEqual(D._state["files"][str(path)], original)
            D.refresh()
        updated = D._state["files"][str(path)]
        self.assertEqual(updated["reasoning_effort"], expected["reasoning_effort"])
        for field in ("totals", "records", "size", "mtime", "offset"):
            self.assertEqual(updated[field], original[field], field)

    def test_peers_retain_safe_session_metadata_without_private_identity_state(self):
        _, current, _ = self.fixture()
        current["state"]["claude_effort_responses"] = {"private-identity": ["private-request"]}
        exported = D._export_agg(current)
        self.assertEqual(exported["sessions"][0]["reasoning_effort"], current["reasoning_effort"])
        self.assertNotIn("private-identity", json.dumps(exported))
        self.assertNotIn("private-request", json.dumps(exported))
        self.assertNotIn("reasoning_effort_version", exported)
        self.assertEqual(set(exported["state"]) - {"dom_model", "ai_lines"}, set())
        remote = copy.deepcopy(exported)
        remote["_device"] = "other-device"
        D._state["files"] = {"local": current}
        with patch.object(D, "_peer_items", return_value=[("remote", remote)]):
            sessions = D.build_payload()["sessions"]
        self.assertEqual({s["device"] for s in sessions}, {"fixture-device", "other-device"})
        self.assertTrue(all(s["reasoning_effort"] == current["reasoning_effort"] for s in sessions))
        for version in range(52, D.CACHE_VERSION + 1):
            self.assertTrue(D._peer_cache_ok(version), version)
        for version in (True, "58", 51, D.CACHE_VERSION + 1):
            self.assertFalse(D._peer_cache_ok(version), version)


if __name__ == "__main__":
    unittest.main()
