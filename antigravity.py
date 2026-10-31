"""Bounded, read-only reader for Antigravity local conversation databases.

Antigravity's protobuf format is unstable. This module decodes only fields
needed for usage attribution and safe activity facts; it never returns message
text, prompt text, tool arguments, credentials, or raw protobuf bytes. Unknown or
oversized optional blobs reduce detail rather than discarding usable step data.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import glob
import os
from pathlib import Path
import re
import sqlite3
import stat
import urllib.parse


class AntigravityReadError(RuntimeError):
    """The source is unreadable or its required SQLite schema is unsupported."""


class AntigravityUnsupportedError(AntigravityReadError):
    """The discovered store uses an unsupported Antigravity schema."""


_ROOT_LABELS = {
    "antigravity": "Antigravity App",
    "antigravity-ide": "Antigravity IDE",
    "antigravity-cli": "Antigravity CLI",
}
_ROOT_NAMES = tuple(_ROOT_LABELS)

# Guards, not expected conversation sizes. Column lengths are inspected before
# BLOBs are materialized; generator metadata has reached hundreds of MB upstream.
MAX_ROWS = 100_000
MAX_PROTO_BYTES = 2 * 1024 * 1024
MAX_PROTO_FIELDS = 100_000
MAX_EVENTS = 5_000
MAX_TEXT = 200
MAX_TITLE = 160
MAX_STEP_INDEX = (1 << 31) - 1

_STATUS = {
    0: "unknown", 1: "pending", 2: "running", 3: "done", 4: "invalid",
    5: "cleared", 6: "canceled", 7: "error", 8: "generating",
    9: "waiting", 11: "queued", 12: "interrupted",
}
_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))
_EFFORT_SUFFIX = re.compile(r"\s*\((low|medium|high|xhigh|max)\)\s*$", re.I)


def conversation_roots(home=None):
    """Return only Antigravity's three documented per-user conversation roots."""
    home = os.path.expanduser("~") if home is None else os.fspath(home)
    gemini = os.path.join(home, ".gemini")
    return [(os.path.join(gemini, name), _ROOT_LABELS[name]) for name in _ROOT_NAMES]


def _root_entry(entry):
    if isinstance(entry, (tuple, list)) and len(entry) == 2:
        return os.path.abspath(os.fspath(entry[0])), str(entry[1])
    root = os.path.abspath(os.fspath(entry))
    return root, _ROOT_LABELS.get(os.path.basename(root), "Antigravity IDE")


def _under_root(path, root):
    try:
        real_root = os.path.realpath(root)
        return os.path.commonpath((real_root, os.path.realpath(path))) == real_root
    except (OSError, ValueError):
        return False


def discover_conversations(roots=None):
    """Return ``[(path, editor_label), ...]`` from the exact known roots.

    Symlinked DB files and paths resolving outside their root are skipped.
    Physical aliases are reduced by real path/inode only. Distinct database files
    sharing a conversation UUID must reach the aggregate-level usage selector,
    which can compare parsed usage rather than guessing from file size.
    """
    roots = conversation_roots() if roots is None else roots
    out = []
    seen_paths, seen_inodes = set(), set()
    for entry in roots:
        root, editor = _root_entry(entry)
        directory = os.path.join(root, "conversations")
        if os.path.islink(root) or os.path.islink(directory):
            continue
        for path in sorted(glob.glob(os.path.join(directory, "*.db"))):
            try:
                if os.path.islink(path) or not _under_root(path, root):
                    continue
                st = os.stat(path, follow_symlinks=False)
                if not stat.S_ISREG(st.st_mode):
                    continue
                real = os.path.realpath(path)
                inode = (st.st_dev, st.st_ino)
                if real in seen_paths or inode in seen_inodes:
                    continue
                seen_paths.add(real)
                seen_inodes.add(inode)
                out.append((real, editor))
            except OSError:
                continue
    return sorted(out, key=lambda item: item[0])


def legacy_pb_inventory(roots=None):
    """Count unsupported conversation/implicit ``.pb`` files, without opening them."""
    roots = conversation_roots() if roots is None else roots
    seen, count = set(), 0
    for entry in roots:
        root, _editor = _root_entry(entry)
        if os.path.islink(root):
            continue
        for leaf in ("conversations", "implicit"):
            directory = os.path.join(root, leaf)
            if os.path.islink(directory):
                continue
            for path in glob.glob(os.path.join(directory, "*.pb")):
                try:
                    if os.path.islink(path) or not _under_root(path, root):
                        continue
                    st = os.stat(path, follow_symlinks=False)
                    if stat.S_ISREG(st.st_mode):
                        inode = (st.st_dev, st.st_ino)
                        if inode not in seen:
                            seen.add(inode)
                            count += 1
                except OSError:
                    continue
    return count


def _varint(buf, pos):
    value = 0
    for index in range(10):
        if pos >= len(buf):
            raise ValueError("short protobuf varint")
        byte = buf[pos]
        pos += 1
        if index == 9 and byte > 1:
            raise ValueError("protobuf varint overflows uint64")
        value |= (byte & 0x7f) << (index * 7)
        if byte < 0x80:
            return value, pos
    raise ValueError("protobuf varint is too long")


def _fields(blob):
    """Decode only the protobuf wire types present in verified messages."""
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise ValueError("protobuf value is not bytes")
    buf = memoryview(blob)
    if len(buf) > MAX_PROTO_BYTES:
        raise ValueError("protobuf value exceeds the size limit")
    out = {}
    pos = 0
    count = 0
    while pos < len(buf):
        count += 1
        if count > MAX_PROTO_FIELDS:
            raise ValueError("protobuf field count exceeds the limit")
        tag, pos = _varint(buf, pos)
        number, wire = tag >> 3, tag & 7
        if number == 0 or number > (1 << 29) - 1:
            raise ValueError("invalid protobuf field number")
        if wire == 0:
            value, pos = _varint(buf, pos)
        elif wire == 1:
            end = pos + 8
            if end > len(buf):
                raise ValueError("short protobuf fixed64")
            value, pos = buf[pos:end], end
        elif wire == 2:
            length, pos = _varint(buf, pos)
            end = pos + length
            if end > len(buf):
                raise ValueError("short protobuf bytes")
            value, pos = buf[pos:end], end
        elif wire == 5:
            end = pos + 4
            if end > len(buf):
                raise ValueError("short protobuf fixed32")
            value, pos = buf[pos:end], end
        else:
            raise ValueError("unsupported protobuf wire type")
        out.setdefault(number, []).append(value)
    return out


