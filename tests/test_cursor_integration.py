"""Mock-only coverage for Cursor's shared telemetry, timeline and native paths."""

from contextlib import ExitStack, closing
from datetime import date, datetime, time, timezone
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import dashboard as D
import cursor_data as CD
import native_activity as NA
import session_timeline as ST
import session_names as SN
import telemetry_query as TQ


TODAY = "2026-10-25"
LOCAL = "a" * 32
REMOTE = "b" * 32
PARENT = "composer-parent-full-id-123456"
CHILD = "composer-child-full-id-987654"
PARENT_UUID = "11111111-2222-4333-8444-555555555555"
MODEL = "Fixture Cursor Model"
UNAVAILABLE_FIELDS = {
    "input_output": "unavailable", "cache_read": "unavailable",
    "cache_write": "unavailable", "reasoning": "unavailable",
    "response_output": "unavailable",
}
PARTIAL_FIELDS = {
    "input_output": "partial", "cache_read": "unavailable",
    "cache_write": "unavailable", "reasoning": "unavailable",
    "response_output": "unavailable",
}


def _stamp(hour=12, day=TODAY):
    return datetime.combine(date.fromisoformat(day), time(hour), tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _usage_row(*, inp=0, out=0, asst=0, user=0, status="unavailable", availability=None):
    row = D._zero()
    row.update({"in": inp, "out": out, "asst": asst, "user": user})
    row["usage_status"] = status
    row["usage_availability"] = dict(availability or UNAVAILABLE_FIELDS)
    return row


def _cursor_session(identity, *, child=False, parent_id=None, usage="partial", title="Fixture Cursor session"):
    quality = PARTIAL_FIELDS if usage == "partial" else UNAVAILABLE_FIELDS
    row = _usage_row(inp=120 if usage == "partial" else 0,
                     out=18 if usage == "partial" else 0,
                     asst=1, user=1, status=usage, availability=quality)
    fields = {
        "_sid": identity, "id": identity[:8], "source": "cursor", "ide": "Cursor",
        "editor": "Cursor", "title": title, "project": "fixture-project", "model": MODEL,
        "start": _stamp(10), "end": _stamp(12), "in": row["in"], "out": row["out"],
        "cr": 0, "cc": 0, "asst": 1, "user": 1, "req": 0, "tools": 0,
        "active": 120.0, "subagents": 1 if not child else 0, "subagent": child,
        "usage_status": usage, "usage_availability": dict(quality),
        "records": {TODAY + "\t" + MODEL: row},
    }
    if parent_id:
        fields["parent_id"] = parent_id
    return fields


def _cursor_aggregate(path, sessions, *, device=None, extra=None):
    records = {}
    for session in sessions:
        for key, row in session["records"].items():
            slot = records.setdefault(key, D._zero())
            for field, value in row.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    slot[field] = slot.get(field, 0) + value
    agg = {
        "source": "cursor", "path": path, "project": "fixture-project", "editor": "Cursor",
        "ide": "Cursor", "records": records, "sessions": sessions,
        "tools": {TODAY + "\tCursor Search": 1},
        "hourly": {TODAY + "\t12": {"tokens": 138, "msgs": 1}},
        "state": {"ai_lines": {}},
    }
    if device:
        agg["_device"] = device
    if extra:
        agg.update(extra)
    return agg


class CursorIntegrationTests(unittest.TestCase):
    def dashboard_payload(self, aggregates, *, peer_items=()):
        files = {aggregate["path"]: aggregate for aggregate in aggregates}
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(D, "_state", {"files": files, "version": D.CACHE_VERSION}))
        stack.enter_context(patch.object(D, "_local_id", return_value=LOCAL))
        stack.enter_context(patch.object(D, "_project_preferences", return_value=({"version": 1, "aliases": {}, "roots": {}}, None)))
        stack.enter_context(patch.object(D, "_peer_items", return_value=list(peer_items)))
        stack.enter_context(patch.object(D, "_peer_cfg", return_value={"device_id": LOCAL, "peers": {}}))
        stack.enter_context(patch.object(D, "_devices_meta", return_value=[{"id": LOCAL, "name": "Fixture local", "local": True}]))
        stack.enter_context(patch.object(D.N, "project_items", side_effect=lambda items, *_args: items))
        stack.enter_context(patch.object(D.P, "_ide_of", return_value="Cursor"))
        stack.enter_context(patch.object(D.P, "activity_of", return_value={}))
        stack.enter_context(patch.object(D.SN, "display_names", return_value={}))
        stack.enter_context(patch.object(D, "_mcp_servers", return_value=[]))
        stack.enter_context(patch.object(D, "_context_files", return_value=[]))
        stack.enter_context(patch.object(D, "_installed", return_value={"items": [], "used": []}))
        stack.enter_context(patch.object(D, "_codex_config", return_value={"effort": None}))
        stack.enter_context(patch.object(D, "DEVICE", {"name": "Fixture local", "os": "test"}))
        return D._build_payload_locked()

    def test_usage_quality_full_ids_and_explicit_parent_links_reach_payload_and_mcp(self):
        parent = _cursor_session(PARENT)
        parent.update({"current_model": "Fixture current setting", "model_status": "unavailable",
                       "requested_models": ["Fixture requested model"], "requested_model_status": "recorded",
                       "current_context_tokens": 24000, "current_context_percent": 48.0,
                       "current_context_limit": 50000,
                       "current_context_asof": _stamp(11),
                       "undated_usage": {"in": 64, "out": 9, "asst": 2,
                                         "model": "PRIVATE-UNDATED-MODEL",
                                         "prompt": "PRIVATE-UNDATED-PROMPT",
                                         "cwd": "/Users/private/undated-work"}})
        child = _cursor_session(CHILD, child=True, parent_id=PARENT, title="Fixture child")
        child["undated_usage"] = {
            "in": True, "out": -3, "asst": False,
            "model": "PRIVATE-INVALID-LOCAL-MODEL", "secret": "PRIVATE-INVALID-LOCAL-SENTINEL",
        }
        payload = self.dashboard_payload([_cursor_aggregate("/fixture/cursor/state.vscdb", [parent, child])])

        row = next(record for record in payload["records"] if record["source"] == "cursor")
        self.assertEqual(row["usage_status"], "partial")
        self.assertEqual(row["usage_availability"]["input_output"], "partial")
        self.assertEqual(row["usage_availability"]["cache_read"], "unavailable")
        parent_row = next(session for session in payload["sessions"] if session["rename_id"] == PARENT)
        child_row = next(session for session in payload["sessions"] if session["rename_id"] == CHILD)
        self.assertEqual(parent_row["id"], PARENT[:8])
        self.assertNotIn("undated_usage", child_row)
        self.assertNotIn("PRIVATE-INVALID-LOCAL-MODEL", json.dumps(payload))
        self.assertNotIn("PRIVATE-INVALID-LOCAL-SENTINEL", json.dumps(payload))
        self.assertEqual(child_row["subagent_parent"], {
            "id": PARENT, "title": "Fixture Cursor session", "source": "cursor", "device": LOCAL,
        })
        self.assertTrue(child_row["rename_id"].endswith(CHILD[-6:]))
        self.assertFalse(child_row["rename_available"])

        usage = TQ.query_usage(payload, {"period": "all", "group_by": ["tool"]}, today=TODAY)
        cursor_precision = next(item for item in usage["source_precision"] if item["tool"] == "Cursor")
        self.assertEqual(cursor_precision["precision"], "lower_bound")
        self.assertEqual(cursor_precision["usage_status"], "partial")
        self.assertEqual(cursor_precision["usage_availability"]["cache_read"], "unavailable")
        listed = TQ.list_sessions(payload, {"period": "all", "tools": ["Cursor"]}, today=TODAY)
        child_public = next(session for session in listed["sessions"] if session["tool"] == "cursor"
                            and session.get("model") == MODEL and session["subagent"])
        self.assertEqual(child_public["usage_status"], "partial")
        self.assertEqual(child_public["usage_availability"]["input_output"], "partial")
        parent_public = next(session for session in listed["sessions"] if session["tool"] == "cursor"
                             and not session["subagent"])
        self.assertEqual(parent_public["model_status"], "unavailable")
        self.assertEqual(parent_public["current_model"], "Fixture current setting")
        self.assertEqual(parent_public["requested_models"], ["Fixture requested model"])
        self.assertEqual(parent_public["current_context_tokens"], 24000)
        self.assertEqual(parent_public["current_context_limit"], 50000)
        self.assertEqual(parent_row["undated_usage"], {"in": 64, "out": 9, "asst": 2})
        self.assertNotIn("PRIVATE-UNDATED-MODEL", json.dumps(payload))
        self.assertNotIn("PRIVATE-UNDATED-PROMPT", json.dumps(payload))
        self.assertNotIn("/Users/private/undated-work", json.dumps(payload))
        self.assertEqual(parent_public["tokens"], 138)
        self.assertEqual(parent_public["undated_usage"], {"in": 64, "out": 9, "asst": 2})
        self.assertEqual(parent_public["undated_usage_scope"],
                         "whole-session (not filtered by date range)")
        self.assertEqual(listed["totals"]["tokens"], 276,
                         "undated counters are disclosure only and never change selected totals")

        scoped = TQ.list_sessions(payload, {"from": TODAY, "to": TODAY}, today=TODAY)
        scoped_parent = next(session for session in scoped["sessions"] if session["tool"] == "cursor"
                             and not session["subagent"])
        self.assertEqual(scoped_parent["undated_usage"], {"in": 64, "out": 9, "asst": 2})
        self.assertEqual(scoped_parent["undated_usage_scope"],
                         "whole-session (not filtered by date range)")

        payload_parent = next(session for session in payload["sessions"]
                              if session["rename_id"] == PARENT)
        payload_parent["undated_usage"] = {
            "in": True, "out": -3, "asst": 2, "model": "PRIVATE-INVALID-MODEL",
            "secret": "PRIVATE-INVALID-SENTINEL",
        }
        safe_mcp = TQ.list_sessions(payload, {"period": "all"}, today=TODAY)
        safe_parent = next(session for session in safe_mcp["sessions"] if session["tool"] == "cursor"
                           and not session["subagent"])
        self.assertEqual(safe_parent["undated_usage"], {"asst": 2})
        self.assertNotIn("model", safe_parent["undated_usage"])
        self.assertNotIn("PRIVATE-INVALID-MODEL", json.dumps(safe_mcp))
        self.assertNotIn("PRIVATE-INVALID-SENTINEL", json.dumps(safe_mcp))
        self.assertNotIn(PARENT, json.dumps(usage), "MCP session references are opaque")

    def test_metadata_only_cursor_records_remain_unavailable_in_usage_and_sessions(self):
        session = _cursor_session(PARENT, usage="unavailable", title="Metadata only")
        payload = self.dashboard_payload([_cursor_aggregate(
            "/fixture/cursor/metadata-only.vscdb", [session])])
        record = next(row for row in payload["records"] if row["source"] == "cursor")
        self.assertEqual(record["asst"], 1)
        self.assertEqual(record["usage_status"], "unavailable")
        self.assertEqual(record["usage_availability"]["input_output"], "unavailable")

        usage = TQ.query_usage(payload, {"period": "all", "group_by": []}, today=TODAY)
        precision = next(row for row in usage["source_precision"] if row["tool"] == "Cursor")
        self.assertEqual(precision["precision"], "lower_bound")
        self.assertEqual(precision["usage_status"], "unavailable")
        self.assertEqual(precision["usage_availability"]["input_output"], "unavailable")

        listed = TQ.list_sessions(payload, {"period": "all"}, today=TODAY)
        item = next(row for row in listed["sessions"] if row["tool"] == "cursor")
        self.assertEqual(item["usage_status"], "unavailable")
        self.assertIsNone(item["tokens"])
        self.assertIsNone(item["estimated_cost_usd"])
        self.assertIn("cursor_token_usage", item["missing_fields"])

    def test_parent_resolution_is_scoped_by_device_and_rejects_ambiguous_duplicates(self):
        parent = _cursor_session(PARENT)
        remote_child = _cursor_session(CHILD, child=True, parent_id=PARENT)
        remote_agg = _cursor_aggregate("/peer/cursor/state.vscdb", [remote_child], device=REMOTE)
        payload = self.dashboard_payload([_cursor_aggregate("/local/cursor/state.vscdb", [parent])],
                                         peer_items=[("peer:" + REMOTE + ":remote", remote_agg)])
        child = next(session for session in payload["sessions"] if session["source"] == "cursor"
                     and session["device"] == REMOTE)
        self.assertNotIn("subagent_parent", child)

        first = _cursor_aggregate("/local/one/state.vscdb", [_cursor_session(PARENT)])
        duplicate = _cursor_aggregate("/local/two/state.vscdb", [_cursor_session(PARENT)])
        linked_child = _cursor_aggregate("/local/child/state.vscdb", [
            _cursor_session(CHILD, child=True, parent_id=PARENT)])
        payload = self.dashboard_payload([first, duplicate, linked_child])
        child = next(session for session in payload["sessions"] if session["rename_id"] == CHILD)
        self.assertNotIn("subagent_parent", child, "two same-device candidates make the link ambiguous")

    def test_timeline_resolves_full_native_id_and_verifies_discovered_database(self):
        path = "/fixture/Cursor/User/globalStorage/state.vscdb"
        aggregate = _cursor_aggregate(path, [_cursor_session(PARENT)])
        with patch.object(D, "_state", {"files": {path: aggregate}}), \
                patch.object(D, "_local_id", return_value=LOCAL), \
                patch.object(D.P, "discover", return_value=[("cursor", path, "Cursor")]), \
                patch.object(D.ST, "timeline", return_value={"available": True, "events": [],
                                                              "truncated": False, "detail": "source log"}) as timeline:
            result = D.build_session_timeline(LOCAL, "cursor", PARENT)
            self.assertTrue(result["available"])
            timeline.assert_called_once_with("cursor", path, PARENT, date_from=None, date_to=None, view="all")
            timeline.reset_mock()
            short = D.build_session_timeline(LOCAL, "cursor", PARENT[:8])
            self.assertFalse(short["available"])
            timeline.assert_not_called()
            other_device = D.build_session_timeline(REMOTE, "cursor", PARENT)
            self.assertIn("original device", other_device["detail"])
            timeline.assert_not_called()

    def test_native_summary_hides_partial_and_unavailable_totals(self):
        partial = _cursor_session(PARENT, usage="partial", title="Partial session")
        unavailable = _cursor_session(CHILD, child=True, usage="unavailable", title="Unavailable session")
        input_output_only = _cursor_session(PARENT_UUID, usage="recorded", title="Input output only")
        input_output_only.update({"in": 120, "out": 18, "usage_status": "recorded",
                                  "usage_availability": {
                                      "input_output": "recorded", "cache_read": "unavailable",
                                      "cache_write": "unavailable", "reasoning": "unavailable",
                                      "response_output": "unavailable",
                                  }})
        items = [
            ("/fixture/state.vscdb", _cursor_aggregate(
                "/fixture/state.vscdb", [partial, unavailable, input_output_only])),
        ]
        result = NA.project(items, observed_at=_stamp(), local_device=LOCAL)
        partial_row = next(row for row in result["sessions"] if row["title"] == "Partial session")
        unavailable_row = next(row for row in result["sessions"] if row["title"] == "Unavailable session")
        incomplete_row = next(row for row in result["sessions"] if row["title"] == "Input output only")
        self.assertIsNone(partial_row["tokens"])
        self.assertEqual(partial_row["usage_status"], "partial")
        self.assertIsNone(unavailable_row["tokens"])
        self.assertEqual(unavailable_row["usage_status"], "unavailable")
        self.assertIsNone(incomplete_row["tokens"], "cache totals are still unavailable")
        self.assertEqual(incomplete_row["usage_status"], "recorded")
        rendered = json.dumps(result)
        self.assertNotIn(PARENT, rendered)
        self.assertNotIn(CHILD, rendered)

    def test_timeline_filters_before_cap_and_emits_only_allowlisted_metadata(self):
        database = Path(tempfile.gettempdir()) / "cursor-timeline-fixture.db"
        database.touch()
        self.addCleanup(lambda: database.unlink(missing_ok=True))
        events = [
            {"timestamp": _stamp(9, "2026-10-24"), "kind": "request", "model": MODEL,
             "usage": {"input": 999, "output": 1}, "usage_availability": {"input_output": "recorded"},
             "prompt": "PRIVATE-OLD-PROMPT"},
            {"timestamp": _stamp(10), "kind": "request", "model": MODEL,
             "usage": {"input": 0, "output": 0}, "usage_status": "unavailable",
             "usage_availability": {"input_output": "unavailable", "cache_read": "unavailable",
                                     "cache_write": "unavailable"},
             "cwd": "/Users/private/work", "prompt": "PRIVATE-PROMPT"},
            {"timestamp": _stamp(11), "kind": "request", "model": MODEL,
             "usage": {"input": 22, "output": 3}, "usage_status": "partial",
             "usage_availability": {"input_output": "partial", "cache_read": "unavailable",
                                     "cache_write": "unavailable"},
             "content": "PRIVATE-CONTENT"},
        ]

        class CursorReadError(Exception):
            pass

        fake = types.ModuleType("cursor_data")
        fake.CursorReadError = CursorReadError

        def read_conversation(path, identity, *, max_events, event_filter):
            self.assertEqual(identity, PARENT)
            self.assertEqual(event_filter, {"date_from": TODAY, "date_to": TODAY, "view": "requests"})
            filtered = [event for event in events if ST._cursor_event_matches(
                event, event_filter["date_from"], event_filter["date_to"], event_filter["view"])]
            return {"conversation_id": identity, "events": filtered[:max_events],
                    "events_truncated": len(filtered) > max_events, "detail_status": "recorded"}

        fake.read_conversation = read_conversation
        with patch.dict(sys.modules, {"cursor_data": fake}):
            result = ST.timeline("cursor", database, PARENT, max_events=1,
                                 date_from=TODAY, date_to=TODAY, view="requests")
        self.assertTrue(result["available"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["events"]), 1)
        event = result["events"][0]
        self.assertEqual(event["usage_status"], "unavailable")
        self.assertNotIn("input", event)
        self.assertEqual(event["usage_availability"]["input_output"], "unavailable")
        serialized = json.dumps(result)
        for private in ("PRIVATE-OLD-PROMPT", "PRIVATE-PROMPT", "PRIVATE-CONTENT", "/Users/private", "cursor-timeline-fixture"):
            self.assertNotIn(private, serialized)

    def test_sqlite_timeline_reader_filters_before_cap_and_excludes_source_content(self):
        composer_id = "11111111-2222-4333-8444-555555555555"
        old_stamp = _stamp(9, "2026-10-24")
        current_stamp = _stamp(10)
        later_stamp = _stamp(11)
        with tempfile.TemporaryDirectory() as folder:
            user = Path(folder) / "Cursor" / "User"
            store = user / "globalStorage" / "state.vscdb"
            store.parent.mkdir(parents=True)
            with closing(sqlite3.connect(store)) as connection, connection:
                connection.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
                composer = {
                    "composerId": composer_id,
                    "name": "Safe fixture conversation",
                    "createdAt": old_stamp,
                    "lastUpdatedAt": later_stamp,
                    "modelConfig": {"modelName": "cursor-current-setting"},
                    "cwd": "/Users/private/workspace",
                }
                rows = [("composerData:" + composer_id, json.dumps(composer))]
                bubbles = [
                    ("bubbleId:" + composer_id + ":old-user", {
                        "type": 1, "createdAt": old_stamp, "text": "PRIVATE-OLD-PROMPT",
                        "modelInfo": {"modelName": "cursor-requested-only"},
                    }),
                    ("bubbleId:" + composer_id + ":old-response", {
                        "type": 2, "createdAt": old_stamp, "requestId": "PRIVATE-OLD-REQUEST",
                        "text": "PRIVATE-OLD-RESPONSE", "modelInfo": {"modelName": "cursor-old-model"},
                        "tokenCount": {"inputTokens": 1, "outputTokens": 2},
                    }),
                    ("bubbleId:" + composer_id + ":current-response", {
                        "type": 2, "createdAt": current_stamp, "requestId": "PRIVATE-CURRENT-REQUEST",
                        "text": "PRIVATE-RESPONSE", "modelInfo": {"modelName": "cursor-observed-model"},
                        "tokenCount": {"inputTokens": 3, "outputTokens": 4},
                        "toolFormerData": {"name": "run_search", "args": "PRIVATE-ARGS",
                                           "result": "PRIVATE-RESULT"},
                    }),
                    ("bubbleId:" + composer_id + ":later-response", {
                        "type": 2, "createdAt": later_stamp, "requestId": "PRIVATE-LATER-REQUEST",
                        "text": "PRIVATE-LATER-RESPONSE", "modelInfo": {"modelName": "cursor-later-model"},
                        "tokenCount": {"inputTokens": 5, "outputTokens": 6},
                    }),
                ]
                rows.extend((key, json.dumps(value)) for key, value in bubbles)
                rows.append(("agentKv:blob:fixture", json.dumps({
                    "role": "assistant",
                    "providerOptions": {"cursor": {"requestId": "PRIVATE-CURRENT-REQUEST",
                                                       "modelName": "Agent Linked Model"}},
                    "content": [{"type": "text", "text": "PRIVATE-AGENT-CONTENT"}],
                }).encode("utf-8")))
                connection.executemany("INSERT INTO cursorDiskKV(key, value) VALUES (?, ?)", rows)

            result = ST.timeline("cursor", store, composer_id, max_events=1,
                                 date_from=TODAY, date_to=TODAY, view="requests")
            self.assertTrue(result["available"])
            self.assertTrue(result["truncated"], "two current requests must be capped only after filtering")
            self.assertEqual(len(result["events"]), 1)
            event = result["events"][0]
            self.assertEqual(event["kind"], "request")
            self.assertEqual(event["timestamp"], current_stamp)
            self.assertEqual(event["model"], "cursor-observed-model")
            serialized = json.dumps(result)
            for private in (
                "PRIVATE-OLD-PROMPT", "PRIVATE-OLD-RESPONSE", "PRIVATE-RESPONSE",
                "PRIVATE-LATER-RESPONSE", "PRIVATE-ARGS", "PRIVATE-RESULT",
                "PRIVATE-AGENT-CONTENT", "PRIVATE-CURRENT-REQUEST", "/Users/private",
                str(store), "cursor-current-setting", "cursor-requested-only",
            ):
                self.assertNotIn(private, serialized)
            with self.assertRaises(CD.CursorReadError):
                CD.read_conversation(store, composer_id[:8])

    def test_peer_export_uses_opaque_store_key_and_safe_cursor_allowlists(self):
        path = "/Users/private/Cursor/User/globalStorage/state.vscdb"
        session = _cursor_session(PARENT_UUID, title="Fixture shared session")
        session.update({
            "current_model": "Fixture current setting", "current_context_tokens": 12000,
            "current_context_limit": 64000,
            "current_context_percent": 45.5, "current_context_asof": _stamp(11),
            "requested_models": ["Fixture requested model"], "requested_model_status": "recorded",
            "undated_usage": {"in": 31, "out": 7, "asst": 1,
                              "model": "PRIVATE-UNDATED-MODEL", "prompt": "PRIVATE-UNDATED-PROMPT",
                              "unexpected": {"cwd": "/Users/private/undated-work"}},
            "response_model_status": "partial", "cwd": "/Users/private/workspace",
            "events": [{"prompt": "PRIVATE-RAW-EVENT"}], "request_join_id": "PRIVATE-REQUEST-ID",
            "agentKv": "PRIVATE-BLOB",
        })
        aggregate = _cursor_aggregate(path, [session], extra={
            "cwd": "/Users/private/workspace", "events": [{"prompt": "PRIVATE-RAW-EVENT"}],
            "request_join_id": "PRIVATE-REQUEST-ID", "agentKv": "PRIVATE-BLOB",
            "cursor": {"usage_status": "partial", "model_status": "partial",
                       "detail_status": "recorded", "usage_availability": PARTIAL_FIELDS,
                       "events": [{"prompt": "PRIVATE-RAW-EVENT"}],
                       "request_join_id": "PRIVATE-REQUEST-ID", "raw_blob": "PRIVATE-BLOB"},
        })
        with patch.object(D.P, "_ide_of", return_value="Cursor"), \
                patch.object(D.P, "activity_of", return_value={}):
            exported = D._export_agg(aggregate)
        self.assertNotIn("path", exported)
        self.assertEqual(exported["cursor"]["usage_status"], "partial")
        self.assertNotIn("events", exported["cursor"])
        self.assertEqual(exported["sessions"][0]["_sid"], PARENT_UUID)
        self.assertEqual(exported["sessions"][0]["current_model"], "Fixture current setting")
        self.assertEqual(exported["sessions"][0]["current_context_limit"], 64000)
        self.assertEqual(exported["sessions"][0]["requested_models"], ["Fixture requested model"])
        self.assertEqual(exported["sessions"][0]["undated_usage"], {"in": 31, "out": 7, "asst": 1})
        self.assertNotIn("cwd", exported["sessions"][0])

        remote = dict(exported, _device=REMOTE)
        remote_payload = self.dashboard_payload([], peer_items=[("peer:cursor-store", remote)])
        remote_session = remote_payload["sessions"][0]
        remote_record = next(record for record in remote_payload["records"] if record["source"] == "cursor")
        self.assertEqual(remote_session["rename_id"], PARENT_UUID)
        self.assertEqual(remote_session["device"], REMOTE)
        self.assertEqual(remote_record["usage_status"], "partial")

        with patch.object(D, "_state", {"files": {path: aggregate}}), \
                patch.object(D, "_local_id", return_value=LOCAL), \
                patch.object(D, "_peer", {"export": None}), \
                patch.object(D.P, "_ide_of", return_value="Cursor"), \
                patch.object(D.P, "activity_of", return_value={}):
            _, compressed = D._export_body()
        body = json.loads(D.gzip.decompress(compressed).decode("utf-8"))
        self.assertEqual(list(body["files"]), [D._cursor_store_export_key(path)])
        self.assertEqual(D._cursor_store_export_key(path), D._cursor_store_export_key(
            "/Users/private/Cursor/User/../User/globalStorage/state.vscdb"))
        serialized = json.dumps(body)
        for private in (path, "PRIVATE-RAW-EVENT", "PRIVATE-REQUEST-ID", "PRIVATE-BLOB",
                        "PRIVATE-UNDATED-MODEL", "PRIVATE-UNDATED-PROMPT",
                        "/Users/private/undated-work", "/Users/private/workspace"):
            self.assertNotIn(private, serialized)

        malformed_session = dict(session, undated_usage={
            "in": True, "out": -4, "asst": 3, "model": "PRIVATE-INVALID-MODEL",
            "prompt": "PRIVATE-INVALID-SENTINEL",
        })
        malformed_aggregate = _cursor_aggregate(path, [malformed_session])
        with patch.object(D.P, "_ide_of", return_value="Cursor"), \
                patch.object(D.P, "activity_of", return_value={}):
            malformed_export = D._export_agg(malformed_aggregate)
        self.assertEqual(malformed_export["sessions"][0]["undated_usage"], {"asst": 3})
        malformed_json = json.dumps(malformed_export)
        self.assertNotIn("PRIVATE-INVALID-MODEL", malformed_json)
        self.assertNotIn("PRIVATE-INVALID-SENTINEL", malformed_json)


if __name__ == "__main__":
    unittest.main()
