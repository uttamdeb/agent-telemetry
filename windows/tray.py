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
from windows.monitor import Monitor, import_ledger, login_enabled, toggle_login

if sys.platform != "win32":
    raise SystemExit("The system tray app runs on Windows. Run python install.py to select your OS.")

from windows.win32 import *  # Typed Win32 bindings, no third-party GUI toolkit.
from windows.popover import Popover
from windows.presentation import compact


class Tray:
    def __init__(self, generation=None, self_test=False):
        self.self_test = self_test
        self.settings_path = native.support_dir() / "tray-settings.json"
        try:
            self.settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(self.settings, dict):
                self.settings = {}
        except (OSError, ValueError):
            self.settings = {}
        interval = 15
        self.monitor = Monitor(ROOT, native.support_dir().parent / "AgentTelemetry",
                               sys.executable, interval)
        self.generation = None if self_test else native.register(generation)
        self.quitting = False
        self.pending_ui = 0
        self.queue = queue.Queue()
        self.next_poll = 0
        self.custom_icon = None
        self.icon_key = None
        self.callback = WNDPROC(self.window_proc)  # keep callback alive
        self.instance = get_module(None)
        # A real, DPI-sized chart glyph, never the generic Python/application icon.
        self.base_icon = load_image(None, str(ROOT / "windows" / "assets" / "TrayIcon.ico"),
                                    1, 32, 32, 0x10)
        if not self.base_icon:
            raise C.WinError(C.get_last_error())
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
        self.popover = Popover(self)
        if self_test:
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

    def test_handles(self):
        # Hosted runners may have no Explorer. Visual/interaction fixtures are
        # tested separately by tests/run_windows_native.py, using no real data.
        try:
            icon = self.numbers_icon("1.2M", "$2.34")
            if not icon:
                raise C.WinError(C.get_last_error())
            destroy_icon(icon)
            self.popover.update(force=True)
            print("PASS Win32 window, branded/numeric icons and native popover; Explorer icon added:", self.icon_added)
        finally:
            destroy_window(self.window)

    def submit(self, operation, argument=None):
        if self.quitting and operation != "quit":
            return
        intent = self.monitor.intent
        if operation in ("start", "stop", "quit") or (operation == "open" and not self.monitor.requested.is_set()):
            intent = self.monitor.request(operation in ("start", "open"))
        self.queue.put((operation, argument, intent))
        if operation != "poll":
            self.pending_ui += 1
        if hasattr(self, "popover"):
            self.popover.update(force=True)

    def worker(self):
        while True:
            operation, argument, intent = self.queue.get()
            quit_ok = False
            try:
                if operation in ("start", "stop", "quit"):
                    result = self.monitor.apply_command(operation, intent)
                    quit_ok = operation == "quit" and result
                elif operation == "open":
                    self.monitor.open_dashboard(intent, webbrowser.open)
                elif operation == "refresh":
                    self.monitor.refresh(parse=True, intent=intent)
                elif operation == "poll":
                    self.monitor.sync(intent=intent)
                elif operation == "interval":
                    self.monitor.set_interval(argument, intent)
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
                # Only the UI thread updates HWNDs and its pending-operation count.
                post(self.window, 0x8002, int(quit_ok),
                     int(operation == "quit") | (int(operation != "poll") << 1))

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

    def login_enabled(self):
        return login_enabled(sys.executable, Path(__file__).resolve())

    def toggle_login(self):
        toggle_login(sys.executable, Path(__file__).resolve())

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

    def command(self, command):
        if command == 1:
            self.submit("open")
        elif command == 2:
            self.submit("stop" if self.monitor.requested.is_set() else "start")
        elif command == 3:
            self.submit("refresh")
        elif command == 5:
            self.settings["auto_start"] = not self.settings.get("auto_start", False)
            self.save_settings()
        elif command == 6:
            self.toggle_login()
        elif command == 7:
            self.choose_import()
        elif command == 8:
            self.quit()
        elif command == 12:
            self.submit("start")
        elif command in (15, 60, 300, 900, 1000):
            self.submit("interval", 0 if command == 1000 else command)

    def quit(self):
        if not self.quitting:
            self.quitting = True
            self.submit("quit")

    def window_proc(self, window, message, wparam, lparam):
        try:
            if message == taskbar_created and hasattr(self, "nid"):
                notify(0, C.byref(self.nid))
            elif message == 0x8001 and lparam in (0x202, 0x205):
                self.popover.show()
            elif message == 0x113:
                if not native.heartbeat(self.generation, closing=self.quitting):
                    self.quit()
                if not self.quitting and self.monitor.requested.is_set() and time.time() >= self.next_poll and not self.queue.unfinished_tasks:
                    self.next_poll = time.time() + 1
                    self.submit("poll")
                self.update_icon()
                self.popover.update()
            elif message == 0x8002:
                if lparam & 2:
                    self.pending_ui = max(0, self.pending_ui - 1)
                if wparam:
                    native.unregister(self.generation)
                    destroy_window(window)
                elif lparam & 1:
                    self.quitting = False
                else:
                    self.update_icon()
                if not wparam:
                    self.popover.update(force=True)
            elif message == 0x8003:
                self.popover.hide()
                message_box(window, "The panel could not be drawn. See the local tray.log for details.",
                            "AgentTelemetry", 0x10)
            elif message == 0x10:
                self.quit()
            elif message == 0x2:
                if hasattr(self, "popover"):
                    self.popover.close()
                if hasattr(self, "nid"):
                    notify(2, C.byref(self.nid))
                if self.custom_icon:
                    destroy_icon(self.custom_icon)
                if self.base_icon:
                    destroy_icon(self.base_icon)
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
            if not is_visible(self.popover.window) or not dialog_message(self.popover.window, C.byref(message)):
                translate(C.byref(message))
                dispatch(C.byref(message))


def main():
    arguments = argparse.ArgumentParser()
    arguments.add_argument("--native-generation")
    arguments.add_argument("--self-test", action="store_true")
    options = arguments.parse_args()
    enable_dpi_awareness()
    if not options.self_test:
        native.support_dir().mkdir(parents=True, exist_ok=True)
        # pythonw has no console streams; keep failures inspectable locally.
        sys.stderr = open(native.support_dir() / "tray.log", "a", encoding="utf-8", buffering=1)
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
        if options.self_test:
            app.test_handles()
        elif app.generation is not None:
            app.run()
    finally:
        api(O, "CoUninitialize", None)()
        close_handle(mutex)


if __name__ == "__main__":
    main()
