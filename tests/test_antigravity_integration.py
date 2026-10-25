"""Mock-only integration coverage for Antigravity's shared telemetry path."""

from contextlib import ExitStack, redirect_stderr
from datetime import date, datetime, time, timezone
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

import antigravity as AG
import dashboard as D
import editor_snapshot as ES
import mcp_backend as MCP
import native_activity as NA
import session_names as SN
import session_timeline as ST
import telemetry_insights as TI
import telemetry_query as TQ


TODAY = date.today().isoformat()
PARENT = "11111111-2222-4333-8444-555555555555"
CHILD = "66666666-7777-4888-8999-aaaaaaaaaaaa"
ROOT_ONLY = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
DEVICE = "d" * 32
FIELD_AVAILABILITY = {
    "input_output": "recorded", "cache_read": "partial", "cache_write": "recorded",
    "reasoning": "unavailable", "response_output": "unavailable",
}


def _stamp(hour=12):
    local_zone = datetime.now().astimezone().tzinfo
    return datetime.combine(date.fromisoformat(TODAY), time(hour, 0), tzinfo=local_zone).isoformat()


def _record(inp=256, out=16, cr=12, cc=4, asst=1, req=1):
    row = D._zero()
    row.update({"in": inp, "out": out, "cr": cr, "cc": cc,
                "asst": asst, "req": req, "user": 1, "tools": 2})
    return row


def _antigravity_aggregate(identity=PARENT, *, subagent=False, parent_id=None, root_id=None,
                           cwd="/fixture/private/workspace", status="partial", records=None,
                           title="Synthetic Antigravity session", subagent_requests=0):
    row = _record() if records is None else records
    key = TODAY + "\tUnknown"
    availability = dict(FIELD_AVAILABILITY)
    session = {
        "source": "antigravity",
        "_sid": identity, "conversation_id": identity, "id": identity,
        "title": title, "project": "fixture-project", "branch": "fixture-branch",
        "model": "Unknown", "start": _stamp(10), "end": _stamp(12),
        "subagent": subagent, "parent_id": parent_id, "root_id": root_id,
        "records": {key: dict(row)}, "days": {TODAY: dict(row)},
        "cwd": cwd, "in": row["in"], "out": row["out"], "cr": row["cr"], "cc": row["cc"],
        "reason": row["reason"], "req": row["req"], "asst": row["asst"],
        "user": row["user"], "tools": row["tools"], "active": 42.0,
        "response_out": 7,
        "usage_status": status, "usage_availability": availability,
        "record_availability": {key: availability}, "user_messages": 1,
        "assistant_messages": 1, "tool_calls": 2, "subagent_requests": subagent_requests,
        "reasoning_effort": {"version": 1, "days": {key: {"levels": {"high": 1}, "unrecorded": 0}}},
    }
    return {
        "source": "antigravity", "path": f"/fixture/.gemini/antigravity-ide/conversations/{identity}.db",
        "conversation_id": identity, "project": "fixture-project", "cwd": cwd,
        "records": {key: dict(row)}, "tools": {TODAY + "\tantigravity\tRead": 2},
        "sessions": [session],
        "antigravity": {
            "usage_status": status, "usage_availability": availability,
            "model_status": "partial", "detail_status": "recorded",
            "record_availability": {key: availability}, "subagent_requests": subagent_requests,
            "undated_usage": {field: 0 for field in ("in", "out", "cr", "cc", "reason", "response_out", "req")},
            "unknown_model_usage": {
                "in": row["in"], "out": row["out"], "cr": row["cr"], "cc": row["cc"],
                "reason": row["reason"], "response_out": 7,
            },
            "response_out": 7,
        },
    }


