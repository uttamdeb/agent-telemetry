"""Local native-app installation/control. No network, no telemetry contents."""
import json
import ntpath
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

_lock = threading.Lock()
REFRESH_INTERVALS = (0, 15, 60, 300, 900)


def _project_path(value):
    if not isinstance(value, str):
        return None
    if not value or len(value) > 4096 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    if not (os.path.isabs(value) or ntpath.isabs(value)):
        return None
    return ntpath.normpath(value).replace("\\", "/").rstrip("/")


def relocated_project_path(value, preferences):
    """Resolve only explicitly recorded directory moves, at component boundaries."""
    current = _project_path(value)
    if current is None:
        return value
    roots = preferences.get("roots", {})
    seen = set()
    changed = False
    for _ in range(len(roots) + 1):
        folded = current.lower() if ntpath.splitdrive(current)[0] else current
        if folded in seen:
            raise ValueError("Project relocation preferences contain a cycle.")
        seen.add(folded)
        for old, new in sorted(roots.items(), key=lambda item: -len(item[0])):
            compare = old.lower() if ntpath.splitdrive(old)[0] else old
            if folded == compare or folded.startswith(compare + "/"):
                current = new + current[len(old):]
                changed = True
                break
        else:
            return current if changed else value
    raise ValueError("Project relocation preferences contain a cycle.")


def validate_project_preferences(value):
    if (not isinstance(value, dict) or value.get("version") != 1
            or set(value) - {"version", "aliases", "roots"}):
        raise ValueError("Project naming preferences have an unsupported format.")
    aliases, roots = value.get("aliases", {}), value.get("roots", {})
    if not isinstance(aliases, dict) or not isinstance(roots, dict) or max(len(aliases), len(roots)) > 100:
        raise ValueError("Project naming preferences exceed the supported limits.")
    for old, new in aliases.items():
        for label in (old, new):
            if (not isinstance(label, str) or not label.strip() or len(label) > 160
                    or any(ord(c) < 32 or ord(c) == 127 or c in "/\\" for c in label)):
                raise ValueError("Project display names must be plain nonempty labels.")
    for old in aliases:
        seen, current = set(), old
        while current in aliases:
            if current in seen:
                raise ValueError("Project display names contain a cycle.")
            seen.add(current)
            current = aliases[current]
    normalized = {}
    for old, new in roots.items():
        source, destination = _project_path(old), _project_path(new)
        if not source or not destination or source in normalized:
            raise ValueError("Project relocations require distinct absolute directory paths.")
        normalized[source] = destination
    result = {"version": 1, "aliases": dict(aliases), "roots": normalized}
    for root in normalized:
        relocated_project_path(root, result)
    return result


def project_preferences():
    """Private presentation preferences; never part of the usage ledger or exports."""
    path = support_dir() / "project-names.json"
    try:
        if path.is_symlink():
            raise ValueError("Project naming preferences cannot be read safely.")
        with path.open("rb") as stream:
            raw = stream.read(65537)
    except FileNotFoundError:
        return {"version": 1, "aliases": {}, "roots": {}}
    except OSError:
        raise ValueError("Project naming preferences could not be read; recorded names are shown.") from None
    try:
        if len(raw) > 65536:
            raise ValueError
        return validate_project_preferences(json.loads(raw))
    except (ValueError, TypeError, UnicodeError):
        raise ValueError("Project naming preferences are invalid; recorded names are shown. The file was preserved.") from None


def set_project_preferences(value):
    value = validate_project_preferences(value)
    if len(json.dumps(value).encode("utf-8")) > 65536:
        raise ValueError("Project naming preferences exceed the supported size limit.")
    with _lock:
        # Refuse to replace a corrupt or unknown existing preference file.
        project_preferences()
        _write("project-names.json", value)


def project_items(items, local_device, preferences):
    """Display aliases apply locally; directory relocations never alter peer paths."""
    aliases = preferences.get("aliases", {})
    def label(value):
        for _ in range(len(aliases)):
            if value not in aliases:
                break
            value = aliases[value]
        return value
    def display(row, remote):
        out = dict(row)
        if isinstance(out.get("project"), str):
            out["project"] = label(out["project"])
        if not remote and "cwd" in out:
            out["cwd"] = relocated_project_path(out["cwd"], preferences)
        return out
    result = []
    for path, aggregate in items:
        remote = str(path).startswith("peer:") or aggregate.get("_device") not in (None, "", local_device)
        mapped = display(aggregate, remote)
        if isinstance(aggregate.get("sessions"), list):
            mapped["sessions"] = [display(s, remote) for s in aggregate["sessions"]]
        result.append((path, mapped))
    return result


def refresh_interval():
    value = _read("refresh-settings.json").get("seconds", 15)
    return value if type(value) is int and value in REFRESH_INTERVALS else 15


def set_refresh_interval(seconds):
    if type(seconds) is not int or seconds not in REFRESH_INTERVALS:
        raise ValueError("seconds must be one of 0, 15, 60, 300, 900")
    with _lock:
        _write("refresh-settings.json", {"seconds": seconds})



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
