"""AgentTelemetry native Windows notification-area app; Python stdlib only."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native
from windows.monitor import Monitor, import_ledger

if sys.platform != "win32":
    raise SystemExit("The system tray app runs on Windows. Run python install.py to select your OS.")

U, S, K, G, O = (C.WinDLL(name, use_last_error=True) for name in
                 ("user32", "shell32", "kernel32", "gdi32", "ole32"))
LRESULT = C.c_ssize_t
WNDPROC = C.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)


class WNDCLASS(C.Structure):
    _fields_ = [("style", W.UINT), ("proc", WNDPROC), ("extra", C.c_int),
        ("windowExtra", C.c_int), ("instance", W.HINSTANCE), ("icon", W.HICON),
        ("cursor", W.HANDLE), ("background", W.HBRUSH), ("menu", W.LPCWSTR), ("name", W.LPCWSTR)]


class GUID(C.Structure):
    _fields_ = [("a", W.DWORD), ("b", W.WORD), ("c", W.WORD), ("d", C.c_ubyte * 8)]


class NOTIFYICONDATA(C.Structure):
    _fields_ = [("size", W.DWORD), ("window", W.HWND), ("id", W.UINT),
        ("flags", W.UINT), ("message", W.UINT), ("icon", W.HICON),
        ("tip", W.WCHAR * 128), ("state", W.DWORD), ("mask", W.DWORD),
        ("info", W.WCHAR * 256), ("version", W.UINT), ("title", W.WCHAR * 64),
        ("infoFlags", W.DWORD), ("guid", GUID), ("balloonIcon", W.HICON)]


class ICONINFO(C.Structure):
    _fields_ = [("icon", W.BOOL), ("x", W.DWORD), ("y", W.DWORD),
                ("mask", W.HBITMAP), ("color", W.HBITMAP)]


class BROWSEINFO(C.Structure):
    _fields_ = [("owner", W.HWND), ("root", C.c_void_p), ("display", W.LPWSTR),
        ("title", W.LPCWSTR), ("flags", W.UINT), ("callback", C.c_void_p),
        ("parameter", W.LPARAM), ("image", C.c_int)]


def api(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype, function.argtypes = result, list(arguments)
    return function


get_module = api(K, "GetModuleHandleW", W.HMODULE, W.LPCWSTR)
register_class = api(U, "RegisterClassW", W.ATOM, C.POINTER(WNDCLASS))
create_window = api(U, "CreateWindowExW", W.HWND, W.DWORD, W.LPCWSTR, W.LPCWSTR,
    W.DWORD, C.c_int, C.c_int, C.c_int, C.c_int, W.HWND, W.HMENU, W.HINSTANCE, C.c_void_p)
default_proc = api(U, "DefWindowProcW", LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
get_message = api(U, "GetMessageW", C.c_int, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT)
translate = api(U, "TranslateMessage", W.BOOL, C.POINTER(W.MSG))
dispatch = api(U, "DispatchMessageW", LRESULT, C.POINTER(W.MSG))
post = api(U, "PostMessageW", W.BOOL, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
destroy_window = api(U, "DestroyWindow", W.BOOL, W.HWND)
post_quit = api(U, "PostQuitMessage", None, C.c_int)
set_timer = api(U, "SetTimer", C.c_size_t, W.HWND, C.c_size_t, W.UINT, C.c_void_p)
notify = api(S, "Shell_NotifyIconW", W.BOOL, W.DWORD, C.POINTER(NOTIFYICONDATA))
load_icon = api(U, "LoadIconW", W.HICON, W.HINSTANCE, C.c_void_p)
destroy_icon = api(U, "DestroyIcon", W.BOOL, W.HICON)
popup = api(U, "CreatePopupMenu", W.HMENU)
append = api(U, "AppendMenuW", W.BOOL, W.HMENU, W.UINT, C.c_size_t, W.LPCWSTR)
destroy_menu = api(U, "DestroyMenu", W.BOOL, W.HMENU)
get_cursor = api(U, "GetCursorPos", W.BOOL, C.POINTER(W.POINT))
foreground = api(U, "SetForegroundWindow", W.BOOL, W.HWND)
track = api(U, "TrackPopupMenu", W.UINT, W.HMENU, W.UINT, C.c_int, C.c_int, C.c_int, W.HWND, C.c_void_p)
message_box = api(U, "MessageBoxW", C.c_int, W.HWND, W.LPCWSTR, W.LPCWSTR, W.UINT)
taskbar_created = api(U, "RegisterWindowMessageW", W.UINT, W.LPCWSTR)("TaskbarCreated")
create_mutex = api(K, "CreateMutexW", W.HANDLE, C.c_void_p, W.BOOL, W.LPCWSTR)
close_handle = api(K, "CloseHandle", W.BOOL, W.HANDLE)
get_dc = api(U, "GetDC", W.HDC, W.HWND)
release_dc = api(U, "ReleaseDC", C.c_int, W.HWND, W.HDC)
compatible_dc = api(G, "CreateCompatibleDC", W.HDC, W.HDC)
bitmap = api(G, "CreateCompatibleBitmap", W.HBITMAP, W.HDC, C.c_int, C.c_int)
select_object = api(G, "SelectObject", W.HANDLE, W.HDC, W.HANDLE)
delete_object = api(G, "DeleteObject", W.BOOL, W.HANDLE)
delete_dc = api(G, "DeleteDC", W.BOOL, W.HDC)
background_color = api(G, "SetBkColor", W.DWORD, W.HDC, W.DWORD)
text_color = api(G, "SetTextColor", W.DWORD, W.HDC, W.DWORD)
text_out = api(G, "ExtTextOutW", W.BOOL, W.HDC, C.c_int, C.c_int, W.UINT,
               C.POINTER(W.RECT), W.LPCWSTR, W.UINT, C.c_void_p)
create_font = api(G, "CreateFontW", W.HANDLE, *([C.c_int] * 5 + [W.DWORD] * 8 + [W.LPCWSTR]))
create_bitmap = api(G, "CreateBitmap", W.HBITMAP, C.c_int, C.c_int, W.UINT, W.UINT, C.c_void_p)
create_icon = api(U, "CreateIconIndirect", W.HICON, C.POINTER(ICONINFO))
browse = api(S, "SHBrowseForFolderW", C.c_void_p, C.POINTER(BROWSEINFO))
path_from_id = api(S, "SHGetPathFromIDListW", W.BOOL, C.c_void_p, W.LPWSTR)
free_id = api(O, "CoTaskMemFree", None, C.c_void_p)


def compact(value):
    if value >= 1000000:
        return "%.1fM" % (value / 1000000)
    if value >= 1000:
        return "%.1fk" % (value / 1000)
    return str(value)


class Tray:
    def __init__(self, generation=None, self_test=False):
        self.settings_path = native.support_dir() / "tray-settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        interval = self.settings.get("interval", 300)
        if interval not in (60, 300, 900):
            interval = 300
        self.monitor = Monitor(ROOT, native.support_dir().parent / "AgentTelemetry",
                               sys.executable, interval)
        self.generation = None if self_test else native.register(generation)
        self.quitting = False
        self.queue = queue.Queue()
        self.next_poll = 0
        self.custom_icon = None
        self.icon_key = None
        self.callback = WNDPROC(self.window_proc)  # keep callback alive
        self.instance = get_module(None)
        self.base_icon = load_icon(None, C.c_void_p(32512))
        window_class = WNDCLASS(0, self.callback, 0, 0, self.instance, self.base_icon,
                               None, None, None, "AgentTelemetryTray")
        if not register_class(C.byref(window_class)):
            raise C.WinError(C.get_last_error())
        self.window = create_window(0, window_class.name, "AgentTelemetry", 0,
                                    0, 0, 0, 0, None, None, self.instance, None)
        if not self.window:
            raise C.WinError(C.get_last_error())
        self.nid = NOTIFYICONDATA()
        self.nid.size = C.sizeof(NOTIFYICONDATA)
        self.nid.window, self.nid.id = self.window, 1
        self.nid.flags, self.nid.message = 1 | 2 | 4, 0x8001
        self.nid.icon, self.nid.tip = self.base_icon, "AgentTelemetry"
        self.icon_added = bool(notify(0, C.byref(self.nid)))
        if self_test:
            # Hosted Windows runners may have no Explorer; exercise Win32 handles,
            # GDI numeric icons and menu construction without claiming Explorer UI.
            icon = self.numbers_icon("1.2M", "$2.34")
            if not icon:
                raise C.WinError(C.get_last_error())
            destroy_icon(icon)
            menu = self.build_menu()
            destroy_menu(menu)
            print("PASS Win32 hidden window, numeric icon and native popup menu; Explorer icon added:", self.icon_added)
            notify(2, C.byref(self.nid))
            destroy_window(self.window)
            return
        if self.generation is None:
            destroy_window(self.window)
            return
        threading.Thread(target=self.worker, daemon=True).start()
        set_timer(self.window, 1, 1000, None)
        if generation or self.settings.get("auto_start"):
            self.submit("start")

    def save_settings(self):
        native._write("tray-settings.json", self.settings)

    def submit(self, operation, argument=None):
        if operation == "start":
            self.monitor.requested.set()
        if operation in ("stop", "quit"):
            self.monitor.requested.clear()
        self.queue.put((operation, argument))

    def worker(self):
        while True:
            operation, argument = self.queue.get()
            quit_ok = False
            try:
                if operation == "start":
                    self.monitor.start()
                elif operation == "stop":
                    self.monitor.stop()
                elif operation == "quit":
                    quit_ok = self.monitor.stop()
                elif operation == "refresh":
                    self.monitor.refresh(parse=True)
                elif operation == "poll":
                    self.monitor.refresh()
                elif operation == "interval":
                    if self.monitor.process is not None:
                        if self.monitor.stop():
                            self.monitor.requested.set()
                            self.monitor.start()
                elif operation == "import":
                    if self.monitor.requested.is_set() or self.monitor._probe() is not None:
                        raise ValueError("Stop the existing dashboard before importing usage history.")
                    import_ledger(argument, self.monitor.data_dir)
                    self.monitor._state("History imported. Start monitoring when ready.")
            except Exception as error:
                traceback.print_exc()
                self.monitor._state("Could not complete the action: " + str(error), True)
            finally:
                self.queue.task_done()
                post(self.window, 0x8002, int(quit_ok), int(operation == "quit"))

    def numbers_icon(self, tokens, spend):
        dc = get_dc(None)
        memory = compatible_dc(dc)
        color = bitmap(dc, 32, 32)
        old = select_object(memory, color)
        font = create_font(12, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 0, 0, "Segoe UI")
        old_font = select_object(memory, font)
        background_color(memory, 0x302820)
        text_color(memory, 0xFFFFFF)
        rectangle = W.RECT(0, 0, 32, 32)
        text_out(memory, 0, 1, 2, C.byref(rectangle), tokens, len(tokens), None)
        text_out(memory, 0, 16, 0, None, spend, len(spend), None)
        mask_bits = C.create_string_buffer(128)
        mask = create_bitmap(32, 32, 1, 1, mask_bits)
        icon = create_icon(C.byref(ICONINFO(True, 0, 0, mask, color)))
        select_object(memory, old_font)
        select_object(memory, old)
        delete_object(font)
        delete_object(mask)
        delete_object(color)
        delete_dc(memory)
        release_dc(None, dc)
        return icon

    def update_icon(self):
        snapshot = self.monitor.snapshot()
        today = snapshot["date"] == time.strftime("%Y-%m-%d")
        tokens = compact(snapshot["tokens"]) if today else "—"
        spend = "$%.2f" % snapshot["spend"] if today else "—"
        self.nid.tip = ("AgentTelemetry: " + snapshot["status"] + "; " + tokens +
                        " tokens; estimated " + spend)[:127]
        key = (tokens, spend, self.settings.get("display", "icon"))
        if key != self.icon_key:
            new = self.numbers_icon(tokens, spend) if key[2] == "numbers" else None
            self.nid.icon = new or self.base_icon
            notify(1, C.byref(self.nid))
            if self.custom_icon:
                destroy_icon(self.custom_icon)
            self.custom_icon, self.icon_key = new, key
        else:
            notify(1, C.byref(self.nid))

    def build_menu(self):
        menu = popup()
        snapshot = self.monitor.snapshot()
        today = snapshot["date"] == time.strftime("%Y-%m-%d")
        append(menu, 2, 0, "AgentTelemetry — " + snapshot["status"])
        append(menu, 2, 0, "Today: " + (format(snapshot["tokens"], ",") if today else "unavailable") + " tokens")
        append(menu, 2, 0, "Estimated API spend: " + ("$%.2f" % snapshot["spend"] if today else "unavailable"))
        if snapshot["stale"]:
            append(menu, 2, 0, "Figures are from the last successful refresh")
        append(menu, 0x800, 0, None)
        append(menu, 0, 1, "Open dashboard")
        append(menu, 0, 2, "Stop monitoring" if snapshot["requested"] else "Start monitoring")
        append(menu, 0 if snapshot["requested"] else 2, 3, "Refresh now")
        append(menu, 0x800, 0, None)
        append(menu, 8 if self.settings.get("display") == "numbers" else 0, 4, "Show numbers in tray icon")
        for identifier, seconds in ((60, 60), (300, 300), (900, 900)):
            append(menu, 8 if self.monitor.interval == seconds else 0, identifier,
                   "Refresh every %d minute%s" % (seconds // 60, "" if seconds == 60 else "s"))
        append(menu, 8 if self.settings.get("auto_start") else 0, 5, "Start monitoring when app opens")
        append(menu, 8 if self.login_enabled() else 0, 6, "Open at Login")
        append(menu, 2 if snapshot["requested"] or self.monitor.process else 0, 7, "Import existing data…")
        append(menu, 0x800, 0, None)
        append(menu, 0, 8, "Quit AgentTelemetry")
        return menu

    def login_enabled(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
                command, _ = winreg.QueryValueEx(key, "AgentTelemetry")
                return command == subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve())])
        except OSError:
            return False

    def toggle_login(self):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as key:
            if self.login_enabled():
                winreg.DeleteValue(key, "AgentTelemetry")
            else:
                winreg.SetValueEx(key, "AgentTelemetry", 0, winreg.REG_SZ,
                                  subprocess.list2cmdline([sys.executable, str(Path(__file__).resolve())]))

    def choose_import(self):
        buffer = C.create_unicode_buffer(260)
        info = BROWSEINFO(self.window, None, buffer, "Choose the existing AgentTelemetry data folder",
                          0x41, None, 0, 0)
        identifier = browse(C.byref(info))
        if not identifier:
            return
        try:
            if path_from_id(identifier, buffer):
                self.submit("import", buffer.value)
        finally:
            free_id(identifier)

    def menu(self):
        menu = self.build_menu()
        try:
            position = W.POINT()
            get_cursor(C.byref(position))
            foreground(self.window)
            command = track(menu, 0x100 | 0x2, position.x, position.y, 0, self.window, None)
            post(self.window, 0, 0, 0)
        finally:
            destroy_menu(menu)
        if command == 1:
            webbrowser.open(self.monitor.url)
            if not self.monitor.requested.is_set():
                self.submit("start")
        elif command == 2:
            self.submit("stop" if self.monitor.requested.is_set() else "start")
        elif command == 3:
            self.submit("refresh")
        elif command == 4:
            self.settings["display"] = "icon" if self.settings.get("display") == "numbers" else "numbers"
            self.save_settings()
            self.update_icon()
        elif command == 5:
            self.settings["auto_start"] = not self.settings.get("auto_start", False)
            self.save_settings()
        elif command == 6:
            self.toggle_login()
        elif command == 7:
            self.choose_import()
        elif command == 8:
            self.quit()
        elif command in (60, 300, 900):
            self.monitor.interval = self.settings["interval"] = command
            self.save_settings()
            self.next_poll = 0
            self.submit("interval")

    def quit(self):
        if not self.quitting:
            self.quitting = True
            self.submit("quit")

    def window_proc(self, window, message, wparam, lparam):
        try:
            if message == taskbar_created and hasattr(self, "nid"):
                notify(0, C.byref(self.nid))
            elif message == 0x8001 and lparam in (0x202, 0x205):
                self.menu()
            elif message == 0x113:
                if not native.heartbeat(self.generation):
                    self.quit()
                if not self.quitting and self.monitor.requested.is_set() and time.time() >= self.next_poll and not self.queue.unfinished_tasks:
                    self.next_poll = time.time() + self.monitor.interval
                    self.submit("poll")
                self.update_icon()
            elif message == 0x8002:
                if wparam:
                    native.unregister(self.generation)
                    destroy_window(window)
                elif lparam:
                    self.quitting = False
                else:
                    self.update_icon()
            elif message == 0x10:
                self.quit()
            elif message == 0x2:
                if hasattr(self, "nid"):
                    notify(2, C.byref(self.nid))
                if self.custom_icon:
                    destroy_icon(self.custom_icon)
                post_quit(0)
            else:
                return default_proc(window, message, wparam, lparam)
        except Exception as error:
            traceback.print_exc()
            message_box(window, str(error), "AgentTelemetry", 0x10)
        return 0

    def run(self):
        message = W.MSG()
        while True:
            result = get_message(C.byref(message), None, 0, 0)
            if result == -1:
                raise C.WinError(C.get_last_error())
            if result == 0:
                break
            translate(C.byref(message))
            dispatch(C.byref(message))


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--native-generation")
    arguments.add_argument("--self-test", action="store_true")
    options = arguments.parse_args()
    # Single instance per Windows session; the OS closes the mutex on process exit.
    mutex = create_mutex(None, False, "Local\\AgentTelemetryTray")
    if not mutex:
        raise C.WinError(C.get_last_error())
    if C.get_last_error() == 183:
        close_handle(mutex)
        return
    api(O, "CoInitializeEx", C.c_long, C.c_void_p, W.DWORD)(None, 2)
    try:
        app = Tray(options.native_generation, options.self_test)
        if not options.self_test and app.generation is not None:
            app.run()
    finally:
        api(O, "CoUninitialize", None)()
        close_handle(mutex)


if __name__ == "__main__":
    main()
