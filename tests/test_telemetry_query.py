import copy
from pathlib import Path
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import telemetry_query as tq


TODAY = "2026-10-18"
LOCAL = "local-device-private-id"
REMOTE = "remote-device-private-id"


def row(day, source="codex", model="gpt-6-sol", project=r"C:\Users\uttam\work\alpha",
        ide="VS Code", device=LOCAL, **changes):
    value = {
        "date": day, "source": source, "model": model, "project": project, "ide": ide,
        "device": device, "in": 100, "out": 20, "cr": 10, "cc": 5,
        "cc5": 5, "cc1": 0, "reason": 4, "cost": 0.125,
        "req": 2, "asst": 1, "user": 1, "tools": 3, "active": 30.5,
    }
    value.update(changes)
    return value


def payload(records=None, sessions=None):
    return {
        "generated_at": 1792281600,
        "device": {"id": LOCAL, "name": "Uttam’s workstation"},
        "devices": [
            {"id": LOCAL, "name": "Uttam’s workstation"},
            {"id": REMOTE, "name": r"C:\private\remote-name"},
        ],
        "records": records if records is not None else [],
        "sessions": sessions if sessions is not None else [],
        "sessions_total": len(sessions or []),
        "model_vendor": {"gpt-6-sol": "OpenAI", "gpt-4o": "OpenAI", "cursor-model": "Cursor"},
        "home": r"C:\Users\uttam",
        "account_metadata": {"email": "person@example.test", "token": "secret"},
    }


def vector(*, cost=0.125, inp=100, out=20, cr=10, cc=5, asst=1, user=1,
           tools=3, active=30, req=2, cc5=5, cc1=0, reason=4, ws=0):
    return [cost, inp, out, cr, cc, asst, user, tools, 0, active, req, cc5, cc1, reason, ws]


