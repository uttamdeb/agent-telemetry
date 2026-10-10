"""Native Win32 counterpart of macOS StatusMenu, using only Python's stdlib.

Standard button/combobox HWNDs provide keyboard navigation and accessibility names;
owner drawing supplies the shared card layout instead of a long system context menu.
Network, parsing and shutdown work stays on Tray's existing serialized worker.
"""
import time
import traceback

from windows import win32 as w
from windows.presentation import WIDTH, INTERVALS, figures, layout, position

C, W = w.C, w.W


def color(hexadecimal):
    red, green, blue = bytes.fromhex(hexadecimal)
    return red | green << 8 | blue << 16


def palette(dark=None):
    contrast = w.HIGHCONTRAST(C.sizeof(w.HIGHCONTRAST), 0, None)
    if w.system_parameters(0x42, contrast.size, C.byref(contrast), 0) and contrast.flags & 1:
        return {"bg": w.system_color(5), "card": w.system_color(5),
                "text": w.system_color(8), "muted": w.system_color(8),
                "line": w.system_color(8), "accent": w.system_color(13),
                "on_accent": w.system_color(14), "good": w.system_color(8),
                "warning": w.system_color(8), "hover": w.system_color(15)}
    if dark is None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                dark = not winreg.QueryValueEx(key, "AppsUseLightTheme")[0]
        except OSError:
            dark = False
    values = ("202023", "2b2b2e", "f5f5f7", "b5b5be", "424249", "9a83f3", "17131f", "6cd391", "ffc06e", "37373c") if dark else (
        "fafafa", "f0f0f2", "202026", "60606b", "d8d8df", "7053d6", "ffffff", "257448", "925315", "e7e7ec")
    return dict(zip(("bg", "card", "text", "muted", "line", "accent", "on_accent", "good", "warning", "hover"), map(color, values)))


