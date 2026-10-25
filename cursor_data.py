"""Read Cursor's local conversation metadata from its SQLite store.

The parser keeps only bounded telemetry fields from each message.  Message
text, tool arguments/results, file contents, auth state, and binary agentKv blobs
are never returned by the public timeline reader or retained in aggregates.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import sqlite3
import urllib.parse
from collections import defaultdict


MAX_ROW_BYTES = 16 * 1024 * 1024
MAX_AGENT_JSON_BYTES = 4 * 1024 * 1024
MAX_TIMELINE_EVENTS = 5_000
MAX_TIMELINE_SCAN_ROWS = 100_000
MAX_STORE_ROWS = 200_000
MAX_WORKSPACE_DIRS = 10_000
CURSOR_SCHEMA = 1
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_MODEL_RE = re.compile(r"^[A-Za-z0-9@._:/+-]{1,120}$")
_TOOL_RE = re.compile(r"^[A-Za-z0-9_.:/+-]{1,100}$")
_PATH_RE = re.compile(
    r"(?:/Users/|/home/|[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2})"
    r"[^/\\\"\s]+[\\/]{1,2}"
    r"(?:Documents[\\/]{1,2}GitHub|Documents|Desktop|repos?|code|dev|projects|src|work)"
    r"[\\/]{1,2}([^/\\\"\s]+)", re.I)
_SAFE_STATUSES = frozenset((
    "pending", "running", "done", "completed", "success", "error", "failed",
    "canceled", "cancelled", "waiting", "queued", "interrupted", "skipped",
    "accepted", "rejected", "partial", "started", "finished", "blocked",
))
_VIEW_KINDS = {
    "activity": frozenset(("session", "user_turn", "model", "subagent", "checkpoint", "compaction", "error")),
    "requests": frozenset(("request",)),
    "tools": frozenset(("tool",)),
    "compactions": frozenset(("compaction",)),
}


class CursorReadError(Exception):
    """A source read or identity check failed; messages never include local paths."""


def _int(value, maximum=10**15):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= maximum:
        return None
    return value


def _safe_id(value):
    return value if isinstance(value, str) and _ID_RE.fullmatch(value) else None


def _safe_model(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value.lower() == "default" or not _MODEL_RE.fullmatch(value):
        return None
    return value[:120]


def _safe_tool(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or not _TOOL_RE.fullmatch(value):
        return None
    return value[:100]


def _safe_status(value):
    if isinstance(value, str):
        normalized = value.strip().lower().replace(" ", "_")
        return normalized if normalized in _SAFE_STATUSES else None
    return None


def _timestamp(value):
    if not isinstance(value, str) or not value or len(value) > 80:
        return None
    try:
        _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None
    return value


def _from_ms(value):
    value = _int(value, 10**16)
    if value is None or value == 0:
        return None
    try:
        return _dt.datetime.fromtimestamp(value / 1000, tz=_dt.timezone.utc).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None


def _timestamp_value(value):
    if isinstance(value, str):
        return _timestamp(value)
    return _from_ms(value)


def _leaf(path):
    if not isinstance(path, str) or not path:
        return None
    parts = re.split(r"[/\\]+", path.rstrip("/\\"))
    return parts[-1] if parts and parts[-1] else None


def _file_uri_path(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme.lower() != "file":
            return value if os.path.isabs(value) or re.match(r"^[A-Za-z]:[\\/]", value) else None
        path = urllib.parse.unquote(parsed.path)
        if parsed.netloc and parsed.netloc.lower() != "localhost":
            path = "//" + parsed.netloc + path
        if re.match(r"^/[A-Za-z]:[\\/]", path):
            path = path[1:]
        return path or None
    except (TypeError, ValueError):
        return None


def _decode_json(value, limit=MAX_ROW_BYTES):
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytes):
        if len(value) > limit:
            return None, True
        try:
            value = value.decode("utf-8")
        except UnicodeDecodeError:
            return None, True
    if not isinstance(value, str):
        return None, False
    if len(value.encode("utf-8", "replace")) > limit:
        return None, True
    try:
        return json.loads(value), False
    except (json.JSONDecodeError, TypeError, ValueError):
        return None, False


def _open_ro_sqlite(db_path):
    absolute = os.path.abspath(os.fspath(db_path))
    uri = "file:" + urllib.parse.quote(absolute, safe="/:") + "?mode=ro&busy_timeout=5000"
    con = None
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5.0)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error:
        try:
            if con is not None:
                con.close()
        except sqlite3.Error:
            pass
    immutable = "file:" + urllib.parse.quote(absolute, safe="/:") + "?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(immutable, uri=True, timeout=5.0)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error as exc:
        try:
            if con is not None:
                con.close()
        except sqlite3.Error:
            pass
        raise CursorReadError("Cursor DatabaseError: database unavailable") from exc


def _workspace_map(db_path):
    """Map a composer id to one explicit workspace URI, when unambiguous."""
    user_dir = os.path.dirname(os.path.dirname(os.path.abspath(os.fspath(db_path))))
    root = os.path.join(user_dir, "workspaceStorage")
    candidates = defaultdict(set)
    if not os.path.isdir(root):
        return {}, False
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return {}, False
    if len(names) > MAX_WORKSPACE_DIRS:
        return {}, True
    for name in names:
        folder_path = os.path.join(root, name)
        if os.path.islink(folder_path) or not os.path.isdir(folder_path):
            continue
        workspace_json = os.path.join(folder_path, "workspace.json")
        try:
            if os.path.islink(workspace_json) or os.path.getsize(workspace_json) > 1024 * 1024:
                continue
            with open(workspace_json, encoding="utf-8") as source:
                workspace = json.load(source)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
            continue
        cwd = _file_uri_path(workspace.get("folder") if isinstance(workspace, dict) else None)
        if not cwd:
            continue
        state_db = os.path.join(folder_path, "state.vscdb")
        if not os.path.isfile(state_db) or os.path.islink(state_db):
            continue
        try:
            con = _open_ro_sqlite(state_db)
        except CursorReadError:
            continue
        try:
            tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "ItemTable" not in tables:
                continue
            rows = con.execute(
                "SELECT CASE WHEN length(value) <= ? THEN value ELSE NULL END "
                "FROM ItemTable WHERE key IN (?, ?)",
                (2 * 1024 * 1024, "composer.composerData", "composer.composerHeaders"),
            )
            for (value,) in rows:
                if value is None:
                    continue
                parsed, oversized = _decode_json(value, 2 * 1024 * 1024)
                if oversized or not isinstance(parsed, dict):
                    continue
                composers = parsed.get("allComposers")
                if not isinstance(composers, list):
                    continue
                for item in composers:
                    cid = _safe_id(item.get("composerId")) if isinstance(item, dict) else None
                    if cid:
                        candidates[cid].add(cwd)
        except sqlite3.Error:
            continue
        finally:
            con.close()
    return ({cid: next(iter(paths)) for cid, paths in candidates.items() if len(paths) == 1}, False)


def workspace_signature(db_path):
    """Return bounded workspaceStorage file signatures without reading values."""
    user_dir = os.path.dirname(os.path.dirname(os.path.abspath(os.fspath(db_path))))
    root = os.path.join(user_dir, "workspaceStorage")
    try:
        names = sorted(os.listdir(root))
        root_stat = os.stat(root)
    except OSError:
        return []
    result = [("root", root_stat.st_mtime_ns, len(names))]
    truncated = len(names) > MAX_WORKSPACE_DIRS
    for name in names[:MAX_WORKSPACE_DIRS]:
        folder = os.path.join(root, name)
        if os.path.islink(folder) or not os.path.isdir(folder):
            continue
        identity = hashlib.sha256(name.encode("utf-8", "surrogatepass")).hexdigest()[:16]
        for basename in ("workspace.json", "state.vscdb", "state.vscdb-wal"):
            path = os.path.join(folder, basename)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if os.path.islink(path):
                continue
            result.append((identity, basename, st.st_size, st.st_mtime_ns))
    if truncated:
        result.append(("truncated", len(names)))
    return result


def _meta_from_json(obj):
    if not isinstance(obj, dict):
        return {}
    config = obj.get("modelConfig") if isinstance(obj.get("modelConfig"), dict) else {}
    title = obj.get("name") or obj.get("subtitle")
    mode = obj.get("unifiedMode")
    if isinstance(mode, bool) or not isinstance(mode, (str, int)):
        mode = None
    elif isinstance(mode, str):
        mode = mode[:80]
    prompt_breakdown = obj.get("promptTokenBreakdown") if isinstance(obj.get("promptTokenBreakdown"), dict) else {}
    context_tokens = _int(prompt_breakdown.get("totalUsedTokens"))
    context_source = "promptTokenBreakdown" if context_tokens is not None and context_tokens > 0 else None
    if context_tokens is None or context_tokens == 0:
        fallback = _int(obj.get("contextTokensUsed"))
        if fallback is not None:
            context_tokens = fallback
            context_source = "contextTokensUsed"
    subagent_children = []
    values = obj.get("subagentComposerIds")
    if isinstance(values, list):
        subagent_children.extend(cid for cid in (_safe_id(value) for value in values) if cid)
    child_composers = obj.get("subComposerIds")
    child_composer_count = (len({cid for cid in (_safe_id(value) for value in child_composers) if cid})
                            if isinstance(child_composers, list) else 0)
    context_percent = obj.get("contextUsagePercent")
    if (isinstance(context_percent, bool) or not isinstance(context_percent, (int, float))
            or not 0 <= context_percent <= 100):
        context_percent = None
    project_hints = set()
    for field in ("workspaceIdentifier", "trackedGitRepos"):
        values = []
        _strings_in(obj.get(field), values)
        for value in values:
            path = _file_uri_path(value)
            leaf = _leaf(path) if path else None
            if leaf and leaf not in (".", ".."):
                project_hints.add(leaf)
            for match in _PATH_RE.finditer(value):
                leaf = match.group(1).strip()
                if leaf and leaf not in (".", ".."):
                    project_hints.add(leaf)
    return {
        "created": _timestamp_value(obj.get("createdAt")),
        "created_ms": _int(obj.get("createdAt")),
        "updated": _timestamp_value(obj.get("lastUpdatedAt")) or _timestamp_value(obj.get("createdAt")),
        "updated_ms": _int(obj.get("lastUpdatedAt")) or _int(obj.get("createdAt")),
        "context_asof": _timestamp_value(obj.get("lastUpdatedAt")),
        "title": title[:160] if isinstance(title, str) and title.strip() else None,
        "current_model": _safe_model(config.get("modelName")),
        "max_mode": config.get("maxMode") is True,
        "mode": mode,
        "lines_add": _int(obj.get("totalLinesAdded")) or 0,
        "lines_del": _int(obj.get("totalLinesRemoved")) or 0,
        "archived": obj.get("isArchived") is True,
        "subagent": obj.get("isSubagent") is True,
        "subagent_type": _safe_tool(obj.get("subagentTypeName")),
        "children": list(dict.fromkeys(subagent_children)),
        "child_composer_count": max(child_composer_count, _int(obj.get("numSubComposers")) or 0),
        "project_hints": sorted(project_hints),
        "subagents": len(set(subagent_children)),
        "context_tokens": context_tokens,
        "context_source": context_source,
        "context_percent": float(context_percent) if context_percent is not None else None,
        "context_limit": _int(obj.get("contextTokenLimit")),
    }


def _merge_meta(target, extra):
    for key, value in extra.items():
        if key in ("children", "project_hints"):
            target[key] = list(dict.fromkeys((target.get(key) or []) + (value or [])))
        elif value is not None and value != "":
            if key in ("lines_add", "lines_del"):
                target[key] = value
            elif key in ("subagents", "child_composer_count"):
                target[key] = max(target.get(key) or 0, value)
            else:
                target[key] = value


def _bubble_key(key):
    if not isinstance(key, str) or not key.startswith("bubbleId:"):
        return None, None
    rest = key[len("bubbleId:"):]
    composer_id, separator, bubble_id = rest.partition(":")
    composer_id = _safe_id(composer_id)
    if not composer_id:
        return None, None
    bubble_id = _safe_id(bubble_id) if separator else None
    if not bubble_id:
        # A few Cursor bubble keys contain punctuation outside the conventional
        # ID alphabet. Keep a stable opaque identity without retaining the raw
        # key or dropping its otherwise valid message.
        bubble_id = "h" + hashlib.sha256(rest.encode("utf-8", "surrogatepass")).hexdigest()[:24]
    return composer_id, bubble_id


def _strings_in(value, out, depth=0):
    if depth > 5:
        return
    if isinstance(value, str):
        if len(value) <= 4096:
            out.append(value)
    elif isinstance(value, dict):
        for child in value.values():
            _strings_in(child, out, depth + 1)
    elif isinstance(value, list):
        for child in value[:200]:
            _strings_in(child, out, depth + 1)


def _bubble_summary(obj, bubble_id):
    if not isinstance(obj, dict):
        return None
    bubble_type = _int(obj.get("type"), 100)
    if bubble_type not in (1, 2):
        return None
    created = _timestamp_value(obj.get("createdAt"))
    model_info = obj.get("modelInfo") if isinstance(obj.get("modelInfo"), dict) else {}
    token_count = obj.get("tokenCount") if isinstance(obj.get("tokenCount"), dict) else {}
    tool_data = obj.get("toolFormerData") if isinstance(obj.get("toolFormerData"), dict) else {}
    hints = set()
    for field in ("attachedFolders", "attachedFoldersNew", "workspaceUris", "recentlyViewedFiles", "relevantFiles"):
        values = []
        _strings_in(obj.get(field), values)
        for value in values:
            for match in _PATH_RE.finditer(value):
                leaf = match.group(1).strip()
                if leaf and leaf not in (".", ".."):
                    hints.add(leaf)
    tool = _safe_tool(tool_data.get("name") or tool_data.get("tool"))
    request_id = _safe_id(obj.get("requestId"))
    return {
        "bubble_id": bubble_id,
        "type": bubble_type,
        "created_at": created,
        "started_at": _from_ms(obj.get("startedAtMs")),
        "completed_at": _from_ms(obj.get("completedAtMs")),
        "turn_duration_ms": _int(obj.get("turnDurationMs")),
        "model": _safe_model(model_info.get("modelName")),
        "requested_model": _safe_model(model_info.get("modelName")) if bubble_type == 1 else None,
        "input_tokens": _int(token_count.get("inputTokens")),
        "output_tokens": _int(token_count.get("outputTokens")),
        "input_present": "inputTokens" in token_count and _int(token_count.get("inputTokens")) is not None,
        "output_present": "outputTokens" in token_count and _int(token_count.get("outputTokens")) is not None,
        "thinking_ms": _int(obj.get("thinkingDurationMs")),
        "request_id": request_id,
        "tool": tool,
        "tool_call_id": _safe_id(tool_data.get("toolCallId")),
        "model_call_id": _safe_id(tool_data.get("modelCallId")),
        "tool_index": _int(tool_data.get("toolIndex"), 10**9),
        "tool_status": _safe_status(tool_data.get("status")),
        "checkpoint": bool(_safe_id(obj.get("checkpointId"))),
        "project_hints": sorted(hints),
        "has_timestamp": bool(created),
    }


def _agent_messages(rows):
    by_request = defaultdict(list)
    tools = []
    json_count = 0
    undecoded_count = 0
    for row in rows:
        if len(row) == 3:
            key, value, oversized = row
        else:
            key, value = row
            oversized = False
        if oversized:
            undecoded_count += 1
            continue
        raw, oversized = value, False
        if isinstance(raw, memoryview):
            raw = raw.tobytes()
        if isinstance(raw, bytes):
            if len(raw) > MAX_AGENT_JSON_BYTES:
                undecoded_count += 1
                continue
            if not raw.lstrip(b" \t\r\n").startswith(b"{"):
                undecoded_count += 1
                continue
        elif not isinstance(raw, str) or not raw.lstrip().startswith("{"):
            undecoded_count += 1
            continue
        obj, oversized = _decode_json(raw, MAX_AGENT_JSON_BYTES)
        if oversized or not isinstance(obj, dict):
            undecoded_count += 1
            continue
        json_count += 1
        options = obj.get("providerOptions") if isinstance(obj.get("providerOptions"), dict) else {}
        cursor = options.get("cursor") if isinstance(options.get("cursor"), dict) else {}
        request_id = _safe_id(cursor.get("requestId"))
        model = _safe_model(cursor.get("modelName"))
        if request_id:
            by_request[request_id].append({"role": obj.get("role"), "model": model})
        content = obj.get("content")
        if request_id and isinstance(content, list):
            for block in content[:500]:
                if not isinstance(block, dict) or block.get("type") != "tool-call":
                    continue
                name = _safe_tool(block.get("toolName"))
                call_id = _safe_id(block.get("toolCallId"))
                if name and call_id:
                    tools.append({"request_id": request_id, "tool_call_id": call_id, "tool": name})
    return by_request, tools, json_count, undecoded_count


def _enrich_agent_requests(composers, bubbles, agent_requests, agent_tools):
    request_bubbles = defaultdict(list)
    seen_tool_ids = set()
    for composer_id, rows in bubbles.items():
        for bubble in rows:
            request_id = bubble.get("request_id")
            if request_id:
                request_bubbles[request_id].append((composer_id, bubble))
            tool_call_id = bubble.get("tool_call_id")
            if tool_call_id:
                seen_tool_ids.add(tool_call_id)
    for request_id, agent_rows in agent_requests.items():
        matched = request_bubbles.get(request_id, [])
        models = {row.get("model") for row in agent_rows if row.get("model")}
        if (len(matched) == 1 and len(models) == 1
                and all(row.get("role") == "assistant" for row in agent_rows)):
            composer_id, bubble = matched[0]
            if bubble.get("type") == 2 and not bubble.get("model"):
                bubble["model"] = next(iter(models))
                bubble["model_source"] = "agent_request_id"
                composers.setdefault(composer_id, {}).setdefault("agent_linked", True)
    for item in agent_tools:
        if item["tool_call_id"] in seen_tool_ids:
            continue
        matched = request_bubbles.get(item["request_id"], [])
        if (len(matched) != 1 or
                not any(row.get("role") == "assistant" for row in agent_requests.get(item["request_id"], []))):
            continue
        composer_id, bubble = matched[0]
        if bubble.get("type") != 2:
            continue
        bubble.setdefault("extra_tools", []).append({
            "tool": item["tool"], "tool_call_id": item["tool_call_id"],
        })
        seen_tool_ids.add(item["tool_call_id"])


def _count_query_rows(con, query, params=()):
    row = con.execute(query, params).fetchone()
    return int(row[0] or 0) if row else 0


def read_store(db_path, composer_id=None, *, max_scan_rows=MAX_STORE_ROWS, strict=True, _connection=None):
    """Read one or all conversations into bounded, prompt-free telemetry fields.

    The private parser helper returns only allowlisted metadata; it never
    returns message text, tool arguments/results, arbitrary JSON, or raw blobs.
    """
    if composer_id is not None and not _safe_id(composer_id):
        raise CursorReadError("Invalid Cursor conversation id")
    try:
        max_scan_rows = max(0, min(int(max_scan_rows), MAX_STORE_ROWS))
    except (TypeError, ValueError, OverflowError):
        max_scan_rows = MAX_STORE_ROWS
    owns_connection = _connection is None
    con = _connection if _connection is not None else _open_ro_sqlite(db_path)
    result = {
        "composers": {}, "bubbles": defaultdict(list), "workspace": {},
        "agent_blob_count": 0, "agent_json_count": 0,
        "agent_unparsed_count": 0, "detail_partial": False,
        "storage_scan_truncated": False, "workspace_scan_truncated": False,
    }
    try:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "cursorDiskKV" not in tables:
            raise CursorReadError("Cursor source schema unavailable")
        header_cols = set()
        header_count = 0
        select = []
        composer_count = _count_query_rows(
            con, "SELECT COUNT(*) FROM cursorDiskKV WHERE key LIKE 'composerData:%'")
        bubble_count = _count_query_rows(
            con, "SELECT COUNT(*) FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'")
        agent_count = _count_query_rows(
            con, "SELECT COUNT(*) FROM cursorDiskKV WHERE key LIKE 'agentKv:blob:%'")
        if "composerHeaders" in tables:
            header_cols = {row[1] for row in con.execute("PRAGMA table_info(composerHeaders)")}
            header_count = _count_query_rows(con, "SELECT COUNT(*) FROM composerHeaders")
        row_count = header_count + composer_count + bubble_count + agent_count
        if strict and row_count > max_scan_rows:
            raise CursorReadError("Cursor source exceeds bounded row budget")
        if max_scan_rows <= 0 and row_count:
            result["storage_scan_truncated"] = True
            result["detail_partial"] = True
            return result
        remaining = max_scan_rows

        if "composerHeaders" in tables:
            wanted = ("composerId", "createdAt", "lastUpdatedAt", "isArchived", "isSubagent",
                      "subagentTypeName", "value")
            select = [field for field in wanted if field in header_cols]
            if "composerId" in select:
                value_expr = ('CASE WHEN length("value") <= ? THEN "value" ELSE NULL END AS "value", '
                              '(length("value") > ?) AS "oversized", ') if "value" in select else ""
                query_fields = [f'"{field}"' for field in select if field != "value"]
                order = (' ORDER BY CASE WHEN "composerId" = ? THEN 0 ELSE 1 END, "composerId"'
                         if composer_id is not None else ' ORDER BY "composerId"')
                query = "SELECT " + value_expr + ", ".join(query_fields) + " FROM composerHeaders" + order + " LIMIT ?"
                if value_expr:
                    params = ((MAX_ROW_BYTES, MAX_ROW_BYTES, composer_id, remaining)
                              if composer_id is not None else (MAX_ROW_BYTES, MAX_ROW_BYTES, remaining))
                    cursor = con.execute(query, params)
                    row_names = ["value", "oversized"] + [field for field in select if field != "value"]
                else:
                    params = ((composer_id, remaining) if composer_id is not None else (remaining,))
                    cursor = con.execute(query, params)
                    row_names = query_fields
                loaded = 0
                for raw_row in cursor:
                    loaded += 1
                    row = dict(zip(select, raw_row))
                    if value_expr:
                        row = {field: raw_row[row_names.index(field)] for field in select}
                        row["oversized"] = bool(raw_row[1])
                    cid = _safe_id(row.get("composerId"))
                    if not cid:
                        continue
                    meta = result["composers"].setdefault(cid, {})
                    value = None
                    if row.get("value") is not None:
                        value, bad_json = _decode_json(row.get("value"))
                        if row.get("oversized") or bad_json or not isinstance(value, dict):
                            if strict:
                                raise CursorReadError("Cursor header metadata is malformed")
                            result["detail_partial"] = True
                            value = None
                    if isinstance(value, dict):
                        _merge_meta(meta, _meta_from_json(value))
                    _merge_meta(meta, {
                        "created": _timestamp_value(row.get("createdAt")),
                        "created_ms": _int(row.get("createdAt")),
                        "updated": _timestamp_value(row.get("lastUpdatedAt")) or _timestamp_value(row.get("createdAt")),
                        "updated_ms": _int(row.get("lastUpdatedAt")) or _int(row.get("createdAt")),
                        "archived": bool(row.get("isArchived")),
                        "subagent": bool(row.get("isSubagent")),
                        "subagent_type": _safe_tool(row.get("subagentTypeName")),
                    })
                remaining -= loaded
                if header_count > loaded:
                    result["storage_scan_truncated"] = True
                    result["detail_partial"] = True

        composer_order = (" ORDER BY CASE WHEN key = ? THEN 0 ELSE 1 END, key"
                          if composer_id is not None else " ORDER BY key")
        composer_query = (
            "SELECT key, CASE WHEN length(value) <= ? THEN value ELSE NULL END, "
            "(length(value) > ?) FROM cursorDiskKV WHERE key LIKE 'composerData:%'" + composer_order + " LIMIT ?")
        composer_params = ((MAX_ROW_BYTES, MAX_ROW_BYTES, "composerData:" + composer_id, max(0, remaining))
                           if composer_id is not None else (MAX_ROW_BYTES, MAX_ROW_BYTES, max(0, remaining)))
        composer_rows = con.execute(composer_query, composer_params)
        loaded = 0
        for key, value, oversized in composer_rows:
            loaded += 1
            cid = key[len("composerData:"):] if isinstance(key, str) and key.startswith("composerData:") else None
            cid = _safe_id(cid)
            # Cursor leaves SQL NULL composerData values when a composer is
            # deleted. They are tombstones, not malformed JSON metadata.
            if cid and value is None and not oversized:
                continue
            obj, bad_json = _decode_json(value) if value is not None else (None, False)
            if not cid or oversized or bad_json or not isinstance(obj, dict):
                if strict:
                    raise CursorReadError("Cursor composer metadata is malformed")
                result["detail_partial"] = True
                continue
            stored_id = _safe_id(obj.get("composerId"))
            if stored_id and stored_id != cid:
                if strict:
                    raise CursorReadError("Cursor composer identity is inconsistent")
                result["detail_partial"] = True
                continue
            meta = result["composers"].setdefault(cid, {})
            _merge_meta(meta, _meta_from_json(obj))
        remaining -= loaded
        if composer_count > loaded:
            result["storage_scan_truncated"] = True
            result["detail_partial"] = True

        bubble_order = (" ORDER BY CASE WHEN key LIKE ? THEN 0 ELSE 1 END, key"
                        if composer_id is not None else " ORDER BY key")
        bubble_query = (
            "SELECT key, CASE WHEN length(value) <= ? THEN value ELSE NULL END, "
            "(length(value) > ?) FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'" + bubble_order + " LIMIT ?")
        if composer_id is not None:
            bubble_prefix = "bubbleId:" + composer_id + ":%"
            bubble_params = (MAX_ROW_BYTES, MAX_ROW_BYTES, bubble_prefix, max(0, remaining))
        else:
            bubble_params = (MAX_ROW_BYTES, MAX_ROW_BYTES, max(0, remaining))
        bubble_rows = con.execute(bubble_query, bubble_params)
        loaded = 0
        for key, value, oversized in bubble_rows:
            loaded += 1
            cid, bubble_id = _bubble_key(key)
            if not cid or not bubble_id or (composer_id is not None and cid != composer_id):
                if strict:
                    raise CursorReadError("Cursor bubble identity is malformed")
                result["detail_partial"] = True
                continue
            # Deleted bubbles remain as SQL NULL tombstones in some Cursor
            # database revisions; do not turn them into empty messages.
            if value is None and not oversized:
                continue
            obj, bad_json = _decode_json(value) if value is not None else (None, False)
            if oversized or bad_json or not isinstance(obj, dict):
                if strict:
                    raise CursorReadError("Cursor bubble data is malformed")
                result["detail_partial"] = True
                continue
            bubble = _bubble_summary(obj, bubble_id)
            if bubble is None:
                if strict:
                    raise CursorReadError("Cursor bubble format is unsupported")
                result["detail_partial"] = True
                continue
            if not bubble.get("has_timestamp"):
                result["detail_partial"] = True
            result["composers"].setdefault(cid, {})
            result["bubbles"][cid].append(bubble)
        remaining -= loaded
        if bubble_count > loaded:
            result["storage_scan_truncated"] = True
            result["detail_partial"] = True

        agent_rows_cursor = con.execute(
            "SELECT key, CASE WHEN length(value) <= ? THEN value ELSE NULL END, "
            "(length(value) > ?) FROM cursorDiskKV WHERE key LIKE 'agentKv:blob:%' ORDER BY key LIMIT ?",
            (MAX_AGENT_JSON_BYTES, MAX_AGENT_JSON_BYTES, max(0, remaining)),
        )
        result["agent_blob_count"] = agent_count
        agent_requests, agent_tools, agent_json_count, agent_unparsed_count = _agent_messages(agent_rows_cursor)
        result["agent_json_count"] = agent_json_count
        result["agent_unparsed_count"] = agent_unparsed_count
        if agent_count or agent_unparsed_count or result["storage_scan_truncated"]:
            result["detail_partial"] = True
        if not result["storage_scan_truncated"]:
            _enrich_agent_requests(result["composers"], result["bubbles"], agent_requests, agent_tools)
    except sqlite3.Error as exc:
        raise CursorReadError("Cursor DatabaseError: query failed") from exc
    finally:
        if owns_connection:
            con.close()

    result["workspace"], result["workspace_scan_truncated"] = _workspace_map(db_path)
    if result["workspace_scan_truncated"]:
        result["detail_partial"] = True
    parents = defaultdict(list)
    if not result["storage_scan_truncated"]:
        for parent_id, meta in result["composers"].items():
            for child_id in meta.get("children", []):
                parents[child_id].append(parent_id)
    result["parents"] = {child: values[0] for child, values in parents.items() if len(values) == 1}
    for cid, rows in result["bubbles"].items():
        rows.sort(key=lambda item: (_event_time({"timestamp": item.get("created_at")}), item.get("bubble_id") or ""))
        result["composers"].setdefault(cid, {})
    return result


def _event_time(event):
    timestamp = event.get("timestamp")
    if not timestamp:
        return 0.0
    try:
        value = _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return 0.0


def _local_day(timestamp, localize=None):
    """Return the machine-local calendar day for an ISO timestamp.

    `localize` is injectable so midnight-boundary behavior can be tested
    independently of the host running the test suite.
    """
    try:
        value = _dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        local_value = localize(value) if callable(localize) else value.astimezone()
        if not isinstance(local_value, _dt.datetime):
            return ""
        return local_value.date().isoformat()
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return ""


def _in_filter(timestamp, event_filter, localize=None):
    if not isinstance(event_filter, dict):
        return True
    day = _local_day(timestamp, localize)
    if not day:
        return False
    start = event_filter.get("date_from")
    end = event_filter.get("date_to")
    if isinstance(start, str) and len(start) == 10 and day < start:
        return False
    if isinstance(end, str) and len(end) == 10 and day > end:
        return False
    view = event_filter.get("view")
    allowed = _VIEW_KINDS.get(view) if isinstance(view, str) else None
    if allowed is not None and event_filter.get("kind") not in allowed:
        return False
    return True


def _bubble_events(bubbles, created_at=None, children=None, child_meta=None,
                   unique_request_ids=None):
    events = []
    seen_tool_ids = set()
    for bubble in bubbles:
        timestamp = (bubble.get("created_at") or bubble.get("started_at")
                     or bubble.get("completed_at"))
        if not timestamp:
            continue
        bubble_type = bubble.get("type")
        model = bubble.get("model")
        input_tokens = bubble.get("input_tokens")
        output_tokens = bubble.get("output_tokens")
        available_tokens = bool((input_tokens or 0) > 0 or (output_tokens or 0) > 0)
        usage = {}
        if available_tokens:
            if input_tokens is not None:
                usage["input"] = input_tokens
            if output_tokens is not None:
                usage["output"] = output_tokens
        usage_state = "unavailable"
        if available_tokens:
            usage_state = "recorded" if bubble.get("input_present") and bubble.get("output_present") else "partial"
        if bubble_type == 1:
            event = {"timestamp": timestamp, "kind": "user_turn"}
            if bubble.get("requested_model"):
                event["requested_model"] = bubble["requested_model"]
            events.append(event)
        elif bubble_type == 2:
            request_id = bubble.get("request_id")
            request_is_unique = bool(request_id and unique_request_ids is not None
                                     and request_id in unique_request_ids)
            if request_is_unique:
                request_event = {
                    "timestamp": bubble.get("started_at") or timestamp,
                    "kind": "request",
                }
                if model:
                    request_event["model"] = model
                events.append(request_event)
            response_timestamp = bubble.get("completed_at") or timestamp
            event = {"timestamp": response_timestamp, "kind": "model"}
            if model:
                event["model"] = model
            if usage:
                event["usage"] = usage
            event["usage_availability"] = {
                "input_output": usage_state, "cache_read": "unavailable",
                "cache_write": "unavailable", "reasoning": "unavailable",
                "response_output": "unavailable",
            }
            event["usage_status"] = usage_state
            events.append(event)
            if bubble.get("tool"):
                tool_id = bubble.get("tool_call_id")
                if not tool_id or tool_id not in seen_tool_ids:
                    if tool_id:
                        seen_tool_ids.add(tool_id)
                    tool_event = {"timestamp": response_timestamp, "kind": "tool", "label": bubble["tool"]}
                    if bubble.get("tool_status"):
                        tool_event["status"] = bubble["tool_status"]
                    events.append(tool_event)
            for item in bubble.get("extra_tools", []):
                tool_id = item.get("tool_call_id")
                if not tool_id or tool_id not in seen_tool_ids:
                    if tool_id:
                        seen_tool_ids.add(tool_id)
                    tool_event = {"timestamp": response_timestamp, "kind": "tool", "label": item["tool"]}
                    events.append(tool_event)
        if bubble.get("checkpoint"):
            events.append({"timestamp": timestamp, "kind": "checkpoint"})
    if created_at:
        events.append({"timestamp": created_at, "kind": "session", "status": "started"})
    if children and child_meta:
        for child_id in children:
            meta = child_meta.get(child_id) if isinstance(child_meta, dict) else None
            timestamp = meta.get("created") if isinstance(meta, dict) else None
            if timestamp:
                event = {"timestamp": timestamp, "kind": "subagent", "status": "started"}
                label = meta.get("subagent_type")
                if label:
                    event["label"] = label
                events.append(event)
    events.sort(key=lambda item: (_event_time(item), item["kind"], item.get("label", "")))
    return events


def read_conversation(db_path, composer_id, max_events=MAX_TIMELINE_EVENTS, event_filter=None,
                      localize=None):
    """Return one local Cursor conversation as a safe, bounded event snapshot.

    `event_filter`, when provided, is a mapping with optional `date_from`,
    `date_to`, and `view` (`all`, `activity`, `requests`, `tools`, or
    `compactions`). Date/view filtering happens before the event cap.
    """
    composer_id = _safe_id(composer_id)
    if not composer_id:
        raise CursorReadError("Invalid Cursor conversation id")
    try:
        cap = max(0, min(int(max_events), MAX_TIMELINE_EVENTS))
    except (TypeError, ValueError, OverflowError):
        cap = 400
    snapshot = read_store(db_path, composer_id, max_scan_rows=MAX_TIMELINE_SCAN_ROWS,
                          strict=False)
    bubbles = snapshot.get("bubbles", {}).get(composer_id, [])
    meta = snapshot.get("composers", {}).get(composer_id)
    if meta is None and not bubbles:
        raise CursorReadError("Cursor conversation not found")
    raw_truncated = snapshot.get("storage_scan_truncated") is True
    request_occurrences = defaultdict(int)
    for other_bubbles in snapshot.get("bubbles", {}).values():
        for bubble in other_bubbles:
            if bubble.get("request_id"):
                request_occurrences[bubble["request_id"]] += 1
    unique_request_ids = ({request_id for request_id, count in request_occurrences.items() if count == 1}
                          if not raw_truncated else None)
    all_events = _bubble_events(bubbles, (meta or {}).get("created"),
                                (meta or {}).get("children"), snapshot.get("composers"),
                                unique_request_ids)
    filtered = []
    for event in all_events:
        event_filter_with_kind = dict(event_filter or {})
        event_filter_with_kind["kind"] = event.get("kind")
        if _in_filter(event.get("timestamp", ""), event_filter_with_kind, localize):
            filtered.append(event)
    events_truncated = raw_truncated or len(filtered) > cap
    events = filtered[:cap]
    workspace_path = snapshot.get("workspace", {}).get(composer_id)
    project = _leaf(workspace_path) if workspace_path else None
    if not project:
        project = next(iter((meta or {}).get("project_hints", [])), None)
    if not project:
        hints = sorted({hint for bubble in bubbles for hint in bubble.get("project_hints", [])})
        project = hints[0] if len(hints) == 1 else None
    model_names = sorted({bubble.get("model") for bubble in bubbles if bubble.get("type") == 2 and bubble.get("model")})
    known_assistant = sum(1 for bubble in bubbles if bubble.get("type") == 2 and bubble.get("model"))
    assistant_count = sum(1 for bubble in bubbles if bubble.get("type") == 2)
    model_status = ("unavailable" if not known_assistant else
                    "recorded" if known_assistant == assistant_count else "partial")
    usage_bubbles = [b for b in bubbles if b.get("type") == 2 and
                     ((b.get("input_tokens") or 0) > 0 or (b.get("output_tokens") or 0) > 0)]
    usage_status = ("unavailable" if not usage_bubbles else
                    "recorded" if len(usage_bubbles) == assistant_count and all(
                        b.get("input_present") and b.get("output_present")
                        for b in usage_bubbles) else "partial")
    if raw_truncated:
        usage_status = "partial"
        model_status = "partial"
    detail_status = "partial" if (snapshot.get("detail_partial") or snapshot.get("agent_blob_count")) else "recorded"
    return {
        "conversation_id": composer_id,
        "title": (meta or {}).get("title"),
        "project": project,
        "first_activity": min((b["created_at"] for b in bubbles if b.get("created_at")), default=None),
        "last_activity": max((b["created_at"] for b in bubbles if b.get("created_at")), default=None),
        "events": events,
        "events_truncated": events_truncated,
        "usage_status": usage_status,
        "usage_availability": {
            "input_output": usage_status,
            "cache_read": "unavailable", "cache_write": "unavailable",
            "reasoning": "unavailable", "response_output": "unavailable",
        },
        "model_status": model_status,
        "models": model_names,
        "detail_status": "partial" if events_truncated or detail_status == "partial" else "recorded",
        "parent_id": snapshot.get("parents", {}).get(composer_id),
    }
