"""Local native-app installation/control. No network, no telemetry contents."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

_lock = threading.Lock()


def support_dir():
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "AgentTelemetryNative"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "AgentTelemetryNative"
    return Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "AgentTelemetryNative"


def _read(name):
    try:
        value = json.loads((support_dir() / name).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(name, value):
    root = support_dir()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = root / (name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as file:
            if os.name != "nt":
                os.chmod(temporary, 0o600)
            json.dump(value, file)
        os.replace(str(temporary), str(root / name))
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def status():
    manifest = _read("native-install.json")
    heartbeat = _read("native-status.json")
    control = _read("native-control.json")
    command = manifest.get("command")
    entry = manifest.get("entry")
    installed = (manifest.get("platform") == sys.platform and isinstance(command, list)
                 and bool(command) and all(isinstance(x, str) for x in command)
                 and isinstance(entry, str) and bool(entry) and Path(entry).exists())
    stamp = heartbeat.get("time")
    running = (isinstance(stamp, (int, float)) and 0 <= time.time() - stamp < 15
               and heartbeat.get("generation") == control.get("generation"))
    requested_at = control.get("requested_at")
    failed = (control.get("enabled") is True and not running and isinstance(requested_at, (int, float))
              and time.time() - requested_at > 20)
    return {"supported": sys.platform in ("darwin", "win32"), "installed": bool(installed),
            "running": bool(running), "requested": control.get("enabled") is True,
            "error": "The app has not responded. Try opening it manually or reinstalling." if failed else None,
            "label": "Menu bar app" if sys.platform == "darwin" else "System tray app"}


def action(enabled):
    if not isinstance(enabled, bool):
        raise ValueError("enabled must be a boolean")
    with _lock:
        current = status()
        control = _read("native-control.json")
        if enabled:
            if not current["installed"]:
                raise ValueError("Install the native app first: run python install.py from the repository.")
            if current["running"]:
                if not current["requested"]:
                    raise ValueError("The native app is closing. Wait for it to finish before launching again.")
                return current
            generation = uuid.uuid4().hex
            _write("native-control.json", {"enabled": True, "generation": generation, "requested_at": time.time()})
            command = _read("native-install.json")["command"] + ["--native-generation", generation]
            try:
                subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
            except OSError:
                _write("native-control.json", {"enabled": False, "generation": generation})
                raise ValueError("The native app could not be launched. Reinstall it with python install.py.")
        else:
            control["enabled"] = False
            _write("native-control.json", control)
        return status()


def register(generation=None):
    """A manually opened app is enabled; a canceled launch must not revive it."""
    if generation is None:
        generation = uuid.uuid4().hex
        _write("native-control.json", {"enabled": True, "generation": generation})
    if not heartbeat(generation):
        return None
    return generation


def heartbeat(generation, closing=False):
    control = _read("native-control.json")
    enabled = control.get("enabled") is True
    if control.get("generation") != generation or (not enabled and not closing):
        return False
    _write("native-status.json", {"generation": generation, "pid": os.getpid(), "time": time.time()})
    return enabled


def unregister(generation):
    control = _read("native-control.json")
    if control.get("generation") == generation:
        control["enabled"] = False
        _write("native-control.json", control)
        # A stale heartbeat cannot make a later process look running.
        _write("native-status.json", {})
