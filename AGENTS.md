# AGENTS.md

**AgentTelemetry** — a local, **stdlib-only** dashboard that reads the logs your AI coding tools already write
to this machine and shows tokens, estimated cost, and breakdowns by model / day / tool /
project / hour. Nothing leaves the machine unless the user turns on sharing (see "Your
devices"). Nothing to install.

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

## Native clients and installation (v2.0)

`python3 install.py` (macOS) / `python install.py` (Windows) detects the OS and installs
the corresponding native client for this user; `--no-launch` opts out of opening it.
macOS requires Swift/macOS SDK for source building and bundles hash-pinned CPython.
Windows uses Python `ctypes` and native Win32 APIs, with the installed `pythonw.exe`;
no third-party GUI dependency. Linux stays on the existing browser workflow.

- `native.py` owns the installed-client manifest, launch/quit requests and heartbeat
  protocol in the separate **AgentTelemetryNative** application-support directory.
  Do not put those files into the ledger directory: import requires an empty destination.
- `windows/tray.py` owns Win32 UI and its serialized worker queue;
  `windows/monitor.py` owns testable backend attachment, ownership, stale summaries and import.
- `macos/AgentTelemetryMac` owns SwiftUI/AppKit UI, app-owned Python and WKWebView.
  `NativeControl.swift` handles the same heartbeat/control-file protocol as Windows.
- Web Settings uses `GET/POST /api/native` to launch/quit the installed client. Its
  running status requires a live matching heartbeat. A pending/failed launch is not running.
  Both clients attach to port 7878. Closing a client never terminates an attached backend.
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
- Packaging must traverse nested Mach-O files with NUL-delimited filenames, sign them
  before their app container, verify signatures and never bundle user data. The Windows
  install payload also uses an explicit allowlist. Local ad-hoc macOS builds are not
  a notarized public installer; signed distribution has a separate credential gate.

Checks: `python3 -m unittest discover -s tests`, `node tests/test_frontend.js`,
`swift build --package-path macos/AgentTelemetryMac`, `python3 tests/run_mac_native.py`,
and on Windows `python windows/tray.py --self-test` plus `python install.py --no-launch`.
CI runs backend/frontend checks on Linux, macOS and Windows and native checks on their OS.

## Layout

| File | Role |
|---|---|
| `dashboard.py` | stdlib `http.server`. Serves `/`, `/static/*`, `/chart.js`, `/manifest.json`, `/sw.js`, `/api/{data,storage,refresh,settings,cache,update,devices}`. Owns the cache, the aggregate merge, `_cost()` and the device sharing listener. |
| `parser.py` | `discover()` lists log files; `update_file()` routes each to a `parse_*`. Holds `PRICING` and the model-name normalizers. |
| `static/core.js` | `SRC`/`ORDER`, state `S`, formatting, date ranges, filtering. |
| `static/charts.js` | Chart.js theming, `mk()`/`hbar()`/`areaDS()`, calendar + heatmap SVG. |
| `static/views.js` | The eight views, controls, events, boot. |
| `index.html` | Shell only: sidebar (device name, tabs, live/refresh/theme/settings), page header (period, range, metric, filters), card markup, an SVG icon sprite. |

Eight tabs in the sidebar: Overview · Cost · Models · Tools · Projects · Sessions ·
**Optimize** · Storage. Tab lives in `location.hash`. **Every filter lives in the query
string too**, so a reload, bookmark or pasted link opens the same view: `filtersFromURL()`
reads it at boot and `filtersToURL()` writes it after every `renderAll()`, both in `core.js`.
The params are `?range=` (a preset, or `custom` with `&from=&to=` as YYYY-MM-DD),
`?metric=` (`tokens|cost|messages|time`), `?rate=out`, `?tools=`, `?providers=`, `?models=`,
`?projects=`, `?ides=`, `?devices=` (each repeated once per value), `?exact=1` and `?q=`.
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
are the default measure — the Overview hero follows it, and cost is one of its tiles. Every chart has a table twin (`.tv[data-tv]`, `chartTable()`), and each view
leads with one hero figure — keep it that way rather than adding a second.

The device name at the top of the sidebar is `DEVICE` in `dashboard.py`: the OS's own name
for the machine (`scutil --get ComputerName` on macOS, `%COMPUTERNAME%`, `/etc/machine-info`
`PRETTY_HOSTNAME`), falling back to the bare hostname. It's in the payload as `device`.

Aggregates are keyed `records["date\tmodel"]`, `tools["date\tname"]`,
`hourly["date\thour"]` — **every dimension carries a date** so the UI can filter by range.
In the payload every usage row (records, sessions, activity, tools, hourly, ctx, skills,
reads) also carries a `device` id; see "Your devices".

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
   once made the whole opencode parser silently yield nothing. Write to stderr.
9. **Any write endpoint goes through `Handler._csrf_ok()`.** There's no auth, so any page
   the user visits can POST here; with `Content-Type: text/plain` it's a CORS simple
   request with no preflight. That was enough to set `cleanupPeriodDays=1` and make Claude
   Code delete transcripts. The guard demands a JSON content type and same-origin.
10. **Colours are a validated palette and `ORDER` in `core.js` is the safety mechanism** —
    adjacent pairs must clear CVD ΔE ≥ 8 and normal-vision ΔE ≥ 15 in *both* themes.
    Re-run the data-viz skill's `validate_palette.js` over the whole sequence after any
    reorder or hue change. Identity must never be colour-alone: keep legends and tooltips.

## The IDE dimension

`records` carry an `ide` — which editor/surface the work ran in — resolved once per
aggregate by `_ide_of()` and filterable like project or model. Each source records it
differently and none agree on spelling, so they collapse to a shared vocabulary:
Copilot's comes from *which editor's storage* the file sat in, Claude/Codex stamp an
`entrypoint`/`originator`, and Cursor/Claude Desktop/opencode/Hermes run in exactly one
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
- **`--rebuild` destroys archived sessions too**, not just the Settings buttons — it
  skips the cache entirely and re-parses from disk, and an archived session has no
  disk to parse. Back up `.usage_cache.json` before running it on a machine whose
  logs have been archived off.
- The Settings panel's **Rebuild / Delete cache** actions still drop archived
  sessions permanently; both warn about exactly this. Don't add a third path that
  clears the cache without the same warning.
- **One conversation can hold two ledger entries**, and each used to count in full:
  Codex's archive feature moves a rollout into `archived_sessions/` (the old path stays,
  archived, beside the new live one), and a Copilot chat can exist as both `<uuid>.json`
  and `<uuid>.jsonl` across its storage-format migration, or in both VS Code's and
  Cursor's storage after Cursor imported VS Code's. `build_payload` keeps one per
  conversation **per device** (`_one_per_conversation`: fullest including cache writes,
  then live, then newest). Use `_leaf()` on paths so Windows copies deduplicate on a Mac too.
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
- **Gemini 4 Argon uses announced introductory API-equivalent rates** of $2/$10 per
  1M input/output tokens and $0.10 cached input, verified against Google's
  [launch post](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/)
  on 2026-10-02. Access is limited to trusted testers; no public API model ID is listed.
  `_canonicalize` recognizes Argon display/raw spellings without treating audio/image/Live
  variants as the text model. The later $4/$20 price has no effective date — move the
  intro tuple into `PRICE_HISTORY` only when it takes effect. Cache storage is not modeled.