class AntigravityIntegrationTests(unittest.TestCase):
    def dashboard_payload(self, aggregates):
        files = {aggregate["path"]: aggregate for aggregate in aggregates}
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(D, "_state", {"files": files, "version": D.CACHE_VERSION}))
        stack.enter_context(patch.object(D, "_local_id", return_value=DEVICE))
        stack.enter_context(patch.object(D, "_project_preferences", return_value=({"version": 1, "aliases": {}, "roots": {}}, None)))
        stack.enter_context(patch.object(D, "_peer_items", return_value=[]))
        stack.enter_context(patch.object(D, "_peer_cfg", return_value={"device_id": DEVICE, "peers": {}}))
        stack.enter_context(patch.object(D, "_devices_meta", return_value=[{"id": DEVICE, "name": "Fixture device", "local": True}]))
        stack.enter_context(patch.object(D.N, "project_items", side_effect=lambda items, *_args: items))
        stack.enter_context(patch.object(D.P, "_ide_of", return_value="Antigravity IDE"))
        stack.enter_context(patch.object(D.P, "activity_of", return_value={}))
        stack.enter_context(patch.object(D.SN, "display_names", return_value={}))
        stack.enter_context(patch.object(D, "_mcp_servers", return_value=[]))
        stack.enter_context(patch.object(D, "_context_files", return_value=[]))
        stack.enter_context(patch.object(D, "_installed", return_value={"items": [], "used": []}))
        stack.enter_context(patch.object(D, "_codex_config", return_value={"effort": None}))
        stack.enter_context(patch.object(D, "DEVICE", {"name": "Fixture device", "os": "test"}))
        return D._build_payload_locked()

    def test_payload_native_and_mcp_keep_source_and_usage_quality(self):
        aggregate = _antigravity_aggregate()
        payload = self.dashboard_payload([aggregate])
        record = next(row for row in payload["records"] if row["source"] == "antigravity")
        session = next(row for row in payload["sessions"] if row["source"] == "antigravity")
        self.assertEqual(record["usage_status"], "partial")
        self.assertEqual(record["usage_availability"]["cache_read"], "partial")
        self.assertEqual(session["usage_availability"]["reasoning"], "unavailable")
        self.assertNotIn("cwd", session)
        self.assertNotIn("/fixture/private/workspace", json.dumps(payload))
        self.assertIn("antigravity", TI._EXACT_SOURCES)
        self.assertEqual(TQ._KNOWN_TOOLS["antigravity"], "Antigravity")

        usage = TQ.query_usage(payload, {"period": "all"}, today=TODAY)
        source = next(row for row in usage["source_precision"] if row["tool"] == "Antigravity")
        self.assertEqual(source["precision"], "exact")
        self.assertEqual(source["usage_status"], "partial")
        self.assertIn("not inferred", source["note"])
        listed = TQ.list_sessions(payload, {"period": "all"}, today=TODAY)
        antigravity = next(row for row in listed["sessions"] if row["tool"] == "antigravity")
        self.assertEqual(antigravity["usage_status"], "partial")
        self.assertEqual(antigravity["usage_availability"]["cache_read"], "partial")

        native = NA.project([(aggregate["path"], aggregate)], observed_at=_stamp(12),
                            identity=SN.identity, local_device=DEVICE)
        native_row = native["sessions"][0]
        self.assertEqual((native_row["source"], native_row["tool"], native_row["provider"]),
                         ("antigravity", "Antigravity", "Google"))
        self.assertIsNone(native_row["tokens"], "partial token fields must not be totalled as complete")
        self.assertEqual(native_row["usage_status"], "partial")

    def test_timeline_filters_before_cap_and_flattens_checkpoint_usage_safely(self):
        timestamp = _stamp(11)
        events = [
            {"timestamp": _stamp(10), "kind": "tool", "tool": "Read", "status": "error",
             "step_index": 1, "arguments": "PRIVATE-ARGUMENT"},
            {"timestamp": _stamp(9), "kind": "request", "model": "Old model",
             "usage": {"in": 1, "out": 2}, "usage_availability": {"input_output": "recorded"}},
            {"timestamp": timestamp, "kind": "checkpoint", "step_index": 18, "status": "done",
             "intent_only": True, "included_steps": 0, "prompt": "PRIVATE-PROMPT"},
            {"timestamp": timestamp, "kind": "request", "step_index": 18, "model": "Unknown",
             "checkpoint_usage": True, "usage": {"in": 256, "out": 16, "cr": 0, "cc": 0, "reason": 0},
             "usage_availability": {"input_output": "recorded", "cache_read": "recorded",
                                     "cache_write": "recorded", "reasoning": "unavailable"},
             "request_id": "PRIVATE-REQUEST-ID", "cwd": "/fixture/private"},
            {"timestamp": timestamp, "kind": "compaction", "step_index": 20,
             "status": "done", "trigger": "unknown"},
        ]
        calls = []

        def read_snapshot(path, *, max_events, event_filter, include_private):
            calls.append((Path(path).name, max_events, include_private))
            selected = [event for event in events if event_filter(event)]
            return {"conversation_id": PARENT, "events": selected[:max_events],
                    "events_truncated": len(selected) > max_events, "detail_status": "recorded"}

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / f"{PARENT}.db"
            path.touch()
            with patch.object(ST.AG, "read_conversation", side_effect=read_snapshot):
                result = ST.timeline("antigravity", path, PARENT, max_events=1,
                                     date_from=TODAY, date_to=TODAY, view="activity")
                self.assertTrue(result["available"])
                self.assertEqual(len(result["events"]), 1)
                checkpoint = result["events"][0]
                self.assertEqual(checkpoint["kind"], "checkpoint")
                self.assertEqual(checkpoint["label"], "Recorded checkpoint")
                self.assertTrue(checkpoint["intent_only"])
                self.assertEqual((checkpoint["input"], checkpoint["output"]), (256, 16))
                self.assertNotIn("usage", checkpoint)
                self.assertEqual(checkpoint["usage_availability"]["reasoning"], "unavailable")
                self.assertTrue(result["truncated"])

                tools = ST.timeline("antigravity", path, PARENT, max_events=5, view="tools")
                self.assertEqual(tools["events"][0]["status"], "error")
                self.assertEqual(tools["events"][0]["label"], "Read")
                compactions = ST.timeline("antigravity", path, PARENT, view="compactions")
                self.assertEqual([event["kind"] for event in compactions["events"]], ["compaction"])
                mismatched = ST.timeline("antigravity", path, CHILD, view="all")
                self.assertFalse(mismatched["available"])
        self.assertEqual(calls[0][1:], (2, False))
        serialized = json.dumps(result) + json.dumps(tools) + json.dumps(compactions)
        for private in ("PRIVATE-PROMPT", "PRIVATE-ARGUMENT", "PRIVATE-REQUEST-ID", "/fixture/private", "arguments"):
            self.assertNotIn(private, serialized)

    def test_source_scoped_parent_ids_and_explicit_roles_do_not_cross_link(self):
        codex_row = _record()
        codex = {
            "source": "codex", "path": "/fixture/codex/rollout.jsonl", "_session_id": PARENT,
            "records": {TODAY + "\tFixture model": codex_row},
            "sessions": [{"source": "codex", "id": PARENT[:8], "project": "fixture-project", "model": "Fixture model",
                          "start": _stamp(10), "end": _stamp(12), "records": {TODAY + "\tFixture model": codex_row},
                          "asst": 1, "req": 1, "in": 256, "out": 16, "cr": 0, "cc": 0}],
        }
        parent = _antigravity_aggregate(PARENT, status="recorded", subagent_requests=2)
        child = _antigravity_aggregate(CHILD, subagent=True, parent_id=PARENT, root_id=PARENT,
                                       status="recorded", title="Synthetic child")
        child_alias = copy.deepcopy(child)
        child_alias["path"] = child["path"].replace("antigravity-ide", "antigravity")
        root_only = _antigravity_aggregate(ROOT_ONLY, subagent=False, root_id=PARENT,
                                           status="recorded", title="Synthetic unmarked session",
                                           subagent_requests=1)
        payload = self.dashboard_payload([codex, parent, child, child_alias, root_only])
        antigravity_sessions = [row for row in payload["sessions"] if row["source"] == "antigravity"]
        child_row = next(row for row in antigravity_sessions if row["rename_id"] == CHILD)
        parent_row = next(row for row in antigravity_sessions if row["rename_id"] == PARENT)
        root_only_row = next(row for row in antigravity_sessions if row["rename_id"] == ROOT_ONLY)
        self.assertTrue(child_row["subagent"])
        self.assertEqual(child_row["subagent_parent"]["id"], PARENT)
        self.assertEqual(parent_row["subagent_children"], 1)
        self.assertEqual(parent_row["subagent_requests"], 2)
        self.assertFalse(root_only_row["subagent"])
        self.assertNotIn("subagent_parent", root_only_row)
        self.assertEqual(root_only_row["subagent_requests"], 1)
        self.assertNotIn("subagent_children", root_only_row)
        codex_session = next(row for row in payload["sessions"] if row["source"] == "codex")
        self.assertNotIn("subagent_parent", codex_session)
        self.assertEqual(parent_row["rename_id"], codex_session["rename_id"])

    def test_editor_workspace_uses_private_cwd_and_links_only_explicit_parent(self):
        parent_row = _record(inp=400, out=20, cr=0, cc=0)
        child_row = _record(inp=200, out=10, cr=0, cc=0)
        parent = _antigravity_aggregate(PARENT, status="recorded", records=parent_row)
        parent["sessions"][0]["records"] = {TODAY + "\tUnknown": parent_row}
        parent["records"] = {TODAY + "\tUnknown": parent_row}
        child = _antigravity_aggregate(CHILD, subagent=True, parent_id=PARENT, root_id=PARENT,
                                       status="recorded", records=child_row)
        child["sessions"][0]["records"] = {TODAY + "\tUnknown": child_row}
        child["records"] = {TODAY + "\tUnknown": child_row}
        request = urlencode([("scope", "workspace"), ("range", "today"), ("root", "/fixture/private/workspace")])
        result = ES.build_snapshot(
            [(parent["path"], parent), (child["path"], child)], DEVICE, request, "fixture-revision",
            price_usage=D._priced_usage, deduplicate=D._one_per_conversation,
            identity=SN.identity, claude_parent_key=D._claude_parent_key,
            now=datetime.combine(date.fromisoformat(TODAY), time(12)).timestamp(),
        )
        self.assertEqual(result["summary"]["tokens"], 630)
        self.assertEqual(result["sessions_total"], 2)
        linked = next(row for row in result["sessions"] if row["id"] == CHILD)
        self.assertEqual(linked["parent_status"], "linked")
        self.assertEqual(linked["parent_key"], next(row["key"] for row in result["sessions"] if row["id"] == PARENT))
        self.assertTrue(linked["subagent"])
        self.assertNotIn("/fixture/private/workspace", json.dumps(result))

    def test_cache_61_retains_live_and_archived_entries_and_peer_reader_accepts_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            original = {"version": 61, "files": {
                "live": {"source": "antigravity", "records": {"2026-10-01\tUnknown": {"in": 4}}},
                "archive": {"source": "codex", "archived": True, "records": {"2026-09-01\tFixture": {"in": 8}}},
            }}
            encoded = json.dumps(original)
            path.write_text(encoded, encoding="utf-8")
            with patch.object(D, "CACHE_PATH", str(path)), \
                    patch.object(D, "_state", {"files": {}, "version": D.CACHE_VERSION}), \
                    patch.object(D, "_dirty", {"v": False}), \
                    patch.object(D, "_cache_error", {"v": None}), \
                    redirect_stderr(io.StringIO()):
                D.load_cache()
                self.assertEqual(set(D._state["files"]), {"live", "archive"})
                self.assertTrue(D._dirty["v"])
            self.assertEqual(path.read_text(encoding="utf-8"), encoded)
        self.assertTrue(D._peer_cache_ok(61))
        self.assertTrue(D._peer_cache_ok(62))
        self.assertFalse(D._peer_cache_ok(999))

    def test_peer_export_keeps_safe_summary_and_numeric_session_metadata_without_cwd(self):
        aggregate = _antigravity_aggregate(status="partial")
        with patch.object(D.P, "_ide_of", return_value="Antigravity IDE"), \
                patch.object(D.P, "activity_of", return_value={}):
            exported = D._export_agg(aggregate)
        serialized = json.dumps(exported)
        self.assertNotIn("/fixture/private/workspace", serialized)
        self.assertNotIn("cwd", serialized)
        self.assertEqual(exported["antigravity"]["usage_status"], "partial")
        self.assertEqual(exported["conversation_id"], PARENT.lower())
        self.assertEqual(exported["antigravity"]["undated_usage"]["req"], 0)
        self.assertEqual(exported["antigravity"]["unknown_model_usage"]["in"], 256)
        self.assertEqual(exported["sessions"][0]["in"], 256)
        self.assertEqual(exported["sessions"][0]["response_out"], 7)
        self.assertEqual(exported["sessions"][0]["subagent_requests"], 0)
        self.assertEqual(exported["sessions"][0]["record_availability"][TODAY + "\tUnknown"]["reasoning"], "unavailable")
        self.assertNotIn("prompt", serialized)

        peer_id = "e" * 32
        peer = {"cache_version": 62, "files": {aggregate["path"]: exported}}
        with patch.object(D, "_peer_cfg", return_value={"peers": {peer_id: {}}}), \
                patch.object(D, "_peer", {"mirrors": {}}):
            D._set_mirror(peer_id, peer)
            mirror = D._peer["mirrors"][peer_id][aggregate["path"]]
        self.assertEqual(mirror["antigravity"]["usage_status"], "partial")
        self.assertEqual(mirror["antigravity"]["unknown_model_usage"]["in"], 256)
        self.assertEqual(
            D._conversation_key(f"peer:{peer_id}:antigravity:{PARENT.lower()}", mirror),
            (peer_id, "antigravity", PARENT.lower()),
        )
        self.assertNotIn("cwd", mirror["sessions"][0])

        with patch.object(D, "_state", {"files": {aggregate["path"]: aggregate}}), \
                patch.object(D, "_local_id", return_value=DEVICE), \
                patch.object(D, "_peer", {"export": None}), \
                patch.object(D.P, "_ide_of", return_value="Antigravity IDE"), \
                patch.object(D.P, "activity_of", return_value={}):
            _, compressed = D._export_body()
        body = json.loads(D.gzip.decompress(compressed).decode("utf-8"))
        self.assertEqual(list(body["files"]), ["antigravity:" + PARENT.lower()])
        exported_json = json.dumps(body)
        self.assertNotIn("/fixture/private/workspace", exported_json)
        self.assertNotIn(aggregate["path"], exported_json)

    def test_malformed_usage_status_values_are_dropped_without_projection_errors(self):
        malformed = _antigravity_aggregate(status="recorded")
        malformed["antigravity"]["usage_status"] = {"private": "object"}
        malformed["antigravity"]["usage_availability"] = {"cache_read": []}
        malformed["antigravity"]["record_availability"] = {TODAY + "\tUnknown": {"reasoning": {}}}
        malformed["antigravity"]["undated_usage"] = {"in": {"private": "value"}, "req": -1}
        malformed["antigravity"]["unknown_model_usage"] = {"out": True, "reason": 17}
        malformed["sessions"][0]["usage_status"] = []
        malformed["sessions"][0]["usage_availability"] = {"cache_read": {"bad": True}}
        malformed["sessions"][0]["record_availability"] = {TODAY + "\tUnknown": {"cache_read": []}}
        with patch.object(D.P, "_ide_of", return_value="Antigravity IDE"), \
                patch.object(D.P, "activity_of", return_value={}):
            exported = D._export_agg(malformed)
        self.assertNotIn("usage_status", exported["antigravity"])
        self.assertNotIn("usage_availability", exported["antigravity"])
        self.assertNotIn("undated_usage", exported["antigravity"])
        self.assertEqual(exported["antigravity"]["unknown_model_usage"], {"reason": 17})
        self.assertNotIn("usage_status", exported["sessions"][0])
        self.assertNotIn("usage_availability", exported["sessions"][0])

        public_item = TQ._session_public_item(
            {"source": "antigravity", "usage_status": [],
             "usage_availability": {"cache_read": []}},
            {field: 0 for field in TQ._VECTOR_FIELDS}, {"device": {"id": DEVICE}}, {},
            False, False, {},
        )
        self.assertNotIn("usage_status", public_item)
        self.assertNotIn("usage_availability", public_item)

        safe_events = MCP._safe_timeline({"available": True, "events": [
            {"kind": "model", "timestamp": _stamp(), "effort": [], "status": {}},
            {"kind": "compaction", "timestamp": _stamp(), "trigger": {}},
        ]})
        self.assertNotIn("effort", safe_events["events"][0])
        self.assertNotIn("trigger", safe_events["events"][1])

        native = NA.project([(malformed["path"], malformed)], observed_at=_stamp(12),
                            identity=SN.identity, local_device=DEVICE)
        self.assertNotIn("usage_status", native["sessions"][0])
        editor = ES.build_snapshot(
            [(malformed["path"], malformed)], DEVICE,
            urlencode([("scope", "all"), ("range", "today")]), "fixture-revision",
            price_usage=D._priced_usage, deduplicate=D._one_per_conversation,
            identity=SN.identity, claude_parent_key=D._claude_parent_key,
            now=datetime.combine(date.fromisoformat(TODAY), time(12)).timestamp(),
        )
        self.assertNotIn("usage_status", editor["sessions"][0])

    def test_mcp_timeline_status_checkpoint_allowlist_and_source_alias(self):
        raw = {
            "available": True, "detail": "source log", "events": [
                {"kind": "tool", "timestamp": _stamp(), "label": "Read", "status": "error",
                 "prompt": "PRIVATE", "arguments": "PRIVATE"},
                {"kind": "checkpoint", "timestamp": _stamp(), "label": "Recorded checkpoint",
                 "input": 256, "output": 16, "intent_only": True,
                 "usage_availability": {"input_output": "recorded", "private": "bad"},
                 "cwd": "/fixture/private"},
            ],
        }
        safe = MCP._safe_timeline(raw)
        self.assertEqual(safe["events"][0]["status"], "error")
        checkpoint = safe["events"][1]
        self.assertEqual((checkpoint["input"], checkpoint["output"]), (256, 16))
        self.assertTrue(checkpoint["intent_only"])
        self.assertEqual(checkpoint["usage_availability"], {"input_output": "recorded"})
        self.assertNotIn("PRIVATE", json.dumps(safe))
        self.assertNotIn("/fixture/private", json.dumps(safe))
        self.assertEqual(MCP._safe_diagnostics({"unsupported_formats": [
            {"source": "antigravity", "format": "legacy_pb", "count": 2,
             "hint": "Standalone Antigravity legacy .pb conversations are unsupported; file contents were not read.",
             "path": "/fixture/private"},
        ]})["unsupported_formats"][0]["count"], 2)

    def test_runtime_packaging_allowlists_include_reader_and_attribution_assets(self):
        root = Path(__file__).resolve().parents[1]
        checks = {
            "install.py": ("antigravity.py",),
            "setup.py": ('"antigravity"',),
            "pyproject.toml": ("static/tool-icons/antigravity.svg",
                               "static/tool-icons/LICENSE.antigravity.txt"),
            "MANIFEST.in": ("include antigravity.py", "include docs/antigravity.md",
                            "include static/tool-icons/antigravity.svg", "include static/tool-icons/LICENSE.antigravity.txt"),
            "vscode/scripts/backend-files.js": ("antigravity.py", "static/tool-icons/antigravity.svg",
                                                "static/tool-icons/LICENSE.antigravity.txt"),
            "macos/AgentTelemetryMac/Scripts/package-app.sh": ("antigravity.py",),
            "tests/check_python_distribution.py": ("antigravity.py", "docs/antigravity.md",
                                                    "static/tool-icons/antigravity.svg"),
        }
        for filename, needles in checks.items():
            content = (root / filename).read_text(encoding="utf-8")
            for needle in needles:
                with self.subTest(filename=filename, needle=needle):
                    self.assertIn(needle, content)
        self.assertTrue((root / "static/tool-icons/antigravity.svg").is_file())
        self.assertTrue((root / "static/tool-icons/LICENSE.antigravity.txt").is_file())
        self.assertTrue((root / "docs/antigravity.md").is_file())


if __name__ == "__main__":
    unittest.main()
