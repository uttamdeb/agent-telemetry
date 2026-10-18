"""Copilot SDK and VS Code Agent Host accounting, using invented stores only."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


class CopilotSDKTests(unittest.TestCase):
    SID = "11111111-2222-3333-4444-555555555555"
    HOST = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    MODEL = "customendpoint/Cloudflare Workers AI/@cf/zai-org/glm-5.3"

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="telemetry-sdk-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.editor = self.root / "Code"
        self.path = self.root / "session-state" / self.SID / "events.jsonl"
        self.path.parent.mkdir(parents=True)
        self.db = self.editor / "agentSessionData" / self.HOST / "session.db"
        self.events = [self.event("session.start", "start", 0, {
            "sessionId": self.SID, "selectedModel": self.MODEL,
            "context": {"cwd": r"C:\Users\fixture\repos\demo"}, "copilotVersion": "1.0.test"})]
        for mock in [patch.object(P, "COPILOT_ROOTS", [str(self.editor)]),
                     patch.object(P, "COPILOT_SDK_GLOBS", [str(self.root / "session-state/*/events.jsonl")]),
                     patch.object(P, "_SDK_HOSTS", {"at": 0, "roots": (), "map": {}})]:
            mock.start()
            self.addCleanup(mock.stop)

    def event(self, kind, eid, minute, data, **extra):
        return {"type": kind, "id": eid, "timestamp": f"2026-10-15T10:{minute:02d}:00Z",
                "data": data, **extra}

    def turn(self, n, minute=1, model=None):
        model = model or self.MODEL
        self.events += [self.event("user.message", f"u{n}", minute, {"content": f"Prompt {n}"}),
                        self.event("assistant.message", f"a{n}", minute + 1, {
                            "content": "A reply", "model": model, "messageId": f"m{n}", "apiCallId": f"call{n}"})]

    def store(self):
        self.db.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db)
        con.executescript("""PRAGMA journal_mode=WAL;
            PRAGMA wal_autocheckpoint=0;
            CREATE TABLE session_metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE turns (id TEXT PRIMARY KEY, event_id TEXT);
            CREATE TABLE turn_usage (turn_id TEXT PRIMARY KEY, usage TEXT);
            CREATE TABLE secret_config (credentials TEXT);
            INSERT INTO secret_config VALUES ('DO-NOT-COLLECT-THIS-SECRET');""")
        con.executemany("INSERT INTO session_metadata VALUES (?,?)", [
            ("defaultChatProviderData", json.dumps({"sdkSessionId": self.SID})),
            ("customTitle", "Greeting"), ("agentHost.git", json.dumps({"branchName": "main"}))])
        con.commit()
        self.addCleanup(con.close)
        return con

    def usage(self, con, n, inp=100, out=20, **changes):
        row = {"model": self.MODEL, "inputTokens": inp, "outputTokens": out, "cachedTokens": 10}
        row.update(changes)
        usage = {"inputTokens": 1, "outputTokens": 1, "model": self.MODEL,
                 "_meta": {"turnTokenTotals": [row], "directTurnTokenTotals": [row],
                           "contextAttribution": {"totalTokens": 900000}}}
        con.execute("INSERT OR REPLACE INTO turns VALUES (?,?)", (f"request{n}", f"u{n}"))
        con.execute("INSERT OR REPLACE INTO turn_usage VALUES (?,?)", (f"request{n}", json.dumps(usage)))
        con.commit()

    def parse(self, previous=None, write=True):
        if write:
            self.path.write_text("".join(json.dumps(e) + "\n" for e in self.events), encoding="utf-8")
        return P.update_file(previous, "copilot", str(self.path), "Copilot SDK", {})

    def test_discovery_and_exact_host_totals_not_context_or_last_call(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.assertIn(("copilot", str(self.path), "Copilot SDK"), P.discover())
        self.assertEqual(agg["title"], "Greeting")
        self.assertEqual((agg["project"], agg["editor"], agg["branch"]), ("demo", "VS Code", "main"))
        self.assertEqual(agg["sessions"][0]["models"], ["GLM 5.3"])
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "cr", "user", "asst", "req")], [90, 20, 10, 1, 1, 1])
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "recorded")
        self.assertNotIn("DO-NOT-COLLECT", json.dumps(agg))
        self.assertNotIn("Prompt 1", json.dumps(agg["state"]))
        self.assertAlmostEqual(D._cost("copilot", "GLM 5.3", 90, 20, 10, 0, 0), .0002166)

    def test_workspace_path_backfills_and_child_context_never_overrides_parent(self):
        self.turn(1)
        agg = self.parse()
        self.assertEqual(agg["cwd"], r"C:\Users\fixture\repos\demo")
        agg["copilot_sdk"]["version"] = 2
        agg.pop("cwd")
        newer = self.parse(agg, write=False)
        self.assertEqual(newer["cwd"], r"C:\Users\fixture\repos\demo")
        self.events += [self.event("session.context_changed", "move", 4, {"cwd": "/fixtures/other"}),
                        self.event("session.context_changed", "child-move", 5, {"cwd": "/private/child"}, agentId="child")]
        self.assertEqual(self.parse(newer)["cwd"], "/fixtures/other")

    def test_three_named_sdk_children_are_breakdowns_not_extra_usage(self):
        self.turn(1)
        for i in range(3):
            aid = "child-" + str(i)
            self.events += [self.event("subagent.started", "spawn-" + aid, 3,
                {"agentDisplayName": "Review " + str(i), "model": self.MODEL}, agentId=aid),
                self.event("assistant.message", "reply-" + aid, 4,
                    {"content": "PRIVATE CHILD CONTENT", "model": self.MODEL, "apiCallId": "call-" + aid}, agentId=aid),
                self.event("session.usage_record", "usage-" + aid, 4,
                    {"usage": {"model": self.MODEL, "apiCallId": "call-" + aid,
                     "inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 10}}, agentId=aid)]
        replay = copy.deepcopy(self.events[-1]); replay["id"] = "replayed-receipt"
        self.events.append(replay)
        spawn = copy.deepcopy(self.events[-4]); spawn["id"] = "replayed-spawn"
        self.events.append(spawn)
        self.events += [self.event("session.compaction_start", "child-compact-start", 5, {}, agentId="child-0"),
            self.event("session.compaction_complete", "child-compact", 6,
                {"success": True, "compactionTokensUsed": {"model": self.MODEL,
                 "inputTokens": 10, "outputTokens": 2}}, agentId="child-0")]
        agg = self.parse()
        children = agg["copilot_sdk"]["agents"]
        self.assertEqual(len(children), 3)
        self.assertEqual(agg["sessions"][0]["subagents"], 3)
        self.assertEqual([a["title"] for a in children], ["Review 0", "Review 1", "Review 2"])
        self.assertTrue(all(a["model"] == "GLM 5.3" and a["usage_status"] == "recorded" for a in children))
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"], agg["totals"]["cr"]), (280, 62, 30))
        self.assertEqual(len(children[0]["compactions"]), 1)
        self.assertEqual(children[0]["compactions"][0]["duration_ms"], 60000)
        self.assertEqual(agg["sessions"][0]["compactions"], [])
        self.assertNotIn("PRIVATE CHILD CONTENT", json.dumps(children))

    def test_child_without_receipts_stays_unavailable(self):
        self.events.append(self.event("subagent.started", "spawn", 2,
            {"agentDisplayName": "Research", "model": self.MODEL}, agentId="child"))
        self.assertEqual(self.parse()["copilot_sdk"]["agents"][0]["usage_status"], "unavailable")

    def test_child_compaction_usage_does_not_double_count_inclusive_host_totals(self):
        self.turn(1)
        self.events += [self.event("subagent.started", "spawn", 3, {"model": self.MODEL}, agentId="child"),
            self.event("session.compaction_complete", "compact", 4, {"success": True,
                "compactionTokensUsed": {"model": self.MODEL, "inputTokens": 10, "outputTokens": 2}}, agentId="child")]
        con = self.store()
        self.usage(con, 1, inp=1000, out=100, cachedTokens=0)
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (1000, 100))
        child = agg["copilot_sdk"]["agents"][0]
        self.assertEqual(child["usage_status"], "partial")
        self.assertEqual(sum(r["in"] + r["out"] for r in child["records"].values()), 12)

    def test_wal_only_updates_and_idempotent_refresh(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        before = self.db.stat().st_mtime_ns
        self.usage(con, 1, 200, 40)
        self.assertEqual(self.db.stat().st_mtime_ns, before)
        newer = self.parse(agg, write=False)
        self.assertEqual((newer["totals"]["in"], newer["totals"]["out"]), (190, 40))
        self.assertEqual(newer["totals"]["user"], 1)
        self.assertIs(self.parse(newer, write=False), newer)

    def test_unavailable_and_failed_attempts_keep_real_activity(self):
        self.events.append(self.event("user.message", "u1", 1, {"content": "Hello"}))
        self.events += [self.event("session.error", "err", 2, {"message": "PRIVATE ERROR TEXT"}),
                        self.event("abort", "abort", 3, {})]
        agg = self.parse()
        self.assertEqual((agg["totals"]["user"], agg["totals"]["asst"], agg["totals"]["in"]), (1, 0, 0))
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "unavailable")
        self.assertEqual(agg["copilot_sdk"]["errors"], 1)
        self.assertTrue(D._did_something(agg["sessions"][0]))
        self.assertNotIn("PRIVATE ERROR TEXT", json.dumps(agg))

    def test_partial_coverage_never_estimates_missing_turn(self):
        self.turn(1)
        self.turn(2, 4)
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 90)
        self.assertEqual(agg["totals"]["user"], 2)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 1)
        exact = [r["exact"] for r in agg["records"].values() if "exact" in r]
        self.assertEqual(sum(r.get("user", 0) for r in exact), 1)
        self.assertEqual(sum(r.get("asst", 0) for r in exact), 1)

    def test_child_usage_inclusive_totals_and_tool_twins_count_once(self):
        self.turn(1)
        self.events[-1]["data"]["toolRequests"] = [{"toolCallId": "tool1", "name": "view"}]
        self.events += [self.event("tool.execution_start", "tool-start", 3, {"toolCallId": "tool1", "toolName": "view"}),
                        self.event("assistant.message", "child", 3, {"content": "Internal child reply", "model": "gpt-6-luna", "apiCallId": "child-call"}, agentId="child1"),
                        self.event("subagent.started", "spawn", 2, {"toolCallId": "child1"})]
        self.events.append(copy.deepcopy(self.events[2]))
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.assertEqual((agg["totals"]["asst"], agg["totals"]["tools"], agg["totals"]["in"]), (1, 1, 90))
        self.assertEqual(agg["sessions"][0]["subagents"], 1)

    def test_bad_usage_retries_without_mutating_previous(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        saved = copy.deepcopy(agg)
        con.execute("UPDATE turn_usage SET usage='broken'")
        con.commit()
        with self.assertRaises(ValueError): self.parse(agg, write=False)
        self.assertEqual(agg, saved)
        self.usage(con, 1, 300, 30)
        self.assertEqual(self.parse(agg, write=False)["totals"]["in"], 290)

    def test_invalid_token_counts_refused(self):
        for value in [True, -1, 1.5, float("inf"), "100", None]:
            self.assertIsNone(P._sdk_tokens({"inputTokens": value, "outputTokens": 2}))
        self.assertIsNone(P._sdk_tokens({"inputTokens": 10, "outputTokens": 2, "cacheReadTokens": 11}))
        self.assertIsNone(P._sdk_tokens({"inputTokens": 10, "outputTokens": 2, "reasoningTokens": 3}))

    def test_custom_endpoint_model_names_are_canonical(self):
        for raw, expected in [(self.MODEL, "GLM 5.3"),
            ("customendpoint/My endpoint/openai/gpt-6.1-sol", "GPT-6.1 Sol"),
            ("anthropic/claude-haiku-5-5", "Claude Haiku 5.5")]:
            self.assertEqual(P._sdk_model(raw), expected)

    def test_deleted_auxiliary_store_retains_recovered_usage(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.turn(2, 4)
        with patch.object(P, "_copilot_sdk_hosts", return_value={}):
            newer = self.parse(agg)
        self.assertEqual((newer["totals"]["in"], newer["totals"]["user"]), (90, 2))
        self.assertEqual(newer["title"], "Greeting")
        self.assertEqual(newer["editor"], "VS Code")
        self.assertEqual(newer["copilot_sdk"]["usage_status"], "partial")

    def test_shutdown_cumulative_snapshots_do_not_double_count(self):
        self.turn(1)
        metric = {self.MODEL: {"requests": {"count": 1, "cost": 0}, "usage": {
            "inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 10, "cacheWriteTokens": 0, "reasoningTokens": 5}}}
        self.events += [self.event("session.shutdown", "shutdown1", 3, {"modelMetrics": metric}),
                        self.event("session.shutdown", "shutdown2", 4, {"modelMetrics": metric})]
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "cr", "reason")], [90, 20, 10, 5])
        self.assertEqual(agg["sessions"][0]["reason"], 5)

    def test_shutdown_before_resumed_turn_does_not_overwrite_later_usage(self):
        self.turn(1)
        self.events.append(self.event("session.shutdown", "old-shutdown", 3, {"modelMetrics": {
            self.MODEL: {"usage": {"inputTokens": 1000, "outputTokens": 200}}}}))
        self.turn(2, 4)
        con = self.store()
        self.usage(con, 2)
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 90)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 1)

    def test_shutdown_does_not_claim_optional_fields_for_other_models(self):
        self.turn(1)
        other = "gpt-6-luna"
        self.turn(2, 4, other)
        self.events.append(self.event("session.shutdown", "shutdown", 6, {"modelMetrics": {
            self.MODEL: {"usage": {"inputTokens": 100, "outputTokens": 20, "reasoningTokens": 5}},
            other: {"usage": {"inputTokens": 100, "outputTokens": 20}}}}))
        agg = self.parse()
        self.assertNotIn("reason", agg["copilot_sdk"]["recorded_fields"])

    def test_shutdown_with_fewer_calls_does_not_claim_full_history(self):
        self.turn(1)
        self.turn(2, 4)
        self.events.append(self.event("session.shutdown", "shutdown", 6, {"modelMetrics": {
            self.MODEL: {"requests": {"count": 1}, "usage": {"inputTokens": 100, "outputTokens": 20}}}}))
        agg = self.parse()
        self.assertEqual(agg["totals"]["req"], 2)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertFalse(any(r.get("exact", {}).get("user", 0) for r in agg["records"].values()))

    def test_last_call_fallback_does_not_claim_whole_turn_precision(self):
        self.turn(1)
        self.events.append(self.event("assistant.message", "second-call", 3, {
            "content": "Another response", "model": self.MODEL, "messageId": "m2", "apiCallId": "call2"}))
        con = self.store()
        self.usage(con, 1)
        con.execute("UPDATE turn_usage SET usage=?", (json.dumps({"inputTokens": 100,
            "outputTokens": 20, "model": self.MODEL}),))
        con.commit()
        agg = self.parse()
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["totals"]["in"], 100)
        self.assertEqual(sum(r.get("exact", {}).get("user", 0) for r in agg["records"].values()), 0)

    def test_unmatched_or_wrong_session_database_usage_is_not_imported(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 99, 90000, 5000)
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 0)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "unavailable")

    def test_multiple_host_copies_choose_fullest_turn_not_sum(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        other = self.root / "other.db"
        with closing(sqlite3.connect(other)) as target:
            con.backup(target)
            target.execute("UPDATE turn_usage SET usage=?", (json.dumps({"_meta": {"turnTokenTotals": [
                {"model": self.MODEL, "inputTokens": 200, "outputTokens": 40}]}}),))
            target.commit()
        with patch.object(P, "_copilot_sdk_hosts", return_value={self.SID: [
            (str(other), "VS Code Insiders"), (str(self.db), "VS Code")]}):
            agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 200)
        self.assertEqual(agg["totals"]["out"], 40)
        self.assertEqual(agg["totals"]["user"], 1)
        self.assertIn("ambiguous_editor", agg["copilot_sdk"]["limitations"])

    def test_empty_host_totals_do_not_claim_recorded_zero_usage(self):
        self.turn(1)
        con = self.store()
        self.usage(con, 1)
        con.execute("UPDATE turn_usage SET usage=?", (json.dumps({"_meta": {"turnTokenTotals": []}}),))
        con.commit()
        agg = self.parse()
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "unavailable")

    def test_queued_prompts_use_originating_ids_and_chronological_active_time(self):
        self.events += [self.event("user.message", "u1", 1, {"content": "First", "messageId": "um1"}),
                        self.event("user.message", "u2", 2, {"content": "Queued", "messageId": "um2"}),
                        self.event("assistant.message", "a1", 3, {"content": "First answer", "model": self.MODEL,
                            "apiCallId": "call1", "originatingMessageId": "um1"}),
                        self.event("assistant.message", "a2", 4, {"content": "Second answer", "model": self.MODEL,
                            "apiCallId": "call2", "originatingMessageId": "um2"})]
        con = self.store()
        self.usage(con, 1)
        self.usage(con, 2)
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("user", "asst", "in")], [2, 2, 180])
        self.assertEqual(agg["sessions"][0]["active"], 180)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "recorded")

    def test_compaction_between_turns_keeps_its_separate_usage(self):
        self.turn(1)
        self.events += [self.event("assistant.turn_end", "end", 3, {}),
                       self.event("session.compaction_complete", "compact", 4, {"success": True,
                           "compactionTokensUsed": {"model": self.MODEL, "inputTokens": 20, "outputTokens": 5}})]
        con = self.store()
        self.usage(con, 1)
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (110, 25))

    def test_compaction_usage_event_and_completion_are_not_double_counted(self):
        self.turn(1)
        tokens = {"model": self.MODEL, "inputTokens": 20, "outputTokens": 5}
        self.events += [self.event("assistant.turn_end", "end", 3, {}),
            self.event("assistant.usage", "compact-usage", 4, dict(tokens, interactionType="conversation-compaction")),
            self.event("session.compaction_complete", "compact", 4, {"success": True, "compactionTokensUsed": tokens})]
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (20, 5))

    def test_output_only_usage_is_partial_and_never_guesses_input(self):
        self.turn(1)
        self.events[-1]["data"]["outputTokens"] = 20
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (0, 20))
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["copilot_sdk"]["recorded_fields"], ["out"])
        self.assertFalse(any("exact" in r for r in agg["records"].values()))

    def test_fork_inherited_history_and_nested_host_copies_are_excluded(self):
        self.turn(1)
        inherited = self.event("user.message", "inherited", 1, {"content": "Parent prompt"})
        inherited["timestamp"] = "2026-10-14T10:00:00Z"
        self.events.insert(1, inherited)
        con = self.store()
        self.usage(con, 1)
        copied = self.db.parent.with_name(self.HOST + "--tool-output")
        copied.mkdir()
        destination = sqlite3.connect(copied / "session.db")
        self.addCleanup(destination.close)
        con.backup(destination)
        agg = self.parse()
        self.assertEqual((agg["totals"]["user"], agg["totals"]["in"]), (1, 90))
        self.assertEqual(len(P._copilot_sdk_hosts()[self.SID]), 1)

    def test_multi_day_shutdown_is_not_assigned_to_shutdown_day(self):
        self.turn(1)
        self.turn(2, 4)
        for e in self.events[-2:]: e["timestamp"] = e["timestamp"].replace("2026-10-15", "2026-10-16")
        self.events.append(self.event("session.shutdown", "shutdown", 6, {"modelMetrics": {self.MODEL: {"usage": {
            "inputTokens": 100, "outputTokens": 20}}}}))
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 0)
        self.assertIn("undated_usage", agg["copilot_sdk"]["limitations"])
        self.assertEqual(agg["copilot_sdk"]["shutdown_totals"][0]["in"], 100)

    def test_saved_usage_events_and_reasoning_do_not_add_usage_twice(self):
        self.turn(1)
        self.events += [self.event("assistant.usage", "usage", 2, {"model": self.MODEL,
            "inputTokens": 100, "outputTokens": 20, "cacheReadTokens": 10, "reasoningTokens": 8})]
        self.events.append(copy.deepcopy(self.events[-1]))
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"], agg["totals"]["reason"]), (90, 20, 8))
        con = self.store()
        self.usage(con, 1)
        P._SDK_HOSTS["at"] = 0
        newer = self.parse(agg, write=False)
        self.assertEqual(newer["totals"]["in"], 90)

    def test_partial_line_and_corrupt_complete_event(self):
        self.turn(1)
        agg = self.parse()
        with self.path.open("a") as f: f.write('{"unfinished":')
        self.assertEqual(self.parse(agg, write=False)["totals"]["user"], 1)
        with self.path.open("a") as f: f.write('\n')
        with self.assertRaises(ValueError): self.parse(agg, write=False)

    def test_compactions_only_successful_and_no_summary_text(self):
        self.events += [self.event("session.compaction_start", "cs", 1, {}),
            self.event("session.compaction_complete", "cf", 2, {"success": False}),
            self.event("session.compaction_start", "cs2", 3, {}),
            self.event("session.compaction_complete", "cc", 4, {"success": True,
                "preCompactionTokens": 100, "summaryContent": "PRIVATE SUMMARY"})]
        agg = self.parse()
        self.assertEqual(len(agg["compactions"]), 1)
        self.assertEqual(agg["compactions"]["cc"]["duration_ms"], 60000)
        self.assertEqual(agg["totals"]["user"], 0)
        self.assertNotIn("PRIVATE SUMMARY", json.dumps(agg))

    def receipt(self, call, minute, inp=100, out=20, **extra):
        usage = dict(model=self.MODEL, apiCallId=call, inputTokens=inp, outputTokens=out,
                     interactionType="conversation-agent", accounting={"sourceSessionId": self.SID,
                     "usageId": call, "sequence": len(self.events)})
        usage.update(extra)
        return self.event("session.usage_record", f"receipt-{len(self.events)}", minute, {"usage": usage})

    def test_receipt_before_message_replay_and_live_twin_count_once(self):
        self.turn(1)
        receipt = self.receipt("call1", 2, cacheReadTokens=10, reasoningTokens=5)
        self.events.insert(2, receipt)
        self.events.append(dict(receipt, id="replayed-receipt", timestamp="2026-10-16T10:02:00Z"))
        self.events.append(self.event("assistant.usage", "live-twin", 2, receipt["data"]["usage"]))
        # An old anonymous live event is not added on top of complete receipts.
        self.events.append(self.event("assistant.usage", "anonymous-twin", 2,
                           dict(model=self.MODEL, inputTokens=100, outputTokens=20)))
        self.events.append(self.event("session.usage_checkpoint", "checkpoint", 3,
                           {"accountingSnapshot": {"modelMetrics": {self.MODEL: {
                               "usage": {"inputTokens": 100, "outputTokens": 20}}}}}))
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "cr", "reason", "req")], [90, 20, 10, 5, 1])
        self.assertEqual(agg["copilot_sdk"]["usage_sources"], ["usage_receipts"])
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "recorded")
        self.assertEqual(agg["records"]["2026-10-15\tGLM 5.3"]["in"], 90)
        self.assertNotIn("2026-10-16\tGLM 5.3", agg["records"])

    def test_stale_origin_followups_and_reused_turn_counter_after_resume(self):
        con = self.store()
        for n, turn_id in ((1, "0"), (2, "2"), (3, "0")):
            minute = n * 5
            self.events.append(self.event("user.message", f"u{n}", minute,
                               {"content": f"Prompt {n}", "messageId": f"um{n}", "turnId": turn_id}))
            for call_n, runtime_id in enumerate((turn_id, str(int(turn_id) + 1)), 1):
                call = f"call{n}-{call_n}"
                self.events.append(self.receipt(call, minute + call_n, inp=50, out=10))
                self.events.append(self.event("assistant.message", call, minute + call_n,
                    {"content": "Reply", "model": self.MODEL, "apiCallId": call,
                     "turnId": runtime_id, "originatingMessageId": "um1" if n < 3 else "um3"}))
            self.usage(con, n, inp=100, out=20, cachedTokens=0)
        # Resumed continuation id 2 must not select the old second prompt.
        self.events.append(self.receipt("resumed-extra", 18, inp=50, out=10))
        self.events.append(self.event("assistant.message", "resumed-extra", 18,
            {"content": "Reply", "model": self.MODEL, "apiCallId": "resumed-extra",
             "turnId": "2", "originatingMessageId": "um3"}))
        self.usage(con, 3, inp=150, out=30, cachedTokens=0)
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "req", "user")], [350, 70, 7, 3])
        self.assertEqual(agg["copilot_sdk"]["usage_sources"], ["usage_receipts"])
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 0)

    def test_receipts_without_host_and_compactions_inside_and_between_turns(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        tokens = dict(model=self.MODEL, inputTokens=20, outputTokens=5)
        self.events.append(self.event("session.compaction_complete", "inside", 3,
                           dict(success=True, trigger="threshold", compactionTokensUsed=tokens)))
        self.events.append(self.receipt("compaction", 3, inp=20, out=5,
                                       interactionType="conversation-compaction"))
        self.events.append(self.event("assistant.turn_end", "end", 4, {}))
        self.events.append(self.event("session.compaction_complete", "between", 5,
                           dict(success=True, trigger="manual", compactionTokensUsed=tokens)))
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (140, 30))
        self.assertEqual(len(agg["compactions"]), 2)
        con = self.store()
        self.usage(con, 1, inp=120, out=25, cachedTokens=0)
        P._SDK_HOSTS["at"] = 0
        newer = self.parse(agg, write=False)
        self.assertEqual((newer["totals"]["in"], newer["totals"]["out"]), (140, 30))

    def test_partial_receipts_do_not_replace_full_host_totals(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        con = self.store()
        # The host includes additional child usage not present in the stream.
        self.usage(con, 1, inp=300, out=60, cachedTokens=0)
        agg = self.parse()
        self.assertEqual((agg["totals"]["in"], agg["totals"]["out"]), (300, 60))
        self.assertEqual(agg["copilot_sdk"]["usage_sources"], ["vscode_turn_usage"])

    def test_receipt_child_call_ids_are_scoped_and_twins_deduplicated(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        child_receipt = self.receipt("call1", 3, inp=50, out=10)
        child_receipt["agentId"] = "child"
        child_receipt["data"]["usage"]["accounting"]["sourceSessionId"] = "child-session"
        self.events.append(child_receipt)
        self.events.append(self.event("assistant.message", "child-message", 3,
                           dict(model=self.MODEL, apiCallId="call1"), agentId="child"))
        self.events.append(self.event("assistant.usage", "child-twin", 3,
                           dict(model=self.MODEL, apiCallId="call1", inputTokens=50, outputTokens=10), agentId="child"))
        foreign = self.receipt("foreign", 4, inp=999999)
        foreign["data"]["usage"]["accounting"]["sourceSessionId"] = "another-session"
        self.events.append(foreign)
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "req", "asst")], [150, 30, 2, 1])

    def test_receipt_without_assistant_message_still_records_call(self):
        self.events.append(self.event("user.message", "u1", 1, {"content": "Prompt"}))
        self.events.append(self.receipt("call1", 2))
        agg = self.parse()
        self.assertEqual([agg["totals"][k] for k in ("in", "out", "req", "asst")], [100, 20, 1, 0])

    def test_missing_call_receipt_keeps_partial_status(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        self.events.append(self.event("assistant.message", "a2", 3,
                           dict(model=self.MODEL, apiCallId="call2", content="Reply")))
        self.events.append(self.event("assistant.usage", "anonymous", 2,
                           dict(model=self.MODEL, inputTokens=100, outputTokens=20)))
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 100)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 1)

    def test_host_turn_without_reply_recovers_only_on_unambiguous_date(self):
        self.turn(1)
        self.events.append(self.event("user.message", "u2", 3, {"content": "Prompt"}))
        con = self.store()
        self.usage(con, 1)
        self.usage(con, 2)
        agg = self.parse()
        self.assertEqual(agg["totals"]["in"], 180)
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 0)
        self.events[-1]["timestamp"] = self.events[-1]["timestamp"].replace("2026-10-15", "2026-10-16")
        newer = self.parse(agg)
        self.assertEqual(newer["totals"]["in"], 90)
        self.assertEqual(newer["copilot_sdk"]["missing_turns"], 1)
        self.assertIn("undated_usage", newer["copilot_sdk"]["limitations"])

    def test_receipts_keep_multi_day_usage_on_its_recorded_date(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        self.events.append(self.event("assistant.message", "a2", 3,
                           {"content": "Reply", "model": self.MODEL, "apiCallId": "call2"}))
        self.events.append(self.receipt("call2", 3, inp=200, out=40))
        for e in self.events[-2:]:
            e["timestamp"] = e["timestamp"].replace("2026-10-15", "2026-10-16")
        con = self.store()
        self.usage(con, 1, inp=300, out=60, cachedTokens=0)
        agg = self.parse()
        self.assertEqual(agg["records"]["2026-10-15\tGLM 5.3"]["in"], 100)
        self.assertEqual(agg["records"]["2026-10-16\tGLM 5.3"]["in"], 200)
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "recorded")

    def test_receipt_reparse_version_and_malformed_receipt_preserve_previous(self):
        self.turn(1)
        self.events.append(self.receipt("call1", 2))
        agg = self.parse()
        agg["copilot_sdk"]["version"] = 1
        newer = self.parse(agg, write=False)
        self.assertIsNot(newer, agg)
        self.assertEqual(newer["copilot_sdk"]["version"], 3)
        saved = copy.deepcopy(newer)
        self.events.append(self.receipt("broken", 3, inp=-1))
        with self.assertRaises(ValueError): self.parse(newer)
        self.assertEqual(newer, saved)

    def test_known_compaction_triggers_preserved_unknown_not_invented(self):
        triggers = ["threshold", "context_limit_retry", "memory_pressure", "model_switch", "manual", "new-trigger"]
        for i, trigger in enumerate(triggers, 1):
            self.events.append(self.event("session.compaction_complete", f"c{i}", i,
                               dict(success=True, trigger=trigger)))
        agg = self.parse()
        self.assertEqual([c["trigger"] for c in agg["compactions"].values()], triggers[:-1] + ["unknown"])

    def test_compaction_tokens_do_not_hide_missing_conversation_usage(self):
        self.turn(1)
        self.events.append(self.event("session.compaction_complete", "c1", 3, dict(success=True,
            compactionTokensUsed=dict(model=self.MODEL, inputTokens=20, outputTokens=5))))
        agg = self.parse()
        self.assertEqual(agg["copilot_sdk"]["usage_status"], "partial")
        self.assertEqual(agg["copilot_sdk"]["missing_turns"], 1)

    def test_optional_migration_preserves_live_and_archived(self):
        cache = self.root / "cache.json"
        files = {"live": {"source": "copilot", "totals": {"in": 500}},
                 "archived": {"source": "codex", "archived": True, "totals": {"in": 123}}}
        for version in (57, 60):
            cache.write_text(json.dumps({"version": version, "files": files}))
            with patch.object(D, "CACHE_PATH", str(cache)), patch.dict(D._state, {"files": {}}), patch.dict(D._dirty, {"v": False}):
                D.load_cache()
                self.assertEqual(D._state["files"], files)
                self.assertTrue(D._dirty["v"])
                self.assertTrue(D._peer_cache_ok(version))


if __name__ == "__main__": unittest.main()
