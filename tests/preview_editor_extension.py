"""Serve the bundled editor API with invented usage, never discover real logs."""
import argparse
import datetime
import json
from pathlib import Path
import signal
import sys
import threading


def main():
    options = argparse.ArgumentParser()
    options.add_argument("--backend", required=True)
    options.add_argument("--workspace", required=True)
    options.add_argument("--data-dir", required=True)
    args = options.parse_args()
    sys.path.insert(0, str(Path(args.backend).resolve()))
    import dashboard as D
    D._configure_data_dir(args.data_dir)
    D._local_id = lambda: "fixture-device"
    D.SN.display_names = lambda: {}
    D.N.refresh_interval = lambda: 0
    D.P.discover = lambda: []
    today = datetime.date.today().isoformat()
    workspace = str(Path(args.workspace).resolve())
    parent_id = "00000001-0000-4000-8000-000000000001"

    def session(number, title, source="codex", child=False, parent=None, cwd=workspace):
        identity = f"{number:08d}-0000-4000-8000-000000000001"
        root = Path(args.data_dir) / "invented-logs"
        source_path = (str(root / f"rollout-{identity}.jsonl") if source == "codex"
                       else str(root / f"{identity}.jsonl"))
        model = "GPT-6.1 Sol" if source == "codex" else "Sonnet 5.5"
        usage = dict(D._zero(), **{"in": number * 1000, "out": number * 200,
                                   "cr": number * 2000, "req": 2, "asst": 2})
        record = {today + "\t" + model: usage}
        row = {"id": identity[:8], "source": source, "title": title,
               "project": "fixture-project", "model": model, "subagent": child,
               "parent_id": parent, "start": today + "T09:00:00+00:00",
               "end": today + "T10:30:00+00:00", **usage,
               "reasoning_effort": {"days": {today + "\t" + model: {"levels": {"max": 2}}}},
               "compactions": [{"date": today}] if number == 1 else []}
        return {"path": source_path, "source": source, "project": "fixture-project",
                "cwd": cwd, "_session_id": identity, "subagent": child,
                "records": record, "sessions": [row]}

    aggregates = [session(1, "Build the release"),
                  session(2, "Review accessibility", child=True, parent=parent_id),
                  session(3, "Windows verification", child=True, parent=parent_id),
                  session(4, "Explore the data", source="claude"),
                  session(5, "Parent unavailable", child=True),
                  session(6, "A different workspace", cwd=str(Path(workspace).parent / "other-project"))]
    D._state["files"] = {agg["path"]: agg for agg in aggregates}
    D._meta.update(last_refresh=1, building=False, files=len(aggregates))

    def refresh(verbose=False):
        # A deterministic append to invented retained usage; no source parser runs.
        with D._refresh_lock:
            first = aggregates[0]
            next(iter(first["records"].values()))["in"] += 1000
            D._meta["last_refresh"] += 1
        return None

    D.refresh = refresh

    class FixtureHandler(D.Handler):
        """Expose only the fixture operations; other dashboard APIs stay unreachable."""
        def _not_found(self):
            self.close_connection = True
            self._send(404, json.dumps({"error": "Fixture endpoint not found."}))

        def do_GET(self):
            if self.path.split("?", 1)[0] in ("/api/health", "/api/sync", "/api/editor"):
                super().do_GET()
            else:
                self._not_found()

        def do_POST(self):
            if self.path.split("?", 1)[0] == "/api/refresh":
                super().do_POST()
            else:
                self._not_found()

        do_HEAD = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_TRACE = do_CONNECT = _not_found

    server = D.Server(("127.0.0.1", 0), FixtureHandler)
    D.BIND.update(host="127.0.0.1", port=server.server_port)
    server.timeout = 0.2
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps({"port": server.server_port, "workspace": workspace}), flush=True)
    try:
        while not stop.is_set():
            server.handle_request()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
