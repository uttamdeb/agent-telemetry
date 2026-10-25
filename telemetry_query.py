"""Read-only, privacy-aware queries over AgentTelemetry's public payload.

This module deliberately knows nothing about the ledger, filesystem, HTTP server,
or network.  Its only input is the dictionary returned by ``dashboard.build_payload``.
All outward-facing values are constructed from an allowlist; payload dictionaries are
never copied wholesale into a result.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import math
import re
import unicodedata
from typing import Any


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOOL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MAX_CUSTOM_DAYS = 3660
_MAX_LIMIT = 500
_MAX_OFFSET = 100_000
_MAX_FILTER_VALUES = 100
_MAX_FILTER_TEXT = 200
_MAX_CATALOG_ITEMS = 500
_FUTURE_WARNING = "Future dates were excluded; the selected range ends today."
_KNOWN_TOOLS = {
    "claude": "Claude Code",
    "codex": "Codex",
    "copilot": "Copilot",
    "cursor": "Cursor",
    "claude-desktop": "Claude Desktop",
    "opencode": "OpenCode",
    "hermes": "Hermes Agent",
    "openclaw": "OpenClaw",
    "antigravity": "Antigravity",
}
_EXACT_TOOLS = frozenset({
    "claude", "codex", "claude-desktop", "opencode", "hermes", "openclaw", "antigravity",
})
_GROUP_FIELDS = ("date", "project", "tool", "model", "provider", "ide", "device")
_RECORD_FIELDS = (
    "date", "source", "model", "project", "ide", "device", "in", "out", "cr", "cc",
    "cc5", "cc1", "reason", "cost", "req", "asst", "user", "tools", "active", "prem", "ws",
    "lc_in", "lc_out", "lc_cc5", "lc_cc1", "lc_cr", "lc_asst", "usage_status", "usage_availability",
)
_USAGE_STATES = frozenset(("recorded", "partial", "unavailable"))
_USAGE_AVAILABILITY_FIELDS = frozenset(("input_output", "cache_read", "cache_write", "reasoning", "response_output"))
_SUM_FIELDS = (
    "in", "out", "cr", "cc", "cc5", "cc1", "reason", "cost", "req", "asst", "user",
    "tools", "active",
)
_METRIC_SOURCES = {
    "fresh_input_tokens": ("in",),
    "output_tokens": ("out",),
    "cache_read_tokens": ("cr",),
    "cache_write_tokens": ("cc",),
    "cache_write_5m_tokens": ("cc5",),
    "cache_write_1h_tokens": ("cc1",),
    "reasoning_tokens": ("reason",),
    "estimated_cost_usd": ("cost",),
    "requests": ("req",),
    "assistant_messages": ("asst",),
    "user_prompts": ("user",),
    "tool_calls": ("tools",),
    "active_seconds": ("active",),
}
_VECTOR_FIELDS = (
    "cost", "in", "out", "cr", "cc", "asst", "user", "tools", "prem", "active", "req",
    "cc5", "cc1", "reason", "ws",
)
_VECTOR_INDEX = {name: i for i, name in enumerate(_VECTOR_FIELDS)}
_SESSION_METRICS = {
    "fresh_input_tokens": "in",
    "output_tokens": "out",
    "cache_read_tokens": "cr",
    "cache_write_tokens": "cc",
    "cache_write_5m_tokens": "cc5",
    "cache_write_1h_tokens": "cc1",
    "reasoning_tokens": "reason",
    "estimated_cost_usd": "cost",
    "requests": "req",
    "assistant_messages": "asst",
    "user_prompts": "user",
    "tool_calls": "tools",
    "active_seconds": "active",
}


def _safe_text(value: Any, limit: int = 160) -> str:
    """Return bounded display text with controls removed and absolute paths hidden."""
    if not isinstance(value, str):
        return ""
    value = "".join(ch for ch in value if not unicodedata.category(ch).startswith("C"))
    value = " ".join(value.split()).strip()[:limit]
    if _looks_like_path(value):
        value = _leaf(value)
    return value[:limit]


def _looks_like_path(value: str) -> bool:
    return (value.startswith(("/", "\\", "~/", "~\\"))
            or bool(re.search(r"(?:^|\s)[A-Za-z]:[\\/].+", value))
            or bool(re.search(r"(?:^|\s)/(?:Users|home|private|tmp|var|opt)/", value)))


def _leaf(value: str) -> str:
    return value.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]


def _project_text(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "(unknown)"
    # The project dimension may be a cwd. Only its final display component can
    # leave the process, even when the user has enabled project names.
    value = value.rstrip("/\\").replace("\\", "/").rsplit("/", 1)[-1]
    return _safe_text(value, 120) or "(unknown)"


def _raw_text(value: Any, limit: int = 160) -> str:
    if value is None:
        return "(unknown)"
    if isinstance(value, str) and _looks_like_path(value):
        return "(unknown)"
    return _safe_text(value, limit) or "(unknown)"


def _safe_tool_id(value: Any) -> str:
    if not isinstance(value, str):
        return "(unknown)"
    if value in _KNOWN_TOOLS:
        return value
    return value if _TOOL_ID_RE.fullmatch(value) else "(unknown)"


def _alias(kind: str, value: str, policy: dict[str, Any]) -> str:
    key = policy.get("alias_key", "")
    if isinstance(key, bytes):
        key_bytes = key
    elif isinstance(key, str):
        key_bytes = key.encode("utf-8", "replace")
    else:
        key_bytes = b""
    digest = hmac.new(key_bytes, (kind + "\0" + value).encode("utf-8", "replace"), hashlib.sha256).hexdigest()
    return f"{kind}-{digest[:16]}"


def _policy_for(payload: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    """Copy trusted user settings and attach display names read from the payload."""
    supplied = policy if isinstance(policy, dict) else {}
    out = {
        "include_names": supplied.get("include_names") is True,
        "include_peers": supplied.get("include_peers") is True,
        "alias_key": supplied.get("alias_key", "") if isinstance(supplied.get("alias_key", ""), (str, bytes)) else "",
        "_device_names": {},
    }
    names: dict[str, str] = out["_device_names"]
    local = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    local_id = local.get("id")
    local_name = local.get("name")
    if isinstance(local_id, str) and local_id and isinstance(local_name, str):
        names[local_id] = local_name
    devices = payload.get("devices")
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id, name = item.get("id"), item.get("name")
            if (isinstance(device_id, str) and device_id and isinstance(name, str)
                    and (out["include_peers"] or device_id == local_id)):
                names[device_id] = name
    return out


def label(kind: str, value: Any, policy: dict[str, Any] | None = None) -> str:
    """Return a sanitized label or stable HMAC alias for a project/device/session."""
    if kind not in {"project", "device", "session"}:
        return _raw_text(value)
    if kind == "session":
        return _alias("session", str(value), policy if isinstance(policy, dict) else {})
    raw = value if isinstance(value, str) else ""
    if not raw or raw == "(unknown)":
        return "(unknown)"
    p = policy if isinstance(policy, dict) else {}
    if p.get("include_names") is True:
        if kind == "project":
            return _project_text(raw)
        names = p.get("_device_names")
        if isinstance(names, dict) and isinstance(names.get(raw), str):
            raw_name = names[raw]
            if not _looks_like_path(raw_name) and "/" not in raw_name and "\\" not in raw_name:
                name = _safe_text(raw_name, 100)
                if name:
                    return name
    return _alias(kind, raw, p)


def _device_id(payload: dict[str, Any]) -> str | None:
    device = payload.get("device")
    if isinstance(device, dict) and isinstance(device.get("id"), str) and device["id"]:
        return device["id"]
    return None


def _as_date(value: Any, name: str) -> date:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise ValueError(f"{name} must be a YYYY-MM-DD calendar date.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid YYYY-MM-DD calendar date.") from exc


def _today_value(today: date | str | None) -> date:
    if today is None:
        return date.today()
    if isinstance(today, datetime):
        raise ValueError("today must be a local calendar date, not a datetime.")
    if isinstance(today, date):
        return today
    return _as_date(today, "today")


def _argument_filters(arguments: dict[str, Any]) -> dict[str, list[str]]:
    nested = arguments.get("filters", {})
    if nested is None:
        nested = {}
    if not isinstance(nested, dict):
        raise ValueError("filters must be an object of exact-value lists.")
    aliases = {"tools": ("tools", "tool"), "providers": ("providers", "provider"),
               "models": ("models", "model"), "projects": ("projects", "project"),
               "ides": ("ides", "ide"), "devices": ("devices", "device")}
    normalized: dict[str, list[str]] = {}
    for dim, keys in aliases.items():
        raw = None
        for key in keys:
            if key in arguments:
                raw = arguments[key]
                break
            if key in nested:
                raw = nested[key]
                break
        if raw is None:
            normalized[dim] = []
            continue
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, (list, tuple, set, frozenset)):
            raise ValueError(f"{dim} must be a string or a list of strings.")
        values: list[str] = []
        for item in raw:
            if not isinstance(item, str) or len(item) > _MAX_FILTER_TEXT:
                raise ValueError(f"Each {dim} filter must be text of at most {_MAX_FILTER_TEXT} characters.")
            if item not in values:
                values.append(item)
            if len(values) > _MAX_FILTER_VALUES:
                raise ValueError(f"At most {_MAX_FILTER_VALUES} {dim} filters are allowed.")
        normalized[dim] = values
    return normalized


def _range_context(payload: dict[str, Any], arguments: dict[str, Any], today: date | str | None = None,
                   policy: dict[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    now = _today_value(today)
    raw_from = arguments.get("from", arguments.get("from_date"))
    raw_to = arguments.get("to", arguments.get("to_date"))
    period = arguments.get("period")
    if period is not None and not isinstance(period, str):
        raise ValueError("period must be a supported string.")
    if (raw_from is None) != (raw_to is None):
        raise ValueError("Provide both from and to dates for a custom range.")

    warnings: list[str] = []
    if raw_from is not None:
        start, end = _as_date(raw_from, "from"), _as_date(raw_to, "to")
        if period not in (None, "custom"):
            raise ValueError("Use either period or custom from/to dates, not both.")
        period_name = "custom"
        if end > now:
            end = now
            warnings.append(_FUTURE_WARNING)
        if start > end:
            raise ValueError("from must be on or before to, and the range must include a date through today.")
        if (end - start).days + 1 > _MAX_CUSTOM_DAYS:
            raise ValueError(f"A custom date range may not exceed {_MAX_CUSTOM_DAYS} days.")
    else:
        aliases = {"7d": "last_7_days", "30d": "last_30_days", "mtd": "month_to_date"}
        period_name = aliases.get(period, period) if period else "today"
        if period_name == "today":
            start = end = now
        elif period_name == "last_7_days":
            start, end = now - timedelta(days=6), now
        elif period_name == "last_30_days":
            start, end = now - timedelta(days=29), now
        elif period_name == "month_to_date":
            start, end = now.replace(day=1), now
        elif period_name == "all":
            dates = []
            rows = payload.get("records")
            scoped_policy = _policy_for(payload, policy)
            local_id = _device_id(payload)
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict):
                        row_device = row.get("device") if isinstance(row.get("device"), str) and row.get("device") else local_id
                        if not scoped_policy["include_peers"] and (
                                (local_id is not None and row_device != local_id)
                                or (local_id is None and row_device is not None)):
                            continue
                        raw = row.get("date")
                        try:
                            d = _as_date(raw, "record date") if raw != "0000-00-00" else None
                        except ValueError:
                            d = None
                        if d is not None and d <= now:
                            dates.append(d)
            start = min(dates) if dates else date(2000, 1, 1)
            end = now
        else:
            raise ValueError("period must be today, last_7_days, last_30_days, month_to_date, all, or custom.")

    filters = _argument_filters(arguments)
    exact_only = arguments.get("exact_only", arguments.get("exact", False))
    if not isinstance(exact_only, bool):
        raise ValueError("exact_only must be a boolean.")
    return {
        "from": start.isoformat(), "to": end.isoformat(), "period": period_name,
        "filters": filters, "exact_only": exact_only, "warnings": warnings,
        "today": now.isoformat(),
    }


def _finite_number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _model_provider(payload: dict[str, Any], model: str) -> str:
    if model == "(user)":
        return "Other"
    vendors = payload.get("model_vendor")
    value = vendors.get(model) if isinstance(vendors, dict) else None
    return _raw_text(value) if isinstance(value, str) and value else "Other"


def _match_value(raw: str, requested: list[str], kind: str, policy: dict[str, Any], payload: dict[str, Any]) -> bool:
    return matches_filter(kind, raw, requested, policy=policy, payload=payload)


def matches_filter(kind: str, raw: Any, requested: list[str] | tuple[str, ...], *,
                   policy: dict[str, Any] | None = None,
                   payload: dict[str, Any] | None = None) -> bool:
    """Match project/device/IDE selectors like record queries do.

    Raw identifiers, safe display labels, and stable aliases are accepted so a
    catalog selector remains usable when the user's display-name preference changes.
    """
    if not requested:
        return True
    if kind not in {"project", "device", "ide"}:
        return False
    raw = raw if isinstance(raw, str) else "(unknown)"
    data = payload if isinstance(payload, dict) else {}
    effective = _policy_for(data, policy)
    candidates = {raw, _raw_text(raw)}
    if kind == "project":
        candidates.add(_project_text(raw))
        candidates.add(_alias("project", raw, effective))
    candidates.add(label(kind, raw, effective))
    if kind == "device":
        candidates.add(_alias("device", raw, effective))
        names = effective.get("_device_names")
        if isinstance(names, dict) and isinstance(names.get(raw), str):
            name = names[raw]
            if not _looks_like_path(name) and "/" not in name and "\\" not in name:
                candidates.add(_safe_text(name, 100))
        if raw == _device_id(data):
            candidates.add("This device")
    return any(value in candidates for value in requested)


def _row_copy(row: dict[str, Any]) -> dict[str, Any]:
    out = {key: row.get(key) for key in _RECORD_FIELDS if key in row}
    if not isinstance(out.get("usage_status"), str) or out.get("usage_status") not in _USAGE_STATES:
        out.pop("usage_status", None)
    availability = out.get("usage_availability")
    if isinstance(availability, dict):
        out["usage_availability"] = {
            field: state for field, state in availability.items()
            if field in _USAGE_AVAILABILITY_FIELDS and isinstance(state, str) and state in _USAGE_STATES
        }
        if not out["usage_availability"]:
            out.pop("usage_availability", None)
    else:
        out.pop("usage_availability", None)
    return out


def _combine_usage_states(states: list[Any]) -> str | None:
    safe = [state for state in states if isinstance(state, str) and state in _USAGE_STATES]
    if not safe:
        return None
    return safe[0] if all(state == safe[0] for state in safe) else "partial"


def _usage_quality(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = row.get("source", row.get("tool"))
        if not isinstance(source, str):
            continue
        slot = grouped.setdefault(source, {"statuses": [], "fields": {}})
        if isinstance(row.get("usage_status"), str) and row.get("usage_status") in _USAGE_STATES:
            slot["statuses"].append(row["usage_status"])
        availability = row.get("usage_availability")
        if isinstance(availability, dict):
            for field, state in availability.items():
                if field in _USAGE_AVAILABILITY_FIELDS and isinstance(state, str) and state in _USAGE_STATES:
                    slot["fields"].setdefault(field, []).append(state)
    result = {}
    for source, slot in grouped.items():
        fields = {field: _combine_usage_states(states)
                  for field, states in slot["fields"].items()}
        result[source] = {
            "usage_status": _combine_usage_states(slot["statuses"]),
            "usage_availability": {field: state for field, state in fields.items() if state},
        }
    return result


def _selected_rows(payload: dict[str, Any], context: dict[str, Any], policy: dict[str, Any]) -> tuple[list[dict[str, Any]], int, int, list[str]]:
    rows = payload.get("records")
    if not isinstance(rows, list):
        return [], 0, 0, []
    f = context["filters"]
    local_id = _device_id(payload)
    selected: list[dict[str, Any]] = []
    future_excluded = 0
    visible_rows = 0
    visible_dates: list[str] = []
    for source_row in rows:
        if not isinstance(source_row, dict):
            continue
        row = _row_copy(source_row)
        source = row.get("source")
        model = row.get("model")
        raw_date = row.get("date")
        if not isinstance(source, str) or not source or not isinstance(model, str):
            continue
        if context["exact_only"]:
            if source not in _EXACT_TOOLS and source != "copilot":
                continue
            if source == "copilot":
                exact = source_row.get("exact")
                if not isinstance(exact, dict):
                    continue
                # Long-context counters describe a subset of the exact row only;
                # never carry the estimated parent row's pricing subset forward.
                for field in ("lc_in", "lc_out", "lc_cc5", "lc_cc1", "lc_cr", "lc_asst"):
                    row.pop(field, None)
                for key in _RECORD_FIELDS:
                    if key in exact and key not in {"date", "source", "model", "project", "ide", "device"}:
                        row[key] = exact[key]
        if not isinstance(raw_date, str) or not _DATE_RE.fullmatch(raw_date):
            continue
        try:
            row_date = date.fromisoformat(raw_date)
        except ValueError:
            continue
        project = row.get("project") if isinstance(row.get("project"), str) else "(unknown)"
        ide = row.get("ide") if isinstance(row.get("ide"), str) else "(unknown)"
        has_device = isinstance(row.get("device"), str) and bool(row.get("device"))
        device = row.get("device") if has_device else local_id
        if not policy["include_peers"] and (
                (local_id is not None and device != local_id)
                or (local_id is None and has_device)):
            continue
        visible_rows += 1
        if row_date > date.fromisoformat(context["today"]):
            future_excluded += 1
            continue
        visible_dates.append(raw_date)
        if raw_date < context["from"] or raw_date > context["to"]:
            continue
        if device is None:
            device = "(unknown)"
        provider = _model_provider(payload, model)
        if model != "(user)":
            if f["models"] and model not in f["models"]:
                continue
            if f["providers"] and provider not in f["providers"]:
                continue
        if f["tools"] and source not in f["tools"] and _KNOWN_TOOLS.get(source) not in f["tools"]:
            continue
        if not _match_value(project, f["projects"], "project", policy, payload):
            continue
        if not _match_value(ide, f["ides"], "ide", policy, payload):
            continue
        if not _match_value(device, f["devices"], "device", policy, payload):
            continue
        row["project"] = project
        row["ide"] = ide
        row["device"] = device
        row["provider"] = provider
        selected.append(row)
    return selected, future_excluded, visible_rows, visible_dates


def select_records(payload: dict[str, Any], arguments: dict[str, Any], *, policy: dict[str, Any] | None = None,
                   today: date | str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Select dated record copies using dashboard-equivalent filters.

    Returned row copies are internal analytics inputs, not public results. They contain
    only record fields needed by the query/insights engines. ``context`` retains the
    normalized, unredacted filter values for internal matching; callers must pass it
    through ``result_meta`` before exposing metadata.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload must be a build_payload() dictionary.")
    effective_policy = _policy_for(payload, policy)
    context = _range_context(payload, arguments, today, effective_policy)
    selected, future_excluded, visible_rows, visible_dates = _selected_rows(payload, context, effective_policy)
    context["coverage"] = {
        "records_in_payload": visible_rows,
        "records_payload_complete": payload.get("records_complete") if isinstance(payload.get("records_complete"), bool) else None,
        "future_rows_excluded": future_excluded,
        "available_date_range": {"from": min(visible_dates) if visible_dates else None,
                                  "to": max(visible_dates) if visible_dates else None},
    }
    context["selected_records"] = len(selected)
    context["selected_sources"] = sorted({row["source"] for row in selected if isinstance(row.get("source"), str)})
    context["source_usage"] = _usage_quality(selected)
    return selected, context


def _display_value(dimension: str, raw: Any, payload: dict[str, Any], policy: dict[str, Any]) -> str:
    if dimension == "project":
        return label("project", raw if isinstance(raw, str) else "(unknown)", policy)
    if dimension == "device":
        return label("device", raw if isinstance(raw, str) else "(unknown)", policy)
    if dimension == "tool":
        tool = _safe_tool_id(raw)
        return _KNOWN_TOOLS.get(tool, tool)
    if dimension == "provider":
        return _raw_text(raw)
    if dimension == "ide":
        return _raw_text(raw)
    if dimension == "model":
        return _raw_text(raw)
    return _raw_text(raw)


class _Sums:
    __slots__ = ("values", "seen", "missing", "rows")

    def __init__(self) -> None:
        self.values = {key: 0 for key in _SUM_FIELDS}
        self.seen = {key: 0 for key in _SUM_FIELDS}
        self.missing = set()
        self.rows = 0

    def add(self, row: dict[str, Any]) -> None:
        self.rows += 1
        for key in _SUM_FIELDS:
            value = _finite_number(row.get(key)) if key in row else None
            if value is None:
                self.missing.add(key)
            else:
                self.values[key] += value
                self.seen[key] += 1

    def output(self) -> dict[str, Any]:
        def known(key: str) -> int | float | None:
            return self.values[key] if self.seen[key] else None

        token_fields = ("in", "out", "cr", "cc")
        token_missing = any(key in self.missing for key in token_fields)
        known_tokens = sum(self.values[key] for key in token_fields)
        complete_token_fields = all(self.seen[key] == self.rows for key in token_fields)
        input_complete = all(self.seen[key] == self.rows for key in ("in", "cr", "cc"))
        out = {
            "tokens": known_tokens if complete_token_fields else None,
            "known_tokens": known_tokens if any(self.seen[key] for key in token_fields) else None,
            "input_tokens": (self.values["in"] + self.values["cr"] + self.values["cc"])
                            if input_complete else None,
            "fresh_input_tokens": known("in"),
            "output_tokens": known("out"),
            "cache_read_tokens": known("cr"),
            "cache_write_tokens": known("cc"),
            "cache_write_5m_tokens": known("cc5"),
            "cache_write_1h_tokens": known("cc1"),
            "reasoning_tokens": known("reason"),
            "estimated_cost_usd": known("cost"),
            "requests": known("req"),
            "assistant_messages": known("asst"),
            "user_prompts": known("user"),
            "tool_calls": known("tools"),
            "active_seconds": known("active"),
            "record_rows": self.rows,
            "missing_fields": sorted(self.missing),
        }
        # Reasoning is a subset of output; cache-write tiers are subsets of cc.
        # This explicit note helps callers avoid adding those values twice.
        if token_missing and not self.missing.intersection(token_fields):
            out["tokens"] = None
        return out


def _group_key(row: dict[str, Any], dimension: str) -> str:
    value = row.get({"tool": "source"}.get(dimension, dimension))
    if dimension == "provider":
        value = row.get("provider")
    if dimension == "project" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "ide" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "device" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "tool" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "model" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "date" and not isinstance(value, str):
        value = "(unknown)"
    if dimension == "tool":
        return _safe_tool_id(value)
    if dimension in {"model", "provider", "ide"}:
        return _raw_text(value)
    return str(value)


def _normalize_group_by(arguments: dict[str, Any]) -> list[str]:
    raw = arguments.get("group_by", ["date"])
    if raw is None:
        raw = []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("group_by must be a list of date, project, tool, model, provider, ide, or device.")
    if len(raw) > len(_GROUP_FIELDS):
        raise ValueError("group_by contains too many dimensions.")
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str) or value not in _GROUP_FIELDS:
            raise ValueError("group_by values must be date, project, tool, model, provider, ide, or device.")
        if value not in out:
            out.append(value)
    return out


def _paginate(arguments: dict[str, Any]) -> tuple[int, int]:
    limit = arguments.get("limit", 50)
    offset = arguments.get("offset", 0)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= _MAX_LIMIT:
        raise ValueError(f"limit must be an integer from 1 to {_MAX_LIMIT}.")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= _MAX_OFFSET:
        raise ValueError(f"offset must be an integer from 0 to {_MAX_OFFSET}.")
    return limit, offset


def _safe_filters(context: dict[str, Any], policy: dict[str, Any]) -> dict[str, list[str]]:
    mappings = {"tools": "tool", "providers": "provider", "models": "model", "projects": "project",
                "ides": "ide", "devices": "device"}
    result: dict[str, list[str]] = {}
    for key, kind in mappings.items():
        values = context.get("filters", {}).get(key, [])
        if key == "tools":
            result[key] = [_KNOWN_TOOLS.get(value, _raw_text(value)) for value in values]
        elif key in ("projects", "devices"):
            prefix = "project-" if kind == "project" else "device-"
            result[key] = [value if re.fullmatch(re.escape(prefix) + r"[0-9a-f]{16}", value)
                           else label(kind, value, policy) for value in values]
        else:
            result[key] = [_raw_text(value) for value in values]
    return result


def result_meta(payload: dict[str, Any], selection: dict[str, Any], *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build public-safe range, freshness, coverage and limitation metadata."""
    context = selection if isinstance(selection, dict) else {}
    safe_policy = _policy_for(payload if isinstance(payload, dict) else {}, policy)
    generated = _finite_number(payload.get("generated_at")) if isinstance(payload, dict) else None
    response_built_at = None
    if generated is not None and generated > 0:
        try:
            response_built_at = datetime.fromtimestamp(generated, timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            response_built_at = None
    raw_meta = payload.get("meta") if isinstance(payload, dict) else None
    raw_meta = raw_meta if isinstance(raw_meta, dict) else {}
    last_refresh = _finite_number(raw_meta.get("last_refresh"))
    last_scan_at = None
    last_scan_at_epoch = None
    scan_age_seconds = None
    scan_timestamp_in_future = None
    if last_refresh is not None and last_refresh > 0:
        try:
            last_scan_at = datetime.fromtimestamp(last_refresh, timezone.utc).isoformat().replace("+00:00", "Z")
            last_scan_at_epoch = last_refresh
            now_epoch = datetime.now(timezone.utc).timestamp()
            scan_timestamp_in_future = last_refresh > now_epoch
            if not scan_timestamp_in_future:
                scan_age_seconds = round(max(0.0, now_epoch - last_refresh), 1)
        except (OverflowError, OSError, ValueError):
            last_scan_at = None
            last_scan_at_epoch = None
            scan_age_seconds = None
            scan_timestamp_in_future = None
    building = raw_meta.get("building") if isinstance(raw_meta.get("building"), bool) else None
    warnings = [
        "Costs are API list-price estimates and may differ from a provider invoice; subscriptions may have no per-token charge.",
        "Tokens include fresh input, output, cache reads, and cache writes. Reasoning tokens are already part of output; cache-write tiers are already part of cache writes.",
        "Active time is the dashboard's gap-capped estimate, not wall-clock duration.",
    ]
    if building is True:
        warnings.append("A dashboard scan is running; last-scan age refers to the last completed scan.")
    if scan_timestamp_in_future is True:
        warnings.append("The last-scan timestamp is in the future, so scan age is unknown.")
    if _FUTURE_WARNING in context.get("warnings", []):
        warnings.append(_FUTURE_WARNING)
    selected_sources = context.get("selected_sources", [])
    selected_sources = sorted({value for value in selected_sources if isinstance(value, str)}) if isinstance(selected_sources, list) else []
    precision_by_source = {}
    for source in selected_sources:
        safe_source = _safe_tool_id(source)
        if safe_source == "copilot":
            if context.get("exact_only") is True:
                precision, note = "recorded_exact_subrows", "Only recorded exact Copilot subrows are included; estimated chat-text contributions are excluded."
            else:
                precision, note = "mixed_recorded_and_estimated", "Copilot can mix recorded token counts with text-based estimates; exact_only keeps recorded subrows where available."
        elif safe_source == "cursor":
            precision, note = "lower_bound", "Cursor records token usage for only a small share of messages; reported tokens are a lower bound."
        elif safe_source in _EXACT_TOOLS:
            precision, note = "exact", "Token counts are recorded by the source."
        else:
            precision, note = "unknown", "This source has no declared precision classification."
        source_info = {"tool": _KNOWN_TOOLS.get(safe_source, safe_source), "precision": precision, "note": note}
        if safe_source in ("antigravity", "cursor"):
            source_usage = context.get("source_usage") if isinstance(context.get("source_usage"), dict) else {}
            quality = source_usage.get(safe_source) if isinstance(source_usage.get(safe_source), dict) else {}
            status = quality.get("usage_status")
            availability = quality.get("usage_availability")
            if isinstance(status, str) and status in _USAGE_STATES:
                source_info["usage_status"] = status
            if isinstance(availability, dict) and availability:
                source_info["usage_availability"] = {
                    field: state for field, state in availability.items()
                    if field in _USAGE_AVAILABILITY_FIELDS and isinstance(state, str) and state in _USAGE_STATES
                }
            if safe_source == "antigravity" and status == "partial":
                source_info["note"] = "Antigravity records usage partially; missing token fields are not inferred."
            elif safe_source == "antigravity" and status == "unavailable":
                source_info["note"] = "Antigravity usage is unavailable; missing token fields are not inferred."
            elif safe_source == "cursor" and status == "partial":
                source_info["note"] = "Cursor records token usage for only a small share of messages; reported tokens are a lower bound, and missing fields are not inferred."
            elif safe_source == "cursor" and status == "unavailable":
                source_info["note"] = "Cursor usage is unavailable for the selected messages; missing token fields are not inferred."
        precision_by_source[safe_source] = source_info
    source_precision = [precision_by_source[key] for key in sorted(precision_by_source)]
    for source_info in source_precision:
        if (source_info["precision"] in {"mixed_recorded_and_estimated", "lower_bound", "unknown"}
                or source_info.get("usage_status") in ("partial", "unavailable")):
            if source_info["note"] not in warnings:
                warnings.append(source_info["note"])
    supplied = context.get("coverage") if isinstance(context.get("coverage"), dict) else {}
    complete = supplied.get("records_payload_complete")
    date_range = supplied.get("available_date_range")
    if isinstance(date_range, dict):
        safe_dates = {}
        for key in ("from", "to"):
            try:
                safe_dates[key] = _as_date(date_range.get(key), key).isoformat() if date_range.get(key) is not None else None
            except ValueError:
                safe_dates[key] = None
    else:
        safe_dates = {"from": None, "to": None}
    coverage = {
        "records_in_payload": _count(supplied.get("records_in_payload")),
        "records_payload_complete": complete if isinstance(complete, bool) else None,
        "future_record_rows_excluded": _count(supplied.get("future_rows_excluded")),
        "available_date_range": safe_dates,
    }
    for key in ("sessions_visible", "sessions_total", "session_rows_unavailable_for_range", "future_sessions_excluded"):
        if key in supplied:
            value = supplied.get(key)
            coverage[key] = value if value is None else _count(value)
    if isinstance(supplied.get("sessions_truncated"), bool) or supplied.get("sessions_truncated") is None:
        if "sessions_truncated" in supplied:
            coverage["sessions_truncated"] = supplied.get("sessions_truncated")
    range_meta: dict[str, Any] = {}
    for key in ("from", "to"):
        try:
            range_meta[key] = _as_date(context.get(key), key).isoformat() if context.get(key) is not None else None
        except ValueError:
            range_meta[key] = None
    period = context.get("period")
    range_meta["period"] = period if period in {"today", "last_7_days", "last_30_days", "month_to_date", "all", "custom"} else None
    return {
        "range": range_meta,
        "response_built_at": response_built_at,
        "last_scan_at": last_scan_at,
        "last_scan_at_epoch": last_scan_at_epoch,
        "scan_age_seconds": scan_age_seconds,
        "scan_timestamp_in_future": scan_timestamp_in_future,
        "building": building,
        "coverage": coverage,
        "filters": _safe_filters(context, safe_policy) if context else {},
        "exact_only": context.get("exact_only") is True,
        "source_precision": source_precision,
        "warnings": warnings,
    }


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def query_usage(payload: dict[str, Any], arguments: dict[str, Any], *, policy: dict[str, Any] | None = None,
                today: date | str | None = None) -> dict[str, Any]:
    """Group dated usage records, returning totals over all matches and a bounded page."""
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    group_by = _normalize_group_by(arguments)
    limit, offset = _paginate(arguments)
    rows, context = select_records(payload, arguments, policy=policy, today=today)
    safe_policy = _policy_for(payload, policy)
    totals = _Sums()
    for row in rows:
        totals.add(row)
    groups: dict[tuple[str, ...], _Sums] = {}
    if group_by:
        for row in rows:
            key = tuple(_group_key(row, dimension) for dimension in group_by)
            groups.setdefault(key, _Sums()).add(row)
    rendered: list[dict[str, Any]] = []
    for key, sums in groups.items():
        item: dict[str, Any] = {}
        raw_values = dict(zip(group_by, key))
        for dimension, value in raw_values.items():
            if dimension == "date":
                item[dimension] = value
            elif dimension == "tool":
                item[dimension] = _safe_tool_id(value)
                item["tool_label"] = _KNOWN_TOOLS.get(value, _safe_tool_id(value))
            else:
                item[dimension] = _display_value(dimension, value, payload, safe_policy)
        item.update(sums.output())
        rendered.append(item)
    # Sort by the requested dimension values, with dates ascending and all other
    # dimensions in stable Unicode order. No input dictionary or hash order matters.
    rendered.sort(key=lambda row: tuple(str(row.get(dim, "")) for dim in group_by))
    page = rendered[offset:offset + limit]
    meta = result_meta(payload, context, policy=policy)
    return {
        **meta,
        "group_by": group_by,
        "rows": page,
        "totals": totals.output(),
        "total_rows": len(rendered),
        "returned_rows": len(page),
        "limit": limit,
        "offset": offset,
        "truncated": offset + len(page) < len(rendered),
        "notes": ["Totals cover every matching record before pagination."],
    }


def _date_map_sum(day_map: Any, start: str, end: str, model_filters: list[str], providers: list[str],
                  payload: dict[str, Any]) -> tuple[dict[str, int | float], int, bool]:
    """Sum a per-model day map; return fields, matching days, and missing-vector flag."""
    sums = {field: 0 for field in _VECTOR_FIELDS}
    matched_dates: set[str] = set()
    missing_vector = False
    if not isinstance(day_map, dict):
        return sums, 0, True
    for model, dates in day_map.items():
        if not isinstance(model, str):
            continue
        is_user = model == "(user)"
        if not is_user and model_filters and model not in model_filters:
            continue
        if not is_user and providers and _model_provider(payload, model) not in providers:
            continue
        if not isinstance(dates, dict):
            continue
        for day, vector in dates.items():
            if not isinstance(day, str) or day < start or day > end:
                continue
            matched_dates.add(day)
            if not isinstance(vector, (list, tuple)):
                missing_vector = True
                continue
            for idx, field in enumerate(_VECTOR_FIELDS):
                if idx >= len(vector):
                    missing_vector = True
                    continue
                value = _finite_number(vector[idx])
                if value is None:
                    missing_vector = True
                    continue
                sums[field] += value
    return sums, len(matched_dates), missing_vector


def _has_matching_model(day_map: Any, model_filters: list[str], providers: list[str], payload: dict[str, Any]) -> bool:
    if not isinstance(day_map, dict):
        return False
    for model in day_map:
        if not isinstance(model, str) or model == "(user)":
            continue
        if model_filters and model not in model_filters:
            continue
        if providers and _model_provider(payload, model) not in providers:
            continue
        return True
    return False


def _sum_days(day_map: Any, start: str, end: str) -> tuple[dict[str, int | float], int, bool]:
    sums = {field: 0 for field in _VECTOR_FIELDS}
    matched_dates: set[str] = set()
    missing_vector = False
    if not isinstance(day_map, dict):
        return sums, 0, True
    for day, vector in day_map.items():
        if not isinstance(day, str) or day < start or day > end:
            continue
        matched_dates.add(day)
        if not isinstance(vector, (list, tuple)):
            missing_vector = True
            continue
        for idx, field in enumerate(_VECTOR_FIELDS):
            if idx >= len(vector):
                missing_vector = True
                continue
            value = _finite_number(vector[idx])
            if value is None:
                missing_vector = True
                continue
            sums[field] += value
    return sums, len(matched_dates), missing_vector


def _session_alias_material(session: dict[str, Any]) -> str | None:
    source = session.get("source")
    device = session.get("device")
    native_id = session.get("rename_id")
    if not all(isinstance(v, str) and v for v in (source, device, native_id)):
        return None
    return "\0".join((source, device, native_id))


def session_alias(session: dict[str, Any], policy: dict[str, Any] | None = None) -> str | None:
    """Create an opaque alias tied to source, device, and full native session id."""
    if not isinstance(session, dict):
        return None
    material = _session_alias_material(session)
    if material is None:
        return None
    return _alias("session", material, policy if isinstance(policy, dict) else {})


def resolve_session(payload: dict[str, Any], opaque_id: Any, policy: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Resolve an alias against the bounded public session rows, without accepting raw ids."""
    if not isinstance(payload, dict) or not isinstance(opaque_id, str) or not re.fullmatch(r"session-[0-9a-f]{16}", opaque_id):
        return None
    effective = _policy_for(payload, policy)
    sessions = payload.get("sessions")
    if not isinstance(sessions, list):
        return None
    matches = [s for s in sessions if isinstance(s, dict) and session_alias(s, effective) == opaque_id]
    return matches[0] if len(matches) == 1 else None


def _session_model_summary(model_map: Any, session_model: str, session: dict[str, Any], payload: dict[str, Any],
                           model_filters: list[str], providers: list[str], start: str, end: str,
                           *, allow_whole_session: bool) -> dict[str, Any]:
    """Describe only models with positive usage in the selected session range."""
    if not isinstance(model_map, dict):
        if not allow_whole_session:
            return {
                "model": None, "models": None, "nmodels": None,
                "model_scope": "unavailable", "models_truncated": False,
            }

        # Legacy sessions can retain only their whole-session dominant model. Keep
        # that limitation explicit instead of presenting it as range-scoped detail.
        source_models = session.get("models") if isinstance(session, dict) else None
        whole_models: list[str] = []
        if isinstance(source_models, (list, tuple)):
            for raw_model in source_models:
                if not isinstance(raw_model, str) or raw_model == "(user)":
                    continue
                shown_model = _raw_text(raw_model)
                if shown_model != "(unknown)" and shown_model not in whole_models:
                    whole_models.append(shown_model)

        dominant = _raw_text(session_model)
        if session_model == "(user)" or dominant == "(unknown)":
            dominant = None
        if dominant and dominant not in whole_models:
            whole_models.insert(0, dominant)

        raw_count = session.get("nmodels") if isinstance(session, dict) else None
        known_count = raw_count if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0 else None
        if not whole_models and known_count is None and session_model == "(user)":
            return {
                "model": "No model usage", "models": [], "nmodels": 0,
                "model_scope": "whole-session", "models_truncated": False,
            }
        if not whole_models and known_count is None:
            return {
                "model": None, "models": None, "nmodels": None,
                "model_scope": "unavailable", "models_truncated": False,
            }
        shown_models = whole_models[:6]
        shown = dominant or (shown_models[0] if shown_models else None)
        return {
            "model": shown, "models": shown_models, "nmodels": known_count,
            "model_scope": "whole-session",
            "models_truncated": len(whole_models) > len(shown_models)
            or (known_count is not None and known_count > len(shown_models)),
        }

    ranked: list[tuple[int | float, int | float, int | float, str, str]] = []
    for model_name, dates in model_map.items():
        if not isinstance(model_name, str) or model_name == "(user)":
            continue
        if model_filters and model_name not in model_filters:
            continue
        if providers and _model_provider(payload, model_name) not in providers:
            continue
        if not isinstance(dates, dict):
            continue
        totals: dict[str, int | float] = {field: 0 for field in ("in", "out", "cr", "cc", "req", "asst", "cost")}
        for day, vector in dates.items():
            if not isinstance(day, str) or day < start or day > end:
                continue
            if not isinstance(vector, (list, tuple)):
                continue
            for field in totals:
                index = _VECTOR_INDEX[field]
                if index < len(vector):
                    value = _finite_number(vector[index])
                    if value is not None:
                        totals[field] += value
        tokens = totals["in"] + totals["out"] + totals["cr"] + totals["cc"]
        requests = totals["req"]
        responses = totals["asst"]
        if tokens > 0 or requests > 0 or responses > 0 or totals["cost"] > 0:
            ranked.append((tokens, requests, responses, model_name.casefold(), model_name))

    # Model names are the final ascending tie-break so the result is stable across
    # dictionary insertion order and agrees with the browser's scoped summary.
    ranked.sort(key=lambda row: (-row[0], -row[1], -row[2], row[3], row[4]))
    names = [_raw_text(row[4]) for row in ranked]
    count = len(ranked)
    return {
        "model": names[0] if names else "No model usage",
        "models": names[:6],
        "nmodels": count,
        "model_scope": "selected",
        "models_truncated": count > 6,
    }


def _cursor_undated_usage(value: Any) -> dict[str, int]:
    """Return only safe integer Cursor counters; never infer a model or a date."""
    if not isinstance(value, dict):
        return {}
    safe: dict[str, int] = {}
    for field in ("in", "out", "asst"):
        number = value.get(field)
        if type(number) is int and 0 <= number <= 10**15:
            safe[field] = number
    return safe


def _session_public_item(session: dict[str, Any], sums: dict[str, Any], payload: dict[str, Any],
                         policy: dict[str, Any], unknown: bool, missing_vector: bool,
                         model_summary: dict[str, Any]) -> dict[str, Any]:
    source = session.get("source") if isinstance(session.get("source"), str) else "(unknown)"
    safe_source = source if source in _KNOWN_TOOLS else _raw_text(source)
    project = session.get("project") if isinstance(session.get("project"), str) else "(unknown)"
    device = session.get("device") if isinstance(session.get("device"), str) else _device_id(payload) or "(unknown)"
    ide = session.get("ide") if isinstance(session.get("ide"), str) else "(unknown)"
    item = {
        "tool": safe_source,
        "tool_label": _KNOWN_TOOLS.get(source, safe_source),
        "project": label("project", project, policy),
        "device": label("device", device, policy),
        "ide": _raw_text(ide),
        **model_summary,
        "archived": session.get("archived") is True,
        "subagent": session.get("subagent") is True,
        "usage_available": not unknown,
        "range_scope": "unknown" if unknown else "daily",
    }
    if source == "cursor":
        for field in ("model_status", "response_model_status", "requested_model_status", "detail_status"):
            status = session.get(field)
            if isinstance(status, str) and status in _USAGE_STATES:
                item[field] = status
        current_model = session.get("current_model")
        if isinstance(current_model, str):
            safe_current_model = _raw_text(current_model)
            if safe_current_model != "(unknown)":
                item["current_model"] = safe_current_model
        requested_models = session.get("requested_models")
        if isinstance(requested_models, list):
            safe_requested = list(dict.fromkeys(
                safe for value in requested_models[:6]
                if (safe := _raw_text(value)) != "(unknown)"))
            if safe_requested:
                item["requested_models"] = safe_requested
        context_tokens = session.get("current_context_tokens")
        if type(context_tokens) is int and 0 <= context_tokens <= 10**15:
            item["current_context_tokens"] = context_tokens
        context_limit = session.get("current_context_limit")
        if type(context_limit) is int and 0 <= context_limit <= 10**15:
            item["current_context_limit"] = context_limit
        context_percent = _finite_number(session.get("current_context_percent"))
        if context_percent is not None and 0 <= context_percent <= 100:
            item["current_context_percent"] = context_percent
        context_asof = session.get("current_context_asof")
        if isinstance(context_asof, str) and len(context_asof) <= 80:
            try:
                datetime.fromisoformat(context_asof.replace("Z", "+00:00"))
            except ValueError:
                pass
            else:
                item["current_context_asof"] = context_asof
        undated_usage = _cursor_undated_usage(session.get("undated_usage"))
        if undated_usage:
            item["undated_usage"] = undated_usage
            item["undated_usage_scope"] = "whole-session (not filtered by date range)"
    usage_status = None
    if source in ("antigravity", "cursor"):
        status = session.get("usage_status")
        if isinstance(status, str) and status in _USAGE_STATES:
            usage_status = status
            item["usage_status"] = status
            if status == "unavailable":
                item["usage_available"] = False
        availability = session.get("usage_availability")
        if isinstance(availability, dict):
            safe_availability = {
                field: state for field, state in availability.items()
                if field in _USAGE_AVAILABILITY_FIELDS and isinstance(state, str) and state in _USAGE_STATES
            }
            if safe_availability:
                item["usage_availability"] = safe_availability
    opaque = session_alias(session, policy)
    if opaque:
        item["session"] = opaque
    start = session.get("start")
    end = session.get("end")
    if isinstance(start, str):
        item["start_date"] = _safe_text(start[:10], 10)
    if isinstance(end, str):
        item["end_date"] = _safe_text(end[:10], 10)
    if unknown:
        item.update({key: None for key in (
            "tokens", "input_tokens", "fresh_input_tokens", "output_tokens", "cache_read_tokens",
            "cache_write_tokens", "cache_write_5m_tokens", "cache_write_1h_tokens", "reasoning_tokens",
            "estimated_cost_usd", "requests", "assistant_messages", "user_prompts", "tool_calls", "active_seconds",
        )})
        item["missing_fields"] = ["daily_session_usage"]
    else:
        # All fields derive from per-day vectors selected above. A short legacy vector
        # leaves newer measures unavailable instead of assuming they were zero.
        def val(field: str) -> int | float | None:
            if missing_vector and _VECTOR_INDEX[field] >= 10:
                return None
            return sums[field]
        in_v, out_v, cr_v, cc_v = (val(x) for x in ("in", "out", "cr", "cc"))
        token_known = [x for x in (in_v, out_v, cr_v, cc_v) if x is not None]
        all_token = all(x is not None for x in (in_v, out_v, cr_v, cc_v))
        all_input = all(x is not None for x in (in_v, cr_v, cc_v))
        item.update({
            "tokens": sum(token_known) if all_token else None,
            "known_tokens": sum(token_known) if token_known else None,
            "input_tokens": in_v + cr_v + cc_v if all_input else None,
            "fresh_input_tokens": in_v,
            "output_tokens": out_v,
            "cache_read_tokens": cr_v,
            "cache_write_tokens": cc_v,
            "cache_write_5m_tokens": val("cc5"),
            "cache_write_1h_tokens": val("cc1"),
            "reasoning_tokens": val("reason"),
            "estimated_cost_usd": val("cost"),
            "requests": val("req"),
            "assistant_messages": val("asst"),
            "user_prompts": val("user"),
            "tool_calls": val("tools"),
            "active_seconds": val("active"),
            "missing_fields": ["legacy_day_vector_fields"] if missing_vector else [],
        })
    if source == "cursor" and usage_status == "unavailable":
        # Cursor placeholder counters are not evidence of a zero-token request.
        # Keep recorded message/tool activity, while marking all token-derived
        # session measures unavailable for this session.
        for field in (
            "tokens", "known_tokens", "input_tokens", "fresh_input_tokens", "output_tokens",
            "cache_read_tokens", "cache_write_tokens", "cache_write_5m_tokens",
            "cache_write_1h_tokens", "reasoning_tokens", "estimated_cost_usd",
        ):
            item[field] = None
        missing = item.get("missing_fields") if isinstance(item.get("missing_fields"), list) else []
        item["missing_fields"] = sorted(set(missing + ["cursor_token_usage"]))
    return item


def list_sessions(payload: dict[str, Any], arguments: dict[str, Any], *, policy: dict[str, Any] | None = None,
                  today: date | str | None = None) -> dict[str, Any]:
    """List sessions using only their daily/model-day contributions in the selected range."""
    if not isinstance(payload, dict) or not isinstance(arguments, dict):
        raise ValueError("payload and arguments must be objects.")
    limit, offset = _paginate(arguments)
    _, context = select_records(payload, arguments, policy=policy, today=today)
    # Session range/filter context is normalized by select_records; this function does
    # not require a matching usage record to preserve user-only or older session rows.
    safe_policy = _policy_for(payload, policy)
    filters = context["filters"]
    start, end = context["from"], context["to"]
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    local_id = _device_id(payload)
    candidates: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any], bool, bool]] = []
    unavailable = 0
    future_excluded = 0
    visible_sessions = 0
    for session in sessions:
        if not isinstance(session, dict):
            continue
        source = session.get("source")
        if not isinstance(source, str) or not source:
            continue
        if context["exact_only"] and source not in _EXACT_TOOLS and source != "copilot":
            continue
        project = session.get("project") if isinstance(session.get("project"), str) else "(unknown)"
        ide = session.get("ide") if isinstance(session.get("ide"), str) else "(unknown)"
        has_session_device = isinstance(session.get("device"), str) and bool(session.get("device"))
        device = session.get("device") if has_session_device else local_id or "(unknown)"
        model = session.get("model") if isinstance(session.get("model"), str) else "(unknown)"
        if not safe_policy["include_peers"] and (
                (local_id is not None and device != local_id)
                or (local_id is None and has_session_device)):
            continue
        visible_sessions += 1
        if filters["tools"] and source not in filters["tools"] and _KNOWN_TOOLS.get(source) not in filters["tools"]:
            continue
        if not _match_value(project, filters["projects"], "project", safe_policy, payload):
            continue
        if not _match_value(ide, filters["ides"], "ide", safe_policy, payload):
            continue
        if not _match_value(device, filters["devices"], "device", safe_policy, payload):
            continue

        exact_copilot = context["exact_only"] and source == "copilot"
        model_map_key = "exact_model_days" if exact_copilot else "model_days"
        days_key = "exact_days" if exact_copilot else "days"
        model_map = session.get(model_map_key)
        day_map = session.get(days_key)
        has_model_filters = bool(filters["models"] or filters["providers"])
        if exact_copilot and not has_model_filters and (not isinstance(day_map, dict) or not day_map):
            continue
        if exact_copilot and has_model_filters and not isinstance(model_map, dict):
            continue
        model_limited = False
        sums: dict[str, int | float]
        day_count: int
        missing_vector: bool
        unknown = False
        if has_model_filters:
            if isinstance(model_map, dict):
                if not _has_matching_model(model_map, filters["models"], filters["providers"], payload):
                    continue
                sums, day_count, missing_vector = _date_map_sum(
                    model_map, start, end, filters["models"], filters["providers"], payload)
                if not day_count:
                    continue
            else:
                # The daily total may span several models. Do not assign it to the
                # session's dominant model when filtered detail was not retained.
                session_model_matches = (model == "(user)" or model in filters["models"]) if filters["models"] else False
                provider_matches = (model == "(user)" or _model_provider(payload, model) in filters["providers"]) if filters["providers"] else False
                if not ((not filters["models"] or session_model_matches)
                        and (not filters["providers"] or provider_matches)):
                    continue
                if isinstance(day_map, dict):
                    sums, day_count, missing_vector = _sum_days(day_map, start, end)
                    if not day_count:
                        continue
                    unknown = True
                else:
                    unknown = True
                    day_count = 0
                    sums = {field: 0 for field in _VECTOR_FIELDS}
                    missing_vector = True
                model_limited = True
        elif isinstance(day_map, dict):
            sums, day_count, missing_vector = _sum_days(day_map, start, end)
            if not day_count:
                continue
            model_limited = bool(session.get("detail_limited")) or not isinstance(model_map, dict)
        elif isinstance(model_map, dict):
            sums, day_count, missing_vector = _date_map_sum(model_map, start, end, [], [], payload)
            if not day_count:
                continue
        else:
            boundary = session.get("end") or session.get("start") or ""
            boundary_date = boundary[:10] if isinstance(boundary, str) else ""
            if boundary_date > context["today"]:
                future_excluded += 1
                continue
            if boundary_date < start or boundary_date > end:
                continue
            unknown = True
            day_count = 0
            sums = {field: 0 for field in _VECTOR_FIELDS}
            missing_vector = True
            model_limited = True
        if unknown:
            unavailable += 1
        model_summary = _session_model_summary(
            model_map, model, session, payload, filters["models"], filters["providers"], start, end,
            allow_whole_session=(not context["exact_only"] and not has_model_filters),
        )
        item = _session_public_item(session, sums, payload, safe_policy, unknown, missing_vector, model_summary)
        if model_limited:
            item["model_detail_limited"] = True
        item["days_in_range"] = day_count if not unknown else None
        sort_end = session.get("end") if isinstance(session.get("end"), str) else ""
        sort_start = session.get("start") if isinstance(session.get("start"), str) else ""
        sort_alias = session_alias(session, safe_policy) or ""
        sort_key = (sort_end, sort_start, source, project, device, sort_alias)
        candidates.append((sort_key, item, sums, unknown, missing_vector))

    candidates.sort(key=lambda entry: entry[0], reverse=True)
    context["selected_sources"] = sorted({
        entry[1]["tool"] for entry in candidates if isinstance(entry[1].get("tool"), str)
    })
    context["source_usage"] = _usage_quality([entry[1] for entry in candidates])
    rendered = [entry[1] for entry in candidates]
    page = rendered[offset:offset + limit]
    # Totals are recomputed from every selected session with available daily data.
    totals = {field: 0 for field in _VECTOR_FIELDS}
    total_sessions = 0
    missing_any = False
    for _, _, sums, unknown, missing_vector in candidates:
        if unknown:
            continue
        total_sessions += 1
        missing_any = missing_any or missing_vector
        for field in _VECTOR_FIELDS:
            totals[field] += sums[field]
    total_adapter = _session_totals(totals, total_sessions, missing_any, unavailable)
    context["coverage"]["sessions_visible"] = visible_sessions
    sessions_total = payload.get("sessions_total")
    has_sessions_total = isinstance(sessions_total, int) and not isinstance(sessions_total, bool) and sessions_total >= len(sessions)
    payload_truncated = has_sessions_total and sessions_total > len(sessions)
    if safe_policy["include_peers"]:
        context["coverage"]["sessions_total"] = sessions_total if has_sessions_total else None
        context["coverage"]["sessions_truncated"] = payload_truncated if has_sessions_total else None
    else:
        context["coverage"]["sessions_total"] = None if payload_truncated else visible_sessions
        context["coverage"]["sessions_truncated"] = True if payload_truncated else (False if has_sessions_total else None)
    context["coverage"]["session_rows_unavailable_for_range"] = unavailable
    context["coverage"]["future_sessions_excluded"] = future_excluded
    meta = result_meta(payload, context, policy=policy)
    warnings = list(meta["warnings"])
    if context["coverage"]["sessions_truncated"] is True:
        warnings.append("The dashboard payload contains only the newest 2,000 sessions; older sessions may be absent.")
    if unavailable:
        warnings.append("Some matching sessions lack daily detail, so their range usage is unavailable and excluded from totals.")
    if any(entry[1].get("model_detail_limited") for entry in candidates):
        warnings.append("Some older sessions have daily totals but no per-model daily split; model-filtered totals may be unavailable.")
    return {
        **meta,
        "sessions": page,
        "totals": total_adapter,
        "total_rows": len(candidates),
        "returned_rows": len(page),
        "limit": limit,
        "offset": offset,
        "truncated": offset + len(page) < len(candidates) or context["coverage"]["sessions_truncated"] is True,
        "coverage": meta["coverage"],
        "warnings": warnings,
    }


def _session_totals(sums: dict[str, int | float], rows: int, missing_vector: bool, unavailable: int) -> dict[str, Any]:
    missing = missing_vector
    get = lambda key: (None if rows == 0 or (missing_vector and _VECTOR_INDEX[key] >= 10) else sums[key])
    in_v, out_v, cr_v, cc_v = (get(x) for x in ("in", "out", "cr", "cc"))
    token_known = [x for x in (in_v, out_v, cr_v, cc_v) if x is not None]
    token_complete = all(x is not None for x in (in_v, out_v, cr_v, cc_v))
    input_complete = all(x is not None for x in (in_v, cr_v, cc_v))
    result = {
        "tokens": sum(token_known) if token_complete else None,
        "known_tokens": sum(token_known) if token_known else None,
        "input_tokens": in_v + cr_v + cc_v if input_complete else None,
        "fresh_input_tokens": in_v,
        "output_tokens": out_v,
        "cache_read_tokens": cr_v,
        "cache_write_tokens": cc_v,
        "cache_write_5m_tokens": get("cc5"),
        "cache_write_1h_tokens": get("cc1"),
        "reasoning_tokens": get("reason"),
        "estimated_cost_usd": get("cost"),
        "requests": get("req"),
        "assistant_messages": get("asst"),
        "user_prompts": get("user"),
        "tool_calls": get("tools"),
        "active_seconds": get("active"),
        "matched_sessions_with_daily_data": rows,
        "sessions_with_unavailable_daily_data": unavailable,
        "missing_fields": ["legacy_day_vector_fields"] if missing else [],
    }
    return result


def get_catalog(payload: dict[str, Any], arguments: dict[str, Any] | None = None, *,
                policy: dict[str, Any] | None = None, today: date | str | None = None) -> dict[str, Any]:
    """Return only safe labels for dimensions present in the selected record range."""
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object.")
    rows, context = select_records(payload, arguments, policy=policy, today=today)
    safe_policy = _policy_for(payload, policy)
    values: dict[str, set[str]] = {key: set() for key in ("tools", "providers", "models", "projects", "ides", "devices")}
    for row in rows:
        source = row.get("source")
        if isinstance(source, str):
            values["tools"].add(source)
        provider = row.get("provider")
        if isinstance(provider, str):
            values["providers"].add(provider)
        model = row.get("model")
        if isinstance(model, str):
            values["models"].add(model)
        for dimension in ("projects", "ides", "devices"):
            raw = row.get({"projects": "project", "ides": "ide", "devices": "device"}[dimension])
            if isinstance(raw, str):
                values[dimension].add(raw)
    catalog: dict[str, list[dict[str, str]]] = {}
    for dimension, members in values.items():
        if dimension == "tools":
            # Source IDs are expected to be short tool slugs, but the aggregate
            # payload is still untrusted input. Do not echo malformed IDs (which
            # could be paths); collapse them to one safe unknown entry.
            rendered_by_value = {}
            for raw in sorted(members):
                safe = _safe_tool_id(raw)
                rendered_by_value[safe] = {
                    "value": safe,
                    "label": _KNOWN_TOOLS.get(safe, safe),
                }
            rendered = [rendered_by_value[value] for value in sorted(rendered_by_value)]
        else:
            rendered = []
            for raw in sorted(members):
                if dimension in ("projects", "devices"):
                    kind = "project" if dimension == "projects" else "device"
                    safe = label(kind, raw, safe_policy)
                    rendered.append({"value": safe, "label": safe})
                else:
                    safe = _raw_text(raw)
                    rendered.append({"value": safe, "label": safe})
        catalog[dimension] = rendered
    catalog_counts = {dimension: len(items) for dimension, items in catalog.items()}
    catalog_truncated = {dimension: count > _MAX_CATALOG_ITEMS for dimension, count in catalog_counts.items()}
    catalog = {dimension: items[:_MAX_CATALOG_ITEMS] for dimension, items in catalog.items()}
    meta = result_meta(payload, context, policy=policy)
    meta["catalog"] = catalog
    meta["catalog_counts"] = catalog_counts
    meta["catalog_truncated"] = catalog_truncated
    meta["catalog_item_limit"] = _MAX_CATALOG_ITEMS
    meta["supported_dimensions"] = {
        "group_by": list(_GROUP_FIELDS),
        "filters": ["tools", "models", "providers", "projects", "ides", "devices"],
    }
    meta["supported_periods"] = ["today", "last_7_days", "last_30_days", "month_to_date", "all", "custom"]
    meta["custom_range_max_days"] = _MAX_CUSTOM_DAYS
    meta["metric_definitions"] = {
        "tokens": "Fresh input + output + cache reads + cache writes; reasoning is already part of output.",
        "input_tokens": "Fresh input + cache reads + cache writes.",
        "estimated_cost_usd": "API list-price estimate, not an invoice or subscription charge.",
        "requests": "Recorded request count.",
        "assistant_messages": "Recorded assistant message count.",
        "user_prompts": "Recorded user prompt count.",
        "tool_calls": "Recorded tool-call count.",
        "active_seconds": "Dashboard gap-capped active-time estimate, not wall-clock duration.",
    }
    meta["source_record_count"] = len(rows)
    return meta


__all__ = [
    "get_catalog", "label", "list_sessions", "matches_filter", "query_usage", "resolve_session",
    "result_meta", "select_records", "session_alias",
]
