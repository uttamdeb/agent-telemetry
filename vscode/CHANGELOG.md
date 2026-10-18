# Changelog

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
