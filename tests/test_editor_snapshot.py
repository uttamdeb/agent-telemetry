"""Private editor projection: invented aggregates only, no log discovery or parsing."""
from contextlib import ExitStack
import copy
import datetime
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import editor_snapshot as E

TODAY = datetime.date.today().isoformat()
NOW = datetime.datetime.combine(datetime.date.today(), datetime.time(12)).timestamp()
PARENT = "12345678-1234-1234-1234-123456789abc"
CHILD = "87654321-1234-1234-1234-123456789abc"
OTHER = "abcdef12-1234-1234-1234-123456789abc"
MODEL = "GPT-6.1 Sol"


def query(scope="workspace", roots=("/fixtures/project",), period="today"):
    return urlencode([("scope", scope), ("range", period)] + [("root", root) for root in roots])


def aggregate(identity=PARENT, cwd="/fixtures/project", source="codex", subagent=False,
              date=TODAY, model=MODEL, inp=100, out=10, cr=20, cc=5):
    path = (f"/private/logs/rollout-2026-10-18T10-00-00-{identity}.jsonl" if source == "codex"
            else f"/private/logs/{identity}.jsonl")
    row = dict(D._zero(), **{"in": inp, "out": out, "cr": cr, "cc": cc, "cc5": cc, "asst": 1, "req": 1})
    session = {"id": identity[:8], "source": source, "title": "Invented title", "project": "project",
               "model": model, "start": date + "T10:00:00+00:00", "end": date + "T10:30:00+00:00",
               "subagent": subagent, **row}
    return {"path": path, "source": source, "cwd": cwd, "project": "project", "_session_id": identity,
            "subagent": subagent, "records": {date + "\t" + model: row}, "sessions": [session]}


