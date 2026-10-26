"""
parser.py — Incremental usage-log parser for local AI coding tools.

Sources: Claude Code, Claude Desktop (agent mode), Codex CLI, GitHub Copilot
(VS Code / Insiders / Cursor), Cursor native AI, opencode, and Hermes Agent. Each tool stores
interaction logs locally; this module discovers those files, parses them
incrementally (append-only .jsonl read from a byte offset; rewritten stores
and SQLite databases re-read on change), and produces per-file aggregates the
server merges into one dataset. Everything is keyed off the user's own home
dir — nothing is hardcoded to a machine or account, so it works on any
Mac/Linux install of the same tools.

No third-party dependencies — stdlib only.
"""
import os, sys, json, glob, re, time, shutil, subprocess, copy, hashlib
if __package__:
    from . import accounts as A
    from . import antigravity as AG
else:
    import accounts as A
    import antigravity as AG
from datetime import datetime, timezone

HOME = os.path.expanduser("~")


def _leaf(path):
    """Last path component of a cwd recorded on ANY OS — a macOS/Linux log read on
    Windows (or vice versa) still has the other platform's separator in it."""
    if not path:
        return ""
    return os.path.basename(str(path).replace("\\", "/").rstrip("/"))

# ---------------------------------------------------------------------------
# Source locations
# ---------------------------------------------------------------------------
CLAUDE_GLOBS = [os.path.join(HOME, ".claude", "projects", "**", "*.jsonl")]
# Codex's own index of thread names — the title it shows in its UI. It is NOT in
# the rollout file, and the short name the editor displays lives only in a VS Code
# cache key that appears and vanishes within seconds, so this is the one durable
# source. Append-only: a renamed thread gets a NEW line, so the LAST entry wins.
CODEX_SESSION_INDEX = os.path.join(HOME, ".codex", "session_index.jsonl")

CODEX_GLOBS = [
    os.path.join(HOME, ".codex", "sessions", "**", "*.jsonl"),
    os.path.join(HOME, ".codex", "archived_sessions", "**", "*.jsonl"),
]
# Claude Desktop "local agent mode" runs Claude Code in a sandbox; it writes
# standard Claude-format transcripts under a nested .claude/projects/ tree
# (the sibling audit.jsonl mirrors the same sessions, so we deliberately skip it).
def _app_support_roots(name):
    """Per-user application-data dirs for `name` on macOS, Windows and Linux."""
    out, seen = [], set()
    for base in (os.path.join(HOME, "Library", "Application Support"),   # macOS
                 os.environ.get("APPDATA"),                              # Windows
                 os.environ.get("LOCALAPPDATA"),                         # Windows
                 os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config")):
        if not base:
            continue
        r = os.path.join(base, name)
        if r not in seen:
            seen.add(r); out.append(r)
    return out


CLAUDE_DESKTOP_GLOBS = [
    os.path.join(r, "local-agent-mode-sessions", "**", ".claude", "projects", "**", "*.jsonl")
    for r in _app_support_roots("Claude")
]
# Gemini CLI persists ONLY user prompts locally (no model / no tokens / no responses),
# so this source contributes activity (prompts/sessions/days/projects) but no token data.
GEMINI_GLOBS = [
    os.path.join(HOME, ".gemini", "tmp", "*", "chats", "*.jsonl"),
]
# Antigravity stores conversation databases under exactly three local roots.
# The reader owns root validation and rejects symlinked/out-of-root databases.
ANTIGRAVITY_ROOTS = AG.conversation_roots(HOME)
# Cursor's native AI: one SQLite key-value store. Bubbles hold messages; token counts
# are present on only a few and no reliable per-message model is stored.
# (CURSOR_DBS is derived portably from the editor roots below.)
def _editor_roots(names):
    """VS Code-family app-support dirs across macOS, Linux and Windows."""
    bases = [
        os.path.join(HOME, "Library", "Application Support"),          # macOS
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),  # Linux
        os.environ.get("APPDATA", ""),                                 # Windows
    ]
    out = []
    for base in bases:
        if not base:
            continue
        for n in names:
            out.append(os.path.join(base, n))
    return out


# VS Code forks that ship Copilot Chat and therefore write the same chatSessions
# store. Adding a fork here is all it takes to cover it — the on-disk format is
# identical because they all inherit VS Code's chat storage. Verified present on
# this machine: Code, Code - Insiders, Cursor, Puku. The rest are included because
# they are the same shape and cost nothing when absent (glob on a missing dir is
# simply empty), NOT because they were tested here.
COPILOT_ROOTS = _editor_roots([
    "Code", "Code - Insiders", "VSCodium", "Cursor", "Puku",
    "Windsurf", "Trae", "Positron",
])
COPILOT_SDK_GLOBS = [os.path.join(HOME, ".copilot", "session-state", "*", "events.jsonl")]
CURSOR_DBS = [os.path.join(r, "User", "globalStorage", "state.vscdb")
              for r in _editor_roots(["Cursor"])]

# opencode (SST) — per-message JSON at storage/message/{sessionID}/msg_*.json.
# cost is stored as 0, so we compute it from tokens like every other source.
def _opencode_roots():
    roots, seen = [], set()
    win = [os.path.join(b, "opencode")
           for b in (os.environ.get("LOCALAPPDATA"), os.environ.get("APPDATA")) if b]
    for r in [os.environ.get("OPENCODE_DATA_DIR"),
              os.path.join(os.environ.get("XDG_DATA_HOME") or
                           os.path.join(HOME, ".local", "share"), "opencode"),
              *win,
              os.path.join(HOME, ".opencode")]:
        if r and r not in seen:
            seen.add(r); roots.append(r)
    return roots


OPENCODE_ROOTS = _opencode_roots()
# Current opencode stores interaction history in a single SQLite database.
OPENCODE_DBS = [os.path.join(r, "opencode.db") for r in OPENCODE_ROOTS]

# Hermes Agent (NousResearch) — one SQLite state.db under $HERMES_HOME (default
# ~/.hermes, or %LOCALAPPDATA%\hermes on native Windows) holding every session,
# same "one store, many sessions" shape as Cursor's state.vscdb.
def _hermes_home():
    override = os.environ.get("HERMES_HOME", "").strip()
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        if base:
            return os.path.join(base, "hermes")
    return os.path.join(HOME, ".hermes")


HERMES_DB = os.path.join(_hermes_home(), "state.db")

# OpenClaw (formerly Clawdbot / Moltbot) — $OPENCLAW_STATE_DIR, default ~/.openclaw,
# with one directory per agent under agents/. Current builds keep every session of an
# agent in agents/<id>/agent/openclaw-agent.sqlite; older ones wrote one JSONL
# transcript per session under agents/<id>/sessions/ (the SQLite migration leaves
# those behind as <id>.jsonl.deleted.<ts> archives).
def _openclaw_roots():
    override = os.environ.get("OPENCLAW_STATE_DIR", "").strip()
    roots = [override] if override else []
    roots += [os.path.join(HOME, d) for d in (".openclaw", ".clawdbot", ".moltbot")]
    seen, out = set(), []
    for r in roots:
        r = os.path.normpath(os.path.expanduser(r))
        if r not in seen:
            seen.add(r); out.append(r)
    return out


EDITOR_LABEL = {
    "Code": "VS Code",
    "Code - Insiders": "VS Code Insiders",
    "VSCodium": "VSCodium",
    "Cursor": "Cursor",
    "Puku": "Puku",
    "Windsurf": "Windsurf",
    "Trae": "Trae",
    "Positron": "Positron",
}

# ---------------------------------------------------------------------------
# Pricing — USD per 1,000,000 tokens:
#   (input, output, cache_write_5m, cache_write_1h, cache_read)
# Anthropic prices verified against the current model table (Opus 4.x = $5/$25,
# NOT the old $15/$75 — Opus pricing dropped with 4.5). Cache write = 1.25x input
# (5-min TTL) / 2x input (1-hour TTL); cache read = 0.1x input, EXCEPT Opus 5.5
# (0.05x) and Fable/Mythos 5.1 (0.025x) — so the cache_read slot is written out per
# row, never derived from input. Most OpenAI rows have no cache-write tier
# (cw5/cw1 = 0, billed at the input rate); cached input goes in the cache_read slot.
# Codex/Copilot/Cursor are subscription-billed, so their $ is an API-equivalent
# estimate, not an actual charge. Costs are computed at request time — edit
# freely, no re-parse needed.
# ---------------------------------------------------------------------------
PRICING = {
    # Anthropic Claude 5 family — verified against platform.claude.com pricing (2026-09-29).
    # 5.1 keeps 5's $10/$50 but cuts cache reads to 0.025x input ($0.25, not $1).
    "Claude Fable 5.1": (10, 50, 12.5, 20, 0.25),
    "Claude Mythos 5.1": (10, 50, 12.5, 20, 0.25),
    "Claude Fable 5": (10, 50, 12.5, 20, 1.0),
    "Claude Mythos 5": (10, 50, 12.5, 20, 1.0),
    # Sonnet 5.5 lists the same $2/$10 as Sonnet 5, with the usual 0.1x cache read.
    "Claude Sonnet 5.5": (2, 10, 2.5, 4, 0.20),
    # Sonnet 5's $2/$10 launch "intro" rate became its standard price — Anthropic
    # cancelled the $3/$15 increase scheduled for 2026-09-01, so there's no date split.
    "Claude Sonnet 5": (2, 10, 2.5, 4, 0.20),
    "Claude Mythos Preview": (10, 50, 12.5, 20, 1.0),
    # Opus 5.5 — $4/$20, the first Opus below $5/$25; cache read is 0.05x ($0.20).
    "Claude Opus 5.5": (4, 20, 5, 8, 0.20),
    # Anthropic Opus 4.5+ — $5/$25 (current pricing)
    "Claude Opus 5": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.8": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.7": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.6": (5, 25, 6.25, 10, 0.50),
    "Claude Opus 4.5": (5, 25, 6.25, 10, 0.50),
    # Anthropic Opus 4.1 / 4.0 — legacy $15/$75 pricing
    "Claude Opus 4.1": (15, 75, 18.75, 30, 1.50),
    "Claude Opus 4": (15, 75, 18.75, 30, 1.50),
    # Anthropic Sonnet — $3/$15
    "Claude Sonnet 4.6": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 4.5": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 4": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 3.7": (3, 15, 3.75, 6, 0.30),
    "Claude Sonnet 3.5": (3, 15, 3.75, 6, 0.30),
    # Haiku 5.5, effective October 7, verified against the Claude pricing page
    # on 2026-10-12. This tuple is for requests with <=100K total prompt tokens.
    "Claude Haiku 5.5": (0.10, 0.50, 0.125, 0.20, 0.01),
    # Earlier Anthropic Haiku
    "Claude Haiku 4.5": (1, 5, 1.25, 2, 0.10),
    "Claude Haiku 3.5": (0.80, 4, 1.0, 1.6, 0.08),
    # OpenAI GPT-5.6 series (Sol/Terra/Luna) — CURRENT rates, verified against
    # developers.openai.com/api/docs/pricing (2026-09-23). All three are cuts from
    # the launch prices, which PRICE_HISTORY keeps for usage dated before them.
    # cache read = 0.1x input; cache write = 1.25x input (the pricing page lists it
    # for GPT-5.6 and GPT-6 only — older rows keep 0, which _cost() bills at the
    # plain input rate). Long-context (>272K input) rates are per request and not
    # modeled; see AGENTS.md "What the logs cannot show".
    # Sol's $4/$20 is a promo "at least through November 21, 2026" — if it reverts,
    # move this tuple into PRICE_HISTORY and restore $5/$30 here.
    "GPT-5.6 Sol": (4, 20, 5.00, 0, 0.40),
    "GPT-5.6 Terra": (2, 12, 2.50, 0, 0.20),
    "GPT-5.6 Luna": (0.20, 1.20, 0.25, 0, 0.02),
    # GPT-6 — verified directly against developers.openai.com/api/docs/models/
    # gpt-6-{astra,sol,luna} and the pricing page (2026-09-23); Sol and Luna launched
    # 2026-09-22 at half GPT-5.6's promo price. Cache write 1.25x input, as above —
    # parse_codex carves cache_write_input_tokens out of input into cc5.
    "GPT-6 Astra": (10, 50, 12.50, 0, 1),
    "GPT-6 Sol": (2, 10, 2.50, 0, 0.20),
    "GPT-6 Luna": (0.10, 0.50, 0.125, 0, 0.01),
    # GPT-6.1 Sol — same $2/$10 as GPT-6 Sol, but cached input is 5% of input, not 10%
    # (verified against the model page and the pricing page, 2026-09-30). Sol is the
    # only 6.1 variant listed. The page now states the 272K long-context threshold; it
    # is still not modeled (the one 6.1 request seen here has 136K of input).
    "GPT-6.1 Sol": (2, 10, 2.50, 0, 0.10),
    # OpenAI GPT-5.4 / 5.5 — verified from OpenAI API pricing docs (2026-07).
    # NOTE: GPT-5.5 has a >272K-input surcharge (2x in / 1.5x out for the session)
    # not modeled here, so heavy-context Codex sessions may cost somewhat more.
    "GPT-5.5": (5, 30, 0, 0, 0.50),
    "GPT-5.5 Pro": (30, 180, 0, 0, 3.0),
    "GPT-5.4": (2.5, 15, 0, 0, 0.25),
    "GPT-5.4 Mini": (0.75, 4.5, 0, 0, 0.075),
    "GPT-5.4 Nano": (0.20, 1.25, 0, 0, 0.02),
    # GPT-5.3 Codex, 5.2, 5.1, 5, 5 Mini and 5 Nano verified against the pricing page
    # (2026-09-30). 5.2 and 5.3 Codex are $1.75/$14 — they were carried at 5.1's $1.25/$10.
    # The page has no plain "GPT-5.3" row, so that one is still an estimate.
    "GPT-5.3": (2.5, 15, 0, 0, 0.25),
    "GPT-5.3 Codex": (1.75, 14, 0, 0, 0.175),
    "GPT-5.2": (1.75, 14, 0, 0, 0.175),
    "GPT-5.1": (1.25, 10, 0, 0, 0.125),
    "GPT-5": (1.25, 10, 0, 0, 0.125),
    "GPT-5 Mini": (0.25, 2, 0, 0, 0.025),
    "GPT-5 Nano": (0.05, 0.40, 0, 0, 0.005),
    # Microsoft, via Copilot — docs.github.com/en/copilot/reference/copilot-billing/
    # models-and-pricing (2026-09-27): the per-token rate Copilot bills past a plan's
    # included allowance. No cache-write column is listed.
    "MAI-Code-1.1-Flash": (0.20, 1.20, 0, 0, 0.02),
    # OpenAI legacy (estimates)
    "GPT-4.1": (2, 8, 0, 0, 0.5),
    "GPT-4.1 Mini": (0.40, 1.6, 0, 0, 0.10),
    "GPT-4o": (2.5, 10, 0, 0, 1.25),
    "o4-mini": (1.1, 4.4, 0, 0, 0.275),
    "o3": (2, 8, 0, 0, 0.5),
    # Google Gemini — Standard paid tier for text/image/video input, read from
    # ai.google.dev/gemini-api/docs/pricing (2026-09-30). Gemini bills no cache write,
    # only storage per hour, so cw stays 0. Not modeled, as with OpenAI's long context:
    # prompts over 200K on Pro (2x input, 1.2-1.5x output), audio input (about 2x), and
    # the Live / TTS / image / embedding / Veo models, which bill audio, image and video
    # tokens at rates one tuple cannot express and no coding agent logs.
    # Gemini 4 Argon — announced introductory API-equivalent rates, verified
    # against blog.google/innovation-and-ai/models-and-research/gemini-models/
    # gemini-4-argon/ (2026-10-02): $2 input / $10 output, cached input 95% off.
    # Access is limited to trusted testers; no public API id is listed yet. The
    # later $4/$20 rate has no effective date: only move this into PRICE_HISTORY
    # when that change takes effect. Cache storage is not modeled.
    "Gemini 4 Argon": (2.00, 10.00, 0, 0, 0.10),
    # 3.6 to 3.8 Flash are a promo "through December 31, 2026" and double on 2027-01-01:
    # then move these tuples into PRICE_HISTORY dated 2026-12-31 and put the doubled
    # ones here ($1.50 / $7.50, cache read $0.15).
    "Gemini 3.8 Flash": (0.75, 3.75, 0, 0, 0.075),
    "Gemini 3.7 Flash": (0.75, 3.75, 0, 0, 0.075),
    "Gemini 3.6 Flash": (0.75, 3.75, 0, 0, 0.075),
    "Gemini 3.5 Flash": (1.50, 9.00, 0, 0, 0.15),
    "Gemini 3.5 Flash-Lite": (0.30, 2.50, 0, 0, 0.03),
    "Gemini 3.1 Pro": (2.00, 12.00, 0, 0, 0.20),
    "Gemini 3.1 Flash-Lite": (0.25, 1.50, 0, 0, 0.025),
    "Gemini 3 Flash": (0.50, 3.00, 0, 0, 0.05),
    "Gemini 2.5 Pro": (1.25, 10.00, 0, 0, 0.125),
    "Gemini 2.5 Computer Use": (1.25, 10.00, 0, 0, 0),   # the page lists no caching
    "Gemini 2.5 Flash": (0.30, 2.50, 0, 0, 0.03),
    "Gemini 2.5 Flash-Lite": (0.10, 0.40, 0, 0, 0.01),
    # Cloudflare-hosted GLM 5.3, verified 2026-10-15 against
    # developers.cloudflare.com/workers-ai/models/glm-5.3/.
    # API-equivalent estimate; other hosts and subscription billing can differ.
    "GLM 5.3": (1.40, 4.40, 0, 0, 0.26),
}


# Earlier list prices, for usage logged BEFORE a vendor price change. PRICING
# always holds today's rate (the Optimize tab re-prices savings from it); a
# record dated on or before `until` bills at the older tuple instead, so a price
# cut never rewrites what past usage would have cost at the time.
#   display name -> [(until_date_inclusive, (in, out, cw5, cw1, cr)), ...], oldest first
PRICE_HISTORY = {
    # "Starting today", 2026-07-30: Terra -20%, Luna -80% (OpenAI staff post,
    # community.openai.com/t/1388484).
    "GPT-5.6 Terra": [("2026-07-29", (2.5, 15, 0, 0, 0.25))],
    "GPT-5.6 Luna": [("2026-07-29", (1, 6, 0, 0, 0.10))],
    # "Starting today", 2026-08-21: Sol -20% "for the next 3 months"
    # (community.openai.com/t/1391726).
    "GPT-5.6 Sol": [("2026-08-20", (5, 30, 0, 0, 0.50))],
}


# Claude request options that reprice a whole response, carried as a suffix on the
# model name so they show as their own row and price without a new record field.
FAST_SUFFIX = " (fast)"
US_SUFFIX = " (US)"
# Fast mode's own rate card (platform.claude.com pricing, 2026-09): the cache
# multipliers apply on top of the fast input rate, same as standard speed.
FAST_PRICING = {
    "Claude Opus 5.5": (8, 40, 10, 16, 0.40),
    "Claude Opus 5": (10, 50, 12.5, 20, 1.0),
    "Claude Opus 4.8": (10, 50, 12.5, 20, 1.0),
}
US_MULTIPLIER = 1.1          # inference_geo "us", every token category
WEB_SEARCH_USD = 10 / 1000   # Anthropic web search: $10 per 1,000 searches
LONG_CONTEXT_PRICING = {"Claude Haiku 5.5": (0.50, 2.50, 0.625, 1.0, 0.05)}
_LONG_FIELDS = ("in", "out", "cc5", "cc1", "cr")


def price_of(display, date=None, long_context=False):
    """(in, out, cw5, cw1, cr) for a model as of `date` ("YYYY-MM-DD"), else today.
    Unknown models price at zero."""
    if display.endswith(US_SUFFIX):
        return tuple(round(x * US_MULTIPLIER, 6)
                     for x in price_of(display[:-len(US_SUFFIX)], date, long_context))
    if display.endswith(FAST_SUFFIX):
        # a model with no published fast rate prices at zero rather than at a guess
        return FAST_PRICING.get(display[:-len(FAST_SUFFIX)], (0, 0, 0, 0, 0))
    display = _canonicalize(display)
    if long_context and display in LONG_CONTEXT_PRICING:
        return LONG_CONTEXT_PRICING[display]
    if date:
        for until, p in PRICE_HISTORY.get(display, ()):
            if date <= until:
                return p
    p = PRICING.get(display)
    if p is None:
        # a row cached under an older spelling ("gemini-3.1-pro-preview") still prices
        p = PRICING.get(_canonicalize(display), (0, 0, 0, 0, 0))
    return p


def vendor_of(display):
    d = display.lower()
    if d.startswith("claude"):
        return "Anthropic"
    if d.startswith(("gpt", "o3", "o4", "o1")):
        return "OpenAI"
    if "gemini" in d or "gemma" in d:
        return "Google"
    if display in ("Auto", "(synthetic)", "Unknown"):
        return "Other"
    return "Other"


# ---------------------------------------------------------------------------
# Model-name normalization → a single display name shared across all tools
# ---------------------------------------------------------------------------
def normalize_claude(raw):
    if not raw or raw == "<synthetic>":
        return "(synthetic)"
    base = re.sub(r"-\d{8}$", "", raw)               # strip trailing date snapshot
    if "mythos-preview" in base:
        return "Claude Mythos Preview"
    # handles both "claude-opus-4-8" (X.Y) and "claude-sonnet-5" (single version),
    # and the newer fable/mythos tiers of the Claude 5 family
    m = re.match(r"claude-(opus|sonnet|haiku|fable|mythos)-(\d+)(?:-(\d+))?$", base)
    if m:
        tier = m.group(1).capitalize()
        ver = f"{m.group(2)}.{m.group(3)}" if m.group(3) else m.group(2)
        return f"Claude {tier} {ver}"
    return raw


def normalize_codex(raw):
    if not raw:
        return "Unknown"
    # capture version + any named/size suffix (mini, nano, codex, sol, terra, luna, pro...)
    m = re.match(r"gpt-([\d.]+)(?:-([a-z]+))?", raw)
    if m:
        ver, suffix = m.group(1), m.group(2)
        name = f"GPT-{ver}"
        if suffix:
            name += " " + suffix.capitalize()
        return name
    if raw.startswith("o"):
        return raw
    return raw


def normalize_copilot(model_id, details):
    """Copilot's `details` string ("Claude Sonnet 4.5 • 0x") gives the cleanest
    display name; fall back to parsing the modelId ("anthropic/claude-sonnet-4-5-...")."""
    if details:
        name = details.split("•")[0].strip()
        if name:
            return _canonicalize(name)
    if model_id:
        base = model_id.split("/")[-1]
        base = re.sub(r"-\d{8}$", "", base)  # drop trailing date stamp
        return _canonicalize(base)
    return "Unknown"


def _canonicalize(name):
    """Map any display spelling, deployment alias, or raw model id to a canonical
    name. Copilot exposes the same model under many labels — e.g. 'Azure GPT-5.5',
    '(ai-foundry-10ms)gpt-5.5', 'OpenAI: GPT-5.5', 'azure-gpt-5.5' — all → 'GPT-5.5'."""
    n = re.sub(r"\([^)]*\)", "", name).strip()        # drop "(...)" qualifiers
    low = n.lower().replace("_", "-")
    # strip a leading provider / deployment tag
    low = re.sub(r"^(openai|azure|anthropic|google|microsoft|github(\.copilot[-\w]*)?|"
                 r"copilot|ai-foundry[-\w]*)\b[\s:/\-]*", "", low).strip()
    if low in ("auto", "copilot/auto", ""):
        return "Auto" if "auto" in low else "Unknown"
    if "raptor" in low:
        return "Raptor Mini"

    # Claude — "Claude Sonnet 4.5", "claude-sonnet-4-5", "claude-3.5-sonnet"
    tier = next((t.capitalize() for t in ("opus", "sonnet", "haiku") if t in low), None)
    if "claude" in low and tier:
        nums = re.findall(r"\d+(?:\.\d+)?", low)
        ver = ""
        if len(nums) >= 2 and "." not in nums[0] and "." not in nums[1] and len(nums[1]) == 1:
            ver = f"{nums[0]}.{nums[1]}"      # collapse "4-5" → "4.5"
        elif nums:
            ver = nums[0]
        return f"Claude {tier} {ver}".strip()

    # Gemini — "gemini-3.1-pro-preview", "Google: Gemini 4 Argon", "gemini-4-argon"
    # → one display name. Preview/date/effort suffixes drop; Live, TTS, image, embedding and
    # the like are different products with their own rates, so they keep their own spelling.
    mgm = re.match(r"(?:models/)?gemini-?\s*(\d+(?:\.\d+)?)-?\s*(argon|pro|flash[\s\-]*lite|flash|computer-use)\b(.*)$", low)
    if mgm and not re.search(r"live|tts|image|audio|embed|transcribe|translate|robot|omni",
                             mgm.group(3)):
        kind = {"argon": "Argon", "pro": "Pro", "flash": "Flash", "computer-use": "Computer Use"}.get(
            mgm.group(2), "Flash-Lite")
        return f"Gemini {mgm.group(1)} {kind}"

    # GPT with a version number → GPT-x.y (keep named/size variants distinct)
    mg = re.match(r"gpt-?\s*(\d+(?:\.\d+)?)", low)
    if mg:
        suffix = ""
        for s in ("sol", "terra", "luna", "astra", "mini", "nano", "pro", "codex"):
            if s in low:
                suffix = " " + s.capitalize()
                break
        return f"GPT-{mg.group(1)}{suffix}".strip()
    if low.startswith("gpt-oss"):
        return "GPT-OSS"

    # o-series (o3, o4-mini)
    mo = re.match(r"o\d+(?:-mini)?", low)
    if mo:
        return mo.group(0)

    return n or "Unknown"


# ---------------------------------------------------------------------------
# Timestamp helpers — everything is bucketed in the machine's LOCAL timezone
# ---------------------------------------------------------------------------
def _from_iso(ts):
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()
    except Exception:
        return None


def _from_ms(ms):
    try:
        return datetime.fromtimestamp(ms / 1000.0)
    except Exception:
        return None


def _buckets(dt):
    """Return (date 'YYYY-MM-DD', hour 0-23, day-of-week 0=Mon)."""
    return dt.strftime("%Y-%m-%d"), dt.hour, dt.weekday()


# ---------------------------------------------------------------------------
# Per-file aggregate container
# ---------------------------------------------------------------------------
def _blank_agg(source, path):
    agg = {
        "source": source,
        "path": path,
        "size": 0,
        "mtime": 0.0,
        "offset": 0,           # bytes parsed (jsonl only)
        "records": {},          # "date\tmodel" -> token/count dict
        "tools": {},
        # date\tskill -> tokens attributed to a Skill (Claude Code's attributionSkill)
        "skills": {},
        # date\tbucket -> tokens sent at that per-request context size
        "ctx": {},            # "date\ttool name" -> count
        "hourly": {},           # "date\thour" -> {tokens, msgs}  (day-of-week is
                                #   derived from the date, so it needs no bucket)
        "project": "(unknown)",
        "editor": None,
        "title": None,          # human-readable session name, when the tool logs one
        "branch": None,         # git branch the work happened on
        "entry": None,          # entrypoint / originator (CLI vs IDE)
        "cliver": None,         # tool version that wrote the log
        "totals": {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0,
                   "reason": 0, "asst": 0, "user": 0, "req": 0, "prem": 0.0,
                   "tools": 0, "side": 0},
        "first_ts": None,
        "last_ts": None,
        # Last real-turn timestamp seen, for gap-capped active-time tracking.
        # Only meaningful for single-session sources (claude/codex/copilot):
        # for those, parsing is incremental (byte-offset resume), so this MUST
        # persist across calls or every incremental chunk would look like a
        # burst with no prior event. It is harmless on a full reparse too — a
        # fresh _blank_agg resets it to None, and that one call walks the whole
        # file in order, so nothing is lost. Multi-session sources (Cursor,
        # opencode DB, Hermes) track this per-session in a local dict instead,
        # since one file holds many unrelated sessions and they are always
        # fully reparsed from scratch — see their own parse_* functions.
        "_active_last": None,
        # transient stream state for incremental codex parsing
        "state": {"cur_model": None},
        "sessions": [],
    }
    if source in ("claude", "claude-desktop", "codex"):
        # Present and empty means observed zero; absent on an older archive means
        # unknown. Keep event metadata separate from all billed usage counters.
        if source in ("claude", "codex"):
            agg["account_metadata_version"] = A.METADATA_VERSION
        agg["compactions"] = {}
        _reasoning_effort_init(agg)
    if source == "codex":
        _codex_timing_state(agg)  # empty logs need no repeated timing backfill
        agg["parent_metadata_version"] = 1
    return agg


def _rec(agg, date, model):
    key = f"{date}\t{model}"
    sid = agg.get("_record_session")
    bucket = (agg.setdefault("_session_records", {}).setdefault(sid, {})
              if sid is not None else agg["records"])
    r = bucket.get(key)
    if r is None:
        r = {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
              "asst": 0, "user": 0, "req": 0, "prem": 0.0, "tools": 0, "cost": 0.0,
              "active": 0.0}
        bucket[key] = r
    return r


_REASONING_EFFORT_VERSION = 1
_CLAUDE_EFFORTS = frozenset(("low", "medium", "high", "xhigh", "max"))
_CODEX_EFFORTS = _CLAUDE_EFFORTS | {"none", "minimal", "ultra"}


def _reasoning_effort_init(agg):
    """Optional recorded settings, separate from every accounting counter."""
    agg["reasoning_effort_version"] = _REASONING_EFFORT_VERSION
    return agg.setdefault("reasoning_effort", {"version": _REASONING_EFFORT_VERSION, "days": {}})


def _effort_level(value, allowed):
    # Never stringify source objects, infer a default, or interpret thinking tokens.
    return value if isinstance(value, str) and value in allowed else None


def _effort_identity(value):
    """Bound correlation identities without adding source ids to public metadata."""
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return None
    # json.loads accepts escaped lone surrogates in malformed source identities.
    # Keep those hashable without ever publishing or attempting to render them.
    return hashlib.sha256(value.encode("utf-8", errors="surrogatepass")).hexdigest()


def _reasoning_effort_observe(agg, dt, model, level, memo_name, identity=None,
                            limit=8, replace=False):
    """Count one accepted response observation; later blocks can enrich it.

    The observation keeps its original date/model even if a streamed correction
    arrives after midnight. Missing later metadata cannot erase an earlier value;
    explicit invalid metadata can. This does not create an accounting row.
    """
    if not dt:
        return
    bucket = f"{_buckets(dt)[0]}\t{model}"
    if bucket not in agg["records"]:
        return
    metadata = _reasoning_effort_init(agg)
    memo = agg["state"].setdefault(memo_name, {})
    key = _effort_identity(identity)
    previous = memo.pop(key, None) if key else None
    if previous:
        bucket, old_level = previous
        if level is None and not replace:
            level = old_level
        memo[key] = [bucket, level]
        if old_level == level:
            return
        row = metadata["days"][bucket]
        if old_level is None:
            row["unrecorded"] -= 1
        else:
            row["levels"][old_level] -= 1
            if not row["levels"][old_level]:
                del row["levels"][old_level]
    else:
        row = metadata["days"].setdefault(bucket, {"levels": {}, "unrecorded": 0})
        if key:
            memo[key] = [bucket, level]
    if level is None:
        row["unrecorded"] += 1
    else:
        row["levels"][level] = row["levels"].get(level, 0) + 1
    while len(memo) > limit:
        del memo[next(iter(memo))]