def _first(fields, number, default=None):
    values = fields.get(number)
    return values[-1] if values else default


def _text(value, limit=MAX_TEXT):
    if isinstance(value, str):
        text = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        try:
            text = bytes(value).decode("utf-8", "replace")
        except (ValueError, MemoryError):
            return None
    else:
        return None
    text = " ".join("".join(ch for ch in text if ch >= " " and ch != "\x7f").split())
    return text[:limit] if text else None


def _message(fields, number):
    value = _first(fields, number)
    return _fields(value) if isinstance(value, memoryview) else {}


def _packed_varints(fields, number):
    result = []
    for value in fields.get(number, ()):
        if isinstance(value, int):
            result.append(value)
            continue
        if not isinstance(value, memoryview):
            continue
        pos = 0
        while pos < len(value):
            item, pos = _varint(value, pos)
            if item <= MAX_STEP_INDEX:
                result.append(item)
            else:
                raise ValueError("packed protobuf index is out of range")
    return result


def _timestamp(blob):
    if blob is None:
        return None
    try:
        fields = _fields(blob)
        sec, nanos = _first(fields, 1, 0), _first(fields, 2, 0)
        if not isinstance(sec, int) or not isinstance(nanos, int):
            return None
        if sec >= (1 << 63):
            sec -= 1 << 64
        if not -62135596800 <= sec <= 253402300799 or not 0 <= nanos < 1_000_000_000:
            return None
        return (datetime.fromtimestamp(sec, timezone.utc) +
                timedelta(microseconds=nanos // 1000)).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _timestamp_field(fields, number):
    value = _first(fields, number)
    return _timestamp(value) if isinstance(value, memoryview) else None


def _safe_label(value, limit=MAX_TEXT):
    # Callers select source fields explicitly. Strip controls/cap length, while
    # preserving slashes used in model ids and git branch names.
    return _text(value, limit)


def _local_path(uri):
    value = _text(uri, 8192)
    if not value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                return None
            value = urllib.parse.unquote(parsed.path)
            if os.name == "nt" and re.match(r"^/[A-Za-z]:/", value):
                value = value[1:]
        elif parsed.scheme:
            return None
        if not os.path.isabs(value):
            return None
        return os.path.normpath(value)
    except (UnicodeError, ValueError):
        return None


def _local_leaf(uri):
    value = _local_path(uri)
    if not value:
        return None
    value = value.replace("\\", "/").rstrip("/")
    return _safe_label(value.rsplit("/", 1)[-1], 100)


def _signed64(value):
    return value - (1 << 64) if isinstance(value, int) and value >= (1 << 63) else value


def _usage(fields):
    """Return documented numeric counters and wire-level field availability."""
    if not fields:
        return None
    mapping = {"in": 2, "out": 3, "cc": 4, "cr": 5,
               "reason": 9, "response_out": 10}
    values, present = {}, {}
    for name, number in mapping.items():
        raw = _first(fields, number)
        present[name] = isinstance(raw, int)
        value = _signed64(raw) if isinstance(raw, int) else 0
        if not isinstance(value, int) or value < 0 or value > 10**15:
            present[name] = False
            value = 0
        values[name] = value
    # Thinking tokens are a subset of output tokens; clamp only when output is
    # actually recorded. An omitted output field must not erase known reasoning.
    if present.get("out"):
        values["reason"] = min(values["reason"], values["out"])
    ids = []
    for number in (11, 7, 12):
        value = _safe_label(_first(fields, number), 160)
        if value:
            ids.append(value)
    model_enum = _first(fields, 1)
    values["request_id"] = ids[0] if ids else None
    values["model_enum"] = model_enum if isinstance(model_enum, int) else None
    values["availability"] = present
    return values


def _usage_id(usage):
    return usage.get("request_id") if usage else None


def _usage_quality(usage, timestamp=None, model=None, completed=False, order=0):
    present = (usage or {}).get("availability", {})
    recorded = sum(bool(present.get(name)) for name in
                   ("in", "out", "cr", "cc", "reason", "response_out"))
    core = int(bool(present.get("in"))) + int(bool(present.get("out")))
    return (recorded, core == 2, bool(completed), bool(timestamp),
            timestamp or "", order, model not in (None, "", "Unknown"))


def _model_names(raw, display):
    # Actual response text wins. Model enums and requested labels are ignored.
    raw_name = _safe_label(raw)
    display_name = _safe_label(display)
    model = raw_name or display_name or "Unknown"
    effort = None
    if display_name:
        match = _EFFORT_SUFFIX.search(display_name)
        if match:
            candidate = match.group(1).lower()
            if candidate in _EFFORTS:
                effort = candidate
    return model, display_name, effort


def _parse_usage_metadata(meta_blob):
    meta = _fields(meta_blob)
    usage = _usage(_message(meta, 9))
    model_info = _message(meta, 24)
    model_name = _first(model_info, 8) or _first(model_info, 12) or _first(model_info, 20)
    display_name = _first(model_info, 20)
    model, display, effort = _model_names(model_name, display_name)
    source = _first(meta, 3)
    created = (_timestamp_field(meta, 1) or _timestamp_field(meta, 32) or
               _timestamp_field(meta, 7))
    finished = _timestamp_field(meta, 8) or _timestamp_field(meta, 7)
    call_blob = _first(meta, 29) or _first(meta, 4)
    call_name = None
    if isinstance(call_blob, memoryview):
        call = _fields(call_blob)
        call_name = _safe_label(_first(call, 9) or _first(call, 2))
    source_step = None
    source_step_blob = _first(meta, 20)
    if isinstance(source_step_blob, memoryview):
        info = _fields(source_step_blob)
        trajectory_id = _safe_label(_first(info, 1), 128)
        source_index = _first(info, 2)
        metadata_index = _first(info, 3)
        cascade_id = _safe_label(_first(info, 4), 128)
        if trajectory_id and isinstance(source_index, int) and 0 <= source_index <= MAX_STEP_INDEX:
            source_step = (trajectory_id, source_index,
                           metadata_index if isinstance(metadata_index, int) else None,
                           cascade_id)
    return {"source": source if isinstance(source, int) else None,
            "created_at": created, "finished_at": finished, "usage": usage,
            "model": model, "model_display": display, "effort": effort,
            "tool": call_name, "source_step": source_step}


def _parse_generation(blob, gen_idx):
    outer = _fields(blob)
    chat = _message(outer, 1)
    usage = _usage(_message(chat, 4))
    raw_model = _first(chat, 19) or _first(chat, 22)
    display_model = _first(chat, 21)
    model, display, effort = _model_names(raw_model, display_model)
    start = _message(chat, 9)
    timestamp = _timestamp_field(start, 4)
    context_meta = _message(start, 10)
    context = {}
    estimated, maximum = _first(context_meta, 1), _first(context_meta, 4)
    if isinstance(estimated, int) and 0 <= estimated <= 10**9:
        context["estimated_tokens_used"] = estimated
    if isinstance(maximum, int) and 0 <= maximum <= 10**9:
        context["max_context_tokens"] = maximum
    return {"gen_index": gen_idx, "step_indices": _packed_varints(outer, 2),
            "usage": usage, "model": model, "model_display": display,
            "effort": effort, "timestamp": timestamp, "context": context or None}


def _parse_checkpoint(payload):
    if not isinstance(payload, memoryview):
        return None
    try:
        wrapper = _fields(payload)
        checkpoint_blob = _first(wrapper, 30)
        if not isinstance(checkpoint_blob, memoryview):
            return None
        checkpoint = _fields(checkpoint_blob)
        intent_only = _first(checkpoint, 9, 0) == 1
        included = _packed_varints(checkpoint, 3)
        if not included:
            start, end = _first(checkpoint, 11), _first(checkpoint, 12)
            if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= MAX_STEP_INDEX:
                included_count = end - start + 1
            else:
                included_count = None
        else:
            included_count = len(set(included))
        title = _safe_label(_first(checkpoint, 10), MAX_TITLE)
        index = _first(checkpoint, 1)
        return {"intent_only": intent_only, "included_steps": included_count,
                "title": title, "checkpoint_index": index if isinstance(index, int) else None,
                "summarization_failed": _first(checkpoint, 16, 0) == 1,
                "used_fallback_summary": _first(checkpoint, 17, 0) == 1}
    except ValueError:
        return None


def _public_usage(usage):
    if not usage:
        return None
    return {key: usage[key] for key in ("in", "out", "cr", "cc", "reason", "response_out")}


def _field_availability(present):
    result = {}
    for label, names in (("input_output", ("in", "out")),
                         ("cache_read", ("cr",)), ("cache_write", ("cc",)),
                         ("reasoning", ("reason",)),
                         ("response_output", ("response_out",))):
        states = [bool(present.get(name)) for name in names]
        result[label] = ("recorded" if all(states) else
                         "partial" if any(states) else "unavailable")
    return result


def _event_date(timestamp):
    if not isinstance(timestamp, str):
        return None
    try:
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            return None
        return value.astimezone().strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError):
        return None


def _event_hour(timestamp):
    if not isinstance(timestamp, str):
        return None
    try:
        value = datetime.fromisoformat(timestamp)
        if value.tzinfo is None:
            return None
        value = value.astimezone()
        return f"{value.strftime('%Y-%m-%d')}\t{value.hour}"
    except (TypeError, ValueError, OverflowError):
        return None


def _record(timestamp, model, display, effort, usage, context, key, kind="request",
            step_index=None):
    if not usage:
        return None
    return {"key": key, "kind": kind, "timestamp": timestamp if isinstance(timestamp, str) else None,
            "date": _event_date(timestamp), "model": model or "Unknown",
            "model_display": display, "effort": effort,
            "in": usage["in"], "out": usage["out"], "cr": usage["cr"],
            "cc": usage["cc"], "reason": usage["reason"],
            "response_out": usage["response_out"],
            "availability": dict(usage["availability"]),
            "usage_availability": _field_availability(usage["availability"]),
            "context": context, "step_index": step_index}


def _aggregate_status(rows, malformed=False):
    any_numeric = any(any(row.get("availability", {}).values()) for row in rows)
    if not rows or not any_numeric:
        status = "unavailable"
    elif malformed or any(not (row.get("timestamp") and
                                  row.get("availability", {}).get("in") and
                                  row.get("availability", {}).get("out")) for row in rows):
        status = "partial"
    else:
        status = "recorded"
    summary = {}
    for label in ("input_output", "cache_read", "cache_write", "reasoning", "response_output"):
        states = [row.get("usage_availability", {}).get(label, "unavailable") for row in rows]
        if not states or all(state == "unavailable" for state in states):
            summary[label] = "unavailable"
        elif all(state == "recorded" for state in states):
            summary[label] = "recorded"
        else:
            summary[label] = "partial"
    return status, summary


def _open_snapshot(path):
    absolute = os.path.abspath(os.fspath(path))
    uri = "file:" + urllib.parse.quote(absolute, safe="/:") + "?mode=ro"
    con = None
    try:
        con = sqlite3.connect(uri, uri=True, timeout=5.0)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        # Force the read transaction before any source table query so all reads
        # use one coherent DB/WAL snapshot.
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error as exc:
        if con is not None:
            con.close()
        # A static read-only copy can be opened without shared-memory writes.
        # Never use immutable mode while a nonempty WAL exists: it would ignore
        # committed conversation rows and silently return stale totals.
        try:
            wal_size = os.path.getsize(absolute + "-wal")
        except OSError:
            wal_size = 0
        if wal_size:
            raise AntigravityReadError("cannot open a consistent read-only WAL snapshot") from exc
        try:
            con = sqlite3.connect(uri.replace("?mode=ro", "?mode=ro&immutable=1"),
                                  uri=True, timeout=5.0)
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            con.execute("SELECT count(*) FROM sqlite_master").fetchone()
            return con
        except sqlite3.Error as fallback_exc:
            if con is not None:
                con.close()
            raise AntigravityReadError("cannot open read-only conversation database") from fallback_exc


def _table_columns(con, table):
    # `table` is an internal fixed name, never input from a request or DB value.
    return {row[1] for row in con.execute(f"PRAGMA table_info({table})")}


def _bounded_rows(con, table, select):
    # `select` is built only from fixed schema column names above.
    rows = con.execute(f"SELECT {select} FROM {table} ORDER BY idx LIMIT ?",
                       (MAX_ROWS + 1,)).fetchall()
    return rows[:MAX_ROWS], len(rows) > MAX_ROWS


def _load_small_blob(con, table, column, row_id, length, key_column="idx"):
    if not isinstance(length, int) or length < 0 or length > MAX_PROTO_BYTES:
        return None
    row = con.execute(f"SELECT {column} FROM {table} WHERE {key_column}=?", (row_id,)).fetchone()
    if not row or row[0] is None:
        return None
    value = row[0]
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) > MAX_PROTO_BYTES:
        return None
    return memoryview(value)