class EditorSnapshotTests(unittest.TestCase):
    def snapshot(self, files, params=None, cap=None):
        with ExitStack() as stack:
            if cap is not None:
                stack.enter_context(patch.object(E, "MAX_SESSIONS", cap))
            return E.build_snapshot([(a["path"], a) for a in files], "local", params or query(),
                "fixture-revision", price_usage=D._priced_usage, deduplicate=D._one_per_conversation,
                identity=D.SN.identity, claude_parent_key=D._claude_parent_key, now=NOW)

    def test_query_rejects_invalid_and_ambiguous_inputs_without_echoing_paths(self):
        invalid = ("", "scope=workspace", "scope=all&root=/private", "scope=other", "scope=all&range=1d",
                   "scope=all&scope=workspace", "scope=all&range=today&range=7d", "scope=all&file=/private",
                   "scope=workspace&root=relative", "scope=workspace&root=C:relative", "scope=all&range=",
                   "scope=workspace&root=%00/private", "scope=workspace&root=%FF", "scope=workspace&root=%zz",
                   query(roots=("//server/",)), query(roots=("/private/" + "x" * 4096,)),
                   query(roots=tuple("/root/" + str(i) for i in range(21))))
        for text in invalid:
            with self.subTest(query=text[:60]), self.assertRaises(ValueError) as failure:
                E.parse_query(text)
            self.assertNotIn("/private", str(failure.exception))
        self.assertEqual(E.parse_query("scope=all"), ("all", "today", []))

    def test_absolute_cwd_containment_never_matches_project_basename(self):
        files = [aggregate(), aggregate(CHILD, cwd="/elsewhere/project"),
                 aggregate(OTHER, cwd="/fixtures/project-neighbor")]
        result = self.snapshot(files)
        self.assertEqual(result["summary"]["tokens"], 135)
        self.assertEqual(result["sessions_total"], 1)
        self.assertEqual(result["scope"]["roots"], ["project"])
        self.assertEqual(result["scope"]["matched_sessions"], 1)
        self.assertNotIn("/fixtures", json.dumps(result))
        self.assertEqual(self.snapshot([aggregate(cwd="/fixtures/project/sub/../src")])["sessions_total"], 1)

    def test_windows_case_separators_unc_and_component_boundaries(self):
        for root, cwd, included in ((r"C:\Projects\App", "c:/projects/app/src", True),
                                    (r"C:\Projects\App", "c:/projects/apple", False),
                                    (r"\\server\share\App", "//SERVER/share/app/src", True),
                                    ("/Projects/App", "/projects/app", False),
                                    ("/", "/project", True)):
            with self.subTest(root=root, cwd=cwd):
                self.assertEqual(self.snapshot([aggregate(cwd=cwd)], query(roots=(root,)))["sessions_total"], int(included))

    def test_multi_root_and_session_cwd_do_not_duplicate_usage(self):
        agg = aggregate(cwd=None)
        agg["sessions"][0]["cwd"] = "/fixtures/project/src"
        result = self.snapshot([agg], query(roots=("/fixtures", "/fixtures/project", "/fixtures/project")))
        self.assertEqual(result["summary"]["tokens"], 135)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["scope"]["roots"], ["fixtures", "project"])

    def test_root_aliases_keep_both_matching_paths_but_one_display_label(self):
        files = [aggregate(cwd="/var/fixtures/project"),
                 aggregate(CHILD, cwd="/private/var/fixtures/project/src")]
        result = self.snapshot(files, query(roots=("/var/fixtures/project", "/private/var/fixtures/project")))
        self.assertEqual(result["summary"]["tokens"], 270)
        self.assertEqual(result["sessions_total"], 2)
        self.assertEqual(result["scope"]["roots"], ["project"])
        self.assertEqual(result["scope"]["label"], "project")

    def test_shortened_store_ids_remain_unavailable_and_keys_survive_reordering(self):
        store = aggregate(source="cursor")
        one, two = copy.deepcopy(store["sessions"][0]), copy.deepcopy(store["sessions"][0])
        one.update(id="same1234", start=TODAY + "T10:00:00Z", records=copy.deepcopy(store["records"]))
        two.update(id="same1234", start=TODAY + "T11:00:00Z", records=copy.deepcopy(store["records"]))
        store["sessions"] = [one, two]
        result = self.snapshot([store], query("all", ()))
        keys = {s["start"]: s["key"] for s in result["sessions"]}
        self.assertEqual(len(set(keys.values())), 2)
        self.assertTrue(all(s["id"] == "" and s["detail_limited"] for s in result["sessions"]))
        store["sessions"].reverse()
        changed = self.snapshot([store], query("all", ()))
        self.assertEqual(keys, {s["start"]: s["key"] for s in changed["sessions"]})
        store["sessions"] = [one, copy.deepcopy(one), copy.deepcopy(one)]
        duplicates = self.snapshot([store], query("all", ()))
        self.assertEqual(len({s["key"] for s in duplicates["sessions"]}), 3)

    def test_copilot_uses_full_chat_id_instead_of_earlier_workspace_uuid(self):
        agg = aggregate(source="copilot")
        agg["path"] = "/private/" + OTHER + "/chatSessions/" + PARENT + ".jsonl"
        result = self.snapshot([agg])
        self.assertEqual(result["sessions"][0]["id"], PARENT)

    def test_unattributed_archives_stay_in_all_scope_and_peers_never_do(self):
        missing = aggregate(cwd=None)
        missing["archived"] = True
        relative = aggregate(CHILD, cwd="project")
        peer = aggregate(OTHER)
        peer["_device"] = "remote"
        workspace = self.snapshot([missing, relative, peer])
        self.assertEqual(workspace["sessions_total"], 0)
        self.assertEqual(workspace["scope"]["unassigned_sessions"], 2)
        all_scope = self.snapshot([missing, relative, peer], query("all", ()))
        self.assertEqual(all_scope["summary"]["tokens"], 270)
        self.assertEqual(all_scope["sessions_total"], 2)
        self.assertEqual(all_scope["scope"]["label"], "All projects")
        self.assertTrue(any(s["archived"] for s in all_scope["sessions"]))

    def test_conversation_dedup_uses_fullest_copy_then_live(self):
        old = aggregate(inp=50)
        old["archived"] = True
        old["path"] = "/archive/" + Path(old["path"]).name
        live = aggregate()
        before = copy.deepcopy([old, live])
        result = self.snapshot([old, live])
        self.assertEqual(result["summary"]["tokens"], 135)
        self.assertEqual(result["sessions_total"], 1)
        self.assertFalse(result["sessions"][0]["archived"])
        self.assertEqual([old, live], before)
        self.assertEqual(result["sessions"][0]["key"], self.snapshot([old])["sessions"][0]["key"])

    def test_scoped_days_price_each_model_and_do_not_copy_database_totals(self):
        store = aggregate(source="opencode")
        store["path"] = "/private/logs/opencode.db"
        store["cwd"] = "/fixtures/project"
        first, second = copy.deepcopy(store["sessions"][0]), copy.deepcopy(store["sessions"][0])
        first.update(_sid="native-one", cwd="/fixtures/project", records={TODAY + "\t" + MODEL: {"in": 10, "cost": 0.5, "asst": 1}})
        second.update(_sid="native-two", cwd="/other/project", records={TODAY + "\tUnknown": {"out": 20, "cost": 1.5, "asst": 1}})
        store["sessions"] = [first, second]
        result = self.snapshot([store])
        self.assertEqual(result["summary"]["tokens"], 10)
        self.assertEqual(result["summary"]["cost"], .5)
        self.assertEqual(result["sessions"][0]["tokens"], 10)
        all_scope = self.snapshot([store], query("all", ()))
        self.assertEqual(all_scope["summary"]["tokens"], 30)
        self.assertEqual(all_scope["summary"]["cost"], 2)
        self.assertEqual({s["tokens"] for s in all_scope["sessions"]}, {10, 20})
        # An old multi-session store's aggregate cwd cannot identify its children.
        for session in store["sessions"]:
            session.pop("cwd")
            session.pop("records")
        self.assertEqual(self.snapshot([store])["sessions_total"], 0)
        legacy = self.snapshot([store], query("all", ()))
        self.assertEqual(legacy["summary"]["tokens"], 135)
        self.assertTrue(all(s["tokens"] == 0 and s["detail_limited"] for s in legacy["sessions"]))

    def test_range_prices_recorded_tiers_and_scopes_efforts_compactions(self):
        agg = aggregate()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        older = (datetime.date.today() - datetime.timedelta(days=35)).isoformat()
        agg["records"][yesterday + "\tHaiku 5.5"] = {"in": 200000, "lc_in": 200000, "out": 10, "lc_out": 10, "asst": 1}
        agg["records"][older + "\t" + MODEL] = {"in": 9999, "asst": 1}
        session = agg["sessions"][0]
        session["compactions"] = [{"date": TODAY}, {"date": yesterday}, {"date": older}]
        session["reasoning_effort"] = {"days": {TODAY + "\t" + MODEL: {"levels": {"high": 1}},
            yesterday + "\tHaiku 5.5": {"levels": {"max": 1}}, older + "\t" + MODEL: {"levels": {"low": 1}}}}
        today = self.snapshot([agg])
        week = self.snapshot([agg], query(period="7d"))
        self.assertEqual(today["sessions"][0]["efforts"], ["high"])
        self.assertEqual(week["sessions"][0]["efforts"], ["high", "max"])
        self.assertEqual(week["summary"]["compactions"], 2)
        self.assertEqual(len(week["days"]), 7)
        expected = sum(D._priced_usage("codex", key.split("\t")[1], key[:10], row, False)["cost"]
                       for key, row in agg["records"].items() if key[:10] >= yesterday)
        self.assertAlmostEqual(week["summary"]["cost"], expected)
        self.assertEqual(week["sessions"][0]["cost"], week["summary"]["cost"])
        self.assertEqual(sum(d["tokens"] for d in week["days"]), week["summary"]["tokens"])

    def test_ultra_and_partial_effort_remain_visible_without_changing_usage(self):
        agg = aggregate()
        yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
        agg["sessions"][0]["reasoning_effort"] = {"days": {
            TODAY + "\t" + MODEL: {"levels": {"ultra": 2}, "unrecorded": 1},
            yesterday + "\t" + MODEL: {"levels": {"xhigh": 1}, "unrecorded": 0}}}
        today = self.snapshot([agg])
        self.assertEqual(today["sessions"][0]["efforts"], ["ultra", "unrecorded"])
        self.assertEqual(today["summary"]["tokens"], 135)
        self.assertEqual(self.snapshot([agg], query(period="7d"))["sessions"][0]["efforts"],
                         ["xhigh", "ultra", "unrecorded"])
        agg["sessions"][0]["reasoning_effort"]["days"][TODAY + "\t" + MODEL]["levels"] = {}
        self.assertEqual(self.snapshot([agg])["sessions"][0]["efforts"], ["unrecorded"])

    def test_parent_edges_require_native_identity_and_keep_child_usage_separate(self):
        parent, child = aggregate(), aggregate(CHILD, subagent=True, inp=10)
        child["sessions"][0]["parent_id"] = PARENT
        result = self.snapshot([parent, child])
        main = next(s for s in result["sessions"] if not s["subagent"])
        worker = next(s for s in result["sessions"] if s["subagent"])
        self.assertEqual(worker["parent_status"], "linked")
        self.assertEqual(worker["parent_key"], main["key"])
        self.assertEqual(worker["id"], CHILD)
        self.assertEqual(main["tokens"], 135)
        self.assertEqual(result["summary"]["tokens"], 180)
        self.assertEqual(result["summary"]["main_agents"], 1)
        self.assertEqual(result["summary"]["subagents"], 1)
        parent["cwd"] = "/other/workspace"
        private = self.snapshot([parent, child])["sessions"][0]
        self.assertEqual(private["parent_status"], "outside_scope")
        self.assertIsNone(private["parent_title"])
        self.assertIsNone(private["parent_key"])
        for invalid in (PARENT[:8], CHILD, None):
            child["sessions"][0]["parent_id"] = invalid
            self.assertEqual(self.snapshot([child])["sessions"][0]["parent_status"], "unavailable")

    def test_claude_parent_uses_full_recorded_transcript_namespace(self):
        root = "/private/claude/projects/project"
        parent, child = aggregate(source="claude"), aggregate(CHILD, source="claude", subagent=True)
        parent["path"] = root + "/" + PARENT + ".jsonl"
        child["path"] = root + "/" + PARENT + "/subagents/workflows/wf_one/agent-first.jsonl"
        worker = next(s for s in self.snapshot([parent, child])["sessions"] if s["subagent"])
        self.assertEqual(worker["parent_status"], "linked")
        self.assertEqual(worker["id"], PARENT + "/subagents/workflows/wf_one/agent-first")
        parent["path"] = "/other" + parent["path"]
        worker = next(s for s in self.snapshot([parent, child])["sessions"] if s["subagent"])
        self.assertEqual(worker["parent_status"], "unavailable")

    def test_session_cap_never_caps_summary_or_leaves_dangling_parent_key(self):
        parent, child = aggregate(), aggregate(CHILD, subagent=True)
        parent["sessions"][0]["end"] = TODAY + "T10:00:00+00:00"
        child["sessions"][0]["parent_id"] = PARENT
        result = self.snapshot([parent, child], cap=1)
        self.assertEqual(result["summary"]["tokens"], 270)
        self.assertEqual(result["summary"]["sessions"], 2)
        self.assertEqual(result["sessions_total"], 2)
        self.assertEqual(len(result["sessions"]), 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["sessions"][0]["parent_status"], "outside_scope")

    def test_prompt_only_and_compaction_only_sessions_remain_visible(self):
        prompts = aggregate(inp=0, out=0, cr=0, cc=0)
        prompts["records"] = {TODAY + "\t(user)": {"user": 1}}
        compact = aggregate(CHILD, inp=0, out=0, cr=0, cc=0)
        compact["records"] = {}
        compact["sessions"][0]["compactions"] = [{"date": TODAY}]
        result = self.snapshot([prompts, compact])
        self.assertEqual(result["sessions_total"], 2)
        self.assertEqual(result["summary"]["tokens"], 0)
        self.assertEqual(result["summary"]["compactions"], 1)

    def test_private_fields_are_whitelisted_and_numbers_stay_finite(self):
        agg = aggregate()
        agg.update(account_metadata={"secret": "PRIVATE_ACCOUNT"}, state={"arguments": "PRIVATE_ARGUMENT"})
        agg["sessions"][0].update(prompt="PRIVATE_PROMPT", account_metadata={"secret": "PRIVATE_ACCOUNT"},
                                  project="/private/project", start="PRIVATE_TIMESTAMP")
        for row in agg["records"].values():
            row.update({"in": float("nan"), "out": float("inf"), "cr": -1, "cc": True})
        result = self.snapshot([agg])
        serialized = json.dumps(result, allow_nan=False)
        for secret in ("PRIVATE_ACCOUNT", "PRIVATE_ARGUMENT", "PRIVATE_PROMPT", "PRIVATE_TIMESTAMP", "/private", "/fixtures"):
            self.assertNotIn(secret, serialized)
        self.assertTrue(all(math.isfinite(n) and n >= 0 for n in result["summary"].values()))

    def test_dashboard_wrapper_holds_parser_lock_and_never_reads_sources(self):
        agg = aggregate()
        before = copy.deepcopy(agg)
        def check_price(*args):
            self.assertTrue(D._refresh_lock.locked())
            return original_price(*args)
        original_price = D._priced_usage
        with patch.dict(D._state, {"files": {agg["path"]: agg}}), \
             patch.object(D, "_local_id", return_value="local"), \
             patch.object(D, "refresh_sync", return_value={"revision": "shared-clock"}), \
             patch.object(D.SN, "display_names", return_value={}), \
             patch.object(D, "_priced_usage", side_effect=check_price), \
             patch.object(D.P, "discover", side_effect=AssertionError("must not discover")), \
             patch.object(D.P, "update_file", side_effect=AssertionError("must not reparse")), \
             patch.object(D, "_peer_items", side_effect=AssertionError("must not read peers")), \
             patch.object(D, "_build_payload_locked", side_effect=AssertionError("must not scan setup")), \
             patch("builtins.open", side_effect=AssertionError("must not read files")):
            result = D.build_editor_snapshot(query())
        self.assertEqual(result["revision"], "shared-clock")
        self.assertEqual(agg, before)

    def test_private_claude_display_aliases_are_resolved_before_projection(self):
        agg = aggregate(source="claude", subagent=True)
        agg["path"] = "/private/claude/" + PARENT + "/subagents/agent-fixture.jsonl"
        with patch.dict(D._state, {"files": {agg["path"]: agg}}), \
             patch.object(D, "_local_id", return_value="local"), \
             patch.object(D, "refresh_sync", return_value={"revision": "fixture"}), \
             patch.object(D.SN, "display_names", return_value={"private-alias-key": "Readable worker name"}), \
             patch.object(D.SN, "display_name", return_value="Readable worker name") as alias:
            result = D.build_editor_snapshot(query())
        self.assertEqual(result["sessions"][0]["title"], "Readable worker name")
        self.assertEqual(alias.call_args.args[1:4], ("local", "claude", agg["path"]))
        self.assertNotIn("private-alias-key", json.dumps(result))
        with patch.dict(D._state, {"files": {agg["path"]: agg}}), \
             patch.object(D, "_local_id", return_value="local"), \
             patch.object(D, "refresh_sync", return_value={"revision": "fixture"}), \
             patch.object(D.SN, "display_names", side_effect=ValueError("private metadata path")):
            unavailable = D.build_editor_snapshot(query())
        self.assertEqual(unavailable["sessions"][0]["title"], "Invented title")
        self.assertTrue(any("display names could not be read" in line for line in unavailable["limitations"]))