def _claude_effort(obj):
    # Claude Code's per-turn value supersedes its base effort only when nonnull.
    # An explicitly malformed override is evidence of an unavailable setting.
    value = obj.get("perTurnEffort")
    if value is not None:
        return _effort_level(value, _CLAUDE_EFFORTS), True
    value = obj.get("effort")
    return _effort_level(value, _CLAUDE_EFFORTS), value is not None


def _finish_store_records(agg):
    """Retain session attribution, then derive the store's date/model rollup."""
    rows = agg.pop("_session_records", {})
    dims = agg.pop("_session_dims", {})
    agg.pop("_record_session", None)
    # The full native id stays on the session: the visible id is only its first
    # eight characters, which OpenCode's time-ordered ids share within minutes.
    for s in agg["sessions"]:
        s["records"] = rows.get(s.get("_sid"), {})
        weights = {}
        for key, row in s["records"].items():
            model = key.split("\t", 1)[1]
            if model != "(user)":
                weights[model] = weights.get(model, 0) + sum(row.get(k, 0) for k in ("in", "out", "cr", "cc"))
        ranked = sorted(weights, key=weights.get, reverse=True)
        if ranked:
            s["model"] = ranked[0]
            s["models"] = ranked[:6]
            s["nmodels"] = len(ranked)
    agg["records"] = {}
    for bucket in rows.values():
        for key, row in bucket.items():
            date, model = key.split("\t", 1)
            total = _rec(agg, date, model)
            for field, value in row.items():
                if isinstance(value, (int, float)):
                    total[field] = total.get(field, 0) + value
    if _retention_kind(agg) == "store":
        # Private: what each session added to the store's tools and hourly rows.
        # Never exported or sent to the page; see reconcile().
        agg["_store_detail"] = {s["_sid"]: dims.get(s["_sid"]) or {"tools": {}, "hourly": {}}
                                for s in agg["sessions"] if s.get("_sid")}


# "Active time" — a gap-capped estimate of how long a real person was actually
# driving the tool, the same heuristic WakaTime/RescueTime use: sum the gaps
# BETWEEN consecutive real turns, but only when the gap is short enough that the
# user was plausibly still there. A long gap means they stepped away; the model
# "thinking" or running tools for a while does not, which is why the cap is
# generous rather than tight.
ACTIVE_GAP_CAP = 300.0  # seconds. Long enough to bridge a normal turn (thinking +
                        # a few tool calls); short enough that a coffee break or
                        # an overnight-resumed session doesn't count as "working".


def _active_gap(prev_iso, dt):
    """Seconds to attribute as active time for one event, given the previous
    real event's ISO timestamp (or None/unparseable). Returns 0 for the first
    event of a burst, or when the gap exceeds the cap — an unusually long gap
    means the user stepped away, not that they worked through it. Also 0, safely,
    if events arrive out of chronological order (a negative gap never passes)."""
    if not prev_iso:
        return 0.0
    try:
        prev = datetime.fromisoformat(prev_iso)
    except (TypeError, ValueError):
        return 0.0
    gap = (dt - prev).total_seconds()
    return gap if 0 < gap <= ACTIVE_GAP_CAP else 0.0


def _session_dims(agg):
    """A multi-session store's per-session tools/hourly, or None for one-session logs.
    Kept so a session the tool later deletes can keep its whole contribution."""
    sid = agg.get("_record_session")
    if sid is None:
        return None
    return agg.setdefault("_session_dims", {}).setdefault(sid, {"tools": {}, "hourly": {}})


def _tool(agg, date, name):
    """Count one tool/function call, keyed by day so the UI can date-filter it."""
    k = f"{date}\t{name}"
    agg["tools"][k] = agg["tools"].get(k, 0) + 1
    agg["totals"]["tools"] += 1
    dims = _session_dims(agg)
    if dims is not None:
        dims["tools"][k] = dims["tools"].get(k, 0) + 1


def _bump_time(agg, dt, tokens, msgs):
    date, hour, _dow = _buckets(dt)
    h = agg["hourly"].setdefault(f"{date}\t{hour}", {"tokens": 0, "msgs": 0})
    h["tokens"] += tokens
    h["msgs"] += msgs
    dims = _session_dims(agg)
    if dims is not None:
        sh = dims["hourly"].setdefault(f"{date}\t{hour}", {"tokens": 0, "msgs": 0})
        sh["tokens"] += tokens
        sh["msgs"] += msgs
    iso = dt.isoformat()
    if agg["first_ts"] is None or iso < agg["first_ts"]:
        agg["first_ts"] = iso
    if agg["last_ts"] is None or iso > agg["last_ts"]:
        agg["last_ts"] = iso


_TITLE_NOISE = re.compile(
    r"^\s*([-*>|]|#|<|```|\[|Context from my IDE|Files mentioned by the user|"
    r"Active file:|Screenshot|Caveat:|Distinguish instructions|<system-reminder)", re.I)


def _clean_prompt(text):
    """First line of a prompt that is actual user intent, not IDE/tool preamble."""
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if not line or _TITLE_NOISE.match(line):
            continue
        return line
    return ""


# Title sources, weakest to strongest. Tools APPEND a new title record every
# time the session is re-titled, so within a rank the LAST one seen wins —
# otherwise a session keeps the first name it was ever auto-given and a manual
# rename is silently ignored.
TITLE_RANK = {"prompt": 1, "ai": 2, "custom": 3}


def _set_title(agg, text, kind="prompt"):
    rank = TITLE_RANK.get(kind, 1)
    if kind == "prompt":
        text = _clean_prompt(text)
    if not text:
        return
    t = " ".join(str(text).split())[:90]
    if not t:
        return
    have = agg.get("_title_rank", 0)
    if rank < have:
        return                      # never let a weaker source overwrite
    if kind == "prompt" and agg.get("title"):
        return                      # the FIRST prompt, not the latest
    agg["title"] = t
    agg["_title_rank"] = rank


def _first_text(content):
    """First plain-text chunk of a Claude/Codex message content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for b in content:
            if isinstance(b, str):
                return b
            if isinstance(b, dict) and isinstance(b.get("text"), str):
                return b["text"]
    return ""


# ===========================================================================
# ACTIVITY — what each turn was for, and whether its edits landed first time
# ===========================================================================
# A turn is one typed prompt plus all the agent work until the next typed prompt.
# It is classified mostly from what the agent DID — which kinds of files it edited,
# which shell commands it ran (a test runner, git, an installer) — because that is
# harder to get wrong than guessing from words. The prompt's wording only settles
# what an edit was for (a fix, a refactor, a feature) and what a tool-free turn was.
# The prompt is scored when it arrives and dropped; the cache keeps a few small
# per-intent counts (_prompt_intents), never its text.
CATEGORIES = ("build", "fix", "refactor", "test", "docs", "review", "explore",
              "research", "data", "plan", "delegate", "vcs", "ops", "chat")
# Older cache entries used different ids; map them so archived rows still land.
_LEGACY_CATEGORY = {"coding": "build", "feature": "build", "debugging": "fix",
                    "refactoring": "refactor", "testing": "test", "exploration": "explore",
                    "planning": "plan", "delegation": "delegate", "git": "vcs",
                    "build/deploy": "ops", "brainstorming": "plan", "conversation": "chat",
                    "general": "chat"}

# What a tool call is, by the names each agent actually logs (seen in local logs:
# Claude Code's Bash/Read/Edit/Write/WebFetch/..., Codex's exec/exec_command/
# write_stdin/apply_patch/update_plan/view_image).
_TOOL_KIND = {
    "Edit": "edit", "Write": "edit", "MultiEdit": "edit", "NotebookEdit": "edit",
    "apply_patch": "edit",
    "Read": "read", "Grep": "read", "Glob": "read", "LS": "read", "NotebookRead": "read",
    "view_image": "read",
    "Bash": "shell", "PowerShell": "shell", "exec": "shell", "exec_command": "shell",
    "write_stdin": "shell", "shell": "shell", "local_shell": "shell",
    "WebFetch": "web", "WebSearch": "web", "web_search": "web",
    "Agent": "agent", "Task": "agent", "Workflow": "agent",
    "TodoWrite": "plan", "EnterPlanMode": "plan", "ExitPlanMode": "plan",
    "TaskCreate": "plan", "TaskUpdate": "plan", "update_plan": "plan",
    "Skill": "skill",
    "AskUserQuestion": "ask", "request_user_input": "ask", "request_user_input_async": "ask",
}


def _tool_kind(name):
    if name.startswith("mcp__"):
        return "mcp"
    return _TOOL_KIND.get(name, "other")


# --- shell commands: what a run was, judged from the program it starts ---------
_CMD_SPLIT = re.compile(r"\|\||&&|[;|\n]")
_WRAPPERS = {"sudo", "env", "time", "nohup", "exec", "command", "timeout", "caffeinate"}
_LAUNCHERS = {"npx", "bunx", "uvx", "pipx"}          # the NEXT word is the program
_TEST_PROGS = {"pytest", "jest", "vitest", "mocha", "rspec", "phpunit", "ctest", "tox",
               "nox", "ava", "karma", "playwright", "cypress"}
_LINT_PROGS = {"tsc", "eslint", "ruff", "mypy", "pyright", "flake8", "pylint", "black",
               "prettier", "golangci-lint", "shellcheck", "stylelint", "biome", "rubocop"}
_OPS_PROGS = {"docker", "docker-compose", "kubectl", "helm", "terraform", "brew", "apt",
              "apt-get", "pip", "pip3", "uv", "poetry", "systemctl", "vercel", "flyctl",
              "netlify", "wrangler", "pm2", "make", "cmake", "gradle", "mvn", "xcodebuild"}
_RUNTIMES = {"python", "python3", "node", "deno", "bun", "ruby", "php", "perl", "java",
             "bash", "sh", "zsh", "swift", "go", "cargo", "dotnet", "npm", "yarn", "pnpm"}
_PKG = {"npm", "yarn", "pnpm", "bun"}
_GIT_WRITE = {"commit", "push", "pull", "merge", "rebase", "checkout", "switch", "cherry-pick",
              "tag", "stash", "reset", "revert", "add", "restore", "am", "apply", "fetch", "clone"}


def _cmd_kinds(cmd):
    """{"test", "vcs", "ops", "check"} for one shell command line — "check" meaning
    it ran, built, linted or tested something, i.e. it could tell an edit didn't work."""
    kinds = set()
    for seg in _CMD_SPLIT.split(re.sub(r"'[^']*'|\"[^\"]*\"", "''", cmd or "")):
        w = [x for x in seg.split() if not re.match(r"^[A-Za-z_]\w*=", x)]
        while w and _leaf(w[0]) in _WRAPPERS:
            w = w[1:]
        if w and _leaf(w[0]) in _LAUNCHERS:
            w = w[1:]
        if not w:
            continue
        prog, args = _leaf(w[0]).lower(), [a.lower() for a in w[1:4]]
        a0 = args[0] if args else ""
        if (prog in _TEST_PROGS
                or (prog in _PKG and a0 in ("test", "t"))
                or (prog in ("go", "cargo", "dotnet", "swift", "deno", "mvn", "gradle") and "test" in args[:2])
                or (prog.startswith("python") and ("pytest" in args or "unittest" in args))
                or (prog == "node" and "--test" in args) or (prog == "make" and a0 == "test")):
            kinds.update(("test", "check"))
        elif prog == "git":
            if a0 in _GIT_WRITE:
                kinds.add("vcs")
        elif prog == "gh":
            if a0 in ("pr", "release"):
                kinds.add("vcs")
        elif (prog in _OPS_PROGS
              or (prog in _PKG and a0 in ("install", "i", "ci", "add", "publish", "build"))
              or (prog in ("cargo", "go") and a0 in ("build", "install"))):
            kinds.update(("ops", "check"))
        elif prog in _LINT_PROGS or (prog in ("cargo", "go") and a0 in ("check", "vet", "clippy")):
            kinds.add("check")
        elif prog in _RUNTIMES or prog.startswith("./") or w[0].startswith("./"):
            # running the project: a script, `node -e`, `npm run dev`, `go run`...
            if args or prog in ("npm", "yarn", "pnpm"):
                kinds.add("check")
        elif prog in ("curl", "wget", "http") and re.search(r"localhost|127\.0\.0\.1|0\.0\.0\.0", seg):
            kinds.add("check")
    return kinds


# --- files: an edit to docs or tests says what the turn was for ------------------
_DOC_EXT = (".md", ".mdx", ".txt", ".rst", ".adoc")
_TEST_PATH = re.compile(r"(^|[/\\])(tests?|__tests__|specs?)[/\\]|(^|[/\\])test_[^/\\]*$"
                        r"|_test\.\w+$|\.(test|spec)\.\w+$", re.I)


def _file_kind(path):
    p = str(path or "")
    if not p:
        return "code"
    if _TEST_PATH.search(p):
        return "test"
    return "doc" if p.lower().endswith(_DOC_EXT) else "code"


# --- the prompt's intent: weighted word lists, highest total wins ----------------
_INTENTS = (   # (intent, weight, pattern) — order breaks ties
    ("fix", 3, r"\b(fix(e[sd]|ing)?|bugs?|buggy|broken|breaks?|crash\w*|errors?|fail(s|ed|ing|ure)?|"
               r"regress\w*|not working|doesn'?t work|isn'?t working|wrong|incorrect|traceback|"
               r"exception|stack ?trace|flaky|hotfix)\b"),
    ("refactor", 3, r"\b(refactor\w*|renam\w*|restructur\w*|reorgani[sz]\w*|clean ?up|cleanup|"
                    r"simplif\w*|dedup\w*|de-?duplicat\w*|tidy|decoupl\w*|modulari[sz]\w*)\b"),
    ("test", 2, r"\b(tests?|testing|unit ?tests?|e2e|coverage|pytest|jest|vitest|specs?)\b"),
    ("docs", 2, r"\b(docs?|documentation|document(ing)?|readme|changelog|docstrings?|"
                r"release notes|write-?up)\b"),
    ("review", 2, r"\b(review\w*|audit\w*|critique|look (it )?over|double[- ]check|"
                  r"sanity[- ]check|second opinion)\b"),
    ("plan", 2, r"\b(plan(ning)?|design|architect\w*|approach(es)?|roadmap|proposal|strategy|"
                r"brainstorm\w*|ideas?|trade-?offs?)\b"),
    ("build", 1, r"\b(add(s|ing)?|implement\w*|create\w*|build(ing)?|new|features?|support|"
                 r"introduc\w*|integrat\w*|set ?up|scaffold\w*)\b"),
)
_INTENT_RX = [(k, w, re.compile(p, re.I)) for k, w, p in _INTENTS]


def _prompt_intents(text):
    """{intent: score} for a prompt — small ints only; the text itself is dropped."""
    t = (text or "")[:4000]
    out = {}
    for k, w, rx in _INTENT_RX:
        n = min(len(rx.findall(t)), 5)
        if n:
            out[k] = n * w
    return out


def _top_intent(scores):
    best = None
    for k, _, _ in _INTENTS:                 # declaration order breaks ties
        if scores.get(k, 0) > (scores.get(best, 0) if best else 0):
            best = k
    return best


def _classify_turn(tr):
    n, cmd, fk = tr.get("n") or {}, tr.get("cmd") or {}, tr.get("fk") or {}
    intent = _top_intent(tr.get("iv") or {})
    if n.get("edit"):
        if fk.get("doc") and not (fk.get("code") or fk.get("test")):
            return "docs"
        if fk.get("test") and not fk.get("code"):
            return "test"
        return intent if intent in ("fix", "refactor", "test") else "build"
    if n.get("agent"):
        return "delegate"
    if cmd.get("test"):
        return "test"
    if cmd.get("vcs") and cmd.get("vcs", 0) >= cmd.get("ops", 0):
        return "vcs"
    if cmd.get("ops"):
        return "ops"
    looked = n.get("read", 0) + n.get("shell", 0)
    if n.get("mcp") and n["mcp"] >= looked + n.get("web", 0):
        return "data"
    if n.get("web") and n["web"] >= looked:
        return "research"
    if n.get("plan") and not looked:
        return "plan"
    if looked or n.get("mcp") or n.get("web"):
        return intent if intent in ("review", "fix", "plan") else "explore"
    return "plan" if intent == "plan" else "chat"


def _turn_open(agg, dt, text, implicit=False):
    """A typed prompt: close the running turn and start a new one. `implicit` is the
    work a session does before its first counted prompt (it opened with a slash
    command or a task notification): its tokens are classified like any turn's, but
    it isn't a prompt, so it adds nothing to turn, edit or one-shot counts."""
    if agg.get("subagent"):      # a subagent's whole file is one delegated task
        return
    _turn_close(agg)
    agg["state"]["turn"] = {"d": _buckets(dt)[0], "iv": _prompt_intents(text), "n": {},
                            "cmd": {}, "fk": {}, "ed": {}, "rw": 0, "tok": {}}
    if implicit:
        agg["state"]["turn"]["imp"] = 1


def _turn_tool(agg, name, file=None, cmd=None):
    """One tool call inside the running turn. Rework = a file edited again after a
    command ran that could have shown the previous edit didn't work."""
    tr = agg["state"].get("turn")
    if not tr:
        return
    kind = _tool_kind(name)
    tr["n"][kind] = tr["n"].get(kind, 0) + 1
    if kind == "shell" and cmd:
        ks = _cmd_kinds(cmd)
        for k in ks - {"check"}:
            tr["cmd"][k] = tr["cmd"].get(k, 0) + 1
        if "check" in ks:
            for f in tr["ed"]:
                tr["ed"][f] = 1          # every file edited so far has now been checked
    elif kind == "edit":
        f = file or "?"
        fk = _file_kind(file)
        tr["fk"][fk] = tr["fk"].get(fk, 0) + 1
        if tr["ed"].get(f) == 1:
            tr["rw"] += 1
        if f in tr["ed"] or len(tr["ed"]) < 64:
            tr["ed"][f] = 0

def _turn_usage(agg, model, inp, out, cr, cc5, cc1, ws=0):
    tr = agg["state"].get("turn")
    if not tr:
        return
    t = tr["tok"].setdefault(model or "Unknown", [0, 0, 0, 0, 0, 0, 0])
    for j, v in enumerate((inp, out, cr, cc5, cc1, ws, 1)):
        t[j] += v


_ACT_FIELDS = ("in", "out", "cr", "cc5", "cc1", "ws")


def _turn_rows(tr):
    """One finished (or still-open) turn -> {"date\\tmodel\\tcategory": row}."""
    tok = tr.get("tok") or {}
    if not tok:                  # no reply yet, or interrupted before one
        return {}
    cat = _classify_turn(tr) if "n" in tr else "chat"   # a pre-v48 open turn
    # an implicit turn (see _turn_open) counts its tokens only: it was no prompt
    edits = bool((tr.get("n") or {}).get("edit")) and not tr.get("imp")
    rt = tr.get("rw", 0)
    dom = max(tok, key=lambda m: (tok[m][6], tok[m][0] + tok[m][1]))
    out = {}
    for m, t in tok.items():
        row = {k: t[j] for j, k in enumerate(_ACT_FIELDS)}
        if len(t) > 7:
            _bump_long(row, t[7:12])
        row.update(turns=0, edits=0, oneshot=0, retries=0)
        if m == dom and not tr.get("imp"):
            row.update(turns=1, edits=int(edits), oneshot=int(edits and not rt), retries=rt)
        if edits:                # every editing turn, so a retried one can be compared to the rest
            row.update({"e" + k: t[j] for j, k in enumerate(_ACT_FIELDS)})
            if len(t) > 7:
                _bump_long(row, t[7:12], "e")
        if edits and rt:         # the whole turn's cost is the price of not landing it
            row.update({"r" + k: t[j] for j, k in enumerate(_ACT_FIELDS)})
            if len(t) > 7:
                _bump_long(row, t[7:12], "r")
        out[f"{tr['d']}\t{m}\t{cat}"] = row
    return out


def _add_rows(dst, rows):
    for k, row in rows.items():
        e = dst.setdefault(k, {})
        for f, v in row.items():
            e[f] = e.get(f, 0) + v


def _turn_close(agg):
    tr = agg["state"].pop("turn", None)
    if tr:
        rows = _turn_rows(tr)
        _add_rows(agg.setdefault("activity", {}), rows)
        for h in agg["state"].get("haiku_context", {}).values():
            values = h["turns"].pop("", None)
            if values is not None:
                key = next((k for k in rows if k.split("\t")[1] == h["model"]), None)
                if key:
                    dst = h["turns"].setdefault(key, [0] * 5)
                    for i, v in enumerate(values): dst[i] += v


def activity_of(agg):
    """Closed turns plus the one still open — a session's last turn has no next
    prompt to close it. Read-only, so a later chunk can still extend that turn."""
    rows = {}
    for k, row in (agg.get("activity") or {}).items():
        d, m, c = (k.split("\t") + ["", "", ""])[:3]
        _add_rows(rows, {f"{d}\t{m}\t{_LEGACY_CATEGORY.get(c, c)}": row})
    tr = (agg.get("state") or {}).get("turn")
    if tr:
        _add_rows(rows, _turn_rows(tr))
    return rows


# ===========================================================================
# READ HYGIENE — reads that put tokens into context for nothing (Claude Code)
# ===========================================================================
# A file read again with no edit to it since (and no /compact in between) adds
# the same tokens to context a second time; a read inside generated or vendored
# folders is rarely what anyone meant. Counted per day, with the size of what
# each such read returned (chars / 4) — never the content itself.
_JUNK_PATH = re.compile(
    r"(^|[/\\])(node_modules|dist|build|out|\.next|\.nuxt|\.svelte-kit|target|vendor|"
    r"__pycache__|\.venv|venv|\.git|coverage|\.cache|\.turbo|Pods|DerivedData)([/\\]|$)"
    r"|\.min\.(js|css)$|(^|[/\\])(package-lock\.json|yarn\.lock|pnpm-lock\.yaml|"
    r"poetry\.lock|Cargo\.lock|Gemfile\.lock)$")


def _read_hygiene(agg, date, name, inp, tool_id):
    st = agg["state"]
    seen, ids = st.setdefault("rd", {}), st.setdefault("rid", {})
    kind = _tool_kind(name)
    if kind == "edit":
        p = inp.get("file_path") or inp.get("notebook_path")
        if p:
            for k in [k for k in seen if k.split("\t", 1)[0] == p]:
                del seen[k]      # changed: reading it again is how you see the change
        return
    if name not in ("Read", "Grep"):
        return
    p = str(inp.get("file_path") or inp.get("path") or "")
    # [reads, re-reads, junk reads, re-read tokens, junk tokens]
    e = agg.setdefault("reads", {}).setdefault(date, [0, 0, 0, 0, 0])
    flag = None
    if name == "Read" and p:
        e[0] += 1
        key = f"{p}\t{inp.get('offset')}\t{inp.get('limit')}"   # a different slice is new
        if key in seen:
            e[1] += 1
            flag = 1
        seen[key] = 1
        if len(seen) > 512:
            seen.clear()
    if p and _JUNK_PATH.search(p):
        e[2] += 1
        flag = 2
    if flag and tool_id:
        ids[tool_id] = [date, flag]
        while len(ids) > 64:
            ids.pop(next(iter(ids)))


def _result_chars(c):
    if isinstance(c, str):
        return len(c)
    if isinstance(c, list):
        return sum(len(b.get("text") or "") for b in c if isinstance(b, dict))
    return 0


def _read_result(agg, blk):
    """A tool_result: if it answers a flagged read, book the tokens it returned."""
    hit = agg["state"].get("rid", {}).pop(blk.get("tool_use_id"), None)
    if hit:
        e = agg.setdefault("reads", {}).setdefault(hit[0], [0, 0, 0, 0, 0])
        e[2 + hit[1]] += _result_chars(blk.get("content")) // 4


# ===========================================================================
# CLAUDE CODE
# ===========================================================================
def _is_subagent_path(path):
    """Claude Code writes each subagent's transcript to
    <session-id>/subagents/agent-<id>.jsonl. Those files are 100% isSidechain."""
    p = str(path or "").replace("\\", "/")
    return "/subagents/" in p and _leaf(p).startswith("agent-")


# How the type:"user" records Claude Code writes on its own begin — none is a
# prompt (see the user branch of parse_claude).
_NOT_TYPED = ("<local-command-caveat>", "<command-name>", "<command-message>",
              "<local-command-stdout>", "<task-notification>", "[Request interrupted by user")
# a slash command ("/compact", "/model opus") — but not a path like "/Users/..."
_SLASH_CMD = re.compile(r"/[a-z][\w:.-]*(?:\s|$)")


def _claude_compaction(agg, obj, dt):
    """Record a completed boundary, never its summary text or inferred usage."""
    meta = obj.get("compactMetadata")
    meta = meta if isinstance(meta, dict) else {}
    def count(key):
        value = meta.get(key)
        return value if type(value) is int and value >= 0 else None
    # Claude supplies UUIDs. Older/id-less markers have a stable timestamp key;
    # coalescing identical completion stamps is safer than counting a replay.
    uid = obj.get("uuid")
    key = uid if isinstance(uid, str) and uid else "at:" + dt.astimezone(timezone.utc).isoformat()
    events = agg.setdefault("compactions", {})
    if key not in events:
        events[key] = {"id": key, "timestamp": dt.isoformat(), "date": _buckets(dt)[0],
                       "trigger": meta.get("trigger") if meta.get("trigger") in ("manual", "auto") else "unknown",
                       "before": count("preTokens"), "after": count("postTokens"),
                       "duration_ms": count("durationMs")}


def _long_values(row, prefix=""):
    return tuple(row.get(prefix + "lc_" + f, 0) for f in _LONG_FIELDS)


def _bump_long(row, values, prefix=""):
    # Pricing subsets only: never add these to tokens, messages or model calls.
    for f, v in zip(_LONG_FIELDS + ("asst",), values):
        key = prefix + "lc_" + f
        row[key] = row.get(key, 0) + v


def _haiku_price_usage(agg, rk, model, date, full, delta, first, skill):
    """Remember a request's pricing tier separately from the existing usage deltas."""
    st = agg["state"]
    memo = st.setdefault("haiku_context", {})
    h = memo.setdefault(rk, {"model": model, "long": False, "days": {}, "turns": {}, "skills": {}}) if rk else {
        "model": model, "long": False, "days": {}, "turns": {}, "skills": {}}
    values = [delta[0], delta[1], delta[4], delta[5], delta[2], int(first)]
    _bump_long(_rec(agg, date, model), [0] * 6)
    day = h["days"].setdefault(date, [0] * 6)
    for i, v in enumerate(values): day[i] += v
    tr = st.get("turn")
    if tr:
        turn = h["turns"].setdefault("", [0] * 5)
        for i, v in enumerate(values[:5]): turn[i] += v
    if skill:
        sk = date + "\t" + skill
        usage = agg["skills"][sk].setdefault("haiku_usage", {}).setdefault(model, {})
        for f, v in zip(_LONG_FIELDS, values): usage[f] = usage.get(f, 0) + v
        part = h["skills"].setdefault(sk, [0] * 6)
        for i, v in enumerate(values): part[i] += v
    # resp holds per-component maxima, including streamed corrections. Input
    # includes cache reads/writes; output and reasoning do not set the tier.
    current = st["resp"][rk] if rk else full
    long = current[0] + current[2] + max(current[3], current[4] + current[5]) > 100_000
    upgrade = long and not h["long"]
    if upgrade:
        # If input metadata grows across blocks, reprice earlier contributions
        # on their original dates/turns/skills without changing any usage counts.
        for d, part in h["days"].items(): _bump_long(_rec(agg, d, model), part)
        for key, part in h["turns"].items():
            if key:
                row = agg["activity"][key]
                _bump_long(row, part)
                if "ein" in row: _bump_long(row, part, "e")
                if "rin" in row: _bump_long(row, part, "r")
        for sk, part in h["skills"].items():
            _bump_long(agg["skills"][sk]["haiku_usage"][model], part)
    elif h["long"]:
        _bump_long(_rec(agg, date, model), values)
        if skill: _bump_long(usage, values)
    if tr and (upgrade or h["long"]):
        part = h["turns"][""] if upgrade else values[:5]
        tokens = tr["tok"][model]
        tokens.extend([0] * max(0, 12 - len(tokens)))
        for i, v in enumerate(part): tokens[7 + i] += v
    h["long"] = h["long"] or long
    agg["haiku_pricing"] = 1


