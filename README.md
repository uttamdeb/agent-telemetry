# AgentTelemetry

**Live, local telemetry for your AI coding agents.** AgentTelemetry reads the interaction
logs your tools already write to your own machine and serves a clean, interactive
dashboard — estimated spend, tokens, active time, cache efficiency, an **Anthropic vs OpenAI
vs Google** provider comparison, an activity calendar, and breakdowns by **model, provider,
day, hour, weekday, tool, IDE, project and session** — plus how much **disk** all these logs
eat, and suggestions for spending less, drawn from your own numbers.

**Your data stays on your machine** — unless you turn on [Your devices](#your-devices-optional)
to combine two of your own computers over your local network. Optional account lookups
contact the provider only after you choose **Read once**; account metadata is never shared
with paired devices. No account, no API key, no
telemetry, no dependencies — just Python's standard library and a vendored copy of Chart.js.

Covers **Claude Code · Claude Desktop · Codex · GitHub Copilot · Cursor · OpenCode · Hermes Agent · OpenClaw**.

## Download the app

| Platform | Download |
| --- | --- |
| macOS 13+ · Apple Silicon | **[Download DMG](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/AgentTelemetry-2.0.0-macos-arm64.dmg)** |
| macOS 13+ · Intel | **[Download DMG](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/AgentTelemetry-2.0.0-macos-x86_64.dmg)** |
| Windows 10/11 · x64 | **[Download EXE installer](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/AgentTelemetry-2.0.0-windows-x64-setup.exe)** |

**Python is bundled — no terminal setup needed.** macOS: open the DMG, drag the app to
Applications and open it. Windows: run the installer and open AgentTelemetry from Start.
Open at Login is optional. Quit the app and wait for saving before installing an update;
upgrades and uninstall preserve telemetry history.

If you have archived history in a source checkout, leave **Open AgentTelemetry**
unchecked in Windows setup, open the app manually and **Import existing data** before
starting monitoring. On macOS, opening the app manually also lets you import first.

These builds are unsigned/unnotarized. macOS may require **System Settings → Privacy &
Security → Open Anyway**; Windows SmartScreen may require **More info → Run anyway**.
[All releases](https://github.com/uttamdeb/agent-telemetry/releases) ·
[SHA-256 checksums](https://github.com/uttamdeb/agent-telemetry/releases/download/v2.0.0/SHA256SUMS.txt)

---

## Preview

<table>
  <tr>
    <th>Menu bar companion</th>
    <th>Usage dashboard</th>
  </tr>
  <tr>
    <td><img src="docs/screenshots/macos-menu-bar.png" width="300" alt="AgentTelemetry menu bar popover with today's token and spend totals and display settings"></td>
    <td><img src="docs/screenshots/dashboard-overview.png" width="640" alt="AgentTelemetry dashboard showing token trends and usage breakdowns by tool and model"></td>
  </tr>
</table>

## Quick start

```bash
git clone https://github.com/uttamdeb/agent-telemetry.git
cd agent-telemetry
python3 dashboard.py
```

Then open **http://127.0.0.1:7878**. That's it — no `pip install`, no setup.

> First run parses your local logs (can take ~30–60s if you have large Codex logs), writes
> a cache, and is instant thereafter. The page auto-refreshes every ~15s, so a session
> you're running *right now* shows up within seconds.

Options: `python3 dashboard.py --port 9000` · `--rebuild` (ignore cache, full re-parse) ·
`--interval 20` (background refresh seconds) · `--data-dir /path/to/state` (store the
cache and optional device-sharing files outside the checkout). Without `--data-dir`, current
checkout behavior is unchanged. Or `./run.sh [flags]`.

## Recognizing subagent sessions

Sessions shows a full **Subagent** label, a branching icon, and a searchable session
identifier for transcripts that the source identifies as delegated work. Use
**All sessions / Subagents / Other sessions** to focus the list; the choice survives
reloads and shared links without changing the other dashboard totals. Session titles
also open details from the keyboard.

Recorded delegation counts and Claude sidechain usage are explained in the drawer.
Unmarked or older entries are not assumed to be primary sessions, and parent links
are not inferred. See [behavior and limitations](docs/subagent-sessions.md).

## Subscription limits and account details

The bottom of Cost shows subscription limits for Codex and Claude Code when they
have a session today on this device. Codex's locally recorded snapshots show reset
times. Choose **Refresh account & limits** to read the installed Codex CLI's account,
auth type, plan and current limits once. Claude's **Refresh account** reads its auth,
provider and optional plan; percentage meters require the optional local status-line
capture. Both actions ask before a lookup that may contact the provider.

Missing data stays unavailable. Older snapshots are stale; percentages are never
estimated from tokens or combined across devices. Session details show recorded
creator/account IDs with their attribution limits. Account metadata is private to the
source device. See [setup, research and limitations](docs/account-limits.md).

## Local servers (macOS)

The **Local servers** tab and menu bar can list local project listeners, pin them,
and save an explicit launch command for Start and Restart. Process actions ask for
confirmation. They require a ready macOS backend; Windows and Linux show the support
limitation. Commands stay on this machine and are never included in device sharing.
Running a saved command gives it your account's file and network access.
See [behavior, privacy and limitations](docs/local-servers.md).

## Install the menu bar / system tray app

Use the [app downloads](#download-the-app) above for a ready-to-run installation.
To build/install from a checkout, run **`python3 install.py` on macOS** or **`python install.py` on Windows**.
The installer detects the OS, installs its matching native client for the current user and
opens it. Use `--no-launch` to install without opening. Linux keeps the browser dashboard.

| Platform | Native client | Source installation requirements |
| --- | --- | --- |
| macOS 13+ | SwiftUI/AppKit menu item, token/spend popover and embedded dashboard | Python to run the installer; Swift 5.8+ and a macOS SDK to build the app. The app then uses its bundled Python. |
| Windows 10/11 | Branded tray icon and native summary popover matching the macOS layout; dashboard in your browser | Standard Python 3.8+ including `pythonw.exe`. No pip packages or separate GUI toolkit. |

After installation, **web Settings → Menu bar / system tray** has a toggle to launch or
quit the native app. Status reflects the app's heartbeat, not just a launch attempt. You
can also open **AgentTelemetry.app** in `~/Applications` or **AgentTelemetry** from the
Windows Start menu. The app's **Open at Login** and **Start monitoring when app opens**
options are separate and off by default. Opening from the installer or web toggle starts
monitoring for that launch. No dashboard setting is required to open an app manually.

Both native popovers put a monitoring switch above today's token/spend cards, with
expandable **Details & settings**, a dashboard button and always-visible refresh/quit
controls. Windows follows the system light/dark or high-contrast theme, scales to the
tray's monitor, and scrolls settings on smaller displays. Tab/Space/Enter operate the
native controls; Escape or clicking elsewhere closes the panel. Icon mode uses the
AgentTelemetry app icon; the numeric display remains available. Both have a text tooltip.
Windows uses Segoe UI and opens the full dashboard in your browser; macOS uses its
system font and embeds it.

Both clients reuse a dashboard already running on port 7878 and stop only a backend they
started. A web/native app using a custom dashboard port is not supported by the native
clients. Failure shows stale figures explicitly, and retries can recover. Import existing
data before first app-owned monitoring if you need archived history: importing validates
and copies the ledger without overwriting existing app data or changing the originals.
For that flow, install with `--no-launch`, open the app manually and choose Import before
Start monitoring. A default install attaches safely when the old dashboard is still running.

Browser windows, the macOS menu bar and the Windows tray share one refresh interval:
15 seconds (default), 1/5/15 minutes, or Manual. Change it in web Settings or the native
app while monitoring. They sample a small shared clock each second and show figures
from the same snapshot; a manual refresh in either app updates the others too, normally
within a second plus request time. Manual continues listening for those explicit refreshes.
The native figures are today's usage across all tools and connected devices; compare
them with an unfiltered Today dashboard. Pause pauses only that browser's figures.
Changing the interval does not restart the backend or reload the embedded dashboard.
The server's `--interval` controls log parsing separately. Older attached backends without
the sync API retain independent polling until upgraded and restarted.

Read the [macOS guide](macos/AgentTelemetryMac/README.md) and
[Windows guide](windows/README.md) for paths, controls and development checks. macOS
downloads and source builds are ad-hoc signed; a Developer ID signed/notarized installer
is not supplied. The backend remains stdlib-only and no runtime update checks
are added. The macOS packaging step downloads a hash-pinned Python runtime explicitly.

Local app integrations can use `GET /api/health` for a small readiness response that contains
no usage data, and `GET /api/summary` for today's token total and API-equivalent estimated
spend. Both are read-only; they follow the dashboard's existing host and origin rules.
`GET /api/sync` supplies the shared display clock; `POST /api/sync` sets its validated
interval through the same JSON/same-origin CSRF guard. The data and summary HTTP endpoints
retain one serialized snapshot per display tick, in memory only; the ledger is unaffected.
`GET /api/native` reports local app status. `POST /api/native` requires the same CSRF guard
as all writes. `POST /api/shutdown` additionally requires the owning Windows client's
per-process control token; a normal standalone dashboard cannot be stopped by that route.

> `--rebuild` deletes the cache, and with it every session whose log has since been deleted
> from disk (see [Storage](#storage--what-these-logs-cost-you-in-disk)). Copy
> `.usage_cache.json` somewhere safe first if you've cleaned up old logs.

**Requirements:** Python 3.8+ on **macOS, Linux or Windows**. On Windows run
`python dashboard.py` (or `run.cmd`); on macOS/Linux `python3 dashboard.py` (or `./run.sh`).

It works on anyone's machine because **nothing is hardcoded** — every location is derived at
runtime from your own `$HOME` / `%APPDATA%` / `%LOCALAPPDATA%` / `$XDG_*`, the numbers are
read live from your own logs on every refresh, and the disk figures come from your own drive.
Two people running this see two completely different dashboards.

---

## Everyone sees *their own* usage

This is the important part if you're sharing it: the dashboard has **no bundled data**.
On each machine it scans that user's own logs (`~/.claude`, `~/.codex`,
`~/Library/Application Support/…`, `~/.local/share/opencode`, `~/.hermes`, …) and builds a **fresh**
`.usage_cache.json` locally. That cache is **gitignored and never committed**, so a clone
starts empty and shows only the cloning user's numbers. (If you ever *copy the folder*
instead of cloning, delete `.usage_cache.json` first — that file is your personal data.)

---

## Data sources

| Tool | Where it reads | Tokens |
|---|---|---|
| **Claude Code** | `~/.claude/projects/**/*.jsonl` | exact (in/out/cache read+write, 5m/1h tiers) |
| **Claude Desktop** (agent mode) | `Claude/local-agent-mode-sessions/**` under App Support / `%APPDATA%` / `~/.config` | exact |
| **Codex** | `~/.codex/sessions/**`, `~/.codex/archived_sessions/**` | exact (in/cached/out/reasoning); subagents are identified and labelled |
| **GitHub Copilot** | VS Code-family `chatSessions/*.{json,jsonl}`; SDK/CLI `~/.copilot/session-state/*/events.jsonl`, enriched from VS Code `agentSessionData/*/session.db` | recorded request/turn tokens where available; SDK shutdown totals where safely dated. SDK gaps show a warning tooltip and unavailable/partial usage; older non-SDK chats retain text estimates. Premium multipliers are separate from estimated dollars. |
| **Cursor** (native AI) | `Cursor/User/globalStorage/state.vscdb` under App Support / `%APPDATA%` / `~/.config` | partial — model, mode, timestamps, tool calls and AI-line stats are exact; tokens are on only ~2% of messages |
| **OpenCode** | `~/.local/share/opencode/opencode.db`, `%LOCALAPPDATA%\opencode\opencode.db`, `~/.opencode/opencode.db` (or `$OPENCODE_DATA_DIR`) | V1/V2 SQLite; exact (in/out/reasoning/cache), prompts and tools; cost is read from OpenCode's own per-message value |
| **Hermes Agent** | `~/.hermes/state.db` (or `$HERMES_HOME`, `%LOCALAPPDATA%\hermes`) | exact (in/out/cache/reasoning, per model) |
| **OpenClaw** | `~/.openclaw/agents/<agent>/agent/openclaw-agent.sqlite` (or `$OPENCLAW_STATE_DIR`; also the older `~/.clawdbot`, `~/.moltbot`) and the older per-session `sessions/*.jsonl` | exact (in/out/cache read/cache write/reasoning, per response); its own logged cost is used only for a model with no verified price. Large events are stored zstd-compressed: read with the `zstd` command or Python 3.14+ |

A tool you don't use simply contributes nothing. **Attribution is by tool, not by model** —
a Claude or GPT model used *inside* Copilot/Cursor/OpenCode/Hermes/OpenClaw counts under that tool, and the
Models table lists each `model × tool` row separately.

Copilot's newer SDK-based agent sessions use a different store from older Copilot Chat.
AgentTelemetry joins their event history to VS Code's saved per-turn usage using native
session and event IDs. Custom endpoint models, including Cloudflare GLM 5.3, still count
as **Copilot**. A warning beside a session explains missing usage, partial coverage,
errors or interruptions. Missing SDK usage is never guessed from chat length or presented
as a known zero. See [Copilot SDK data coverage](docs/copilot-sdk.md) for sources and limits.

---

## What you get

Eight views in a left-hand sidebar, light + dark theme, everything date-filterable, and the
**name of this machine** at the top — so a screenshot always says which computer it came from.

The controls read as a sentence above every view — "**Tokens** from **all tools** over
**the last 30 days** ‹ ›" — click a bold phrase to change it; the arrows step back and
forward a period at a time. Inside them: the **measure** (**Tokens · Cost · Messages · Time**
— tokens by default; every chart and ranking switches together), the **period** (today, the
last 7–365 days, this week / month / quarter / year, last month, all time, or a custom
range), and a single **Filters** panel (tool, provider, model, project, IDE / surface, and *exact tokens only*).
Active filters show as removable chips. Every figure is compared with the equal-length
period just before it, and every chart has a **table view** (the grid icon) so nothing is
readable only by hovering.

**Overview** — one hero number (the chosen measure, tokens by default) with its change vs
the previous period. In Tokens mode, hover over or keyboard-focus the total for a tooltip
showing tokens excluding cache reads; Escape dismisses it. An estimated-spend sparkline
sits below; Messages and Time keep spend in a tile.
The per-day chart is stacked by tool, followed by supporting tiles, **tool, model and project mix**, highlights,
an **hour × weekday heatmap**, and token mix per tool. The 12-month activity calendar and
**Monthly rhythm** token chart let you click a day or month to select that period.
Both keep their historical window while honoring tool, provider, model, project, IDE,
device and exact-token filters; Monthly rhythm also has a table view with full counts.
With another device connected, **By device** compares each computer's share of the
selected measure (tokens, messages, estimated cost or active time), with a table view.
It follows the same date and usage filters as the rest of Overview.

**Cost** — total with per-active-day, 30-day run rate, per session and per prompt ·
cumulative spend by tool against the previous period · **cache hit rate and what caching
saved you** · spend by model, by project and by **token type** (what cache reads vs writes vs
output actually cost you) · effective rate by model, as *all tokens* or *per output* (the one
that's comparable across providers) · daily spend.

**Models** — provider cards (who made the model, independent of the tool that ran it) ·
sortable `model × tool` table with a ⚠ on any model missing a price · model timeline ·
provider share over time · **provider × tool matrix** · **model efficiency** (how often each
model's edits land first time, retries per edit, tokens and cost per prompt).

**Tools & agents** — active time, tool calls per prompt, context amplification, subagent
share, MCP and web calls, Copilot premium requests, Cursor's AI lines kept · top tool calls
· calls by category · **what your prompts were for** (building, fixing, refactoring,
testing, exploring, git, deploys… — each prompt classified by what the agent did: the files
it edited and the commands it ran; Claude Code & Codex) · **edits that landed first time** (one-shot rate per tool, retries,
and what the retried prompts cost) · **where you work** (IDE × tool) · MCP servers, with the
tools used out of those each server offered · **Skills** · the full tool list.

**Projects** — ranked by the chosen measure, concentration stats, and a searchable table
where clicking a row opens that project's sessions.

**Sessions** — real session titles, tool, project, model, tokens, cost, prompts, replies,
tool calls, active time, cache % — search, sort, and click any row for a detail panel.
Claude and Codex sessions also show completed compactions in the selected date range.
Claude records manual/auto counts, context before → after, and duration in the detail
panel. Codex checkpoints provide completion times; missing manual/auto triggers and
context sizes show **Not recorded**. Newer Codex completion records can supply
duration when their request/turn/thread identities and timing match exactly one checkpoint.
Missing or ambiguous timing shows **Unavailable**. Codex compaction request usage,
when logged, is already included in token/cost totals and is never counted again.
Compaction-only days remain visible. Events survive log deletion once recorded;
older ledger entries without event metadata say “Not recorded.” Context sizes are
separate from billed tokens: missing compaction request usage/cost is not inferred. Events
belong to the session and are not attributed to a model filter.

Use the pencil beside a session name (or **Rename** in its detail panel) to rename it
in the **original tool**. A warning shows the old/new names and exact target before
anything is saved. Close the session in its original tool before confirming, and reload
that tool if needed. Token, cost, prompt, tool-call and compaction records stay intact.
Previous title metadata is backed up privately in
`AgentTelemetryNative/session-title-backups/`, separately from the usage ledger.
This supports local Claude Code transcripts, Codex through its installed CLI’s
[official name API](https://learn.chatgpt.com/docs/app-server), and OpenCode’s SQLite
session stores. Other formats, remote sessions and deleted logs cannot be edited.
Tool/provider icons are bundled locally, including the OpenAI mark for Codex; no remote
logo requests occur. Their sources and licenses are in `static/tool-icons/README.md`.

**Optimize** — suggestions derived from your own logs, ranked by what they'd save: sessions
re-reading a very large context, thinking share, tool-heavy sessions that never delegated,
cache written but never read, a costly model doing light work, what each **Skill** costs,
edits that needed a retry, oversized **CLAUDE.md / AGENTS.md**, sessions that open heavier
than your leanest ones, wasted file reads, installed skills and agents you never use, and
MCP servers you never call, barely use, or load everywhere but use in one project. Nothing is shown unless
your data supports it, and because the estimates overlap they are never summed.

**Storage** — see below.

**Keyboard** — `1`/`7`/`3`/`9`/`a` ranges, `m` month-to-date, `,` / `.` previous / next
period, `Shift`+`T`/`C`/`M`/`A` tokens / cost / messages / active time, `[` sidebar, `/`
search, `t` theme, `r` refresh.

**Links** — every filter is in the address bar, so a reload, a bookmark or a pasted link opens
the same view. `?range=` takes `today`, `7d`, `30d`, `mtd`… or `custom` with
`&from=2026-09-01&to=2026-09-15`; then `?metric=`, `?tools=`, `?providers=`, `?models=`,
`?projects=`, `?ides=`, `?devices=` (repeat a param for each value), `?exact=1`, `?q=` for the
search box, and `#cost`, `#sessions`… for the tab. For example
`/?range=7d&metric=cost&tools=claude&projects=sims#cost`. Only what differs from the default
is written, and changing a filter never adds a browser-history entry.

**What changed?** — Overview and Cost compare the selected range with the preceding
equal-length range. Switch between projects, models, tools and (for tokens/cost)
token types to see signed contributions, including decreases and categories that
disappeared. Each breakdown reconciles independently to the total change. Click a
category for current-period sessions or its previous value for earlier sessions.
All usage filters apply; All time has no comparison. Missing prior observations and
an unfinished current period are labeled. Contributions identify where usage changed,
not a causal explanation. Cost remains an API-equivalent estimate.

**Session timeline** — the session drawer combines daily retained usage with recorded
compaction markers and local Claude/Codex source events: model requests, tool calls,
user turns and recorded subagent starts. No prompts or tool arguments are returned.
The read is bounded to the first 64 MiB and 400 events in the selected date range;
truncation is explicit. Date filters use local dates; model filters apply to events
with model attribution. Archived/remote sessions and unsupported formats retain
daily detail where available; missing timestamps or history are not reconstructed.

**Backups and troubleshooting** — Settings shows detected-source scan results,
actionable parsing errors, ledger failures and models missing prices. An inactive
tool is not reported as stale merely because it has no recent activity. Backups are
private copies of the ledger in `.usage-backups/` beside it (including with
`--data-dir`), excluded from device sharing and installation payloads. A saved-ledger
snapshot is kept on the first save each UTC day; updates and cache resets require a
safety copy first. Retention keeps 7 daily snapshots and up to 5 each of manual,
pre-restore and corrupt-file recovery copies. A failed daily backup does not stop
normal ledger saving and is reported in Settings.

Restore shows stored-session counts and requires a one-use, five-minute confirmation
ticket. Changed backups, disk contents or newly parsed usage invalidate the preview.
The current ledger is preserved before replacement, including unreadable bytes as
a non-restorable recovery copy. Backups cover aggregates, not original transcripts,
device pairing settings or native preferences; local backups do not protect against
loss of the drive. Use `python tests/preview_features.py --port 8896` for an isolated
preview with invented data.

**Version and updates** — the sidebar footer shows the running version. *Check for updates*
asks GitHub (the only time the dashboard goes online, and only when you click); *Update*
fast-forwards your checkout and restarts the server. It refuses if you have local edits or
commits that aren't on GitHub. A copy that isn't a git clone has no update button.

---

## Storage — what these logs cost you in disk

The tools you use write a *lot* to disk, and nothing else tells you how much. The **Storage**
tab shows total footprint and per-tool bytes, a free-space gauge that warns when the drive is
nearly full, storage accumulation over time, the largest individual log files, **bytes per 1M
tokens** (which tool stores its history most expensively), AI data on disk AgentTelemetry does
*not* analyse, and copy-paste cleanup commands **generated for your own paths and your own
shell** (`find` on macOS/Linux, PowerShell on Windows). AgentTelemetry never deletes anything
itself.

Deleting old logs does **not** shrink your analytics — AgentTelemetry keeps every session it has
already parsed, so the cleanup is safe.

---

## Cost notes (read this)

Costs are **estimates** computed as `tokens × price` — the tools store token counts, **not
dollars**, so cost is always derived. Rates live in `parser.py → PRICING` as
`(input, output, cache_write_5m, cache_write_1h, cache_read)` per 1M tokens; edit freely
(recomputed on each request, no re-parse needed).

- **Anthropic** rates are current list prices (Opus 5.5 $4/$20, Opus 5 & 4.x $5/$25, Sonnet 5
  & 5.5 $2/$10, Sonnet 4.x $3/$15, Haiku 4.5 $1/$5; cache write 1.25×/2× input for 5-min/1-hour, cache
  read 0.1× — 0.05× on Opus 5.5). **OpenAI** GPT-5.4/5.5/5.6/6 are verified from OpenAI docs;
  older/other models are estimates.
- **Haiku 5.5** uses [Anthropic's published rates](https://platform.claude.com/docs/en/about-claude/pricing):
  $0.10/$0.50 per million input/output tokens up to 100K prompt tokens, $0.50/$2.50
  above 100K. The threshold includes cached input and applies to each request.
  Available Claude logs backfill this pricing detail on a normal restart; token and
  message counts stay unchanged. Older archives/peers and other tools without that
  detail use the short-prompt rate. Optimize model substitutions conservatively use
  the higher tier when the target request size is unknown.
- **Price changes are date-aware.** When a vendor cuts a price (OpenAI's GPT-5.6 cuts of
  2026-07-30 and 2026-08-21), usage from before the change keeps the rate it had then —
  see `parser.py → PRICE_HISTORY`.
- **These are API-equivalent values.** If you're on a subscription (Claude Max/Pro, Codex,
  Copilot), you don't pay per token — the $ is "what this would cost at API rates."
- **Cursor** rarely logs token counts, and **older Copilot chats** have none, so their tokens
  (and thus $) are rough. Current Copilot builds log real per-request tokens, which are used.
  Copilot's honest metric is **request count** and its **premium-request** total (both shown);
  Cursor's is **messages, tool calls and AI lines kept** (also shown).
- **Billing details that change the price are applied**: prompt-cache writes at their own
  rate (Claude and GPT-5.6/GPT-6), Claude fast mode, US-only inference (1.1x) and web
  searches ($10/1K). A forked or subagent Codex thread's replay of its parent is skipped,
  so it isn't billed twice.
- A model with no price row reads as **$0** — add it to `PRICING` (see below).
- **Gemini** (2.5, 3, 3.1 and 3.5 to 3.8: Pro, Flash, Flash-Lite, Computer Use) is priced
  when it runs through supported tools, at the Standard rate for prompts up to 200K
  tokens. Live, TTS, image and embedding models are not priced, and nor are the higher Pro rate
  for longer prompts or audio input. The Gemini CLI itself writes no token counts to read.
- **Gemini 4 Argon** is recognized across the supported tools and uses Google's
  [announced introductory rates](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-4-argon/):
  $2 input, $10 output and $0.10 cached input per 1M tokens. Access is still limited to
  trusted testers, and Google has not listed a public API model ID. These are API-equivalent
  estimates for Argon usage found in logs. The later $4/$20 rate has no effective date yet
  and is not applied ahead of time; cache storage charges are not modeled.

## Note on log retention

Some tools delete old logs. **Claude Code** prunes transcripts after `cleanupPeriodDays`
(default **30**); **Codex** keeps everything. The dashboard also keeps parsed sessions in
its cache even after a tool deletes the on-disk log, so totals don't silently shrink once seen.

You can change Claude Code's retention window from the dashboard itself — the **⚙** button
at the foot of the sidebar edits `cleanupPeriodDays` in your own `~/.claude/settings.json` (leave it blank
to fall back to the tool's default). The write is atomic and keeps a `.bak`; every other
setting in the file is preserved untouched. It's the only file outside its own cache that the
dashboard ever writes.

---

## Extending it

- **Add a model's price / fix an unknown model:** edit `PRICING` (and, if needed, the
  model-name normalizer) in `parser.py`. Verify rates against the vendor's docs.
- **Add a new tool:** add its paths + a `parse_*` function in `parser.py`, wire `discover()`
  and `update_file()`, then add it to `SRC`/`ORDER` in `static/core.js` and give it a
  `--t-<source>` colour in `static/app.css`. The tool colours are a colour-blind-validated
  palette whose *order* is the safety mechanism — see AGENTS.md before changing them.

See **[AGENTS.md](AGENTS.md)** for a concise, agent-oriented guide (any coding agent can run
and extend this from that file).

## Files

`dashboard.py` (server + cache + cost + `/api/storage`) · `parser.py` (log parsers + pricing) ·
`index.html` (shell) · `static/app.css` · `static/core.js` · `static/charts.js` ·
`static/views.js` · `chart.umd.min.js` (vendored Chart.js) · `AGENTS.md` · `run.sh`.

## Install it as an app (optional)

The dashboard can run as a standalone window in your Dock or taskbar. It's **off by
default** — open the **⚙** menu and turn on *Install as an app*, then use your browser's
Install / Add to Dock. That registers a service worker so the shell still opens when
`dashboard.py` isn't running; your usage data is never cached, `/api/` always hits the live
server. Turning it off again unregisters the worker and clears its cache.

## Your devices (optional)

Use your coding agents on more than one computer? You can see them all in one dashboard.
It's **off by default** and works over your **local network** only. Open **⚙ → Your devices**:

1. On the computer you want to read from, click **Share this device…**. It shows an
   address (like `your-mac.local:7879`) and a pairing code.
2. On the computer you're looking from, enter that address and code, then click **Connect…**.
3. To see each one from the other, do both steps on both.

Once connected, a **Devices** filter appears and the header reads "…from all tools **on all
devices**…". The sidebar device card also becomes a dropdown: choose **All devices** or
one computer. Both controls share the same filter, which is preserved in the URL.
The connected device's usage is fetched every minute, and a copy is kept in
`.peers/` so it still shows while that computer is asleep. Disconnecting deletes the copy.

What sharing sends: session titles (often the start of a prompt), project and branch names,
log file paths, models, token counts, costs and times. Full prompts, replies and file
contents are never sent. Sharing opens one read-only port (7879) that answers only to the
pairing code. The dashboard itself stays on `127.0.0.1`. The connection is **not
encrypted**, so share only on a network you trust. The setup checks on the Optimize tab
(instruction files, installed skills, MCP servers) and the Storage tab still describe only
the computer you're looking at. `.peers.json` and `.peers/` are gitignored.

**If it can't connect**, the error says which of these it is:

- **The other device's firewall.** On Windows, allow Python on *private* networks when asked,
  and set the Wi-Fi to Private (Settings → Network & internet). On macOS, allow incoming
  connections when asked.
- **A VPN** (like 1.1.1.1 with WARP) on either device can get in the way.
- **A `.local` name that doesn't resolve.** Use the IP address shown next to it instead.

Once connected, a device that moves to a new IP address is still found: every answer
lists the other addresses it can be reached at, and those are tried when the saved one
stops working.

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** — it covers the setup, the hard rules
(stdlib only, nothing hardcoded, never commit your cache), how to add a tool source or
a model price, and how to test a change. **[AGENTS.md](AGENTS.md)** is the architecture
guide. Please read the note on setting your git email before your first commit.

Found a security problem? See **[SECURITY.md](SECURITY.md)** — don't open a public issue.

## License

MIT — see [LICENSE](LICENSE).
