# Native recent activity

The native Windows and macOS clients can request a small recent-session summary
from the running local dashboard:

```http
GET /api/native-activity
```

The route is read-only, offline, loopback-only, and protected by the dashboard's
same-origin request guard. It reads the in-memory retained local aggregates under
the parser refresh lock, applies the dashboard's per-conversation deduplication,
then emits only its bounded projection. It does not discover source logs, refresh
the ledger, write preferences, or access the peer cache. The response is not
included in `/api/health`, `/api/summary`, or peer exports.

The response has this shape:

```json
{
  "observed_at": "2026-10-19T03:00:00Z",
  "window_seconds": 900,
  "sessions": [
    {
      "key": "opaque hash",
      "source": "codex",
      "tool": "Codex",
      "provider": "OpenAI",
      "title": "Untitled session",
      "tokens": 1234,
      "last_activity_at": "2026-10-19T02:58:00Z",
      "subagent": false
    }
  ],
  "total": 1
}
```

`sessions` contains at most three eligible sessions, newest first; `total` counts
all eligible sessions before that display limit. A session is eligible when its
recorded end timestamp is parseable and falls within the inclusive 15-minute
window ending at `observed_at`. Future, missing, malformed, archived, remote, or
explicitly non-live records are omitted. Naive source timestamps follow the
parser's source convention: most ISO timestamps mean UTC; Cursor, OpenCode,
Hermes, and OpenClaw session-end values come from local epoch conversion and are
interpreted in the machine's local timezone.

`tokens` is the lifetime recorded input, output, cache-read, and cache-write sum
for that individual session. It does not estimate recent usage or current context
size. A missing, invalid, or ambiguous multi-session aggregate count is `null`,
not zero. Copilot SDK's inline child breakdowns are inclusive and do not become
additional rows. `source` and `tool` identify the coding tool, never the billed
model. `provider` is a fixed tool-vendor label for Claude, Codex, Copilot, and
Cursor; it is `null` for tools that can use multiple model providers. It does not
identify the provider that billed a particular request.

The opaque `key` is a SHA-256 hash of the tool identity and normalized source
path, so identical short IDs from separate profiles remain distinct. It does not
expose the source ID or path. Titles come only from the parser's retained session
title field, are bounded, and have control characters removed. A tool may use a
short prompt-derived display title in that field; the endpoint never reads or
returns raw messages, arguments, model, project, path, or account fields. Missing
titles appear as “Untitled session.” Claude child display aliases are applied
only after a row passes local and recent-activity checks.

The separately guarded `GET /api/local-servers` route supplies the native clients'
local server inventory; it has its own confirmed-action write endpoints and is
not part of this recent-activity response.
