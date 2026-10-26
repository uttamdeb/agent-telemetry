"""Usage a tool deletes from a log that is still on disk stays in the ledger.

OpenCode, Hermes and OpenClaw keep every session in one store; deleting a session
there (or OpenCode's undo deleting messages) used to drop its usage on the next
full reparse. Logs that shrink or are rewritten without a request did the same.
"""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P

STAMP = 1791789401130          # 2026-10-12 local-ish, epoch ms
SESSION_COLUMNS = ("id TEXT PRIMARY KEY, directory TEXT, title TEXT, agent TEXT, model TEXT, "
                   "version TEXT, parent_id TEXT, time_created INTEGER, time_updated INTEGER, "
                   "fork_session_id TEXT")
PRIVATE = ("_retained", "_store_detail", "_parsed_under", "_session_dims", "_prior_keys")


class RetentionBase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-retention-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.found = []
        for item in [patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}),
                     patch.dict(D._meta, D._meta.copy()), patch.dict(D._dirty, {"v": False}),
                     patch.dict(D._grew, {"v": False, "saved": 0}),
                     patch.dict(D._gen, D._gen.copy()), patch.dict(D._cache_error, {"v": None}),
                     patch.object(D, "CACHE_PATH", str(self.root / "cache.json")),
                     patch.object(P, "_copilot_project_map", return_value={}),
                     patch.object(P, "discover", side_effect=lambda: list(self.found)),
                     patch.object(D, "_peer_items", return_value=[]),
                     patch.object(D, "_local_id", return_value="fixture-device"),
                     patch.object(D, "_devices_meta", return_value=[]),
                     patch.object(D, "_context_files", return_value=[]),
                     patch.object(D, "_installed", return_value=[]),
                     patch.object(D, "_mcp_servers", return_value={})]:
            item.start(); self.addCleanup(item.stop)

    def refresh(self):
        D.refresh()

    def usage(self):
        """Every total the page derives, from the payload itself."""
        p = D.build_payload()
        return {
            "tokens": sum(r["in"] + r["out"] + r["cr"] + r["cc"] for r in p["records"]),
            "cost": round(sum(r["cost"] for r in p["records"]), 6),
            "asst": sum(r["asst"] for r in p["records"]),
            "user": sum(r["user"] for r in p["records"]),
            "tools": sum(t["count"] for t in p["tools"]),
            "hourly": sum(h["tokens"] for h in p["hourly"]),
            "session_tokens": sum(s["in"] + s["out"] + s["cr"] + s["cc"] for s in p["sessions"]),
        }

    def sessions(self):
        return {s.get("rename_id") or s["id"]: s for s in D.build_payload()["sessions"]}

    def entry(self, path):
        return D._state["files"][str(path)]


