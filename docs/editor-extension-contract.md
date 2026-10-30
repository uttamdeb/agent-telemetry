# Editor extension implementation contract

Internal interface for the first AgentTelemetry editor extension. The Python
backend and durable ledger remain the source of truth. No parser/schema migration
is needed. All extension media and runtime code are bundled; no telemetry leaves
the machine. Compatible desktop VS Code editors use the same stable APIs.

## Read-only editor endpoint

`GET /api/editor?scope=workspace&root=<absolute-path>&range=today`

`root` may repeat (up to 20); range is `today`, `7d`, or `30d`. `scope=all`
requires no roots. Origin validation and a loopback-only check apply. Invalid
parameters return 400. This is a read-only endpoint, not a filesystem browser.
Use existing aggregates, pricing and conversation deduplication under the parser
lock. Do not reparse, rebuild, mutate the ledger or launch commands.

Workspace attribution requires a recorded absolute working-directory path,
matched by normalized path containment. Never match only a project basename.
Missing paths cannot be attributed to a workspace, while their history remains
available in all-project scope. This also affects current store-backed sources
that do not retain absolute working directories, including Cursor, OpenCode,
Hermes and OpenClaw. Remote/peer data is excluded.

Response (numbers are finite, nonnegative unless a documented delta):

```js
{
  schema: 1,
  generated_at: 1790000000, // seconds
  date: '2026-10-18', from: '2026-10-18', to: '2026-10-18',
  revision: 'shared-sync-revision',
  scope: {kind: 'workspace', label: 'my-project', roots: ['my-project'],
          matched_sessions: 3, unassigned_sessions: 2},
  summary: {tokens: 1234, input: 200, output: 34, cache_read: 1000,
            cache_write: 0, cost: 0.12, sessions: 3,
            main_agents: 1, subagents: 2, compactions: 0},
  days: [{date: '2026-10-18', tokens: 1234, cost: 0.12}],
  models: [{name: 'Model name', source: 'codex', tokens: 1234, cost: 0.12}],
  sessions: [{key: 'opaque-stable-identity', id: 'full-native-id', // empty if not retained
              source: 'codex', device: 'local-device-id', title: 'Session name',
              project: 'my-project', subagent: false, parent_key: null,
              parent_title: null,
              parent_status: 'none', // linked | outside_scope | unavailable | none
              model: 'Model name', efforts: ['max'],
              tokens: 1234, input: 200, output: 34, cache_read: 1000,
              cache_write: 0, cost: 0.12, compactions: 0,
              start: '2026-10-18T09:00:00Z', end: '2026-10-18T09:30:00Z',
              archived: false, detail_limited: false}],
  sessions_total: 3,
  truncated: false,
  limitations: ['Costs are estimates at API list prices.']
}
```

Totals come from records, not the session list, which is capped at 500 rows.
Copilot SDK child rows optionally carry `usage_in_parent:true` and
`usage_status:recorded|partial|unavailable`. Their dated receipts are breakdowns
of inclusive parent usage and are excluded from summary token/cost accumulation.
Unavailable usage renders as a dash. Parent and child compaction counts are separate.
`sessions_total` and `truncated` disclose this cap. Session figures use per-session
scoped daily usage, never whole-database totals copied into every child. Parent edges use
full recorded source identities; missing edges stay unavailable. Some current
store-backed sources and old aggregates retain only a shortened id: `id` is then
empty and `detail_limited` is true, while the opaque key still provides stable
selection. A parent's own
tokens and each child's tokens remain separate; never add child totals to a
parent that already includes them. Titles are private local display metadata;
do not ship account metadata, transcripts, arguments or source paths.

## Extension host and webview bridge

Files live in `vscode/`. Plain CommonJS JavaScript and Node built-ins keep the
extension portable and dependency-free at runtime. Minimum stable VS Code API
1.85, `extensionKind: ["ui"]`, desktop only. Restricted workspaces may connect
read-only but must not execute Python or trust workspace-supplied settings.
Remote workspaces must show an explicit local-machine message and disable
workspace scope rather than guessing a remote path belongs to this machine.

