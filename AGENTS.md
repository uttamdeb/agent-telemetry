# AGENTS.md

**AgentTelemetry** — a local, **stdlib-only** dashboard that reads the logs your AI coding tools already write
to this machine and shows tokens, estimated cost, and breakdowns by model / day / tool /
project / hour. Inventory, telemetry and the ledger stay on this machine by default. Separate
user actions can share selected exports with paired devices, contact a provider
for a confirmed account lookup, or fetch upstream release metadata after a clicked update check
or release-notes load.
An explicitly started saved server command can access the network with the user's permissions.
Nothing to install.

```bash
python3 dashboard.py            # http://127.0.0.1:7878
```
Flags: `--port`, `--host`, `--interval`, `--rebuild`. First run parses everything (~30–60s
with big Codex logs), then caches; later refreshes are incremental. If asked to "run the
dashboard", check `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:7878/api/data`
first — it may already be up.

`--data-dir PATH` moves `.usage_cache.json`, `.peers.json`, and `.peers/` into a writable
directory; omitting it keeps the checkout-local paths. Native local clients use `/api/health`
for readiness only and `/api/summary` for today's token count and estimated spend. Health
must never include analytics records or project names.

Release notes are separate from the usage ledger. `GET /api/release-notes` reads cached
metadata offline; same-origin loopback `POST /api/release-notes` handles the baseline,
display acknowledgement and explicit GitHub check. Never fetch on startup or polling.
The first open seeds the baseline, and a later version is acknowledged only after display.
See [docs/release-notes.md](docs/release-notes.md).

## Native clients and installation (v2.0)

`python3 install.py` (macOS) / `python install.py` (Windows) detects the OS and installs
the corresponding native client for this user; `--no-launch` opts out of opening it.
macOS requires Swift/macOS SDK for source building and bundles hash-pinned CPython.
Windows uses Python `ctypes` and native Win32 APIs, with the installed `pythonw.exe`;
no third-party GUI dependency. Linux stays on the existing browser workflow.

- `native.py` owns the installed-client manifest, launch/quit requests and heartbeat
  protocol in the separate **AgentTelemetryNative** application-support directory.
  Do not put those files into the ledger directory: import requires an empty destination.
  Original-title backups live here in `session-title-backups/` (private metadata only,
  never token/prompt history). `session_names.py` implements confirmed source edits:
  Claude appends its native `custom-title` record; Codex uses the official local
  `thread/read` / `thread/name/set` API, never starts/resumes a thread or model turn;
  OpenCode updates only the title columns of recognized SQLite session tables in one
  transaction. Unknown/generated schemas or title triggers are refused. Remote,
  missing and unverified source formats cannot be edited. UI/POST targets use FULL
  native ids (`rename_id`), never the eight-character ids shown in the table.
  `POST /api/session-name` goes through `_csrf_ok()`: preview returns a one-use,
  five-minute ticket; writing requires that ticket and explicit warning confirmation.
  Re-check title/file state after confirmation; back up previous metadata before any
  write. Never rewrite a usage transcript or rebuild the ledger for a title edit.
  Source-derived title edits/display capability fields do not change aggregate shape.
- `windows/tray.py` owns the notification icon and serialized worker queue;
  `windows/popover.py` renders the native counterpart of macOS `StatusMenu` using
  standard button/combobox HWNDs, with theme/DPI support, clipped scrollable settings
  and a fixed footer. `windows/presentation.py` holds portable layout/date/position logic;
  `windows/win32.py` holds typed ctypes bindings. Bind each API overload independently
  (`dll[name]`), because `getattr` caches a shared object whose argtypes can be overwritten.
  The installer explicitly includes these modules and `windows/assets/*.ico` by name.
  `windows/monitor.py` owns testable backend attachment, ownership, stale summaries and import.
- `macos/AgentTelemetryMac` owns SwiftUI/AppKit UI, app-owned Python and WKWebView.
  `NativeControl.swift` handles the same heartbeat/control-file protocol as Windows.
  Icon display reuses the installed app's full-color icon (copied before resizing on
  macOS, `AgentTelemetry.ico` on Windows), including both popover headers.
  Keep the optional numeric display and monitoring status tooltip/accessibility labels.
- Web Settings uses `GET/POST /api/native` to launch/quit the installed client. Its
  running status requires a live matching heartbeat. A pending/failed launch is not running.
  Both clients attach to port 7878. Closing a client never terminates an attached backend.
- `GET/POST /api/sync` is the shared display clock/interval (0, 15, 60, 300, 900 seconds;
  default 15). POST uses `_csrf_ok()`. Preferences live in AgentTelemetryNative;
  ledger parsing still follows `--interval`. Clients sample the tiny clock once a second
  and fetch figures only on changed ticks; Manual still listens for explicit refreshes.
  HTTP data and summary share a serialized snapshot per tick, under the parser lock.
  Manual refresh, cache actions and device actions invalidate it. Never save it in the ledger.
  Retry failed payloads on the same tick and discard superseded async results. Changing
  cadence must not restart a backend or reload a WebView. Older services use independent
  polling until upgraded. Native figures represent unfiltered Today, including peers.
- Windows gracefully shuts down only its owned backend using `POST /api/shutdown`,
  JSON/same-origin CSRF validation **and** a random `AGENT_TELEMETRY_CONTROL_TOKEN`
  supplied only in that child's environment. Never expose this token in health, data,
  logs or release assets. Standalone services have no token and reject this endpoint.
- Open at Login and automatic monitoring are optional and off by default. The
  explicit installer/web launch starts monitoring for that launch without changing defaults.
- Keep backend, parser and ledger schema identical across clients. No cache-version bump
  for UI/installation-only changes; no `--rebuild` for routine upgrades.
- Combine `@Published` emits in `willSet`: render the incoming summary/status, not an
  immediate reread. Check lifecycle/cancellation **after** every readiness await.
- Keep last successful figures but mark failures stale and retry. Preserve the WebView's
  current query/hash across polling changes and app-owned backend restarts.
- Windows visual changes require `python tests/run_windows_native.py --screenshots
  windows-ui-fixtures` on Windows and inspection of its mock-data render artifacts.
  An accepted Shell notification icon or successful HWND creation does not prove a
  readable panel. Cover both themes, 100/150/200% scale, collapse/expand, keyboard,
  outside dismissal, stale/retry and graceful-saving states. No real ledger in screenshots.
- Keep retries alive when cadence changes during an outage. Windows queued work carries
  a monotonically increasing UI intent; an older operation cannot undo a newer Start/Stop.
  Keep the closing client's heartbeat alive until its owned backend has saved and exited,
  so a web launch cannot race a slow shutdown. Never force-kill it to meet a timeout.
- Packaging must traverse nested Mach-O files with NUL-delimited filenames, sign them
  before their app container, verify signatures and never bundle user data. The Windows
  install payload also uses an explicit allowlist. Local ad-hoc macOS builds are not
  a notarized public installer; signed distribution has a separate credential gate.

Checks: `python3 -m unittest discover -s tests`, `node tests/test_frontend.js`,
`swift build --package-path macos/AgentTelemetryMac`, `python3 tests/run_mac_native.py`,
and on Windows `python windows/tray.py --self-test` plus `python install.py --no-launch`.
CI runs backend/frontend checks on Linux, macOS and Windows and native checks on their OS.

Release downloads use `.github/workflows/release-downloads.yml`, dispatched against an
existing tag. It builds architecture-specific macOS DMGs and a Windows x64 EXE. The EXE
uses Inno Setup only at build time and bundles hash-pinned CPython with the unchanged
release-tag backend/UI allowlist. The runtime is CPython 3.15.2 (`PYTHON_VERSION` in
`windows/installer/build.py`; the macOS app pins the same version in `package-app.sh`, and CI runs the
unit tests on 3.15 too). Its `python315._pth` (named for the version, or it is ignored) lists the app
folder BEFORE `python315.zip`: a
`._pth` stops Python adding the script's folder, and with the stdlib first `import antigravity` found
the stdlib's xkcd module (it opens a browser), so the backend could not start. Keep it first, and keep
`antigravity` the only top-level module named like a stdlib one (`test_release_packaging.py`). `windows/installer/register_install.py` registers app
controls, preserves an existing owned login opt-in, and guards installation/uninstall
against a live or closing client; it never deletes the ledger or preferences. Run
`tests/run_windows_installer.py` on Windows to verify install, packaged Win32 UI, upgrade,
running-client refusal and uninstall against mock archived data; supply the release
version, source commit and cache version as expected values. CI reads all three from the
release tag checkout so verification stays correct as releases advance. CI uploads artifacts only;
release publication remains explicit. Label these unsigned/unnotarized downloads
accurately. Keep the existing public tag intact when adding packaging metadata/assets.

## Layout

| File | Role |
|---|---|
| `dashboard.py` | stdlib `http.server`. Serves `/`, `/static/*`, `/chart.js`, `/manifest.json`, `/sw.js`, `/api/{data,storage,refresh,settings,cache,update,release-notes,devices}`. Owns the cache, the aggregate merge, `_cost()` and the device sharing listener. |
| `parser.py` | **AgentTelemetry Pulsar**, the log-ingestion engine (the module stays `parser.py`). `discover()` lists log files; `update_file()` routes each to a `parse_*`. Holds `PRICING` and the model-name normalizers. |
| `cursor_data.py` | Bounded read-only Cursor metadata and conversation-event projection; no raw content retained. |
| `antigravity.py` | Read-only Antigravity SQLite conversation snapshot and bounded safe event reader; parser integration routes the same records to every client. |
| `static/core.js` | `SRC`/`ORDER`, state `S`, formatting, date ranges, filtering. |
| `static/tool-icons/` | Bundled tool/provider SVG marks, pinned sources and licenses. |
| `session_names.py` | Warning previews, private title backups and verified original-tool renames. |
| `accounts.py` | Private account observations, optional read-only CLI account queries and Claude status-line capture. |
| `release_notes.py` | Private cached GitHub release metadata and one-time first-open acknowledgement state; network access is explicit. |
| `static/charts.js` | Chart.js theming, `mk()`/`hbar()`/`areaDS()`, calendar + heatmap SVG. |
| `static/views.js` | The eight views, controls, events, boot. |
| `native_activity.py` | Pure, bounded projection for native clients' recent local-session list. |
| `index.html` | Shell only: sidebar (device name, tabs, live/refresh/theme/settings), page header (period, range, metric, filters), card markup, an SVG icon sprite. |

