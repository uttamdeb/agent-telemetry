"""Private, opt-in GitHub release notes and first-open acknowledgement state.

Only :meth:`ReleaseNotes.check` makes a network request. State lives beside the
native client preferences, never in the usage ledger.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
import threading
import time
from typing import Optional, Tuple
import urllib.error
import urllib.parse
import urllib.request

if __package__:
    from . import native
else:
    import native


_FILENAME = "release-notes.json"
_LOCK_FILENAME = "release-notes.lock"
_FORMAT_VERSION = 1
_LOCK = threading.RLock()
_LOCK_TIMEOUT_SECONDS = 5.0
_MAX_FILE_BYTES = 16 * 1024 * 1024
_MAX_INSTALLS = 8
_MAX_RELEASES_PER_INSTALL = 6
_MAX_API_BYTES = 1024 * 1024
_MAX_BODY_CHARS = 48_000
_LEASE_SECONDS = 120
_API_ROOT = "https://api.github.com/repos/uttamdeb/agent-telemetry"
_RELEASE_ROOT = "https://github.com/uttamdeb/agent-telemetry/releases/tag/"
_STABLE_RE = re.compile(r"^v?(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})\.(0|[1-9][0-9]{0,8})$")
_DESCRIBE_RE = re.compile(
    r"^(v?(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8})\.(?:0|[1-9][0-9]{0,8}))"
    r"-[0-9]+-g[0-9a-fA-F]{4,40}(?:-dirty)?$"
)
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseNotesError(ValueError):
    """A safe release-notes state or claim error."""


class _APIError(Exception):
    def __init__(self, message: str, *, missing: bool = False):
        super().__init__(message)
        self.message = message
        self.missing = missing


def _canonical_path(path) -> str:
    try:
        raw = os.fspath(path)
        if not isinstance(raw, str) or not raw:
            raise TypeError
        return os.path.normcase(os.path.realpath(os.path.abspath(os.path.expanduser(raw))))
    except (OSError, TypeError, ValueError):
        raise ReleaseNotesError("The AgentTelemetry installation could not be identified safely.") from None


def _current_info(version) -> Tuple[Optional[str], Optional[str]]:
    """Return (canonical stable version, source tag spelling) when recognizable."""
    if not isinstance(version, dict):
        return None, None
    describe = version.get("describe")
    if not isinstance(describe, str) or not describe or len(describe) > 128:
        return None, None
    match = _STABLE_RE.fullmatch(describe)
    if match:
        components = tuple(int(part) for part in match.groups())
        return ".".join(str(part) for part in components), describe
    match = _DESCRIBE_RE.fullmatch(describe)
    if match:
        tag = match.group(1)
        stable = _STABLE_RE.fullmatch(tag)
        if stable:
            components = tuple(int(part) for part in stable.groups())
            return ".".join(str(part) for part in components), tag
    return None, None


def _version_tuple(value: Optional[str]):
    if not isinstance(value, str):
        return None
    match = _STABLE_RE.fullmatch(value)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def _tag_version(tag) -> Optional[Tuple[str, Tuple[int, int, int]]]:
    if not isinstance(tag, str) or len(tag) > 128:
        return None
    match = _STABLE_RE.fullmatch(tag)
    if not match:
        return None
    numbers = tuple(int(part) for part in match.groups())
    return ".".join(str(part) for part in numbers), numbers


def _tag_url(tag: str) -> str:
    if _tag_version(tag) is None:
        raise ReleaseNotesError("Release information contains an unsupported tag.")
    return _RELEASE_ROOT + urllib.parse.quote(tag, safe="")


def _clean_text(value: str, limit: int, *, allow_newlines: bool = False) -> str:
    # API strings are shown as text by the UI. Drop controls and invalid surrogate
    # code points without interpreting any Markdown or HTML here.
    chars = []
    for char in value:
        code = ord(char)
        if code < 32 or 0x7F <= code < 0xA0:
            if allow_newlines and char in "\n\r\t":
                chars.append(char)
            else:
                chars.append(" ")
        elif 0xD800 <= code <= 0xDFFF:
            chars.append("\uFFFD")
        else:
            chars.append(char)
        if len(chars) >= limit:
            break
    return "".join(chars).strip()


def _valid_published_at(value) -> Optional[str]:
    if not isinstance(value, str) or len(value) > 64:
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        return None
    try:
        # fromisoformat accepts the full timestamp forms GitHub publishes.
        import datetime
        datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
    except (ValueError, OverflowError):
        return None
    return value


def _strict_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid number")))


def _public_release(value: dict, *, required_version: Optional[str] = None) -> dict:
    if not isinstance(value, dict):
        raise _APIError("GitHub returned invalid release information.")
    tag = value.get("tag_name")
    tag_result = _tag_version(tag)
    if tag_result is None:
        raise _APIError("GitHub returned an unsupported release tag.")
    canonical, _ = tag_result
    if required_version is not None and canonical != required_version:
        raise _APIError("GitHub returned release information for a different version.")
    if value.get("draft") is not False or value.get("prerelease") is not False:
        raise _APIError("GitHub did not return a published stable release.")
    published = _valid_published_at(value.get("published_at"))
    if published is None:
        raise _APIError("GitHub did not return a published release.")
    raw_name = value.get("name")
    if raw_name is None or raw_name == "":
        name = "AgentTelemetry " + tag
    elif isinstance(raw_name, str):
        name = _clean_text(raw_name, 200)
        if not name:
            name = "AgentTelemetry " + tag
    else:
        raise _APIError("GitHub returned invalid release information.")
    raw_body = value.get("body")
    if raw_body is None:
        raw_body = ""
    if not isinstance(raw_body, str):
        raise _APIError("GitHub returned invalid release notes.")
    truncated = len(raw_body) > _MAX_BODY_CHARS
    body = _clean_text(raw_body, _MAX_BODY_CHARS, allow_newlines=True)
    return {
        "key": tag,
        "tag": tag,
        "name": name,
        "published_at": published,
        "url": _tag_url(tag),
        "body": body,
        "truncated": truncated,
        "empty": not bool(body.strip()),
        "availability": "available",
    }


def _validate_saved_release(key, item) -> dict:
    if not isinstance(key, str) or _tag_version(key) is None or not isinstance(item, dict):
        raise ValueError("invalid release")
    expected = {"key", "tag", "name", "published_at", "url", "body", "truncated", "empty", "availability"}
    if set(item) != expected or item.get("key") != key or item.get("tag") != key:
        raise ValueError("invalid release")
    parsed = _tag_version(key)
    if not parsed or item.get("url") != _tag_url(key):
        raise ValueError("invalid release")
    if (not isinstance(item.get("name"), str) or not item["name"] or len(item["name"]) > 200
            or not isinstance(item.get("published_at"), str)
            or _valid_published_at(item["published_at"]) != item["published_at"]
            or not isinstance(item.get("body"), str) or len(item["body"]) > _MAX_BODY_CHARS
            or type(item.get("truncated")) is not bool or type(item.get("empty")) is not bool
            or item.get("availability") != "available"
            or item["empty"] != (not bool(item["body"].strip()))):
        raise ValueError("invalid release")
    return dict(item)


def _empty_state() -> dict:
    return {"version": _FORMAT_VERSION, "installs": {}}


def _path() -> Path:
    return Path(native.support_dir()) / _FILENAME


@contextmanager
def _file_lock():
    root = Path(native.support_dir())
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        lock_path = root / _LOCK_FILENAME
        if lock_path.is_symlink():
            raise ReleaseNotesError("Release notes storage cannot be locked safely. The file was preserved.")
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
                            raise ReleaseNotesError("Release notes are busy in another AgentTelemetry process. Retry shortly.") from None
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
                            raise ReleaseNotesError("Release notes are busy in another AgentTelemetry process. Retry shortly.") from None
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
                raise ReleaseNotesError("Release notes storage could not be unlocked safely.")
    except ReleaseNotesError:
        raise
    except OSError:
        raise ReleaseNotesError("Release notes storage could not be locked safely. Check AgentTelemetry's local settings folder.") from None


def _load() -> dict:
    path = _path()
    try:
        if path.is_symlink():
            raise ReleaseNotesError("Release notes storage cannot be read safely. The file was preserved.")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_FILE_BYTES + 1)
    except FileNotFoundError:
        return _empty_state()
    except ReleaseNotesError:
        raise
    except OSError:
        raise ReleaseNotesError("Release notes storage could not be read safely. The file was preserved.") from None
    if len(raw) > _MAX_FILE_BYTES:
        raise ReleaseNotesError("Release notes storage exceeds the safe size limit. The file was preserved.")
    try:
        value = _strict_json(raw)
        if (not isinstance(value, dict) or set(value) != {"version", "installs"}
                or value.get("version") != _FORMAT_VERSION or not isinstance(value.get("installs"), dict)
                or len(value["installs"]) > _MAX_INSTALLS):
            raise ValueError("unsupported state")
        for install_key, entry in value["installs"].items():
            if not isinstance(install_key, str) or not _HEX_RE.fullmatch(install_key) or not isinstance(entry, dict):
                raise ValueError("invalid install")
            if set(entry) != {"baseline", "seen", "latest_key", "releases", "lease"}:
                raise ValueError("invalid install")
            for field in ("baseline", "seen"):
                item = entry.get(field)
                if item is not None and _version_tuple(item) is None:
                    raise ValueError("invalid version")
            latest_key = entry.get("latest_key")
            if latest_key is not None and _tag_version(latest_key) is None:
                raise ValueError("invalid latest release")
            releases = entry.get("releases")
            if not isinstance(releases, dict) or len(releases) > _MAX_RELEASES_PER_INSTALL:
                raise ValueError("invalid releases")
            entry["releases"] = {key: _validate_saved_release(key, item) for key, item in releases.items()}
            if latest_key is not None and latest_key not in entry["releases"]:
                raise ValueError("missing latest release")
            lease = entry.get("lease")
            if lease is not None:
                if (not isinstance(lease, dict) or set(lease) != {"claim", "version", "expires"}
                        or not isinstance(lease.get("claim"), str) or not _HEX_RE.fullmatch(lease["claim"])
                        or _version_tuple(lease.get("version")) is None
                        or isinstance(lease.get("expires"), bool)
                        or not isinstance(lease.get("expires"), (int, float))
                        or not math.isfinite(lease["expires"]) or lease["expires"] <= 0):
                    raise ValueError("invalid lease")
        return value
    except (ValueError, TypeError, UnicodeError, OverflowError, ReleaseNotesError):
        raise ReleaseNotesError("Release notes storage is corrupt or unsupported. The file was preserved.") from None


def _save(state: dict) -> None:
    path = _path()
    root = path.parent
    try:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise ReleaseNotesError("Release notes storage cannot be written safely. The file was preserved.")
        serialized = json.dumps(state, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        encoded = serialized.encode("utf-8", errors="strict")
        if len(encoded) > _MAX_FILE_BYTES:
            raise ReleaseNotesError("Release notes storage exceeds the safe size limit and was not changed.")
        fd, temporary_name = tempfile.mkstemp(prefix=".release-notes-", suffix=".tmp", dir=str(root))
        temporary = Path(temporary_name)
        try:
            if os.name != "nt":
                os.chmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
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
    except ReleaseNotesError:
        raise
    except (OSError, UnicodeError, TypeError, ValueError):
        raise ReleaseNotesError("Release notes storage could not be saved. Check AgentTelemetry's local settings folder.") from None


def _new_install() -> dict:
    return {"baseline": None, "seen": None, "latest_key": None, "releases": {}, "lease": None}


def _get_install(state: dict, key: str, *, create: bool) -> Optional[dict]:
    entry = state["installs"].get(key)
    if entry is None and create:
        if len(state["installs"]) >= _MAX_INSTALLS:
            raise ReleaseNotesError("Release notes storage already contains the maximum number of installations.")
        entry = _new_install()
        state["installs"][key] = entry
    return entry


def _remember(entry: dict, item: dict, *, latest: bool = False) -> None:
    key = item["key"]
    entry["releases"].pop(key, None)
    entry["releases"][key] = item
    if latest:
        entry["latest_key"] = key
    current_key = None
    if entry.get("baseline"):
        for old_key in entry["releases"]:
            parsed = _tag_version(old_key)
            if parsed and parsed[0] == entry["baseline"]:
                current_key = old_key
                break
    protected = {key}
    if entry.get("latest_key"):
        protected.add(entry["latest_key"])
    if current_key:
        protected.add(current_key)
    while len(entry["releases"]) > _MAX_RELEASES_PER_INSTALL:
        for old_key in entry["releases"]:
            if old_key not in protected:
                del entry["releases"][old_key]
                break
        else:
            # With the fixed current/latest protections this can only happen if a
            # future format changes the supported count unexpectedly.
            raise ReleaseNotesError("Release notes storage reached its safe release limit.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _fetch_json(url: str):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "AgentTelemetry release notes",
        "X-GitHub-Api-Version": "2022-11-28",
    }, method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        response = opener.open(request, timeout=8)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise _APIError("GitHub has no published release for this version.", missing=True) from None
        if exc.code in (403, 429):
            raise _APIError("GitHub update information is temporarily rate limited. Try again later.") from None
        raise _APIError("GitHub update information is temporarily unavailable. Try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise _APIError("Could not reach GitHub. Check your connection and try again.") from None
    try:
        geturl = getattr(response, "geturl", None)
        if callable(geturl) and geturl() != url:
            raise _APIError("GitHub redirected the release request, so the response was not used.")
        getcode = getattr(response, "getcode", None)
        if callable(getcode) and getcode() != 200:
            if getcode() == 404:
                raise _APIError("GitHub has no published release for this version.", missing=True)
            if getcode() in (403, 429):
                raise _APIError("GitHub update information is temporarily rate limited. Try again later.")
            raise _APIError("GitHub update information is temporarily unavailable. Try again later.")
        headers = getattr(response, "headers", None)
        content_length = headers.get("Content-Length") if headers is not None else None
        if content_length is not None:
            try:
                if int(content_length) > _MAX_API_BYTES:
                    raise _APIError("GitHub returned release information that was too large.")
            except (TypeError, ValueError):
                raise _APIError("GitHub returned invalid release information.") from None
        raw = response.read(_MAX_API_BYTES + 1)
        if not isinstance(raw, bytes) or len(raw) > _MAX_API_BYTES:
            raise _APIError("GitHub returned release information that was too large.")
    except _APIError:
        raise
    except (OSError, ValueError):
        raise _APIError("GitHub release information could not be read safely.") from None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    try:
        value = _strict_json(raw)
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise _APIError("GitHub returned invalid release information.") from None
    if not isinstance(value, dict):
        raise _APIError("GitHub returned invalid release information.")
    return value


def _fetch_release(url: str, required_version: Optional[str] = None):
    value = _fetch_json(url)
    return _public_release(value, required_version=required_version)


def _placeholder(version: Optional[str], tag_hint: Optional[str] = None) -> dict:
    tag = tag_hint if tag_hint and _tag_version(tag_hint) and _tag_version(tag_hint)[0] == version else None
    # Releases are tagged vX.Y.Z, but a packaged app knows only its bare version
    # ("2.2.0"), and /releases/tag/2.2.0 is a 404.
    url = _tag_url("v" + tag.lstrip("v")) if tag else ""
    label = "AgentTelemetry " + (version or "release")
    return {
        "key": tag or version,
        "tag": tag or version or "",
        "name": label,
        "published_at": None,
        "url": url,
        "body": "",
        "truncated": False,
        "empty": False,
        "availability": "unavailable",
    }


class ReleaseNotes:
    """Fetch and persist safe GitHub release metadata for one app installation."""

    def __init__(self, app_dir, version):
        self.version = version if isinstance(version, dict) else {}
        self.current_version, self._current_tag = _current_info(self.version)
        canonical = _canonical_path(app_dir)
        self._install_key = hashlib.sha256(os.fsencode(canonical)).hexdigest()

    def _view(self, entry: Optional[dict], *, error: Optional[str] = None) -> dict:
        releases = entry.get("releases", {}) if isinstance(entry, dict) else {}
        latest_key = entry.get("latest_key") if isinstance(entry, dict) else None
        latest = releases.get(latest_key) if latest_key else None
        release = None
        if self.current_version:
            for key, item in releases.items():
                parsed = _tag_version(key)
                if parsed and parsed[0] == self.current_version:
                    release = item
                    break
        if release is None:
            release = _placeholder(self.current_version, self._current_tag)
        seen = entry.get("seen") if isinstance(entry, dict) else None
        pending = bool(self.current_version and _version_tuple(seen)
                       and _version_tuple(self.current_version) > _version_tuple(seen))
        latest_version = _tag_version(latest["tag"])[0] if latest else None
        update_available = bool(self.current_version and latest_version
                                and _version_tuple(latest_version) > _version_tuple(self.current_version))
        result = {
            "current_version": self.current_version,
            "pending": pending,
            "release": release,
            "latest": latest,
            "update_available": update_available,
        }
        if error:
            result["error"] = error
        return result

    def status(self) -> dict:
        """Return cached notes and popup state without creating files or accessing network."""
        with _LOCK:
            try:
                state = _load()
                entry = _get_install(state, self._install_key, create=False)
                return self._view(entry)
            except ReleaseNotesError as exc:
                result = self._view(None, error=str(exc))
                result["pending"] = False
                return result

    def _ensure_baseline(self, state: dict) -> tuple[dict, bool]:
        entry = _get_install(state, self._install_key, create=True)
        changed = False
        if self.current_version and entry.get("baseline") is None and entry.get("seen") is None:
            entry["baseline"] = self.current_version
            entry["seen"] = self.current_version
            entry["lease"] = None
            changed = True
        return entry, changed

    def check(self) -> dict:
        """Fetch only after an explicit user action, retaining prior cache on failure."""
        state_problem = None
        try:
            with _LOCK, _file_lock():
                state = _load()
                entry, changed = self._ensure_baseline(state)
                if changed:
                    _save(state)
                cached = self._view(entry)
        except ReleaseNotesError as exc:
            state = None
            entry = None
            cached = self._view(None)
            state_problem = str(exc)

        fetched_latest = None
        fetched_current = None
        errors = []
        latest_success = False
        current_success = False
        try:
            fetched_latest = _fetch_release(_API_ROOT + "/releases/latest")
            latest_success = True
        except _APIError as exc:
            errors.append(exc.message)

        if self.current_version:
            if fetched_latest and _tag_version(fetched_latest["tag"])[0] == self.current_version:
                fetched_current = fetched_latest
                current_success = True
            else:
                tag = self._current_tag or ("v" + self.current_version)
                alternate_tag = tag[1:] if tag.startswith("v") else "v" + tag
                tags = [tag]
                if alternate_tag != tag:
                    tags.append(alternate_tag)
                for candidate in tags:
                    try:
                        fetched_current = _fetch_release(
                            _API_ROOT + "/releases/tags/" + urllib.parse.quote(candidate, safe=""),
                            required_version=self.current_version,
                        )
                        current_success = True
                        break
                    except _APIError as exc:
                        if not exc.missing:
                            errors.append(exc.message)
                            break

        if state_problem is None:
            try:
                with _LOCK, _file_lock():
                    state = _load()
                    entry, changed = self._ensure_baseline(state)
                    if fetched_latest:
                        _remember(entry, fetched_latest, latest=True)
                    if fetched_current and (not fetched_latest or fetched_current["key"] != fetched_latest["key"]):
                        _remember(entry, fetched_current)
                    if changed or fetched_latest or fetched_current:
                        _save(state)
                    cached = self._view(entry)
            except ReleaseNotesError as exc:
                state_problem = str(exc)
        if fetched_latest:
            latest_value = fetched_latest
        else:
            latest_value = cached.get("latest")
        if fetched_current:
            current_value = fetched_current
        else:
            current_value = cached.get("release")
            if current_value and current_value.get("availability") != "available":
                current_value = None

        update_available = bool(
            self.current_version and latest_value
            and _version_tuple(_tag_version(latest_value["tag"])[0]) > _version_tuple(self.current_version)
        )
        result = {
            "status": "available" if latest_success or current_success else "unavailable",
            "current_version": self.current_version,
            "latest": latest_value,
            "release": current_value,
            "update_available": update_available,
            "download_url": latest_value["url"] if latest_value else None,
        }
        error = state_problem or (errors[0] if errors else None)
        if error:
            result["error"] = error
        return result

    def claim(self) -> dict:
        """Establish first-install baseline or reserve one popup for this version."""
        if not self.current_version:
            return {**self._view(None), "claim": None, "pending": False}
        with _LOCK, _file_lock():
            state = _load()
            entry = _get_install(state, self._install_key, create=True)
            changed = False
            if entry.get("baseline") is None and entry.get("seen") is None:
                entry["baseline"] = self.current_version
                entry["seen"] = self.current_version
                entry["lease"] = None
                changed = True
            seen = _version_tuple(entry.get("seen"))
            current = _version_tuple(self.current_version)
            lease = entry.get("lease")
            now = time.time()
            lease_version = _version_tuple(lease["version"]) if lease else None
            if lease and (lease["expires"] <= now or current > lease_version):
                entry["lease"] = None
                lease = None
                changed = True
            pending = bool(seen and current > seen)
            token = None
            if pending and lease is None:
                token = secrets.token_hex(32)
                entry["lease"] = {"claim": token, "version": self.current_version,
                                  "expires": now + _LEASE_SECONDS}
                changed = True
            if changed:
                _save(state)
            result = self._view(entry)
            result["pending"] = bool(pending and token)
            result["claim"] = token
            return result

    def shown(self, claim) -> dict:
        """Acknowledge only the live claim for this exact running app version."""
        if not isinstance(claim, str) or not _HEX_RE.fullmatch(claim):
            raise ReleaseNotesError("The release notes display claim is invalid or expired. Open the dashboard again to retry.")
        if not self.current_version:
            raise ReleaseNotesError("The current app version cannot be identified, so release notes were not acknowledged.")
        with _LOCK, _file_lock():
            state = _load()
            entry = _get_install(state, self._install_key, create=False)
            lease = entry.get("lease") if entry else None
            if (not lease or lease["version"] != self.current_version
                    or lease["expires"] <= time.time()
                    or not hmac.compare_digest(lease["claim"], claim)):
                raise ReleaseNotesError("The release notes display claim is invalid or expired. Open the dashboard again to retry.")
            old_seen = _version_tuple(entry.get("seen"))
            current = _version_tuple(self.current_version)
            if old_seen is None or current > old_seen:
                entry["seen"] = self.current_version
            entry["lease"] = None
            _save(state)
            result = self._view(entry)
            result["shown"] = True
            return result
