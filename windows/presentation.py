"""Tray presentation and positioning, independent of Win32 and personal data."""
import datetime
import math

WIDTH = 310  # Same logical width and content order as macOS StatusMenu.
INTERVALS = ((15, "15 sec"), (60, "1 min"), (300, "5 min"), (900, "15 min"), (0, "Manual"))
DEFAULT_LIST_HEIGHT = 138
SETTINGS_FORM_HEIGHT = 183


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


def _utc(value):
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=datetime.timezone.utc)
        return parsed.astimezone(datetime.timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def recent_sessions(snapshot, now=None):
    """Return at most three recent local sessions, aging them out without I/O."""
    activity = snapshot.get("activity") or {}
    rows = activity.get("sessions") if isinstance(activity, dict) else None
    window = activity.get("window_seconds", 900) if isinstance(activity, dict) else 900
    if type(window) is not int or not 1 <= window <= 3600 or not isinstance(rows, list):
        return []
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif isinstance(now, str):
        now = _utc(now)
    if not isinstance(now, datetime.datetime):
        return []
    if now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    now = now.astimezone(datetime.timezone.utc)
    cutoff = now - datetime.timedelta(seconds=window)
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        activity_at = _utc(row.get("last_activity_at"))
        if activity_at is None or activity_at < cutoff or activity_at > now:
            continue
        title = row.get("title")
        tool = row.get("tool")
        tokens = row.get("tokens")
        if not isinstance(title, str) or not isinstance(tool, str):
            continue
        if tokens is not None and (type(tokens) is not int or tokens < 0):
            tokens = None
        provider = row.get("provider")
        if not isinstance(provider, str) or not provider.strip():
            provider = None
        minutes = max(0, math.floor((now - activity_at).total_seconds() / 60))
        age = "Just now" if minutes == 0 else "%d min ago" % minutes
        result.append(dict(row, title=title, tool=tool, provider=provider,
                           tokens=tokens, age=age, _activity_at=activity_at))
    result.sort(key=lambda row: row["_activity_at"], reverse=True)
    return result[:3]


def local_servers(snapshot):
    """Project the visible pinned or running rows; addresses never gain a scheme."""
    value = snapshot.get("servers") or {}
    rows = value.get("servers") if isinstance(value, dict) else None
    if not isinstance(rows, list):
        return []
    selected = []
    for row in rows:
        if not isinstance(row, dict) or (row.get("state") != "running" and row.get("isPinned") is not True):
            continue
        title = row.get("title") or row.get("project") or row.get("process") or "Local server"
        host, port = row.get("host"), row.get("port")
        if not isinstance(title, str) or not isinstance(host, str) or type(port) is not int:
            continue
        if not 1 <= port <= 65535:
            continue
        host = host.strip()
        if not host or host in ("0.0.0.0", "::", "*", "[::]"):
            host = "localhost"
        address_host = "[%s]" % host if ":" in host and not host.startswith("[") else host
        selected.append({"title": " ".join(title.split())[:100] or "Local server",
                         "state": row["state"], "host": host[:255], "port": port,
                         "isPinned": row.get("isPinned") is True,
                         "address": "%s:%d" % (address_host, port)})
    selected.sort(key=lambda row: (row["isPinned"], row["state"] == "running"), reverse=True)
    return selected[:3]


def layout(expanded=False, stale=False, warning=False, max_height=None, list_height=DEFAULT_LIST_HEIGHT):
    """Logical pixels; footer stays outside the expandable settings region."""
    card_height = 102 if stale else 82
    details = 60 + card_height + 8 + (66 if warning else 0)
    settings = details + 32
    list_height = max(90, min(420, int(list_height)))
    content_height = list_height + (8 + SETTINGS_FORM_HEIGHT if expanded else 0)
    viewport = content_height
    if max_height is not None:
        viewport = min(content_height, max(0, max_height - settings - 86))
    dashboard = settings + viewport + 8
    return {"card": (14, 60, 282, card_height), "details": details,
            "settings": settings, "dashboard": dashboard,
            "settings_height": viewport, "settings_content_height": content_height,
            "list_height": list_height,
            "settings_start": list_height + 8 if expanded else None,
            "footer": dashboard + 38, "height": dashboard + 78}


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
