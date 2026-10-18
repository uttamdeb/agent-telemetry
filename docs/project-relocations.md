# Local project names and folder moves

Folder moves do not change recorded historical working directories or archived
project labels. AgentTelemetry can keep a private presentation mapping in
`project-names.json` inside its **AgentTelemetryNative** support directory.

The format is version 1 with `aliases` (old project label to current label) and
`roots` (old absolute folder path to new absolute folder path). Paths are matched
at component boundaries, including nested working directories. Invalid, cyclic,
unknown or oversized settings are preserved and reported; recorded names remain
available. Use `native.set_project_preferences(mapping)` to validate and save
local preferences. This function writes preferences only; it never moves folders.

Display aliases group current and historical labels across dashboard views,
MCP analytics, Storage and editor projections. Existing project filters in browser
bookmarks are canonicalized to the current display label. Directory mappings apply
only to this machine's recorded working directories, so editor workspace matching
can follow an explicitly moved folder without relying on project basenames. Peer
working directories are never treated as local folders.

The durable ledger, archived entries, source log paths, source/native session IDs,
titles, parent relationships, token counts, pricing, costs and peer exports are
unchanged. Preferences are not included in exports or release assets. No cache
version bump or rebuild is required. Original tool transcripts retain their recorded
paths; these settings do not rewrite history or relocate an original tool's logs.

For an actual folder move, first back up the ledger and mutable configuration,
stop the dashboard gracefully, rename the directory, update active launch/MCP and
workspace configuration, repair Git worktree metadata, and restart against the same
ledger. Compatibility symlinks can keep currently open editors and processes working
until they are reopened at the new path. Moving a data directory changes MCP's
directory binding: preserve the user's existing scopes by confirming access again
for the relocated ledger, then reconnect and verify the client.
