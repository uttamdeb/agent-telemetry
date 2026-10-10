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
from windows.monitor import Monitor, import_ledger, login_enabled, toggle_login


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

    def test_closing_heartbeat_keeps_launch_blocked_until_saving_finishes(self):
        self.manifest()
        generation = N.register()
        N.action(False)
        with patch.object(N.time, "time", return_value=100):
            self.assertFalse(N.heartbeat(generation, closing=True))
        with patch.object(N.time, "time", return_value=110):
            state = N.status()
            self.assertTrue(state["running"])
            self.assertFalse(state["requested"])
            with self.assertRaisesRegex(ValueError, "closing"):
                N.action(True)
        N.unregister(generation)
        self.assertFalse(N.status()["running"])

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

    def test_shared_clock_settings_validation_manual_and_csrf(self):
        server = D.Server(("127.0.0.1", 0), D.Handler)
        binding = patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port})
        binding.start()
        self.addCleanup(binding.stop)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        def request(method, body=None, content="application/json", origin=None):
            connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
            headers = {"Content-Type": content}
            if origin: headers["Origin"] = origin
            connection.request(method, "/api/sync", json.dumps(body) if body is not None else None, headers)
            response = connection.getresponse()
            result = response.status, json.loads(response.read())
            connection.close()
            return result
        self.assertEqual(request("GET")[1]["seconds"], 15)
        self.assertEqual(request("POST", {"seconds": 60}, "text/plain")[0], 403)
        self.assertEqual(request("POST", {"seconds": 60}, origin="https://example.invalid")[0], 403)
        for seconds in (True, -1, 1, "60", 15000, None):
            self.assertEqual(request("POST", {"seconds": seconds})[0], 400)
        self.assertEqual(request("POST", {"seconds": 60})[1]["seconds"], 60)
        with patch.object(D.time, "time", return_value=61):
            first = request("GET")[1]
        with patch.object(D.time, "time", return_value=119):
            self.assertEqual(request("GET")[1], first)
        with patch.object(D.time, "time", return_value=120):
            self.assertNotEqual(request("GET")[1]["revision"], first["revision"])
        request("POST", {"seconds": 0})
        with patch.object(D.time, "time", return_value=120):
            manual = request("GET")[1]
        with patch.object(D.time, "time", return_value=10000):
            self.assertEqual(request("GET")[1], manual)
            D.notify_refresh()
            self.assertNotEqual(request("GET")[1]["revision"], manual["revision"])
        self.assertEqual(set(manual), {"seconds", "revision"})

    def test_browser_and_native_serve_one_snapshot_even_if_parsing_changes(self):
        today = __import__("time").strftime("%Y-%m-%d")
        payload = {"records": [{"date": today, "in": 100, "out": 10, "reason": 7, "cost": 1.25}]}
        # Serialization freezes nested values too, without saving the ledger.
        with patch.dict(D._client_snapshot, {"revision": None, "data": None, "summary": None}), \
             patch.object(D, "_build_payload_locked", return_value=payload) as build, \
             patch.object(D, "refresh_sync", return_value={"seconds": 60, "revision": "tick"}) as clock, \
             patch.object(D, "save_cache") as save:
            first_data, first_summary = D.client_snapshot()
            payload["records"][0].update({"in": 200, "cost": 2.5})
            second_data, second_summary = D.client_snapshot()
            self.assertEqual(first_data, second_data)
            self.assertEqual(first_summary, second_summary)
            self.assertEqual(json.loads(second_summary), {"date": today, "tokens": 110, "spend": 1.25})
            build.assert_called_once()
            clock.return_value = {"seconds": 60, "revision": "manual-refresh"}
            new_data, new_summary = D.client_snapshot()
            self.assertEqual(json.loads(new_data)["records"][0]["in"], 200)
            self.assertEqual(json.loads(new_summary)["tokens"], 210)
            self.assertEqual(json.loads(new_summary)["spend"], 2.5)
            save.assert_not_called()

    def test_http_browser_and_windows_client_sync_without_touching_ledger(self):
        import urllib.request
        today = __import__("time").strftime("%Y-%m-%d")
        payload = {"records": [{"date": today, "in": 100, "out": 10, "cost": 1.25}]}
        server = D.Server(("127.0.0.1", 0), D.Handler)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:" + str(server.server_port)
        monitor = Monitor(self.root, self.root / "data", sys.executable, url=base)
        monitor.request(True)
        browser = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path):
            with browser.open(base + path) as response: return json.load(response)
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": server.server_port}), \
             patch.dict(D._client_snapshot, {"revision": None, "data": None, "summary": None}), \
             patch.object(D, "_build_payload_locked", return_value=payload), \
             patch.object(D, "refresh") as parse, patch.object(D, "save_cache") as save:
            monitor.set_interval(0)
            first = get("/api/data")
            self.assertEqual(monitor.snapshot()["tokens"], first["records"][0]["in"] + 10)
            payload["records"][0]["in"] = 200  # Background parse finishes after the snapshot.
            monitor.sync()
            self.assertEqual(monitor.snapshot()["tokens"], 110)
            self.assertEqual(get("/api/data"), first)
            before = get("/api/sync")
            monitor.refresh(parse=True)  # Tray's explicit refresh updates the browser too.
            self.assertNotEqual(get("/api/sync")["revision"], before["revision"])
            current = get("/api/data")["records"][0]
            self.assertEqual(monitor.snapshot()["tokens"], current["in"] + 10)
            self.assertEqual(monitor.snapshot()["spend"], current["cost"])
            parse.assert_called_once_with(verbose=False)
            save.assert_not_called()
            self.assertFalse((self.root / "data").exists())

    def test_malformed_shared_preference_uses_safe_default(self):
        for value in (True, "60", -1, 1, 999999999):
            N._write("refresh-settings.json", {"seconds": value})
            self.assertEqual(N.refresh_interval(), 15)

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

    def test_login_toggle_quotes_paths_and_only_changes_its_own_run_value(self):
        from types import SimpleNamespace
        from contextlib import nullcontext
        import subprocess
        values = {"AnotherApp": ("keep this", 1)}
        opened = []
        def key(root, path):
            opened.append((root, path))
            return nullcontext("fixture-key")
        def query(key, name):
            if name not in values:
                raise FileNotFoundError(name)
            return values[name]
        registry = SimpleNamespace(HKEY_CURRENT_USER="fixture-user", REG_SZ=1,
            OpenKey=key, CreateKey=key, QueryValueEx=query,
            SetValueEx=lambda key,name,reserved,kind,value: values.update({name:(value,kind)}),
            DeleteValue=lambda key,name: values.pop(name))
        python, entry = r"C:\Program Files\Python\pythonw.exe", r"C:\User Folder\AgentTelemetry\tray.py"
        with patch.dict(sys.modules, {"winreg": registry}):
            self.assertFalse(login_enabled(python, entry))
            toggle_login(python, entry)
            self.assertTrue(login_enabled(python, entry))
            self.assertEqual(values["AgentTelemetry"], (subprocess.list2cmdline([python,entry]), 1))
            toggle_login(python, entry)
            self.assertFalse(login_enabled(python, entry))
        self.assertEqual(values, {"AnotherApp": ("keep this", 1)})
        self.assertTrue(all(root=="fixture-user" and path==r"Software\Microsoft\Windows\CurrentVersion\Run"
                            for root,path in opened))

    def test_cancelled_probe_does_not_start_or_connect(self):
        def probe():
            self.monitor.requested.clear()
            return {"ready": True, "service": "agent-telemetry"}
        with patch.object(self.monitor, "_probe", side_effect=probe), patch("subprocess.Popen") as child:
            self.monitor.start()
            child.assert_not_called()
        self.monitor.stop()
        self.assertEqual(self.monitor.snapshot()["status"], "Monitoring off")

    def test_newer_start_cannot_make_an_old_probe_current_again(self):
        first = self.monitor.request(True)
        def probe():
            self.monitor.request(False)
            self.monitor.request(True)
            return {"ready": True, "service": "agent-telemetry"}
        with patch.object(self.monitor, "_probe", side_effect=probe), \
             patch.object(self.monitor, "refresh") as refresh, patch("subprocess.Popen") as child:
            self.monitor.start(first)
            child.assert_not_called()
            refresh.assert_not_called()
        self.assertTrue(self.monitor.requested.is_set())

    def test_interval_change_does_not_restart_backend_or_undo_stop(self):
        intent = self.monitor.request(True)
        self.monitor.process = object()
        def setting(*args, **kwargs):
            self.monitor.request(False)  # UI Stop arrives during the owned shutdown.
            return {"seconds": 60, "revision": "fixture"}
        with patch.object(self.monitor, "stop") as stop, \
             patch.object(self.monitor, "start") as start, \
             patch.object(self.monitor, "_request", side_effect=setting), \
             patch.object(self.monitor, "refresh") as refresh:
            self.monitor.set_interval(60, intent)
            stop.assert_not_called()
            start.assert_not_called()
            refresh.assert_not_called()
        self.assertFalse(self.monitor.requested.is_set())

    def test_shared_ticks_manual_refresh_and_failure_retry_match_dashboard(self):
        state = {"seconds": 60, "revision": "first"}
        summary = {"date": "2026-10-10", "tokens": 100, "spend": 1.25}
        calls = []
        def request(path, *args, **kwargs):
            calls.append(path)
            if path == "/api/sync": return dict(state)
            if path == "/api/refresh": state["revision"] = "manual"; return {}
            return dict(summary)
        with patch.object(self.monitor, "_request", side_effect=request):
            self.monitor.sync()
            self.assertEqual(self.monitor.interval, 60)
            self.assertEqual(self.monitor.snapshot()["tokens"], 100)
            summary["tokens"] = 200
            self.monitor.sync()
            self.assertEqual(self.monitor.snapshot()["tokens"], 100)
            self.assertEqual(calls.count("/api/summary"), 1)
            state.update(seconds=0, revision="browser-selected-manual")
            self.monitor.sync()
            self.assertEqual(self.monitor.interval, 0)
            summary["tokens"] = 300
            self.monitor.sync()
            self.assertEqual(self.monitor.snapshot()["tokens"], 200)
            state["revision"] = "browser-manual-refresh"
            self.monitor.sync()
            self.assertEqual(self.monitor.snapshot()["tokens"], 300)
            self.monitor.refresh(parse=True)
            self.assertEqual(state["revision"], "manual")
            # A failed summary must retry even when Manual keeps the tick constant.
            self.monitor.stale = True
            summary["tokens"] = 400
            self.monitor.sync()
            self.assertEqual(self.monitor.snapshot()["tokens"], 400)
            self.assertFalse(self.monitor.stale)

    def test_native_interval_updates_the_shared_dashboard_setting(self):
        with patch.object(self.monitor, "_request", return_value={"seconds": 900, "revision": "new"}) as request, \
             patch.object(self.monitor, "refresh") as refresh:
            self.monitor.set_interval(900)
            request.assert_called_once_with("/api/sync", {"seconds": 900})
            self.assertEqual(self.monitor.interval, 900)
            refresh.assert_called_once_with(intent=0)

    def test_latest_start_survives_an_older_queued_stop(self):
        old_stop = self.monitor.request(False)
        latest_start = self.monitor.request(True)
        with patch.object(self.monitor, "stop") as stop, patch.object(self.monitor, "start") as start:
            self.monitor.apply_command("stop", old_stop)
            self.monitor.apply_command("start", latest_start)
            stop.assert_not_called()
            start.assert_called_once_with(latest_start)
        self.assertTrue(self.monitor.requested.is_set())

    def test_late_summary_from_an_old_intent_is_not_applied(self):
        def request(*args, **kwargs):
            self.monitor.request(False)
            self.monitor.request(True)
            return {"date": "2026-10-10", "tokens": 999, "spend": 99}
        with patch.object(self.monitor, "_request", side_effect=request):
            self.monitor.refresh()
        self.assertEqual(self.monitor.snapshot()["tokens"], 0)

    def test_restart_waits_for_an_already_owned_starting_process(self):
        from unittest.mock import Mock
        process = Mock(pid=123)
        process.poll.return_value = None
        self.monitor.process = process
        health = {"ready": True, "service": "agent-telemetry", "pid": 123}
        with patch.object(self.monitor, "_probe", side_effect=[None, health]), \
             patch.object(self.monitor, "refresh") as refresh, patch("subprocess.Popen") as child:
            self.monitor.start()
            child.assert_not_called()
            refresh.assert_called_once_with(intent=0)

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

    def test_open_dashboard_waits_for_readiness_and_respects_cancel(self):
        from unittest.mock import Mock
        ready = {"ready":True, "service":"agent-telemetry"}
        events = []
        def probe():
            events.append("probe")
            return ready if len(events) >= 3 else dict(ready, ready=False)
        opener = Mock(side_effect=lambda url: events.append("open"))
        with patch.object(self.monitor, "_probe", side_effect=probe), \
             patch.object(self.monitor, "refresh") as refresh, patch("time.sleep"):
            self.monitor.open_dashboard(opener=opener)
            refresh.assert_called_once_with(intent=0)
            opener.assert_called_once_with(self.monitor.url)
        self.assertEqual(events, ["probe", "probe", "probe", "open"])
        opener.reset_mock()
        def cancel():
            self.monitor.request(False)
            return ready
        with patch.object(self.monitor, "_probe", side_effect=cancel):
            self.monitor.open_dashboard(opener=opener)
            opener.assert_not_called()

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
