# Identifying subagent sessions

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
that session's total and are not added again. These figures describe the whole
session, independently of the selected date and model filters, and do not identify
which visible child rows belong to it. The existing usage calculations are
unchanged; the feature makes no new claim about cross-transcript deduplication.

No parser, ledger shape, cache version, source files, or sharing protocol changes
are needed. Test the interface using invented data only:

```bash
python3 tests/preview_subagents.py --port 8897
```
