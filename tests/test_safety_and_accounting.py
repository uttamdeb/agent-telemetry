"""Failure and accounting regressions. All files/HTTP servers are isolated."""
import contextlib
import copy
from datetime import datetime, timezone
import http.client
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


class SafetyAndAccountingTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-safety-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        patches = [patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}),
            patch.dict(D._meta, D._meta.copy()), patch.dict(D._dirty, {"v": False}),
            patch.dict(D._grew, {"v": False, "saved": 0}), patch.dict(D._gen, D._gen.copy()),
            patch.dict(D._cache_error, {"v": None}),
            patch.object(D, "CACHE_PATH", str(self.root / "cache.json")),
            patch.object(P, "_copilot_project_map", return_value={}),
            patch.object(D, "_peer_items", return_value=[]),
            patch.object(D, "_local_id", return_value="fixture-device"),
            patch.object(D, "_devices_meta", return_value=[]),
            patch.object(D, "_context_files", return_value=[]),
            patch.object(D, "_installed", return_value=[]),
            patch.object(D, "_mcp_servers", return_value={})]
        for item in patches:
            item.start(); self.addCleanup(item.stop)

    def codex_event(self, kind, payload):
        return json.dumps({"timestamp": "2026-10-02T10:00:00Z", "type": kind, "payload": payload}) + "\n"

    def codex_log(self):
        path = self.root / "codex.jsonl"
        head = self.codex_event("turn_context", {"model": "gemini-4-argon"})
        usage = self.codex_event("token_usage_record", {"usage": {"input_tokens": 100, "output_tokens": 10}})
        path.write_text(head + usage)
        return path, head, usage

    def test_incremental_failures_rollback_and_repaired_append_counts_once(self):
        path, head, usage = self.codex_log()
        original = P.update_file(None, "codex", str(path), None, {})
        D._state["files"] = {str(path): original}
        before = copy.deepcopy(original)
        path.write_text(head + usage * 2 + self.codex_event("token_usage_record", {"usage": {"input_tokens": "bad"}}))
        with patch.object(P, "discover", return_value=[("codex", str(path), None)]), contextlib.redirect_stderr(io.StringIO()):
            D.refresh(); D.refresh()
            self.assertEqual(original, before)
            path.write_text(head + usage * 2)
            D.refresh(); D.refresh()
        self.assertIs(D._state["files"][str(path)], original)
        self.assertEqual(original["totals"]["in"], 200)

    def test_save_failure_retains_retry_and_previous_disk_snapshot(self):
        path, _, _ = self.codex_log()
        cache = Path(D.CACHE_PATH)
        old = json.dumps({"version": D.CACHE_VERSION, "files": {}})
        cache.write_text(old)
        with patch.object(P, "discover", return_value=[("codex", str(path), None)]):
            with patch.object(D.os, "replace", side_effect=OSError("disk full")), contextlib.redirect_stderr(io.StringIO()):
                D.refresh()
            self.assertTrue(D._dirty["v"])
            self.assertEqual(cache.read_text(), old)
            self.assertIn("save failed", D.build_payload()["cache_error"])
            D.refresh()
        self.assertFalse(D._dirty["v"])
        self.assertEqual(len(json.loads(cache.read_text())["files"]), 1)
        self.assertIsNone(D.build_payload()["cache_error"])

    def test_invalid_ledger_is_preserved_until_explicit_reset(self):
        for raw in ('{"files":', '[1]', '{"files": []}'):
            with self.subTest(raw=raw):
                cache = Path(D.CACHE_PATH); cache.write_text(raw)
                with contextlib.redirect_stderr(io.StringIO()) as err:
                    D.load_cache()
                    self.assertFalse(D.save_cache())
                self.assertEqual(cache.read_text(), raw)
                self.assertIn("preserving", err.getvalue())
                D.cache_action("delete")
                self.assertIsNone(D._cache_error["v"])

    def test_delete_failure_and_restart_failure_keep_history(self):
        D._state["files"] = {"archived": {"archived": True}}
        Path(D.CACHE_PATH).write_text("{}")
        with patch.object(D.os, "remove", side_effect=OSError("denied")):
            with self.assertRaises(ValueError): D.cache_action("delete")
        self.assertIn("archived", D._state["files"])
        with patch.object(D, "save_cache", return_value=False), patch.object(D.time, "sleep"), \
             patch.object(D.os, "execv") as execute, contextlib.redirect_stderr(io.StringIO()):
            D._restart()
        execute.assert_not_called()

    def opencode_db(self):
        db = self.root / "opencode.db"; con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE session (id TEXT, directory TEXT, title TEXT, agent TEXT, model TEXT, version TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER)")
        con.execute("CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER, data TEXT)")
        con.execute("CREATE TABLE part (message_id TEXT, data TEXT)")
        data = {}
        for i, name in enumerate(("Alpha", "Beta"), 1):
            ts = int(datetime(2026, 10, i, 10, tzinfo=timezone.utc).timestamp() * 1000)
            sid, mid = "session-" + name, "msg_" + name
            con.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)", (sid, "/fixture/" + name,
                name, "build", json.dumps({"id": "gemini-4-argon", "providerID": "google"}), "test", None, ts, ts))
            row = {"id": mid, "sessionID": sid, "role": "assistant", "modelID": "gemini-4-argon", "providerID": "google",
                   "time": {"created": ts}, "tokens": {"input": i * 100, "output": i * 10}, "cost": i / 10}
            con.execute("INSERT INTO message VALUES (?,?,?,?)", (mid, sid, ts, json.dumps(row)))
            con.execute("INSERT INTO part VALUES (?,?)", (mid, json.dumps({"type": "tool", "tool": "read"})))
            data[name] = row
        con.commit(); con.close()
        return db, data

    def total_tokens(self, payload):
        return sum(r.get(k, 0) for r in payload["records"] for k in ("in", "out", "cr", "cc"))

    def test_cursor_projects_and_session_days_stay_separate(self):
        db = self.root / "cursor.db"; con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE cursorDiskKV (key TEXT, value TEXT)")
        for i, project in enumerate(("Alpha", "Beta"), 1):
            sid = "cursor-" + project
            ts = int(datetime(2026, 10, i, 10, tzinfo=timezone.utc).timestamp() * 1000)
            con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("composerData:" + sid,
                json.dumps({"composerId": sid, "createdAt": ts, "modelConfig": {"modelName": "gemini-4-argon"}})))
            con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("bubbleId:" + sid + ":reply",
                json.dumps({"type": 2, "createdAt": "2026-10-0%dT10:00:00Z" % i,
                    "tokenCount": {"inputTokens": i * 100, "outputTokens": i * 10},
                    "attachedFolders": ["/home/fixture/projects/" + project]})))
        con.commit(); con.close()
        D._state["files"] = {str(db): P.update_file(None, "cursor", str(db), None, {})}
        payload = D.build_payload()
        self.assertEqual(self.total_tokens(payload), 330)
        self.assertEqual({r["project"] for r in payload["records"]}, {"Alpha", "Beta"})
        for s in payload["sessions"]:
            i = 1 if s["project"] == "Alpha" else 2
            self.assertEqual(set(s["days"]), {"2026-10-0%d" % i})
            self.assertEqual(sum(sum(v[1:5]) for v in s["days"].values()), i * 110)

    def test_hermes_projects_and_mixed_model_cost_reconcile(self):
        db = self.root / "hermes.db"; con = sqlite3.connect(str(db))
        con.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, git_branch TEXT, title TEXT, model TEXT, started_at REAL, ended_at REAL, archived INTEGER, source TEXT)")
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, reasoning_tokens INTEGER, api_call_count INTEGER, first_seen REAL, last_seen REAL)")
        con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL, tool_calls TEXT)")
        for i, project in enumerate(("Alpha", "Beta"), 1):
            ts = datetime(2026, 10, i, 10, tzinfo=timezone.utc).timestamp()
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)", (project, "/fixture/" + project,
                None, project, "gemini-4-argon", ts, ts, 0, "cli"))
            con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                (project, "gemini-4-argon", 100, 10, 20, 0, 0, 1, ts, ts))
            con.execute("INSERT INTO messages VALUES (?,?,?,?)", (project, "assistant", ts, "[]"))
            if i == 1:
                con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (project, "claude-sonnet-4-6", 200, 20, 30, 40, 5, 2, ts, ts))
        con.commit(); con.close()
        D._state["files"] = {str(db): P.update_file(None, "hermes", str(db), None, {})}
        payload = D.build_payload()
        self.assertEqual(self.total_tokens(payload), 550)
        self.assertEqual({r["project"] for r in payload["records"]}, {"Alpha", "Beta"})
        for s in payload["sessions"]:
            rows = [r for r in payload["records"] if r["project"] == s["project"]]
            self.assertAlmostEqual(s["cost"], sum(r["cost"] for r in rows), places=6)
            self.assertEqual(sum(v[10] for v in s["days"].values()), 3 if s["project"] == "Alpha" else 1)
        alpha = next(s for s in payload["sessions"] if s["project"] == "Alpha")
        self.assertEqual(alpha["cc5"], 40)
        self.assertEqual(alpha["nmodels"], 2)

    def test_database_sessions_and_projects_reconcile(self):
        db, _ = self.opencode_db()
        D._state["files"] = {str(db): P.update_file(None, "opencode", str(db), None, {})}
        payload = D.build_payload()
        self.assertEqual(self.total_tokens(payload), 330)
        self.assertEqual({r["project"] for r in payload["records"]}, {"Alpha", "Beta"})
        by_project = {s["project"]: s for s in payload["sessions"]}
        self.assertEqual(sum(sum(v[1:5]) for v in by_project["Alpha"]["days"].values()), 110)
        self.assertEqual(sum(sum(v[1:5]) for v in by_project["Beta"]["days"].values()), 220)
        self.assertEqual(set(by_project["Alpha"]["days"]), {"2026-10-01"})
        self.assertAlmostEqual(sum(r["cost"] for r in payload["records"]), .3)
        self.assertAlmostEqual(sum(s["cost"] for s in payload["sessions"]), .3)

    def test_migration_dedup_preserves_unmigrated_messages_and_devices(self):
        db, data = self.opencode_db()
        legacy = self.root / "storage" / "message" / "session-Alpha"; legacy.mkdir(parents=True)
        (legacy / "msg_Alpha.json").write_text(json.dumps(data["Alpha"]))
        extra = copy.deepcopy(data["Alpha"]); extra["id"] = "msg_extra"; extra["tokens"] = {"input": 300, "output": 30}
        (legacy / "msg_extra.json").write_text(json.dumps(extra))
        database = P.update_file(None, "opencode", str(db), None, {})
        old = P.update_file(None, "opencode", str(legacy), None, {})
        old["archived"] = True
        before = copy.deepcopy(old)
        D._state["files"] = {str(db): database, str(legacy): old}
        self.assertEqual(self.total_tokens(D.build_payload()), 660)
        self.assertEqual(old, before)
        database["_device"] = "another-device"
        self.assertEqual(self.total_tokens(D.build_payload()), 770)
        database.pop("_device")
        old["opencode_messages"] = old["opencode_messages"][:1]
        self.assertEqual(self.total_tokens(D.build_payload()), 330)

    def test_mixed_models_write_tiers_and_projects_in_openclaw(self):
        def event(model, token, write1=0):
            return {"type": "message", "timestamp": "2026-10-02T10:00:00Z", "message": {"role": "assistant",
                "model": model, "usage": {"input": token, "output": 0, "cacheWrite": write1,
                "cacheWrite1h": write1, "cost": {"total": 99}}}}
        events = {"A": [{"type": "session", "cwd": "/fixture/Alpha"}, event("gemini-4-argon", 1_000_000),
                        event("claude-opus-5-5", 1_000_000, 1_000_000)],
                  "B": [{"type": "session", "cwd": "/fixture/Beta"}, event("gemini-4-argon", 1_000_000)]}
        events["B"][1]["timestamp"] = "2026-10-02T10:00:01Z"
        agg = P._blank_agg("openclaw", "fixture-agent")
        with patch.object(P, "_openclaw_sessions", return_value=(events, {}, 0)):
            P.parse_openclaw(agg, "fixture-agent")
        D._state["files"] = {"fixture-agent": agg}
        payload = D.build_payload()
        self.assertEqual({r["project"] for r in payload["records"]}, {"Alpha", "Beta"})
        self.assertAlmostEqual(sum(r["cost"] for r in payload["records"]), 16)
        self.assertAlmostEqual(sum(s["cost"] for s in payload["sessions"]), 16)
        self.assertEqual(sum(v[12] for s in payload["sessions"] for v in s["days"].values()), 1_000_000)

    def test_exact_copilot_component_is_separate_from_estimates(self):
        agg = P._blank_agg("copilot", "fixture")
        exact = {"timestamp": 1790899200000, "modelId": "gemini-4-argon", "message": {"text": "test"},
                 "promptTokens": 100, "completionTokens": 10}
        P._copilot_apply_request(agg, exact)
        estimated = dict(exact); estimated.pop("promptTokens"); estimated.pop("completionTokens")
        P._copilot_apply_request(agg, estimated)
        P._finalize_session(agg, "copilot", "fixture")
        D._state["files"] = {"fixture": agg}
        payload = D.build_payload(); row = payload["records"][0]
        self.assertEqual(row["in"], 101); self.assertEqual(row["exact"]["in"], 100)
        self.assertEqual(row["exact"]["req"], 1)
        self.assertEqual(sum(v[1] for v in payload["sessions"][0]["exact_days"].values()), 100)

    def test_copilot_full_and_incremental_reads_have_same_active_time(self):
        lines = []
        for i in range(8):
            request = {"requestId": "request-%d" % i, "timestamp": 1790899200000 + i * 60000,
                       "modelId": "gemini-4-argon", "promptTokens": 100, "completionTokens": 10,
                       "result": {}, "message": {"text": "fixture"}}
            lines.append(json.dumps({"kind": 2, "k": ["requests"], "v": [request]}))
        full = P._blank_agg("copilot", "fixture")
        incremental = P._blank_agg("copilot", "fixture")
        P.parse_copilot_jsonl(full, lines)
        for line in lines:
            P.parse_copilot_jsonl(incremental, [line])
        self.assertEqual(full["records"], incremental["records"])
        self.assertEqual(sum(r["active"] for r in full["records"].values()), 420)
        self.assertEqual(sum(r["exact"]["active"] for r in full["records"].values()), 420)

    def test_resolved_prices_include_suffixes_and_historical_aliases(self):
        agg = P._blank_agg("claude", "fixture")
        for name in ("Claude Opus 5.5 (US)", "Claude Opus 5.5 (fast)", "gpt-5.6-sol"):
            P._rec(agg, "2026-10-02", name)["in"] = 100
        D._state["files"] = {"fixture": agg}
        p = D.build_payload()
        for name in p["prices"]:
            self.assertEqual(p["pricing"][name], p["prices"][name])
        self.assertTrue(p["pricing_history"]["gpt-5.6-sol"])
        for date, price in p["pricing_history"]["gpt-5.6-sol"]:
            self.assertEqual(tuple(price), P.price_of("GPT-5.6 Sol", date))

    def test_http_guard_covers_reads_and_refresh_requires_guarded_post(self):
        server = D.Server(("127.0.0.1", 0), D.Handler); port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": port}), \
             patch.object(D, "build_payload", return_value={}), patch.object(D, "refresh") as refresh:
            def request(method, path, host, origin=None, ctype="application/json", fetch_site=None, body="{}", extra=None):
                con = http.client.HTTPConnection("127.0.0.1", port)
                headers = {"Host": host, "Content-Type": ctype, **(extra or {})}
                if origin is not None: headers["Origin"] = origin
                if fetch_site is not None: headers["Sec-Fetch-Site"] = fetch_site
                con.request(method, path, body=body if method == "POST" else None, headers=headers)
                response = con.getresponse(); status = response.status; response.read(); con.close(); return status
            host = "localhost:%d" % port; evil = "evil.example:%d" % port
            for path in ("/api/data", "/api/storage", "/api/refresh", "/api/settings", "/"):
                self.assertEqual(request("GET", path, evil, "http://" + evil), 403)
            self.assertEqual(request("GET", "/api/data", host), 200)
            self.assertEqual(request("GET", "/api/refresh", host), 405)
            self.assertEqual(request("POST", "/api/refresh", host, "http://" + host), 200)
            self.assertEqual(request("POST", "/api/refresh", host, ctype="text/plain"), 403)
            self.assertEqual(request("POST", "/api/refresh", host, "http://" + evil), 403)
            self.assertEqual(request("POST", "/api/settings", host, body="[]"), 400)
            self.assertEqual(request("GET", "/api/data", host, fetch_site="cross-site"), 403)
            # A link on another site (the Pro portal, a README) may open the page itself, and only that.
            link = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
            for path in ("/", "/index.html", "/?range=7d"):
                self.assertEqual(request("GET", path, host, fetch_site="cross-site", extra=link), 200, path)
            self.assertEqual(request("GET", "/", evil, fetch_site="cross-site", extra=link), 403, "Host is still checked")
            for path in ("/api/data", "/api/storage", "/static/core.js", "/manifest.json"):
                self.assertEqual(request("GET", path, host, fetch_site="cross-site", extra=link), 403, path)
            for extra in ({"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
                          {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty"},
                          {"Sec-Fetch-Mode": "no-cors", "Sec-Fetch-Dest": "script"}, {}):
                self.assertEqual(request("GET", "/", host, fetch_site="cross-site", extra=extra), 403, extra)
            self.assertEqual(request("POST", "/api/refresh", host, ctype="application/x-www-form-urlencoded",
                                     fetch_site="cross-site", extra=link, body="x=1"), 403, "a cross-site form post")
            self.assertEqual(request("POST", "/api/refresh", host, fetch_site="cross-site", extra=link), 403)
            self.assertEqual(refresh.call_count, 1)
            with patch.dict(D.BIND, {"host": "0.0.0.0"}), patch.object(D, "_lan_addrs", return_value=["192.168.1.2"]):
                self.assertEqual(request("GET", "/api/data", "192.168.1.2:%d" % port), 200)
            with patch.dict(D.BIND, {"host": "::1"}):
                self.assertEqual(request("GET", "/api/data", "[::1]:%d" % port), 200)

    def test_mcp_usage_keeps_source_and_rare_tools_outside_top_tools(self):
        files = {}
        for source in ("claude", "codex"):
            agg = P._blank_agg(source, source)
            agg["tools"] = {"2026-10-02\tpopular-%d" % i: 100 for i in range(65)}
            agg["tools"]["2026-10-02\tmcp__fixture__rare"] = 1
            files[source] = agg
        D._state["files"] = files
        payload = D.build_payload()
        self.assertFalse(any(t["name"].startswith("mcp__") for t in payload["tools"]))
        calls = payload["mcp_inventory"]["calls"]
        self.assertEqual({c["source"] for c in calls}, {"claude", "codex"})
        self.assertTrue(all(c["calls"] == 1 and c["tools"] == ["mcp__fixture__rare"] for c in calls))

    def test_old_archived_entries_and_peer_schema_remain_readable(self):
        legacy = P._blank_agg("opencode", "gone.db")
        P._rec(legacy, "2026-10-01", "Gemini 4 Argon")["in"] = 300
        legacy["archived"] = True
        legacy["sessions"] = [{"id": name, "source": "opencode", "model": "Gemini 4 Argon",
            "project": name, "in": n, "out": 0, "cr": 0, "cc": 0, "asst": 1,
            "start": "2026-10-01T10:00:00Z", "end": "2026-10-02T10:00:00Z"} for name, n in (("A", 100), ("B", 200))]
        Path(D.CACHE_PATH).write_text(json.dumps({"version": 52, "files": {"gone.db": legacy}}))
        with contextlib.redirect_stderr(io.StringIO()): D.load_cache()
        self.assertEqual(D._state["files"]["gone.db"], legacy)
        payload = D.build_payload()
        self.assertTrue(all(s["days"] is None and s["detail_limited"] for s in payload["sessions"]))
        self.assertTrue(D._peer_cache_ok(52)); self.assertTrue(D._peer_cache_ok(53))
        self.assertFalse(D._peer_cache_ok(51)); self.assertFalse(D._peer_cache_ok(True))
        self.assertIn("opencode_ids", D._export_agg({"source": "opencode", "opencode_ids": []}))


if __name__ == "__main__": unittest.main()
