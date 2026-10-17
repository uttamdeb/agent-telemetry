"""Real Win32 controls against disposable test-owned jobs, never user servers."""
import ctypes as C
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import local_servers as L
import local_servers_windows as W


@unittest.skipUnless(sys.platform == 'win32', 'Requires Windows TCP and process APIs')
class WindowsServerLiveTests(unittest.TestCase):
    def test_owned_listener_discovery_configure_pin_stop_start_restart(self):
        api = W._api()
        kernel = C.WinDLL('kernel32', use_last_error=True)
        duplicate = kernel['DuplicateHandle']
        duplicate.restype, duplicate.argtypes = W.BOOL, [W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD]
        terminate_job = kernel['TerminateJobObject']
        terminate_job.restype, terminate_job.argtypes = W.BOOL, [W.HANDLE, W.DWORD]
        jobs = []
        real_launch = W.launch

        def owned_launch(*args, **kwargs):
            child = real_launch(*args, **kwargs)
            retained = W.HANDLE()
            current = api.GetCurrentProcess()
            self.assertTrue(duplicate(current, child.job, current, C.byref(retained), 0, False, 2))
            jobs.append(retained)
            return child

        with tempfile.TemporaryDirectory(prefix='telemetry-windows-owned-') as temporary:
            root = Path(temporary)
            project = root / 'project space ü'
            project.mkdir()
            state = project / 'identity.txt'
            script = project / 'listener.py'
            script.write_text(
                "import os,socket,sys,time\n"
                "from pathlib import Path\n"
                "assert 'AGENT_TELEMETRY_CONTROL_TOKEN' not in os.environ\n"
                "assert os.path.samefile(Path.cwd(), Path(sys.argv[2]).parent)\n"
                "with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:\n"
                " s.bind(('127.0.0.1',int(sys.argv[1])))\n"
                " s.listen(8)\n"
                " Path(sys.argv[2]).write_text(str(os.getpid()))\n"
                " while True: time.sleep(1)\n", encoding='utf-8')
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            args = [sys.executable, '-I', str(script), str(port), str(state)]
            command = subprocess.list2cmdline(args)
            inventory = L.LocalServerInventory(str(root / 'inventory'))
            # Initial process is also fixture-owned but not launched by the
            # inventory, so its working folder must initially be unknown.
            first = subprocess.Popen(args, cwd=project, env=L._command_env(),
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 15
                row = None
                while time.monotonic() < deadline:
                    row = next((item for item in inventory.snapshot()['servers']
                                if item['pid'] == first.pid and item['port'] == port), None)
                    if row:
                        break
                    self.assertIsNone(first.poll(), 'Test listener exited before discovery')
                    time.sleep(.1)
                self.assertIsNotNone(row, 'Test-owned TCP listener was not discovered')
                self.assertEqual(row['cwd'], '')
                profile = inventory.configure(row['id'], command, str(project))['profileId']
                inventory.set_pinned(row['id'], True)
                inventory.stop(row['id'])
                first.wait(timeout=5)
                with patch.object(W, 'launch', side_effect=owned_launch), \
                        patch.dict(os.environ, {'AGENT_TELEMETRY_CONTROL_TOKEN': 'test-only-private'}):
                    started = inventory.start(profile)['server']
                    self.assertTrue(os.path.samefile(started['cwd'], project))
                    self.assertTrue(started['isPinned'])
                    restarted = inventory.restart(profile, started['id'])['server']
                    self.assertNotEqual(started['id'], restarted['id'])
                    self.assertEqual(restarted['profileId'], profile)
                    self.assertTrue(restarted['isPinned'])
                    inventory.stop(restarted['id'])
                self.assertNotIn('test-only-private', Path(inventory.path).read_text(encoding='utf-8'))
            finally:
                if first.poll() is None:
                    first.terminate()  # The held Popen handle names only this test's initial child.
                first.wait(timeout=5)
                for job in jobs:
                    terminate_job(job, 1)  # Only duplicated handles to jobs this test created.
                    api.CloseHandle(job)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and port in W.listening_ports():
                    time.sleep(.1)


if __name__ == '__main__':
    unittest.main()
