"""History and health integration checks. Uses only temporary synthetic ledgers."""
from contextlib import ExitStack
import copy
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import dashboard as D
import diagnostics as DG
import ledger_backups as LB
import parser as P


class FeatureIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.path = self.root / "fixture.jsonl"
        self.path.write_text(''.join(json.dumps({"type": kind, "timestamp": stamp, "payload": body})+'\n'
            for kind, stamp, body in [
                ("session_meta", "2026-10-12T10:00:00Z", {"id": "full-fixture-id", "cwd": str(self.root / "project")}),
                ("turn_context", "2026-10-12T10:00:01Z", {"model": "fixture-unpriced-model"}),
                ("token_usage_record", "2026-10-12T10:00:02Z", {"usage": {"input_tokens": 100, "output_tokens": 20}})]), encoding="utf-8")
        self.aggregate = P.update_file(None, "codex", str(self.path), None, {})
        self.cache = self.root / "ledger.json"
        patches = [patch.object(D, "CACHE_PATH", str(self.cache)),
            patch.dict(D._state, {"version": D.CACHE_VERSION, "files": {str(self.path): self.aggregate}}, clear=True),
            patch.dict(D._meta, {"files": 1, "building": False}, clear=True),
            patch.dict(D._cache_error, {"v": None}, clear=True), patch.dict(D._backup_day, {"key": None}),
            patch.dict(D._restore_tickets, {}, clear=True), patch.dict(D._gen, {"v": 0}),
            patch.dict(D._dirty, {"v": False}), patch.dict(D._grew, {"v": False, "saved": 0}),
            patch.dict(D._peer, {"export": None}), patch.object(DG, "tracker", DG.DiagnosticsTracker()),
            patch.object(D, "_local_id", return_value="fixture-device"),
            patch.object(P, "_copilot_project_map", return_value={}),
            patch.object(P, "discover", return_value=[("codex", str(self.path), None)])]
        for item in patches:
            self.stack.enter_context(item)
        self.assertTrue(D.save_cache())

    def test_restore_preserves_previous_disk_and_archived_entries(self):
        self.aggregate["archived"] = True
        item = D.backup_action({"action": "create"})["backup"]
        original = copy.deepcopy(D._state["files"])
        D._state["files"] = {}
        D._gen["v"] += 1
        preview = D.backup_action({"action": "preview", "id": item["id"]})
        self.assertEqual(preview["backup"]["sessions"], 1)
        self.assertEqual(preview["current"]["sessions"], 0)
        empty_disk = self.cache.read_bytes()
        self.assertTrue(D.backup_action({"action": "restore", "ticket": preview["ticket"], "confirmed": True})["ok"])
        self.assertEqual(D._state["files"], original)
        safeguards = [b for b in D.backup_status()["backups"] if b["reason"] == "pre-restore"]
        self.assertTrue(any((self.root / LB.BACKUP_DIR_NAME / b["id"]).read_bytes() == empty_disk for b in safeguards))
        with self.assertRaisesRegex(ValueError, "expired|already used"):
            D.backup_action({"action": "restore", "ticket": preview["ticket"], "confirmed": True})

    def test_restore_requires_confirmation_and_refuses_new_usage(self):
        item = D.backup_action({"action": "create"})["backup"]
        preview = D.backup_action({"action": "preview", "id": item["id"]})
        original = self.cache.read_bytes()
        with self.assertRaisesRegex(ValueError, "confirm"):
            D.backup_action({"action": "restore", "ticket": preview["ticket"]})
        D._gen["v"] += 1
        with self.assertRaisesRegex(ValueError, "Usage changed"):
            D.backup_action({"action": "restore", "ticket": preview["ticket"], "confirmed": True})
        self.assertEqual(self.cache.read_bytes(), original)

    def test_restore_recovers_an_unreadable_ledger_without_deleting_original(self):
        item = D.backup_action({"action": "create"})["backup"]
        self.cache.write_bytes(b'{broken ledger')
        D._cache_error["v"] = "unreadable"
        preview = D.backup_action({"action": "preview", "id": item["id"]})
        D.backup_action({"action": "restore", "ticket": preview["ticket"], "confirmed": True})
        self.assertIsNone(D._cache_error["v"])
        self.assertIn(str(self.path), D._state["files"])
        safeguards = [b for b in D.backup_status()["backups"] if b["reason"] == "safeguard"]
        self.assertTrue(safeguards)
        self.assertEqual((self.root / LB.BACKUP_DIR_NAME / safeguards[0]["id"]).read_bytes(), b'{broken ledger')

    def test_failed_backup_blocks_destructive_cache_action(self):
        original = self.cache.read_bytes()
        with patch.object(LB, "create_backup", side_effect=OSError("private path should not escape")):
            with self.assertRaisesRegex(ValueError, "safety backup"):
                D.cache_action("delete")
        self.assertEqual(self.cache.read_bytes(), original)
        self.assertIn(str(self.path), D._state["files"])

    def test_normal_saves_continue_when_scheduled_backup_fails(self):
        D._backup_day["key"] = None
        with patch.object(LB, "create_backup", side_effect=OSError("no space")):
            self.assertTrue(D.save_cache())
        self.assertIn("backup_error", D._meta)
        self.assertEqual(json.loads(self.cache.read_bytes())["files"], D._state["files"])

    def test_diagnostics_surface_parse_failures_and_recover_without_paths(self):
        with patch.object(P, "update_file", side_effect=PermissionError("SECRET C:/private/project token=credential")):
            D.refresh()
        report = D.build_diagnostics()
        self.assertEqual(report["sources"][0]["status"], "error")
        self.assertEqual(report["sources"][0]["errors"], 1)
        self.assertIn("fixture-unpriced-model", report["missing_prices"])
        serialized = json.dumps(report)
        for private in ("SECRET", "private/project", "credential", str(self.root)):
            self.assertNotIn(private, serialized)
        D.refresh()
        self.assertEqual(D.build_diagnostics()["sources"][0]["errors"], 0)

    def test_timeline_resolves_full_native_ids_on_original_device(self):
        data = D.build_session_timeline("fixture-device", "codex", "full-fixture-id")
        self.assertTrue(data["available"])
        self.assertEqual(data["events"][0]["kind"], "request")
        for device, identity in (("remote", "full-fixture-id"), ("fixture-device", "full-fix"), ("fixture-device", "../../secret")):
            with patch.object(D.ST, "timeline") as reader:
                self.assertFalse(D.build_session_timeline(device, "codex", identity)["available"])
                reader.assert_not_called()
        with patch.object(P, "discover", return_value=[]), patch.object(D.ST, "timeline") as reader:
            self.assertFalse(D.build_session_timeline("fixture-device", "codex", "full-fixture-id")["available"])
            reader.assert_not_called()

    def test_private_routes_are_csrf_and_origin_guarded_and_health_stays_minimal(self):
        server = D.Server(("127.0.0.1", 0), D.Handler)
        self.addCleanup(server.server_close)
        self.stack.enter_context(patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), thread.join(timeout=3)))

        def request(method, route, body=None, headers=None):
            client = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                client.request(method, route, body=body, headers=headers or {})
                response = client.getresponse()
                return response.status, json.loads(response.read())
            finally:
                client.close()

        for route in ("/api/backups", "/api/diagnostics", "/api/session-timeline?device=fixture-device&source=codex&id=full-fixture-id"):
            self.assertEqual(request("GET", route)[0], 200)
            self.assertEqual(request("GET", route, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(request("POST", "/api/backups", '{"action":"create"}', {"Content-Type": "text/plain"})[0], 403)
        self.assertEqual(request("POST", "/api/backups", '{"action":"create"}', {"Content-Type": "application/json"})[0], 200)
        health = request("GET", "/api/health")[1]
        self.assertEqual(set(health), {"service", "version", "ready", "building", "pid"})
        self.assertNotIn("account_metadata", D._export_agg(self.aggregate))


if __name__ == "__main__":
    unittest.main()