def _trajectory_metadata(con, tables):
    result = {"created_at": None, "parent_id": None, "root_id": None,
              "project": None, "git_root": None, "branch": None, "cwd": None,
              "subagent": False, "subagent_type": None, "subagent_role": None,
              "nesting_depth": None}
    if "trajectory_metadata_blob" not in tables:
        return result, False
    columns = _table_columns(con, "trajectory_metadata_blob")
    if not {"id", "data"}.issubset(columns):
        return result, False
    row = con.execute("SELECT length(data) FROM trajectory_metadata_blob WHERE id='main'").fetchone()
    if not row or row[0] is None:
        return result, False
    blob = _load_small_blob(con, "trajectory_metadata_blob", "data", "main", row[0], "id")
    if blob is None:
        return result, False
    try:
        fields = _fields(blob)
        result["created_at"] = _timestamp_field(fields, 2)
        result["parent_id"] = _safe_label(_first(fields, 5), 128)
        result["root_id"] = _safe_label(_first(fields, 6), 128)
        for workspace_blob in fields.get(1, ()):
            if not isinstance(workspace_blob, memoryview):
                continue
            workspace = _fields(workspace_blob)
            workspace_uri = _first(workspace, 1)
            result["cwd"] = result["cwd"] or _local_path(workspace_uri)
            result["project"] = result["project"] or _local_leaf(workspace_uri)
            result["git_root"] = result["git_root"] or _local_leaf(_first(workspace, 2))
            result["branch"] = result["branch"] or _safe_label(_first(workspace, 4), 120)
        depth = _first(fields, 17)
        if isinstance(depth, int) and 0 <= depth <= 128:
            result["nesting_depth"] = depth
        subagent = _first(fields, 8)
        if isinstance(subagent, memoryview):
            spec = _fields(subagent)
            result["subagent_type"] = _safe_label(_first(spec, 1), 100)
            result["subagent_role"] = _safe_label(_first(spec, 2), 100)
            result["subagent"] = True
        return result, True
    except ValueError:
        return result, False


