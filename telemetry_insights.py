"""Reusable, offline comparison and optimization analysis for AgentTelemetry.

The functions in this module operate on the same in-memory payload as the
dashboard and MCP. They do not read the ledger, scan files, call providers, or
mutate the supplied payload. Estimates describe possible areas to investigate;
they are not causal findings or promised savings.
"""

import math
import re
from datetime import date, timedelta


METRICS = ("tokens", "cost", "messages", "time")
DIMENSIONS = ("project", "tool", "model", "provider", "ide", "device", "token_type")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EXACT_SOURCES = frozenset(("claude", "codex", "claude-desktop", "opencode", "hermes", "openclaw", "antigravity"))
_TOOL_LABELS = {
    "claude": "Claude Code", "codex": "Codex", "copilot": "Copilot",
    "cursor": "Cursor", "claude-desktop": "Claude Desktop", "opencode": "OpenCode",
    "hermes": "Hermes Agent", "openclaw": "OpenClaw",
    "antigravity": "Antigravity",
}
_TOKEN_PART_LABELS = {
    "in": "Input", "out": "Output", "cr": "Cache reads", "cc": "Cache writes",
    "other": "Unsplit / other cost",
}
_DAY_FIELDS = ("cost", "in", "out", "cr", "cc", "asst", "user", "tools", "prem",
               "active", "req", "cc5", "cc1", "reason", "ws")
_NUMERIC_FIELDS = frozenset((
    "cost", "in", "out", "cr", "cc", "asst", "user", "tools", "prem", "active", "req",
    "cc5", "cc1", "reason", "ws", "tok", "n", "sessions", "calls", "edits", "oneshot",
    "retry_cost", "edit_cost", "bytes", "sent", "desc_chars", "open_ctx", "side", "subagents",
    "rereads", "junk", "reread_tok", "junk_tok", "reads",
))
_VECTOR_FIELDS = {name: index for index, name in enumerate(_DAY_FIELDS)}
_CACHE_CAVEAT = "Savings estimates are heuristic and overlap with other findings; do not add them together."
_ESTIMATE_CAVEAT = "Costs are API list-price estimates, not subscription charges or guaranteed savings."
_MAX_EVIDENCE_ROWS = 20


def _query_module():
    if __package__:
        from . import telemetry_query
    else:
        import telemetry_query
    return telemetry_query


def _query_api():
    # Lazy import keeps this calculation module importable on its own in focused
    # tests; the application supplies the shared selection/privacy implementation.
    query = _query_module()
    return query.select_records, query.label, query.result_meta


def _matches_filter(kind, raw, requested, *, policy=None, payload=None):
    return _query_module().matches_filter(kind, raw, requested, policy=policy, payload=payload)


