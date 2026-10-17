"""Local-server endpoints: isolated API boundary tests, no process control."""
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D


class LocalServerHTTPTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="telemetry-server-http-")
        self.addCleanup(temporary.cleanup)
        self.server = D.Server(("127.0.0.1", 0), D.Handler)
        self.addCleanup(self.server.server_close)
        self.bind = patch.dict(D.BIND, {"host": "127.0.0.1", "port": self.server.server_port})
        self.bind.start()
        self.addCleanup(self.bind.stop)
        directory = patch.object(D, "DATA_DIR", temporary.name)
        directory.start()
        self.addCleanup(directory.stop)
        factory = patch.object(D, "LocalServerInventory")
        self.factory = factory.start()
        self.addCleanup(factory.stop)
        self.inventory = self.factory.return_value
        self.inventory.snapshot.return_value = {"supported": True, "servers": [], "updatedAt": "fixture"}
        for method in ("stop", "configure", "set_pinned", "start", "restart"):
            getattr(self.inventory, method).return_value = {"ok": True}
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.shutdown)

    def request(self, method="GET", action="", body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        self.addCleanup(connection.close)
        connection.request(method, "/api/local-servers" + action,
                           json.dumps(body) if body is not None else None,
                           headers or {"Content-Type": "application/json"})
        response = connection.getresponse()
        return response.status, json.loads(response.read())

    def test_private_get_is_loopback_only_and_same_origin(self):
        self.assertEqual(self.request()[0], 200)
        self.inventory.snapshot.assert_called_once_with()
        self.inventory.snapshot.reset_mock()
        for headers in ({"Origin": "https://example.invalid"}, {"Host": "attacker.invalid"},
                        {"Sec-Fetch-Site": "cross-site"}):
            self.assertEqual(self.request(headers=headers)[0], 403)
        with patch.object(D.Handler, "_loopback_client", return_value=False):
            self.assertEqual(self.request()[0], 403)
            self.assertEqual(self.request("POST", "/pin", {"id": "fixture", "pinned": True})[0], 403)
        self.inventory.snapshot.assert_not_called()
        self.inventory.set_pinned.assert_not_called()

    def test_all_writes_require_json_and_same_origin(self):
        for action in ("/configure", "/pin", "/stop", "/start", "/restart"):
            for headers in ({"Content-Type": "text/plain"},
                            {"Content-Type": "application/json", "Origin": "https://example.invalid"}):
                with self.subTest(action=action, headers=headers):
                    self.assertEqual(self.request("POST", action, {"confirmed": True}, headers)[0], 403)
        self.factory.assert_not_called()

    def test_process_actions_require_explicit_boolean_confirmation(self):
        for action in ("/stop", "/start", "/restart"):
            for value in (None, False, 1, "true"):
                with self.subTest(action=action, value=value):
                    status, result = self.request("POST", action, {"confirmed": value})
                    self.assertEqual(status, 400)
                    self.assertIn("Confirm", result["error"])
        self.factory.assert_not_called()

    def test_action_arguments_and_restart_target_identity(self):
        rows = [
            ("configure", {"id": "server-a", "command": "npm run dev"}, "configure", ("server-a", "npm run dev", None)),
            ("pin", {"id": "server-a", "pinned": True}, "set_pinned", ("server-a", True)),
            ("stop", {"id": "server-a", "confirmed": True}, "stop", ("server-a",)),
            ("start", {"profileId": "profile-a", "confirmed": True}, "start", ("profile-a",)),
            ("restart", {"profileId": "profile-a", "serverId": "server-a", "confirmed": True}, "restart", ("profile-a", "server-a")),
        ]
        for action, body, method, arguments in rows:
            with self.subTest(action=action):
                self.assertEqual(self.request("POST", "/" + action, body)[0], 200)
                getattr(self.inventory, method).assert_called_once_with(*arguments)

    def test_invalid_body_and_inventory_errors_are_visible(self):
        self.assertEqual(self.request("POST", "/pin", ["not an object"])[0], 400)
        self.assertEqual(self.request("POST", "/configure", {"command": "x" * 17000})[0], 400)
        self.inventory.start.side_effect = ValueError("That server is already running.")
        status, result = self.request("POST", "/start", {"profileId": "fixture", "confirmed": True})
        self.assertEqual(status, 400)
        self.assertEqual(result["error"], "That server is already running.")
        self.inventory.snapshot.side_effect = RuntimeError("Could not inspect local listening ports.")
        status, result = self.request()
        self.assertEqual(status, 500)
        self.assertIn("Could not inspect", result["error"])


if __name__ == "__main__":
    unittest.main()
