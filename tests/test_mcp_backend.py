"""MCP dispatch stays within read-only builders and sanitizes private side data."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mcp_access
import mcp_backend
import native
import telemetry_query


TODAY = "2026-10-18"
LOCAL = "local-device-sentinel"
REMOTE = "peer-device-sentinel"
NATIVE_ID = "native-session-id-secret"
PROJECT = r"C:\Users\person\private-project"


def _vector():
    return [0.25, 100, 20, 10, 5, 1, 1, 3, 0, 30, 2, 5, 0, 4, 0]


def _payload():
    session = {
        "source": "codex", "device": LOCAL, "rename_id": NATIVE_ID,
        "project": PROJECT, "ide": "VS Code", "model": "gpt-6-sol",
        "title": "session title sentinel", "path": r"C:\private\rollout.jsonl",
        "start": TODAY + "T10:00:00Z", "end": TODAY + "T10:01:00Z",
        "days": {TODAY: _vector()},
        "model_days": {"gpt-6-sol": {TODAY: _vector()}},
    }
    return {
        "generated_at": time.time(),
        "device": {"id": LOCAL, "name": "private host name"},
        "devices": [
            {"id": LOCAL, "name": "private host name"},
            {"id": REMOTE, "name": "peer host name"},
        ],
        "records": [
            {"date": TODAY, "source": "codex", "model": "gpt-6-sol", "project": PROJECT,
             "ide": "VS Code", "device": LOCAL, "in": 100, "out": 20, "cr": 10, "cc": 5,
             "cc5": 5, "cc1": 0, "reason": 4, "cost": 0.25, "req": 2, "asst": 1,
             "user": 1, "tools": 3, "active": 30},
            {"date": TODAY, "source": "codex", "model": "gpt-6-sol", "project": "peer-private-project",
             "ide": "VS Code", "device": REMOTE, "in": 1000, "out": 0, "cr": 0, "cc": 0,
             "cc5": 0, "cc1": 0, "reason": 0, "cost": 9.0, "req": 1, "asst": 1,
             "user": 1, "tools": 0, "active": 0},
        ],
        "sessions": [session],
        "sessions_total": 1,
        "model_vendor": {"gpt-6-sol": "OpenAI"},
    }


def _request(tool, arguments):
    return json.dumps({"tool": tool, "arguments": arguments}, separators=(",", ":")).encode("utf-8")


class MCPBackendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-mcp-backend-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.support = self.root / "AgentTelemetryNative"
        self.data = self.root / "ledger"
        self.data.mkdir()
        self.url = "http://127.0.0.1:7878"
        item = patch.object(native, "support_dir", return_value=self.support)
        item.start()
        self.addCleanup(item.stop)
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        self.token = mcp_access.credentials(self.url)
        self.calls = {"payload": 0, "health": 0, "accounts": 0, "timeline": 0}
        self.timeline_expectation = (LOCAL, "codex", NATIVE_ID)
        self.timeline_expected_dates = {"date_from": None, "date_to": None}
        self.payload = _payload()
        self.builders = {
            "payload": self._payload,
            "health": self._health,
            "accounts": self._accounts,
            "timeline": self._timeline,
            "is_building": lambda: False,
        }

    def _payload(self):
        self.calls["payload"] += 1
        return self.payload

    def _health(self):
        self.calls["health"] += 1
        return {"status": "available", "generation": 2, "sources": []}

    def _accounts(self):
        self.calls["accounts"] += 1
        return {"entries": [], "stale_after_seconds": 300}

    def _timeline(self, device, source, session_id, **kwargs):
        self.calls["timeline"] += 1
        self.assertEqual((device, source, session_id), self.timeline_expectation)
        self.assertEqual(kwargs, self.timeline_expected_dates)
        if source == "cursor":
            return {
                "available": True, "truncated": False, "detail": "source log",
                "events": [
                    {"kind": "user_turn", "timestamp": TODAY + "T10:00:00Z",
                     "requested_model": "cursor-requested-model", "prompt": "CURSOR_PROMPT_SENTINEL",
                     "path": "/Users/private/Cursor/state.vscdb"},
                    {"kind": "request", "timestamp": TODAY + "T10:00:01Z",
                     "model": "cursor-observed-model", "input": 12, "output": 3,
                     "usage_status": "partial",
                     "usage_availability": {"input_output": "partial", "cache_read": "unavailable"},
                     "source_path": "/Users/private/Cursor/state.vscdb",
                     "request_id": "CURSOR_REQUEST_ID_SENTINEL"},
                ],
            }
        return {
            "available": True, "truncated": False, "detail": "source log",
            "events": [{
                "kind": "request", "timestamp": TODAY + "T10:00:01Z", "model": "gpt-6-sol",
                "input": 10, "native_id": NATIVE_ID, "prompt": "PROMPT_SENTINEL",
                "label": r"C:\Users\person\secret.log", "extra": "SENTINEL",
            }],
        }

    def execute(self, tool, arguments=None):
        return mcp_backend.execute_query(
            _request(tool, arguments or {}), "Bearer " + self.token, self.url, self.data,
            self.builders,
        )

    def test_usage_and_catalog_use_only_user_consent_policy(self):
        result = self.execute("get_usage", {
            "from": TODAY, "to": TODAY, "group_by": ["project", "device"],
        })
        serialized = json.dumps(result)
        self.assertEqual(result["totals"]["tokens"], 135)
        self.assertNotIn(PROJECT, serialized)
        self.assertNotIn(LOCAL, serialized)
        self.assertNotIn(REMOTE, serialized)
        self.assertNotIn("peer-private-project", serialized)
        self.assertEqual(result["privacy_warning"], mcp_access.WARNING)
        catalog = self.execute("get_catalog", {"from": TODAY, "to": TODAY})
        self.assertNotIn(PROJECT, json.dumps(catalog))
        self.assertNotIn("peer-private-project", json.dumps(catalog))

    def test_session_results_and_timeline_never_forward_title_native_id_or_source_extras(self):
        sessions = self.execute("list_sessions", {"from": TODAY, "to": TODAY})
        serialized = json.dumps(sessions)
        self.assertNotIn(NATIVE_ID, serialized)
        self.assertNotIn("session title sentinel", serialized)
        selector = sessions["sessions"][0]["session"]
        timeline = self.execute("get_session_timeline", {"session": selector})
        self.assertEqual(timeline["events"][0]["model"], "gpt-6-sol")
        self.assertEqual(timeline["events"][0]["input"], 10)
        rendered = json.dumps(timeline)
        for secret in (NATIVE_ID, "PROMPT_SENTINEL", "SENTINEL", "secret.log"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(timeline["detail"], "source log")

    def test_cursor_mcp_timeline_forwards_date_bounds_and_never_path_or_source_content(self):
        cursor_id = "cursor-full-composer-id-123456789"
        cursor_session = {
            "source": "cursor", "device": LOCAL, "rename_id": cursor_id,
            "project": PROJECT, "ide": "Cursor", "model": "cursor-observed-model",
            "title": "private Cursor title", "path": "/Users/private/Cursor/state.vscdb",
            "start": TODAY + "T10:00:00Z", "end": TODAY + "T10:01:00Z",
            "archived": False, "usage_status": "partial",
            "undated_usage": {"in": 90, "out": 12, "asst": 2,
                              "model": "PRIVATE-UNDATED-MODEL",
                              "prompt": "PRIVATE-UNDATED-PROMPT"},
        }
        self.payload["sessions"].append(cursor_session)
        sessions = self.execute("list_sessions", {"from": "2026-10-17", "to": TODAY})
        cursor_item = next(item for item in sessions["sessions"] if item["tool"] == "cursor")
        self.assertEqual(cursor_item["undated_usage"], {"in": 90, "out": 12, "asst": 2})
        self.assertEqual(cursor_item["undated_usage_scope"],
                         "whole-session (not filtered by date range)")
        self.assertNotIn("model", cursor_item["undated_usage"])
        self.assertIsNone(cursor_item["tokens"])
        self.assertEqual(sessions["totals"]["tokens"], 135,
                         "undated Cursor usage is excluded from selected-range MCP totals")
        listed_json = json.dumps(sessions)
        self.assertNotIn("PRIVATE-UNDATED-MODEL", listed_json)
        self.assertNotIn("PRIVATE-UNDATED-PROMPT", listed_json)
        policy = {"include_peers": False, "alias_key": "cursor-safe-timeline-test-key"}
        selector = telemetry_query.session_alias(cursor_session, policy)
        self.timeline_expectation = (LOCAL, "cursor", cursor_id)
        self.timeline_expected_dates = {"date_from": "2026-10-17", "date_to": TODAY}

        result = mcp_backend._timeline(self.payload, {
            "session": selector, "from": "2026-10-17", "to": TODAY,
        }, policy, self._timeline)
        self.assertEqual(result["detail"], "source log")
        self.assertEqual(result["events"][0]["requested_model"], "cursor-requested-model")
        self.assertEqual(result["events"][1]["usage_status"], "partial")
        self.assertEqual(result["events"][1]["usage_availability"]["cache_read"], "unavailable")
        rendered = json.dumps(result)
        for secret in (cursor_id, "private Cursor title", "CURSOR_PROMPT_SENTINEL",
                       "CURSOR_REQUEST_ID_SENTINEL", "/Users/private", "state.vscdb"):
            self.assertNotIn(secret, rendered)
        with self.assertRaises(mcp_backend.mcp_tools.ToolArgumentError):
            mcp_backend.mcp_tools.validate_arguments("get_session_timeline", {
                "session": selector, "path": "/Users/private/Cursor/state.vscdb",
            })

    def test_timeline_reasons_are_fixed_and_remote_or_archived_sources_are_never_read(self):
        remote_session = {
            "source": "codex", "device": REMOTE, "rename_id": "remote-native-id", "archived": False,
        }
        self.payload["sessions"].append(remote_session)
        peer_policy = {"include_peers": True, "alias_key": "test-alias-key"}
        remote_alias = telemetry_query.session_alias(remote_session, peer_policy)
        calls_before = self.calls["timeline"]
        remote = mcp_backend._timeline(self.payload, {"session": remote_alias}, peer_policy, self._timeline)
        self.assertEqual(remote["detail"], "remote_source")
        self.assertNotIn("remote-native-id", json.dumps(remote))
        self.assertEqual(self.calls["timeline"], calls_before)
        with self.assertRaises(mcp_backend.MCPBackendError):
            mcp_backend._timeline(self.payload, {"session": remote_alias}, {
                "include_peers": False, "alias_key": "test-alias-key",
            }, self._timeline)

        archived = dict(self.payload["sessions"][0], archived=True, rename_id="archived-native-id")
        self.payload["sessions"].append(archived)
        archived_alias = telemetry_query.session_alias(archived, {"alias_key": "test-alias-key"})
        result = mcp_backend._timeline(self.payload, {"session": archived_alias}, {
            "include_peers": False, "alias_key": "test-alias-key",
        }, self._timeline)
        self.assertEqual(result["detail"], "archived_source")
        self.assertEqual(self.calls["timeline"], calls_before)

        unsupported = dict(self.payload["sessions"][0], source="opencode", rename_id="opaque")
        self.payload["sessions"].append(unsupported)
        unsupported_alias = telemetry_query.session_alias(unsupported, {"alias_key": "test-alias-key"})
        result = mcp_backend._timeline(self.payload, {"session": unsupported_alias}, {
            "include_peers": False, "alias_key": "test-alias-key",
        }, self._timeline)
        self.assertEqual(result["detail"], "unsupported_tool")
        self.assertEqual(self.calls["timeline"], calls_before)

    def test_timeline_event_bound_marks_truncated(self):
        result = mcp_backend._safe_timeline({
            "available": True, "detail": "source log", "truncated": False,
            "events": [{"kind": "request", "timestamp": TODAY + "T10:00:00Z"} for _ in range(401)],
        })
        self.assertEqual(len(result["events"]), 400)
        self.assertTrue(result["truncated"])

    def test_health_and_quota_sanitizers_drop_raw_error_paths_and_identity_strings(self):
        report = {
            "status": "partial_error", "generation": 4, "started_at": 1792281600,
            "finished_at": 1792281601, "discovered": 3, "read": 2, "errors": 1,
            "backup_error": r"C:\private\account\ERROR_SENTINEL",
            "missing_prices": ["gpt-6-sentinel", r"C:\Users\someone\private.log", "gpt-6-sentinel"],
            "ledger": {"status": "Save failed", "hint": "RAW_PATH"},
            "sources": [{"source": "codex", "status": "error", "discovered": 2,
                         "read": 0, "errors": 1, "error_category": "permission",
                         "hint": "RAW_PATH", "path": "PATH_SENTINEL", "last_attempt": 1792281601}],
            "issues": [{"category": "parse_error", "hint": "RAW_EXCEPTION", "path": "SECRET"}],
        }
        safe = mcp_backend._safe_diagnostics(report)
        rendered = json.dumps(safe)
        for secret in ("ERROR_SENTINEL", r"C:\Users\someone\private.log", "RAW_PATH", "PATH_SENTINEL", "RAW_EXCEPTION", "SECRET"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(safe["sources"][0]["hint"], mcp_backend._DIAGNOSTIC_HINTS["permission"])
        self.assertEqual(safe["missing_price_model_count"], 3)
        self.assertEqual(safe["missing_price_models_observed_count"], 3)
        self.assertEqual(safe["missing_price_model_count_kind"], "observed")
        self.assertEqual(safe["missing_price_models"], ["gpt-6-sentinel"])
        bounded_models = mcp_backend._safe_diagnostics({
            "missing_prices": [f"model-{index}" for index in range(105)],
        })
        self.assertEqual(bounded_models["missing_price_model_count"], 105)
        self.assertEqual(bounded_models["missing_price_model_count_kind"], "observed_lower_bound")
        self.assertEqual(len(bounded_models["missing_price_models"]), 100)
        self.assertTrue(bounded_models["missing_price_models_truncated"])
        source_capped_models = mcp_backend._safe_diagnostics({
            "missing_prices": [f"model-{index}" for index in range(100)],
        })
        self.assertEqual(source_capped_models["missing_price_models_observed_count"], 100)
        self.assertEqual(source_capped_models["missing_price_model_count_kind"], "observed_lower_bound")
        self.assertTrue(source_capped_models["missing_price_models_truncated"])
        unknown = mcp_backend._safe_diagnostics({
            "status": "SECRET_STATUS", "generation": "invalid",
            "sources": [{"source": "codex", "status": "SECRET_SOURCE_STATUS", "read": "bad"}],
        })
        self.assertEqual(unknown["status"], "unknown")
        self.assertIsNone(unknown["generation"])
        self.assertEqual(unknown["sources"][0]["status"], "unknown")
        self.assertIsNone(unknown["sources"][0]["read"])

        now = time.time()
        self.builders["accounts"] = lambda: {
            "stale_after_seconds": 300,
            "entries": [{
                "source": "codex", "profile": r"C:\private\profile", "identity": {"email": "EMAIL_SENTINEL"},
                "identity_stale": True, "error": "ERROR_SENTINEL", "unavailable": "ACCOUNT_SENTINEL",
                "snapshots": [{
                    "observed_at": now, "origin": "Local Codex log", "plan": "PLAN_SENTINEL",
                    "limit_id": "ID_SENTINEL", "limit_name": "NAME_SENTINEL", "attribution": "ATTR_SENTINEL",
                    "stale": True, "windows": [
                        {"label": "RAW_LABEL", "window_minutes": 300, "used_percent": 80.5,
                         "resets_at": now + 100, "stale": True, "expired": False},
                        {"label": "RAW_LABEL", "window_minutes": 300, "used_percent": float("nan"),
                         "resets_at": "BAD", "stale": True, "expired": False},
                    ],
                }],
            }],
        }
        safe_account = self.execute("get_subscription_limits", {})
        rendered = json.dumps(safe_account)
        for secret in ("profile", "EMAIL_SENTINEL", "ERROR_SENTINEL", "ACCOUNT_SENTINEL", "PLAN_SENTINEL",
                       "ID_SENTINEL", "NAME_SENTINEL", "ATTR_SENTINEL", "RAW_LABEL"):
            self.assertNotIn(secret, rendered)
        self.assertEqual(safe_account["entries"][0]["observation_type"], "local_codex_log_snapshot")
        self.assertIn("never sum", safe_account["scope_note"])
        self.assertTrue(safe_account["entries"][0]["unbound"])
        self.assertEqual(safe_account["entries"][0]["windows"][0]["label"], "5-hour limit")
        self.assertEqual(safe_account["entries"][0]["windows"][1]["used_percent"], None)

    def test_quota_staleness_is_recomputed_from_observation_and_reset_times(self):
        now = 1792281600
        account = {
            "stale_after_seconds": 300,
            "entries": [{"source": "codex", "identity_stale": False, "snapshots": [
                {"observed_at": now - 10, "origin": "Codex account API", "stale": False,
                 "windows": [
                     {"window_minutes": 300, "used_percent": 25, "resets_at": now + 600, "stale": False},
                     {"window_minutes": 10080, "used_percent": 75, "resets_at": now - 1, "stale": False},
                     {"window_minutes": 60, "used_percent": None, "resets_at": None, "stale": False},
                 ]},
                {"observed_at": now + 10, "origin": "Codex account API", "stale": False,
                 "windows": [{"window_minutes": 300, "used_percent": 50,
                              "resets_at": now + 600, "stale": False}]},
                {"observed_at": now - 400, "origin": "Codex account API", "stale": False,
                 "windows": [{"window_minutes": 300, "used_percent": 50,
                              "resets_at": now + 600, "stale": False}]},
                {"observed_at": None, "origin": "Codex account API", "stale": False,
                 "windows": [{"window_minutes": 300, "used_percent": 50,
                              "resets_at": now + 600, "stale": False}]},
            ]}],
        }
        with patch("mcp_backend.time.time", return_value=now):
            safe = mcp_backend._safe_account_limits(account, {})
            capped = mcp_backend._safe_account_limits({**account, "stale_after_seconds": 600}, {})
        entries = safe["entries"]
        self.assertEqual(capped["stale_after_seconds"], 300)
        self.assertFalse(entries[0]["stale"])
        self.assertFalse(entries[0]["windows"][0]["stale"])
        self.assertEqual(entries[0]["windows"][0]["remaining_percent"], 75)
        self.assertTrue(entries[0]["windows"][1]["stale"])
        self.assertTrue(entries[0]["windows"][1]["expired"])
        self.assertTrue(entries[0]["windows"][2]["stale"])
        self.assertIsNone(entries[0]["windows"][2]["used_percent"])
        self.assertNotIn("remaining_percent", entries[0]["windows"][2])
        self.assertTrue(entries[1]["stale"])
        self.assertTrue(entries[2]["stale"])
        self.assertTrue(entries[3]["stale"])

    def test_fresh_unbound_log_and_capture_limits_keep_freshness_separate_from_identity(self):
        now = 1792281600
        account = {
            "stale_after_seconds": 300,
            "entries": [
                {"source": "codex", "identity_stale": True, "snapshots": [{
                    "observed_at": now - 10, "origin": "Local Codex log", "stale": False,
                    "windows": [{"window_minutes": 300, "used_percent": 45,
                                 "resets_at": now + 600, "stale": False}],
                }]},
                {"source": "claude", "identity_stale": True, "snapshots": [{
                    "observed_at": now - 10, "origin": "Claude status-line capture", "stale": False,
                    "windows": [{"window_minutes": 10080, "used_percent": 70,
                                 "resets_at": now + 600, "stale": False}],
                }]},
                {"source": "codex", "identity_stale": True, "snapshots": [{
                    "observed_at": now - 10, "origin": "Codex account API", "stale": True,
                    "windows": [{"window_minutes": 300, "used_percent": 20,
                                 "resets_at": now + 600, "stale": True}],
                }]},
                {"source": "claude", "identity_stale": True, "snapshots": [{
                    "observed_at": now - 10, "origin": "Claude status-line capture",
                    "windows": [{"window_minutes": 300, "used_percent": 30,
                                 "resets_at": now + 600}],
                }]},
            ],
        }
        with patch("mcp_backend.time.time", return_value=now):
            safe = mcp_backend._safe_account_limits(account, {})
        codex, claude, switched, missing_flags = safe["entries"]
        for item, expected_remaining in ((codex, 55), (claude, 30)):
            self.assertFalse(item["stale"])
            self.assertTrue(item["identity_stale"])
            self.assertTrue(item["unbound"])
            self.assertEqual(item["windows"][0]["remaining_percent"], expected_remaining)
        self.assertTrue(switched["stale"])
        self.assertNotIn("remaining_percent", switched["windows"][0])
        self.assertTrue(missing_flags["stale"])
        self.assertTrue(missing_flags["windows"][0]["stale"])
        self.assertNotIn("remaining_percent", missing_flags["windows"][0])

    def test_subscription_limit_caps_are_disclosed_after_source_filtering(self):
        now = 1792281600

        def snapshot(window_count=1, observed=now):
            return {
                "observed_at": observed, "origin": "Codex account API", "stale": False,
                "windows": [{
                    "window_minutes": 300 + index, "used_percent": 40,
                    "resets_at": now + 600, "stale": False,
                } for index in range(window_count)],
            }

        entries = [
            {"source": "codex", "snapshots": [snapshot()], "profile": "private-profile"}
            for _ in range(101)
        ]
        entries.extend(
            {"source": "claude", "snapshots": [snapshot(14)] + [snapshot() for _ in range(17)],
             "profile": "another-private-profile"}
            if index == 0 else
            {"source": "claude", "snapshots": [snapshot()], "profile": "another-private-profile"}
            for index in range(101)
        )

        with patch("mcp_backend.time.time", return_value=now):
            result = mcp_backend._safe_account_limits({"entries": entries}, {"source": "claude"})

        self.assertEqual(result["source_entries_returned"], 100)
        self.assertEqual(result["source_entries_limit"], 100)
        self.assertTrue(result["source_entries_truncated"])
        self.assertEqual(result["snapshot_observations_returned"], 16 + 99)
        self.assertEqual(result["snapshot_limit_per_source_entry"], 16)
        self.assertTrue(result["snapshot_observations_truncated"])
        self.assertEqual(result["quota_windows_returned"], 12 + 15 + 99)
        self.assertEqual(result["window_limit_per_observation"], 12)
        self.assertTrue(result["quota_windows_truncated"])
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["entries"]), 16 + 99)
        self.assertTrue(all(item["source"] == "claude" for item in result["entries"]))
        self.assertEqual(result["entries"][0]["windows_returned"], 12)
        self.assertEqual(result["entries"][0]["windows_observed_count"], 14)
        self.assertTrue(result["entries"][0]["windows_truncated"])
        rendered = json.dumps(result)
        self.assertNotIn("private-profile", rendered)
        self.assertNotIn("another-private-profile", rendered)

    def test_usage_does_not_contact_accounts_and_query_args_cannot_expand_scope(self):
        self.execute("get_usage", {"from": TODAY, "to": TODAY, "group_by": []})
        self.assertEqual(self.calls["accounts"], 0)
        with self.assertRaises(mcp_backend.MCPBackendError) as caught:
            self.execute("get_usage", {"include_names": True})
        self.assertEqual(caught.exception.status, 400)

    def test_serialized_query_response_has_a_bounded_size(self):
        with patch.object(mcp_backend.telemetry_query, "query_usage",
                          return_value={"large": "x" * mcp_backend.MAX_RESPONSE_BYTES}):
            with self.assertRaises(mcp_backend.MCPBackendError) as caught:
                self.execute("get_usage", {"from": TODAY, "to": TODAY})
        self.assertEqual(caught.exception.status, 413)
        self.assertEqual(caught.exception.code, "result_too_large")

    def test_revocation_and_ledger_switch_during_or_before_query_discard_result(self):
        def revoke_during_build():
            mcp_access.configure(self.url, self.data, {"enabled": False})
            return self.payload

        self.builders["payload"] = revoke_during_build
        with self.assertRaises(mcp_backend.MCPBackendError) as caught:
            self.execute("get_usage", {"from": TODAY, "to": TODAY})
        self.assertEqual(caught.exception.status, 403)
        self.assertEqual(caught.exception.code, "forbidden")

        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        self.token = mcp_access.credentials(self.url)
        other_data = self.root / "other-ledger"
        other_data.mkdir()
        with self.assertRaises(mcp_backend.MCPBackendError) as caught:
            mcp_backend.execute_query(_request("get_usage", {"from": TODAY, "to": TODAY}),
                                      "Bearer " + self.token, self.url, other_data, self.builders)
        self.assertEqual(caught.exception.status, 403)

    def test_settings_response_contains_local_config_but_never_key(self):
        pythonw = self.root / "pythonw.exe"
        python = self.root / "python.exe"
        server_script = self.root / "mcp_server.py"
        pythonw.touch()
        python.touch()
        server_script.touch()

        result = mcp_backend.settings_response(
            self.url, self.data, str(pythonw), str(server_script))
        rendered = json.dumps(result)
        self.assertNotIn(self.token, rendered)
        self.assertNotIn("Bearer", rendered)
        config = result["client_config"]["mcpServers"]["agent-telemetry"]
        self.assertEqual(config["command"], str(python))
        self.assertEqual(config["args"][0], str(server_script))
        self.assertEqual(config["args"][-2:], ["--backend", self.url])
        self.assertEqual(set(result) - {"enabled", "include_names", "include_peers", "warning", "client_config"}, set())

    def test_request_parser_rejects_duplicate_keys_unknown_fields_and_large_bodies(self):
        for raw in (
            b'{"tool":"get_usage","tool":"get_catalog","arguments":{}}',
            b'{"tool":"get_usage","arguments":{},"extra":true}',
            b'{"tool":"get_usage","arguments":{"x":NaN}}',
            b"[" * 100 + b"]" * 100,
            b" " * (mcp_backend.MAX_REQUEST_BYTES + 1),
        ):
            with self.subTest(size=len(raw)), self.assertRaises(mcp_backend.MCPBackendError):
                mcp_backend.parse_request(raw)


if __name__ == "__main__":
    unittest.main()
