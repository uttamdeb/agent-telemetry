"""Compile actual macOS native sources against an ephemeral mock dashboard."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

if sys.platform != "darwin":
    raise SystemExit("Native macOS fixtures require macOS")
arguments = argparse.ArgumentParser()
arguments.add_argument("--screenshots", type=Path)
options = arguments.parse_args()
if options.screenshots:
    options.screenshots.mkdir(parents=True, exist_ok=True)
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "macos/AgentTelemetryMac/Sources/AgentTelemetryMac"
state = {"failed": False, "delay": False, "seconds": 15, "revision": 0, "tokens": 100,
         "localServerSupported": True, "localServerGets": 0, "localServerActions": [],
         "nativeActivitySupported": True, "nativeActivityFailed": False,
         "nativeActivityDelay": False, "nativeActivityEmpty": False, "nativeActivityGets": 0,
         "nativeActivityRedirect": False, "nativeActivityOversized": False,
         "nativeActivityRedirectFollowed": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        if self.path == "/fail": state["failed"] = True
        if self.path == "/recover": state["failed"] = False
        if self.path == "/delay": state["delay"] = True
        if self.path == "/advance": state["tokens"] = 200; state["revision"] += 1
        if self.path == "/raw-advance": state["tokens"] = 250
        if self.path == "/disable-local-server-support": state["localServerSupported"] = False
        if self.path == "/enable-local-server-support": state["localServerSupported"] = True
        if self.path == "/fail-native-activity": state["nativeActivityFailed"] = True
        if self.path == "/recover-native-activity": state["nativeActivityFailed"] = False
        if self.path == "/disable-native-activity-support": state["nativeActivitySupported"] = False
        if self.path == "/enable-native-activity-support": state["nativeActivitySupported"] = True
        if self.path == "/delay-native-activity": state["nativeActivityDelay"] = True
        if self.path == "/restore-native-activity-delay": state["nativeActivityDelay"] = False
        if self.path == "/empty-native-activity": state["nativeActivityEmpty"] = True
        if self.path == "/restore-native-activity": state["nativeActivityEmpty"] = False
        if self.path == "/redirect-native-activity": state["nativeActivityRedirect"] = True
        if self.path == "/restore-native-activity-redirect": state["nativeActivityRedirect"] = False
        if self.path == "/oversize-native-activity": state["nativeActivityOversized"] = True
        if self.path == "/restore-native-activity-oversize": state["nativeActivityOversized"] = False
        if self.path == "/fixture/local-server-actions":
            body = json.dumps(state["localServerActions"]).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/fixture/local-server-count":
            body = json.dumps({"count": state["localServerGets"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/fixture/native-activity-count":
            body = json.dumps({"count": state["nativeActivityGets"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/fixture/native-activity-redirect-count":
            body = json.dumps({"count": state["nativeActivityRedirectFollowed"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/fixture/redirect-followed":
            state["nativeActivityRedirectFollowed"] += 1
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")
            return
        if self.path == "/api/native-activity":
            state["nativeActivityGets"] += 1
            if state["nativeActivityDelay"]:
                time.sleep(0.35)
            if state["nativeActivityRedirect"]:
                self.send_response(302)
                self.send_header("Location", "/fixture/redirect-followed")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if state["nativeActivityOversized"]:
                body = b" " * (64 * 1024 + 1)
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            now = datetime.now(timezone.utc)
            stamp = lambda seconds: (now - timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
            body = json.dumps({
                "observed_at": stamp(0),
                "window_seconds": 900,
                "sessions": [] if state["nativeActivityEmpty"] else [
                    {"key": "a" * 64, "source": "codex", "tool": "Codex", "provider": "OpenAI",
                     "title": "Fixture session with a long title that should truncate gracefully in the compact native popover", "tokens": 12845,
                     "last_activity_at": stamp(30), "subagent": False},
                    {"key": "b" * 64, "source": "claude", "tool": "Claude Code", "provider": "Anthropic",
                     "title": "Reviewing the fixture", "tokens": 5820,
                     "last_activity_at": stamp(95), "subagent": True},
                    {"key": "c" * 64, "source": "opencode", "tool": "OpenCode", "provider": None,
                     "title": "An older local fixture session", "tokens": None,
                     "last_activity_at": stamp(420), "subagent": False},
                ],
                "total": 0 if state["nativeActivityEmpty"] else 5,
            }).encode()
            if not state["nativeActivitySupported"]:
                self.send_response(404)
            else:
                self.send_response(503 if state["nativeActivityFailed"] else 200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/local-servers":
            state["localServerGets"] += 1
            body = json.dumps({
                "supported": state["localServerSupported"],
                "message": None if state["localServerSupported"] else "Local server inventory is unsupported in this fixture.",
                "updatedAt": "2026-10-10T12:00:00Z",
                "servers": [{
                    "id": "server-fixture", "pid": 123, "process": "vite", "cwd": "/tmp/Fixture",
                    "project": "A Very Long Fixture Project Name for Native Popover Truncation", "host": "127.0.0.1", "port": 3000,
                    "startedAt": "2026-10-10T11:00:00Z", "state": "running",
                    "firstSeen": "2026-10-10T11:00:00Z", "lastSeen": "2026-10-10T12:00:00Z",
                    "stoppedAt": None, "profileId": "profile-fixture", "isConfigured": True,
                    "isPinned": True,
                }] if state["localServerSupported"] else [],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/"):
            if self.path == "/api/health" and state["delay"]:
                time.sleep(0.35)
            self.send_response(503 if state["failed"] else 200)
            body = json.dumps({"service": "agent-telemetry", "version": "fixture", "ready": True, "building": False}
                if self.path == "/api/health" else {"seconds": state["seconds"], "revision": str(state["revision"])}
                if self.path == "/api/sync" else {"date": "2026-10-10", "tokens": state["tokens"], "spend": 1.25}).encode()
        else:
            # Keep navigation slower than the old 700 ms baseline delay, so the
            # regression exercises waiting for completion rather than timing luck.
            if "range=today" in self.path:
                time.sleep(1)
            self.send_response(200)
            body = b"<!doctype html><html><head><title>Fixture</title></head><body>Fixture</body></html>"
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path.startswith("/api/local-servers/"):
            state["localServerActions"].append({"path": self.path, "body": json.loads(raw or b"{}")})
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if not state["failed"]:
            if self.path == "/api/sync": state["seconds"] = json.loads(raw)["seconds"]
            state["revision"] += 1
        self.do_GET()


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
        names = (
            "AppSettings.swift",
            "AgentTelemetryAppIcon.swift",
            "CacheMigration.swift",
            "DashboardWebView.swift",
            "LocalServerMonitor.swift",
            "NativeActivityMonitor.swift",
            "StatusMenu.swift",
            "MenuBarPopoverController.swift",
            "NativeControl.swift",
        )
        command = ["swiftc", "-sdk", sdk, "-parse-as-library", str(backend)]
        command += [str(SOURCE / name) for name in names]
        command += [str(ROOT / "tests/MacNativeRegression.swift"), "-o", str(root / "native-tests")]
        subprocess.run(command, check=True)
        run = [str(root / "native-tests"), str(server.server_port),
               str(ROOT / "macos/AgentTelemetryMac/Resources/AppIcon.png")]
        if options.screenshots:
            run.append(str(options.screenshots.resolve()))
        subprocess.run(run, check=True, timeout=60)
finally:
    server.shutdown()
