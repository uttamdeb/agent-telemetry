"""Account snapshots are private observations, never another usage counter."""
import copy
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import accounts as A
import dashboard as D
import parser as P

AT = 1791806400


def limits(used=11, camel=False):
    if camel:
        return {"limitId": "codex", "planType": "plus", "primary": {
            "usedPercent": used, "windowDurationMins": 300, "resetsAt": AT + 3600},
            "secondary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": AT + 86400}}
    return {"limit_id": "codex", "plan_type": "plus", "primary": {
        "used_percent": used, "window_minutes": 300, "resets_at": AT + 3600},
        "secondary": {"used_percent": used, "window_minutes": 10080, "resets_at": AT + 86400},
        "credentials": "DO NOT COPY", "credits": {"balance": "DO NOT COPY"}}


class AccountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-accounts-")
        self.addCleanup(temp.cleanup); self.root = Path(temp.name)
        for item in [patch.object(A.N, "support_dir", return_value=self.root / "private"),
                     patch.dict(A._live, {}, clear=True), patch.object(A, "_profile", return_value=str(self.root)),
                     patch.object(A, "_auth_signature", return_value=("fixture-auth",)),
                     patch.object(A.time, "time", return_value=AT)]:
            item.start(); self.addCleanup(item.stop)

    def line(self, kind, payload, at="2026-10-12T12:00:00Z"):
        return json.dumps({"type": kind, "timestamp": at, "payload": payload}) + "\n"

    def aggregate(self, quota=None):
        path = self.root / "profile" / "sessions" / "fixture.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.line("session_meta", {"id": "fixture-thread", "creator_account_id": "creator-A",
            "creator_user_id": "user-A", "access_token": "DO NOT COPY"})
            + self.line("turn_context", {"model": "gpt-6.1-sol"})
            + self.line("token_usage_record", {"usage": {"input_tokens": 100, "output_tokens": 10}})
            + self.line("event_msg", {"type": "token_count", "rate_limits": limits() if quota is None else quota}))
        return P.update_file(None, "codex", str(path), None, {})

    def test_log_metadata_and_quota_do_not_change_usage_or_leak_secrets(self):
        agg = self.aggregate()
        baseline = P._blank_agg("codex", agg["path"])
        with patch.object(A, "observe_codex"):
            P.parse_codex(baseline, Path(agg["path"]).read_text().splitlines())
        for key in ("totals", "records", "hourly", "tools", "activity"):
            self.assertEqual(agg.get(key), baseline.get(key), key)
        self.assertEqual(A.session_metadata(agg), {"creator_account_id": "creator-A", "creator_user_id": "user-A"})
        self.assertNotIn("DO NOT COPY", json.dumps(agg))
        snapshot = agg["account_metadata"]["quota_snapshots"]["codex"]
        self.assertEqual([w["remaining_percent"] for w in snapshot["windows"]], [89, 89])
        self.assertEqual([w["label"] for w in snapshot["windows"]], ["5-hour limit", "Weekly limit"])

    def test_missing_invalid_zero_and_boundary_percentages(self):
        for value in (None, True, "11", -1, 101, 10**1000, float("nan"), float("inf")):
            with self.subTest(value=value):
                snapshot = A.codex_snapshot(limits(value), AT)
                self.assertIsNone(snapshot["windows"][0]["remaining_percent"])
        for used, left in ((0, 100), (100, 0), (11.5, 88.5)):
            self.assertEqual(A.codex_snapshot(limits(used), AT)["windows"][0]["remaining_percent"], left)
        self.assertEqual(A.codex_snapshot({}, AT)["windows"], [])
        self.assertIsNone(A.codex_snapshot(None, AT))
        self.assertIsNone(A.codex_snapshot(limits(), "bad-date"))
        self.assertIsNone(A.timestamp("2026-10-12T12:00:00"))
        huge = limits(); huge["primary"]["window_minutes"] = 10**1000
        self.assertIsNone(A.codex_snapshot(huge, AT)["windows"][0]["window_minutes"])

    def test_snapshot_replaces_missing_windows_and_ignores_older_snapshots(self):
        agg = self.aggregate()
        latest = limits(40); latest.pop("secondary")
        P.parse_codex(agg, [self.line("event_msg", {"type": "token_count", "rate_limits": latest}, "2026-10-12T12:01:00Z")])
        P.parse_codex(agg, [self.line("event_msg", {"type": "token_count", "rate_limits": limits(5)})])
        windows = agg["account_metadata"]["quota_snapshots"]["codex"]["windows"]
        self.assertEqual(len(windows), 1); self.assertEqual(windows[0]["remaining_percent"], 60)

    def test_fork_replay_does_not_import_parent_quota(self):
        agg = P._blank_agg("codex", "fixture")
        P.parse_codex(agg, [self.line("session_meta", {"id": "child", "forked_from_id": "parent", "creator_account_id": "B"}),
            self.line("event_msg", {"type": "token_count", "rate_limits": limits()}, "2026-10-12T12:00:00.1Z")])
        self.assertNotIn("quota_snapshots", agg["account_metadata"])

    def test_claude_observations_are_incomplete_history_not_billing_auth(self):
        agg = P._blank_agg("claude", "fixture")
        P.parse_claude(agg, [json.dumps({"type": "limit-status", "accountUuid": "A", "api_key": "DO NOT COPY"}),
                           json.dumps({"type": "limit-status", "accountUuid": "B", "usedPercentage": 99})])
        self.assertEqual(A.session_metadata(agg), {"recorded_account_ids": ["A", "B"]})
        self.assertNotIn("DO NOT COPY", json.dumps(agg)); self.assertNotIn("quota_snapshots", agg["account_metadata"])

    def test_stale_expired_future_and_unknown_resets_never_reset_to_full(self):
        s = A.codex_snapshot(limits(99), AT)
        for now in (AT + 301, AT - 1): self.assertTrue(A._present(s, now)["stale"])
        expired = A._present(s, AT + 3601)
        self.assertTrue(expired["windows"][0]["expired"])
        self.assertEqual(expired["windows"][0]["remaining_percent"], 1)
        s["windows"][0]["resets_at"] = None
        self.assertTrue(A._present(s, AT)["windows"][0]["stale"])

    def test_profiles_buckets_devices_not_summed_or_attributed_to_creator(self):
        a = self.aggregate(); b = copy.deepcopy(a)
        b["path"] = str(self.root / "other" / "sessions" / "b.jsonl")
        remote = copy.deepcopy(a); remote["_device"] = "remote"; remote["path"] = "remote/sessions/r.jsonl"
        entries = A.build_accounts([a, b, remote], "local", now=AT)["entries"]
        logs = [e for e in entries if any(s["origin"] == "Local Codex log" for s in e["snapshots"])]
        self.assertEqual(len(logs), 2)
        self.assertTrue(all(e["device"] == "local" for e in entries))
        for entry in logs:
            self.assertIsNone(entry["identity"])
            self.assertNotIn("creator-A", json.dumps(entry))
            self.assertEqual(entry["snapshots"][0]["windows"][0]["remaining_percent"], 89)

    def test_get_is_offline_and_does_not_read_credentials(self):
        with patch.object(A, "_codex_read") as codex, patch.object(A, "_claude_read") as claude:
            A.build_accounts([self.aggregate()], "local", now=AT)
            codex.assert_not_called(); claude.assert_not_called()

    def test_windows_profiles_remain_distinct_when_read_on_mac_or_linux(self):
        a = self.aggregate(); b = copy.deepcopy(a); archived = copy.deepcopy(a)
        a["path"] = r"C:\Users\fixture\.codex\sessions\2026\10\12\a.jsonl"
        b["path"] = r"D:\profiles\work\sessions\2026\10\12\b.jsonl"
        archived["path"] = r"C:\Users\fixture\.codex\archived_sessions\a.jsonl"
        entries = A.build_accounts([a, b, archived], "local", now=AT)["entries"]
        logs = [e for e in entries if any(s["origin"] == "Local Codex log" for s in e["snapshots"])]
        self.assertEqual({e["profile"] for e in logs}, {"C:/Users/fixture/.codex", "D:/profiles/work"})
        self.assertEqual(len(logs), 2, "Archived and live copies share a profile; separate profiles do not")

    def test_empty_logs_are_marked_parsed_without_repeated_account_backfill(self):
        for source in ("codex", "claude"):
            with self.subTest(source=source):
                path = self.root / (source + "-empty.jsonl"); path.write_text("")
                aggregate = P.update_file(None, source, str(path), None, {})
                self.assertEqual(aggregate["account_metadata_version"], A.METADATA_VERSION)
                self.assertIs(P.update_file(aggregate, source, str(path), None, {}), aggregate)

    def test_opt_in_and_strict_allowlist_for_live_account(self):
        with patch.object(A, "_codex_read", return_value=({"type": "chatgpt", "email": "fixture@example.invalid",
            "planType": "plus", "accessToken": "DO NOT COPY"}, {"rateLimits": limits(camel=True), "secret": "DO NOT COPY"})) as read:
            with self.assertRaisesRegex(ValueError, "consent"): A.refresh_account("codex")
            read.assert_not_called()
            A.refresh_account("codex", True)
        result = A.build_accounts([], "local", now=AT)
        self.assertNotIn("DO NOT COPY", json.dumps(result))
        self.assertEqual(result["entries"][0]["identity"]["plan"], "plus")
        self.assertEqual(result["entries"][0]["snapshots"][0]["windows"][0]["remaining_percent"], 89)

    def test_api_key_logout_and_missing_plan_do_not_infer_subscription(self):
        for account in ({"type": "apiKey"}, None, {"type": "amazonBedrock", "credentialSource": "awsManaged"}):
            with patch.object(A, "_codex_read", return_value=(account, {})):
                A.refresh_account("codex", True)
            entry = A.build_accounts([], "local", now=AT)["entries"][0]
            self.assertIsNone(entry["identity"]["plan"]); self.assertEqual(entry["snapshots"], [])

    def test_account_switch_hides_previous_quota_and_failed_switch_drops_cache(self):
        with patch.object(A, "_codex_read", return_value=({"type": "chatgpt", "email": "A"}, {"rateLimits": limits(camel=True)})):
            A.refresh_account("codex", True)
        with patch.object(A, "_auth_signature", return_value=("different-account",)):
            entry = A.build_accounts([], "local", now=AT)["entries"][0]
            self.assertIsNone(entry["identity"]); self.assertEqual(entry["snapshots"], [])
            with patch.object(A, "_codex_read", side_effect=ValueError("Unavailable")):
                with self.assertRaises(ValueError): A.refresh_account("codex", True)
        self.assertNotIn("codex", A._live)

    def test_changed_credentials_during_lookup_are_discarded(self):
        with patch.object(A, "_auth_signature", side_effect=[("A",), ("B",)]), patch.object(A, "_codex_read", return_value=({"type": "chatgpt"}, {})):
            with self.assertRaisesRegex(ValueError, "changed"): A.refresh_account("codex", True)
        self.assertNotIn("codex", A._live)

    def test_failed_refresh_keeps_last_success_but_marks_it_stale(self):
        with patch.object(A, "_codex_read", return_value=({"type": "chatgpt"}, {"rateLimits": limits(camel=True)})):
            A.refresh_account("codex", True)
        with patch.object(A, "_codex_read", side_effect=ValueError("Offline")):
            with self.assertRaises(ValueError): A.refresh_account("codex", True)
        entry = A.build_accounts([], "local", now=AT)["entries"][0]
        self.assertEqual(entry["error"], "Offline"); self.assertTrue(entry["snapshots"][0]["stale"])

    def test_rpc_account_switch_discards_old_figures_even_without_file_changes(self):
        with patch.object(A, "_codex_read", return_value=({"type": "chatgpt", "email": "A"}, {"rateLimits": limits(camel=True)})):
            A.refresh_account("codex", True)
        with patch.object(A, "_codex_read", side_effect=A.AccountChanged("Changed account")):
            with self.assertRaises(A.AccountChanged): A.refresh_account("codex", True)
        self.assertIsNone(A.build_accounts([], "local", now=AT)["entries"][0]["identity"])

    def test_claude_capture_keeps_only_documented_windows_and_replaces_absence(self):
        A.capture_claude({"rate_limits": {"seven_day": {"used_percentage": 11, "resets_at": AT + 1000},
            "spend_limit": {"used_percentage": 90, "secret": "DO NOT COPY"}},
            "context_window": {"remaining_percentage": 88}, "session_id": "DO NOT COPY", "prompt": "DO NOT COPY"})
        file = self.root / "private" / "claude-quota-snapshot.json"
        saved = json.loads(file.read_text()); self.assertEqual(set(saved["rate_limits"]), {"seven_day"})
        self.assertNotIn("DO NOT COPY", file.read_text())
        entry = A.build_accounts([], "local", now=AT)["entries"][1]
        self.assertEqual(entry["snapshots"][0]["windows"][0]["remaining_percent"], 89)
        self.assertIn("cannot match", entry["snapshots"][0]["attribution"])
        A.capture_claude({"context_window": {"remaining_percentage": 100}})
        self.assertEqual(A.build_accounts([], "local", now=AT)["entries"][1]["snapshots"][0]["windows"], [])

    def test_metadata_excluded_from_device_export(self):
        agg = self.aggregate()
        exported = D._export_agg(agg)
        self.assertNotIn("account_metadata", exported)
        self.assertNotIn("creator-A", json.dumps(exported)); self.assertNotIn("used_percent", json.dumps(exported))

    def test_slow_account_read_cannot_hold_the_telemetry_parser_lock(self):
        started, release = threading.Event(), threading.Event()
        aggregate = self.aggregate()
        observed = []
        def read(aggregates, device):
            started.set(); release.wait(timeout=2)
            observed.append(aggregates[0]["account_metadata"]["creator_account_id"])
            return {"entries": []}
        with patch.dict(D._state, {"files": {aggregate["path"]: aggregate}}), patch.object(D, "_local_id", return_value="local"), patch.object(A, "build_accounts", side_effect=read):
            worker = threading.Thread(target=D.build_account_payload)
            worker.start()
            try:
                self.assertTrue(started.wait(timeout=1))
                acquired = D._refresh_lock.acquire(timeout=.2)
                self.assertTrue(acquired, "Slow account read blocked usage parsing")
                if acquired:
                    aggregate["account_metadata"]["creator_account_id"] = "changed-later"
                    D._refresh_lock.release()
            finally:
                release.set(); worker.join(timeout=2)
        self.assertEqual(observed, ["creator-A"])

    def test_v56_migration_keeps_archives_and_failed_live_backfill(self):
        agg = self.aggregate(); old = copy.deepcopy(agg)
        old.pop("account_metadata_version"); old.pop("account_metadata")
        archived = copy.deepcopy(old); archived["archived"] = True
        file = self.root / "ledger.json"
        file.write_text(json.dumps({"version": 56, "files": {agg["path"]: old, "gone": archived}}))
        with patch.object(D, "CACHE_PATH", str(file)), patch.dict(D._state, {"files": {}}), patch.dict(D._dirty, {"v": False}):
            D.load_cache()
            self.assertEqual(D._state["files"]["gone"], archived)
            with patch.object(P, "parse_codex", side_effect=ValueError("Broken backfill")):
                with self.assertRaises(ValueError): P.update_file(old, "codex", agg["path"], None, {})
            self.assertNotIn("account_metadata", old)
            fresh = P.update_file(old, "codex", agg["path"], None, {})
            self.assertEqual(fresh["totals"], old["totals"])
            self.assertIn("account_metadata", fresh)
        self.assertTrue(D._peer_cache_ok(56))

    def test_cli_commands_and_protocol_are_read_only_and_account_switch_is_refused(self):
        class FakeProcess:
            def __init__(self, after):
                self.stdin = io.StringIO()
                self.stdout = io.StringIO("".join(json.dumps({"id": i, "result": r}) + "\n" for i, r in (
                    (1, {}), (2, {"account": {"type": "chatgpt", "email": "A"}}),
                    (3, {"rateLimits": limits(camel=True)}), (4, {"account": after}))))
                self.sent = None
            def wait(self, timeout): self.sent = self.stdin.getvalue() if not self.stdin.closed else self.sent
        # Preserve writes for inspection when the implementation closes stdin.
        class Input(io.StringIO):
            def close(self): self.recorded = self.getvalue()
        for after, success in (({"type": "chatgpt", "email": "A"}, True), ({"type": "chatgpt", "email": "B"}, False)):
            process = FakeProcess(after); process.stdin = Input()
            with patch.object(A.shutil, "which", return_value="fixture-codex"), patch.object(A.subprocess, "Popen", return_value=process) as popen:
                if success: A._codex_read()
                else:
                    with self.assertRaisesRegex(ValueError, "changed"): A._codex_read()
            sent = [json.loads(s) for s in process.stdin.recorded.splitlines()]
            self.assertEqual([m["method"] for m in sent], ["initialize", "initialized", "account/read", "account/rateLimits/read", "account/read"])
            self.assertFalse(sent[2]["params"]["refreshToken"])
            self.assertNotIn("AGENT_TELEMETRY_CONTROL_TOKEN", popen.call_args.kwargs["env"])

    def test_claude_auth_output_is_allowlisted_and_api_key_plan_unknown(self):
        with patch.object(A, "_claude_read", return_value={"loggedIn": True, "authMethod": "api_key", "apiProvider": "firstParty", "apiKey": "DO NOT COPY"}):
            A.refresh_account("claude", True)
        entry = A.build_accounts([], "local", now=AT)["entries"][1]
        self.assertEqual(entry["identity"]["auth"], "api_key")
        self.assertIsNone(entry["identity"]["plan"]); self.assertNotIn("DO NOT COPY", json.dumps(entry))

    @unittest.skipUnless(A.shutil.which("codex"), "Optional installed Codex CLI is unavailable")
    def test_actual_codex_account_read_in_empty_isolated_profile(self):
        environment = os.environ.copy()
        for name in ("OPENAI_API_KEY", "CODEX_AUTH_TOKEN", "AGENT_TELEMETRY_CONTROL_TOKEN"):
            environment.pop(name, None)
        environment["CODEX_HOME"] = str(self.root / "empty-codex-profile")
        Path(environment["CODEX_HOME"]).mkdir()
        with patch.object(A, "_environment", return_value=environment):
            account, quota = A._codex_read()
        self.assertIsNone(account); self.assertEqual(quota, {})
        self.assertFalse((Path(environment["CODEX_HOME"]) / "auth.json").exists())

    def test_accounts_endpoint_requires_csrf_and_consent_and_health_has_no_metadata(self):
        server = D.Server(("127.0.0.1", 0), D.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        port = server.server_address[1]
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": port}), patch.dict(D._state, {"files": {}}), patch.object(D, "_local_id", return_value="local"), patch.object(A, "_codex_read") as read:
            def req(method, route, body=None, headers=None):
                c = http.client.HTTPConnection("127.0.0.1", port)
                c.request(method, route, body, headers or {}); r = c.getresponse()
                status, data = r.status, json.loads(r.read()); c.close(); return status, data
            self.assertEqual(req("POST", "/api/accounts", '{}', {"Content-Type": "text/plain"})[0], 403)
            self.assertEqual(req("POST", "/api/accounts", '{"source":"codex"}', {"Content-Type": "application/json"})[0], 400)
            self.assertEqual(req("GET", "/api/accounts", headers={"Origin": "https://evil.invalid"})[0], 403)
            self.assertEqual(req("GET", "/api/accounts")[0], 200); read.assert_not_called()
            health = req("GET", "/api/health")[1]; self.assertNotIn("accounts", health); self.assertNotIn("identity", health)


if __name__ == "__main__": unittest.main()