class OpenCodeRetentionTests(RetentionBase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "opencode.db"
        self.found = [("opencode", str(self.path), "opencode")]
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE session_v2 (" + SESSION_COLUMNS + ")")
        self.db.execute("CREATE TABLE session_message (id TEXT PRIMARY KEY, session_id TEXT, "
                        "time_created INTEGER, data TEXT, type TEXT, seq INTEGER)")

    def session(self, sid, created=STAMP):
        self.db.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sid, "/mock/" + sid, "Title " + sid, "build",
                         '{"id":"glm-5.3","providerID":"fixture"}', "fixture", None,
                         created, created + 60000, None))

    def message(self, sid, number, role, created=None, tokens=None):
        created = created if created is not None else STAMP + number * 1000
        data = {"time": {"created": created}}
        if role == "user":
            data["text"] = "Fixture prompt"
        else:
            data.update({"model": {"id": "glm-5.3", "providerID": "fixture"}, "cost": .1,
                         "tokens": tokens or {"input": 10, "output": 5, "reasoning": 2,
                                              "cache": {"read": 100, "write": 20}},
                         "content": [{"type": "tool", "name": "bash"}]})
        self.db.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                        ("msg-%s-%d" % (sid, number), sid, created, json.dumps(data), role, number))

    def conversation(self, sid, created=STAMP, turns=2, offset=0):
        self.session(sid, created)
        for n in range(turns):
            self.message(sid, offset + 2 * n, "user", created + 2 * n * 1000)
            self.message(sid, offset + 2 * n + 1, "assistant", created + (2 * n + 1) * 1000)

    def delete_session(self, sid):
        self.db.execute("DELETE FROM session_message WHERE session_id=?", (sid,))
        self.db.execute("DELETE FROM session_v2 WHERE id=?", (sid,))
        self.db.commit()

    def test_deleted_session_keeps_every_total_and_shows_as_deleted(self):
        self.conversation("ses_alpha_long_identity"); self.conversation("ses_beta_long_identity", STAMP + 3600_000)
        self.db.commit(); self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 4 * 135)
        self.delete_session("ses_beta_long_identity"); self.refresh()
        self.assertEqual(self.usage(), before)
        beta = self.sessions()["ses_beta_long_identity"]
        self.assertTrue(beta["retained"]); self.assertTrue(beta["archived"])
        self.assertFalse(beta["rename_available"])
        self.assertIn("deleted", beta["rename_reason"])
        alpha = self.sessions()["ses_alpha_long_identity"]
        self.assertFalse(alpha["archived"]); self.assertNotIn("retained", alpha)
        # Nothing private leaves the machine, but a connected device sees the usage.
        exported = D._export_agg(self.entry(self.path))
        self.assertFalse(set(PRIVATE) & set(exported))
        self.assertEqual(sum(r["in"] + r["out"] + r["cr"] + r["cc"]
                             for r in exported["records"].values()), 4 * 135)
        self.assertEqual(len(exported["opencode_ids"]), 4)
        # Unchanged store: no reparse, nothing kept twice.
        entry = self.entry(self.path)
        self.refresh()
        self.assertIs(self.entry(self.path), entry)
        self.assertEqual(self.usage(), before)

    def test_session_that_returns_replaces_its_kept_copy(self):
        self.conversation("ses_alpha_long_identity"); self.conversation("ses_beta_long_identity", STAMP + 3600_000)
        self.db.commit(); self.refresh()
        before = self.usage()
        self.delete_session("ses_beta_long_identity"); self.refresh()
        self.conversation("ses_beta_long_identity", STAMP + 3600_000); self.db.commit(); self.refresh()
        self.assertEqual(self.usage(), before)
        self.assertNotIn("retained", self.sessions()["ses_beta_long_identity"])
        self.assertNotIn("_retained", self.entry(self.path))

    def test_undo_deleting_messages_keeps_their_usage_and_new_work_adds(self):
        self.conversation("ses_alpha_long_identity", turns=3); self.db.commit(); self.refresh()
        before = self.usage()
        # OpenCode's undo deletes the reverted turn's messages from the store.
        self.db.execute("DELETE FROM session_message WHERE id IN ('msg-ses_alpha_long_identity-4', "
                        "'msg-ses_alpha_long_identity-5')")
        self.db.commit(); self.refresh()
        self.assertEqual(self.usage(), before)
        alpha = self.sessions()["ses_alpha_long_identity"]
        self.assertTrue(alpha["retained_usage"]); self.assertFalse(alpha["archived"])
        self.assertEqual(alpha["asst"], 3); self.assertEqual(alpha["user"], 3)
        self.message("ses_alpha_long_identity", 6, "user", STAMP + 10_000)
        self.message("ses_alpha_long_identity", 7, "assistant", STAMP + 11_000)
        self.db.commit(); self.refresh()
        after = self.usage()
        self.assertEqual(after["tokens"], before["tokens"] + 135)
        self.assertEqual(after["asst"], before["asst"] + 1)
        self.assertEqual(after["tools"], before["tools"] + 1)
        # The session is deleted later: everything it ever had is kept once.
        self.delete_session("ses_alpha_long_identity"); self.refresh()
        self.assertEqual(self.usage(), after)

    def test_undo_then_retry_in_the_same_refresh_keeps_the_deleted_turn(self):
        self.conversation("ses_alpha_long_identity", turns=3); self.db.commit(); self.refresh()
        before = self.usage()
        # OpenCode deletes the reverted turn when the retried prompt is sent: one
        # refresh sees a same-sized turn vanish and appear.
        self.db.execute("DELETE FROM session_message WHERE id IN ('msg-ses_alpha_long_identity-4', "
                        "'msg-ses_alpha_long_identity-5')")
        self.message("ses_alpha_long_identity", 6, "user", STAMP + 4_000)
        self.message("ses_alpha_long_identity", 7, "assistant", STAMP + 5_000)
        self.db.commit(); self.refresh()
        after = self.usage()
        self.assertEqual(after["tokens"], before["tokens"] + 135)
        self.assertEqual(after["asst"], before["asst"] + 1)
        self.assertEqual(after["user"], before["user"] + 1)
        self.assertEqual(after["tools"], before["tools"] + 1)
        self.assertEqual(after["hourly"], before["hourly"] + 135)

    def test_session_emptied_then_used_again_keeps_its_old_turns(self):
        self.conversation("ses_alpha_long_identity", turns=3); self.db.commit(); self.refresh()
        before = self.usage()
        self.db.execute("DELETE FROM session_message WHERE session_id='ses_alpha_long_identity'")
        self.db.commit(); self.refresh()
        self.assertEqual(self.usage(), before)
        self.message("ses_alpha_long_identity", 10, "user", STAMP + 20_000)
        self.message("ses_alpha_long_identity", 11, "assistant", STAMP + 21_000)
        self.db.commit(); self.refresh()
        self.assertEqual(self.usage()["tokens"], before["tokens"] + 135)
        alpha = self.sessions()["ses_alpha_long_identity"]
        self.assertNotIn("retained", alpha); self.assertTrue(alpha["retained_usage"])
        self.assertEqual(len([s for s in D.build_payload()["sessions"]]), 1)
        # Restoring the original messages replaces what was kept: nothing twice.
        for n in range(6):
            self.message("ses_alpha_long_identity", n, "user" if n % 2 == 0 else "assistant", STAMP + n * 1000)
        self.db.commit(); self.refresh()
        self.assertEqual(self.usage()["tokens"], before["tokens"] + 135)

    @unittest.skipUnless(hasattr(time, "tzset"), "needs time.tzset")
    def test_timezone_change_rebuckets_without_keeping_old_rows(self):
        original = os.environ.get("TZ")

        def zone(name):
            os.environ["TZ"] = name
            time.tzset()

        def restore():
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()
        self.addCleanup(restore)
        zone("Asia/Dhaka")
        self.conversation("ses_alpha_long_identity", turns=2); self.db.commit(); self.refresh()
        before = self.usage()
        zone("America/Los_Angeles")
        self.conversation("ses_beta_long_identity", STAMP + 3600_000, turns=1); self.db.commit(); self.refresh()
        after = self.usage()
        self.assertEqual(after["tokens"], before["tokens"] + 135)
        self.assertEqual(after["hourly"], after["tokens"])
        self.assertNotIn("_retained", self.entry(self.path))

    def test_parser_version_change_lowers_counts_but_keeps_deleted_sessions(self):
        self.conversation("ses_alpha_long_identity", turns=2)
        self.conversation("ses_beta_long_identity", STAMP + 3600_000)
        self.db.commit(); self.refresh()
        before = self.usage()
        self.entry(self.path)["_parsed_under"] = D.CACHE_VERSION - 1   # parsed by an older parser
        self.db.execute("DELETE FROM session_message WHERE id='msg-ses_alpha_long_identity-3'")
        self.delete_session("ses_beta_long_identity"); self.refresh()
        after = self.usage()
        # A lower count after a version change is a correction, not a deletion...
        self.assertEqual(after["tokens"], before["tokens"] - 135)
        # ...but a session that is gone from the store is kept regardless.
        self.assertTrue(self.sessions()["ses_beta_long_identity"]["retained"])
        self.assertEqual(self.entry(self.path)["_parsed_under"], D.CACHE_VERSION)

    def test_entry_written_before_full_ids_matches_unambiguous_short_ids(self):
        # Two sessions share their eight-character display id but not their start.
        self.conversation("ses_abcd_one", STAMP); self.conversation("ses_abcd_two", STAMP + 600_000)
        self.conversation("ses_zzzz_three", STAMP + 1_200_000)
        self.db.commit(); self.refresh()
        before = self.usage()
        legacy = self.entry(self.path)
        for key in ("_store_detail", "_parsed_under"):
            legacy.pop(key, None)
        for s in legacy["sessions"]:
            s.pop("_sid")
        self.delete_session("ses_abcd_two"); self.delete_session("ses_zzzz_three"); self.refresh()
        after = self.usage()
        # Tokens, cost and turns are kept; an old entry never recorded which
        # session each tool call or hour belonged to, so those cannot be.
        for field in ("tokens", "cost", "asst", "user", "session_tokens"):
            self.assertEqual(after[field], before[field], field)
        kept = [s for s in D.build_payload()["sessions"] if s.get("retained")]
        self.assertEqual(sorted(s["id"] for s in kept), ["ses_abcd", "ses_zzzz"])

    def test_older_entry_records_session_detail_once_before_any_deletion(self):
        self.conversation("ses_alpha_long_identity"); self.conversation("ses_beta_long_identity", STAMP + 3600_000)
        self.db.commit(); self.refresh()
        before = self.usage()
        legacy = self.entry(self.path)
        for key in ("_store_detail", "_parsed_under"):
            legacy.pop(key)
        for s in legacy["sessions"]:
            s.pop("_sid")
        self.refresh()                         # the store itself is unchanged
        upgraded = self.entry(self.path)
        self.assertIsNot(upgraded, legacy)
        self.assertEqual(set(upgraded["_store_detail"]), {"ses_alpha_long_identity", "ses_beta_long_identity"})
        self.assertNotIn("_retained", upgraded)
        self.assertEqual(self.usage(), before)
        self.refresh()
        self.assertIs(self.entry(self.path), upgraded)   # once, not on every refresh
        self.delete_session("ses_beta_long_identity"); self.refresh()
        self.assertEqual(self.usage(), before)          # tools and hours included

    def test_migrated_session_falling_back_to_its_v1_rows_keeps_later_messages(self):
        # OpenCode's V2 migration copies messages under the same ids and leaves
        # the V1 rows behind; an emptied V2 session falls back to them.
        sid = "ses_migrated_long_identity"
        self.db.execute("CREATE TABLE session (" + SESSION_COLUMNS + ")")
        self.db.execute("CREATE TABLE message (id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT)")
        self.db.execute("CREATE TABLE part (id TEXT PRIMARY KEY, message_id TEXT, session_id TEXT, "
                        "time_created INTEGER, data TEXT)")
        self.db.execute("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sid, "/mock/" + sid, "Title", "build", '{"id":"glm-5.3","providerID":"fixture"}',
                         "fixture", None, STAMP, STAMP + 60000, None))
        tokens = {"input": 10, "output": 5, "reasoning": 2, "cache": {"read": 100, "write": 20}}
        for n, role in ((0, "user"), (1, "assistant")):
            data = {"role": role, "time": {"created": STAMP + n * 1000}}
            if role == "assistant":
                data.update(modelID="glm-5.3", providerID="fixture", cost=.1, tokens=tokens)
                self.db.execute("INSERT INTO part VALUES (?,?,?,?,?)", ("prt-%d" % n, "msg-%s-%d" % (sid, n),
                                sid, STAMP + n * 1000, json.dumps({"type": "tool", "tool": "bash"})))
            self.db.execute("INSERT INTO message VALUES (?,?,?,?)",
                            ("msg-%s-%d" % (sid, n), sid, STAMP + n * 1000, json.dumps(data)))
        self.conversation(sid, turns=2)                    # migrated, then one more turn
        self.db.commit(); self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 2 * 135)
        self.db.execute("DELETE FROM session_message WHERE session_id=?", (sid,))   # undo to the start
        self.db.commit(); self.refresh()
        self.assertEqual(self.usage(), before)
        self.message(sid, 10, "user", STAMP + 20_000)
        self.message(sid, 11, "assistant", STAMP + 21_000)
        self.db.commit(); self.refresh()
        after = self.usage()
        self.assertEqual(after["tokens"], before["tokens"] + 135)
        self.assertEqual(after["asst"], before["asst"] + 1)
        self.assertEqual(after["tools"], before["tools"] + 1)

    def test_ambiguous_short_ids_are_neither_kept_nor_double_counted(self):
        self.conversation("ses_same_one", STAMP); self.conversation("ses_same_two", STAMP)
        self.db.commit(); self.refresh()
        legacy = self.entry(self.path)
        legacy.pop("_store_detail")
        for s in legacy["sessions"]:
            s.pop("_sid")
        self.delete_session("ses_same_two"); self.refresh()
        self.assertEqual(self.usage()["tokens"], 2 * 135)
        self.assertFalse(any(s.get("retained") for s in D.build_payload()["sessions"]))


