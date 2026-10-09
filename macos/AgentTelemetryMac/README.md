# AgentTelemetry for macOS

This is a SwiftUI menu-bar wrapper for the existing local AgentTelemetry dashboard. It starts the same Python standard-library server, keeps the dashboard in a `WKWebView`, and stores its cache under `~/Library/Application Support/AgentTelemetry`.

## Build a local app

Requirements: macOS 13 or later, Swift 5.8 or later, and a macOS SDK available through `xcrun` (the Xcode Command Line Tools SDK is enough for local builds; full Xcode is recommended for distribution).

From the repository root, run:

```sh
macos/AgentTelemetryMac/Scripts/package-app.sh
```

The script compiles for the current Mac architecture, downloads a pinned standalone CPython runtime, verifies its SHA-256, bundles only the dashboard source/assets it needs, and creates `macos/AgentTelemetryMac/build/AgentTelemetry-macos-<architecture>.dmg`. To use a previously downloaded matching Python archive, set `AGENT_TELEMETRY_PYTHON_ARCHIVE` to its path. The disk image contains an `Applications` shortcut for drag-to-install. Local builds use ad-hoc signing and are intended for development.

The bundled interpreter comes from Astral's `python-build-standalone` release `20261003` (CPython 3.13.16). The package script pins separate Apple Silicon and Intel archive hashes in source. The archive is an install-only, stripped runtime; the app removes its unused `pip`, `idle`, and `pydoc` launchers and includes CPython's license file and a `BUILD-INFO.txt` with the source and checksum. The Python backend uses no third-party packages.

## Signed distribution

Set `AGENT_TELEMETRY_SIGN_IDENTITY` to a Developer ID Application signing identity. For notarization, first store credentials in the macOS Keychain with `xcrun notarytool store-credentials`, then set `AGENT_TELEMETRY_NOTARY_PROFILE` to that profile name before running the package script. The script signs nested Mach-O files before the app, submits the DMG, waits for notarization, and staples the result. Do not put signing passwords, API keys, or certificates in the repository.

Build and notarize each architecture on a matching Mac. The current package script emits architecture-specific images rather than a universal binary.

## Data and privacy

- The app reads the same supported local coding-tool logs as the dashboard; source logs stay where the tools wrote them.
- The Python service binds only to `127.0.0.1` and writes its durable cache in Application Support.
- The menu bar can show either a status icon or today's compact token and estimated-spend figures. The popover shows the labeled totals and monitoring state.
- Automatic monitoring and Open at Login are off by default. The dashboard's device-sharing feature stays off unless the user enables it there.
- **Import existing data…** copies `.usage_cache.json` and optional device-sharing state from a selected folder. It validates the JSON structure, stages a private copy, and leaves the source files untouched. Import is disabled while the local service is running and never overwrites data already in the app's support folder.
- The app bundle is built from an explicit source/assets allowlist. The packaging script also refuses to continue if it finds a user cache, peer state, or `server.log` in the resources it is about to ship.

## Local development

Run the web dashboard with `python3 dashboard.py` from the repository root for the existing standalone workflow. Build the native shell with `swift build --package-path macos/AgentTelemetryMac`. To create a DMG, use the package script above. The menu-bar app chooses icon or numbers in **Details & settings**; the five-minute default reduces background polling, and its embedded dashboard pauses polling while its window is hidden.
