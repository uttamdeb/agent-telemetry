# Cursor and Antigravity: local coverage review

Reviewed 2026-10-25 against CodeBurn `main` revision
`1a3ce74af8842d1d3946d5329a0d93e497d61583`. CodeBurn's own documentation can
lag its implementation; this review reads the provider source as well as docs.
The comparison informed independent Python stdlib implementation and tests.
It does not execute CodeBurn or read its credentials/cache.

Primary references:

- [Cursor reader](https://github.com/getagentseal/codeburn/blob/1a3ce74af8842d1d3946d5329a0d93e497d61583/src/providers/cursor.ts)
- [Cursor import and sync documentation](https://github.com/getagentseal/codeburn/blob/1a3ce74af8842d1d3946d5329a0d93e497d61583/docs/providers/cursor.md)
- [Antigravity reader](https://github.com/getagentseal/codeburn/blob/1a3ce74af8842d1d3946d5329a0d93e497d61583/src/providers/antigravity.ts)
- [Cursor hooks](https://cursor.com/docs/hooks)

## Cursor findings and decisions

| Evidence in CodeBurn | AgentTelemetry decision |
| --- | --- |
| `bubbleId` rows expose per-message timestamps and `modelInfo`; newer bubbles have zero token placeholders. | Keep recorded bubble usage and metadata. Missing usage is unavailable, never proof of zero cost. A current composer setting is separate from historical model attribution. |
| Workspace folder URIs and composer lists live in sibling `workspaceStorage` databases. | Resolve explicit session-to-workspace links read-only, retain cwd privately and derive the displayed project. Ambiguous associations remain unknown. |
| JSON `agentKv` rows can contain `providerOptions.cursor.requestId` and `modelName`. | Use explicit, unambiguous request links only. Unlinked blobs and binary data do not create additional usage. Tool-call IDs must deduplicate corresponding bubble evidence. |
| The agent stream also contains message text, system context and tool arguments. | Never cache/export these contents. No public fixtures from real conversations. |
| `promptTokenBreakdown.totalUsedTokens` and `contextTokensUsed` are context snapshots. | Keep any supported numeric context metadata separate from consumed/billed token counters, daily usage and cost. |
| Local usage falls back to text length / 4 and context-meter estimates. | Do not adopt this fallback. It cannot reconstruct repeated input, cache usage or billed model calls. |
| Unjoined agent stream calls use the database modification time as a timestamp. | Do not invent activity dates from database mtime or arbitrary row adjacency. |
| Authenticated CSV sync supplements local records with account-wide billed events. | Not part of this local reader. AT does not extract Cursor login tokens or start automatic provider requests. A future user-confirmed billing import would need its own account, scope, deduplication and attribution design. |
| Local reads use bounded scans and WAL-aware signatures. | Use bounded read-only snapshots, transactional replacement and WAL-aware refresh. A failed parse retains the previous ledger for retry. |

Cursor's own hooks expose additional lifecycle/subagent/compaction observations,
but require configuring future event capture. Reading existing logs is not an
installation of hooks. This change does not silently modify user/project hook
configuration, enable cloud sharing, or call inference/provider APIs.

## Antigravity findings and decisions

Both implementations read `gen_metadata` and `steps` SQLite records, decode
usage fields and use the generation's explicit packed step-index mapping.
AT already preserves cache/reasoning availability, recorded model/effort detail,
subagent evidence, completed compactions and bounded on-demand timelines. Its
existing linked-receipt reconciliation must remain intact.

CodeBurn also discovers standalone `.pb` files under `implicit`, and can use a
running language-server RPC endpoint or an opt-in CLI status-line hook. AT's
supported direct SQLite path does not require a running agent. The legacy-file
inventory now includes direct `implicit/*.pb` alongside `conversations/*.pb` in
the known Antigravity roots, without opening their contents. Diagnostics explicitly
report these files as unsupported; they contribute no invented sessions or usage.

AT does not copy process-token extraction, local RPC probing, hook installation,
placeholder-model lookup tables, or filesystem-time attribution. These are
separate access/accuracy choices and are not evidence that local SQLite usage is
missing. Unknown models stay unknown and unpriced. Sleeping/stopped-agent
behavior remains governed by the available source files and retained ledger.

## Remaining limits

This is coverage of verified local metadata, not a claim to recover every hidden
Cursor/Antigravity internal field. Unknown binary schemas, omitted usage, ambiguous
request links and missing dates remain explicit limitations. The actual application
billing dashboard may contain requests/cache usage that never reached local logs.
See [Cursor support](cursor.md) and [Antigravity support](antigravity.md).