class HermesRetentionTests(RetentionBase):
    def setUp(self):
        super().setUp()
        self.path = self.root / "state.db"
        self.found = [("hermes", str(self.path), "Hermes Agent")]
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE sessions (id TEXT, cwd TEXT, git_branch TEXT, title TEXT, model TEXT, "
                    "started_at REAL, ended_at REAL, archived INTEGER, source TEXT)")
        con.execute("CREATE TABLE session_model_usage (session_id TEXT, model TEXT, input_tokens INTEGER, "
                    "output_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, "
                    "reasoning_tokens INTEGER, api_call_count INTEGER, first_seen REAL, last_seen REAL)")
        con.execute("CREATE TABLE messages (session_id TEXT, role TEXT, timestamp REAL, tool_calls TEXT)")
        for i, sid in enumerate(("hermes-alpha", "hermes-beta")):
            ts = STAMP / 1000 + i * 3600
            con.execute("INSERT INTO sessions VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, "/fixture/" + sid, None, sid, "claude-sonnet-4-6", ts, ts + 60, 0, "cli"))
            con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (sid, "claude-sonnet-4-6", 100, 10, 20, 5, 0, 1, ts, ts))
            con.execute("INSERT INTO messages VALUES (?,?,?,?)", (sid, "user", ts, None))
            con.execute("INSERT INTO messages VALUES (?,?,?,?)",
                        (sid, "assistant", ts + 5, json.dumps([{"function": {"name": "terminal"}}])))
        con.commit(); con.close()

    def test_main_model_change_moves_counts_without_keeping_old_rows(self):
        self.refresh()
        before = self.usage()
        con = sqlite3.connect(self.path)
        ts = STAMP / 1000 + 120
        con.execute("INSERT INTO session_model_usage VALUES (?,?,?,?,?,?,?,?,?,?)",
                    ("hermes-alpha", "gpt-5.5", 5000, 100, 0, 0, 0, 2, ts, ts))
        con.execute("INSERT INTO messages VALUES (?,?,?,?)", ("hermes-alpha", "user", ts, None))
        con.execute("INSERT INTO messages VALUES (?,?,?,?)", ("hermes-alpha", "assistant", ts + 5, None))
        con.commit(); con.close()
        self.refresh()
        after = self.usage()
        self.assertEqual(after["tokens"], before["tokens"] + 5100)
        self.assertEqual(after["asst"], before["asst"] + 1)
        self.assertEqual(after["user"], before["user"] + 1)
        self.assertEqual(after["tools"], before["tools"])

    def test_deleted_session_keeps_usage(self):
        self.refresh()
        before = self.usage()
        con = sqlite3.connect(self.path)
        for table, column in (("sessions", "id"), ("session_model_usage", "session_id"), ("messages", "session_id")):
            con.execute("DELETE FROM %s WHERE %s='hermes-beta'" % (table, column))
        con.commit(); con.close()
        self.refresh()
        self.assertEqual(self.usage(), before)
        self.assertTrue(self.sessions()["hermes-beta"]["retained"])


