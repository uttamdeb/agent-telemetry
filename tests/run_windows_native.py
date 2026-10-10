"""Actual Windows controls/rendering fixtures; stdlib only, no live telemetry.

Run on Windows: python tests/run_windows_native.py --screenshots windows-ui-fixtures
The images contain invented figures, not the user's dashboard or desktop.
"""
import argparse
import ctypes as C
import datetime
from pathlib import Path
import struct
import sys
import tempfile
import time
import zlib
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if sys.platform != "win32":
    raise SystemExit("Run these native rendering checks on Windows.")

import native
from windows import win32 as w
from windows.popover import palette
from windows.tray import Tray

W = w.W


class FixtureTray(Tray):
    def __init__(self):
        self.calls = []
        self.fixture_login = False
        super().__init__(self_test=True)

    def submit(self, operation, argument=None):
        self.calls.append((operation, argument))
        if operation in ("start", "stop", "quit"):
            self.monitor.request(operation == "start")

    def login_enabled(self):
        return self.fixture_login

    def toggle_login(self):
        self.fixture_login = not self.fixture_login

    def save_settings(self):
        pass  # Fixtures never write into real native preferences.


class BITMAPINFO(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
        ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
        ("image_size", W.DWORD), ("x", W.LONG), ("y", W.LONG),
        ("used", W.DWORD), ("important", W.DWORD), ("colors", W.DWORD * 3)]


