"""Recorded reasoning settings enrich sessions without changing accounting."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import parser as P


class ReasoningEffortTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-effort-")
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "fixture.jsonl"
        item = patch.object(P, "_codex_thread_names", return_value={})
        item.start(); self.addCleanup(item.stop)

    def claude(self, number, response=None, timestamp="2026-10-12T12:00:00Z",
               model="claude-sonnet-4-6", usage=None, **fields):
        return json.dumps({"type": "assistant", "uuid": f"{number:012x}",
            "timestamp": timestamp, "requestId": response or f"request-{number}",
            "message": {"id": response or f"response-{number}", "model": model,
                "content": [], "usage": usage or {"input_tokens": 100, "output_tokens": 10}},
            **fields}) + "\n"

    def codex(self, kind, payload, timestamp="2026-10-12T12:00:00Z"):
        return json.dumps({"type": kind, "timestamp": timestamp, "payload": payload}) + "\n"

    def context(self, turn="turn-one", model="gpt-6.1-sol", **fields):
        return self.codex("turn_context", {"turn_id": turn, "model": model, **fields})

    def usage(self, response="response-one", turn="turn-one", **fields):
        return self.codex("token_usage_record", {"response_id": response, "turn_id": turn,
            "usage": {"input_tokens": 100, "cached_input_tokens": 20,
                      "output_tokens": 10, "reasoning_output_tokens": 4}, **fields})

    def token_count(self, total=100):
        return self.codex("event_msg", {"type": "token_count", "info": {
            "last_token_usage": {"input_tokens": 100, "output_tokens": 10},
            "total_token_usage": {"input_tokens": total, "output_tokens": total // 10}}})

    def parse(self, source, lines, path=None):
        path = path or self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(lines))
        return P.update_file(None, source, str(path), None, {})

    def row(self, agg, model=None, date=None):
        rows = agg["reasoning_effort"]["days"]
        matches = [row for key, row in rows.items()
                   if (model is None or key.endswith("\t" + model))
                   and (date is None or key.startswith(date + "\t"))]
        self.assertEqual(len(matches), 1, rows)
        return matches[0]

    def day(self, timestamp):
        return P._buckets(P._from_iso(timestamp))[0]

    def test_claude_base_override_fallback_and_invalid_types(self):
        cases = [({"effort": "max"}, "max"),
                 ({"effort": "low", "perTurnEffort": "xhigh"}, "xhigh"),
                 ({"effort": "high", "perTurnEffort": None}, "high"),
                 ({"effort": "medium"}, "medium"),
                 ({}, None), ({"effort": None}, None),
                 ({"effort": "HIGH"}, None), ({"effort": False}, None),
                 ({"effort": {"value": "high"}}, None),
                 ({"effort": "high", "perTurnEffort": "unsupported"}, None),
                 ({"effort": "high", "perTurnEffort": []}, None),
                 ({"effort": "none"}, None), ({"effort": "ultra"}, None)]
        for fields, expected in cases:
            with self.subTest(fields=fields):
                agg = self.parse("claude", [self.claude(1, **fields)])
                self.assertEqual(self.row(agg), {"levels": {expected: 1} if expected else {},
                                               "unrecorded": int(expected is None)})

    def test_claude_does_not_read_effort_from_nested_message_or_thinking(self):
        line = json.loads(self.claude(1, thinkingMetadata={"effort": "max", "budget": 99999}))
        line["message"]["effort"] = "max"
        line["message"]["usage"]["output_tokens_details"] = {"thinking_tokens": 9}
        agg = self.parse("claude", [json.dumps(line) + "\n"])
        self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})
        self.assertEqual(agg["totals"]["reason"], 9)

    def test_claude_streamed_metadata_corrects_original_day_without_recount(self):
        first = self.claude(1, response="stream", timestamp="2026-10-11T12:00:00Z")
        later = self.claude(2, response="stream", timestamp="2026-10-12T12:00:00Z", effort="max",
                            usage={"input_tokens": 100, "output_tokens": 30})
        agg = self.parse("claude", [first])
        self.assertEqual(self.row(agg)["unrecorded"], 1)
        with self.path.open("a") as handle: handle.write(later)
        agg = P.update_file(json.loads(json.dumps(agg)), "claude", str(self.path), None, {})
        self.assertEqual(agg["reasoning_effort"]["days"], {
            self.day("2026-10-11T12:00:00Z") + "\tClaude Sonnet 4.6":
                {"levels": {"max": 1}, "unrecorded": 0}})
        self.assertEqual(agg["totals"]["asst"], 1)
        self.assertEqual(agg["totals"]["out"], 30)
        self.assertEqual(agg, P.update_file(None, "claude", str(self.path), None, {}))

    def test_missing_later_block_keeps_known_value_and_malformed_override_clears_it(self):
        agg = self.parse("claude", [self.claude(1, response="stream", effort="max"),
            self.claude(2, response="stream")])
        self.assertEqual(self.row(agg), {"levels": {"max": 1}, "unrecorded": 0})
        P.parse_claude(agg, [self.claude(3, response="stream", effort="high", perTurnEffort=False)])
        self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})

    def test_claude_uuid_and_response_dedup_are_preserved(self):
        first = self.claude(1, response="one", effort="high")
        agg = self.parse("claude", [first, first, self.claude(2, response="one", effort="high")])
        self.assertEqual(self.row(agg), {"levels": {"high": 1}, "unrecorded": 0})
        before = copy.deepcopy(agg)
        P.parse_claude(agg, [first])
        self.assertEqual(agg, before)

    def test_claude_inline_child_is_unrecorded_but_standalone_child_uses_own_value(self):
        lines = [self.claude(1, effort="max"), self.claude(2, effort="high", isSidechain=True)]
        parent = self.parse("claude", lines)
        self.assertEqual(self.row(parent), {"levels": {"max": 1}, "unrecorded": 1})
        child_path = self.path.parent / "fixture" / "subagents" / "agent-fixture.jsonl"
        child = self.parse("claude", lines[1:], child_path)
        self.assertTrue(child["subagent"])
        self.assertEqual(self.row(child), {"levels": {"high": 1}, "unrecorded": 0})

    def test_claude_desktop_uses_same_defensive_metadata_fields(self):
        agg = self.parse("claude-desktop", [self.claude(1, effort="low"), self.claude(2)])
        self.assertEqual(self.row(agg), {"levels": {"low": 1}, "unrecorded": 1})

    def test_claude_synthetic_and_user_records_add_no_observations(self):
        agg = self.parse("claude", [self.claude(1, model="<synthetic>", effort="max"),
            json.dumps({"type": "user", "effort": "max", "timestamp": "2026-10-12T12:00:00Z",
                        "message": {"content": "Fixture prompt"}})])
        self.assertEqual(agg["reasoning_effort"]["days"], {})

    def test_mixed_dates_models_and_fast_price_identity_are_retained(self):
        agg = self.parse("claude", [self.claude(1, effort="low"),
            self.claude(2, effort="max", model="claude-opus-4-6"),
            self.claude(3, effort="high", timestamp="2026-10-13T12:00:00Z"),
            self.claude(4, effort="medium", usage={"input_tokens": 10, "speed": "fast"})])
        self.assertEqual(len(agg["reasoning_effort"]["days"]), 4)
        self.assertTrue(set(agg["reasoning_effort"]["days"]) <= set(agg["records"]))
        self.assertEqual(self.row(agg, "Claude Sonnet 4.6" + P.FAST_SUFFIX)["levels"], {"medium": 1})

    def test_codex_recognizes_all_levels_and_clears_missing_null_invalid_context(self):
        for level in ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            with self.subTest(level=level):
                agg = self.parse("codex", [self.context(effort=level), self.usage()])
                self.assertEqual(self.row(agg), {"levels": {level: 1}, "unrecorded": 0})
        for fields in ({}, {"effort": None}, {"effort": False}, {"effort": "HIGH"}, {"effort": []}):
            with self.subTest(fields=fields):
                agg = self.parse("codex", [self.context(effort="max"),
                    self.context(turn="turn-two", **fields), self.usage(turn="turn-two")])
                self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})

    def test_codex_ignores_thread_settings_and_collaboration_fallback(self):
        agg = self.parse("codex", [self.context(collaboration_mode={"settings": {"reasoning_effort": "max"}}),
            self.codex("event_msg", {"type": "thread_settings_applied", "thread_settings": {"reasoning_effort": "max"}}),
            self.usage()])
        self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})

    def test_codex_modern_usage_requires_turn_and_accounting_model_match(self):
        agg = self.parse("codex", [self.context(effort="max"),
            self.context(turn="turn-two", model="gpt-6-luna", effort="low"),
            self.usage(turn="turn-one"), self.usage(response="other", turn="turn-two")])
        self.assertEqual(self.row(agg), {"levels": {"low": 1}, "unrecorded": 1})
        self.assertEqual(set(agg["reasoning_effort"]["days"]), set(agg["records"]))

    def test_codex_unmatched_compaction_usage_has_no_invented_effort(self):
        agg = self.parse("codex", [self.context(effort="high"), self.usage(turn="compaction-turn"),
            self.codex("compacted", {"compaction_response_id": "response-one"})])
        self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})
        self.assertEqual(agg["totals"]["req"], 1)

    def test_codex_usage_response_observations_dedup_and_can_enrich_unknown(self):
        agg = self.parse("codex", [self.usage(), self.context(effort="max"), self.usage(), self.usage()])
        self.assertEqual(self.row(agg), {"levels": {"max": 1}, "unrecorded": 0})
        # Accounting keeps its established source behavior; metadata adds no usage.
        self.assertEqual(agg["totals"]["req"], 3)

    def test_codex_missing_or_invalid_response_identity_does_not_collapse_observations(self):
        for response in ("", None, False, "x" * 513):
            with self.subTest(response=response):
                agg = self.parse("codex", [self.context(effort="high"), self.usage(response=response),
                    self.context(turn="turn-two", effort="max"), self.usage(response=response, turn="turn-two")])
                self.assertEqual(self.row(agg), {"levels": {"high": 1, "max": 1}, "unrecorded": 0})

    def test_escaped_lone_surrogates_in_source_identities_do_not_break_parsing(self):
        line = json.loads(self.claude(1, effort="max"))
        line["message"]["id"] = "malformed-\ud800"
        line["requestId"] = "malformed-\udfff"
        claude = self.parse("claude", [json.dumps(line) + "\n"])
        self.assertEqual(self.row(claude), {"levels": {"max": 1}, "unrecorded": 0})
        codex = self.parse("codex", [self.context(turn="malformed-\ud800", effort="high"),
            self.usage(response="malformed-\udfff", turn="malformed-\ud800")])
        self.assertEqual(self.row(codex), {"levels": {"high": 1}, "unrecorded": 0})

    def test_uncorrelatable_claude_identity_does_not_recount_streamed_blocks(self):
        response = "x" * 513
        agg = self.parse("claude", [self.claude(1, response=response, effort="high"),
                                    self.claude(2, response=response, effort="high")])
        self.assertEqual(self.row(agg), {"levels": {"high": 1}, "unrecorded": 0})

    def test_codex_legacy_token_count_dedup_and_modern_handover(self):
        legacy = [self.context(effort="high"), self.token_count(), self.token_count()]
        agg = self.parse("codex", legacy)
        self.assertEqual(self.row(agg), {"levels": {"high": 1}, "unrecorded": 0})
        self.assertEqual(agg["totals"]["req"], 1)
        modern = [self.context(effort="max"), self.usage(), self.token_count(), self.token_count(200)]
        agg = self.parse("codex", modern)
        self.assertEqual(self.row(agg), {"levels": {"max": 1}, "unrecorded": 0})
        self.assertEqual(agg["totals"]["req"], 1)

    def test_codex_fork_replay_cannot_count_parent_metadata(self):
        lines = [self.codex("session_meta", {"id": "child", "forked_from_id": "parent", "thread_source": "subagent"}),
            self.codex("turn_context", {"turn_id": "parent-turn", "model": "gpt-6.1-sol", "effort": "max"},
                       "2026-10-12T12:00:00.100Z"),
            self.codex("token_usage_record", {"turn_id": "parent-turn", "response_id": "parent-response",
                "usage": {"input_tokens": 900}}, "2026-10-12T12:00:00.200Z"),
            self.codex("turn_context", {"turn_id": "child-turn", "model": "gpt-6.1-sol", "effort": "low"},
                       "2026-10-12T12:00:04Z"),
            self.codex("token_usage_record", {"turn_id": "child-turn", "response_id": "child-response",
                "usage": {"input_tokens": 100}}, "2026-10-12T12:00:05Z")]
        agg = self.parse("codex", lines)
        self.assertTrue(agg["subagent"])
        self.assertEqual(self.row(agg), {"levels": {"low": 1}, "unrecorded": 0})
        self.assertEqual(agg["totals"]["in"], 100)

    def test_codex_inherited_context_is_not_child_owned_effort(self):
        agg = self.parse("codex", [
            self.codex("session_meta", {"forked_from_id": "parent"}),
            self.codex("turn_context", {"model": "gpt-6.1-sol", "effort": "max"},
                       "2026-10-12T12:00:00.100Z"),
            self.codex("event_msg", {"type": "token_count", "info": {
                "last_token_usage": {"input_tokens": 100}}}, "2026-10-12T12:00:04Z")])
        self.assertEqual(self.row(agg), {"levels": {}, "unrecorded": 1})

    def test_incremental_cache_roundtrip_matches_full_codex_parse(self):
        lines = [self.context(effort="max"), self.usage(),
                 self.context(turn="turn-two", effort="low"), self.usage(response="two", turn="turn-two")]
        agg = self.parse("codex", lines[:2])
        with self.path.open("a") as handle: handle.write("".join(lines[2:]))
        resumed = P.update_file(json.loads(json.dumps(agg)), "codex", str(self.path), None, {})
        self.assertEqual(resumed, P.update_file(None, "codex", str(self.path), None, {}))
        self.assertEqual(self.row(resumed), {"levels": {"max": 1, "low": 1}, "unrecorded": 0})

    def test_private_correlation_state_is_bounded_and_not_in_session_metadata(self):
        claude = self.parse("claude", [self.claude(n, effort="max") for n in range(50)])
        self.assertEqual(len(claude["state"]["claude_effort_responses"]), 8)
        lines = []
        for n in range(70):
            lines.extend([self.context(turn=f"private-turn-{n}", effort="max"),
                          self.usage(response=f"private-response-{n}", turn=f"private-turn-{n}")])
        codex = self.parse("codex", lines)
        self.assertEqual(len(codex["state"]["codex_effort_contexts"]), 32)
        self.assertEqual(len(codex["state"]["codex_effort_responses"]), 32)
        public = codex["sessions"][0]["reasoning_effort"]
        self.assertEqual(public, codex["reasoning_effort"])
        self.assertNotIn("private-", json.dumps(public))
        public["days"].clear()
        self.assertTrue(codex["reasoning_effort"]["days"])

    def test_supported_empty_and_context_only_files_have_empty_metadata_without_usage(self):
        for source in ("claude", "claude-desktop", "codex"):
            with self.subTest(source=source):
                agg = self.parse(source, [])
                self.assertEqual(agg["reasoning_effort_version"], 1)
                self.assertEqual(agg["reasoning_effort"], {"version": 1, "days": {}})
        agg = self.parse("codex", [self.context(effort="max")])
        self.assertEqual(agg["records"], {})
        self.assertEqual(agg["reasoning_effort"]["days"], {})
        self.assertEqual(sum(agg["totals"].values()), 0)

    def test_metadata_backfill_precedes_unchanged_return_and_failure_preserves_entry(self):
        for source, lines in (("claude", [self.claude(1, effort="high")]),
                              ("codex", [self.context(effort="high"), self.usage()])):
            with self.subTest(source=source):
                old = self.parse(source, lines)
                old.pop("reasoning_effort_version"); old.pop("reasoning_effort")
                old["sessions"][0].pop("reasoning_effort")
                before = copy.deepcopy(old)
                with patch.object(P, "parse_" + source, side_effect=ValueError("fixture failure")):
                    with self.assertRaises(ValueError):
                        P.update_file(old, source, str(self.path), None, {})
                self.assertEqual(old, before)
                updated = P.update_file(old, source, str(self.path), None, {})
                self.assertEqual(updated["totals"], old["totals"])
                self.assertEqual(self.row(updated)["levels"], {"high": 1})
                self.assertNotIn("reasoning_effort", old)

    def test_missing_archived_source_keeps_older_metadata_unavailable(self):
        old = P._blank_agg("claude", str(self.path))
        old.pop("reasoning_effort_version"); old.pop("reasoning_effort")
        old["archived"] = True
        self.assertIs(P.update_file(old, "claude", str(self.path), None, {}), old)
        self.assertNotIn("reasoning_effort", old)

    def test_metadata_never_changes_accounting(self):
        examples = (("claude", [self.claude(1, effort="max"),
                                 self.claude(2, perTurnEffort="high", isSidechain=True)]),
                    ("codex", [self.context(effort="ultra"), self.usage(), self.token_count()]))
        for source, lines in examples:
            with self.subTest(source=source):
                normal = self.parse(source, lines)
                with patch.object(P, "_reasoning_effort_observe"):
                    baseline = self.parse(source, lines)
                for key in ("records", "totals", "hourly", "ctx", "tools", "activity", "first_ts", "last_ts"):
                    self.assertEqual(normal.get(key), baseline.get(key), key)
                fields = set(normal["sessions"][0]) - {"reasoning_effort"}
                self.assertEqual({k: normal["sessions"][0][k] for k in fields},
                                 {k: baseline["sessions"][0][k] for k in fields})


if __name__ == "__main__":
    unittest.main()
