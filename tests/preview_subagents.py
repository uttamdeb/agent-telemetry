"""Run the Sessions view with invented subagent data, without reading a ledger.

    python tests/preview_subagents.py --port 8897

Open the printed URL on desktop or mobile. The payload includes local, remote,
archived and older synthetic sessions; all write endpoints are disabled.
"""
import argparse
from contextlib import ExitStack
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounts as A
import dashboard as D
import native as N
import parser as P


LOCAL_ID = "fixture-local"
REMOTE_ID = "fixture-remote"
MODELS = {"codex": "GPT-6.1 Sol", "claude": "Claude Sonnet 5.5"}
FIXTURE_SESSION_COUNT = 10


def _vector(*, cost=0.0, input_tokens=0, output_tokens=0, cache_read=0,
            cache_write=0, replies=0, prompts=0, tools=0, active=0, requests=0):
    """The same 15-slot day vector returned by dashboard._day_vectors()."""
    return [cost, input_tokens, output_tokens, cache_read, cache_write, replies,
            prompts, tools, 0, active, requests, cache_write, 0, 0, 0]


def _session(*, source, suffix, title, project, days_ago, tokens, subagent=None,
             delegated=None, side=0, device=LOCAL_ID, archived=False,
             detail_limited=False, long_title=False, malicious=False):
    """Build one fully synthetic UI session; no source transcript is involved."""
    now = datetime.now().astimezone() - timedelta(hours=1)
    start = now - timedelta(days=days_ago, minutes=24)
    end = start + timedelta(minutes=17)
    date = start.date().isoformat()
    model = MODELS[source]
    cache_read = tokens // 10
    cache_write = tokens // 50
    billed_input = tokens - cache_read - cache_write
    input_tokens = billed_input * 3 // 4
    output_tokens = billed_input - input_tokens
    prompts = 3 if not (subagent is True) else 1
    replies = 5 if not (subagent is True) else 2
    tool_calls = 12 if not (subagent is True) else 7
    cost = round(D._cost(source, model, input_tokens, output_tokens, cache_read,
                         cache_write, 0, 0, date), 2)
    name = title
    if long_title:
        name = ("Long session title for narrow screens: "
                "a repeated repository investigation across generated tests, "
                "accessibility review, regression checks, and follow-up notes " * 3).strip()
    if malicious:
        name = '<img src=x onerror=alert(1)> "quoted" & unsafe title'
        project = '</td><script>alert("project")</script>'
    sid = f"{0x50000000 + suffix:08x}-0000-4000-8000-{suffix:012d}"
    result = {
        "id": sid[:8],
        "rename_id": sid,
        "rename_available": False,
        "rename_reason": "Rename is disabled in the mock preview.",
        "source": source,
        "title": name,
        "project": project,
        "model": model,
        "models": [model],
        "nmodels": 1,
        "ide": "VS Code" if source == "codex" else "CLI",
        "device": device,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "in": input_tokens,
        "out": output_tokens,
        "cr": cache_read,
        "cc": cache_write,
        "cc5": cache_write,
        "cc1": 0,
        "reason": 0,
        "cost": cost,
        "asst": replies,
        "user": prompts,
        "req": replies,
        "tools": tool_calls,
        "prem": 0,
        "active": 420,
        "side": side,
        "bytes": 12000 + suffix,
        "archived": archived,
    }
    if detail_limited:
        result["detail_limited"] = True
    if subagent is not None:
        result["subagent"] = subagent
    if delegated is not None:
        result["subagents"] = delegated

    if not detail_limited:
        vector = _vector(cost=cost, input_tokens=input_tokens, output_tokens=output_tokens,
                         cache_read=cache_read, cache_write=cache_write,
                         replies=replies, prompts=prompts, tools=tool_calls,
                         active=420, requests=replies)
        result["days"] = {date: vector}
        result["model_days"] = {model: {date: vector}}
    return result


