"""Authenticated, read-only MCP query dispatch for the local dashboard."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
import re
import time
import unicodedata

if __package__:
    from . import mcp_access, mcp_tools, telemetry_insights, telemetry_query
else:
    import mcp_access
    import mcp_tools
    import telemetry_insights
    import telemetry_query


MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_SOURCES = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")
_TIMELINE_KINDS = {
    "request", "user_turn", "tool", "subagent", "model", "reasoning", "session",
    "error", "compaction", "checkpoint",
}
_TIMELINE_NUMBERS = {
    "input", "output", "cache_read", "cache_write", "reasoning", "response_output", "web_search",
    "step_index", "included_steps", "estimated_tokens_used", "max_context_tokens",
    "before", "after",
}
_TIMELINE_STATUSES = {
    "unknown", "pending", "running", "done", "invalid", "cleared", "canceled",
    "error", "generating", "waiting", "queued", "interrupted",
}
_TIMELINE_USAGE_AVAILABILITY = {
    "input_output", "cache_read", "cache_write", "reasoning", "response_output",
}
_TIMELINE_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
_TIMELINE_TRIGGERS = {
    "manual", "auto", "threshold", "context_limit_retry", "memory_pressure", "model_switch", "unknown",
}
_DIAGNOSTIC_CATEGORIES = {
    "permission", "missing_file", "invalid_json", "decode", "disk_full", "parse_error",
}
_DIAGNOSTIC_STATUSES = {
    "error", "partial_error", "available", "idle", "retained_only",
}
_DIAGNOSTIC_HINTS = {
    "permission": "A source could not be read. Check its file permissions and refresh.",
    "missing_file": "A source changed during scanning. Refresh to rediscover files.",
    "invalid_json": "A source contains invalid JSON. Let the source finish writing, then refresh.",
    "decode": "A source contains text that could not be decoded. Check it and refresh.",
    "disk_full": "The local cache could not be saved. Check free space and refresh.",
    "parse_error": "A source could not be parsed. Update AgentTelemetry and refresh.",
}
_LEDGER_STATUSES = {
    "Available": "available",
    "Unreadable": "unreadable",
    "Save failed": "save_failed",
}
_MISSING_PRICE_MODEL_LIMIT = 100  # build_diagnostics() caps its returned list here
_ACCOUNT_ENTRY_LIMIT = 100
_ACCOUNT_SNAPSHOT_LIMIT = 16
_QUOTA_WINDOW_LIMIT = 12


class MCPBackendError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def error_payload(error: MCPBackendError) -> dict:
    return {"error": {"code": error.code, "message": error.message}}


def _pairs_no_duplicates(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate object key")
        value[key] = item
    return value


def _reject_constant(_value):
    raise ValueError("non-finite JSON number")


def parse_request(raw: bytes) -> dict:
    """Parse one bounded request with duplicate-key and non-finite rejection."""
    if not isinstance(raw, bytes):
        raise MCPBackendError(400, "invalid_request", "The MCP query body must be JSON.")
    if len(raw) > MAX_REQUEST_BYTES:
        raise MCPBackendError(413, "request_too_large", "The MCP query body exceeds the size limit.")
    try:
        body = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                          parse_constant=_reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise MCPBackendError(400, "invalid_json", "The MCP query body is invalid JSON.") from None
    if not isinstance(body, dict) or set(body) != {"tool", "arguments"}:
        raise MCPBackendError(400, "invalid_request", "The MCP query must contain only tool and arguments.")
    if not isinstance(body.get("tool"), str) or not isinstance(body.get("arguments"), dict):
        raise MCPBackendError(400, "invalid_request", "The MCP query requires a tool name and arguments object.")
    return body


def _authorization_failure(header, error):
    if not isinstance(header, str) or not header.startswith("Bearer "):
        return MCPBackendError(401, "unauthorized", "MCP authorization is missing or invalid.")
    message = str(error)
    if message.startswith("MCP authorization is invalid"):
        return MCPBackendError(401, "unauthorized", "MCP authorization is missing or invalid.")
    if message.startswith(("MCP access is disabled", "MCP access is bound to a different")):
        return MCPBackendError(403, "forbidden", "MCP access is disabled, revoked, or bound to another local data directory.")
    return MCPBackendError(500, "access_settings_unavailable", "MCP access settings could not be read safely. Repair them in AgentTelemetry Settings.")


def _safe_count(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None


def _finite_between(value, minimum, maximum):
    if type(value) is int:
        return minimum <= value <= maximum
    if type(value) is float:
        return math.isfinite(value) and minimum <= value <= maximum
    return False


def _iso_epoch(value):
    if _finite_between(value, 1, 253402300799):
        try:
            return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return None
    return None


def _safe_diagnostics(report):
    """Reduce diagnostics to fixed statuses/categories/counts/timestamps only."""
    report = report if isinstance(report, dict) else {}
    raw_sources = report.get("sources") if isinstance(report.get("sources"), list) else []
    sources = []
    for row in raw_sources[:200]:
        if not isinstance(row, dict):
            continue
        source = row.get("source")
        if not isinstance(source, str) or not _SOURCES.fullmatch(source):
            source = "unknown"
        status = row.get("status")
        if not isinstance(status, str) or status not in _DIAGNOSTIC_STATUSES:
            status = "unknown"
        category = row.get("error_category")
        if not isinstance(category, str) or category not in _DIAGNOSTIC_CATEGORIES:
            category = None
        item = {
            "source": source,
            "status": status,
            "discovered": _safe_count(row.get("discovered")),
            "read": _safe_count(row.get("read")),
            "errors": _safe_count(row.get("errors")),
            "last_attempt": _iso_epoch(row.get("last_attempt")),
            "last_success": _iso_epoch(row.get("last_success")),
            "error_category": category,
        }
        if category:
            item["hint"] = _DIAGNOSTIC_HINTS[category]
        elif status == "retained_only":
            item["hint"] = "Only retained history is available; new figures need readable source logs."
        sources.append(item)
    unsupported_formats = []
    raw_unsupported = report.get("unsupported_formats") if isinstance(report.get("unsupported_formats"), list) else []
    for row in raw_unsupported[:20]:
        if not isinstance(row, dict) or row.get("source") != "antigravity" or row.get("format") != "legacy_pb":
            continue
        count = _safe_count(row.get("count"))
        if count:
            unsupported_formats.append({
                "source": "antigravity", "format": "legacy_pb", "count": count,
                "hint": "Standalone Antigravity legacy .pb conversations are unsupported; file contents were not read.",
            })
    raw_ledger = report.get("ledger") if isinstance(report.get("ledger"), dict) else {}
    ledger_status = raw_ledger.get("status")
    ledger_name = ledger_status if isinstance(ledger_status, str) else ""
    ledger = {
        "status": _LEDGER_STATUSES.get(ledger_name, "unknown"),
        "issue": ledger_name in ("Unreadable", "Save failed"),
    }
    if ledger_name == "Unreadable":
        ledger["hint"] = "The original cache was preserved. Restore a valid backup in Settings."
    elif ledger_name == "Save failed":
        ledger["hint"] = "Check disk space and permissions, then refresh."
    raw_issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    categories = {}
    for item in raw_issues[:200]:
        category = item.get("category") if isinstance(item, dict) else None
        if isinstance(category, str) and category in _DIAGNOSTIC_CATEGORIES:
            categories[category] = categories.get(category, 0) + 1
    raw_status = report.get("status")
    status = raw_status if isinstance(raw_status, str) and raw_status in _DIAGNOSTIC_STATUSES else "unknown"
    missing = report.get("missing_prices")
    missing_models = []
    if isinstance(missing, list):
        seen_models = set()
        for raw_model in missing[:1000]:
            model = _safe_text(raw_model)
            if model and model not in seen_models and len(missing_models) < _MISSING_PRICE_MODEL_LIMIT:
                seen_models.add(model)
                missing_models.append(model)
    missing_count = min(len(missing), 1_000_000) if isinstance(missing, list) else 0
    missing_source_capped = isinstance(missing, list) and len(missing) >= _MISSING_PRICE_MODEL_LIMIT
    missing_models_truncated = (
        isinstance(missing, list)
        and (missing_source_capped or len(missing_models) < len(missing))
    )
    return {
        "status": status,
        "generation": _safe_count(report.get("generation")),
        "started_at": _iso_epoch(report.get("started_at")),
        "finished_at": _iso_epoch(report.get("finished_at")),
        "discovered": _safe_count(report.get("discovered")),
        "read": _safe_count(report.get("read")),
        "errors": _safe_count(report.get("errors")),
        "sources": sources,
        "unsupported_formats": unsupported_formats,
        "issue_categories": categories,
        "ledger": ledger,
        "backup_issue": bool(report.get("backup_error")),
        # The source itself caps its model list at 100. Keep the legacy count as
        # an observed count and label its completeness instead of implying it is
        # the full total when the source cap may have been reached.
        "missing_price_model_count": missing_count,
        "missing_price_models_observed_count": missing_count,
        "missing_price_model_count_kind": "observed_lower_bound" if missing_source_capped else "observed",
        "missing_price_models": missing_models,
        "missing_price_models_truncated": missing_models_truncated,
    }


def _safe_text(value, limit=120):
    if not isinstance(value, str):
        return None
    value = " ".join("".join(ch for ch in value if not unicodedata.category(ch).startswith("C")).split())
    value = value[:limit].strip()
    if not value:
        return None
    # Model/tool labels may be derived from log data; do not echo obvious local paths.
    if (value.startswith(("/", "\\", "~/", "~\\"))
            or re.search(r"(?:^|\s)[A-Za-z]:[\\/].+", value)
            or re.search(r"(?:^|\s)/(?:Users|home|private|tmp|var|opt)/", value)):
        return None
    return value


def _safe_timestamp(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _safe_timeline(raw):
    raw = raw if isinstance(raw, dict) else {}
    events = []
    source_events = raw.get("events") if isinstance(raw.get("events"), list) else []
    for event in source_events[:400]:
        if not isinstance(event, dict):
            continue
        kind = event.get("kind")
        timestamp = _safe_timestamp(event.get("timestamp"))
        if not isinstance(kind, str) or kind not in _TIMELINE_KINDS or timestamp is None:
            continue
        item = {"kind": kind, "timestamp": timestamp}
        for field in ("label", "model"):
            safe = _safe_text(event.get(field))
            if safe is not None:
                item[field] = safe
        for field in ("subagent_role", "subagent_type"):
            safe = _safe_text(event.get(field), 80)
            if safe is not None:
                item[field] = safe
        effort = event.get("effort")
        if isinstance(effort, str) and effort in _TIMELINE_EFFORTS:
            item["effort"] = effort
        if isinstance(event.get("intent_only"), bool):
            item["intent_only"] = event["intent_only"]
        trigger = event.get("trigger")
        if isinstance(trigger, str) and trigger in _TIMELINE_TRIGGERS:
            item["trigger"] = trigger
        status = event.get("status")
        if kind == "tool" and isinstance(status, str) and status in _TIMELINE_STATUSES:
            item["status"] = status
        availability = event.get("usage_availability")
        if isinstance(availability, dict):
            safe_availability = {
                field: value for field, value in availability.items()
                if field in _TIMELINE_USAGE_AVAILABILITY and value in ("recorded", "partial", "unavailable")
            }
            if safe_availability:
                item["usage_availability"] = safe_availability
        for field in _TIMELINE_NUMBERS:
            value = event.get(field)
            if _finite_between(value, 0, 10**15):
                item[field] = value
        events.append(item)
    detail = raw.get("detail")
    if not isinstance(detail, str) or detail not in (
            "source log", "partial", "unsupported_tool", "remote_source",
            "archived_source", "source_unavailable"):
        detail = "source_unavailable"
    return {
        "available": raw.get("available") is True,
        "events": events,
        "returned_events": len(events),
        "truncated": raw.get("truncated") is True or len(source_events) > 400,
        "detail": detail,
    }


def _quota_snapshot(source, snapshot, identity_stale, stale_after, now_epoch):
    if not isinstance(snapshot, dict):
        return None
    origins = {
        "Codex account API": "codex_cli_lookup",
        "Claude status-line capture": "claude_status_line_capture",
        "Local Codex log": "local_codex_log_snapshot",
    }
    origin = snapshot.get("origin")
    observed_value = snapshot.get("observed_at")
    observed = _iso_epoch(observed_value)
    observed_valid = observed is not None
    observed_fresh = (
        observed_valid
        and observed_value <= now_epoch
        and now_epoch - observed_value <= stale_after
    )
    # Identity can be intentionally unbound for log and status-line observations.
    # Keep that uncertainty visible without confusing it with observation age;
    # live account lookup failures already arrive with explicit stale flags.
    snapshot_stale = not observed_fresh or snapshot.get("stale") is not False
    windows = []
    raw_windows = snapshot.get("windows") if isinstance(snapshot.get("windows"), list) else []
    for window in raw_windows[:_QUOTA_WINDOW_LIMIT]:
        if not isinstance(window, dict):
            continue
        minutes = window.get("window_minutes")
        if not _finite_between(minutes, 1, 52_560_000):
            continue
        used = window.get("used_percent")
        if not _finite_between(used, 0, 100):
            used = None
        label = "5-hour limit" if minutes == 300 else "Weekly limit" if minutes == 10080 else "Other limit"
        reset_value = window.get("resets_at")
        reset = _iso_epoch(reset_value)
        reset_valid = reset is not None
        expired = bool(reset_valid and reset_value <= now_epoch)
        stale = snapshot_stale or window.get("stale") is not False or not reset_valid or expired
        safe_window = {
            "label": label,
            "window_minutes": minutes,
            "used_percent": used,
            "resets_at": reset,
            "stale": stale,
            "expired": expired,
        }
        if used is not None and not stale:
            safe_window["remaining_percent"] = 100 - used
        windows.append(safe_window)
    return {
        "source": source,
        "observation_type": origins.get(origin, "saved_local_observation") if isinstance(origin, str) else "saved_local_observation",
        "observed_at": observed,
        "stale": snapshot_stale,
        "identity_stale": identity_stale is True,
        "unbound": True,
        "windows": windows,
        "windows_returned": len(windows),
        "windows_observed_count": _safe_count(len(raw_windows)),
        "windows_truncated": len(raw_windows) > _QUOTA_WINDOW_LIMIT,
    }


def _safe_account_limits(payload, arguments):
    payload = payload if isinstance(payload, dict) else {}
    raw_entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
    selected = arguments.get("source")
    stale_after = payload.get("stale_after_seconds")
    if type(stale_after) is not int or not 1 <= stale_after <= 86400:
        stale_after = 300
    stale_after = min(stale_after, 300)
    now_epoch = time.time()
    # Filter first, then apply the profile-entry cap. Looking at one extra
    # matching entry is enough to disclose truncation without retaining an
    # unbounded intermediate list.
    entries = []
    entries_truncated = False
    for entry in raw_entries:
        if not isinstance(entry, dict):
            continue
        source = entry.get("source")
        if source not in ("codex", "claude") or (selected and selected != source):
            continue
        if len(entries) >= _ACCOUNT_ENTRY_LIMIT:
            entries_truncated = True
            break
        entries.append(entry)

    items = []
    snapshot_observations_returned = 0
    snapshot_observations_truncated = False
    quota_windows_returned = 0
    quota_windows_truncated = False
    for entry in entries:
        source = entry["source"]
        snapshots = entry.get("snapshots") if isinstance(entry.get("snapshots"), list) else []
        if not snapshots:
            items.append({
                "source": source,
                "observation_type": "unavailable",
                "observed_at": None,
                "stale": True,
                "identity_stale": entry.get("identity_stale") is not False,
                "unbound": True,
                "windows": [],
            })
            continue
        if len(snapshots) > _ACCOUNT_SNAPSHOT_LIMIT:
            snapshot_observations_truncated = True
        for snapshot in snapshots[:_ACCOUNT_SNAPSHOT_LIMIT]:
            item = _quota_snapshot(source, snapshot, entry.get("identity_stale"), stale_after, now_epoch)
            if item is not None:
                items.append(item)
                snapshot_observations_returned += 1
                quota_windows_returned += item["windows_returned"]
                quota_windows_truncated = quota_windows_truncated or item["windows_truncated"]
    truncated = entries_truncated or snapshot_observations_truncated or quota_windows_truncated
    return {
        "entries": items,
        "source_entries_returned": len(entries),
        "source_entries_limit": _ACCOUNT_ENTRY_LIMIT,
        "source_entries_truncated": entries_truncated,
        "snapshot_observations_returned": snapshot_observations_returned,
        "snapshot_limit_per_source_entry": _ACCOUNT_SNAPSHOT_LIMIT,
        "snapshot_observations_truncated": snapshot_observations_truncated,
        "quota_windows_returned": quota_windows_returned,
        "window_limit_per_observation": _QUOTA_WINDOW_LIMIT,
        "quota_windows_truncated": quota_windows_truncated,
        "truncated": truncated,
        "stale_after_seconds": stale_after,
        "refresh_performed": False,
        "scope_note": (
            "These are account-wide saved observations, not usage-derived allowances. "
            "Identity is unbound and may differ across observations; never sum across entries, "
            "devices, or windows. Remaining percent is shown only for a fresh known window. "
            "Expired windows are stale and are never refilled or reset by this tool."
        ),
    }


def _timeline(payload, arguments, policy, builder):
    selector = arguments.get("session")
    selected = telemetry_query.resolve_session(payload, selector, policy=policy)
    if not isinstance(selected, dict):
        raise MCPBackendError(400, "invalid_session", "The session selector is invalid or no longer available. List sessions and try again.")
    source = selected.get("source")
    device = selected.get("device")
    native_id = selected.get("rename_id")
    if (not isinstance(source, str) or not isinstance(device, str)
            or not isinstance(native_id, str) or not native_id):
        raise MCPBackendError(400, "invalid_session", "The session selector is invalid or no longer available. List sessions and try again.")
    local_device = telemetry_query._device_id(payload)
    if device != local_device:
        if policy.get("include_peers") is not True:
            raise MCPBackendError(400, "invalid_session", "The session selector is invalid or no longer available. List sessions and try again.")
        return _safe_timeline({"available": False, "events": [], "detail": "remote_source"})
    if selected.get("archived") is True:
        return _safe_timeline({"available": False, "events": [], "detail": "archived_source"})
    if source not in ("claude", "claude-desktop", "codex", "antigravity"):
        return _safe_timeline({"available": False, "events": [], "detail": "unsupported_tool"})
    try:
        result = builder(device, source, native_id,
                         date_from=arguments.get("from"), date_to=arguments.get("to"))
    except ValueError:
        return _safe_timeline({"available": False, "events": [], "detail": "source_unavailable"})
    if isinstance(result, dict) and result.get("available") is not True:
        raw_detail = result.get("detail")
        if raw_detail == "Source events are available on the original device.":
            reason = "remote_source"
        elif raw_detail == "The source log is unavailable; retained daily detail is unchanged.":
            reason = "source_unavailable"
        elif raw_detail == "The original source log could not be verified.":
            reason = "source_unavailable"
        else:
            reason = "source_unavailable"
        result = {"available": False, "events": [], "truncated": result.get("truncated") is True,
                  "detail": reason}
    return _safe_timeline(result)


def _dispatch(tool, arguments, policy, builders):
    if tool == "get_data_health":
        return _safe_diagnostics(builders["health"]())
    if tool == "get_subscription_limits":
        return _safe_account_limits(builders["accounts"](), arguments)
    if tool == "get_session_timeline":
        payload = builders["payload"]()
        return _timeline(payload, arguments, policy, builders["timeline"])
    if tool in ("get_usage", "compare_usage", "get_insights", "list_sessions", "get_catalog"):
        payload = builders["payload"]()
        if tool == "get_usage":
            return telemetry_query.query_usage(payload, arguments, policy=policy)
        if tool == "compare_usage":
            return telemetry_insights.compare_usage(payload, arguments, policy=policy)
        if tool == "get_insights":
            return telemetry_insights.get_insights(payload, arguments, policy=policy)
        if tool == "list_sessions":
            return telemetry_query.list_sessions(payload, arguments, policy=policy)
        return telemetry_query.get_catalog(payload, arguments, policy=policy)
    raise MCPBackendError(400, "invalid_tool", "Unknown AgentTelemetry tool.")


def execute_query(raw_body: bytes, authorization_header, backend_url: str, data_dir,
                  builders: dict, *, authorize=mcp_access.authorize) -> dict:
    """Authorize, run one in-memory query, then re-authorize before returning it."""
    if not isinstance(authorization_header, str) or not authorization_header.startswith("Bearer "):
        raise MCPBackendError(401, "unauthorized", "MCP authorization is missing or invalid.")
    try:
        policy = authorize(backend_url, data_dir, authorization_header)
    except mcp_access.AccessError as error:
        raise _authorization_failure(authorization_header, error) from None
    except Exception:
        raise MCPBackendError(500, "access_settings_unavailable", "MCP access settings could not be read safely. Repair them in AgentTelemetry Settings.") from None

    body = parse_request(raw_body)
    tool = body["tool"]
    try:
        arguments = mcp_tools.validate_arguments(tool, body["arguments"])
    except mcp_tools.ToolArgumentError as error:
        raise MCPBackendError(400, "invalid_params", str(error)) from None
    is_building = builders.get("is_building")
    try:
        if callable(is_building) and is_building():
            raise MCPBackendError(503, "building", "The local dashboard is refreshing. Retry when it is ready.")
        result = _dispatch(tool, arguments, policy, builders)
    except MCPBackendError:
        raise
    except (ValueError, TypeError, OverflowError):
        raise MCPBackendError(400, "invalid_params", "The local query could not be validated. Check the tool arguments.") from None
    except Exception:
        raise MCPBackendError(500, "query_failed", "The local telemetry query failed. Check AgentTelemetry's local status and retry.") from None
    if not isinstance(result, dict):
        raise MCPBackendError(500, "query_failed", "The local telemetry query returned an invalid result. Retry or check its local status.")
    try:
        # A changed scope rotates the credential. Discard results computed using
        # an old policy if the user revoked access while the query was running.
        authorize(backend_url, data_dir, authorization_header)
    except mcp_access.AccessError:
        raise MCPBackendError(403, "forbidden", "MCP access changed while this query was running. Retry after reviewing Settings.") from None
    except Exception:
        raise MCPBackendError(500, "access_settings_unavailable", "MCP access settings could not be rechecked safely. Retry or repair them in Settings.") from None
    safe = dict(result)
    safe["privacy_warning"] = mcp_access.WARNING
    try:
        serialized = json.dumps(safe, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise MCPBackendError(500, "query_failed", "The local telemetry query returned an invalid result. Retry or check its local status.") from None
    if len(serialized.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise MCPBackendError(413, "result_too_large", "The local result exceeds the response size limit. Narrow the query and retry.")
    return safe


def client_config(backend_url: str, python_executable: str, server_script: str) -> dict:
    backend = mcp_access.canonical_backend_url(backend_url)
    python_path = os.path.abspath(os.fspath(python_executable))
    if os.path.basename(python_path).lower() == "pythonw.exe":
        console_path = os.path.join(os.path.dirname(python_path), "python.exe")
        if os.path.isfile(console_path):
            python_path = console_path
    script_path = os.path.abspath(os.fspath(server_script))
    return {"mcpServers": {"agent-telemetry": {
        "command": python_path,
        "args": [script_path, "--backend", backend],
    }}}


def settings_response(backend_url: str, data_dir, python_executable: str, server_script: str) -> dict:
    backend = mcp_access.canonical_backend_url(backend_url)
    return {
        **mcp_access.status(backend, data_dir),
        "client_config": client_config(backend, python_executable, server_script),
    }
