# Google Antigravity support

AgentTelemetry reads locally stored Antigravity SQLite conversation databases as
the `antigravity` tool source. The reader uses Python's standard library and does
not sign in to Google, contact a provider, install or run hooks, or change
Antigravity files.

## Files AgentTelemetry reads

The supported conversation stores are direct `.db` files under these exact
directories in the user's home folder:

| Surface | Directory |
| --- | --- |
| Antigravity App | `~/.gemini/antigravity/conversations/` |
| Antigravity IDE | `~/.gemini/antigravity-ide/conversations/` |
| Antigravity CLI | `~/.gemini/antigravity-cli/conversations/` |

Each recognized database represents one conversation. Discovery does not use a
wildcard across similarly named Antigravity directories, descend into arbitrary
subdirectories, or scan backup locations. Standalone legacy `.pb` files are not
supported; their absence from the supported database scan does not mean those
older conversations were parsed.

The public [Antigravity hooks documentation](https://www.antigravity.google/docs/hooks)
describes hook configuration and a `transcriptPath` under the product's `brain/`
directory. AgentTelemetry does not install hooks or read hook configuration or
transcript files; this integration reads the supported SQLite conversation
databases only.

## Recorded usage and session detail

Usage reconciliation follows the recorded links between generation metadata
and step receipts. When distinct linked receipts reconcile with the generation's
recorded totals, AgentTelemetry keeps the receipts so per-request model and
timestamp detail is preserved. Otherwise it records the generation once and
skips its linked duplicate step statistics. Standalone step receipts are counted
once. Generation indexes and step indexes are different identities; they are
linked only through the recorded packed step-index mapping, never by matching
their numbers. Reasoning remains a recorded subtype of output and is not added
to token totals a second time.

Intent-only checkpoint usage is counted once through the linked generation or,
when no generation receipt is available, its step receipt. An unavailable
internal model remains `Unknown`. A checkpoint is listed as a compaction only
when it is completed, is not intent-only, and has no recorded summarization
failure; failed and incomplete checkpoints stay checkpoint events.

For each usage field the parser retains whether it was recorded, partial, or
unavailable. Missing cache or reasoning values are not inferred as zero. Usage
with an unknown model is still counted once, but an unverified model price is
not invented. Costs remain estimates at the configured API list prices.

On-demand session timelines read only the conversation resolved from a full
session ID already present in the local ledger and a path rediscovered by the
dashboard. The caller cannot supply a filename. Returned events are bounded and
contain recorded timestamps, event kinds, safe model/tool labels, and numeric
usage where present; prompts, tool arguments, raw source blobs, account data,
and paths are not returned. Date and event-view filters apply before the output
cap. Timeline detail never changes the ledger totals.

An `intent_only` checkpoint is surfaced as a checkpoint event and remains
outside the compaction list. A subagent marker and an explicit immediate
`parent_id` are required to link a parent; `root_id` alone does not imply an
immediate parent or turn an otherwise unmarked session into a subagent.

## Schema limits

The SQLite layout is reverse-engineered from local examples and can change as
Antigravity is updated. Unknown schemas remain unavailable and are reported as a
parse problem rather than silently treated as a successful empty parse. The
standalone legacy `.pb` format is not read, and no completeness claim is made
for it. Google may update the storage format independently of the public hooks
documentation.
