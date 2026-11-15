"""Verified native title edits and private Claude-child display names. No turns."""
import hashlib
import json
from contextlib import closing, contextmanager
import os
from pathlib import Path, PurePosixPath
import queue
import re
import sqlite3
import subprocess
import sys
import threading
import time
import uuid

if __package__:
    from . import native as N
else:
    import native as N

SUPPORTED = {"claude", "codex", "opencode"}
_plans = {}
_display_lock = threading.Lock()
_DISPLAY_FILE = "session-display-names.json"


def claude_child_identity(path):
    """Recognize native child layouts, including workflow transcripts.

    Normalize separators for retained Windows paths without treating any caller
    identity as a filename. Only the dashboard's discovered path reaches here.
    """
    text = str(path)
    normalized = text.replace("\\", "/")
    windows = "\\" in text or bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("//")
    parts = PurePosixPath(normalized).parts
    for index, part in enumerate(parts):
        if part != "subagents" or index == 0: continue
        try: parent = str(uuid.UUID(parts[index - 1]))
        except ValueError: continue
        child = parts[index + 1:]
        if len(child) == 3:
            if child[0] != "workflows" or not re.fullmatch(r"wf_[A-Za-z0-9_-]+", child[1]): continue
        elif len(child) != 1: continue
        if not re.fullmatch(r"agent-[A-Za-z0-9_-]+\.jsonl", child[-1]): continue
        if windows: child = tuple(part.lower() for part in child)
        identity = "/".join((parent, "subagents", *child))[:-6]
        return identity if len(identity) <= 512 else ""
    return ""


def display_scope(source, path, subagent=False):
    """Whether this session's name is kept only in AgentTelemetry.

    Claude subagents, and every tool whose own title can't be edited safely
    (Copilot, Cursor, Claude Desktop, Antigravity, Hermes, OpenClaw, OpenCode's
    older JSON storage): their source files are never written for a name.
    """
    if source == "claude": return bool(subagent)
    if source == "opencode": return not str(path).endswith(".db")
    return source not in SUPPORTED


def display_availability(source, path, subagent=False, remote=False):
    if remote: return False, "Name this session on its source device."
    if source == "claude" and (not subagent or not claude_child_identity(path)):
        return False, "This Claude child transcript layout is not supported for a local display name."
    if not display_scope(source, path, subagent):
        return False, "This session is renamed in its own tool."
    if not os.path.isfile(path):
        return False, "The original log is unavailable; its saved telemetry is preserved."
    if source == "claude":
        return True, "Names this subagent only in AgentTelemetry; Claude and its parent session stay unchanged."
    return True, "Names this session only in AgentTelemetry; the tool keeps its own title."


def display_names():
    """Read strict private metadata; corrupt/unreadable data must never be replaced."""
    try:
        with open(N.support_dir() / _DISPLAY_FILE, "rb") as file:
            raw = file.read(1024 * 1024 + 1)
    except FileNotFoundError: return {}
    except OSError as error:
        raise ValueError("Saved display names could not be read. Check the private preferences directory before retrying.") from error
    try:
        data = json.loads(raw)
        if (len(raw) > 1024 * 1024 or not isinstance(data, dict) or set(data) != {"version", "names"}
                or type(data.get("version")) is not int or data["version"] != 1
                or not isinstance(data.get("names"), dict)):
            raise ValueError()
        for key, name in data["names"].items():
            if not re.fullmatch(r"[0-9a-f]{64}", key) or validate_name(name) != name:
                raise ValueError()
        return data["names"]
    except (ValueError, TypeError) as error:
        raise ValueError("Saved display names are invalid. Preserve or restore the private preferences file before retrying.") from error


def _display_key(device, source, path, session_id):
    value = [device, source, os.path.normcase(os.path.realpath(path)), session_id]
    return hashlib.sha256(json.dumps(value, ensure_ascii=True).encode("utf-8")).hexdigest()


def display_name(names, device, source, path, session_id):
    return names.get(_display_key(device, source, path, session_id))


@contextmanager
def _display_store_lock():
    root = N.support_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _display_lock, open(root / (_DISPLAY_FILE + ".lock"), "a+b") as file:
        if os.name != "nt": os.chmod(file.name, 0o600)
        if os.name == "nt":
            import msvcrt
            file.seek(0, os.SEEK_END)
            if not file.tell(): file.write(b"\0"); file.flush()
            file.seek(0)
            lock = lambda: msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            unlock = lambda: (file.seek(0), msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1))
        else:
            import fcntl
            lock = lambda: fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            unlock = lambda: fcntl.flock(file.fileno(), fcntl.LOCK_UN)
        deadline = time.monotonic() + 3
        while True:
            try: lock(); break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    raise ValueError("Another display-name save is still running. Wait and retry.")
                time.sleep(.05)
        try: yield
        finally: unlock()