def _trajectory_index(con, tables):
    """Return trajectory/cascade identities belonging to this SQLite store."""
    if "trajectory_meta" not in tables:
        return set(), set(), False
    columns = _table_columns(con, "trajectory_meta")
    if not {"trajectory_id", "cascade_id"}.issubset(columns):
        return set(), set(), False
    try:
        rows = con.execute("SELECT trajectory_id,cascade_id FROM trajectory_meta LIMIT ?",
                           (MAX_ROWS + 1,)).fetchall()
    except sqlite3.Error:
        return set(), set(), False
    if len(rows) > MAX_ROWS:
        raise AntigravityReadError("trajectory index exceeds the safe scan limit; previous ledger retained")
    trajectories, cascades = set(), set()
    for trajectory_id, cascade_id in rows:
        trajectory_id = _safe_label(trajectory_id, 128)
        cascade_id = _safe_label(cascade_id, 128)
        if trajectory_id:
            trajectories.add(trajectory_id)
        if cascade_id:
            cascades.add(cascade_id)
    return trajectories, cascades, bool(trajectories)


def _invoke_subagent_specs(payload):
    """Read typed InvokeSubagent specs only; never inspect prompt/argument text."""
    if not isinstance(payload, memoryview):
        return []
    try:
        outer = _fields(payload)
        invoke_blob = _first(outer, 143)
        if not isinstance(invoke_blob, memoryview):
            return []
        invoke = _fields(invoke_blob)
        specs = invoke.get(9, ())
        if not specs or not all(isinstance(item, memoryview) for item in specs):
            return []
        parsed = []
        for item in specs[:100]:
            spec = _fields(item)
            parsed.append({"type": _safe_label(_first(spec, 1), 100),
                           "role": _safe_label(_first(spec, 2), 100)})
        return parsed
    except ValueError:
        return []


