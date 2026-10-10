# AgentTelemetry for Windows

The v2.0 native notification-area app uses Python's standard library (`ctypes`) and
Windows APIs. It needs Windows 10/11 and a standard Python 3.8+ installation containing
`pythonw.exe`; there are no pip packages, Electron runtime or additional GUI toolkit.

## Install and open

From the checkout, run `python install.py`. It detects Windows, copies an explicit
app/backend allowlist to `%LOCALAPPDATA%\Programs\AgentTelemetry`, creates a Start-menu
shortcut and launches the tray app. `--no-launch` leaves it closed. Later, use the
**AgentTelemetry** Start-menu shortcut or **web Settings → Menu bar / system tray →
System tray app**. If Windows hides the icon in the overflow area, open the taskbar's
hidden-icons arrow or choose to show AgentTelemetry in taskbar settings.

Click either mouse button on its icon for the menu: today's tokens and estimated API
spend, monitoring status, Open dashboard, Start/Stop, Refresh, icon/numbers display,
1/5/15-minute refresh cadence, automatic monitoring, Open at Login, Import and Quit.
Open dashboard waits for backend readiness before opening the default browser; Stop
cancels a pending open. Numbers also have text labels in the
menu and tooltip; colour is never the only source of information.

Open at Login writes only the current user's `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
AgentTelemetry entry, and stays off until selected. Automatic monitoring is also off
by default. An installer/web-toggle launch starts monitoring for that launch.

## Data and process ownership

The client attaches to a dashboard already on `127.0.0.1:7878` (older releases included)
or starts its own backend. Custom dashboard ports are not supported by the client.
It never stops an attached service. Its own backend stores the durable ledger, peer
files and local parser diagnostics in `%LOCALAPPDATA%\AgentTelemetry`; controls,
heartbeat, tray preferences and GUI error log are separate in `%LOCALAPPDATA%\AgentTelemetryNative`.

Import existing data before first app-owned monitoring to preserve history whose logs
were deleted. Import requires no service on port 7878 and an empty destination. It
validates the ledger/sharing state, stages copies, refuses symlinks/overwrites, and leaves
the original files untouched. Installation and app settings never delete the ledger.

Failures retain and label the last successful figures; polling retries recovery.
Stop/Quit requests an authenticated, CSRF-guarded graceful shutdown only of its owned
process and waits for the ledger to flush. It does not force-kill a slow save. If a save
is still finishing, the menu reports it and a later Stop/Quit can retry.
Queued work honors the newest Start/Stop request. Closing keeps its heartbeat alive
while saving, so the web toggle cannot launch another instance in the middle of shutdown.

## Development and tests

Run `python windows/tray.py` for the source client. Run `python windows/tray.py --self-test`
to exercise real Win32 window, GDI icon and native menu creation; it prints whether
Explorer accepted the icon, then removes test UI. Run `python -m unittest discover -s
tests -v` and `node tests/test_frontend.js` for the shared logic. CI also runs the installer
with `--no-launch` on a Windows runner. Automated tests use mock/temporary data, never
the user's live ledger.