def _number(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return out if math.isfinite(out) else default


def _integer(value, default=0):
    n = _number(value, default)
    return int(n) if n >= 0 else default


def _valid_day(value):
    if not isinstance(value, str) or not _DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _date(value):
    if not _valid_day(value):
        return None
    return date.fromisoformat(value)


def _safe_meta(payload, context, policy):
    _, _, result_meta = _query_api()
    # `context` is deliberately internal: result_meta is the only route from it
    # to public scope and applies the same alias/peer policy as ordinary queries.
    return result_meta(payload, context, policy=policy)


def _select(payload, arguments, policy, today):
    select_records, _, _ = _query_api()
    rows, context = select_records(payload, arguments, policy=policy, today=today)
    return rows, context


def _metric_value(row, metric):
    if metric == "cost":
        return _number(row.get("cost"))
    if metric == "messages":
        return _number(row.get("asst"))
    if metric == "time":
        return _number(row.get("active"))
    return sum(_number(row.get(k)) for k in ("in", "out", "cr", "cc"))


def _price_tuple(payload, model, day):
    """Get the recorded list-price tuple in force on a row's local date."""
    histories = payload.get("pricing_history") or {}
    history = histories.get(model) or ()
    if day:
        for item in history:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            until, prices = item[0], item[1]
            if isinstance(until, str) and day <= until:
                return _valid_price_tuple(prices)
    pricing = payload.get("pricing") or {}
    prices = pricing.get(model)
    if prices is None:
        prices = (payload.get("prices") or {}).get(model)
    return _valid_price_tuple(prices)


def _valid_price_tuple(value):
    if not isinstance(value, (list, tuple)) or len(value) < 5:
        return (0.0, 0.0, 0.0, 0.0, 0.0)
    return tuple(max(0.0, _number(value[i])) for i in range(5))


def _token_cost_parts(row, payload):
    """Return dated per-token-type costs, including recorded long-context subsets."""
    model = row.get("model")
    p = _price_tuple(payload, model, row.get("date"))
    long_p = _valid_price_tuple((payload.get("pricing_long_context") or {}).get(model))
    if not any(long_p):
        long_p = p

    def part(field, count, base, high):
        long_count = _number(row.get("lc_" + field))
        return (_number(count) * base + long_count * (high - base)) / 1_000_000.0

    cc5 = _number(row.get("cc5"))
    cc1 = _number(row.get("cc1"))
    if not cc5 and not cc1:
        cc5 = _number(row.get("cc"))
    cw5 = p[2] or p[0]
    high_cw5 = long_p[2] or long_p[0]
    cw1 = p[3] or p[2] or p[0]
    high_cw1 = long_p[3] or long_p[2] or long_p[0]
    return {
        "in": part("in", row.get("in"), p[0], long_p[0]),
        "out": part("out", row.get("out"), p[1], long_p[1]),
        "cr": part("cr", row.get("cr"), p[4], long_p[4]),
        "cc": part("cc5", cc5, cw5, high_cw5) + part("cc1", cc1, cw1, high_cw1),
    }


def _cost_parts_for_row(row, payload):
    total = _number(row.get("cost"))
    if not total:
        return {}
    parts = _token_cost_parts(row, payload)
    subtotal = sum(parts.values())
    # Logged costs, discounts, web-search fees, and unavailable rates cannot be
    # assigned to token classes with evidence. Preserve them as one remainder.
    if not subtotal or subtotal > total + 0.000002:
        return {"other": total}
    remainder = total - subtotal
    if remainder < 0:
        largest = max(parts, key=parts.get)
        parts[largest] += remainder
    elif remainder:
        parts["other"] = remainder
    return {key: value for key, value in parts.items() if value}


def _group_key(row, dimension, metric, payload):
    if dimension == "token_type":
        if metric == "cost":
            return _cost_parts_for_row(row, payload)
        return {key: _number(row.get(field)) for key, field in (
            ("in", "in"), ("out", "out"), ("cr", "cr"), ("cc", "cc")) if _number(row.get(field))}
    if dimension == "tool":
        return str(row.get("source") or "(unknown)"), "tool"
    if dimension == "project":
        return str(row.get("project") or "(unknown)"), "project"
    if dimension == "model":
        return str(row.get("model") or "(unknown)"), "model"
    if dimension == "provider":
        model = str(row.get("model") or "")
        provider = (payload.get("model_vendor") or {}).get(model) or "Other"
        return str(provider), "provider"
    if dimension == "ide":
        return str(row.get("ide") or "(unknown)"), "ide"
    local_id = ((payload.get("device") or {}).get("id") or "local")
    return str(row.get("device") or local_id), "device"


def _display_policy(payload, policy):
    effective = dict(policy or {})
    names = {}
    local = payload.get("device") if isinstance(payload.get("device"), dict) else {}
    local_id = local.get("id")
    if isinstance(local_id, str) and isinstance(local.get("name"), str):
        names[local_id] = local["name"]
    devices = payload.get("devices")
    if isinstance(devices, list):
        for item in devices:
            if not isinstance(item, dict):
                continue
            device_id, name = item.get("id"), item.get("name")
            if isinstance(device_id, str) and isinstance(name, str) and (
                    effective.get("include_peers") is True or device_id == local_id):
                names[device_id] = name
    effective["_device_names"] = names
    return effective


def _display_label(raw, kind, policy, payload=None):
    _, label, _ = _query_api()
    if kind == "tool":
        return _TOOL_LABELS.get(raw, label("tool", raw, policy=policy))
    return label(kind, raw, policy=_display_policy(payload or {}, policy))


def _safe_tool_label(raw):
    if not isinstance(raw, str) or not raw:
        return "(unknown)"
    if raw in _TOOL_LABELS:
        return raw
    _, label, _ = _query_api()
    return label("tool", raw)


def _public_group_key(raw, kind, policy):
    """Keep project/device identifiers aliased even when display names are allowed."""
    if kind in ("project", "device"):
        alias_policy = dict(policy or {})
        alias_policy["include_names"] = False
        _, label, _ = _query_api()
        return label(kind, raw, policy=alias_policy)
    _, label, _ = _query_api()
    return label(kind, raw, policy=policy)


def _sum_by_dimension(rows, metric, dimension, payload, policy):
    grouped = {}
    labels = {}
    if dimension == "token_type":
        for row in rows:
            parts = _group_key(row, dimension, metric, payload)
            for key, value in parts.items():
                grouped[key] = grouped.get(key, 0.0) + value
                labels[key] = _TOKEN_PART_LABELS.get(key, key)
    else:
        for row in rows:
            raw, kind = _group_key(row, dimension, metric, payload)
            grouped[raw] = grouped.get(raw, 0.0) + _metric_value(row, metric)
            if raw not in labels:
                labels[raw] = _display_label(raw, kind, policy, payload)
    return grouped, labels


def _empty_comparison(reason, metric, dimension):
    return {
        "available": False, "reason": reason, "metric": metric, "dimension": dimension,
        "current": None, "baseline": None, "delta": None, "percent_change": None,
        "contributions": [], "reconciles": False, "caveats": [_CACHE_CAVEAT],
    }


def _missing_metric_fields(rows, fields):
    missing = set()
    for row in rows:
        for field in fields:
            if field not in row or isinstance(row.get(field), bool):
                missing.add(field)
                continue
            try:
                value = float(row[field])
            except (TypeError, ValueError, OverflowError):
                missing.add(field)
                continue
            if not math.isfinite(value):
                missing.add(field)
    return sorted(missing)


def compare_usage(payload, arguments, *, policy=None, today=None):
    """Compare a filtered current range with the previous or explicit baseline.

    `percent_change` is a ratio (0.25 means 25%); it is `None` when the baseline
    is zero. Contribution rows are a complete decomposition and reconcile via
    the returned `Other` row when the requested limit omits small dimensions.
    """
    arguments = dict(arguments or {})
    metric = arguments.get("metric", "tokens")
    dimension = arguments.get("dimension", "project")
    if metric not in METRICS:
        raise ValueError("metric must be tokens, cost, messages, or time")
    if dimension not in DIMENSIONS:
        raise ValueError("dimension must be project, tool, model, provider, ide, device, or token_type")
    if dimension == "token_type" and metric not in ("tokens", "cost"):
        raise ValueError("token_type contributions are available only for tokens and cost")
    try:
        limit = int(arguments.get("limit", 50))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("limit must be an integer from 1 to 500")
    limit = max(1, min(500, limit))

    current_rows, current_context = _select(payload, arguments, policy, today)
    current_from = current_context.get("from")
    current_to = current_context.get("to")
    if not (_valid_day(current_from) and _valid_day(current_to)):
        return _empty_comparison("The current date range is unavailable.", metric, dimension)

    explicit_from = arguments.get("baseline_from")
    explicit_to = arguments.get("baseline_to")
    if bool(explicit_from) != bool(explicit_to):
        raise ValueError("baseline_from and baseline_to must be supplied together")
    if explicit_from:
        if not (_valid_day(explicit_from) and _valid_day(explicit_to)):
            raise ValueError("baseline_from and baseline_to must be YYYY-MM-DD dates")
        base_from, base_to = explicit_from, explicit_to
        if base_from > base_to:
            raise ValueError("baseline_from must be on or before baseline_to")
    else:
        period = str(current_context.get("period") or arguments.get("period") or "").lower()
        if period in ("all", "all_time", "all-time"):
            return _empty_comparison(
                "All history has no automatic preceding range; provide baseline_from and baseline_to.",
                metric, dimension)
        start, end = date.fromisoformat(current_from), date.fromisoformat(current_to)
        days = (end - start).days + 1
        base_to = (start - timedelta(days=1)).isoformat()
        base_from = (start - timedelta(days=days)).isoformat()

    baseline_arguments = dict(arguments)
    baseline_arguments.pop("period", None)
    baseline_arguments.pop("baseline_from", None)
    baseline_arguments.pop("baseline_to", None)
    baseline_arguments["from"] = base_from
    baseline_arguments["to"] = base_to
    baseline_rows, baseline_context = _select(payload, baseline_arguments, policy, today)

    required = {
        "tokens": ("in", "out", "cr", "cc"),
        "cost": ("cost",), "messages": ("asst",), "time": ("active",),
    }[metric]
    missing = sorted(set(_missing_metric_fields(current_rows, required)
                         + _missing_metric_fields(baseline_rows, required)))
    if missing:
        result = _empty_comparison(
            "Selected usage rows are missing fields required for this metric.", metric, dimension)
        result["missing_fields"] = missing
        result["current"] = {"from": current_from, "to": current_to,
                              "scope": _safe_meta(payload, current_context, policy)}
        result["baseline"] = {"from": baseline_context.get("from", base_from),
                              "to": baseline_context.get("to", base_to),
                              "scope": _safe_meta(payload, baseline_context, policy),
                              "explicit": bool(explicit_from)}
        return result

    current_total = sum(_metric_value(row, metric) for row in current_rows)
    baseline_total = sum(_metric_value(row, metric) for row in baseline_rows)
    delta = current_total - baseline_total
    percent_change = (delta / abs(baseline_total)) if baseline_total else None

    cur_groups, cur_labels = _sum_by_dimension(current_rows, metric, dimension, payload, policy)
    base_groups, base_labels = _sum_by_dimension(baseline_rows, metric, dimension, payload, policy)
    all_keys = set(cur_groups) | set(base_groups)
    ordered = sorted(all_keys, key=lambda key: (
        -abs(cur_groups.get(key, 0.0) - base_groups.get(key, 0.0)), str(key).casefold(), str(key)))
    rows = []
    for key in ordered:
        current = cur_groups.get(key, 0.0)
        baseline = base_groups.get(key, 0.0)
        group_kind = "project" if dimension == "project" else "device" if dimension == "device" else dimension
        rows.append({"key": _public_group_key(key, group_kind, policy),
                     "label": cur_labels.get(key, base_labels.get(key, str(key))),
                     "current": current, "baseline": baseline, "delta": current - baseline})
    omitted = rows[limit:]
    visible = rows[:limit]
    if omitted:
        visible.append({
            "key": "__other__", "label": "Other ({} dimensions)".format(len(omitted)),
            "current": sum(row["current"] for row in omitted),
            "baseline": sum(row["baseline"] for row in omitted),
            "delta": sum(row["delta"] for row in omitted), "other": True,
        })
    reconciled_delta = sum(row["delta"] for row in visible)
    reconciles = math.isclose(reconciled_delta, delta, rel_tol=1e-9, abs_tol=1e-8)

    from_meta = _safe_meta(payload, current_context, policy)
    baseline_meta = _safe_meta(payload, baseline_context, policy)
    coverage = from_meta.get("coverage") or {}
    coverage_warnings = list(from_meta.get("warnings") or ())
    baseline_coverage = (baseline_meta.get("coverage") or {})
    date_range = baseline_coverage.get("available_date_range") or {}
    base_floor = date_range.get("from") if _valid_day(date_range.get("from")) else None
    if base_floor and base_from < base_floor:
        coverage_warnings.append("The baseline starts before the earliest available usage history.")
    if not baseline_rows:
        coverage_warnings.append("No matching usage rows were recorded in the baseline range.")
    if coverage.get("records_payload_complete") is False:
        coverage_warnings.append("The selected record payload is incomplete, so totals may be partial.")
    coverage_warnings.extend(baseline_meta.get("warnings") or ())
    today_value = today.isoformat() if isinstance(today, date) else today
    if not _valid_day(today_value):
        today_value = current_context.get("today")
    is_current_partial = bool(_valid_day(today_value) and current_to >= today_value)
    if is_current_partial:
        coverage_warnings.append("The current range includes today and may still be in progress.")

    return {
        "available": True, "metric": metric, "dimension": dimension,
        "current": {"from": current_from, "to": current_to, "total": current_total,
                    "scope": from_meta},
        "baseline": {"from": baseline_context.get("from", base_from),
                     "to": baseline_context.get("to", base_to), "total": baseline_total,
                     "scope": baseline_meta, "explicit": bool(explicit_from)},
        "delta": delta, "percent_change": percent_change,
        "contributions": visible, "contribution_count": len(rows),
        "omitted_contribution_count": len(omitted), "reconciles": reconciles,
        "partial_current_period": is_current_partial,
        "coverage": coverage, "caveats": [_CACHE_CAVEAT, _ESTIMATE_CAVEAT],
        "coverage_warnings": list(dict.fromkeys(coverage_warnings)),
    }


def _filters(context):
    value = context.get("filters") or {}
    return {k: set(v or ()) for k, v in value.items() if isinstance(v, (list, tuple, set))}


def _pass_source(source, context):
    filters = _filters(context)
    tools = filters.get("tools")
    if tools and source not in tools and _TOOL_LABELS.get(source) not in tools:
        return False
    if context.get("exact_only") and source not in _EXACT_SOURCES and source != "copilot":
        return False
    return True


def _local_id(payload):
    return str(((payload.get("device") or {}).get("id") or "local"))


def _pass_device(row, context, payload, policy):
    filters = _filters(context)
    device = row.get("device") or _local_id(payload)
    if filters.get("devices") and not _matches_filter(
            "device", device, list(filters["devices"]), policy=policy, payload=payload):
        return False
    if not (policy or {}).get("include_peers", False) and device != _local_id(payload):
        return False
    return True


def _pass_project(row, context, payload, policy):
    values = _filters(context).get("projects")
    raw = row.get("project") or "(unknown)"
    return not values or _matches_filter(
        "project", raw, list(values), policy=policy, payload=payload)


def _pass_ide(row, context, payload, policy):
    values = _filters(context).get("ides")
    raw = row.get("ide") or "(unknown)"
    return not values or _matches_filter(
        "ide", raw, list(values), policy=policy, payload=payload)


def _pass_model(model, payload, context):
    if model == "(user)":
        return True
    filters = _filters(context)
    if filters.get("models") and model not in filters["models"]:
        return False
    if filters.get("providers"):
        provider = (payload.get("model_vendor") or {}).get(model) or "Other"
        if provider not in filters["providers"]:
            return False
    return True


def _date_in(row, context):
    return context.get("from", "0000") <= str(row.get("date") or "") <= context.get("to", "9999")


def _pass_aux(row, payload, context, policy, *, model_field="model", project=True, ide=True):
    if not isinstance(row, dict) or not _date_in(row, context):
        return False
    source = row.get("source")
    if source is not None and not _pass_source(source, context):
        return False
    if not _pass_device(row, context, payload, policy):
        return False
    if project and not _pass_project(row, context, payload, policy):
        return False
    if ide and not _pass_ide(row, context, payload, policy):
        return False
    if model_field and model_field in row and not _pass_model(row.get(model_field), payload, context):
        return False
    return True


def _sum_vector(target, vector):
    if not isinstance(vector, (list, tuple)):
        return
    for index, field in enumerate(_DAY_FIELDS):
        if index < len(vector):
            target[field] = target.get(field, 0.0) + _number(vector[index])


def _session_model_days(session, exact):
    source = session.get("source")
    if exact and source == "copilot":
        return session.get("exact_model_days"), session.get("exact_days")
    return session.get("model_days"), session.get("days")


def _session_matches_model(session, payload, context, exact):
    filters = _filters(context)
    if not filters.get("models") and not filters.get("providers"):
        return True
    model_days, _ = _session_model_days(session, exact)
    if isinstance(model_days, dict):
        return any(model != "(user)" and _pass_model(model, payload, context) for model in model_days)
    # A dominant/summary model cannot attribute a mixed session to a selected
    # model or provider. Callers report this scope as unavailable.
    return False


_SESSION_FINDING_IDS = (
    "cache-waste", "model-fit", "context-tax", "subagents", "subagent-share", "heavy-openers",
)


def _session_scope_matches(session, payload, context, policy, exact):
    source = session.get("source")
    if source and not _pass_source(source, context):
        return False
    if not _pass_device(session, context, payload, policy):
        return False
    if not _pass_project(session, context, payload, policy):
        return False
    return _pass_ide(session, context, payload, policy)


def _session_detail_issue(session, payload, context, policy, exact):
    """Return a reason when this selected session cannot be sliced faithfully."""
    if not _session_scope_matches(session, payload, context, policy, exact):
        return None
    start, end = context.get("from", "0000"), context.get("to", "9999")
    session_start = str(session.get("start") or "")[:10]
    session_end = str(session.get("end") or session.get("start") or "")[:10]
    has_start = _valid_day(session_start)
    has_end = _valid_day(session_end)
    if has_start and has_end and (session_end < start or session_start > end):
        return None
    filters = _filters(context)
    has_model_filter = bool(filters.get("models") or filters.get("providers"))
    model_days, days = _session_model_days(session, exact)
    if exact and session.get("source") == "copilot" and not isinstance(days, dict):
        return "Some Copilot sessions have no exact per-day usage data."
    if has_model_filter and not isinstance(model_days, dict):
        return "Some sessions lack per-model daily usage needed for model/provider filters."
    if isinstance(days, dict) or isinstance(model_days, dict):
        return None
    fully_covered = (has_start and has_end and session_start >= start and session_end <= end)
    if fully_covered and not has_model_filter:
        return None
    if not has_start or not has_end:
        return "Some summary-only sessions lack dates needed to prove their full-session range."
    return "Some summary-only sessions cross the selected dates and cannot be apportioned to this range."


def _combined_model_days(model_days):
    result = {}
    if not isinstance(model_days, dict):
        return None
    for by_day in model_days.values():
        if not isinstance(by_day, dict):
            continue
        for day, vector in by_day.items():
            if isinstance(vector, (list, tuple)):
                _sum_vector(result.setdefault(day, {}), vector)
    return result


def _clip_model_days(model_days, payload, context):
    if not isinstance(model_days, dict):
        return None
    filters = _filters(context)
    start, end = context.get("from", "0000"), context.get("to", "9999")
    clipped = {}
    for model, values in model_days.items():
        if (filters.get("models") or filters.get("providers")) and model != "(user)":
            if not _pass_model(model, payload, context):
                continue
        if not isinstance(values, dict):
            continue
        day_values = {day: vector for day, vector in values.items()
                      if start <= str(day) <= end}
        if day_values:
            clipped[model] = day_values
    return clipped


def _session_range(session, payload, context, policy, exact):
    if not isinstance(session, dict):
        return None
    source = session.get("source")
    if source and not _pass_source(source, context):
        return None
    if not _pass_device(session, context, payload, policy):
        return None
    if (not _pass_project(session, context, payload, policy)
            or not _pass_ide(session, context, payload, policy)):
        return None
    if not _session_matches_model(session, payload, context, exact):
        return None

    model_days, days = _session_model_days(session, exact)
    if exact and session.get("source") == "copilot" and days is None:
        return None
    filters = _filters(context)
    if filters.get("models") or filters.get("providers"):
        if isinstance(model_days, dict):
            selected_days = {}
            for model, values in model_days.items():
                if model == "(user)" or not _pass_model(model, payload, context):
                    continue
                if isinstance(values, dict):
                    for day, vector in values.items():
                        _sum_vector(selected_days.setdefault(day, {}), vector)
        else:
            selected_days = days
    else:
        selected_days = days if isinstance(days, dict) else _combined_model_days(model_days)

    start, end = context.get("from", "0000"), context.get("to", "9999")
    clipped_model_days = _clip_model_days(model_days, payload, context)
    if isinstance(selected_days, dict):
        observed_days = [day for day in selected_days if start <= day <= end]
        if not observed_days:
            # Insights has no compaction-only finding. A compaction can make a
            # session relevant to the selected date, but it cannot attribute
            # the session's whole-summary usage to that date.
            return None
        totals = {}
        for day in observed_days:
            vector = selected_days.get(day)
            if isinstance(vector, dict):
                for field, value in vector.items():
                    if field in _VECTOR_FIELDS:
                        totals[field] = totals.get(field, 0.0) + _number(value)
            else:
                _sum_vector(totals, vector)
        # Once a date/model slice exists, never fall back to the whole-session
        # summary for fields omitted by a short or incomplete archived vector.
        out = {key: value for key, value in session.items() if key not in _DAY_FIELDS}
        out.update(totals)
        if clipped_model_days is not None:
            out["model_days"] = clipped_model_days
            if exact and source == "copilot":
                out["exact_model_days"] = clipped_model_days
        return out

    session_start = str(session.get("start") or "")[:10]
    session_end = str(session.get("end") or session.get("start") or "")[:10]
    filters = _filters(context)
    fully_covered = (_valid_day(session_start) and _valid_day(session_end)
                     and session_start >= start and session_end <= end)
    if not fully_covered or filters.get("models") or filters.get("providers"):
        return None
    # Old archives may retain only a whole-session summary. Keep its degraded
    # scope explicit; the caller has already proved the entire session is in range.
    out = dict(session)
    out["detail_limited"] = True
    return out


def _records_for_current(payload, context):
    # select_records has already applied exact Copilot subrows and all record filters.
    return context.get("_selected_rows") or []


def _finding(identifier, title, description, action, severity="info", estimate=None,
             evidence=None, tools=None, local_only=False, limitations=None):
    all_tools = sorted(set(_safe_tool_label(tool) for tool in (tools or ())))
    scope = {"tools": all_tools[:_MAX_EVIDENCE_ROWS], "local_only": bool(local_only)}
    if len(all_tools) > _MAX_EVIDENCE_ROWS:
        scope["tools_omitted"] = len(all_tools) - _MAX_EVIDENCE_ROWS
    return {
        "id": identifier, "title": title, "description": description,
        "suggested_action": action, "severity": severity,
        "estimated_saving": estimate, "estimate_basis": "heuristic estimate; not additive" if estimate is not None else None,
        "evidence": evidence or {},
        "scope": scope,
        "limitations": list(limitations or ()),
    }


def _unsupported(identifier, reason, required_fields=None):
    value = {"id": identifier, "reason": reason}
    if required_fields:
        value["required_fields"] = list(required_fields)
    return value


def _row_array(payload, key, unsupported, detector_ids):
    value = payload.get(key)
    if key not in payload or not isinstance(value, list):
        for identifier in detector_ids:
            unsupported.append(_unsupported(identifier, "The payload does not include this analysis source."))
        return None
    return value


def _valid_nonnegative_number(value):
    if isinstance(value, bool):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number >= 0


def _has_fields(rows, fields):
    if not rows:
        return True
    for row in rows:
        if not isinstance(row, dict):
            return False
        for field in fields:
            if field not in row or row[field] is None:
                return False
            if field in _NUMERIC_FIELDS:
                value = row[field]
                if isinstance(value, bool):
                    return False
                try:
                    numeric = float(value)
                except (TypeError, ValueError, OverflowError):
                    return False
                if not math.isfinite(numeric):
                    return False
    return True


def _require_fields(rows, fields, identifier, unsupported):
    if rows is None:
        return False
    if not _has_fields(rows, fields):
        unsupported.append(_unsupported(identifier, "The source is missing fields needed by this detector.", fields))
        return False
    return True


def _session_rows(payload, context, policy, exact, unsupported):
    sessions = _row_array(payload, "sessions", unsupported,
                          ("cache-waste", "model-fit", "context-tax", "subagents", "subagent-share", "heavy-openers"))
    if sessions is None:
        return None
    if not _has_fields(sessions, ("source", "project", "ide", "device")):
        for identifier in ("cache-waste", "model-fit", "context-tax", "subagents", "subagent-share", "heavy-openers"):
            unsupported.append(_unsupported(identifier, "The session source is missing scope fields needed by this detector.",
                                            ("source", "project", "ide", "device")))
        return None
    out = []
    issues = []
    for session in sessions:
        issue = _session_detail_issue(session, payload, context, policy, exact)
        if issue:
            issues.append(issue)
        clipped = _session_range(session, payload, context, policy, exact)
        if clipped is not None:
            out.append(clipped)
    if issues:
        reason = sorted(set(issues))[0]
        for identifier in _SESSION_FINDING_IDS:
            item = _unsupported(identifier, reason, ("sessions.days", "sessions.model_days"))
            item["affected_sessions"] = len(issues)
            unsupported.append(item)
        return None
    return out


def _request_rate(row, payload):
    calls = _number(row.get("asst") or row.get("req"))
    if not calls:
        return 0.0
    p = _price_tuple(payload, row.get("model"), row.get("date"))
    long_p = _valid_price_tuple((payload.get("pricing_long_context") or {}).get(row.get("model")))
    if not any(long_p):
        long_p = p
    return p[4] + (long_p[4] - p[4]) * _number(row.get("lc_asst")) / calls


def _cached_token_cost(records, predicate, use_requests, payload):
    calls = 0.0
    per = 0.0
    for row in records:
        if row.get("model") == "(user)" or not predicate(row):
            continue
        count = _number(row.get("req") or row.get("asst")) if use_requests else _number(row.get("asst"))
        calls += count
        per += count * _request_rate(row, payload) / 1_000_000.0
    return calls, per


def _cache_write_rate(records, payload):
    tokens = 0.0
    cost = 0.0
    for row in records:
        cc5, cc1 = _number(row.get("cc5")), _number(row.get("cc1"))
        flat = 0.0 if cc5 or cc1 else _number(row.get("cc"))
        row_tokens = cc5 + cc1 + flat
        tokens += row_tokens
        cost += _token_cost_parts(row, payload)["cc"]
    return (cost / tokens) if tokens else 0.0


def _model_family(source):
    if source == "codex":
        return "codex"
    if source in ("claude", "claude-desktop"):
        return "claude"
    return None


def _date_vector(session, start, end, exact=False):
    model_days, days = _session_model_days(session, exact)
    source = session.get("source")
    if exact and source == "copilot":
        selected = session.get("exact_days") or {}
    else:
        selected = session.get("days") or {}
    total = {}
    for day, vector in selected.items() if isinstance(selected, dict) else ():
        if start <= day <= end:
            _sum_vector(total, vector)
    return total


def _session_alias(session, policy):
    try:
        return _query_module().session_alias(session, policy=policy)
    except (ImportError, AttributeError, TypeError):
        return None


def _local_scope_allowed(context, payload, policy):
    selected = _filters(context).get("devices")
    return not selected or _matches_filter(
        "device", _local_id(payload), list(selected), policy=policy, payload=payload)


def _session_evidence(session, policy, **values):
    alias = _session_alias(session, policy)
    result = dict(values)
    if alias:
        result["session"] = alias
    return result


def _sorted_findings(findings):
    return sorted(findings, key=lambda item: (
        -(_number(item.get("estimated_saving"), -1.0) if item.get("estimated_saving") is not None else 0.0),
        item["id"]))


def _dedupe_unsupported(items):
    seen = set()
    result = []
    for item in items:
        if item["id"] in seen:
            continue
        seen.add(item["id"])
        result.append(item)
    return result


def _row_date_filter(payload, key, fields, context, policy, unsupported, identifier,
                     *, project=True, ide=True, model_field="model"):
    rows = _row_array(payload, key, unsupported, (identifier,))
    if rows is None or not _require_fields(rows, fields, identifier, unsupported):
        return None
    return [row for row in rows if _pass_aux(row, payload, context, policy,
                                              project=project, ide=ide, model_field=model_field)]


def _counterfactual_cost(row, model, payload, day):
    base = _price_tuple(payload, model, day)
    high = _valid_price_tuple((payload.get("pricing_long_context") or {}).get(model))
    # For substitutions, use the published high-context tuple where one exists;
    # request-size/tier metadata is not sufficient to pick a cheaper tier safely.
    p = high if any(high) else base
    inp, out = _number(row.get("in")), _number(row.get("out"))
    cr, cc5, cc1 = _number(row.get("cr")), _number(row.get("cc5")), _number(row.get("cc1"))
    if not cc5 and not cc1:
        cc5 = _number(row.get("cc"))
    return (inp * p[0] + out * p[1] + cr * p[4]
            + cc5 * (p[2] or p[0]) + cc1 * (p[3] or p[2] or p[0])) / 1_000_000.0


def _model_fit(records, sessions, payload, policy, exact, context, unsupported):
    identifier = "model-fit"
    if sessions is None:
        return None
    if _filters(context).get("models") or _filters(context).get("providers"):
        unsupported.append(_unsupported(identifier,
                                        "Model substitution needs the full set of observed candidate models; model/provider filters hide alternatives."))
        return None
    required = ("out", "tools", "cost", "source", "subagent")
    if not _require_fields(sessions, required, identifier, unsupported):
        return None
    if not (payload.get("prices") or payload.get("pricing")):
        unsupported.append(_unsupported(identifier, "Recorded model prices are unavailable for this substitution estimate.",
                                        ("prices", "pricing")))
        return None
    if not payload.get("model_vendor"):
        unsupported.append(_unsupported(identifier, "Model vendor metadata is unavailable; same-vendor candidates cannot be selected.",
                                        ("model_vendor",)))
        return None

    used_models = {row.get("model") for row in records if row.get("model") and row.get("model") != "(user)"}
    vendor_map = payload.get("model_vendor") or {}
    grouped = {}
    ambiguous = 0
    missing_models = 0
    missing_pricing_fields = 0
    for session in sessions:
        if session.get("subagent"):
            continue
        if not (_number(session.get("out")) < 4000 and _number(session.get("tools")) <= 3
                and _number(session.get("cost")) > 0.02):
            continue
        model_days, _ = _session_model_days(session, exact)
        candidates = sorted(model for model, days in (model_days or {}).items()
                            if isinstance(model, str) and model != "(user)"
                            and isinstance(days, dict) and days)
        if len(candidates) > 1:
            ambiguous += 1
            continue
        if not candidates:
            missing_models += 1
            continue
        model = candidates[0]
        source_days = model_days[model]
        source_cost = 0.0
        complete_cost = True
        for vector in source_days.values():
            if isinstance(vector, dict):
                usage = vector
            elif isinstance(vector, (list, tuple)) and vector:
                usage = {field: vector[index] for index, field in enumerate(_DAY_FIELDS)
                         if index < len(vector)}
            else:
                usage = {}
            if (not _has_fields([usage], ("cost", "in", "out", "cr"))
                    or not any(field in usage for field in ("cc", "cc5", "cc1"))):
                complete_cost = False
                break
            value = usage.get("cost")
            if value is None or isinstance(value, bool):
                complete_cost = False
                break
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError):
                complete_cost = False
                break
            if not math.isfinite(numeric):
                complete_cost = False
                break
            source_cost += numeric
        if not complete_cost:
            missing_pricing_fields += 1
            continue
        if source_cost <= 0.02:
            continue
        grouped.setdefault(model, []).append((session, source_days, source_cost))
    if missing_models:
        item = _unsupported(identifier,
                            "Some light-work sessions lack dated model usage needed for repricing.",
                            ("model_days",))
        item["affected_sessions"] = missing_models
        unsupported.append(item)
    if missing_pricing_fields:
        item = _unsupported(identifier,
                            "Some light-work sessions lack dated model-specific cost/token fields needed for repricing.",
                            ("model_days.cost", "model_days.in", "model_days.out", "model_days.cr"))
        item["affected_sessions"] = missing_pricing_fields
        unsupported.append(item)
    if ambiguous:
        item = _unsupported(identifier,
                            "Some light-work sessions used multiple billed models and cannot be repriced as one model.",
                            ("model_days",))
        item["affected_sessions"] = ambiguous
        unsupported.append(item)

    rows = []
    total_estimate = 0.0
    session_count = 0
    tools = set()
    for model, matched in grouped.items():
        source_price = _price_tuple(payload, model, min(matched[0][1]) if matched[0][1] else None)
        if not any(source_price):
            continue
        vendor = vendor_map.get(model)
        if not vendor:
            continue
        candidate = None
        candidate_output = source_price[1]
        for other in sorted(used_models):
            if other == model or vendor_map.get(other) != vendor:
                continue
            price = _price_tuple(payload, other, min(matched[0][1]) if matched[0][1] else None)
            if price[1] > 0 and price[1] < candidate_output:
                candidate_output, candidate = price[1], other
        if candidate is None:
            continue
        now = 0.0
        then = 0.0
        included = 0
        for session, days, source_cost in matched:
            target_cost = 0.0
            for day, vector in days.items():
                if isinstance(vector, dict):
                    usage = vector
                elif isinstance(vector, (list, tuple)):
                    usage = {field: vector[index] for index, field in enumerate(_DAY_FIELDS)
                             if index < len(vector)}
                else:
                    continue
                target_cost += _counterfactual_cost(usage, candidate, payload, day)
            if target_cost >= source_cost:
                continue
            now += source_cost
            then += target_cost
            included += 1
            tools.add(session.get("source"))
        saving = now - then
        if included and saving >= 0.5:
            total_estimate += saving
            session_count += included
            rows.append({"source_model": _display_label(model, "model", policy, payload),
                         "candidate_model": _display_label(candidate, "model", policy, payload),
                         "sessions": included, "current_cost": now, "modeled_cost": then,
                         "estimated_difference": saving})
    if not rows:
        return None
    rows.sort(key=lambda row: (-row["estimated_difference"], row["source_model"]))
    visible_rows = rows[:_MAX_EVIDENCE_ROWS]
    evidence = {"sessions": session_count, "substitutions": visible_rows}
    if len(rows) > _MAX_EVIDENCE_ROWS:
        evidence["substitutions_omitted"] = len(rows) - _MAX_EVIDENCE_ROWS
    return _finding(identifier, "A costly model doing very light work",
                    "Some sessions produced under 4,000 output tokens with at most three tool calls. "
                    "A lower-output-rate model from the same provider that you already use has a lower "
                    "published-price estimate for the same recorded usage.",
                    "For a quick lookup or one-shot task, consider starting with the smaller model.",
                    "high" if total_estimate > 20 else "low", total_estimate,
                    evidence, tools,
                    limitations=["This is a price-only counterfactual and says nothing about answer quality.",
                                 "Long-context target prices are applied conservatively where published; "
                                 "per-request pricing tiers and unlogged web-search charges may differ."])


