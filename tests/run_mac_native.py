"""Compile actual macOS native sources against an ephemeral mock dashboard."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

if sys.platform != "darwin":
    raise SystemExit("Native macOS fixtures require macOS")
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "macos/AgentTelemetryMac/Sources/AgentTelemetryMac"
state = {"failed": False, "delay": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/fail": state["failed"] = True
        if self.path == "/recover": state["failed"] = False
        if self.path == "/delay": state["delay"] = True
        if self.path.startswith("/api/"):
            if self.path == "/api/health" and state["delay"]:
                time.sleep(0.35)
            self.send_response(503 if state["failed"] else 200)
            body = json.dumps({"service": "agent-telemetry", "version": "fixture", "ready": True, "building": False}
                if self.path == "/api/health" else {"date": "2026-10-10", "tokens": 100, "spend": 1.25}).encode()
        else:
            self.send_response(200)
            body = b"<!doctype html><html><head><title>Fixture</title></head><body>Fixture</body></html>"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
try:
    with tempfile.TemporaryDirectory(prefix="telemetry-mac-native-") as temp:
        root = Path(temp)
        backend = root / "BackendController.swift"
        # This is the only production source substitution; no user port/data used.
        backend.write_text((SOURCE / backend.name).read_text().replace(
            "http://127.0.0.1:7878", "http://127.0.0.1:" + str(server.server_port)))
        sdk = subprocess.check_output(["xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
        names = ("AppSettings.swift", "CacheMigration.swift", "DashboardWebView.swift", "StatusMenu.swift", "MenuBarPopoverController.swift")
        command = ["swiftc", "-sdk", sdk, "-parse-as-library", str(backend)]
        command += [str(SOURCE / name) for name in names]
        command += [str(ROOT / "tests/MacNativeRegression.swift"), "-o", str(root / "native-tests")]
        subprocess.run(command, check=True)
        subprocess.run([str(root / "native-tests"), str(server.server_port)], check=True, timeout=30)
finally:
    server.shutdown()
