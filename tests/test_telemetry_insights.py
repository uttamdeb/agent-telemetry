"""Synthetic regression coverage for the shared MCP insights engine."""

import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from telemetry_insights import compare_usage, get_insights
from telemetry_query import label


FIXTURE = Path(__file__).parent / "fixtures" / "mcp_insights_parity.json"


def _strings(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(key)
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, str):
        yield value


class TelemetryInsightsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def setUp(self):
        self.payload = copy.deepcopy(self.fixture["payload"])
        self.today = self.fixture["range"]["today"]
        self.range_args = {"from": self.fixture["range"]["from"],
                           "to": self.fixture["range"]["to"]}

    def test_automatic_comparison_reconciles_and_uses_policy_safe_history(self):
        before = copy.deepcopy(self.payload)
        result = compare_usage(self.payload, {**self.range_args, "metric": "tokens", "dimension": "project"},
                              today=self.today)

        self.assertTrue(result["available"])
        self.assertEqual(result["current"]["total"], self.fixture["expected"]["current_tokens"])
        self.assertEqual(result["baseline"]["total"], self.fixture["expected"]["baseline_tokens"])
        self.assertEqual(result["delta"], self.fixture["expected"]["delta_tokens"])
        self.assertTrue(result["reconciles"])
        self.assertEqual((result["baseline"]["from"], result["baseline"]["to"]),
                         ("2026-10-03", "2026-10-04"))
        values = [(row["current"], row["baseline"], row["delta"]) for row in result["contributions"]]
        self.assertEqual(values, [(4366, 64, 4302), (450, 11, 439)])
        self.assertEqual(result["coverage"]["available_date_range"]["from"], "2026-10-03")
        self.assertFalse(any("earliest available usage history" in warning
                             for warning in result["coverage_warnings"]))
        self.assertEqual(self.payload, before, "Analysis must not mutate the ledger payload.")

    def test_project_and_device_contribution_keys_stay_aliased_when_names_are_enabled(self):
        policy = {"include_names": True, "include_peers": True, "alias_key": "fixture-alias-key"}
        result = compare_usage(self.payload, {**self.range_args, "dimension": "project"},
                              policy=policy, today=self.today)
        raw_project = "C:\\Users\\Private\\Alpha Workspace"
        project_alias = label("project", raw_project, policy={**policy, "include_names": False})
        alpha = next(row for row in result["contributions"] if row["label"] == "Alpha Workspace")
        self.assertEqual(alpha["key"], project_alias)
        self.assertNotEqual(alpha["key"], raw_project)
        self.assertIn("Alpha Workspace", alpha["label"])

        devices = compare_usage(self.payload, {**self.range_args, "dimension": "device"},
                                policy=policy, today=self.today)
        local_alias = label("device", "local-device-secret-71",
                            policy={**policy, "include_names": False})
        self.assertEqual(devices["contributions"][0]["key"], local_alias)
        self.assertEqual(devices["contributions"][0]["label"], "Local Device Secret Name")

    def test_comparison_limit_reconciles_with_other_row(self):
        result = compare_usage(self.payload, {**self.range_args, "dimension": "project", "limit": 1},
                              today=self.today)
        self.assertEqual(len(result["contributions"]), 2)
        self.assertTrue(result["contributions"][-1]["other"])
        self.assertEqual(result["omitted_contribution_count"], 1)
        self.assertTrue(result["reconciles"])
        self.assertEqual(sum(row["delta"] for row in result["contributions"]), result["delta"])

    def test_exact_copilot_rows_replace_estimated_parent_usage(self):
        result = compare_usage(self.payload, {
            "from": "2026-10-05", "to": "2026-10-05", "exact_only": True,
            "dimension": "token_type",
        }, today=self.today)
        self.assertTrue(result["available"])
        self.assertEqual(result["current"]["total"], 396)
        self.assertNotEqual(result["current"]["total"], 370 + 3996)
        self.assertTrue(result["reconciles"])

    def test_cost_token_parts_use_dated_rates_and_put_logged_remainder_in_other(self):
        result = compare_usage(self.payload, {
            "from": "2026-10-05", "to": "2026-10-05", "tools": ["codex"],
            "metric": "cost", "dimension": "token_type",
        }, today=self.today)
        self.assertEqual(result["current"]["total"], 0.001)
        self.assertIsNone(result["percent_change"], "A zero baseline has no defined percent growth.")
        current = {row["key"]: row["current"] for row in result["contributions"]}
        for key, expected in self.fixture["expected"]["october_5_codex_cost_parts"].items():
            self.assertAlmostEqual(current[key], expected, places=12)
        self.assertAlmostEqual(sum(current.values()), result["current"]["total"], places=12)
        self.assertTrue(result["reconciles"])

    def test_all_history_requires_an_explicit_baseline_and_zero_baseline_growth_is_null(self):
        unavailable = compare_usage(self.payload, {"period": "all", "dimension": "project"},
                                    today=self.today)
        self.assertFalse(unavailable["available"])
        self.assertIn("provide baseline_from and baseline_to", unavailable["reason"])

        explicit = compare_usage(self.payload, {
            "period": "all", "baseline_from": "2019-12-31", "baseline_to": "2019-12-31",
            "dimension": "project",
        }, today=self.today)
        self.assertTrue(explicit["available"])
        self.assertEqual(explicit["baseline"]["total"], 0)
        self.assertIsNone(explicit["percent_change"])

    def test_missing_metric_fields_are_unavailable(self):
        row = next(row for row in self.payload["records"]
                   if row.get("date") == "2026-10-05" and row.get("source") == "codex")
        row.pop("cc")
        result = compare_usage(self.payload, {
            "from": "2026-10-05", "to": "2026-10-05", "metric": "tokens",
        }, today=self.today)
        self.assertFalse(result["available"])
        self.assertEqual(result["missing_fields"], ["cc"])

    def test_auxiliary_session_filters_accept_aliases_and_do_not_echo_private_data(self):
        policy = {"include_names": True, "include_peers": True, "alias_key": "fixture-alias-key"}
        project = "C:\\Users\\Private\\Alpha Workspace"
        project_alias = label("project", project, policy={**policy, "include_names": False})
        device_alias = label("device", "local-device-secret-71",
                             policy={**policy, "include_names": False})
        args = {**self.range_args, "projects": [project_alias], "devices": [device_alias]}
        result = get_insights(self.payload, args, policy=policy, today=self.today)
        cache = next(finding for finding in result["findings"] if finding["id"] == "cache-waste")
        self.assertEqual(cache["evidence"]["sessions"], 1)
        self.assertEqual(cache["evidence"]["cache_write_tokens"], 25001)
        self.assertEqual(result["scope"]["filters"]["projects"], [project_alias])

        serialized = json.dumps(result, sort_keys=True)
        for sentinel in self.fixture["private_sentinels"]:
            self.assertNotIn(sentinel, serialized)
        self.assertNotIn("SECRET_MCP_COMMAND_DO_NOT_ECHO", serialized)

    def test_peer_scope_controls_history_floor_and_device_contributions(self):
        args = {"from": "2020-01-01", "to": "2020-01-01", "dimension": "device"}
        local_only = compare_usage(self.payload, args, today=self.today)
        self.assertEqual(local_only["current"]["total"], 0)
        self.assertEqual(local_only["contributions"], [])

        shared = compare_usage(self.payload, args,
                               policy={"include_peers": True, "alias_key": "fixture-alias-key"},
                               today=self.today)
        self.assertEqual(shared["current"]["total"], 3600)
        self.assertTrue(shared["reconciles"])
        self.assertEqual(shared["contributions"][0]["key"],
                         label("device", "peer-device-secret-29",
                               policy={"include_names": False, "alias_key": "fixture-alias-key"}))
        self.assertNotIn("peer-device-secret-29", json.dumps(shared))

    def test_summary_only_sessions_are_used_only_when_the_range_contains_the_whole_session(self):
        payload = copy.deepcopy(self.payload)
        payload["sessions"] = payload["sessions"][:1]
        payload["sessions_total"] = 1
        session = payload["sessions"][0]
        session["end"] = "2026-10-06T09:30:00Z"
        session.pop("days")
        session.pop("model_days")

        full = get_insights(payload, {"from": "2026-10-05", "to": "2026-10-06"}, today=self.today)
        cache = next(finding for finding in full["findings"] if finding["id"] == "cache-waste")
        self.assertEqual(cache["evidence"]["cache_write_tokens"], 25001)
        self.assertTrue(any("summary-only detail" in caveat for caveat in full["caveats"]))

        partial = get_insights(payload, {"from": "2026-10-06", "to": "2026-10-06"}, today=self.today)
        self.assertNotIn("cache-waste", {finding["id"] for finding in partial["findings"]})
        unsupported = {item["id"]: item for item in partial["unsupported"]}
        for identifier in ("cache-waste", "model-fit", "context-tax", "subagents",
                           "subagent-share", "heavy-openers"):
            self.assertIn(identifier, unsupported)
            self.assertEqual(unsupported[identifier]["affected_sessions"], 1)

    def test_compaction_only_date_does_not_reuse_whole_session_usage(self):
        payload = copy.deepcopy(self.payload)
        session = payload["sessions"][0]
        session["end"] = "2026-10-06T09:30:00Z"
        session["compactions"] = [{"date": "2026-10-06", "trigger": "manual"}]
        payload["sessions"] = [session]
        payload["sessions_total"] = 1

        result = get_insights(payload, {"from": "2026-10-06", "to": "2026-10-06"}, today=self.today)

        # The session has 25,001 cache-write tokens on 10-05 and a compaction
        # on 10-06, but no usage vector for the selected date. Compactions do
        # not make its whole-session counters evidence for 10-06.
        self.assertNotIn("cache-waste", {finding["id"] for finding in result["findings"]})

    def test_model_filtered_summary_without_daily_model_splits_is_unavailable(self):
        payload = copy.deepcopy(self.payload)
        payload["sessions"] = payload["sessions"][:1]
        payload["sessions_total"] = 1
        payload["sessions"][0].pop("model_days")
        result = get_insights(payload, {"from": "2026-10-05", "to": "2026-10-05", "models": ["Model M"]},
                              today=self.today)
        self.assertNotIn("cache-waste", {finding["id"] for finding in result["findings"]})
        item = next(item for item in result["unsupported"] if item["id"] == "cache-waste")
        self.assertIn("per-model daily usage", item["reason"])

    def test_missing_detector_inputs_and_short_archived_vectors_are_not_zero_filled(self):
        payload = copy.deepcopy(self.payload)
        row = next(row for row in payload["records"]
                   if row.get("date") == "2026-10-05" and row.get("source") == "codex")
        row["reason"] = None
        result = get_insights(payload, {
            "from": "2026-10-05", "to": "2026-10-05", "tools": ["codex"],
        }, today=self.today)
        unsupported = {item["id"]: item for item in result["unsupported"]}
        self.assertIn("thinking", unsupported)
        self.assertIn("reason", unsupported["thinking"]["required_fields"])

        payload = copy.deepcopy(self.payload)
        payload["sessions"] = payload["sessions"][:1]
        payload["sessions_total"] = 1
        session = payload["sessions"][0]
        short = [0.06, 1000, 100, 2000, 25001, 10, 10]
        session["days"]["2026-10-05"] = short
        session["model_days"]["Model M"]["2026-10-05"] = short
        result = get_insights(payload, {"from": "2026-10-05", "to": "2026-10-05"}, today=self.today)
        unsupported = {item["id"]: item for item in result["unsupported"]}
        self.assertIn("subagents", unsupported)
        self.assertIn("tools", unsupported["subagents"]["required_fields"])
        self.assertIn("model-fit", unsupported)

    def test_instruction_sent_bytes_falls_back_safely_and_evidence_is_bounded(self):
        payload = copy.deepcopy(self.payload)
        payload["context_files"] = [
            {"source": "claude", "project": "*",
             "bytes": 100000000, "sent": None, "file": f"C:\\private\\instruction-{index}.md",
             "since": "2026-10-03"}
            for index in range(25)
        ]
        result = get_insights(payload, self.range_args, today=self.today)
        finding = next(item for item in result["findings"] if item["id"] == "instruction-files")
        self.assertEqual(len(finding["evidence"]["file_sizes"]), 20)
        self.assertEqual(finding["evidence"]["file_sizes_omitted"], 5)
        self.assertEqual(finding["evidence"]["file_sizes"][0]["sent_bytes"], 100000000)
        serialized = json.dumps(result)
        self.assertNotIn("instruction-0.md", serialized)
        self.assertNotIn("C:\\private", serialized)

    def test_invalid_instruction_sent_bytes_are_unsupported(self):
        payload = copy.deepcopy(self.payload)
        payload["context_files"][0]["sent"] = "unknown-size"
        result = get_insights(payload, self.range_args, today=self.today)
        self.assertFalse(any(item["id"] == "instruction-files" for item in result["findings"]))
        self.assertTrue(any(item["id"] == "instruction-files" for item in result["unsupported"]))

    def test_retry_evidence_sanitizes_unknown_source_labels(self):
        payload = copy.deepcopy(self.payload)
        payload["activity"].append({
            "date": "2026-10-05", "source": "C:\\Users\\Private\\secret-tool.exe",
            "model": "Model M", "project": "C:\\Users\\Private\\Alpha Workspace",
            "ide": "VS Code", "device": "local-device-secret-71", "edits": 10,
            "oneshot": 5, "retry_cost": 5, "edit_cost": 8,
        })
        result = get_insights(payload, self.range_args, today=self.today)
        serialized = json.dumps(result)
        self.assertNotIn("secret-tool.exe", serialized)
        self.assertNotIn("C:\\Users\\Private\\secret-tool", serialized)
        retry = next(item for item in result["findings"] if item["id"] == "retry-tax")
        self.assertIn("(unknown)", [item["tool"] for item in retry["evidence"]["tool_rates"]])

    def test_model_substitution_evidence_is_bounded(self):
        payload = copy.deepcopy(self.payload)
        payload["sessions"] = []
        payload["sessions_total"] = 21
        payload["prices"]["Small Model"] = [0.1, 0.5, 0, 0, 0]
        payload["pricing"]["Small Model"] = [0.1, 0.5, 0, 0, 0]
        payload["model_vendor"]["Small Model"] = "Example Provider"
        payload["records"].append({
            "date": "2026-10-05", "source": "claude", "model": "Small Model",
            "project": "C:\\Users\\Private\\Alpha Workspace", "ide": "VS Code",
            "device": "local-device-secret-71", "in": 100, "out": 10, "cr": 0,
            "cc": 0, "cost": 0.00002, "asst": 1, "user": 1, "active": 1,
            "req": 1,
        })
        vector = [2.5, 1000000, 100, 0, 0, 1, 1, 1, 0, 60, 1, 0, 0, 0, 0]
        for index in range(21):
            model = f"Expensive Model {index:02d}"
            payload["prices"][model] = [2, 10, 2.5, 4, 0.2]
            payload["pricing"][model] = [2, 10, 2.5, 4, 0.2]
            payload["model_vendor"][model] = "Example Provider"
            payload["records"].append({
                "date": "2026-10-05", "source": "claude", "model": model,
                "project": "C:\\Users\\Private\\Alpha Workspace", "ide": "VS Code",
                "device": "local-device-secret-71", "in": 1000000, "out": 100,
                "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
                "cost": 2.5, "asst": 1, "user": 1, "active": 60, "req": 1,
            })
            payload["sessions"].append({
                "source": "claude", "model": model,
                "project": "C:\\Users\\Private\\Alpha Workspace", "ide": "VS Code",
                "device": "local-device-secret-71", "start": "2026-10-05T09:00:00Z",
                "end": "2026-10-05T09:30:00Z", "subagent": False, "cost": 2.5,
                "out": 100, "tools": 1, "user": 1, "side": 0, "subagents": 0,
                "open_ctx": 1000, "days": {"2026-10-05": vector},
                "model_days": {model: {"2026-10-05": vector}},
            })
        result = get_insights(payload, {
            "from": "2026-10-05", "to": "2026-10-05", "tools": ["claude"],
        }, today=self.today)
        finding = next(item for item in result["findings"] if item["id"] == "model-fit")
        self.assertEqual(len(finding["evidence"]["substitutions"]), 20)
        self.assertEqual(finding["evidence"]["substitutions_omitted"], 1)

    def test_optimize_numeric_thresholds_and_overlap_are_explicit(self):
        result = get_insights(self.payload, {
            **self.range_args, "tools": ["claude", "codex"],
        }, today=self.today)
        by_id = {finding["id"]: finding for finding in result["findings"]}
        self.assertIn("cache-waste", by_id)
        self.assertEqual(by_id["cache-waste"]["evidence"]["sessions"], 2)
        self.assertIn("retry-tax", by_id)
        self.assertAlmostEqual(by_id["retry-tax"]["estimated_saving"], 2.0)
        self.assertTrue(by_id["retry-tax"]["scope"]["tools"])
        self.assertTrue(result["findings_overlap"])
        self.assertIn("do not add them together", result["caveats"][0])


if __name__ == "__main__":
    unittest.main()
