import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import parser as P
import session_timeline as timeline


class SessionTimelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "session.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, *rows):
        self.path.write_text("\n".join(json.dumps(item, separators=(",", ":")) for item in rows) + "\n",
                             encoding="utf-8")

    def call(self, source, session_id="full-session-id", **kwargs):
        return timeline.timeline(source, self.path, session_id, **kwargs)

    def test_claude_request_repeated_blocks_are_one_max_usage_event(self):
        self.write(
            {"type": "user", "uuid": "u-1", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:00Z", "message": {"content": "secret prompt"}},
            {"type": "assistant", "uuid": "a-1", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:02Z", "requestId": "r-1",
             "message": {"id": "m-1", "model": "Claude Model <img>", "usage": {"input_tokens": 20,
                 "output_tokens": 2, "cache_read_input_tokens": 0}, "content": []}},
            {"type": "assistant", "uuid": "a-2", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:03Z", "requestId": "r-1",
             "message": {"id": "m-1", "model": "Claude Model <img>", "usage": {"input_tokens": 20,
                 "output_tokens": 5, "cache_read_input_tokens": True, "cache_creation_input_tokens": 3},
                 "content": [{"type": "tool_use", "id": "tool-1", "name": "Read<unsafe>", "input": {"secret": "arg"}}]}},
            {"type": "assistant", "uuid": "a-1", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:04Z", "requestId": "r-1",
             "message": {"id": "m-1", "model": "Claude Model <img>", "usage": {"input_tokens": 20,
                 "output_tokens": 999}, "content": []}},
            {"type": "assistant", "uuid": "a-3", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:05Z", "requestId": "r-1",
             "message": {"id": "m-1", "model": "Claude Model <img>", "usage": {"input_tokens": 20,
                 "output_tokens": 5}, "content": []}},
        )
        result = self.call("claude")
        self.assertTrue(result["available"])
        self.assertEqual([e["kind"] for e in result["events"]], ["user_turn", "model", "tool", "request"])
        request = result["events"][-1]
        self.assertEqual((request["input"], request["output"], request["cache_write"]), (20, 5, 3))
        self.assertEqual(request["cache_read"], 0)  # the malformed bool did not replace the earlier zero
        serialized = json.dumps(result)
        for private in ("secret prompt", "secret", "requestId", "session.jsonl"):
            self.assertNotIn(private, serialized)

    def test_claude_resumed_uuid_does_not_duplicate_turn_or_usage(self):
        one = {"type": "user", "uuid": "same-uuid", "sessionId": "full-session-id",
               "timestamp": "2026-10-01T10:00:00Z", "message": {"content": "secret"}}
        self.write(one, one)
        self.assertEqual(len(self.call("claude")["events"]), 1)

    def test_claude_sidechains_are_child_only_and_queued_prompts_follow_parser_rules(self):
        self.write(
            {"type": "user", "uuid": "side-user", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:00Z", "isSidechain": True,
             "message": {"content": "private delegated task"}},
            {"type": "attachment", "uuid": "queued-ok", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:01Z", "attachment": {"type": "queued_command",
                 "commandMode": "prompt", "prompt": "A real steering prompt"}},
            {"type": "attachment", "uuid": "queued-empty", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:02Z", "attachment": {"type": "queued_command",
                 "commandMode": "prompt", "prompt": "   "}},
            {"type": "attachment", "uuid": "queued-command", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:03Z", "attachment": {"type": "queued_command",
                 "commandMode": "prompt", "prompt": "/compact"}},
            {"type": "user", "uuid": "user-tool-result", "sessionId": "full-session-id",
             "timestamp": "2026-10-01T10:00:04Z", "message": {"content": [
                 {"type": "tool_result", "content": "private tool output"}]}},
        )
        parent = self.call("claude")
        self.assertEqual([e["kind"] for e in parent["events"]], ["user_turn"])
        self.assertNotIn("private", json.dumps(parent))
        child_path = self.path.parent / "parent" / "subagents" / "agent-child.jsonl"
        child_path.parent.mkdir(parents=True)
        child_path.write_text(self.path.read_text(encoding="utf-8"), encoding="utf-8")
        child = timeline.timeline("claude", child_path, "full-session-id")
        self.assertEqual([e["kind"] for e in child["events"]], ["user_turn", "user_turn"])

    def test_timeline_range_filters_retained_events_without_losing_history_state(self):
        self.write(
            {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
             "payload": {"id": "full-session-id"}},
            {"type": "event_msg", "timestamp": "2026-10-01T11:00:00Z",
             "payload": {"type": "user_message"}},
            {"type": "turn_context", "timestamp": "2026-10-01T11:00:01Z",
             "payload": {"model": "codex-model"}},
            {"type": "event_msg", "timestamp": "2026-10-02T10:00:00Z",
             "payload": {"type": "token_count", "info": {"last_token_usage": {
                 "input_tokens": 40, "output_tokens": 4}}}},
            {"type": "event_msg", "timestamp": "2026-10-02T10:00:01Z",
             "payload": {"type": "user_message"}},
        )
        day = timeline._event_date("2026-10-02T10:00:00Z")
        result = self.call("codex", max_events=1, date_from=day, date_to=day)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["kind"], "request")
        self.assertEqual(result["events"][0]["input"], 40)

    def test_claude_event_sums_match_parser_update_file_fixture(self):
        self.write(
            {"type": "user", "uuid": "typed", "sessionId": "full-session-id",
             "timestamp": "2026-10-16T10:00:00Z", "message": {"content": "typed prompt"}},
            {"type": "attachment", "uuid": "queued", "sessionId": "full-session-id",
             "timestamp": "2026-10-16T10:00:01Z", "attachment": {"type": "queued_command",
                 "commandMode": "prompt", "prompt": "queued prompt"}},
            {"type": "assistant", "uuid": "block-1", "sessionId": "full-session-id",
             "timestamp": "2026-10-16T10:00:02Z", "requestId": "request-1", "message": {
                 "id": "message-1", "model": "claude-sonnet-5-5", "usage": {
                     "input_tokens": 11, "output_tokens": 3, "cache_read_input_tokens": 2,
                     "cache_creation_input_tokens": 4,
                     "output_tokens_details": {"thinking_tokens": 1}}, "content": []}},
            {"type": "assistant", "uuid": "block-2", "sessionId": "full-session-id",
             "timestamp": "2026-10-16T10:00:03Z", "requestId": "request-1", "message": {
                 "id": "message-1", "model": "claude-sonnet-5-5", "usage": {
                     "input_tokens": 11, "output_tokens": 9, "cache_read_input_tokens": 2,
                     "cache_creation_input_tokens": 4, "server_tool_use": {"web_search_requests": 1},
                     "output_tokens_details": {"thinking_tokens": 2}}, "content": [
                         {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "x"}}]}},
        )
        agg = P.update_file(None, "claude", str(self.path), None, {})
        result = self.call("claude")
        requests = [e for e in result["events"] if e["kind"] == "request"]
        self.assertEqual(len(requests), agg["totals"]["asst"])
        for event_key, total_key in (("input", "in"), ("output", "out"),
                                     ("cache_read", "cr"), ("cache_write", "cc"),
                                     ("reasoning", "reason"), ("web_search", "ws")):
            self.assertEqual(sum(e.get(event_key, 0) for e in requests), agg["totals"][total_key])
        self.assertEqual(sum(e["kind"] == "user_turn" for e in result["events"]), agg["totals"]["user"])
        self.assertEqual(sum(e["kind"] == "tool" for e in result["events"]), agg["totals"]["tools"])

    def test_codex_event_sums_match_parser_across_usage_source_switch(self):
        self.write(
            {"type": "session_meta", "timestamp": "2026-10-16T10:00:00Z",
             "payload": {"id": "full-session-id", "forked_from_id": "parent"}},
            {"type": "turn_context", "timestamp": "2026-10-16T10:00:00.1Z",
             "payload": {"model": "codex-model"}},
            {"type": "token_usage_record", "timestamp": "2026-10-16T10:00:00.2Z",
             "payload": {"usage": {"input_tokens": 999, "output_tokens": 99}}},
            {"type": "event_msg", "timestamp": "2026-10-16T10:00:00.3Z",
             "payload": {"type": "token_count", "info": {"last_token_usage": {
                 "input_tokens": 999, "output_tokens": 99}}}},
            {"type": "event_msg", "timestamp": "2026-10-16T10:00:02Z",
             "payload": {"type": "token_count", "info": {
                 "total_token_usage": {"input_tokens": 40, "output_tokens": 6},
                 "last_token_usage": {"input_tokens": 40, "cached_input_tokens": 10,
                     "cache_write_input_tokens": 5, "output_tokens": 6,
                     "reasoning_output_tokens": 2}}}},
            {"type": "event_msg", "timestamp": "2026-10-16T10:00:02.1Z",
             "payload": {"type": "user_message", "message": "typed prompt"}},
            {"type": "token_usage_record", "timestamp": "2026-10-16T10:00:03Z",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 20,
                 "cache_write_input_tokens": 10, "output_tokens": 12, "reasoning_output_tokens": 4}}},
            {"type": "event_msg", "timestamp": "2026-10-16T10:00:03.1Z",
             "payload": {"type": "token_count", "info": {
                 "total_token_usage": {"input_tokens": 140, "output_tokens": 18},
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 20,
                     "cache_write_input_tokens": 10, "output_tokens": 12,
                     "reasoning_output_tokens": 4}}}},
            {"type": "response_item", "timestamp": "2026-10-16T10:00:04Z",
             "payload": {"type": "function_call", "name": "read_file", "arguments": "private args"}},
        )
        agg = P.update_file(None, "codex", str(self.path), None, {})
        result = self.call("codex")
        requests = [e for e in result["events"] if e["kind"] == "request"]
        self.assertEqual(len(requests), agg["totals"]["req"])
        for event_key, total_key in (("input", "in"), ("output", "out"),
                                     ("cache_read", "cr"), ("cache_write", "cc"),
                                     ("reasoning", "reason")):
            self.assertEqual(sum(e.get(event_key, 0) for e in requests), agg["totals"][total_key])
        self.assertEqual(sum(e["kind"] == "user_turn" for e in result["events"]), agg["totals"]["user"])
        self.assertEqual(sum(e["kind"] == "tool" for e in result["events"]), agg["totals"]["tools"])

    def test_codex_usage_record_wins_over_token_count_twin(self):
        self.write(
            {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
             "payload": {"id": "full-session-id"}},
            {"type": "turn_context", "timestamp": "2026-10-01T10:00:01Z",
             "payload": {"model": "codex-model"}},
            {"type": "token_usage_record", "timestamp": "2026-10-01T10:00:02Z",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 20,
                 "cache_write_input_tokens": 10, "output_tokens": 10, "reasoning_output_tokens": 3}}},
            {"type": "event_msg", "timestamp": "2026-10-01T10:00:02Z", "payload": {"type": "token_count",
             "info": {"last_token_usage": {"input_tokens": 100, "cached_input_tokens": 20,
                 "cache_write_input_tokens": 10, "output_tokens": 10, "reasoning_output_tokens": 3}}}},
            {"type": "event_msg", "timestamp": "2026-10-01T10:00:03Z", "payload": {"type": "user_message",
             "message": "secret prompt"}},
            {"type": "response_item", "timestamp": "2026-10-01T10:00:04Z", "payload": {
             "type": "function_call", "name": "read_file", "arguments": "secret arguments"}},
        )
        result = self.call("codex")
        self.assertEqual([e["kind"] for e in result["events"]], ["model", "request", "user_turn", "tool"])
        self.assertEqual((result["events"][1]["input"], result["events"][1]["cache_read"],
                          result["events"][1]["cache_write"]), (70, 20, 10))
        self.assertNotIn("secret", json.dumps(result))

    def test_codex_missing_usage_fields_stay_unknown(self):
        usage = timeline._codex_usage({"input_tokens": 14, "output_tokens": 2})
        self.assertEqual(usage, {"input": 14, "output": 2})

    def test_activity_view_filters_tools_before_cap_and_keeps_codex_agent_identity_private(self):
        rows = [{"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z", "payload": {"id": "full-session-id"}}]
        for n in range(10):
            rows.append({"type": "response_item", "timestamp": "2026-10-01T10:00:01Z",
                         "payload": {"type": "function_call", "name": "Read", "arguments": "SECRET"}})
        rows += [{"type": "event_msg", "timestamp": "2026-10-01T10:01:00Z", "payload": {
            "type": "item_completed", "item": {"type": "SubAgentActivity", "kind": state,
                "agent_thread_id": "PRIVATE-ID", "prompt": "SECRET"}}} for state in ("started", "completed")]
        self.write(*rows)
        result = self.call("codex", view="activity", max_events=2)
        self.assertFalse(result["truncated"])
        self.assertEqual([e["label"] for e in result["events"]], ["Subagent started", "Subagent completed"])
        self.assertEqual([e["agent"] for e in result["events"]], [1, 1])
        for secret in ("PRIVATE-ID", "SECRET", "arguments"):
            self.assertNotIn(secret, json.dumps(result))

    def test_claude_agent_requests_correlate_results_without_claiming_background_completion(self):
        def assistant(n, background):
            return {"type": "assistant", "uuid": "block-"+str(n), "sessionId": "full-session-id",
                "timestamp": "2026-10-01T10:00:00Z", "effort": "max", "message": {
                    "model": "claude-sonnet-5-5", "content": [{"type": "tool_use", "id": "tool-"+str(n),
                    "name": "Agent", "input": {"run_in_background": background, "prompt": "SECRET"}}]}}
        def result(n):
            return {"type": "user", "uuid": "result-"+str(n), "sessionId": "full-session-id",
                "timestamp": "2026-10-01T10:00:01Z", "message": {"content": [{"type": "tool_result",
                    "tool_use_id": "tool-"+str(n), "content": "SECRET"}]}}
        self.write(assistant(1, False), assistant(2, True), result(1), result(2))
        events = self.call("claude", view="activity")["events"]
        agents = [e for e in events if e["kind"] == "subagent"]
        self.assertEqual([e["label"] for e in agents], ["Subagent requested", "Subagent requested", "Subagent completed", "Subagent tool returned"])
        self.assertEqual([e["agent"] for e in agents], [1, 2, 1, 2])
        self.assertEqual(sum(e["kind"] == "reasoning" for e in events), 1)
        self.assertNotIn("SECRET", json.dumps(events))

    def test_copilot_activity_receipts_tools_and_compactions_are_bounded_private_and_deduplicated(self):
        def event(n, kind, data, aid=None):
            return {"id": str(n), "type": kind, "timestamp": "2026-10-01T10:00:%02dZ" % n,
                    "data": data, **({"agentId": aid} if aid else {})}
        usage = {"apiCallId": "PRIVATE-CALL", "model": "@cf/zai-org/glm-5.3", "inputTokens": 100,
                 "outputTokens": 5, "cacheReadTokens": 40, "accounting": {"sourceSessionId": "full-session-id"}}
        self.write(event(0, "session.start", {"sessionId": "full-session-id", "selectedModel": usage["model"]}),
            event(1, "user.message", {"messageId": "prompt", "content": "SECRET"}),
            event(2, "subagent.started", {"model": usage["model"], "agentDescription": "SECRET"}, "PRIVATE-AGENT"),
            event(3, "subagent.configured", {"model": usage["model"], "reasoningEffort": "max"}, "PRIVATE-AGENT"),
            event(4, "assistant.message", {"toolRequests": [{"toolCallId": "PRIVATE-TOOL", "name": "Read", "arguments": "SECRET"}]}),
            event(5, "tool.execution_start", {"toolCallId": "PRIVATE-TOOL", "toolName": "Read"}),
            event(6, "assistant.usage", usage), event(7, "session.usage_record", {"usage": usage}),
            event(8, "session.usage_record", {"usage": usage}),
            event(9, "session.compaction_complete", {"success": True, "trigger": "threshold", "preCompactionTokens": 1000, "postCompactionTokens": 100, "summaryContent": "SECRET"}),
            event(10, "subagent.completed", {"result": "SECRET"}, "PRIVATE-AGENT"),
            event(11, "session.error", {"message": "SECRET"}))
        result = self.call("copilot")
        self.assertTrue(result["available"])
        requests = [e for e in result["events"] if e["kind"] == "request"]
        self.assertEqual(len(requests), 1)
        self.assertEqual((requests[0]["input"], requests[0]["cache_read"]), (60, 40))
        self.assertEqual(len(self.call("copilot", view="tools")["events"]), 1)
        compactions = self.call("copilot", view="compactions")["events"]
        self.assertEqual((compactions[0]["before"], compactions[0]["after"]), (1000, 100))
        activity = self.call("copilot", view="activity")["events"]
        self.assertTrue(any(e["label"] == "Subagent started" for e in activity if e["kind"] == "subagent"))
        self.assertFalse(any(e["kind"] in ("request", "tool") for e in activity))
        for secret in ("SECRET", "PRIVATE-", "arguments", "sourceSessionId"):
            self.assertNotIn(secret, json.dumps(result))
        self.assertFalse(self.call("copilot", session_id="wrong-full-id")["available"])
        with self.assertRaises(ValueError):
            self.call("copilot", view="../filename")

    def test_codex_token_count_fallback_deduplicates_repeated_signature(self):
        tc = {"type": "event_msg", "timestamp": "2026-10-01T10:00:02Z", "payload": {"type": "token_count",
              "info": {"total_token_usage": {"input_tokens": 30},
                       "last_token_usage": {"input_tokens": 30, "output_tokens": 3}}}}
        self.write({"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
                    "payload": {"id": "full-session-id"}}, tc, tc)
        events = self.call("codex")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["input"], 30)

    def test_codex_fork_replay_burst_is_skipped(self):
        self.write(
            {"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
             "payload": {"id": "full-session-id", "forked_from_id": "parent"}},
            {"type": "turn_context", "timestamp": "2026-10-01T10:00:00.1Z", "payload": {"model": "inherited"}},
            {"type": "event_msg", "timestamp": "2026-10-01T10:00:00.2Z", "payload": {"type": "user_message"}},
            {"type": "token_usage_record", "timestamp": "2026-10-01T10:00:00.3Z",
             "payload": {"usage": {"input_tokens": 999, "output_tokens": 1}}},
            {"type": "event_msg", "timestamp": "2026-10-01T10:00:02Z", "payload": {"type": "user_message"}},
        )
        events = self.call("codex")["events"]
        self.assertEqual([e["kind"] for e in events], ["user_turn"])

    def test_codex_requires_matching_full_session_identity(self):
        self.write({"type": "session_meta", "timestamp": "2026-10-01T10:00:00Z",
                    "payload": {"id": "some-other-full-id"}},
                   {"type": "event_msg", "timestamp": "2026-10-01T10:00:01Z",
                    "payload": {"type": "user_message"}})
        self.assertEqual(self.call("codex")["events"], [])

    def test_missing_archived_source_is_unavailable_and_untrusted_source_rejected(self):
        self.path.unlink(missing_ok=True)
        self.assertFalse(self.call("codex")["available"])
        self.assertFalse(timeline.timeline("opencode", self.path, "full-session-id")["available"])

    def test_event_limit_and_byte_limit_are_reported_partial(self):
        self.write(
            {"type": "user", "uuid": "u1", "sessionId": "full-session-id", "timestamp": "2026-10-01T10:00:00Z",
             "message": {"content": "first"}},
            {"type": "user", "uuid": "u2", "sessionId": "full-session-id", "timestamp": "2026-10-01T10:00:01Z",
             "message": {"content": "second"}},
        )
        limited_events = self.call("claude", max_events=1)
        self.assertTrue(limited_events["truncated"])
        self.assertEqual(limited_events["detail"], "partial")
        with patch.object(timeline, "MAX_BYTES", 40):
            limited_bytes = self.call("claude")
        self.assertTrue(limited_bytes["truncated"])
        self.assertEqual(limited_bytes["detail"], "partial")

    def test_invalid_timestamps_and_zero_usage_are_preserved_safely(self):
        self.write(
            {"type": "user", "sessionId": "full-session-id", "timestamp": "not a date"},
            {"type": "assistant", "sessionId": "full-session-id", "timestamp": "2026-10-01T10:00:02Z",
             "message": {"model": "claude", "usage": {"input_tokens": 0, "output_tokens": -1}}},
        )
        events = self.call("claude", view="requests")["events"]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["input"], 0)
        self.assertNotIn("output", events[0])


if __name__ == "__main__":
    unittest.main()