def _cost_summary(records):
    return sum(_number(row.get("cost")) for row in records)


def _tool_rows(payload, context, policy, start, end):
    tools = []
    for row in payload.get("tools", ()) or ():
        if not isinstance(row, dict) or not start <= str(row.get("date") or "") <= end:
            continue
        if not _pass_source(row.get("source"), context) or not _pass_device(row, context, payload, policy):
            continue
        tools.append(row)
    return tools


def _mcp_used_for_source(payload, context, policy, source, start, end):
    inventory = payload.get("mcp_inventory") or {}
    calls = inventory.get("calls") or []
    used = {}
    has_source = bool(calls) and all(isinstance(row, dict) and row.get("source") for row in calls)
    if has_source:
        for row in calls:
            if row.get("source") != source or not start <= str(row.get("date") or "") <= end:
                continue
            if not _pass_source(source, context):
                continue
            name = str(row.get("server") or "")
            used[name] = used.get(name, 0) + _number(row.get("calls"))
        return used
    for row in _tool_rows(payload, context, policy, start, end):
        if row.get("source") != source:
            continue
        name = str(row.get("name") or "")
        if not name.startswith("mcp__"):
            continue
        rest = name[5:]
        server = rest.split("__", 1)[0]
        used[server] = used.get(server, 0) + _number(row.get("count"))
    return used


