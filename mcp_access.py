"""Opt-in, loopback-only MCP access credentials outside the telemetry ledger."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from urllib.parse import urlsplit
from contextlib import contextmanager

import native


_FILENAME = "mcp-access.json"
_LOCK_FILENAME = "mcp-access.lock"
_FORMAT_VERSION = 1
_LOCK = threading.RLock()
_MAX_FILE_BYTES = 64 * 1024
_MAX_ENDPOINTS = 64
_LOCK_TIMEOUT_SECONDS = 5.0
WARNING = (
    "MCP access is off by default. When enabled, AgentTelemetry returns selected local "
    "telemetry to this client; a cloud-backed agent may send tool results to its provider. "
    "Project and device names, and data from other devices, require separate opt-in scopes."
)


class AccessError(ValueError):
    """A safe, user-actionable access configuration or authorization failure."""


def canonical_backend_url(backend_url: str) -> str:
    """Accept only a literal loopback HTTP origin and normalize its spelling."""
    if not isinstance(backend_url, str) or not backend_url or len(backend_url) > 256:
        raise AccessError("Backend must be a literal loopback URL such as http://127.0.0.1:7878.")
    if any(ord(ch) < 0x21 or ord(ch) == 0x7f for ch in backend_url):
        raise AccessError("Backend URL contains invalid characters.")
    try:
        parsed = urlsplit(backend_url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        raise AccessError("Backend URL is invalid. Use a literal loopback address and valid port.") from None
    if parsed.scheme.lower() != "http" or not host or parsed.username is not None or parsed.password is not None:
        raise AccessError("Backend must use plain HTTP to a literal loopback address, without user info.")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment or "?" in backend_url or "#" in backend_url:
        raise AccessError("Backend URL must be a loopback origin without a path, query, or fragment.")
    if "%" in parsed.netloc:
        raise AccessError("Backend URL must use a literal loopback address without escaping.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise AccessError("Backend host must be the literal address 127.0.0.1 or [::1].") from None
    if not address.is_loopback or str(address) not in ("127.0.0.1", "::1"):
        raise AccessError("Backend host must be the literal address 127.0.0.1 or [::1].")
    port = 80 if port is None else port
    if not 1 <= port <= 65535:
        raise AccessError("Backend port must be between 1 and 65535.")
    host_part = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{host_part}:{port}"


def _data_identity(data_dir) -> str:
    if data_dir is None:
        raise AccessError("The active AgentTelemetry data directory is unavailable.")
    try:
        raw = os.fspath(data_dir)
        if not isinstance(raw, str) or not raw:
            raise TypeError
        canonical = os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(raw))))
    except (OSError, TypeError, ValueError):
        raise AccessError("The active AgentTelemetry data directory is unavailable.") from None
    return hashlib.sha256(os.fsencode(canonical)).hexdigest()


def _entry_key(backend_url: str) -> str:
    return hashlib.sha256(backend_url.encode("utf-8")).hexdigest()


def _path() -> Path:
    return Path(native.support_dir()) / _FILENAME


@contextmanager
def _file_lock():
    """Serialize access across dashboard processes sharing native support state."""
    root = Path(native.support_dir())
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = root / _LOCK_FILENAME
        if lock_path.is_symlink():
            raise AccessError("MCP access settings cannot be locked safely. Keep the file and repair it in Settings.")
        flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(lock_path), flags, 0o600)
        acquired = False
        try:
            if os.name != "nt":
                os.fchmod(fd, 0o600)
            if os.fstat(fd).st_size == 0:
                os.lseek(fd, 0, os.SEEK_SET)
                os.write(fd, b"\0")
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise AccessError("MCP access settings are busy in another AgentTelemetry process. Retry shortly.") from None
                        time.sleep(0.05)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise AccessError("MCP access settings are busy in another AgentTelemetry process. Retry shortly.") from None
                        time.sleep(0.05)
            yield
        finally:
            unlock_failed = False
            if acquired:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    unlock_failed = True
            os.close(fd)
            if unlock_failed:
                raise AccessError("MCP access settings could not be unlocked safely.")
    except AccessError:
        raise
    except OSError:
        raise AccessError("MCP access settings could not be locked safely. Check AgentTelemetry's local settings folder.") from None


def _load() -> dict:
    path = _path()
    try:
        if path.is_symlink():
            raise AccessError("MCP access settings cannot be read safely. Keep the file and repair it in Settings.")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return {"version": _FORMAT_VERSION, "endpoints": {}}
    except AccessError:
        raise
    except OSError:
        raise AccessError("MCP access settings cannot be read safely. Keep the file and repair it in Settings.") from None
    if len(raw) > _MAX_FILE_BYTES:
        raise AccessError("MCP access settings exceed the safe size limit. The file was preserved.")
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError):
        raise AccessError("MCP access settings are corrupt. The file was preserved; repair it in Settings.") from None
    if (not isinstance(value, dict) or value.get("version") != _FORMAT_VERSION
            or not isinstance(value.get("endpoints"), dict)):
        raise AccessError("MCP access settings are unsupported or corrupt. The file was preserved.")
    if len(value["endpoints"]) > _MAX_ENDPOINTS:
        raise AccessError("MCP access settings exceed the safe endpoint limit. The file was preserved.")
    for key, entry in value["endpoints"].items():
        if (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key) or not isinstance(entry, dict)
                or not isinstance(entry.get("backend"), str)
                or not isinstance(entry.get("data_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["data_hash"])
                or type(entry.get("enabled")) is not bool
                or type(entry.get("include_names")) is not bool
                or type(entry.get("include_peers")) is not bool
                or not isinstance(entry.get("token"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", entry["token"])):
            raise AccessError("MCP access settings are unsupported or corrupt. The file was preserved.")
        try:
            normalized = canonical_backend_url(entry["backend"])
        except AccessError:
            raise AccessError("MCP access settings contain an invalid backend. The file was preserved.") from None
        if _entry_key(normalized) != key or normalized != entry["backend"]:
            raise AccessError("MCP access settings are inconsistent. The file was preserved.")
    return value


def _save(value: dict) -> None:
    path = _path()
    root = path.parent
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise AccessError("MCP access settings cannot be written safely. Keep the file and repair it in Settings.")
        serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
        if len(serialized.encode("utf-8")) > _MAX_FILE_BYTES:
            raise AccessError("MCP access settings exceed the safe size limit and were not changed.")
        fd, temporary_name = tempfile.mkstemp(prefix=".mcp-access-", suffix=".tmp", dir=str(root))
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temporary), str(path))
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
    except AccessError:
        raise
    except OSError:
        raise AccessError("MCP access settings could not be saved. Check AgentTelemetry's local settings folder.") from None


def _public(entry: dict | None, data_hash: str) -> dict:
    entry = entry if isinstance(entry, dict) else {}
    matches = bool(entry) and hmac.compare_digest(entry.get("data_hash", ""), data_hash)
    return {
        "enabled": bool(matches and entry.get("enabled") is True),
        "include_names": bool(matches and entry.get("include_names") is True),
        "include_peers": bool(matches and entry.get("include_peers") is True),
        "warning": WARNING,
    }


def status(backend_url: str, data_dir) -> dict:
    """Return consent state without ever returning a credential or local path."""
    backend = canonical_backend_url(backend_url)
    data_hash = _data_identity(data_dir)
    key = _entry_key(backend)
    with _LOCK, _file_lock():
        state = _load()
        return _public(state["endpoints"].get(key), data_hash)


def configure(backend_url: str, data_dir, body) -> dict:
    """Apply explicit local consent; endpoint entries for other ports stay intact."""
    backend = canonical_backend_url(backend_url)
    data_hash = _data_identity(data_dir)
    if not isinstance(body, dict):
        raise AccessError("MCP access settings must be a JSON object.")
    unknown = set(body) - {"enabled", "include_names", "include_peers", "confirmed"}
    if unknown:
        raise AccessError("MCP access settings contain an unsupported field.")
    if "enabled" not in body or type(body["enabled"]) is not bool:
        raise AccessError("enabled must be a boolean.")
    for field in ("include_names", "include_peers", "confirmed"):
        if field in body and type(body[field]) is not bool:
            raise AccessError(f"{field} must be a boolean.")
    key = _entry_key(backend)
    with _LOCK, _file_lock():
        state = _load()
        old = state["endpoints"].get(key)
        same_binding = bool(old) and hmac.compare_digest(old.get("data_hash", ""), data_hash)
        prior = old if same_binding else {}
        enabled = body["enabled"]
        names = body.get("include_names", prior.get("include_names", False))
        peers = body.get("include_peers", prior.get("include_peers", False))
        expands_scope = ((names and not prior.get("include_names", False))
                         or (peers and not prior.get("include_peers", False)))
        if (enabled and not prior.get("enabled", False) or expands_scope) and body.get("confirmed") is not True:
            raise AccessError("Confirm MCP access and any name or peer sharing in the AgentTelemetry Settings prompt.")
        changed = (not same_binding or enabled != prior.get("enabled", False)
                   or names != prior.get("include_names", False)
                   or peers != prior.get("include_peers", False))
        token = secrets.token_hex(32) if changed else prior["token"]
        if old is None and len(state["endpoints"]) >= _MAX_ENDPOINTS:
            raise AccessError("MCP access settings already contain the maximum number of local dashboards.")
        state["endpoints"][key] = {
            "backend": backend,
            "data_hash": data_hash,
            "enabled": enabled,
            "include_names": names,
            "include_peers": peers,
            "token": token,
        }
        _save(state)
        return _public(state["endpoints"][key], data_hash)


def credentials(backend_url: str) -> str:
    """Return the active local key for one endpoint, or fail with a safe hint."""
    backend = canonical_backend_url(backend_url)
    key = _entry_key(backend)
    with _LOCK, _file_lock():
        state = _load()
        entry = state["endpoints"].get(key)
        if not entry or entry.get("backend") != backend or entry.get("enabled") is not True:
            raise AccessError("MCP access is disabled. Enable it in AgentTelemetry Settings, then restart the MCP client.")
        return entry["token"]


def authorize(backend_url: str, data_dir, authorization_header) -> dict:
    """Validate a bearer key and return the configured, non-client-controlled policy."""
    backend = canonical_backend_url(backend_url)
    data_hash = _data_identity(data_dir)
    if not isinstance(authorization_header, str) or not authorization_header.startswith("Bearer "):
        raise AccessError("MCP authorization is missing or invalid.")
    supplied = authorization_header[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", supplied):
        raise AccessError("MCP authorization is missing or invalid.")
    key = _entry_key(backend)
    with _LOCK, _file_lock():
        state = _load()
        entry = state["endpoints"].get(key)
        if not entry or entry.get("backend") != backend or entry.get("enabled") is not True:
            raise AccessError("MCP access is disabled or has been revoked.")
        if not hmac.compare_digest(entry.get("data_hash", ""), data_hash):
            raise AccessError("MCP access is bound to a different AgentTelemetry data directory. Re-enable it in Settings.")
        if not hmac.compare_digest(entry["token"], supplied):
            raise AccessError("MCP authorization is invalid. Re-enable access in AgentTelemetry Settings.")
        return {
            "include_names": entry["include_names"],
            "include_peers": entry["include_peers"],
            "alias_key": hashlib.sha256((entry["token"] + ":alias").encode("utf-8")).hexdigest(),
        }

