"""Serve Settings/MCP UI against invented data and isolated temporary state.

Run: python tests/preview_mcp.py --port 8890

This fixture never scans source logs, reads user settings, starts a native client,
contacts an updater/provider, or launches an MCP server. Access preferences live in
a temporary native-support directory that is removed when the preview stops.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import native as N
import parser as P


def duration_seconds(value):
    try:
        duration = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError("duration must be a whole number of seconds") from None
    if not 1 <= duration <= 3600:
        raise argparse.ArgumentTypeError("duration must be between 1 and 3600 seconds")
    return duration


class PreviewLocalServers:
    """Do not enumerate or control host processes from the preview."""
    def __init__(self, *_args, **_kwargs):
        pass

    def snapshot(self):
        return {"supported": False, "servers": [], "profiles": []}

    def stop(self, *_args, **_kwargs):
        return {"ok": False, "error": "Unavailable in mock preview."}

    def configure(self, *_args, **_kwargs):
        return {"ok": False, "error": "Unavailable in mock preview."}

    def set_pinned(self, *_args, **_kwargs):
        return {"ok": False, "error": "Unavailable in mock preview."}

    def start(self, *_args, **_kwargs):
        return {"ok": False, "error": "Unavailable in mock preview."}

    def restart(self, *_args, **_kwargs):
        return {"ok": False, "error": "Unavailable in mock preview."}


class PreviewHandler(D.Handler):
    """Add a same-origin stop hook only to this mock preview server."""
    def do_POST(self):
        if self.path.split("?", 1)[0] != "/api/preview/stop":
            return super().do_POST()
        if not self._loopback_client() or not self._csrf_ok():
            self._send(403, json.dumps({"error": "preview stop requires a same-origin loopback request"}))
            return
        self._send(200, json.dumps({"ok": True}))
        threading.Thread(target=self.server.shutdown, name="mcp-preview-stop", daemon=True).start()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8890, help="loopback port; use 0 to choose a free port")
    cli.add_argument("--duration", type=duration_seconds, default=None,
                     help="stop the preview automatically after 1–3600 seconds (default: manual stop)")
    args = cli.parse_args()

    with tempfile.TemporaryDirectory(prefix="agenttelemetry-mcp-preview-") as directory, ExitStack() as stack:
        root = Path(directory)
        ledger = root / "ledger"
        support = root / "native-support"
        ledger.mkdir()
        support.mkdir()
        now = time.time()
        today = datetime.now().astimezone().date().isoformat()
        devices = {"this": {"id": "preview-device", "name": "MCP Preview", "os": "Fixture",
                            "platform": "fixture", "sharing": False, "addresses": [], "pulled_by": [], "port": 0},
                   "peers": []}
        payload = {
            "generated_at": now,
            "meta": {"files": 0, "last_refresh": now, "last_duration": 0, "building": False,
                     "parse_errors": 0},
            "cache_error": None,
            "device": {"id": "preview-device", "name": "MCP Preview", "os": "Fixture", "host": "fixture"},
            "devices": [{"id": "preview-device", "name": "MCP Preview", "os": "Fixture"}],
            "version": {"git": False, "describe": "Mock MCP preview"},
            "home": "~",
            "openclaw_undecoded": 0,
            "mcp_servers": {},
            "prices": {},
            "codex_effort": None,
            "skills": [], "activity": [], "mcp_inventory": {"servers": {}, "loaded": [], "calls": []},
            "reads": [], "context_files": [], "installed": {"items": [], "used": []}, "ctx": [],
            "records": [], "tools": [], "projects": [], "hourly": [], "sessions": [], "sessions_total": 0,
            "model_vendor": {}, "ai_lines": [], "pricing": {}, "pricing_history": {},
            "pricing_long_context": {},
            "pricing_note": "Preview data is invented and contains no local usage.",
        }
        summary = {"date": today, "tokens": 0, "cost": 0, "messages": 0, "active_seconds": 0,
                   "building": False, "source": "fixture"}
        settings = {"claude_settings_path": "Temporary mock settings", "claude_settings_exists": False,
                    "claude_cleanup_days": None, "claude_cleanup_default": 30,
                    "cache_path": "Temporary empty mock ledger", "cache_bytes": 0, "cache_files": 0}
        diagnostic = {"sources": [], "ledger": {"status": "Available", "issue": False},
                      "generated_at": now}
        storage = {"files": [], "files_total": 0, "bytes": 0, "extras": [], "growth": [], "reclaimable": {}}

        # Keep every mutable endpoint inside the fixture. The handlers still serve
        # the real Settings UI and real /api/mcp consent route on an ephemeral origin.
        for item in [
            patch.object(D, "DATA_DIR", str(ledger)),
            patch.object(D, "CACHE_PATH", str(ledger / ".usage_cache.json")),
            patch.object(D, "PEERS_PATH", str(ledger / ".peers.json")),
            patch.object(D, "PEER_DIR", str(ledger / ".peers")),
            patch.object(D, "_state", {"files": {}, "version": D.CACHE_VERSION}),
            patch.object(D, "_meta", {"last_refresh": now, "last_duration": 0, "files": 0, "building": False}),
            patch.object(D, "_cache_error", {"v": None}),
            patch.object(D, "DEVICE", {"name": "MCP Preview", "os": "Fixture", "host": "fixture"}),
            patch.object(D, "VERSION", {"git": False, "describe": "Mock MCP preview"}),
            patch.object(D, "client_snapshot", return_value=(json.dumps(payload), json.dumps(summary))),
            patch.object(D, "build_payload", return_value=payload),
            patch.object(D, "build_settings", return_value=settings),
            patch.object(D, "build_diagnostics", return_value=diagnostic),
            patch.object(D, "build_account_payload", return_value={"accounts": [], "claude_capture": None}),
            patch.object(D, "build_storage", return_value=storage),
            patch.object(D, "backup_status", return_value={"available": False, "backups": []}),
            patch.object(D, "backup_action", return_value={"ok": True, "preview": "Mock backup action"}),
            patch.object(D, "devices_status", return_value=devices),
            patch.object(D, "devices_action", return_value={"ok": True, "status": devices}),
            patch.object(D, "update_action", return_value={"ok": True, "git": False, "behind": 0, "changes": []}),
            patch.object(D, "refresh", return_value=None),
            patch.object(D, "cache_action", return_value={"action": "preview", "dropped": 0}),
            patch.object(D, "save_claude_cleanup_days", return_value=settings),
            patch.object(D, "build_session_timeline", return_value={"available": False, "events": [], "truncated": False}),
            patch.object(D, "LocalServerInventory", PreviewLocalServers),
            patch.object(D, "set_session_name", return_value={"ok": False, "error": "Unavailable in mock preview."}),
            patch.object(D.A, "refresh_account", return_value=None),
            patch.object(N, "support_dir", return_value=support),
            patch.object(N, "status", return_value={"supported": False, "installed": False, "running": False,
                                                     "requested": False, "label": "Native app preview unavailable"}),
            patch.object(N, "action", return_value={"ok": True, "supported": False}),
            patch.object(N, "refresh_interval", return_value=15),
            patch.object(N, "set_refresh_interval", return_value={"seconds": 15}),
            patch.object(P, "discover", return_value=[]),
            patch.object(P, "HOME", "~"),
            patch.object(D, "_peer_items", return_value=[]),
            patch.object(D, "_devices_meta", return_value=payload["devices"]),
            patch.object(D, "_mcp_servers", return_value={}),
            patch.object(D, "_codex_config", return_value={"effort": None}),
            patch.object(D, "_context_files", return_value=[]),
            patch.object(D, "_installed", return_value=[]),
            patch.object(D, "_peer_cfg", return_value={"share": {"on": False}, "peers": []}),
        ]:
            stack.enter_context(item)

        bind = {"host": "127.0.0.1", "port": args.port}
        stack.enter_context(patch.object(D, "BIND", bind))
        server = D.Server(("127.0.0.1", args.port), PreviewHandler)
        bind.update(host="127.0.0.1", port=server.server_port)
        print(f"Mock MCP Settings preview: http://127.0.0.1:{server.server_port}/", flush=True)
        end_note = f"It will stop in {args.duration} seconds." if args.duration is not None else "Stop with Ctrl+C or POST /api/preview/stop."
        print(f"Invented empty data only; no MCP server is launched. {end_note}", flush=True)
        timer = None
        if args.duration is not None:
            timer = threading.Timer(args.duration, server.shutdown)
            timer.name = "mcp-preview-timeout"
            timer.daemon = True
            timer.start()
        try:
            server.serve_forever(poll_interval=0.25)
        except KeyboardInterrupt:
            pass
        finally:
            if timer is not None:
                timer.cancel()
            server.server_close()


if __name__ == "__main__":
    main()
