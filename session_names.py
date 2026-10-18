"""Title-only writes to verified local tool stores. No turns or model requests."""
import json
from contextlib import closing
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid

if __package__:
    from . import native as N
else:
    import native as N

SUPPORTED = {"claude", "codex", "opencode"}
_plans = {}


def identity(aggregate, session):
    # Visible session ids are shortened to eight characters. Never use them as
    # write identities: resolve the full native id from its original metadata.
    if aggregate["source"] == "claude":
        return Path(aggregate.get("path", "")).stem
    if aggregate["source"] == "codex":
        if aggregate.get("_session_id"): return aggregate["_session_id"]
        tail = Path(aggregate.get("path", "")).stem[-36:]
        try: uuid.UUID(tail)
        except ValueError: return ""
        return tail
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
    if source == "codex" and not shutil.which("codex"):
        return False, "Codex's local CLI is required to use its official rename API."
    if source == "codex":
        try: _codex_home(path)
        except ValueError as error: return False, str(error)
    return True, ""


def _codex_home(path):
    for parent in Path(path).resolve().parents:
        if parent.name in ("sessions", "archived_sessions"):
            return str(parent.parent)
    raise ValueError("The original Codex profile could not be resolved safely.")


def _codex_rpc(method, params, home=None):
    if method not in ("thread/read", "thread/name/set"):
        raise ValueError("Only Codex title metadata methods are permitted.")
    executable = shutil.which("codex")
    if not executable:
        raise ValueError("Codex CLI is unavailable.")
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
            "name": "agent_telemetry", "title": "AgentTelemetry", "version": "2.0.0"}}})
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
    if (plan is None or time.monotonic() - plan["at"] > 300 or
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
