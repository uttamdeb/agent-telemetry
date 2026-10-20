# AgentTelemetry for Windows

The native notification-area app uses Python's standard library (`ctypes`) and
Windows APIs. The [Windows x64 EXE installer in GitHub Releases](https://github.com/uttamdeb/agent-telemetry/releases/latest)
includes Python and supports Windows 10/11; no separate Python installation is needed.
There are no pip packages, Electron runtime or additional GUI toolkit. The EXE is
unsigned, so SmartScreen may show a first-run warning (**More info → Run anyway**).

## Install and open

Run the EXE installer, then use the **AgentTelemetry** Start-menu shortcut. Installation
is for the current user and needs no administrator permission. Quit the app and wait
for its ledger save before upgrading/uninstalling; the installer refuses a live client.
Uninstall removes application files and its shortcut, while retaining usage history and
preferences. The installer creates no login-startup entry unless preserving a previous
AgentTelemetry opt-in; it never changes another application's entry.

To import archived history from a source checkout, uncheck **Open AgentTelemetry** on
the installer's final page, open from Start and use **Import existing data** before
Start monitoring. This keeps the import destination empty.

For source installation, use standard Python 3.8+ with `pythonw.exe`, then run
`python install.py` from the checkout. It detects Windows, copies an explicit
app/backend allowlist to `%LOCALAPPDATA%\Programs\AgentTelemetry`, creates a Start-menu
shortcut and launches the tray app. `--no-launch` leaves it closed. Later, use the
**AgentTelemetry** Start-menu shortcut or **web Settings → Menu bar / system tray →
System tray app**. If Windows hides the icon in the overflow area, open the taskbar's
hidden-icons arrow or choose to show AgentTelemetry in taskbar settings.

To update an EXE installation, Quit AgentTelemetry, wait for saving and run the new EXE.
The source installer refuses to replace a bundled installation so its runtime and
uninstaller remain intact. To update a source installation, Quit AgentTelemetry and let
any owned backend finish saving, then run `python install.py` from the updated checkout. The installer
replaces only application files; usage history and tray preferences stay in place.

Click either mouse button on the AgentTelemetry app icon for a native popover matching the
macOS layout: a monitoring switch, Today token/spend cards, **Details & settings**,
Open dashboard, Refresh and Quit. Expand settings for icon/numbers display, shared
15-second/1/5/15-minute or Manual cadence, automatic monitoring, Open at Login and Import.
The panel uses the system light/dark or high-contrast theme and monitor DPI. Settings
scroll on smaller displays while the dashboard/refresh/quit controls stay visible.
Tab moves through native controls, Space/Enter activates them, and Escape or clicking
elsewhere dismisses the popover. The Start-menu shortcut uses the app's own icon.
Web Settings and the tray update the same interval. A manual refresh in
either app updates the others within about a second plus request time; all figures use
the same snapshot. Native totals represent unfiltered Today, including connected devices.
Changing cadence does not restart an owned backend; its log parsing still defaults to 20s.
Open dashboard waits for backend readiness before opening the default browser; Stop
cancels a pending open. Numbers also have text labels in the
popover and tooltip; colour is never the only source of information. Windows uses
Segoe UI and your default browser for the full dashboard; macOS uses SwiftUI and an
embedded dashboard. The tray popovers share the same controls and content order.

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
is still finishing, the popover reports it and a later Stop/Quit can retry.
Queued work honors the newest Start/Stop request. Closing keeps its heartbeat alive
while saving, so the web toggle cannot launch another instance in the middle of shutdown.

## Development and tests

Run `python windows/tray.py` for the source client. Run `python windows/tray.py --self-test`
to exercise real Win32 windows and branded/numeric icon handles; it prints whether
Explorer accepted the icon, then removes test UI. Run
`python tests/run_windows_native.py --screenshots windows-ui-fixtures` to exercise actual
popover controls and generate mock-data PNGs in both themes at 100/150/200% scaling.
Inspect these render fixtures; successful handle creation alone cannot establish visual
quality. Run `python -m unittest discover -s
tests -v` and `node tests/test_frontend.js` for the shared logic. CI also runs the installer
with `--no-launch` on a Windows runner. Automated tests use mock/temporary data, never
the user's live ledger.

## Build a download package

`windows/installer/build.py` uses Inno Setup (build-time only) and a checksum-pinned
CPython embeddable ZIP from python.org. It takes a clean checkout at the requested tag,
stages the existing installer allowlist, and produces a per-user, offline EXE. The
bundled runtime is isolated from global/user Python imports. Native registration lives
outside the ledger. `.github/workflows/release-downloads.yml` builds from an existing
release tag and runs `tests/run_windows_installer.py`: real install, packaged Win32 UI,
running-client refusal, upgrade and uninstall against invented archived data. It only
uploads CI artifacts; publishing release assets is a separate explicit action.

The **Local servers** button opens `#local-servers` in the browser once the backend
is ready. Windows can discover your TCP listeners and configure, pin, start, stop
or restart them there. Configuration asks for a working folder; Stop ends the
selected process immediately after confirmation. See [local server controls](../docs/local-servers.md).