Eight tabs in the sidebar: Overview · Cost · Models · Tools · Projects · Sessions ·
Local servers · Storage. Tab lives in `location.hash`. **Every filter lives in the query
string too**, so a reload, bookmark or pasted link opens the same view: `filtersFromURL()`
reads it at boot and `filtersToURL()` writes it after every `renderAll()`, both in `core.js`.
The params are `?range=` (a preset, or `custom` with `&from=&to=` as YYYY-MM-DD),
`?metric=` (`tokens|cost|messages|time`), `?rate=out`, `?tools=`, `?providers=`, `?models=`,
`?projects=`, `?ides=`, `?devices=` (each repeated once per value), `?exact=1` and `?q=`.
The Sessions-only `?session_type=subagents|other` selector filters that list after
the shared slice; it must not alter analytics totals. Unmarked rows are Other
sessions, not confirmed primary sessions. Preserve ID search, accessible full
Subagent labels, and the selector's reset on project/comparison drilldowns. Counts
follow the current range, shared filters and search. Delegation counts and Claude
sidechain tokens are whole-session evidence, not parent-child links; sidechain
tokens are already included in the parent's total. See docs/subagent-sessions.md.
Copilot SDK's inline child breakdowns are presented separately in payload
`session_children`. Combine them only in the Sessions list, after the same shared
filters/date scope. Never add them to `slice().sessions`, analytics records or project
counts: their usage is already included in the parent.
Keep full parent/agent identities, explicit parent links, unavailable usage labels
and recorded activity dates; do not infer activity on idle days or rename these children.
Only what differs from the default is written, params that aren't the filters' own are left
alone, and a change replaces the history entry the way switching tab does. `?theme=` and
`?side=collapsed|open` preset the look (handy for headless screenshots). **A new filter goes
in `S` and in both functions**, or it silently resets on reload; sort order, table twins,
muted series and expanded rows are display state and stay out of the URL. The sidebar
collapses to an icon rail (`[`, remembered as `aiu.side`); below 900px it becomes a top bar
instead.

**The controls are a sentence**, not a toolbar: "**Tokens** from **all tools** over **the last
30 days** ‹ ›". Each bold phrase is a `.dd` that opens its menu (`#metricPanel`,
`#filtersPanel`, `#rangePanel`); `periodPhrase()` supplies the connecting word ("over",
"in", "across", or none for "today" / "this month"); ‹ › (`stepPeriod`, keys `,` `.`) move
the window back or forward by its own length as a custom range, stopping at today. Tokens
are the default measure — the Overview hero follows it, with estimated spend below and
tokens excluding cache reads in the total's hover/focus tooltip (Escape dismisses it).
Messages/Time keep spend in
a tile; Cost uses the hero itself. Activity and Monthly rhythm keep their historical windows and
apply the same precision/dimension selection as `slice()` via `filteredRecords()`.
Every chart has a table twin (`.tv[data-tv]`, `chartTable()`); SVG chart data lives in
`chartTables`, separately from Chart.js instances. Each view
leads with one hero figure — keep it that way rather than adding a second.

The device name at the top of the sidebar is `DEVICE` in `dashboard.py`: the OS's own name
for the machine (`scutil --get ComputerName` on macOS, `%COMPUTERNAME%`, `/etc/machine-info`
`PRETTY_HOSTNAME`), falling back to the bare hostname. It's in the payload as `device`.

Aggregates are keyed `records["date\tmodel"]`, `tools["date\tname"]`,
`hourly["date\thour"]` — **every dimension carries a date** so the UI can filter by range.
In the payload every usage row (records, sessions, activity, tools, hourly, ctx, skills,
reads) also carries a `device` id; see "Your devices".

**Optional extensions.** `core.js` exposes a feature-neutral registry: `EXT`,
`registerView({id, title, label, icon, after, html, render(d), badge(d)})` and
`registerSettingsPanel({html, begin, current, close, load})`. A plain `<script src>`
placed between `core.js` and `views.js` may call them; nothing in this repository does,
so both lists are empty and every loop over them is a no-op. `registerView` builds the
`#tabs` button and `#v-<id>` section, ignores an id that already exists, and `views.js`
merges registered titles into `VIEW_TITLES` (an unregistered `#hash` falls back to
Overview), dispatches `render(d)` inside `renderAll()`'s error guard and refreshes badges.
`tests/test_extensions_frontend.js` covers it. Extensions use the shared globals
(`RAW`, `slice()`, `fmt*`, `passSrc()`…) at call time, so renaming one is a contract change.

## Rules

1. **Stdlib only, offline.** No runtime dependencies. Vendor any JS (Chart.js already is).
2. **Never commit `.usage_cache.json`**, `server.log`, `.peers.json` or `.peers/` — that's
   the user's own prompts, projects, costs and pairing codes. A fresh clone must start empty.