def _read_steps(con, own_trajectories=(), own_cascades=(), lineage_complete=False):
    columns = _table_columns(con, "steps")
    if not {"idx", "step_type", "status", "metadata"}.issubset(columns):
        raise AntigravityUnsupportedError("unsupported conversation schema: missing required step columns")
    has_payload = "step_payload" in columns
    select = "idx,step_type,status,length(metadata)"
    if has_payload:
        select += ",length(step_payload)"
    rows, truncated = _bounded_rows(con, "steps", select)
    if truncated:
        raise AntigravityReadError("step count exceeds the safe scan limit; previous ledger retained")

    by_idx = {}
    detail_partial = not has_payload
    for row in rows:
        idx, step_type, status, meta_len = row[:4]
        payload_len = row[4] if has_payload else None
        if not isinstance(idx, int) or idx < 0 or idx > MAX_STEP_INDEX:
            raise AntigravityReadError("step index is invalid; previous ledger retained")
        step_type = step_type if isinstance(step_type, int) else 0
        status = status if isinstance(status, int) else 0
        step = {"idx": idx, "step_type": step_type, "status": status, "metadata": None}
        if meta_len not in (None, 0):
            if not isinstance(meta_len, int) or meta_len < 0 or meta_len > MAX_PROTO_BYTES:
                raise AntigravityReadError("step metadata exceeds the safe scan limit; previous ledger retained")
            blob = _load_small_blob(con, "steps", "metadata", idx, meta_len)
            if blob is None:
                raise AntigravityReadError("step metadata could not be read; previous ledger retained")
            try:
                step["metadata"] = _parse_usage_metadata(blob)
            except ValueError as exc:
                raise AntigravityReadError("step metadata protobuf is malformed; previous ledger retained") from exc
        source_step = (step.get("metadata") or {}).get("source_step")
        if source_step:
            trajectory_id, _source_index, _metadata_index, cascade_id = source_step
            if not lineage_complete:
                detail_partial = True
            else:
                step["foreign_replay"] = (
                    trajectory_id not in own_trajectories or
                    bool(cascade_id and own_cascades and cascade_id not in own_cascades))
        # Payloads can contain full prompts and tool arguments. Read only the two
        # verified marker types, extract safe facts, then drop the bytes at once.
        if has_payload and step_type in (23, 127) and payload_len not in (None, 0):
            if not isinstance(payload_len, int) or payload_len < 0 or payload_len > MAX_PROTO_BYTES:
                detail_partial = True
            else:
                payload = _load_small_blob(con, "steps", "step_payload", idx, payload_len)
                if payload is None:
                    detail_partial = True
                elif step_type == 23:
                    step["checkpoint"] = _parse_checkpoint(payload)
                    if step["checkpoint"] is None:
                        detail_partial = True
                elif step_type == 127:
                    step["subagent_specs"] = _invoke_subagent_specs(payload)
                    if not step["subagent_specs"]:
                        detail_partial = True
        elif step_type in (23, 127):
            detail_partial = True
        by_idx[idx] = step

    def replay_key(step):
        source_step = (step.get("metadata") or {}).get("source_step")
        return ("source", source_step) if source_step else ("step", step["idx"])

    def completeness(step):
        metadata = step.get("metadata") or {}
        usage = metadata.get("usage") or {}
        score = sum(bool(v) for v in (usage.get("availability") or {}).values())
        return (step["status"] == 3, score, bool(metadata.get("tool")),
                bool(step.get("checkpoint") or step.get("subagent_specs")), step["idx"])

    effective = {}
    for step in by_idx.values():
        key = replay_key(step)
        previous = effective.get(key)
        if previous is None or completeness(step) > completeness(previous):
            effective[key] = step
    effective_steps = sorted(effective.values(), key=lambda item: item["idx"])

    events, compactions, titles = [], [], []
    assistant_activity, user_activity, tool_activity = [], [], []
    usage_by_step = {}
    user_messages = assistant_messages = tool_calls = subagent_requests = 0
    for step in effective_steps:
        if step.get("foreign_replay"):
            continue
        idx, step_type, status = step["idx"], step["step_type"], step["status"]
        md = step["metadata"] or {}
        timestamp = md.get("created_at") or md.get("finished_at")
        checkpoint = step.get("checkpoint")
        if checkpoint:
            if checkpoint["title"]:
                titles.append((idx, checkpoint["title"]))
            ts = md.get("finished_at") or timestamp
            event = {"timestamp": ts, "kind": "checkpoint", "step_index": idx,
                     "status": _STATUS.get(status, "unknown"),
                     "intent_only": checkpoint["intent_only"],
                     "included_steps": checkpoint["included_steps"],
                     "summarization_failed": checkpoint["summarization_failed"],
                     "used_fallback_summary": checkpoint["used_fallback_summary"]}
            if (not checkpoint["intent_only"] and not checkpoint["summarization_failed"]
                    and status == 3 and ts):
                event["kind"] = "compaction"
                compactions.append({"id": f"checkpoint:{idx}", "timestamp": ts,
                    "date": _event_date(ts), "trigger": "unknown", "before": None,
                    "after": None, "duration_ms": None})
            events.append(event)
        for spec in step.get("subagent_specs", ()):
            subagent_requests += 1
            ts = md.get("finished_at") or timestamp
            if ts:
                events.append({"timestamp": ts, "kind": "subagent", "step_index": idx,
                               "status": _STATUS.get(status, "unknown"),
                               "requested": True,
                               "subagent_type": spec.get("type"), "subagent_role": spec.get("role")})

        if md.get("source") == 4 and timestamp:
            user_messages += 1
            user_activity.append({"timestamp": timestamp, "step_index": idx})
            events.append({"timestamp": timestamp, "kind": "user_turn", "step_index": idx,
                           "status": _STATUS.get(status, "unknown")})
        if md.get("source") == 2 and status == 3 and step_type == 15 and timestamp:
            assistant_messages += 1
            assistant_activity.append({"timestamp": md.get("finished_at") or timestamp,
                                       "step_index": idx})
        if md.get("tool") and timestamp:
            tool_calls += 1
            tool_activity.append({"timestamp": md.get("finished_at") or timestamp,
                                   "step_index": idx, "tool": md["tool"]})
            events.append({"timestamp": md.get("finished_at") or timestamp,
                           "kind": "tool", "step_index": idx, "tool": md["tool"],
                           "status": _STATUS.get(status, "unknown")})
        if md.get("usage"):
            usage_by_step[idx] = {"step_index": idx, "timestamp": timestamp,
                "model": md.get("model") or "Unknown", "model_display": md.get("model_display"),
                "effort": md.get("effort"), "usage": md["usage"],
                "checkpoint": step.get("checkpoint"), "status": status}
    best_by_id = {}
    for idx, candidate in list(usage_by_step.items()):
        rid = _usage_id(candidate["usage"])
        if not rid:
            continue
        previous = best_by_id.get(rid)
        if previous is None:
            best_by_id[rid] = idx
            continue
        prior = usage_by_step[previous]
        if _usage_quality(candidate["usage"], candidate["timestamp"], candidate["model"],
                          candidate["status"] == 3, idx) > _usage_quality(
                              prior["usage"], prior["timestamp"], prior["model"],
                              prior["status"] == 3, previous):
            usage_by_step.pop(previous, None)
            best_by_id[rid] = idx
        else:
            usage_by_step.pop(idx, None)
    if best_by_id:
        filtered_assistant = []
        for item in assistant_activity:
            idx = item["step_index"]
            metadata = (by_idx.get(idx) or {}).get("metadata") or {}
            rid = _usage_id(metadata.get("usage"))
            if rid and rid in best_by_id and best_by_id[rid] != idx:
                continue
            filtered_assistant.append(item)
        assistant_activity = filtered_assistant
        assistant_messages = len(assistant_activity)
    foreign_keys = {replay_key(step) for step in effective_steps if step.get("foreign_replay")}
    return {"steps": by_idx, "effective_steps": effective_steps,
            "step_keys": {idx: replay_key(step) for idx, step in by_idx.items()},
            "foreign_step_keys": foreign_keys,
            "usage": usage_by_step, "events": events,
            "assistant_activity": assistant_activity,
            "user_activity": user_activity, "tool_activity": tool_activity,
            "compactions": compactions, "titles": titles,
            "detail_partial": detail_partial, "user_messages": user_messages,
            "assistant_messages": assistant_messages, "tool_calls": tool_calls,
            "subagent_requests": subagent_requests}