class EditorHTTPTests(unittest.TestCase):
    def handler(self, path="/api/editor?scope=all", headers=None, address="127.0.0.1"):
        handler = object.__new__(D.Handler)
        handler.path, handler.headers = path, headers or {"Host": "127.0.0.1:7878"}
        handler.client_address = (address, 12345)
        handler._send = Mock()
        return handler

    def test_origin_and_loopback_guards_run_before_projection(self):
        for headers, address in (({"Host": "attacker.invalid:7878"}, "127.0.0.1"),
                                  ({"Host": "127.0.0.1:7878", "Origin": "https://attacker.invalid"}, "127.0.0.1"),
                                  ({"Host": "127.0.0.1:7878", "Sec-Fetch-Site": "cross-site"}, "127.0.0.1"),
                                  ({"Host": "127.0.0.1:7878"}, "192.168.1.10")):
            handler = self.handler(headers=headers, address=address)
            with patch.dict(D.BIND, {"host": "127.0.0.1", "port": 7878}), patch.object(D, "build_editor_snapshot") as build:
                handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], 403)
            build.assert_not_called()

    def test_invalid_query_and_backend_failures_do_not_expose_private_paths(self):
        handler = self.handler()
        for failure, code in ((ValueError("/private/path"), 400), (RuntimeError("/private/path"), 500)):
            with patch.dict(D.BIND, {"host": "127.0.0.1", "port": 7878}), \
                 patch.object(D, "build_editor_snapshot", side_effect=failure), patch("traceback.print_exc"):
                handler.do_GET()
            self.assertEqual(handler._send.call_args.args[0], code)
            self.assertNotIn("/private", handler._send.call_args.args[1])

    def test_successful_get_uses_exact_query_and_has_no_write_route(self):
        handler = self.handler("/api/editor?scope=workspace&root=%2Ffixtures%2Fproject&range=7d")
        with patch.dict(D.BIND, {"host": "127.0.0.1", "port": 7878}), \
             patch.object(D, "build_editor_snapshot", return_value={"schema": 1}) as build:
            handler.do_GET()
        build.assert_called_once_with("scope=workspace&root=%2Ffixtures%2Fproject&range=7d")
        self.assertEqual(handler._send.call_args.args, (200, '{"schema": 1}'))


if __name__ == "__main__":
    unittest.main()
