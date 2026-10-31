"""Serve invented Antigravity data for local visual QA; reads no ledger or logs.

Run: python3 tests/preview_antigravity.py --port 8898
"""
import argparse
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import parser as P
DEVICE, MODEL, PRICED_MODEL = "antigravity-preview", "Preview-only unknown model", "Gemini 3.8 Flash"


def fixture():
    now = datetime.now().astimezone().replace(microsecond=0)
    day, stamp = now.date().isoformat(), now.isoformat()
    parent_id, child_id = "a19a4b25-7e63-4f5c-9d11-2c86a12e2a01", "b28b5c36-8f74-405d-ae22-3d97b23f3b12"
    compact_stamp = day + "T10:08:00Z"
    availability = {"input_output": "recorded", "cache_read": "recorded", "cache_write": "recorded",
                    "reasoning": "recorded", "response_output": "unavailable"}

    def row(identity, title, usage, child=False, ide="Antigravity IDE"):
        vector = [0, usage["in"], usage["out"], usage["cr"], usage["cc"], usage["asst"], usage["user"], usage["tools"],
                  0, usage["active"], usage["req"], usage["cc"], 0, usage["reason"], 0]
        session = {"id": identity[:8], "rename_id": identity, "rename_available": False,
            "rename_reason": "Rename is disabled in this synthetic preview.", "source": "antigravity", "title": title,
            "project": "antigravity-demo", "model": MODEL, "models": [MODEL], "nmodels": 1, "ide": ide,
            "device": DEVICE, "start": stamp, "end": stamp, **usage, "subagent": child,
            "usage_status": "recorded", "usage_availability": dict(availability), "days": {day: vector},
            "model_days": {MODEL: {day: vector}}, "compactions": [],
            "reasoning_effort": {"version": 1, "days": {day+"\t"+MODEL: {"levels": {"high": usage["req"]}, "unrecorded": 0}}}}
        if child:
            session["subagent_parent"] = {"id": parent_id, "title": "Antigravity main session", "source": "antigravity", "device": DEVICE}
        else:
            session.update(subagent_children=1, compactions=[{"date": day, "timestamp": compact_stamp,
                "trigger": "threshold", "before": 2400000, "after": 850000}])
        return session

    usage = {"in": 12345, "out": 678, "cr": 9000, "cc": 120, "reason": 84,
             "asst": 7, "user": 3, "req": 7, "tools": 2, "active": 240, "cost": 0.0}
    child_usage = {"in": 8800, "out": 1100, "cr": 3500, "cc": 300, "reason": 400,
                   "asst": 3, "user": 1, "req": 3, "tools": 1, "active": 120, "cost": 0.0}
    sessions = [row(parent_id, "Antigravity main session", usage),
                row(child_id, "Antigravity subagent review", child_usage, True, "Antigravity App")]
    records, hourly, tools = [], [], []
    for session in sessions:
        record = {k: session.get(k, 0) for k in ("in", "out", "cr", "cc", "reason", "asst", "user", "req", "tools", "active", "cost")}
        record.update(date=day, source="antigravity", model=MODEL, project=session["project"],
                      ide=session["ide"], device=DEVICE)
        records.append(record)
        hourly.append({"date": day, "hour": now.hour, "source": "antigravity", "device": DEVICE,
                       "tokens": record["in"]+record["out"]+record["cr"]+record["cc"], "msgs": record["asst"]})
        tools.append({"date": day, "source": "antigravity", "device": DEVICE,
                      "name": "Read" if not session["subagent"] else "Search", "count": record["tools"]})
    known_price = P.PRICING[PRICED_MODEL]
    known_record = {"date": day, "source": "antigravity", "model": PRICED_MODEL, "project": "antigravity-demo",
        "ide": "Antigravity IDE", "device": DEVICE, "in": 4200, "out": 900, "cr": 1200, "cc": 0,
        "reason": 90, "asst": 4, "req": 4, "user": 2, "tools": 1, "active": 120,
        "cost": (4200*known_price[0]+900*known_price[1]+1200*known_price[4])/1e6}
    records.append(known_record)
    events = [
        {"timestamp": day+"T10:01:00Z", "kind": "session", "label": "Session started"},
        {"timestamp": day+"T10:02:00Z", "kind": "model", "label": "Model selected", "model": MODEL},
        {"timestamp": day+"T10:03:00Z", "kind": "reasoning", "label": "Recorded effort · high", "model": MODEL},
        {"timestamp": day+"T10:04:00Z", "kind": "tool", "label": "Read", "status": "done", "model": MODEL},
        {"timestamp": day+"T10:05:00Z", "kind": "tool", "label": "Search", "status": "error", "model": MODEL},
        {"timestamp": day+"T10:06:00Z", "kind": "subagent", "label": "Subagent started", "agent": 1, "model": MODEL},
        {"timestamp": day+"T10:07:00Z", "kind": "checkpoint", "label": "Preview-only intent checkpoint", "model": MODEL,
         "input": 37, "output": 4, "usage_availability": {"input_output": "recorded", "cache_read": "unavailable",
         "cache_write": "unavailable", "reasoning": "unavailable", "response_output": "unavailable"}},
        {"timestamp": compact_stamp, "kind": "compaction", "trigger": "threshold", "before": 2400000, "after": 850000},
    ]
    device = {"id": DEVICE, "name": "Antigravity preview · synthetic", "os": "Fixture macOS", "host": "fixture"}
    data = {"device": device, "devices": [{**device, "local": True}], "records": records, "sessions": sessions,
        "sessions_total": len(sessions), "session_children": [], "session_children_total": 0,
        "hourly": hourly, "tools": tools, "ctx": [], "reads": [], "skills": [], "ai_lines": [],
        "model_vendor": {MODEL: "Google", PRICED_MODEL: "Google"}, "pricing": {PRICED_MODEL: list(P.PRICING[PRICED_MODEL])},
        "prices": {PRICED_MODEL: list(P.PRICING[PRICED_MODEL])}, "pricing_history": {},
        "pricing_long_context": {}, "pricing_note": "Synthetic usage; no price row exists for the preview-only model.",
        "meta": {"files": 2, "last_refresh": now.timestamp(), "parse_errors": 0, "building": False},
        "version": {"describe": "Synthetic Antigravity preview", "git": False}, "home": "~",
        "mcp_servers": [], "mcp_inventory": None, "installed": {"items": [], "used": []},
        "context_files": [], "codex_effort": None}
    storage = {"sources": [{"source": "antigravity", "files": 2, "bytes": 180000}], "extras": [],
        "disk": {"total": 100000000000, "free": 50000000000, "used": 50000000000},
        "files": [{"source": "antigravity", "project": "antigravity-demo", "bytes": 120000,
                   "last": stamp, "path": "/fixture/antigravity/conversation.db"}], "files_total": 2,
        "cache_bytes": 0, "growth": [{"date": day, "source": "antigravity", "bytes": 180000}],
        "reclaimable": {}, "cleanup": {"commands": [], "days": 90, "shell": "zsh"}}
    diagnostics = {"sources": [{"source": "antigravity", "status": "available", "read": 2, "discovered": 2, "errors": 0}],
        "read": 2, "errors": 0, "missing_prices": [MODEL], "unsupported_formats": [{"source": "antigravity",
        "format": "legacy_pb", "count": 2, "hint": "Older conversation files are counted but not parsed; current database logs are supported."}]}
    return data, storage, diagnostics, {"available": True, "events": events, "truncated": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs): super().__init__(*args, directory=str(ROOT), **kwargs)
    def translate_path(self, path):
        parsed = urllib.parse.urlsplit(path).path
        if parsed == "/chart.js": parsed = "/chart.umd.min.js"
        return super().translate_path(parsed)
    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path
        data, storage, diagnostics, timeline = FIXTURE
        responses = {"/api/data": data, "/api/storage": storage, "/api/diagnostics": diagnostics,
            "/api/backups": {"backups": []}, "/api/accounts": {"entries": [], "accounts": []},
            "/api/devices": {"sharing": False, "devices": data["devices"]},
            "/api/sync": {"seconds": 15, "revision": "synthetic:15:1"}, "/api/native": {"supported": False, "installed": False, "running": False},
            "/api/local-servers": {"supported": False, "servers": [], "profiles": []},
            "/api/settings": {}, "/api/release-notes": {"available": False},
            "/api/health": {"ok": True}, "/api/summary": {"date": data["records"][0]["date"], "tokens": 0, "spend": 0}}
        if route == "/api/session-timeline": self._json(timeline)
        elif route in responses: self._json(responses[route])
        else: super().do_GET()
    def do_POST(self):
        route = urllib.parse.urlsplit(self.path).path
        if route == "/api/release-notes":
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0 or length > 16384:
                    raise ValueError("invalid request body length")
                body = json.loads(self.rfile.read(length))
            except (ValueError, UnicodeError, json.JSONDecodeError):
                self._json({"error": "invalid synthetic release-notes request"}, 400)
                return
            if body == {"action": "claim"}:
                # Match first-open baseline behavior without creating state or checking GitHub.
                release = {"tag": "0.0.0", "name": "Preview baseline", "published_at": None,
                           "url": "", "body": "", "truncated": False, "empty": False,
                           "availability": "unavailable"}
                self._json({"current_version": "0.0.0", "pending": False, "release": release,
                            "latest": None, "update_available": False, "claim": None})
                return
        self._json({"error": "Writes are disabled in this synthetic preview."}, 403)
    def _json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *_args): pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--port", type=int, default=8898)
    args = parser.parse_args(); FIXTURE = fixture()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Mock-only Antigravity preview: http://127.0.0.1:{server.server_port}/?range=7d&theme=dark#sessions", flush=True)
    print("Open the Sessions drawer for effort, cache, checkpoint, tool status and compaction detail; all counts are invented.", flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
