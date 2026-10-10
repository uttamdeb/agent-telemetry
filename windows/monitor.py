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
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def snapshot(self):
        with self.lock:
            return dict(self.summary, status=self.status, stale=self.stale,
                        requested=self.requested.is_set(), last_success=self.last_success)

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
                    return
            if self.interval and time.time() >= self.next_legacy_poll:
                self.next_legacy_poll = time.time() + self.interval
                self.refresh(intent=intent)
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
        except urllib.error.URLError:
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
                                   AGENT_TELEMETRY_VERSION="2.0.0", PYTHONDONTWRITEBYTECODE="1")
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

    def open_dashboard(self, intent=None, opener=None):
        intent = self.intent if intent is None else intent
        # First-run parsing can take a minute. Open only once the service is ready,
        # and never open it on behalf of an older, canceled Start request.
        if self.start(intent) and self._active(intent):
            (opener or webbrowser.open)(self.url)

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
        except (OSError, ValueError, KeyError, TypeError):
            if self._active(intent):
                self._state("Dashboard unavailable; showing the last successful refresh. Retrying…", True)

    def stop(self, cancel=True):
        if cancel:
            self.request(False)
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
