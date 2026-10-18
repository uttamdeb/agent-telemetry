# Recorded reasoning effort

Open **Sessions** to see **Recorded effort** below a Claude or Codex model. Open
the session drawer for its levels and date/model associations. Date, model,
provider and device selection apply to this metadata as they do to the session
figures. The model remains the billed model; effort is a separate annotation.

- **Max**, **High**, and similar labels mean that setting was recorded.
- **Mixed** means more than one recognized setting appears in the selected scope.
- **Partially recorded** means some accepted usage has no usable setting. This
  includes Claude inline child usage whose own setting cannot identify the
  parent's effort.
- **Not recorded** means there is no usable evidence in that scope. It never
  means a default setting, disabled thinking, or zero reasoning tokens.
- Codex **None** is an explicitly recorded value. Claude has no equivalent effort
  value in this feature. Codex **Ultra** describes its delegation setting.

Effort controls are not comparable measures of intelligence, actual thinking
depth or reasoning-token consumption. They can affect response length, tool use
and other output. No new accounting metric or filter is introduced.

## Research and source evidence

Verified October 18, 2026, against official documentation and installed clients.
Local inspection read metadata only; it did not start a conversation, make a
model request, inspect credentials, or alter any tool's settings.

### Claude

The [effort documentation](https://platform.claude.com/docs/en/build-with-claude/effort)
describes `output_config.effort`, including low, medium, high, xhigh and max.
[Thinking controls](https://platform.claude.com/docs/en/build-with-claude/thinking)
and [legacy extended thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking)
are separate: a thinking-token budget cannot be converted into an effort label.
Claude Code's [model configuration](https://code.claude.com/docs/en/model-config)
describes user controls, but a current preference cannot establish a past request.

Installed Claude Code 2.1.300/2.1.301 request/transcript code records the validated
request effort on assistant records as `effort`; `perTurnEffort` records a turn
override. A valid nonnull override wins. A null or absent override falls back to
the base field; a malformed nonnull override makes the setting unavailable.
Recognized labels are stored only for accepted assistant usage, alongside the
same local date and canonical model. Streamed blocks share one observation and a
later block can correct missing metadata without adding another observation.
Inline sidechains do not supply the parent's setting. Standalone child logs read
their own fields. Old logs and records without these fields remain unavailable.

The [2.1.300 release notes](https://github.com/anthropics/claude-code/releases/tag/v2.1.300)
separately describe gzip for outgoing API requests and extra request information
in local transcripts. The inspected 2.1.301 session remained ordinary JSONL;
assistant model/usage fields were intact. New request records are not treated as
extra usage or used to retain raw request parameters. The local JSONL metadata
is implementation evidence, not a promised stable public schema.

### Codex

The [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents reasoning settings. The [app-server protocol](https://learn.chatgpt.com/docs/app-server)
supports turn-specific settings, so a thread can use several levels.
[Codex models](https://learn.chatgpt.com/docs/models?surface=app) also distinguish
delegation behavior from the API's [reasoning controls](https://developers.openai.com/api/docs/guides/reasoning).

Installed Codex 0.162.0-alpha.17.2 schemas and retained rollout metadata from
0.128–0.162 record the selected setting in top-level `turn_context.payload.effort`.
The parser recognizes none, minimal, low, medium, high, xhigh, max and ultra.
Low may be called Light in a client; xhigh is shown as Extra high here.
Unknown, null and missing values clear the active evidence rather than carrying
an older setting forward.

Modern accepted usage must match its recorded turn identity and model. Unmatched
compaction requests do not inherit the last visible turn's setting. Legacy usage
without turn identities uses only an available active recorded context. Fork
replay does not establish child-owned effort evidence. Context and response
identity memoization is bounded; an evicted context yields unavailable metadata.

Experimental in-flight settings updates can change a running turn. No verified
local persistence pattern was observed for every such update. The label means
**recorded turn context**, not proof that every internal request used that level.
Current configuration, parent settings, spawn arguments, nested fallback fields
and thinking-token counts are not substitutes for that evidence.

## Ledger, sharing and compatibility

Cache version 59 adds optional versioned metadata, bucketed by local date and
canonical model. Counts are deduplicated metadata observations, not a new request
count. Existing token, cost, message, call and time totals are unchanged.

Available Claude/Codex logs missing the metadata marker are reparsed
transactionally. Failed backfills keep the old aggregate and retry even if the
file signature is unchanged. Versions 53–58 retain every entry during migration;
deleted archives cannot be backfilled and remain available with missing labels.
**Do not rebuild the ledger.**

Safe session metadata follows an explicitly enabled device share, like other
session fields. Private bounded parser identity state, raw requests, settings
objects, prompts and thinking text are not added to that export. Peers with
versions 52–58 remain readable. Other tools have no effort annotation until their
source format has been verified.
