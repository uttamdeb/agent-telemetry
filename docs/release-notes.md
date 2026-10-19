# Release notes and update checks

Release notes are fetched from GitHub only after a user clicks **Check for updates**.
The dashboard does not contact GitHub during startup or background refresh. It keeps
bounded release metadata in the private `AgentTelemetryNative/release-notes.json`
preferences file, separate from the usage ledger and its backups. `GET /api/release-notes`
reads that cache offline.

The first open records the current stable application version as the installation's
baseline. That avoids presenting an old release as a fresh upgrade when the feature is
first introduced. Each canonical application path has its own baseline, so a separately
located `uvx` or versioned install can be treated as a new installation. A later stable
version can be claimed for display once. The UI
acknowledges the claim only after the notes are shown; if display fails or the page
closes first, the short claim expires and a later open can retry. Refreshing assets or
reinstalling the same version does not create another popup. If notes were not cached
before an upgrade, the popup's **Load notes** action explicitly contacts GitHub; until
then the offline view can show that notes are unavailable.

The API's read route is origin-checked and loopback-only. Its JSON write route uses the
same-origin CSRF guard and accepts only the `claim`, `shown`, and explicit `check`
actions. A `check` fetches only GitHub's latest published stable release and the exact
tag matching the running version, with bounded response sizes and timeouts. The latest
notes are cached for offline display after a version upgrade. Release text is treated
as text by the UI; the dashboard does not interpret it as HTML.

The existing Git update flow remains available to source checkouts. It fast-forwards
only when local edits and unpublished commits do not block the update, preserves the
ledger before applying, and restarts after saving. Packaged macOS and Windows copies
can check release metadata, but installation is a manual download from the official
[GitHub Releases page](https://github.com/uttamdeb/agent-telemetry/releases/latest).
Release notes do not change the ledger schema, clear archived history, or require a
cache rebuild.
