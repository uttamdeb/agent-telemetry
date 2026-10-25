"""Pure projection of recently active local sessions for native clients.

This module never discovers files, reads a ledger, or performs I/O. The dashboard
passes its retained local aggregates while holding the parser refresh lock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import os
import unicodedata
from typing import Callable, Iterable, Optional


WINDOW_SECONDS = 15 * 60
MAX_SESSIONS = 3
MAX_TITLE_CHARS = 90
_USAGE_STATUSES = frozenset(("recorded", "partial", "unavailable"))
_AVAILABILITY_FIELDS = frozenset((
    "input_output", "cache_read", "cache_write", "reasoning", "response_output",
))

# Keep these aligned with the dashboard's source (tool) dimension. The billed
# model never determines the tool or provider shown here.
TOOLS = {
    "claude": ("Claude Code", "Anthropic"),
    "claude-desktop": ("Claude Desktop", "Anthropic"),
    "codex": ("Codex", "OpenAI"),
    "copilot": ("Copilot", "GitHub"),
    "cursor": ("Cursor", "Cursor"),
    "opencode": ("OpenCode", None),
    "hermes": ("Hermes Agent", None),
    "openclaw": ("OpenClaw", None),
    "antigravity": ("Antigravity", "Google"),
}

_UTC = timezone.utc
_USAGE_FIELDS = ("in", "out", "cr", "cc")
_MAX_TOKENS = 10**15
_CONTROL_PREFIX = ("Cc", "Cf", "Cs", "Co", "Cn")
# These parsers turn provider epoch values into datetime.fromtimestamp() local
# datetimes before serializing session.end. Other core session times pass through
# parser._from_iso, whose naive-input convention is UTC.
_LOCAL_NAIVE_END_SOURCES = frozenset(("cursor", "opencode", "hermes", "openclaw"))


def _as_utc(value, *, source=None) -> Optional[datetime]:
    """Parse like parser._from_iso: a naive source timestamp means UTC."""
    if not isinstance(value, str) or not value or len(value) > 128:
        return None
    try:
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            if source in _LOCAL_NAIVE_END_SOURCES:
                # These session summaries serialize datetime.fromtimestamp()'s
                # local wall time without an offset. astimezone() attaches the
                # machine's local zone before conversion to UTC.
                return parsed.astimezone(_UTC)
            # parser._from_iso assigns UTC to naive source timestamps and then
            # converts to local time for chart bucketing. Return that instant in
            # UTC so the native API is stable across local timezone and DST.
            parsed = parsed.replace(tzinfo=_UTC)
        return parsed.astimezone(_UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _now_utc(value=None) -> datetime:
    if value is None:
        return datetime.now(_UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=_UTC)
        return value.astimezone(_UTC)
    parsed = _as_utc(value)
    if parsed is None:
        raise ValueError("observed_at must be a valid timestamp")
    return parsed


def _iso_utc(value: datetime) -> str:
    return value.astimezone(_UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _counter(value) -> Optional[int]:
    if type(value) is not int or value < 0 or value > _MAX_TOKENS:
        return None
    return value


def _sum_bucket_records(records) -> Optional[int]:
    if not isinstance(records, dict) or not records:
        return None
    total = 0
    for row in records.values():
        if not isinstance(row, dict):
            return None
        parts = [_counter(row.get(field)) for field in _USAGE_FIELDS]
        if any(value is None for value in parts):
            return None
        total += sum(parts)
        if total > _MAX_TOKENS:
            return None
    return total


def _session_tokens(session, aggregate, session_count) -> Optional[int]:
    if aggregate.get("source") == "antigravity":
        status = session.get("usage_status", aggregate.get("usage_status"))
        availability = session.get("usage_availability", aggregate.get("usage_availability"))
        if status == "unavailable":
            return None
        if isinstance(availability, dict):
            # A token total requires all four additive token buckets. Reasoning
            # and response-output availability do not change that total.
            if any(availability.get(field) != "recorded"
                   for field in ("input_output", "cache_read", "cache_write")):
                return None
        elif status in ("partial", "unavailable"):
            return None
    parts = [_counter(session.get(field)) for field in _USAGE_FIELDS]
    if all(value is not None for value in parts):
        direct = sum(parts)
        if direct:
            return direct if direct <= _MAX_TOKENS else None

    total = _sum_bucket_records(session.get("records"))
    if total is not None:
        return total
    # Aggregate records are safe as a fallback only for a single-session file.
    # Never repeat a multi-session file's whole total on each row.
    if session_count == 1:
        return _sum_bucket_records(aggregate.get("records"))
    return None


def _safe_title(value) -> str:
    if not isinstance(value, str):
        return "Untitled session"
    clean = []
    for char in value[:MAX_TITLE_CHARS * 4]:
        if char in "\r\n\t":
            clean.append(" ")
            continue
        if unicodedata.category(char).startswith(_CONTROL_PREFIX):
            clean.append(" ")
            continue
        clean.append(char)
    result = " ".join("".join(clean).split())[:MAX_TITLE_CHARS].strip()
    return result or "Untitled session"


def _path_namespace(path, aggregate) -> Optional[str]:
    value = path if isinstance(path, str) and path else aggregate.get("path")
    if not isinstance(value, str) or not value or len(value) > 32768:
        return None
    try:
        return os.path.normcase(os.path.normpath(value))
    except (OSError, ValueError):
        return value


def _source_id(aggregate, session, identity=None, ordinal=0) -> Optional[str]:
    source = aggregate.get("source")
    if callable(identity) and source in ("claude", "codex", "antigravity"):
        try:
            value = identity(aggregate, session)
        except (KeyError, OSError, TypeError, ValueError):
            value = None
        if isinstance(value, str) and value and len(value) <= 4096:
            return "native:" + value
    value = session.get("_sid")
    if not isinstance(value, str) or not value or len(value) > 4096:
        value = session.get("id")
    if isinstance(value, str) and value and len(value) <= 4096:
        # Some finalized multi-session parsers discard _sid and retain only a
        # display id. Start time disambiguates short ids within this source file.
        start = _as_utc(session.get("start"), source=source)
        return "retained:" + value + "\0" + (_iso_utc(start) if start else "")
    start = _as_utc(session.get("start"), source=source)
    end = _as_utc(session.get("end"), source=source)
    title = _safe_title(session.get("title"))
    fields = ["fallback", _iso_utc(start) if start else "", title]
    if not start:
        fields.append(_iso_utc(end) if end else "")
    if not (start or end or title):
        fields.append(str(ordinal))
    return "\0".join(fields)


def _stable_key(source: str, namespace: str, source_id: str) -> str:
    material = ("agent-telemetry-native-activity-v1\0" + source + "\0" +
                namespace + "\0" + source_id)
    return hashlib.sha256(material.encode("utf-8", errors="surrogatepass")).hexdigest()


def _is_live(aggregate, session, local_device=None) -> bool:
    if aggregate.get("archived") is True:
        return False
    if aggregate.get("_device") not in (None, "", local_device):
        return False
    for obj in (aggregate, session):
        if (obj.get("archived") is True or obj.get("archived_session") is True
                or obj.get("live_source") is False or obj.get("source_live") is False
                or obj.get("live") is False):
            return False
    return True


def is_local_live_aggregate(aggregate, local_device=None) -> bool:
    """Allow a live local aggregate to participate in conversation deduplication."""
    return isinstance(aggregate, dict) and _is_live(aggregate, {}, local_device)


def project(items: Iterable, *, observed_at=None, identity: Optional[Callable] = None,
            local_device: Optional[str] = None) -> dict:
    """Build the bounded public snapshot and private alias references.

    ``items`` is the local ``(path, aggregate)`` list after the dashboard's
    ``_one_per_conversation`` deduplication. No source records are modified.
    Call :func:`apply_display_aliases` after eligibility is established and
    after reading the optional title preferences outside the parser lock.
    """
    now = _now_utc(observed_at)
    cutoff = now - timedelta(seconds=WINDOW_SECONDS)
    newest = {}

    for item in items:
        try:
            path, aggregate = item
        except (TypeError, ValueError):
            continue
        if not isinstance(aggregate, dict) or not _is_live(aggregate, {}, local_device):
            continue
        source = aggregate.get("source")
        if not isinstance(source, str):
            continue
        tool = TOOLS.get(source)
        if not tool:
            continue
        sessions = aggregate.get("sessions")
        if not isinstance(sessions, list) or not sessions:
            continue
        namespace = _path_namespace(path, aggregate)
        if namespace is None:
            continue
        for ordinal, session in enumerate(sessions):
            if not isinstance(session, dict) or not _is_live(aggregate, session, local_device):
                continue
            if session.get("source") not in (None, source):
                continue
            end = _as_utc(session.get("end"), source=source)
            if end is None or end < cutoff or end > now:
                continue
            source_id = _source_id(aggregate, session, identity, ordinal)
            subagent = bool(session.get("subagent") or aggregate.get("subagent"))
            title = _safe_title(session.get("title"))
            row = {
                "key": _stable_key(source, namespace, source_id),
                "source": source,
                "tool": tool[0],
                "provider": tool[1],
                "title": title,
                "tokens": _session_tokens(session, aggregate, len(sessions)),
                "last_activity_at": _iso_utc(end),
                "subagent": subagent,
            }
            if source == "antigravity":
                status = session.get("usage_status", aggregate.get("usage_status"))
                if isinstance(status, str) and status in _USAGE_STATUSES:
                    row["usage_status"] = status
                availability = session.get("usage_availability", aggregate.get("usage_availability"))
                if isinstance(availability, dict):
                    safe_availability = {
                        field: value for field, value in availability.items()
                        if field in _AVAILABILITY_FIELDS and isinstance(value, str) and value in _USAGE_STATUSES
                    }
                    if safe_availability:
                        row["usage_availability"] = safe_availability
            alias = None
            if source == "claude" and subagent and isinstance(path, str):
                alias_id = source_id[len("native:"):] if source_id.startswith("native:") else source_id
                alias = (local_device, source, path, alias_id)
            entry = {"row": row, "_alias": alias, "_sort": end}
            previous = newest.get(row["key"])
            if previous is None or (end, row["tokens"] is not None, row["tokens"] or 0) > (
                    previous["_sort"], previous["row"]["tokens"] is not None,
                    previous["row"]["tokens"] or 0):
                newest[row["key"]] = entry

    ordered = sorted(newest.values(), key=lambda entry: (entry["_sort"], entry["row"]["key"]), reverse=True)
    selected = ordered[:MAX_SESSIONS]
    return {
        "observed_at": _iso_utc(now),
        "window_seconds": WINDOW_SECONDS,
        "sessions": [entry["row"] | {"_display_alias": entry["_alias"]} for entry in selected],
        "total": len(ordered),
    }


def has_display_aliases(snapshot) -> bool:
    return any(row.get("_display_alias") for row in snapshot.get("sessions", []))


def apply_display_aliases(snapshot, names, display_name: Callable) -> dict:
    """Apply private Claude child names only after the session passed eligibility."""
    public = []
    for row in snapshot.get("sessions", []):
        result = {key: value for key, value in row.items() if key != "_display_alias"}
        reference = row.get("_display_alias")
        if reference and callable(display_name):
            try:
                alias = display_name(names, *reference) if isinstance(names, dict) else None
            except (OSError, TypeError, ValueError):
                alias = None
            if isinstance(alias, str) and alias.strip():
                result["title"] = _safe_title(alias)
        public.append(result)
    return {
        "observed_at": snapshot["observed_at"],
        "window_seconds": snapshot["window_seconds"],
        "sessions": public,
        "total": snapshot["total"],
    }
