import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cursor_data as CD
import parser as P


def _stamp(minute, day="2026-10-25"):
    return f"{day}T10:{minute:02d}:00Z"


def _make_store(path, *, headers=False, item_table=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value BLOB)")
    if headers:
        connection.execute(
            "CREATE TABLE composerHeaders (composerId TEXT, createdAt TEXT, lastUpdatedAt TEXT, "
            "isArchived INTEGER, isSubagent INTEGER, subagentTypeName TEXT, value BLOB)"
        )
    if item_table:
        connection.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
    return connection


def _composer(connection, composer_id, value):
    connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,?)",
                       ("composerData:" + composer_id, json.dumps(value)))


def _bubble(connection, composer_id, bubble_id, value):
    connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,?)",
                       (f"bubbleId:{composer_id}:{bubble_id}", json.dumps(value)))


class CursorParserTests(unittest.TestCase):
    def test_missing_required_store_table_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
            store.parent.mkdir(parents=True)
            connection = sqlite3.connect(store)
            connection.execute("CREATE TABLE unrelated (value TEXT)")
            connection.commit()
            connection.close()
            with self.assertRaises(CD.CursorReadError):
                CD.read_store(store, strict=True)

    def test_timeline_date_filter_uses_local_day_before_event_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
            connection = _make_store(store)
            composer_id = "99999999-2222-4333-8444-555555555555"
            _composer(connection, composer_id, {
                "composerId": composer_id,
                "createdAt": "2026-10-24T18:00:00Z",
            })
            _bubble(connection, composer_id, "reply-before-local-midnight", {
                "type": 2, "createdAt": "2026-10-24T18:30:00Z",
                "requestId": "request-before-local-midnight",
                "tokenCount": {"inputTokens": 1, "outputTokens": 1},
            })
            _bubble(connection, composer_id, "reply-after-utc-midnight", {
                "type": 2, "createdAt": "2026-10-25T17:30:00Z",
                "requestId": "request-after-utc-midnight",
                "tokenCount": {"inputTokens": 1, "outputTokens": 1},
            })
            connection.commit()
            connection.close()

            # Fixed UTC+06 keeps the test independent of the host timezone.
            localize = lambda value: value.astimezone(
                CD._dt.timezone(CD._dt.timedelta(hours=6)))
            result = CD.read_conversation(
                store, composer_id, max_events=1,
                event_filter={"date_from": "2026-10-25", "date_to": "2026-10-25",
                              "view": "requests"},
                localize=localize,
            )
            self.assertEqual(len(result["events"]), 1)
            self.assertEqual(result["events"][0]["timestamp"], "2026-10-24T18:30:00Z")
            self.assertEqual(result["events"][0]["kind"], "request")
            self.assertTrue(result["events_truncated"])

    def test_per_message_usage_model_quality_and_totals_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
            connection = _make_store(store, headers=True, item_table=True)
            composer_id = "11111111-2222-4333-8444-555555555555"
            _composer(connection, composer_id, {
                "composerId": composer_id, "name": "Synthetic conversation",
                "createdAt": _stamp(0), "lastUpdatedAt": _stamp(4),
                "modelConfig": {"modelName": "composer-2.5", "maxMode": True},
                "promptTokenBreakdown": {"totalUsedTokens": 32000},
                "contextUsagePercent": 48.5, "contextTokenLimit": 65000,
                "totalLinesAdded": 7, "totalLinesRemoved": 2,
            })
            _bubble(connection, composer_id, "user-1", {
                "type": 1, "createdAt": _stamp(0),
                "modelInfo": {"modelName": "grok-4.7"}, "text": "PRIVATE PROMPT",
            })
            _bubble(connection, composer_id, "response-1", {
                "type": 2, "createdAt": _stamp(1), "requestId": "request-1",
                "tokenCount": {"inputTokens": 100, "outputTokens": 10},
                "toolFormerData": {"name": "run_search", "toolCallId": "call-1",
                                   "args": "PRIVATE ARGS", "result": "PRIVATE RESULT"},
            })
            _bubble(connection, composer_id, "user-2", {
                "type": 1, "createdAt": _stamp(2),
                "modelInfo": {"modelName": "default"},
            })
            _bubble(connection, composer_id, "response-2", {
                "type": 2, "createdAt": _stamp(3),
                "modelInfo": {"modelName": "claude-sonnet-4-6"},
                "tokenCount": {"inputTokens": 0, "outputTokens": 0},
                "thinkingDurationMs": 4321,
                "toolFormerData": {"name": "apply_patch", "toolCallId": "call-2"},
            })
            connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,?)", (
                "agentKv:blob:one", json.dumps({
                    "role": "assistant",
                    "providerOptions": {"cursor": {"requestId": "request-1", "modelName": "gpt-5.4-codex"}},
                    "content": [{"type": "tool-call", "toolName": "run_search", "toolCallId": "call-1"}],
                    "text": "PRIVATE AGENT CONTENT",
                }).encode("utf-8")))
            connection.execute("INSERT INTO ItemTable(key,value) VALUES (?,?)", (
                "aiCodeTracking.dailyStats:2026-10-25", json.dumps({
                    "date": "2026-10-25", "tabSuggestedLines": 11, "tabAcceptedLines": 3,
                    "composerSuggestedLines": 8, "composerAcceptedLines": 2,
                })))
            connection.commit()
            connection.close()

            agg = P.update_file(None, "cursor", str(store), "Cursor", {})
            session = agg["sessions"][0]
            dated_usage = [row for key, row in agg["records"].items() if not key.endswith("\t(user)")]
            self.assertEqual(session["_sid"], composer_id)
            self.assertEqual(session["id"], composer_id[:8])
            self.assertEqual(session["model_status"], "recorded")
            self.assertEqual(session["response_model_status"], "recorded")
            self.assertEqual(session["requested_model_status"], "partial")
            self.assertEqual(session["usage_status"], "partial")
            self.assertEqual(session["current_model"], P._normalize_cursor_model("composer-2.5"))
            self.assertNotEqual(session["current_model"], "gpt-5.4-codex")
            self.assertEqual(session["current_context_tokens"], 32000)
            self.assertEqual(session["current_context_percent"], 48.5)
            self.assertEqual(session["current_context_limit"], 65000)
            self.assertEqual(session["current_context_asof"], _stamp(4))
            self.assertEqual(session["think_ms"], 4321)
            self.assertEqual(session["reason"], 0)
            self.assertEqual(session["lines_add"], 7)
            self.assertEqual(session["lines_del"], 2)
            self.assertEqual(agg["state"]["ai_lines"]["2026-10-25"]["composer_accepted"], 2)
            self.assertTrue(all(row["usage_status"] in ("recorded", "unavailable") for row in dated_usage))
            self.assertEqual(sum(row.get("in", 0) + row.get("out", 0) for row in agg["records"].values()), 110)
            self.assertEqual(agg["totals"]["in"] + agg["totals"]["out"], 110)
            self.assertEqual(sum(row.get("asst", 0) for row in agg["records"].values()), 2)
            self.assertEqual(agg["totals"]["asst"], 2)
            self.assertEqual(sum(row.get("user", 0) for row in agg["records"].values()), 2)
            self.assertEqual(agg["totals"]["user"], 2)
            self.assertEqual(sum(agg["tools"].values()), 2)
            self.assertEqual(agg["totals"]["tools"], 2)
            self.assertEqual(sum(row.get("tools", 0) for row in agg["records"].values()), 2)
            self.assertEqual(sum(row.get("active", 0) for row in agg["records"].values()), session["active"])
            self.assertEqual(sum(row.get("active", 0) for row in agg["records"].values()), 180)
            self.assertEqual(agg["cursor"]["usage_status"], "partial")
            self.assertEqual(agg["cursor"]["usage_availability"]["cache_read"], "unavailable")
            self.assertEqual(agg["cursor"]["detail_status"], "partial")
            self.assertNotIn("cwd", session)
            rendered = json.dumps(agg)
            for private in ("PRIVATE PROMPT", "PRIVATE ARGS", "PRIVATE RESULT", "PRIVATE AGENT CONTENT"):
                self.assertNotIn(private, rendered)
            activity = CD.read_conversation(
                store, composer_id, event_filter={"view": "activity"})
            response = next(event for event in activity["events"] if event["kind"] == "model")
            self.assertEqual(response["model"], "gpt-5.4-codex")
            self.assertEqual(response["usage"], {"input": 100, "output": 10})
            requests = CD.read_conversation(
                store, composer_id, event_filter={"view": "requests"})
            self.assertEqual(sum(event["kind"] == "request" for event in requests["events"]), 1)
            self.assertNotIn("PRIVATE AGENT CONTENT", json.dumps(activity))

    def test_workspace_mapping_and_only_explicit_subagent_links(self):
        with tempfile.TemporaryDirectory() as directory:
            user_root = Path(directory) / "Cursor" / "User"
            store = user_root / "globalStorage" / "state.vscdb"
            connection = _make_store(store, headers=True)
            parent_id = "22222222-2222-4333-8444-555555555555"
            child_id = "33333333-2222-4333-8444-555555555555"
            best_id = "44444444-2222-4333-8444-555555555555"
            _composer(connection, parent_id, {
                "composerId": parent_id, "name": "Parent", "createdAt": _stamp(0),
                "subComposerIds": [child_id, best_id], "subagentComposerIds": [child_id],
                "numSubComposers": 2,
            })
            _composer(connection, child_id, {
                "composerId": child_id, "name": "Child", "createdAt": _stamp(1),
                "isSubagent": True, "subagentTypeName": "research",
                "workspaceIdentifier": "file:///fixture/repos/ChildProject",
            })
            _composer(connection, best_id, {
                "composerId": best_id, "name": "Best of N", "createdAt": _stamp(2),
                "isBestOfNSubcomposer": True,
            })
            for cid, bid, minute in ((parent_id, "p", 0), (child_id, "c", 1), (best_id, "b", 2)):
                _bubble(connection, cid, bid, {
                    "type": 2, "createdAt": _stamp(minute),
                    "modelInfo": {"modelName": "fixture-model"},
                    "tokenCount": {"inputTokens": 5, "outputTokens": 1},
                })
            connection.commit()
            connection.close()

            workspace = user_root / "workspaceStorage" / "fixture-workspace"
            workspace.mkdir(parents=True)
            (workspace / "workspace.json").write_text(json.dumps({
                "folder": "file:///fixture/repos/ParentProject"}), encoding="utf-8")
            workspace_db = sqlite3.connect(workspace / "state.vscdb")
            workspace_db.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
            workspace_db.execute("INSERT INTO ItemTable VALUES (?,?)", (
                "composer.composerData", json.dumps({"allComposers": [
                    {"composerId": parent_id},
                ]})))
            workspace_db.commit()
            workspace_db.close()

            agg = P.update_file(None, "cursor", str(store), "Cursor", {})
            sessions = {row["_sid"]: row for row in agg["sessions"]}
            self.assertEqual(sessions[parent_id]["project"], "ParentProject")
            self.assertEqual(sessions[child_id]["project"], "ChildProject")
            self.assertEqual(sessions[parent_id]["subagents"], 1)
            self.assertEqual(sessions[child_id]["parent_id"], parent_id)
            self.assertTrue(sessions[child_id]["subagent"])
            self.assertFalse(sessions[best_id]["subagent"])
            self.assertIsNone(sessions[best_id]["parent_id"])
            self.assertEqual(agg["_cursor_cwds"][parent_id], "/fixture/repos/ParentProject")
            self.assertNotIn("cwd", sessions[parent_id])

            stale = dict(agg)
            stale.pop("cursor_schema", None)
            reprocessed = P.update_file(stale, "cursor", str(store), "Cursor", {})
            self.assertEqual(reprocessed["cursor_schema"], CD.CURSOR_SCHEMA)
            self.assertIsNot(reprocessed, stale)

            workspace_json = workspace / "workspace.json"
            workspace_json.write_text(json.dumps({
                "folder": "file:///fixture/repos/RenamedParentProject"}), encoding="utf-8")
            stat = workspace_json.stat()
            os.utime(workspace_json, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            refreshed = P.update_file(reprocessed, "cursor", str(store), "Cursor", {})
            refreshed_parent = next(row for row in refreshed["sessions"] if row["_sid"] == parent_id)
            self.assertEqual(refreshed_parent["project"], "RenamedParentProject")

    def test_agent_model_join_requires_one_bubble_for_the_exact_request(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "cursor.db"
            connection = _make_store(store)
            first = "55555555-2222-4333-8444-555555555555"
            second = "66666666-2222-4333-8444-555555555555"
            for cid, minute in ((first, 0), (second, 1)):
                _composer(connection, cid, {
                    "composerId": cid, "createdAt": _stamp(minute),
                    "modelConfig": {"modelName": "composer-2.5"},
                })
                _bubble(connection, cid, "reply", {
                    "type": 2, "createdAt": _stamp(minute), "requestId": "request-shared",
                    "tokenCount": {"inputTokens": 7, "outputTokens": 2},
                })
            connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,?)", (
                "agentKv:blob:one", json.dumps({
                    "role": "assistant",
                    "providerOptions": {"cursor": {"requestId": "request-shared", "modelName": "verified-agent-model"}},
                    "content": [],
                })))
            connection.commit()
            connection.close()
            agg = P.update_file(None, "cursor", str(store), "Cursor", {})
            self.assertTrue(all(session["model_status"] == "unavailable" for session in agg["sessions"]))
            self.assertTrue(all("\tUnknown" in key for key in agg["records"]))
            self.assertFalse(any("verified-agent-model" in key for key in agg["records"]))
            self.assertTrue(all(session["current_model"] == P._normalize_cursor_model("composer-2.5")
                                for session in agg["sessions"]))

    def test_tombstones_are_skipped_and_nonnull_corruption_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "cursor.db"
            connection = _make_store(store, headers=True)
            composer_id = "77777777-2222-4333-8444-555555555555"
            connection.execute("INSERT INTO composerHeaders VALUES (?,?,?,?,?,?,?)", (
                composer_id, _stamp(0), _stamp(1), 0, 0, None, None))
            connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,NULL)",
                               ("composerData:" + composer_id,))
            connection.execute("INSERT INTO cursorDiskKV(key,value) VALUES (?,NULL)",
                               (f"bubbleId:{composer_id}:deleted",))
            connection.commit()
            connection.close()
            agg = P.update_file(None, "cursor", str(store), "Cursor", {})
            self.assertEqual(len(agg["sessions"]), 1)
            self.assertEqual(agg["sessions"][0]["usage_status"], "unavailable")

            connection = sqlite3.connect(store)
            connection.execute("UPDATE cursorDiskKV SET value=? WHERE key=?", (
                "{broken-json", "composerData:" + composer_id))
            connection.commit()
            connection.close()
            with self.assertRaises(CD.CursorReadError):
                CD.read_store(store, strict=True)

    def test_timeline_scan_is_bounded_and_reports_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "cursor.db"
            connection = _make_store(store)
            composer_id = "88888888-2222-4333-8444-555555555555"
            _composer(connection, composer_id, {"composerId": composer_id, "createdAt": _stamp(0)})
            for minute in range(3):
                _bubble(connection, composer_id, f"reply-{minute}", {
                    "type": 2, "createdAt": _stamp(minute), "requestId": f"request-{minute}",
                    "tokenCount": {"inputTokens": 1, "outputTokens": 1},
                    "text": "PRIVATE TIMELINE CONTENT",
                })
            connection.commit()
            connection.close()
            with self.assertRaises(CD.CursorReadError):
                CD.read_store(store, max_scan_rows=2, strict=True)
            partial = CD.read_store(store, max_scan_rows=2, strict=False)
            self.assertTrue(partial["storage_scan_truncated"])
            snapshot = CD.read_conversation(
                store, composer_id, max_events=1,
                event_filter={"date_from": "2026-10-25", "date_to": "2026-10-25", "view": "requests"},
            )
            self.assertTrue(snapshot["events_truncated"])
            self.assertEqual(len(snapshot["events"]), 1)
            self.assertEqual(snapshot["events"][0]["kind"], "request")
            self.assertNotIn("text", snapshot["events"][0])
            self.assertNotIn("PRIVATE TIMELINE CONTENT", json.dumps(snapshot))
            self.assertNotIn("compaction", {event["kind"] for event in snapshot["events"]})
            activity = CD.read_conversation(
                store, composer_id, max_events=1,
                event_filter={"date_from": "2026-10-25", "date_to": "2026-10-25", "view": "activity"},
            )
            self.assertEqual(activity["events"][0]["kind"], "model")


if __name__ == "__main__":
    unittest.main()