def _write_display_names(names):
    root = N.support_dir()
    value = json.dumps({"version": 1, "names": names})
    if len(value.encode("utf-8")) > 1024 * 1024:
        raise ValueError("The private display-name file is full; existing names were preserved.")
    temporary = root / (_DISPLAY_FILE + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as file:
            if os.name != "nt": os.chmod(temporary, 0o600)
            file.write(value)
            file.flush(); os.fsync(file.fileno())
        os.replace(temporary, root / _DISPLAY_FILE)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass


def prepare_display(device, source, path, session_id, name, original_title, subagent=True):
    name = validate_name(name)
    ok, reason = display_availability(source, path, subagent)
    if not ok: raise ValueError(reason)
    if source == "claude" and session_id != claude_child_identity(path):
        raise ValueError("The Claude child identity does not match its discovered transcript.")
    names = display_names()
    stat = os.stat(path)
    key = _display_key(device, source, path, session_id)
    ticket, now = uuid.uuid4().hex, time.monotonic()
    for old in list(_plans):
        if now - _plans[old]["at"] > 300: del _plans[old]
    if len(_plans) >= 128: _plans.pop(next(iter(_plans)))
    _plans[ticket] = {"scope": "display", "at": now, "device": device, "source": source,
                      "path": path, "id": session_id, "name": name, "key": key,
                      "previous": names.get(key), "original": original_title,
                      "file_identity": [stat.st_dev, stat.st_ino]}
    return {"ticket": ticket, "scope": "display", "name": name,
            "current_name": names.get(key) or original_title,
            "target": "AgentTelemetry local display name · " + session_id}


def commit_display(ticket, device, source, path, session_id, confirmed):
    if confirmed is not True:
        raise ValueError("Confirm the AgentTelemetry-only display-name preview before saving.")
    plan = _plans.pop(ticket, None) if isinstance(ticket, str) else None
    if (plan is None or plan.get("scope") != "display" or time.monotonic() - plan["at"] > 300
            or [plan[k] for k in ("device", "source", "path", "id")] != [device, source, path, session_id]
            or (source == "claude" and session_id != claude_child_identity(path))):
        raise ValueError("Display-name preview expired or the source changed. Review it again.")
    stat = os.stat(path)
    if ([stat.st_dev, stat.st_ino] != plan["file_identity"]
            or _display_key(device, source, path, session_id) != plan["key"]):
        raise ValueError("The source changed. Review the display-name preview again.")
    with _display_store_lock():
        names = display_names()
        if names.get(plan["key"]) != plan["previous"]:
            raise ValueError("The display name changed. Review the preview again.")
        # Choosing the source name also removes a previous local override.
        if plan["name"] == plan["original"]: names.pop(plan["key"], None)
        else: names[plan["key"]] = plan["name"]
        _write_display_names(names)
    return {"name": plan["name"], "title": plan["name"], "scope": "display",
            "original_title": plan["original"], "display_alias": plan["name"] != plan["original"]}


def identity(aggregate, session):
    # Visible session ids are shortened to eight characters. Never use them as
    # write identities: resolve the full native id from its original metadata.
    if aggregate["source"] == "claude":
        if aggregate.get("subagent"):
            child = claude_child_identity(aggregate.get("path", ""))
            if child: return child
        stem = PurePosixPath(str(aggregate.get("path", "")).replace("\\", "/")).stem
        try: return str(uuid.UUID(stem))
        except ValueError: return stem
    if aggregate["source"] == "codex":
        if aggregate.get("_session_id"): return aggregate["_session_id"]
        name = Path(aggregate.get("path", "")).name
        if name.lower().endswith(".jsonl.zst"):      # the same thread as the plain rollout it replaced
            name = name[:-4]
        tail = Path(name).stem[-36:]
        try: uuid.UUID(tail)
        except ValueError: return ""
        return tail
    if aggregate["source"] == "antigravity":
        # Antigravity's transcript reader records one full UUID per SQLite
        # conversation. Do not use a path, title, or shortened table id as an
        # identity; the reader's snapshot ID is only a fallback for old rows.
        for value in (session.get("_sid"), aggregate.get("conversation_id"), session.get("id")):
            if not isinstance(value, str) or "/" in value or "\\" in value:
                continue
            if value.lower().endswith(".db"):
                value = value[:-3]
            try:
                return str(uuid.UUID(value))
            except (ValueError, AttributeError):
                continue
        return ""
    return session.get("_sid") or session.get("id") or ""


def validate_name(name):
    if not isinstance(name, str):
        raise ValueError("Session name must be text.")
    name = name.strip()
    if not name or len(name) > 90 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Use 1–90 characters without control characters.")
    try: name.encode("utf-8")
    except UnicodeError:
        raise ValueError("Use valid Unicode text for the session name.")
    return " ".join(name.split())


def availability(source, path, remote=False):
    if remote:
        return False, "Rename this session on its source device."
    if not os.path.isfile(path):
        return False, "The original log is unavailable; its saved telemetry is preserved."
    if source not in SUPPORTED or (source == "opencode" and not path.endswith(".db")):
        return False, "This tool's original title format is not yet supported for safe editing."
    if source == "claude":
        try: uuid.UUID(Path(path).stem)
        except ValueError:
            return False, "This Claude transcript has no verified session UUID."
    if source == "codex" and not N.codex_executable():
        return False, ("Codex's command-line program wasn't found. Install the ChatGPT app, "
                       "the Codex editor extension or the Codex CLI to rename Codex sessions.")
    if source == "codex":
        try: _codex_home(path)
        except ValueError as error: return False, str(error)
    return True, ""


RUNNING_CLAUDE = ("Claude Code is running this session, and it would put the old name back on its next turn. "
                  "Rename it with /rename in Claude Code, or rename it here after you close the session.")


def _pid_alive(pid):
    """Whether a process with this id exists. Never os.kill on Windows: there it terminates the process."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel["OpenProcess"]
        open_process.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        open_process.restype = wintypes.HANDLE
        exit_code = kernel["GetExitCodeProcess"]
        exit_code.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        exit_code.restype = wintypes.BOOL
        close = kernel["CloseHandle"]
        close.argtypes = (wintypes.HANDLE,)
        close.restype = wintypes.BOOL
        handle = open_process(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return ctypes.get_last_error() == 5  # ERROR_ACCESS_DENIED: it exists
        try:
            code = wintypes.DWORD()
            return bool(exit_code(handle, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
        finally:
            close(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _same_process(pid, started):
    """False only when this pid now belongs to a process started at another time (a reused pid).
    Claude Code records `procStart` in ps's lstart format, in UTC; Windows records are not compared."""
    if os.name == "nt" or not isinstance(started, str) or not started.strip():
        return True
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=3, env={**os.environ, "TZ": "UTC", "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return True
    actual = " ".join(out.stdout.split())
    return out.returncode != 0 or not actual or actual == " ".join(started.split())


def _this_computer(domain):
    """Claude Code's registry may be shared with another computer; its pids mean nothing here."""
    if domain is None:
        return True
    if not isinstance(domain, str):
        return False
    if os.name == "nt":
        name = os.environ.get("COMPUTERNAME", "")
        return bool(name) and domain.lower().startswith("win32:") and \
            domain[6:].split(".")[0].lower() == name.split(".")[0].lower()
    return domain == sys.platform


def claude_running(path, session_id):
    """Whether a Claude Code process on this computer has this session open. A running session keeps
    its name in memory and appends it again on its next turn, so a rename from here would not last.
    Reads only Claude Code's own session registry (<config>/sessions/<pid>.json) beside the
    transcript's <config>/projects folder."""
    transcript = Path(path)
    if len(transcript.parents) < 3 or transcript.parents[1].name != "projects":
        return False
    registry = transcript.parents[2] / "sessions"
    try:
        names = sorted(os.listdir(registry))[:512]
    except OSError:
        return False
    for name in names:
        match = re.fullmatch(r"(\d{1,10})\.json", name)
        if not match:
            continue
        try:
            with open(registry / name, "rb") as handle:
                record = json.loads(handle.read(65536))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("sessionId") != session_id:
            continue
        pid = record.get("pid")
        if (isinstance(pid, int) and not isinstance(pid, bool) and pid == int(match.group(1)) and pid > 0 and
                _this_computer(record.get("pidDomain")) and _pid_alive(pid) and
                _same_process(pid, record.get("procStart"))):
            return True
    return False


def _codex_home(path):
    for parent in Path(path).resolve().parents:
        if parent.name in ("sessions", "archived_sessions"):
            return str(parent.parent)
    raise ValueError("The original Codex profile could not be resolved safely.")


def _codex_rpc(method, params, home=None):
    if method not in ("thread/read", "thread/name/set"):
        raise ValueError("Only Codex title metadata methods are permitted.")
    executable = N.codex_executable()
    if not executable:
        raise ValueError("Codex's command-line program wasn't found.")
    # A dedicated local metadata connection: never start/resume a thread or turn.
    environment = os.environ.copy()
    environment.pop("AGENT_TELEMETRY_CONTROL_TOKEN", None)
    if home: environment["CODEX_HOME"] = home
    process = subprocess.Popen([executable, "app-server", "--listen", "stdio://",
        "-c", "analytics.enabled=false"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=environment)
    replies = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                try: message = json.loads(line)
                except ValueError: continue
                if message.get("id") in (1, 2): replies.put(message)
        finally: replies.put(None)
    threading.Thread(target=read, daemon=True).start()
    def send(message):
        process.stdin.write(json.dumps(message) + "\n"); process.stdin.flush()
    def receive(expected):
        deadline = time.monotonic() + 20
        while True:
            try: message = replies.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty: raise ValueError("Codex did not confirm the metadata request; refresh before retrying.")
            if message is None:
                raise ValueError("Codex's local name API is unavailable in this installation.")
            if message.get("id") != expected: continue
            if "error" in message:
                raise ValueError("Codex refused this title operation. Use Rename in Codex or update its CLI.")
            return message.get("result", {})
    try:
        send({"id": 1, "method": "initialize", "params": {"clientInfo": {
            "name": "agent_telemetry", "title": "AgentTelemetry", "version": "2.3.1"}}})
        receive(1); send({"method": "initialized", "params": {}})
        send({"id": 2, "method": method, "params": params})
        return receive(2)
    finally:
        process.stdin.close()
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()  # this metadata-only child, never an existing tool/backend
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: pass  # leave it to finish; never force-kill
        process.stdout.close()


def _opencode_rows(connection, session_id):
    rows = []
    if connection.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' "
                          "AND tbl_name IN ('session','session_v2') LIMIT 1").fetchone():
        raise ValueError("Unsupported OpenCode title triggers; original data was not changed.")
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ("session", "session_v2"):
        if table not in tables: continue
        info = {r[1]: r[6] for r in connection.execute("PRAGMA table_xinfo(" + table + ")")}
        if not {"id", "title"}.issubset(info) or info["title"] != 0:
            raise ValueError("Unsupported OpenCode title schema; original data was not changed.")
        row = connection.execute("SELECT title FROM " + table + " WHERE id=?", (session_id,)).fetchone()
        if row is not None: rows.append((table, row[0]))
    if not rows: raise ValueError("The session no longer exists in OpenCode.")
    return rows


def _snapshot(source, path, session_id, fallback):
    if source == "codex":
        thread = _codex_rpc("thread/read", {"threadId": session_id, "includeTurns": False}, home=_codex_home(path)).get("thread", {})
        return {"title": thread.get("name") or fallback}
    if source == "opencode":
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=3)) as c:
            rows = _opencode_rows(c, session_id)
        return {"title": rows[-1][1] or fallback, "rows": rows}
    stat = os.stat(path)
    # Renames append one native title record. Refuse a currently incomplete log.
    title, rank = fallback, 0
    with open(path, "rb") as file:
        if stat.st_size:
            file.seek(-1, os.SEEK_END)
            if file.read(1) != b"\n":
                raise ValueError("The log is still being written. Close the session in Claude and retry.")
        file.seek(0)
        for line in file:
            if b'"customTitle"' not in line and b'"aiTitle"' not in line: continue
            try: record = json.loads(line)
            except ValueError: continue
            if record.get("sessionId") not in (None, session_id): continue
            value = record.get("customTitle") or record.get("aiTitle")
            level = 3 if record.get("customTitle") else 2
            if isinstance(value, str) and value and level >= rank:
                title, rank = value, level
        end = os.fstat(file.fileno())
        if (stat.st_size, stat.st_mtime_ns) != (end.st_size, end.st_mtime_ns):
            raise ValueError("The transcript changed. Close the Claude session and retry.")
    return {"title": title, "signature": [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]}