class Popover:
    def __init__(self, tray):
        self.tray = tray
        self.window = None
        self.panel = None
        self.expanded = False
        self.scroll = 0
        self.scale = 1
        self.dark = None  # OS preference; explicit overrides used only by fixtures.
        self.colors = palette()
        self.fonts = {}
        self.controls = {}
        self.labels = {}
        self.snapshot = tray.monitor.snapshot()
        self.busy = False
        self.warning = False
        self.error = None
        self.anchor = (0, 0, 0, 0)
        self.work = (0, 0, 1920, 1080)
        self.dismissed_at = 0
        self.callback = w.WNDPROC(self.window_proc)
        cls = w.WNDCLASS(0x20000, self.callback, 0, 0, tray.instance, tray.base_icon,
                         w.api(w.U, "LoadCursorW", W.HANDLE, W.HINSTANCE, C.c_void_p)(None, C.c_void_p(32512)),
                         None, None, "AgentTelemetryPopover")
        if not w.register_class(C.byref(cls)):
            raise C.WinError(C.get_last_error())
        self.window = w.create_window(0x10088, cls.name, "AgentTelemetry — Today", 0x82000000,
                                     0, 0, WIDTH, 250, tray.window, None, tray.instance, None)
        if not self.window:
            raise C.WinError(C.get_last_error())
        self.brush = w.solid_brush(self.colors["bg"])
        panel_class = w.WNDCLASS(0, self.callback, 0, 0, tray.instance, None,
                                 cls.cursor, None, None, "AgentTelemetrySettings")
        if not w.register_class(C.byref(panel_class)):
            raise C.WinError(C.get_last_error())
        self.panel = w.create_window(0x10000, panel_class.name, "Details and settings", 0x42000000,
                                     0, 0, 0, 0, self.window, None, tray.instance, None)
        if not self.panel:
            raise C.WinError(C.get_last_error())
        # Standard HWNDs retain focus, keyboard and accessibility behavior.
        for identifier, title in ((2, "Start monitoring"), (9, "Details & settings"),
            (1, "Open dashboard"), (3, "Refresh now"), (8, "Quit AgentTelemetry"),
            (10, "Icon"), (11, "Numbers"), (6, "Open at login"),
            (5, "Start monitoring automatically"), (7, "Import existing data…"), (12, "Retry")):
            handle = w.create_window(0, "BUTTON", title, 0x5001400b, 0, 0, 0, 0,
                                      self.panel if identifier in (5, 6, 7, 10, 11) else self.window,
                                      identifier + 100, tray.instance, None)
            if not handle:
                raise C.WinError(C.get_last_error())
            self.controls[identifier] = handle
            self.labels[identifier] = title
        self.combo = w.create_window(0, "COMBOBOX", "Shared refresh", 0x50210313,
                                      0, 0, 200, 180, self.panel, 20, tray.instance, None)
        if not self.combo:
            raise C.WinError(C.get_last_error())
        for _, title in INTERVALS:
            w.send_text(self.combo, 0x143, 0, title)  # CB_ADDSTRING
        w.set_position(self.panel, self.controls[9], 0, 0, 0, 0, 0x13)  # Dialog tab order: header, details, settings, footer.
        self._fonts()
        self.update(force=True)

    def p(self, logical):
        return round(logical * self.scale)

    def rect(self, x, y, width, height):
        return W.RECT(self.p(x), self.p(y), self.p(x + width), self.p(y + height))

    def _fonts(self):
        for font in self.fonts.values():
            w.delete_object(font)
        self.fonts = {}
        for size, weight in ((10, 400), (11, 400), (12, 400), (12, 600), (13, 600), (20, 600)):
            font = w.create_font(-self.p(size), 0, 0, 0, weight, 0, 0, 0, 1, 0, 0, 0, 5, "Segoe UI")
            if not font:
                raise C.WinError(C.get_last_error())
            self.fonts[size, weight] = font
        w.send(self.combo, 0x30, self.fonts[12, 400], 1)
        w.send(self.combo, 0x153, C.c_size_t(-1).value, self.p(24))  # CB_SETITEMHEIGHT selection
        w.send(self.combo, 0x153, 0, self.p(24))

    def _place(self, identifier, x, y, width, height, visible=True):
        handle = self.combo if identifier == 20 else self.controls[identifier]
        w.set_position(handle, None, self.p(x), self.p(y), self.p(width), self.p(height), 0x14)
        w.show_window(handle, 5 if visible else 0)

    def _label(self, identifier, text):
        if self.labels[identifier] != text:
            self.labels[identifier] = text
            w.set_text(self.controls[identifier], text)

    def update(self, force=False):
        snapshot = self.tray.monitor.snapshot()
        busy = bool(self.tray.queue.unfinished_tasks)
        # Routine clock polling should not disable buttons/focus every second.
        busy = busy and self.tray.pending_ui > 0
        changed = force or snapshot != self.snapshot or busy != self.busy
        self.snapshot, self.busy = snapshot, busy
        if not changed:
            return
        status = snapshot["status"]
        self.warning = snapshot["stale"] or not (status in ("Monitoring on", "Monitoring off", "Using existing dashboard")
            or status.startswith(("Starting", "Stopping", "History imported")))
        self.geometry = layout(self.expanded, snapshot["stale"], self.warning,
                               int((self.work[3] - self.work[1]) / self.scale))
        details, settings, dashboard, footer = (self.geometry[k] for k in ("details", "settings", "dashboard", "footer"))
        self._place(2, 244, 19, 50, 28)
        self._place(9, 14, details, 282, 32)
        self._place(12, 244, details - 29, 52, 24, self.warning)
        self.place_settings()
        self._place(1, 14, dashboard, 282, 30)
        self._place(3, 14, footer, 101, 26)
        self._place(8, 126, footer, 170, 26)
        requested = snapshot["requested"]
        closing = self.tray.quitting
        w.enable_window(self.controls[2], not closing and not busy)
        w.enable_window(self.controls[3], requested and not busy and not closing)
        w.enable_window(self.combo, requested and not busy and not closing)
        w.enable_window(self.controls[7], not requested and not self.tray.monitor.process and not busy and not closing)
        w.enable_window(self.controls[1], not busy and not closing)
        w.enable_window(self.controls[12], not busy and not closing)
        w.enable_window(self.controls[8], not closing)
        for identifier in (5, 6, 10, 11):
            w.enable_window(self.controls[identifier], not closing)
        self._label(2, ("Stop" if requested else "Start") + " monitoring")
        self._label(9, "Details & settings — " + ("expanded" if self.expanded else "collapsed"))
        self._label(5, "Start monitoring automatically — " + ("on" if self.tray.settings.get("auto_start") else "off"))
        self._label(6, "Open at login — " + ("on" if self.tray.login_enabled() else "off"))
        self._label(10, "Icon" + (" — selected" if self.tray.settings.get("display", "icon") == "icon" else ""))
        self._label(11, "Numbers" + (" — selected" if self.tray.settings.get("display") == "numbers" else ""))
        self._label(8, "Saving usage history…" if closing else "Quit AgentTelemetry")
        # Do not change a combo selection underneath an open dropdown.
        if not w.send(self.combo, 0x157, 0, 0):
            selection = next((i for i, row in enumerate(INTERVALS) if row[0] == self.tray.monitor.interval), 0)
            w.send(self.combo, 0x14e, selection, 0)
        tokens, spend = figures(snapshot)
        w.set_text(self.window, "AgentTelemetry — Today: %s tokens, estimated spend %s — %s" % (tokens, spend, status))
        if w.is_visible(self.window):
            self.reposition()
        w.invalidate(self.window, None, False)
        for handle in self.controls.values():
            w.invalidate(handle, None, False)
        w.invalidate(self.panel, None, False)

    def place_settings(self):
        g = self.geometry
        height = g["settings_height"]
        self.scroll = min(self.scroll, max(0, 178 - height))
        scrolling = self.expanded and height < 178
        w.set_position(self.panel, None, self.p(14), self.p(g["settings"]), self.p(282), self.p(height), 0x14)
        w.show_scrollbar(self.panel, 1, scrolling)
        w.show_window(self.panel, 5 if self.expanded else 0)
        # The scrollbar only takes space on small/high-DPI screens.
        inset = 18 if scrolling else 0
        half = (261 - inset) // 2
        y = -self.scroll
        self._place(10, 21, y + 4, half, 28, self.expanded)
        self._place(11, 21 + half, y + 4, 261 - inset - half, 28, self.expanded)
        self._place(20, 129, y + 43, 153 - inset, 170, self.expanded)
        self._place(6, 21, y + 79, 261 - inset, 26, self.expanded)
        self._place(5, 21, y + 108, 261 - inset, 26, self.expanded)
        self._place(7, 21, y + 142, 261 - inset, 28, self.expanded)
        info = w.SCROLLINFO(C.sizeof(w.SCROLLINFO), 7, 0, 177, height, self.scroll, 0)
        w.set_scroll(self.panel, 1, C.byref(info), True)
        w.invalidate(self.panel, None, False)

    def reposition(self):
        x, y = position(self.anchor, self.work, (self.p(WIDTH), self.p(self.geometry["height"])), self.p(8))
        w.set_position(self.window, W.HWND(-1), x, y, self.p(WIDTH), self.p(self.geometry["height"]), 0x10)

    def show(self):
        if w.is_visible(self.window):
            self.hide()
            return
        # Clicking the tray icon first deactivates the popup, then delivers its click.
        if time.monotonic() - self.dismissed_at < .25:
            return
        rect = W.RECT()
        identity = w.NOTIFYICONIDENTIFIER(C.sizeof(w.NOTIFYICONIDENTIFIER), self.tray.window, 1, w.GUID())
        if w.icon_rect(C.byref(identity), C.byref(rect)) != 0:
            cursor = W.POINT()
            w.get_cursor(C.byref(cursor))
            rect = W.RECT(cursor.x, cursor.y, cursor.x, cursor.y)
        monitor = w.monitor_from_rect(C.byref(rect), 2)
        info = w.MONITORINFO()
        info.size = C.sizeof(info)
        if not w.monitor_info(monitor, C.byref(info)):
            raise C.WinError(C.get_last_error())
        self.anchor = (rect.left, rect.top, rect.right, rect.bottom)
        self.work = (info.work.left, info.work.top, info.work.right, info.work.bottom)
        self.scale = w.dpi_for_monitor(monitor) / 96
        self._fonts()
        self.update(force=True)
        self.reposition()
        w.show_window(self.window, 5)
        w.foreground(self.window)
        w.set_focus(self.controls[2])
        w.update_window(self.window)

    def hide(self):
        if self.window and w.is_visible(self.window):
            w.show_window(self.window, 0)
            self.dismissed_at = time.monotonic()

    def box(self, dc, rect, fill, radius=0, stroke=None):
        brush = w.solid_brush(fill)
        pen = w.create_pen(0, 1, stroke if stroke is not None else fill)
        old_brush, old_pen = w.select_object(dc, brush), w.select_object(dc, pen)
        try:
            w.round_rect(dc, rect.left, rect.top, rect.right, rect.bottom, self.p(radius * 2), self.p(radius * 2))
        finally:
            w.select_object(dc, old_brush)
            w.select_object(dc, old_pen)
            w.delete_object(brush)
            w.delete_object(pen)

    def text(self, dc, text, rect, size=12, weight=400, tone="text", flags=0x24):
        previous = w.select_object(dc, self.fonts[size, weight])
        w.text_color(dc, self.colors[tone])
        w.background_mode(dc, 1)
        try:
            w.draw_text(dc, text, len(text), C.byref(rect), flags | 0x800)  # DT_NOPREFIX: show literal '&'.
        finally:
            w.select_object(dc, previous)

    def paint(self, dc):
        g, colors, snapshot = self.geometry, self.colors, self.snapshot
        w.fill_rect(dc, C.byref(self.rect(0, 0, WIDTH, g["height"])), self.brush)
        w.draw_icon(dc, self.p(14), self.p(19), self.tray.base_icon, self.p(28), self.p(28), 0, None, 3)
        self.text(dc, "AgentTelemetry", self.rect(51, 18, 186, 18), 13, 600)
        status = snapshot["status"]
        title = "Needs attention" if self.warning else "Starting…" if status.startswith("Starting") else "Saving history…" if self.tray.quitting or status.startswith("Stopping") else status
        tone = "warning" if self.warning else "good" if snapshot["requested"] else "muted"
        self.box(dc, self.rect(51, 39, 5, 5), colors[tone], 2)
        self.text(dc, title, self.rect(60, 35, 178, 16), 10, tone=tone)
        self.box(dc, self.rect(*g["card"]), colors["card"], 9, colors["line"])
        self.text(dc, "TODAY", self.rect(26, 70, 250, 14), 10, weight=400, tone="muted")
        offset = 0
        if snapshot["stale"]:
            self.text(dc, "Last refresh; figures may be out of date", self.rect(26, 87, 256, 16), 10, tone="muted")
            offset = 20
        tokens, spend = figures(snapshot)
        self.text(dc, "Tokens used", self.rect(26, 90 + offset, 124, 14), 11, tone="muted")
        self.text(dc, "Estimated spend", self.rect(166, 90 + offset, 118, 14), 11, tone="muted")
        self.text(dc, tokens, self.rect(26, 109 + offset, 124, 28), 20, 600)
        self.text(dc, spend, self.rect(166, 109 + offset, 118, 28), 20, 600)
        self.box(dc, self.rect(153, 94 + offset, 1, 34), colors["line"])
        if self.warning:
            self.text(dc, status, self.rect(14, 60 + g["card"][3] + 14, 282, 34), 11,
                      tone="warning", flags=0x10)  # wrapped, never painted over controls
        self.box(dc, self.rect(14, g["footer"] - 3, 282, 1), colors["line"])

    def paint_settings(self, dc):
        w.fill_rect(dc, C.byref(self.rect(0, 0, 282, self.geometry["settings_height"])), self.brush)
        self.text(dc, "Shared refresh", self.rect(21, 45 - self.scroll, 104, 24), 11, tone="muted")

    def draw_button(self, item):
        identifier, dc, rect = item.id - 100, item.dc, item.rect
        enabled = w.is_enabled(item.window)
        focused = bool(item.state & 0x10)
        pressed = bool(item.state & 1)
        tone = "text" if enabled else "muted"
        self.box(dc, rect, self.colors["bg"])
        if identifier == 2:
            on = self.snapshot["requested"]
            track = self.rect(5, 5, 40, 20)
            self.box(dc, track, self.colors["accent"] if on and enabled else self.colors["line"], 10)
            self.box(dc, self.rect(27 if on else 7, 7, 16, 16), self.colors["on_accent"] if on else self.colors["bg"], 8)
        elif identifier in (5, 6):
            checked = self.tray.settings.get("auto_start", False) if identifier == 5 else self.tray.login_enabled()
            self.box(dc, self.rect(0, 6, 14, 14), self.colors["accent"] if checked else self.colors["bg"], 3, self.colors["line"])
            if checked:
                self.text(dc, "✓", self.rect(1, 4, 13, 18), 12, 600, "on_accent", 0x25)
            title = "Open at login" if identifier == 6 else "Start monitoring automatically"
            self.text(dc, title, self.rect(22, 0, 239, 26), 11, tone=tone)
        elif identifier in (10, 11):
            selected = (self.tray.settings.get("display", "icon") == "icon") == (identifier == 10)
            self.box(dc, rect, self.colors["hover"] if selected else self.colors["card"], 5, self.colors["line"])
            self.text(dc, "Icon" if identifier == 10 else "Numbers", rect, 12, 600 if selected else 400, tone, 0x25)
        elif identifier == 1:
            self.box(dc, rect, self.colors["accent"] if enabled else self.colors["line"], 6)
            self.text(dc, "Open dashboard  ↗", rect, 12, 600, "on_accent" if enabled else "muted", 0x25)
        elif identifier == 9:
            self.text(dc, ("⌄" if self.expanded else "›") + "   Details & settings", rect, 12, 600, tone)
        else:
            if pressed:
                self.box(dc, rect, self.colors["hover"], 5)
            label = self.labels[identifier]
            if identifier == 3:
                label = "↻  Refresh now"
            if identifier == 8 and not self.tray.quitting:
                label = "⏻  Quit AgentTelemetry"
            self.text(dc, label, rect, 11, tone="muted" if identifier in (3, 8) or not enabled else "text")
        if focused:
            inset = W.RECT(rect.left + self.p(2), rect.top + self.p(2), rect.right - self.p(2), rect.bottom - self.p(2))
            w.draw_focus(dc, C.byref(inset))

    def command(self, identifier):
        if identifier == 9:
            self.expanded = not self.expanded
            self.scroll = 0
            self.update(force=True)
        elif identifier in (10, 11):
            self.tray.settings["display"] = "icon" if identifier == 10 else "numbers"
            self.tray.save_settings()
            self.tray.update_icon()
            self.update(force=True)
        elif identifier in (1, 7):
            self.hide()
            self.tray.command(identifier)
        else:
            self.tray.command(identifier)
            self.update(force=True)

    def window_proc(self, window, message, wparam, lparam):
        try:
            if message == 0x318 and hasattr(self, "geometry"):  # WM_PRINTCLIENT for fixture screenshots.
                (self.paint_settings if window == self.panel else self.paint)(wparam)
                return 0
            if message == 0xf and hasattr(self, "geometry"):
                paint = w.PAINTSTRUCT()
                dc = w.begin_paint(window, C.byref(paint))
                try:
                    (self.paint_settings if window == self.panel else self.paint)(dc)
                finally:
                    w.end_paint(window, C.byref(paint))
                return 0
            if message == 0x14:
                return 1  # Paint fills the complete background, avoiding flicker.
            if message == 0x2b:
                item = C.cast(lparam, C.POINTER(w.DRAWITEMSTRUCT)).contents
                if item.type == 3:  # ODT_COMBOBOX
                    self.box(item.dc, item.rect, self.colors["hover"] if item.state & 1 else self.colors["card"])
                    if item.item < len(INTERVALS):
                        rect = W.RECT(item.rect.left + self.p(7), item.rect.top, item.rect.right, item.rect.bottom)
                        self.text(item.dc, INTERVALS[item.item][1], rect, 12)
                else:
                    self.draw_button(item)
                return 1
            if window == self.panel and message in (0x115, 0x20a):
                limit = max(0, 178 - self.geometry["settings_height"])
                if message == 0x20a:
                    delta = C.c_short((wparam >> 16) & 0xffff).value
                    self.scroll -= int(delta / 120) * 24
                else:
                    code = wparam & 0xffff
                    if code in (0, 1, 2, 3):
                        self.scroll += (-1 if code in (0, 2) else 1) * (24 if code in (0, 1) else self.geometry["settings_height"])
                    elif code in (4, 5):
                        info = w.SCROLLINFO(C.sizeof(w.SCROLLINFO), 0x10, 0, 0, 0, 0, 0)
                        w.get_scroll(self.panel, 1, C.byref(info))
                        self.scroll = info.track
                    elif code in (6, 7):
                        self.scroll = 0 if code == 6 else limit
                self.scroll = min(max(0, self.scroll), limit)
                self.place_settings()
                return 0
            if message == 0x111:
                identifier, event = wparam & 0xffff, (wparam >> 16) & 0xffff
                # Native IDs deliberately avoid IDOK/IDCANCEL. Otherwise Escape
                # routes to the monitoring button when it happens to have ID 2.
                if identifier == 2 and not lparam:
                    self.hide()
                    return 0
                if 100 < identifier < 120:
                    identifier -= 100
                if window == self.panel and (event == 6 or (identifier == 20 and event == 3)):
                    # Tab into a clipped setting scrolls it into view.
                    top = {10: 4, 11: 4, 20: 43, 6: 79, 5: 108, 7: 142}.get(identifier)
                    if top is not None:
                        height = self.geometry["settings_height"]
                        if top < self.scroll:
                            self.scroll = top
                        elif top + 28 > self.scroll + height:
                            self.scroll = top + 28 - height
                        self.place_settings()
                    return 0
                if identifier == 20 and event == 1:
                    selected = w.send(self.combo, 0x147, 0, 0)
                    if 0 <= selected < len(INTERVALS):
                        self.tray.submit("interval", INTERVALS[selected][0])
                        self.update(force=True)
                elif identifier in self.controls and event == 0:
                    self.command(identifier)
                return 0
            if message == 6 and wparam & 0xffff == 0 and window == self.window:
                # Owned dropdown windows do not count as leaving the popover.
                if not lparam or w.get_ancestor(lparam, 3) != self.window:
                    self.hide()
                return 0
            if message == 0x10:
                self.hide()
                return 0
            if message == 0x2e0 and self.window:
                self.scale = (wparam & 0xffff) / 96
                self._fonts()
                self.update(force=True)
                return 0
            if message in (0x1a, 0x31a) and hasattr(self, "brush"):
                self.colors = palette(self.dark)
                w.delete_object(self.brush)
                self.brush = w.solid_brush(self.colors["bg"])
                self.update(force=True)
                return 0
            if message in (0x133, 0x134, 0x138) and hasattr(self, "brush"):
                w.text_color(wparam, self.colors["text"])
                w.background_color(wparam, self.colors["bg"])
                return self.brush
        except Exception as error:
            self.error = error
            traceback.print_exc()
            # Avoid opening a recursive error dialog from inside WM_PAINT.
            if self.window and not self.tray.self_test:
                w.post(self.tray.window, 0x8003, 0, 0)
        return w.default_proc(window, message, wparam, lparam)

    def close(self):
        if self.window:
            w.destroy_window(self.window)
            self.window = None
        for font in self.fonts.values():
            w.delete_object(font)
        self.fonts.clear()
        w.delete_object(self.brush)