def parse_claude(agg, lines):
    agg["account_metadata_version"] = A.METADATA_VERSION
    _reasoning_effort_init(agg)
    project = agg["project"]
    st = agg["state"]
    # Every record already counted in this file, as the first 12 hex chars of its
    # uuid (plenty to be unique within one file), packed into one string to keep
    # the cache small. Resuming a session can replay its WHOLE history into the
    # same file — same uuids and timestamps, rewritten by the newer CLI — and each
    # replayed record was counted again: usage, tool calls and prompts alike (one
    # session carried 1,379 of them). Persisted because parsing resumes by byte
    # offset, so a replay arrives in a later chunk than the records it copies.
    packed = st.get("seen_uuids") or ""
    seen = {packed[i:i + 12] for i in range(0, len(packed), 12)}
    fresh_uuids = []
    # usage already counted for the last few responses — see the assistant branch
    resp = st.get("resp") or {}
    for line in lines:
        if not line.strip():
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        uid = o.get("uuid")
        if uid:
            uk = (str(uid).replace("-", "") + "0" * 12)[:12]
            if uk in seen:
                continue
            seen.add(uk)
            fresh_uuids.append(uk)
        t = o.get("type")
        A.observe_claude(agg, o)
        cwd = o.get("cwd")
        if cwd:
            project = _leaf(cwd) or cwd
            agg["cwd"] = cwd
        # after a compaction the old reads are gone from context; re-reading is fine
        if o.get("isCompactSummary") or (o.get("type") == "system" and o.get("subtype") == "compact_boundary"):
            agg["state"].get("rd", {}).clear()
        # session metadata Claude Code writes on every entry
        if o.get("gitBranch"):
            agg["branch"] = o["gitBranch"]
        if o.get("entrypoint"):
            agg["entry"] = o["entrypoint"]
        if o.get("version"):
            agg["cliver"] = o["version"]
        # the tool's own name for the session beats a prompt snippet
        if o.get("customTitle"):
            _set_title(agg, o["customTitle"], "custom")
        elif o.get("aiTitle"):
            _set_title(agg, o["aiTitle"], "ai")
        side = bool(o.get("isSidechain"))
        msg = o.get("message") if isinstance(o.get("message"), dict) else None
        dt = _from_iso(o.get("timestamp", "")) if o.get("timestamp") else None
        if (t == "system" and o.get("subtype") == "compact_boundary" and dt
                and (not side or agg.get("subagent"))):
            # Inline sidechains aren't the parent's own compactions. A child's
            # standalone transcript records its events under its own session.
            _claude_compaction(agg, o, dt)

        # model "<synthetic>" is Claude Code talking, not the model: "Prompt is too
        # long", usage-limit notices, API errors. Zero usage, so it was only ever
        # inflating "assistant msgs" (119 of them) and adding a "(synthetic)" row.
        if t == "assistant" and msg and msg.get("model") != "<synthetic>":
            model = normalize_claude(msg.get("model"))
            haiku = model == "Claude Haiku 5.5"
            u = msg.get("usage") or {}
            # Two request options change the price of every token, so they become
            # their own priced model rows (see price_of): fast mode has its own
            # rate card, and US-only inference bills 1.1x on 4.6+ models.
            if u.get("speed") == "fast":
                model += FAST_SUFFIX
            if str(u.get("inference_geo") or "").lower() == "us":
                model += US_SUFFIX
            inp = int(u.get("input_tokens", 0) or 0)
            out = int(u.get("output_tokens", 0) or 0)
            cr = int(u.get("cache_read_input_tokens", 0) or 0)
            cc = int(u.get("cache_creation_input_tokens", 0) or 0)
            # Thinking tokens are a SUBSET of output_tokens (never additive) — the
            # same convention Codex's reasoning_output_tokens uses, and what the UI
            # assumes when it shows "of which reasoning" without stacking it.
            # Without this Claude's extended thinking is invisible: the token
            # composition card and the Optimize "thinking" finding only ever saw Codex.
            reason = int((u.get("output_tokens_details") or {}).get("thinking_tokens", 0) or 0)
            ccd = u.get("cache_creation") or {}
            cc5 = int(ccd.get("ephemeral_5m_input_tokens", 0) or 0)
            cc1 = int(ccd.get("ephemeral_1h_input_tokens", 0) or 0)
            if cc and not (cc5 or cc1):   # older logs without the tier split
                cc5 = cc                  # assume 5-min when untiered
            # server-side web searches bill per search on top of tokens
            ws = int((u.get("server_tool_use") or {}).get("web_search_requests", 0) or 0)
            # Claude Code writes ONE record per content block of a response —
            # thinking, text, each tool_use — and every one repeats the whole
            # response's usage, so summing records counted each API call ~2.3x
            # (Sep 2026: 3.16B tokens logged for 1.35B billed). Count a response
            # once, keyed the way the API bills it; a later block adds only what
            # grew, since output_tokens streams upward (1 on the first block, 388
            # by the last). With replays gone a response's blocks are contiguous,
            # so remembering the last few responses is enough.
            full = [inp, out, cr, cc, cc5, cc1, reason, ws]
            rk = f"{msg['id']}\t{o.get('requestId')}" if msg.get("id") else None
            prev = resp.pop(rk, None) if rk else None
            first = prev is None
            if prev is not None and len(prev) < len(full):   # state saved by an older build
                prev = list(prev) + [0] * (len(full) - len(prev))
            if rk:
                resp[rk] = full if first else [max(a, b) for a, b in zip(full, prev)]
                st["resp"] = resp
                while len(resp) > 8:
                    old_key = next(iter(resp))
                    resp.pop(old_key)
                    st.get("haiku_context", {}).pop(old_key, None)
                    st.get("claude_effort_responses", {}).pop(_effort_identity(old_key), None)
            if not first:
                inp, out, cr, cc, cc5, cc1, reason, ws = (max(0, a - b) for a, b in zip(full, prev))
            if dt:
                r = _rec(agg, _buckets(dt)[0], model)
                effort, explicit = _claude_effort(o)
                if side and not agg.get("subagent"):
                    effort, explicit = None, True
                if first or _effort_identity(rk):
                    _reasoning_effort_observe(agg, dt, model, effort,
                        "claude_effort_responses", rk, replace=explicit)
                r["in"] += inp; r["out"] += out; r["cr"] += cr; r["cc"] += cc
                r["cc5"] += cc5; r["cc1"] += cc1
                r["reason"] += reason
                if ws:
                    r["ws"] = r.get("ws", 0) + ws
                    agg["totals"]["ws"] = agg["totals"].get("ws", 0) + ws
                if "turn" not in agg["state"]:          # replying before any counted prompt
                    _turn_open(agg, dt, None, implicit=True)
                _turn_usage(agg, model, inp, out, cr, cc5, cc1, ws)
                r["asst"] += int(first)
                # count tool_use blocks
                tools = 0
                content = msg.get("content")
                if isinstance(content, list):
                    for blk in content:
                        if isinstance(blk, dict) and blk.get("type") == "tool_use":
                            _tool(agg, _buckets(dt)[0], blk.get("name", "tool"))
                            tools += 1
                            inp_ = blk.get("input") if isinstance(blk.get("input"), dict) else {}
                            nm_ = blk.get("name", "tool")
                            _read_hygiene(agg, _buckets(dt)[0], nm_, inp_, blk.get("id"))
                            # which installed agents and skills get used, by name
                            if nm_ in ("Agent", "Task") and isinstance(inp_.get("subagent_type"), str):
                                k_ = f"{_buckets(dt)[0]}\tagent\t{inp_['subagent_type']}"
                            elif nm_ == "Skill" and isinstance(inp_.get("skill"), str):
                                k_ = f"{_buckets(dt)[0]}\tskill\t{inp_['skill']}"
                            else:
                                k_ = None
                            if k_:
                                iu = agg.setdefault("used_ext", {})
                                iu[k_] = iu.get(k_, 0) + 1
                            _turn_tool(agg, blk.get("name", "tool"),
                                       file=inp_.get("file_path") or inp_.get("notebook_path"),
                                       cmd=inp_.get("command") if isinstance(inp_.get("command"), str) else None)
                r["tools"] += tools
                _bump_time(agg, dt, inp + out + cr + cc, int(first))
                r["active"] += _active_gap(agg["_active_last"], dt)
                agg["_active_last"] = dt.isoformat()
                date0 = _buckets(dt)[0]
                tok = inp + out + cr + cc
                # Which Skill was driving this request, if any. Claude Code stamps
                # attributionSkill on the records a skill produced.
                sk = o.get("attributionSkill")
                if sk:
                    k = f"{date0}\t{sk}"
                    e = agg["skills"].setdefault(k, {"tok": 0, "asst": 0,
                                                     "in": 0, "out": 0, "cr": 0, "cc": 0})
                    e["tok"] += tok; e["asst"] += int(first)
                    e["in"] += inp; e["out"] += out; e["cr"] += cr; e["cc"] += cc
                # How big the context was for THIS request: everything that had to be
                # sent, cached or not. Long conversations cost more even when cached.
                # Taken from the full record, not the delta a later block adds.
                ctx = full[0] + full[2] + full[3]
                # what the session's first request carried before any work: system
                # prompt, tool definitions, CLAUDE.md, memory — the fixed cost of opening one
                if first and not side and agg.get("open_ctx") is None:
                    agg["open_ctx"] = ctx
                b = ("0-50k" if ctx < 50_000 else "50-150k" if ctx < 150_000
                     else "150-400k" if ctx < 400_000 else "400k+")
                ck = f"{date0}\t{b}"
                ce = agg["ctx"].setdefault(ck, {"tok": 0, "n": 0})
                ce["tok"] += tok; ce["n"] += int(first)
                T = agg["totals"]
                T["in"] += inp; T["out"] += out; T["cr"] += cr; T["cc"] += cc
                T["reason"] += reason
                T["cc5"] += cc5; T["cc1"] += cc1
                T["asst"] += int(first)
                if side:                      # spawned subagent, not the main loop
                    T["side"] += inp + out + cr + cc
                if haiku:
                    _haiku_price_usage(agg, rk, model, date0, full,
                        [inp, out, cr, cc, cc5, cc1], first, sk)
        elif t == "user" and msg:
            # Only count what the user actually typed. type:"user" is also how
            # Claude Code logs tool_result echoes and much of its own traffic:
            #   isMeta — anything the harness injects by itself: the "[Image: ...]"
            #     caption after a pasted screenshot (older builds flag it without
            #     turnCompanion), a skill's body, a slash command's expansion, and
            #     "Continue from where you left off." after a "Prompt is too long"
            #     or usage-limit stop.
            #   isCompactSummary — the summary a finished /compact replays back.
            #   origin.kind other than "human" — newer builds stamp who wrote a
            #     turn, and a finished background task arrives as "task-notification".
            #   _NOT_TYPED openings — the three records a slash command (/compact,
            #     /model, ...) writes, a task notification from before `origin`
            #     existed, and the marker pressing Esc leaves behind.
            # Without these, one /compact added +4 to "your prompts", a pasted
            # screenshot +1, and ~14% of all prompts were never typed at all.
            content = msg.get("content")
            is_tool_result = isinstance(content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content)
            if is_tool_result and agg["state"].get("rid"):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result":
                        _read_result(agg, b)
            org = o.get("origin")
            not_typed = (bool(o.get("isMeta")) or bool(o.get("isCompactSummary"))
                         or (isinstance(org, dict) and org.get("kind", "human") != "human")
                         or _first_text(content).lstrip().startswith(_NOT_TYPED))
            if not is_tool_result and not not_typed and dt:
                r = _rec(agg, _buckets(dt)[0], "(user)")
                r["user"] += 1
                agg["totals"]["user"] += 1
                # Normally a sidechain turn is a subagent talking inside a parent
                # session and must not retitle it. But a subagents/agent-*.jsonl file
                # is nothing BUT sidechain, so its first prompt is the task it was
                # given — without this the row has no title at all.
                if not side or agg.get("subagent"):
                    _set_title(agg, _first_text(content), "prompt")
                if not side:
                    _turn_open(agg, dt, _first_text(content))

        elif t == "attachment":
            # A message typed WHILE Claude is working ("steering") is never written
            # as a type:"user" record — Claude Code queues it and logs it here, so
            # the branch above never sees it and those prompts went uncounted.
            #
            # Filters, all load-bearing:
            #   commandMode "task-notification" is the harness telling itself a
            #     background task finished — not something the user typed.
            #   empty prompts are queue bookkeeping with no text.
            #   <ide_opened_file> / <system-reminder> are context the editor injects
            #     through the same channel; they are not prompts either.
            #   a queued "/compact" or "/mcp" is a slash command, not a prompt — the
            #     same reason the user branch drops <command-name> records.
            # Deliberately NOT deduped against type:"user" records: the same text
            # can legitimately appear in both, seconds apart, because the user
            # really did press enter twice (verified: "yes od it" at :09 as a user
            # turn, again at :13 queued, then "yes do it" at :17 — three real sends,
            # not one event logged three times). source_uuid does not link the two.
            a = o.get("attachment") or {}
            if a.get("type") == "queued_command" and a.get("commandMode") == "prompt" and dt:
                txt = _first_text(a.get("prompt")).strip()
                if (txt and not txt.startswith(("<ide_", "<system-reminder"))
                        and not _SLASH_CMD.match(txt)):
                    r = _rec(agg, _buckets(dt)[0], "(user)")
                    r["user"] += 1
                    agg["totals"]["user"] += 1
            # Which MCP tools this session was OFFERED, so the Tools tab can set what
            # was used against what was loaded. Claude Code announces them by name as
            # they become available ("mcp__<server>__<tool>"), before any is called.
            elif a.get("type") == "deferred_tools_delta" and dt:
                inv = agg.setdefault("mcp_offered", {})
                for nm in (a.get("addedNames") or []) + (a.get("readdedNames") or []):
                    if isinstance(nm, str) and nm.startswith("mcp__"):
                        server, _, tool = nm[5:].partition("__")
                        e = inv.setdefault(server, {"d": _buckets(dt)[0], "tools": []})
                        if tool and tool not in e["tools"]:
                            e["tools"].append(tool)

    agg["project"] = project
    agg["editor"] = "Claude Code (CLI)"
    # A file is parsed in pieces as it grows, so what this call saw is only its newest
    # piece. Ranking on that labelled a session by the model it touched last (2M Opus
    # tokens read "Sonnet 5.5" after 15 records of it); rank on the whole file.
    whole = {}
    for key, rec in agg["records"].items():
        mdl = key.split("\t", 1)[1]
        if mdl != "(user)":
            whole[mdl] = whole.get(mdl, 0) + rec["in"] + rec["out"]
    if whole:
        agg["state"]["dom_model"] = max(whole, key=whole.get)
    st["seen_uuids"] = packed + "".join(fresh_uuids)
    st["resp"] = resp


# ===========================================================================
# CODEX CLI
# ===========================================================================
# substrings that mark the giant lines we can skip without json.loads
_CODEX_SKIP = ('"function_call_output"', '"custom_tool_call_output"', '"type": "reasoning"')
# response_item types that are a built-in tool call -> the tool name to count it as
_CODEX_BUILTIN_TOOLS = {"web_search_call": "web_search", "tool_search_call": "tool_search",
                        "image_generation_call": "image_generation"}


def _codex_effort_context(agg, payload, model, replayed=False):
    """Retain only explicit turn metadata, never current config/thread settings."""
    key = _effort_identity(payload.get("turn_id"))
    context_model = model if isinstance(payload.get("model"), str) and payload["model"] else None
    level = None if replayed else _effort_level(payload.get("effort"), _CODEX_EFFORTS)
    st = agg["state"]
    st["codex_effort_context"] = [key, context_model, level]
    contexts = st.setdefault("codex_effort_contexts", {})
    if key:
        contexts.pop(key, None)
        contexts[key] = [context_model, level]
        while len(contexts) > 32:
            del contexts[next(iter(contexts))]


def _codex_effort_usage(agg, dt, model, payload):
    st = agg["state"]
    identity = None
    if payload is None:
        context = st.get("codex_effort_context")
        candidate = context[1:] if context else None
    else:
        turn = _effort_identity(payload.get("turn_id"))
        candidate = st.get("codex_effort_contexts", {}).get(turn)
        response = payload.get("response_id")
        identity = response if _effort_identity(response) else None
    level = candidate[1] if candidate and candidate[0] == model else None
    _reasoning_effort_observe(agg, dt, model, level, "codex_effort_responses",
                             identity, limit=32)


def _codex_usage(agg, dt, u, model, effort_payload=None):
    """Count one model response's usage. `u` is a token_count's last_token_usage or
    a token_usage_record's usage — the two share a shape."""
    inp = int(u.get("input_tokens", 0) or 0)
    cached = int(u.get("cached_input_tokens", 0) or 0)
    # Newer builds split out the part of input_tokens that WROTE the prompt cache.
    # It is a subset of input_tokens and disjoint from cached_input_tokens (checked
    # over 35,736 events: in >= cached + written, always), and OpenAI bills it at
    # 1.25x input on GPT-5.6/GPT-6 — so it moves out of "in" into the cache-write
    # fields, where _cost() prices it at the write rate.
    written = int(u.get("cache_write_input_tokens", 0) or 0)
    out = int(u.get("output_tokens", 0) or 0)
    reason = int(u.get("reasoning_output_tokens", 0) or 0)
    if not (dt and (inp or out)):
        return
    written = min(written, max(0, inp - cached))
    fresh = max(0, inp - cached - written)
    r = _rec(agg, _buckets(dt)[0], model or "Unknown")
    # store non-cached input in "in", cached in "cr", cache writes in "cc"/"cc5"
    r["in"] += fresh
    r["cr"] += cached
    r["cc"] += written; r["cc5"] += written
    r["out"] += out
    r["reason"] += reason
    # one billed model call — what OpenAI's usage page calls a request.
    # Not "asst": that counts visible replies, and one reply can take many calls.
    r["req"] += 1
    _bump_time(agg, dt, inp + out, 0)
    r["active"] += _active_gap(agg["_active_last"], dt)
    agg["_active_last"] = dt.isoformat()
    # Codex reports the FULL context it sent as input_tokens (cached or not),
    # which is exactly the per-request context size — bucket it the same way as
    # Claude Code so the finding is cross-tool.
    date0 = _buckets(dt)[0]
    b = ("0-50k" if inp < 50_000 else "50-150k" if inp < 150_000
         else "150-400k" if inp < 400_000 else "400k+")
    ce = agg["ctx"].setdefault(f"{date0}\t{b}", {"tok": 0, "n": 0})
    ce["tok"] += inp + out; ce["n"] += 1
    T = agg["totals"]
    _turn_usage(agg, model, fresh, out, cached, written, 0)
    # the first request's full input: the fixed cost of opening a session (not for
    # a fork, whose first request carries its parent's whole history)
    if (agg.get("open_ctx") is None and not agg.get("subagent")
            and "replay_last" not in agg["state"] and model != "codex-auto-review"):
        agg["open_ctx"] = inp
    T["in"] += fresh; T["cr"] += cached
    T["cc"] += written; T["cc5"] += written
    T["out"] += out; T["reason"] += reason; T["req"] += 1
    _codex_effort_usage(agg, dt, model or "Unknown", effort_payload)