def _find_idle_mcp(payload, context, policy, source, local_allowed, unsupported):
    identifier = "idle-mcp-" + source
    if not local_allowed:
        unsupported.append(_unsupported(identifier,
                                        "This detector reads local server configuration and is unavailable outside the local-device scope."))
        return None
    configured = payload.get("mcp_servers")
    if not isinstance(configured, list):
        unsupported.append(_unsupported(identifier, "The local MCP configuration inventory is unavailable.",
                                        ("mcp_servers",)))
        return None
    if not _pass_source(source, context):
        return None
    selected = [entry for entry in configured if isinstance(entry, dict)
                and (entry.get("tool") or "claude") == source]
    if not selected:
        return None
    used = _mcp_used_for_source(payload, context, policy, source,
                                context.get("from", "0000"), context.get("to", "9999"))
    norm = lambda value: re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    idle = []
    for item in selected:
        name = norm(item.get("name"))
        if not any(key == name or key in name or name in key for key in map(norm, used) if key and name):
            idle.append(item)
    if not idle:
        return None
    provider = "Claude Code" if source == "claude" else "Codex"
    return _finding(identifier, "{} MCP server{} configured but never called".format(
                        len(idle), "s" if len(idle) != 1 else ""),
                    "The local configuration lists {} server(s) with no matching calls in the selected range. "
                    "This is an inventory observation, not a measured token or cost saving.".format(len(idle)),
                    "Review which servers you still use and disconnect or scope the others in the original tool.",
                    evidence={"configured_without_calls": len(idle), "tool": provider},
                    tools=[source], local_only=True,
                    limitations=["Server call records do not carry model or project attribution, so those filters do not apply.",
                                 "The configuration is current and may differ from what was configured during this historical range."])