def _read_generations(con, tables, steps):
    if "gen_metadata" not in tables:
        return [], False
    columns = _table_columns(con, "gen_metadata")
    if not {"idx", "data"}.issubset(columns):
        return [], True
    rows, truncated = _bounded_rows(con, "gen_metadata", "idx,length(data)")
    if truncated:
        raise AntigravityReadError("generation count exceeds the safe scan limit; previous ledger retained")
    generations, detail_partial = [], False
    for gen_idx, size in rows:
        if not isinstance(gen_idx, int) or gen_idx < 0:
            raise AntigravityReadError("generation index is invalid; previous ledger retained")
        if not isinstance(size, int) or size < 0 or size > MAX_PROTO_BYTES:
            detail_partial = True
            continue
        blob = _load_small_blob(con, "gen_metadata", "data", gen_idx, size)
        if blob is None:
            detail_partial = True
            continue
        try:
            generation = _parse_generation(blob, gen_idx)
        except ValueError:
            detail_partial = True
            continue
        if not generation["usage"]:
            continue
        if not generation["timestamp"]:
            for step_idx in generation["step_indices"]:
                step = steps["steps"].get(step_idx)
                md = (step or {}).get("metadata") or {}
                timestamp = md.get("created_at") or md.get("finished_at")
                if timestamp:
                    generation["timestamp"] = timestamp
                    break
        generations.append(generation)
    deduplicated = {}
    for generation in generations:
        usage = generation["usage"]
        rid = _usage_id(usage)
        key = (("response", rid) if rid else
               ("steps", tuple(sorted(set(generation["step_indices"]))))
               if generation["step_indices"] else ("index", generation["gen_index"]))
        previous = deduplicated.get(key)
        if previous is None:
            deduplicated[key] = generation
            continue
        combined_steps = sorted(set(previous["step_indices"]) | set(generation["step_indices"]))
        if _usage_quality(usage, generation["timestamp"], generation["model"], True,
                          generation["gen_index"]) > _usage_quality(
                              previous["usage"], previous["timestamp"], previous["model"],
                              True, previous["gen_index"]):
            generation["step_indices"] = combined_steps
            deduplicated[key] = generation
        else:
            previous["step_indices"] = combined_steps
    return sorted(deduplicated.values(), key=lambda item: item["gen_index"]), detail_partial


def _split_generation(generation, steps):
    """Split one aggregate generation only when linked request receipts reconcile."""
    linked_indices = sorted(set(generation.get("step_indices", ())))
    candidates = [steps["usage"][idx] for idx in linked_indices if idx in steps["usage"]]
    if len(candidates) < 2:
        return None
    ids = [_usage_id(candidate.get("usage")) for candidate in candidates]
    timestamps = [candidate.get("timestamp") for candidate in candidates]
    if (any(not rid for rid in ids) or len(set(ids)) != len(ids)
            or any(not timestamp for timestamp in timestamps)
            or len(set(timestamps)) != len(timestamps)):
        return None
    generation_usage = generation.get("usage") or {}
    names = ("in", "out", "cr", "cc", "reason", "response_out")
    for name in names:
        if not generation_usage.get("availability", {}).get(name):
            continue
        recorded = [candidate for candidate in candidates
                    if candidate["usage"].get("availability", {}).get(name)]
        if name in ("in", "out") and len(recorded) != len(candidates):
            return None
        if sum(candidate["usage"][name] for candidate in recorded) != generation_usage[name]:
            return None

    parts = []
    gen_enum = generation_usage.get("model_enum")
    for ordinal, idx in enumerate(linked_indices):
        candidate = steps["usage"].get(idx)
        if candidate is None:
            continue
        model = candidate.get("model") or "Unknown"
        display, effort = candidate.get("model_display"), candidate.get("effort")
        step_enum = candidate["usage"].get("model_enum")
        # Internal enums only validate that the generation's recorded response
        # name applies to this otherwise unnamed receipt. The enum is never used
        # as a product name, and explicit step names are retained as recorded.
        if (model == "Unknown" and generation.get("model") != "Unknown"
                and isinstance(gen_enum, int) and step_enum == gen_enum):
            model = generation["model"]
            display = generation.get("model_display")
            effort = generation.get("effort")
        parts.append({
            "gen_index": generation["gen_index"],
            "step_indices": [idx],
            "usage": candidate["usage"],
            "model": model,
            "model_display": display,
            "effort": effort,
            "timestamp": candidate.get("timestamp"),
            # Generation context is shared across its requests; retain it once
            # rather than multiplying a single observation across every receipt.
            "context": generation.get("context") if ordinal == 0 else None,
        })
    return parts if len(parts) >= 2 else None


