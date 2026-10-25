"""Read-only, path-private editor projections of retained local aggregates.

The caller holds the parser lock and supplies the dashboard's existing pricing,
identity and conversation-dedup helpers. This module performs no I/O.
"""
import datetime
import hashlib
import json
import math
import ntpath
import posixpath
import re
import time
import urllib.parse


MAX_SESSIONS = 500
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}")
_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
_FIELDS = {"input": "in", "output": "out", "cache_read": "cr", "cache_write": "cc"}
_USAGE_STATUSES = frozenset(("recorded", "partial", "unavailable"))
_USAGE_AVAILABILITY_FIELDS = frozenset((
    "input_output", "cache_read", "cache_write", "reasoning", "response_output",
))


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    try:
        return value if math.isfinite(value) and value >= 0 else 0
    except OverflowError:
        return 0


def _text(value, limit=200):
    return "".join(c for c in value if c.isprintable())[:limit] if isinstance(value, str) else ""


def _path(value):
    """Lexical absolute paths only; never resolve links or inspect the filesystem."""
    if (not isinstance(value, str) or not value or len(value) > 4096
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        return None
    value = value.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", value) or value.startswith("//"):
        drive, _ = ntpath.splitdrive(value)
        if value.startswith("//"):
            parts = drive.replace("\\", "/").split("/")
            if len(parts) != 4 or not parts[2] or not parts[3]:
                return None
        return "windows", ntpath.normcase(ntpath.normpath(value)).replace("\\", "/")
    if value.startswith("/"):
        return "posix", posixpath.normpath(value)
    return None


def _contains(root, cwd):
    if not root or not cwd or root[0] != cwd[0]:
        return False
    base, target = root[1].rstrip("/"), cwd[1].rstrip("/")
    return target == base or target.startswith(base + "/")


def _leaf(value, fallback="(unknown)"):
    text = _text(value, 4096).replace("\\", "/").rstrip("/")
    return _text(text.rsplit("/", 1)[-1], 120) or fallback


def parse_query(query):
    """Validate the entire query before touching aggregates; errors contain no paths."""
    if not isinstance(query, str) or len(query) > 262144 or re.search(r"%(?![0-9a-fA-F]{2})", query):
        raise ValueError("Invalid editor query.")
    try:
        values = urllib.parse.parse_qs(query, keep_blank_values=True, strict_parsing=True,
                                       max_num_fields=22, errors="strict")
    except (ValueError, UnicodeError) as error:
        raise ValueError("Invalid editor query.") from error
    if set(values) - {"scope", "root", "range"}:
        raise ValueError("Unknown editor query parameter.")
    if len(values.get("scope", [])) != 1 or len(values.get("range", ["today"])) != 1:
        raise ValueError("Expected one scope and one range.")
    scope, period = values["scope"][0], values.get("range", ["today"])[0]
    if scope not in ("workspace", "all") or period not in ("today", "7d", "30d"):
        raise ValueError("Invalid editor scope or range.")
    roots = values.get("root", [])
    if scope == "all" and roots:
        raise ValueError("All-project scope does not accept roots.")
    if scope == "workspace" and not 1 <= len(roots) <= 20:
        raise ValueError("Workspace scope requires 1 to 20 absolute roots.")
    normalized = [_path(root) for root in roots]
    if any(root is None for root in normalized):
        raise ValueError("Workspace roots must be absolute local paths.")
    return scope, period, list(dict.fromkeys(normalized))


def _usage(row):
    result = {name: int(_number(row.get(field))) for name, field in _FIELDS.items()}
    result["tokens"] = sum(result.values())
    result["cost"] = _number(row.get("cost"))
    return result


def _empty():
    return dict(tokens=0, input=0, output=0, cache_read=0, cache_write=0, cost=0.0)


def _add(total, usage):
    for field in total:
        total[field] = _number(total[field] + usage.get(field, 0))


def _records(bucket, source, logged, price_usage, first, last):
    rows = []
    for key, row in (bucket or {}).items():
        if not isinstance(key, str) or "\t" not in key or not isinstance(row, dict):
            continue
        date, model = key.split("\t", 1)
        if len(date) != 10 or not first <= date <= last:
            continue
        safe = {key: _number(value) for key, value in row.items() if isinstance(value, (int, float))}
        priced = price_usage(source, model, date, safe, logged)
        rows.append((date, model, _usage(priced), any(_number(row.get(f)) for f in
                     ("in", "out", "cr", "cc", "asst", "user", "req", "tools"))))
    return rows


def _compactions(session, first, last):
    events = session.get("compactions")
    if not isinstance(events, list):
        return 0
    return sum(1 for e in events if isinstance(e, dict) and isinstance(e.get("date"), str)
               and first <= e["date"] <= last)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 48:
        return None
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _overlaps(session, first, last):
    start, end = _timestamp(session.get("start")), _timestamp(session.get("end"))
    return bool(start and end and start[:10] <= last and end[:10] >= first)


def _efforts(session, first, last):
    metadata = session.get("reasoning_effort") or {}
    if not isinstance(metadata, dict) or not isinstance(metadata.get("days", {}), dict):
        return []
    found = set()
    for key, row in metadata.get("days", {}).items():
        if isinstance(key, str) and first <= key.split("\t", 1)[0] <= last and isinstance(row, dict):
            if _number(row.get("unrecorded")):
                found.add("unrecorded")
            levels = row.get("levels") or {}
            if not isinstance(levels, dict):
                continue
            for level, count in levels.items():
                if level in _EFFORTS and _number(count):
                    found.add(level)
    return [level for level in (*_EFFORTS, "unrecorded") if level in found]


def build_snapshot(items, local, query, revision, *, price_usage, deduplicate,
                   identity, claude_parent_key, display_aliases=None, now=None):
    """Project local retained usage without parsing, source reads or ledger mutation."""
    scope, period, roots = parse_query(query)
    now = time.time() if now is None else now
    today = datetime.date.fromtimestamp(now)
    first = (today - datetime.timedelta(days={"today": 1, "7d": 7, "30d": 30}[period] - 1)).isoformat()
    last = today.isoformat()
    files = deduplicate([(path, agg) for path, agg in items
                         if not str(path).startswith("peer:") and agg.get("_device") in (None, "", local)])
    summary, days, models = _empty(), {}, {}
    selected, all_sessions = [], []
    used_keys = set()
    unassigned = 0
    limited_records = False
    for agg in files:
        source = _text(agg.get("source"), 40)
        logged = ((source == "opencode" and str(agg.get("path", "")).endswith(".db"))
                  or source == "openclaw")
        sessions = agg.get("sessions") or []
        # Like dashboard._record_buckets: current stores retain records per session;
        # older stores retain one aggregate bucket, never copied into each child.
        per_session_records = bool(sessions) and all("records" in s for s in sessions)
        aggregate_cwd = None
        if len(sessions) <= 1:
            aggregate_cwd = (_path(sessions[0].get("cwd")) if sessions else None) or _path(agg.get("cwd"))
        aggregate_match = bool(aggregate_cwd and any(_contains(root, aggregate_cwd) for root in roots))
        if scope == "all" or aggregate_match:
            buckets = [s.get("records") or {} for s in sessions] if per_session_records else [agg.get("records", {})]
        else:
            buckets = [s["records"] for s in sessions if "records" in s and
                       any(_contains(root, _path(s.get("cwd"))) for root in roots)]
        for bucket in buckets:
            for date, model, usage, _ in _records(bucket, source, logged, price_usage, first, last):
                _add(summary, usage)
                _add(days.setdefault(date, _empty()), usage)
                _add(models.setdefault((model, source), _empty()), usage)
        for index, session in enumerate(sessions):
            cwd = _path(session.get("cwd")) or aggregate_cwd
            matched = scope == "all" or any(_contains(root, cwd) for root in roots)
            bucket = session.get("records")
            if bucket is None and len(sessions) == 1:
                bucket = agg.get("records", {})
            limited = bucket is None
            rows = _records(bucket, source, logged, price_usage, first, last)
            if limited:
                own = session.get("days") or {}
                if isinstance(own, dict):
                    fallback = {date + "\t" + str(session.get("model") or "Unknown"): row
                                for date, row in own.items() if isinstance(row, dict)}
                    rows = _records(fallback, source, logged, price_usage, first, last)
            compactions = _compactions(session, first, last)
            active = any(row[3] for row in rows) or bool(compactions)
            if limited and not rows:
                active = _overlaps(session, first, last) and any(_number(session.get(f)) for f in
                    ("in", "out", "cr", "cc", "asst", "user", "req"))
            native_id = identity(agg, session) if source in ("claude", "codex", "antigravity") else session.get("_sid")
            if source == "copilot" and not session.get("_sid"):
                path = str(agg.get("path", "")).replace("\\", "/")
                match = _UUID.search(path.rsplit("/", 1)[-1]) or _UUID.search(path)
                native_id = match.group(0) if match else ""
            if (not isinstance(native_id, str) or len(native_id) > 512
                    or any(not c.isprintable() for c in native_id) or _path(native_id)):
                native_id = ""
            if source == "codex" and not _UUID.fullmatch(native_id):
                native_id = ""
            if source == "antigravity" and not _UUID.fullmatch(native_id):
                native_id = ""
            if source == "claude" and not (_UUID.fullmatch(native_id)
                    or claude_parent_key(agg.get("path"), child=True)):
                native_id = ""
            # Claude's namespace includes the exact transcript root; a UUID/title
            # reused in another profile must never create a parent edge.
            namespace = agg.get("path", "")
            if source == "codex":
                namespace = str(namespace).replace("\\", "/").rsplit("/", 1)[-1]
            elif source == "copilot":
                namespace = ""
            fallback_id = [session.get("id"), session.get("start")]
            if not any(fallback_id):
                fallback_id.append(index)
            key = hashlib.sha256(json.dumps([local, source, native_id or fallback_id, namespace],
                                            ensure_ascii=True).encode()).hexdigest()
            if key in used_keys:
                # No full identity is available for some old stores. Distinguish
                # colliding retained metadata without pretending it is a native ID.
                seed = [key, session.get("project"), session.get("title"), session.get("model"),
                        sorted((session.get("records") or {}).keys())]
                key = hashlib.sha256(json.dumps(seed, ensure_ascii=True).encode()).hexdigest()
                ordinal = 0
                while key in used_keys:
                    ordinal += 1
                    key = hashlib.sha256(json.dumps([seed, ordinal], ensure_ascii=True).encode()).hexdigest()
            used_keys.add(key)
            usage = _empty()
            for _, _, row, _ in rows:
                _add(usage, row)
            ranked = {}
            for _, model, row, _ in rows:
                if model != "(user)":
                    ranked[model] = ranked.get(model, 0) + row["tokens"]
            dominant = max(ranked, key=ranked.get) if ranked else session.get("model") or "Unknown"
            project = _leaf(session.get("project") or agg.get("project"))
            alias = (display_aliases or {}).get((agg.get("path", ""), native_id))
            # Antigravity's role is evidence-based. A recorded parent_id or
            # root_id alone never turns an unmarked session into a subagent.
            is_subagent = (session.get("subagent") is True if source == "antigravity"
                           else bool(session.get("subagent")))
            row = {"key": key, "id": native_id, "source": source, "device": local,
                   "title": _text(alias or session.get("title")) or project,
                   "project": project, "subagent": is_subagent,
                   "parent_key": None, "parent_title": None,
                   "parent_status": "unavailable" if is_subagent else "none",
                   "model": _text(dominant, 160), "efforts": _efforts(session, first, last),
                   **usage, "compactions": compactions,
                   "start": _timestamp(session.get("start")), "end": _timestamp(session.get("end")),
                   "archived": bool(agg.get("archived") or session.get("archived")),
                   "detail_limited": limited or not native_id}
            if source == "antigravity":
                status = session.get("usage_status", agg.get("usage_status"))
                if isinstance(status, str) and status in _USAGE_STATUSES:
                    row["usage_status"] = status
                availability = session.get("usage_availability", agg.get("usage_availability"))
                if isinstance(availability, dict):
                    safe_availability = {
                        field: state for field, state in availability.items()
                        if field in _USAGE_AVAILABILITY_FIELDS and isinstance(state, str) and state in _USAGE_STATUSES
                    }
                    if safe_availability:
                        row["usage_availability"] = safe_availability
            parent = None
            if source == "claude":
                recognized = claude_parent_key(agg.get("path"), child=bool(session.get("subagent")))
                if recognized:
                    parent = ("claude", recognized[0] if session.get("subagent") else recognized)
            elif source == "codex":
                parent = ("codex", native_id.lower()) if _UUID.fullmatch(native_id) else None
            elif source == "copilot" and native_id:
                parent = ("copilot", native_id)
                row["usage_status"] = (session.get("copilot_sdk") or {}).get("usage_status", "recorded")
            elif source == "antigravity" and _UUID.fullmatch(native_id):
                # Conversation UUIDs are globally unique in the source format;
                # deduplicated local copies share this exact parent namespace.
                parent = ("antigravity", native_id.lower())
            all_sessions.append((row, parent, session.get("parent_id")))
            if active and cwd is None:
                unassigned += 1
            if active and matched:
                selected.append(row)
                limited_records |= limited
            if source == "copilot" and native_id and not row["subagent"]:
                # SDK child receipts are already included in the parent's totals.
                # Project them as linked detail rows only, outside usage buckets.
                agents = (session.get("copilot_sdk") or {}).get("agents", [])
                seen_agents = set()
                for agent in agents if isinstance(agents, list) else []:
                    if not isinstance(agent, dict):
                        continue
                    aid = _text(agent.get("id"), 512)
                    if not aid or aid in seen_agents:
                        continue
                    seen_agents.add(aid)
                    child_rows = _records(agent.get("records"), source, False, price_usage, first, last)
                    child_usage = _empty()
                    child_models = {}
                    for _, mdl, values, _ in child_rows:
                        _add(child_usage, values)
                        child_models[mdl] = child_models.get(mdl, 0) + values["tokens"]
                    child_key = hashlib.sha256(json.dumps([key, aid]).encode()).hexdigest()
                    child_status = agent.get("usage_status")
                    if child_status not in ("recorded", "partial", "unavailable"):
                        child_status = "unavailable"
                    child = {**row, **child_usage, "key": child_key,
                        "id": native_id + "/agent/" + aid, "title": _text(agent.get("title")) or "Copilot subagent",
                        "subagent": True, "parent_status": "unavailable",
                        "model": _text(max(child_models, key=child_models.get) if child_models else agent.get("model"), 160),
                        "efforts": [], "compactions": _compactions(agent, first, last),
                        "start": _timestamp(agent.get("start")), "end": _timestamp(agent.get("end")),
                        "usage_in_parent": True, "usage_status": child_status,
                        "detail_limited": child_status != "recorded"}
                    all_sessions.append((child, parent, native_id))
                    activity_dates = agent.get("activity_dates")
                    activity = (any(isinstance(date, str) and first <= date <= last for date in activity_dates)
                                if isinstance(activity_dates, list) else _overlaps(agent, first, last))
                    child_active = bool(child_rows) or bool(child["compactions"]) or activity
                    if child_active and cwd is None:
                        unassigned += 1
                    if child_active and matched:
                        selected.append(child)
    selected.sort(key=lambda s: (s.get("end") or "", s["key"]), reverse=True)
    total = len(selected)
    visible = selected[:MAX_SESSIONS]
    visible_keys = {s["key"] for s in visible}
    candidates = {}
    for row, parent, _ in all_sessions:
        if parent and not row["subagent"]:
            candidates.setdefault(parent, []).append(row)
        elif parent and row["source"] == "codex":
            # A child may itself delegate to another child.
            candidates.setdefault(parent, []).append(row)
    for row, parent, parent_id in all_sessions:
        if row["key"] not in visible_keys or not row["subagent"]:
            continue
        if row["source"] == "codex":
            parent = (("codex", parent_id.lower()) if isinstance(parent_id, str)
                      and _UUID.fullmatch(parent_id) else None)
        elif row["source"] == "antigravity":
            # Only an explicit immediate parent_id is link evidence. root_id is
            # retained by the parser for context but cannot stand in for parent_id.
            parent = (("antigravity", parent_id.lower()) if isinstance(parent_id, str)
                      and _UUID.fullmatch(parent_id) else None)
        found = candidates.get(parent, []) if parent else []
        if len(found) == 1 and found[0]["key"] != row["key"]:
            actual = found[0]
            if actual["key"] in visible_keys:
                row.update(parent_key=actual["key"], parent_title=actual["title"], parent_status="linked")
            else:
                row["parent_status"] = "outside_scope"
    summary.update(sessions=total, main_agents=sum(not s["subagent"] for s in selected),
                   subagents=sum(s["subagent"] for s in selected),
                   compactions=sum(s["compactions"] for s in selected))
    limitations = ["Costs are estimates at API list prices.",
                  "Main agents are sessions without a recorded subagent marker.",
                  "Parent links require full recorded source identities; missing links remain unavailable.",
                  "Compactions and reasoning effort show recorded metadata only; older archives may lack detail."]
    if unassigned:
        limitations.append("Some sessions have no recorded absolute working directory and cannot be attributed to a workspace.")
    if limited_records:
        limitations.append("Some archived sessions lack daily model detail; unavailable usage is not copied from their source database.")
    if any(not session["id"] for session in selected):
        limitations.append("Some source stores retain shortened identities only; their full native session IDs are unavailable.")
    if any(session.get("usage_in_parent") for session in selected):
        limitations.append("Copilot child usage is a breakdown already included in its parent; child rows are not added to totals.")
    labels = list(dict.fromkeys(_leaf(path) for _, path in roots))
    return {"schema": 1, "generated_at": now, "date": last, "from": first, "to": last,
            "revision": revision,
            "scope": {"kind": scope, "label": (", ".join(labels) if labels else "All projects"),
                      "roots": labels, "matched_sessions": total, "unassigned_sessions": unassigned},
            "summary": summary,
            "days": [{"date": (today - datetime.timedelta(days=i)).isoformat(),
                      "tokens": days.get((today - datetime.timedelta(days=i)).isoformat(), {}).get("tokens", 0),
                      "cost": days.get((today - datetime.timedelta(days=i)).isoformat(), {}).get("cost", 0)}
                     for i in reversed(range({"today": 1, "7d": 7, "30d": 30}[period]))],
            "models": [{"name": _text(name, 160), "source": source, "tokens": row["tokens"], "cost": row["cost"]}
                       for (name, source), row in sorted(models.items(), key=lambda p: (-p[1]["tokens"], p[0]))
                       if name != "(user)"],
            "sessions": visible, "sessions_total": total, "truncated": total > len(visible),
            "limitations": limitations}
