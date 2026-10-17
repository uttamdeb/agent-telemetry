"""macOS smoke test against disposable test-owned listeners, never user servers."""
import os
from pathlib import Path
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_servers as L


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("lsof"), "Requires macOS listener discovery")
class LocalServerLiveTests(unittest.TestCase):
    def test_disposable_server_configure_stop_start_and_restart(self):
        with tempfile.TemporaryDirectory(prefix="telemetry-owned-server-") as temporary:
            root = Path(temporary)
            project = root / "project"
            project.mkdir()
            inventory = L.LocalServerInventory(str(root / "inventory"))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            script = project / "listener.py"
            state = root / "listener-state.txt"
            # HTTPServer performs reverse DNS during bind. This fixture tests
            # TCP lifecycle only and deliberately has no hostname lookup.
            script.write_text(
                "import socket, sys, time\n"
                "from pathlib import Path\n"
                "state = Path(sys.argv[2])\n"
                "state.write_text('starting')\n"
                "with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:\n"
                "    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
                "    listener.bind(('127.0.0.1', int(sys.argv[1])))\n"
                "    listener.listen(8)\n"
                "    state.write_text('listening')\n"
                "    while True: time.sleep(1)\n", encoding="utf-8")
            arguments = [sys.executable, "-I", str(script), str(port), str(state)]
            launch = subprocess.Popen
            children = []

            def owned_launch(*args, **kwargs):
                child = launch(*args, **kwargs)
                children.append(child)
                return child

            # Track only explicit test launches. Discovery's subprocess.run uses
            # Popen too; the wrapper deliberately captures those short-lived
            # children as well so every process we clean up was created here.
            with patch.object(L.subprocess, "Popen", side_effect=owned_launch):
                try:
                    first = subprocess.Popen(arguments, cwd=project, stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                             start_new_session=True)
                    deadline = time.monotonic() + 15
                    server = None
                    observed = []
                    while time.monotonic() < deadline:
                        snapshot = inventory.snapshot()
                        observed = [row for row in snapshot["servers"]
                                    if row["pid"] == first.pid or row["port"] == port]
                        server = next((row for row in snapshot["servers"]
                                       if row["pid"] == first.pid and row["port"] == port
                                       and os.path.samefile(row["cwd"], project)), None)
                        if server:
                            break
                        if first.poll() is not None:
                            self.fail("Disposable test server exited before discovery")
                        time.sleep(0.1)
                    if server is None:
                        # Only fixture-owned PID/port metadata enters this error.
                        listener = L._run([L._LSOF, "-a", "-p", str(first.pid), "-nP",
                                           "-iTCP", "-sTCP:LISTEN", "-Fpcn"])
                        identity = L._run([L._PS, "-p", str(first.pid), "-o", "pid=,ppid=,stat=,comm="])
                        cwd = L._working_directories(L._current_uid(), {first.pid})
                        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                            probe.settimeout(1)
                            connect_status = probe.connect_ex(("127.0.0.1", port))
                        self.fail("Disposable listener was not discovered: " + repr({
                            "pid": first.pid, "port": port, "project": str(project),
                            "supported": snapshot.get("supported"), "rows": observed,
                            "listener": (listener.returncode, listener.stdout, listener.stderr) if listener else None,
                            "identity": identity.stdout if identity else None, "cwd": cwd,
                            "connect_status": connect_status,
                            "fixture_state": state.read_text() if state.exists() else "not started",
                        }))
                    command = " ".join(shlex.quote(argument) for argument in arguments)
                    profile_id = inventory.configure(server["id"], command)["profileId"]
                    inventory.set_pinned(server["id"], True)
                    inventory.stop(server["id"])
                    first.wait(timeout=5)
                    started = inventory.start(profile_id)["server"]
                    self.assertTrue(os.path.samefile(started["cwd"], project))
                    self.assertTrue(started["isPinned"])
                    restarted = inventory.restart(profile_id, started["id"])["server"]
                    self.assertNotEqual(restarted["id"], started["id"])
                    self.assertEqual(restarted["profileId"], profile_id)
                    self.assertTrue(restarted["isPinned"])
                    inventory.stop(restarted["id"])
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.terminate()
                    for child in children:
                        try:
                            child.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            # These are test-created subprocesses, never a
                            # discovered process or an existing dashboard.
                            child.kill()
                            child.wait(timeout=5)


if __name__ == "__main__":
    unittest.main()