The host uses loopback HTTP only (127.0.0.1; configurable port). It validates
health identity before analytics, bounds response sizes/timeouts, refuses
redirects, serializes refreshes and discards superseded results. `/api/sync`
is the shared display clock: fetch a new snapshot only on changed revision,
but retry failures on the same revision. Preserve the last successful snapshot
and explicitly mark disconnection/staleness. User Refresh posts JSON to the
existing refresh endpoint. No provider/account lookup is performed.

The backend is bundled from an explicit source allowlist by a build script.
Keep every direct and transitive `dashboard.py` runtime import in that allowlist,
including `editor_snapshot.py` and the dashboard's static scripts.
The extension packaging test guards these entries; the VSIX verifier also
rejects credential files (including legacy MCP ones) and staging files alongside
ledger and device state.
The user explicitly chooses Start monitoring; discover a trusted Python 3.9+
interpreter (including an installed AT runtime where safely discoverable), or
show a useful setup action. Never run workspace code, use shell interpolation,
auto-install dependencies, rebuild a ledger or launch competing services on
different ports against the same data directory. Use the native default ledger
directory. Attach to an existing AT service without owning or stopping it.
A service explicitly started by the extension is shared across editors and
keeps running when an editor closes; disclose this on first start. Never
force-kill a service. Respect an occupied non-AT port.

`vscode/src/webview.js` exports `getHtml(webview, extensionUri, nonce)` and builds
HTML referring only to `vscode/media/` via `asWebviewUri`. A strict CSP forbids
network connections, frames and inline executable content; no unsafe HTML from
telemetry. Restrict `localResourceRoots` to media. No iframe of the full dashboard.

Host -> webview:

```js
{type: 'state', state: {
  connection: {status: 'connected', // connecting | offline | starting | error
               message: '', endpoint: 'http://127.0.0.1:7878',
               version: '2.0.0', last_success: 1790000000, code: ''},
  snapshot: null, // endpoint response above
  scope: 'workspace', range: 'today',
  since: null, // {tokens, cost, since: epoch-seconds, reset: boolean}
  selected_session: null, // opaque session key
  editor_name: 'Visual Studio Code',
  remote: false, trusted: true, workspace_available: true
}}
```

An identified older backend missing `/api/editor` produces connection code
`UPGRADE`. Show restart/upgrade guidance while preserving its monitoring; never
offer to launch another service over it.

Webview -> host allowlist (validate exact shapes; ignore unknown messages):

```js
{type:'ready'}
{type:'refresh'}
{type:'start'}
{type:'openBrowser'}
{type:'changeScope', value:'workspace'} // workspace | all
{type:'changeRange', value:'today'} // today | 7d | 30d
{type:'selectSession', value:'known-key'}
{type:'resetBaseline'}
{type:'setupPython'}
{type:'openSettings'}
```

The host computes `since` from an in-memory baseline of the selected scope/date
range. Changes to scope/date/range reset the baseline; reparses or decreasing
counters reset rather than report a misleading negative amount. Closing/reloading
the editor resets it. This is newly recorded usage, not a claim about editor
activity or currently running workers.

The activity-bar sidebar uses native TreeView rows (summary, connection, recent
main agents and children) and opens the Agent desk in an editor panel. It follows
editor theme, accessibility and keyboard conventions. The webview supports a
keyboard-operable delegation tree, session detail, scope/range controls and a
compact insight into input/output/cache composition. Missing metadata is honest.

Produce and validate an installable universal VSIX on a separate branch and PR.
Marketplace publication is an explicit release action using the verified package;
routine builds only upload CI artifacts. Publisher credentials never belong in
source, packages or public logs. The Marketplace page may show mock-data screenshots;
they are documentation assets and are excluded from the runtime archive.
