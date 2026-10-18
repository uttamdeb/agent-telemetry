"""Project moves affect presentation and matching, never retained accounting/history."""
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import native as N
from test_editor_snapshot import aggregate, query, TODAY


def preferences():
    return N.validate_project_preferences({"version": 1,
        "aliases": {"old-project": "new-project"},
        "roots": {"/fixtures/old-project": "/fixtures/new-project"}})


class ProjectRelocationTests(unittest.TestCase):
    def test_private_preferences_roundtrip_and_missing_is_identity(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(N, "support_dir", return_value=Path(directory)):
            self.assertEqual(N.project_preferences(), {"version": 1, "aliases": {}, "roots": {}})
            N.set_project_preferences(preferences())
            self.assertEqual(N.project_preferences(), preferences())
            if sys.platform != "win32":
                self.assertEqual((Path(directory) / "project-names.json").stat().st_mode & 0o777, 0o600)

    def test_corrupt_unknown_oversized_and_cyclic_preferences_are_preserved(self):
        bad = [b'{', b'{"version":2}', b'x' * 65537,
               b'{"version":1,"aliases":{"a":"b","b":"a"}}',
               b'{"version":1,"roots":{"/a":"/a/subdir"}}',
               b'{"version":1,"roots":{"relative":"/target"}}']
        with tempfile.TemporaryDirectory() as directory, patch.object(N, "support_dir", return_value=Path(directory)):
            path = Path(directory) / "project-names.json"
            for raw in bad:
                with self.subTest(raw=raw[:60]):
                    path.write_bytes(raw)
                    with self.assertRaises(ValueError): N.project_preferences()
                    with self.assertRaises(ValueError): N.set_project_preferences(preferences())
                    self.assertEqual(path.read_bytes(), raw)

    def test_labels_and_paths_validate_before_writing(self):
        for raw in ({"version":1,"aliases":{"a":[]}},
                    {"version":1,"aliases":{"a":"b","b":None}},
                    {"version":1,"aliases":{"a":"/path"}},
                    {"version":1,"aliases":{"a":"line\nbreak"}},
                    {"version":1,"roots":{"/a":"/b","/b":"/a"}},
                    {"version":1,"aliases":{},"extra":"unknown"}):
            with self.subTest(value=raw), self.assertRaises(ValueError):
                N.validate_project_preferences(raw)

    def test_relocation_uses_explicit_full_roots_and_component_boundaries(self):
        p = preferences()
        with patch.object(N.os, "path", N.ntpath):
            self.assertEqual(preferences(), p)
            self.assertEqual(N.relocated_project_path("/fixtures/old-project/src", p),
                             "/fixtures/new-project/src")
        for original, expected in (("/fixtures/old-project/src", "/fixtures/new-project/src"),
                                   ("/fixtures/old-project-neighbor", "/fixtures/old-project-neighbor"),
                                   ("/elsewhere/old-project", "/elsewhere/old-project"),
                                   ("/fixtures/old-project/../outside", "/fixtures/old-project/../outside"),
                                   (None, None)):
            self.assertEqual(N.relocated_project_path(original, p), expected)
        windows = N.validate_project_preferences({"version":1,"roots":{r"C:\Work\Old":r"C:\Work\New"}})
        self.assertEqual(N.relocated_project_path("c:/work/OLD/src", windows), "C:/Work/New/src")
        self.assertEqual(N.relocated_project_path("c:/work/older", windows), "c:/work/older")

    def test_display_copies_preserve_source_identity_raw_archives_and_peer_paths(self):
        raw = aggregate(cwd="/fixtures/old-project")
        raw["project"] = raw["sessions"][0]["project"] = "old-project"
        raw["archived"] = True
        before = copy.deepcopy(raw)
        items = N.project_items([("local",raw),("peer:remote:item",raw)], "local", preferences())
        self.assertEqual(raw, before)
        for _, row in items:
            self.assertEqual(row["project"], "new-project")
            self.assertEqual(row["sessions"][0]["project"], "new-project")
            self.assertEqual(row["path"], raw["path"])
            self.assertIs(row["records"], raw["records"])
        self.assertEqual(items[0][1]["cwd"], "/fixtures/new-project")
        self.assertEqual(items[1][1]["cwd"], "/fixtures/old-project")

    def test_payload_mcp_and_editor_reconcile_without_mutating_the_ledger(self):
        raw = aggregate(cwd="/fixtures/old-project")
        raw["project"] = raw["sessions"][0]["project"] = "old-project"
        before = copy.deepcopy(raw)
        with ExitStack() as stack:
            for target, name, value in ((N,"project_preferences",preferences()),
                    (D,"_local_id","local"),(D,"_peer_items",[]),(D,"_devices_meta",[]),
                    (D,"_mcp_servers",[]),(D,"_codex_config",{"effort":"medium"}),
                    (D,"_context_files",[]),(D,"_installed",[]),(D.SN,"display_names",{})):
                stack.enter_context(patch.object(target,name,return_value=value))
            stack.enter_context(patch.dict(D._state,{"files":{raw["path"]:raw}}))
            payload = D.build_payload()
            self.assertEqual({r["project"] for r in payload["records"]}, {"new-project"})
            self.assertEqual({s["project"] for s in payload["sessions"]}, {"new-project"})
            self.assertEqual(payload["projects"][0]["tokens"],135)
            self.assertEqual(payload["project_aliases"],{"old-project":"new-project"})
            import telemetry_query
            mcp = telemetry_query.query_usage(payload,{"period":"today","group_by":["project"]},
                                               policy={"include_names":True})
            self.assertEqual(mcp["rows"][0]["project"],"new-project")
            self.assertEqual(mcp["totals"]["tokens"],135)
            snapshot = D.build_editor_snapshot(query(roots=("/fixtures/new-project",)))
            self.assertEqual(snapshot["summary"]["tokens"],135)
            self.assertEqual(snapshot["sessions"][0]["project"],"new-project")
            old_workspace = D.build_editor_snapshot(query(roots=("/fixtures/old-project",)))
            self.assertEqual(old_workspace["summary"]["tokens"], snapshot["summary"]["tokens"])
            self.assertEqual(old_workspace["scope"]["roots"], snapshot["scope"]["roots"])
            self.assertEqual(D.build_editor_snapshot(query(roots=("/elsewhere/new-project",)))["sessions_total"],0)
            remote = copy.deepcopy(raw)
            remote["_device"] = "remote"
            with patch.object(D,"_peer_items",return_value=[("peer:remote:item",remote)]):
                shared = D.build_payload()
                self.assertEqual({r["project"] for r in shared["records"]},{"new-project"})
                self.assertEqual(sum(p["tokens"] for p in shared["projects"]),270)
        self.assertEqual(raw,before)


if __name__ == "__main__":
    unittest.main()