def _payload():
    """Build the usual response shell, then attach only invented session usage."""
    payload = D._build_payload_locked()
    sessions = [
        # Codex records a spawn count on the primary thread. These independent
        # child rows intentionally have no parent pointer or inferred pairing.
        _session(source="codex", suffix=1, title="Codex primary: implementation and review",
                 project="session-ux", days_ago=0, tokens=240000, subagent=False, delegated=2),
        _session(source="codex", suffix=2, title="Codex subagent: test coverage audit",
                 project="session-ux", days_ago=0, tokens=88000, subagent=True),
        _session(source="codex", suffix=3, title="Codex subagent: API compatibility scan",
                 project="session-ux", days_ago=1, tokens=62000, subagent=True, device=REMOTE_ID),
        # A subagent can itself spawn another; keep the count on its own row.
        _session(source="codex", suffix=4, title="Codex subagent: split static analysis",
                 project="session-ux", days_ago=3, tokens=51000, subagent=True, delegated=1),
        # Claude's parent carries side-chain usage in its own totals. That says
        # delegated work happened, but does not identify its children or count.
        _session(source="claude", suffix=5, title="Claude primary: docs and accessibility pass",
                 project="session-ux", days_ago=1, tokens=205000, subagent=False, side=97000),
        _session(source="claude", suffix=6, title="Claude subagent: keyboard navigation check",
                 project="session-ux", days_ago=2, tokens=74000, subagent=True),
        _session(source="claude", suffix=7, title="Archived Claude subagent: dependency review",
                 project="session-ux", days_ago=8, tokens=42000, subagent=True, archived=True,
                 detail_limited=True),
        # An older cache row may not have the subagent flag or per-day detail.
        # Preserve the missing fields so the UI can describe status as unknown.
        _session(source="codex", suffix=8, title="Legacy session: delegation status unavailable",
                 project="older-ledger", days_ago=15, tokens=39000, detail_limited=True),
        _session(source="claude", suffix=9, title="Long title example",
                 project="long-title-project", days_ago=4, tokens=58000, subagent=True,
                 long_title=True),
        _session(source="codex", suffix=10, title="Unsafe title example", project="unsafe-project",
                 days_ago=5, tokens=33000, subagent=False, malicious=True),
    ]

    payload["sessions"] = sessions
    payload["sessions_total"] = len(sessions)

    # Give the other dashboard dimensions coherent synthetic rows, so the usual
    # filters, date range, and figures remain usable while inspecting Sessions.
    records, hourly, tools, projects = [], [], [], {}
    for session in sessions:
        date = (session.get("end") or session.get("start") or "")[:10]
        source, model = session["source"], session["model"]
        project, device = session.get("project") or "(unknown)", session["device"]
        record = {"date": date, "source": source, "model": model, "project": project,
                  "ide": session["ide"], "device": device, "in": session["in"],
                  "out": session["out"], "cr": session["cr"], "cc": session["cc"],
                  "cc5": session["cc5"], "cc1": session["cc1"], "reason": 0,
                  "asst": session["asst"], "user": session["user"], "req": session["req"],
                  "tools": session["tools"], "prem": 0, "active": session["active"],
                  "cost": session["cost"]}
        if session.get("detail_limited"):
            continue
        records.append(record)
        hour = int(session["start"][11:13])
        hourly.append({"date": date, "hour": hour, "source": source, "device": device,
                       "tokens": session["in"] + session["out"] + session["cr"] + session["cc"],
                       "msgs": session["asst"]})
        tools.append({"date": date, "source": source, "name": "Agent" if session.get("subagent") else "Read",
                      "device": device, "count": session["tools"]})
        key = (project, source)
        aggregate = projects.setdefault(key, {"tokens": 0, "msgs": 0, "sessions": 0, "cost": 0.0})
        aggregate["tokens"] += record["in"] + record["out"] + record["cr"] + record["cc"]
        aggregate["msgs"] += record["asst"]
        aggregate["sessions"] += 1
        aggregate["cost"] += record["cost"]
    payload["records"] = records
    payload["hourly"] = hourly
    payload["tools"] = tools
    payload["projects"] = [{"project": project, "source": source, **value}
                            for (project, source), value in projects.items()]
    payload["model_vendor"] = {model: P.vendor_of(model) for model in MODELS.values()}
    return payload


def _reject_writes(self):
    self._send(403, json.dumps({"error": "Writes are disabled in this synthetic preview."}))


