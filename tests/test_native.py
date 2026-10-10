"""Native control, install and Windows monitoring regressions; isolated data only."""
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install
import native as N
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
        with patch.object(self.monitor, "_request", side_effect=urllib.error.HTTPError("fixture", 503, "fixture", {}, None)):
            self.monitor.refresh(parse=True)
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