class TelemetryQueryTests(unittest.TestCase):
    def setUp(self):
        self.data = payload([
            row("2026-10-17"),
            row("2026-10-17", "copilot", "gpt-4o", r"D:\code\beta", "VS Code",
                **{"in": 50, "out": 10, "cr": 20, "cc": 5, "cost": 0.25,
                   "exact": {"in": 20, "out": 5, "cr": 5, "cc": 0, "cc5": 0,
                             "cc1": 0, "reason": 1, "cost": 0.05, "req": 1,
                             "asst": 1, "user": 0, "tools": 1, "active": 8}}),
            row("2026-10-16", "cursor", "cursor-model", r"C:\Users\uttam\work\alpha", "Cursor"),
            row("2026-10-17", "codex", "gpt-6-sol", "remote-project", "VS Code", REMOTE,
                **{"in": 1000, "out": 0, "cr": 0, "cc": 0, "cost": 9.0}),
            row("2026-10-19", "codex", "gpt-6-sol", "future-project", "VS Code", LOCAL),
            row("2026-10-17", "codex", "(user)", r"C:\Users\uttam\work\alpha", "VS Code",
                **{"in": 0, "out": 0, "cr": 0, "cc": 0, "cost": 0, "req": 0,
                   "asst": 0, "user": 1, "tools": 0, "active": 0, "reason": 0}),
            row("2026-10-17", "future-tool", "custom-model", "mystery", "Other IDE", LOCAL,
                **{"in": 10, "out": 2, "cr": 0, "cc": 0}),
        ])

    def test_grouping_reconciles_daily_and_preserves_metric_semantics(self):
        result = tq.query_usage(self.data, {
            "period": "last_30_days", "group_by": ["date", "project", "tool"],
        }, today=TODAY, policy={"alias_key": "test-secret"})

        self.assertEqual(result["range"], {
            "from": "2026-09-19", "to": TODAY, "period": "last_30_days",
        })
        self.assertEqual(result["totals"]["tokens"], 367)
        self.assertEqual(result["totals"]["input_tokens"], 315)
        self.assertEqual(result["totals"]["fresh_input_tokens"], 260)
        self.assertEqual(result["totals"]["output_tokens"], 52)
        self.assertEqual(result["totals"]["cache_read_tokens"], 40)
        self.assertEqual(result["totals"]["cache_write_tokens"], 15)
        self.assertEqual(result["totals"]["reasoning_tokens"], 16)
        self.assertEqual(result["totals"]["requests"], 8)
        self.assertEqual(result["totals"]["assistant_messages"], 4)
        self.assertEqual(result["totals"]["user_prompts"], 5)
        self.assertEqual(result["totals"]["tool_calls"], 12)
        self.assertEqual(result["totals"]["active_seconds"], 122)
        self.assertEqual(result["total_rows"], 4)
        self.assertEqual(sum(item["tokens"] for item in result["rows"]), result["totals"]["tokens"])
        self.assertTrue(all("tool_label" in item for item in result["rows"]))
        self.assertEqual(result["coverage"]["future_record_rows_excluded"], 1)
        precision = {item["tool"]: item["precision"] for item in result["source_precision"]}
        self.assertEqual(precision["Copilot"], "mixed_recorded_and_estimated")
        self.assertEqual(precision["Cursor"], "lower_bound")
        self.assertTrue(any("text-based estimates" in warning for warning in result["warnings"]))
        self.assertTrue(all("C:\\Users" not in str(item) for item in result["rows"]))

    def test_totals_are_before_pagination_and_empty_group_by_is_totals_only(self):
        args = {"period": "all", "group_by": ["project"], "limit": 1, "offset": 1}
        result = tq.query_usage(self.data, args, today=TODAY, policy={"alias_key": "k"})
        self.assertEqual(result["total_rows"], 3)
        self.assertEqual(result["returned_rows"], 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["totals"]["tokens"], 367)
        only_totals = tq.query_usage(self.data, {"period": "all", "group_by": []}, today=TODAY)
        self.assertEqual(only_totals["rows"], [])
        self.assertEqual(only_totals["total_rows"], 0)
        self.assertEqual(only_totals["totals"]["tokens"], 367)

    def test_exact_only_matches_core_tool_flags_and_uses_copilot_exact_rows(self):
        result = tq.query_usage(self.data, {
            "period": "all", "group_by": ["tool"], "exact_only": True,
        }, today=TODAY)
        by_tool = {item["tool"]: item for item in result["rows"]}
        self.assertEqual(set(by_tool), {"codex", "copilot"})
        self.assertEqual(result["totals"]["tokens"], 165)
        self.assertEqual(by_tool["copilot"]["tokens"], 30)
        precision = {item["tool"]: item["precision"] for item in result["source_precision"]}
        self.assertEqual(precision["Copilot"], "recorded_exact_subrows")

    def test_model_and_provider_filters_keep_user_marker(self):
        result = tq.query_usage(self.data, {
            "period": "all", "models": ["not-a-model"], "providers": ["not-a-provider"],
            "tools": ["Codex"], "group_by": ["model"],
        }, today=TODAY)
        self.assertEqual(result["total_rows"], 1)
        self.assertEqual(result["totals"]["user_prompts"], 1)
        self.assertEqual(result["rows"][0]["model"], "(user)")

    def test_default_local_scope_aliases_and_opt_in_names_are_safe(self):
        result = tq.query_usage(self.data, {
            "period": "all", "group_by": ["project", "device"],
        }, today=TODAY, policy={"alias_key": "my-local-key"})
        serialized = repr(result)
        self.assertNotIn(LOCAL, serialized)
        self.assertNotIn(REMOTE, serialized)
        self.assertNotIn("remote-project", serialized)
        self.assertNotIn("Users", serialized)
        project_value = result["rows"][0]["project"]
        self.assertRegex(project_value, r"^project-[0-9a-f]{16}$")
        device_value = result["rows"][0]["device"]
        self.assertRegex(device_value, r"^device-[0-9a-f]{16}$")
        filtered = tq.query_usage(self.data, {
            "period": "all", "projects": [project_value], "group_by": ["project"],
        }, today=TODAY, policy={"alias_key": "my-local-key"})
        self.assertEqual(filtered["total_rows"], 1)
        self.assertEqual(filtered["filters"]["projects"], [project_value])

        names = tq.query_usage(self.data, {
            "period": "all", "group_by": ["project", "device"],
        }, today=TODAY, policy={"include_names": True, "alias_key": "my-local-key"})
        rendered = repr(names)
        self.assertIn("alpha", rendered)
        self.assertIn("Uttam’s workstation", rendered)
        self.assertNotIn("C:\\Users", rendered)
        self.assertNotIn("remote-name", rendered)
        with_peers = tq.query_usage(self.data, {
            "period": "all", "group_by": ["project", "device"],
        }, today=TODAY, policy={"include_peers": True, "include_names": True, "alias_key": "k"})
        self.assertEqual(with_peers["totals"]["tokens"], 1367)
        self.assertNotIn("remote-name", repr(with_peers))

        # Catalog aliases remain valid selectors even after enabling safe names.
        project_alias = tq.label("project", r"C:\Users\uttam\work\alpha", {"alias_key": "my-local-key"})
        device_alias = tq.label("device", LOCAL, {"alias_key": "my-local-key"})
        alias_filtered = tq.query_usage(self.data, {
            "period": "all", "projects": [project_alias], "devices": [device_alias], "group_by": [],
        }, today=TODAY, policy={"include_names": True, "alias_key": "my-local-key"})
        self.assertEqual(alias_filtered["totals"]["tokens"], 270)
        self.assertTrue(tq.matches_filter("project", r"C:\Users\uttam\work\alpha", [project_alias],
                                          policy={"include_names": True, "alias_key": "my-local-key"},
                                          payload=self.data))

    def test_no_local_id_does_not_accidentally_include_explicit_devices(self):
        data = payload([row("2026-10-17", device=None), row("2026-10-17", device=REMOTE)])
        data["device"] = {"name": "No ID"}
        result = tq.query_usage(data, {"period": "all", "group_by": []}, today=TODAY)
        self.assertEqual(result["totals"]["tokens"], 135)

    def test_invalid_ranges_pagination_filters_and_boolean_numbers_are_rejected(self):
        invalid = [
            ({"from": "2026-10-17"}, "both from and to"),
            ({"from": "2026-02-30", "to": TODAY}, "valid"),
            ({"from": "2010-01-01", "to": TODAY}, "may not exceed"),
            ({"from": "2026-10-19", "to": "2026-10-20"}, "on or before"),
            ({"limit": True}, "integer"),
            ({"offset": False}, "integer"),
            ({"limit": float("nan")}, "integer"),
            ({"models": [True]}, "text"),
            ({"group_by": ["path"]}, "group_by values"),
            ({"exact_only": "true"}, "boolean"),
        ]
        for args, message in invalid:
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, message):
                tq.query_usage(self.data, args, today=TODAY)
        clamped = tq.query_usage(self.data, {
            "from": "2026-10-17", "to": "2026-10-20", "group_by": [],
        }, today=TODAY)
        self.assertEqual(clamped["range"]["to"], TODAY)
        self.assertIn("Future dates were excluded", " ".join(clamped["warnings"]))

    def test_absent_and_nonfinite_usage_is_not_assumed_zero(self):
        incomplete = row("2026-10-17", **{"in": 10, "out": 3, "cr": 0, "cc": 0})
        incomplete.pop("cc1")
        incomplete["cost"] = float("nan")
        result = tq.query_usage(payload([incomplete]), {"period": "all", "group_by": []}, today=TODAY)
        self.assertIsNone(result["totals"]["estimated_cost_usd"])
        self.assertIn("cost", result["totals"]["missing_fields"])
        self.assertIn("cc1", result["totals"]["missing_fields"])
        self.assertEqual(result["totals"]["tokens"], 13)

    def test_select_records_and_queries_do_not_mutate_payload(self):
        before = copy.deepcopy(self.data)
        selected, context = tq.select_records(self.data, {"period": "all", "exact_only": True}, today=TODAY)
        self.assertTrue(selected)
        self.assertIn("coverage", context)
        tq.query_usage(self.data, {"period": "all", "group_by": ["date"]}, today=TODAY)
        self.assertEqual(self.data, before)

    def test_exact_copilot_selection_drops_parent_long_context_subsets(self):
        exact = {"in": 10, "out": 2, "cr": 0, "cc": 0, "cost": 0.01}
        data = payload([row("2026-10-17", "copilot", "gpt-4o", exact=exact,
                            lc_in=50, lc_out=10, lc_cc5=0, lc_cc1=0, lc_cr=0, lc_asst=1)])
        records, _ = tq.select_records(data, {"period": "all", "exact_only": True}, today=TODAY)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["in"], 10)
        for field in ("lc_in", "lc_out", "lc_cc5", "lc_cc1", "lc_cr", "lc_asst"):
            self.assertNotIn(field, records[0])

    def test_sessions_use_daily_scope_safe_alias_and_model_day_filter(self):
        d1 = "2026-10-17"
        d2 = "2026-10-16"
        gpt_day = vector(inp=100, out=20, cr=10, cc=5, cost=0.25, active=30, user=0)
        other_day = vector(inp=900, out=90, cr=10, cc=0, cost=1.25, active=300)
        user_day = vector(inp=0, out=0, cr=0, cc=0, cost=0, asst=0, user=1, tools=0, active=0, req=0)
        total_day = vector(inp=100, out=20, cr=10, cc=5, cost=0.25, active=30, user=1)
        session = {
            "source": "codex", "device": LOCAL, "rename_id": "full-native-session-id-123456789",
            "project": r"C:\private\work\alpha", "ide": "VS Code", "model": "gpt-6-sol",
            "title": "private session title", "path": r"C:\private\rollouts\secret.jsonl",
            "account_metadata": {"email": "secret@example.test"},
            "start": d2 + "T01:00:00", "end": d1 + "T02:00:00", "archived": False,
            "days": {d1: total_day, d2: other_day},
            "model_days": {"gpt-6-sol": {d1: gpt_day}, "other-model": {d2: other_day}, "(user)": {d1: user_day}},
        }
        data = payload([], [session])
        scoped = tq.list_sessions(data, {"from": d1, "to": d1}, today=TODAY,
                                  policy={"alias_key": "stable"})
        self.assertEqual(scoped["total_rows"], 1)
        self.assertEqual(scoped["sessions"][0]["tokens"], 135)
        self.assertEqual(scoped["sessions"][0]["estimated_cost_usd"], 0.25)
        self.assertEqual(scoped["totals"]["tokens"], 135)
        self.assertEqual(scoped["sessions"][0]["user_prompts"], 1)
        public = repr(scoped)
        for private in ("private session title", "secret.jsonl", "secret@example.test", session["rename_id"], LOCAL):
            self.assertNotIn(private, public)
        alias = scoped["sessions"][0]["session"]
        self.assertRegex(alias, r"^session-[0-9a-f]{16}$")
        self.assertIs(tq.resolve_session(data, alias, {"alias_key": "stable"}), session)
        self.assertIsNone(tq.resolve_session(data, session["rename_id"], {"alias_key": "stable"}))

        model_scoped = tq.list_sessions(data, {
            "from": d1, "to": d1, "models": ["gpt-6-sol"],
        }, today=TODAY, policy={"alias_key": "stable"})
        self.assertEqual(model_scoped["sessions"][0]["tokens"], 135)
        self.assertEqual(model_scoped["sessions"][0]["user_prompts"], 1)

    def test_session_source_device_and_full_id_define_alias_and_limited_detail_stays_unavailable(self):
        base = {"source": "codex", "device": LOCAL, "rename_id": "same-native-id"}
        self.assertNotEqual(tq.session_alias(base, {"alias_key": "key"}),
                            tq.session_alias({**base, "device": REMOTE}, {"alias_key": "key"}))
        self.assertNotEqual(tq.session_alias(base, {"alias_key": "key"}),
                            tq.session_alias({**base, "source": "claude"}, {"alias_key": "key"}))
        self.assertIsNone(tq.session_alias({"source": "codex", "device": LOCAL, "id": "short"}))

        limited = {
            "source": "claude", "device": LOCAL, "rename_id": "claude-full-id",
            "project": "proj", "model": "claude-sonnet", "end": "2026-10-17T02:00:00",
            "detail_limited": True, "days": None,
        }
        data = payload([], [limited])
        result = tq.list_sessions(data, {"period": "all"}, today=TODAY,
                                  policy={"alias_key": "stable"})
        self.assertEqual(result["sessions"][0]["usage_available"], False)
        self.assertIsNone(result["sessions"][0]["tokens"])
        self.assertIsNone(result["totals"]["tokens"])
        self.assertEqual(result["totals"]["sessions_with_unavailable_daily_data"], 1)
        self.assertTrue(any("daily detail" in warning for warning in result["warnings"]))

    def test_catalog_and_metadata_never_forward_unknown_payload_fields(self):
        data = self.data
        data["cache_error"] = "C:\\private\\traceback prompt text"
        data["session_titles"] = ["do not reveal"]
        result = tq.get_catalog(data, {"period": "all"}, today=TODAY,
                                policy={"alias_key": "catalog-key"})
        rendered = repr(result)
        for private in ("C:\\private", "prompt text", "do not reveal", "person@example.test", LOCAL, REMOTE):
            self.assertNotIn(private, rendered)
        self.assertRegex(result["catalog"]["projects"][0]["value"], r"^project-[0-9a-f]{16}$")
        self.assertEqual(result["supported_periods"], ["today", "last_7_days", "last_30_days", "month_to_date", "all", "custom"])
        self.assertIn("tokens", result["metric_definitions"])

    def test_malformed_tool_sources_are_sanitized_and_deduplicated_everywhere(self):
        secret_sources = [
            r"C:\Users\person\private-tool.exe",
            r"D:\private\another-tool.exe",
        ]
        data = payload([
            row("2026-10-17", source=source, model=r"C:\Users\person\private-model.json",
                **{"in": 3, "out": 1, "cr": 0, "cc": 0, "cost": 0.01})
            for source in secret_sources
        ])
        grouped = tq.query_usage(data, {"period": "all", "group_by": ["tool", "model"]}, today=TODAY)
        catalog = tq.get_catalog(data, {"period": "all"}, today=TODAY)
        rendered = repr({"grouped": grouped, "catalog": catalog})
        for secret in (*secret_sources, "private-tool.exe", "another-tool.exe", "private-model.json"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(grouped["total_rows"], 1)
        self.assertEqual(grouped["rows"][0]["tool"], "(unknown)")
        self.assertEqual(grouped["rows"][0]["tool_label"], "(unknown)")
        self.assertEqual(grouped["rows"][0]["model"], "(unknown)")
        self.assertEqual(catalog["catalog_counts"]["tools"], 1)
        self.assertEqual(catalog["catalog"]["tools"], [{"value": "(unknown)", "label": "(unknown)"}])

    def test_freshness_uses_last_completed_scan_not_payload_build_time(self):
        data = payload([])
        scan_epoch = time.time() - 20
        data["meta"] = {
            "last_refresh": scan_epoch, "building": True,
            "cache_error": r"C:\private\account\prompt-secret",
            "unrecognized": "must not be forwarded",
        }
        result = tq.query_usage(data, {"group_by": []}, today=TODAY)
        self.assertEqual(result["last_scan_at_epoch"], scan_epoch)
        self.assertTrue(result["last_scan_at"].endswith("Z"))
        self.assertGreaterEqual(result["scan_age_seconds"], 19)
        self.assertLess(result["scan_age_seconds"], 23)
        self.assertIs(result["building"], True)
        self.assertIs(result["scan_timestamp_in_future"], False)
        self.assertIn("response_built_at", result)
        self.assertNotIn("observed_at", result)
        self.assertNotIn("prompt-secret", repr(result))
        self.assertNotIn("must not be forwarded", repr(result))
        self.assertTrue(any("scan is running" in warning for warning in result["warnings"]))

        data["meta"]["last_refresh"] = time.time() + 60
        future = tq.query_usage(data, {"group_by": []}, today=TODAY)
        self.assertIs(future["scan_timestamp_in_future"], True)
        self.assertIsNone(future["scan_age_seconds"])
        self.assertTrue(any("timestamp is in the future" in warning for warning in future["warnings"]))

        data["meta"] = {"building": False}
        missing = tq.query_usage(data, {"group_by": []}, today=TODAY)
        self.assertIsNone(missing["last_scan_at"])
        self.assertIsNone(missing["last_scan_at_epoch"])
        self.assertIsNone(missing["scan_age_seconds"])
        self.assertIsNone(missing["scan_timestamp_in_future"])
        self.assertIs(missing["building"], False)

    def test_catalog_is_bounded_with_full_counts_and_deterministic_order(self):
        records = [row("2026-10-17", project=f"project-{i:04d}") for i in range(507)]
        data = payload(records)
        first = tq.get_catalog(data, {"period": "all"}, today=TODAY,
                               policy={"alias_key": "catalog-bound"})
        second = tq.get_catalog(data, {"period": "all"}, today=TODAY,
                                policy={"alias_key": "catalog-bound"})
        projects = first["catalog"]["projects"]
        self.assertEqual(first["catalog_item_limit"], 500)
        self.assertEqual(first["catalog_counts"]["projects"], 507)
        self.assertTrue(first["catalog_truncated"]["projects"])
        self.assertEqual(len(projects), 500)
        self.assertEqual(projects, second["catalog"]["projects"])
        self.assertFalse(first["catalog_truncated"]["tools"])


if __name__ == "__main__":
    unittest.main()
