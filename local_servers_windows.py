"""Windows TCP discovery and identity-checked server control, using Win32 only.

Process arguments, environments and foreign process memory are never inspected.
Windows has no supported API for another process's working directory, so a user
supplies that folder when configuring a listener. Launched children are attributed
with an unnamed job assigned before the launcher's first instruction executes.
"""
import contextlib
import ctypes as C
import functools
import ntpath
import os
import socket
import struct
import subprocess
import threading


DWORD = C.c_uint32
WORD = C.c_uint16
BOOL = C.c_int32
HANDLE = C.c_void_p
PVOID = C.c_void_p
SIZE = C.c_size_t
QUERY = 0x1000
SYNCHRONIZE = 0x100000
WAIT_TIMEOUT = 258
INFINITE = 0xffffffff
MAX_TABLE = 2 * 1024 * 1024


class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class TCP4(C.Structure):
    _fields_ = [(name, DWORD) for name in
                ("state", "address", "port", "remote_address", "remote_port", "pid")]


class TCP6(C.Structure):
    _fields_ = [("address", C.c_ubyte * 16), ("scope", DWORD), ("port", DWORD),
                ("remote_address", C.c_ubyte * 16), ("remote_scope", DWORD),
                ("remote_port", DWORD), ("state", DWORD), ("pid", DWORD)]


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("size", DWORD), ("descriptor", PVOID), ("inherit", BOOL)]


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", DWORD), ("reserved", C.c_wchar_p),
                ("desktop", C.c_wchar_p), ("title", C.c_wchar_p)] + [
        (name, DWORD) for name in ("x", "y", "width", "height", "chars_x", "chars_y", "fill", "flags")
    ] + [("show", WORD), ("reserved_size", WORD), ("reserved_bytes", PVOID),
         ("input", HANDLE), ("output", HANDLE), ("error", HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [("startup", STARTUPINFO), ("attributes", PVOID)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("pid", DWORD), ("tid", DWORD)]


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("sid", PVOID), ("attributes", DWORD)]


def _error(operation, code=None):
    code = code if code is not None else getattr(C, "get_last_error", lambda: 0)()
    return OSError(code, operation + " failed (Windows error " + str(code) + ").")


class _API:
    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Windows server controls require Windows.")
        kernel = C.WinDLL("kernel32", use_last_error=True)
        security = C.WinDLL("advapi32", use_last_error=True)
        network = C.WinDLL("iphlpapi", use_last_error=True)

        def bind(dll, name, result, *arguments):
            # DLL attribute lookup caches function objects: bind every API
            # independently so one signature cannot overwrite another.
            function = dll[name]
            function.restype, function.argtypes = result, list(arguments)
            setattr(self, name, function)

        bind(kernel, "CloseHandle", BOOL, HANDLE)
        bind(kernel, "GetCurrentProcess", HANDLE)
        bind(kernel, "OpenProcess", HANDLE, DWORD, BOOL, DWORD)
        bind(kernel, "GetProcessTimes", BOOL, HANDLE, *([C.POINTER(FILETIME)] * 4))
        bind(kernel, "QueryFullProcessImageNameW", BOOL, HANDLE, DWORD, C.c_wchar_p, C.POINTER(DWORD))
        bind(security, "OpenProcessToken", BOOL, HANDLE, DWORD, C.POINTER(HANDLE))
        bind(security, "GetTokenInformation", BOOL, HANDLE, DWORD, PVOID, DWORD, C.POINTER(DWORD))
        bind(security, "ConvertSidToStringSidW", BOOL, PVOID, C.POINTER(C.c_wchar_p))
        bind(kernel, "LocalFree", HANDLE, HANDLE)
        bind(network, "GetExtendedTcpTable", DWORD, PVOID, C.POINTER(DWORD), BOOL, DWORD, DWORD, DWORD)
        bind(kernel, "TerminateProcess", BOOL, HANDLE, DWORD)
        bind(kernel, "CreateJobObjectW", HANDLE, PVOID, C.c_wchar_p)
        bind(kernel, "AssignProcessToJobObject", BOOL, HANDLE, HANDLE)
        bind(kernel, "IsProcessInJob", BOOL, HANDLE, HANDLE, C.POINTER(BOOL))
        bind(kernel, "CreateFileW", HANDLE, C.c_wchar_p, DWORD, DWORD, PVOID, DWORD, DWORD, HANDLE)
        bind(kernel, "InitializeProcThreadAttributeList", BOOL, PVOID, DWORD, DWORD, C.POINTER(SIZE))
        bind(kernel, "UpdateProcThreadAttribute", BOOL, PVOID, DWORD, SIZE, PVOID, SIZE, PVOID, C.POINTER(SIZE))
        bind(kernel, "DeleteProcThreadAttributeList", None, PVOID)
        bind(kernel, "CreateProcessW", BOOL, C.c_wchar_p, C.c_wchar_p, PVOID, PVOID,
             BOOL, DWORD, PVOID, C.c_wchar_p, PVOID, C.POINTER(PROCESS_INFORMATION))
        bind(kernel, "ResumeThread", DWORD, HANDLE)
        bind(kernel, "WaitForSingleObject", DWORD, HANDLE, DWORD)
        bind(kernel, "GetExitCodeProcess", BOOL, HANDLE, C.POINTER(DWORD))
        bind(kernel, "GetSystemDirectoryW", DWORD, C.c_wchar_p, DWORD)


@functools.lru_cache(maxsize=1)
def _api():
    return _API()


@contextlib.contextmanager
def _process(pid, access=QUERY):
    api = _api()
    handle = api.OpenProcess(access, False, int(pid))
    try:
        yield handle
    finally:
        if handle:
            api.CloseHandle(handle)


def _owner(handle):
    api = _api()
    token = HANDLE()
    if not api.OpenProcessToken(handle, 0x0008, C.byref(token)):
        raise _error("Reading process ownership")
    try:
        size = DWORD()
        api.GetTokenInformation(token, 1, None, 0, C.byref(size))
        if not 0 < size.value <= 65536:
            raise _error("Reading process ownership")
        buffer = C.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, 1, buffer, size, C.byref(size)):
            raise _error("Reading process ownership")
        sid = SID_AND_ATTRIBUTES.from_buffer(buffer).sid
        text = C.c_wchar_p()
        if not api.ConvertSidToStringSidW(sid, C.byref(text)):
            raise _error("Reading process ownership")
        try:
            return text.value
        finally:
            api.LocalFree(C.cast(text, HANDLE))
    finally:
        api.CloseHandle(token)


