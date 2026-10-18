"""HTTP route tests for local MCP settings and authenticated query dispatch."""
from contextlib import ExitStack
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dashboard
import mcp_access
import native


class MCPRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="telemetry-mcp-routes-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / "ledger"
        self.ledger.mkdir()
        self.support = self.root / "AgentTelemetryNative"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(native, "support_dir", return_value=self.support))
        self.stack.enter_context(patch.object(dashboard, "DATA_DIR", str(self.ledger)))
        self.stack.enter_context(patch.object(dashboard, "BIND", {"host": "127.0.0.1", "port": 0}))
        self.stack.enter_context(patch.object(dashboard.Handler, "_mcp_builders", return_value={
            "health": lambda: {"status": "available", "generation": 7, "sources": []},
            "is_building": lambda: False,
        }))
        self.server = dashboard.Server(("127.0.0.1", 0), dashboard.Handler)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self._stop_server)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"

    def _stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            data = None if body is None else json.dumps(body).encode("utf-8")
            request_headers = dict(headers or {})
            if data is not None:
                request_headers.setdefault("Content-Type", "application/json")
                request_headers.setdefault("Origin", self.url)
            connection.request(method, path, body=data, headers=request_headers)
            response = connection.getresponse()
            content = response.read()
            return response.status, json.loads(content) if content else None
        finally:
            connection.close()

    def test_settings_route_returns_local_config_and_no_key(self):
        status, before = self.request("GET", "/api/mcp")
        self.assertEqual(status, 200)
        self.assertFalse(before["enabled"])
        config = before["client_config"]["mcpServers"]["agent-telemetry"]
        self.assertTrue(Path(config["command"]).is_absolute())
        self.assertTrue(Path(config["args"][0]).is_absolute())
        self.assertEqual(config["args"][-2:], ["--backend", self.url])
        self.assertNotIn("token", json.dumps(before).lower())
        self.assertNotIn("bearer", json.dumps(before).lower())

        status, enabled = self.request("POST", "/api/mcp", {
            "enabled": True, "confirmed": True,
        })
        self.assertEqual(status, 200)
        self.assertTrue(enabled["enabled"])
        self.assertNotIn("token", json.dumps(enabled).lower())
        self.assertNotIn("bearer", json.dumps(enabled).lower())

    def test_query_requires_bearer_same_origin_and_returns_sanitized_data(self):
        self.request("POST", "/api/mcp", {"enabled": True, "confirmed": True})
        token = mcp_access.credentials(self.url)
        status, result = self.request("POST", "/api/mcp/query", {
            "tool": "get_data_health", "arguments": {},
        }, {"Authorization": "Bearer " + token})
        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "available")
        self.assertIn("privacy_warning", result)
        self.assertNotIn(token, json.dumps(result))

        status, error = self.request("POST", "/api/mcp/query", {
            "tool": "get_data_health", "arguments": {},
        }, {"Authorization": "Bearer " + token, "Origin": "http://evil.example"})
        self.assertEqual(status, 403)
        self.assertEqual(error["error"]["code"], "forbidden")

    def test_query_rejects_non_loopback_client_even_with_valid_bearer(self):
        self.request("POST", "/api/mcp", {"enabled": True, "confirmed": True})
        token = mcp_access.credentials(self.url)
        with patch.object(dashboard.Handler, "_loopback_client", return_value=False):
            status, error = self.request("POST", "/api/mcp/query", {
                "tool": "get_data_health", "arguments": {},
            }, {"Authorization": "Bearer " + token})
        self.assertEqual(status, 403)
        self.assertEqual(error["error"]["code"], "forbidden")

    def test_nested_settings_json_is_rejected_without_stopping_server(self):
        body = b"{\"x\":" * 200 + b"0" + b"}" * 200
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request("POST", "/api/mcp", body=body, headers={
                "Content-Type": "application/json", "Origin": self.url,
            })
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()
        status, result = self.request("GET", "/api/mcp")
        self.assertEqual(status, 200)
        self.assertFalse(result["enabled"])


if __name__ == "__main__":
    unittest.main()
