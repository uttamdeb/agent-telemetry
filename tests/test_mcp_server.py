"""MCP stdio and HTTP tests use fake results, local sockets, and temporary native state."""
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mcp_access
import mcp_server
import native


def _meta(version="2026-07-28"):
    return {"_meta": {
        "io.modelcontextprotocol/protocolVersion": version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "fixture-client", "version": "1"},
    }}


def _message(method, request_id=None, params=None, *, include_id=True):
    value = {"jsonrpc": "2.0", "method": method}
    if include_id:
        value["id"] = request_id
    if params is not None:
        value["params"] = params
    return (json.dumps(value, separators=(",", ":")) + "\n").encode("utf-8")


def _decode_lines(raw):
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]


class DelayedEOF(io.BytesIO):
    """Keep simulated stdio open briefly so accepted requests can reply."""
    def __init__(self, data, delay=0.1):
        super().__init__(data)
        self.delay = delay

    def readline(self, size=-1):
        line = super().readline(size)
        if not line:
            time.sleep(self.delay)
        return line


class WaitForRepliesEOF(io.BytesIO):
    """Hold simulated stdin open until the test observes the expected replies."""
    def __init__(self, data, replies_ready):
        super().__init__(data)
        self.replies_ready = replies_ready

    def readline(self, size=-1):
        line = super().readline(size)
        if not line:
            self.replies_ready.wait(timeout=5)
        return line


class ReplyCountingOutput(io.BytesIO):
    def __init__(self, replies_ready, expected):
        super().__init__()
        self.replies_ready = replies_ready
        self.expected = expected
        self._count_lock = threading.Lock()
        self._line_count = 0

    def write(self, value):
        with self._count_lock:
            written = super().write(value)
            self._line_count += bytes(value).count(b"\n")
            if self._line_count >= self.expected:
                self.replies_ready.set()
            return written


def _stop_http_server(server):
    server.shutdown()
    server.server_close()


class MCPServerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-mcp-server-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.support = self.root / "AgentTelemetryNative"
        self.data = self.root / "ledger"
        self.url = "http://127.0.0.1:7878"
        item = patch.object(native, "support_dir", return_value=self.support)
        item.start()
        self.addCleanup(item.stop)

    def _server(self, backend_call=None):
        return mcp_server.MCPServer(self.url, backend_call=backend_call or (lambda *_: {}), error_stream=io.StringIO())

    def test_modern_discovery_tools_resources_and_prompts_work_without_consent(self):
        calls = []
        server = self._server(lambda *args: calls.append(args) or {})
        modern = _meta()
        messages = [
            _message("server/discover", 1, modern),
            _message("tools/list", 2, modern),
            _message("resources/list", 3, modern),
            _message("resources/read", 4, {**modern, "uri": "telemetry://guide"}),
            _message("prompts/list", 5, modern),
            _message("prompts/get", 6, {**modern, "name": "analyze_usage", "arguments": {"period": "today"}}),
        ]
        output = io.BytesIO()
        server.serve(DelayedEOF(b"".join(messages)), output)
        replies = {reply["id"]: reply["result"] for reply in _decode_lines(output.getvalue())}
        self.assertEqual(set(replies), {1, 2, 3, 4, 5, 6})
        self.assertEqual(replies[1]["supportedVersions"], ["2026-07-28"])
        self.assertEqual(replies[1]["resultType"], "complete")
        self.assertEqual(replies[1]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"], "agent-telemetry")
        self.assertEqual([tool["name"] for tool in replies[2]["tools"]], [
            "get_usage", "compare_usage", "get_insights", "list_sessions", "get_session_timeline",
            "get_data_health", "get_catalog", "get_subscription_limits",
        ])
        self.assertEqual([item["uri"] for item in replies[3]["resources"]], ["telemetry://guide", "telemetry://schema"])
        self.assertIn("cloud-backed agent", replies[4]["contents"][0]["text"].lower())
        self.assertEqual([prompt["name"] for prompt in replies[5]["prompts"]], ["analyze_usage", "optimize_project"])
        self.assertIn("read-only tools", replies[6]["messages"][0]["content"]["text"])
        self.assertFalse(calls)

    def test_disabled_tool_call_is_an_actionable_mcp_tool_error(self):
        calls = []
        server = self._server(lambda *args: calls.append(args) or {})
        params = {**_meta(), "name": "get_usage", "arguments": {}}
        output = io.BytesIO()
        server.serve(DelayedEOF(_message("tools/call", 7, params)), output)
        [reply] = _decode_lines(output.getvalue())
        result = reply["result"]
        self.assertTrue(result["isError"])
        self.assertIn("Enable it in AgentTelemetry Settings", result["content"][0]["text"])
        self.assertIn("com.agenttelemetry/privacy-warning", result["_meta"])
        self.assertFalse(calls)

    def test_enabled_tool_call_uses_bearer_and_returns_rows_plus_privacy_warning(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        seen = []

        def backend(url, token, name, arguments):
            seen.append((url, token, name, arguments))
            return {"rows": [{"tool": "Codex", "tokens": 100}], "total_rows": 1, "returned_rows": 1}

        params = {**_meta(), "name": "get_usage", "arguments": {"group_by": ["tool"], "limit": 10}}
        output = io.BytesIO()
        self._server(backend).serve(DelayedEOF(_message("tools/call", 8, params)), output)
        [reply] = _decode_lines(output.getvalue())
        result = reply["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(seen[0][0], self.url)
        self.assertEqual(seen[0][1], mcp_access.credentials(self.url))
        self.assertEqual(seen[0][2:], ("get_usage", {"group_by": ["tool"], "limit": 10}))
        self.assertEqual(result["structuredContent"]["rows"][0]["tokens"], 100)
        self.assertIn("privacy_warning", result["structuredContent"])
        self.assertIn('"tokens":100', result["content"][0]["text"])
        self.assertEqual(result["resultType"], "complete")
        self.assertIn("com.agenttelemetry/privacy-warning", result["_meta"])

    def test_invalid_args_unknown_methods_and_invalid_ids_are_jsonrpc_errors(self):
        calls = []
        server = self._server(lambda *args: calls.append(args) or {})
        modern = _meta()
        messages = [
            _message("tools/call", "bad-args", {**modern, "name": "get_usage", "arguments": {"include_names": True}}),
            _message("tools/call", "unknown", {**modern, "name": "unknown", "arguments": {}}),
            _message("what/is/this", 12, modern),
            _message("ping", True, modern),
        ]
        output = io.BytesIO()
        server.serve(io.BytesIO(b"".join(messages)), output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual(len(replies), 4)
        self.assertEqual(replies[0]["error"]["code"], -32602)
        self.assertEqual(replies[1]["error"]["code"], -32602)
        self.assertEqual(replies[2]["error"]["code"], -32601)
        self.assertEqual(replies[3]["id"], None)
        self.assertEqual(replies[3]["error"]["code"], -32600)
        self.assertFalse(calls)

    def test_legacy_initialize_notifications_and_tool_result_shape(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        messages = [
            _message("initialize", 1, {"protocolVersion": "2025-06-18", "capabilities": {}}),
            _message("notifications/initialized", include_id=False),
            _message("tools/call", 2, {
                "name": "get_usage", "arguments": {}, "_meta": {"progressToken": 12},
            }),
        ]
        output = io.BytesIO()
        self._server(lambda *_: {"totals": {"tokens": 1}}).serve(DelayedEOF(b"".join(messages)), output)
        replies = {reply["id"]: reply["result"] for reply in _decode_lines(output.getvalue())}
        self.assertEqual(replies[1]["protocolVersion"], "2025-06-18")
        self.assertNotIn("resultType", replies[1])
        self.assertNotIn("resultType", replies[2])
        self.assertFalse(replies[2]["isError"])

    def test_unknown_legacy_version_is_negotiated_to_latest_supported(self):
        messages = [
            _message("initialize", 1, {"protocolVersion": "2030-01-01", "capabilities": {}}),
            _message("notifications/initialized", include_id=False),
            _message("tools/list", 2, {"_meta": {"progressToken": 1}}),
        ]
        output = io.BytesIO()
        self._server().serve(DelayedEOF(b"".join(messages)), output)
        replies = {reply["id"]: reply for reply in _decode_lines(output.getvalue())}
        self.assertEqual(replies[1]["result"]["protocolVersion"], "2025-11-25")
        self.assertIn("result", replies[2])

    def test_notifications_are_one_way_and_cancelled_calls_are_suppressed(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        calls = []

        def slow_backend(*args):
            calls.append(args)
            time.sleep(0.05)
            return {"rows": [{"sentinel": "cancelled result"}]}

        modern = _meta()
        messages = [
            _message("notifications/initialized", include_id=False),
            _message("ping", 1, modern),
            _message("tools/call", 4, {**modern, "name": "get_usage", "arguments": {}}),
            _message("notifications/cancelled", include_id=False, params={"requestId": 4}),
        ]
        output = io.BytesIO()
        self._server(slow_backend).serve(DelayedEOF(b"".join(messages)), output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual([reply["id"] for reply in replies], [1])
        self.assertNotIn("cancelled result", output.getvalue().decode("utf-8"))

    def test_arbitrary_valid_notifications_are_one_way_and_invalid_notification_is_rejected(self):
        messages = [
            _message("custom/event", include_id=False),
            _message("notifications/vendor-event", include_id=False),
            json.dumps({"jsonrpc": "1.0", "method": "custom/event"}).encode("utf-8") + b"\n",
            json.dumps({"jsonrpc": "2.0", "method": ""}).encode("utf-8") + b"\n",
        ]
        output = io.BytesIO()
        self._server().serve(DelayedEOF(b"".join(messages)), output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual(len(replies), 2)
        self.assertTrue(all(reply["id"] is None for reply in replies))
        self.assertTrue(all(reply["error"]["code"] == -32600 for reply in replies))

    def test_duplicate_inflight_ids_are_rejected_and_completed_ids_can_be_reused(self):
        calls = []

        def slow_backend(*args):
            calls.append(args)
            time.sleep(0.08)
            return {"rows": []}

        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        modern = _meta()
        call = _message("tools/call", 9, {**modern, "name": "get_usage", "arguments": {}})
        stream = DelayedEOF(call + call, delay=0.2)
        output = io.BytesIO()
        self._server(slow_backend).serve(stream, output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual(len(replies), 2)
        self.assertEqual(sum("error" in reply for reply in replies), 1)
        self.assertEqual(replies[0]["id"], 9)

        class ImmediateExecutor:
            def __init__(self, *args, **kwargs):
                pass

            def submit(self, function, *args):
                from concurrent.futures import Future

                future = Future()
                future.set_running_or_notify_cancel()
                try:
                    future.set_result(function(*args))
                except BaseException as exc:
                    future.set_exception(exc)
                return future

            def shutdown(self, wait=True, cancel_futures=False):
                pass

        ping = _message("ping", "reused", modern)
        output = io.BytesIO()
        with patch.object(mcp_server, "ThreadPoolExecutor", ImmediateExecutor):
            self._server().serve(DelayedEOF(ping * 20), output)
        self.assertEqual(len(_decode_lines(output.getvalue())), 20)

    def test_deeply_nested_json_is_reported_and_server_recovers(self):
        nested = b'{"jsonrpc":"2.0","method":"ping","params":' + b"[" * 100 + b"0" + b"]" * 100 + b"}\n"
        nested += _message("ping", 7, _meta())
        output = io.BytesIO()
        self._server().serve(DelayedEOF(nested), output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual([reply["error"]["code"] for reply in replies[:1]], [-32700])
        self.assertEqual(replies[-1]["id"], 7)

    def test_pending_work_is_bounded_and_shutdown_discards_inflight_results(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        release = threading.Event()

        class ReleaseAtEOF(io.BytesIO):
            def readline(self, size=-1):
                line = super().readline(size)
                if not line:
                    threading.Timer(0.05, release.set).start()
                return line

        def blocked_backend(*_):
            release.wait(2)
            return {"rows": [{"sentinel": "must be discarded on EOF"}]}

        modern = _meta()
        requests = b"".join(_message("tools/call", idx, {
            **modern, "name": "get_usage", "arguments": {},
        }) for idx in range(5))
        output = io.BytesIO()
        with patch.object(mcp_server, "MAX_WORKERS", 1), patch.object(mcp_server, "MAX_PENDING_REQUESTS", 2):
            self._server(blocked_backend).serve(ReleaseAtEOF(requests), output)
        replies = _decode_lines(output.getvalue())
        self.assertEqual(len(replies), 3)
        self.assertTrue(all(reply["error"]["code"] == -32603 for reply in replies))
        self.assertNotIn("must be discarded", output.getvalue().decode("utf-8"))

    def test_bad_and_oversized_frames_recover_without_echoing_input(self):
        modern_ping = _message("ping", 10, _meta())
        oversized = b"{" + b"x" * mcp_server.MAX_MESSAGE_BYTES + b"}\n"
        replies_ready = threading.Event()
        input_stream = WaitForRepliesEOF(b"not-json\n" + oversized + modern_ping, replies_ready)
        output = ReplyCountingOutput(replies_ready, expected=3)
        errors = io.StringIO()
        server = mcp_server.MCPServer(self.url, error_stream=errors)
        serving = threading.Thread(target=server.serve, args=(input_stream, output), daemon=True)
        serving.start()
        self.assertTrue(replies_ready.wait(timeout=5), "server did not emit all expected responses")
        serving.join(timeout=5)
        self.assertFalse(serving.is_alive(), "server did not exit after the fixture released EOF")
        replies = _decode_lines(output.getvalue())
        self.assertEqual(len(replies), 3)
        self.assertEqual(replies[0]["error"]["code"], -32700)
        self.assertEqual(replies[1]["error"]["code"], -32600)
        self.assertEqual(replies[2]["id"], 10)
        self.assertNotIn("xxxx", output.getvalue().decode("utf-8"))
        self.assertNotIn("not-json", errors.getvalue())


class MCPBackendHTTPTests(unittest.TestCase):
    def test_redirect_does_not_forward_bearer_to_another_endpoint(self):
        target_hits = []

        class TargetHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                target_hits.append(self.headers.get("Authorization"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b"{}")
            def log_message(self, *_):
                return

        target = ThreadingHTTPServer(("127.0.0.1", 0), TargetHandler)
        target_thread = threading.Thread(target=target.serve_forever, daemon=True)
        target_thread.start()
        self.addCleanup(_stop_http_server, target)

        class RedirectHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
                self.end_headers()
            def log_message(self, *_):
                return

        redirect = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
        redirect_thread = threading.Thread(target=redirect.serve_forever, daemon=True)
        redirect_thread.start()
        self.addCleanup(_stop_http_server, redirect)
        url = f"http://127.0.0.1:{redirect.server_port}"
        with self.assertRaises(mcp_server.BackendCallError) as caught:
            mcp_server.backend_query(url, "secret-bearer", "get_usage", {})
        self.assertIn("redirect", str(caught.exception))
        self.assertEqual(target_hits, [])

    def test_http_error_body_is_discarded(self):
        class ErrorHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(403)
                self.send_header("Content-Length", "25")
                self.end_headers()
                self.wfile.write(b"credential-secret-sentinel")
            def log_message(self, *_):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), ErrorHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(_stop_http_server, server)
        with self.assertRaises(mcp_server.BackendCallError) as caught:
            mcp_server.backend_query(f"http://127.0.0.1:{server.server_port}", "secret-bearer", "get_usage", {})
        self.assertNotIn("credential-secret-sentinel", str(caught.exception))
        self.assertNotIn("secret-bearer", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

