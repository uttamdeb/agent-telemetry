# GitHub release draft: AgentTelemetry for macOS 0.1.0

**Proposed tag:** `macos-v0.1.0`

**Status:** Hold for owner-authored commit, Developer ID signing, and notarization.

**Local preview:** `macos/AgentTelemetryMac/build/release-candidate/AgentTelemetry-macos-arm64.dmg` is ad-hoc signed and must not be uploaded as the public installer.

## Release description

AgentTelemetry for macOS brings the existing local dashboard to the menu bar. Start and stop monitoring, see today's token total and estimated spend, and open the full dashboard in a native window.

### What's included

- Choose a compact token-and-spend display or a status icon in the menu bar.
- See monitoring state and today's totals in the menu popover.
- Open the existing dashboard in an app window.
- Choose a refresh interval, optionally start monitoring automatically, or open the app at login.
- Import an existing AgentTelemetry cache without removing or changing the original.
- Use the bundled Python runtime; a separate Python installation is not required.

### Install

Download the disk image for your Mac (`arm64` for Apple silicon or `x86_64` for Intel), open it, and drag AgentTelemetry to Applications. Requires macOS 13 or later.

### Privacy

The app reads supported coding-tool logs locally and stores its cache in Application Support. Monitoring at launch, Open at Login, and device sharing are off unless enabled by the user.

## Release checklist

- Have the repository owner commit and push the reviewed source changes. The repository's `AGENTS.md` reserves commit authorship to the owner.
- Build the tagged source with `macos/AgentTelemetryMac/Scripts/package-app.sh` on a matching Mac for each architecture.
- Sign with a Developer ID Application identity, notarize the app and disk image, and staple the notarization ticket. The current build Mac has no valid Developer ID identity, so it cannot produce the public installer yet.
- Verify the signed app and stapled image on a clean macOS account, including first launch, Start/Stop, dashboard opening, and preserving existing Application Support data.
- Generate SHA-256 checksums for the final DMGs and attach them to the GitHub draft release.
- Publish only after the release assets pass the checks above.
