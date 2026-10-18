"""Private rotating backups for AgentTelemetry's durable usage ledger.

The module deliberately operates on bytes and receives the ledger path from its
caller. It does not import dashboard.py, inspect source logs, or mutate a live
in-memory ledger. Callers must serialize it with refresh/save operations.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import secrets
import stat
import tempfile
from pathlib import Path


BACKUP_DIR_NAME = ".usage-backups"
_ID_RE = re.compile(r"^(\d{8}T\d{6}Z)-([0-9a-f]{32})-(scheduled|manual|pre-restore|safeguard)\.json$")
_REASONS = frozenset(("scheduled", "manual", "pre-restore", "safeguard"))
_KEEP_PER_REASON = {"scheduled": 7, "manual": 5, "pre-restore": 5, "safeguard": 5}
_USAGE_FIELDS = frozenset(("in", "out", "cr", "cc", "cc5", "cc1", "reason",
                           "asst", "user", "req", "prem", "tools", "active", "cost", "ws"))


class BackupError(ValueError):
    """A requested backup or restore could not be safely used."""


def _backup_dir(cache_path):
    cache = Path(cache_path).absolute()
    return cache.parent / BACKUP_DIR_NAME


def _check_directory(directory, create=False):
    if create:
        directory.parent.mkdir(parents=True, exist_ok=True)
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    if directory.is_symlink():
        raise BackupError("backup directory must not be a symlink")
    if directory.exists() and not directory.is_dir():
        raise BackupError("backup path is not a directory")
    if directory.exists():
        try:
            os.chmod(directory, 0o700)
        except OSError:
            pass


def _check_cache_path(cache_path):
    path = Path(cache_path).absolute()
    if path.is_symlink():
        raise BackupError("ledger path must not be a symlink")
    if path.exists() and not path.is_file():
        raise BackupError("ledger path is not a regular file")
    return path


def _utc(now=None):
    if now is None:
        now = _dt.datetime.now(_dt.timezone.utc)
    elif isinstance(now, (int, float)):
        now = _dt.datetime.fromtimestamp(now, _dt.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_dt.timezone.utc)
    else:
        now = now.astimezone(_dt.timezone.utc)
    return now


def _filename(reason, now=None):
    stamp = _utc(now).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(16)}-{reason}.json"


def _validate_usage(row):
    if not isinstance(row, dict):
        raise BackupError("ledger aggregate has invalid usage rows")
    fields = _USAGE_FIELDS | {"lc_" + field for field in _USAGE_FIELDS}
    for field in fields.intersection(row):
        value = row[field]
        if type(value) not in (int, float) or value < 0:
            raise BackupError("ledger aggregate has invalid usage values")
        try:
            finite = math.isfinite(value)
        except OverflowError:
            finite = False
        if not finite:
            raise BackupError("ledger aggregate has invalid usage values")
    if "exact" in row:
        _validate_usage(row["exact"])


def _validate_records(records):
    if not isinstance(records, dict):
        raise BackupError("ledger aggregate has invalid records")
    for key, row in records.items():
        if not isinstance(key, str) or "\t" not in key:
            raise BackupError("ledger aggregate has invalid usage keys")
        date, model = key.split("\t", 1)
        try:
            if len(date) != 10 or not model:
                raise ValueError
            _dt.date.fromisoformat(date)
        except ValueError as exc:
            raise BackupError("ledger aggregate has invalid usage keys") from exc
        _validate_usage(row)


def _decode_ledger(raw, expected_version):
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
        raise BackupError("backup is not valid UTF-8 JSON") from exc
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data["version"] < 52 or data["version"] > expected_version
            or not isinstance(data.get("files"), dict)
            or any(not isinstance(agg, dict) for agg in data["files"].values())):
        raise BackupError("ledger structure or version is not supported")
    for agg in data["files"].values():
        if not isinstance(agg.get("source"), str) or not agg["source"].strip():
            raise BackupError("ledger aggregate has no recognized source")
        # These containers are consumed directly by the dashboard and parser.
        # A JSON file can be syntactically valid yet unusable as a ledger.
        for name in ("state", "totals", "tools", "skills", "ctx", "hourly", "activity",
                     "reads", "used_ext", "mcp_offered", "compactions"):
            if name in agg and not isinstance(agg[name], dict):
                raise BackupError("ledger aggregate has invalid " + name)
        _validate_records(agg.get("records", {}))
        sessions = agg.get("sessions", [])
        if not isinstance(sessions, list) or any(not isinstance(s, dict) for s in sessions):
            raise BackupError("ledger aggregate has invalid sessions")
        for session in sessions:
            # Multi-session stores render and price these buckets instead of the
            # aggregate records. Validate them before replacing a working ledger.
            _validate_usage(session)
            if "records" in session:
                _validate_records(session["records"])
            if session.get("days") is not None:
                if not isinstance(session["days"], dict):
                    raise BackupError("ledger session has invalid daily usage")
                for row in session["days"].values():
                    _validate_usage(row)
    return data


def _read_backup(directory, backup_id):
    if not isinstance(backup_id, str) or not _ID_RE.fullmatch(backup_id):
        raise BackupError("invalid backup id")
    path = directory / backup_id
    if path.is_symlink():
        raise BackupError("backup file must not be a symlink")
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise BackupError("backup is not a regular file")
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise BackupError("backup not found") from exc
    return raw


def _metadata(directory, path, expected_version):
    match = _ID_RE.fullmatch(path.name)
    if not match or path.is_symlink():
        return None
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    valid = True
    summary = None
    try:
        data = _decode_ledger(raw, expected_version)
        summary = {"version": data["version"], "entries": len(data["files"]),
                   "archived": sum(1 for a in data["files"].values() if a.get("archived"))}
        sessions = sum(len(a.get("sessions", [])) for a in data["files"].values()
                       if isinstance(a.get("sessions", []), list))
    except BackupError:
        valid = False
    try:
        stamp = _dt.datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        return None
    return {"id": path.name, "created_at": stamp.isoformat().replace("+00:00", "Z"),
            "reason": match.group(3), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
            "valid": valid, "summary": summary,
            "files": summary["entries"] if summary else None,
            "sessions": sessions if valid else None,
            "_mtime_ns": info.st_mtime_ns}


def _list_directory(directory, expected_version):
    _check_directory(directory)
    if not directory.exists():
        return []
    result = []
    for path in directory.iterdir():
        item = _metadata(directory, path, expected_version)
        if item is not None:
            result.append(item)
    result.sort(key=lambda x: (x["created_at"], x["_mtime_ns"]), reverse=True)
    for item in result:
        item.pop("_mtime_ns", None)
    return result


def list_backups(cache_path, expected_version):
    """List recognized snapshots newest-first, including invalid safety snapshots."""
    return _list_directory(_backup_dir(cache_path), expected_version)


def _write_private_atomic(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".ledger-backup-", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(path))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        try:
            dfd = os.open(str(path.parent), getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _rotate(directory, reason, expected_version, protect_id=None):
    listing = _list_directory(directory, expected_version)
    items = [x for x in listing if x["reason"] == reason]
    keep = _KEEP_PER_REASON[reason]
    if len(items) <= keep:
        return
    valid_count = sum(1 for x in listing if x["valid"])
    for item in reversed(items):
        if len(items) <= keep:
            break
        if item["id"] == protect_id:
            continue
        if item["valid"] and valid_count <= 1:
            continue
        (directory / item["id"]).unlink()
        items.remove(item)
        if item["valid"]:
            valid_count -= 1


def create_backup(cache_path, reason="scheduled", force=False, *, expected_version, now=None):
    """Snapshot exact on-disk bytes; return metadata, or None if scheduled today already exists.

    Corrupt/unreadable JSON is retained as a non-restorable `safeguard` snapshot.
    `force=True` bypasses only the daily scheduled throttle.
    """
    if reason not in _REASONS:
        raise BackupError("unknown backup reason")
    path = _check_cache_path(cache_path)
    if not path.exists():
        return None
    raw = path.read_bytes()
    actual_reason = reason
    if reason != "safeguard":
        try:
            _decode_ledger(raw, expected_version)
        except BackupError:
            actual_reason = "safeguard"
    directory = _backup_dir(path)
    _check_directory(directory, create=True)
    current = _utc(now)
    if reason == "scheduled" and not force:
        today = current.date()
        for item in list_backups(path, expected_version):
            if item["reason"] == "scheduled" and _dt.datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).date() == today:
                return None
    name = _filename(actual_reason, current)
    target = directory / name
    if target.exists() or target.is_symlink():
        raise BackupError("backup id collision")
    _write_private_atomic(target, raw)
    _rotate(directory, actual_reason, expected_version, protect_id=name)
    return next(x for x in list_backups(path, expected_version) if x["id"] == name)


def _current_digest(path):
    if path.is_symlink():
        raise BackupError("ledger path must not be a symlink")
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def preview_restore(cache_path, backup_id, expected_version):
    """Validate a candidate and return hashes that bind a later restore to this preview."""
    path = _check_cache_path(cache_path)
    directory = _backup_dir(path)
    _check_directory(directory)
    raw = _read_backup(directory, backup_id)
    ledger = _decode_ledger(raw, expected_version)
    sessions = sum(len(a.get("sessions", [])) for a in ledger["files"].values()
                   if isinstance(a.get("sessions", []), list))
    return {"ok": True, "backup_id": backup_id, "sha256": hashlib.sha256(raw).hexdigest(),
            "current_sha256": _current_digest(path),
            "summary": {"version": ledger["version"], "entries": len(ledger["files"]),
                        "archived": sum(1 for a in ledger["files"].values() if a.get("archived")),
                        "sessions": sessions}}


def restore_backup(cache_path, backup_id, expected_sha256, expected_current_sha256,
                   *, expected_version, now=None):
    """Restore only the exact candidate/current bytes shown in a prior preview.

    The current ledger (valid or corrupt) is first preserved as a private backup.
    The caller must hold its cache/refresh lock for the whole preview-ticket flow.
    """
    path = _check_cache_path(cache_path)
    directory = _backup_dir(path)
    _check_directory(directory)
    raw = _read_backup(directory, backup_id)
    _decode_ledger(raw, expected_version)
    candidate_hash = hashlib.sha256(raw).hexdigest()
    if not secrets.compare_digest(candidate_hash, str(expected_sha256 or "")):
        raise BackupError("backup changed after preview")
    current_hash = _current_digest(path)
    if current_hash != expected_current_sha256:
        raise BackupError("ledger changed after preview")
    if path.exists():
        saved = create_backup(path, reason="pre-restore", force=True,
                              expected_version=expected_version, now=now)
        if saved is None:
            raise BackupError("could not preserve the current ledger")
    _write_private_atomic(path, raw)
    return {"ok": True, "backup_id": backup_id, "sha256": candidate_hash,
            "restored_bytes": len(raw)}
