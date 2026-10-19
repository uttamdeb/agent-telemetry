"""SDK child detail rows must not become additional analytics usage."""
import copy
import unittest

import dashboard as D
import test_claude_subagent_parents as C


class CopilotWebChildTests(unittest.TestCase):
    def parent(self, device=None):
        agg = C.aggregate("/fixture/sdk/events.jsonl", source="copilot", device=device)
        session = agg["sessions"][0]
        session["_sid"] = "parent-native-id"
        session["ide"] = "VS Code"
        session["copilot_sdk"] = {"usage_status": "recorded", "agents": [
            {"id": "child-one", "title": "Review A", "model": "GLM 5.3", "usage_status": "recorded",
             "records": {"2026-10-18\tGLM 5.3": {"in": 20, "out": 5}},
             "start": "2026-10-18T10:00:00Z", "end": "2026-10-18T10:02:00Z",
             "activity_dates": ["2026-10-18"]},
            {"id": "child-two", "title": "Review B", "model": "GLM 5.3", "usage_status": "unavailable",
             "records": {}, "activity_dates": ["2026-10-19"],
             "start": "2026-10-19T10:00:00Z", "end": "2026-10-19T10:02:00Z"}]}
        return agg

    def test_children_have_full_parent_links_and_dated_breakdowns_without_mutating_ledger(self):
        agg = self.parent()
        before = copy.deepcopy(agg)
        payload = C.ClaudeSubagentParentTests().payload([agg])
        self.assertEqual(agg, before)
        self.assertEqual(len(payload["sessions"]), 1)
        children = payload["session_children"]
        self.assertEqual(len(children), 2)
        self.assertEqual(payload["session_children_total"], 2)
        for child in children:
            self.assertTrue(child["subagent"])
            self.assertTrue(child["usage_in_parent"])
            self.assertEqual(child["subagent_parent"]["id"], "parent-native-id")
            self.assertEqual(child["device"], "local")
            self.assertEqual(child["ide"], "VS Code")
            self.assertFalse(child["rename_available"])
            self.assertNotIn("cwd", child)
            self.assertNotIn("bytes", child)
            self.assertNotIn("user", child)
        recorded = next(c for c in children if c["title"] == "Review A")
        self.assertEqual(recorded["rename_id"], "parent-native-id/agent/child-one")
        self.assertEqual(recorded["days"]["2026-10-18"][1:3], [20, 5])
        self.assertGreater(recorded["days"]["2026-10-18"][0], 0)
        missing = next(c for c in children if c["title"] == "Review B")
        self.assertEqual(missing["copilot_sdk"]["usage_status"], "unavailable")
        self.assertEqual(missing["days"], {})

    def test_analytics_and_primary_session_rows_are_unchanged(self):
        agg = self.parent()
        baseline = copy.deepcopy(C.ClaudeSubagentParentTests().payload([agg]))
        agg["sessions"][0]["copilot_sdk"]["agents"] = []
        without = copy.deepcopy(C.ClaudeSubagentParentTests().payload([agg]))
        for key in ("records", "projects", "hourly", "tools", "activity", "sessions_total"):
            self.assertEqual(baseline.get(key), without.get(key), key)
        for payload in (baseline, without):
            payload["sessions"][0]["copilot_sdk"].pop("agents")
            payload.pop("session_children")
            payload.pop("session_children_total")
        self.assertEqual(baseline, without)

    def test_devices_archives_duplicates_and_invalid_optional_metadata(self):
        local, remote = self.parent(), self.parent("remote")
        remote["archived"] = True
        agents = local["sessions"][0]["copilot_sdk"]["agents"]
        agents.extend([copy.deepcopy(agents[0]), {"id": "../invalid"}, {"id": "bad-record", "records": {"bad": []}}])
        payload = C.ClaudeSubagentParentTests().payload([local], peers=[("peer", remote)])
        children = payload["session_children"]
        self.assertEqual(sum(c["rename_id"].endswith("/child-one") for c in children), 2)
        self.assertTrue(all(c["archived"] for c in children if c["device"] == "remote"))
        self.assertFalse(any("../invalid" in c["rename_id"] for c in children))


if __name__ == "__main__":
    unittest.main()