def current_uid():
    return _owner(_api().GetCurrentProcess())


def _created(handle):
    values = [FILETIME() for _ in range(4)]
    if not _api().GetProcessTimes(handle, *(C.byref(value) for value in values)):
        raise _error("Reading process creation time")
    return str((values[0].high << 32) | values[0].low)


def _details(pid):
    with _process(pid) as handle:
        if not handle:
            return None
        try:
            identity = {"uid": _owner(handle), "startedAt": _created(handle), "processGroup": None}
        except OSError:
            return None
        name, size = C.create_unicode_buffer(32768), DWORD(32768)
        identity["process"] = (ntpath.basename(name.value)
                               if _api().QueryFullProcessImageNameW(handle, 0, name, C.byref(size)) else "Unknown process")
        return identity


def process_details(pids):
    return {pid: detail for pid in pids if (detail := _details(pid)) is not None}


def _parse_table(raw, family):
    row_type = TCP4 if family == socket.AF_INET else TCP6
    if len(raw) < 4:
        raise RuntimeError("Windows returned an incomplete TCP table.")
    count = struct.unpack_from("<I", raw)[0]
    size = C.sizeof(row_type)
    if count > (len(raw) - 4) // size:
        raise RuntimeError("Windows returned a truncated TCP table.")
    rows = []
    for index in range(count):
        row = row_type.from_buffer_copy(raw, 4 + index * size)
        if row.state != 2 or row.pid <= 1:
            continue
        port = socket.ntohs(row.port & 0xffff)
        if not 1 <= port <= 65535:
            raise RuntimeError("Windows returned an invalid listening port.")
        if family == socket.AF_INET:
            host = socket.inet_ntop(family, struct.pack("<I", row.address))
        else:
            host = socket.inet_ntop(family, bytes(row.address))
            if row.scope:
                host += "%" + str(row.scope)
        rows.append({"pid": int(row.pid), "host": host, "port": port})
    return rows


def tcp_rows():
    rows = []
    for family in (socket.AF_INET, socket.AF_INET6):
        size = DWORD()
        result = _api().GetExtendedTcpTable(None, C.byref(size), False, family, 3, 0)
        if family == socket.AF_INET6 and result == 50:
            continue
        for _ in range(4):
            if result not in (0, 122) or not 4 <= size.value <= MAX_TABLE:
                raise _error("Scanning local TCP listeners", result)
            buffer = C.create_string_buffer(size.value)
            result = _api().GetExtendedTcpTable(buffer, C.byref(size), False, family, 3, 0)
            if result == 0:
                rows.extend(_parse_table(buffer.raw[:size.value], family))
                break
            if result != 122:
                raise _error("Scanning local TCP listeners", result)
        else:
            raise RuntimeError("Windows TCP listeners changed too quickly to scan safely. Try again.")
    return rows


def listener_rows(uid, known_servers=(), excluded=()):
    endpoints = tcp_rows()
    known = {item.get("pid") for item in known_servers if item.get("state") == "running"}
    details = process_details({row["pid"] for row in endpoints} - set(excluded))
    missing = known & {row["pid"] for row in endpoints} - details.keys() - set(excluded)
    if missing:
        raise RuntimeError("Could not verify the identity of a previously observed Windows listener.")
    rows = {}
    for row in endpoints:
        identity = details.get(row["pid"])
        if not identity or identity["uid"] != uid:
            continue
        key = (row["pid"], row["port"])
        if key not in rows or row["host"] in ("0.0.0.0", "::"):
            rows[key] = {**row, **identity, "cwd": ""}
    return list(rows.values())


def listening_ports():
    return {row["port"] for row in tcp_rows()}