def read_conversation(path, *, max_events=MAX_EVENTS, event_filter=None,
                      include_private=False):
    """Read one conversation in a bounded read-only SQLite snapshot.

    ``event_filter`` receives each sanitized event before ``max_events`` is
    applied. Absolute workspace paths are omitted unless parser calls with
    ``include_private=True``; timeline consumers should use the default.
    """
    if event_filter is not None and not callable(event_filter):
        raise TypeError("event_filter must be callable")
    cap = max(0, min(max_events if type(max_events) is int else MAX_EVENTS, MAX_EVENTS))
    conversation_id = Path(os.fspath(path)).stem
    if not conversation_id or len(conversation_id) > 128:
        raise AntigravityReadError("conversation id exceeds the safe size limit")
    try:
        con = _open_snapshot(path)
    except (sqlite3.Error, OSError) as exc:
        if isinstance(exc, AntigravityReadError):
            raise
        raise AntigravityReadError("cannot open read-only conversation database") from exc
    try:
        tables = {row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "steps" not in tables:
            raise AntigravityUnsupportedError("unsupported conversation schema: missing steps table")
        own_trajectories, own_cascades, lineage_complete = _trajectory_index(con, tables)
        steps = _read_steps(con, own_trajectories, own_cascades, lineage_complete)
        metadata, metadata_available = _trajectory_metadata(con, tables)
        generations, gen_partial = _read_generations(con, tables, steps)
        expanded_generations = []
        for generation in generations:
            split = _split_generation(generation, steps)
            if split:
                for item in split:
                    item["split"] = True
                expanded_generations.extend(split)
            else:
                expanded_generations.append(generation)
        generations = expanded_generations

        records, seen_ids, seen_generations, linked_steps = [], set(), set(), set()
        replay_incomplete = False
        step_models = {}
        events = list(steps["events"])
        for gen in generations:
            usage = gen["usage"]
            linked = {steps["step_keys"].get(i, ("step", i)) for i in gen["step_indices"]}
            rid = _usage_id(usage)
            numeric = any(usage["availability"].values())
            linked_has_numeric_step = any(
                i in steps["usage"] and any(steps["usage"][i]["usage"]["availability"].values())
                for i in gen["step_indices"])
            if not numeric and linked_has_numeric_step:
                continue
            if linked & steps["foreign_step_keys"]:
                local_indices = [i for i in gen["step_indices"]
                                 if steps["step_keys"].get(i, ("step", i))
                                 not in steps["foreign_step_keys"]]
                if any(i not in steps["usage"] or not any(
                        steps["usage"][i]["usage"]["availability"].values())
                       for i in local_indices):
                    replay_incomplete = True
                linked_steps.update(linked & steps["foreign_step_keys"])
                continue
            gen_key = (("response", rid) if rid else
                       ("linked", tuple(sorted(linked))) if numeric and linked else
                       ("index", gen["gen_index"]))
            if gen_key in seen_generations:
                if numeric:
                    linked_steps.update(linked)
                continue
            seen_generations.add(gen_key)
            if numeric:
                linked_steps.update(linked)
                if rid:
                    seen_ids.add(rid)
            elif rid and rid in seen_ids:
                continue
            elif rid:
                seen_ids.add(rid)
            row = _record(gen["timestamp"], gen["model"], gen["model_display"],
                          gen["effort"], usage, gen["context"],
                          f"gen:{gen['gen_index']}:step:{gen['step_indices'][0]}"
                          if gen.get("split") else f"gen:{gen['gen_index']}",
                          step_index=min(gen["step_indices"], default=None))
            if row:
                records.append(row)
                if row["model"] != "Unknown":
                    for step_idx in gen["step_indices"]:
                        step_models.setdefault(step_idx, set()).add(row["model"])
                events.append({"timestamp": row["timestamp"], "kind": "request",
                    "step_index": row["step_index"],
                    "model": row["model"], "effort": row["effort"],
                    "usage": _public_usage(usage),
                    "usage_availability": row["usage_availability"],
                    "context": row["context"]})
        for idx, candidate in sorted(steps["usage"].items()):
            step_key = steps["step_keys"].get(idx, ("step", idx))
            if step_key in linked_steps or step_key in steps["foreign_step_keys"]:
                continue
            usage = candidate["usage"]
            rid = _usage_id(usage)
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            checkpoint_usage = bool((candidate.get("checkpoint") or {}).get("intent_only"))
            row = _record(candidate["timestamp"], candidate["model"],
                          candidate["model_display"], candidate["effort"], usage,
                          None, f"step:{idx}", kind="checkpoint_usage" if checkpoint_usage else "request",
                          step_index=idx)
            if row:
                records.append(row)
                if row["model"] != "Unknown":
                    step_models.setdefault(idx, set()).add(row["model"])
                events.append({"timestamp": row["timestamp"], "kind": "request",
                    "step_index": idx, "model": row["model"], "effort": row["effort"],
                    "usage": _public_usage(usage),
                    "usage_availability": row["usage_availability"],
                    "checkpoint_usage": checkpoint_usage})

        # Message/tool aggregates are built from the complete bounded step scan,
        # separately from the capped public timeline. Generation links provide
        # the actual response model for assistant steps whose step metadata only
        # carries an internal enum or no model name.
        timestamp_models = {}
        for row in records:
            if row.get("timestamp") and row.get("model") != "Unknown":
                timestamp_models.setdefault(row["timestamp"], set()).add(row["model"])

        def activity_model(step_index, timestamp):
            step = steps["steps"].get(step_index) if isinstance(step_index, int) else None
            metadata = (step or {}).get("metadata") or {}
            explicit = metadata.get("model")
            if explicit and explicit != "Unknown":
                return explicit
            candidates = step_models.get(step_index, set()) if isinstance(step_index, int) else set()
            if len(candidates) == 1:
                return next(iter(candidates))
            at_time = timestamp_models.get(timestamp, set())
            return next(iter(at_time)) if len(at_time) == 1 else "Unknown"

        activity_buckets = {"assistant": {}, "user": {}, "tools": {}, "hourly": {}}

        def bump_hour(timestamp):
            hour_key = _event_hour(timestamp)
            if hour_key:
                bucket = activity_buckets["hourly"].setdefault(hour_key, {"msgs": 0})
                bucket["msgs"] += 1

        for item in steps["assistant_activity"]:
            timestamp = item.get("timestamp")
            date = _event_date(timestamp)
            if not date:
                continue
            model = activity_model(item.get("step_index"), timestamp)
            key = f"{date}\t{model}"
            activity_buckets["assistant"][key] = activity_buckets["assistant"].get(key, 0) + 1
            bump_hour(timestamp)
        for item in steps["user_activity"]:
            date = _event_date(item.get("timestamp"))
            if not date:
                continue
            activity_buckets["user"][date] = activity_buckets["user"].get(date, 0) + 1
            bump_hour(item.get("timestamp"))
        for item in steps["tool_activity"]:
            timestamp, tool = item.get("timestamp"), item.get("tool")
            date = _event_date(timestamp)
            if not date or not isinstance(tool, str):
                continue
            model = activity_model(item.get("step_index"), timestamp)
            key = f"{date}\t{model}\t{tool}"
            activity_buckets["tools"][key] = activity_buckets["tools"].get(key, 0) + 1

        # Model-change markers describe transitions from reconciled usage, not
        # every assistant step. Unknown model observations do not invent labels.
        events = [event for event in events if event.get("kind") != "model"]
        previous_model_effort = None
        for row in sorted(records, key=lambda item: (item.get("timestamp") or "", item["key"])):
            model = row.get("model")
            if not row.get("timestamp") or not model or model == "Unknown":
                previous_model_effort = None
                continue
            signature = (model, row.get("effort"))
            if signature != previous_model_effort:
                event = {"timestamp": row["timestamp"], "kind": "model",
                         "step_index": row.get("step_index"), "model": model,
                         "effort": row.get("effort"), "status": "done"}
                events.append(event)
            previous_model_effort = signature

        records.sort(key=lambda item: (item.get("timestamp") or "", item["key"]))
        usage_status, usage_availability = _aggregate_status(records)
        if replay_incomplete and usage_status != "unavailable":
            usage_status = "partial"
            usage_availability = {
                field: "partial" if state != "unavailable" else state
                for field, state in usage_availability.items()
            }
        model_names = [row["model"] for row in records]
        if not model_names or all(name == "Unknown" for name in model_names):
            model_status = "unavailable"
        elif any(name == "Unknown" for name in model_names):
            model_status = "partial"
        else:
            model_status = "recorded"
        detail_status = ("partial" if steps["detail_partial"] or gen_partial or replay_incomplete else
                         "recorded" if metadata_available else "unavailable")

        if metadata.get("created_at"):
            events.append({"timestamp": metadata["created_at"], "kind": "session", "status": "started"})
            if metadata.get("subagent"):
                events.append({"timestamp": metadata["created_at"], "kind": "subagent",
                    "parent_id": metadata.get("parent_id"), "root_id": metadata.get("root_id"),
                    "nesting_depth": metadata.get("nesting_depth"),
                    "subagent_type": metadata.get("subagent_type"),
                    "subagent_role": metadata.get("subagent_role")})

        totals = {key: 0 for key in ("in", "out", "cr", "cc", "reason", "response_out", "req")}
        undated = dict(totals)
        for row in records:
            for key in ("in", "out", "cr", "cc", "reason", "response_out"):
                totals[key] += row[key]
                if row.get("date") is None:
                    undated[key] += row[key]
            totals["req"] += 1
            if row.get("date") is None:
                undated["req"] += 1
        unknown_usage = {key: sum(row[key] for row in records if row["model"] == "Unknown")
                         for key in ("in", "out", "cr", "cc", "reason", "response_out")}

        dated = [event["timestamp"] for event in events if isinstance(event.get("timestamp"), str)]
        dates = {_event_date(value) for value in dated}
        dates.update(row["date"] for row in records if row.get("date"))
        dates.discard(None)
        contexts = [row["context"] for row in records if row.get("context")]
        context = {}
        estimates = [v["estimated_tokens_used"] for v in contexts if "estimated_tokens_used" in v]
        maxima = [v["max_context_tokens"] for v in contexts if "max_context_tokens" in v]
        if estimates:
            context["peak_estimated_tokens_used"] = max(estimates)
        if maxima:
            context["max_context_tokens"] = max(maxima)
        if not context:
            context = None

        # The parser stores full bounded-scan compaction metadata in the durable
        # ledger. Only the timeline event list below is capped.
        compactions = sorted(steps["compactions"],
                             key=lambda item: (item.get("timestamp") or "", item["id"]))
        compactions_truncated = False
        safe_events, events_truncated = [], False
        allowed = {"timestamp", "kind", "step_index", "model", "effort", "tool", "status",
                   "usage", "usage_availability", "context", "intent_only", "included_steps",
                   "summarization_failed", "used_fallback_summary", "checkpoint_usage",
                   "parent_id", "root_id", "nesting_depth", "subagent_type", "subagent_role"}
        allowed.add("requested")
        for event in sorted(events, key=lambda item: (
                item.get("timestamp") or "",
                item.get("step_index") if type(item.get("step_index")) is int else -1,
                item.get("kind", ""))):
            safe = {key: event[key] for key in allowed if key in event and event[key] is not None}
            if not isinstance(safe.get("timestamp"), str):
                continue
            if event_filter is not None and not event_filter(safe):
                continue
            if len(safe_events) >= cap:
                events_truncated = True
                break
            safe_events.append(safe)

        title = sorted(steps["titles"], key=lambda item: item[0])[-1][1] if steps["titles"] else None
        activity_times = [event["timestamp"] for event in events
                          if isinstance(event.get("timestamp"), str)]
        activity_times.extend(row["timestamp"] for row in records if row.get("timestamp"))
        snapshot = {
            "conversation_id": conversation_id,
            "title": title,
            "project": metadata.get("project"),
            "git_root": metadata.get("git_root"),
            "branch": metadata.get("branch"),
            "created_at": metadata.get("created_at"),
            "first_activity": min(activity_times, default=None),
            "last_activity": max(activity_times, default=None),
            "parent_id": metadata.get("parent_id"),
            "root_id": metadata.get("root_id"),
            "subagent": metadata.get("subagent", False),
            "subagent_type": metadata.get("subagent_type"),
            "subagent_role": metadata.get("subagent_role"),
            "nesting_depth": metadata.get("nesting_depth"),
            "context": context,
            "usage_status": usage_status,
            "usage_availability": usage_availability,
            "model_status": model_status,
            "detail_status": detail_status,
            "records": records,
            "totals": totals,
            "undated_usage": undated,
            "unknown_model_usage": unknown_usage,
            "activity_dates": sorted(dates),
            "user_messages": steps["user_messages"],
            "assistant_messages": steps["assistant_messages"],
            "tool_calls": steps["tool_calls"],
            "activity_buckets": activity_buckets,
            "subagent_requests": steps["subagent_requests"],
            "events": safe_events,
            "events_truncated": events_truncated,
            "compactions": compactions,
            "compactions_truncated": compactions_truncated,
            "metadata_available": metadata_available,
        }
        if include_private and metadata.get("cwd"):
            snapshot["cwd"] = metadata["cwd"]
        return snapshot
    except sqlite3.Error as exc:
        raise AntigravityReadError("conversation database query failed") from exc
    finally:
        try:
            con.rollback()
        finally:
            con.close()
