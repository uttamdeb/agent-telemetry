"""Tray presentation and positioning, independent of Win32 and personal data."""
import datetime

WIDTH = 310  # Same logical width and content order as macOS StatusMenu.
INTERVALS = ((15, "15 sec"), (60, "1 min"), (300, "5 min"), (900, "15 min"), (0, "Manual"))


def compact(value):
    if value >= 1000000:
        return "%.1fM" % (value / 1000000)
    if value >= 1000:
        return "%.1fk" % (value / 1000)
    return format(value, ",")


def figures(snapshot, today=None):
    today = today or datetime.date.today().isoformat()
    if snapshot["date"] != today:
        return "—", "—"
    return compact(snapshot["tokens"]), "$%.2f" % snapshot["spend"]


def layout(expanded=False, stale=False, warning=False, max_height=None):
    """Logical pixels; footer stays outside the expandable settings region."""
    card_height = 102 if stale else 82
    details = 60 + card_height + 8 + (66 if warning else 0)
    settings = details + 32
    viewport = 178 if expanded else 0
    if max_height is not None:
        viewport = min(viewport, max(0, max_height - settings - 86))
    dashboard = settings + viewport + 8
    return {"card": (14, 60, 282, card_height), "details": details,
            "settings": settings, "dashboard": dashboard,
            "settings_height": viewport, "footer": dashboard + 38, "height": dashboard + 78}


def position(anchor, work, size, gap=8):
    """Anchor above/below the tray, clamped to this monitor's work area."""
    left, top, right, bottom = work
    width, height = size
    x = min(max(anchor[2] - width, left), max(left, right - width))
    y = anchor[1] - height - gap
    if y < top:
        y = anchor[3] + gap
    y = min(max(y, top), max(top, bottom - height))
    return x, y