def screenshot(window, destination):
    rect = W.RECT()
    assert w.api(w.U, "GetWindowRect", W.BOOL, W.HWND, C.POINTER(W.RECT))(window, C.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    dc = w.get_dc(None)
    memory = w.compatible_dc(dc)
    image = w.bitmap(dc, width, height)
    old = w.select_object(memory, image)
    try:
        assert w.api(w.U, "PrintWindow", W.BOOL, W.HWND, W.HDC, W.UINT)(window, memory, 2)
        w.select_object(memory, old)
        info = BITMAPINFO()
        info.size, info.width, info.height, info.planes, info.bits = 40, width, -height, 1, 32
        raw = C.create_string_buffer(width * height * 4)
        assert w.api(w.G, "GetDIBits", C.c_int, W.HDC, W.HBITMAP, W.UINT, W.UINT,
                     C.c_void_p, C.POINTER(BITMAPINFO), W.UINT)(dc, image, 0, height, raw, C.byref(info), 0) == height
        data = raw.raw
        # A blank window/failed owner draw must not pass as a screenshot test.
        assert len({data[i:i+3] for i in range(0, len(data), 4)}) > 100
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            for x in range(width):
                i = (y * width + x) * 4
                rows.extend((data[i + 2], data[i + 1], data[i]))
        def chunk(kind, contents):
            return struct.pack(">I", len(contents)) + kind + contents + struct.pack(">I", zlib.crc32(kind + contents) & 0xffffffff)
        destination.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    finally:
        w.select_object(memory, old)
        w.delete_object(image)
        w.delete_dc(memory)
        w.release_dc(None, dc)


def pump():
    """Let Windows finish queued layout/paint work before capturing its output."""
    peek = w.api(w.U, "PeekMessageW", W.BOOL, C.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT)
    deadline = time.monotonic() + .05
    msg = W.MSG()
    while time.monotonic() < deadline:
        while peek(C.byref(msg), None, 0, 0, 1):
            w.translate(C.byref(msg))
            w.dispatch(C.byref(msg))
        time.sleep(.005)


def click(panel, identifier):
    w.send(panel.controls[identifier], 0xf5, 0, 0)  # BM_CLICK, actual native button routing.
    assert panel.error is None, panel.error


def main():
    args = argparse.ArgumentParser()
    args.add_argument("--screenshots", type=Path, default=Path("windows-ui-fixtures"))
    options = args.parse_args()
    options.screenshots.mkdir(parents=True, exist_ok=True)
    w.enable_dpi_awareness()
    with tempfile.TemporaryDirectory() as temp, patch.object(native, "support_dir", return_value=Path(temp) / "control"):
        app = FixtureTray()
        panel = app.popover
        try:
            app.monitor.request(True)
            app.monitor.summary = {"date": datetime.date.today().isoformat(), "tokens": 15094633, "spend": 2.59}
            app.monitor._state("Monitoring on", False)
            panel.show()
            assert w.is_visible(panel.window)
            assert not w.is_visible(panel.combo), "Settings should start collapsed"
            click(panel, 9)
            assert panel.expanded and w.is_visible(panel.combo)
            click(panel, 9)
            assert not panel.expanded and not w.is_visible(panel.combo)
            click(panel, 2)
            assert app.calls[-1] == ("stop", None)
            click(panel, 2)
            assert app.calls[-1] == ("start", None)
            panel.update(force=True)
            click(panel, 3)
            assert app.calls[-1] == ("refresh", None)
            click(panel, 9)
            click(panel, 11)
            assert app.settings["display"] == "numbers"
            click(panel, 10)
            assert app.settings["display"] == "icon" and app.nid.icon == app.base_icon
            click(panel, 5)
            assert app.settings["auto_start"] is True
            click(panel, 6)
            assert app.fixture_login is True
            w.send(panel.combo, 0x14e, 4, 0)
            w.send(panel.panel, 0x111, 20 | 1 << 16, panel.combo)
            assert app.calls[-1] == ("interval", 0)
            app.monitor.interval = 0
            panel.update(force=True)
            assert w.send(panel.combo, 0x147, 0, 0) == 4
            # Repeated summary changes retain actual focus/handles, not rebuilt UI.
            handles = dict(panel.controls)
            w.set_focus(panel.controls[9])
            app.monitor.summary["tokens"] += 1
            panel.update()
            assert panel.controls == handles and w.get_focus() == handles[9]
            # Both themes and realistic DPI scales, collapsed and expanded.
            panel.work = (0, 0, 2400, 1600)
            panel.anchor = (2000, 1540, 2032, 1572)
            for dark in (False, True):
                panel.dark = dark
                panel.colors = palette(dark)
                w.delete_object(panel.brush)
                panel.brush = w.solid_brush(panel.colors["bg"])
                for scale in (1, 1.5, 2):
                    panel.scale = scale
                    panel._fonts()
                    for expanded in (False, True):
                        panel.expanded = expanded
                        panel.update(force=True)
                        panel.reposition()
                        w.update_window(panel.window)
                        pump()
                        assert panel.expanded == expanded, ("Layout changed during paint", expanded, panel.expanded)
                        assert panel.error is None, panel.error
                        print("Capture", dark, scale, expanded, panel.geometry)
                        screenshot(panel.window, options.screenshots / ("%s-%s-%s.png" % (
                            "dark" if dark else "light", int(scale * 100), "expanded" if expanded else "collapsed")))
                        assert panel.expanded == expanded, "Printing toggled settings"
            # Failure retains the last figures and exposes Retry, not a blank card.
            app.monitor._state("Dashboard unavailable; showing the last successful refresh. Retrying…", True)
            panel.update(force=True)
            assert panel.warning and w.is_visible(panel.controls[12])
            screenshot(panel.window, options.screenshots / "dark-200-stale.png")
            click(panel, 12)
            assert app.calls[-1] == ("start", None)
            # Busy shutdown must leave Quit visible and prevent a duplicate Quit.
            app.quitting = True
            panel.update(force=True)
            assert w.is_visible(panel.controls[8]) and not w.is_enabled(panel.controls[8])
            assert not w.is_enabled(panel.controls[2])
            screenshot(panel.window, options.screenshots / "dark-200-saving.png")
            app.quitting = False
            panel.update(force=True)
            # Actual dialog keyboard routing closes the popup on Escape.
            msg = W.MSG()
            msg.hWnd, msg.message, msg.wParam = panel.window, 0x100, 0x1b
            assert w.dialog_message(panel.window, C.byref(msg))
            assert not w.is_visible(panel.window)
            panel.dismissed_at = 0
            panel.show()
            w.send(panel.window, 6, 0, 0)
            assert not w.is_visible(panel.window), "Outside activation must dismiss"
            assert panel.error is None, panel.error
            print("PASS Windows native popover actions, focus, Escape/outside dismissal, stale/saving states and 14 real rendering fixtures")
        finally:
            w.destroy_window(app.window)


if __name__ == "__main__":
    main()