def prepare(device, source, path, session_id, name, current_title):
    name = validate_name(name)
    ok, reason = availability(source, path)
    if not ok: raise ValueError(reason)
    if source == "claude":
        try: uuid.UUID(session_id)
        except (ValueError, TypeError):
            raise ValueError("This Claude transcript has no verified session UUID.")
        if Path(path).stem != session_id:
            raise ValueError("The Claude session identity does not match its transcript.")
        if claude_running(path, session_id):
            raise ValueError(RUNNING_CLAUDE)
    snapshot = _snapshot(source, path, session_id, current_title)
    ticket = uuid.uuid4().hex
    now = time.monotonic()
    for key in list(_plans):
        if now - _plans[key]["at"] > 300: del _plans[key]
    if len(_plans) >= 128: _plans.pop(next(iter(_plans)))
    _plans[ticket] = {"at": now, "device": device, "source": source, "path": path,
                      "id": session_id, "name": name, "snapshot": snapshot}
    return {"ticket": ticket, "name": name, "current_name": snapshot["title"],
            "target": _codex_home(path) + " · Codex local thread/name/set · " + session_id if source == "codex" else path}


def _backup(plan):
    # Save only the old title metadata, never duplicate token/prompt history.
    directory = N.support_dir() / "session-title-backups"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = directory / (str(time.time_ns()) + "-" + uuid.uuid4().hex + ".json")
    data = {k: plan[k] for k in ("device", "source", "path", "id", "name", "snapshot")}
    with open(path, "x", encoding="utf-8") as file:
        if os.name != "nt": os.chmod(path, 0o600)
        json.dump(data, file); file.flush(); os.fsync(file.fileno())
    return str(path)


