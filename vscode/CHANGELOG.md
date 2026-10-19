# Changelog

## 2.1.0

- Include the latest AgentTelemetry backend, session activity and release notes in the Agent desk.
- Show recorded Copilot child sessions in workspace views and retain accurate parent and usage relationships.
- Update the shared app and MCP bridge to AgentTelemetry 2.1.0.
- Publish the universal desktop extension through Microsoft Marketplace and Open VSX.

## 2.0.2

- Attribute Copilot SDK sessions to their recorded workspace instead of excluding them from the workspace brief.
- Show named Copilot SDK subagents linked to their parent, with recorded usage and compactions. Child usage remains an inclusive breakdown and is never added to overall totals again.
- Show unavailable and partial usage explicitly; preserve retained history during the optional metadata backfill.

## 2.0.1

- Update the bundled shared backend with the optional read-only MCP analytics bridge and its dashboard Settings controls. MCP stays disabled until explicitly enabled, with separate scopes for display names and paired-device data.
- Include all MCP runtime modules and frontend assets in the verified backend allowlist; retain the app's 2.0.0 version, ledger history and existing Agent desk behavior.
- Document MCP setup, revocation, privacy, service upgrade requirements and the distinction between extension and application versions.
- Generate the VSIX filename from its manifest version so packaging and CI stay aligned on future updates.

## 2.0.0

Initial desktop editor extension package.

- Add the Agent desk with a workspace-first usage brief, Today/7-day/30-day ranges and input/output/cache composition.
- Match workspaces by recorded absolute directory containment and expose history without attribution through All local projects.
- Add a keyboard-operable delegation outline, All sessions/Main agents/Subagents filters, recorded parent navigation, and scoped model, effort and compaction details.
- Measure newly recorded usage against an in-memory baseline, with explicit reset and scope-change behavior.
- Add a native activity sidebar, status bar, refresh controls and access to the full local dashboard.
- Attach to an identified local backend or explicitly start the bundled stdlib-only backend with Python 3.9+, including supported installed-runtime reuse.
- Keep the shared backend running after the editor closes; use the local UI extension host in remote windows and require workspace trust before starting Python.
- Preserve stale snapshots during connection failures and disclose missing metadata, bounded session lists and estimated API costs.
- Reuse AgentTelemetry's icon and app theme, with high-contrast, keyboard-focus and reduced-motion support.
- Restrict the webview to bundled resources and a validated host message allowlist. No account lookup, provider calls or analytics uploads.

This package targets desktop VS Code-compatible editors. Browser-only editors are unsupported.
