"""Source renames require a reviewed warning and preserve billed/event history."""
import contextlib
from contextlib import closing
import copy
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
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import native as N
import parser as P
import session_names as SN


class SessionNameTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-names-")
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        self.sid = str(uuid.uuid4()); self.path = self.root / (self.sid + ".jsonl")
        lines = [{"type": "custom-title", "sessionId": self.sid, "customTitle": "Original fixture"},
                 {"type": "user", "uuid": "fixture-user", "timestamp": "2026-10-12T12:00:00Z", "message": {"content": "Fixture request"}},
                 {"type": "assistant", "uuid": "fixture-assistant", "requestId": "fixture-request",
                  "timestamp": "2026-10-12T12:00:01Z", "message": {"id": "fixture-message", "model": "claude-sonnet-5-5",
                  "content": [{"type": "tool_use", "name": "Bash", "id": "fixture-tool", "input": {"command": "echo fixture"}}],
                  "usage": {"input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 20, "cache_read_input_tokens": 50}}},
                 {"type": "system", "subtype": "compact_boundary", "uuid": "fixture-compact", "timestamp": "2026-10-12T12:00:02Z",
                  "compactMetadata": {"trigger": "manual", "preTokens": 200, "postTokens": 100}}]
        self.path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        self.agg = P.update_file(None, "claude", str(self.path), None, {})
        for item in [patch.object(N, "support_dir", return_value=self.root / "preferences"),
                     patch.dict(SN._plans, {}, clear=True),
                     patch.dict(D._state, {"files": {str(self.path): self.agg}, "version": D.CACHE_VERSION}),
                     patch.dict(D._dirty, {"v": False}), patch.dict(D._gen, D._gen.copy()),
                     patch.object(D, "CACHE_PATH", str(self.root / "ledger.json")),
                     patch.object(D, "_sync_manual", 0),
                     patch.object(P, "discover", return_value=[("claude", str(self.path), None)]),
                     patch.object(D, "_peer_items", return_value=[]),
                     patch.object(D, "_local_id", return_value="fixture-device"),
                     patch.object(D, "_devices_meta", return_value=[]),
                     patch.object(D, "_context_files", return_value=[]),
                     patch.object(D, "_installed", return_value=[]),
                     patch.object(D, "_mcp_servers", return_value={})]:
            item.start(); self.addCleanup(item.stop)
        self.body = {"device": "fixture-device", "source": "claude", "id": self.sid}

    def preview(self, name="New fixture"):
        return D.set_session_name({**self.body, "action": "preview", "name": name})

    def commit(self, preview, **extra):
        return D.set_session_name({**self.body, "action": "rename", "ticket": preview["ticket"], "confirmed": True, **extra})

    def child(self, agent="child-fixture", workflow=None, parent=None, root=None):
        folder = (root or self.root) / (parent or self.sid) / "subagents"
        if workflow: folder = folder / "workflows" / workflow
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / ("agent-" + agent + ".jsonl")
        lines = [{"type": "user", "sessionId": parent or self.sid, "isSidechain": True,
                  "timestamp": "2026-10-12T12:00:00Z", "message": {"content": "Child fixture task"}},
                 {"type": "assistant", "sessionId": parent or self.sid, "isSidechain": True,
                  "timestamp": "2026-10-12T12:00:01Z", "message": {"id": "child-message-" + agent,
                  "model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "Fixture reply"}],
                  "usage": {"input_tokens": 25, "output_tokens": 5}}}]
        path.write_text("".join(json.dumps(line) + "\n" for line in lines))
        aggregate = P.update_file(None, "claude", str(path), None, {})
        D._state["files"][str(path)] = aggregate
        P.discover.return_value.append(("claude", str(path), None))
        identity = SN.identity(aggregate, aggregate["sessions"][0])
        return path, aggregate, {**self.body, "id": identity}

    def child_preview(self, body, name="Private display fixture"):
        return D.set_session_name({**body, "action": "preview", "name": name})

    def child_commit(self, body, preview, **extra):
        return D.set_session_name({**body, "action": "rename", "ticket": preview["ticket"], "confirmed": True, **extra})

    def test_child_identity_includes_parent_workflow_and_copied_windows_separators(self):
        for workflow in (None, "wf_fixture-1"):
            path, aggregate, body = self.child(workflow=workflow)
            tail = "subagents/" + ("workflows/" + workflow + "/" if workflow else "") + "agent-child-fixture"
            self.assertEqual(body["id"], self.sid + "/" + tail)
            for prefix in ("C:\\Users\\fixture\\projects\\", "\\\\host\\share\\"):
                copied = prefix + (self.sid + "/" + tail + ".jsonl").replace("/", "\\")
                self.assertEqual(SN.identity({"source": "claude", "subagent": True, "path": copied}, {}), body["id"])
                self.assertEqual(SN.identity({"source": "claude", "path": prefix + self.sid + ".jsonl"}, {}), self.sid)
                self.assertEqual(SN.identity({"source": "claude", "path": prefix + self.sid.upper() + ".jsonl"}, {}), self.sid)
            self.assertTrue(SN.display_availability("claude", str(path), True)[0])
            with self.assertRaises(ValueError):
                SN.prepare("fixture-device", "claude", str(path), body["id"], "Native edit refused", "Fixture")
        for invalid in ("subagents/agent-id.jsonl", self.sid + "/subagents/unknown/agent-id.jsonl",
                        self.sid + "/subagents/workflows/unknown/agent-id.jsonl", self.sid + "/subagents/agent-id.json"):
            self.assertEqual(SN.claude_child_identity(invalid), "")
        relative = self.sid.upper() + "/subagents/workflows/wf_MiXeD/agent-MiXeD.jsonl"
        self.assertEqual(SN.claude_child_identity("/fixture/" + relative),
                         self.sid + "/subagents/workflows/wf_MiXeD/agent-MiXeD")
        self.assertEqual(SN.claude_child_identity("C:/fixture/" + relative),
                         self.sid + "/subagents/workflows/wf_mixed/agent-mixed")

    def test_full_child_identity_resolves_numeric_timeline_without_changing_parent(self):
        for workflow in (None, "wf_timeline"):
            path, aggregate, body = self.child(workflow=workflow)
            result = D.build_session_timeline("fixture-device", "claude", body["id"])
            self.assertTrue(result["available"])
            self.assertEqual([event["kind"] for event in result["events"]], ["user_turn", "request"])
            self.assertEqual(result["events"][-1]["input"], 25)
            self.assertNotIn("Child fixture task", json.dumps(result))
        with patch.object(P, "discover", return_value=[]):
            self.assertFalse(D.build_session_timeline("fixture-device", "claude", body["id"])["available"])

    def test_child_timeline_refuses_ambiguous_discovered_copies_but_keeps_unique_source(self):
        for workflow in (None, "wf_duplicate_timeline"):
            with self.subTest(workflow=workflow):
                path, aggregate, body = self.child(workflow=workflow)
                copied, _, copied_body = self.child(workflow=workflow, root=self.root / "different-profile")
                self.assertEqual(body["id"], copied_body["id"])
                before = copy.deepcopy(D._state["files"])
                with patch.object(D.ST, "timeline") as timeline:
                    result = D.build_session_timeline("fixture-device", "claude", body["id"])
                    self.assertFalse(result["available"])
                    self.assertEqual(result["events"], [])
                    self.assertIn("More than one", result["detail"])
                    timeline.assert_not_called()
                self.assertEqual(D._state["files"], before)
                # An undiscovered retained duplicate must not hide the one
                # verified source. Duplicate ledger keys for that same path
                # must not make the file ambiguous either.
                D._state["files"]["duplicate-ledger-key"] = copy.deepcopy(aggregate)
                with patch.object(P, "discover", return_value=[("claude", str(path), None)]):
                    result = D.build_session_timeline("fixture-device", "claude", body["id"])
                    self.assertTrue(result["available"])
                    self.assertEqual([event["kind"] for event in result["events"]], ["user_turn", "request"])
                D._state["files"].pop("duplicate-ledger-key")

    def test_child_display_name_never_writes_transcripts_ledger_summary_or_peer_exports(self):
        path, aggregate, body = self.child(workflow="wf_fixture")
        before = copy.deepcopy(D._state["files"])
        raw_parent, raw_child = self.path.read_bytes(), path.read_bytes()
        payload_before, summary = D.build_payload(), D.build_today_summary()
        gen, dirty = D._gen["v"], D._dirty["v"]
        preview = self.child_preview(body, "Private <alias> & fixture")
        self.assertEqual(preview["scope"], "display")
        self.assertFalse((N.support_dir() / SN._DISPLAY_FILE).exists())
        with patch.object(SN, "commit") as native_commit, patch.object(P, "update_file") as parse, patch.object(D, "save_cache") as save:
            result = self.child_commit(body, preview)
            native_commit.assert_not_called(); parse.assert_not_called(); save.assert_not_called()
        self.assertEqual(self.path.read_bytes(), raw_parent)
        self.assertEqual(path.read_bytes(), raw_child)
        self.assertEqual(D._state["files"], before)
        self.assertEqual((D._gen["v"], D._dirty["v"]), (gen, dirty))
        self.assertFalse(Path(D.CACHE_PATH).exists())
        self.assertEqual(D.build_today_summary(), summary)
        after = D.build_payload()
        self.assertEqual(after["records"], payload_before["records"])
        child = next(s for s in after["sessions"] if s["rename_id"] == body["id"])
        self.assertEqual(child["title"], result["title"])
        self.assertEqual(child["original_title"], aggregate["sessions"][0]["title"])
        self.assertEqual(child["rename_scope"], "display")
        self.assertTrue(child["rename_available"])
        self.assertTrue(child["display_alias"])
        for item in D._state["files"].values():
            self.assertNotIn(result["title"], json.dumps(D._export_agg(item)))
        file = N.support_dir() / SN._DISPLAY_FILE
        contents = json.loads(file.read_text())
        self.assertNotIn(str(path), file.read_text())
        self.assertEqual(list(contents["names"].values()), [result["title"]])
        if sys.platform != "win32": self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        original = aggregate["sessions"][0]["title"] or aggregate["sessions"][0]["project"] or body["id"]
        self.child_commit(body, self.child_preview(body, original))
        self.assertEqual(SN.display_names(), {})
        self.assertEqual(path.read_bytes(), raw_child)

    def test_child_previews_are_one_use_confirmed_and_detect_stale_aliases(self):
        path, aggregate, body = self.child()
        preview = self.child_preview(body)
        with self.assertRaises(ValueError): self.child_commit(body, preview, confirmed=False)
        self.child_commit(body, preview)
        with self.assertRaises(ValueError): self.child_commit(body, preview)
        first, second = self.child_preview(body, "First fixture"), self.child_preview(body, "Second fixture")
        self.child_commit(body, first)
        with self.assertRaisesRegex(ValueError, "changed"): self.child_commit(body, second)
        expired = self.child_preview(body)
        SN._plans[expired["ticket"]]["at"] -= 301
        with self.assertRaisesRegex(ValueError, "expired"): self.child_commit(body, expired)
        native_preview = self.preview()
        with self.assertRaises(ValueError): self.child_commit(body, native_preview)
        alias_preview = self.child_preview(body)
        with self.assertRaises(ValueError): self.commit(alias_preview)

    def test_child_aliases_refuse_corrupt_store_and_preserve_atomic_write_failure(self):
        path, aggregate, body = self.child()
        self.child_commit(body, self.child_preview(body))
        file = N.support_dir() / SN._DISPLAY_FILE
        original = file.read_bytes()
        with patch("builtins.open", side_effect=PermissionError("fixture unavailable")):
            with self.assertRaisesRegex(ValueError, "could not be read"): self.child_preview(body)
        self.assertEqual(file.read_bytes(), original)
        pending = self.child_preview(body, "Replacement fixture")
        with patch.object(SN.os, "replace", side_effect=OSError("fixture full disk")):
            with self.assertRaises(OSError): self.child_commit(body, pending)
        self.assertEqual(file.read_bytes(), original)
        self.assertFalse(list(file.parent.glob("*.tmp")))
        for value in (b"unreadable fixture", b'{"version":2,"names":{}}', b'{"version":true,"names":{}}',
                      b'{"version":1,"names":{},"unknown":"preserve this"}',
                      b'{"version":1,"names":{"wrong-key":"name"}}'):
            file.write_bytes(value)
            with self.assertRaisesRegex(ValueError, "invalid"): self.child_preview(body)
            self.assertEqual(file.read_bytes(), value)
            child = next(s for s in D.build_payload()["sessions"] if s["rename_id"] == body["id"])
            self.assertFalse(child["rename_available"])
            self.assertEqual(child["title"], aggregate["sessions"][0]["title"])

    def test_child_names_are_scoped_and_unknown_remote_missing_or_ambiguous_sources_are_refused(self):
        path, aggregate, body = self.child(agent="same-child")
        other, other_aggregate, other_body = self.child(agent="same-child", parent=str(uuid.uuid4()))
        self.assertNotEqual(body["id"], other_body["id"])
        first, second = self.child_preview(body, "First child alias"), self.child_preview(other_body, "Second child alias")
        self.child_commit(body, first); self.child_commit(other_body, second)
        self.assertEqual(set(SN.display_names().values()), {"First child alias", "Second child alias"})
        remote = copy.deepcopy(aggregate); remote["_device"] = "peer-fixture"
        with patch.object(D, "_peer_items", return_value=[("peer:fixture:child", remote)]):
            child = next(s for s in D.build_payload()["sessions"] if s["device"] == "peer-fixture")
            self.assertEqual(child["title"], aggregate["sessions"][0]["title"])
            self.assertNotIn("display_alias", child)
            self.assertNotIn("original_title", child)
            self.assertFalse(child["rename_available"])
        with self.assertRaises(ValueError): self.child_preview({**body, "device": "peer-fixture"})
        with self.assertRaises(ValueError): self.child_preview({**body, "id": "agent-sa"})
        with patch.object(P, "discover", return_value=[]):
            with self.assertRaises(ValueError): self.child_preview(body)
        pending = self.child_preview(body)
        path.unlink()
        with self.assertRaises(ValueError): self.child_commit(body, pending)
        duplicate, _, duplicate_body = self.child(agent="same-child", parent=other_body["id"].split("/")[0], root=self.root / "different-profile")
        self.assertEqual(duplicate_body["id"], other_body["id"])
        with self.assertRaisesRegex(ValueError, "More than one"): self.child_preview(other_body)

    def test_child_http_display_name_uses_same_origin_json_and_confirmation_gates(self):
        path, aggregate, child = self.child(workflow="wf_http-fixture")
        source_bytes = path.read_bytes()
        server = D.Server(("127.0.0.1", 0), D.Handler); self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:" + str(server.server_port)
        def post(body, headers):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("POST", "/api/session-name", json.dumps(body), headers)
            response = connection.getresponse(); status = response.status; data = json.loads(response.read()); connection.close()
            return status, data
        body = {**child, "action": "preview", "name": "HTTP private alias"}
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}):
            self.assertEqual(post(body, {"Content-Type": "text/plain"})[0], 403)
            self.assertEqual(post(body, {"Content-Type": "application/json", "Origin": "https://example.invalid"})[0], 403)
            self.assertFalse((N.support_dir() / SN._DISPLAY_FILE).exists())
            headers = {"Content-Type": "application/json", "Origin": origin}
            status, preview = post(body, headers)
            self.assertEqual(status, 200); self.assertEqual(preview["scope"], "display")
            commit = {**child, "action": "rename", "ticket": preview["ticket"]}
            self.assertEqual(post(commit, headers)[0], 400)
            self.assertFalse((N.support_dir() / SN._DISPLAY_FILE).exists())
            self.assertEqual(post({**commit, "confirmed": True}, headers)[0], 200)
            self.assertEqual(post({**commit, "confirmed": True}, headers)[0], 400)
        self.assertEqual(path.read_bytes(), source_bytes)

    def test_preview_cancel_and_missing_confirmation_do_not_write_source_or_ledger(self):
        before, raw = copy.deepcopy(self.agg), self.path.read_bytes()
        preview = self.preview()
        self.assertEqual(preview["current_name"], "Original fixture")
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.agg, before)
        with self.assertRaises(ValueError): self.commit(preview, confirmed=False)
        with self.assertRaises(ValueError): self.commit({"ticket": "invented"})
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse((N.support_dir() / "session-title-backups").exists())

    def test_claude_appends_native_title_only_preserving_all_usage_activity_and_compactions(self):
        before, raw = copy.deepcopy(self.agg), self.path.read_bytes()
        result = self.commit(self.preview("My <fixture> session"))
        self.assertEqual(self.path.read_bytes()[:len(raw)], raw)
        line = json.loads(self.path.read_bytes()[len(raw):])
        self.assertEqual(line, {"type": "custom-title", "customTitle": "My <fixture> session", "sessionId": self.sid})
        updated = D._state["files"][str(self.path)]
        for key in ("totals", "records", "hourly", "tools", "skills", "compactions", "ctx"):
            self.assertEqual(updated[key], before[key], key)
        self.assertEqual(P.activity_of(updated), P.activity_of(before))
        self.assertEqual(updated["sessions"][0]["title"], result["name"])
        backup = Path(result["backup"])
        self.assertEqual(json.loads(backup.read_text())["snapshot"]["title"], "Original fixture")
        if sys.platform != "win32": self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError): self.commit({"ticket": next(iter(SN._plans), "consumed")})

    def test_changed_or_incomplete_transcript_and_backup_failure_refuse_writes(self):
        preview = self.preview()
        with self.path.open("a") as file: file.write(json.dumps({"type": "custom-title", "customTitle": "Changed elsewhere", "sessionId": self.sid}) + "\n")
        current = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "changed"): self.commit(preview)
        self.assertEqual(self.path.read_bytes(), current)
        with self.path.open("a") as file: file.write('{"unfinished":')
        with self.assertRaisesRegex(ValueError, "still being written"): self.preview()
        self.path.write_bytes(current)
        preview = self.preview()
        with patch.object(SN, "_backup", side_effect=OSError("fixture disk full")):
            with self.assertRaises(OSError): self.commit(preview)
        self.assertEqual(self.path.read_bytes(), current)

    def test_unknown_remote_imported_and_invalid_names_cannot_edit_a_source(self):
        raw = self.path.read_bytes()
        for name in ("", "  ", "a\nb", "x" * 91, "\ud800", True, 42, []):
            with self.subTest(name=repr(name)), self.assertRaises(ValueError): self.preview(name)
        for identity in ({"id": "unknown"}, {"device": "peer"}, {"source": "unknown"}):
            with self.assertRaises(ValueError): D.set_session_name({**self.body, **identity, "action": "preview", "name": "Wrong"})
        with patch.object(P, "discover", return_value=[]):
            with self.assertRaises(ValueError): self.preview()
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse(SN.availability("claude", str(self.path), remote=True)[0])
        self.assertFalse(SN.availability("cursor", str(self.path))[0])

    def test_partial_append_preserves_existing_and_concurrently_appended_records(self):
        raw = self.path.read_bytes()
        original_write = SN.os.write
        appended = b'{"type":"user","uuid":"later-user","timestamp":"2026-10-12T12:00:03Z","message":{"content":"Later fixture request"}}\n'
        for concurrent in (False, True):
            with self.subTest(concurrent=concurrent):
                self.path.write_bytes(raw)
                preview = self.preview()
                first = [True]
                def short_write(fd, data):
                    if not first[0]: return original_write(fd, data)
                    first[0] = False
                    count = original_write(fd, data[:12])
                    if concurrent:
                        with self.path.open("ab") as file: file.write(appended)
                    return count
                with patch.object(SN.os, "write", side_effect=short_write):
                    with self.assertRaisesRegex(OSError, "incomplete"): self.commit(preview)
                after = self.path.read_bytes()
                self.assertEqual(after[:len(raw)], raw)
                if concurrent:
                    self.assertTrue(after.endswith(appended))
                    updated = P.update_file(None, "claude", str(self.path), None, {})
                    self.assertEqual(updated["totals"]["user"], 2)
                else: self.assertFalse(after[len(raw):].strip())

    def test_visible_id_collision_cannot_rename_the_wrong_conversation(self):
        second_id = self.sid[:8] + str(uuid.uuid4())[8:]
        second_path = self.root / (second_id + ".jsonl")
        second_path.write_text(self.path.read_text().replace(self.sid, second_id))
        second = P.update_file(None, "claude", str(second_path), None, {})
        self.assertEqual(second["sessions"][0]["id"], self.agg["sessions"][0]["id"])
        D._state["files"][str(second_path)] = second
        with patch.object(P, "discover", return_value=[("claude", str(self.path), None), ("claude", str(second_path), None)]):
            with self.assertRaises(ValueError):
                D.set_session_name({**self.body, "id": self.sid[:8], "action": "preview", "name": "Wrong"})
            body = {**self.body, "id": second_id}
            preview = D.set_session_name({**body, "action": "preview", "name": "Second only"})
            D.set_session_name({**body, "action": "rename", "ticket": preview["ticket"], "confirmed": True})
        self.assertEqual(self.agg["sessions"][0]["title"], "Original fixture")
        self.assertEqual(D._state["files"][str(second_path)]["sessions"][0]["title"], "Second only")

    def database(self):
        path = self.root / "opencode.db"
        with closing(sqlite3.connect(path)) as c, c:
            for table in ("session", "session_v2"):
                c.execute("CREATE TABLE " + table + " (id TEXT PRIMARY KEY,title TEXT,tokens INTEGER,time_updated INTEGER)")
                c.execute("INSERT INTO " + table + " VALUES (?,?,123,456)", (self.sid, "Original database title"))
            c.execute("CREATE TABLE message (id TEXT PRIMARY KEY,data TEXT)")
            c.execute("INSERT INTO message VALUES ('fixture','immutable usage')")
        return path

    def test_opencode_transaction_changes_only_titles_in_both_schemas(self):
        path = self.database()
        preview = SN.prepare("fixture-device", "opencode", str(path), self.sid, "Renamed", "Original database title")
        SN.commit(preview["ticket"], "fixture-device", "opencode", str(path), self.sid, True)
        with closing(sqlite3.connect(path)) as c, c:
            for table in ("session", "session_v2"):
                self.assertEqual(c.execute("SELECT title,tokens,time_updated FROM " + table).fetchone(), ("Renamed", 123, 456))
            self.assertEqual(c.execute("SELECT * FROM message").fetchall(), [("fixture", "immutable usage")])
        preview = SN.prepare("fixture-device", "opencode", str(path), self.sid, "Again", "Renamed")
        with closing(sqlite3.connect(path)) as c, c: c.execute("UPDATE session_v2 SET title='Someone else'")
        with self.assertRaisesRegex(ValueError, "changed"):
            SN.commit(preview["ticket"], "fixture-device", "opencode", str(path), self.sid, True)
        with closing(sqlite3.connect(path)) as c, c:
            self.assertEqual(c.execute("SELECT title FROM session").fetchone()[0], "Renamed")
            c.execute("CREATE TRIGGER unexpected AFTER UPDATE ON session BEGIN DELETE FROM message; END")
        with self.assertRaisesRegex(ValueError, "triggers"):
            SN.prepare("fixture-device", "opencode", str(path), self.sid, "Wrong", "Renamed")

    def test_codex_uses_only_read_and_name_api_without_starting_a_thread_or_turn(self):
        path = self.root / "codex-profile" / "sessions" / "codex.jsonl"
        path.parent.mkdir(parents=True); path.write_text('original rollout\n')
        calls = []
        def rpc(method, params, **kwargs):
            calls.append((method, params))
            self.assertEqual(kwargs["home"], str(path.parent.parent.resolve()))
            return {"thread": {"name": "Original Codex"}} if method == "thread/read" else {}
        with patch.object(SN.shutil, "which", return_value="fixture-codex"), patch.object(SN, "_codex_rpc", side_effect=rpc):
            preview = SN.prepare("fixture-device", "codex", str(path), self.sid, "New Codex", "Original Codex")
            SN.commit(preview["ticket"], "fixture-device", "codex", str(path), self.sid, True)
        self.assertEqual([m for m, _ in calls], ["thread/read", "thread/read", "thread/name/set"])
        self.assertTrue(all(not p.get("includeTurns") for m, p in calls if m == "thread/read"))
        self.assertEqual(path.read_text(), 'original rollout\n')

    @unittest.skipUnless(SN.shutil.which("codex"), "Optional native Codex CLI is not installed")
    def test_actual_codex_api_isolated_profile_updates_native_index_without_rewriting_rollout(self):
        home = self.root / "isolated-codex"
        folder = home / "sessions" / "2026" / "10" / "12"; folder.mkdir(parents=True)
        sid = str(uuid.uuid4()); stamp = "2026-10-12T12:00:00Z"
        path = folder / ("rollout-2026-10-12T12-00-00-" + sid + ".jsonl")
        path.write_text(json.dumps({"timestamp": stamp, "type": "session_meta", "payload": {
            "id": sid, "timestamp": stamp, "cwd": str(self.root), "originator": "codex_cli_rs",
            "cli_version": "0.151.0", "source": "cli", "model_provider": "openai"}}) + "\n")
        original = path.read_bytes()
        wrong_home = self.root / "wrong-profile"
        with patch.dict(SN.os.environ, {"CODEX_HOME": str(wrong_home)}):
            preview = SN.prepare("fixture-device", "codex", str(path), sid, "Native fixture rename", "Fixture")
            SN.commit(preview["ticket"], "fixture-device", "codex", str(path), sid, True)
            actual = SN._codex_rpc("thread/read", {"threadId": sid, "includeTurns": False}, home=str(home))
        self.assertEqual(actual["thread"]["name"], "Native fixture rename")
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(wrong_home.exists())
        record = json.loads((home / "session_index.jsonl").read_text().splitlines()[-1])
        self.assertEqual((record["id"], record["thread_name"]), (sid, "Native fixture rename"))

    def test_http_warning_gate_requires_json_same_origin_and_one_use_ticket(self):
        server = D.Server(("127.0.0.1", 0), D.Handler); self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start(); self.addCleanup(server.shutdown)
        origin = "http://127.0.0.1:" + str(server.server_port)
        def post(body, headers):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            connection.request("POST", "/api/session-name", json.dumps(body), headers)
            response = connection.getresponse(); status = response.status; data = json.loads(response.read()); connection.close()
            return status, data
        body = {**self.body, "action": "preview", "name": "HTTP title"}; raw = self.path.read_bytes()
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}):
            self.assertEqual(post(body, {"Content-Type": "text/plain"})[0], 403)
            self.assertEqual(post(body, {"Content-Type": "application/json", "Origin": "https://example.invalid"})[0], 403)
            headers = {"Content-Type": "application/json", "Origin": origin}
            status, preview = post(body, headers); self.assertEqual(status, 200)
            commit = {**self.body, "action": "rename", "ticket": preview["ticket"]}
            self.assertEqual(post(commit, headers)[0], 400)
            self.assertEqual(self.path.read_bytes(), raw)
            self.assertEqual(post({**commit, "confirmed": True}, headers)[0], 200)
            self.assertEqual(post({**commit, "confirmed": True}, headers)[0], 400)


if __name__ == "__main__": unittest.main()
