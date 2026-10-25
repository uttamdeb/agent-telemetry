# Cursor support

AgentTelemetry reads supported Cursor session metadata from the local Cursor
SQLite store. It does not sign in to Cursor, contact a model provider, install
or run hooks, fetch shared transcripts, or change Cursor files. The reader uses
Python's standard library and opens the database read-only.

## Local stores and fields

The primary store is `Cursor/User/globalStorage/state.vscdb` under the user's
application-data directory (Application Support on macOS, `%APPDATA%` on
Windows, and `~/.config` on Linux). Cursor's SQLite schema is private and can
change between releases; an unrecognized shape is not treated as a successful
empty parse.

| Local source | Retained fields | Limits |
| --- | --- | --- |
| `composerHeaders` and `cursorDiskKV` `composerData:<composerId>` entries | From headers: `composerId`, `createdAt`, `lastUpdatedAt`, `isArchived`, `isSubagent`, `subagentTypeName`; saved `name`/title, `unifiedMode`, `totalLinesAdded`, `totalLinesRemoved`, and `numSubComposers`. From composer data: `createdAt`, `lastUpdatedAt`, `name`, `unifiedMode`, `modelConfig.modelName`, `modelConfig.maxMode`, `totalLinesAdded`, `totalLinesRemoved`, explicit `subagentComposerIds`, generic child-composer count from `subComposerIds`/`numSubComposers`, and project leaf hints from `workspaceIdentifier`/`trackedGitRepos`. Only `subagentComposerIds` create child identities or parent links; generic child-composer counts can include comparisons and do not create subagent links or counts. Workspace/project paths are reduced to display leaves. The saved model is stored separately as `current_model`. Context snapshots (`current_context_tokens`, `current_context_percent`, `current_context_limit`, `current_context_asof`) stay separate from usage. | The composer model setting can be current or last-selected. It is not used to label past requests or token rows. Context snapshots are not token usage or cost. Empty `usageData` does not produce a zero-usage receipt. |
| `cursorDiskKV` `bubbleId:<composerId>:<bubbleId>` entries | `type`, `createdAt`, `startedAtMs`, `completedAtMs`, and `turnDurationMs`; explicit assistant `modelInfo.modelName`; user-bubble `requested_model` as request metadata; `tokenCount.inputTokens` and `tokenCount.outputTokens` when recorded; `thinkingDurationMs` as time; safe `toolFormerData` name/status/`toolCallId`/`modelCallId`; and checkpoint flags. | A missing or all-zero placeholder token count is unavailable usage, not a measured zero. A user bubble's requested model is not proof of the model that answered. Cache read/write, reasoning tokens, response-output subtypes, and model-call counts are unavailable unless the source records them. `thinkingDurationMs` is not reasoning-token usage. |
| `agentKv` entries | A model name may fill a missing response model only when `providerOptions.cursor.requestId` joins one-to-one to an assistant bubble and has a concrete `cursor.modelName`. Tool-call metadata is joined only through explicit request IDs and exact `toolCallId` values, with duplicate calls removed. | These values are stored in blobs and do not all have a verified composer/request link. Unmatched, ambiguous, malformed, or newer values do not add usage or events. No usage is derived from blob order, row IDs, text, or context meters. |
| `workspaceStorage/*/workspace.json` and its `state.vscdb` `ItemTable` | `workspace.json.folder` maps to `composer.composerData` or `composer.composerHeaders` JSON entries in `allComposers[].composerId`. A unique ID association supplies a local project folder; only its leaf is retained as the project display name. | Unmapped composers remain without a workspace association. Ambiguous mappings are not guessed. Workspace databases are used for this association, not as an alternate transcript source. |
| `.cursor/projects/*/agent-transcripts` | Not used. | These files can contain conversation content and tool text but do not provide a verified model/token/timestamp usage record. They are not parsed or copied into AgentTelemetry. |
| `.cursor/ai-tracking/ai-code-tracking.db`; legacy `state.vscdb` `ItemTable` daily stats | The separate `ai-code-tracking.db` is not read. Legacy `aiCodeTracking.dailyStats*` values in `ItemTable` supply date-scoped Tab/Composer suggested and accepted line counts only. | Line counts do not identify a generated request, model, or tokens. Source file contents, hashes, commit messages, and scoring metadata are not used. |

AgentTelemetry keeps tool attribution as **Cursor**, even when an explicit
message model identifies another provider. Session titles, dates, tool labels,
workspace associations, and line counts are local metadata; prompt text, tool
arguments, raw database blobs, and account credentials are not returned by the
dashboard or session timeline.

## Coverage labels and model identity

Token coverage is recorded separately for each session and dated model bucket:

- **Recorded** means the local source supplied that usage field.
- **Partial** means some usage was recorded and some was missing or unavailable.
- **Unavailable** means the source did not supply usable values. Zero placeholders
  do not count as recorded usage.

Input/output, cache-read, cache-write, reasoning, and response-output fields keep
their own availability state. Missing cache values are not displayed as zero or
used to calculate a cache-hit rate. Missing tokens are not reconstructed from
message length, prompts, completion text, elapsed time, or context-window meters.
Cost values remain API-list estimates and are unavailable when the model or
verified price is unavailable; a missing rate does not mean the usage was free.

Historical model identity comes from explicit assistant-message metadata, or an
unambiguous request ID that joins to such a message. A `default` marker is not a
model name. If the source does not identify the model, usage stays under
`Unknown`; a saved current composer setting is never substituted. The dashboard's
**Exact tokens only** filter continues to omit Cursor because Cursor's local
usage coverage is incomplete.

## Subagents and event detail

A session is marked as a subagent only from Cursor's recorded subagent marker.
Parent links require an explicit composer ID in the parent's child list. Counts,
labels, or matching titles do not create a parent relationship or add tokens.

The session timeline resolves the full composer ID against a retained local
session and rediscovers its source path through the dashboard. Returned detail is
bounded to timestamps, safe event kinds and labels, explicit model names, and
numeric usage where available. It does not return prompts, message bodies, tool
arguments, paths, or raw database records. Missing event detail remains
unavailable or partial; it does not change usage totals.

Cursor's [hooks documentation](https://cursor.com/docs/hooks) describes
session, subagent, tool, and compaction lifecycle hooks. AgentTelemetry does not
install those hooks or read hook output. Cursor's
[shared transcripts](https://cursor.com/help/ai-features/shared-transcripts)
feature shares full conversation history through links; AgentTelemetry does not
fetch those links or access shared transcript content.

Cursor plans combine subscription usage and, on some plans, third-party API
usage. AgentTelemetry does not infer a Cursor subscription charge or account
balance from local messages. See Cursor's current
[Models & Pricing documentation](https://cursor.com/docs/models-and-pricing);
AgentTelemetry only estimates API-equivalent cost when its own model and price
data support that calculation.

## Retained history and undated receipts

When Cursor tombstones or deletes a previously read bubble, AT retains its safe
numeric/message metadata in the private ledger. Stable hashed event identities
prevent restored rows from counting twice. This retention begins once this reader
has observed the records; it cannot reconstruct rows deleted before capture.
No prompt text, tool arguments or raw request IDs are retained in this state.
A failed or unsupported database read leaves the prior ledger unchanged for retry.
Routine upgrades preserve compatible history; do not rebuild the ledger to upgrade.

A receipt without a recorded timestamp stays in a whole-session **undated usage**
supplement. It is never assigned to the session creation date or added to daily,
model/date-filtered or selected-range totals. Missing model/cache fields remain
unavailable and the supplement has no inferred cost. On-demand timelines use only
the still-readable source events; retained daily figures remain available when
source detail has been removed.
