"""Testable monitoring core shared by the stdlib-only Windows tray client."""
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
import urllib.error
import urllib.request
import webbrowser


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The tray talks only to its local dashboard; do not follow its redirects."""
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def login_enabled(python, entry):
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            command, _ = winreg.QueryValueEx(key, "AgentTelemetry")
            return command == subprocess.list2cmdline([str(python), str(entry)])
    except OSError:
        return False


def toggle_login(python, entry):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
        if login_enabled(python, entry):
            winreg.DeleteValue(key, "AgentTelemetry")
        else:
            winreg.SetValueEx(key, "AgentTelemetry", 0, winreg.REG_SZ,
                             subprocess.list2cmdline([str(python), str(entry)]))


class Monitor:
    def __init__(self, root, data_dir, python, interval=15, url="http://127.0.0.1:7878"):
        self.root, self.data_dir = Path(root), Path(data_dir)
        self.python, self.interval, self.url = str(python), interval, url
        self.process = None
        self.token = None
        self.requested = threading.Event()
        self.lock = threading.Lock()
        self.intent = 0
        self.summary = {"date": "", "tokens": 0, "spend": 0}
        self.status = "Monitoring off"
        self.stale = False
        self.legacy = False
        self.last_success = None
        self.sync_revision = None
        self.sync_supported = True
        self.next_legacy_poll = 0
        # Detail panels are independent of the usage summary: a missing or
        # failed detail endpoint must never blank or stale the figures card.
        self.activity = {"observed_at": "", "window_seconds": 900,
                         "sessions": [], "total": 0}
        self.activity_supported = None
        self.activity_stale = False
        self.activity_error = None
        self.activity_last_success = None
        self.servers = {"supported": False, "servers": []}
        self.servers_supported = None
        self.servers_stale = False
        self.servers_error = None
        self.servers_last_success = None
        self.details_retry = False
        self.details_retry_at = 0
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def snapshot(self):
        with self.lock:
            return dict(self.summary, status=self.status, stale=self.stale,
                        requested=self.requested.is_set(), last_success=self.last_success,
                        activity={"observed_at": self.activity["observed_at"],
                                  "window_seconds": self.activity["window_seconds"],
                                  "sessions": [dict(row) for row in self.activity["sessions"]],
                                  "total": self.activity["total"]},
                        activity_supported=self.activity_supported,
                        activity_stale=self.activity_stale or self.stale or not self.requested.is_set(),
                        activity_error=self.activity_error,
                        activity_last_success=self.activity_last_success,
                        servers={"supported": self.servers["supported"],
                                 "servers": [dict(row) for row in self.servers["servers"]]},
                        servers_supported=self.servers_supported,
                        servers_stale=self.servers_stale or self.stale or not self.requested.is_set(),
                        servers_error=self.servers_error,
                        servers_last_success=self.servers_last_success)

    def _state(self, status, stale=None):
        with self.lock:
            self.status = status
            if stale is not None:
                self.stale = stale

    def request(self, enabled):
        """Record the latest UI intent before a queued/network operation completes."""
        with self.lock:
            self.intent += 1
            if enabled:
                self.requested.set()
            else:
                self.requested.clear()
            return self.intent

    def is_current(self, intent):
        with self.lock:
            return self.intent == intent

    def _active(self, intent):
        return self.requested.is_set() and self.is_current(intent)

    def apply_command(self, operation, intent):
        if not self.is_current(intent):
            return False
        if operation == "start":
            self.start(intent)
        elif operation in ("stop", "quit"):
            return self.stop(cancel=False)
        return False

    def set_interval(self, seconds, intent=None):
        intent = self.intent if intent is None else intent
        if not self.is_current(intent):
            return
        if type(seconds) is not int or seconds not in (0, 15, 60, 300, 900):
            raise ValueError("Invalid refresh interval")
        try:
            state = self._request("/api/sync", {"seconds": seconds})
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            if self.is_current(intent):
                self.interval = seconds
                self.sync_supported = False
                self.next_legacy_poll = 0
            return
        if not self.is_current(intent):
            return
        self._accept_sync(state)
        self.refresh(intent=intent)

    def _accept_sync(self, state):
        seconds, revision = state.get("seconds"), state.get("revision")
        if type(seconds) is not int or seconds not in (0, 15, 60, 300, 900) or not isinstance(revision, str):
            raise ValueError("Invalid refresh settings")
        changed = revision != self.sync_revision
        self.interval, self.sync_revision = seconds, revision
        return changed

    def sync(self, intent=None):
        """Poll the tiny shared clock; fetch figures only when its tick changes."""
        intent = self.intent if intent is None else intent
        if not self._active(intent):
            return
        try:
            if self.sync_supported and not self.legacy:
                try:
                    state = self._request("/api/sync")
                except urllib.error.HTTPError as error:
                    if error.code != 404:
                        raise
                    self.sync_supported = False
                else:
                    if self._active(intent):
                        changed = self._accept_sync(state)
                        if changed or self.stale:
                            self.refresh(intent=intent)
                        elif self.details_retry and time.monotonic() >= self.details_retry_at:
                            self._refresh_details(intent)
                    return
            if self.interval and time.time() >= self.next_legacy_poll:
                self.next_legacy_poll = time.time() + self.interval
                self.refresh(intent=intent)
            elif self.details_retry and time.monotonic() >= self.details_retry_at:
                self._refresh_details(intent)
        except (OSError, ValueError, KeyError, TypeError):
            if self._active(intent):
                self._state("Dashboard unavailable; showing the last successful refresh. Retrying…", True)

    def _request(self, path, body=None, token=None, timeout=10):
        headers = {"Origin": self.url}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-AgentTelemetry-Control"] = token
        request = urllib.request.Request(self.url + path, raw, headers)
        try:
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            raise

    def _probe(self):
        try:
            health = self._request("/api/health", timeout=2)
            if health.get("service") != "agent-telemetry":
                raise ValueError("Port 7878 is used by another application.")
            return health
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise ValueError("The dashboard is unavailable on port 7878.")
            # Old releases do not have /health or /summary.
            with self.opener.open(self.url + "/", timeout=2) as response:
                html = response.read().decode("utf-8", errors="replace")
            if "<title>AgentTelemetry</title>" not in html:
                raise ValueError("Port 7878 is used by another application.")
            data = self._request("/api/data")
            if not isinstance(data.get("records"), list):
                raise ValueError("The existing dashboard could not be read.")
            return {"service": "agent-telemetry", "ready": True, "legacy": True}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # Not answering yet. The backend binds its port before its first parse and answers only after it, so
            # a health request can be accepted and then time out (a bare TimeoutError, not a URLError). Treating
            # that as a failure made start() give up on a backend that was starting normally.
            return None

    def start(self, intent=None):
        intent = self.intent if intent is None else intent
        if not self._active(intent):
            return
        self._state("Starting…", False)
        try:
            health = self._probe()
            if not self._active(intent):
                return
            if health is None and self.process is None:
                self.data_dir.mkdir(parents=True, exist_ok=True)
                self.token = secrets.token_urlsafe(32)
                environment = dict(os.environ, AGENT_TELEMETRY_CONTROL_TOKEN=self.token,
                                   AGENT_TELEMETRY_VERSION="2.2.0", PYTHONDONTWRITEBYTECODE="1")
                # Child parser diagnostics remain available locally, never in a release.
                with open(self.data_dir / "server.log", "ab") as log:
                    self.process = subprocess.Popen([self.python, str(self.root / "dashboard.py"),
                        "--host", "127.0.0.1", "--port", "7878",
                        "--data-dir", str(self.data_dir)], cwd=str(self.root), env=environment,
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for _ in range(600):
                if not self._active(intent):
                    return
                if self.process is not None and self.process.poll() is not None:
                    self.process = None
                    raise ValueError("The local backend exited. See its local server.log and retry.")
                health = self._probe()
                if not self._active(intent):
                    return
                if health and health.get("ready"):
                    if self.process is not None and health.get("pid") != self.process.pid:
                        raise ValueError("Another dashboard took port 7878. Stop monitoring and retry.")
                    self.legacy = bool(health.get("legacy"))
                    self.sync_supported = True
                    self.sync_revision = None
                    self.refresh(intent=intent)
                    return True
                time.sleep(0.5)
            raise ValueError("The dashboard did not finish starting. Stop monitoring and retry.")
        except (OSError, ValueError) as error:
            if self._active(intent):
                self._state(str(error), True)

    def open_dashboard(self, intent=None, opener=None, view=None):
        intent = self.intent if intent is None else intent
        # First-run parsing can take a minute. Open only once the service is ready,
        # and never open it on behalf of an older, canceled Start request.
        if self.start(intent) and self._active(intent):
            destinations = {"local-servers": "#local-servers",
                            "sessions": "?range=7d#sessions"}
            (opener or webbrowser.open)(self.url + destinations.get(view, ""))

    def refresh(self, parse=False, intent=None):
        intent = self.intent if intent is None else intent
        if not self._active(intent):
            return
        try:
            if parse:
                self._request("/api/refresh", {}, timeout=60)
            payload = self._request("/api/data" if self.legacy else "/api/summary")
            if self.legacy:
                today = datetime.date.today().isoformat()
                rows = [r for r in payload["records"] if r.get("date") == today]
                payload = {"date": today, "tokens": sum(sum(r.get(k, 0) for k in
                    ("in", "out", "cr", "cc")) for r in rows),
                    "spend": sum(r.get("cost", 0) for r in rows)}
            if not self._active(intent):
                return
            summary = {"date": str(payload["date"]), "tokens": int(payload["tokens"]),
                       "spend": float(payload["spend"])}
            with self.lock:
                self.summary = summary
                self.stale = False
                self.last_success = time.time()
                self.status = "Monitoring on" if self.process is not None else "Using existing dashboard"
            self._refresh_details(intent)
        except (OSError, ValueError, KeyError, TypeError):
            if self._active(intent):
                self._state("Dashboard unavailable; showing the last successful refresh. Retrying…", True)

    @staticmethod
    def _activity_payload(payload):
        if not isinstance(payload, dict):
            raise ValueError("Invalid recent activity response")
        observed_at = payload.get("observed_at")
        window = payload.get("window_seconds")
        total = payload.get("total")
        sessions = payload.get("sessions")
        if (not isinstance(observed_at, str) or len(observed_at) > 64
                or type(window) is not int or not 1 <= window <= 3600
                or type(total) is not int or total < 0
                or not isinstance(sessions, list)):
            raise ValueError("Invalid recent activity response")
        result = []
        for row in sessions[:3]:
            if not isinstance(row, dict):
                continue
            key, source, tool, title, activity_at = (row.get(name) for name in
                ("key", "source", "tool", "title", "last_activity_at"))
            tokens, provider, subagent = row.get("tokens"), row.get("provider"), row.get("subagent")
            if (not all(isinstance(value, str) and value and len(value) <= limit for value, limit in
                    ((key, 128), (source, 64), (tool, 80), (title, 120), (activity_at, 64)))
                    or (tokens is not None and (type(tokens) is not int or tokens < 0))
                    or (provider is not None and (not isinstance(provider, str) or len(provider) > 80))
                    or type(subagent) is not bool):
                continue
            result.append({"key": key, "source": source, "tool": tool, "provider": provider,
                           "title": title, "tokens": tokens,
                           "last_activity_at": activity_at, "subagent": subagent})
        return {"observed_at": observed_at, "window_seconds": window,
                "sessions": result, "total": total}

    @staticmethod
    def _servers_payload(payload):
        if not isinstance(payload, dict) or type(payload.get("supported")) is not bool:
            raise ValueError("Invalid local server response")
        servers = payload.get("servers")
        if not isinstance(servers, list):
            raise ValueError("Invalid local server response")
        clean = []
        for row in servers:
            if not isinstance(row, dict):
                continue
            state, host, port = row.get("state"), row.get("host"), row.get("port")
            if (state not in ("running", "stopped") or not isinstance(host, str)
                    or len(host) > 255 or type(port) is not int or not 1 <= port <= 65535):
                continue
            title = row.get("project") or row.get("process") or "Local server"
            if not isinstance(title, str):
                title = "Local server"
            title = " ".join(title.split())[:100] or "Local server"
            clean.append({"state": state, "title": title, "host": host,
                          "port": port, "isPinned": row.get("isPinned") is True})
        message = payload.get("message")
        if not isinstance(message, str):
            message = ""
        return {"supported": payload["supported"], "servers": clean[:100],
                "message": " ".join(message.split())[:180]}

    def _refresh_details(self, intent):
        """Fetch local details on the summary worker; details never gate figures."""
        if not self._active(intent):
            return
        retry = False
        for kind, path, validate in (("activity", "/api/native-activity", self._activity_payload),
                                     ("servers", "/api/local-servers", self._servers_payload)):
            if not self._active(intent):
                return
            try:
                payload = validate(self._request(path, timeout=8))
            except urllib.error.HTTPError as error:
                missing = error.code == 404
                error.close()
                message = "Update dashboard to see this detail." if missing else "Could not refresh; retrying…"
                retry = retry or not missing
                self._set_detail_result(kind, error=message, supported=False if missing else None,
                                        retry=not missing, intent=intent)
                continue
            except (OSError, ValueError, KeyError, TypeError):
                retry = True
                self._set_detail_result(kind, error="Could not refresh; retrying…", retry=True,
                                        intent=intent)
                continue
            if not self._active(intent):
                return
            if kind == "activity":
                self._set_detail_result(kind, payload=payload, supported=True, intent=intent)
            else:
                self._set_detail_result(kind, payload=payload,
                                        supported=payload["supported"], intent=intent)
        with self.lock:
            if self._active_locked(intent):
                self.details_retry = retry
                self.details_retry_at = time.monotonic() + (3 if retry else 0)

    def _active_locked(self, intent):
        return self.requested.is_set() and self.intent == intent

    def _set_detail_result(self, kind, payload=None, error=None, supported=None,
                           retry=False, intent=None):
        with self.lock:
            if intent is not None and not self._active_locked(intent):
                return
            now = time.time()
            if kind == "activity":
                if payload is not None:
                    self.activity = payload
                    self.activity_supported = True
                    self.activity_last_success = now
                    self.activity_error = None
                    self.activity_stale = False
                else:
                    if supported is not None:
                        self.activity_supported = supported
                    self.activity_error = error
                    self.activity_stale = True
            else:
                if payload is not None:
                    self.servers = payload
                    self.servers_supported = supported
                    self.servers_last_success = now
                    self.servers_error = payload.get("message") or None
                    self.servers_stale = False
                else:
                    if supported is not None:
                        self.servers_supported = supported
                    self.servers_error = error
                    self.servers_stale = True
            if retry:
                self.details_retry = True

    def _mark_details_stale(self):
        with self.lock:
            self.activity_stale = True
            self.servers_stale = True
            self.details_retry = False

    def stop(self, cancel=True):
        if cancel:
            self.request(False)
        self._mark_details_stale()
        process = self.process
        if process is not None:
            self._state("Stopping; saving usage history…")
            # No Windows SIGKILL fallback: wait for the ledger to flush.
            for _ in range(60):
                if process.poll() is not None:
                    break
                try:
                    self._request("/api/shutdown", {}, self.token, timeout=2)
                    process.wait(timeout=2)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    time.sleep(0.5)
            if process.poll() is None:
                self._state("The owned backend is still saving. Try Stop or Quit again.", True)
                return False
            self.process = None
            self.token = None
        self._state("Monitoring off", False)
        return True


def import_ledger(source, destination):
    """Copy only into empty app data, with rollback; source and archives stay intact."""
    import shutil
    import tempfile
    source, destination = Path(source), Path(destination)
    cache = source / ".usage_cache.json"
    if source.is_symlink() or not source.is_dir() or cache.is_symlink() or not cache.is_file():
        raise ValueError("Choose an existing AgentTelemetry folder containing .usage_cache.json.")
    data = json.loads(cache.read_text(encoding="utf-8"))
    if (not isinstance(data, dict) or not isinstance(data.get("files"), dict)
            or not all(isinstance(v, dict) for v in data["files"].values())):
        raise ValueError("The usage ledger is invalid; nothing was imported.")
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("The app already has data. Import never overwrites it.")
    for name in (".peers.json", ".peers"):
        path = source / name
        if path.is_symlink():
            raise ValueError("Import does not follow symlinks.")
        if path.exists():
            if name == ".peers.json" and (not path.is_file() or not isinstance(
                    json.loads(path.read_text(encoding="utf-8")), dict)):
                raise ValueError("The sharing state is invalid.")
            if name == ".peers":
                if not path.is_dir() or any(p.is_symlink() or not (p.is_file() or p.is_dir())
                                          for p in path.rglob("*")):
                    raise ValueError("The device mirror folder is invalid.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".telemetry-import-", dir=str(destination.parent)))
    try:
        shutil.copy2(str(cache), str(stage / cache.name))
        if os.name != "nt":
            os.chmod(stage / cache.name, 0o600)
        for name in (".peers.json", ".peers"):
            path = source / name
            if path.is_file():
                shutil.copy2(str(path), str(stage / name))
            elif path.is_dir():
                shutil.copytree(str(path), str(stage / name))
        if destination.exists():
            destination.rmdir()  # also refuses a destination that became nonempty
        stage.rename(destination)
    finally:
        if stage.exists():
            shutil.rmtree(str(stage))