class _MockLocalServers:
    def __init__(self, _data_dir):
        pass

    def snapshot(self):
        return {"supported": False, "servers": [], "profiles": []}

    def __getattr__(self, name):
        raise RuntimeError("Local server actions are disabled in this preview.")


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8897)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="telemetry-subagents-preview-") as directory, ExitStack() as stack:
        root = Path(directory)
        data_dir = root / "data"
        native_dir = root / "native"
        base_state = {"files": {}, "version": D.CACHE_VERSION}
        fixture_meta = {"last_refresh": time.time(), "last_duration": 0.0,
                        "files": FIXTURE_SESSION_COUNT, "building": False,
                        "parse_errors": 0}
        fake_devices = [
            {"id": LOCAL_ID, "name": "This device · synthetic data", "os": "Fixture macOS", "local": True},
            {"id": REMOTE_ID, "name": "Other device · synthetic data", "os": "Fixture Windows",
             "local": False, "shown": True, "synced": time.time()},
        ]
        for item in [
                patch.object(D, "DATA_DIR", str(data_dir)),
                patch.object(D, "CACHE_PATH", str(data_dir / ".usage_cache.json")),
                patch.object(D, "PEERS_PATH", str(data_dir / ".peers.json")),
                patch.object(D, "PEER_DIR", str(data_dir / ".peers")),
                patch.object(D, "_state", base_state),
                patch.object(D, "_meta", fixture_meta),
                patch.object(D, "_local_id", return_value=LOCAL_ID),
                patch.object(D, "DEVICE", {"name": "This device · synthetic data",
                                             "os": "Fixture macOS", "host": "fixture"}),
                patch.object(D, "_devices_meta", return_value=fake_devices),
                patch.object(D, "_peer_items", return_value=[]),
                patch.object(D, "_context_files", return_value=[]),
                patch.object(D, "_installed", return_value=[]),
                patch.object(D, "_mcp_servers", return_value={}),
                patch.object(D, "_codex_config", return_value={"effort": None}),
                patch.object(D, "VERSION", {"git": False, "describe": "Synthetic subagent preview"}),
                patch.object(D, "PEER_PULL_EVERY", 10**9),
                patch.object(D, "build_settings", return_value={"cache_files": 0, "cache_bytes": 0}),
                patch.object(D, "build_account_payload", return_value={"accounts": [], "status": "offline"}),
                patch.object(D, "backup_status", return_value={"backups": []}),
                patch.object(D, "build_diagnostics", return_value={"scans": [], "errors": []}),
                patch.object(D, "devices_status", return_value={"sharing": False, "devices": fake_devices}),
                patch.object(D, "build_storage", return_value={"files": [], "cache": {"bytes": 0}}),
                patch.object(D, "build_session_timeline", return_value={
                    "available": False, "events": [], "truncated": False,
                    "detail": "Source event detail is not included in this synthetic preview."}),
                patch.object(D, "LocalServerInventory", _MockLocalServers),
                patch.object(D.Handler, "do_POST", _reject_writes),
                patch.object(N, "support_dir", return_value=native_dir),
                patch.object(P, "HOME", str(root / "home")),
                patch.object(P, "discover", return_value=[]),
                patch.object(P, "_copilot_project_map", return_value={}),
                patch.object(A, "build_accounts", return_value={"accounts": [], "status": "offline"}),
                patch.object(A, "refresh_account", side_effect=RuntimeError("Account lookups are disabled.")),
                patch.object(A, "_profile", return_value="Synthetic profile"),
                patch.object(A, "_auth_signature", return_value=("synthetic",)),
        ]:
            stack.enter_context(item)

        payload = _payload()
        stack.enter_context(patch.object(D, "_build_payload_locked", side_effect=lambda: deepcopy(payload)))
        D._client_snapshot.update(revision=None, data=None, summary=None)
        server = D.Server(("127.0.0.1", args.port), D.Handler)
        D.BIND.update(host="127.0.0.1", port=server.server_port)
        print(f"Mock-only Sessions preview: http://127.0.0.1:{server.server_port}/?range=30d&theme=dark#sessions",
              flush=True)
        print("Use ?theme=light to preview the light theme; resize the browser for mobile layout.", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
