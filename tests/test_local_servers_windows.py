"""Portable Windows safety checks; real Win32 lifecycle is checked separately."""
import ctypes as C
import os
import socket
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch

import local_servers as L
import local_servers_windows as W


class WindowsTableTests(unittest.TestCase):
    def test_ipv4_ipv6_and_malformed_tables(self):
        a = W.TCP4(2, struct.unpack('<I', socket.inet_pton(socket.AF_INET, '127.0.0.1'))[0],
                   socket.htons(5173), 0, 0, 1234)
        self.assertEqual(W._parse_table(struct.pack('<I', 1) + bytes(a), socket.AF_INET),
                         [{'pid': 1234, 'host': '127.0.0.1', 'port': 5173}])
        b = W.TCP6()
        b.state, b.pid, b.port = 2, 5678, socket.htons(9876)
        b.address[:] = socket.inet_pton(socket.AF_INET6, '::1')
        self.assertEqual(W._parse_table(struct.pack('<I', 1) + bytes(b), socket.AF_INET6),
                         [{'pid': 5678, 'host': '::1', 'port': 9876}])
        self.assertEqual(C.sizeof(W.TCP4), 24)
        self.assertEqual(C.sizeof(W.TCP6), 56)
        for raw in (b'', struct.pack('<I', 2) + bytes(a)):
            with self.assertRaises(RuntimeError):
                W._parse_table(raw, socket.AF_INET)

    def test_discovery_filters_owners_and_preserves_unverifiable_known_listener(self):
        rows = [{'pid': n, 'host': '127.0.0.1', 'port': n} for n in (1234, 2345, 3456)]
        details = {1234: {'uid': 'self', 'startedAt': '1', 'process': 'python.exe'},
                   2345: {'uid': 'other', 'startedAt': '2', 'process': 'node.exe'}}
        with patch.object(W, 'tcp_rows', return_value=rows), patch.object(W, 'process_details', return_value=details):
            found = W.listener_rows('self')
            self.assertEqual([row['pid'] for row in found], [1234])
            self.assertEqual(found[0]['cwd'], '')
            with self.assertRaisesRegex(RuntimeError, 'identity'):
                W.listener_rows('self', [{'pid': 3456, 'state': 'running'}])

    def test_stop_validates_identity_on_the_same_held_handle(self):
        api = Mock()
        api.OpenProcess.return_value = 88
        server = {'pid': 1234, 'startedAt': '123456'}
        with patch.object(W, '_api', return_value=api), patch.object(W, '_owner', return_value='self'), \
                patch.object(W, 'current_uid', return_value='self'), patch.object(W, '_created', return_value='changed'):
            with self.assertRaisesRegex(ValueError, 'changed'):
                W.stop(server)
            api.TerminateProcess.assert_not_called()
        api.CloseHandle.assert_called_with(88)
        with patch.object(W, '_api', return_value=api), patch.object(W, '_owner', return_value='other'), \
                patch.object(W, 'current_uid', return_value='self'), patch.object(W, '_created', return_value='123456'):
            with self.assertRaises(ValueError):
                W.stop(server)
            api.TerminateProcess.assert_not_called()
        with patch.object(W, '_api', return_value=api), patch.object(W, '_owner', return_value='self'), \
                patch.object(W, 'current_uid', return_value='self'), patch.object(W, '_created', return_value='123456'):
            W.stop(server)
        api.TerminateProcess.assert_called_once_with(88, 1)


class WindowsInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd = os.path.join(self.tmp.name, 'project space ü')
        os.mkdir(self.cwd)
        self.inventory = L.LocalServerInventory(self.tmp.name)
        self.backend = Mock()
        self.backend.current_uid.return_value = 'self'
        self.row = dict(pid=12345, startedAt='123456', process='python.exe',
                        uid='self', cwd='', host='127.0.0.1', port=5173, processGroup=None)
        self.backend.listener_rows.return_value = [self.row]
        for target, name, value in ((L.platform, 'system', 'Windows'), (L, '_windows', self.backend)):
            mocked = patch.object(target, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.server_id = L._server_id(12345, '123456', 5173)

    def test_requires_explicit_folder_then_retains_exact_identity_only(self):
        row = self.inventory.snapshot()['servers'][0]
        self.assertIsNone(row['profileId'])
        with self.assertRaisesRegex(ValueError, 'full path'):
            self.inventory.configure(row['id'], 'npm run dev')
        with self.assertRaisesRegex(ValueError, 'folder'):
            self.inventory.set_pinned(row['id'], True)
        for directory in ('relative', '\0bad', os.path.join(self.cwd, 'missing')):
            with self.assertRaises(ValueError):
                self.inventory.configure(row['id'], 'npm run dev', directory)
        profile = self.inventory.configure(row['id'], 'npm run dev', self.cwd)['profileId']
        self.inventory.set_pinned(row['id'], True)
        known = self.inventory.snapshot()['servers'][0]
        self.assertEqual(known['profileId'], profile)
        self.assertTrue(known['isPinned'])
        self.backend.listener_rows.return_value = [dict(self.row, startedAt='reused')]
        snapshot = self.inventory.snapshot()['servers']
        replacement = next(item for item in snapshot if item['state'] == 'running')
        self.assertIsNone(replacement['profileId'])
        self.assertFalse(replacement['isConfigured'])
        self.assertTrue(any(item['profileId'] == profile and item['state'] == 'stopped' for item in snapshot))

    def test_launch_only_accepts_listener_in_the_new_job(self):
        profile = self.inventory.configure(self.server_id, 'npm run dev', self.cwd)['profileId']
        self.backend.listener_rows.return_value = [dict(self.row, pid=54321, startedAt='new')]
        process = Mock(pid=43210)
        self.backend.launched_listener.return_value = False
        snapshot = self.inventory._scan_and_store(expected_profile_id=profile, launch_process=process)
        self.assertIsNone(self.inventory._last_launch_server_id)
        self.assertFalse(next(item for item in snapshot['servers'] if item['state'] == 'running')['isConfigured'])
        self.backend.launched_listener.return_value = True
        snapshot = self.inventory._scan_and_store(expected_profile_id=profile, launch_process=process)
        self.assertEqual(self.inventory._last_launch_server_id, L._server_id(54321, 'new', 5173))
        self.assertEqual(next(item for item in snapshot['servers'] if item['state'] == 'running')['profileId'], profile)

    def test_windows_stop_uses_provider_and_protects_dashboard(self):
        self.inventory.stop(self.server_id)
        self.backend.stop.assert_called_once()
        with patch.object(L, '_protected_pids', return_value={12345}):
            with self.assertRaisesRegex(ValueError, 'own dashboard'):
                self.inventory.stop(self.server_id)
        self.assertEqual(self.backend.stop.call_count, 1)

    def test_control_token_is_case_insensitive_and_commands_stay_private(self):
        with patch.dict(os.environ, {'Agent_Telemetry_Control_Token': 'private', 'PUBLIC_FIXTURE': 'ok'}):
            self.assertNotIn('Agent_Telemetry_Control_Token', L._command_env())
        self.inventory.configure(self.server_id, 'echo private-command', self.cwd)
        self.assertNotIn('private-command', str(self.inventory.snapshot()))


if __name__ == '__main__':
    unittest.main()
