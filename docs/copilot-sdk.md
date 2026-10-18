# Copilot SDK data coverage

Verified against GitHub/Microsoft documentation, source and local records on
2026-10-18. The newer Copilot harness in VS Code uses the SDK/CLI runtime; its
records are separate from older extension-host Copilot Chat files. All usage is
attributed to Copilot, including custom endpoint models.

## Read-only sources

1. `~/.copilot/session-state/<native-id>/events.jsonl`: messages, actual model ids,
   timestamps, tool calls, errors, interruptions and successful compactions.
   Full prompts, reasoning text, tool arguments/results, summaries and credentials
   are not copied into the ledger. The usual first-prompt session title is retained.
2. `<editor-data>/agentSessionData/<host-id>/session.db`: the native SDK id in
   `session_metadata.defaultChatProviderData`, title/branch metadata, and per-turn
   `turn_usage`. `turns.event_id` links each usage row to its actual SDK user event.
   SQLite and WAL are read consistently in a read-only transaction. Database-only
   changes trigger an update even when `events.jsonl` has not grown.
3. Persisted `session.usage_record.data.usage` receipts in newer runtimes, and
   saved `assistant.usage` / `session.shutdown` events when present. Live usage
   events normally are ephemeral. Cumulative `session.usage_checkpoint` snapshots
   are not additional usage and are not summed with receipts.

Only explicit ids connect records. Session titles, prompt text and nearby times
are never used to guess a match. Nested tool-output/peer-chat database copies are
excluded. Other editors/platforms use the same portable editor-root discovery.

## Accounting

Per-call receipts use their recorded model, token counts and timestamp. API call
identities deduplicate receipt replays and live-event twins, even when a receipt
precedes its assistant message. Direct user/assistant `turnId` matches re-anchor
stale `originatingMessageId` values; reused user turn counters reset that mapping
on resume. Queued prompts retain explicit native associations.

VS Code's `_meta.turnTokenTotals` is the whole turn's per-model usage, including
subagents. Top-level `inputTokens`/`outputTokens` can describe only its last call.
`directTurnTokenTotals`, context-window sizes and subagent totals must not be
added to the inclusive totals. Cached input is separated from ordinary input;
reasoning remains a subset of output. Missing breakdown fields stay unavailable
in the session detail. Exact-only figures include activity from covered turns.
Complete dated receipts take precedence only when they cover the observed calls
and every model/token category in the saved host snapshot. Otherwise the host
snapshot is used once; receipts are not added on top. A turn with no associated
reply can still use its explicit DB/user-event join when the entire stream is on
one local day. Undated multi-day totals remain excluded.

Successful compactions retain timestamps, recorded context sizes and matched
duration. Trigger labels include automatic context threshold, context-limit retry,
memory pressure, model switch and manual. In-progress/failed compactions do not
increase the completed count. `compactionTokensUsed` supplies actual request
usage, separate from context sizes; inclusive host totals already contain it.
Between-turn compaction usage is added separately. Compaction usage-event twins
are excluded in favor of completion usage, and compaction tokens alone never
prove that a conversation turn has complete usage.

SDK shutdown metrics are cumulative: they replace/enrich recorded usage, rather
than being added to it. Stale snapshots cannot overwrite later recorded turns;
a snapshot reporting fewer calls cannot claim complete history. Only single-day
session totals can enter daily analytics.
Multi-day totals lacking call dates appear as supplemental session-wide figures;
they are not assigned wholesale to the shutdown day. Missing usage and unsaved
failed calls cannot be reconstructed from prompt lengths. Recorded errors do not
create assistant replies.

The session warning tooltip explains unavailable/partial coverage and recorded
errors or interruptions. Unknown usage displays a dash or “Unavailable,” rather
than a known zero. Partial figures are lower bounds; native/web summaries include
recorded counts only. The SDK's premium-request multipliers/nano-AI units are not
USD. API-equivalent estimates remain separate from actual billing.

GLM 5.3 uses Cloudflare Workers AI list rates verified on 2026-10-15:
$1.40/M input, $4.40/M output and $0.26/M cached input. Other hosts or actual
account billing can differ. OpenCode's own logged per-message costs still take
precedence in its existing parser.

SDK snapshots contain only normalized numeric/model metadata. If an auxiliary
database disappears, already recovered turn usage survives in the durable ledger.
Version 58 retains all existing version 53–57 entries and older archived history.
SDK parser revision 3 reparses available SDK logs even when their file signatures
are unchanged. Cache 61 retains existing v53–60 entries, including deleted archives;
failed reparses preserve the previous entry for retry. The recorded parent cwd
is private workspace-attribution metadata and remains excluded from peer exports.
Explicit `subagent.started` agent IDs identify named child breakdowns. Per-agent
receipts are deduplicated, and successful child compactions retain their own timing.
Child compaction request usage enters the inclusive parent accounting once.
Agent desk projects these children with recorded parent links; child rows never
increase summary tokens or estimated cost. Missing child receipts remain unavailable,
and incomplete receipt coverage is partial. Recorded lifecycle events do not prove
that a worker is still running. Dashboard session details list the recorded identities.
No rebuild is needed. Parsing never resumes a session, starts a model turn, reads
credential stores or changes a user's telemetry settings.

## What is not automatically collected

The SDK also has experimental `session.usage.getMetrics` and context-info RPCs.
Those require a runtime connection; they are not an offline historical store.
AgentTelemetry does not attach to or resume a live session to query them.
VS Code can optionally export OTel traces to a local database or another sink.
AgentTelemetry does not enable that setting or an external exporter automatically.
Missing data remains explicitly incomplete until a supported saved record exists.

## Primary references

- [GitHub SDK usage and billing](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/usage-and-billing)
- [SDK event fields and ephemeral events](https://docs.github.com/en/copilot/how-tos/copilot-sdk/features/streaming-events)
- [Generated SDK event schema](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts)
- [VS Code Copilot Agent Host implementation](https://github.com/microsoft/vscode/blob/main/src/vs/platform/agentHost/node/copilot/copilotAgentSession.ts)
- [VS Code session data interface](https://github.com/microsoft/vscode/blob/main/src/vs/platform/agentHost/common/sessionDataService.ts)
- [VS Code OTel architecture and opt-in storage](https://github.com/microsoft/vscode/blob/main/src/vs/platform/agentHost/OTEL.md)
- [GitHub's October 6 SDK/IDE metrics announcement](https://github.blog/changelog/2026-10-06-update-your-ide-to-restore-agent-activity-in-copilot-usage-metrics/)
- [Cloudflare GLM 5.3 rates](https://developers.cloudflare.com/workers-ai/models/glm-5.3/)

Regression fixtures: `python3 -m unittest tests.test_copilot_sdk -v` and
`node tests/test_frontend.js`. Fixtures contain invented records only.
