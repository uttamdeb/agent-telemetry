"""Cursor schema migration retains the existing durable ledger; synthetic only."""
import contextlib
import copy
import io
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import dashboard as D
import parser as P
import cursor_data as CD


class CursorMigrationTests(unittest.TestCase):
    def test_previous_compatible_ledger_retains_all_entries(self):
        files = {
            "missing-cursor-store": {"source": "cursor", "archived": True,
                                     "records": {"2026-01-01\tUnknown": {"in": 42}}},
            "old-live-store": {"source": "cursor", "records": {}, "sessions": []},
            "other-live-log": {"source": "codex", "records": {"unchanged": {"in": 17}}},
        }
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "ledger.json"
            for version in range(53, 63):
                with self.subTest(version=version):
                    path.write_text(json.dumps({"version": version, "files": files}))
                    with patch.object(D, "CACHE_PATH", str(path)), \
                         patch.object(D, "_state", {"files": {}, "version": D.CACHE_VERSION}), \
                         patch.object(D, "_dirty", {"v": False}), \
                         patch.object(D, "_cache_error", {"v": None}), \
                         contextlib.redirect_stderr(io.StringIO()):
                        D.load_cache()
                        self.assertEqual(D._state["files"], copy.deepcopy(files))
                        self.assertTrue(D._dirty["v"])
                        self.assertIsNone(D._cache_error["v"])
                    self.assertEqual(json.loads(path.read_text())["version"], version)

    def test_tombstone_and_deleted_rows_preserve_history_without_duplicate_receipts(self):
        cid = "11111111-2222-4333-8444-555555555555"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.vscdb"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)")
            con.execute("INSERT INTO cursorDiskKV VALUES(?,?)", ("composerData:" + cid,
                        json.dumps({"composerId": cid, "createdAt": "2026-10-25T10:00:00Z"})))
            def bubble(identity, value, minute=0):
                con.execute("INSERT OR REPLACE INTO cursorDiskKV VALUES(?,?)", (
                    "bubbleId:" + cid + ":" + identity, json.dumps({
                        "type": 2, "createdAt": f"2026-10-25T10:{minute:02d}:00Z",
                        "requestId": "PRIVATE-REQUEST-IDENTITY-" + identity,
                        "tokenCount": {"inputTokens": value, "outputTokens": 2},
                        "modelInfo": {"modelName": "fixture-model"}})))
                con.commit()
            bubble("first", 10)
            first = P.update_file(None, "cursor", str(path), "Cursor", {})
            self.assertEqual(first["totals"]["in"], 10)
            self.assertNotIn("PRIVATE-REQUEST-IDENTITY", json.dumps(first))
            con.execute("UPDATE cursorDiskKV SET value=NULL WHERE key=?", ("bubbleId:" + cid + ":first",))
            bubble("second", 20, 1)
            second = P.update_file(first, "cursor", str(path), "Cursor", {})
            self.assertEqual(second["totals"]["in"], 30)
            self.assertEqual(second["totals"]["asst"], 2)
            self.assertTrue(second["sessions"][0]["retained_history"])
            con.execute("DELETE FROM cursorDiskKV WHERE key LIKE 'bubbleId:%'")
            con.commit()
            third = P.update_file(second, "cursor", str(path), "Cursor", {})
            self.assertEqual(third["totals"], second["totals"])
            bubble("first", 10)
            restored = P.update_file(third, "cursor", str(path), "Cursor", {})
            self.assertEqual(restored["totals"], second["totals"])
            con.execute("DROP TABLE cursorDiskKV")
            con.commit()
            with self.assertRaises(CD.CursorReadError):
                P.update_file(restored, "cursor", str(path), "Cursor", {})
            self.assertEqual(restored["totals"]["in"], 30)
            con.close()

    def test_undated_usage_is_supplemental_and_never_assigned_to_creation_day(self):
        cid = "11111111-2222-4333-8444-555555555555"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "state.vscdb"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE cursorDiskKV(key TEXT PRIMARY KEY,value BLOB)")
            con.execute("INSERT INTO cursorDiskKV VALUES(?,?)", ("composerData:" + cid,
                json.dumps({"composerId": cid, "createdAt": "2026-10-25T10:00:00Z"})))
            con.execute("INSERT INTO cursorDiskKV VALUES(?,?)", ("bubbleId:" + cid + ":undated",
                json.dumps({"type": 2, "tokenCount": {"inputTokens": 8084, "outputTokens": 587}})))
            con.commit(); con.close()
            agg = P.update_file(None, "cursor", str(path), "Cursor", {})
            self.assertEqual(agg["sessions"][0]["undated_usage"], {"in": 8084, "out": 587, "asst": 1})
            self.assertEqual(agg["totals"]["in"], 0)
            self.assertEqual(agg["records"], {})
            self.assertEqual(agg["sessions"][0]["days"], {})

    def test_previous_peer_versions_remain_readable(self):
        for version in range(52, D.CACHE_VERSION + 1):
            self.assertTrue(D._peer_cache_ok(version), version)
        for version in (None, True, "62", 51, D.CACHE_VERSION + 1):
            self.assertFalse(D._peer_cache_ok(version), version)
