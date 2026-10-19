# Local MCP access

AgentTelemetry can expose selected analytics through a read-only Model Context Protocol (MCP) server. The MCP bridge uses stdio with the client and calls only the already-running dashboard over loopback. It does not start the dashboard, scan or rebuild the ledger, refresh an account, launch a saved command, edit a client configuration, or write telemetry.

MCP access is off by default. Enable **Local MCP access** in AgentTelemetry Settings and confirm the scope there. The switches are separate: one enables access, one permits project and device display names, and one permits data from other connected devices. With names disabled, project and device labels are opaque aliases. Tool arguments cannot expand those scopes. Disabling access revokes the local credential; it cannot retract context already received by an MCP client.

The generated `mcpServers` JSON snippet is for clients whose settings accept that configuration format. Other clients must use the displayed command and arguments in their own configuration format. The snippet contains the local Python executable, bridge script and loopback dashboard address, but no bearer key; AgentTelemetry keeps that key in its private application-support directory, outside the usage ledger. AgentTelemetry never writes to another application's configuration. Start the dashboard explicitly, then start or reconnect the MCP client. The MCP client launches `mcp_server.py` as a stdio subprocess; modern clients use `server/discover` and legacy clients use `initialize` followed by `notifications/initialized`. `tools/list` discovers the tools. The bridge does not start a dashboard for you.

**A local bridge does not make a cloud-backed client local.** When the client requests a tool, AgentTelemetry returns the selected result to that client. The client may transmit tool results and surrounding conversation to its model provider according to the client's own settings and policies. Review those settings before enabling access. MCP labels and findings are untrusted telemetry data, not instructions to execute.

This is one of several distinct network paths. The dashboard does not check for updates on a timer: Git update checks and release-note loads contact GitHub only after a user click. **Your devices** sharing is separately enabled and sends the selected export to a paired device on the local network. Account lookups require a separate confirmed **Read once** action and contact a provider. An explicitly started saved local-server command can use its own network access. These flows have separate controls; MCP consent does not enable them.

## Available tools

All tools are read-only. Date windows are inclusive; omit the range to use today. Relative periods are `today`, `last_7_days`, `last_30_days`, `month_to_date` and `all`. A custom range uses both `from` and `to` in `YYYY-MM-DD` form and may span up to 3,660 days. Filters are flat lists: `tools`, `models`, `providers`, `projects`, `ides` and `devices`. Use labels from `get_catalog`; when names are disabled, project and device values are opaque aliases that can still be used as filters. `exact_only` excludes estimated components where the source supports exact attribution. Missing fields and retained-detail limits stay unavailable rather than being inferred as zero; results include coverage and warnings where applicable. Pagination and result limits keep responses bounded.

| Tool | Purpose and limits |
| --- | --- |
| `get_usage` | Return totals and optional breakdowns by date, project, tool, model, provider, IDE or device. Set `group_by` to an empty list for totals only. Rows are paginated, up to 500 per request. |
| `compare_usage` | Compare a current inclusive range with an explicit baseline or the immediately preceding range of equal length. Select `tokens`, `cost`, `messages` or `time` and one contribution dimension: project, tool, model, provider, IDE, device or token type. Signed contributions include decreases and reconcile to the total when all rows (including `Other`, when paginated) are included. `all` history needs an explicit baseline. A zero baseline has no percentage-growth value. This is accounting context, not a causal explanation. |
| `get_insights` | Return supported Optimize findings for the selected range and filters, plus explicit unsupported reasons when required data or attribution is missing. Findings may overlap, are heuristic, and are not guaranteed savings; do not add their estimates together. At most 100 findings are returned. |
| `list_sessions` | Return bounded session summaries and an opaque `session-…` selector for timeline lookup. Titles and native session IDs are omitted. A session without enough retained daily detail may be excluded from a partial date range and called out in coverage. |
| `get_session_timeline` | Read event kinds, timestamps, tool/model labels and numeric usage for a selector returned by `list_sessions`. Optional `from` and `to` dates must be supplied together. Prompts, commands, arguments, paths and native session IDs are not returned. Available detail depends on what the source log retained; deleted or unsupported sources can have limited or unavailable timelines. |
| `get_data_health` | Read aggregate source-health categories and statuses. It omits log paths, exception text, prompts and credentials. |
| `get_catalog` | List labels represented in a selected range, normally on the local device. Names follow the separate name and peer scopes. Each catalog dimension is capped at 500 items and reports truncation. |
| `get_subscription_limits` | Read recent locally saved Codex or Claude quota observations. This is offline: it does not run a CLI lookup, refresh credentials, or return account identity. Values can be stale, unverified or absent. |

### Example queries

These are `arguments` for the named MCP tool. Group the last 7 days by date, project and tool:

```json
{
  "period": "last_7_days",
  "group_by": ["date", "project", "tool"],
  "limit": 50
}
```

Compare token contributions by project for an inclusive date range:

```json
{
  "from": "2026-10-05",
  "to": "2026-10-11",
  "metric": "tokens",
  "dimension": "project",
  "limit": 100
}
```

Read supported findings for one tool:

```json
{
  "period": "month_to_date",
  "tools": ["codex"],
  "limit": 50
}
```

Use `list_sessions` first, then pass only one returned opaque selector to `get_session_timeline`. Never substitute a native session ID.

## Protocol and dependencies

The bridge supports MCP protocol versions `2026-07-28`, `2025-11-25` and `2025-06-18`. It uses stdio only. The dashboard's authenticated `/api/mcp/query` route is a private loopback backend for the bridge, not a Streamable HTTP MCP endpoint; do not configure it as a remote MCP server. Stdio carries one newline-delimited JSON-RPC message per line; JSON-RPC batch arrays are not accepted.

The Python wheel and source distribution include the bridge modules and Settings' MCP client assets. In a package install, `python -m agent_telemetry.mcp_server --help` and `--version` inspect the bridge without starting it or creating user files. Settings still generates the direct bridge command for the active Python executable; start the dashboard explicitly before connecting the MCP client.

Runtime code uses Python's standard library and does not require Node.js or an MCP SDK. CI installs the official JavaScript MCP SDK `1.30.1` into a temporary directory for an interoperability smoke test only; it is not bundled or installed with AgentTelemetry. That SDK v1 smoke test exercises the legacy `2025-11-25` handshake. The modern `2026-07-28` `server/discover` path is covered by the Python protocol tests.

For protocol details, see the [MCP 2026-07-28 specification](https://modelcontextprotocol.io/specification/2026-07-28), its [stdio and Streamable HTTP transport bindings](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports), [tools](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), and the [legacy 2025-11-25 lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

The tool schemas and guide resource are served by the local bridge. Query failures report a bounded, safe message rather than backend exception text. If the bridge cannot connect, check that AgentTelemetry is running and that MCP access remains enabled for the same data directory and port.

## Optimization parity note

The browser Optimize detectors in `static/views.js`, comparisons in `static/insights.js`, and MCP analysis in `telemetry_insights.py` currently have separate calculation paths. The cross-language fixture in `tests/test_mcp_insights_parity.js` checks comparison partitions, dated token-cost splits, and representative cache-waste, thinking-share and retry-tax calculations. It does not claim that every browser detector has identical thresholds or rendered findings. MCP reports detector-specific unsupported reasons when source detail is missing; broader UI parity needs additional shared calculations and fixtures.
