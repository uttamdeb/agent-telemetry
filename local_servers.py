"""Local TCP listener inventory and short-lived history for the dashboard."""

from __future__ import annotations

import contextlib
import errno
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone

try:
    import fcntl
except ImportError:
    fcntl = None


_LSOF = shutil.which("lsof") or "/usr/sbin/lsof"
_PS = shutil.which("ps") or "/bin/ps"
_HISTORY_DAYS = 30
_MAX_SERVERS = 100
_MAX_PROFILES = 100
_MAX_INVENTORY_BYTES = 2 * 1024 * 1024
_MAX_COMMAND_OUTPUT_BYTES = 2 * 1024 * 1024
_COMMAND_TIMEOUT = 1
_LOCK_TIMEOUT = 1
_START_TIMEOUT = 10
_STOP_TIMEOUT = 8
_PORT_RE = re.compile(r"^(.*):(\d+)$")
_ID_RE = re.compile(r"^[0-9a-f]{20}$")
_CONTROL_TOKEN = "AGENT_TELEMETRY_CONTROL_TOKEN"
_INVENTORY_THREAD_LOCKS = {}
_INVENTORY_THREAD_LOCKS_GUARD = threading.Lock()


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def _parse_stamp(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _server_id(pid, started_at, port):
    identity = f"{int(pid)}:{str(started_at).strip()}:{int(port)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _profile_id(cwd, port):
    if not isinstance(cwd, str) or not cwd.strip():
        raise ValueError("Choose a server folder before saving a profile.")
    directory = os.path.realpath(os.path.expanduser(str(cwd).strip()))
    directory = os.path.normcase(directory)
    identity = f"{directory}:{int(port)}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]


def _uses_running_port(server, port):
    try:
        return server.get("state") == "running" and int(server.get("port")) == port
    except (AttributeError, TypeError, ValueError):
        return False


def _is_local_server(item):
    """Keep project/service listeners; omit OS processes running from /."""
    cwd = item.get("cwd")
    if platform.system() == "Windows":
        # Win32 exposes ownership and creation time, but no supported foreign
        # process working-directory API. Configuration asks the user for it.
        return isinstance(cwd, str)
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    cwd = os.path.normpath(os.path.expanduser(cwd.strip()))
    if cwd == os.path.sep or cwd.startswith(("/System/", "/Library/")):
        return False
    return True


@contextlib.contextmanager
def _thread_lock(path):
    key = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    with _INVENTORY_THREAD_LOCKS_GUARD:
        entry = _INVENTORY_THREAD_LOCKS.get(key)
        if entry is None:
            entry = {"lock": threading.Lock(), "users": 0}
            _INVENTORY_THREAD_LOCKS[key] = entry
        entry["users"] += 1
    lock = entry["lock"]
    acquired = False
    try:
        if not lock.acquire(timeout=_LOCK_TIMEOUT):
            raise ValueError("Another local server operation is still running. Wait and refresh.")
        acquired = True
        yield
    finally:
        if acquired:
            lock.release()
        with _INVENTORY_THREAD_LOCKS_GUARD:
            entry["users"] -= 1
            if entry["users"] == 0 and _INVENTORY_THREAD_LOCKS.get(key) is entry:
                del _INVENTORY_THREAD_LOCKS[key]


@contextlib.contextmanager
def _inventory_lock(path):
    """Serialize local threads as well as cooperating processes."""
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, mode=0o700, exist_ok=True)
    with _thread_lock(path):
        with open(path, "a+b") as lock_file:
            try:
                os.fchmod(lock_file.fileno(), 0o600)
            except (AttributeError, OSError):
                pass
            if fcntl is not None:
                deadline = time.monotonic() + _LOCK_TIMEOUT
                while True:
                    try:
                        fcntl.lockf(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise ValueError("Another local server operation is still running. Wait and refresh.")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    fcntl.lockf(lock_file.fileno(), fcntl.LOCK_UN)
            elif os.name == "nt":
                import msvcrt
                lock_file.seek(0, os.SEEK_END)
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                lock_file.seek(0)
                deadline = time.monotonic() + _LOCK_TIMEOUT
                while True:
                    try:
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise ValueError("Another local server operation is still running. Wait and refresh.")
                        time.sleep(0.05)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - feature is gated off on other platforms
                yield


@contextlib.contextmanager
def _profile_start_lock(path):
    # Separate lock files keep starts/restarts for the same profile serialized
    # across both HTTP threads and processes without blocking other profiles.
    with _inventory_lock(path):
        yield


def _command_env():
    env = {key: value for key, value in os.environ.items() if key.upper() != _CONTROL_TOKEN}
    env["LC_ALL"] = "C"
    return env


def _run(arguments):
    try:
        result = subprocess.run(
            arguments, check=False, capture_output=True, text=True,
            timeout=_COMMAND_TIMEOUT, env=_command_env(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if (len(result.stdout.encode("utf-8", "replace")) > _MAX_COMMAND_OUTPUT_BYTES
            or len(result.stderr.encode("utf-8", "replace")) > _MAX_COMMAND_OUTPUT_BYTES):
        return None
    return result


def _lsof_result(arguments, description):
    result = _run(arguments)
    if result is None:
        raise RuntimeError(f"Could not {description}; the scan timed out or the command failed.")
    # lsof returns 1 with no output when there are simply no matches. Other
    # nonzero results may be partial, so refusing to store them avoids falsely
    # marking previously running entries as stopped.
    if result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip():
        return result
    if result.returncode != 0:
        detail = result.stderr.strip()
        raise RuntimeError(f"Could not {description}" + (f": {detail}" if detail else "."))
    return result


def _process_details(pids):
    if platform.system() == "Windows":
        return _windows().process_details(pids)
    if not pids:
        return {}
    result = _run([
        _PS, "-p", ",".join(str(pid) for pid in sorted(pids)), "-o", "pid=,lstart=,uid=,pgid=",
    ])
    if result is None or result.returncode != 0:
        return {}
    details = {}
    for line in result.stdout.splitlines():
        fields = line.strip().split(None, 7)
        if len(fields) == 8:
            try:
                details[int(fields[0])] = {
                    "startedAt": " ".join(fields[1:6]),
                    "uid": fields[6],
                    "processGroup": int(fields[7]),
                }
            except ValueError:
                continue
    return details


def _process_start_times(pids):
    return {pid: row["startedAt"] for pid, row in _process_details(pids).items()}


def _process_uids(pids):
    return {pid: row["uid"] for pid, row in _process_details(pids).items()}


def _process_groups(pids):
    return {pid: row["processGroup"] for pid, row in _process_details(pids).items()}


def _working_directories(uid, pids=None):
    if pids:
        arguments = [_LSOF, "-a", "-p", ",".join(str(pid) for pid in sorted(pids)), "-d", "cwd", "-Fpn"]
    else:
        arguments = [_LSOF, "-a", "-u", str(uid), "-d", "cwd", "-Fpn"]
    result = _lsof_result(
        arguments,
        "inspect local process working directories",
    )
    pid = None
    paths = {}
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
                if pid <= 0:
                    raise ValueError
            except ValueError:
                raise RuntimeError("The local working-directory scan returned an invalid process id.")
        elif line.startswith("n"):
            path = line[1:]
            if pid is None or not path:
                raise RuntimeError("The local working-directory scan returned invalid process data.")
            paths[pid] = path
    return paths


def _endpoint(line):
    match = _PORT_RE.fullmatch(line)
    if not match:
        raise RuntimeError("The local listener scan returned an invalid TCP endpoint.")
    host, raw_port = match.groups()
    try:
        port = int(raw_port)
    except ValueError:
        raise RuntimeError("The local listener scan returned an invalid TCP port.")
    host = host.strip("[]")
    if not host or not 1 <= port <= 65535 or any(char.isspace() for char in host):
        raise RuntimeError("The local listener scan returned an invalid TCP endpoint.")
    return host, port


def _listener_rows(uid, known_servers=()):
    """Return complete listener records for the current user."""
    if platform.system() == "Windows":
        return _windows().listener_rows(uid, known_servers, _protected_pids())
    result = _lsof_result([
        _LSOF, "-a", "-u", str(uid), "-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn",
    ], "inspect local listening ports")

    processes = {}
    pid = None
    for line in result.stdout.splitlines():
        if line.startswith("p"):
            try:
                pid = int(line[1:])
                if pid <= 0:
                    raise ValueError
            except ValueError:
                raise RuntimeError("The local listener scan returned an invalid process id.")
            processes.setdefault(pid, {"process": "", "endpoints": set()})
        elif pid is not None and line.startswith("c"):
            processes[pid]["process"] = line[1:]
        elif line.startswith("n"):
            if pid is None:
                raise RuntimeError("The local listener scan returned an endpoint without a process id.")
            processes[pid]["endpoints"].add(_endpoint(line[1:]))

    processes.pop(os.getpid(), None)
    if not processes:
        return []

    pids = set(processes)
    details = _process_details(pids)
    missing_identity = pids - details.keys()
    if missing_identity:
        raise RuntimeError("Could not verify the identity of one or more local listener processes.")

    current_uid = str(uid)
    for process_id in pids:
        if str(details[process_id]["uid"]) != current_uid:
            raise RuntimeError("The local listener scan returned a process owned by another user.")

    cwd_by_pid = _working_directories(uid, pids)
    missing_cwd = pids - cwd_by_pid.keys()
    if missing_cwd:
        raise RuntimeError("Could not verify the working directory of one or more local listeners.")

    rows = {}
    for process_id, data in processes.items():
        started_at = details[process_id]["startedAt"]
        cwd = cwd_by_pid[process_id]
        if not isinstance(started_at, str) or not started_at.strip() or not isinstance(cwd, str):
            raise RuntimeError("The local listener scan returned invalid process data.")
        for host, port in data["endpoints"]:
            key = (process_id, started_at, port)
            previous = rows.get(key)
            if previous is None or host in ("*", "0.0.0.0", "::"):
                rows[key] = {
                    "pid": process_id,
                    "process": data["process"] or "Unknown process",
                    "cwd": cwd,
                    "host": host,
                    "port": port,
                    "startedAt": started_at.strip(),
                    "uid": current_uid,
                    "processGroup": details[process_id]["processGroup"],
                }
    return list(rows.values())


def _listening_ports():
    """Read occupied ports for every user so filtered rows cannot hide conflicts."""
    if platform.system() == "Windows":
        return _windows().listening_ports()
    result = _lsof_result(
        [_LSOF, "-nP", "-iTCP", "-sTCP:LISTEN", "-Fn"],
        "check local port availability",
    )
    ports = set()
    for line in result.stdout.splitlines():
        if line.startswith("n"):
            _, port = _endpoint(line[1:])
            ports.add(port)
    return ports


def _probe_port_available(port):
    """Check local bind availability without sending application traffic."""
    probes = (
        (socket.AF_INET, ("0.0.0.0", port)),
        (socket.AF_INET6, ("::", port, 0, 0)),
    )
    for family, address in probes:
        probe = None
        try:
            probe = socket.socket(family, socket.SOCK_STREAM)
            if platform.system() == "Windows":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if family == socket.AF_INET6:
                probe.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
            probe.bind(address)
        except OSError as error:
            if family == socket.AF_INET6 and error.errno in (errno.EAFNOSUPPORT, errno.EPROTONOSUPPORT):
                continue
            if error.errno in (errno.EADDRINUSE, errno.EACCES, errno.EADDRNOTAVAIL):
                return False
            raise RuntimeError(f"Could not safely check whether port {port} is available: {error}.")
        finally:
            if probe is not None:
                probe.close()
    return True


def _capability():
    system = platform.system()
    if system == "Windows":
        return True, system, ""
    if system != "Darwin":
        return False, system, "Local server controls are supported on macOS and Windows."
    missing = [name for name, path in (("lsof", _LSOF), ("ps", _PS))
               if not os.path.isfile(path) or not os.access(path, os.X_OK)]
    if missing:
        return True, system, "Local server scanning requires " + " and ".join(missing) + " on macOS."
    return True, system, ""


def _current_uid():
    if platform.system() == "Windows":
        return _windows().current_uid()
    return str(os.getuid()) if hasattr(os, "getuid") else getpass.getuser()


def _windows():
    import local_servers_windows
    return local_servers_windows


def _protected_pids():
    pids = {os.getpid()}
    for key in ("AGENT_TELEMETRY_BACKEND_PID", "AGENT_TELEMETRY_SERVER_PID"):
        try:
            value = int(os.environ.get(key, ""))
        except ValueError:
            continue
        if value > 0:
            pids.add(value)
    return pids


def _reap_later(process):
    """Collect a launcher that may still finish after the bounded startup wait."""
    if process.poll() is not None:
        process.wait()
        return
    threading.Thread(target=process.wait, name="local-server-launch-reaper", daemon=True).start()


class LocalServerInventory:
    """Persist recent listeners and user-configured server profiles."""

    def __init__(self, data_dir):
        self.path = os.path.join(data_dir, ".local_servers.json")
        self.lock_path = self.path + ".lock"
        self._last_launch_server_id = None

    def _require_supported(self):
        supported, system, message = _capability()
        if not supported:
            raise ValueError(message)
        if message:
            raise RuntimeError(message)
        return system

    def snapshot(self):
        supported, system, message = _capability()
        if not supported:
            return {
                "supported": False,
                "platform": system,
                "message": message,
                "updatedAt": "",
                "servers": [],
            }
        if message:
            raise RuntimeError(message)
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _inventory_lock(self.lock_path):
            payload = self._scan_and_store()
        return {
            "supported": True,
            "platform": system,
            "updatedAt": payload["updatedAt"],
            "servers": [self._public_server(item) for item in payload["servers"]],
        }

    def stop(self, server_id):
        self._require_supported()
        if not isinstance(server_id, str) or not _ID_RE.fullmatch(server_id):
            raise ValueError("Choose a server to stop.")
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _inventory_lock(self.lock_path):
            payload = self._scan_and_store()
            server = next((item for item in payload["servers"] if item["id"] == server_id), None)
            if server is None or server["state"] != "running":
                raise ValueError("That server is no longer running. Refresh the list and try again.")
            self._stop_server(server)
        return {"ok": True, "server": self._public_server(server)}

    def _stop_server(self, server):
        try:
            pid = server.get("pid")
            if type(pid) is not int or pid <= 1:
                raise ValueError
        except (AttributeError, ValueError):
            raise ValueError("That server no longer has a valid process id. Refresh and try again.")
        if pid in _protected_pids():
            raise ValueError("AgentTelemetry will not stop its own dashboard process.")
        expected_start = server.get("startedAt")
        if not isinstance(expected_start, str) or not expected_start.strip():
            raise ValueError("The process changed since this list was loaded. Refresh before stopping it.")
        if platform.system() == "Windows":
            _windows().stop(server)
            return
        current = _process_details({pid}).get(pid, {})
        current_start = current.get("startedAt")
        if not current_start or current_start != expected_start or str(current.get("uid")) != _current_uid():
            raise ValueError("The process changed since this list was loaded. Refresh before stopping it.")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            raise ValueError("That server has already stopped.")
        except PermissionError:
            raise ValueError("macOS denied permission to stop that process.")

    def configure(self, server_id, command, cwd=None):
        system = self._require_supported()
        command = command.strip() if isinstance(command, str) else ""
        if not command or len(command) > 4096 or "\0" in command:
            raise ValueError("Enter a launch command of 1–4096 characters.")
        if not isinstance(server_id, str) or not _ID_RE.fullmatch(server_id):
            raise ValueError("Choose a server from the recent list first.")
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _inventory_lock(self.lock_path):
            payload = self._scan_and_store()
            server = next((item for item in payload["servers"] if item["id"] == server_id), None)
            if server is None:
                raise ValueError("That server is no longer in the recent list. Refresh and try again.")
            if system == "Windows":
                cwd = cwd.strip() if isinstance(cwd, str) else server.get("cwd", "")
                if not cwd or len(cwd) > 4096 or "\0" in cwd or not os.path.isabs(os.path.expanduser(cwd)):
                    raise ValueError("Enter the full path to the server's working folder.")
                cwd = os.path.realpath(os.path.expanduser(cwd))
            else:
                cwd = server.get("cwd")
            if not isinstance(cwd, str) or not os.path.isdir(cwd):
                raise ValueError("The server folder is no longer available. Configure it again from a running server.")
            try:
                port = int(server.get("port"))
            except (TypeError, ValueError):
                raise ValueError("The server port is invalid. Configure it again from a running server.")
            if not 1 <= port <= 65535:
                raise ValueError("The server port is invalid. Configure it again from a running server.")
            profile_id = _profile_id(cwd, port)
            stored = self._read_payload()
            profiles = {item["id"]: item for item in stored["profiles"]}
            if profile_id not in profiles and len(profiles) >= _MAX_PROFILES:
                raise ValueError("The local server profile limit has been reached.")
            profile = profiles.get(profile_id, {})
            profiles[profile_id] = {
                "id": profile_id,
                "cwd": os.path.realpath(os.path.expanduser(cwd)),
                "port": port,
                "command": command,
                "isPinned": bool(profile.get("isPinned")),
            }
            updated = {**stored, "profiles": list(profiles.values())}
            if system == "Windows":
                # Bind the chosen folder only to this exact PID + creation time
                # + port, never to an unrelated replacement on the same port.
                for item in updated["servers"]:
                    if item["id"] == server_id:
                        item["cwd"] = cwd
                        item["project"] = os.path.basename(cwd.rstrip(os.sep))
            self._apply_profile_flags(updated)
            self._write(updated)
        return {"ok": True, "profileId": profile_id}

    def set_pinned(self, server_id, pinned):
        self._require_supported()
        if type(pinned) is not bool:
            raise ValueError("Choose whether to pin or unpin this server.")
        if not isinstance(server_id, str) or not _ID_RE.fullmatch(server_id):
            raise ValueError("Choose a server from the recent list first.")
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _inventory_lock(self.lock_path):
            payload = self._scan_and_store()
            server = next((item for item in payload["servers"] if item["id"] == server_id), None)
            if server is None:
                raise ValueError("That server is no longer in the recent list. Refresh and try again.")
            if not server.get("cwd"):
                raise ValueError("Configure this server's working folder before pinning it.")
            profile_id = _profile_id(server["cwd"], server["port"])
            stored = self._read_payload()
            profiles = {item["id"]: item for item in stored["profiles"]}
            profile = profiles.get(profile_id, {
                "id": profile_id,
                "cwd": os.path.realpath(os.path.expanduser(server["cwd"])),
                "port": int(server["port"]),
                "command": "",
                "isPinned": False,
            })
            if pinned:
                if profile_id not in profiles and len(profiles) >= _MAX_PROFILES:
                    raise ValueError("The local server profile limit has been reached.")
                profile["isPinned"] = True
                profiles[profile_id] = profile
            elif profile.get("command"):
                profile["isPinned"] = False
                profiles[profile_id] = profile
            else:
                profiles.pop(profile_id, None)
            updated = {**stored, "profiles": list(profiles.values())}
            self._apply_profile_flags(updated)
            self._write(updated)
        return {"ok": True, "profileId": profile_id, "isPinned": pinned}

    def start(self, profile_id):
        self._require_supported()
        if not isinstance(profile_id, str) or not _ID_RE.fullmatch(profile_id):
            raise ValueError("Choose a valid configured server to start.")
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _profile_start_lock(f"{self.lock_path}.start.{profile_id}"):
            return self._start_profile(profile_id)

    def _validate_profile(self, profile_id, profiles):
        profile = next((item for item in profiles if item.get("id") == profile_id), None)
        command = profile.get("command") if profile else None
        if not isinstance(command, str) or not command.strip() or "\0" in command or len(command) > 4096:
            raise ValueError("Save a launch command for this server before starting it.")
        cwd = profile.get("cwd")
        if not isinstance(cwd, str) or not os.path.isdir(cwd):
            raise ValueError("The saved server folder no longer exists. Configure the launch command again.")
        try:
            port = int(profile.get("port"))
        except (TypeError, ValueError):
            raise ValueError("The saved server port is invalid. Configure the launch command again.")
        if not 1 <= port <= 65535:
            raise ValueError("The saved server port is invalid. Configure the launch command again.")
        if _profile_id(cwd, port) != profile_id:
            raise ValueError("The saved server profile is invalid. Configure the launch command again.")
        return profile

    def _start_profile(self, profile_id, expected_profile=None):
        with _inventory_lock(self.lock_path):
            snapshot = self._scan_and_store()
            payload = self._read_payload()
            profile = self._validate_profile(profile_id, payload["profiles"])
            if expected_profile is not None and any(
                    profile.get(key) != expected_profile.get(key) for key in ("cwd", "port", "command")):
                raise ValueError("The saved server configuration changed during restart. Refresh and try again.")
            if any(item.get("profileId") == profile_id and item.get("state") == "running"
                   for item in snapshot["servers"]):
                raise ValueError("That server is already running.")
            port = int(profile["port"])
            if port in _listening_ports() or not _probe_port_available(port):
                raise ValueError(f"Port {port} is already used by another process.")
            cwd = profile["cwd"]
            command = profile["command"]
            try:
                if platform.system() == "Windows":
                    process = _windows().launch(command, cwd, _command_env())
                else:
                    process = subprocess.Popen(
                        ["/bin/zsh", "-lc", command],
                        cwd=cwd,
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        start_new_session=True,
                        close_fds=True,
                        env=_command_env(),
                    )
            except OSError as error:
                raise ValueError(f"Could not launch the saved command: {error.strerror or error}.")

        try:
            deadline = time.monotonic() + _START_TIMEOUT
            while time.monotonic() < deadline:
                with _inventory_lock(self.lock_path):
                    snapshot = self._scan_and_store(
                        launch_group=process.pid,
                        expected_profile_id=profile_id,
                        **({"launch_process": process} if platform.system() == "Windows" else {}),
                    )
                    candidate_id = self._last_launch_server_id
                server = next((item for item in snapshot["servers"]
                               if item.get("id") == candidate_id
                               and item.get("profileId") == profile_id
                               and item.get("state") == "running"), None)
                if server:
                    return {"ok": True, "server": self._public_server(server)}
                exit_status = process.poll()
                if exit_status is not None:
                    raise ValueError(f"The launch command exited with status {exit_status} before port {port} opened.")
                time.sleep(0.4)
            raise ValueError(f"The launch command did not open port {port} within {_START_TIMEOUT} seconds.")
        finally:
            _reap_later(process)

    def restart(self, profile_id, server_id=None):
        self._require_supported()
        if not isinstance(profile_id, str) or not _ID_RE.fullmatch(profile_id):
            raise ValueError("Choose a valid configured server to restart.")
        if not isinstance(server_id, str) or not _ID_RE.fullmatch(server_id):
            raise ValueError("Confirm the exact running server again before restarting it.")
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        with _profile_start_lock(f"{self.lock_path}.start.{profile_id}"):
            with _inventory_lock(self.lock_path):
                snapshot = self._scan_and_store()
                payload = self._read_payload()
                # Validate the saved launch configuration before stopping anything.
                profile = self._validate_profile(profile_id, payload["profiles"])
                target = next((item for item in snapshot["servers"]
                               if item.get("id") == server_id
                               and item.get("profileId") == profile_id
                               and item.get("state") == "running"), None)
                if target is None:
                    raise ValueError("That server changed since confirmation. Refresh the list and try again.")
                self._stop_server(target)

            deadline = time.monotonic() + _STOP_TIMEOUT
            while time.monotonic() < deadline:
                with _inventory_lock(self.lock_path):
                    snapshot = self._scan_and_store()
                target_running = any(item.get("id") == server_id and item.get("state") == "running"
                                     for item in snapshot["servers"])
                if not target_running:
                    replacement_running = any(item.get("profileId") == profile_id
                                              and item.get("state") == "running"
                                              for item in snapshot["servers"])
                    if replacement_running:
                        raise ValueError("The confirmed server was replaced while stopping. Refresh before restarting it.")
                    return self._start_profile(profile_id, expected_profile=profile)
                time.sleep(0.3)
            raise ValueError(f"The server did not stop within {_STOP_TIMEOUT} seconds, so it was not restarted.")

    def _scan_and_store(self, launch_group=None, expected_profile_id=None, launch_process=None):
        """Scan, merge, and persist inventory; return a snapshot object."""
        now = _now()
        stamp = _stamp(now)
        payload = self._read_payload()
        profiles = {item["id"]: item for item in payload["profiles"]}
        old = payload["servers"]
        by_id = {}
        for item in old:
            started_at = item.get("startedAt")
            server_id = _server_id(item["pid"], started_at, item["port"])
            item = dict(item)
            item["id"] = server_id
            if isinstance(started_at, str):
                item["startedAt"] = started_at.strip()
            previous = by_id.get(server_id)
            if previous:
                first_seen = [value for value in (previous.get("firstSeen"), item.get("firstSeen")) if value]
                if item.get("lastSeen", "") > previous.get("lastSeen", ""):
                    by_id[server_id] = item
                if first_seen:
                    by_id[server_id]["firstSeen"] = min(first_seen)
            else:
                by_id[server_id] = item

        uid = _current_uid()
        windows = platform.system() == "Windows"
        live_rows = _listener_rows(uid, old) if windows else _listener_rows(uid)
        if not isinstance(live_rows, list):
            raise RuntimeError("The local listener scan returned invalid data.")
        live = []
        for row in live_rows:
            if not isinstance(row, dict):
                raise RuntimeError("The local listener scan returned invalid data.")
            if str(row.get("uid", uid)) != uid:
                continue
            if not self._valid_listener(row):
                raise RuntimeError("The local listener scan returned invalid process data.")
            if _is_local_server(row):
                live.append(row)

        seen = set()
        self._last_launch_server_id = None
        for row in live:
            server_id = _server_id(row["pid"], row["startedAt"], row["port"])
            seen.add(server_id)
            previous = by_id.get(server_id, {})
            launched = False
            if windows:
                row = dict(row)
                expected = profiles.get(expected_profile_id, {})
                if (launch_process is not None and expected.get("port") == row["port"]
                        and _windows().launched_listener(row, launch_process)):
                    row["cwd"] = expected["cwd"]
                    launched = True
                elif previous.get("cwd"):
                    row["cwd"] = previous["cwd"]
            profile_id = _profile_id(row["cwd"], row["port"]) if row["cwd"] else None
            server = {
                "id": server_id,
                "pid": row["pid"],
                "process": row["process"] or "Unknown process",
                "cwd": row["cwd"],
                "project": os.path.basename(row["cwd"].rstrip(os.sep)) if row["cwd"] else "",
                "host": row["host"],
                "port": row["port"],
                "startedAt": row["startedAt"],
                "state": "running",
                "firstSeen": previous.get("firstSeen", stamp),
                "lastSeen": stamp,
                "stoppedAt": None,
                "profileId": profile_id,
                "isConfigured": bool(profiles.get(profile_id, {}).get("command")),
                "isPinned": bool(profiles.get(profile_id, {}).get("isPinned")),
            }
            by_id[server_id] = server
            if ((launched or (not windows and launch_group is not None and row.get("processGroup") == launch_group))
                    and expected_profile_id == profile_id):
                self._last_launch_server_id = server_id

        for server_id, item in by_id.items():
            if server_id not in seen and item.get("state") == "running":
                item["state"] = "stopped"
                item["stoppedAt"] = stamp

        for item in by_id.values():
            try:
                profile_id = _profile_id(item.get("cwd", ""), item.get("port"))
            except (TypeError, ValueError):
                profile_id = ""
            profile = profiles.get(profile_id, {})
            item["profileId"] = profile_id or None
            item["isConfigured"] = bool(profile.get("command"))
            item["isPinned"] = bool(profile.get("isPinned"))

        cutoff = now - timedelta(days=_HISTORY_DAYS)
        kept = []
        for item in by_id.values():
            last_seen = _parse_stamp(item.get("lastSeen"))
            if last_seen and last_seen >= cutoff:
                kept.append(item)
        kept = self._deduplicate_profiles(kept, self._last_launch_server_id)
        kept.sort(key=lambda item: (
            item.get("isPinned", False), item.get("state") == "running", item.get("lastSeen", "")
        ), reverse=True)
        kept = kept[:_MAX_SERVERS]
        stored_servers = [self._stored_server(item) for item in kept]
        self._write({"updatedAt": stamp, "servers": stored_servers, "profiles": list(profiles.values())})

        display = list(kept)
        displayed_profiles = {item.get("profileId") for item in display}
        for profile in profiles.values():
            if profile["id"] in displayed_profiles or not (profile.get("command") or profile.get("isPinned")):
                continue
            cwd = profile["cwd"]
            display.append({
                "id": profile["id"],
                "pid": 0,
                "process": "Saved server",
                "cwd": cwd,
                "project": os.path.basename(cwd.rstrip(os.sep)),
                "host": "localhost",
                "port": profile["port"],
                "startedAt": "",
                "state": "stopped",
                "firstSeen": "",
                "lastSeen": "",
                "stoppedAt": None,
                "profileId": profile["id"],
                "isConfigured": bool(profile.get("command")),
                "isPinned": bool(profile.get("isPinned")),
            })
        display = self._deduplicate_profiles(display, self._last_launch_server_id)
        return {"updatedAt": stamp, "servers": display}

    @staticmethod
    def _valid_listener(row):
        pid = row.get("pid")
        port = row.get("port")
        return (
            type(pid) is int and pid > 1
            and isinstance(row.get("process"), str)
            and isinstance(row.get("cwd"), str)
            and isinstance(row.get("host"), str) and bool(row.get("host"))
            and type(port) is int and 1 <= port <= 65535
            and isinstance(row.get("startedAt"), str) and bool(row.get("startedAt").strip())
            and (row.get("processGroup") is None or type(row.get("processGroup")) is int)
        )

    @staticmethod
    def _stored_server(item):
        keys = (
            "id", "pid", "process", "cwd", "project", "host", "port", "startedAt", "state",
            "firstSeen", "lastSeen", "stoppedAt", "profileId", "isConfigured", "isPinned",
        )
        stored = {key: item.get(key) for key in keys}
        stored["isConfigured"] = bool(item.get("isConfigured", False))
        stored["isPinned"] = bool(item.get("isPinned", False))
        return stored

    @staticmethod
    def _public_server(item):
        return LocalServerInventory._stored_server(item)

    @staticmethod
    def _deduplicate_profiles(servers, launch_server_id=None):
        by_profile = {}
        for item in servers:
            key = item.get("profileId") or item.get("id")
            previous = by_profile.get(key)
            if previous is None:
                by_profile[key] = item
                continue
            if item.get("id") == launch_server_id:
                winner, loser = item, previous
            elif previous.get("id") == launch_server_id:
                winner, loser = previous, item
            else:
                item_rank = (item.get("state") == "running", item.get("lastSeen", ""), item.get("startedAt", ""))
                previous_rank = (previous.get("state") == "running", previous.get("lastSeen", ""), previous.get("startedAt", ""))
                winner, loser = (item, previous) if item_rank > previous_rank else (previous, item)
            first_seen = [value for value in (winner.get("firstSeen"), loser.get("firstSeen")) if value]
            if first_seen:
                winner = dict(winner)
                winner["firstSeen"] = min(first_seen)
            by_profile[key] = winner
        return list(by_profile.values())

    def _read(self):
        return self._read_payload()["servers"]

    @staticmethod
    def _apply_profile_flags(payload):
        profiles = {item["id"]: item for item in payload["profiles"]}
        for server in payload["servers"]:
            try:
                profile_id = _profile_id(server.get("cwd", ""), server.get("port"))
            except (TypeError, ValueError):
                profile_id = ""
            profile = profiles.get(profile_id, {})
            server["profileId"] = profile_id or None
            server["isConfigured"] = bool(profile.get("command"))
            server["isPinned"] = bool(profile.get("isPinned"))

    def _read_payload(self):
        try:
            with open(self.path, "rb") as file:
                raw = file.read(_MAX_INVENTORY_BYTES + 1)
        except FileNotFoundError:
            return {"updatedAt": "", "servers": [], "profiles": []}
        except OSError as error:
            raise RuntimeError(f"Could not read the local server inventory: {error.strerror or error}.")
        if len(raw) > _MAX_INVENTORY_BYTES:
            raise RuntimeError("The local server inventory is too large to read safely.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError, TypeError) as error:
            raise RuntimeError(f"The local server inventory is corrupt and was left unchanged: {error}.")
        if not isinstance(payload, dict):
            raise RuntimeError("The local server inventory has an invalid shape and was left unchanged.")
        servers = payload.get("servers", [])
        profiles = payload.get("profiles", [])
        updated_at = payload.get("updatedAt", "")
        if (not isinstance(servers, list) or not isinstance(profiles, list)
                or not isinstance(updated_at, str) or len(servers) > _MAX_SERVERS
                or len(profiles) > _MAX_PROFILES):
            raise RuntimeError("The local server inventory has an invalid shape and was left unchanged.")
        clean_servers = []
        for item in servers:
            if not self._valid_stored_server(item):
                raise RuntimeError("The local server inventory contains invalid server data and was left unchanged.")
            clean_servers.append(self._stored_server(item))
        clean_profiles = []
        for item in profiles:
            if not self._valid_profile_record(item):
                raise RuntimeError("The local server inventory contains invalid profile data and was left unchanged.")
            clean_profiles.append({
                "id": item["id"], "cwd": item["cwd"], "port": item["port"],
                "command": item["command"], "isPinned": bool(item.get("isPinned", False)),
            })
        return {"updatedAt": updated_at, "servers": clean_servers, "profiles": clean_profiles}

    @staticmethod
    def _valid_stored_server(item):
        if not isinstance(item, dict):
            return False
        pid = item.get("pid")
        port = item.get("port")
        return (
            isinstance(item.get("id"), str) and bool(item["id"])
            and type(pid) is int and pid > 1
            and isinstance(item.get("process"), str)
            and isinstance(item.get("cwd"), str)
            and isinstance(item.get("project"), str)
            and isinstance(item.get("host"), str)
            and type(port) is int and 1 <= port <= 65535
            and isinstance(item.get("startedAt"), str) and bool(item["startedAt"])
            and item.get("state") in ("running", "stopped")
            and isinstance(item.get("firstSeen"), str)
            and isinstance(item.get("lastSeen"), str)
            and (item.get("stoppedAt") is None or isinstance(item.get("stoppedAt"), str))
            and (item.get("profileId") is None or isinstance(item.get("profileId"), str))
            and type(item.get("isConfigured", False)) is bool
            and type(item.get("isPinned", False)) is bool
        )

    @staticmethod
    def _valid_profile_record(item):
        if not isinstance(item, dict):
            return False
        profile_id = item.get("id")
        cwd = item.get("cwd")
        port = item.get("port")
        command = item.get("command")
        pinned = item.get("isPinned", False)
        return (
            isinstance(profile_id, str) and _ID_RE.fullmatch(profile_id)
            and isinstance(cwd, str) and bool(cwd)
            and type(port) is int and 1 <= port <= 65535
            and isinstance(command, str) and len(command) <= 4096 and "\0" not in command
            and type(pinned) is bool
            and _profile_id(cwd, port) == profile_id
        )

    def _write(self, payload):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".local_servers.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, separators=(",", ":"))
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
