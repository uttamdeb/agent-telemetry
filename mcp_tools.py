"""Stable, read-only MCP tool contracts for AgentTelemetry."""

from __future__ import annotations

from datetime import date
import re


class ToolArgumentError(ValueError):
    """An input does not match the published tool contract."""


_PERIODS = ("today", "last_7_days", "last_30_days", "month_to_date", "all")
_FILTERS = ("tools", "models", "providers", "projects", "ides", "devices")
_GROUPS = ("date", "project", "tool", "model", "provider", "ide", "device")
_METRICS = ("tokens", "cost", "messages", "time")
_DIMENSIONS = ("project", "tool", "model", "provider", "ide", "device", "token_type")


def _string_array(description):
    return {
        "type": "array", "maxItems": 100, "uniqueItems": True,
        "items": {"type": "string", "minLength": 1, "maxLength": 200},
        "description": description,
    }


_RANGE_PROPERTIES = {
    "period": {
        "type": "string", "enum": list(_PERIODS), "default": "today",
        "description": "Relative date window. Omit to use today; supply from and to instead for an inclusive custom range.",
    },
    "from": {"type": "string", "format": "date", "description": "Inclusive start date, YYYY-MM-DD; pair with to."},
    "to": {"type": "string", "format": "date", "description": "Inclusive end date, YYYY-MM-DD; pair with from."},
}

_FILTER_PROPERTIES = {
    "tools": _string_array("Exact tool names to include."),
    "models": _string_array("Exact model names to include."),
    "providers": _string_array("Exact model providers to include."),
    "projects": _string_array("Project labels to include; labels may be anonymized when project names are disabled in Settings."),
    "ides": _string_array("Exact editor or surface names to include."),
    "devices": _string_array("Device labels to include; labels may be anonymized when device names are disabled in Settings."),
    "exact_only": {"type": "boolean", "default": False, "description": "Limit results to exact-source usage records where the source supports that precision mode."},
}


def _object(properties=None, required=(), description=None):
    out = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        out["required"] = list(required)
    if description:
        out["description"] = description
    return out


_USAGE_PROPERTIES = {
    **_RANGE_PROPERTIES, **_FILTER_PROPERTIES,
    "group_by": {
        "type": "array", "minItems": 0, "maxItems": len(_GROUPS), "uniqueItems": True,
        "items": {"type": "string", "enum": list(_GROUPS)},
        "description": "Break results down by supported dimensions; an empty list requests totals only.",
    },
    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
    "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
}
_SESSION_PROPERTIES = {
    **_RANGE_PROPERTIES, **_FILTER_PROPERTIES,
    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50},
    "offset": {"type": "integer", "minimum": 0, "maximum": 100000, "default": 0},
}
_CATALOG_PROPERTIES = {**_RANGE_PROPERTIES, **_FILTER_PROPERTIES}
_COMPARE_PROPERTIES = {
    **_RANGE_PROPERTIES, **_FILTER_PROPERTIES,
    "metric": {"type": "string", "enum": list(_METRICS), "description": "Measure to compare."},
    "dimension": {"type": "string", "enum": list(_DIMENSIONS), "description": "Dimension whose groups contribute to the change."},
    "baseline_from": {"type": "string", "format": "date", "description": "Inclusive baseline start date; pair with baseline_to."},
    "baseline_to": {"type": "string", "format": "date", "description": "Inclusive baseline end date; pair with baseline_from."},
    "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 50},
}
_INSIGHT_PROPERTIES = {**_RANGE_PROPERTIES, **_FILTER_PROPERTIES,
                       "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 50}}

_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "openWorldHint": False,
    "idempotentHint": True,
}


def _tool(name, title, description, schema):
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": schema,
        "outputSchema": {"type": "object"},
        "annotations": dict(_ANNOTATIONS),
    }


TOOLS = [
    _tool("get_usage", "Query usage", "Read aggregated local usage by date, project, tool, model, provider, editor, or device. Costs are estimates at published API prices. Defaults to today. Never returns prompts or arguments.", _object(_USAGE_PROPERTIES)),
    _tool("compare_usage", "Compare usage", "Compare an inclusive current date range with an optional inclusive baseline range, and show contributions by one dimension. This is accounting context, not a causal finding. Defaults to today for the current range.", _object(_COMPARE_PROPERTIES, ("metric", "dimension"))),
    _tool("get_insights", "Explain usage opportunities", "Summarize supported usage patterns and Optimize opportunities for the selected range and filters. Findings describe the data and do not claim causes. Defaults to today.", _object(_INSIGHT_PROPERTIES)),
    _tool("list_sessions", "List sessions", "List summarized local sessions with opaque selectors for timeline lookup. Session titles and native IDs are never returned; project/device labels follow the Settings scopes. Defaults to today.", _object(_SESSION_PROPERTIES)),
    _tool("get_session_timeline", "Read session timeline", "Read event kinds, timestamps, tool/model labels, and numeric usage for a session selector returned by list_sessions. Prompts, commands, arguments, and native identifiers are never returned.", _object({
        "session": {"type": "string", "pattern": "^session-[0-9a-f]{16}$", "minLength": 24, "maxLength": 24, "description": "Opaque selector from list_sessions; native session IDs are not accepted."},
        "from": {"type": "string", "format": "date", "description": "Optional inclusive start date; pair with to."},
        "to": {"type": "string", "format": "date", "description": "Optional inclusive end date; pair with from."},
    }, ("session",))),
    _tool("get_data_health", "Check data health", "Read aggregate scan and source-health categories for troubleshooting. This does not expose log paths, exception text, prompts, or credentials.", _object()),
    _tool("get_catalog", "Browse telemetry catalog", "List available tools, models, project labels, editors, and device labels represented in aggregated telemetry. Names may be anonymized by the Settings scopes. Defaults to today and the local device.", _object(_CATALOG_PROPERTIES)),
    _tool("get_subscription_limits", "Read saved account limits", "Read recent locally saved account-wide Codex or Claude quota observations only. This tool is offline: it never runs a CLI lookup, refreshes credentials, or returns account identity. Identity is unbound and may differ across entries; never sum entries, devices, or windows or derive limits from token counts. Remaining percent is shown only for a fresh known window; expired windows stay stale and are never refilled.", _object({
        "source": {"type": "string", "enum": ["codex", "claude"], "description": "Optionally limit to one local provider observation."},
    })),
]

TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}
_RANGE_TOOL_NAMES = {"get_usage", "list_sessions", "get_catalog", "get_insights", "compare_usage"}


def _bad(message):
    raise ToolArgumentError(message)


def _valid_date(value, field):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        _bad(f"{field} must be a real date in YYYY-MM-DD format.")
    try:
        date.fromisoformat(value)
    except ValueError:
        _bad(f"{field} must be a real date in YYYY-MM-DD format.")


def _validate_range(args, *, baseline=False):
    if "period" in args and ("from" in args or "to" in args):
        _bad("Use period or a from/to date pair, not both.")
    has_from, has_to = "from" in args, "to" in args
    if has_from != has_to:
        _bad("from and to must be supplied together.")
    if has_from:
        _valid_date(args["from"], "from")
        _valid_date(args["to"], "to")
        if args["from"] > args["to"]:
            _bad("from must be on or before to.")
    elif "period" in args and args["period"] not in _PERIODS:
        _bad("period must be today, last_7_days, last_30_days, month_to_date, or all.")
    if baseline:
        has_start, has_end = "baseline_from" in args, "baseline_to" in args
        if has_start != has_end:
            _bad("baseline_from and baseline_to must be supplied together.")
        if has_start:
            _valid_date(args["baseline_from"], "baseline_from")
            _valid_date(args["baseline_to"], "baseline_to")
            if args["baseline_from"] > args["baseline_to"]:
                _bad("baseline_from must be on or before baseline_to.")


def _validate_common(args, *, baseline=False):
    _validate_range(args, baseline=baseline)
    for name in _FILTERS:
        if name not in args:
            continue
        values = args[name]
        if not isinstance(values, list) or len(values) > 100:
            _bad(f"{name} must be a list of up to 100 distinct strings.")
        seen = set()
        for value in values:
            if (not isinstance(value, str) or not value or len(value) > 200
                    or value != value.strip() or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)):
                _bad(f"Each {name} value must be a non-empty string of at most 200 characters.")
            if value in seen:
                _bad(f"{name} values must be distinct.")
            seen.add(value)
    if "exact_only" in args and type(args["exact_only"]) is not bool:
        _bad("exact_only must be a boolean.")


def _validate_int(args, name, minimum, maximum):
    if name in args and (type(args[name]) is not int or not minimum <= args[name] <= maximum):
        _bad(f"{name} must be an integer from {minimum} through {maximum}.")


def validate_arguments(name: str, arguments) -> dict:
    """Validate without runtime dependencies and return a safe shallow copy."""
    tool = TOOL_BY_NAME.get(name)
    if tool is None:
        _bad("Unknown AgentTelemetry tool.")
    if not isinstance(arguments, dict):
        _bad("Tool arguments must be a JSON object.")
    allowed = set(tool["inputSchema"].get("properties", {}))
    unknown = set(arguments) - allowed
    if unknown:
        _bad("Tool arguments contain an unsupported field.")
    args = dict(arguments)
    if name in _RANGE_TOOL_NAMES:
        _validate_common(args, baseline=name == "compare_usage")
    if name == "get_usage":
        if "group_by" in args:
            groups = args["group_by"]
            if (not isinstance(groups, list) or len(groups) > len(_GROUPS)
                    or any(not isinstance(group, str) or group not in _GROUPS for group in groups)
                    or len(set(groups)) != len(groups)):
                _bad("group_by must contain distinct supported dimensions.")
        _validate_int(args, "limit", 1, 500)
        _validate_int(args, "offset", 0, 100000)
    elif name == "list_sessions":
        _validate_int(args, "limit", 1, 100)
        _validate_int(args, "offset", 0, 100000)
    elif name == "compare_usage":
        if args.get("metric") not in _METRICS:
            _bad("metric must be tokens, cost, messages, or time.")
        if args.get("dimension") not in _DIMENSIONS:
            _bad("dimension must be project, tool, model, provider, ide, device, or token_type.")
        _validate_int(args, "limit", 1, 500)
    elif name == "get_insights":
        _validate_int(args, "limit", 1, 100)
    elif name == "get_session_timeline":
        session = args.get("session")
        if not isinstance(session, str) or not re.fullmatch(r"session-[0-9a-f]{16}", session):
            _bad("session must be an opaque selector returned by list_sessions.")
        if ("from" in args) != ("to" in args):
            _bad("from and to must be supplied together.")
        if "from" in args:
            _valid_date(args["from"], "from")
            _valid_date(args["to"], "to")
            if args["from"] > args["to"]:
                _bad("from must be on or before to.")
    elif name == "get_subscription_limits":
        if "source" in args and args["source"] not in ("codex", "claude"):
            _bad("source must be codex or claude.")
    return args
