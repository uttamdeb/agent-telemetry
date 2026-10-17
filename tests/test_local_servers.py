import contextlib
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

import local_servers


class FakeProcess:
    def __init__(self, pid, exit_status=None):
        self.pid = pid
        self.exit_status = exit_status
        self.poll = mock.Mock(return_value=exit_status)
        self.wait = mock.Mock(return_value=exit_status)


class LocalServerInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = os.path.join(self.temp.name, "project")
        os.makedirs(self.cwd)
        self.inventory = local_servers.LocalServerInventory(self.temp.name)
        system = mock.patch.object(local_servers.platform, "system", return_value="Darwin")
        system.start()
        self.addCleanup(system.stop)
        self.capability = mock.patch.object(
            local_servers, "_capability", return_value=(True, "Darwin", "")
        )
        self.capability.start()
        self.addCleanup(self.capability.stop)
        self.pid = 32751
        self.started_at = "Thu Oct 15 09:30:00 2026"
        self.port = 5173
        self.row = self.listener(self.pid, self.started_at, self.port, self.pid)
        self.server_id = local_servers._server_id(self.pid, self.started_at, self.port)

    def listener(self, pid, started_at, port, process_group=None, cwd=None):
        return {
            "pid": pid,
            "process": "node",
            "cwd": cwd or self.cwd,
            "host": "127.0.0.1",
            "port": port,
            "startedAt": started_at,
            "uid": local_servers._current_uid(),
            "processGroup": process_group,
        }

    def save_profile(self, command="npm run dev", cwd=None, port=None):
        cwd = cwd or self.cwd
        port = self.port if port is None else port
        profile_id = local_servers._profile_id(cwd, port)
        self.inventory._write({
            "updatedAt": "",
            "servers": [],
            "profiles": [{
                "id": profile_id,
                "cwd": os.path.realpath(cwd),
                "port": port,
                "command": command,
                "isPinned": False,
            }],
        })
        return profile_id

    def process_details(self, pid=None, started_at=None, uid=None):
        pid = self.pid if pid is None else pid
        return {pid: {
            "startedAt": started_at or self.started_at,
            "uid": local_servers._current_uid() if uid is None else uid,
            "processGroup": pid,
        }}

    def test_snapshot_returns_capability_and_accepts_missing_inventory(self):
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]):
            payload = self.inventory.snapshot()
        self.assertTrue(payload["supported"])
        self.assertEqual(payload["platform"], "Darwin")
        self.assertEqual(payload["servers"][0]["id"], self.server_id)
        self.assertTrue(os.path.isfile(self.inventory.path))

    def test_mutating_actions_use_the_servers_list_and_save_configuration(self):
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]):
            configured = self.inventory.configure(self.server_id, "npm run dev")
            pinned = self.inventory.set_pinned(self.server_id, True)
            with mock.patch.object(local_servers, "_process_details", return_value=self.process_details()), \
                    mock.patch.object(local_servers.os, "kill") as kill:
                stopped = self.inventory.stop(self.server_id)

        self.assertEqual(configured["profileId"], local_servers._profile_id(self.cwd, self.port))
        self.assertTrue(pinned["isPinned"])
        self.assertEqual(stopped["server"]["id"], self.server_id)
        kill.assert_called_once_with(self.pid, local_servers.signal.SIGTERM)
        stored = self.inventory._read_payload()
        self.assertEqual(stored["profiles"][0]["command"], "npm run dev")
        self.assertTrue(stored["profiles"][0]["isPinned"])

    def test_stop_rejects_changed_start_time_or_owner(self):
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]), \
                mock.patch.object(local_servers, "_process_details", return_value=self.process_details(started_at="reused pid")), \
                mock.patch.object(local_servers.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "changed since"):
                self.inventory.stop(self.server_id)
            kill.assert_not_called()

        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]), \
                mock.patch.object(local_servers, "_process_details", return_value=self.process_details(uid="another-user")), \
                mock.patch.object(local_servers.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "changed since"):
                self.inventory.stop(self.server_id)
            kill.assert_not_called()

    def test_start_strips_control_token_and_accepts_only_its_process_group(self):
        profile_id = self.save_profile()
        launched = FakeProcess(86142)
        calls = 0

        def rows(_uid):
            nonlocal calls
            calls += 1
            if calls == 1:
                return []
            return [self.listener(90112, self.started_at, self.port, launched.pid)]

        with mock.patch.dict(os.environ, {"AGENT_TELEMETRY_CONTROL_TOKEN": "private"}), \
                mock.patch.object(local_servers, "_listener_rows", side_effect=rows), \
                mock.patch.object(local_servers, "_listening_ports", return_value=set()), \
                mock.patch.object(local_servers, "_probe_port_available", return_value=True), \
                mock.patch.object(local_servers.subprocess, "Popen", return_value=launched) as popen:
            result = self.inventory.start(profile_id)

        self.assertEqual(result["server"]["profileId"], profile_id)
        child_env = popen.call_args.kwargs["env"]
        self.assertNotIn("AGENT_TELEMETRY_CONTROL_TOKEN", child_env)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_start_checks_all_listening_ports_before_running_saved_command(self):
        profile_id = self.save_profile()
        with mock.patch.object(local_servers, "_listener_rows", return_value=[]), \
                mock.patch.object(local_servers, "_listening_ports", return_value={self.port}), \
                mock.patch.object(local_servers.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "already used"):
                self.inventory.start(profile_id)
        popen.assert_not_called()

    def test_start_rejects_port_when_bind_probe_finds_a_foreign_listener(self):
        profile_id = self.save_profile()
        with mock.patch.object(local_servers, "_listener_rows", return_value=[]), \
                mock.patch.object(local_servers, "_listening_ports", return_value=set()), \
                mock.patch.object(local_servers, "_probe_port_available", return_value=False), \
                mock.patch.object(local_servers.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "already used"):
                self.inventory.start(profile_id)
        popen.assert_not_called()

    def test_start_does_not_claim_an_unrelated_listener_with_the_same_profile_and_port(self):
        profile_id = self.save_profile()
        launched = FakeProcess(86142)
        calls = 0

        def rows(_uid):
            nonlocal calls
            calls += 1
            if calls == 1:
                return []
            return [self.listener(90112, self.started_at, self.port, 99999)]

        with mock.patch.object(local_servers, "_listener_rows", side_effect=rows), \
                mock.patch.object(local_servers, "_listening_ports", return_value=set()), \
                mock.patch.object(local_servers, "_probe_port_available", return_value=True), \
                mock.patch.object(local_servers.subprocess, "Popen", return_value=launched), \
                mock.patch.object(local_servers.time, "monotonic", side_effect=[0, 0, 0, 0, 0, 16]), \
                mock.patch.object(local_servers.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "did not open"):
                self.inventory.start(profile_id)

    def test_restart_requires_exact_live_server_and_validates_profile_before_stop(self):
        profile_id = self.save_profile()
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]), \
                mock.patch.object(local_servers.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "exact running server"):
                self.inventory.restart(profile_id)
            kill.assert_not_called()

        missing_cwd = os.path.join(self.temp.name, "deleted-project")
        bad_profile_id = self.save_profile(cwd=missing_cwd)
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]), \
                mock.patch.object(local_servers.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "folder no longer exists"):
                self.inventory.restart(bad_profile_id, self.server_id)
            kill.assert_not_called()

        self.save_profile()
        stale_id = "0" * 20
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]), \
                mock.patch.object(local_servers.os, "kill") as kill:
            with self.assertRaisesRegex(ValueError, "changed since confirmation"):
                self.inventory.restart(profile_id, stale_id)
            kill.assert_not_called()

    def test_restart_holds_profile_lifecycle_and_starts_after_exact_process_stops(self):
        profile_id = self.save_profile()
        launched = FakeProcess(86142)
        replacement = self.listener(90112, "Thu Oct 15 10:00:00 2026", self.port, launched.pid)
        calls = 0
        events = []

        def rows(_uid):
            nonlocal calls
            calls += 1
            if calls == 1:
                return [self.row]
            if calls in (2, 3):
                return []
            return [replacement]

        original_lock = local_servers._profile_start_lock

        @contextlib.contextmanager
        def tracking_lock(path):
            events.append(("lock-enter", path))
            with original_lock(path):
                try:
                    yield
                finally:
                    events.append(("lock-exit", path))

        def launch(*args, **kwargs):
            events.append(("launch", args, kwargs))
            return launched

        with mock.patch.object(local_servers, "_listener_rows", side_effect=rows), \
                mock.patch.object(local_servers, "_process_details", return_value=self.process_details()), \
                mock.patch.object(local_servers, "_listening_ports", return_value=set()), \
                mock.patch.object(local_servers, "_probe_port_available", return_value=True), \
                mock.patch.object(local_servers, "_profile_start_lock", side_effect=tracking_lock), \
                mock.patch.object(local_servers.subprocess, "Popen", side_effect=launch), \
                mock.patch.object(local_servers.os, "kill") as kill, \
                mock.patch.object(local_servers.time, "monotonic", side_effect=[0] * 20), \
                mock.patch.object(local_servers.time, "sleep"):
            result = self.inventory.restart(profile_id, self.server_id)

        self.assertEqual(result["server"]["pid"], replacement["pid"])
        kill.assert_called_once_with(self.pid, local_servers.signal.SIGTERM)
        self.assertEqual([event[0] for event in events], ["lock-enter", "launch", "lock-exit"])
        self.assertIn(profile_id, events[0][1])

    def test_restart_refuses_a_saved_command_changed_while_waiting(self):
        profile_id = self.save_profile(command="npm run original")
        calls = 0

        def rows(_uid):
            nonlocal calls
            calls += 1
            return [self.row] if calls <= 3 else []

        def change_profile(_seconds):
            self.inventory.configure(self.server_id, "npm run changed")

        with mock.patch.object(local_servers, "_listener_rows", side_effect=rows), \
                mock.patch.object(local_servers, "_process_details", return_value=self.process_details()), \
                mock.patch.object(local_servers.os, "kill"), \
                mock.patch.object(local_servers.time, "monotonic", side_effect=[0] * 50), \
                mock.patch.object(local_servers.time, "sleep", side_effect=change_profile), \
                mock.patch.object(local_servers.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(ValueError, "configuration changed during restart"):
                self.inventory.restart(profile_id, self.server_id)
        popen.assert_not_called()
        self.assertEqual(self.inventory._read_payload()["profiles"][0]["command"], "npm run changed")

    def test_recent_rows_are_deduplicated_and_expired_profiles_remain_available(self):
        profile_id = self.save_profile(command="npm run dev")
        old_seen = local_servers._stamp(local_servers._now() - local_servers.timedelta(days=40))
        previous = {
            "id": local_servers._server_id(1001, self.started_at, self.port),
            "pid": 1001,
            "process": "node-old",
            "cwd": self.cwd,
            "project": "project",
            "host": "127.0.0.1",
            "port": self.port,
            "startedAt": self.started_at,
            "state": "stopped",
            "firstSeen": old_seen,
            "lastSeen": old_seen,
            "stoppedAt": old_seen,
            "profileId": profile_id,
            "isConfigured": True,
            "isPinned": False,
        }
        self.inventory._write({
            "updatedAt": old_seen,
            "servers": [previous],
            "profiles": self.inventory._read_payload()["profiles"],
        })
        with mock.patch.object(local_servers, "_listener_rows", return_value=[]):
            payload = self.inventory.snapshot()
        self.assertEqual(len(payload["servers"]), 1)
        saved = payload["servers"][0]
        self.assertEqual(saved["id"], profile_id)
        self.assertEqual(saved["pid"], 0)
        self.assertEqual(saved["state"], "stopped")
        self.assertTrue(saved["isConfigured"])
        self.assertEqual(self.inventory._read_payload()["servers"], [])

        recent_a = dict(previous, pid=1002, startedAt="Thu Oct 15 10:30:00 2026", lastSeen=local_servers._stamp(local_servers._now()))
        recent_b = dict(previous, pid=1003, startedAt="Thu Oct 15 11:30:00 2026", lastSeen=local_servers._stamp(local_servers._now()))
        recent_a["id"] = local_servers._server_id(recent_a["pid"], recent_a["startedAt"], self.port)
        recent_b["id"] = local_servers._server_id(recent_b["pid"], recent_b["startedAt"], self.port)
        recent_a["isConfigured"] = False
        recent_b["isConfigured"] = False
        self.inventory._write({"updatedAt": "", "servers": [recent_a, recent_b], "profiles": []})
        with mock.patch.object(local_servers, "_listener_rows", return_value=[]):
            deduplicated = self.inventory.snapshot()
        self.assertEqual(len(deduplicated["servers"]), 1)
        self.assertEqual(deduplicated["servers"][0]["pid"], 1003)

    def test_discovery_only_inventory_round_trips_through_configure_and_pin(self):
        legacy = {
            "id": self.server_id,
            "pid": self.pid,
            "process": "node",
            "cwd": self.cwd,
            "project": "project",
            "host": "127.0.0.1",
            "port": self.port,
            "startedAt": self.started_at,
            "state": "running",
            "firstSeen": local_servers._stamp(local_servers._now()),
            "lastSeen": local_servers._stamp(local_servers._now()),
            "stoppedAt": None,
        }
        self.inventory._write({"updatedAt": "", "servers": [legacy]})
        with mock.patch.object(local_servers, "_listener_rows", return_value=[self.row]):
            self.inventory.configure(self.server_id, "npm run legacy")
            self.inventory.set_pinned(self.server_id, True)
        saved = self.inventory._read_payload()
        self.assertEqual(saved["profiles"][0]["command"], "npm run legacy")
        self.assertTrue(saved["profiles"][0]["isPinned"])
        self.assertTrue(saved["servers"][0]["isConfigured"])
        self.assertTrue(saved["servers"][0]["isPinned"])

    def test_corrupt_or_unreadable_inventory_is_preserved_when_scan_fails(self):
        original = b'{"servers": [broken'
        with open(self.inventory.path, "wb") as file:
            file.write(original)
        with mock.patch.object(local_servers, "_listener_rows") as scan:
            with self.assertRaisesRegex(RuntimeError, "corrupt and was left unchanged"):
                self.inventory.snapshot()
            scan.assert_not_called()
        with open(self.inventory.path, "rb") as file:
            self.assertEqual(file.read(), original)

    def test_scan_errors_and_invalid_listener_rows_do_not_rewrite_inventory(self):
        old = {
            "id": self.server_id,
            "pid": self.pid,
            "process": "node",
            "cwd": self.cwd,
            "project": "project",
            "host": "127.0.0.1",
            "port": self.port,
            "startedAt": self.started_at,
            "state": "running",
            "firstSeen": local_servers._stamp(local_servers._now()),
            "lastSeen": local_servers._stamp(local_servers._now()),
            "stoppedAt": None,
            "profileId": local_servers._profile_id(self.cwd, self.port),
            "isConfigured": False,
            "isPinned": False,
        }
        self.inventory._write({"updatedAt": "before", "servers": [old], "profiles": []})
        with open(self.inventory.path, "rb") as file:
            original = file.read()
        with mock.patch.object(local_servers, "_listener_rows", side_effect=RuntimeError("lsof timed out")):
            with self.assertRaisesRegex(RuntimeError, "lsof timed out"):
                self.inventory.snapshot()
        with open(self.inventory.path, "rb") as file:
            self.assertEqual(file.read(), original)

        invalid = dict(self.row, port=70000)
        with mock.patch.object(local_servers, "_listener_rows", return_value=[invalid]):
            with self.assertRaisesRegex(RuntimeError, "invalid process data"):
                self.inventory.snapshot()
        with open(self.inventory.path, "rb") as file:
            self.assertEqual(file.read(), original)

    def test_unsupported_platform_returns_empty_without_scan_or_write_and_rejects_actions(self):
        with mock.patch.object(local_servers, "_capability", return_value=(False, "Linux", "macOS only")), \
                mock.patch.object(local_servers, "_listener_rows") as scan:
            payload = self.inventory.snapshot()
            self.assertFalse(payload["supported"])
            self.assertEqual(payload["platform"], "Linux")
            self.assertEqual(payload["servers"], [])
            self.assertFalse(os.path.exists(self.inventory.path))
            scan.assert_not_called()
            with self.assertRaisesRegex(ValueError, "macOS only"):
                self.inventory.stop(self.server_id)

    def test_missing_darwin_dependencies_are_explicit_errors(self):
        with mock.patch.object(local_servers, "_capability", return_value=(True, "Darwin", "lsof is missing")), \
                mock.patch.object(local_servers, "_listener_rows") as scan:
            with self.assertRaisesRegex(RuntimeError, "lsof is missing"):
                self.inventory.snapshot()
            scan.assert_not_called()
            self.assertFalse(os.path.exists(self.inventory.path))

    def test_inventory_lock_serializes_threads_for_the_same_path(self):
        active = 0
        overlaps = []
        state_lock = threading.Lock()

        def worker():
            nonlocal active
            with local_servers._inventory_lock(self.inventory.lock_path):
                with state_lock:
                    active += 1
                    overlaps.append(active)
                time.sleep(0.02)
                with state_lock:
                    active -= 1

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(max(overlaps), 1)

    def test_listener_endpoint_parser_rejects_invalid_ports(self):
        self.assertEqual(local_servers._endpoint("[::1]:3000"), ("::1", 3000))
        for value in ("localhost:http", "*:0", "*:65536", "bad endpoint"):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                local_servers._endpoint(value)


if __name__ == "__main__":
    unittest.main()
