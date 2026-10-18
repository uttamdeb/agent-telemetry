"""Run the four feature views with invented local data, never a real ledger.

    python tests/preview_features.py --port 8896
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import diagnostics as DG
import native as N
import parser as P


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--port", type=int, default=8896)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="telemetry-features-preview-") as directory, ExitStack() as stack:
        root = Path(directory)
        fixtures = []
        today = datetime.now().astimezone().replace(hour=9, minute=0, second=0, microsecond=0)
        for index, (project, days, tokens) in enumerate([
                ("Dashboard", 0, 850000), ("Dashboard", 8, 120000),
                ("Website", 1, 160000), ("Website", 9, 340000),
                ("Research", 2, 280000), ("Research", 10, 90000)]):
            stamp = today - timedelta(days=days)
            path = root / ("fixture-%d.jsonl" % index)
            identity = "fixture-thread-%d" % index
            lines = [("session_meta", {"id": identity, "cwd": str(root / project)}),
                     ("turn_context", {"model": "gpt-6.1-sol", "effort": "high"}),
                     ("event_msg", {"type": "task_started"}),
                     ("event_msg", {"type": "user_message", "message": "Invented preview task"}),
                     ("event_msg", {"type": "item_completed", "item": {"type": "SubAgentActivity",
                         "kind": "started", "agent_thread_id": "preview-worker"}}),
                     ("response_item", {"type": "function_call", "name": "read_file", "arguments": "{}"}),
                     ("token_usage_record", {"response_id": "fixture-response", "usage": {
                         "input_tokens": tokens, "cached_input_tokens": tokens // 2, "output_tokens": tokens // 20}}),
                     ("compacted", {"compaction_response_id": "fixture-compact"}),
                     ("event_msg", {"type": "item_completed", "item": {"type": "SubAgentActivity",
                         "kind": "completed", "agent_thread_id": "preview-worker"}}),
                     ("event_msg", {"type": "task_complete"})]
            path.write_text("".join(json.dumps({"type": kind, "payload": body,
                "timestamp": (stamp + timedelta(seconds=n * 12)).isoformat()})+"\n"
                for n, (kind, body) in enumerate(lines)), encoding="utf-8")
            fixtures.append(("codex", str(path), None))
        for item in [patch.object(D, "CACHE_PATH", str(root / ".usage_cache.json")),
                     patch.object(N, "support_dir", return_value=root / "native"),
                     patch.object(D, "_local_id", return_value="fixture-device"),
                     patch.object(D, "DEVICE", {"name": "Feature preview", "os": "Synthetic data", "host": "fixture"}),
                     patch.object(D, "_devices_meta", return_value=[{"id": "fixture-device", "name": "Feature preview", "os": "Fixture"}]),
                     patch.object(D, "_context_files", return_value=[]), patch.object(D, "_installed", return_value=[]),
                     patch.object(D, "_mcp_servers", return_value={}), patch.object(D, "_codex_config", return_value={"effort": None}),
                     patch.object(D, "_peer_items", return_value=[]), patch.object(P, "discover", return_value=fixtures),
                     patch.object(P, "_copilot_project_map", return_value={}),
                     patch.object(D, "VERSION", {"git": False, "describe": "Feature preview"})]:
            stack.enter_context(item)
        D.refresh()
        D.backup_action({"action": "create"})
        server = D.Server(("127.0.0.1", args.port), D.Handler)
        D.BIND.update(host="127.0.0.1", port=server.server_port)
        print(f"Mock preview: http://127.0.0.1:{server.server_port}/?range=7d", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