def _find_mcp_shape(payload, context, local_allowed, unsupported):
    identifier = "mcp-shape"
    if not local_allowed:
        unsupported.append(_unsupported(identifier,
                                        "This detector reads local MCP inventory and is unavailable outside the local-device scope."))
        return None
    inventory = payload.get("mcp_inventory")
    if not isinstance(inventory, dict) or not isinstance(inventory.get("servers"), dict):
        unsupported.append(_unsupported(identifier, "The local MCP server inventory is unavailable.",
                                        ("mcp_inventory.servers",)))
        return None
    if not _pass_source("claude", context):
        return None
    loaded_rows = inventory.get("loaded")
    call_rows = inventory.get("calls")
    if not isinstance(loaded_rows, list) or not isinstance(call_rows, list):
        unsupported.append(_unsupported(identifier, "MCP load or call evidence is unavailable.",
                                        ("mcp_inventory.loaded", "mcp_inventory.calls")))
        return None
    start, end = context.get("from", "0000"), context.get("to", "9999")
    loaded, calls, projects_loaded, projects_called, used_tools = {}, {}, {}, {}, {}
    for row in loaded_rows:
        if not isinstance(row, dict) or not start <= str(row.get("date") or "") <= end:
            continue
        server = str(row.get("server") or "")
        loaded[server] = loaded.get(server, 0) + _number(row.get("sessions"))
        projects_loaded.setdefault(server, set()).add(str(row.get("project") or "(unknown)"))
    for row in call_rows:
        if not isinstance(row, dict) or not start <= str(row.get("date") or "") <= end:
            continue
        if row.get("source") not in (None, "claude"):
            continue
        server = str(row.get("server") or "")
        calls[server] = calls.get(server, 0) + _number(row.get("calls"))
        projects_called.setdefault(server, set()).add(str(row.get("project") or "(unknown)"))
        if row.get("source") == "claude":
            for name in row.get("tools") or ():
                prefix = str(name)[5:].split("__", 1)[0] if str(name).startswith("mcp__") else ""
                if prefix:
                    used_tools[prefix] = used_tools.get(prefix, set()) | {str(name)}
    if not all(isinstance(row, dict) and row.get("source") for row in call_rows):
        for row in _tool_rows(payload, context, {"include_peers": False}, start, end):
            if row.get("source") != "claude":
                continue
            name = str(row.get("name") or "")
            if name.startswith("mcp__"):
                prefix = name[5:].split("__", 1)[0]
                used_tools.setdefault(prefix, set()).add(name)
    flagged = 0
    mostly_unused = 0
    single_project = 0
    for server, sessions in loaded.items():
        offered = len((inventory["servers"].get(server) or {}).get("tools") or ())
        used = len(used_tools.get(server) or ())
        projects_l = len(projects_loaded.get(server) or ())
        projects_c = len(projects_called.get(server) or ())
        if sessions >= 3 and offered >= 10 and used and used / offered <= 0.1:
            mostly_unused += 1
            flagged += 1
        elif projects_c == 1 and projects_l >= 3 and calls.get(server):
            single_project += 1
            flagged += 1
    if not flagged:
        return None
    return _finding(identifier, "{} MCP server{} loaded more widely than used".format(
                        flagged, "s" if flagged != 1 else ""),
                    "The observed MCP inventory shows servers offering many tools with few used, or servers "
                    "loaded across several projects but called in only one.",
                    "Consider project-scoping a server or choosing a lighter tool set for rarely used capabilities.",
                    evidence={"servers_flagged": flagged, "mostly_unused_tools": mostly_unused,
                              "used_in_one_project": single_project},
                    tools=["claude"], local_only=True,
                    limitations=["Inventory and calls are local-only and do not include model or project filters.",
                                 "Server names and tool definitions are intentionally omitted."])


