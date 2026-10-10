"""Native control, install and Windows monitoring regressions; isolated data only."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import http.client
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
import native as N
import dashboard as D
from windows.monitor import Monitor, import_ledger


class NativeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-native-tests-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        item = patch.object(N, "support_dir", return_value=self.root / "controls")
        item.start()
        self.addCleanup(item.stop)

    def manifest(self):
        entry = self.root / "app"
        entry.touch()
        N._write("native-install.json", {"platform": sys.platform, "entry": str(entry),
                                        "command": [str(entry)]})

    def test_launch_requires_install_and_boolean(self):
        with self.assertRaises(ValueError): N.action(True)
        with self.assertRaises(ValueError): N.action("false")
        self.assertFalse(N.status()["installed"])

    def test_pending_launch_is_not_reported_running_and_cannot_revive_after_cancel(self):
        self.manifest()
        with patch.object(N.subprocess, "Popen") as launch:
            result = N.action(True)
            self.assertTrue(result["requested"])
            self.assertFalse(result["running"])
            generation = launch.call_args.args[0][-1]
        N.action(False)
        self.assertIsNone(N.register(generation))
        self.assertFalse(N.status()["running"])

    def test_manual_open_heartbeat_stop_and_duplicate_launch(self):
        self.manifest()
        generation = N.register()
        self.assertTrue(N.status()["running"])
        with patch.object(N.subprocess, "Popen") as launch:
            N.action(True)
            launch.assert_not_called()
        N.action(False)
        self.assertFalse(N.heartbeat(generation))
        N.unregister(generation)
        self.assertFalse(N.status()["running"])

    def test_expired_heartbeat_and_failed_launch(self):
        self.manifest()
        generation = N.register()
        N._write("native-status.json", {"generation": generation, "time": 0})
        self.assertFalse(N.status()["running"])
        with patch.object(N.subprocess, "Popen", side_effect=OSError("fixture")):
            with self.assertRaises(ValueError): N.action(True)
        self.assertFalse(N.status()["requested"])

    def test_malformed_manifest_and_unresponsive_launch_are_visible(self):
        N._write("native-install.json", {"platform": sys.platform, "command": ["fixture"], "entry": 42})
        self.assertFalse(N.status()["installed"])
        N._write("native-control.json", {"enabled": True, "generation": "fixture", "requested_at": 0})
        self.assertIn("not responded", N.status()["error"])

    def test_http_native_and_owned_shutdown_require_csrf_and_owner_token(self):
        server = D.Server(("127.0.0.1", 0), D.Handler)
        server.stopping = threading.Event()
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}), \
             patch.dict(D.os.environ, {"AGENT_TELEMETRY_CONTROL_TOKEN": "fixture-owner"}):
            def post(path, headers):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("POST", path, '{}', headers)
                response = connection.getresponse()
                result = response.status
                response.read()
                connection.close()
                return result
            self.assertEqual(post("/api/native", {"Content-Type": "text/plain"}), 403)
            self.assertEqual(post("/api/native", {"Content-Type": "application/json", "Origin": "https://example.invalid"}), 403)
            self.assertEqual(post("/api/shutdown", {"Content-Type": "application/json"}), 403)
            self.assertFalse(server.stopping.is_set())
            self.assertEqual(post("/api/shutdown", {"Content-Type": "application/json", "X-AgentTelemetry-Control": "fixture-owner"}), 200)
            self.assertTrue(server.stopping.is_set())

    def test_summary_deduplicates_only_within_device_and_excludes_reasoning(self):
        today = __import__("time").strftime("%Y-%m-%d")
        row = {"in": 750000, "out": 100000, "cr": 200000, "cc": 50000, "cc5": 50000, "reason": 40000}
        aggregate = {"source": "codex", "records": {today + "\tGemini 4 Argon": row}}
        archived = copy.deepcopy(aggregate)
        archived["archived"] = True
        peer = copy.deepcopy(aggregate)
        peer["_device"] = "fixture-peer"
        with patch.dict(D._state, {"files": {"/live/rollout.jsonl": aggregate, "/archive/rollout.jsonl": archived}}), \
             patch.object(D, "_peer_items", return_value=[("/peer/rollout.jsonl", peer)]):
            self.assertEqual(D.build_today_summary(), {"date": today, "tokens": 2200000, "spend": 5.24})

    def test_payload_allowlist_excludes_personal_data(self):
        with tempfile.TemporaryDirectory() as repository:
            root = Path(repository)
            for name in install.BACKEND_FILES:
                (root / name).write_text("fixture")
            (root / "static").mkdir()
            (root / "static" / "app.css").write_text("fixture")
            (root / "windows").mkdir()
            for name in ("tray.py", "monitor.py"):
                (root / "windows" / name).write_text("fixture")
            for name in (".usage_cache.json", ".peers.json", "server.log"):
                (root / name).write_text("personal fixture")
            (root / ".peers").mkdir()
            with patch.object(install, "ROOT", root):
                install.copy_windows_payload(self.root / "payload")
            names = {p.name for p in (self.root / "payload").rglob("*")}
            self.assertFalse(names & {".usage_cache.json", ".peers.json", "server.log", ".peers"})

    def test_import_preserves_archives_source_and_refuses_overwrite_symlink(self):
        source = self.root / "source"
        source.mkdir()
        raw = '{"version":53,"files":{"archived":{"archived":true}}}'
        (source / ".usage_cache.json").write_text(raw)
        destination = self.root / "data"
        import_ledger(source, destination)
        self.assertEqual((source / ".usage_cache.json").read_text(), raw)
        self.assertEqual((destination / ".usage_cache.json").read_text(), raw)
        with self.assertRaises(ValueError): import_ledger(source, destination)
        invalid = self.root / "invalid"
        invalid.mkdir()
        (invalid / ".usage_cache.json").write_text('{"files":[]}')
        with self.assertRaises(ValueError): import_ledger(invalid, self.root / "invalid-out")
        try:
            linked = self.root / "linked"
            linked.symlink_to(source, target_is_directory=True)
        except OSError:
            return  # Windows accounts without symlink rights still test validation/overwrite.
        with self.assertRaises(ValueError): import_ledger(linked, self.root / "linked-out")


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor("/fixture", "/fixture-data", sys.executable)
        self.monitor.requested.set()

    def test_cancelled_probe_does_not_start_or_connect(self):
        def probe():
            self.monitor.requested.clear()
            return {"ready": True, "service": "agent-telemetry"}
        with patch.object(self.monitor, "_probe", side_effect=probe), patch("subprocess.Popen") as child:
            self.monitor.start()
            child.assert_not_called()
        self.monitor.stop()
        self.assertEqual(self.monitor.snapshot()["status"], "Monitoring off")

    def test_first_snapshot_failure_retention_and_recovery(self):
        first = {"date": "2026-10-10", "tokens": 100, "spend": 1.25}
        with patch.object(self.monitor, "_request", return_value=first):
            self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()["tokens"], 100)
        error = urllib.error.HTTPError("fixture", 503, "fixture", {}, None)
        try:
            with patch.object(self.monitor, "_request", side_effect=error):
                self.monitor.refresh(parse=True)
        finally:
            error.close()
        failed = self.monitor.snapshot()
        self.assertEqual(failed["tokens"], 100)
        self.assertTrue(failed["stale"])
        self.assertIn("unavailable", failed["status"])
        with patch.object(self.monitor, "_request", return_value=dict(first, tokens=200)):
            self.monitor.refresh()
        self.assertFalse(self.monitor.snapshot()["stale"])
        self.assertEqual(self.monitor.snapshot()["tokens"], 200)

    def test_detach_does_not_stop_existing_service(self):
        with patch.object(self.monitor, "_request") as request:
            self.assertTrue(self.monitor.stop())
            request.assert_not_called()

    def test_owned_stop_uses_token_and_waits_without_force_kill(self):
        from unittest.mock import Mock
        process = Mock()
        process.poll.return_value = None
        self.monitor.process, self.monitor.token = process, "fixture-own"
        def shutdown(*args, **kwargs):
            process.poll.return_value = 0
            return {"ok": True}
        with patch.object(self.monitor, "_request", side_effect=shutdown) as request:
            self.assertTrue(self.monitor.stop())
            request.assert_called_once_with("/api/shutdown", {}, "fixture-own", timeout=2)
        process.kill.assert_not_called()
        process.terminate.assert_not_called()
        self.assertIsNone(self.monitor.process)

    def test_legacy_summary_does_not_add_reasoning_and_filters_date(self):
        self.monitor.legacy = True
        today = __import__("datetime").date.today().isoformat()
        data = {"records": [{"date": today, "in": 100, "out": 10, "cr": 20,
                             "cc": 30, "reason": 5, "cost": 1.25},
                            {"date": "2000-01-01", "in": 10000}]}
        with patch.object(self.monitor, "_request", return_value=data):
            self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()["tokens"], 160)


if __name__ == "__main__":
    unittest.main()
