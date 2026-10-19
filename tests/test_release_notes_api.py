"""Local-only HTTP tests for the release notes and update endpoints."""
import http.client
import json
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import Mock, patch

import dashboard as D


class ReleaseNotesAPITests(unittest.TestCase):
    def setUp(self):
        self.old_bind = dict(D.BIND)
        self.old_version = D.VERSION
        D.VERSION = {"git": False, "describe": "v2.0.0"}
        self.server = D.Server(("127.0.0.1", 0), D.Handler)
        D.BIND.update(host="127.0.0.1", port=self.server.server_port)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=5)
        D.BIND.clear()
        D.BIND.update(self.old_bind)
        D.VERSION = self.old_version

    def request(self, method, path, body=None, *, origin=None):
        headers = {}
        if method == "POST":
            headers["Content-Type"] = "application/json"
            headers["Sec-Fetch-Site"] = "same-origin"
            if body is None:
                body = b"{}"
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode("utf-8")
        if origin is not None:
            headers["Origin"] = origin
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        try:
            parsed = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError):
            parsed = payload.decode("utf-8", "replace")
        return response.status, parsed

    def post(self, path, body, *, origin=None):
        return self.request("POST", path, body, origin=origin)

    def test_status_is_loopback_only_cached_and_does_not_create_state(self):
        with tempfile.TemporaryDirectory() as directory:
            support = Path(directory) / "AgentTelemetryNative"
            with patch.object(D.N, "support_dir", return_value=support):
                status, payload = self.request("GET", "/api/release-notes")
            self.assertEqual(status, 200)
            self.assertEqual(payload["current_version"], "2.0.0")
            self.assertFalse(payload["pending"])
            self.assertFalse(support.exists(), "status must not create or seed release-note storage")

        with patch.object(D.Handler, "_loopback_client", return_value=False):
            status, payload = self.request("GET", "/api/release-notes")
        self.assertEqual(status, 403)
        self.assertIn("only on this machine", payload["error"])

    def test_release_notes_actions_dispatch_only_valid_same_origin_requests(self):
        service = Mock()
        service.claim.return_value = {"pending": True, "claim": "a" * 64}
        service.shown.return_value = {"pending": False, "shown": True}
        service.check.return_value = {"status": "available", "latest": {"tag": "v2.1.0"}}
        with patch.object(D.RN, "ReleaseNotes", return_value=service):
            status, claim = self.post("/api/release-notes", {"action": "claim"})
            self.assertEqual(status, 200)
            self.assertEqual(claim["claim"], "a" * 64)

            status, shown = self.post("/api/release-notes", {"action": "shown", "claim": "a" * 64})
            self.assertEqual(status, 200)
            self.assertTrue(shown["shown"])

            status, checked = self.post("/api/release-notes", {"action": "check"})
            self.assertEqual(status, 200)
            self.assertEqual(checked["status"], "available")

            service.claim.assert_called_once_with()
            service.shown.assert_called_once_with("a" * 64)
            service.check.assert_called_once_with()

            for raw in (b'{"action":"claim","action":"check"}',
                        b'{"action":"claim","extra":true}'):
                status, _ = self.post("/api/release-notes", raw)
                self.assertEqual(status, 400)
            status, _ = self.post("/api/release-notes", {"action": "shown", "claim": "bad"})
            self.assertEqual(status, 400)
            service.check.assert_called_once_with()

        status, _ = self.post("/api/release-notes", {"action": "claim"},
                              origin="https://example.invalid")
        self.assertEqual(status, 403)

        with patch.object(D.Handler, "_loopback_client", return_value=False):
            status, _ = self.post("/api/release-notes", {"action": "claim"})
        self.assertEqual(status, 403)

    def test_release_notes_body_is_bounded(self):
        oversized = b'{"action":"claim","extra":"' + b"x" * 16370 + b'"}'
        status, payload = self.post("/api/release-notes", oversized)
        self.assertEqual(status, 413)
        self.assertIn("16 KiB", payload["error"])

    def test_update_check_attaches_github_result_without_losing_git_result(self):
        D.VERSION = {"git": True, "describe": "v2.0.0", "commit": "abc"}
        git_result = {"upstream": "origin/main", "behind": 0, "ahead": 0,
                      "changes": [], "current": D.VERSION}
        service = Mock()
        service.check.side_effect = RuntimeError("mock notes API failure")
        service.status.return_value = {"current_version": "2.0.0", "pending": False,
                                       "release": None, "latest": None, "update_available": False}
        with patch.object(D, "update_action", return_value=git_result) as update, \
                patch.object(D.RN, "ReleaseNotes", return_value=service):
            status, payload = self.post("/api/update", {"action": "check"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["upstream"], "origin/main")
        self.assertEqual(payload["behind"], 0)
        self.assertEqual(payload["release_notes"]["status"], "unavailable")
        self.assertIn("GitHub", payload["release_notes"]["error"])
        update.assert_called_once_with("check")

    def test_packaged_update_check_is_manual_download_and_apply_is_refused(self):
        D.VERSION = {"git": False, "describe": "2.0.0"}
        notes = {"status": "available", "current_version": "2.0.0", "release": None,
                 "latest": {"tag": "v2.1.0"}, "update_available": True}
        with patch.object(D.RN, "ReleaseNotes") as factory:
            factory.return_value.check.return_value = notes
            status, payload = self.post("/api/update", {"action": "check"})
            self.assertEqual(status, 200)
            self.assertEqual(payload["current"], D.VERSION)
            self.assertEqual(payload["behind"], 0)
            self.assertEqual(payload["ahead"], 0)
            self.assertEqual(payload["changes"], [])
            self.assertTrue(payload["download_only"])
            self.assertEqual(payload["release_notes"], notes)

            status, payload = self.post("/api/update", {"action": "apply"})
        self.assertEqual(status, 400)
        self.assertIn("github.com/uttamdeb/agent-telemetry/releases/latest", payload["error"])

    def test_unknown_update_action_is_rejected_before_any_update_or_network_check(self):
        with patch.object(D, "update_action") as update, \
                patch.object(D.RN, "ReleaseNotes") as release_notes:
            status, payload = self.post("/api/update", {"action": "fetch"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "unknown action")
        update.assert_not_called()
        release_notes.assert_not_called()


if __name__ == "__main__":
    unittest.main()
