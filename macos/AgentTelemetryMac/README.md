# AgentTelemetry for macOS

Part of AgentTelemetry v2.0.0. From the repository root, `python3 install.py` detects
macOS, builds and installs `~/Applications/AgentTelemetry.app`, then opens it.
`--no-launch` installs without opening; `--bundle PATH` installs a built app instead
of rebuilding. Existing ledgers are never removed or overwritten by installation.

Open the app from Finder or use **web Settings → Menu bar / system tray → Menu bar app**.
The toggle launches/quits the installed app and reports a real heartbeat. Open at Login
and automatic monitoring remain separate, off-by-default preferences. Installer/web
launch starts monitoring for that launch. An app opened manually offers Start monitoring.
Dragging a built app into Applications also works; opening it registers it for the toggle.

This is a SwiftUI menu-bar wrapper for the existing local AgentTelemetry dashboard. It starts the same Python standard-library server, keeps the dashboard in a `WKWebView`, and stores its cache under `~/Library/Application Support/AgentTelemetry`.

## Build a local app

Requirements: macOS 13 or later, Swift 5.8 or later, and a macOS SDK available through `xcrun` (the Xcode Command Line Tools SDK is enough for local builds; full Xcode is recommended for distribution).

From the repository root, run:

```sh
macos/AgentTelemetryMac/Scripts/package-app.sh
```

The script compiles for the current Mac architecture, downloads a pinned standalone CPython runtime, verifies its SHA-256, bundles only the dashboard source/assets it needs, and creates `macos/AgentTelemetryMac/build/AgentTelemetry-macos-<architecture>.dmg`. To use a previously downloaded matching Python archive, set `AGENT_TELEMETRY_PYTHON_ARCHIVE` to its path. `AGENT_TELEMETRY_SDK_PATH` can select another installed SDK. The disk image contains an `Applications` shortcut for drag-to-install. Local builds use ad-hoc signing and are intended for development. This source release does not supply a notarized public installer.

The bundled interpreter comes from Astral's `python-build-standalone` release `20261003` (CPython 3.13.16). The package script pins separate Apple Silicon and Intel archive hashes in source. The archive is an install-only, stripped runtime; the app removes its unused `pip`, `idle`, and `pydoc` launchers and includes CPython's license file and a `BUILD-INFO.txt` with the source and checksum. The Python backend uses no third-party packages.

## Signed distribution

Set `AGENT_TELEMETRY_SIGN_IDENTITY` to a Developer ID Application signing identity. For notarization, first store credentials in the macOS Keychain with `xcrun notarytool store-credentials`, then set `AGENT_TELEMETRY_NOTARY_PROFILE` to that profile name before running the package script. The script signs nested Mach-O files before the app, submits the DMG, waits for notarization, and staples the result. Do not put signing passwords, API keys, or certificates in the repository.

Build and notarize each architecture on a matching Mac. The current package script emits architecture-specific images rather than a universal binary.

## Data and privacy

- The app reads the same supported local coding-tool logs as the dashboard; source logs stay where the tools wrote them.
- The Python service binds only to `127.0.0.1` and writes its durable cache in Application Support.
- It attaches to an existing port-7878 dashboard without taking ownership. Quit/Stop leaves that service running. A custom dashboard port is not supported by this client.
- Control/heartbeat files are in `~/Library/Application Support/AgentTelemetryNative`, separately from the ledger, so they do not prevent importing into an empty data folder.
- The menu bar can show either a status icon or today's compact token and estimated-spend figures. The popover shows the labeled totals and monitoring state.
- Automatic monitoring and Open at Login are off by default. The dashboard's device-sharing feature stays off unless the user enables it there.
- **Import existing data…** copies `.usage_cache.json` and optional device-sharing state from a selected folder. It validates the JSON structure, stages a private copy, and leaves the source files untouched. Import is disabled while the local service is running and never overwrites data already in the app's support folder.
- The app bundle is built from an explicit source/assets allowlist. The packaging script also refuses to continue if it finds a user cache, peer state, or `server.log` in the resources it is about to ship.
- Failed refreshes keep the last successful summary, visibly mark it stale and retry; a late startup result cannot undo Stop. Menu figures render the incoming published value. Polling changes and owned restarts preserve dashboard filters and tab.
- Stop/Quit waits for an owned backend to finish parsing and save the ledger; it does not force-kill a long parse after a fixed deadline.
- Changing cadence during an outage keeps retrying. The closing app continues its heartbeat until saving completes, preventing a competing web launch.

## Local development

Run the web dashboard with `python3 dashboard.py` from the repository root for the existing standalone workflow. Build the native shell with `swift build --package-path macos/AgentTelemetryMac`. To create a DMG, use the package script above. The menu-bar app chooses icon or numbers in **Details & settings**; the five-minute default reduces background polling, and its embedded dashboard pauses polling while its window is hidden.

Run `python3 tests/run_mac_native.py` from the repo root for isolated native display,
failure/recovery, cancellation and navigation regression checks. Run the stdlib suite
and frontend checks too. Signing traversal uses NUL-delimited names and signs every
nested Mach-O file before the container; Developer ID/notarization needs real credentials
and is not proven by an ad-hoc local package.
