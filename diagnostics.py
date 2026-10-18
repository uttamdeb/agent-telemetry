"""Privacy-safe, in-memory parser health summaries for the local dashboard."""

from __future__ import annotations

import threading
import time
import re
from typing import Iterable, Mapping


_ISSUE_LIMIT = 12
_SOURCE_KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}\Z")


def _source_key(source: object) -> str:
    value = str(source).lower()
    return value if _SOURCE_KEY.fullmatch(value) else "unknown"

_ERRORS = (
    ("permission", "A log could not be read. Check its file permissions and try Refresh again."),
    ("missing_file", "A log changed or disappeared during the scan. Refresh to rediscover files."),
    ("invalid_json", "A log contains invalid JSON. Check that the source tool finished writing it, then Refresh."),
    ("decode", "A log uses text that could not be decoded. Check the source file and Refresh."),
    ("disk_full", "The local analytics cache could not be saved. Free disk space, then Refresh."),
    ("parse_error", "A log could not be parsed. Update the dashboard and Refresh; the previous saved figures are retained."),
)


def _error_info(error: object) -> tuple[str, str]:
    """Map an exception to fixed public text; never return its message."""
    name = type(error).__name__.lower()
    message = str(error).lower() if isinstance(error, (OSError, ValueError, UnicodeError)) else ""
    if isinstance(error, PermissionError) or "permission" in name or "access is denied" in message:
        key = "permission"
    elif isinstance(error, FileNotFoundError) or "no such file" in message:
        key = "missing_file"
    elif isinstance(error, UnicodeError) or "decode" in name:
        key = "decode"
    elif isinstance(error, ValueError) or "json" in name:
        key = "invalid_json"
    elif isinstance(error, OSError) and getattr(error, "errno", None) == 28:
        key = "disk_full"
    else:
        key = "parse_error"
    return key, dict((category, hint) for category, hint in _ERRORS)[key]


def _sources(found: Mapping | Iterable) -> dict[str, int]:
    if isinstance(found, Mapping):
        result = {}
        for source, count in found.items():
            key = _source_key(source)
            try:
                amount = max(0, int(count))
            except (TypeError, ValueError):
                amount = 0
            if key and amount:
                result[key] = amount
        return result
    result: dict[str, int] = {}
    for source in found or ():
        key = _source_key(source)
        if key:
            result[key] = result.get(key, 0) + 1
    return result


class DiagnosticsTracker:
    """Track only bounded source counts and fixed, non-sensitive issue labels."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._generation = 0
        self._started_at = None
        self._finished_at = None
        self._sources: dict[str, dict] = {}
        self._issues: list[dict] = []
        self._last_success: dict[str, float] = {}

    def begin_scan(self, found: Mapping | Iterable) -> None:
        now = time.time()
        counts = _sources(found)
        with self._lock:
            self._generation += 1
            self._started_at = now
            self._finished_at = None
            self._sources = {
                source: {
                    "source": source, "discovered": count, "read": 0, "errors": 0,
                    "last_attempt": now, "last_success": self._last_success.get(source),
                    "error_category": None, "hint": None,
                }
                for source, count in counts.items()
            }
            self._issues = []

    def record_success(self, source: str, path: str = "") -> None:
        del path  # Paths can contain project names and are never retained.
        now = time.time()
        key = _source_key(source)
        with self._lock:
            item = self._sources.setdefault(key, {
                "source": key, "discovered": 0, "read": 0, "errors": 0,
                "last_attempt": None, "last_success": None,
                "error_category": None, "hint": None,
            })
            item["read"] += 1
            item["last_attempt"] = now
            item["last_success"] = now
            self._last_success[key] = now

    def record_failure(self, source: str, path: str, error: object) -> None:
        del path  # Paths and arbitrary exception strings are not public diagnostics.
        now = time.time()
        key = _source_key(source)
        category, hint = _error_info(error)
        with self._lock:
            item = self._sources.setdefault(key, {
                "source": key, "discovered": 0, "read": 0, "errors": 0,
                "last_attempt": None, "last_success": None,
                "error_category": None, "hint": None,
            })
            item["errors"] += 1
            item["last_attempt"] = now
            item["error_category"] = category
            item["hint"] = hint
            self._issues.append({"source": key, "at": now, "category": category, "hint": hint})
            if len(self._issues) > _ISSUE_LIMIT:
                del self._issues[:-_ISSUE_LIMIT]

    def finish_scan(self) -> None:
        with self._lock:
            self._finished_at = time.time()

    def snapshot(self) -> dict:
        with self._lock:
            sources = []
            for value in self._sources.values():
                row = dict(value)
                row["status"] = ("error" if row["errors"] and not row["read"] else
                                 "partial_error" if row["errors"] else
                                 "available" if row["read"] else "idle")
                sources.append(row)
            return {
                "generation": self._generation,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "sources": sources,
                "issues": [dict(issue) for issue in self._issues],
                "discovered": sum(row["discovered"] for row in sources),
                "read": sum(row["read"] for row in sources),
                "errors": sum(row["errors"] for row in sources),
                "status": ("error" if sources and all(row["status"] == "error" for row in sources) else
                           "partial_error" if any(row["errors"] for row in sources) else
                           "available" if any(row["read"] for row in sources) else "idle"),
            }


tracker = DiagnosticsTracker()


def begin_scan(found: Mapping | Iterable) -> None:
    tracker.begin_scan(found)


def record_success(source: str, path: str = "") -> None:
    tracker.record_success(source, path)


def record_failure(source: str, path: str, error: object) -> None:
    tracker.record_failure(source, path, error)


def finish_scan() -> None:
    tracker.finish_scan()


def get_scan_report(files=None, prices=None, ledger_status=None, detected_sources=None) -> dict:
    """Return parser health plus optional safe, caller-computed status rows.

    The three optional arguments are accepted so the HTTP layer can compose
    ledger and pricing health without putting paths, model rows, or file state
    into this tracker. The caller is responsible for passing allowlisted rows.
    """
    del files, prices, ledger_status, detected_sources
    return tracker.snapshot()