_EXEC_CMD = re.compile(r"""\bcmd\s*:\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|`([^`]*)`)""")
_PATCH_FILE = re.compile(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", re.M)


def _codex_edit_event(agg, changes):
    """An applied patch, however it was applied (apply_patch, or from inside exec):
    the one place every Codex build records each file an edit touched."""
    first = not agg["state"].get("edit_events")
    agg["state"]["edit_events"] = True
    files = list(changes)[:16] if isinstance(changes, dict) else []
    ed = (agg["state"].get("turn") or {}).get("ed")
    for f in files or [None]:
        if first and f and ed is not None:
            # the rollout's first patch was already counted from its apply_patch call,
            # under the name the patch text used (usually relative): re-key, don't recount
            n = f.replace("\\", "/")
            same = f if f in ed else next((k for k in ed if n.endswith("/" + k.replace("\\", "/"))), None)
            if same is not None:
                ed[f] = ed.pop(same)
                continue
        _turn_tool(agg, "apply_patch", file=f)


def _codex_turn_tool(agg, nm, pl):
    """Feed one Codex tool call to the activity classifier."""
    kind = _tool_kind(nm)
    if kind == "edit":
        if agg["state"].get("edit_events"):
            return               # this rollout logs each applied patch; counted there
        # apply_patch names its files inside the patch text; one call can touch several
        body = pl.get("input") if isinstance(pl.get("input"), str) else str(pl.get("arguments") or "")
        for f in _PATCH_FILE.findall(body)[:16] or [None]:
            _turn_tool(agg, nm, file=f.strip() if f else None)
        return
    cmd = None
    if kind == "shell" and isinstance(pl.get("input"), str):
        # Codex Desktop's `exec` tool takes a JS program that calls
        # tools.exec_command({cmd: "..."}); pull the commands out of it.
        cmd = " ; ".join("".join(g).replace('\\"', '"').replace("\\'", "'")
                         for g in _EXEC_CMD.findall(pl["input"])[:8]) or None
    elif kind == "shell":
        try:
            args = json.loads(pl.get("arguments") or "{}")
        except (ValueError, TypeError):
            args = {}
        if isinstance(args, dict):
            c = args.get("cmd") or args.get("command")
            if isinstance(c, list):          # ["bash", "-lc", "<script>"]
                c = c[-1] if c else None
            cmd = c if isinstance(c, str) else None
    _turn_tool(agg, nm, cmd=cmd)

_CODEX_COMPACTION_TIMING_VERSION = 2


def _codex_timing_state(agg):
    return agg["state"].setdefault("compaction_timing", {
        "version": _CODEX_COMPACTION_TIMING_VERSION,
        "requests": {}, "checkpoints": {}, "completions": {}})


def _codex_timing_id(value):
    return value if isinstance(value, str) and 0 < len(value) <= 200 else None


def _codex_timing_put(bucket, key, value):
    previous = bucket.get(key)
    # Conflicting identities/timestamps are ambiguous, never last-write-wins.
    if previous is not None and previous != value:
        bucket[key] = {"ambiguous": True}
    else:
        bucket[key] = value
    while len(bucket) > 32:
        del bucket[next(iter(bucket))]


def _codex_timing_request(agg, payload):
    rid, tid, thread = (_codex_timing_id(payload.get(k)) for k in ("response_id", "turn_id", "thread_id"))
    if not all((rid, tid, thread)) or (agg.get("_session_id") and thread != agg["_session_id"]):
        return
    state = _codex_timing_state(agg)
    _codex_timing_put(state["requests"], rid, {"turn_id": tid, "thread_id": thread})
    invalid = state["requests"][rid].get("ambiguous")
    for meta in state["checkpoints"].values():
        if meta.get("response_id") == rid and (invalid or
                (meta.get("turn_id"), meta.get("thread_id")) != (tid, thread)):
            meta["ambiguous"] = True
            invalid = True
    if invalid:
        event = agg.get("compactions", {}).get("response:" + rid)
        if event: event["duration_ms"] = None
        _codex_apply_timings(agg)


def _codex_timing_checkpoint(agg, payload, key, new):
    rid = _codex_timing_id(payload.get("compaction_response_id"))
    state = _codex_timing_state(agg)
    if not new and key not in state["checkpoints"]:
        # An old checkpoint's identity/ambiguity may have left the bounded maps.
        # Keep its previous result; replaying only one candidate cannot rebind it.
        _codex_apply_timings(agg)
        return
    request = state["requests"].get(rid)
    if not request or request.get("ambiguous"):
        _codex_apply_timings(agg)
        return  # No explicit top-level request -> turn -> thread identity chain.
    _codex_timing_put(state["checkpoints"], key, {
        **request, "response_id": rid,
        "timestamp_ms": round(_from_iso(agg["compactions"][key]["timestamp"]).timestamp() * 1000)})
    _codex_apply_timings(agg)


def _codex_timing_completion(agg, payload):
    item = payload.get("item")
    if (payload.get("type") != "item_completed" or not isinstance(item, dict)
            or item.get("type") not in ("ContextCompaction", "contextCompaction")):
        return
    iid, tid, thread = (_codex_timing_id(v) for v in (item.get("id"), payload.get("turn_id"), payload.get("thread_id")))
    start, end = payload.get("started_at_ms"), payload.get("completed_at_ms")
    if (not all((iid, tid, thread)) or (agg.get("_session_id") and thread != agg["_session_id"])
            or type(start) is not int or type(end) is not int
            or not 0 <= start <= end <= 253402300799999):
        return
    state = _codex_timing_state(agg)
    previous = state["completions"].get(iid)
    _codex_timing_put(state["completions"], iid, {
        "turn_id": tid, "thread_id": thread, "start": start, "end": end})
    if state["completions"][iid].get("ambiguous"):
        # Preserve the conflict on affected checkpoints before the item itself
        # leaves bounded state. Never let eviction turn it into a unique match.
        for key, event in agg.get("compactions", {}).items():
            at = round(_from_iso(event["timestamp"]).timestamp() * 1000)
            if start <= at <= end or (previous and not previous.get("ambiguous")
                    and previous["start"] <= at <= previous["end"]):
                event["duration_ms"] = None
                if key in state["checkpoints"]: state["checkpoints"][key]["ambiguous"] = True
    _codex_apply_timings(agg)


def _codex_apply_timings(agg):
    """Enrich existing checkpoints only through an unambiguous identity/time match.

    Duration is the explicit lifecycle end-start, never the distance between log
    lines. One completion must cover exactly one checkpoint in the same turn and
    thread, and that checkpoint must have exactly one covering completion.
    """
    state = _codex_timing_state(agg)
    checkpoints, completions = state["checkpoints"], state["completions"]
    # Include unlinked/older checkpoints when checking uniqueness. Evicting
    # bounded correlation state must never turn an ambiguous interval into one
    # apparently unique candidate.
    events = agg.get("compactions", {})
    times = {key: round(_from_iso(e["timestamp"]).timestamp() * 1000)
             for key, e in events.items()}
    unique = set()
    for iid, completion in completions.items():
        if completion.get("ambiguous"): continue
        covered = [key for key, at in times.items() if completion["start"] <= at <= completion["end"]]
        if len(covered) == 1:
            unique.add(iid)
        elif len(covered) > 1:
            for key in covered:
                events[key]["duration_ms"] = None
                if key in checkpoints: checkpoints[key]["ambiguous"] = True
    matches = {}
    for key, checkpoint in checkpoints.items():
        matches[key] = [iid for iid, completion in completions.items()
            if iid in unique and not checkpoint.get("ambiguous") and not completion.get("ambiguous")
            and checkpoint["thread_id"] == completion["thread_id"]
            and checkpoint["turn_id"] == completion["turn_id"]
            and completion["start"] <= checkpoint["timestamp_ms"] <= completion["end"]]
    for key, candidates in matches.items():
        event = agg.get("compactions", {}).get(key)
        if event is None: continue
        # Re-evaluate late/interleaved records; ambiguity clears earlier enrichment.
        event["duration_ms"] = None
        if len(candidates) > 1:
            checkpoints[key]["ambiguous"] = True
        if len(candidates) != 1: continue
        iid = candidates[0]
        if sum(iid in values for values in matches.values()) != 1: continue
        completion = completions[iid]
        event["duration_ms"] = completion["end"] - completion["start"]


def _codex_compaction(agg, payload, dt):
    """Persist a checkpoint without importing its copied history or usage snapshot."""
    key = None
    for field, prefix in (("compaction_response_id", "response:"), ("window_id", "window:")):
        value = payload.get(field)
        if isinstance(value, str) and value:
            key = prefix + value
            break
    key = key or "at:" + dt.astimezone(timezone.utc).isoformat()
    # Trigger/context aren't recorded by CompactedItem. Explicit lifecycle timing
    # can enrich duration, but copied history and usage snapshots cannot.
    events = agg.setdefault("compactions", {})
    new = key not in events
    events.setdefault(key, {
        "id": key, "timestamp": dt.isoformat(), "date": _buckets(dt)[0],
        "trigger": "unknown", "before": None, "after": None, "duration_ms": None,
    })
    _codex_timing_checkpoint(agg, payload, key, new)


def parse_codex(agg, lines):
    agg["account_metadata_version"] = A.METADATA_VERSION
    agg["parent_metadata_version"] = 1
    _reasoning_effort_init(agg)
    cur_model = agg["state"].get("cur_model")
    project = agg["project"]
    for line in lines:
        if not line.strip():
            continue
        # A completed checkpoint can contain copied tool outputs/reasoning. Read
        # it for metadata, retaining the existing skip behavior for every other
        # record (even one containing a nested "compacted" string).
        skip = any(s in line for s in _CODEX_SKIP)
        if skip and '"compacted"' not in line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if skip and (not isinstance(o, dict) or o.get("type") != "compacted"):
            continue
        t = o.get("type")
        pl = o.get("payload") or {}
        if t == "compacted" and not isinstance(pl, dict):
            continue
        pt = pl.get("type")
        dt = _from_iso(o.get("timestamp", "")) if o.get("timestamp") else None

        # A forked thread — `/fork`, and every subagent, which Codex forks from its
        # parent — opens by rewriting the parent's whole history into its own file,
        # all stamped within ~0.2s of its session_meta: every token_count, reply,
        # prompt and tool call the parent already logged, including its spawn
        # markers. Counting it bills the parent twice (one fork replayed 73 usage
        # events, 10.9M tokens, matching its parent's running total exactly). So
        # everything in that burst is skipped except turn_context, which carries
        # the model the fork inherits. The burst ends at the first gap over 1s:
        # real work resumed 3.5-4.8s after session_meta in every fork seen, so a
        # fixed 5s cutoff would also clip the fork's own first turn.
        st = agg["state"]
        context_replayed = False
        if st.get("replay_last") and dt:
            last = _from_iso(st["replay_last"])
            if last and (dt - last).total_seconds() <= 1.0:
                # A checkpoint formerly skipped with its large copied history
                # must not change the existing usage parser's replay clock.
                if not skip:
                    st["replay_last"] = o["timestamp"]
                if t != "turn_context":
                    continue
                context_replayed = True
            else:
                if not skip:
                    st["replay_last"] = None

        A.observe_codex(agg, t, pl, o.get("timestamp"))
        if t == "session_meta":
            if pl.get("forked_from_id") and "replay_last" not in st and o.get("timestamp"):
                st["replay_last"] = o["timestamp"]
            cwd = pl.get("cwd")
            if cwd:
                project = _leaf(cwd) or cwd
                agg["cwd"] = cwd
            if pl.get("originator"):
                agg["entry"] = pl["originator"]
            if pl.get("cli_version"):
                agg["cliver"] = pl["cli_version"]
            # A spawned subagent's OWN rollout file self-identifies — no cross-file
            # correlation needed, unlike Claude Code's isSidechain records which live
            # inside the parent's log.
            # full thread id — the filename is truncated to 8 chars in the session
            # dict, but the editor-state lookup needs the whole UUID
            agg["_session_id"] = pl.get("id") or pl.get("session_id") or agg.get("_session_id")
            source_meta = pl.get("source")
            sub_source = source_meta.get("subagent") if isinstance(source_meta, dict) else None
            spawn = sub_source.get("thread_spawn") if isinstance(sub_source, dict) else None
            if pl.get("thread_source") == "subagent" or isinstance(spawn, dict):
                agg["subagent"] = True
                # A control/spawn parent is distinct from forked_from_id, which
                # only identifies copied history and is not a delegation link.
                parents = [pl.get("parent_thread_id")]
                agg.pop("_parent_session_id", None)
                if isinstance(spawn, dict):
                    parents.append(spawn.get("parent_thread_id"))
                ids = {v.lower() for v in parents if isinstance(v, str) and re.fullmatch(
                    r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", v)}
                if len(ids) == 1:
                    parent = next(iter(ids))
                    if parent != str(agg.get("_session_id") or "").lower():
                        agg["_parent_session_id"] = parent
                # Stashed, not titled yet: many subagent transcripts carry no
                # UserMessage of their own (the task was handed to them at spawn
                # time, not as an in-band turn), so this is a last-resort label —
                # applied in _finalize_session only if nothing better ever showed up.
                agg["_agent_path"] = pl.get("agent_path")
            g = pl.get("git")
            if isinstance(g, dict) and g.get("branch"):
                agg["branch"] = g["branch"]
        elif t == "turn_context":
            m = pl.get("model")
            if m:
                cur_model = normalize_codex(m)
            _codex_effort_context(agg, pl, cur_model, context_replayed)
            cwd = pl.get("cwd")
            if cwd:
                project = _leaf(cwd) or cwd
                agg["cwd"] = cwd
        elif t == "compacted" and dt:
            # Only the durable checkpoint counts. Lifecycle item completions and
            # context_compacted notices can describe the same event; ignore twins.
            # This sits AFTER the fork replay guard so inherited events stay out.
            _codex_compaction(agg, pl, dt)
        elif t == "token_usage_record":
            _codex_timing_request(agg, pl)
            # Newer Codex builds (seen from 2026-09) log one of these per model
            # response. They are the better usage source: token_count below logs
            # zeros for a compaction request and nothing for a response cut off
            # mid-turn, and re-emits old numbers. So once a rollout has shown one,
            # usage comes from these alone — each is written BEFORE its token_count
            # twin, so the handover can't count a response twice.
            agg["state"]["usage_records"] = True
            _codex_usage(agg, dt, pl.get("usage") or {}, cur_model, pl)
        elif t == "event_msg":
            _codex_timing_completion(agg, pl)
            if pt == "patch_apply_end":
                _codex_edit_event(agg, pl.get("changes"))
            if pt == "token_count":
                info = pl.get("info") or {}
                last = info.get("last_token_usage") or {}
                tot = info.get("total_token_usage") or {}
                # A new billed response always moves its thread's running total, so
                # an event whose (total, last) pair was already seen is a re-emission
                # — Codex repeats the previous token_count when a turn starts (one
                # 78K-token request was logged 3x, hours apart). Checked against the
                # last 32 events, not just the previous one: two Codex processes on
                # one thread interleave two running totals in the same file, so a
                # re-emission can land a few events after its original. 776 of them
                # double-counted 116M tokens. Lists, not tuples: they round-trip
                # through the JSON cache.
                sig = [tot.get("input_tokens"), tot.get("output_tokens"),
                       last.get("input_tokens"), last.get("output_tokens")]
                recent = agg["state"].setdefault("recent_tc", [])
                if (not agg["state"].get("usage_records")
                        and (last.get("input_tokens") or last.get("output_tokens"))
                        and not (tot and sig in recent)):
                    recent.append(sig)
                    del recent[:-32]
                    _codex_usage(agg, dt, last, cur_model)
            elif pt == "agent_message":
                model = cur_model or "Unknown"
                # codex-auto-review is not a model you talked to — it's Codex's own
                # auto-approval reviewer, re-assessing the real session's transcript
                # once per action. Its turns get tokens/cost like any other record
                # (below), but must not inflate the human-facing prompt/message counts.
                if dt and model != "codex-auto-review":
                    _rec(agg, _buckets(dt)[0], model)["asst"] += 1
                    agg["totals"]["asst"] += 1
                    _bump_time(agg, dt, 0, 1)   # heatmap msgs — else Codex never shows there
            elif pt == "user_message":
                if dt and cur_model != "codex-auto-review":
                    _rec(agg, _buckets(dt)[0], "(user)")["user"] += 1
                    agg["totals"]["user"] += 1
                    _set_title(agg, pl.get("message") or _first_text(pl.get("content")), "prompt")
                    _turn_open(agg, dt, pl.get("message") or _first_text(pl.get("content")))
            elif pt == "item_completed":
                # Recent Codex CLI builds (0.151.x alpha) stopped emitting the flat
                # agent_message/user_message payloads above. Every turn's user text,
                # assistant text, tool activity and reasoning now arrives as ONE
                # item_completed event wrapping an `item` whose OWN `type` names the
                # real kind (UserMessage, AgentMessage, Reasoning, CommandExecution,
                # FileChange, SubAgentActivity, ...). Without this branch every session
                # written by the new format silently has 0 prompts and 0 messages —
                # tokens/cost/tools stay correct because those come from the separate
                # token_count and response_item events, which this format still emits
                # unchanged.
                item = pl.get("item") or {}
                it = item.get("type")
                if it == "UserMessage" and dt and cur_model != "codex-auto-review":
                    _rec(agg, _buckets(dt)[0], "(user)")["user"] += 1
                    agg["totals"]["user"] += 1
                    _set_title(agg, _first_text(item.get("content")), "prompt")
                    _turn_open(agg, dt, _first_text(item.get("content")))
                elif it == "AgentMessage" and dt:
                    model = cur_model or "Unknown"
                    if model != "codex-auto-review":
                        _rec(agg, _buckets(dt)[0], model)["asst"] += 1
                        agg["totals"]["asst"] += 1
                        _bump_time(agg, dt, 0, 1)
                elif it == "FileChange":
                    _codex_edit_event(agg, item.get("changes"))
                elif it == "SubAgentActivity" and item.get("kind") == "started":
                    # Counted on the PARENT's own file — a spawn marker, not a token
                    # or message event — so this session's own "delegated to a
                    # subagent" count is known without reading any other file.
                    agg["_spawned"] = agg.get("_spawned", 0) + 1
                    _turn_tool(agg, "Agent")
        elif t == "response_item" and dt:
            if pt in _CODEX_BUILTIN_TOOLS:
                # The model's own built-in tools are response items of their own
                # type, not function calls — and never an event_msg, which is where
                # this used to look: web search alone was 298 calls never counted.
                date = _buckets(dt)[0]
                _tool(agg, date, _CODEX_BUILTIN_TOOLS[pt])
                _rec(agg, date, cur_model or "Unknown")["tools"] += 1
                _turn_tool(agg, _CODEX_BUILTIN_TOOLS[pt])
            elif pt in ("function_call", "custom_tool_call"):
                date = _buckets(dt)[0]
                nm = pl.get("name") or ("function" if pt == "function_call" else "custom_tool")
                # Codex does not prefix MCP tools the way Claude Code does — it keeps
                # the bare tool name and puts the server in `namespace` ("mcp__azure").
                # Normalise to mcp__<server>__<tool> so MCP usage is attributable and
                # comparable across tools; without this an MCP tool is indistinguishable
                # from a built-in and every Codex server looks unused.
                ns = pl.get("namespace") or ""
                if ns.startswith("mcp__"):
                    nm = f"{ns}__{nm}"
                _tool(agg, date, nm)
                _rec(agg, date, cur_model or "Unknown")["tools"] += 1
                _codex_turn_tool(agg, nm, pl)

    _codex_timing_state(agg)
    agg["state"]["cur_model"] = cur_model
    agg["project"] = project
    agg["editor"] = "Codex (CLI)"


# ===========================================================================
# GITHUB COPILOT (VS Code / Insiders / Cursor chat sessions)
# ===========================================================================
def _copilot_text_len(response):
    """Approximate the assistant response length in characters."""
    total = 0
    if isinstance(response, list):
        for part in response:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                v = part.get("value")
                if isinstance(v, str):
                    total += len(v)
                elif isinstance(v, dict) and isinstance(v.get("value"), str):
                    total += len(v["value"])
                c = part.get("content")
                if isinstance(c, dict) and isinstance(c.get("value"), str):
                    total += len(c["value"])
    return total


def _copilot_apply_request(agg, r, fallback_ts=None):
    """Aggregate a single Copilot request record. Shared by the .json (whole-object)
    and .jsonl (mutation-log) parsers. Returns the normalized model name."""
    ts = r.get("timestamp") or fallback_ts
    dt = _from_ms(ts) if ts else None
    if not dt:
        return None
    details = (r.get("result") or {}).get("details") or ""
    model = normalize_copilot(r.get("modelId"), details)
    # billing indicator from "... • 1x" (older) or "... • 1.8 credits" (current)
    mult = 0.0
    mm = re.search(r"([0-9.]+)\s*(?:x\b|credits?\b)", details)
    if mm:
        try:
            mult = float(mm.group(1))
        except Exception:
            mult = 0.0
    msg = r.get("message") or {}
    in_chars = len(msg.get("text", "")) if isinstance(msg, dict) else 0
    if isinstance(msg, dict) and msg.get("text"):
        _set_title(agg, msg["text"], "prompt")
    out_chars = _copilot_text_len(r.get("response"))
    # Copilot logs the real per-request token counts (promptTokens/completionTokens),
    # patched in once the request finishes — prefer those over the char/4 guess,
    # which is all that's available for a request still mid-stream.
    prompt_tok, completion_tok = r.get("promptTokens"), r.get("completionTokens")
    exact = (isinstance(prompt_tok, (int, float)) and not isinstance(prompt_tok, bool)
             and isinstance(completion_tok, (int, float)) and not isinstance(completion_tok, bool))
    if exact:
        est_in, est_out = int(prompt_tok), int(completion_tok)
    else:
        est_in = in_chars // 4
        est_out = out_chars // 4
    meta = (r.get("result") or {}).get("metadata") or {}
    ntools = 0
    date = _buckets(dt)[0]
    for round_ in (meta.get("toolCallRounds") or []):
        for tc in (round_.get("toolCalls") or []):
            _tool(agg, date, tc.get("name") or "tool")
            ntools += 1
    rec = _rec(agg, date, model)
    rec["in"] += est_in; rec["out"] += est_out
    rec["req"] += 1; rec["user"] += 1; rec["asst"] += 1
    rec["prem"] += mult; rec["tools"] += ntools
    _bump_time(agg, dt, est_in + est_out, 1)
    gap = _active_gap(agg["_active_last"], dt)
    rec["active"] += gap
    if exact:
        counts = rec.setdefault("exact", {})
        for field, value in (("in", est_in), ("out", est_out), ("req", 1), ("user", 1),
                             ("asst", 1), ("prem", mult), ("tools", ntools), ("active", gap)):
            counts[field] = counts.get(field, 0) + value
    agg["_active_last"] = dt.isoformat()
    T = agg["totals"]
    T["in"] += est_in; T["out"] += est_out
    T["req"] += 1; T["user"] += 1; T["asst"] += 1
    T["prem"] += mult
    return model


def parse_copilot(agg, obj):
    """Older Copilot format: one whole-JSON document (rewritten each save)."""
    if obj.get("customTitle"):
        _set_title(agg, obj["customTitle"], "custom")
    mr = {}
    for r in (obj.get("requests") or []):
        m = _copilot_apply_request(agg, r, obj.get("lastMessageDate"))
        if m:
            mr[m] = mr.get(m, 0) + 1
    if mr:
        agg["state"]["dom_model"] = max(mr, key=mr.get)


def parse_copilot_jsonl(agg, lines):
    """Newer Copilot format: append-only mutation log. A request starts as a
    near-empty stub — modelId "copilot/auto" if Auto mode picked it, no result,
    no token counts — and Copilot patches in the real resolved model, the actual
    promptTokens/completionTokens, tool calls and credits only once the turn
    finishes: a `result` patch at its index is what "finished" means. Until then
    a long, tool-heavy turn can span several of the dashboard's own 20s refresh
    cycles, still streaming. Finalizing on first sight (the old approach) froze
    every request at whatever partial snapshot existed on ITS first refresh —
    "Auto" mode never resolved to a real model name, tool calls made after that
    moment went uncounted, and every token/credit figure was a rough char-count
    guess. So: a request newly seen this read is buffered (not marked seen) and
    carried in agg["state"] across as many incremental reads as it takes, with
    every later patch to its index merged in; it's only finalized — counted and
    marked seen — once a `result` patch actually lands for it."""
    seen = set(agg["state"].get("seen_req") or [])
    req_order = agg["state"].get("req_order") or []   # array index -> requestId; requests[] only ever grows
    pending = agg["state"].get("copilot_pending") or {}   # requestId -> draft, still incomplete
    touched = set()
    for line in lines:
        if '"requests"' not in line and '"customTitle"' not in line:   # cheap skip
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        kind, k = o.get("kind"), o.get("k")
        if k == ["customTitle"]:            # session renamed from the chat UI
            _set_title(agg, o.get("v"), "custom")
        elif kind == 0:                                   # initial full snapshot
            v = o.get("v")
            if isinstance(v, dict) and v.get("customTitle"):
                _set_title(agg, v["customTitle"], "custom")
            reqs = v.get("requests") if isinstance(v, dict) else None
            if isinstance(reqs, list):
                for idx, r in enumerate(reqs):
                    rid = r.get("requestId") if isinstance(r, dict) else None
                    if not rid:
                        continue
                    if idx >= len(req_order):
                        req_order.extend([None] * (idx + 1 - len(req_order)))
                    req_order[idx] = rid
                    if rid not in seen and rid not in pending:
                        pending[rid] = dict(r)
                        touched.add(rid)
        elif kind == 2 and k == ["requests"]:            # new request(s) appended
            for r in (o.get("v") or []):
                if not isinstance(r, dict):
                    continue
                rid = r.get("requestId")
                if not rid:
                    continue
                req_order.append(rid)
                if rid not in seen and rid not in pending:
                    pending[rid] = dict(r)
                    touched.add(rid)
        elif isinstance(k, list) and len(k) >= 2 and k[0] == "requests" and isinstance(k[1], int):
            idx = k[1]
            rid = req_order[idx] if 0 <= idx < len(req_order) else None
            draft = pending.get(rid) if rid else None
            if draft is not None:
                if len(k) == 2:                          # whole request object replaced
                    if kind == 1 and isinstance(o.get("v"), dict):
                        draft.update(o["v"])
                else:                                      # a single field patched
                    field = k[2]
                    if kind == 2 and isinstance(draft.get(field), list):
                        draft[field] = draft[field] + (o.get("v") or [])
                    else:
                        draft[field] = o.get("v")
                touched.add(rid)
    # Request order is chronological; set iteration made active gaps depend on
    # hash order and differ between a full reparse and incremental reads.
    for rid in req_order:
        if rid not in touched:
            continue
        draft = pending.get(rid)
        if draft is not None and draft.get("result") is not None:
            _copilot_apply_request(agg, draft)
            seen.add(rid)
            del pending[rid]
    agg["state"]["seen_req"] = list(seen)
    agg["state"]["req_order"] = req_order
    agg["state"]["copilot_pending"] = pending


# Copilot SDK / Agent Host. Usage events are normally ephemeral; VS Code saves
# whole-turn model totals separately in agentSessionData/<host-id>/session.db.
# Read only those allowlisted tables, joining explicit SDK and user-event ids.
_SDK_HOSTS = {"at": 0, "roots": (), "map": {}}
_SDK_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


def _sdk_number(value):
    return value if type(value) in (int, float) and 0 <= value < float("inf") else None


def _sdk_stat(path):
    try:
        st = os.stat(path)
        return [st.st_size, st.st_mtime_ns]
    except FileNotFoundError:
        return None


def _copilot_sdk_hosts():
    """Index only canonical host databases, never tool-output/peer-chat copies."""
    roots = tuple(COPILOT_ROOTS)
    if roots == _SDK_HOSTS["roots"] and time.monotonic() - _SDK_HOSTS["at"] < 5:
        return _SDK_HOSTS["map"]
    from contextlib import closing
    import sqlite3
    found = {}
    for root in roots:
        for db in glob.glob(os.path.join(root, "agentSessionData", "*", "session.db")):
            if not _SDK_UUID.fullmatch(_leaf(os.path.dirname(db))):
                continue
            try:
                with closing(_open_ro_sqlite(db)) as con:
                    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {"session_metadata", "turns", "turn_usage"} <= tables:
                        continue
                    row = con.execute("SELECT value FROM session_metadata WHERE key='defaultChatProviderData'").fetchone()
                    data = json.loads(row[0]) if row else {}
                    sid = data.get("sdkSessionId")
                    if isinstance(sid, str) and sid:
                        editor = EDITOR_LABEL.get(_leaf(root), _leaf(root))
                        found.setdefault(sid, []).append((db, editor))
            except (OSError, sqlite3.Error, ValueError, TypeError) as e:
                sys.stderr.write(f"[copilot-sdk] {db}: {type(e).__name__}: {e}\n")
                # A temporarily locked/unreadable index must not erase a known
                # dependency and silently replace its previously recovered usage.
                for sid, old in _SDK_HOSTS["map"].items():
                    for item in old:
                        if item[0] == db:
                            found.setdefault(sid, []).append(item)
    _SDK_HOSTS.update(at=time.monotonic(), roots=roots, map=found)
    return found


def _sdk_model(raw):
    # Cloudflare/custom endpoint namespaces are not a model's display identity.
    return _normalize_opencode(raw.split("/")[-1], "") if isinstance(raw, str) else "Unknown"


def _sdk_tokens(data):
    """SDK input includes cached tokens; reasoning is a subset of output."""
    inp, out = _sdk_number(data.get("inputTokens")), _sdk_number(data.get("outputTokens"))
    if inp is None or out is None:
        return None
    cr = _sdk_number(data.get("cacheReadTokens", data.get("cachedTokens", 0)))
    cw = _sdk_number(data.get("cacheWriteTokens", 0))
    reason = _sdk_number(data.get("reasoningTokens", 0))
    if cr is None or cw is None or reason is None or cr + cw > inp or reason > out:
        return None
    if any(n != int(n) for n in (inp, out, cr, cw, reason)):
        return None
    return {"in": int(inp - cr - cw), "out": int(out), "cr": int(cr),
            "cc": int(cw), "cc5": int(cw), "cc1": 0, "reason": int(reason)}


def _update_copilot_sdk(previous, path):
    sid = _leaf(os.path.dirname(path))
    hosts = _copilot_sdk_hosts().get(sid, [])
    sig = [_sdk_stat(path)] + [[db, _sdk_stat(db), _sdk_stat(db + "-wal")] for db, _ in hosts]
    if previous and previous.get("_sig") == sig and previous.get("copilot_sdk", {}).get("version") == 3:
        return previous
    lines, offset = _read_new_bytes(path, 0)
    fresh = _blank_agg("copilot", path)
    # Full parsing is deliberate: a rewritten/WAL-only usage row can correct a
    # completed turn without appending an SDK event. Stage everything atomically.
    fresh["size"], fresh["mtime"] = sig[0][0], os.path.getmtime(path)
    fresh["offset"] = offset
    if previous and previous.get("title"):
        _set_title(fresh, previous["title"], "ai")
    parse_copilot_sdk(fresh, lines, hosts, previous)
    fresh["_sig"] = sig
    _finalize_session(fresh, "copilot", path)
    fresh["sessions"][0]["id"] = sid[:8]
    fresh["sessions"][0]["_sid"] = sid
    fresh["sessions"][0]["reason"] = fresh["totals"]["reason"]
    fresh["sessions"][0]["copilot_sdk"] = copy.deepcopy(fresh["copilot_sdk"])
    return fresh


def _sdk_subagents(events, started):
    """Recorded children are inclusive usage breakdowns, never extra aggregates."""
    children, receipts, calls, compaction_starts = {}, {}, {}, {}

    def text(value, limit=160):
        return value[:limit] if isinstance(value, str) and value and all(c.isprintable() for c in value) else None

    for e in events:
        dt = _from_iso(e.get("timestamp", ""))
        if dt is None or (started and dt < started):
            continue
        d, aid = e["data"], text(e.get("agentId"), 512)
        if e.get("type") == "subagent.started" and aid and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", aid):
            children.setdefault(aid, {"id": aid, "title": text(d.get("agentDisplayName")) or
                text(d.get("agentName")) or "Copilot subagent", "model": _sdk_model(d.get("model")),
                "start": dt.isoformat(), "end": dt.isoformat(), "status": "started",
                "records": {}, "compactions": [], "activity_dates": [], "usage_status": "unavailable"})
    for e in events:
        aid, d, kind = e.get("agentId"), e["data"], e.get("type")
        child = children.get(aid) if isinstance(aid, str) else None
        dt = _from_iso(e.get("timestamp", ""))
        if child is None or dt is None or dt.isoformat() < child["start"]:
            continue
        child["end"] = max(child["end"], dt.isoformat())
        date = _buckets(dt)[0]
        if date not in child["activity_dates"]: child["activity_dates"].append(date)
        if kind in ("subagent.completed", "subagent.failed"):
            child["status"] = "completed" if kind.endswith("completed") else "failed"
        if kind in ("subagent.configured", "session.model_change"):
            raw = d.get("newModel") if kind == "session.model_change" else d.get("model")
            if isinstance(raw, str): child["model"] = _sdk_model(raw)
        if kind == "assistant.message" and isinstance(d.get("apiCallId"), str):
            calls.setdefault(aid, set()).add(d["apiCallId"])
        if kind in ("session.usage_record", "assistant.usage"):
            usage = d.get("usage") if kind == "session.usage_record" else d
            if not isinstance(usage, dict) or _sdk_tokens(usage) is None:
                continue  # the accounting parser validates persisted receipts
            if usage.get("interactionType") == "conversation-compaction":
                continue
            accounting = usage.get("accounting") or {}
            key = usage.get("apiCallId") or accounting.get("usageId") or e["id"]
            bucket = receipts.setdefault(aid, {})
            old = bucket.get(key)
            if old is None or kind == "session.usage_record":
                bucket[key] = (old[0] if old else dt, usage)
        if kind == "session.compaction_start":
            compaction_starts[aid] = dt
        elif kind == "session.compaction_complete":
            began = compaction_starts.pop(aid, None)
            if d.get("success") is True:
                duration = (dt - began).total_seconds() * 1000 if began else None
                child["compactions"].append({"id": e["id"], "timestamp": dt.isoformat(),
                    "date": _buckets(dt)[0], "duration_ms": duration if duration is not None and duration >= 0 else None})
            usage = d.get("compactionTokensUsed")
            if isinstance(usage, dict) and _sdk_tokens(usage) is not None:
                receipts.setdefault(aid, {})["compaction:" + e["id"]] = (dt, usage)
    for aid, child in children.items():
        observed = set()
        conversation = False
        for identity, (dt, usage) in receipts.get(aid, {}).items():
            conversation |= not str(identity).startswith("compaction:")
            mdl = _sdk_model(usage.get("model"))
            row = child["records"].setdefault(_buckets(dt)[0] + "\t" + mdl, {})
            for key, value in _sdk_tokens(usage).items():
                row[key] = row.get(key, 0) + value
            if usage.get("apiCallId"): observed.add(usage["apiCallId"])
        if child["records"]:
            child["usage_status"] = "recorded" if conversation and calls.get(aid, set()) <= observed else "partial"
    return list(children.values())


def parse_copilot_sdk(agg, lines, hosts=(), previous=None):
    from contextlib import closing
    sid = _leaf(os.path.dirname(agg["path"]))
    events, seen = [], set()
    for line in lines:
        if not line.strip():
            continue
        e = json.loads(line)  # malformed complete records fail transactionally
        if not isinstance(e, dict) or not isinstance(e.get("data"), dict):
            raise ValueError("Invalid Copilot SDK event")
        eid = e.get("id")
        if not isinstance(eid, str) or not eid or eid in seen:
            continue
        seen.add(eid)
        events.append(e)
    info = {"version": 3, "usage_status": "unavailable", "usage_sources": [],
            "errors": 0, "aborts": 0, "missing_turns": 0, "limitations": [], "recorded_fields": []}
    agg["copilot_sdk"] = info
    agg["compactions"] = {}
    agg["editor"], agg["entry"] = "Copilot SDK", "copilot-agent"
    segments, message_segments, turn_segments, segment, current_model = {}, {}, {}, None, "Unknown"
    origin_owners = {}
    calls, tool_ids, reply_ids, spawned = set(), set(), set(), set()
    call_segments, active_segment = {}, None
    usage_events, pending_usage, shutdowns, compaction_starts, started = [], {}, [], {}, None
    for e in events:
        t, d = e.get("type"), e["data"]
        dt = _from_iso(e.get("timestamp", ""))
        if dt is None:
            continue
        if started and dt < started:
            continue  # a fork's copied pre-session history is not new usage
        child = bool(e.get("agentId") or d.get("parentToolCallId"))
        if t == "session.start":
            if d.get("sessionId") != sid:
                raise ValueError("SDK session identity does not match its directory")
            context = d.get("context") or {}
            agg["cwd"] = context.get("cwd") if isinstance(context.get("cwd"), str) else None
            agg["project"] = _leaf(context.get("cwd")) or "(no folder)"
            agg["cliver"] = d.get("copilotVersion")
            current_model = _sdk_model(d.get("selectedModel"))
            started = dt
            agg["branch"] = context.get("branch")
        elif t == "session.model_change" and not child:
            current_model = _sdk_model(d.get("newModel"))
        elif t == "session.title_changed" and not child:
            _set_title(agg, d.get("title"), "custom")
        elif t == "session.context_changed" and not child:
            if isinstance(d.get("cwd"), str): agg["cwd"] = d["cwd"]
            agg["project"] = _leaf(d.get("cwd")) or agg["project"]
            agg["branch"] = d.get("branch") or agg.get("branch")
        elif t == "user.message" and not child:
            content = d.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            if d.get("messageId") and d["messageId"] in message_segments:
                continue
            segment = {"id": e["id"], "dt": dt, "model": current_model,
                       "dates": set(), "calls": set(), "call_events": [], "tool_events": [],
                       "messages": [], "message_usage": [], "usage": []}
            segments[e["id"]] = segment
            if d.get("messageId"): message_segments[d["messageId"]] = segment
            if d.get("turnId"):
                # SDK turn counters restart on resume. An explicitly reused
                # user turn id starts a new namespace; old counter values must
                # not steal later continuation calls from this new prompt.
                if d["turnId"] in turn_segments:
                    turn_segments.clear()
                    origin_owners.clear()
                turn_segments[d["turnId"]] = segment
            _set_title(agg, content)
        elif t == "assistant.message":
            # A user can queue another prompt while an earlier turn is working.
            # The originating native message id wins over stream adjacency.
            # New runtimes sometimes retain an earlier originatingMessageId.
            # A direct native user/assistant turnId match is stronger evidence.
            origin = d.get("originatingMessageId")
            native_turn = turn_segments.get(d.get("turnId"))
            target = native_turn or origin_owners.get(origin) or message_segments.get(origin, segment)
            if native_turn and origin and not child:
                # Subsequent API turns can keep this same stale origin too.
                # Re-anchor only from the explicit user/assistant turnId match,
                # never from prompt text or time proximity (queued prompts).
                origin_owners[origin] = native_turn
            message_id = (e.get("agentId"), d.get("messageId") or e["id"])
            if message_id in reply_ids:
                continue
            reply_ids.add(message_id)
            model = _sdk_model(d.get("model")) if d.get("model") else (target["model"] if target else current_model)
            if target:
                target["dates"].add(_buckets(dt)[0])
                target.setdefault("moments", []).append(dt)
                output = _sdk_number(d.get("outputTokens"))
                if output is not None and output == int(output):
                    target["message_usage"].append((dt, model, int(output)))
                call = d.get("apiCallId")
                if isinstance(call, str) and call and (e.get("agentId"), call) not in calls:
                    calls.add((e.get("agentId"), call))
                    target["calls"].add((e.get("agentId"), call))
                    target["call_events"].append((dt, model))
                    call_segments[(e.get("agentId"), call)] = target
                    _rec(agg, _buckets(dt)[0], model)["req"] += 1
                if not child:
                    active_segment = target
                    target["model"] = model
                    if target is segment: current_model = model
                    target["messages"].append((dt, model, bool(str(d.get("content") or "").strip())))
            for tool in d.get("toolRequests") or []:
                key = tool.get("toolCallId") if isinstance(tool, dict) else None
                if key and key not in tool_ids:
                    tool_ids.add(key)
                    _tool(agg, _buckets(dt)[0], tool.get("name") or tool.get("toolName") or "tool")
                    _rec(agg, _buckets(dt)[0], model)["tools"] += 1
                    if target: target["tool_events"].append((dt, model))
        elif t == "tool.execution_start":
            key = d.get("toolCallId")
            if key and key not in tool_ids:
                tool_ids.add(key)
                _tool(agg, _buckets(dt)[0], d.get("toolName") or "tool")
                _rec(agg, _buckets(dt)[0], current_model)["tools"] += 1
                if segment: segment["tool_events"].append((dt, current_model))
        elif t in ("assistant.usage", "session.usage_record"):
            if t == "session.usage_record":
                d = d.get("usage")
                if not isinstance(d, dict) or _sdk_tokens(d) is None:
                    raise ValueError("Invalid persisted SDK usage receipt")
            if d.get("interactionType") == "conversation-compaction":
                continue  # the same request is recorded by compaction_complete
            accounting = d.get("accounting") or {}
            if not isinstance(accounting, dict):
                raise ValueError("Invalid SDK usage accounting identity")
            if accounting.get("sourceSessionId") not in (None, sid) and not child:
                continue  # a copied receipt does not belong to this session
            call = d.get("apiCallId")
            identity = call or accounting.get("usageId") or e["id"]
            scope = e.get("agentId") or accounting.get("sourceSessionId") or sid
            key = (scope, identity)
            entry = {"model": _sdk_model(d.get("model")), "data": d, "dt": dt,
                     "call": (e.get("agentId"), call) if call else None,
                     "owner": active_segment or segment,
                     "source": "usage_receipts" if t == "session.usage_record" else "usage_events"}
            # Receipts may precede their assistant message and may be replayed
            # under a new event id. One API call contributes once, including its
            # ephemeral assistant.usage twin. Prefer the durable receipt.
            old = pending_usage.get(key)
            if not old or entry["source"] == "usage_receipts" or old["source"] != "usage_receipts":
                if old and old["source"] == "usage_receipts":
                    entry["dt"], entry["owner"] = old["dt"], old["owner"]
                pending_usage[key] = entry
        elif t == "assistant.turn_start" and not child:
            active_segment = turn_segments.get(d.get("turnId"), segment)
        elif t == "assistant.turn_end" and not child:
            active_segment = None
        elif t == "subagent.started":
            spawned.add(e.get("agentId") or d.get("toolCallId") or e["id"])
        elif t == "session.error":
            info["errors"] += 1
        elif t == "abort":
            info["aborts"] += 1
        elif t == "session.shutdown" and not child:
            shutdowns.append((dt, d))
        elif t == "session.compaction_start":
            compaction_starts[e.get("agentId") or d.get("parentToolCallId")] = dt
        elif t == "session.compaction_complete":
            began = compaction_starts.pop(e.get("agentId") or d.get("parentToolCallId"), None)
            tokens = d.get("compactionTokensUsed")
            if isinstance(tokens, dict):
                entry = {"model": _sdk_model(tokens.get("model")), "data": tokens, "dt": dt,
                         "source": "usage_events", "compaction": True}
                (active_segment["usage"] if active_segment else usage_events).append(entry)
            if d.get("success") is True and not child:
                before = _sdk_number(d.get("preCompactionTokens"))
                after = _sdk_number(d.get("postCompactionTokens"))
                duration = (dt - began).total_seconds() * 1000 if began else None
                agg["compactions"][e["id"]] = {"id": e["id"], "timestamp": dt.isoformat(),
                    "date": _buckets(dt)[0], "trigger": d.get("trigger") if d.get("trigger") in
                    ("manual", "auto", "threshold", "context_limit_retry", "memory_pressure", "model_switch") else "unknown",
                    "before": before, "after": after, "duration_ms": duration if duration is not None and duration >= 0 else None}

    for entry in pending_usage.values():
        owner = call_segments.get(entry["call"], entry["owner"])
        if entry["call"] and entry["call"] not in calls:
            calls.add(entry["call"])
            _rec(agg, _buckets(entry["dt"])[0], entry["model"])["req"] += 1
            if owner:
                owner["calls"].add(entry["call"])
                owner["call_events"].append((entry["dt"], entry["model"]))
        (owner["usage"] if owner else usage_events).append(entry)

    # Retain already recovered, numeric-only turn snapshots if the auxiliary
    # database is later deleted. Never retain its prompts, tool output or config.
    stored = copy.deepcopy((previous or {}).get("state", {}).get("sdk_host_usage", {}))
    editors, recovered = set(), {}
    for db, editor in hosts:
        if not os.path.exists(db):
            continue
        with closing(_open_ro_sqlite(db)) as con:
            con.execute("BEGIN")  # consistent metadata + usage snapshot, including WAL
            meta = dict(con.execute("SELECT key,value FROM session_metadata WHERE key IN "
                "('defaultChatProviderData','customTitle','agentHost.git')"))
            provider = json.loads(meta.get("defaultChatProviderData", "{}"))
            if provider.get("sdkSessionId") != sid:
                continue
            editors.add(editor)
            if meta.get("customTitle"):
                _set_title(agg, meta["customTitle"], "ai")
            if meta.get("agentHost.git"):
                agg["branch"] = json.loads(meta["agentHost.git"]).get("branchName") or agg.get("branch")
            rows = con.execute("SELECT t.event_id,u.usage FROM turn_usage u JOIN turns t ON t.id=u.turn_id")
            for anchor, raw in rows:
                if anchor not in segments:
                    continue
                usage = json.loads(raw)
                m = usage.get("_meta") or {}
                mc = m.get("vscode.modelCall") or {}
                if mc.get("sdkSessionId") not in (None, sid):
                    continue
                totals = m.get("turnTokenTotals")
                complete = isinstance(totals, list) and bool(totals)
                # Top-level usage describes only the LAST call, not the whole
                # turn. It is complete only for an observed single-call turn.
                if totals is None:
                    totals = [usage]
                    complete = len(segments[anchor]["calls"]) == 1
                if not isinstance(totals, list):
                    raise ValueError("Invalid SDK turn token totals")
                clean = []
                for row in totals:
                    if not isinstance(row, dict) or _sdk_tokens(row) is None:
                        raise ValueError("Invalid SDK token counts")
                    clean.append({k: row[k] for k in ("model", "inputTokens", "outputTokens", "cachedTokens",
                        "cacheReadTokens", "cacheWriteTokens", "reasoningTokens") if k in row})
                candidate = {"totals": clean, "complete": complete,
                             "premium": _sdk_number(m.get("cost"))}
                def rank(snapshot):
                    return (snapshot["complete"], sum(row["inputTokens"] + row["outputTokens"] for row in snapshot["totals"]))
                if anchor not in recovered or rank(candidate) >= rank(recovered[anchor]):
                    recovered[anchor] = candidate
    stored.update(recovered)
    if len(editors) == 1:
        agg["editor"] = next(iter(editors))
    elif len(editors) > 1:
        info["limitations"].append("ambiguous_editor")
    elif previous and stored:
        agg["editor"] = previous.get("editor") or agg["editor"]
        agg["branch"] = agg.get("branch") or previous.get("branch")
    stored = {k: v for k, v in stored.items() if k in segments}
    agg["state"]["sdk_host_usage"] = stored
    known_fields = None

    def add_usage(dt, model, data, hourly=True):
        nonlocal known_fields
        tokens = _sdk_tokens(data)
        if tokens is None:
            return False
        date = _buckets(dt)[0]
        fields = {"in", "out"}
        if "cacheReadTokens" in data or "cachedTokens" in data: fields.add("cr")
        if "cacheWriteTokens" in data: fields.add("cc")
        if "reasoningTokens" in data: fields.add("reason")
        known_fields = fields if known_fields is None else known_fields & fields
        r = _rec(agg, date, model)
        exact = r.setdefault("exact", {})
        for key, n in tokens.items():
            r[key] += n
            exact[key] = exact.get(key, 0) + n
        premium = _sdk_number(data.get("cost"))
        if premium is not None:
            r["prem"] += premium
            exact["prem"] = exact.get("prem", 0) + premium
        if hourly:
            _bump_time(agg, dt, sum(tokens[k] for k in ("in", "out", "cr", "cc")), 0)
        return True

    stream_dates = {_buckets(ts)[0] for e in events
                    if (ts := _from_iso(e.get("timestamp", ""))) is not None and (not started or ts >= started)}
    timed_activity = []
    for anchor, seg in segments.items():
        dt, model = seg["dt"], seg["model"]
        activity = []
        user = _rec(agg, _buckets(dt)[0], model)
        user["user"] += 1
        _bump_time(agg, dt, 0, 0)
        activity.append((dt, model, {"user": 1}))
        timed_activity.append((dt, model, anchor))
        for ts, mdl, visible in seg["messages"]:
            if visible:
                _rec(agg, _buckets(ts)[0], mdl)["asst"] += 1
                _bump_time(agg, ts, 0, 1)
                activity.append((ts, mdl, {"asst": 1}))
                timed_activity.append((ts, mdl, anchor))
        saved = stored.get(anchor)
        covered = False
        entries = [entry for entry in seg["usage"] if _sdk_tokens(entry["data"]) is not None]
        dated_entries = [entry for entry in entries if entry.get("call") or entry.get("compaction")]
        recorded_calls = {entry.get("call") for entry in entries if entry.get("call")}
        if recorded_calls:
            entries = dated_entries  # anonymous live twins cannot be added safely
        receipts_complete = bool(seg["calls"]) and seg["calls"] <= recorded_calls
        # Host totals can include calls/subagents absent from the event stream.
        # Prefer dated per-call receipts only when they also cover that snapshot.
        if receipts_complete and saved:
            observed, expected = {}, {}
            for rows, dest in (([(e["model"], e["data"]) for e in dated_entries], observed),
                               ([(_sdk_model(r.get("model")), r) for r in saved["totals"]], expected)):
                for mdl, data in rows:
                    tokens = _sdk_tokens(data)
                    for field in ("in", "out", "cr", "cc"):
                        dest[(mdl, field)] = dest.get((mdl, field), 0) + tokens[field]
            receipts_complete = all(observed.get(key, 0) >= value for key, value in expected.items())
        saved_dated = (len(seg["dates"] | {_buckets(dt)[0]}) == 1
                       and (bool(seg["dates"]) or len(stream_dates) == 1))
        if receipts_complete:
            for entry in dated_entries:
                add_usage(entry["dt"], entry["model"], entry["data"])
                info["usage_sources"].append(entry["source"])
            covered = True
        elif saved and saved["totals"] and saved_dated:
            moments = seg.get("moments") or [dt]
            # An explicit DB/user-event join still recovers a turn whose reply
            # carries a stale origin id, but only if the whole stream is on one
            # local date. Never invent a day for an undated multi-day total.
            same_hour = len({(_buckets(ts)[0], ts.hour) for ts in moments}) == 1
            if not same_hour: info["limitations"].append("hourly_usage")
            for row in saved["totals"]:
                add_usage(moments[-1], _sdk_model(row.get("model")), row, hourly=same_hour)
            info["usage_sources"].append("vscode_turn_usage")
            covered = saved["complete"]
            if not saved["complete"]:
                info["missing_turns"] += 1
        elif seg["usage"]:
            for entry in entries:
                add_usage(entry["dt"], entry["model"], entry["data"])
            info["usage_sources"].extend(entry["source"] for entry in entries)
            # Compaction usage is not evidence of a covered conversation call.
            conversation = [entry for entry in entries if not entry.get("compaction")]
            covered = (seg["calls"] <= recorded_calls if recorded_calls else
                       len(conversation) >= max(1, len(seg["calls"])))
            if not covered: info["missing_turns"] += 1
        else:
            info["missing_turns"] += 1
            for ts, mdl, output in seg["message_usage"]:
                _rec(agg, _buckets(ts)[0], mdl)["out"] += output
                _bump_time(agg, ts, output, 0)
                known_fields = {"out"} if known_fields is None else known_fields & {"out"}
                info["usage_sources"].append("message_output")
            if saved:
                info["limitations"].append("undated_usage")
        if saved and saved.get("premium") is not None:
            _rec(agg, _buckets(dt)[0], model)["prem"] += saved["premium"]
            activity.append((dt, model, {"prem": saved["premium"]}))
        seg["exact_covered"] = covered
        if covered:
            activity += [(ts, mdl, {"req": 1}) for ts, mdl in seg["call_events"]]
            activity += [(ts, mdl, {"tools": 1}) for ts, mdl in seg["tool_events"]]
            for ts, mdl, counts in activity:
                row = _rec(agg, _buckets(ts)[0], mdl)
                if "exact" in row:
                    for key, n in counts.items():
                        row["exact"][key] = row["exact"].get(key, 0) + n
    for dt, model, anchor in sorted(timed_activity, key=lambda x: x[0]):
        gap = _active_gap(agg["_active_last"], dt)
        row = _rec(agg, _buckets(dt)[0], model)
        row["active"] += gap
        if segments[anchor]["exact_covered"] and "exact" in row:
            row["exact"]["active"] = row["exact"].get("active", 0) + gap
        agg["_active_last"] = dt.isoformat()
    for entry in usage_events:
        add_usage(entry["dt"], entry["model"], entry["data"])
        info["usage_sources"].append(entry["source"])
    # Shutdown snapshots are cumulative: never add them to turn/event usage.
    # Only a single usage day permits assigning a session total to a daily row.
    # Multi-day totals remain visible as supplemental metadata, never invented
    # per-day/per-hour usage.
    dates = {key.split("\t", 1)[0] for key in agg["records"]}
    if started:
        dates.add(_buckets(started)[0])
    if shutdowns:
        shut_dt, shut = shutdowns[-1]
        terminal = not agg.get("last_ts") or shut_dt.isoformat() >= agg["last_ts"]
        replaced, complete_models, shutdown_fields = set(), set(), []
        if _sdk_number(shut.get("sessionStartTime")) is not None:
            origin = _from_ms(shut["sessionStartTime"])
            if origin: dates.add(_buckets(origin)[0])
        metrics = shut.get("modelMetrics") or {}
        info["shutdown_totals"] = []
        for raw_model, metric in metrics.items():
            row = metric.get("usage") if isinstance(metric, dict) else None
            tokens = _sdk_tokens(row) if isinstance(row, dict) else None
            if tokens is None:
                continue
            mdl = _sdk_model(raw_model)
            info["shutdown_totals"].append(dict(model=mdl, **tokens))
            if len(dates) == 1 and terminal:
                r = _rec(agg, next(iter(dates)), mdl)
                # A smaller stale shutdown cannot retract later recorded turns.
                if sum(tokens[k] for k in ("in", "out", "cr", "cc")) >= sum(r[k] for k in ("in", "out", "cr", "cc")):
                    r.update(tokens)
                    r.setdefault("exact", {}).update(tokens)
                    fields = {"in", "out"}
                    if "cacheReadTokens" in row: fields.add("cr")
                    if "cacheWriteTokens" in row: fields.add("cc")
                    if "reasoningTokens" in row: fields.add("reason")
                    replaced.add(mdl)
                    shutdown_fields.append(fields)
                    req = metric.get("requests") or {}
                    n = _sdk_number(req.get("count"))
                    if n is not None and n == int(n):
                        if n >= r["req"]: complete_models.add(mdl)
                        r["req"] = max(r["req"], int(n))
                    premium = _sdk_number(req.get("cost"))
                    if premium is not None: r["prem"] = premium
                    info["usage_sources"].append("shutdown")
                    info["limitations"].append("hourly_usage")
        if shutdown_fields:
            fields = set.intersection(*shutdown_fields)
            models = {key.split("\t", 1)[1] for key in agg["records"]}
            known_fields = fields if models <= replaced or known_fields is None else known_fields & fields
        if len(dates) != 1 and info["shutdown_totals"]:
            info["limitations"].append("undated_usage")
        elif (terminal and complete_models and {s["model"] for s in segments.values()} <= complete_models):
            info["missing_turns"] = 0
            for row in agg["records"].values():
                if "exact" in row:
                    for key in ("asst", "user", "req", "tools", "active", "prem"):
                        row["exact"][key] = row[key]
        for key in ("totalPremiumRequests", "totalNanoAiu", "totalApiDurationMs"):
            value = _sdk_number(shut.get(key))
            if value is not None: info[key] = value
    info["usage_sources"] = sorted(set(info["usage_sources"]))
    info["recorded_fields"] = sorted(known_fields or [])
    info["limitations"] = sorted(set(info["limitations"]))
    info["usage_status"] = ("partial" if info["missing_turns"] or "undated_usage" in info["limitations"] else "recorded") if info["usage_sources"] else "unavailable"
    # Precision filtering retains exact observed activity from covered turns but
    # cannot turn absent token records into zero-token exact sessions.
    for row in agg["records"].values():
        for key in agg["totals"]:
            if key != "tools": agg["totals"][key] += row.get(key, 0)
    ranked = sorted(dict.fromkeys(key.split("\t", 1)[1] for key in agg["records"]),
        key=lambda m: -sum(r["in"] + r["out"] + r["cr"] + r["cc"] for k, r in agg["records"].items() if k.endswith("\t" + m)))
    agg["state"]["dom_model"] = ranked[0] if ranked else current_model
    agg["_spawned"] = len(spawned)
    info["agents"] = _sdk_subagents(events, started)


# ===========================================================================
# GEMINI CLI  (prompts only — no model, no tokens, no responses persisted)
# ===========================================================================
def parse_gemini(agg, path):
    agg["source"] = "gemini"
    agg["editor"] = "Gemini CLI"
    parts = path.split(os.sep)
    try:
        agg["project"] = parts[parts.index("tmp") + 1]
    except (ValueError, IndexError):
        agg["project"] = "gemini"
    # the log is a series of MongoDB-style {"$set":{"messages":[...]}} snapshots;
    # the snapshot with the most messages is the full conversation
    best = None
    try:
        with open(path) as f:
            for line in f:
                if '"messages"' not in line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                st = o.get("$set")
                if isinstance(st, dict) and isinstance(st.get("messages"), list):
                    if best is None or len(st["messages"]) > len(best):
                        best = st["messages"]
    except Exception:
        return
    if not best:
        return
    model = "Gemini"
    for m in best:
        if not isinstance(m, dict) or m.get("type") != "user":
            continue
        # skip the auto-injected session context preamble
        txt = ""
        c = m.get("content")
        if isinstance(c, list) and c and isinstance(c[0], dict):
            txt = c[0].get("text", "") or ""
        if txt.startswith("<session_context>"):
            continue
        dt = _from_iso(m.get("timestamp", "")) if m.get("timestamp") else None
        if not dt:
            continue
        date = _buckets(dt)[0]
        r = _rec(agg, date, model)
        r["asst"] += 1   # 1 prompt ≈ 1 model turn (Gemini logs no responses/tokens)
        r["user"] += 1
        _bump_time(agg, dt, 0, 1)
        r["active"] += _active_gap(agg["_active_last"], dt)
        agg["_active_last"] = dt.isoformat()
        agg["totals"]["asst"] += 1
        agg["totals"]["user"] += 1


# ==========================================================================
# ANTIGRAVITY  (bounded local SQLite conversation databases)
# ==========================================================================
def _antigravity_merge_state(previous, current):
    states = (previous, current)
    if all(value == "recorded" for value in states):
        return "recorded"
    if all(value == "unavailable" for value in states):
        return "unavailable"
    return "partial"


def parse_antigravity(agg, path):
    """Read one Antigravity SQLite conversation and fold safe facts into agg."""
    snapshot = AG.read_conversation(path, max_events=AG.MAX_EVENTS,
                                    include_private=True)
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("conversation_id"), str):
        raise AG.AntigravityReadError("conversation identity is unavailable")

    conversation_id = snapshot["conversation_id"]
    usage = snapshot.get("totals") if isinstance(snapshot.get("totals"), dict) else {}
    summary = {
        "usage_status": snapshot.get("usage_status", "unavailable"),
        "usage_availability": dict(snapshot.get("usage_availability") or {}),
        "model_status": snapshot.get("model_status", "unavailable"),
        "detail_status": snapshot.get("detail_status", "unavailable"),
        "record_availability": {},
        "response_out": 0,
        "undated_usage": {key: 0 for key in ("in", "out", "cr", "cc", "reason", "response_out", "req")},
        "unknown_model_usage": {key: 0 for key in ("in", "out", "cr", "cc", "reason", "response_out")},
        "subagent_requests": max(0, int(snapshot.get("subagent_requests", 0) or 0)),
    }
    agg["source"] = "antigravity"
    agg["_session_id"] = conversation_id
    agg["conversation_id"] = conversation_id
    agg["cwd"] = snapshot.get("cwd")
    agg["project"] = snapshot.get("project") or "(unknown)"
    agg["branch"] = snapshot.get("branch")
    agg["title"] = snapshot.get("title")
    agg["subagent"] = snapshot.get("subagent") is True
    agg["parent_id"] = snapshot.get("parent_id")
    agg["root_id"] = snapshot.get("root_id")
    agg["nesting_depth"] = snapshot.get("nesting_depth")
    agg["subagent_type"] = snapshot.get("subagent_type")
    agg["subagent_role"] = snapshot.get("subagent_role")
    agg["context"] = copy.deepcopy(snapshot.get("context"))
    agg["activity_dates"] = list(snapshot.get("activity_dates") or [])
    agg["compactions"] = {
        item["id"]: dict(item) for item in snapshot.get("compactions", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    agg["antigravity"] = summary
    _reasoning_effort_init(agg)

    totals = agg["totals"]
    rows = snapshot.get("records") if isinstance(snapshot.get("records"), list) else []
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = _canonicalize(row.get("model") if isinstance(row.get("model"), str) else "Unknown")
        if not model:
            model = "Unknown"
        effort = row.get("effort") if row.get("effort") in ("low", "medium", "high", "xhigh", "max") else None
        numeric = {key: max(0, int(row.get(key, 0) or 0))
                   for key in ("in", "out", "cr", "cc", "reason", "response_out")}
        req = max(0, int(row.get("req", 1) or 0))
        availability = row.get("usage_availability") if isinstance(row.get("usage_availability"), dict) else {}
        timestamp = row.get("timestamp")
        dt = _from_iso(timestamp) if isinstance(timestamp, str) else None
        date = _buckets(dt)[0] if dt else None
        if date:
            record = _rec(agg, date, model)
            for key in ("in", "out", "cr", "cc", "reason"):
                record[key] += numeric[key]
                totals[key] += numeric[key]
            record["req"] += req
            totals["req"] += req
            record_key = f"{date}\t{model}"
            bucket_state = summary["record_availability"].get(record_key)
            if bucket_state is None:
                summary["record_availability"][record_key] = {
                    field: availability.get(field, "unavailable")
                    for field in ("input_output", "cache_read", "cache_write", "reasoning", "response_output")
                }
            else:
                for field in bucket_state:
                    bucket_state[field] = _antigravity_merge_state(
                        bucket_state[field], availability.get(field, "unavailable"))
            _bump_time(agg, dt, numeric["in"] + numeric["out"] + numeric["cr"] + numeric["cc"], 0)
            record["active"] += _active_gap(agg.get("_active_last"), dt)
            agg["_active_last"] = dt.isoformat()
            _reasoning_effort_observe(agg, dt, model, effort,
                                      "antigravity_effort_responses", identity=row.get("key"))
        else:
            for key in ("in", "out", "cr", "cc", "reason", "response_out"):
                summary["undated_usage"][key] += numeric[key]
                if key in totals:
                    totals[key] += numeric[key]
            summary["undated_usage"]["req"] += req
            totals["req"] += req
        summary["response_out"] += numeric["response_out"]
        if model == "Unknown":
            for key in summary["unknown_model_usage"]:
                summary["unknown_model_usage"][key] += numeric[key]
        if date and model != "(user)":
            context = row.get("context") if isinstance(row.get("context"), dict) else {}
            estimate = context.get("estimated_tokens_used")
            if isinstance(estimate, int) and not isinstance(estimate, bool) and estimate >= 0:
                label = ("0-50k" if estimate < 50_000 else "50-150k" if estimate < 150_000
                         else "150-400k" if estimate < 400_000 else "400k+")
                ctx = agg["ctx"].setdefault(f"{date}\t{label}", {"tok": 0, "n": 0})
                ctx["tok"] += estimate
                ctx["n"] += req

    # Full safe activity buckets are produced during the bounded source scan,
    # independently of the capped timeline event list.
    activity = snapshot.get("activity_buckets") if isinstance(snapshot.get("activity_buckets"), dict) else {}
    for key, count in (activity.get("assistant") or {}).items():
        if not isinstance(key, str) or "\t" not in key or not isinstance(count, int):
            continue
        date, model_raw = key.split("\t", 1)
        model = _canonicalize(model_raw)
        record = _rec(agg, date, model)
        record["asst"] += count
        totals["asst"] += count
    for date, count in (activity.get("user") or {}).items():
        if not isinstance(date, str) or not isinstance(count, int):
            continue
        record = _rec(agg, date, "(user)")
        record["user"] += count
        totals["user"] += count
    for key, count in (activity.get("tools") or {}).items():
        if not isinstance(key, str) or key.count("\t") < 2 or not isinstance(count, int):
            continue
        date, model_raw, tool = key.split("\t", 2)
        model = _canonicalize(model_raw)
        record = _rec(agg, date, model)
        record["tools"] += count
        tool_key = f"{date}\t{tool}"
        agg["tools"][tool_key] = agg["tools"].get(tool_key, 0) + count
        totals["tools"] += count
    for hour_key, value in (activity.get("hourly") or {}).items():
        if (not isinstance(hour_key, str) or not isinstance(value, dict)
                or not isinstance(value.get("msgs"), int)):
            continue
        hour = agg["hourly"].setdefault(hour_key, {"tokens": 0, "msgs": 0})
        hour["msgs"] += value["msgs"]

    totals["asst"] = sum((activity.get("assistant") or {}).values())
    totals["user"] = sum((activity.get("user") or {}).values())
    totals["tools"] = sum((activity.get("tools") or {}).values())
    source_totals = snapshot.get("totals") if isinstance(snapshot.get("totals"), dict) else {}
    # Requests and usage sums include dated and undated rows. Reassign from the
    # reader's deduplicated snapshot to avoid message/event caps affecting totals.
    for key in ("in", "out", "cr", "cc", "reason", "req"):
        if isinstance(source_totals.get(key), int) and not isinstance(source_totals.get(key), bool):
            totals[key] = source_totals[key]
    if snapshot.get("first_activity"):
        agg["first_ts"] = snapshot["first_activity"]
    if snapshot.get("last_activity"):
        agg["last_ts"] = snapshot["last_activity"]

    session_fields = {
        "conversation_id": conversation_id,
        "_sid": conversation_id,
        "id": conversation_id[:8],
        "in": usage.get("in", 0),
        "out": usage.get("out", 0),
        "cr": usage.get("cr", 0),
        "cc": usage.get("cc", 0),
        "reason": usage.get("reason", 0),
        "req": usage.get("req", 0),
        "asst": snapshot.get("assistant_messages", 0),
        "user": snapshot.get("user_messages", 0),
        "tools": snapshot.get("tool_calls", 0),
        "response_out": summary["response_out"],
        "parent_id": snapshot.get("parent_id"),
        "root_id": snapshot.get("root_id"),
        "subagent": snapshot.get("subagent") is True,
        "subagent_type": snapshot.get("subagent_type"),
        "subagent_role": snapshot.get("subagent_role"),
        "nesting_depth": snapshot.get("nesting_depth"),
        "context": copy.deepcopy(snapshot.get("context")),
        "usage_status": summary["usage_status"],
        "usage_availability": dict(summary["usage_availability"]),
        "model_status": summary["model_status"],
        "detail_status": summary["detail_status"],
        "record_availability": copy.deepcopy(summary["record_availability"]),
        "user_messages": snapshot.get("user_messages", 0),
        "assistant_messages": snapshot.get("assistant_messages", 0),
        "tool_calls": snapshot.get("tool_calls", 0),
        "subagent_requests": summary["subagent_requests"],
        "activity_dates": list(snapshot.get("activity_dates") or []),
    }
    # _finalize_session creates the row after this parser returns. Retain the
    # source-only values privately so the update route can add them to that row.
    agg["_antigravity_session"] = session_fields


# ===========================================================================
# CURSOR  (native AI chat bubbles in a SQLite key-value store)
# ===========================================================================
def _normalize_cursor_model(raw):
    """Normalize a concrete Cursor model identifier for display."""
    if not isinstance(raw, str) or not raw.strip():
        return "Unknown"
    first = raw.split(",")[0].strip()          # multi-model sessions list them
    if not first or first.lower() == "default":
        return "Unknown"
    base = re.sub(r"-(?:high-)?(?:thinking|reasoning|max|fast)$", "", first.lower())
    m = re.match(r"claude-(\d+(?:\.\d+)?)-(opus|sonnet|haiku)$", base)
    if m:
        return f"Claude {m.group(2).capitalize()} {m.group(1)}"
    if base.startswith("composer"):
        n = base.split("-", 1)[1] if "-" in base else ""
        return ("Cursor Composer " + n).strip()
    canon = _canonicalize(base)
    return canon or first


def _cursor_ai_lines(con):
    """Cursor's own AI-code accounting: lines it suggested vs. lines you kept,
    per day, split by tab-completion and composer. No other tool records this."""
    out = {}
    import sqlite3
    max_rows = 100_000
    try:
        count = con.execute(
            "SELECT COUNT(*) FROM ItemTable WHERE key LIKE 'aiCodeTracking.dailyStats%'"
        ).fetchone()[0]
        if count > max_rows:
            raise ValueError("Cursor code-tracking summary exceeds bounded row budget")
        rows = con.execute(
            "SELECT key, CASE WHEN length(value) <= 2097152 THEN value ELSE NULL END, "
            "(length(value) > 2097152) FROM ItemTable "
            "WHERE key LIKE 'aiCodeTracking.dailyStats%' LIMIT ?", (max_rows,)
        )
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc).lower():
            return out
        raise
    for _k, v, oversized in rows:
        if oversized:
            raise ValueError("Cursor code-tracking summary is oversized")
        if v is None:
            continue
        try:
            o = json.loads(v)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            continue
        if not isinstance(o, dict):
            continue
        d = o.get("date")
        if not isinstance(d, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) or _from_iso(d) is None:
            continue
        fields = {}
        for source, target in (("tabSuggestedLines", "tab_suggested"),
                               ("tabAcceptedLines", "tab_accepted"),
                               ("composerSuggestedLines", "composer_suggested"),
                               ("composerAcceptedLines", "composer_accepted")):
            value = o.get(source)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**15:
                fields[target] = value
        if fields:
            out[d] = {name: fields.get(name, 0) for name in (
                "tab_suggested", "tab_accepted", "composer_suggested", "composer_accepted")}
    return out


def _open_ro_sqlite(db_path):
    """Open a tool's live SQLite store read-only, correctly, on any filesystem.

    Neither flag alone is safe:
      * `mode=ro` alone reads the -wal, so a tool that is RUNNING has its recent
        activity visible — but SQLite must create a -shm alongside the db, so it
        raises "attempt to write a readonly database" on read-only media.
      * `immutable=1` needs no -shm and works there — but it tells SQLite the file
        can never change, so it ignores the -wal entirely. While the tool is
        running its newest sessions are invisible, or the open fails outright.
    Prefer correctness, fall back to availability.
    """
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro&busy_timeout=5000", uri=True)
        # connect() is lazy — on read-only media it succeeds and only fails when a
        # query forces the -shm to be created. Probe before trusting it.
        con.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return con
    except sqlite3.Error:
        try:
            con.close()
        except Exception:
            pass
        return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def parse_cursor(agg, db_path):
    from contextlib import closing
    if __package__:
        from . import cursor_data as CD
    else:
        import cursor_data as CD
    with closing(_open_ro_sqlite(db_path)) as con:
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")  # one consistent snapshot across metadata/bubble queries
        snapshot = CD.read_store(db_path, max_scan_rows=CD.MAX_STORE_ROWS,
                                 strict=True, _connection=con)
        ai_lines = _cursor_ai_lines(con)
    snapshot, history = _cursor_retain_history(snapshot, agg.get("_cursor_history"), CD)
    agg["_cursor_history"] = history
    agg["cursor_schema"] = CD.CURSOR_SCHEMA
    _parse_cursor_store(agg, snapshot, ai_lines, CD)



def _cursor_retain_history(snapshot, previous, CD):
    """Keep numeric/message metadata when Cursor tombstones source rows.

    This private state contains the reader's allowlisted summaries only. Hash
    bubble/request/tool identities before persistence; no source content or raw
    join IDs is kept. Current rows replace their old summaries, never add twins.
    """
    previous = previous if isinstance(previous, dict) and previous.get("version") == 1 else {}
    bubbles = copy.deepcopy(previous.get("bubbles", {}))
    current_ids = set()

    def digest(kind, value):
        return hashlib.sha256(("cursor-history-v1\0" + kind + "\0" + value).encode()).hexdigest()

    for cid, rows in snapshot.get("bubbles", {}).items():
        bucket = bubbles.setdefault(cid, {})
        for row in rows:
            safe = copy.deepcopy(row)
            bubble_id = digest("bubble:" + cid, safe["bubble_id"])
            safe["bubble_id"] = bubble_id
            for field in ("request_id", "tool_call_id", "model_call_id"):
                if safe.get(field):
                    safe[field] = digest(field, safe[field])
            for tool in safe.get("extra_tools", []):
                if tool.get("tool_call_id"):
                    tool["tool_call_id"] = digest("tool_call_id", tool["tool_call_id"])
            older = bucket.get(bubble_id, {})
            # A later zero placeholder is not evidence that a known receipt was
            # free. Keep known counters until a real replacement receipt exists.
            if (not (safe.get("input_tokens") or safe.get("output_tokens"))
                    and (older.get("input_tokens") or older.get("output_tokens"))):
                for field in ("input_tokens", "output_tokens", "input_present", "output_present"):
                    safe[field] = older.get(field)
            for field in ("created_at", "model"):
                if not safe.get(field) and older.get(field):
                    safe[field] = older[field]
            bucket[bubble_id] = safe
            current_ids.add((cid, bubble_id))
    if sum(len(rows) for rows in bubbles.values()) > CD.MAX_STORE_ROWS:
        raise CD.CursorReadError("Cursor retained history exceeds the safe row limit; previous ledger retained")
    merged = dict(snapshot)
    for field in ("composers", "workspace", "parents"):
        merged[field] = {**copy.deepcopy(previous.get(field, {})), **snapshot.get(field, {})}
    merged["bubbles"] = {cid: list(rows.values()) for cid, rows in bubbles.items()}
    retained = {cid for cid, rows in bubbles.items()
                if any((cid, key) not in current_ids for key in rows)}
    merged["retained_sessions"] = retained
    history = {"version": 1, "bubbles": bubbles,
               **{field: copy.deepcopy(merged[field]) for field in ("composers", "workspace", "parents")}}
    return merged, history


def _cursor_status(total, recorded, complete=None):
    if not total or not recorded:
        return "unavailable"
    if complete is None:
        complete = recorded
    return "recorded" if recorded == total and complete == recorded else "partial"


def _cursor_combine_status(values):
    states = [value for value in values if value in ("recorded", "partial", "unavailable")]
    if not states:
        return "unavailable"
    return states[0] if all(value == states[0] for value in states) else "partial"


def _cursor_availability(usage_status):
    return {
        "input_output": usage_status,
        "cache_read": "unavailable",
        "cache_write": "unavailable",
        "reasoning": "unavailable",
        "response_output": "unavailable",
    }


def _parse_cursor_store(agg, snapshot, ai_lines, CD):
    agg["source"] = "cursor"
    agg["editor"] = "Cursor"
    agg["project"] = "Cursor"
    composers = snapshot.get("composers", {})
    bubbles = snapshot.get("bubbles", {})
    workspace = snapshot.get("workspace", {})
    session_data = {}
    active_last = {}
    quality_by_session = {}
    tool_seen = {}
    response_models_by_session = {}
    global_first = None
    global_last = None
    summary_usage_states = []
    summary_model_states = []

    def top(counter):
        return max(counter, key=lambda key: (counter[key], key)) if counter else None

    def new_session():
        return {
            "in": 0, "out": 0, "asst": 0, "user": 0, "tools": 0,
            "think": 0, "projects": {}, "days": {}, "active": 0.0,
            "start": None, "end": None, "assistant_total": 0,
            "usage_evidence": 0, "usage_complete": 0, "undated_usage": 0,
            "undated_counts": {"in": 0, "out": 0, "asst": 0},
            "response_models": {}, "requested_models": {}, "user_total": 0,
        }

    for cid in composers:
        session_data[cid] = new_session()

    # Sort by UTC before gap-capped active-time accounting. Source key order is
    # lexical and can be wrong when offsets differ or bubbles interleave.
    for cid, rows in bubbles.items():
        session = session_data.setdefault(cid, new_session())
        sorted_rows = sorted(rows, key=lambda row: (
            CD._event_time({"timestamp": row.get("created_at")}), row.get("bubble_id") or ""))
        for bubble in sorted_rows:
            typ = bubble.get("type")
            if typ not in (1, 2):
                continue
            dt = _from_iso(bubble.get("created_at")) if bubble.get("created_at") else None
            model_name = _normalize_cursor_model(bubble.get("model")) if typ == 2 else None
            requested_model = _normalize_cursor_model(bubble.get("requested_model")) if typ == 1 else None
            token_in, token_out = bubble.get("input_tokens"), bubble.get("output_tokens")
            positive_usage = bool(typ == 2 and ((token_in or 0) > 0 or (token_out or 0) > 0))

            if typ == 2:
                session["assistant_total"] += 1
                if model_name and model_name != "Unknown":
                    session["response_models"][model_name] = session["response_models"].get(model_name, 0) + 1
            else:
                session["user_total"] += 1
                if requested_model and requested_model != "Unknown":
                    session["requested_models"][requested_model] = session["requested_models"].get(requested_model, 0) + 1
            if positive_usage:
                session["usage_evidence"] += 1
                if bubble.get("input_present") and bubble.get("output_present"):
                    session["usage_complete"] += 1

            tool_specs = []
            if typ == 2:
                if bubble.get("tool"):
                    tool_specs.append((bubble["tool"], bubble.get("tool_call_id")))
                for extra in bubble.get("extra_tools", []):
                    tool_specs.append((extra.get("tool"), extra.get("tool_call_id")))
            accepted_tools = []
            seen = tool_seen.setdefault(cid, set())
            for index, (tool_name, tool_id) in enumerate(tool_specs):
                identity = ("id", tool_id) if tool_id else (
                    "bubble", bubble.get("bubble_id"), index, tool_name)
                if not tool_name or identity in seen:
                    continue
                seen.add(identity)
                accepted_tools.append(tool_name)

            for hint in bubble.get("project_hints", []):
                if isinstance(hint, str) and hint:
                    session["projects"][hint] = session["projects"].get(hint, 0) + 1
            if bubble.get("thinking_ms") is not None:
                session["think"] += bubble["thinking_ms"]

            # A date is required for dated ledger attribution. Keep source model
            # coverage, but do not invent a date when the bubble lacks one.
            if dt is None:
                if positive_usage:
                    session["undated_usage"] += 1
                    session["undated_counts"]["in"] += token_in or 0
                    session["undated_counts"]["out"] += token_out or 0
                    session["undated_counts"]["asst"] += 1
                continue

            date = _buckets(dt)[0]
            iso = dt.isoformat()
            event_time = CD._event_time({"timestamp": iso})
            if global_first is None or event_time < global_first[0]:
                global_first = (event_time, iso)
            if global_last is None or event_time > global_last[0]:
                global_last = (event_time, iso)
            if session["start"] is None or event_time < CD._event_time({"timestamp": session["start"]}):
                session["start"] = iso
            if session["end"] is None or event_time > CD._event_time({"timestamp": session["end"]}):
                session["end"] = iso
            session["tools"] += len(accepted_tools)
            day = session["days"].setdefault(date, {
                "in": 0, "out": 0, "cr": 0, "cc": 0, "asst": 0, "user": 0,
                "tools": 0, "active": 0.0,
            })
            gap = _active_gap(active_last.get(cid), dt)
            active_last[cid] = iso
            session["active"] += gap
            day["active"] += gap

            row = None
            if typ == 2:
                row_model = model_name or "Unknown"
                agg["_record_session"] = cid
                row = _rec(agg, date, row_model)
                row["asst"] += 1
                day["asst"] += 1
                if positive_usage:
                    in_value = token_in if bubble.get("input_present") else 0
                    out_value = token_out if bubble.get("output_present") else 0
                    row["in"] += in_value
                    row["out"] += out_value
                    day["in"] += in_value
                    day["out"] += out_value
                    session["in"] += in_value
                    session["out"] += out_value
                    agg["totals"]["in"] += in_value
                    agg["totals"]["out"] += out_value
                key = f"{date}\t{row_model}"
                stats = quality_by_session.setdefault((cid, key), {
                    "total": 0, "evidence": 0, "complete": 0,
                })
                stats["total"] += 1
                if positive_usage:
                    stats["evidence"] += 1
                    if bubble.get("input_present") and bubble.get("output_present"):
                        stats["complete"] += 1
                session["asst"] += 1
                agg["totals"]["asst"] += 1
            else:
                agg["_record_session"] = cid
                row = _rec(agg, date, "(user)")
                row["user"] += 1
                day["user"] += 1
                session["user"] += 1
                agg["totals"]["user"] += 1

            if typ == 2:
                for tool_name in accepted_tools:
                    _tool(agg, date, tool_name)
                    if row is not None:
                        row["tools"] += 1
                    day["tools"] += 1
            token_sum = ((token_in or 0) + (token_out or 0)) if positive_usage else 0
            _bump_time(agg, dt, token_sum, 1 if typ == 2 else 0)
            if row is not None:
                row["active"] += gap

    for cid in composers:
        session_data.setdefault(cid, new_session())

    # Prefer explicit workspaceStorage association; publish only its leaf. The
    # full cwd is retained in a private aggregate map for local matching.
    project_counts = {}
    private_cwds = {}
    for cid, session in session_data.items():
        meta = composers.get(cid, {})
        for hint in meta.get("project_hints", []):
            if isinstance(hint, str) and hint:
                session["projects"][hint] = session["projects"].get(hint, 0) + 1
        cwd = workspace.get(cid)
        project = _leaf(cwd) if cwd else None
        project = project or top(session["projects"]) or "Cursor"
        session["project"] = project
        project_counts[project] = project_counts.get(project, 0) + 1
        if cwd:
            private_cwds[cid] = cwd
    agg["_cursor_cwds"] = private_cwds
    agg["state"]["ai_lines"] = ai_lines
    agg["project"] = top(project_counts) or "Cursor"

    sessions = []
    for cid, session in session_data.items():
        meta = composers.get(cid, {})
        if session["start"] is None:
            session["start"] = meta.get("created") or meta.get("updated")
        if session["end"] is None:
            session["end"] = meta.get("updated") or meta.get("created")
        usage_status = _cursor_status(
            session["assistant_total"], session["usage_evidence"],
            session["usage_complete"] if not session["undated_usage"] else -1)
        if session["undated_usage"] and usage_status == "recorded":
            usage_status = "partial"
        response_model_status = _cursor_status(
            session["assistant_total"], sum(session["response_models"].values()))
        requested_model_status = _cursor_status(
            session["user_total"], sum(session["requested_models"].values()))
        if session["assistant_total"]:
            summary_usage_states.append(usage_status)
            summary_model_states.append(response_model_status)
        mode = {1: "chat", 2: "agent"}.get(meta.get("mode"), meta.get("mode"))
        parent_id = snapshot.get("parents", {}).get(cid)
        is_subagent = meta.get("subagent") is True or bool(parent_id)
        current_model = _normalize_cursor_model(meta.get("current_model")) if meta.get("current_model") else None
        if current_model == "Unknown":
            current_model = None

        records = agg.get("_session_records", {}).get(cid, {})
        row_quality = {}
        for key, row in records.items():
            if key.endswith("\t(user)"):
                continue
            stats = quality_by_session.get((cid, key), {"total": 0, "evidence": 0, "complete": 0})
            status = _cursor_status(stats["total"], stats["evidence"], stats["complete"])
            availability = _cursor_availability(status)
            row["usage_status"] = status
            row["usage_availability"] = availability
            row_quality[key] = availability
        response_models = sorted(session["response_models"],
                                 key=lambda name: (-session["response_models"][name], name))
        if session["assistant_total"] > sum(session["response_models"].values()):
            response_models.append("Unknown")
        requested_models = sorted(session["requested_models"],
                                  key=lambda name: (-session["requested_models"][name], name))
        response_models_by_session[cid] = response_models
        sessions.append({
            "_sid": cid, "_cursor_sid": cid, "id": cid[:8], "source": "cursor",
            "ide": IDE_FIXED["cursor"], "editor": "Cursor", "title": meta.get("title"),
            "project": session["project"], "model": top(session["response_models"]) or "Unknown",
            "models": response_models[:6], "nmodels": len(response_models),
            "current_model": current_model,
            "current_context_tokens": meta.get("context_tokens"),
            "current_context_percent": meta.get("context_percent"),
            "current_context_limit": meta.get("context_limit"),
            "current_context_asof": meta.get("context_asof"),
            "requested_models": requested_models[:6],
            "requested_model_status": requested_model_status,
            "response_model_status": response_model_status, "model_status": response_model_status,
            "start": session["start"], "end": session["end"],
            "in": session["in"], "out": session["out"], "cr": 0, "cc": 0,
            "cc5": 0, "cc1": 0, "reason": 0, "response_out": 0,
            "asst": session["asst"], "user": session["user"], "req": 0, "prem": 0.0,
            "tools": session["tools"], "side": 0, "days": session["days"],
            "active": round(session["active"], 1),
            "mode": ("max " + mode) if mode and meta.get("max_mode") else mode,
            "lines_add": meta.get("lines_add", 0), "lines_del": meta.get("lines_del", 0),
            "think_ms": session["think"], "subagents": meta.get("subagents", 0),
            "subagent": is_subagent,
            "subagent_type": meta.get("subagent_type") if is_subagent else None,
            "parent_id": parent_id,
            "archived_session": bool(meta.get("archived")),
            "retained_history": cid in snapshot.get("retained_sessions", set()),
            "undated_usage": dict(session["undated_counts"]),
            "usage_status": usage_status, "usage_availability": _cursor_availability(usage_status),
            "detail_status": "partial" if snapshot.get("detail_partial") else "recorded",
            "record_availability": row_quality,
        })
    agg["sessions"] = sessions
    _finish_store_records(agg)

    # _finish_store_records consumes _sid while building per-session records.
    # Restore Cursor's full native composerId for names and timeline lookup.
    for session in agg["sessions"]:
        session["_sid"] = session.pop("_cursor_sid", None) or session.get("_sid") or session.get("id")
        session["id"] = session["_sid"][:8]
        session["models"] = response_models_by_session.get(session["_sid"], session.get("models", []))[:6]
        session["nmodels"] = len(response_models_by_session.get(session["_sid"], session.get("models", [])))
        if not session.get("model") or session.get("model") == "Unknown":
            known = [model for model in session.get("models", []) if model != "Unknown"]
            if known:
                session["model"] = known[0]

    global_states = {}
    for session in agg["sessions"]:
        for key, fields in (session.get("record_availability") or {}).items():
            global_states.setdefault(key, []).append(fields.get("input_output", "unavailable"))
    global_quality = {}
    for key, states in global_states.items():
        status = _cursor_combine_status(states)
        availability = _cursor_availability(status)
        global_quality[key] = availability
        if key in agg["records"]:
            agg["records"][key]["usage_status"] = status
            agg["records"][key]["usage_availability"] = availability
    detail_status = "partial" if snapshot.get("detail_partial") else "recorded"
    summary_usage = _cursor_combine_status(summary_usage_states)
    agg["cursor"] = {
        "usage_status": summary_usage,
        "usage_availability": _cursor_availability(summary_usage),
        "model_status": _cursor_combine_status(summary_model_states),
        "detail_status": detail_status,
        "record_availability": global_quality,
    }
    agg["first_ts"] = global_first[1] if global_first else None
    agg["last_ts"] = global_last[1] if global_last else None


# ===========================================================================
# OPENCODE  (SST) — per-message JSON files; real token counts + model + provider
# ===========================================================================
def _from_ms_or_s(v):
    """Epoch ms (opencode's time.created) or seconds (Hermes' REAL timestamps) — accept both."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v > 1e12:        # milliseconds
        return _from_ms(v)
    if v > 1e9:         # seconds
        return _from_ms(v * 1000)
    return None


def _normalize_opencode(model_id, provider):
    """opencode modelID is the provider's raw id. Cloudflare Workers AI aliases use
    the @cf/<publisher>/<model> namespace; opencode's own providers expose a mix
    of bare ids. Map both onto readable display names."""
    if not model_id:
        return "Unknown"
    # Cloudflare IDs look like @cf/moonshotai/kimi-k2.7-code — the meaningful
    # part is the last path segment, which is also what bare opencode ids use.
    base = model_id.split("/")[-1]
    low = base.lower()

    # Kimi (Moonshot) — @cf/moonshotai/kimi-k2.7-code or kimi-k3
    m = re.match(r"^kimi-k([0-9.]+)(?:-code)?$", low)
    if m:
        suffix = " Code" if "code" in low else ""
        return f"Kimi K{m.group(1)}{suffix}"

    # Google Gemma via Cloudflare — @cf/google/gemma-4-26b-a4b-it
    m = re.match(r"^gemma-(\d+(?:\.\d+)?)-(\d+b)(?:-[a-z0-9\-]+)*$", low)
    if m:
        return f"Gemma {m.group(1)} {m.group(2).upper()}"

    # DeepSeek — deepseek-v4-flash[-free]
    m = re.match(r"^deepseek-v?([0-9.]+)-flash(-free)?$", low)
    if m:
        free = " Free" if m.group(2) else ""
        return f"DeepSeek V{m.group(1)} Flash{free}"

    # Qwen — qwen3.7-max
    m = re.match(r"^qwen(\d+(?:\.\d+)?)(?:-(max|plus|coder))?$", low)
    if m:
        suffix = " " + m.group(2).capitalize() if m.group(2) else ""
        return f"Qwen {m.group(1)}{suffix}"

    # Zhipu GLM via Cloudflare — @cf/zai-org/glm-5.2
    m = re.match(r"^glm-(\d+(?:\.\d+)?)$", low)
    if m:
        return f"GLM {m.group(1)}"

    # opencode-hosted aliases without a recognised family
    if low == "big-pickle":
        return "Big Pickle"
    if low == "north-mini-code" or low == "north-mini-code-free":
        return "North Mini Code"
    if low.startswith("x-preview"):
        return "X Preview"

    # Claude/GPT/o-series handled by the shared canonicalizer
    name = _canonicalize(model_id)
    if name and name != model_id:
        return name

    return base


def _opencode_project(session_dir, sid):
    """Best-effort project label from the session file's directory/title; opencode
    keeps it under storage/session/*/{sid}.json a couple levels up."""
    try:
        storage = os.path.dirname(os.path.dirname(session_dir))   # .../storage
        for sf in glob.glob(os.path.join(storage, "session", "*", sid + ".json")):
            with open(sf) as f:
                o = json.load(f)
            d = o.get("directory") or o.get("cwd") or ""
            if d:
                return _leaf(d) or d
            if o.get("title"):
                return str(o["title"])[:40]
            break
    except Exception as e:
        sys.stderr.write(f"[opencode:metadata] {session_dir}: {type(e).__name__}: {e}\n")
    return "opencode"


def parse_opencode(agg, session_dir, msg_files):
    agg["source"] = "opencode"
    agg["editor"] = "opencode"
    sid = os.path.basename(session_dir)
    agg["project"] = _opencode_project(session_dir, sid)
    model_tokens = {}
    agg["opencode_messages"] = []
    for mf in msg_files:
        with open(mf) as f:
            o = json.load(f)
        if o.get("role") != "assistant":
            continue
        t = o.get("tokens") or {}
        cache = t.get("cache") or {}
        inp = int(t.get("input", 0) or 0)
        out = int(t.get("output", 0) or 0)
        reason = int(t.get("reasoning", 0) or 0)
        cr = int(cache.get("read", 0) or 0)
        cw = int(cache.get("write", 0) or 0)
        model = _normalize_opencode(o.get("modelID"), o.get("providerID"))
        dt = _from_ms_or_s((o.get("time") or {}).get("created"))
        if not dt or not (inp or out or cr or cw):
            continue
        date = _buckets(dt)[0]
        # tool invocations ride along as typed parts on the message; the shape has
        # moved between opencode versions, so accept either spelling defensively
        ntools = 0
        tool_names = []
        for part in (o.get("parts") or o.get("content") or []):
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("tool", "tool-invocation", "tool_use", "tool-call"):
                name = (part.get("tool") or part.get("name")
                        or (part.get("toolInvocation") or {}).get("toolName") or "tool")
                tool_names.append(str(name))
                _tool(agg, date, str(name))
                ntools += 1
        r = _rec(agg, date, model)
        before = dict(r)
        r["tools"] += ntools
        r["in"] += inp; r["out"] += out; r["reason"] += reason
        r["cr"] += cr; r["cc"] += cw; r["cc5"] += cw  # untiered cache write -> 5m rate
        r["asst"] += 1
        _bump_time(agg, dt, inp + out + cr + cw, 1)
        r["active"] += _active_gap(agg["_active_last"], dt)
        agg["_active_last"] = dt.isoformat()
        mid = o.get("id") or os.path.splitext(_leaf(mf))[0]
        agg["opencode_messages"].append({"id": [sid, mid], "key": date + "\t" + model,
            "ts": dt.isoformat(), "tools": tool_names,
            "usage": {k: v - before.get(k, 0) for k, v in r.items()}})
        T = agg["totals"]
        T["in"] += inp; T["out"] += out; T["reason"] += reason
        T["cr"] += cr; T["cc"] += cw; T["cc5"] += cw; T["asst"] += 1
        model_tokens[model] = model_tokens.get(model, 0) + inp + out
    if model_tokens:
        agg["state"]["dom_model"] = max(model_tokens, key=model_tokens.get)


# ===========================================================================
# OPENCODE SQLite database (current opencode stores messages in opencode.db)
# ===========================================================================
def parse_opencode_db(agg, db_path):
    """Parse the current opencode SQLite database. One DB holds many sessions,
    messages and parts; we aggregate tokens per day/model and return a session
    list similar to Cursor's composer breakdown."""
    import sqlite3
    agg["source"] = "opencode"
    agg["editor"] = "opencode"

    def _loads(v):
        try:
            return json.loads(v) if v else None
        except Exception:
            return None

    sessions_meta = {}
    agg["opencode_ids"] = []
    sess = {}          # sid -> running totals
    model_tokens = {}
    # Per-session last-message timestamp for active-time gaps. Keep the original
    # V1 traversal; V2 rows use their persisted chronological/sequence order.
    active_last = {}
    con = _open_ro_sqlite(db_path)
    cur = con.cursor()
    try:
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        v2 = {"session_v2", "session_message"}.issubset(tables)
        legacy = {"session", "message", "part"}.issubset(tables)
        if not (v2 or legacy):
            raise sqlite3.OperationalError("unsupported opencode session schema")
        agg["opencode_schema"] = 2 if v2 else 1
        # ---- session metadata ---------------------------------------------
        session_tables = (["session"] if legacy else []) + (["session_v2"] if v2 else [])
        for table in session_tables:
            columns = {r[1] for r in cur.execute("PRAGMA table_info(" + table + ")")}
            fork = "fork_session_id" if "fork_session_id" in columns else "NULL"
            for row in cur.execute(
                    "SELECT id, directory, title, agent, model, version, "
                    "parent_id, time_created, time_updated, " + fork + " FROM " + table):
                sid, directory, title, agent, model_json, version, parent_id, tc, tu, fork_id = row
                model = _loads(model_json) or {}
                sessions_meta[sid] = {
                    "directory": directory or "",
                    "title": title or "",
                    "agent": agent or "",
                    "model_id": model.get("id"),
                    "provider_id": model.get("providerID"),
                    "variant": model.get("variant"),
                    "version": version or "",
                    "parent_id": parent_id,
                    "start": tc,
                    "end": tu,
                    "fork_id": fork_id,
                }

        # dominant project for aggregate-level project bucket
        proj_tally = {}
        for s in sessions_meta.values():
            p = _leaf(s["directory"]) or s["directory"] or "opencode"
            proj_tally[p] = proj_tally.get(p, 0) + 1
        agg["project"] = max(proj_tally, key=proj_tally.get) if proj_tally else "opencode"

        # ---- tool parts (batch) --------------------------------------------
        # tool rows in part have {"type":"tool", "tool":"<name>", ...}
        tools_by_msg = {}
        tool_rows = cur.execute("SELECT message_id, data FROM part WHERE json_extract(data,'$.type')='tool'") if legacy else []
        for mid, data in tool_rows:
            o = _loads(data)
            if not o:
                continue
            name = o.get("tool") or o.get("name") or "tool"
            tools_by_msg.setdefault(mid, []).append(str(name))

        # ---- messages -------------------------------------------------------
        queries = []
        if legacy:
            query = "SELECT id, session_id, time_created, data, NULL AS role, 0 AS v2, 0 AS seq FROM message"
            if v2:
                # A migrated V2 session is authoritative; its V1 copy stops
                # growing. Keep V1-only sessions, never add both full copies.
                query += (" WHERE session_id NOT IN (SELECT session_id FROM session_message "
                          "WHERE type IN ('user','assistant') AND session_id IN (SELECT id FROM session_v2))")
            queries.append(query)
        if v2:
            queries.append("SELECT id, session_id, time_created, data, type AS role, 1 AS v2, seq FROM session_message")
        query = " UNION ALL ".join(queries)
        if v2:
            query += " ORDER BY session_id, time_created, seq, id"
        for row in cur.execute(query):
            mid, sid, tc, data, stored_role, is_v2, seq = row
            o = _loads(data)
            if is_v2 and not isinstance(o, dict):
                raise ValueError("invalid opencode V2 message metadata")
            if not o:
                continue
            meta = sessions_meta.get(sid)
            # OpenCode's own stats excludes fork-copied messages by creation
            # time, even if the original parent has since been deleted.
            if is_v2 and meta and meta.get("fork_id") and tc < meta["start"]:
                continue
            role = stored_role if is_v2 else o.get("role")
            ts = (o.get("time") or {}).get("created") or tc
            dt = _from_ms_or_s(ts)
            if not dt:
                continue
            date, hour, _dow = _buckets(dt)
            agg["_record_session"] = sid
            # One summary per counted message, by id: a message OpenCode deletes
            # later (undo) keeps exactly what it added. See reconcile().
            dims = _session_dims(agg)

            if role == "user":
                r = _rec(agg, date, "(user)")
                r["user"] += 1
                agg["totals"]["user"] += 1
                _bump_time(agg, dt, 0, 0)
                # No session object touched here (only the assistant branch
                # below creates one) — but bumping active_last still means the
                # NEXT assistant reply's gap is measured from this prompt, so
                # "time waiting for/reading the reply" correctly lands on the
                # session once that branch runs.
                gap = _active_gap(active_last.get(sid), dt)
                r["active"] += gap
                active_last[sid] = dt.isoformat()
                dims.setdefault("msgs", {})[mid] = ["u", date, hour, "(user)", 0, 0, 0, 0, 0, 0.0, [], gap]
                # weak title from first user prompt of the session
                if meta and not meta.get("_weak_title_set"):
                    text = o.get("text", "") if is_v2 else ""
                    for part in (o.get("parts") or o.get("content") or []):
                        if isinstance(part, dict) and part.get("type") == "text":
                            text = part.get("text", "")
                            break
                    if text:
                        # kind="prompt" is the lowest title rank, so a real
                        # session title from the DB still wins over this.
                        _set_title(agg, text)
                        meta["_weak_title_set"] = True
                s = sess.setdefault(sid, _blank_opencode_session())
                if "end" not in s or dt.isoformat() > s["end"]:
                    s["end"] = dt.isoformat()
                s["user"] += 1
                continue

            if role != "assistant":
                continue

            t = o.get("tokens") or {}
            cache = t.get("cache") or {}
            inp = int(t.get("input", 0) or 0)
            out = int(t.get("output", 0) or 0)
            reason = int(t.get("reasoning", 0) or 0)
            cr = int(cache.get("read", 0) or 0)
            cw = int(cache.get("write", 0) or 0)
            cost = float(o.get("cost") or 0.0)
            if not (inp or out or cr or cw):
                # token-less assistant bookkeeping rows (flow control, etc.)
                continue
            agg["opencode_ids"].append([sid, mid])
            model_ref = o.get("model") if is_v2 else None
            model_ref = model_ref if isinstance(model_ref, dict) else {}
            provider = model_ref.get("providerID") or o.get("providerID") or (meta.get("provider_id") if meta else None)
            model = _normalize_opencode(model_ref.get("id") or o.get("modelID"), provider)

            model_tokens[model] = model_tokens.get(model, 0) + inp + out + cr + cw

            # V2 embeds tools in assistant content instead of the V1 part table.
            ntools = 0
            names = [str(part.get("name") or "tool") for part in o.get("content", [])
                     if isinstance(part, dict) and part.get("type") == "tool"] if is_v2 else tools_by_msg.get(mid, [])
            for name in names:
                _tool(agg, date, name)
                ntools += 1

            r = _rec(agg, date, model)
            r["tools"] += ntools
            r["in"] += inp; r["out"] += out; r["reason"] += reason
            r["cr"] += cr; r["cc"] += cw; r["cc5"] += cw
            r["asst"] += 1
            r["cost"] += cost

            _bump_time(agg, dt, inp + out + cr + cw, 1)
            gap = _active_gap(active_last.get(sid), dt)
            active_last[sid] = dt.isoformat()
            r["active"] += gap
            T = agg["totals"]
            T["in"] += inp; T["out"] += out; T["reason"] += reason
            T["cr"] += cr; T["cc"] += cw; T["cc5"] += cw; T["asst"] += 1
            T["cost"] = T.get("cost", 0.0) + cost
            if meta and meta.get("parent_id"):
                T["side"] = T.get("side", 0) + inp + out + cr + cw

            s = sess.setdefault(sid, _blank_opencode_session())
            iso = dt.isoformat()
            if "start" not in s or iso < s["start"]:
                s["start"] = iso
            if "end" not in s or iso > s["end"]:
                s["end"] = iso
            s["in"] += inp; s["out"] += out; s["reason"] += reason
            s["cr"] += cr; s["cc"] += cw; s["cc5"] += cw
            s["asst"] += 1
            s["tools"] += ntools
            s["active"] += gap
            s["cost"] += cost
            dims.setdefault("msgs", {})[mid] = ["a", date, hour, model, inp, out, reason, cr, cw,
                                                cost, list(names), gap]

        # ---- build per-session summaries ----------------------------------
        out = []
        for sid, s in sess.items():
            meta = sessions_meta.get(sid, {})
            model_id = meta.get("model_id")
            provider_id = meta.get("provider_id")
            model = _normalize_opencode(model_id, provider_id)
            directory = meta.get("directory") or "opencode"
            title = meta.get("title") or agg.get("title")
            agent = meta.get("agent")
            start_dt = _from_ms_or_s(meta.get("start"))
            end_dt = _from_ms_or_s(meta.get("end"))
            out.append({
                "_sid": sid, "id": (sid or "")[:8],
                "source": "opencode", "ide": IDE_FIXED["opencode"],
                "editor": "opencode",
                "title": title,
                "project": _leaf(directory) or directory or "opencode",
                "model": model,
                "start": start_dt.isoformat() if start_dt else s.get("start"),
                "end": end_dt.isoformat() if end_dt else s.get("end"),
                "in": s["in"], "out": s["out"], "cr": s["cr"], "cc": s["cc"],
                "cc5": s["cc5"], "cc1": s["cc1"],
                "asst": s["asst"], "user": s["user"], "req": 0,
                "prem": 0.0, "tools": s["tools"], "side": 0,
                "cost": s["cost"], "active": round(s.get("active", 0), 1),
                "cliver": meta.get("version"),
                "mode": agent,
            })
        agg["sessions"] = sorted(out, key=lambda x: x.get("end") or "", reverse=True)
        _finish_store_records(agg)
        if model_tokens:
            agg["state"]["dom_model"] = max(model_tokens, key=model_tokens.get)
    finally:
        con.close()


def _blank_opencode_session():
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
            "asst": 0, "user": 0, "tools": 0, "cost": 0.0, "active": 0.0}


# ===========================================================================
# HERMES AGENT (NousResearch) — one SQLite state.db, many sessions.
# `sessions` carries per-session metadata, `session_model_usage` carries a real
# input/output/cache/reasoning token breakdown per (session, model) pair (a
# session can switch models mid-way, same as Codex), and `messages` carries a
# per-turn timestamp/role/tool_calls used only for day-bucketed counts.
# ===========================================================================
def _normalize_hermes(model_id):
    """Hermes routes through many providers verbatim (Anthropic/OpenAI/OpenRouter/
    Nous's own Hermes models); _canonicalize already maps the Claude/GPT/o-series
    spellings, same as opencode's normalizer."""
    if not model_id:
        return "Unknown"
    name = _canonicalize(model_id)
    if name and name != model_id:
        return name
    return model_id.split("/")[-1]


def parse_hermes(agg, db_path):
    from contextlib import closing
    with closing(_open_ro_sqlite(db_path)) as con:
        _parse_hermes_store(agg, con)


def _parse_hermes_store(agg, con):
    import sqlite3
    agg["source"] = "hermes"
    agg["editor"] = "Hermes Agent"
    cur = con.cursor()

    sess = {}
    try:
        rows = cur.execute(
            "SELECT id, cwd, git_branch, title, model, started_at, ended_at, "
            "archived, source FROM sessions").fetchall()
    except Exception:
        con.close()
        raise
    for sid, cwd, branch, title, model, started, ended, archived, chan in rows:
        sess[sid] = {
            "cwd": cwd, "branch": branch, "title": title, "model": model,
            "started": started, "ended": ended or started,
            "archived": bool(archived), "channel": chan,
            "days": {}, "asst": 0, "user": 0, "tools": 0, "req": 0,
            "in": 0, "out": 0, "cr": 0, "cc": 0, "reason": 0, "active": 0.0,
        }

    try:
        urows = cur.execute(
            "SELECT session_id, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_write_tokens, reasoning_tokens, api_call_count, first_seen, last_seen "
            "FROM session_model_usage").fetchall()
    except sqlite3.OperationalError as e:
        if not str(e).startswith("no such table:"):
            con.close()
            raise
        urows = []
    model_tokens = {}   # session_id -> {display_model: tokens}
    for sid, model, it, ot, cr, cw, reason, calls, first, last in urows:
        s = sess.get(sid)
        if s is None:
            continue
        dt = _from_ms_or_s(first or last or s["started"])
        if not dt:
            continue
        date = _buckets(dt)[0]
        disp = _normalize_hermes(model)
        it, ot, cr, cw = int(it or 0), int(ot or 0), int(cr or 0), int(cw or 0)
        reason, calls = int(reason or 0), int(calls or 0)
        agg["_record_session"] = sid
        r = _rec(agg, date, disp)
        r["in"] += it; r["out"] += ot; r["cr"] += cr; r["cc"] += cw
        r["cc5"] += cw          # untiered cache write -> 5m rate, like opencode
        r["reason"] += reason; r["req"] += calls
        T = agg["totals"]
        T["in"] += it; T["out"] += ot; T["cr"] += cr; T["cc"] += cw
        T["cc5"] += cw; T["reason"] += reason; T["req"] += calls
        _bump_time(agg, dt, it + ot + cr + cw, 0)
        s["in"] += it; s["out"] += ot; s["cr"] += cr; s["cc"] += cw
        s["reason"] += reason; s["req"] += calls
        dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                          "asst": 0, "user": 0, "tools": 0, "active": 0.0})
        dd["in"] += it; dd["out"] += ot; dd["cr"] += cr; dd["cc"] += cw
        model_tokens.setdefault(sid, {})
        model_tokens[sid][disp] = model_tokens[sid].get(disp, 0) + it + ot

    dom_model = {sid: max(mt, key=mt.get) for sid, mt in model_tokens.items() if mt}

    # Per-session last-message timestamp for active-time gaps. Declared here,
    # not above: only THIS loop's timestamps are real per-turn events. The
    # session_model_usage loop above is one summary row per (session, model)
    # pair — its "first_seen"/"last_seen" span the whole pair's usage, not a
    # single turn, so it must never feed a gap calculation.
    active_last = {}
    try:
        mrows = cur.execute(
            "SELECT session_id, role, timestamp, tool_calls FROM messages").fetchall()
    except sqlite3.OperationalError as e:
        if not str(e).startswith("no such table:"):
            con.close()
            raise
        mrows = []
    for sid, role, ts, tool_calls_json in mrows:
        s = sess.get(sid)
        if s is None or not ts:
            continue
        dt = _from_ms_or_s(ts)
        if not dt:
            continue
        date = _buckets(dt)[0]
        model = dom_model.get(sid) or _normalize_hermes(s.get("model")) or "Unknown"
        agg["_record_session"] = sid
        r = _rec(agg, date, model)
        dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0,
                                          "asst": 0, "user": 0, "tools": 0, "active": 0.0})
        gap = _active_gap(active_last.get(sid), dt)
        active_last[sid] = dt.isoformat()
        r["active"] += gap; s["active"] += gap; dd["active"] += gap
        if role == "user":
            r["user"] += 1; agg["totals"]["user"] += 1; s["user"] += 1; dd["user"] += 1
        elif role == "assistant":
            r["asst"] += 1; agg["totals"]["asst"] += 1; s["asst"] += 1; dd["asst"] += 1
            _bump_time(agg, dt, 0, 1)
            if tool_calls_json:
                try:
                    calls = json.loads(tool_calls_json) or []
                except Exception:
                    calls = []
                for tc in calls:
                    if not isinstance(tc, dict):
                        continue
                    fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                    name = fn.get("name") or tc.get("name") or "tool"
                    _tool(agg, date, name)
                    r["tools"] += 1; s["tools"] += 1; dd["tools"] += 1
    con.close()

    tally = {}
    for s in sess.values():
        p = _leaf(s["cwd"]) or s["cwd"] or "(unknown)"
        w = s["in"] + s["out"] + s["asst"] + s["user"]
        if w:
            tally[p] = tally.get(p, 0) + w
    agg["project"] = (max(tally, key=tally.get) if tally else "Hermes Agent")

    out = []
    for sid, s in sess.items():
        mt = model_tokens.get(sid, {})
        ranked = [m for m, _ in sorted(mt.items(), key=lambda kv: -kv[1])]
        dom = dom_model.get(sid) or _normalize_hermes(s["model"])
        models = [dom] + [m for m in ranked if m != dom]
        start = _from_ms_or_s(s["started"])
        end = _from_ms_or_s(s["ended"]) or start
        out.append({
            "_sid": sid, "id": (sid or "")[:8], "source": "hermes", "ide": IDE_FIXED["hermes"], "editor": "Hermes Agent",
            "title": s.get("title"), "project": _leaf(s["cwd"]) or s["cwd"] or "(unknown)",
            "model": dom, "models": models[:6], "nmodels": len(mt),
            "branch": s.get("branch"), "entry": s.get("channel"),
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
            "in": s["in"], "out": s["out"], "cr": s["cr"], "cc": s["cc"],
            "cc5": s["cc"], "cc1": 0,
            "asst": s["asst"], "user": s["user"], "req": s["req"], "prem": 0.0,
            "tools": s["tools"], "side": 0, "days": s["days"],
            "active": round(s.get("active", 0), 1),
            "archived_session": bool(s.get("archived")),
        })
    agg["sessions"] = out
    _finish_store_records(agg)



# ===========================================================================
# OPENCLAW — per-agent SQLite (current) and JSONL transcripts (older / archived).
# Transcript events are the same JSON either way: a `session` header, then
# `message` entries whose assistant messages carry a normalized `usage`
# {input, output, cacheRead, cacheWrite, cacheWrite1h?, reasoning?, cost.total} —
# input EXCLUDES cache reads and writes (OpenClaw's model layer subtracts them from
# the provider's prompt count), and reasoning is a subset of output, like ours.
# ===========================================================================
_OPENCLAW_DB = os.path.join("agent", "openclaw-agent.sqlite")


def _zstd_decode_many(blobs):
    """Decode zstd frames: Python 3.14's compression.zstd when present, else one
    batched run of the `zstd` command. Returns {key: text}; what can't be decoded is
    left out (and counted by the caller), never guessed."""
    if not blobs:
        return {}
    try:
        from compression import zstd as _z          # Python 3.14+
        out = {}
        for k, b in blobs.items():
            try:
                out[k] = _z.decompress(b).decode("utf-8", "replace")
            except Exception as e:
                sys.stderr.write(f"[openclaw] zstd row {k}: {e}\n")
        return out
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if not exe:
        return {}
    import tempfile
    out = {}
    with tempfile.TemporaryDirectory(prefix="agenttelemetry-zstd-") as tmp:
        names = {}
        for i, (k, b) in enumerate(blobs.items()):
            fn = os.path.join(tmp, f"{i}.zst")
            with open(fn, "wb") as f:
                f.write(b)
            names[k] = fn
        keys = list(names)
        for j in range(0, len(keys), 400):          # keep the command line bounded
            chunk = keys[j:j + 400]
            r = subprocess.run([exe, "-d", "-q", "-f", *[names[k] for k in chunk]],
                               capture_output=True, text=True)
            if r.returncode != 0:
                sys.stderr.write(f"[openclaw] zstd: {r.stderr.strip()[:200]}\n")
            for k in chunk:
                try:
                    with open(names[k][:-4], encoding="utf-8", errors="replace") as f:
                        out[k] = f.read()
                except OSError:
                    pass
    return out


def _openclaw_ts(entry):
    m = entry.get("message") if isinstance(entry.get("message"), dict) else {}
    ts = entry.get("timestamp") or m.get("timestamp")
    if isinstance(ts, (int, float)):
        return _from_ms_or_s(ts)
    return _from_iso(ts) if isinstance(ts, str) else None


def _openclaw_sessions(agent_dir):
    """{session_id: [event dict, ...]} in order, plus {session_id: meta}, plus how
    many compressed events couldn't be decoded. SQLite first; a JSONL transcript
    only for a session the database doesn't have."""
    events, meta, undecoded = {}, {}, 0
    db = os.path.join(agent_dir, _OPENCLAW_DB)
    if os.path.exists(db):
        con = _open_ro_sqlite(db)
        try:
            cur = con.cursor()
            cols = {r[1] for r in cur.execute("PRAGMA table_info(transcript_events)")}
            zcol = "event_zstd" if "event_zstd" in cols else "NULL"
            rows = cur.execute(f"SELECT session_id, seq, event_json, {zcol}, created_at "
                               "FROM transcript_events ORDER BY session_id, seq").fetchall()
            packed = {(sid, seq): bytes(z) for sid, seq, ej, z, _ in rows if ej is None and z}
            plain = _zstd_decode_many(packed)
            undecoded = len(packed) - len(plain)
            for sid, seq, ej, z, created in rows:
                text = ej if ej is not None else plain.get((sid, seq))
                if not text:
                    continue
                try:
                    e = json.loads(text)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    if not e.get("timestamp") and created:
                        e["timestamp"] = created
                    events.setdefault(sid, []).append(e)
            try:
                for sid, model, prov in cur.execute(
                        "SELECT session_id, model, model_provider FROM session_windows"):
                    meta.setdefault(sid, {}).update(model=model, provider=prov)
            except Exception as e:
                sys.stderr.write(f"[openclaw] session_windows: {e}\n")
            try:
                for sid, label, disp in cur.execute(
                        "SELECT current_session_id, label, display_name FROM session_nodes"):
                    meta.setdefault(sid, {})["title"] = label or disp
            except Exception as e:
                sys.stderr.write(f"[openclaw] session_nodes: {e}\n")
        finally:
            con.close()
    sdir = os.path.join(agent_dir, "sessions")
    index = {}
    try:
        with open(os.path.join(sdir, "sessions.json"), encoding="utf-8") as f:
            raw = json.load(f)
        for v in (raw.values() if isinstance(raw, dict) else []):
            if isinstance(v, dict) and v.get("sessionId"):
                index[v["sessionId"]] = v.get("label") or v.get("displayName") or v.get("subject")
    except (OSError, ValueError):
        pass
    for fn in sorted(glob.glob(os.path.join(sdir, "*.jsonl")) + glob.glob(os.path.join(sdir, "*.jsonl.*"))):
        sid = os.path.basename(fn).split(".jsonl", 1)[0]
        if sid in events:                   # the database already holds this session
            continue
        try:
            with open(fn, encoding="utf-8", errors="replace") as f:
                evs = []
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        evs.append(e)
        except OSError as e:
            sys.stderr.write(f"[openclaw] {fn}: {e}\n")
            continue
        if evs:
            events[sid] = evs
            if index.get(sid):
                meta.setdefault(sid, {})["title"] = index[sid]
    return events, meta, undecoded


def parse_openclaw(agg, agent_dir):
    agg["source"] = "openclaw"
    agg["editor"] = "OpenClaw"
    agent = _leaf(agent_dir) or "agent"
    events, meta, undecoded = _openclaw_sessions(agent_dir)
    agg["state"]["undecoded"] = undecoded
    if undecoded:
        sys.stderr.write(f"[openclaw] {agent_dir}: {undecoded} compressed transcript event(s) "
                         "not read — install zstd (or use Python 3.14+) to include them\n")
    # A fork copies its parent's messages: count each response once. A session the
    # store no longer holds keeps its responses in the ledger (reconcile), so a
    # fork must not start counting them as its own once the parent is deleted.
    prior = agg.pop("_prior_keys", None) or {}
    seen = {k for sid, keys in prior.items() if sid not in events for k in keys}
    out, tally = [], {}
    T = agg["totals"]
    for sid, evs in events.items():
        agg["_record_session"] = sid
        m0 = meta.get(sid, {})
        cur_model = m0.get("model")
        cwd, start, end = None, None, None
        s = {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0, "asst": 0,
             "user": 0, "tools": 0, "req": 0, "cost": 0.0, "active": 0.0, "days": {}}
        mt, last = {}, None
        for e in evs:
            t = e.get("type")
            if t == "session":
                cwd = e.get("cwd") or cwd
                continue
            if t == "model_change":
                cur_model = e.get("modelId") or e.get("model") or cur_model
                continue
            if t != "message" or not isinstance(e.get("message"), dict):
                continue
            msg = e["message"]
            dt = _openclaw_ts(e)
            if not dt:
                continue
            date = _buckets(dt)[0]
            role = msg.get("role")
            if role == "assistant":
                u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
                key = hashlib.sha256(json.dumps(
                    [msg.get("timestamp") or e.get("timestamp"), msg.get("model") or cur_model,
                     u.get("input"), u.get("output"), u.get("cacheRead"), u.get("cacheWrite")],
                    default=str).encode()).hexdigest()[:16]
                if key in seen and (u.get("input") or u.get("output")):
                    continue               # the same response, copied into a fork
                seen.add(key)
                if u.get("input") or u.get("output"):
                    _session_dims(agg).setdefault("keys", []).append(key)
            elif role != "user":
                continue
            start = start or dt; end = dt
            dd = s["days"].setdefault(date, {"in": 0, "out": 0, "cr": 0, "cc": 0, "asst": 0,
                                             "user": 0, "tools": 0, "active": 0.0, "cost": 0.0})
            gap = _active_gap(last, dt); last = dt.isoformat()
            s["active"] += gap; dd["active"] += gap
            if role == "user":
                internal = msg.get("__openclaw") if isinstance(msg.get("__openclaw"), dict) else {}
                if msg.get("excludeFromContext") or internal.get("contextFreeCommand"):
                    continue
                r = _rec(agg, date, "(user)")
                r["user"] += 1; r["active"] += gap; T["user"] += 1; s["user"] += 1; dd["user"] += 1
                if not m0.get("title"):
                    _set_title(agg, _first_text(msg.get("content")), "prompt")
                    m0.setdefault("_first", _first_text(msg.get("content")))
                continue
            if role != "assistant":
                continue
            model = _normalize_hermes(msg.get("model") or cur_model)
            u = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
            inp, out_, cr = int(u.get("input") or 0), int(u.get("output") or 0), int(u.get("cacheRead") or 0)
            cw = int(u.get("cacheWrite") or 0)
            cw1 = min(cw, int(u.get("cacheWrite1h") or 0))
            reason = int(u.get("reasoning") or 0)
            cost = u.get("cost") if isinstance(u.get("cost"), dict) else {}
            logged = float(cost.get("total") or 0)
            r = _rec(agg, date, model)
            r["in"] += inp; r["out"] += out_; r["cr"] += cr; r["cc"] += cw
            r["cc5"] += cw - cw1; r["cc1"] += cw1; r["reason"] += reason
            r["asst"] += 1; r["req"] += 1; r["cost"] += logged; r["active"] += gap
            T["in"] += inp; T["out"] += out_; T["cr"] += cr; T["cc"] += cw
            T["cc5"] += cw - cw1; T["cc1"] += cw1; T["reason"] += reason
            T["asst"] += 1; T["req"] += 1
            _bump_time(agg, dt, inp + out_ + cr + cw, 1)
            ntools = 0
            for b in (msg.get("content") or []) if isinstance(msg.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") in ("toolCall", "tool_use") and b.get("name"):
                    _tool(agg, date, b["name"]); ntools += 1
            r["tools"] += ntools; T["tools"] += ntools
            for k, v in (("in", inp), ("out", out_), ("cr", cr), ("cc", cw)):
                s[k] += v; dd[k] += v
            s["cc5"] += cw - cw1; s["cc1"] += cw1; s["reason"] += reason
            s["asst"] += 1; s["req"] += 1; s["tools"] += ntools; s["cost"] += logged
            dd["asst"] += 1; dd["tools"] += ntools; dd["cost"] += logged
            mt[model] = mt.get(model, 0) + inp + out_
        if not (s["asst"] or s["user"]):
            continue
        project = _leaf(cwd) or f"OpenClaw · {agent}"
        tally[project] = tally.get(project, 0) + s["in"] + s["out"] + s["asst"]
        ranked = [m for m, _ in sorted(mt.items(), key=lambda kv: -kv[1])] or [_normalize_hermes(m0.get("model"))]
        title = m0.get("title") or ((m0.get("_first") or "").strip()[:90] or None)
        out.append({
            "_sid": sid, "id": (sid or "")[:8], "source": "openclaw", "ide": IDE_FIXED["openclaw"],
            "editor": "OpenClaw", "title": title, "project": project,
            "model": ranked[0], "models": ranked[:6], "nmodels": len(mt),
            "branch": None, "entry": agent,
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "in": s["in"], "out": s["out"], "cr": s["cr"], "cc": s["cc"], "cc5": s["cc5"], "cc1": s["cc1"],
            "asst": s["asst"], "user": s["user"], "req": s["req"], "prem": 0.0,
            "tools": s["tools"], "side": 0, "days": s["days"], "cost": round(s["cost"], 6),
            "active": round(s["active"], 1),
        })
    agg["project"] = max(tally, key=tally.get) if tally else f"OpenClaw · {agent}"
    agg["sessions"] = out
    _finish_store_records(agg)

# ===========================================================================
# Incremental file scanning
# ===========================================================================
def _read_new_bytes(path, offset):
    """Return (list_of_complete_lines, new_offset). Reads only bytes past offset
    and stops at the last newline so a partial trailing line isn't parsed."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    if not data:
        return [], offset
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return [], offset  # no complete line yet
    chunk = data[:last_nl + 1]
    new_offset = offset + len(chunk)
    text = chunk.decode("utf-8", errors="replace")
    return text.splitlines(), new_offset


def _uri_to_path(uri):
    """file:///Users/me/My%20Repo -> /Users/me/My Repo (also passes plain paths)."""
    from urllib.parse import unquote, urlparse
    if not uri:
        return ""
    if uri.startswith("file://"):
        pr = urlparse(uri)
        return unquote(pr.path)
    return unquote(uri)


def _copilot_project_map():
    """Map workspaceStorage hash -> friendly workspace folder name."""
    m = {}
    for root in COPILOT_ROOTS:
        for wj in glob.glob(os.path.join(root, "User", "workspaceStorage", "*", "workspace.json")):
            try:
                o = json.load(open(wj))
            except Exception:
                continue
            path = _uri_to_path(o.get("folder") or o.get("workspace") or "")
            if not path:
                continue
            name = _leaf(path)
            # A MULTI-ROOT window stores a pointer to a workspace *file*, whose
            # basename is literally "workspace.json" — read it for the real roots.
            if name in ("workspace.json",) or path.endswith(".code-workspace"):
                try:
                    wo = json.load(open(path))
                    roots = [_leaf(_uri_to_path(f.get("path") or f.get("uri") or ""))
                             for f in (wo.get("folders") or [])]
                    roots = [r for r in roots if r]
                    name = " + ".join(roots[:2]) + ("…" if len(roots) > 2 else "")
                except Exception:
                    name = ""
                if not name:
                    name = "(multi-root workspace)"
            if name:
                m[os.path.dirname(wj)] = name
    return m


def discover():
    """Return list of (source, path, editor_hint)."""
    out = []
    claude_paths = set()
    for g in CLAUDE_GLOBS:
        for p in glob.glob(g, recursive=True):
            # Project-folder compatibility links must not parse one transcript
            # twice. Keep the physical path so parents and children share a root.
            p = os.path.realpath(p)
            if p not in claude_paths:
                claude_paths.add(p)
                out.append(("claude", p, None))
    for g in CODEX_GLOBS:
        for p in glob.glob(g, recursive=True):
            out.append(("codex", p, None))
    for g in CLAUDE_DESKTOP_GLOBS:
        for p in glob.glob(g, recursive=True):
            out.append(("claude-desktop", p, "Claude Desktop (agent mode)"))
    # NOTE: Gemini CLI is intentionally NOT discovered — its local chat logs persist
    # only a re-written session-context preamble (no prompts/responses/tokens/model),
    # so there is no usable usage data to report. See GEMINI_GLOBS / parse_gemini.
    for path, editor in AG.discover_conversations(ANTIGRAVITY_ROOTS):
        out.append(("antigravity", path, editor))
    for db in CURSOR_DBS:
        if os.path.exists(db):
            out.append(("cursor", db, "Cursor"))
    if os.path.exists(HERMES_DB):
        out.append(("hermes", HERMES_DB, "Hermes Agent"))
    # OpenClaw: one entry per agent directory — it holds both the SQLite store and
    # any older JSONL transcripts, which parse_openclaw reconciles
    for root in _openclaw_roots():
        for ad in sorted(glob.glob(os.path.join(root, "agents", "*"))):
            if (os.path.exists(os.path.join(ad, _OPENCLAW_DB))
                    or glob.glob(os.path.join(ad, "sessions", "*.jsonl*"))):
                out.append(("openclaw", ad, "OpenClaw"))
    # opencode: current versions keep everything in opencode.db; older versions
    # used storage/message/<session>/msg_*.json. Discover both so upgrades and
    # legacy installs are both covered.
    seen_db_inodes = set()
    for db in OPENCODE_DBS:
        if not os.path.exists(db):
            continue
        try:
            st = os.stat(db)
            inode = (st.st_dev, st.st_ino)
        except Exception:
            continue
        if inode in seen_db_inodes:
            continue
        seen_db_inodes.add(inode)
        out.append(("opencode", db, "opencode"))
    for root in OPENCODE_ROOTS:
        for d in glob.glob(os.path.join(root, "storage", "message", "*")):
            if os.path.isdir(d):
                out.append(("opencode", d, "opencode"))
    for pattern in COPILOT_SDK_GLOBS:
        for path in glob.glob(pattern):
            out.append(("copilot", path, "Copilot SDK"))
    for root in COPILOT_ROOTS:
        editor = EDITOR_LABEL.get(os.path.basename(root), os.path.basename(root))
        # older VS Code stores chat sessions as *.json (whole-object); newer builds use
        # *.jsonl (an append-only mutation log) — parse both.
        pats = [
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*.json"),
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*.jsonl"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*.json"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*.jsonl"),
            # Newer builds nest each session in its own directory as
            # chatSessions/<uuid>/index.json instead of a flat file. Seen on Puku;
            # the flat globs above miss it entirely because it is one level deeper.
            os.path.join(root, "User", "workspaceStorage", "*", "chatSessions", "*", "index.json"),
            os.path.join(root, "User", "globalStorage", "emptyWindowChatSessions", "*", "index.json"),
        ]
        for pat in pats:
            for p in glob.glob(pat):
                out.append(("copilot", p, editor))
    return out


# ===========================================================================
# Usage a tool deleted from a log that is still on disk
#
# The ledger keeps a log that disappears (dashboard.py marks it archived). It
# could not see the other way usage vanished: the log stays, the tool removes
# part of what it held, and the next full reparse rebuilt the aggregate without
# it. OpenCode, Hermes and OpenClaw keep every session in one store, so deleting
# a session there (or OpenCode's undo, which deletes the reverted messages)
# dropped its tokens and cost from every total. A Claude or Copilot log that got
# shorter (Copilot rewrites its chat log without requests removed in the chat)
# did the same.
#
# reconcile() runs when update_file returns a NEW aggregate for a path the ledger
# already had. What the source no longer holds lives in agg["_retained"], beside
# what it does hold, and is added back after every reparse. Kept usage is found
# by IDENTITY wherever the source has one, never by comparing totals alone:
# usage also moves between rows without being deleted (a session's main model
# changes, a timezone change re-buckets local dates and hours, an OpenClaw fork
# takes over its parent's copied responses), and the same refresh can bring a
# deletion and a same-sized new turn (OpenCode's undo, then a retry).
#   * a store session that is gone keeps its last-known contribution whole and
#     shows as deleted; if the source has it again, the source's copy replaces it
#   * an OpenCode message gone from a session still present keeps the usage it
#     was counted with, by message id (_store_detail holds one summary each)
#   * a Claude/Copilot log that got shorter keeps what each dimension's totals
#     lost, capped so usage that only moved row is not kept; the records it had
#     counted stay in the parser's seen-set, so restored ones are not recounted
# Message and log shortfalls are measured only when the SOURCE changed and both
# parses ran under the same ledger version: a parser fix that lowers counts ships
# with a CACHE_VERSION bump, and retention must not undo it. Hermes and OpenClaw
# keep whole deleted sessions only. Codex logs are only ever deleted whole (the
# archive keeps them) and have no record identity to make a shortened-then-
# restored log safe. Cursor keeps its own row history (_cursor_retain_history);
# Antigravity, Copilot SDK and Copilot .json hold one conversation per path.
# ===========================================================================
_RETAIN_DIMS = ("records", "tools", "skills", "ctx", "hourly", "reads", "activity",
                "used_ext", "totals", "reasoning_effort")
_RETAIN_SESSION_FIELDS = ("in", "out", "cr", "cc", "cc5", "cc1", "reason", "asst", "user",
                          "req", "prem", "tools", "cost", "active", "ws")
# A key added to these dims needs the full shape its readers index directly.
_RETAIN_BLANK = {
    "records": {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
                "asst": 0, "user": 0, "req": 0, "prem": 0.0, "tools": 0, "cost": 0.0,
                "active": 0.0},
    "hourly": {"tokens": 0, "msgs": 0},
    "skills": {"tok": 0, "asst": 0, "in": 0, "out": 0, "cr": 0, "cc": 0},
    "ctx": {"tok": 0, "n": 0},
    "reasoning_effort": {"levels": {}, "unrecorded": 0},
    # activity rows are read with .get(), and an "e"/"r" field marks an editing
    # or retried turn, so they get no zero-filled template.
}
_RETAIN_EPS = 1e-9
# The record identities each log's parser skips as already counted.
_LOG_SEEN = {"claude": "seen_uuids", "claude-desktop": "seen_uuids", "copilot": "seen_req"}


def _retention_kind(agg):
    """"store" (many sessions in one database), "log" (an append-only log with
    record identity that can get shorter) or None: not handled here."""
    source, path = agg.get("source"), str(agg.get("path") or "")
    if source in ("hermes", "openclaw") or (source == "opencode" and path.endswith(".db")):
        return "store"
    if source in ("claude", "claude-desktop") or (
            source == "copilot" and path.endswith(".jsonl") and _leaf(path) != "events.jsonl"):
        return "log"
    return None


def _is_count(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _shortfall(before, now):
    """What `before` held that `now` no longer does: max(0, before - now) for each
    count, through dicts and numeric lists. None when nothing is missing."""
    if _is_count(before):
        gap = before - (now if _is_count(now) else 0)
        return gap if gap > _RETAIN_EPS else None
    if isinstance(before, dict):
        now = now if isinstance(now, dict) else {}
        out = {}
        for key, value in before.items():
            gap = _shortfall(value, now.get(key))
            if gap is not None:
                out[key] = gap
        return out or None
    if isinstance(before, list) and before and all(_is_count(v) for v in before):
        now = now if isinstance(now, list) else []
        out = [_shortfall(v, now[i] if i < len(now) else 0) or 0 for i, v in enumerate(before)]
        return out if any(out) else None
    return None


def _without(value, kept):
    """`value` less what was already kept: what the source itself held."""
    return (_shortfall(value, kept) or {}) if kept else value


def _flat(value, path=()):
    """{path: count} for every count in one row (dicts and numeric lists)."""
    out = {}
    if _is_count(value):
        out[path] = value
    elif isinstance(value, dict):
        for key, item in value.items():
            out.update(_flat(item, path + (key,)))
    elif isinstance(value, list) and all(_is_count(v) for v in value):
        for i, item in enumerate(value):
            out[path + (i,)] = item
    return out


def _unflat(paths, template):
    """Rebuild a row from {path: count}, in the shape of `template`."""
    if () in paths:
        return paths[()]
    if isinstance(template, list):
        out = [0] * len(template)
        for path, value in paths.items():
            out[path[0]] = value
        return out
    grouped = {}
    for path, value in paths.items():
        grouped.setdefault(path[0], {})[path[1:]] = value
    template = template if isinstance(template, dict) else {}
    return {key: _unflat(sub, template.get(key)) for key, sub in grouped.items()}


def _capped_gap(before, now):
    """Rows `before` held that `now` lacks, never more per field than the whole
    dimension lost: usage that only moved to another row is not a deletion."""
    before = before if isinstance(before, dict) else {}
    now = now if isinstance(now, dict) else {}
    old = {key: _flat(row) for key, row in before.items()}
    new = {key: _flat(row) for key, row in now.items()}
    lost = {}
    for rows, sign in ((old, 1), (new, -1)):
        for row in rows.values():
            for path, value in row.items():
                lost[path] = lost.get(path, 0) + sign * value
    gap = {}
    for key in sorted(old, reverse=True):          # newest date first
        for path, value in old[key].items():
            take = min(value - new.get(key, {}).get(path, 0), lost.get(path, 0))
            if take > _RETAIN_EPS:
                lost[path] -= take
                gap.setdefault(key, {})[path] = take
    return {key: _unflat(paths, before[key]) for key, paths in gap.items()} or None


def _add_counts(target, delta, blank=None):
    """Add every count in `delta` into `target`. A top-level key `target` lacks is
    created from `blank`, so a reader indexing a record field never fails."""
    for key, value in delta.items():
        if _is_count(value):
            current = target.get(key)
            target[key] = (current if _is_count(current) else 0) + value
        elif isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = copy.deepcopy(blank) if blank is not None else {}
            _add_counts(target[key], value)
        elif isinstance(value, list):
            current = target.get(key) if isinstance(target.get(key), list) else []
            current = current + [0] * (len(value) - len(current))
            target[key] = [c + (value[i] if i < len(value) else 0) for i, c in enumerate(current)]


def _dim(agg, dim):
    value = (agg.get("reasoning_effort") or {}).get("days") if dim == "reasoning_effort" else agg.get(dim)
    return value if isinstance(value, dict) else {}


def _add_dim(agg, dim, delta):
    if dim == "reasoning_effort":
        if not isinstance(agg.get("reasoning_effort"), dict):
            return
        target = agg["reasoning_effort"].setdefault("days", {})
    else:
        target = agg.setdefault(dim, {})
    _add_counts(target, delta, _RETAIN_BLANK.get(dim))


def _session_counts(session):
    return {f: session[f] for f in _RETAIN_SESSION_FIELDS if _is_count(session.get(f))}


def _widen_span(agg, start, end):
    for field, value, pick in (("first_ts", start, min), ("last_ts", end, max)):
        values = [v for v in (agg.get(field), value) if isinstance(v, str) and v]
        if values:
            agg[field] = pick(values)


def _may_measure(prev, fresh, kind, version):
    """Only a change in the source itself, parsed by the same ledger version, is
    evidence that usage was deleted rather than counted differently."""
    if prev.get("_parsed_under", version) != version:
        return False
    if kind == "log":
        return (fresh.get("size") or 0) < (prev.get("size") or 0)
    return prev.get("_sig") != fresh.get("_sig")


def _seen_ids(agg, field):
    """The record identities a log's parser has counted, in order."""
    value = (agg.get("state") or {}).get(field)
    if field == "seen_uuids":
        value = value if isinstance(value, str) else ""
        return [value[i:i + 12] for i in range(0, len(value), 12)]
    return list(value) if isinstance(value, list) else []


def _set_seen(agg, field, ids):
    ids = list(dict.fromkeys(ids))
    agg.setdefault("state", {})[field] = "".join(ids) if field == "seen_uuids" else ids


def _merge_seen(fresh, prev):
    """Records the previous parse counted stay counted: if a shortened log gets
    them back, the parser skips them, since the kept usage already holds them."""
    field = _LOG_SEEN.get(fresh.get("source"))
    if field:
        _set_seen(fresh, field, _seen_ids(fresh, field) + _seen_ids(prev, field))


def _seed_kept_seen(fresh, ids):
    """A full reparse also skips the records whose usage is kept: a log that got
    them back (a synced copy restored, say) must not count them twice."""
    field = _LOG_SEEN.get(fresh.get("source"))
    if field and isinstance(ids, list) and ids:
        _set_seen(fresh, field, [str(x) for x in ids])


def _reconcile_log(prev, fresh, measure):
    """An append-only log: keep what each dimension's totals lost when it got shorter."""
    kept = copy.deepcopy(prev.get("_retained") or {})
    fresh_events = fresh.get("compactions") if isinstance(fresh.get("compactions"), dict) else {}
    field = _LOG_SEEN.get(fresh.get("source"))
    if measure and field:
        # The records this parse lost, whose usage is kept below.
        have = set(_seen_ids(fresh, field))
        gone = [x for x in _seen_ids(prev, field) if x not in have]
        if gone:
            kept["seen"] = list(dict.fromkeys(list(kept.get("seen") or []) + gone))
    _merge_seen(fresh, prev)
    if measure:
        old = kept.get("dims") or {}
        dims = {}
        for dim in _RETAIN_DIMS:
            held = _without(_dim(prev, dim), old.get(dim))
            gap = (_shortfall(held, _dim(fresh, dim)) if dim == "totals"
                   else _capped_gap(held, _dim(fresh, dim)))
            merged = copy.deepcopy(old.get(dim) or {})
            if gap:
                _add_counts(merged, gap, _RETAIN_BLANK.get(dim))
            if merged:
                dims[dim] = merged
        kept["dims"] = dims
        prev_events = prev.get("compactions") if isinstance(prev.get("compactions"), dict) else {}
        kept["compactions"] = {cid: copy.deepcopy(e) for cid, e in prev_events.items()
                               if cid not in fresh_events}
        if dims:
            for field, pick in (("first_ts", min), ("last_ts", max)):
                values = [v for v in (prev.get(field), kept.get(field)) if isinstance(v, str) and v]
                if values:
                    kept[field] = pick(values)
    else:
        kept["compactions"] = {cid: e for cid, e in (kept.get("compactions") or {}).items()
                               if cid not in fresh_events}
    kept = {k: v for k, v in kept.items() if v}
    if not (kept.get("dims") or kept.get("compactions")):
        return
    fresh["_retained"] = kept
    for dim, delta in (kept.get("dims") or {}).items():
        _add_dim(fresh, dim, delta)
    if isinstance(fresh.get("compactions"), dict):
        for cid, event in (kept.get("compactions") or {}).items():
            fresh["compactions"].setdefault(cid, copy.deepcopy(event))
    _widen_span(fresh, kept.get("first_ts"), kept.get("last_ts"))
    if any("\tClaude Haiku 5.5" in key for key in (kept.get("dims") or {}).get("records", {})):
        # Kept usage was priced when it was parsed and cannot be backfilled; an
        # absent marker would reparse this log on every refresh.
        fresh.setdefault("haiku_pricing", 1)
    _finalize_session(fresh, fresh["source"], fresh["path"])


def _store_matches(prev_sessions, fresh_by_sid):
    """[(previous session, identity, fresh sid or None)]. Full native ids where the
    ledger has them; an entry written before full ids were kept matches on the
    eight-character id only when that is unambiguous (else also on its start).
    Anything ambiguous is left out: neither kept nor compared."""
    out, legacy, fresh_short, claimed = [], {}, {}, set()
    for sid in fresh_by_sid:
        fresh_short.setdefault(sid[:8], []).append(sid)
    for s in prev_sessions:
        if s.get("_sid"):
            out.append((s, s["_sid"], s["_sid"] if s["_sid"] in fresh_by_sid else None))
        elif s.get("id"):
            legacy.setdefault(s["id"], []).append(s)
    for short, group in legacy.items():
        candidates = fresh_short.get(short, [])
        starts = [s.get("start") for s in group]
        unmatched = []
        for s in group:
            if len(group) == 1 and len(candidates) == 1:
                match = list(candidates)
            elif starts.count(s.get("start")) > 1:
                continue                     # same short id and start: cannot tell apart
            else:
                match = [sid for sid in candidates if fresh_by_sid[sid].get("start") == s.get("start")]
            if len(match) == 1 and match[0] not in claimed:
                claimed.add(match[0])
                out.append((s, match[0], match[0]))
            elif not match:
                unmatched.append(s)
        # Gone only if every remaining session with this short id is accounted
        # for: one left over might be this session with a changed start.
        if all(sid in claimed for sid in candidates):
            out.extend((s, f"short:{short}@{s.get('start')}", None) for s in unmatched
                       if starts.count(s.get("start")) == 1)
    return out


def _store_returned(identity, fresh_by_sid):
    """The fresh sid of a kept session the store holds again, or None. One kept
    before full ids were stored matches by short id and start together."""
    if identity in fresh_by_sid:
        return identity
    if identity.startswith("short:"):
        short, _, start = identity[6:].partition("@")
        found = [sid for sid, s in fresh_by_sid.items()
                 if sid[:8] == short and str(s.get("start")) == start]
        if len(found) == 1:
            return found[0]
    return None


def _store_response_keys(agg):
    """{session id: counted response keys} of a previous OpenClaw parse, kept
    sessions included, so a fork cannot recount a deleted parent's responses."""
    keys = {sid: d.get("keys") or [] for sid, d in (agg.get("_store_detail") or {}).items()}
    for entry in ((agg.get("_retained") or {}).get("sessions") or {}).values():
        sid = (entry.get("session") or {}).get("_sid")
        if sid and entry.get("keys"):
            keys.setdefault(sid, entry["keys"])
    return keys


def _message_usage(sid, mid, m):
    """What one stored OpenCode message added, exactly as parse_opencode_db counts it:
    [role, date, hour, model, in, out, reasoning, cache read, cache write, cost,
     tool names, active seconds]."""
    role, date, hour, model, inp, out, reason, cr, cw, cost, tools, gap = m
    key = date + "\t" + model
    if role == "u":       # a prompt's gap counts on its record, not on the session
        return {"records": {key: {"user": 1, "active": gap}}, "tools": {}, "hourly": {},
                "session": {"user": 1}, "totals": {"user": 1}, "ids": []}
    names = {}
    for name in tools:
        names[date + "\t" + name] = names.get(date + "\t" + name, 0) + 1
    usage = {"in": inp, "out": out, "reason": reason, "cr": cr, "cc": cw, "cc5": cw,
             "asst": 1, "cost": cost, "tools": len(tools)}
    return {"records": {key: dict(usage, active=gap)}, "tools": names,
            "hourly": {f"{date}\t{hour}": {"tokens": inp + out + cr + cw, "msgs": 1}},
            "session": dict(usage, active=gap), "totals": usage, "ids": [[sid, mid]]}


def _reconcile_store(prev, fresh, measure):
    """Many sessions in one database: keep a deleted session whole, and (OpenCode)
    each message deleted from a session that is still there."""
    kept = copy.deepcopy(prev.get("_retained") or {})
    whole, deleted, new_deleted = kept.get("sessions") or {}, kept.get("messages") or {}, {}
    fresh_by = {s["_sid"]: s for s in fresh.get("sessions", []) if s.get("_sid")}
    before_detail = prev.get("_store_detail") or {}
    now_detail = fresh.get("_store_detail") or {}
    prev_ids = [x for x in prev.get("opencode_ids") or [] if isinstance(x, list) and len(x) == 2]
    live = [s for s in prev.get("sessions", []) if not s.get("retained")]
    for s, identity, sid in _store_matches(live, fresh_by):
        source_sid = s.get("_sid")
        detail = (before_detail.get(source_sid) or {}) if source_sid else {}
        gone = deleted.get(identity) or {}       # its messages deleted earlier
        if sid is None:
            session = copy.deepcopy(s)
            session.update(retained=True, archived=True)
            session.pop("retained_usage", None)
            entry = {"session": session, "tools": copy.deepcopy(detail.get("tools") or {}),
                     "hourly": copy.deepcopy(detail.get("hourly") or {})}
            for mid, m in gone.items():
                part = _message_usage(source_sid, mid, m)
                _add_counts(entry["tools"], part["tools"])
                _add_counts(entry["hourly"], part["hourly"], _RETAIN_BLANK["hourly"])
            if source_sid:
                entry["ids"] = [x for x in prev_ids if x[0] == source_sid]
                if detail.get("keys"):
                    entry["keys"] = list(detail["keys"])
                if detail.get("msgs") is not None:
                    entry["msgs"] = {**detail["msgs"], **gone}
            whole[identity] = entry
            continue
        now = now_detail.get(sid) or {}
        now_msgs = now.get("msgs") or {}
        still = {mid: m for mid, m in gone.items() if mid not in now_msgs}
        # A message counted last time and missing now was deleted, also across
        # OpenCode's V1/V2 copies (a deleted or emptied V2 session falls back to
        # its V1 rows): the migration keeps message ids, and V1 has no forks, so
        # a migrated session never loses fork-copied rows.
        if measure and isinstance(detail.get("msgs"), dict) and isinstance(now.get("msgs"), dict):
            still.update({mid: m for mid, m in detail["msgs"].items() if mid not in now_msgs})
        if still:
            new_deleted[sid] = still
    for identity, entry in list(whole.items()):
        sid = _store_returned(identity, fresh_by)
        if sid is None:
            continue
        del whole[identity]
        now = now_detail.get(sid) or {}
        if isinstance(entry.get("msgs"), dict):
            # Emptied then used again, or restored: keep exactly what is not back.
            back = {mid: m for mid, m in entry["msgs"].items() if mid not in (now.get("msgs") or {})}
            if back:
                new_deleted.setdefault(sid, {}).update(back)
        elif entry.get("keys") and not set(entry["keys"]) & set(now.get("keys") or []):
            whole[identity] = entry    # new work under a reused id; the old stays kept
    if not (whole or new_deleted):
        return
    fresh["_retained"] = {k: v for k, v in (("sessions", whole), ("messages", new_deleted)) if v}
    totals = fresh.setdefault("totals", {})
    known_ids = {tuple(x) for x in fresh.get("opencode_ids") or []}

    def add(session, records, tools, hourly, session_usage, total_usage, ids):
        if session is not None:
            _add_counts(session.setdefault("records", {}), records, _RETAIN_BLANK["records"])
            _add_counts(session, session_usage)
        _add_counts(fresh.setdefault("records", {}), records, _RETAIN_BLANK["records"])
        _add_counts(fresh.setdefault("tools", {}), tools)
        _add_counts(fresh.setdefault("hourly", {}), hourly, _RETAIN_BLANK["hourly"])
        _add_counts(totals, {k: v for k, v in total_usage.items() if k in totals or k == "cost"})
        if "opencode_ids" in fresh:
            for x in ids:
                if tuple(x) not in known_ids:
                    known_ids.add(tuple(x))
                    fresh["opencode_ids"].append(list(x))

    for entry in whole.values():
        session = copy.deepcopy(entry["session"])
        fresh["sessions"].append(session)
        add(None, session.get("records") or {}, entry.get("tools") or {}, entry.get("hourly") or {},
            {}, _session_counts(session), entry.get("ids") or [])
        _widen_span(fresh, session.get("start"), session.get("end"))
    for sid, messages in new_deleted.items():
        session = fresh_by[sid]
        for mid, m in messages.items():
            part = _message_usage(sid, mid, m)
            add(session, part["records"], part["tools"], part["hourly"], part["session"],
                part["totals"], part["ids"])
        session["retained_usage"] = True


def reconcile(prev, fresh, version):
    """Add back the usage `prev` counted that the source no longer holds (see the
    section comment). `version` is the ledger's CACHE_VERSION; every new
    aggregate is stamped with it."""
    if not isinstance(fresh, dict) or fresh is prev:
        return fresh
    kind = _retention_kind(fresh)
    if isinstance(prev, dict) and kind and _retention_kind(prev) == kind:
        measure = _may_measure(prev, fresh, kind, version)
        if kind == "store":
            _reconcile_store(prev, fresh, measure)
        else:
            _reconcile_log(prev, fresh, measure)
    fresh["_parsed_under"] = version
    return fresh


def update_file(agg, source, path, editor_hint, proj_map):
    """(Re)parse a single file incrementally. Returns the updated agg."""
    try:
        st = os.stat(path)
    except OSError:
        return agg
    size, mtime = st.st_size, st.st_mtime
    kept_seen = (agg.get("_retained") or {}).get("seen") if isinstance(agg, dict) else None

    if source == "copilot" and _leaf(path) == "events.jsonl":
        return _update_copilot_sdk(agg, path)

    if source == "copilot" and path.endswith(".json"):
        # older whole-object format, rewritten on each save → full reparse on change
        if agg and agg.get("size") == size and agg.get("mtime") == mtime:
            return agg
        fresh = _blank_agg(source, path)
        # project name from workspaceStorage hash
        ws_dir = os.path.dirname(os.path.dirname(path))  # .../<hash>
        fresh["project"] = proj_map.get(ws_dir, "(no folder)")
        fresh["editor"] = editor_hint
        with open(path) as f:
            obj = json.load(f)
        parse_copilot(fresh, obj)
        fresh["size"], fresh["mtime"] = size, mtime
        _finalize_session(fresh, source, path)
        return fresh

    if source == "openclaw":
        # an agent directory: re-parse when its database, WAL or any transcript changes
        parts = [os.path.join(path, _OPENCLAW_DB), os.path.join(path, _OPENCLAW_DB) + "-wal"]
        parts += sorted(glob.glob(os.path.join(path, "sessions", "*.jsonl*")))
        sig, total = [], 0
        for f in parts:
            try:
                fs = os.stat(f); sig.append([os.path.basename(f), fs.st_size, fs.st_mtime]); total += fs.st_size
            except OSError:
                pass
        # _store_detail: per-session tools/hourly, recorded once for older entries
        # so a session deleted later keeps all of its usage (see reconcile).
        if agg and agg.get("_sig") == sig and "_store_detail" in agg:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        fresh["size"] = total
        if agg:
            fresh["_prior_keys"] = _store_response_keys(agg)
        parse_openclaw(fresh, path)
        fresh["_sig"] = sig
        fresh["mtime"] = max((x[2] for x in sig), default=mtime)
        return fresh

    if source == "cursor":
        if __package__:
            from . import cursor_data as CD
        else:
            import cursor_data as CD
        wal = path + "-wal"
        try:
            wst = os.stat(wal)
        except OSError:
            wst = None
        workspace_sig = CD.workspace_signature(path)
        sig = [size, st.st_mtime_ns, wst.st_size if wst else 0,
               wst.st_mtime_ns if wst else 0, workspace_sig]
        if agg and agg.get("_sig") == sig and agg.get("cursor_schema") == CD.CURSOR_SCHEMA:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        if agg and isinstance(agg.get("_cursor_history"), dict):
            fresh["_cursor_history"] = agg["_cursor_history"]
        parse_cursor(fresh, path)
        fresh["_sig"] = sig
        fresh["cursor_schema"] = CD.CURSOR_SCHEMA
        fresh["size"] = size + (wst.st_size if wst else 0)
        fresh["mtime"] = max(mtime, wst.st_mtime if wst else 0)
        return fresh

    if source in ("gemini", "hermes"):
        # A live SQLite writer can append to the WAL without touching the main
        # database. Include it, or Cursor/Hermes stay stale until a checkpoint.
        wal = path + "-wal" if source == "hermes" else None
        try:
            wst = os.stat(wal) if wal else None
        except FileNotFoundError:
            wst = None
        sig = [size, mtime, wst.st_size if wst else 0, wst.st_mtime_ns if wst else 0]
        if agg and agg.get("_sig") == sig and (source != "hermes" or "_store_detail" in agg):
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        if source == "gemini":
            parse_gemini(fresh, path)
            _finalize_session(fresh, source, path)
        else:
            parse_hermes(fresh, path)   # sets its own per-session sessions
        fresh["_sig"] = sig
        fresh["size"] = size + (wst.st_size if wst else 0)
        fresh["mtime"] = max(mtime, wst.st_mtime if wst else 0)
        return fresh

    if source == "antigravity":
        # Include the WAL in the signature: active Antigravity writes may never
        # touch the main database until a checkpoint. Full reparse is staged in
        # a fresh aggregate so any required-schema/protobuf failure leaves the
        # previous aggregate and signature untouched for retry.
        wal = path + "-wal"
        try:
            wst = os.stat(wal)
        except OSError:
            wst = None
        sig = [size, st.st_mtime_ns, wst.st_size if wst else 0,
               wst.st_mtime_ns if wst else 0]
        if agg and agg.get("_sig") == sig and agg.get("antigravity_schema") == 1:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        parse_antigravity(fresh, path)
        fresh["size"] = size + (wst.st_size if wst else 0)
        fresh["mtime"] = max(mtime, wst.st_mtime if wst else 0)
        fresh["_sig"] = sig
        fresh["antigravity_schema"] = 1
        _finalize_session(fresh, source, path)
        if fresh.get("sessions"):
            fresh["sessions"][0].update(fresh.pop("_antigravity_session", {}))
        return fresh

    if source == "opencode":
        if path.endswith(".db"):
            # current opencode: a single SQLite DB for all sessions. Re-parse when
            # the DB or its WAL changes; parse_opencode_db builds its own session
            # list so don't run the generic _finalize_session over the top.
            wal = path + "-wal"
            wal_size = os.path.getsize(wal) if os.path.exists(wal) else 0
            wal_mtime = os.path.getmtime(wal) if os.path.exists(wal) else 0
            sig = [size, mtime, wal_size, wal_mtime]
            if agg and agg.get("_sig") == sig and "opencode_schema" in agg and "_store_detail" in agg:
                return agg
            fresh = _blank_agg(source, path)
            fresh["editor"] = editor_hint
            fresh["size"] = size + wal_size
            parse_opencode_db(fresh, path)
            fresh["_sig"] = sig
            fresh["mtime"] = max(mtime, wal_mtime)
            return fresh

        # older opencode: a directory of msg_*.json for one session
        msgs = sorted(glob.glob(os.path.join(path, "msg_*.json")))
        try:
            sig_mtime = max((os.path.getmtime(m) for m in msgs), default=0.0)
        except OSError:
            sig_mtime = mtime
        sig = [len(msgs), sig_mtime]
        if agg and agg.get("_sig") == sig:
            return agg
        fresh = _blank_agg(source, path)
        fresh["editor"] = editor_hint
        try:                                  # size first: _finalize_session reads it
            fresh["size"] = sum(os.path.getsize(m) for m in msgs)
        except OSError:
            fresh["size"] = 0
        parse_opencode(fresh, path, msgs)
        _finalize_session(fresh, source, path)
        fresh["_sig"] = sig
        fresh["mtime"] = sig_mtime
        return fresh

    # jsonl (claude / codex) — incremental append
    if source == "codex" and agg and agg.get("parent_metadata_version") != 1:
        # Optional source identity backfill: preserve the old entry if parsing
        # fails, and never rebuild or drop archived conversations.
        agg = None
    if (source in ("claude", "claude-desktop", "codex") and agg
            and agg.get("reasoning_effort_version") != _REASONING_EFFORT_VERSION):
        # Available sources can backfill optional metadata without rebuilding the
        # durable ledger. Failure leaves the caller's old entry/signature intact.
        agg = None
    if (source == "codex" and agg
            and agg.get("state", {}).get("compaction_timing", {}).get("version") != _CODEX_COMPACTION_TIMING_VERSION):
        # Stage a full timing backfill locally; the caller retains its old ledger
        # entry/signature if parsing fails. No global cache reset is involved.
        agg = None
    haiku_backfill = (agg and source in ("claude", "claude-desktop")
                     and "haiku_pricing" not in agg
                     and any("\tClaude Haiku 5.5" in k for k in agg.get("records", {})))
    if (not agg or agg.get("size", 0) > size
            or haiku_backfill
            or (source in ("claude", "codex") and agg.get("account_metadata_version") != A.METADATA_VERSION)
            or (source in ("claude", "claude-desktop", "codex") and "compactions" not in agg)):
        agg = _blank_agg(source, path)  # new/truncated log or missing metadata → reparse
        _seed_kept_seen(agg, kept_seen)
    offset = agg["offset"]
    if size == offset and agg.get("mtime") == mtime:
        return agg  # unchanged
    lines, new_offset = _read_new_bytes(path, offset)
    # Stage counters and stream state together, retaining a retryable snapshot.
    previous = agg
    agg = copy.deepcopy(agg)
    if lines:
        if source in ("claude", "claude-desktop"):
            agg["subagent"] = _is_subagent_path(path)
            parse_claude(agg, lines)
        elif source == "copilot":
            parse_copilot_jsonl(agg, lines)
        else:
            parse_codex(agg, lines)
    if source == "copilot":
        ws_dir = os.path.dirname(os.path.dirname(path))   # .../<workspace hash>
        agg["project"] = proj_map.get(ws_dir, "(no folder)")
    if source == "claude-desktop":
        # nested transcripts live under a VM scratch cwd; give them a clean label
        agg["project"] = "Claude Desktop"
        agg["editor"] = "Claude Desktop (agent mode)"
    agg["offset"] = new_offset
    agg["size"], agg["mtime"] = size, mtime
    agg["editor"] = agg.get("editor") or editor_hint
    _finalize_session(agg, source, path)
    previous.clear()
    previous.update(agg)
    return previous


# ---------------------------------------------------------------------------
# Which IDE / surface a session actually ran in.
#
# Every source records this differently and none of them agree on spelling:
#   Copilot  — implicit in WHICH editor's storage the file came from ("Code")
#   Claude   — an `entrypoint` field on each record ("claude-vscode", "cli")
#   Codex    — an `originator` in session_meta ("codex_vscode", "Codex Desktop")
#   Cursor / Claude Desktop / opencode / Hermes — one surface by definition
# so they are collapsed to a shared vocabulary before anything groups by them.
#
# CAVEAT worth keeping in mind: "claude-vscode" and "codex_vscode" name the VS Code
# *extension*, not the fork hosting it. Run either inside Cursor, Windsurf or
# Antigravity and the log still says vscode — the host is genuinely not recorded, so
# those land under "VS Code" and cannot be split further from the log alone.
# ---------------------------------------------------------------------------
# Codex's own log says only "vscode" — it never records WHICH VS Code variant
# hosted it, so Insiders work is indistinguishable from stable from the rollout
# alone. The editor itself does know: its globalStorage/state.vscdb carries the
# Codex extension's per-thread UI state under "openai.chatgpt", and a thread id
# appearing there means that editor opened it. Build {thread id -> editor} from
# every known editor and use it to recover the variant.
#
# A thread present in TWO editors' state is genuinely ambiguous (it was opened in
# both) and is deliberately left unattributed rather than guessed.
_VSCODE_THREADS = {"at": 0.0, "map": {}}
_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _vscode_thread_owners():
    """{codex thread id: editor label} for threads owned by exactly one editor."""
    if time.time() - _VSCODE_THREADS["at"] < 60:
        return _VSCODE_THREADS["map"]
    per = {}
    for root in COPILOT_ROOTS:
        db = os.path.join(root, "User", "globalStorage", "state.vscdb")
        if not os.path.exists(db):
            continue
        label = EDITOR_LABEL.get(os.path.basename(root), os.path.basename(root))
        try:
            con = _open_ro_sqlite(db)
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key='openai.chatgpt'").fetchone()
            con.close()
        except Exception as e:
            sys.stderr.write(f"[ide] {db}: {type(e).__name__}: {e}\n")
            continue
        if not row or not row[0]:
            continue
        v = row[0]
        if isinstance(v, bytes):
            v = v.decode("utf-8", "replace")
        for tid in set(_UUID_RE.findall(v)):
            per.setdefault(tid, set()).add(label)
    out = {t: next(iter(owners)) for t, owners in per.items() if len(owners) == 1}
    _VSCODE_THREADS.update(at=time.time(), map=out)
    return out


IDE_FROM_ENTRY = {
    "claude-vscode": "VS Code",
    "codex_vscode": "VS Code",
    "vscode": "VS Code",
    "cli": "CLI",
    "codex_cli": "CLI",
    "codex_exec": "CLI",
    "codex desktop": "Codex Desktop",
    "codex_desktop": "Codex Desktop",
    "codex_work_desktop": "Codex Desktop",
    "local-agent": "Claude Desktop",
    # A newer entrypoint: the Claude Code CLI stamps this when launched from
    # inside the Claude Desktop app itself — distinct from source "claude-desktop"
    # (Desktop's own agent-mode logs, a wholly different file format), but from
    # the user's chair both are "I was in the Claude Desktop app," so they collapse
    # to the same IDE label rather than showing as two confusingly-similar rows.
    "claude-desktop": "Claude Desktop",
}

# Sources that only ever run in one place — no per-record signal needed.
IDE_FIXED = {
    "cursor": "Cursor",
    "claude-desktop": "Claude Desktop",
    "opencode": "CLI",
    "hermes": "CLI",
    "gemini": "CLI",
    "openclaw": "OpenClaw",
}


def _ide_of(source, agg, editor_hint=None):
    """Normalised IDE/surface for one aggregate. Never guesses: an unrecognised
    entrypoint is passed through as-is rather than being forced into a bucket, so a
    new host shows up as itself instead of silently becoming 'VS Code'."""
    if source in IDE_FIXED:
        return IDE_FIXED[source]
    if source == "antigravity":
        return agg.get("editor") or editor_hint or "Antigravity IDE"
    if source == "copilot":
        # the editor whose storage this file came out of
        return agg.get("editor") or editor_hint or "VS Code"
    entry = (agg.get("entry") or "").strip()
    ide = IDE_FROM_ENTRY.get(entry.lower(), entry) if entry else "CLI"
    # Recover the VS Code variant for Codex, which only ever logs "vscode".
    if source == "codex" and ide == "VS Code":
        owner = _vscode_thread_owners().get(agg.get("_session_id") or "")
        if owner:
            return owner
    return ide


_CODEX_NAMES = {"at": 0.0, "sig": None, "map": {}}


def _codex_thread_names():
    """{thread id: latest thread_name} from Codex's own session index."""
    try:
        st = os.stat(CODEX_SESSION_INDEX)
        sig = (st.st_size, st.st_mtime)
    except OSError:
        return {}
    if sig == _CODEX_NAMES["sig"] and time.time() - _CODEX_NAMES["at"] < 60:
        return _CODEX_NAMES["map"]
    out = {}
    try:
        with open(CODEX_SESSION_INDEX) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                tid, name = o.get("id"), o.get("thread_name")
                if tid and name:
                    out[tid] = name          # append-only: last line wins
    except OSError as e:
        sys.stderr.write(f"[codex] {CODEX_SESSION_INDEX}: {e}\n")
        return _CODEX_NAMES["map"]
    _CODEX_NAMES.update(at=time.time(), sig=sig, map=out)
    return out


def _finalize_session(agg, source, path):
    """Roll the file's totals into a single session summary."""
    T = agg["totals"]
    # Last-resort title for a Codex subagent that never had a UserMessage of its
    # own — its task arrived at spawn time, not as an in-band turn. Only applied
    # if nothing stronger (a real prompt) ever set agg["title"].
    # Codex's own name for the thread beats a prompt snippet — same standing as
    # Claude's aiTitle, and the reason the dashboard disagreed with Codex's UI.
    if source == "codex" and agg.get("_session_id"):
        name = _codex_thread_names().get(agg["_session_id"])
        if name:
            _set_title(agg, name, "ai")
    if agg.get("subagent") and not agg.get("title") and agg.get("_agent_path"):
        # agent_path is namespaced ("/root/science_audit"); every sample seen has
        # a constant, uninformative leading segment, so use the leaf only.
        leaf = agg["_agent_path"].strip("/").split("/")[-1].replace("_", " ").replace("-", " ")
        if leaf:
            agg["title"] = (leaf[:1].upper() + leaf[1:])[:90]
    # rank the models used in this session by tokens (a session — especially a
    # resumed Codex rollout — can switch models mid-way)
    mt = {}
    active_total = 0.0
    for k, r in agg["records"].items():
        mdl = k.split("\t", 1)[1]
        active_total += r.get("active", 0.0)   # active time isn't per-model, but
                                                 # _rec() carries it on every record
        if mdl == "(user)":
            continue
        mt[mdl] = mt.get(mdl, 0) + r["in"] + r["out"]
    ranked = [m for m, _ in sorted(mt.items(), key=lambda kv: -kv[1])]
    dom = agg["state"].get("dom_model") or (ranked[0] if ranked else "Unknown")
    models = [dom] + [m for m in ranked if m != dom]   # dominant first
    base = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"rollout-\d{4}-\d{2}-\d{2}T[\d-]+-([0-9a-f]{8})", base)
    if m:
        base = m.group(1)
    agg["sessions"] = [{
        "id": base[:8],
        "source": source,
        "subagent": bool(agg.get("subagent")),
        "editor": agg.get("editor"),
        "project": agg.get("project"),
        "model": dom,
        "models": models[:6],          # for the "+N" indicator / tooltip
        "nmodels": len(mt),
        "title": agg.get("title"),
        "branch": agg.get("branch"),
        "entry": agg.get("entry"),
        "cliver": agg.get("cliver"),
        "start": agg.get("first_ts"),
        "end": agg.get("last_ts"),
        "in": T["in"], "out": T["out"], "cr": T["cr"], "cc": T["cc"],
        "cc5": T["cc5"], "cc1": T["cc1"],
        "asst": T["asst"], "user": T["user"], "req": T["req"], "ws": T.get("ws", 0),
        "prem": T["prem"], "tools": T["tools"], "side": T.get("side", 0),
        # Reuses Cursor's "subagents" (a count) — here, how many SubAgentActivity
        # "started" markers this Codex session's own file recorded. 0 for anyone
        # who didn't spawn any, so it renders identically to Cursor's absence case.
        "subagents": agg.get("_spawned", 0),
        "open_ctx": agg.get("open_ctx"),
        "ide": _ide_of(source, agg),
        "active": round(active_total, 1),
        "bytes": agg.get("size", 0), "archived": bool(agg.get("archived")),
    }]
    if agg.get("_retained"):
        # Usage the tool deleted from this log is kept (reconcile); the drawer says so.
        agg["sessions"][0]["retained_usage"] = True
    if "reasoning_effort" in agg:
        # Only bounded level/count/date/model metadata is public; correlation
        # state remains private and never appears in a session payload.
        agg["sessions"][0]["reasoning_effort"] = copy.deepcopy(agg["reasoning_effort"])
    if source == "codex" and agg.get("subagent") and agg.get("_parent_session_id"):
        agg["sessions"][0]["parent_id"] = agg["_parent_session_id"]
    if "compactions" in agg:
        events = sorted(agg["compactions"].values(), key=lambda e: (e["timestamp"], e["id"]))
        session = agg["sessions"][0]
        session["compactions"] = events
        # Completion is session activity, but must not create a usage/hourly row
        # or advance the active-time clock between actual prompts and responses.
        if events:
            session["start"] = min(t for t in (session["start"], events[0]["timestamp"]) if t)
            session["end"] = max(t for t in (session["end"], events[-1]["timestamp"]) if t)
