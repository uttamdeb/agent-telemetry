"""Run a real MCP SDK stdio client against an isolated fake dashboard."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading


ROOT = Path(__file__).resolve().parents[1]
SDK_ROOT = os.environ.get("MCP_SDK_ROOT", "")
NODE = os.environ.get("MCP_NODE") or shutil.which("node")


def main():
    if not NODE or not SDK_ROOT or not (Path(SDK_ROOT) / "package.json").is_file():
        print("SKIP: set MCP_NODE/PATH and MCP_SDK_ROOT to run the pinned SDK smoke test")
        return 0

    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def do_POST(self):
            if self.path != "/api/mcp/query":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            authorization = self.headers.get("Authorization", "")
            requests.append((authorization.startswith("Bearer "), body))
            if not authorization.startswith("Bearer "):
                payload = {"error": {"code": "unauthorized", "message": "Denied."}}
                self.send_response(401)
            else:
                payload = {
                    "rows": [{"tool": "Codex", "tokens": 42}],
                    "totals": {"tokens": 42},
                    "range": {"from": "2026-10-18", "to": "2026-10-18"},
                    "privacy_warning": "mock dashboard privacy notice",
                }
                self.send_response(200)
            encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    with tempfile.TemporaryDirectory(prefix="agenttelemetry-mcp-sdk-") as temporary:
        temp = Path(temporary)
        ledger = temp / "ledger"
        ledger.mkdir()
        env = dict(os.environ)
        if sys.platform == "win32":
            env["LOCALAPPDATA"] = str(temp)
            native_root = temp / "AgentTelemetryNative"
        elif sys.platform == "darwin":
            env["HOME"] = str(temp)
            native_root = temp / "Library" / "Application Support" / "AgentTelemetryNative"
        else:
            env["XDG_STATE_HOME"] = str(temp)
            native_root = temp / "AgentTelemetryNative"
        native_root.mkdir(parents=True)

        # Import after creating an isolated app-support root; no real user prefs or ledger
        # are read or written by this smoke test.
        sys.path.insert(0, str(ROOT))
        import mcp_access
        import native

        native.support_dir = lambda: native_root
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            backend = f"http://127.0.0.1:{server.server_address[1]}"
            mcp_access.configure(backend, ledger, {
                "enabled": True,
                "confirmed": True,
            })
            script = ROOT / "tests" / "test_mcp_sdk.mjs"
            command = [
                NODE, str(script), SDK_ROOT, sys.executable,
                str(ROOT / "mcp_server.py"), backend,
            ]
            result = subprocess.run(
                command, cwd=ROOT, env=env, text=True,
                capture_output=True, timeout=45, check=False,
            )
            if result.returncode:
                sys.stderr.write(result.stderr)
                sys.stdout.write(result.stdout)
                return result.returncode
            if len(requests) != 1 or not requests[0][0]:
                raise AssertionError("SDK client did not make exactly one authenticated usage query")
            if requests[0][1] != {
                "tool": "get_usage",
                "arguments": {"period": "today", "group_by": ["tool"]},
            }:
                raise AssertionError("SDK query did not preserve the expected narrow arguments")
            if b"NATIVE_THREAD_SENTINEL" in result.stdout.encode() or b"PROMPT_SENTINEL" in result.stdout.encode():
                raise AssertionError("SDK output included a native-data sentinel")
            print("MCP SDK 1.30.1 stdio smoke test passed")
            return 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    raise SystemExit(main())
