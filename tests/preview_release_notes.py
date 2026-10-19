"""Serve a release-notes UI preview using synthetic telemetry and isolated temp state.

    python tests/preview_release_notes.py --port 8879
    python tests/preview_release_notes.py --port 8879 --empty

The fixture never reads the user's logs, ledger, native preferences, or GitHub.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import native as N
import parser as P


def _release(tag, body, *, availability="available"):
    return {"key": tag, "tag": tag, "name": f"AgentTelemetry {tag}",
            "published_at": "2026-10-18T12:00:00Z",
            "url": f"https://github.com/uttamdeb/agent-telemetry/releases/tag/{tag}",
            "body": body, "truncated": False,
            "empty": availability == "available" and not bool(body.strip()),
            "availability": availability}


def _notes_body():
    return """## AgentTelemetry v2.1.0

This release brings a faster first view, clearer session detail, and a smoother native dashboard.

### Downloads

| Platform | Installer | Notes |
| --- | --- | --- |
| Windows x64 | [Download EXE](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/AgentTelemetrySetup.exe) | [Install guide](https://github.com/uttamdeb/agent-telemetry/blob/main/docs/install.md) |
| macOS Apple silicon | [Download DMG](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/AgentTelemetry-arm64.dmg) | [Build notes](https://github.com/uttamdeb/agent-telemetry/blob/main/docs/macos.md) |
| macOS Intel | [Download DMG](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.1.0/AgentTelemetry-x64.dmg) | [Build notes](https://github.com/uttamdeb/agent-telemetry/blob/main/docs/macos.md) |

### What’s new

- **A quicker first view.** The dashboard now loads a small synchronized clock and refreshes figures only when the shared tick changes.
- Session names can be edited after a warning preview, while transcripts and usage totals stay untouched.
- New timeline details show recorded activity, compactions, and agent lifecycle events without exposing prompts or tool arguments.
- Storage backups make routine recovery easier, with a preview step before restoring any saved ledger.
- Account limits remain private and separate from usage totals; a refresh still requires an explicit click.

### Improvements

- Charts now have clearer table alternatives and preserve filters in bookmarked links.
- Local server controls explain which process will be stopped and keep ownership checks tied to the original process.
- The native dashboard keeps the current view and query when the backend restarts after an update.
- Release downloads are built from a tag and labelled clearly when they are unsigned or not notarized.
- OpenCode sessions and project labels are easier to review across devices.

### Fixes

- Fixed stale summaries after an incremental log append.
- Fixed several retry states that could stop after a failed refresh.
- Improved keyboard focus, reduced-motion behavior, and narrow-window scrolling.
- Preserved archived sessions when supported cache metadata is upgraded.

Read the [MCP guide](https://github.com/uttamdeb/agent-telemetry/blob/main/docs/mcp.md) or visit the [VS Code Marketplace](https://marketplace.visualstudio.com/items?itemName=uttamdeb.agent-telemetry).
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8879)
    parser.add_argument("--empty", action="store_true", help="preview unavailable/offline note fallback")
    args = parser.parse_args()

    tag = "v2.1.0"
    availability = "unavailable" if args.empty else "available"
    current_release = _release(tag, "" if args.empty else _notes_body(), availability=availability)
    latest_release = _release("v2.2.0", "## A later release\n\nThis newer version is available to install.")

    class FixtureReleaseNotes:
        def __init__(self, _app_dir, _version):
            pass

        def status(self):
            result = {"current_version": "2.1.0", "pending": True,
                      "release": current_release, "latest": latest_release,
                      "update_available": True}
            if args.empty:
                result["error"] = "Mock offline preview. No GitHub request was made."
            return result

        def claim(self):
            return {"current_version": "2.1.0", "pending": True,
                    "claim": "a" * 64, "release": current_release,
                    "latest": latest_release, "update_available": True}

        def shown(self, _claim):
            return {"shown": True, "current_version": "2.1.0", "pending": False}

        def check(self):
            result = {"status": "unavailable" if args.empty else "available",
                      "current_version": "2.1.0", "release": current_release,
                      "latest": latest_release, "update_available": True,
                      "download_url": latest_release["url"]}
            if args.empty:
                result["error"] = "Mock offline preview. Notes could not be fetched."
            return result

    with tempfile.TemporaryDirectory(prefix="telemetry-release-notes-preview-") as directory, ExitStack() as stack:
        root = Path(directory)
        fixture = root / "fixture-session.jsonl"
        stamp = datetime.now().astimezone().isoformat()
        events = [
            ("session_meta", {"id": "preview-session", "cwd": str(root / "PreviewProject")}),
            ("turn_context", {"model": "gpt-6.1-sol", "effort": "high"}),
            ("event_msg", {"type": "task_started"}),
            ("event_msg", {"type": "user_message", "message": "Synthetic preview fixture"}),
            ("response_item", {"type": "function_call", "name": "read_file", "arguments": "{}"}),
            ("token_usage_record", {"response_id": "preview-response", "usage": {
                "input_tokens": 32000, "cached_input_tokens": 12000, "output_tokens": 1600}}),
            ("event_msg", {"type": "task_complete"}),
        ]
        fixture.write_text("".join(json.dumps({"type": kind, "payload": body, "timestamp": stamp}) + "\n"
                               for kind, body in events), encoding="utf-8")
        fixtures = [("codex", str(fixture), None)]
        patches = [
            patch.object(D, "CACHE_PATH", str(root / ".usage_cache.json")),
            patch.object(N, "support_dir", return_value=root / "native-preferences"),
            patch.object(D, "_local_id", return_value="release-notes-preview-device"),
            patch.object(D, "DEVICE", {"name": "Release notes preview", "os": "Synthetic data", "host": "fixture"}),
            patch.object(D, "_devices_meta", return_value=[{"id": "release-notes-preview-device", "name": "Release notes preview", "os": "Fixture"}]),
            patch.object(D, "_context_files", return_value=[]),
            patch.object(D, "_installed", return_value=[]),
            patch.object(D, "_mcp_servers", return_value={}),
            patch.object(D, "_codex_config", return_value={"effort": None}),
            patch.object(D, "_peer_items", return_value=[]),
            patch.object(D, "build_account_payload", return_value={"entries": []}),
            patch.object(D, "VERSION", {"git": False, "describe": "v2.1.0"}),
            patch.object(D, "update_action", return_value={"current": {"git": False, "describe": "v2.1.0"},
                                                            "behind": 0, "ahead": 0, "changes": [], "download_only": True}),
            patch.object(D, "_release_notes_status", new=FixtureReleaseNotes(None, None).status),
            patch.object(D, "_release_notes_check", new=FixtureReleaseNotes(None, None).check),
            patch.object(D.RN, "ReleaseNotes", FixtureReleaseNotes),
            patch.object(P, "discover", return_value=fixtures),
            patch.object(P, "_copilot_project_map", return_value={}),
        ]
        for item in patches:
            stack.enter_context(item)
        D.refresh()
        server = D.Server(("127.0.0.1", args.port), D.Handler)
        mode = "empty/offline" if args.empty else "cached release notes"
        print(f"Mock {mode} preview: http://127.0.0.1:{server.server_port}/?range=7d&nativeApp=1", flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close()


if __name__ == "__main__":
    main()
