"""Focused, mock-only tests for the native recent-session projection."""

from datetime import datetime, timedelta, timezone
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

import dashboard as D
import native_activity as NA
import parser as P


UTC = timezone.utc
NOW = datetime(2026, 10, 19, 3, 0, tzinfo=UTC)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def session(source, sid, end, **values):
    row = {"_sid": sid, "source": source, "end": end}
    row.update(values)
    return row


def aggregate(source, path, sessions, **values):
    result = {"source": source, "path": path, "sessions": sessions}
    result.update(values)
    return result


class NativeActivityTests(unittest.TestCase):
    def test_window_boundary_and_local_archived_remote_and_future_exclusion(self):
        boundary = stamp(NOW - timedelta(seconds=900))
        items = [
            ("boundary", aggregate("cursor", "boundary", [session("cursor", "at", boundary)])),
            ("old", aggregate("cursor", "old", [session("cursor", "old", stamp(NOW - timedelta(seconds=900, microseconds=1)))])),
            ("future", aggregate("cursor", "future", [session("cursor", "future", stamp(NOW + timedelta(seconds=1)))])),
            ("missing", aggregate("cursor", "missing", [session("cursor", "missing", None)])),
            ("bad", aggregate("cursor", "bad", [session("cursor", "bad", "not-a-date")])),
            ("remote", aggregate("cursor", "remote", [session("cursor", "remote", stamp(NOW))], _device="f" * 32)),
            ("archived", aggregate("cursor", "archived", [session("cursor", "archived", stamp(NOW))], archived=True)),
            ("child-archived", aggregate("cursor", "child-archived", [session("cursor", "archived-child", stamp(NOW), archived_session=True)])),
            ("not-live", aggregate("cursor", "not-live", [session("cursor", "not-live", stamp(NOW), live_source=False)])),
        ]
        result = NA.project(items, observed_at=NOW)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["sessions"][0]["title"], "Untitled session")
        self.assertEqual(result["sessions"][0]["last_activity_at"], boundary)

    def test_utc_offsets_dst_and_naive_source_conventions(self):
        # Different offsets crossing a DST fall-back remain distinct UTC instants.
        self.assertEqual(NA._as_utc("2026-11-01T01:30:00-04:00"), datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        self.assertEqual(NA._as_utc("2026-11-01T01:30:00-05:00"), datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
        # parser._from_iso treats ordinary naive ISO values as UTC.
        self.assertEqual(NA._as_utc("2026-10-19T02:59:00", source="claude"), datetime(2026, 10, 19, 2, 59, tzinfo=UTC))
        # These source parsers serialize local datetime.fromtimestamp() values.
        local_naive = datetime(2026, 10, 19, 2, 59)
        self.assertEqual(NA._as_utc(local_naive.isoformat(), source="cursor"), local_naive.astimezone(UTC))
        # Crossing UTC midnight is normalized before formatting.
        row = session("cursor", "midnight", "2026-10-19T00:05:00+05:30")
        result = NA.project([("p", aggregate("cursor", "p", [row]))], observed_at="2026-10-18T18:40:00Z")
        self.assertEqual(result["sessions"][0]["last_activity_at"], "2026-10-18T18:35:00Z")

    def test_tokens_use_session_lifetime_records_and_preserve_unknowns(self):
        recorded = {"2026-10-19\tmodel": {"in": 2, "out": 3, "cr": 4, "cc": 5}}
        per_session = session("cursor", "recorded", stamp(NOW), records=recorded)
        direct = session("cursor", "direct", stamp(NOW), **{"in": 2, "out": 3, "cr": 4, "cc": 5})
        multi_a = session("cursor", "multi-a", stamp(NOW))
        multi_b = session("cursor", "multi-b", stamp(NOW))
        invalid = session("cursor", "invalid", stamp(NOW), **{"in": True, "out": 5, "cr": float("nan"), "cc": -1})
        total_records = {"2026-10-19\tmodel": {"in": 100, "out": 200, "cr": 300, "cc": 400}}
        self.assertEqual(NA._session_tokens(per_session, {}, 1), 14)
        self.assertEqual(NA._session_tokens(direct, {}, 1), 14)
        self.assertIsNone(NA._session_tokens(multi_a, {"records": total_records}, 2))
        self.assertIsNone(NA._session_tokens(multi_b, {"records": total_records}, 2))
        self.assertEqual(NA._session_tokens(session("cursor", "single", stamp(NOW)), {"records": total_records}, 1), 1000)
        self.assertIsNone(NA._session_tokens(invalid, {}, 1))
        self.assertIsNone(NA._session_tokens(session("cursor", "zero", stamp(NOW), **{"in": 0, "out": 0, "cr": 0, "cc": 0}), {}, 1))
        self.assertIsNone(NA._session_tokens(session("cursor", "huge", stamp(NOW), **{"in": 10**100, "out": 0, "cr": 0, "cc": 0}), {}, 1))

    def test_titles_are_bounded_and_use_retained_session_names(self):
        titled = session("claude", "named", stamp(NOW), title="A\tclear\nname\x00" + "x" * 120)
        prompt = session("codex", "prompt", stamp(NOW), title="private user prompt")
        copilot_prompt = session("copilot", "copilot", stamp(NOW), title="SDK first prompt")
        claw_prompt = session("openclaw", "claw", stamp(NOW), title="transcript first message")
        items = (
            aggregate("claude", "claude", [titled], _title_rank=2),
            aggregate("codex", "codex", [prompt], _title_rank=1),
            aggregate("copilot", "copilot", [copilot_prompt], _title_rank=2),
            aggregate("openclaw", "claw", [claw_prompt], _title_rank=0),
        )
        rows = [NA.project([(item["path"], item)], observed_at=NOW)["sessions"][0] for item in items]
        self.assertEqual(rows[0]["title"], ("A clear name " + "x" * 120)[:NA.MAX_TITLE_CHARS])
        self.assertEqual(rows[1]["title"], "private user prompt")
        self.assertEqual(rows[2]["title"], "SDK first prompt")
        self.assertEqual(rows[3]["title"], "transcript first message")

    def test_output_allowlist_opaque_keys_subagents_and_session_children(self):
        child = session("cursor", "source-session-id", stamp(NOW), subagent=True,
                        **{"in": 1, "out": 2, "cr": 3, "cc": 4, "project": "private-project"})
        agg = aggregate("cursor", "private/path", [child], session_children=[{"id": "inclusive-child"}],
                        account_metadata={"private": True}, prompt="private prompt")
        result = NA.apply_display_aliases(NA.project([("private/path", agg)], observed_at=NOW), {}, lambda *args: None)
        self.assertEqual(set(result), {"observed_at", "window_seconds", "sessions", "total"})
        row = result["sessions"][0]
        self.assertEqual(set(row), {"key", "source", "tool", "provider", "title", "tokens", "last_activity_at", "subagent"})
        self.assertEqual(len(row["key"]), 64)
        self.assertNotIn("source-session-id", json.dumps(result))
        self.assertNotIn("private-project", json.dumps(result))
        self.assertNotIn("private/path", json.dumps(result))
        self.assertNotIn("inclusive-child", json.dumps(result))
        self.assertTrue(row["subagent"])

    def test_dashboard_deduplicates_and_caps_rows_but_reports_full_total(self):
        now = NOW
        files = {}
        # Copilot identity uses the directory UUID so JSON/JSONL migration copies
        # share one conversation key in the existing deduplicator.
        sid = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        duplicate1 = aggregate("copilot", f"/logs/{sid}.json", [session("copilot", sid, stamp(now - timedelta(minutes=10)), **{"in": 1, "out": 0, "cr": 0, "cc": 0})], records={"d\tm": {"in": 1, "out": 0, "cr": 0, "cc": 0}})
        duplicate2 = aggregate("copilot", f"/logs/{sid}.jsonl", [session("copilot", sid, stamp(now - timedelta(minutes=9)), **{"in": 2, "out": 0, "cr": 0, "cc": 0})], records={"d\tm": {"in": 10, "out": 0, "cr": 0, "cc": 0}})
        archived_copy = aggregate("copilot", f"/logs/archive/{sid}.json", [session("copilot", sid, stamp(now - timedelta(minutes=8)), **{"in": 100, "out": 0, "cr": 0, "cc": 0})], records={"d\tm": {"in": 1000, "out": 0, "cr": 0, "cc": 0}}, archived=True)
        files[duplicate1["path"]] = duplicate1
        files[duplicate2["path"]] = duplicate2
        files[archived_copy["path"]] = archived_copy
        for index in range(4):
            source_id = f"unique-{index}"
            item = aggregate("cursor", f"/logs/{source_id}", [session("cursor", source_id,
                            stamp(now - timedelta(minutes=index)), **{"in": index, "out": 0, "cr": 0, "cc": 0})])
            files[item["path"]] = item
        with patch.dict(D._state, {"files": files}), patch.object(D, "_local_id_readonly", return_value=None):
            result = D.build_native_activity(observed_at=now)
        self.assertEqual(result["total"], 5)
        self.assertEqual(len(result["sessions"]), 3)
        self.assertEqual(result["sessions"][0]["last_activity_at"], stamp(now))
        self.assertEqual(sum(row["source"] == "copilot" for row in result["sessions"]), 0)

    def test_parser_finalized_cursor_opencode_and_copilot_rows_have_stable_namespaced_keys(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cursor_db = root / "cursor.db"
            connection = sqlite3.connect(cursor_db)
            try:
                connection.execute("CREATE TABLE cursorDiskKV (key TEXT, value TEXT)")
                sid = "cursor-identity-123456"
                at_ms = int(NOW.timestamp() * 1000)
                connection.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("composerData:" + sid,
                    json.dumps({"composerId": sid, "createdAt": at_ms, "name": "Cursor title",
                                "modelConfig": {"modelName": "fixture-model"}})))
                connection.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:" + sid + ":reply",
                    json.dumps({"type": 2, "createdAt": stamp(NOW),
                                "tokenCount": {"inputTokens": 12, "outputTokens": 3}})))
                connection.commit()
            finally:
                connection.close()
            cursor_agg = P.update_file(None, "cursor", str(cursor_db), "Cursor", {})
            self.assertEqual(cursor_agg["sessions"][0]["_sid"], sid)

            open_db = root / "opencode.db"
            connection = sqlite3.connect(open_db)
            try:
                connection.execute("CREATE TABLE session_v2 (id TEXT PRIMARY KEY, directory TEXT, title TEXT, agent TEXT, model TEXT, version TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER, fork_session_id TEXT)")
                connection.execute("CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT, type TEXT, seq INTEGER)")
                sid = "open-code-session-long-id"
                at_ms = int(NOW.timestamp() * 1000)
                connection.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (sid, "/fixture/project", "OpenCode title", "build", json.dumps({"id": "model", "providerID": "local-provider"}), "test", None, at_ms - 1000, at_ms, None))
                connection.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                    ("message-1", sid, at_ms, json.dumps({"time": {"created": at_ms}, "tokens": {"input": 8, "output": 2}, "model": {"id": "model", "providerID": "local-provider"}}), "assistant", 1))
                connection.commit()
            finally:
                connection.close()
            opencode_agg = P.update_file(None, "opencode", str(open_db), "OpenCode", {})
            self.assertNotIn("_sid", opencode_agg["sessions"][0])

            copilot_dir = root / "copilot" / "12345678-1234-4234-8234-123456789012"
            copilot_dir.mkdir(parents=True)
            copilot_path = copilot_dir / "events.jsonl"
            events = [
                {"id": "start", "type": "session.start", "timestamp": stamp(NOW - timedelta(minutes=1)),
                 "data": {"sessionId": copilot_dir.name, "selectedModel": "fixture-model"}},
                {"id": "user", "type": "user.message", "timestamp": stamp(NOW), "data": {"content": "Copilot title"}},
            ]
            copilot_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
            with patch.object(P, "_copilot_sdk_hosts", return_value={}):
                copilot_agg = P.update_file(None, "copilot", str(copilot_path), "Copilot SDK", {})

            rows = []
            for path, source_agg in ((cursor_db, cursor_agg), (open_db, opencode_agg),
                                     (copilot_path, copilot_agg)):
                rows.extend(NA.project([(str(path), source_agg)], observed_at=NOW)["sessions"])
            self.assertEqual({row["source"] for row in rows}, {"cursor", "opencode", "copilot"})
            self.assertEqual({row["title"] for row in rows}, {"Cursor title", "OpenCode title", "Copilot title"})
            cursor_row = next(row for row in rows if row["source"] == "cursor")
            self.assertIsNone(cursor_row["tokens"], "Cursor has no recorded cache-read/write fields")
            self.assertEqual(cursor_row["usage_availability"]["input_output"], "recorded")
            self.assertEqual(cursor_row["usage_availability"]["cache_read"], "unavailable")
            self.assertTrue(all(row["tokens"] is not None for row in rows if row["source"] != "cursor"))
            self.assertEqual({row["provider"] for row in rows if row["source"] == "opencode"}, {None})
            self.assertEqual(len({row["key"] for row in rows}), 3)
            # Identical native IDs in different log profiles remain separate.
            repeated = session("cursor", "shared-id", stamp(NOW), start=stamp(NOW - timedelta(minutes=2)))
            first = NA.project([("/profile-one/cursor.db", aggregate("cursor", "/profile-one/cursor.db", [repeated]))], observed_at=NOW)["sessions"][0]["key"]
            second = NA.project([("/profile-two/cursor.db", aggregate("cursor", "/profile-two/cursor.db", [repeated]))], observed_at=NOW)["sessions"][0]["key"]
            self.assertNotEqual(first, second)

    def test_matching_local_device_is_allowed_but_other_devices_are_filtered(self):
        matching = aggregate("cursor", "same-device", [session("cursor", "mine", stamp(NOW))], _device="a" * 32)
        other = aggregate("cursor", "other-device", [session("cursor", "other", stamp(NOW))], _device="b" * 32)
        self.assertTrue(NA.is_local_live_aggregate(matching, "a" * 32))
        self.assertFalse(NA.is_local_live_aggregate(other, "a" * 32))

    def test_builder_reads_no_preferences_or_ledger_and_loads_aliases_outside_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            peers = root / ".peers.json"
            cache = root / ".usage_cache.json"
            parent = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
            source_path = rf"C:\Users\fixture\.claude\projects\project\{parent}\subagents\agent-child.jsonl"
            item = aggregate("claude", source_path, [session("claude", "unused-short-id", stamp(NOW), subagent=True)], subagent=True)
            files = {source_path: item}
            alias_calls = []

            def names():
                self.assertFalse(D._refresh_lock.locked(), "display preferences must be read outside parser lock")
                alias_calls.append("names")
                return {"fixture": "ignored"}

            with patch.dict(D._state, {"files": files}), \
                    patch.dict(D._peer, {"cfg": None}), \
                    patch.object(D, "PEERS_PATH", str(peers)), \
                    patch.object(D, "CACHE_PATH", str(cache)), \
                    patch.object(D, "_local_id_readonly", return_value="c" * 32), \
                    patch.object(D, "_peer_cfg", side_effect=AssertionError("must not create peer preferences")), \
                    patch.object(D.SN, "display_names", side_effect=names), \
                    patch.object(D.SN, "display_name", return_value="My child") as display_name:
                result = D.build_native_activity(observed_at=NOW)
            self.assertEqual(alias_calls, ["names"])
            self.assertEqual(result["sessions"][0]["title"], "My child")
            display_name.assert_called_once()
            self.assertFalse(peers.exists())
            self.assertFalse(cache.exists())
            self.assertEqual(list(root.iterdir()), [])

    def test_http_route_is_origin_guarded_loopback_only_and_outside_health(self):
        payload = {"observed_at": stamp(NOW), "window_seconds": 900, "sessions": [], "total": 0}
        server = D.Server(("127.0.0.1", 0), D.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}), \
                    patch.object(D, "build_native_activity", return_value=payload):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/native-activity")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(json.loads(response.read()), payload)
                connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/native-activity", headers={"Origin": "https://evil.example"})
                response = connection.getresponse()
                self.assertEqual(response.status, 403)
                response.read()
                connection.close()

                with patch.object(D.Handler, "_loopback_client", return_value=False):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("GET", "/api/native-activity")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 403)
                    response.read()
                    connection.close()

                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                connection.request("GET", "/api/health")
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                health = response.read().decode("utf-8")
                self.assertNotIn("sessions", health)
                self.assertNotIn("key", health)
                connection.close()

                with patch.object(D, "client_snapshot", return_value=(b"{}", b'{"tokens": 3}')):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    connection.request("GET", "/api/summary")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    summary = response.read().decode("utf-8")
                    self.assertNotIn("sessions", summary)
                    self.assertNotIn("key", summary)
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
