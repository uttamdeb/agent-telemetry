"""Stdlib regression tests; all stores and cache actions use temporary fixtures."""
import contextlib
import copy
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


class RegressionTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="telemetry-test-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        patches = [
            patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}),
            patch.dict(D._meta, D._meta.copy()),
            patch.dict(D._dirty, {"v": False}),
            patch.dict(D._grew, {"v": False}),
            patch.dict(D._gen, D._gen.copy()),
            patch.object(D, "CACHE_PATH", str(self.root / "cache.json")),
            patch.object(D, "save_cache"),
            patch.object(P, "_copilot_project_map", return_value={}),
            patch.object(D, "_peer_items", return_value=[]),
            patch.object(D, "_local_id", return_value="local-test"),
            patch.object(D, "_devices_meta", return_value=[]),
            patch.object(D, "_context_files", return_value=[]),
            patch.object(D, "_installed", return_value=[]),
            patch.object(D, "_mcp_servers", return_value={}),
        ]
        for mock in patches:
            mock.start()
            self.addCleanup(mock.stop)

    def aggregate(self, source="copilot", device=None):
        agg = P._blank_agg(source, "fixture")
        P._rec(agg, "2026-10-02", "Gemini 4 Argon").update(
            {"in": 100, "out": 10, "asst": 1})
        if device:
            agg["_device"] = device
        return agg

    def test_gpt_6_1_terra_is_named_and_priced_from_every_source(self):
        # Verified against developers.openai.com/api/docs/pricing (2026-10-30): $1 / $5, cached $0.05,
        # 30-minute cache write $1.25, short context. It priced at $0 before, though every source named it.
        for raw in ("gpt-6.1-terra", "openai/gpt-6.1-terra", "GPT-6.1 Terra", "gpt-6.1-terra-2026-10-26"):
            with self.subTest(raw=raw):
                self.assertEqual(P._canonicalize(raw), "GPT-6.1 Terra")
                self.assertEqual(P.price_of(raw, "2026-10-30"), (1, 5, 1.25, 0, 0.05))
        self.assertEqual(P.normalize_codex("gpt-6.1-terra"), "GPT-6.1 Terra")
        self.assertEqual(P.normalize_copilot("gpt-6.1-terra", ""), "GPT-6.1 Terra")
        self.assertEqual(P._normalize_opencode("gpt-6.1-terra", "openai"), "GPT-6.1 Terra")
        self.assertEqual(P.price_of("GPT-6.1 Sol", "2026-10-30"), (2, 10, 2.50, 0, 0.10), "Sol unchanged")

    def test_argon_names_prices_and_modality_exclusions(self):
        aliases = ["Gemini 4 Argon", "Google: Gemini 4 Argon", "gemini-4-argon",
                   "gemini-4-argon-preview", "gemini_4_argon", "google/gemini-4-argon",
                   "models/gemini-4-argon", "gemini-4-argon-preview-2026-09-30"]
        for name in aliases:
            with self.subTest(name=name):
                self.assertEqual(P._canonicalize(name), "Gemini 4 Argon")
                self.assertEqual(P.price_of(name), (2, 10, 0, 0, 0.1))
                self.assertEqual(P._normalize_cursor_model(name), "Gemini 4 Argon")
                self.assertEqual(P._normalize_hermes(name), "Gemini 4 Argon")
                self.assertEqual(P._normalize_opencode(name, "google"), "Gemini 4 Argon")
                self.assertEqual(P.normalize_copilot(name, ""), "Gemini 4 Argon")
        for suffix in ("live", "tts", "image", "audio", "embedding", "transcribe",
                       "translate", "robot", "omni"):
            with self.subTest(modality=suffix):
                name = "gemini-4-argon-" + suffix
                self.assertNotEqual(P._canonicalize(name), "Gemini 4 Argon")
                self.assertFalse(any(P.price_of(name)))
        args = ("Gemini 4 Argon", 1_000_000, 1_000_000, 1_000_000, 0, 0)
        self.assertAlmostEqual(D._cost("cursor", *args), 12.1)
        self.assertEqual(D._cost("opencode", *args, logged_cost=7.5), 7.5)
        self.assertAlmostEqual(D._cost("openclaw", *args, logged_cost=99), 12.1)

    def test_copilot_recorded_tokens_and_older_text_estimates(self):
        path = self.root / "chat.json"
        request = {"timestamp": 1790899200000, "modelId": "google/gemini-4-argon",
                   "message": {"text": "test"}, "promptTokens": 1_000_000,
                   "completionTokens": 100_000,
                   "result": {"details": "Gemini 4 Argon • 1x"}}
        path.write_text(json.dumps({"requests": [request]}), encoding="utf-8")
        agg = P.update_file(None, "copilot", str(path), "VS Code", {})
        row = next(iter(agg["records"].values()))
        self.assertEqual((row["in"], row["out"]), (1_000_000, 100_000))
        self.assertTrue(all(k.endswith("Gemini 4 Argon") for k in agg["records"]))
        request.pop("promptTokens")
        request.pop("completionTokens")
        estimated = P._blank_agg("copilot", "fixture")
        P._copilot_apply_request(estimated, request)
        self.assertEqual(next(iter(estimated["records"].values()))["in"], 1)
        D._state["files"] = {str(path): agg}
        note = D.build_payload()["pricing_note"]
        self.assertIn("uses recorded request token counts", note)
        self.assertNotIn("logs NO token counts", note)

    def test_cursor_and_hermes_detect_wal_only_changes(self):
        for source in ("cursor", "hermes"):
            with self.subTest(source=source):
                db = self.root / (source + ".db")
                con = sqlite3.connect(str(db))
                self.addCleanup(con.close)
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA wal_autocheckpoint=0")
                if source == "cursor":
                    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
                    con.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
                    con.execute("INSERT INTO cursorDiskKV VALUES (?,?)", ("composerData:session",
                        json.dumps({"composerId": "session", "createdAt": 1790899200000,
                                    "modelConfig": {"modelName": "gemini-4-argon"}})))

                    def append(i):
                        con.execute("INSERT INTO cursorDiskKV VALUES (?,?)",
                            ("bubbleId:session:" + str(i), json.dumps({"type": 2,
                             "createdAt": "2026-10-02T10:00:0%dZ" % i,
                             "tokenCount": {"inputTokens": 100, "outputTokens": 10}})))
                        con.commit()
                else:
                    con.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, git_branch TEXT, "
                                "title TEXT, model TEXT, started_at INTEGER, ended_at INTEGER, "
                                "archived INTEGER, source TEXT)")
                    con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, "
                                "input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER, "
                                "cache_write_tokens INTEGER, reasoning_tokens INTEGER, api_call_count INTEGER, "
                                "first_seen INTEGER, last_seen INTEGER)")
                    con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, "
                                "timestamp INTEGER, tool_calls TEXT)")
                    con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                        ("session", "/fixture", "main", "Test", "gemini-4-argon",
                         1790899200, 1790899200, 0, "cli"))
                    con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                        ("session", "gemini-4-argon", 0, 0, 0, 0, 0, 0, 1790899200, 1790899200))

                    def append(i):
                        con.execute("UPDATE session_model_usage SET input_tokens=?, "
                                    "output_tokens=?, api_call_count=?", (i * 100, i * 10, i))
                        con.execute("INSERT INTO messages VALUES (?,?,?,?)",
                                    ("session", "assistant", 1790899200 + i, None))
                        con.commit()
                append(1)
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                first = P.update_file(None, source, str(db), source, {})
                main_stat = (db.stat().st_size, db.stat().st_mtime_ns)
                append(2)
                self.assertEqual(main_stat, (db.stat().st_size, db.stat().st_mtime_ns))
                second = P.update_file(first, source, str(db), source, {})
                self.assertIsNot(second, first)
                self.assertEqual((second["totals"]["in"], second["totals"]["asst"]), (200, 2))
                self.assertEqual(second["size"], db.stat().st_size + Path(str(db) + "-wal").stat().st_size)
                self.assertIs(P.update_file(second, source, str(db), source, {}), second)
                con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                third = P.update_file(second, source, str(db), source, {})
                self.assertEqual(third["totals"], second["totals"])

    def test_failed_full_reparses_keep_previous_history_and_retry(self):
        chat = self.root / "chat.json"
        chat.write_text("{}", encoding="utf-8")
        legacy = self.root / "session"
        legacy.mkdir()
        agent = self.root / "agent"
        agent.mkdir()
        cases = [("copilot", chat, "parse_copilot"), ("opencode", legacy, "parse_opencode"),
                 ("openclaw", agent, "parse_openclaw")]
        for source, func in (("cursor", "parse_cursor"), ("hermes", "parse_hermes"),
                             ("opencode", "parse_opencode_db")):
            path = self.root / (source + ".db")
            path.write_text("placeholder", encoding="utf-8")
            cases.append((source, path, func))
        for source, path, func in cases:
            with self.subTest(source=source, parser=func):
                previous = self.aggregate(source)
                D._state["files"] = {str(path): previous}
                stderr = io.StringIO()
                with patch.object(P, "discover", return_value=[(source, str(path), None)]), \
                     patch.object(P, func, side_effect=TypeError("regression sentinel")), \
                     contextlib.redirect_stderr(stderr):
                    for _ in range(2):
                        D.refresh(verbose=False)
                        self.assertIs(D._state["files"][str(path)], previous)
                self.assertEqual(stderr.getvalue().count("TypeError: regression sentinel"), 2)
                self.assertIn("[parse:%s] %s" % (source, path), stderr.getvalue())

    def test_corrupt_sqlite_reports_error_and_preserves_history(self):
        for source in ("cursor", "hermes", "opencode"):
            with self.subTest(source=source):
                path = self.root / (source + ".db")
                path.write_text("not a database", encoding="utf-8")
                previous = self.aggregate(source)
                D._state["files"] = {str(path): previous}
                stderr = io.StringIO()
                opened = []
                original_open = P._open_ro_sqlite

                def capture_open(db_path):
                    con = original_open(db_path)
                    opened.append(con)
                    return con

                with patch.object(P, "discover", return_value=[(source, str(path), None)]), \
                     patch.object(P, "_open_ro_sqlite", side_effect=capture_open), \
                     contextlib.redirect_stderr(stderr):
                    D.refresh(verbose=False)
                self.assertIs(D._state["files"][str(path)], previous)
                self.assertIn("DatabaseError", stderr.getvalue())
                self.assertEqual(len(opened), 1)
                with self.assertRaises(sqlite3.ProgrammingError):
                    opened[0].execute("SELECT 1")

    def test_cache_migration_retains_archived_ledger(self):
        archived = self.aggregate()
        archived["archived"] = True
        Path(D.CACHE_PATH).write_text(json.dumps({"version": 52,
            "files": {"gone.json": archived, "live.json": self.aggregate("codex")}}), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            D.load_cache()
        self.assertEqual(D._state["files"], {"gone.json": archived})

    def test_conversation_deduplication_is_per_device_and_cross_platform(self):
        uid = "11111111-1111-1111-1111-111111111111"
        for source, name in (("copilot", uid + ".json"), ("codex", "rollout-" + uid + ".jsonl")):
            with self.subTest(source=source):
                local, peer = self.aggregate(source), self.aggregate(source, "remote-test")
                for sep in ("/", "\\"):
                    path = sep.join(("root", "sessions", name))
                    archive = sep.join(("root", "archived_sessions", name))
                    self.assertEqual(len(D._one_per_conversation([(path, local),
                        (archive, copy.deepcopy(local))])), 1)
                    self.assertEqual(len(D._one_per_conversation([(path, local),
                        ("peer:remote-test:" + archive, peer)])), 2)
                fuller = copy.deepcopy(local)
                next(iter(fuller["records"].values()))["cc"] = 500
                self.assertIs(D._one_per_conversation([("/sessions/" + name, local),
                    ("/archive/" + name, fuller)])[0], fuller)

    def test_payload_and_refresh_do_not_mutate_records_concurrently(self):
        agg = self.aggregate()
        D._state["files"] = {"fixture": agg}
        ready, done = threading.Event(), threading.Event()

        def grow():
            if ready.wait(2):
                with D._refresh_lock:
                    P._rec(agg, "2026-10-02", "second model")
                done.set()

        worker = threading.Thread(target=grow, daemon=True)
        worker.start()
        original_cost = D._cost

        def interleave(*args, **kwargs):
            if not ready.is_set():
                ready.set()
                self.assertFalse(done.wait(0.05), "refresh mutated the payload's records")
            return original_cost(*args, **kwargs)

        try:
            with patch.object(D, "_cost", side_effect=interleave):
                self.assertEqual(len(D.build_payload()["records"]), 1)
        finally:
            ready.set()
            worker.join(timeout=2)
        self.assertTrue(done.is_set())
        self.assertEqual(len(D.build_payload()["records"]), 2)

    def test_cache_rebuild_completes_and_disappearing_files_are_skipped(self):
        D._state["files"] = {"old": self.aggregate()}
        Path(D.CACHE_PATH).write_text("{}", encoding="utf-8")
        with patch.object(P, "discover", return_value=[]):
            result = D.cache_action("rebuild")
        self.assertEqual((result["dropped"], result["files"]), (1, 0))
        self.assertFalse(Path(D.CACHE_PATH).exists())
        with patch.object(P, "discover", return_value=[("codex", str(self.root / "gone.jsonl"), None)]):
            D.refresh(verbose=False)
        self.assertEqual(D._state["files"], {})


if __name__ == "__main__":
    unittest.main()