- **Claude's advisor iterations** (`usage.iterations[].type == "advisor_message"`) are not
  priced separately — none exist in any log seen, so their shape can't be verified. Every
  iteration here is a plain `message`, already covered by the response's own `usage`.

## Field conventions that differ by source (do not "fix" these)

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
- **Cursor** (`state.vscdb`): sessions in `cursorDiskKV` under `composerData:*` (newer
  builds also `composerHeaders`); messages are `bubbleId:*`. Each bubble has its **own**
  `createdAt` — use it, not the session's, or a months-long session lands on day one.
  `ItemTable` holds `aiCodeTracking.dailyStats.*` (AI lines suggested vs accepted). Only
  ~2% of bubbles carry tokens; that's Cursor, not a parsing gap.
- **Copilot** logs real `promptTokens`/`completionTokens` on finished requests in current
  VS Code builds; older chats have none, so their tokens are estimated from message text
  (chars/4). It also logs a premium-request multiplier in `result.details` ("… • 1x") —
  that's its real billing unit. Not read yet (none exist on this machine): the Copilot CLI's
  `~/.copilot/session-state/*/events.jsonl`, JetBrains, and `GitHub.copilot-chat/transcripts/`
  (turns only, no tokens).
- **opencode**: current versions use one SQLite `opencode.db`; older ones use
  `storage/message/<session>/msg_*.json`. Both are read. Only the DB records a real
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
- **SQLite stores**: always open via `_open_ro_sqlite()`. Neither flag is safe alone —
  `mode=ro` reads the `-wal` (so a *running* tool's newest sessions are visible) but must
  create a `-shm`, which fails on read-only media; `immutable=1` needs no `-shm` but ignores
  the `-wal` entirely. The helper tries the first, probes it with a query (connect is lazy),
  and falls back to the second.
- **Live SQLite refreshes must track the WAL too.** Cursor and Hermes use a signature
  of the database and its `-wal`, like opencode. A committed message can change only the
  WAL until the writer checkpoints; checking just the main DB leaves the dashboard stale.

## Common tasks

**A model shows $0 / an unknown name** → in `parser.py`, add
`PRICING["<Display Name>"] = (input, output, cache_write_5m, cache_write_1h, cache_read)`
(USD per 1M; OpenAI rows put the cache-write rate in cw5 — 1.25x input where the pricing
page lists one, else 0, which bills at the input rate — 0 in cw1, and the cached rate last), and
make the normalizer map the raw id to that name. Pricing applies at request time — no
re-parse needed; a normalizer change needs a `CACHE_VERSION` bump and a normal restart
to reparse live logs while preserving archived entries. Never use `--rebuild` for a
routine migration: it discards the durable history.
A new `claude-<tier>-<n>-<m>` id normalises by itself, so only the price row is missing and
nothing warns: the model just costs $0 (Sonnet 5.5 did, for its first day). Diff the vendor's
whole table against `PRICING`, and look for all-zero rows in the payload's `prices`.

**A vendor changed a price** → the new tuple goes in `PRICING` (always today's rate — the
Optimize tab re-prices savings from it) and the old one moves into `PRICE_HISTORY` with
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

## The Optimize tab

Findings are computed client-side in `static/views.js` by the functions in
`OPT_FINDERS`, ranked by estimated saving. A finder returns `null` when it does not
apply — **never render a finding that isn't backed by the user's own numbers**, and
every one must carry the figure it came from plus something concrete to do.

**Never hardcode a model name, price or tool list into a finding.** Savings are
re-priced from `RAW.prices` (the real per-1M rates for the models that user actually
ran) and candidate swaps come from `cheaperPeer()`, which only ever suggests a model
they already use from the same maker. A finder that named specific models would go
stale and would be wrong for anyone whose mix differs.

A finding's left stripe carries **tool identity**, painted from the same
`--t-<source>` token as every chart and badge — orange is still Claude Code, green
still Codex. It is only painted when exactly one tool is in scope; a finding spanning
several has no single owner and stays neutral. It must never encode severity again:
`--accent` is byte-identical to `--t-opencode` and `--warn` collides with
`--t-claude-desktop`, so a severity stripe silently painted findings in the colour of
a tool they had nothing to do with.

Each finding sets `tools: [...]`. `scopeLabel()` renders a "<tool> only" chip when a
finding does not span every source present in the range — the MCP, Skills and
`/compact` advice is Claude Code's, and a Codex or Cursor user must not read it as
advice about their own setup. No chip is shown when it applies to everything, since
then the label is noise.

**MCP attribution differs by tool.** Claude Code names MCP tools
`mcp__<server>__<tool>`; Codex keeps the bare tool name and puts the server in a
separate `namespace` field (`"mcp__azure"`). `parse_codex` normalises to Claude's
shape — without that, an MCP tool is indistinguishable from a built-in and every
Codex server looks unused. Configured servers come from `~/.claude.json` and the
`[mcp_servers.*]` blocks of `~/.codex/config.toml` (parsed by regex, not tomllib,
which is 3.11+).

**Setup checks** (payload fields in brackets): instruction files over 8 KB, priced as the
bytes actually sent × the requests that carried them at the cache-read rate
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

The other setup checks: sessions that open heavier than the user's own leanest 10% (`open_ctx` on each
session: the first request's full input); re-reads of unchanged files and reads inside
build / dependency folders, with the tokens they returned (`reads`, Claude Code — note it
already answers an unchanged re-read with a short stub, so this rarely fires); installed
skills and subagents unused over ≥ 14 days (`installed`, description lengths only); MCP
servers used for ≤ 10% of the tools they offer, or used in one project while loaded in
several (`mcp_inventory.loaded/calls`, per project). Inventory calls retain their
source and distinct tool names before the top-tools table folds its long tail into
"(other)"; setup findings must use that complete inventory for zero-call claims.

It leans on three signals the other tabs don't use: `attributionSkill` (which Skill
drove a request — this is how "/dataviz cost you $20" is possible), a per-request
context-size histogram (`agg["ctx"]`, bucketed 0-50k / 50-150k / 150-400k / 400k+),
and the MCP servers configured in `~/.claude.json` (`_mcp_servers()`) compared against
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
  redo alone — the Optimize finder compares it with `edit_cost` (all editing turns) to get
  an excess from the user's own averages rather than a guessed fraction.
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
- **Single-session sources** (Claude/Claude Desktop, Codex, Copilot, legacy opencode —
  one file per session) persist the last-event timestamp on `agg["_active_last"]`,
  because Claude/Codex/Copilot-jsonl parse incrementally (byte-offset resume): the
  function only ever sees the NEW lines, so the previous timestamp must survive across
  calls or every incremental chunk looks like the start of a fresh burst. A fresh
  `_blank_agg` (full reparse) correctly resets it to `None`.
