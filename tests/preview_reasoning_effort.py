"""Preview recorded-effort labels using invented data only.

    python tests/preview_reasoning_effort.py --port 8898

The existing mock-only Sessions harness supplies isolated temporary directories,
invented usage and disabled write endpoints. No ledger, source log, account or
configuration is read. Open ?theme=light or ?theme=dark and #sessions.
"""
from copy import deepcopy
from datetime import date as local_date, timedelta
import sys

import preview_subagents as fixture


_base_payload = fixture._payload


def _payload():
    payload = _base_payload()
    examples = [
        ("Extra high Codex review", {"xhigh": 2}, 0),
        ("Mixed Codex effort", {"low": 1, "high": 1}, 0),
        ("Codex High with partial recording", {"high": 1}, 2),
        ("Codex Ultra delegation", {"ultra": 1}, 0),
        ("Claude Max documentation", {"max": 3}, 0),
        ("Claude Max with unrecorded child usage", {"max": 1}, 1),
        ("Archived Claude with no recorded effort", None, 0),
        ("Legacy Codex with no recorded effort", None, 0),
        ("Claude mixed effort across models", {"medium": 1, "max": 1}, 0),
        ("Codex explicit None", {"none": 1}, 0),
    ]
    for session, (title, levels, unrecorded) in zip(payload["sessions"], examples):
        session["title"] = title
        if levels is None:
            continue
        date = session["end"][:10]
        session["reasoning_effort"] = {"version": 1, "days": {
            date + "\t" + session["model"]: {"levels": levels, "unrecorded": unrecorded}}}
    mixed = payload["sessions"][8]
    date = mixed["end"][:10]
    second_date = (local_date.fromisoformat(date) + timedelta(days=1)).isoformat()
    model = "Claude Opus 5.5"
    mixed["reasoning_effort"]["days"][date + "\t" + mixed["model"]]["levels"] = {"medium": 1}
    mixed["reasoning_effort"]["days"][second_date + "\t" + model] = {"levels": {"max": 1}, "unrecorded": 0}
    mixed["models"].append(model)
    mixed["nmodels"] = 2
    mixed["end"] = second_date + mixed["end"][10:]
    # Split this invented session between two dates/models. Keep whole prompts
    # and tool calls, and preserve its total usage for scope checks in a browser.
    vector = mixed["days"][date]
    first = [value / 2 for value in vector]
    second = deepcopy(first)
    first[6], second[6] = 1, 0
    first[7], second[7] = 3, 4
    mixed["days"] = {date: first, second_date: second}
    mixed["model_days"] = {mixed["model"]: {date: first}, model: {second_date: second}}
    fields = ["cost", "in", "out", "cr", "cc", "asst", "user", "tools", "prem", "active", "req", "cc5", "cc1", "reason", "ws"]
    for index, record in enumerate(payload["records"]):
        if record["project"] == mixed["project"]:
            payload["records"][index] = {**record, **dict(zip(fields, first))}
            payload["records"].append({**record, "date": second_date, "model": model,
                                       **dict(zip(fields, second))})
            break
    payload["model_vendor"][model] = "Anthropic"
    payload["codex_effort"] = "ultra"  # proves missing historical effort stays missing
    payload["version"] = {"git": False, "describe": "Synthetic reasoning effort preview"}
    return payload


if __name__ == "__main__":
    fixture._payload = _payload
    if "--port" not in sys.argv:
        sys.argv.extend(["--port", "8898"])
    fixture.main()
