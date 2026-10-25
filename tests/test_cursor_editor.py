"""Synthetic Cursor editor snapshots; never reads source stores or user data."""
import copy
import datetime
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import editor_snapshot as E


TODAY = datetime.date.today().isoformat()
NOW = datetime.datetime.combine(datetime.date.today(), datetime.time(12)).timestamp()
CURSOR_ID = "12345678-1234-4234-8234-123456789abc"
CLAUDE_ID = "87654321-1234-4234-8234-123456789abc"
WORKSPACE = "/fixtures/cursor-project"
UNAVAILABLE = {"input_output": "unavailable", "cache_read": "unavailable",
               "cache_write": "unavailable", "reasoning": "unavailable",
               "response_output": "unavailable"}


def query(scope="workspace", root=WORKSPACE):
    pairs = [("scope", scope), ("range", "today")]
    if scope == "workspace":
        pairs.append(("root", root))
    return urlencode(pairs)


def cursor_aggregate(identity=CURSOR_ID, status="unavailable", model_status="unavailable",
                     model="Unknown", current_model="Claude Sonnet 5", tokens=0,
                     cwd=WORKSPACE):
    records = {TODAY + "\t" + model: {"in": tokens, "out": 0, "asst": 2, "user": 1, "tools": 1}}
    session = {
        "_sid": identity, "id": identity[:8], "source": "cursor", "title": "Synthetic Cursor chat",
        "project": "cursor-project", "model": model, "models": [] if model == "Unknown" else [model],
        "model_status": model_status, "response_model_status": model_status,
        "current_model": current_model, "current_context_tokens": 12000,
        "current_context_percent": 24.0, "current_context_limit": 50000,
        "current_context_asof": TODAY + "T11:45:00Z",
        "usage_status": status, "usage_availability": dict(UNAVAILABLE),
        "start": TODAY + "T10:00:00Z", "end": TODAY + "T11:45:00Z",
        "subagent": False, "records": copy.deepcopy(records), "cwd": cwd,
    }
    if status == "partial":
        session["usage_availability"]["input_output"] = "partial"
        session["usage_availability"]["cache_read"] = "unavailable"
    return {
        "path": "/fixture/cursor/state.vscdb", "source": "cursor", "project": "Cursor",
        "_device": "local", "_cursor_cwds": {identity: cwd} if cwd else {},
        "records": records, "sessions": [session],
    }


def claude_aggregate():
    model = "Claude Sonnet 5"
    usage = {"in": 200, "out": 40, "cr": 0, "cc": 0, "asst": 1, "user": 1, "req": 1}
    record = {**D._zero(), **usage}
    return {
        "path": "/fixture/claude/session.jsonl", "source": "claude", "cwd": WORKSPACE,
        "project": "cursor-project", "_device": "local", "records": {TODAY + "\t" + model: record},
        "sessions": [{"id": CLAUDE_ID[:8], "_sid": CLAUDE_ID, "source": "claude",
            "title": "Synthetic Claude chat", "project": "cursor-project", "model": model,
            "start": TODAY + "T10:30:00Z", "end": TODAY + "T10:45:00Z", "subagent": False,
            **usage, "records": {TODAY + "\t" + model: copy.deepcopy(record)}}],
    }


class CursorEditorSnapshotTests(unittest.TestCase):
    def snapshot(self, files, params=None):
        return E.build_snapshot([(item["path"], item) for item in files], "local", params or query(),
            "fixture-revision", price_usage=D._priced_usage, deduplicate=D._one_per_conversation,
            identity=D.SN.identity, claude_parent_key=D._claude_parent_key, now=NOW)

    def test_workspace_match_uses_private_cursor_id_map_without_exporting_paths(self):
        aggregate = cursor_aggregate(cwd=None)
        aggregate["_cursor_cwds"] = {CURSOR_ID: WORKSPACE + "/src"}
        aggregate["sessions"][0].pop("cwd")
        result = self.snapshot([aggregate])
        self.assertEqual(result["sessions_total"], 1)
        row = result["sessions"][0]
        self.assertEqual(row["usage_status"], "unavailable")
        self.assertEqual(row["cost_status"], "unavailable")
        self.assertEqual(row["tokens"], 0)
        self.assertEqual(row["cost"], 0)
        self.assertEqual(row["current_model"], "Claude Sonnet 5")
        self.assertEqual(row["model"], "Unknown", "current composer setting is not a historical model")
        self.assertEqual(row["current_context_tokens"], 12000)
        self.assertEqual(row["current_context_percent"], 24.0)
        self.assertEqual(row["current_context_limit"], 50000)
        self.assertEqual(row["current_context_asof"], TODAY + "T11:45:00Z")
        self.assertNotIn("cwd", row)
        self.assertNotIn(WORKSPACE, json.dumps(result))

    def test_cursor_only_summary_marks_zero_fields_unavailable(self):
        result = self.snapshot([cursor_aggregate()])
        self.assertEqual(result["summary"]["tokens"], 0)
        self.assertEqual(result["summary"]["cost"], 0)
        self.assertEqual(result["summary"]["usage_status"], "unavailable")
        self.assertEqual(result["summary"]["cost_status"], "unavailable")
        self.assertEqual(result["sessions"][0]["usage_status"], "unavailable")

    def test_mixed_summary_labels_known_source_totals_as_partial(self):
        result = self.snapshot([cursor_aggregate(), claude_aggregate()])
        self.assertEqual(result["summary"]["usage_status"], "partial")
        self.assertEqual(result["summary"]["cost_status"], "partial")
        self.assertEqual(result["summary"]["tokens"], 240)
        self.assertGreater(result["summary"]["cost"], 0)
        cursor = next(row for row in result["sessions"] if row["source"] == "cursor")
        self.assertEqual((cursor["tokens"], cursor["cost"]), (0, 0))
        self.assertEqual(cursor["usage_status"], "unavailable")
        self.assertNotIn(WORKSPACE, json.dumps(result))

    def test_partial_cursor_usage_and_unknown_legacy_status_are_explicit(self):
        partial = cursor_aggregate(status="partial", model_status="partial", model="Claude Sonnet 5", tokens=200)
        partial["sessions"][0]["records"][TODAY + "\t" + "Claude Sonnet 5"]["out"] = 40
        partial["records"][TODAY + "\t" + "Claude Sonnet 5"] = copy.deepcopy(
            partial["sessions"][0]["records"][TODAY + "\t" + "Claude Sonnet 5"])
        result = self.snapshot([partial])
        row = result["sessions"][0]
        self.assertEqual(row["usage_status"], "partial")
        self.assertEqual(row["cost_status"], "partial")
        self.assertEqual(row["usage_availability"]["input_output"], "partial")
        self.assertEqual(row["usage_availability"]["cache_read"], "unavailable")
        self.assertEqual(result["summary"]["usage_status"], "partial")

        legacy = cursor_aggregate()
        for field in ("usage_status", "usage_availability", "model_status", "response_model_status"):
            legacy["sessions"][0].pop(field, None)
        old = self.snapshot([legacy])
        self.assertEqual(old["sessions"][0]["usage_status"], "unknown")
        self.assertEqual(old["sessions"][0]["model_status"], "unknown")
        self.assertEqual(old["summary"]["usage_status"], "unknown")
        self.assertEqual(old["summary"]["cost_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