- **Multi-session sources** (Cursor, opencode's SQLite DB, Hermes — one store holding
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
- Cursor, opencode SQLite, Hermes and OpenClaw retain per-session `records` keyed by
  date/model. Payload records use each session's project; session days price each
  model and cache tier before summing. `model_days` allows filtering secondary models.
  Copilot additionally retains an `exact` contribution per record, with `exact_days`
  and `exact_model_days` in the payload; exact-only excludes its estimated component.
  Older archived stores cannot recover missing detail: show `detail_limited`, never
  fill a session's days with the whole database's usage.
- The positional `s.days[date]` array preserves its first ten slots and appends fields:
  `[cost,in,out,cr,cc,asst,user,tools,prem,active,req,cc5,cc1,reason,ws]`.
  `static/core.js`'s `clipSession()` also reads older ten-slot entries.

## Your devices — the one opt-in exception to "nothing leaves this machine"

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
  version-53 reader explicitly accepts version 52 (v1.7.0), whose missing optional
  precision/attribution fields retain the older detail limits; all other versions
  are refused.
- `build_payload` adds the copies to its file loop as `peer:<id>:<path>` with
  `agg["_device"]`, keys everything by device the way it already does by IDE, and prices
  them with THIS machine's `PRICING`. Setup data (MCP inventory and calls, installed-skill
  use, instruction-file cwds, OpenClaw's undecoded count) is skipped for remote copies.
  On the client, the Optimize setup finders are wrapped in `onThisDevice()`, which judges
  only this machine's usage against this machine's files and adds a "<device> only" chip.
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
(`VERSION` in `dashboard.py`: `git describe --tags`, commit, branch). **Apart from the
opt-in device sharing above, checking for an update is the only thing that touches the
network, and only on a click** — never on a timer, which would break "nothing leaves
this machine". `POST /api/update` (through
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