class OpenClawRetentionTests(RetentionBase):
    def setUp(self):
        super().setUp()
        self.agent = self.root / "agents" / "main"
        (self.agent / "sessions").mkdir(parents=True)
        self.found = [("openclaw", str(self.agent), "OpenClaw")]

    @staticmethod
    def event(ts, role, inp=0):
        msg = {"role": role, "content": [{"type": "text", "text": "fixture"}]}
        if role == "assistant":
            msg.update(model="claude-sonnet-4-6", usage={"input": inp, "output": 10, "cacheRead": 0, "cacheWrite": 0},
                       content=[{"type": "toolCall", "name": "bash"}])
        return {"type": "message", "timestamp": ts, "message": msg}

    def write(self, sid, events):
        lines = [{"type": "session", "cwd": "/fixture/" + sid}] + events
        (self.agent / "sessions" / (sid + ".jsonl")).write_text("".join(json.dumps(e) + "\n" for e in lines))

    def test_fork_does_not_recount_a_deleted_parents_responses(self):
        shared = [self.event("2026-10-12T10:00:00Z", "user"), self.event("2026-10-12T10:00:05Z", "assistant", 100)]
        self.write("a-parent", shared)
        self.write("b-fork", shared + [self.event("2026-10-12T11:00:00Z", "user"),
                                       self.event("2026-10-12T11:00:05Z", "assistant", 300)])
        self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 110 + 310)
        (self.agent / "sessions" / "a-parent.jsonl").unlink()
        self.refresh()
        self.assertEqual(self.usage()["tokens"], before["tokens"])
        self.assertEqual(self.usage()["asst"], before["asst"])
        self.assertTrue(self.sessions()["a-parent"]["retained"])


    def test_new_fork_sorting_before_its_parent_counts_shared_history_once(self):
        shared = [self.event("2026-10-12T10:00:00Z", "user"), self.event("2026-10-12T10:00:05Z", "assistant", 100)]
        self.write("b-parent", shared); self.refresh()
        self.write("a-fork", shared + [self.event("2026-10-12T11:00:00Z", "user"),
                                       self.event("2026-10-12T11:00:05Z", "assistant", 300)])
        self.refresh()
        self.assertEqual(self.usage()["tokens"], 110 + 310)
        self.assertEqual(self.usage()["asst"], 2)
        self.refresh()
        self.assertEqual(self.usage()["tokens"], 110 + 310)

    def test_parent_shortened_while_a_fork_holds_the_copy_counts_once(self):
        a = self.event("2026-10-12T10:00:05Z", "assistant", 100)
        b = self.event("2026-10-12T10:10:05Z", "assistant", 200)
        self.write("a-parent", [a, b])
        self.write("b-fork", [a, b, self.event("2026-10-12T11:00:05Z", "assistant", 300)])
        self.refresh()
        before = self.usage()
        self.write("a-parent", [a]); self.refresh()
        self.assertEqual(self.usage()["tokens"], before["tokens"])
        self.assertEqual(self.usage()["asst"], before["asst"])


