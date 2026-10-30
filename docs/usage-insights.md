# Usage explanations and history tools

These features use local aggregates and source logs. They do not request account
information, contact a model, or introduce a new ledger schema.

## Comparison definition

Overview and Cost no longer show a "What changed?" card. The same contribution
maths (`usageChange` and `changeParts` in `static/insights.js`) remains the
reference for the MCP `compare_usage` tool ([mcp.md](mcp.md)), which is held in
parity with it. By default the comparison is the immediately preceding range with
the same number of calendar days. An unfinished window such as Today includes its
available observations; there is no elapsed-hours adjustment. All time has no
preceding range, so it needs an explicit baseline.

For category `g`, its contribution is `current[g] - previous[g]`. Categories partition
the records within one grouping, so the sum of contributions equals the difference
in total usage. Projects, models, tools, and token types are alternative groupings;
their contributions must never be added together. A zero net change may still
contain offsetting movements.

Contributions are signed absolute changes rather than percentages of the net change,
which can be zero or small despite large offsetting movements. It also avoids a
relative growth claim from a zero or absent previous observation. An empty previous
range means no recorded usage, not proof that no external usage occurred. The
[ONS guidance on percentage units](https://service-manual.ons.gov.uk/content/numbers/percentages)
distinguishes percentage changes from percentage-point differences; contributions
do not mix them. Contributions describe where the recorded difference lies and
are not evidence of a causal effect.

Token totals include input, output, cache reads, and cache writes; reasoning stays
within output. Cost parts use the record's dated pricing, including existing pricing
subsets. Unreconciled discounts/logged costs stay unsplit rather than being assigned
to token types by assumption. Costs remain API-equivalent estimates.

## Source timelines

The source reader resolves full native session identities through the local ledger
and discovered source paths. It streams up to the first 64 MiB, returns at most 400
events within the selected local dates, and explicitly marks truncation. Earlier
events still establish model context, fork replay boundaries, and usage-source
precedence even when they fall outside the displayed date range. A recent interval
after the byte limit can have no readable events; retained daily figures remain.

The drawer opens on **Activity**: recorded user turns, model/effort changes,
subagent lifecycle markers, available session/turn lifecycle events, and completed
compactions. **All events**, **Model requests**, **Tools**, and **Compactions** let
you narrow the list. The source reader applies this selector before the event cap,
so a long run of tool calls cannot hide agent activity. This display selector does
not change usage totals or the dashboard's shared filters.

Local Claude/Claude Desktop and Codex logs provide turn, request and tool activity.
Claude Agent/Task tool calls are labeled **Subagent requested**; a matched synchronous
tool result records completion or failure. Background tool returns are labeled as
returns, without claiming the worker has finished. Codex's explicit SubAgentActivity
markers provide starts, contacts, completions, failures and closes where recorded.
Effort/model changes use only recorded values, not current configuration.

Copilot SDK logs additionally supply explicit subagent starts/completions, model and
reasoning configuration changes, session starts/resumes/stops, interruptions and
recorded errors. Legacy Copilot storage still uses retained daily/compaction detail.
Successful SDK compactions include the recorded trigger and context sizes; source
and ledger twins are displayed once. Child compactions remain separate. Requests
use per-call saved usage receipts where available; missing receipts are not inferred
from the inclusive daily totals. Receipt replays, ephemeral twins and repeated tool
starts are deduplicated. Child request counts are breakdowns of the parent's usage.

Subagent numbers are local to the source log. They let you match a request/start to
its recorded result without exposing native identities, task descriptions or prompts.
They are not a reconstructed cross-session parent/child tree.

Claude response blocks and resumed UUIDs are deduplicated using the parser's rules.
Codex retains legacy token-count usage until the first own usage record, ignores its
later twins, and splits cached input from fresh input. Synthetic fixtures cross-check
request usage against the existing parser. The timeline is an investigation aid;
recorded events may be incomplete and are not additional billable usage. It returns
no prompt text, tool arguments, copied history, or account metadata. Subagent markers
do not establish a parent/child tree when the source lacks reliable identities.

## Backup and restore behavior

The ledger is durable history. Backups therefore preserve its exact bytes, including
archived entries, and live beside the configured ledger in `.usage-backups/`.
Rotation retains seven scheduled snapshots and five each of manual, pre-restore, and
corrupt-file safeguards. A scheduled snapshot preserves the saved ledger before its
first replacement each UTC day. A fresh installation creates its first snapshot
after its first successful save. Failed scheduled copies are reported while normal
saves continue; forced copies must succeed before update/cache-reset operations.

Restore previews validate the snapshot and bind one-use five-minute tickets to both
SHA-256 digests and the parser generation. A candidate edit, a changed current disk
ledger, or newly parsed usage requires a fresh preview. Confirmation saves the current
bytes first, then replaces the ledger and invalidates display/peer-export snapshots.
Writes flush and fsync a same-directory temporary file before
[`os.replace`](https://docs.python.org/3/library/os.html#os.replace). Local copies are
not drive-loss protection, do not restore original transcripts or preferences, and
are excluded from device exports and installation payloads. Corrupt safety snapshots
can be preserved for manual recovery but are not offered as in-app restore candidates.

## Diagnostics and verification

Diagnostics retain source counts, scan timestamps, and fixed error categories only.
Raw exceptions and absolute paths are discarded from that payload. Parser stderr
keeps its existing debugging behavior. Retained-only sources are distinguished from
current scans; a tool's lack of recent activity is not a freshness failure.

Development fixtures and regression tests use temporary ledgers. The mock preview
is `python tests/preview_features.py --port 8896`. Native packaging allowlists include
all three new Python modules. No cache rebuild, provider lookup, or installed-app
upgrade is needed to test these changes.