def get_insights(payload, arguments, *, policy=None, today=None):
    """Return privacy-safe Optimize findings for the selected usage range.

    The existing dashboard uses 18 source-aware rules (including separate
    Claude and Codex idle-MCP checks). Supported rules retain their thresholds;
    a missing input is reported in `unsupported` instead of being interpreted
    as a clean bill of health.
    """
    arguments = dict(arguments or {})
    try:
        limit = int(arguments.get("limit", 50))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("limit must be an integer from 1 to 100")
    limit = max(1, min(100, limit))
    records, context = _select(payload, arguments, policy, today)
    exact = bool(context.get("exact_only"))
    sessions = _session_rows(payload, context, policy, exact, unsupported := [])
    meta = _safe_meta(payload, context, policy)
    findings = []
    local_allowed = _local_scope_allowed(context, payload, policy)
    local_id = _local_id(payload)
    local_records = [row for row in records if (row.get("device") or local_id) == local_id]
    local_sessions = [row for row in (sessions or ()) if (row.get("device") or local_id) == local_id]
    total_cost = _cost_summary(records)
    start, end = context.get("from", "0000"), context.get("to", "9999")

    def add(value):
        if value:
            findings.append(value)

    # 1. Claude's un-reused cache writes; no dollar estimate is inferred from
    # token counts because write/read tiers vary by model and retention.
    if sessions is not None:
        identifier = "cache-waste"
        needed = ("cc", "cr")
        if _require_fields(sessions, needed, identifier, unsupported):
            bad = [s for s in sessions if _number(s.get("cc")) > 20000
                   and _number(s.get("cr")) < _number(s.get("cc")) * 0.5]
            if bad:
                cc = sum(_number(s.get("cc")) for s in bad)
                cr = sum(_number(s.get("cr")) for s in bad)
                add(_finding(identifier, "Cache written with little reuse",
                             "{} session(s) wrote {:,.0f} cache tokens and read back less than half that amount. "
                             "Low reuse alone does not show how much cost could be avoided.".format(len(bad), cc),
                             "Keep related questions in the same session when useful, and check the tool's cache retention settings.",
                             evidence={"sessions": len(bad), "cache_write_tokens": cc, "cache_read_tokens": cr},
                             tools=[s.get("source") for s in bad],
                             limitations=["Cache write billing depends on model and retention tier; no savings amount is inferred."] ))

    # 2. Lightweight sessions with a lower-rate same-provider model the user
    # already ran. Candidate repricing uses date-aware recorded rate tables.
    if sessions is not None:
        add(_model_fit(records, sessions, payload, policy, exact, context, unsupported))

    # 3/4. Local MCP inventories are configuration observations, not usage rows.
    add(_find_idle_mcp(payload, context, policy, "claude", local_allowed, unsupported))
    add(_find_idle_mcp(payload, context, policy, "codex", local_allowed, unsupported))

    # 5. Reasoning/output share. The suggested reduction is only a small
    # heuristic proxy and is never described as causal.
    identifier = "thinking"
    if _require_fields(records, ("reason", "out", "cost", "source"), identifier, unsupported):
        reason = sum(_number(r.get("reason")) for r in records)
        out = sum(_number(r.get("out")) for r in records)
        cost = total_cost
        share = reason / out if out else 0.0
        if out and share >= 0.15:
            add(_finding(identifier, "Extended thinking is a large share of your output",
                         "{:,.0f} of {:,.0f} output tokens ({:.1%}) were reasoning tokens, billed at the output rate.".format(
                             reason, out, share),
                         "Use higher reasoning effort for difficult work and a lower default for routine edits.",
                         "high" if share > 0.3 else "low", cost * share * 0.3,
                         {"reasoning_tokens": reason, "output_tokens": out, "reasoning_share": share,
                          "estimated_cost_in_range": cost},
                         [r.get("source") for r in records if _number(r.get("reason"))],
                         limitations=["Reducing reasoning may change answer quality; the estimate is not a measured saving."]))

    # 6. Expensive long sessions relative to this device's own median cost per prompt.
    if sessions is not None:
        identifier = "context-tax"
        if _require_fields(sessions, ("user", "cost"), identifier, unsupported):
            with_prompts = [s for s in sessions if _number(s.get("user")) >= 5 and _number(s.get("cost")) > 0]
            if len(with_prompts) >= 5:
                per = sorted(_number(s.get("cost")) / _number(s.get("user")) for s in with_prompts)
                median = per[len(per) // 2]
                bad = sorted((s for s in with_prompts
                              if _number(s.get("cost")) / _number(s.get("user")) > max(median * 8, 1.0)),
                             key=lambda s: -_number(s.get("cost")))
                if bad:
                    excess = sum(_number(s.get("cost")) - median * _number(s.get("user")) for s in bad)
                    add(_finding(identifier, "Long sessions reread a very large context",
                                 "{} session(s) cost more than eight times the median of ${:.2f} per prompt. "
                                 "A growing conversation can make each new request more expensive.".format(
                                     len(bad), median),
                                 "Start a fresh session when the task changes, or compact history you no longer need.",
                                 "high" if excess > 50 else "low", max(excess, 0.0),
                                 {"sessions": len(bad), "median_cost_per_prompt": median,
                                  "highest_cost_per_prompt": max(_number(s.get("cost")) / _number(s.get("user")) for s in bad)},
                                 [s.get("source") for s in bad],
                                 limitations=["This compares observed session costs and does not establish that context caused them."]))

    # 7. Tool-heavy sessions without recorded delegation markers.
    if sessions is not None:
        identifier = "subagents"
        needed = ("tools", "side", "subagents", "cost", "source", "subagent")
        if _require_fields(sessions, needed, identifier, unsupported):
            parents = [s for s in sessions if not s.get("subagent")]
            heavy = [s for s in parents if _number(s.get("tools")) >= 150
                     and not _number(s.get("side")) and not _number(s.get("subagents"))]
            if len(heavy) >= 2:
                spend = sum(_number(s.get("cost")) for s in heavy)
                used = sum(1 for s in parents if _number(s.get("side")) > 0 or _number(s.get("subagents")) > 0)
                add(_finding(identifier, "Tool-heavy sessions that never delegated to a subagent",
                             "{} session(s) made at least 150 tool calls without a recorded subagent, and cost ${:,.2f}. "
                             "Delegation can keep broad searches out of the parent's context.".format(len(heavy), spend),
                             "For broad codebase exploration, consider delegating and keeping the summary.",
                             "high" if spend > 100 else "low", spend * 0.15,
                             {"sessions": len(heavy), "tool_calls": sum(_number(s.get("tools")) for s in heavy),
                              "cost_in_range": spend, "other_sessions_with_delegation": used},
                             [s.get("source") for s in heavy],
                             limitations=["The 15% estimate is a heuristic. Delegation can add work and is not always cheaper."]))

    # 8. Cache hit share uses input + cache reads + cache writes as context.
    identifier = "low-cache"
    if _require_fields(records, ("in", "cr", "cc", "source"), identifier, unsupported):
        cr = sum(_number(r.get("cr")) for r in records)
        ctx = sum(_number(r.get("in")) + _number(r.get("cr")) + _number(r.get("cc")) for r in records)
        rate = cr / ctx if ctx else 1.0
        if ctx and rate < 0.7:
            add(_finding(identifier, "Prompt cache is doing less work than it could",
                         "Only {:.1%} of measured context tokens came from cache reads.".format(rate),
                         "Keep stable context together where practical; frequent edits near the top can invalidate cached prefixes.",
                         "high" if rate < 0.4 else "low", None,
                         {"cache_read_tokens": cr, "context_tokens": ctx, "cache_read_share": rate},
                         [r.get("source") for r in records if _number(r.get("cr") or r.get("cc"))],
                         limitations=["Cache behavior depends on the provider and model; the gap is not a measured dollar saving."]))

    # 9. Claude skill usage has only date/device detail, so project/model filters
    # cannot be applied. Tool filtering can still exclude this Claude-specific rule.
    identifier = "skills"
    if _pass_source("claude", context):
        skill_rows = _row_array(payload, "skills", unsupported, (identifier,))
        if skill_rows is not None and _require_fields(skill_rows, ("date", "name", "device", "tok", "asst", "cost"),
                                                      identifier, unsupported):
            selected = [r for r in skill_rows if _date_in(r, context) and _pass_device(r, context, payload, policy)]
            spend = sum(_number(r.get("cost")) for r in selected)
            if spend:
                by_name = {}
                for row in selected:
                    key = str(row.get("name") or "")
                    item = by_name.setdefault(key, {"tokens": 0.0, "requests": 0.0, "cost": 0.0})
                    item["tokens"] += _number(row.get("tok"))
                    item["requests"] += _number(row.get("asst"))
                    item["cost"] += _number(row.get("cost"))
                rec_total = _cost_summary(records)
                add(_finding(identifier, "Where your Skills are spending",
                             "Claude Code Skills drove ${:,.2f}{} in the selected range.".format(
                                 spend, " ({:.1%} of selected spend)".format(spend / rec_total) if rec_total else ""),
                             "For frequently used Skills, narrow when they trigger or reduce repeated context they add.",
                             evidence={"skills_used": len(by_name), "requests": sum(x["requests"] for x in by_name.values()),
                                       "tokens": sum(x["tokens"] for x in by_name.values()), "spend": spend},
                             tools=["claude"],
                             limitations=["Skill rows have date/device detail only; project, model and IDE filters cannot apply.",
                                          "The payload does not include Skill instruction contents."]))

    # 10. Very large request contexts. The source data has source/date/device,
    # but does not identify a model or project for each context measurement.
    identifier = "big-context"
    ctx_rows = _row_array(payload, "ctx", unsupported, (identifier,))
    if ctx_rows is not None and _require_fields(ctx_rows, ("date", "bucket", "source", "device", "tok", "n"),
                                                identifier, unsupported):
        selected = [r for r in ctx_rows if _pass_aux(r, payload, context, policy,
                                                       model_field=None, project=False, ide=False)]
        buckets = {}
        source_set = set()
        total_tokens = 0.0
        for row in selected:
            bucket = str(row.get("bucket") or "(unknown)")
            entry = buckets.setdefault(bucket, {"tokens": 0.0, "requests": 0.0})
            entry["tokens"] += _number(row.get("tok"))
            entry["requests"] += _number(row.get("n"))
            total_tokens += _number(row.get("tok"))
            if row.get("source"):
                source_set.add(row["source"])
        big = sum(buckets.get(key, {}).get("tokens", 0.0) for key in ("150-400k", "400k+"))
        share = big / total_tokens if total_tokens else 0.0
        if total_tokens and share >= 0.4:
            cost = sum(_number(row.get("cost")) for row in records if row.get("source") in source_set)
            add(_finding(identifier, "Many requests ran with very large context",
                         "{:.1%} of measured request-context tokens were sent at 150k tokens or more.".format(share),
                         "Start a fresh session when switching tasks and compact history that no longer helps.",
                         "high" if share > 0.7 else "low", cost * share * 0.15,
                         {"large_context_tokens": big, "measured_context_tokens": total_tokens,
                          "large_context_share": share, "requests": sum(x["requests"] for x in buckets.values()),
                          "buckets": buckets}, source_set,
                         limitations=["Context rows lack project/model attribution; only tool and device filters apply.",
                                      "The 15% estimate is heuristic and overlaps with other findings."]))

    # 11. Subagent share is a cost-centre observation, not a saving claim.
    if sessions is not None:
        identifier = "subagent-share"
        if _require_fields(sessions, ("subagent", "cost", "source"), identifier, unsupported):
            subs = [s for s in sessions if s.get("subagent")]
            sub_cost = sum(_number(s.get("cost")) for s in subs)
            session_cost = sum(_number(s.get("cost")) for s in sessions)
            share = sub_cost / session_cost if session_cost else 0.0
            if subs and session_cost and share >= 0.2:
                add(_finding(identifier, "A large share of spend ran inside subagents",
                             "{} subagent transcript(s) cost ${:,.2f}, or {:.1%} of the selected session cost.".format(
                                 len(subs), sub_cost, share),
                             "Use subagents for broad exploration where isolation helps; simple delegated work may fit inline.",
                             evidence={"subagent_sessions": len(subs), "subagent_cost": sub_cost,
                                       "session_cost": session_cost, "subagent_share": share},
                             tools=[s.get("source") for s in subs],
                             limitations=["Subagent spend can be the right tradeoff and is not treated as waste."]))

    # 12. High global Codex effort with a high reasoning share.
    identifier = "codex-effort"
    if "codex_effort" not in payload:
        unsupported.append(_unsupported(identifier, "The local Codex reasoning-effort setting is unavailable.",
                                        ("codex_effort",)))
    elif payload.get("codex_effort") and str(payload.get("codex_effort")).lower() in ("high", "xhigh", "ultra", "max"):
        if not local_allowed:
            unsupported.append(_unsupported(identifier,
                                            "This detector reads the local Codex effort setting and is unavailable outside the local-device scope."))
            codex = []
        else:
            codex = [r for r in local_records if r.get("source") == "codex"]
        if _require_fields(codex, ("reason", "out", "cost"), identifier, unsupported):
            reason = sum(_number(r.get("reason")) for r in codex)
            out = sum(_number(r.get("out")) for r in codex)
            cost = _cost_summary(codex)
            share = reason / out if out else 0.0
            effort = str(payload["codex_effort"])
            if out and cost and share >= 0.2:
                add(_finding(identifier, "Codex reasoning effort is set to {}".format(effort),
                             "{:,.0f} of {:,.0f} Codex output tokens ({:.1%}) were reasoning tokens across ${:,.2f} estimated spend.".format(
                                 reason, out, share, cost),
                             "Consider a lower global default and raise effort for tasks that need it.",
                             "high" if share > 0.5 else "low", cost * share * 0.25,
                             {"effort": effort, "reasoning_tokens": reason, "output_tokens": out,
                              "reasoning_share": share, "codex_spend": cost}, ["codex"], local_only=True,
                             limitations=["The setting is global; this estimate does not predict output quality."]))

    # 13. Repeated edits relative to each tool's own one-shot average.
    identifier = "retry-tax"
    activity = _row_array(payload, "activity", unsupported, (identifier,))
    if activity is not None and _require_fields(activity,
            ("date", "source", "model", "project", "ide", "device", "edits", "oneshot", "retry_cost", "edit_cost"),
            identifier, unsupported):
        selected = [row for row in activity if _pass_aux(row, payload, context, policy)]
        by_source = {}
        for row in selected:
            source = row.get("source")
            item = by_source.setdefault(source, {"edits": 0.0, "oneshot": 0.0,
                                                 "retry_cost": 0.0, "edit_cost": 0.0})
            for field in ("edits", "oneshot", "retry_cost", "edit_cost"):
                item[field] += _number(row.get(field))
        retry_rows = []
        impact = retried = retry_spend = 0.0
        for source in sorted(by_source):
            item = by_source[source]
            if item["edits"] < 10:
                continue
            n_retry = item["edits"] - item["oneshot"]
            if not n_retry or not item["oneshot"]:
                continue
            avg_retry = item["retry_cost"] / n_retry
            avg_one = (item["edit_cost"] - item["retry_cost"]) / item["oneshot"]
            extra = max(0.0, (avg_retry - avg_one) * n_retry)
            if extra <= 0:
                continue
            impact += extra
            retried += n_retry
            retry_spend += item["retry_cost"]
            retry_rows.append({"tool": _safe_tool_label(source), "edits": item["edits"],
                               "one_shot_share": item["oneshot"] / item["edits"],
                               "average_retry_cost": avg_retry, "average_one_shot_cost": avg_one,
                               "estimated_difference": extra})
        if retry_rows and impact >= 1:
            retry_rows.sort(key=lambda row: (-row["estimated_difference"], row["tool"]))
            visible_retry_rows = retry_rows[:_MAX_EVIDENCE_ROWS]
            retry_evidence = {"retried_prompts": retried, "retry_spend": retry_spend,
                              "tool_rates": visible_retry_rows}
            if len(retry_rows) > _MAX_EVIDENCE_ROWS:
                retry_evidence["tool_rates_omitted"] = len(retry_rows) - _MAX_EVIDENCE_ROWS
            add(_finding(identifier, "Editing prompts that needed retries cost more",
                         "About {:,.0f} editing prompts needed a retry. Their observed retry cost was ${:,.2f}; "
                         "the same count at the one-shot average would be about ${:,.2f}.".format(
                             retried, retry_spend, max(0.0, retry_spend - impact)),
                         "State the check command and expected result up front so the agent can verify before making another edit.",
                         "high" if impact > 50 else "low", impact,
                         retry_evidence, [row["tool"] for row in retry_rows],
                         limitations=["This is an observed average comparison, not proof that retries caused the cost difference."]))

    # 14. Large instruction files on the local device. Only file sizes are used;
    # neither paths nor instruction contents leave this calculation.
    identifier = "instruction-files"
    if local_allowed:
        files = payload.get("context_files")
        if not isinstance(files, list):
            unsupported.append(_unsupported(identifier, "Local instruction-file size metadata is unavailable.",
                                            ("context_files",)))
        elif not _pass_source("claude", context) and not _pass_source("codex", context):
            pass
        else:
            if not _has_fields(files, ("source", "project", "bytes")):
                unsupported.append(_unsupported(identifier, "Instruction-file size metadata is missing scope fields.",
                                                ("source", "project", "bytes")))
            elif any(not _valid_nonnegative_number(info.get("bytes"))
                     or (info.get("sent") is not None and not _valid_nonnegative_number(info.get("sent")))
                     for info in files if isinstance(info, dict)):
                unsupported.append(_unsupported(identifier, "Instruction-file sent-size metadata is invalid.",
                                                ("context_files.sent",)))
            else:
                large_files = [f for f in files if _number(
                    f.get("sent") if f.get("sent") is not None else f.get("bytes")) > 8192]
                file_rows = []
                impact = 0.0
                for info in large_files:
                    family = info.get("source")
                    sent = _number(info.get("sent") if info.get("sent") is not None else info.get("bytes"))
                    token_count = sent / 4.0
                    project = info.get("project") or "*"
                    matched = [r for r in local_records
                               if _model_family(r.get("source")) == family
                               and (project == "*" or (r.get("project") or "(unknown)") == project)
                               and (not info.get("since") or str(r.get("date") or "") >= info["since"])]
                    calls, per = _cached_token_cost(matched, lambda row: True, family == "codex", payload)
                    if not calls:
                        continue
                    cost = token_count * per
                    saving = cost * (sent - 4096) / sent if sent else 0.0
                    impact += saving
                    file_rows.append({"bytes": _number(info.get("bytes")), "sent_bytes": sent,
                                      "estimated_context_tokens": token_count, "requests": calls,
                                      "estimated_cost": cost, "estimated_difference": saving})
                if file_rows and impact >= 0.5:
                    file_rows.sort(key=lambda row: (-row["estimated_difference"], row["bytes"]))
                    visible_file_rows = file_rows[:_MAX_EVIDENCE_ROWS]
                    file_evidence = {"files": len(file_rows),
                                     "requests": sum(row["requests"] for row in file_rows),
                                     "estimated_context_cost": sum(row["estimated_cost"] for row in file_rows),
                                     "file_sizes": visible_file_rows}
                    if len(file_rows) > _MAX_EVIDENCE_ROWS:
                        file_evidence["file_sizes_omitted"] = len(file_rows) - _MAX_EVIDENCE_ROWS
                    add(_finding(identifier, "Instruction files over 8 KB are sent with every request",
                                 "{} local instruction file(s) exceed 8 KB. Their observed request count and "
                                 "cache-read rates imply about ${:,.2f} less at a 4 KB size.".format(len(file_rows), impact),
                                 "Keep commands and conventions in the always-loaded instructions; move reference material to files read when needed.",
                                 "high" if impact > 20 else "low", impact,
                                 file_evidence, ["claude", "codex"], local_only=True,
                                 limitations=["Only file sizes are inspected; file paths and contents are omitted.",
                                              "The estimate assumes a 4 KB target and cached context pricing."]))
    else:
        unsupported.append(_unsupported(identifier,
                                        "This detector reads local instruction-file metadata and is unavailable outside the local-device scope."))

    # 15. Starting context compared with the user's own lean sessions.
    if sessions is not None:
        identifier = "heavy-openers"
        families = [s for s in (sessions or ()) if _model_family(s.get("source")) in ("claude", "codex")
                    and not s.get("subagent")]
        family_rows = [s for s in families if s.get("open_ctx") is not None]
        if families and len(family_rows) != len(families):
            unsupported.append(_unsupported(identifier, "Some local Claude/Codex sessions have no opening-context measurement.",
                                            ("sessions.open_ctx",)))
        elif families and _require_fields(family_rows, ("open_ctx",), identifier, unsupported):
            groups = []
            total_impact = 0.0
            used_tools = []
            for family in ("claude", "codex"):
                selected = [s for s in family_rows if _model_family(s.get("source")) == family]
                if len(selected) < 5:
                    continue
                values = sorted(_number(s.get("open_ctx")) for s in selected)
                lean = values[int(len(values) * 0.1)]
                median = values[len(values) // 2]
                if median < lean * 1.3 or median < 20000:
                    continue
                chosen_records = [r for r in records if _model_family(r.get("source")) == family]
                calls, per = _cached_token_cost(chosen_records, lambda row: True, family == "codex", payload)
                if not calls:
                    continue
                req_per_session = calls / max(1, len(selected))
                extra = sum(max(0.0, _number(s.get("open_ctx")) - lean) for s in selected)
                impact = extra * req_per_session * (per / max(1, calls))
                if impact <= 0:
                    continue
                total_impact += impact
                used_tools.append(family)
                groups.append({"tool": _TOOL_LABELS.get(family, family), "sessions": len(selected),
                               "median_opening_tokens": median, "lean_opening_tokens": lean,
                               "estimated_difference": impact})
            if groups and total_impact >= 1:
                add(_finding(identifier, "Sessions start heavier than they need to",
                             "Your typical session opened above the leanest observed sessions; the difference implies "
                             "about ${:,.2f} in additional cached context cost.".format(total_impact),
                             "Review unused MCP servers, long always-loaded instructions and stale memory, then compare a fresh session.",
                             "high" if total_impact > 30 else "low", total_impact,
                             {"tools": groups}, used_tools,
                             limitations=["The opening-context comparison is an estimate based on the 10th percentile of this range."]))
    elif sessions is None:
        pass

    # 16. Claude Code rereads and generated-folder reads. Weighted cache-write
    # price uses each selected date/model's recorded rates.
    identifier = "read-waste"
    reads = _row_date_filter(payload, "reads", ("date", "source", "project", "device", "reads", "rereads",
                                                "junk", "reread_tok", "junk_tok"),
                             context, policy, unsupported, identifier)
    if reads is not None:
        reread_count = sum(_number(row.get("rereads")) for row in reads)
        junk_count = sum(_number(row.get("junk")) for row in reads)
        reread_tokens = sum(_number(row.get("reread_tok")) for row in reads)
        junk_tokens = sum(_number(row.get("junk_tok")) for row in reads)
        read_count = sum(_number(row.get("reads")) for row in reads)
        wasted = reread_tokens + junk_tokens
        if wasted >= 20000:
            claude_records = [r for r in records if _model_family(r.get("source")) == "claude"
                             and r.get("model") != "(user)"]
            by_day_project = {}
            for record in claude_records:
                key = (record.get("date"), record.get("project"))
                by_day_project.setdefault(key, []).append(record)
            fallback_rate = _cache_write_rate(claude_records, payload)
            impact = 0.0
            for row in reads:
                rate_rows = by_day_project.get((row.get("date"), row.get("project")))
                rate = _cache_write_rate(rate_rows, payload) if rate_rows else fallback_rate
                impact += (_number(row.get("reread_tok")) + _number(row.get("junk_tok"))) * rate
            if impact >= 0.5:
                add(_finding(identifier, "Reads that added nothing new used many tokens",
                             "{:,.0f} of {:,.0f} file reads repeated unchanged files or read generated/dependency folders, "
                             "adding {:,.0f} tokens to context.".format(reread_count + junk_count, read_count, wasted),
                             "Point the agent at source files, exclude generated folders and request a specific range for large files.",
                             "low", impact,
                             {"rereads": reread_count, "generated_or_dependency_reads": junk_count,
                              "reread_tokens": reread_tokens, "generated_or_dependency_tokens": junk_tokens,
                              "estimated_cache_write_cost": impact}, ["claude"],
                             limitations=["This is a lower-bound cache-write cost estimate for the added tokens."]))

    # 17. Installed Claude Skills/agents that did not appear in observed usage.
    identifier = "unused-installed"
    if local_allowed:
        installed = payload.get("installed")
        if not isinstance(installed, dict) or not isinstance(installed.get("items"), list):
            unsupported.append(_unsupported(identifier, "The local installed Skills/agents inventory is unavailable.",
                                            ("installed.items",)))
        elif not _pass_source("claude", context):
            pass
        else:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1 if _valid_day(start) and _valid_day(end) else 0
            items = installed["items"]
            used_rows = installed.get("used")
            skill_rows = payload.get("skills")
            if days >= 14:
                if not isinstance(used_rows, list) or not isinstance(skill_rows, list):
                    unsupported.append(_unsupported(identifier, "Usage evidence for installed Skills/agents is unavailable.",
                                                    ("installed.used", "skills")))
                elif not _has_fields(items, ("kind", "name", "scope", "project", "desc_chars")):
                    unsupported.append(_unsupported(identifier, "The installed inventory is missing fields needed for this check.",
                                                    ("kind", "name", "scope", "project", "desc_chars")))
                else:
                    normalize = lambda name: str(name or "").lower().split(":")[-1]
                    used = set()
                    for row in used_rows:
                        if isinstance(row, dict) and _date_in(row, context):
                            used.add(str(row.get("kind")) + "\t" + normalize(row.get("name")))
                    for row in skill_rows:
                        if isinstance(row, dict) and _date_in(row, context) and _pass_device(row, context, payload, policy):
                            used.add("skill\t" + normalize(row.get("name")))
                    idle = [item for item in items if str(item.get("kind")) + "\t" + normalize(item.get("name")) not in used]
                    if idle:
                        token_count = sum(_number(item.get("desc_chars")) / 4.0 for item in idle)
                        claude_records = [r for r in local_records if _model_family(r.get("source")) == "claude"]
                        calls, per = _cached_token_cost(claude_records, lambda row: True, False, payload)
                        impact = token_count * per
                        add(_finding(identifier, "Installed Skills or agents were not used in this range",
                                     "{} local Skill/agent description(s), about {:,.0f} tokens, were not observed in at least two weeks of use.".format(
                                         len(idle), token_count),
                                     "Remove entries you no longer use, or move project-specific Skills/agents into that project.",
                                     evidence={"unused_items": len(idle), "estimated_description_tokens": token_count,
                                               "claude_requests": calls},
                                     tools=["claude"], local_only=True,
                                     limitations=["Only description lengths are used; names, paths and contents are omitted.",
                                                  "The cost estimate uses cache-read prices for the selected local requests."]))
    else:
        unsupported.append(_unsupported(identifier,
                                        "This detector reads local installed Skills/agent metadata and is unavailable outside the local-device scope."))

    # 18. MCP load/call shape, already added through the local inventory helper.
    add(_find_mcp_shape(payload, context, local_allowed, unsupported))

    # Source/device coverage and degradation notes are public-safe only through
    # the query helper; internal filter strings and payload metadata stay private.
    coverage = meta.get("coverage") or {}
    caveats = [_CACHE_CAVEAT, _ESTIMATE_CAVEAT]
    caveats.extend(meta.get("warnings") or ())
    if sessions is not None and _integer(payload.get("sessions_total")) > len(payload.get("sessions") or ()):
        caveats.append("The payload caps the session list; session findings may omit older sessions.")
    if sessions is not None and any(s.get("detail_limited") for s in sessions):
        caveats.append("Some archived sessions retain summary-only detail, so their findings may use whole-session totals.")
    if coverage.get("records_payload_complete") is False:
        caveats.append("The usage record payload is incomplete; findings based on it may be partial.")
    all_findings = _sorted_findings(findings)
    findings = all_findings[:limit]
    for finding in findings:
        finding["scope"]["range"] = meta.get("range")
    return {
        "findings": findings, "unsupported": _dedupe_unsupported(unsupported),
        "scope": meta, "coverage": coverage,
        "caveats": list(dict.fromkeys(caveats)), "limit": limit,
        "findings_truncated": len(all_findings) > limit,
        "findings_overlap": True,
    }
