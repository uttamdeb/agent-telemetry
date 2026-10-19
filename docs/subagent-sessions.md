# Identifying subagent sessions

## Copilot SDK child breakdowns

The browser Sessions view includes named children recorded by Copilot SDK
`subagent.started` events. These appear in **All sessions** and **Subagents**, with
their recorded parent link and dated token/cost breakdown. Parent delegation badges
remain whole-session evidence, while visible child counts follow the current date,
tool, project, model/provider, editor, device and search filters. A lifecycle marker
without saved usage can still show a child on its recorded activity dates; idle days
between start and end are not inferred. Missing child usage stays unavailable.

Copilot's parent total already includes child usage. The web payload keeps these
rows separately in `session_children`; only the Sessions view combines them with
ordinary sessions. They never enter shared analytics slices, Optimize, MCP session
totals, project counts or daily usage a second time. The drawer explains the overlap
and links to the parent for detailed source activity. Prompt/reply/tool-call counts
and active time are not reconstructed from token receipts. Child names remain
read-only, using full parent/agent identities for selection. No ledger change or
rebuild is needed; older clients ignore the optional display rows.

## Labels and filters

The Sessions view marks recognized child transcripts with a **Subagent** label,
branching icon, and session identifier. Search accepts the displayed identifier or
the full native session ID as well as session titles. Open a session's title with
the mouse or keyboard to see its classification and recorded delegation evidence.

The Sessions-only selector shows **All sessions**, **Main agents**, and **Subagents**
in that order. Counts reflect the currently loaded sessions after the dashboard's
date, tool, project, model, device, precision, and search filters. The selector is
saved as `?session_type=subagents` or `?session_type=other`; All is the default.
The existing `other` URL value continues to select Main agents for compatibility
with saved links.
It changes the session list, leaving Overview, Cost, and other analytics totals
unchanged. Project and comparison drilldowns reset the selector to All so it
cannot hide the sessions behind a chart or comparison.

**Main agents** shows sessions not identified as subagents. Some tools and older
retained or shared entries do not carry that marker, so it does not mean that
delegation never happened. An archived or remote session keeps a subagent label
when its retained data includes the marker. The UI does not infer a parent-child
tree from matching titles, projects, dates, or models.

Recorded delegation is separate from child-session classification. Codex and
some other tools record a count on the session that delegated work. Claude can
record sidechain tokens inside a parent session; these tokens are already part of
that session's total and are not added again.

Claude parent sessions also show a count when retained child transcripts use its
recognized source directory: `<parent UUID>/subagents/agent-<full ID>.jsonl`, or
`<parent UUID>/subagents/workflows/wf_<workflow ID>/agent-<full ID>.jsonl`. The
child must already be marked as a Claude subagent. The full parent UUID, project
directory, and source device must agree. A child's full relative transcript path
counts once; workflow directories are part of that identity because names such as
`agent-a0` can be reused by different workflows. Duplicate retained copies of the
same relative transcript do not add another count. Archived entries and shared-device copies keep
this evidence, including copied Windows paths. Unknown layouts are left unlinked.

This is a lower bound on **recorded child transcripts**, not a live worker count.
It covers the whole session, independently of the selected date or model filters.
Missing evidence is not shown as zero. Child transcripts, recorded spawn counts,
and inline sidechain usage remain separate evidence and are never added together.
Children keep their own usage rows; the parent badge adds no tokens or cost and
does not imply that a child's usage is included in its parent's figures. These
associations use retained source paths without rereading logs or guessing from
matching titles, projects, dates, or models. The existing usage calculations are
unchanged; the feature makes no new claim about cross-transcript deduplication.
When exactly one retained parent matches that full source identity, a child also
carries its parent's full native ID, current title, tool, and device as display
metadata. Missing or ambiguous parents stay unresolved; no source path is exposed
by that descriptor.

The child drawer shows the resolved parent's name and an **Open parent** action.
It opens that session's detail without changing dashboard filters; the figures
still follow the selected date and model filters. A parent outside that scope is
labelled accordingly. Missing or ambiguous identities remain unavailable.

Codex links use the explicit `session_meta.parent_thread_id`, or the documented
`source.subagent.thread_spawn.parent_thread_id` form. `session_id` can identify
the root of a multi-agent tree, and `forked_from_id` describes copied history;
neither is used as an immediate-parent substitute. See the
[upstream Codex protocol](https://github.com/openai/codex/blob/main/codex-rs/protocol/src/protocol.rs).

Cache version 60 retains all entries from versions 53–59. Available Codex logs
missing parent metadata are reparsed transactionally; archived entries remain
intact, with unresolved relationships left unknown. Usage calculations and source
files do not change, and no rebuild is needed. Test with invented data only:

```bash
python3 tests/preview_subagents.py --port 8897
```

## Local Claude subagent display names

Claude child transcripts use a parent session UUID plus a separate agent ID.
The supported layouts are `<parent-uuid>/subagents/agent-<id>.jsonl` and
`<parent-uuid>/subagents/workflows/wf_<id>/agent-<id>.jsonl`. The full identity
includes the parent and any workflow path, so reused child IDs do not select a
different parent's transcript. Ambiguous discovered identities are refused.

The pencil and detail action offer **Name in AgentTelemetry** for a recognized
local child. The same preview and confirmation flow makes clear that only its
AgentTelemetry display name changes. Claude's transcript, native subagent name,
and parent session stay unchanged; no model request or native title write runs.
The source title remains visible in the drawer and searchable. Entering that
source title again clears the local override.

Display names persist privately in `AgentTelemetryNative/session-display-names.json`,
separately from the ledger, peer exports, health, and native summaries. Remote,
missing, unknown-layout, or ambiguous source transcripts cannot be named through
this action. Writes are locked and atomic; unreadable or corrupt private metadata
is preserved and blocks a new save. No cache rebuild or version change is needed.

Claude's [subagent documentation](https://code.claude.com/docs/en/sub-agents#subagent-names)
describes names assigned by the Agent tool when spawning, and its
[resume documentation](https://code.claude.com/docs/en/sub-agents#resume-subagents)
describes the parent-scoped transcript layout. These sources do not establish a
safe native child-title rename operation, so AgentTelemetry does not append a
parent-style `custom-title` record to a child transcript. Codex continues to use
its verified native thread-name API; ordinary supported Claude sessions retain
their existing original-tool rename behavior.
