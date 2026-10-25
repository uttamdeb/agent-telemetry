"""Serve invented Cursor data for local visual QA; reads no ledger or logs.

Run: python3 tests/preview_cursor.py --port 8897
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

DEVICE = "cursor-preview-device"
MODEL = "Claude Sonnet 5"
UNKNOWN_MODEL = "Unknown"


def fixture():
    now = datetime.now().astimezone().replace(microsecond=0)
    day, stamp = now.date().isoformat(), now.isoformat()
    parent_id = "cursor-preview-parent-8b176ee1"
    child_id = "cursor-preview-child-9c288ff2"
    partial_id = "cursor-preview-partial-a760cc52"
    claude_id = "claude-preview-session-62ad3b9f"
    price = list(P.PRICING[MODEL])
    partial_usage = {"in": 2600, "out": 420, "cr": 0, "cc": 0, "reason": 0,
                     "asst": 2, "user": 1, "req": 0, "tools": 2, "active": 110}
    partial_cost = (partial_usage["in"] * price[0] + partial_usage["out"] * price[1]) / 1_000_000
    partial_usage["cost"] = partial_cost
    unavailable = {"input_output": "unavailable", "cache_read": "unavailable", "cache_write": "unavailable",
                   "reasoning": "unavailable", "response_output": "unavailable"}
    partial_availability = {"input_output": "partial", "cache_read": "unavailable", "cache_write": "unavailable",
                            "reasoning": "unavailable", "response_output": "unavailable"}

    def row(identity, title, source, project, model, usage, *, status="recorded", availability=None,
            model_status="recorded", detail_status="recorded", subagent=False):
        usage = dict(usage)
        vector = [usage.get("cost", 0), usage.get("in", 0), usage.get("out", 0), usage.get("cr", 0),
                  usage.get("cc", 0), usage.get("asst", 0), usage.get("user", 0), usage.get("tools", 0),
                  usage.get("prem", 0), usage.get("active", 0), usage.get("req", 0), usage.get("cc5", 0),
                  usage.get("cc1", 0), usage.get("reason", 0), usage.get("ws", 0)]
        summary_model = model if model_status in ("recorded", "partial") else "Unavailable"
        models = [model] if model_status in ("recorded", "partial") else []
        session = {"id": identity[:8], "rename_id": identity, "rename_available": False,
            "rename_reason": "Cursor title edits are not supported.", "source": source, "title": title,
            "project": project, "model": summary_model, "models": models, "nmodels": len(models),
            "model_status": model_status, "detail_status": detail_status, "ide": "Cursor" if source == "cursor" else "Terminal",
            "device": DEVICE, "start": stamp, "end": stamp, **usage, "subagent": subagent,
            "usage_status": status, "usage_availability": dict(availability or {}), "days": {day: vector},
            "model_days": {model: {day: vector}} if model_status != "unavailable" else {"Unknown": {day: vector}},
            "record_availability": {day + "\t" + (model if model_status != "unavailable" else "Unknown"):
                dict(availability or unavailable)}, "compactions": []}
        if source == "cursor":
            session["current_model"] = "Current Composer Setting · synthetic"
            session["rename_id"] = identity
        return session

    metadata_usage = {"in": 0, "out": 0, "cr": 0, "cc": 0, "reason": 0, "asst": 3, "user": 2,
                      "req": 0, "tools": 4, "active": 180, "cost": 0.0}
    child_usage = {"in": 0, "out": 0, "cr": 0, "cc": 0, "reason": 0, "asst": 1, "user": 1,
                   "req": 0, "tools": 1, "active": 35, "cost": 0.0}
    cursor_parent = row(parent_id, "Same synthetic review", "cursor", "cursor-petal-project", UNKNOWN_MODEL,
        metadata_usage, status="unavailable", availability=unavailable, model_status="unavailable", detail_status="partial")
    cursor_parent.update(subagents=1)
    cursor_child = row(child_id, "Same synthetic review", "cursor", "cursor-petal-project", UNKNOWN_MODEL,
        child_usage, status="unavailable", availability=unavailable, model_status="unavailable", detail_status="partial",
        subagent=True)
    cursor_child["subagent_parent"] = {"id": parent_id, "title": cursor_parent["title"], "source": "cursor", "device": DEVICE}
    cursor_partial = row(partial_id, "Partially recorded Cursor session", "cursor", "cursor-orchid-project", MODEL,
        partial_usage, status="partial", availability=partial_availability, model_status="partial", detail_status="recorded")
    claude_usage = {"in": 1700, "out": 560, "cr": 850, "cc": 0, "reason": 0, "asst": 2, "user": 2,
                    "req": 2, "tools": 1, "active": 150, "cost": 0.0}
    claude_price = list(P.PRICING["Claude Sonnet 5"])
    claude_usage["cost"] = (claude_usage["in"] * claude_price[0] + claude_usage["out"] * claude_price[1]
                             + claude_usage["cr"] * claude_price[4]) / 1_000_000
    claude = row(claude_id, "Priced Claude fixture", "claude", "claude-lilac-project", "Claude Sonnet 5",
        claude_usage, availability={"input_output": "recorded", "cache_read": "recorded", "cache_write": "recorded"})
    sessions = [cursor_parent, cursor_child, cursor_partial, claude]

    records = []
    hourly, tools = [], []
    for session in sessions:
        record = {key: session.get(key, 0) for key in ("in", "out", "cr", "cc", "reason", "asst", "user", "req", "tools", "active", "cost")}
        record.update(date=day, source=session["source"], model=session["model"] if session["model_status"] in ("recorded", "partial") else UNKNOWN_MODEL,
                      project=session["project"], ide=session["ide"], device=DEVICE,
                      usage_status=session["usage_status"], usage_availability=dict(session["usage_availability"]))
        records.append(record)
        hourly.append({"date": day, "hour": now.hour, "source": session["source"], "device": DEVICE,
                       "tokens": record["in"] + record["out"] + record["cr"] + record["cc"], "msgs": record["asst"]})
        if session["tools"]:
            tools.append({"date": day, "source": session["source"], "device": DEVICE,
                          "name": "Read" if session["source"] == "cursor" else "Search", "count": session["tools"]})

    events = [
        {"timestamp": day + "T10:01:00Z", "kind": "session", "label": "Session started"},
        {"timestamp": day + "T10:02:00Z", "kind": "request", "model": MODEL, "input": 2600, "output": 420,
         "usage_availability": dict(partial_availability)},
        {"timestamp": day + "T10:03:00Z", "kind": "tool", "label": "Read", "status": "done", "model": MODEL},
        {"timestamp": day + "T10:04:00Z", "kind": "subagent", "label": "Subagent started", "agent": 1},
    ]
    device = {"id": DEVICE, "name": "Cursor preview · synthetic", "os": "Fixture macOS", "host": "fixture"}
    pricing = {MODEL: price}
    data = {"device": device, "devices": [{**device, "local": True}], "records": records, "sessions": sessions,
        "sessions_total": len(sessions), "session_children": [], "session_children_total": 0,
        "hourly": hourly, "tools": tools, "ctx": [], "reads": [], "skills": [],
        "ai_lines": [{"date": day, "source": "cursor", "device": DEVICE, "tab_suggested": 35, "tab_accepted": 12,
                      "composer_suggested": 70, "composer_accepted": 28}],
        "model_vendor": {MODEL: "Anthropic", UNKNOWN_MODEL: "Other"}, "pricing": pricing, "prices": pricing,
        "pricing_history": {}, "pricing_long_context": {},
        "meta": {"files": 1, "last_refresh": now.timestamp(), "parse_errors": 0, "building": False},
        "version": {"describe": "Synthetic Cursor preview", "git": False}, "home": "~",
        "mcp_servers": [], "mcp_inventory": None, "installed": {"items": [], "used": []},
        "context_files": [], "codex_effort": None}
    storage = {"sources": [{"source": "cursor", "files": 1, "bytes": 120000}], "extras": [],
        "disk": {"total": 100000000000, "free": 50000000000, "used": 50000000000},
        "files": [{"source": "cursor", "project": "cursor-petal-project", "bytes": 120000,
                   "last": stamp, "path": "/fixture/cursor/state.vscdb"}], "files_total": 1,
        "cache_bytes": 0, "growth": [{"date": day, "source": "cursor", "bytes": 120000}],
        "reclaimable": {}, "cleanup": {"commands": [], "days": 90, "shell": "zsh"}}
    diagnostics = {"sources": [{"source": "cursor", "status": "available", "read": 1, "discovered": 1, "errors": 0}],
        "read": 1, "errors": 0, "missing_prices": [UNKNOWN_MODEL], "unsupported_formats": []}
    return data, storage, diagnostics, {"available": True, "events": events, "truncated": False}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def translate_path(self, path):
        parsed = urllib.parse.urlsplit(path).path
        if parsed == "/chart.js":
            parsed = "/chart.umd.min.js"
        return super().translate_path(parsed)

    def do_GET(self):
        route = urllib.parse.urlsplit(self.path).path
        data, storage, diagnostics, timeline = FIXTURE
        responses = {"/api/data": data, "/api/storage": storage, "/api/diagnostics": diagnostics,
            "/api/backups": {"backups": []}, "/api/accounts": {"entries": [], "accounts": []},
            "/api/devices": {"sharing": False, "devices": data["devices"]},
            "/api/sync": {"seconds": 15, "revision": "synthetic:15:1"},
            "/api/native": {"supported": False, "installed": False, "running": False},
            "/api/local-servers": {"supported": False, "servers": [], "profiles": []},
            "/api/settings": {}, "/api/mcp": {"enabled": False}, "/api/release-notes": {"available": False},
            "/api/health": {"ok": True},
            "/api/summary": {"date": data["records"][0]["date"], "tokens": 0, "spend": 0}}
        if route == "/api/session-timeline":
            self._json(timeline)
        elif route in responses:
            self._json(responses[route])
        else:
            super().do_GET()

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
                release = {"tag": "0.0.0", "name": "Preview baseline", "published_at": None, "url": "",
                           "body": "", "truncated": False, "empty": False, "availability": "unavailable"}
                self._json({"current_version": "0.0.0", "pending": False, "release": release,
                            "latest": None, "update_available": False, "claim": None})
                return
        self._json({"error": "Writes are disabled in this synthetic preview."}, 403)

    def _json(self, value, status=200):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8897)
    args = parser.parse_args()
    FIXTURE = fixture()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Mock-only Cursor preview: http://127.0.0.1:{server.server_port}/?range=7d&theme=light#sessions", flush=True)
    print("Open the Sessions drawer for token/model coverage, duplicate-title subagent, and timeline; all data is synthetic.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
