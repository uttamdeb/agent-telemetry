# Usage explanations and history tools

These features use local aggregates and source logs. They do not request account
information, contact a model, or introduce a new ledger schema.

## Comparison definition

Overview follows the global metric; Cost always compares estimated cost. The
comparison is the immediately preceding range with the same number of calendar
days, matching the existing hero comparison. Today and other unfinished windows
include their available observations and say the current period is in progress.
This is not an elapsed-hours adjustment. All time has no preceding comparison.

For category `g`, its contribution is `current[g] - previous[g]`. Categories partition
the records within one grouping, so the sum of contributions equals the difference
in total usage. Projects, models, tools, and token types are alternative groupings;
their contributions must never be added together. The first five largest absolute
movements are shown, with the remaining categories reconciled into Other and a full
table available. A zero net change may still contain offsetting movements.

The view uses signed absolute changes rather than percentages of the net change,
which can be zero or small despite large offsetting movements. It also avoids a
relative growth claim from a zero or absent previous observation. An empty previous
range means no recorded usage, not proof that no external usage occurred. The
[ONS guidance on percentage units](https://service-manual.ons.gov.uk/content/numbers/percentages)
distinguishes percentage changes from percentage-point differences; this feature
does not mix them. Contributions describe where the recorded difference lies and
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