3. **Never hardcode a path.** Derive from `HOME` / `%APPDATA%` / `%LOCALAPPDATA%` /
   `$XDG_*`. Split path components with `_leaf()` (handles `/` and `\`) — logs written on
   one OS get read on another.
4. **Commits are authored by the repo owner alone.** Never add a `Co-authored-by:` trailer.
5. **Costs are estimates** at API list prices; subscription users pay nothing per token.
   Keep that framing. Verify any price against vendor docs — never guess.
6. **Attribute by tool, not by model.** A Claude model run inside Copilot counts as Copilot.
7. **Bump `CACHE_VERSION`** whenever an aggregate's shape changes.
8. **Never `except Exception: pass` around a parser.** A swallowed error is
   indistinguishable from "the user doesn't have this tool" — that's how a `TypeError`
   once made the whole OpenCode parser silently yield nothing. Write to stderr.
9. **Any write endpoint goes through `Handler._csrf_ok()`.** There's no auth, so any page
   the user visits can POST here; with `Content-Type: text/plain` it's a CORS simple
   request with no preflight. That was enough to set `cleanupPeriodDays=1` and make Claude
   Code delete transcripts. The guard demands a JSON content type and same-origin.
10. **Colours are a validated palette and `ORDER` in `core.js` is the safety mechanism** —
    adjacent pairs must clear CVD ΔE ≥ 8 and normal-vision ΔE ≥ 15 in *both* themes.
    Re-run the data-viz skill's `validate_palette.js` over the whole sequence after any
    reorder or hue change. Identity must never be colour-alone: keep legends and tooltips.
    Tool marks come from `SRC.icon`, not the billed model/provider. Use `toolIcon()` /
    `toolLabel()` for identity throughout filters, badges, bars, legends and tables;
    retain chart swatches and the existing series colors. Brand marks are local assets
    with documented sources; never fetch icons remotely at runtime. Icon-only references
    carry the full tool name/provider as an accessible label and tooltip.
    OpenCode's display name is `OpenCode`; its source id, paths and asset names
    remain `opencode` for compatibility with existing ledgers and saved filters.
11. **"AgentTelemetry Pulsar" is `parser.py`'s user-facing name, nothing more.** The brand
    appears only in user text and docs. Never rename the module or file: `dashboard.py`, the
    tests, the packaging allowlists and downstream builds import `parser` by name.

## The IDE dimension

`records` carry an `ide` — which editor/surface the work ran in — resolved once per
aggregate by `_ide_of()` and filterable like project or model. Each source records it
differently and none agree on spelling, so they collapse to a shared vocabulary:
Copilot's comes from *which editor's storage* the file sat in, Claude/Codex stamp an
`entrypoint`/`originator`, and Cursor/Claude Desktop/OpenCode/Hermes run in exactly one
place. An unrecognised entrypoint passes through **as itself** rather than being forced
into a bucket, so a new host appears rather than silently becoming "VS Code".

**A new `entrypoint` value can collide in spelling with an unrelated `source`.**
`entrypoint: "claude-desktop"` on `source: "claude"` (Claude Code launched from
inside the Desktop app) and `source: "claude-desktop"` (Desktop's own agent-mode
logs — a different file format) both mean "the Claude Desktop app" to the user, but
the first fell through `IDE_FROM_ENTRY` unmapped and showed as a separate, oddly-cased
row. Mapped alongside `local-agent`, the older entrypoint for the same app.

**Codex's VS Code variant is recovered from the editor, not the log.** Codex only ever
writes `vscode`, so Insiders work is indistinguishable from stable in the rollout itself.
But each editor's `globalStorage/state.vscdb` carries the Codex extension's per-thread UI
state under `openai.chatgpt`; a thread id appearing there means that editor opened it.
`_vscode_thread_owners()` builds {thread id → editor} across all known editors (60s TTL)
and `_ide_of` uses it. A thread present in TWO editors is genuinely ambiguous and is left
as plain "VS Code" rather than guessed — here that's 6 of 45.

**Still unrecoverable:** Claude Code. Its `Anthropic.claude-code` state holds only
settings and `hiddenSessionIds`, never a session list, and there is no per-editor
workspaceStorage for it — so `claude-vscode` stays "VS Code" whatever fork hosted it.
Same for any extension run inside Cursor/Windsurf/Antigravity that logs only "vscode".

Adding a VS Code fork is one entry in `COPILOT_ROOTS` + `EDITOR_LABEL`; the chat storage
format is identical across forks. Note newer builds nest sessions as
`chatSessions/<uuid>/index.json` instead of a flat file — both globs are needed.

## The durable ledger is load-bearing — treat it as data, not cache

Once a log is deleted from disk its aggregates exist ONLY in `.usage_cache.json`,
marked `archived`. The Storage tab actively tells users deleting old logs is safe,
so that file stops being a cache and becomes the sole record. Two consequences:

- `load_cache()` **keeps `archived` entries across a `CACHE_VERSION` bump** while
  re-parsing live logs normally. They cannot be re-derived — the source is gone — so
  discarding them on a version change would silently destroy history the UI promised
  to keep. Newer fields are read with `.get()` defaults, so an old-shape archived
  entry degrades rather than breaks.
- Cache **59** adds optional Claude/Codex `reasoning_effort` metadata. Retain all
  v53–58 entries; peers v52–58 remain readable. Available supported logs lacking
  `reasoning_effort_version:1` reparse transactionally before the unchanged-file
  shortcut. Failed backfills retry; deleted archives remain unchanged. Never rebuild.
  Safe session metadata uses local-date/canonical-model buckets of recognized
  `levels` and `unrecorded` observations, with bounded response/context identity
  state kept private. It never changes model keys, prices, tokens, calls or time.
  Claude uses its own assistant `perTurnEffort` override or `effort`; null overrides
  fall back, malformed nonnull overrides remain unavailable. Inline sidechain usage
  stays unrecorded in the parent, while standalone children read their own fields.
  Codex uses recorded top-level turn-context effort matched to accepted usage;
  missing/invalid contexts clear earlier settings. Never infer historical effort
  from current config, thinking-token counts, spawn arguments or another session.
  Sessions labels and drawer details follow date/model/provider/device scope.
  See docs/recorded-effort.md for verified sources and limits.
- Versions 53/54 → **55** add optional Claude/Codex compaction metadata. Version **56**
  adds Haiku 5.5 request-tier pricing subsets. These migrations
  retain every existing entry; `update_file()` fully reparses available Claude/Codex logs
  missing `compactions`, replacing them only after success. OpenCode databases gain
  an `opencode_schema` marker and reparse once even on an unchanged signature to
  discover V2 history. Other sources continue
  incrementally and archived entries stay unchanged. A failed backfill retries even
  when the source size/mtime is unchanged. In v56, only available Claude logs with
  Haiku 5.5 usage missing `haiku_pricing` need a pricing backfill; other sources stay
  incremental. Peers with versions 52/53/54/55 remain readable.
- Cache **62** adds Antigravity while retaining every compatible v61 entry,
  including archives; peers with versions 52–61 remain readable. New
  conversations are discovered incrementally and do not need a rebuild. Its
  reverse-engineered SQLite reader preserves field-level recorded/partial/
  unavailable states, deduplicates generation/step rows only through their
  explicit packed-step mapping, and does not treat intent-only checkpoints as
  compactions. Standalone legacy `.pb` files remain unsupported. See
  docs/antigravity.md.
- Cache **64** adds discovery of Codex's zstd-compressed rollouts (`*.jsonl.zst`) and
  retains every v53–63 entry, live and archived; peers with versions 52–63 remain
  readable. No aggregate changes shape. It is bumped because a device that predates
  it counts a conversation's archived `.jsonl` entry and its live `.jsonl.zst` entry
  as two conversations, so an older receiver must refuse a newer export ("update
  both") rather than double count. The compressed entries appear beside the existing
  ones on the next normal restart: never rebuild, and keep the archived plain entries,
  which are the only record of a rollout whose compressed copy cannot be decoded here.
  See "Codex compresses idle rollouts" under Per-source quirks.
- **`--rebuild` destroys archived sessions too**, not just the Settings buttons — it
  skips the cache entirely and re-parses from disk, and an archived session has no
  disk to parse. Back up `.usage_cache.json` before running it on a machine whose
  logs have been archived off.
- The Settings panel's **Rebuild / Delete cache** actions still drop archived
  sessions permanently; both warn about exactly this. Don't add a third path that
  clears the cache without the same warning.
- **One conversation can hold two ledger entries**, and each used to count in full:
  Codex's archive feature moves a rollout into `archived_sessions/` (the old path stays,
  archived, beside the new live one); Codex also replaces an idle rollout with
  `<rollout>.jsonl.zst` (the plain path goes missing and is archived beside the new live
  compressed one, and the reverse when a thread is decompressed to resume); and a
  Copilot chat can exist as both `<uuid>.json`
  and `<uuid>.jsonl` across its storage-format migration, or in both VS Code's and
  Cursor's storage after Cursor imported VS Code's. `build_payload` keeps one per
  conversation **per device** (`_one_per_conversation`: fullest including cache writes,
  then live, then newest). Use `_leaf()` on paths so Windows copies deduplicate on a Mac
  too; Codex's key is `_codex_leaf()`, the file name without a `.zst` suffix.
  A path found on disk is never archived: `_refresh_locked` clears the mark, which is
  otherwise only ever set when a path goes missing, so a log that came back (a rollout
  Codex decompressed) stayed archived and lost its timeline, rename and live views.
- **Usage a tool deletes from a log that still exists is kept too** (`parser.reconcile`,
  called from both `update_file` call sites in `dashboard.py`). Archiving only covers a
  path that disappears. OpenCode's database, Hermes and OpenClaw hold many sessions in
  one store, and a deleted session (or OpenCode's undo, which deletes the reverted
  messages) silently dropped out of every total on the next full reparse; so did a
  Claude/Copilot log that got shorter (Copilot compacts its chat log without requests
  removed in the chat). Kept usage lives in private `agg["_retained"]` and is re-added
  after every reparse. **It is found by identity, never by comparing totals alone**:
  usage also moves between rows without being deleted (Hermes' main model changes, a
  timezone change re-buckets local dates, an OpenClaw fork takes over its parent's
  copied responses), and one refresh can bring a deletion and a same-sized retry.
  A store session that is gone keeps its whole last-known contribution and is flagged
  `retained` + `archived` (no rename, no timeline, never "live"); if it comes back,
  the source's copy replaces it. OpenCode messages deleted from a session still there
  are kept by message id from per-message summaries in private `_store_detail`
  (`retained_usage` flag, drawer note); a session emptied and used again keeps its old
  messages. Ids hold across OpenCode's V1/V2 copies (the migration keeps them and V1
  has no forks), so a V2 session that falls back to its V1 rows keeps what was written
  since. A shortened Claude/Copilot log keeps what each dimension's totals lost,
  capped so moved usage is never kept; the ids of the records it lost (`_retained.seen`)
  seed every later parse's seen-set, a full reparse included, so restored lines are
  not recounted. A kept row the source lacks takes the full shape its parser indexes
  (`_RETAIN_BLANK`); a partial one made the next append fail. Message and log shortfalls are
  measured only when the SOURCE changed and both parses carry the same `_parsed_under`
  (CACHE_VERSION): **a parser fix that lowers counts must bump `CACHE_VERSION`**, or
  retention will keep the old figures. Hermes and OpenClaw keep whole deleted sessions
  only; Codex is only ever deleted whole (archived) and has no record identity for a
  shortened-then-restored log; Copilot `.json`, Antigravity and Copilot SDK are one
  conversation per path; Cursor keeps its own row history.
  Store sessions now keep their full native `_sid` (the visible id is only eight
  characters; OpenCode's repeat within minutes), which also makes OpenCode renames
  resolve. Entries written earlier match on the short id only when unambiguous (else
  also on `start`); ambiguous ones are neither kept nor compared. `_store_detail` also
  holds each store session's tools/hourly and OpenClaw's counted response keys, so a
  fork never recounts a deleted parent's responses; a store entry without it reparses
  once, even on an unchanged signature. `_retained`, `_store_detail` and
  `_parsed_under` are never exported to peers; the merged usage is. No cache-version
  bump. Like archived sessions, kept usage cannot be re-derived: `--rebuild` and the
  warned Rebuild / Delete cache actions drop it, as does `load_cache()`'s fallback for an
  unlisted version (it keeps archived entries only), so list each migrated version as
  compatible. See `tests/test_retention.py`.
- A failed full reparse leaves the previous ledger entry and signature intact; the
  next refresh retries it. Parser failures propagate to `_refresh_locked`, which reports
  the source, path and exception on stderr even for background refreshes.
- Incremental parsing stages a deep copy and commits it only after parsing succeeds.
  A failed append leaves the previous aggregate and byte offset intact, so retrying
  cannot count a partly parsed append twice. Failed cache saves retain pending flags
  and retry. An unreadable ledger is preserved and blocks saving until restored and
  restarted, or explicitly deleted through the existing warned cache action.
- Payload construction and cache actions share `_refresh_lock` with parsing. Aggregates
  mutate in place, so copying only the outer file list is not a consistent snapshot.

## Usage explanations, history recovery and source health

Private `AgentTelemetryNative/project-names.json` preferences map historical project
labels and explicitly relocated local roots at presentation time. Use
`native.set_project_preferences()` to validate/save them. Never rewrite transcripts,
ledger entries or peer exports for a folder rename; no cache bump/rebuild is needed.
See `docs/project-relocations.md` for migration and compatibility-link guidance.

`ledger_backups.py` keeps private byte-preserving snapshots in `.usage-backups/`
beside `CACHE_PATH`. Serialize create/preview/restore with `_refresh_lock`. Routine
saves try one scheduled snapshot per UTC day; failures surface but do not prevent
saving. Update/cache-reset safety copies are required before destructive work.
`POST /api/backups` uses `_csrf_ok()`; restoring additionally requires a one-use,
five-minute preview ticket bound to the candidate/disk digests and parser generation.
Keep the current ledger before replacing it, and retain every validated restored
entry, including old live/archived entries. Never rebuild to restore. A corrupt-file
safeguard is retained but is not an in-app restore candidate. Backups are private user
data; never commit, share or package them. They do not alter the ledger schema.

`session_timeline.py` supplies on-demand `GET /api/session-timeline` detail only
after resolving the full native ID, source device, ledger entry and discovered
source path. Never accept a caller-supplied filename. Reads are bounded and return
only event kinds/timestamps/model/tool labels/numeric usage and per-log agent ordinals, not prompts, arguments
or account data. Keep token-count/usage-record handover and fork/block dedup aligned
with `parser.py`. Date bounds filter returned events, not the parsing state needed
to identify earlier model/replay/usage context. Local Claude/Codex and Copilot SDK events include recorded model/effort changes,
subagent request/lifecycle markers and available session/turn lifecycle events.
`view=activity|all|requests|tools|compactions` filters before the event cap; Activity
is the drawer default. Claude tool requests are not proof of execution, and a
background tool return is not proof of completion. Deduplicate source/ledger
compactions by timestamp and agent ordinal. Remote/deleted/unsupported sources
use retained daily/compaction detail. Missing metadata remains unavailable.

`diagnostics.py` tracks scan counts and static, actionable error categories in memory;
discard exception messages and paths. `GET /api/diagnostics` is local and origin guarded.
Never add these details to `/api/health` or peer exports. The maintenance Settings UI
lives in `static/maintenance.js`. New frontend harnesses run with Node alongside
`tests/test_frontend.js`: `test_insights_frontend.js`, `test_timeline_frontend.js`,
and `test_maintenance_frontend.js`. `tests/preview_features.py` uses mock data only.
## Local server controls

`local_servers.py` owns the private `.local_servers.json` inventory beside
`--data-dir`; `local_servers_windows.py` implements Win32 listener discovery and
identity-checked control. macOS and Windows support lifecycle actions; Linux
returns `supported:false`. Windows asks for a working folder (never read foreign
process memory), starts commands in a job assigned before resuming, and validates
Stop against ownership and creation time on one held handle. Its immediate Stop
behavior must be disclosed before confirmation. The Windows tray opens the shared
web controls; macOS also shows pinned controls inline. Both macOS UI surfaces use the
Python API; do not reintroduce a separate native scanner/writer when the backend is
unavailable. Saved commands never belong in the ledger, health, summary or peer exports.
All endpoints are loopback-only; writes use `_csrf_ok()`. Start/Stop/Restart require
`confirmed:true`, and Restart also supplies the confirmed `serverId`. Keep process
identity checks, per-profile lifecycle serialization, inventory thread/file locks,
and failure preservation. No command inference, environment capture, auto-start,
force-kill on timeout or ledger rebuild. See docs/local-servers.md.

## Account snapshots

Cost's bottom subscription-limit card appears only for Codex/Claude sessions active
today on the local device. Tool/device selection applies; usage date/model/project
filters do not change account-wide quotas. Combine same-profile entries without
summing percentages, hide empty buckets and keep freshness/unverified-account labels.
Its meters use `GET /api/accounts`, separate from usage
data/summary and health. GET is offline. `POST /api/accounts` requires `_csrf_ok()`
and explicit `confirmed:true` consent for a one-time CLI lookup that may contact the
provider. Never start/resume a thread or model turn, login/logout, or redeem credits.
Codex uses only initialize, account/read (refreshToken:false), and account/rateLimits/read;
Claude uses auth status. Read accounts before/after Codex quota lookup and discard
changed accounts. Credential contents are never decoded; only file signatures and
non-reversible environment fingerprints detect changes. Other processes' credentials
remain unknown. A pipe reader owns closing stdout; closing it on another thread can
hang on an inherited pipe's read lock with recent Codex CLI builds.

`account_metadata` is private optional aggregate metadata in cache version 57.
Codex creator ids mean thread creator, never per-request payer. Claude accountUuid
values are incomplete observed record identities, never inferred auth/plan. Quota
snapshots are account-wide, timestamped and never summed across sessions/devices or
derived from tokens. Log quotas and Claude captures cannot be matched to current
sign-in. They stay explicitly unbound. Missing fields are unavailable; observations
older than five minutes, future-dated observations, passed/missing resets and failed
lookups are stale. Never refill a percentage just because the reset time passed.
Account metadata is excluded from peer exports and remote session displays.
After a failed lookup, reconcile the web view with an offline GET: the backend may
have discarded a switched account. Hide unverified previous identity/live limits
if reconciliation fails, and keep retry controls. Resolve log profile components
across both path separators so copied Windows ledgers stay distinct on macOS.

Versions 53–56 keep all entries during migration. Only available Claude/Codex logs
missing the private metadata marker reparse transactionally; archives stay unchanged
and a failed backfill retries. Peers 52–56 remain readable. Do not rebuild. Live
account results are memory-only. Optional `accounts.py --capture-claude` stores only
Claude's documented five_hour/seven_day status-line fields in AgentTelemetryNative,
outside the ledger; missing fields replace older captures. Never edit a user's status
line automatically. See docs/account-limits.md for verified sources and limitations.
Use tests/preview_accounts.py for web checks with mock data only.

`native_activity.py` projects at most three newest local sessions active in the
last 15 minutes for `GET /api/native-activity`. It reads only retained aggregates
under `_refresh_lock`, applies `_one_per_conversation`, and never scans logs,
refreshes the ledger, or adds a cache field. The endpoint is loopback- and
origin-guarded; health, summary and peer exports do not include its rows. Each
`last_activity_at` is a source session end time, while `tokens` is that session's
lifetime recorded input + output + cache-read + cache-write total. It is not a
current-context or 15-minute token count. Missing or invalid counts remain
unavailable (`null`); inclusive Copilot SDK child breakdowns are not separate
rows. Titles are bounded and read only from the parser's retained session-title
field, which may be a short prompt-derived display title; raw messages are never
read or returned. The key is an opaque hash of the source identity and normalized
source path, never the displayed short id. `provider` is a tool-vendor label for
Claude, Codex, Copilot, and Cursor, not a billed-model provider; it is null for
tools with multiple model providers. The Windows and macOS clients use the
existing guarded `GET /api/local-servers` for the separate local server list.

## What the logs cannot show

- **Claude Code bills calls it never writes down.** A `/compact` (manual or auto) runs a
  summarization request whose usage appears nowhere in the transcript, and Claude Code's
  background Haiku calls never do either. Its own `cost-state` records (rare) include
  them — where they exist, the transcript matches them exactly *except* for those calls,
  typically a few percent of cost. Don't estimate them from `compactMetadata` token sizes.
- **`codex-auto-review` has no public price**, so its tokens are counted but cost $0.
- **OpenAI long-context rates are not billed.** The pricing page now states the threshold
  (a prompt over 272K input tokens; the GPT-6.1 Sol model page gives 2x input and cache
  rates and 1.5x output for the whole request), but it is a per-request rule and the
  aggregates are per day and model, so applying it means pricing each request inside
  `parse_codex`. Until then the 35 Codex requests here that passed 272K bill at the
  standard rate.
- **Gemini prices cover the text models only, at the short-prompt Standard rate.** Pro over
  200K input tokens (2x input), audio input, and the Live / TTS / image / embedding / Veo
  models are not priced: they bill audio, image or video tokens at other rates and are
  per-request rules like OpenAI's 272K one. Gemini bills no cache write (only storage per
  hour), so cw is 0. 3.6 to 3.8 Flash are a promo through 2026-12-31 that doubles on
  2027-01-01 — move it into `PRICE_HISTORY` then. `_canonicalize` gives every source one
  spelling ("Gemini 3.1 Pro"); `price_of` also tries it, so a row cached under an older
  spelling still prices.
- **Gemini 4 Argon is $2/$10 per 1M input/output tokens and $0.10 cached input through
  2026-11-30**, then $4/$20 and $0.20 from 2026-12-01, verified against Google's
  [pricing page](https://ai.google.dev/gemini-api/docs/pricing) on 2026-10-30 (generally
  available as `gemini-4-argon`; first seen in the
  [launch post](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/)).
  `_canonicalize` recognizes Argon display/raw spellings without treating audio/image/Live
  variants as the text model. On or after 2026-12-01 move the current tuple into
  `PRICE_HISTORY` dated 2026-11-30 and put the new rates in `PRICING` — never before.
  Cache storage is not modeled. There is no "Gemini 4 Carbon" on Google's pricing page
  (checked 2026-10-30); add nothing until Google publishes it.
- **Claude's advisor iterations** (`usage.iterations[].type == "advisor_message"`) are not
  priced separately — none exist in any log seen, so their shape can't be verified. Every
  iteration here is a plain `message`, already covered by the response's own `usage`.

## Field conventions that differ by source (do not "fix" these)

- **Haiku 5.5 has per-request pricing tiers.** Verified against Anthropic's
  [pricing page](https://platform.claude.com/docs/en/about-claude/pricing) on 2026-10-12:
  input/output are $0.10/$0.50 per Mtok up to 100K prompt tokens, $0.50/$2.50 above
  100K. Input includes cache reads and writes, excludes output/reasoning. `lc_in`,
  `lc_out`, `lc_cc5`, `lc_cc1`, `lc_cr` and `lc_asst` are subsets used for pricing,
  never additional usage. The existing eight-response dedup window also bounds
  `state.haiku_context`; a streamed threshold correction reprices the request's earlier
  date/turn/skill contributions without changing counts. Old archives/peers and other
  tools lacking this metadata use the short-prompt rate.

- **Claude compactions are durable session events, not inferred model usage.**
  `agg.compactions` maps boundary UUID → timestamp, local date, manual/auto/unknown
  trigger, before/after context sizes and duration in milliseconds. An id-less older
  boundary uses its UTC completion timestamp as a stable fallback. Record only
  completed `system/compact_boundary` events; the summary and `/compact` command
  add no event, prompt, reply or token. Inline sidechains are not the parent's events;
  child transcripts keep their own. Never store summary text or price context sizes.
  Missing numeric fields are `None`, including malformed/negative/bool values; zero
  remains zero and context increases remain visible. An absent map on an old archive
  means unknown, while an empty map on a parsed log means zero observed events.
  Session summaries/export carry the events. Date/tool/project/device filtering applies;
  model/provider filters cannot attribute an event with no recorded model. Such events
  only make a compaction-only day visible when no model/provider filter is selected.
  The drawer states the events' date-only scope when billed model filters are active.
  Completion can extend session start/end but never advances billed active time or
  creates a record/hourly usage row. Keep cumulative cost-state reconciliation separate.

- **Codex compactions use the same durable event view.** Record only top-level
  `type:"compacted"` checkpoints, keyed by `compaction_response_id`, then `window_id`,
  then UTC completion timestamp. Lifecycle `ContextCompaction` completions and
  `context_compacted` notices are twins, not extra events; starts/failed attempts
  add nothing. The existing fork replay guard excludes inherited checkpoints.
  Copied `replacement_history` can contain the large-line skip strings: checkpoints
  bypass that shortcut for event metadata only. Formerly skipped checkpoints must
  not advance/reset the old usage parser's replay clock. Never import copied messages,
  tools or `latest_token_usage_record` (a resume snapshot, not a new model request).
  Type and context sizes are not recorded by Codex and remain unknown. Some
  ContextCompaction completion records carry started_at_ms/completed_at_ms.
  Duration uses their difference only with an explicit top-level response_id ->
  turn_id/thread_id chain and a one-to-one match within that recorded interval.
  Missing/invalid timing, mismatched scopes and overlapping candidates stay unknown.
  The drawer labels missing trigger/context fields "Not recorded" and unmatched
  duration "Unavailable"; never present a known completed event as unknown or infer
  manual/auto from adjacent prompts. Timing conflicts remain sticky while retained;
  replayed checkpoints whose correlation state was evicted cannot be rebound.
  An ambiguous interval clears prior durations even for evicted checkpoints.
  Lifecycle records enrich existing checkpoints only; never count them again.
  The bounded private parser state compaction_timing.version backfills available
  Codex logs once, transactionally, without changing event/aggregate shape or cache
  version. Archived metadata stays unchanged. Never rebuild to recover durations.
  Real compaction `token_usage_record` usage stays in the existing token/cost totals;
  events add no usage. Old archived/mirrored entries missing metadata stay unknown.

- **One Claude response is several records, and a resume can replay them all.** Claude
  Code writes one `type:"assistant"` record per content block (thinking, text, each
  tool_use), each repeating the WHOLE response's `usage` — so tokens, cost and "assistant
  msgs" count once per `(message.id, requestId)`, and a later block adds only what grew
  (`output_tokens` streams upward: 1 on the first block, 388 by the last). Tool calls
  stay per record — each record carries its own block. Separately, resuming a session
  can rewrite its whole history into the same file (same `uuid`s and timestamps, newer
  `version`), so any record whose `uuid` was already seen in that file is skipped —
  `state.seen_uuids`, 12 hex chars per record, persisted for incremental parsing.
  Summing records instead inflated Claude usage ~2.3x (Sep 2026: 3.16B for 1.35B billed).
- **A session's model is ranked on the whole file, never on what one parse saw.** A log
  is parsed in pieces as it grows, so a tally kept inside `parse_claude` holds only the
  newest piece: a session of 2.2M Opus tokens was labelled "Sonnet 5.5" after 15 records
  of it, and listed first in `models`. `dom_model` comes from the file's own `records`,
  as Codex's already does. A session nobody has appended to since keeps its
  old label until it is.
- **A Claude "prompt" is only what the user typed.** Newer builds stamp `origin.kind` on
  a user turn (`human`, `task-notification`); anything not `human` is dropped. So is every
  `isMeta` record — screenshot captions (older builds omit `turnCompanion`), skill bodies,
  slash-command expansions, "Continue from where you left off." after a limit stop — plus
  `isCompactSummary`, and records opening with a slash-command wrapper,
  `<task-notification>` or "[Request interrupted by user". Slash commands never count, a
  queued "/compact" included. Assistant records with model `<synthetic>` are Claude Code's
  own notices (zero usage), not replies. Before this, ~14% of prompts were never typed.
- **A prompt typed while Claude is working is not a `type:"user"` record.** Claude Code
  queues it and writes `type:"attachment"` with `attachment.type == "queued_command"`,
  so the user branch never sees it — that silently undercounted prompts by ~23%.
  Count it, but only `commandMode == "prompt"` (`task-notification` is the harness
  telling itself a background task finished), with non-empty text that isn't an
  `<ide_opened_file>` / `<system-reminder>` injection riding the same channel. Do NOT
  dedupe against `user` records by text: the same words seconds apart are usually the
  user pressing enter twice, and `source_uuid` does not link the two. Codex needs none
  of this — it logs a steer as an ordinary `UserMessage`.
- **User turns** land in two different places: Claude/Claude Desktop/Codex write them
  to a `(user)` marker row in `records`; Copilot/Cursor write them onto the model row.
  Neither writes both, so summing `r.user` across all records is correct — but a check
  that assumes one convention will report a phantom bug.
- **`reason` is a SUBSET of `out`, never additive** — Claude's
  `usage.output_tokens_details.thinking_tokens` and Codex's `reasoning_output_tokens`
  are both already inside `output_tokens`. The UI shows it as "of which reasoning"
  without stacking; anything that adds it to a token total is double-counting.
- **`cc5 + cc1` can disagree with `cc` by a few hundred tokens.** Anthropic itself
  occasionally logs `cache_creation_input_tokens: 0` alongside a non-zero
  `cache_creation.ephemeral_1h_input_tokens`. Both are recorded as-is; cost uses the
  tiered fields. Seen once in 813 rows, worth ~$0.003 — upstream, not ours.
- **Claude request options become model rows.** `usage.speed == "fast"` appends
  `FAST_SUFFIX` (" (fast)") and `usage.inference_geo == "us"` appends `US_SUFFIX` (" (US)").
  `price_of()` strips them: fast mode reads `FAST_PRICING` (a model with no published fast
  rate prices at zero, not at a guess) and US-only multiplies every rate by 1.1. Web
  searches (`server_tool_use.web_search_requests`) land in `ws` and `_cost()` adds
  $10 per 1,000. None of the three occur on this machine yet — they are for other users.
- **Cache writes with no published write rate bill at the input rate**, never $0 — `_cost()`
  falls back to it. Most OpenAI rows have cw5 = 0; GPT-5.6 and GPT-6 list 1.25x input.
- **`req` is one billed model call**, `asst` one visible reply. Codex, Copilot and Hermes
  fill `req`; for Codex both are non-zero and differ (a reply can take many calls), so the
  Sessions drawer shows "Model calls" separately rather than through the `asst||req` fallback.
- **`side` vs `subagents`**: Claude folds subagent tokens into the parent's stream
  (`side`); Codex spawns wholly separate sessions and only the parent's spawn COUNT
  (`subagents`) is knowable. Check both when asking "did this session delegate".

## Per-source quirks

- **Codex has (at least) two incompatible rollout schemas.** Older/stable CLIs emit
  flat `event_msg` payloads (`agent_message`, `user_message`, `token_count`). Recent
  alpha builds (seen: `0.151.x`) wrap turn content in one `item_completed` event
  whose own `item.type` names the real kind (`UserMessage`, `AgentMessage`,
  `Reasoning`, `CommandExecution`, `SubAgentActivity`, ...) — `token_count` and the
  `response_item` tool-call events are unchanged across both, which is exactly why a
  schema mismatch here degrades quietly: tokens/cost/tools keep working while
  prompts/messages silently zero out. `parse_codex` handles both; if Codex ships a
  third shape, check `event_msg` payload types in a fresh rollout file before
  assuming the existing branches still apply.
- **Codex compresses idle rollouts.** In `sessions/**` and `archived_sessions/**` an idle
  thread's `rollout-<stamp>-<thread id>.jsonl` becomes the same name plus `.zst` (one
  zstd frame, no checksum: 180 files, 961 MiB packed / 1.8 GiB of text, the largest
  380 MiB, here). Both spellings are discovered (`CODEX_GLOBS`) and are ONE
  conversation. A compressed file is immutable, so `update_file()` hands it to
  `_update_codex_compressed()`: always read in full, keyed by size/mtime, never from a
  byte offset, streamed (`zstd_stream` / `_zstd_lines`, memory follows the longest
  record: the 380 MiB log peaks near 300 MiB where the plain path needed 1.3 GiB) and
  committed only after every frame decoded, so a failure leaves the previous entry
  (usually the rollout's archived plain one) as it was. `entry["size"]` is the bytes on
  disk, as Storage lists them. The same `_codex_stale()` markers force a full re-read.
  **Decoding has one seam, `_zstd_backend()`**: Python 3.14's `compression.zstd`, else the
  `zstd` command, else nothing (OpenClaw's `_zstd_decode_many` uses it too). The bundled
  macOS and Windows runtimes are CPython 3.15.2, which has `compression.zstd`, so the
  installed apps decode with no `zstd` command; a source checkout on Python 3.13 or older
  still needs the command. Without either, `update_file()` raises
  `ZstdUnavailable`; `_refresh_locked` counts it (`_meta` / payload
  `codex_compressed_unread`, one stderr line with no path whenever the count changes, the
  sidebar note, the Settings hint) and creates NO entry and NO log error: nothing in the
  ledger is dropped or zeroed, and the archived plain entries keep counting. A damaged
  or truncated file raises `ZstdReadError` through the normal parse-failure path
  (stderr, diagnostics "could not be parsed"), is not decoded again until its size or
  mtime changes (`_ZSTD_BAD`), and never commits a partial read. A zero-byte `.zst`
  (still being written) is skipped and never replaces an entry. Lines split on `\n`
  ONLY: the plain path's `str.splitlines()` also splits on U+2028/U+2029/U+0085, which
  Codex writes unescaped, and drops those records (one rollout here lost a prompt that
  way), so do not "fix" the compressed reader to match it. The session timeline reads
  the same stream, bounded at 64 MiB of decoded text; without a decoder it reports the
  log unavailable. Cleanup commands and "reclaimable" include `*.jsonl.zst` only where a
  decoder exists: never offer an unread log for deletion. Session ids, titles, IDE and
  subagent detection need no special case: `session_meta` is read the same way.
- **Codex usage comes from `token_usage_record` once a rollout has one.** Builds from
  2026-09 log one per model response (with a `response_id`), written before its
  `token_count` twin. `token_count` alone misses a compaction request (it logs zeros
  after `compacted`) and a response cut off mid-turn. Before the first record, and in
  older rollouts, `token_count` is used — but an event whose (`total_token_usage`,
  `last_token_usage`) pair was already seen in the last 32 is a re-emission, not new
  usage: Codex repeats the previous one when a turn starts, and two Codex processes on
  one thread interleave two running totals in the same file, so a repeat can land a few
  events late (776 had double-counted 116M tokens). To check the arithmetic, chain each
  event onto the counter it continues and compare to Codex's own totals — neither
  `threads.tokens_used` in `state_5.sqlite` (it resets, and omits compactions) nor a
  `guardian_review` rollout's total (forked from the session it reviews, it starts with
  that session's total on the clock) can be compared directly.
- **A forked Codex thread opens by replaying its parent.** `/fork` and every subagent
  (Codex forks subagents from the parent) write `session_meta.forked_from_id`, then rewrite
  the parent's whole history into the new file within ~0.2s: token_counts, replies,
  prompts, tool calls, even spawn markers. `parse_codex` skips that burst — it ends at the
  first gap over 1s (`state.replay_last`) — keeping only `turn_context` for the inherited
  model. One `/fork` here replayed 73 usage events, matching its parent's running total to
  the token. Don't use a fixed window: real work resumed 3.5–4.8s after `session_meta` in
  every fork seen, so a fixed 5s cutoff would also clip the fork's own first turn.
- **Codex cache writes are carved out of input.** `cache_write_input_tokens` is a subset of
  `input_tokens`, disjoint from `cached_input_tokens` (in >= cached + written in all 35,736
  events checked), so it moves from `in` to `cc`/`cc5` and bills at the write rate.
- **Codex's built-in tools are `response_item`s of their own type** —
  `web_search_call`, `tool_search_call`, `image_generation_call` — not function calls and
  never an `event_msg`. Web searches were uncounted until they were read from there.
- **Codex's session title lives outside the rollout.** `~/.codex/session_index.jsonl`
  maps thread id → `thread_name`, and that is the name Codex's own UI shows. It is
  append-only, so a renamed thread gets a NEW line and the LAST one wins — same
  shape as Claude's `aiTitle`. Applied at rank "ai" so it beats a prompt snippet.
  ~80% coverage here; threads with no entry keep the first-prompt title. Do NOT
  reach for the editor's `agentSessions.model.cache` instead: it holds the same
  string but appears and vanishes within seconds while VS Code runs.
- **Codex subagents self-identify** via `session_meta.thread_source == "subagent"`
  in the CHILD's own file (plus `parent_thread_id`, `agent_path`, `agent_nickname`)
  — no cross-file correlation needed, unlike Claude Code. A subagent's task is
  usually never an in-band `UserMessage` in its own log (it arrives at spawn time),
  so `_finalize_session` falls back to the leaf of `agent_path` as its title, and
  only when no real prompt was ever found. The PARENT's own file separately counts
  `SubAgentActivity` "started" markers into `subagents` (a count, the same field
  Cursor uses) — read that, not `side` (which Codex never sets: its subagents are
  wholly separate sessions, not sidechain records mixed into the parent's stream).
- **Cursor** (`state.vscdb`): `cursor_data.py` reads bounded, read-only snapshots of
  `composerHeaders`, `composerData:*` and `bubbleId:*`. Explicit workspace mappings
  come from sibling `workspaceStorage` metadata; cwd remains private. Preserve full
  composer IDs, per-message dates, safe tool labels/status, explicit parent links and
  recorded usage only. Current model/context settings are separate from historical
  response/model/token attribution. Zero token placeholders mean unavailable, not
  free usage. `agentKv` enrichment requires unambiguous request/tool-call IDs; no
  arbitrary row-order inheritance, text-length estimates, context-meter billing or
  DB-mtime activity dates. Unknown binary detail stays unavailable. Timeline output
  is whitelisted, bounded and resolved from the full ledger identity/discovered DB.
  Private hashed bubble summaries preserve telemetry after source tombstones;
  restored rows replace their original contribution. Undated receipts remain
  whole-session supplements outside dated totals. Cache 63 retains v53–62 ledgers
  and v52–62 peers; do not rebuild.
  Cursor peer exports use explicit safe allowlists and opaque store keys, never
  workspace paths, raw events, request joins, blobs or source content. See
  docs/cursor.md and docs/cursor-codeburn-comparison.md. Metadata-only activity
  remains visible separately from Overview's token/cost shares.
- **Copilot** logs real `promptTokens`/`completionTokens` on finished requests in current
  VS Code builds; older chats have none, so their tokens are estimated from message text
  (chars/4). It also logs a premium-request multiplier in `result.details` ("… • 1x") —
  that's a separate billing unit, never USD. SDK/CLI/Agent Host sessions use
  `~/.copilot/session-state/*/events.jsonl`. `_copilot_sdk_hosts()` indexes canonical
  UUID directories at `<editor>/agentSessionData/*/session.db`; join
  `session_metadata.defaultChatProviderData.sdkSessionId` to the SDK id, then
  `turns.event_id` to the SDK user event and `turn_usage.turn_id` to `turns.id`.
  Prefer `_meta.turnTokenTotals` (inclusive of subagents); top-level usage is only
  the last call, never a whole multi-call turn. Do not add direct/subagent totals
  or contextAttribution sizes on top. Only main-agent messages count as replies;
  tool request/execution twins and replayed event IDs count once. Exact-only keeps
  covered-turn activity separate from missing-turn activity. SDK usage is never
  estimated from text. Read-only DB snapshots track WAL changes even without an
  appended SDK event. Preserve recovered numeric-only turn snapshots if the
  auxiliary DB disappears. Malformed records leave the old aggregate retryable.
  `assistant.usage` is normally ephemeral; persisted copies are usable. Cumulative
  snapshots must never be summed with persisted `session.usage_record`
  receipts. SDK parser revision 2 deduplicates per-call receipts and live twins,
  uses complete dated receipts only when they cover host totals, and re-anchors
  stale origin ids from explicit user/assistant turn ids (reset on reused counters).
  Compaction `threshold` / context-limit / memory-pressure / model-switch triggers
  retain their meaning; completion tokens count once, including between turns.
  Available SDK logs reparse on revision changes; archived entries stay untouched.
  Shutdown totals are cumulative, not additional calls. Multi-day shutdown totals have no
  per-day attribution and stay supplemental metadata. No SDK attachment/resume,
  model request, credential read or automatic OTel setting change. Source ids and
  palette stay `copilot`; unavailable/partial/error sessions carry a tooltip.
  Cache **58** adds optional SDK coverage metadata and retains all v53–57 entries;
  peers 52–57 remain readable. Cache 61 retains all v53–60 entries and peers 52–60.
  SDK revision 3 backfills the private parent cwd and named, explicit agent IDs.
  Copilot child receipts/compactions are inclusive breakdowns, not extra summary usage.
  Agent desk projects these as linked child rows and marks unavailable/partial usage.
  Parent and child compaction timing is isolated by agent ID. Never rebuild.
  See docs/copilot-sdk.md.
  Still not read: JetBrains and `GitHub.copilot-chat/transcripts/` (turns only).
- **OpenCode**: current versions use one SQLite `opencode.db`; older ones use
  `storage/message/<session>/msg_*.json`. All are read. The DB parser supports both
  `session/message/part` (V1) and `session_v2/session_message` (V2). V2's message role
  is a column; model identity is `data.model`, and tools are in assistant `content`.
  Prefer the V2 copy of migrated sessions; keep V1-only sessions and V1 history for
  empty V2 migration stubs. V2 rows are ordered by session/time/sequence. Fork-copied
  rows preceding the fork session's creation are excluded, matching OpenCode's own
  stats even when its parent is gone. Idle/system/synthetic rows are not user prompts.
  Bad V2 JSON fails the parse so the previous ledger survives and retries. Only the DB records a real
  per-message cost, so cost routing keys on whether the aggregate's path ends `.db`.
- **Gemini CLI** is deliberately not parsed — its `chats/*.jsonl` hold only session
  bookkeeping, no prompts/tokens/model.
- **OpenClaw** (`$OPENCLAW_STATE_DIR`, default `~/.openclaw`; the former names `~/.clawdbot`
  and `~/.moltbot` too): one aggregate per AGENT directory, `agents/<id>/`, fully reparsed
  when its signature (DB, `-wal`, every transcript) changes. Current builds keep every session
  in `agent/openclaw-agent.sqlite` — `transcript_events(session_id, seq, event_json,
  event_zstd, ...)`, titles from `session_nodes.label` / `display_name`, the model from
  `session_windows`. Events of 1 KB or more are stored zstd-compressed with `event_json`
  NULL; `_zstd_decode_many` uses Python 3.14's `compression.zstd` or one batched run of the
  `zstd` command, and what can't be decoded is COUNTED and shown ("N OpenClaw events
  unread"), never estimated. Older builds wrote `sessions/<sessionId>.jsonl` (plus the
  `.jsonl.deleted.<ts>` archives the SQLite migration leaves behind) — read only for a
  session id the database doesn't have, or a migrated install counts twice.
  Event shape either way: a `session` header (`cwd`), then `message` entries; an assistant
  message's `usage` is `{input, output, cacheRead, cacheWrite, cacheWrite1h?, reasoning?,
  cost.total}` where `input` EXCLUDES cache reads and writes (OpenClaw's model layer
  subtracts them from the provider's prompt count) and `reasoning` is a subset of `output`.
  A fork copies its parent's messages, so a response is counted once per agent, keyed by
  (message timestamp, model, token counts) — and the check runs before any day/start
  bookkeeping, or the copy gives the fork its parent's start date. `usage.cost.total` is
  OpenClaw's own estimate: `_cost()` uses it only for a model with no `PRICING` row.
- **Hermes Agent** (`~/.hermes/state.db`, `$HERMES_HOME`, or `%LOCALAPPDATA%\hermes`): one
  SQLite store for all sessions. Unlike Cursor it logs a real per-model in/out/cache/
  reasoning breakdown in `session_model_usage` (hence `exact:true`), one row per model a
  session actually used — sessions can switch model mid-way, like Codex. `messages.tool_calls`
  is an OpenAI-shaped JSON array, read only for per-day tool and turn counts; message
  `content` is never read.
- **Antigravity** reads only direct `*.db` files under the exact App, IDE and CLI
  `~/.gemini/antigravity/conversations`, `~/.gemini/antigravity-ide/conversations`,
  and `~/.gemini/antigravity-cli/conversations` roots. One database is one conversation;
  duplicate copies under those roots share a full UUID and are deduplicated per device by
  the fullest ledger entry. Duplicate usage statistics in the companion table are not
  added. `generation_idx` and `step_idx` are different identities and link only through
  the explicit packed step-index mapping. Unknown-model usage counts once as `Unknown`
  without invented pricing; reasoning remains a subset of output. The bounded reader
  returns whitelisted timestamps, event kinds, model/tool labels, safe statuses, and
  numeric usage, never prompts, arguments, raw blobs, or account data. Timeline detail
  resolves only the full ledger identity and a rediscovered source path. A recorded
  `subagent: true` plus an explicit immediate `parent_id` is required for a parent link;
  `root_id` alone is not enough. `intent_only` checkpoints remain checkpoint events,
  never compactions. Standalone legacy `.pb` files are unsupported and unknown schemas
  report a parse error. No hooks are installed and no provider is contacted. The public
  [hooks docs](https://www.antigravity.google/docs/hooks) describe a separate
  `transcriptPath` under `brain/`; it is outside this parser. See docs/antigravity.md.
- **SQLite stores**: always open via `_open_ro_sqlite()`. Neither flag is safe alone —
  `mode=ro` reads the `-wal` (so a *running* tool's newest sessions are visible) but must
  create a `-shm`, which fails on read-only media; `immutable=1` needs no `-shm` but ignores
  the `-wal` entirely. The helper tries the first, probes it with a query (connect is lazy),
  and falls back to the second.
- **Live SQLite refreshes must track the WAL too.** Cursor and Hermes use a signature
  of the database and its `-wal`, like OpenCode. A committed message can change only the
  WAL until the writer checkpoints; checking just the main DB leaves the dashboard stale.

## Common tasks

**A model shows $0 / an unknown name** → in `parser.py`, add
`PRICING["<Display Name>"] = (input, output, cache_write_5m, cache_write_1h, cache_read)`
(USD per 1M; OpenAI rows put the cache-write rate in cw5 — 1.25x input where the pricing
page lists one, else 0, which bills at the input rate — 0 in cw1, and the cached rate last), and
make the normalizer map the raw id to that name. Pricing applies at request time — no
re-parse needed for a flat rate; adding request-tier metadata (Haiku 5.5) or changing
a normalizer needs a `CACHE_VERSION` bump and a normal restart
to reparse live logs while preserving archived entries. Never use `--rebuild` for a
routine migration: it discards the durable history.
A new `claude-<tier>-<n>-<m>` id normalises by itself, so only the price row is missing and
nothing warns: the model just costs $0 (Sonnet 5.5 did, for its first day). Diff the vendor's
whole table against `PRICING`, and look for all-zero rows in the payload's `prices`.

**A vendor changed a price** → the new tuple goes in `PRICING` (always today's rate) and the old one moves into `PRICE_HISTORY` with
the last date it applied. `price_of(model, date)` picks by each record's date, so a cut
never re-prices history. Record a change only once it has taken effect — never pre-encode
an announced future price: Sonnet 5's scheduled $3/$15 increase was encoded ahead of time,
then cancelled by Anthropic, and silently overbilled every day after 2026-08-31 by 50%.

**Add a tool source** → `parser.py`: paths, emit from `discover()`, write `parse_<tool>()`,
route in `update_file()`. Then `SRC`/`ORDER` in `core.js`, a `--t-<source>` colour in
`app.css` (re-validate the palette), bump `CACHE_VERSION`, update README + this file.

**Test a UI change** → headless Chrome catches render failures; both uncaught and caught
errors land on `document.documentElement.dataset.jsError`:

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
  --virtual-time-budget=9000 --dump-dom "http://127.0.0.1:7878/#cost" | grep data-js-error
```

Sweep every tab at `?range=today` too: a line chart needs **two** points to draw a segment,
so single-day ranges render as empty axes unless the dataset uses `pointRadius: soloPoint(data)`.
Add `--screenshot=out.png --window-size=1560,2000` to eyeball it.

## Setup inventory in the payload

This repository ships no recommendation view. The parser and `build_payload` still produce
the setup inventories below (`ctx`, `reads`, `installed`, `context_files`, `skills`,
`mcp_servers`, `mcp_inventory`, `codex_effort`) because they are ledger-backed and part of
the payload shape; an optional extension (see "Optional extensions" under Layout) may judge
them. Keep their shape stable, and never remove a field without considering archived entries
and `CACHE_VERSION`.

**MCP attribution differs by tool.** Claude Code names MCP tools
`mcp__<server>__<tool>`; Codex keeps the bare tool name and puts the server in a
separate `namespace` field (`"mcp__azure"`). `parse_codex` normalises to Claude's
shape — without that, an MCP tool is indistinguishable from a built-in and every
Codex server looks unused. Configured servers come from `~/.claude.json` and the
`[mcp_servers.*]` blocks of `~/.codex/config.toml` (parsed by regex, not tomllib,
which is 3.11+).

**Setup inventories** (payload fields in brackets): instruction files over 8 KB, with the
bytes actually sent and the requests that carried them
(`context_files`, sizes only, never contents). `_context_files` follows each agent's own
loading rules, which differ:
- **Claude Code** reads ~/.claude/CLAUDE.md, then `CLAUDE.md`, `CLAUDE.local.md` and
  `.claude/CLAUDE.md` in the cwd and *every* directory above it, home included. From
  2.1.277 a project with none of those gets its `AGENTS.md` instead (both with the
  `agents-md@builtin` "claude-md-and-agents-md" setting). That entry carries `since`, so
  older requests aren't priced. A file records only its newest version (`cliver`), so
  `since` is the earliest *last* day among the sessions there that ran 2.1.277+: it can
  start late, never early. Before this, this repo's own 37 KB AGENTS.md was never counted.
- **Codex** reads `~/.codex/AGENTS.override.md`, else `AGENTS.md`, then walks from the
  git root DOWN to the cwd. It takes at most one file per directory (the override wins)
  and stops at `project_doc_max_bytes` (32 KiB) in total, so `sent` can be less than
  `bytes`. Walking up past the git root counted files Codex never reads.

The other inventories: sessions that open heavier than the user's own leanest 10% (`open_ctx` on each
session: the first request's full input); re-reads of unchanged files and reads inside
build / dependency folders, with the tokens they returned (`reads`, Claude Code — note it
already answers an unchanged re-read with a short stub, so this rarely fires); installed
skills and subagents unused over ≥ 14 days (`installed`, description lengths only); MCP
servers used for ≤ 10% of the tools they offer, or used in one project while loaded in
several (`mcp_inventory.loaded/calls`, per project). Inventory calls retain their
source and distinct tool names before the top-tools table folds its long tail into
"(other)"; a zero-call claim must use that complete inventory.

The payload carries three signals most tabs don't use: `attributionSkill` (which Skill
drove a request — this is how "/dataviz cost you $20" is possible), a per-request
context-size histogram (`agg["ctx"]`, bucketed 0-50k / 50-150k / 150-400k / 400k+),
and the MCP servers configured in `~/.claude.json` (`_mcp_servers()`), comparable with
`mcp__<server>__*` tool calls. Server names are matched loosely — the same server
appears as `claude-in-chrome` and `Claude_in_Chrome` across versions, and
`google-workspace` shows up in tool names as `workspace`.

## Activity — what each prompt was for, and whether its edits landed

`agg["activity"]` (`"date\tmodel\tcategory"` → turns, edits, oneshot, retries and token
fields) comes from the ACTIVITY block in `parser.py`; the payload ships it as `activity`,
costed per row by `_cost()`.

- **Classified by what the agent did, not by guessing from words.** `_classify_turn` looks
  first at the evidence: an edit makes it a build/fix/refactor turn — or `docs`/`test` when
  every edited file is documentation or a test (`_file_kind`); a spawned agent is `delegate`;
  otherwise the shell commands it ran decide (`_cmd_kinds`: a test runner → `test`, a
  writing `git`/`gh pr` → `vcs`, an installer/builder/deployer → `ops`), then the tool mix
  (`mcp` → `data`, web → `research`, reads/shell → `explore`). The prompt's wording only
  settles what an edit was FOR and what a tool-free turn was: `_prompt_intents` scores
  weighted word lists and the highest total wins, ties by declaration order.
  Categories: build · fix · refactor · test · docs · review · explore · research · data ·
  plan · delegate · vcs · ops · chat. `_LEGACY_CATEGORY` maps ids older caches used.
- **Tool kinds come from the names each agent actually logs** (`_TOOL_KIND`). Codex's
  `exec`/`exec_command`/`write_stdin` are shell, `apply_patch` edit, `update_plan` plan,
  `view_image` read; a Codex subagent spawn is its `SubAgentActivity` "started" marker.

- **A turn is one typed prompt plus everything until the next one.** It opens at the same
  places prompts are counted (`_turn_open`), so the prompt filters above decide what a turn
  is. A queued steer does NOT open a turn — it redirects the running one. A subagent's file
  never opens one: its work is the parent's delegation turn. A Claude session that replies
  before its first counted prompt (it opened with a slash command, `isMeta` text or a task
  notification) gets an *implicit* turn (`imp`): its tokens are classified and costed, but
  it adds nothing to turn, edit, one-shot or retry counts, since nobody typed a prompt.
  Without it that work reached no Activity row at all.
- **The prompt's text never reaches the cache.** `_prompt_intents()` scores it on arrival
  and keeps only small per-intent numbers; the running turn persists in `state.turn` (incremental parsing).
  `activity_of()` adds the still-open last turn read-only, since nothing will close it.
- **Retry (rework)** = a file edited again after a *check* ran — a command that tests,
  builds, lints or runs the project (`_cmd_kinds` → "check"; a lookup like `rg`/`cat`/`git
  log` never is). Each check marks every file edited so far; editing a marked file counts
  one and re-arms it. `oneshot` = an editing turn with none. `retry_cost` is the whole turn's cost, not the
  redo alone, so a consumer compares it with `edit_cost` (all editing turns) to get an
  excess from the user's own averages rather than a guessed fraction.
- **Codex edits come from `patch_apply_end` / `item_completed:FileChange`**, the one place
  every build records each file a patch touched — Codex Desktop applies patches from inside
  its `exec` JS tool, which never shows as `apply_patch`. Once a rollout shows either event,
  `apply_patch` calls stop counting for activity so an edit isn't counted twice. The very
  first event follows the `apply_patch` call that was already counted, under the name the
  patch text used (usually relative, the event's is absolute), so `_codex_edit_event`
  re-keys that file instead of counting it again. Otherwise 10 of 37 rollouts here held the
  same file under two names. `exec`
  programs are scanned for their `cmd:` strings so their commands can be judged; one whose
  commands can't be read is neither a check nor a lookup.
- Turns are attributed to the model that answered most; tokens split by the model that
  actually produced them.

**MCP inventory** (`mcp_inventory` in the payload): Claude Code announces the tools each
session is offered as `type:"attachment"` / `deferred_tools_delta` records (names only —
full definitions load when searched for). `agg["mcp_offered"]` keeps {server → first date,
tool names}; the Tools tab's MCP table reads used / offered and sessions loaded from it.

## Active time — a gap-capped estimate, not wall-clock length

`records`/`sessions` carry `active` seconds: the sum of gaps BETWEEN consecutive real
turns, counted only when the gap is <= `ACTIVE_GAP_CAP` (300s, `parser.py`) — the same
heuristic WakaTime/RescueTime use. It is a lower bound on time actually spent driving
the tool, deliberately not `end - start`: a Codex session resumed after three days away
must not report three days of "active" time. Formatted client-side by `fmtDur()`.

- **Computed at the same choke point every source already shares**: `_bump_time()`
  receives a real timestamp at every one of its 11 call sites, so `_active_gap()` sits
  right next to it rather than duplicating cap logic per source.
- **Single-session sources** (Claude/Claude Desktop, Codex, Copilot, legacy OpenCode —
  one file per session) persist the last-event timestamp on `agg["_active_last"]`,
  because Claude/Codex/Copilot-jsonl parse incrementally (byte-offset resume): the
  function only ever sees the NEW lines, so the previous timestamp must survive across
  calls or every incremental chunk looks like the start of a fresh burst. A fresh
  `_blank_agg` (full reparse) correctly resets it to `None`.
- **Multi-session sources** (Cursor, OpenCode's SQLite DB, Hermes — one store holding
  many unrelated sessions) use a LOCAL dict keyed by session id instead, never
  `agg`-level: these are always fully reparsed from scratch on change (see
  `update_file`), so nothing needs to persist, and persisting at the `agg` level would
  incorrectly bridge a gap across two different sessions in the same store.
- **Not every timestamp in a source is a real turn.** Hermes' `session_model_usage` is
  one SUMMARY row per (session, model) pair — its `first_seen`/`last_seen` span the
  whole pairing, not a single turn — so it must never feed a gap calculation; only the
  `messages` table's per-row timestamps do. Codex's `token_count` event and Copilot's
  `_copilot_apply_request` ARE legitimate per-turn signals (same events `_bump_time`
  already used for the activity heatmap), so both participate normally.
- **Copilot legitimately measures near-zero.** Its interactions are one-shot
  completions, not an agentic tool loop, so consecutive events are usually well over
  the 5-minute cap apart. That is the heuristic doing its job, not a parsing gap.
  Completed mutation-log requests are finalized in their recorded request order;
  iterating a set would make active-time gaps change between reparses.
- Cursor, OpenCode SQLite, Hermes and OpenClaw retain per-session `records` keyed by
  date/model. Payload records use each session's project; session days price each
  model and cache tier before summing. `model_days` allows filtering secondary models.
  Copilot additionally retains an `exact` contribution per record, with `exact_days`
  and `exact_model_days` in the payload; exact-only excludes its estimated component.
  Older archived stores cannot recover missing detail: show `detail_limited`, never
  fill a session's days with the whole database's usage.
- The positional `s.days[date]` array preserves its first ten slots and appends fields:
  `[cost,in,out,cr,cc,asst,user,tools,prem,active,req,cc5,cc1,reason,ws]`.
  `static/core.js`'s `clipSession()` also reads older ten-slot entries.

## Your devices — optional local-network sharing

Settings → **Your devices** combines two (or more) of the user's own computers over the
local network. Both switches are off until the user turns them on, and each asks first
(an `askConfirm()` dialog that says exactly what is sent). Everything lives in
`dashboard.py`'s Devices section.

- **Share** starts a *second* `Server` on `0.0.0.0:7879` (`share.port` in `.peers.json`)
  with `PeerHandler`, which answers ONE route, `GET /api/peer/export`, and only with
  `Authorization: Bearer <pairing code>` (`hmac.compare_digest`; a wrong code waits 1s).
  The main dashboard never leaves `127.0.0.1`. Don't bind it to the network instead: it
  has no auth, and `/api/update`, `/api/settings` and `/api/cache` would be exposed.
- **An export carries only this machine's own aggregates** (`_state["files"]`), never
  the copies it pulled. That is what lets two devices connect both ways without counting
  each other twice. Each aggregate passes through `_export_agg`, a whitelist: no parser
  resume state, no `cwd`, no MCP/skill setup. `ide` and `activity_of()` are resolved on
  the exporting side, because Codex's editor lookup reads that machine's editor storage.
  The body is gzip'd JSON with an ETag (`_gen`, bumped by any refresh that changed
  something), so an unchanged pull is a 304. **"Changed" is where the parse stopped
  (`_parse_mark`: size, mtime, offset), not whether `update_file` returned a new dict.**
  An appended Claude/Codex/Copilot log is parsed in place and comes back as the same
  object, so the identity check alone never saw a running session grow: the ETag stayed
  put and the other device showed the snapshot from when it connected (16.1M there,
  22.3M on the machine itself). The same check decides when the cache is saved: a log
  that only grew is saved at most every `CACHE_SAVE_EVERY` (300s), since a restart
  re-reads it from the saved offset anyway.
- **Connect** pulls every `PEER_PULL_EVERY` (60s) seconds (`peer_puller`) and saves the
  copy to `.peers/<device id>.json` (0600). While the other machine sleeps, the copy still
  shows, along with the error explaining why it couldn't be reached. An export or saved
  copy with an incompatible `CACHE_VERSION` is refused with "update both". The
  version-59 reader explicitly accepts versions 52–58, whose missing optional
  precision/attribution/compaction/pricing fields retain the older detail limits; other
  versions are refused.
- `build_payload` adds the copies to its file loop as `peer:<id>:<path>` with
  `agg["_device"]`, keys everything by device the way it already does by IDE, and prices
  them with THIS machine's `PRICING`. Setup data (MCP inventory and calls, installed-skill
  use, instruction-file cwds, OpenClaw's undecoded count) is skipped for remote copies.
  A consumer of that setup data must judge only this machine's usage against this machine's
  files: another device has its own files, which never leave it.
- The client's `passDev()` filter and the "on all devices" phrase (`#qDev`) appear only
  when `RAW.devices` has more than one entry. A single-device install looks exactly as
  it did before.
- Overview adds one **By device** horizontal bar chart when multiple devices are
  present. It uses filtered records and the global measure, literal device labels,
  the existing neutral bar colour, and a table twin.
- No encryption: the pairing code keeps others on the Wi-Fi from *reading* the export,
  not from sniffing it. The UI says so.
- **The address shown under Share is every address ranked, not the route to the
  internet.** A VPN takes that route over: on Windows with 1.1.1.1's WARP on, it answered
  `172.16.0.2`, which nothing on the Wi-Fi can reach, so connecting from the Mac always
  failed. `_lan_addrs()` gathers the route's address plus every adapter's
  (`gethostbyname_ex`), then shows 192.168/16 and 10/8 first, and 172.16/12 and 100.64/10
  (VPNs, WSL, Docker, Tailscale) only when there's nothing better.
- **A connection survives a new IP.** Every authorized answer carries
  `X-AgentTelemetry-Addrs`, the sharer's current addresses. They're saved as the peer's
  `alts`, and `_pull_any` tries `last_addr`, then the typed `addr`, then those.
  `_reach()` probes each with a 5s connect first (the pull itself allows 30s for a big
  export), so a dead address fails fast, and it names the cause: a name that didn't
  resolve, a refused port, or silence.
- The device name goes in `X-AgentTelemetry-Device` URL-quoted. A header is Latin-1, and
  "Uttam’s MacBook Air" is not; unquoted, every connection attempt failed.

## Version and self-update

The sidebar footer shows the running version from this checkout's git metadata
(`VERSION` in `dashboard.py`: `git describe --tags`, commit, branch). Checking for updates
contacts the upstream only after the user clicks; there are no periodic update requests.
This is separate from opt-in device sharing and confirmed account lookups. `POST /api/update` (through
`_csrf_ok()`): `check` fetches the upstream and reports how far behind; `apply`
fast-forwards only — it refuses local edits to tracked files and commits the upstream
doesn't have — then saves the cache and re-execs the process (`_restart`), and the page
reloads once the new commit answers. A copy that isn't a git checkout shows no button.
The re-exec drops `--rebuild` (and any prefix argparse would accept for it) from the
arguments: it would delete the cache that was just saved, archived history included.

`_upstream()` asks the remote (`ls-remote`) whether the branch's upstream still exists, and
falls back to the remote's default branch when it doesn't. A feature branch that was merged
and deleted keeps its local tracking ref, because a plain `fetch` never prunes. Comparing
against that stale ref reported "Up to date" forever, while `main` had moved on.

`load()` keeps a fetch failure ("cannot reach /api/data") apart from a render failure: the
second sets `data-js-error` — it used to be reported as the server being down, which hid a
render bug from the headless sweep.

## Gotchas

- **Never use the browser's `confirm()` / `alert()` / `prompt()`.** Every dialog is the
  app's own: `askConfirm({title, body, ok, danger})` in `core.js`, a styled `<dialog>`
  that resolves true only on the confirm button. `body` is HTML, so escape anything that
  came from data. `danger:true` paints the button `--bad` for anything destructive.
  Results and errors go inline (`.stg-msg`), never in a popup. While a dialog is open it
  owns the keyboard, and Esc closes it alone, not the drawer behind it.

- **A link from another site opens the page, nothing else.** `_origin_ok()` refuses a cross-site
  `Sec-Fetch-Site` on every GET, except a top-level page load of `/` or `/index.html`
  (`_link_to_page`: `navigate` + `document`), so the Pro portal's "Open local dashboard" works.
  Frames, scripts, fetches and every `/api/*` read stay refused; the Host check always applies.
- **A link is untrusted input.** Whatever `filtersFromURL()` reads may have been written by
  another page, and this app has write endpoints behind only a same-origin check, so script
  injected through a crafted `?projects=` would get past it. Dates, tool ids, `range`,
  `metric` and `rate` are validated on the way in (`renderPills` calls `SRC[s].label`, so an
  unknown tool id would also throw); project, model, IDE, provider and search text only ever
  reach the page through `esc()` or `.textContent`. Keep it so for any new param.
- The **PWA service worker is opt-in** (gear menu, `localStorage` `aiu.pwa`) and never
  registered without consent — a service worker controls the origin until unregistered,
  and localhost ports get reused by other tools. It is network-first: the cache is an
  offline fallback only, never preferred, or `git pull` wouldn't take effect until the
  second reload. `/api/` is never intercepted.
- **Durable ledger**: sessions pruned from disk stay counted and are marked `archived`, so
  totals never silently shrink.
- **`--rebuild` is genuinely destructive on a machine with archived history** — it is not
  just a Settings-panel-only hazard (see above). Forgetting this once already wiped 251
  archived Copilot sessions from the running cache mid-session; recovered from the same
  `~/ai-usage-archives/usage_cache_backup_*.json` this file already tells you to make.
  Check `ls ~/ai-usage-archives/` (or wherever archives were made) BEFORE running
  `--rebuild`, every time — not just the first time.
- A `.dd-panel` that can overflow its `max-height` needs its most important action
  (here: the range picker's "Apply custom range") wrapped in `.dd-pin`, a
  `position:sticky` footer pulled into the panel's own padding — otherwise it silently
  requires scrolling to reach, which is how the custom date range historically hid its
  own submit button.
- Anything shown as a shell command must be built server-side from real discovered paths
  and `os.name` (`_cleanup_plan`) — never a hardcoded `~/Library/...` string.