def stop(server):
    # Holding one handle through validation and termination prevents PID reuse
    # between two API calls from changing which process receives the action.
    with _process(server["pid"], QUERY | 0x0001) as handle:
        if not handle:
            raise ValueError("Windows could not open that process. Refresh and try again.")
        if _owner(handle) != current_uid() or _created(handle) != server["startedAt"]:
            raise ValueError("The process changed since confirmation. Refresh and try again.")
        if not _api().TerminateProcess(handle, 1):
            raise _error("Stopping the selected Windows process")


def launched_listener(row, process):
    with _process(row["pid"]) as handle:
        if not handle or _created(handle) != row["startedAt"]:
            return False
        belongs = BOOL()
        if not _api().IsProcessInJob(handle, process.job, C.byref(belongs)):
            raise _error("Verifying the launched listener")
        return bool(belongs.value)


class WindowsLaunch:
    def __init__(self, handle, job, pid):
        self.handle, self.job, self.pid = handle, job, int(pid)
        self.returncode = None
        self._lock = threading.RLock()

    def poll(self):
        with self._lock:
            if self.returncode is not None:
                return self.returncode
            result = _api().WaitForSingleObject(self.handle, 0)
            if result == WAIT_TIMEOUT:
                return None
            if result != 0:
                raise _error("Waiting for the launch command")
            status = DWORD()
            if not _api().GetExitCodeProcess(self.handle, C.byref(status)):
                raise _error("Reading the launch command status")
            self.returncode = status.value
            return self.returncode

    def wait(self, timeout=None):
        with self._lock:
            if self.handle:
                milliseconds = INFINITE if timeout is None else min(INFINITE - 1, max(0, int(timeout * 1000)))
                result = _api().WaitForSingleObject(self.handle, milliseconds)
                if result == WAIT_TIMEOUT:
                    raise subprocess.TimeoutExpired("saved server command", timeout)
                if result != 0:
                    raise _error("Waiting for the launch command")
                self.poll()
                _api().CloseHandle(self.handle)
                _api().CloseHandle(self.job)
                self.handle = self.job = None
            return self.returncode


def launch(command, cwd, environment):
    api = _api()
    system = C.create_unicode_buffer(32768)
    length = api.GetSystemDirectoryW(system, len(system))
    if not 0 < length < len(system):
        raise _error("Finding the Windows command interpreter")
    executable = ntpath.join(system.value, "cmd.exe")
    command_line = C.create_unicode_buffer(subprocess.list2cmdline([executable]) + ' /d /s /c "' + command + '"')
    clean_env = {key: value for key, value in environment.items() if key.upper() != "AGENT_TELEMETRY_CONTROL_TOKEN"}
    env_block = C.create_unicode_buffer("\0".join(key + "=" + value for key, value in sorted(clean_env.items(), key=lambda item: item[0].upper())) + "\0\0")
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise _error("Creating the launch identity")
    nul, initialized, resumed = None, False, False
    attributes, information = None, PROCESS_INFORMATION()
    try:
        security = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), None, True)
        nul = api.CreateFileW("NUL", 0xc0000000, 3, C.byref(security), 3, 0x80, None)
        if not nul or nul == HANDLE(-1).value:
            nul = None
            raise _error("Preparing server input and output")
        size = SIZE()
        api.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
        if not 0 < size.value <= 65536:
            raise _error("Preparing the launch handle list")
        attributes = C.create_string_buffer(size.value)
        if not api.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)):
            raise _error("Preparing the launch handle list")
        initialized = True
        handles = (HANDLE * 1)(nul)
        if not api.UpdateProcThreadAttribute(attributes, 0, 0x20002, handles, C.sizeof(handles), None, None):
            raise _error("Restricting inherited server handles")
        startup = STARTUPINFOEX()
        startup.startup.cb = C.sizeof(startup)
        startup.startup.flags = 0x100
        startup.startup.input = startup.startup.output = startup.startup.error = nul
        startup.attributes = C.cast(attributes, PVOID)
        # No KILL_ON_JOB_CLOSE: leaving the app or timing out never kills a server.
        # Suspend first, assign the job, then resume, so even a fast shell child
        # cannot race outside the identity used to verify startup.
        flags = 0x00000004 | 0x00000400 | 0x08000000 | 0x00080000
        if not api.CreateProcessW(executable, command_line, None, None, True, flags,
                                  env_block, cwd, C.byref(startup), C.byref(information)):
            raise _error("Launching the saved Windows command")
        if not api.AssignProcessToJobObject(job, information.process):
            raise _error("Assigning the launch identity")
        if api.ResumeThread(information.thread) != 1:
            raise _error("Starting the saved Windows command")
        resumed = True
        return WindowsLaunch(information.process, job, information.pid)
    finally:
        if information.thread:
            api.CloseHandle(information.thread)
        if not resumed:
            if information.process:
                # Only our newly created, still-suspended launcher is cleaned up.
                api.TerminateProcess(information.process, 1)
                api.CloseHandle(information.process)
            api.CloseHandle(job)
        if initialized:
            api.DeleteProcThreadAttributeList(attributes)
        if nul:
            api.CloseHandle(nul)