class LogRetentionTests(RetentionBase):
    """One session per file: deleting the session deletes the file (archived);
    a log that is shortened or rewritten keeps what it no longer holds."""

    def claude_line(self, ident, kind, date, request=None, inp=0):
        record = {"uuid": "%012d" % ident, "timestamp": date, "type": kind}
        if kind == "user":
            record["message"] = {"content": "Fix this fixture"}
        else:
            record.update(requestId=request, message={
                "id": request, "model": "claude-sonnet-4-6",
                "content": [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}],
                "usage": {"input_tokens": inp, "output_tokens": 10, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0}})
        return json.dumps(record) + "\n"

    def claude_turn(self, n, inp):
        return (self.claude_line(2 * n, "user", "2026-10-12T12:%02d:00Z" % n)
                + self.claude_line(2 * n + 1, "assistant", "2026-10-12T12:%02d:05Z" % n, "req-%d" % n, inp))

    def test_shortened_claude_log_keeps_removed_usage_through_appends_and_backfills(self):
        path = self.root / "11111111-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200)); self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 320)
        path.write_text(self.claude_turn(0, 100)); self.refresh()     # the log got shorter
        self.assertEqual(self.usage(), before)
        session = D.build_payload()["sessions"][0]
        self.assertTrue(session["retained_usage"]); self.assertFalse(session["archived"])
        with open(path, "a") as f:
            f.write(self.claude_turn(2, 400))
        self.refresh()
        grown = self.usage()
        self.assertEqual(grown["tokens"], 320 + 410)
        self.assertEqual(grown["asst"], before["asst"] + 1)
        # A metadata backfill reparses the whole log; what was kept stays kept.
        self.entry(path)["reasoning_effort_version"] = 0
        self.refresh()
        self.assertEqual(self.usage(), grown)

    def test_growing_log_and_marker_reparses_keep_nothing_extra(self):
        path = self.root / "22222222-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]
        path.write_text(self.claude_turn(0, 100)); self.refresh()
        with open(path, "a") as f:
            f.write(self.claude_turn(1, 200))
        self.refresh()
        self.entry(path)["reasoning_effort_version"] = 0
        self.refresh()
        self.assertEqual(self.usage()["tokens"], 320)
        self.assertNotIn("_retained", self.entry(path))

    def test_deleted_claude_codex_and_copilot_logs_stay_archived(self):
        claude = self.root / "33333333-2222-4333-8444-555555555555.jsonl"
        claude.write_text(self.claude_turn(0, 100))
        codex = self.root / "rollout-2026-10-12T12-00-00-44444444-2222-4333-8444-555555555555.jsonl"
        usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 12}
        codex.write_text("".join(json.dumps({"type": kind, "timestamp": "2026-10-12T12:00:00Z", "payload": p}) + "\n"
                                 for kind, p in (("session_meta", {"id": "fixture-thread", "cwd": "/fixture"}),
                                                 ("turn_context", {"model": "gpt-6.1-sol"}),
                                                 ("event_msg", {"type": "user_message", "message": "Go"}),
                                                 ("event_msg", {"type": "token_count",
                                                                "info": {"last_token_usage": usage}}))))
        copilot = self.root / "ws" / "chatSessions" / "55555555-2222-4333-8444-555555555555.jsonl"
        copilot.parent.mkdir(parents=True)
        request = {"requestId": "request-1", "timestamp": STAMP, "modelId": "gpt-6.1-sol",
                   "promptTokens": 50, "completionTokens": 5, "result": {}, "message": {"text": "x"}}
        copilot.write_text(json.dumps({"kind": 2, "k": ["requests"], "v": [request]}) + "\n")
        self.found = [("claude", str(claude), None), ("codex", str(codex), None),
                      ("copilot", str(copilot), "Code")]
        self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 110 + 112 + 55)
        for path in (claude, codex, copilot):
            path.unlink()
        self.found = []
        self.refresh()
        self.assertEqual(self.usage()["tokens"], before["tokens"])
        self.assertTrue(all(s["archived"] for s in D.build_payload()["sessions"]))

    def test_restored_lines_of_a_shortened_log_are_not_counted_twice(self):
        path = self.root / "44444444-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200)); self.refresh()
        path.write_text(self.claude_turn(0, 100)); self.refresh()          # caught mid-rewrite
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200) + self.claude_turn(2, 400))
        self.refresh()
        self.assertEqual(self.usage()["tokens"], 110 + 210 + 410)
        self.assertEqual(self.usage()["asst"], 3)

    def test_restored_lines_are_not_counted_twice_by_a_later_full_reparse(self):
        path = self.root / "48888888-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200)); self.refresh()
        path.write_text(self.claude_turn(0, 100)); self.refresh()          # a synced copy reverted
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200) + self.claude_turn(2, 400))
        self.refresh()
        expected = self.usage()
        self.assertEqual(expected["tokens"], 110 + 210 + 410)
        self.entry(path)["reasoning_effort_version"] = 0                  # a later backfill
        self.refresh()
        self.assertEqual(self.usage(), expected)
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200)); self.refresh()
        self.assertEqual(self.usage(), expected)

    def test_kept_rows_keep_the_shape_later_parses_index(self):
        path = self.root / "49999999-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]

        def turn(n, day, effort=None):
            prompt = {"uuid": "%012d" % (2 * n), "timestamp": "2026-10-%sT12:%02d:00Z" % (day, n),
                      "type": "user", "message": {"content": "Fix this fixture"}}
            reply = {"uuid": "%012d" % (2 * n + 1), "timestamp": "2026-10-%sT12:%02d:05Z" % (day, n),
                     "type": "assistant", "requestId": "req-%d" % n, "attributionSkill": "dataviz",
                     "message": {"id": "msg-%d" % n, "model": "claude-sonnet-4-6", "content": [],
                                 "usage": {"input_tokens": 100, "output_tokens": 10,
                                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}}}
            if effort:
                reply["effort"] = effort
            return json.dumps(prompt) + "\n" + json.dumps(reply) + "\n"
        path.write_text(turn(0, 12) + turn(1, 13)); self.refresh()
        path.write_text(turn(0, 12)); self.refresh()       # day 13's skill, context and effort rows are kept
        with open(path, "a") as f:
            f.write(turn(2, 13, "high"))                   # the parser reaches the same rows again
        self.refresh()
        entry = self.entry(path)
        self.assertEqual(entry["offset"], path.stat().st_size)
        self.assertEqual(self.usage()["tokens"], 3 * 110)
        self.assertEqual(entry["skills"]["2026-10-13\tdataviz"]["asst"], 2)
        self.assertEqual(entry["ctx"]["2026-10-13\t0-50k"]["n"], 2)
        effort = entry["reasoning_effort"]["days"]["2026-10-13\tClaude Sonnet 4.6"]
        self.assertEqual(effort, {"levels": {"high": 1}, "unrecorded": 1})

    def test_kept_usage_note_survives_later_appends(self):
        path = self.root / "55555555-2222-4333-8444-555555555555.jsonl"
        self.found = [("claude", str(path), None)]
        path.write_text(self.claude_turn(0, 100) + self.claude_turn(1, 200)); self.refresh()
        path.write_text(self.claude_turn(0, 100)); self.refresh()
        with open(path, "a") as f:
            f.write(self.claude_turn(2, 400))
        self.refresh()
        self.assertTrue(D.build_payload()["sessions"][0]["retained_usage"])

    def copilot_log(self, path, requests, snapshot=False):
        if snapshot:          # Copilot compacts its mutation log into one snapshot line
            path.write_text(json.dumps({"kind": 0, "v": {"requests": requests}}) + "\n")
        else:
            path.write_text("".join(json.dumps({"kind": 2, "k": ["requests"], "v": [r]}) + "\n"
                                    for r in requests))

    @staticmethod
    def copilot_request(n, prompt):
        return {"requestId": "request-%d" % n, "timestamp": STAMP + n * 60000, "modelId": "gpt-6.1-sol",
                "promptTokens": prompt, "completionTokens": 10, "result": {},
                "message": {"text": "fixture " * 50}}

    def test_copilot_log_compacted_without_a_deleted_request_keeps_it(self):
        path = self.root / "ws" / "chatSessions" / "66666666-2222-4333-8444-555555555555.jsonl"
        path.parent.mkdir(parents=True)
        self.found = [("copilot", str(path), "Code")]
        self.copilot_log(path, [self.copilot_request(0, 100), self.copilot_request(1, 200)]); self.refresh()
        before = self.usage()
        self.assertEqual(before["tokens"], 320)
        self.copilot_log(path, [self.copilot_request(0, 100)], snapshot=True); self.refresh()
        self.assertEqual(self.usage(), before)
        # Caught half-written, then complete again: every request counts once.
        path.write_text(""); self.refresh()
        self.copilot_log(path, [self.copilot_request(0, 100), self.copilot_request(1, 200)], snapshot=True)
        self.refresh()
        self.assertEqual(self.usage()["tokens"], 320)
        self.assertEqual(self.usage()["asst"], before["asst"])

    def test_copilot_json_estimate_replaced_by_final_numbers_counts_once(self):
        # The whole-file .json format is not retained: an in-flight request's
        # estimate legitimately gives way to the recorded numbers.
        path = self.root / "ws" / "chatSessions" / "77777777-2222-4333-8444-555555555555.json"
        path.parent.mkdir(parents=True)
        self.found = [("copilot", str(path), "Code")]
        stub = {"requestId": "r", "timestamp": STAMP, "modelId": "copilot/auto", "message": {"text": "x" * 4000}}
        path.write_text(json.dumps({"requests": [stub]})); self.refresh()
        final = dict(stub, modelId="gpt-6.1-sol", promptTokens=80, completionTokens=15,
                     result={"details": "GPT • 1x"})
        path.write_text(json.dumps({"requests": [final]})); os.utime(path, (1, 1)); self.refresh()
        self.assertEqual(self.usage()["tokens"], 95)
        self.assertEqual(self.usage()["asst"], 1)


class ReconcileUnitTests(unittest.TestCase):
    def test_new_aggregate_is_stamped_and_in_place_update_untouched(self):
        fresh = P._blank_agg("claude", "/fixture/a.jsonl")
        self.assertEqual(P.reconcile(None, fresh, 63)["_parsed_under"], 63)
        same = P._blank_agg("claude", "/fixture/b.jsonl")
        self.assertNotIn("_parsed_under", P.reconcile(same, same, 63))

    def test_shortfall_and_add_counts_ignore_text_and_flags(self):
        before = {"a": {"in": 10, "usage_status": "recorded", "exact": {"in": 4}}, "b": [1, 2, 3], "flag": True}
        now = {"a": {"in": 4, "exact": {"in": 4}}, "b": [1, 0]}
        gap = P._shortfall(before, now)
        self.assertEqual(gap, {"a": {"in": 6}, "b": [0, 2, 3]})
        target = {}
        P._add_counts(target, {"date\tm": {"in": 6}}, P._RETAIN_BLANK["records"])
        self.assertEqual(target["date\tm"]["out"], 0)
        self.assertEqual(target["date\tm"]["in"], 6)


if __name__ == "__main__":
    unittest.main()