def _repair_partial_title(path, original_fd, written, line):
    """Remove/blank only our incomplete append, preserving any concurrent records."""
    if written <= 0: return
    end = os.lseek(original_fd, 0, os.SEEK_CUR)
    start = end - written
    state = os.fstat(original_fd)
    # Never truncate: another writer can append even between a size check and
    # truncation. Replace only our fragment with a whitespace line instead.
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (state.st_dev, state.st_ino):
            raise OSError("Transcript moved during an incomplete title append; source preserved.")
        os.lseek(fd, start, os.SEEK_SET)
        if os.read(fd, written) != line[:written]:
            raise OSError("Title fragment changed; refusing to overwrite other records.")
        os.lseek(fd, start, os.SEEK_SET)
        if os.write(fd, b" " * (written - 1) + b"\n") != written:
            raise OSError("Unable to repair title framing; check free disk space before continuing.")
        os.fsync(fd)
    finally: os.close(fd)


def commit(ticket, device, source, path, session_id, confirmed):
    if confirmed is not True:
        raise ValueError("Confirm the original-tool rename warning before saving.")
    plan = _plans.pop(ticket, None) if isinstance(ticket, str) else None
    if (plan is None or plan.get("scope", "native") != "native" or time.monotonic() - plan["at"] > 300 or
            [plan[k] for k in ("device", "source", "path", "id")] != [device, source, path, session_id]):
        raise ValueError("Rename preview expired or the source changed. Review the warning again.")
    name, old = plan["name"], plan["snapshot"]
    if source == "opencode":
        with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw", uri=True, timeout=3)) as c, c:
            c.execute("BEGIN IMMEDIATE")
            if [list(r) for r in _opencode_rows(c, session_id)] != [list(r) for r in old["rows"]]:
                raise ValueError("The tool's title changed. Review the warning again.")
            backup = _backup(plan)
            for table, previous in old["rows"]:
                c.execute("UPDATE " + table + " SET title=? WHERE id=?", (name, session_id))
    elif source == "claude":
        if claude_running(path, session_id):
            raise ValueError(RUNNING_CLAUDE)
        flags = os.O_RDWR | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        try:
            st = os.fstat(fd)
            if [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns] != old["signature"]:
                raise ValueError("The transcript changed. Close the Claude session and review the warning again.")
            backup = _backup(plan)
            line = (json.dumps({"type": "custom-title", "customTitle": name,
                                "sessionId": session_id}, ensure_ascii=True) + "\n").encode("utf-8")
            # One O_APPEND write: never rewrite/truncate existing usage records.
            written = os.write(fd, line)
            if written != len(line):
                _repair_partial_title(path, fd, written, line)
                raise OSError("Title append was incomplete; previous records preserved. Check disk space and retry.")
            os.fsync(fd)
        finally: os.close(fd)
    else:
        current = _snapshot(source, path, session_id, old["title"])
        if current != old: raise ValueError("The tool's title changed. Review the warning again.")
        backup = _backup(plan)
        _codex_rpc("thread/name/set", {"threadId": session_id, "name": name}, home=_codex_home(path))
    return {"name": name, "backup": backup}
