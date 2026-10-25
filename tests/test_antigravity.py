"""Synthetic, stdlib-only coverage for the bounded Antigravity reader."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import antigravity as AG
import parser as P


_MISSING = object()
MODEL = "gemini-7.0-flash"
DISPLAY = "Gemini 7.0 Flash (High)"


def varint(value):
    value = int(value)
    if value < 0:
        raise ValueError("fixture encoder accepts unsigned values only")
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def ufield(number, value):
    return varint(number << 3) + varint(value)


def bfield(number, value):
    value = bytes(value)
    return varint((number << 3) | 2) + varint(len(value)) + value


def sfield(number, value):
    return bfield(number, value.encode("utf-8"))


def timestamp_proto(seconds):
    return ufield(1, seconds)


def usage_proto(*, model_enum=None, input_tokens=_MISSING, output_tokens=_MISSING,
                cache_write=_MISSING, cache_read=_MISSING, reasoning=_MISSING,
                response_output=_MISSING, request_id=None):
    out = bytearray()
    for field, value in ((1, model_enum), (2, input_tokens), (3, output_tokens),
                         (4, cache_write), (5, cache_read), (9, reasoning),
                         (10, response_output)):
        if value is not None and value is not _MISSING:
            out += ufield(field, value)
    if request_id:
        out += sfield(11, request_id)
    return bytes(out)


def model_info(name=None, display=None):
    out = bytearray()
    if name is not None:
        out += sfield(8, name)
    if display is not None:
        out += sfield(20, display)
    return bytes(out)


def call_info(name):
    return sfield(2, name)


def step_metadata(seconds=None, *, source=2, usage=None, model=None, tool=None,
                  trajectory=None, source_index=None, cascade=None):
    out = bytearray()
    if seconds is not None:
        out += bfield(1, timestamp_proto(seconds))
    out += ufield(3, source)
    if usage is not None:
        out += bfield(9, usage)
    if tool is not None:
        out += bfield(4, call_info(tool))
    if model is not None:
        out += bfield(24, model)
    if trajectory is not None:
        info = sfield(1, trajectory)
        if source_index is not None:
            info += ufield(2, source_index)
        if cascade is not None:
            info += sfield(4, cascade)
        out += bfield(20, info)
    return bytes(out)


def step_payload(idx, kind, prompt=None):
    if kind == "checkpoint":
        return bfield(30, bytes(prompt or b""))
    if kind == "subagent":
        spec = sfield(1, "review") + sfield(2, "validator")
        if prompt:
            spec += sfield(3, prompt)
        invoke = bfield(9, spec)
        return bfield(143, invoke)
    raise ValueError(kind)


def checkpoint(*, intent_only=False, included_start=None, included_end=None,
               title=None, failed=False, fallback=False):
    out = bytearray()
    if title is not None:
        out += sfield(10, title)
    if intent_only:
        out += ufield(9, 1)
    if included_start is not None:
        out += ufield(11, included_start)
    if included_end is not None:
        out += ufield(12, included_end)
    if failed:
        out += ufield(16, 1)
    if fallback:
        out += ufield(17, 1)
    return bytes(out)


def generation(idx, step_indices, *, usage, raw_model=MODEL, display=DISPLAY,
               seconds=None, context_estimate=None):
    chat = bytearray()
    if usage is not None:
        chat += bfield(4, usage)
    if raw_model is not None:
        chat += sfield(19, raw_model)
    if display is not None:
        chat += sfield(21, display)
    if seconds is not None or context_estimate is not None:
        start = bytearray()
        if seconds is not None:
            start += bfield(4, timestamp_proto(seconds))
        if context_estimate is not None:
            context = ufield(1, context_estimate) + ufield(4, 2048)
            start += bfield(10, context)
        chat += bfield(9, bytes(start))
    out = bytearray(bfield(1, bytes(chat)))
    if step_indices:
        out += bfield(2, b"".join(varint(item) for item in step_indices))
    return idx, bytes(out)


def trajectory_metadata(*, workspace=None, git=None,
                        branch="fixture-branch", created=1_800_000_000,
                        parent=None, root=None, subagent=None, depth=None):
    workspace = (Path(tempfile.gettempdir()) / "antigravity-fixture"
                 if workspace is None else Path(workspace))
    git = Path(git) if git else workspace / ".git"
    workspace_blob = sfield(1, workspace.as_uri()) + sfield(2, git.as_uri()) + sfield(4, branch)
    out = bfield(1, workspace_blob) + bfield(2, timestamp_proto(created))
    if parent:
        out += sfield(5, parent)
    if root:
        out += sfield(6, root)
    if subagent:
        out += bfield(8, sfield(1, subagent[0]) + sfield(2, subagent[1]))
    if depth is not None:
        out += ufield(17, depth)
    return out


class AntigravityReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="antigravity-fixture-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.seconds = int(datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc).timestamp())

    def make_db(self, name="00000001-0000-4000-8000-000000000001.db", *,
                steps=(), generations=(), trajectory_rows=(), trajectory_blob=None):
        path = self.root / name
        con = sqlite3.connect(path)
        con.executescript("""
            CREATE TABLE steps(idx INTEGER PRIMARY KEY, step_type INTEGER, status INTEGER,
                               metadata BLOB, step_payload BLOB);
            CREATE TABLE gen_metadata(idx INTEGER PRIMARY KEY, data BLOB);
            CREATE TABLE trajectory_meta(trajectory_id TEXT, cascade_id TEXT,
                                         trajectory_type INTEGER, source INTEGER);
            CREATE TABLE trajectory_metadata_blob(id TEXT PRIMARY KEY, data BLOB);
        """)
        con.executemany("INSERT INTO steps(idx,step_type,status,metadata,step_payload) VALUES(?,?,?,?,?)",
                        list(steps))
        con.executemany("INSERT INTO gen_metadata(idx,data) VALUES(?,?)", list(generations))
        con.executemany("INSERT INTO trajectory_meta(trajectory_id,cascade_id,trajectory_type,source) VALUES(?,?,?,?)",
                        [(trajectory_id, cascade_id, 0, 0)
                         for trajectory_id, cascade_id in trajectory_rows])
        if trajectory_blob is not None:
            con.execute("INSERT INTO trajectory_metadata_blob(id,data) VALUES('main',?)",
                        (trajectory_blob,))
        con.commit()
        con.close()
        return path

    def test_usage_privacy_context_checkpoint_subagent_and_filter_before_cap(self):
        one = usage_proto(model_enum=42, input_tokens=20, output_tokens=8,
                          cache_read=5, reasoning=3, response_output=4,
                          request_id="synthetic-response-a")
        two = usage_proto(model_enum=42, input_tokens=10, output_tokens=4,
                          cache_read=0, reasoning=2, response_output=3,
                          request_id="synthetic-response-b")
        intent_usage = usage_proto(input_tokens=11, output_tokens=2, reasoning=1,
                                   request_id="synthetic-checkpoint-usage")
        steps = [
            (0, 14, 3, step_metadata(self.seconds, source=4), None),
            (1, 15, 3, step_metadata(self.seconds + 10, usage=one), None),
            (2, 15, 3, step_metadata(self.seconds + 20, usage=two), None),
            (3, 99, 7, step_metadata(self.seconds + 30, source=2,
                                      model=model_info(MODEL), tool="Read"), None),
            (4, 23, 3, step_metadata(self.seconds + 40, source=5, usage=intent_usage),
             step_payload(4, "checkpoint", checkpoint(intent_only=True, included_end=5))),
            (5, 23, 3, step_metadata(self.seconds + 50, source=5),
             step_payload(5, "checkpoint", checkpoint(included_start=1, included_end=3,
                                                        title="Synthetic fixture title"))),
            (6, 23, 3, step_metadata(self.seconds + 55, source=5),
             step_payload(6, "checkpoint", checkpoint(failed=True, fallback=True))),
            (7, 127, 7, step_metadata(self.seconds + 60, source=5),
             step_payload(7, "subagent", "PRIVATE synthetic delegated prompt")),
        ]
        generations = [
            generation(0, [1], usage=one, seconds=self.seconds + 10,
                       context_estimate=512),
            generation(1, [2], usage=two, seconds=self.seconds + 20),
        ]
        workspace = self.root / "antigravity-fixture"
        path = self.make_db(steps=steps, generations=generations,
                            trajectory_blob=trajectory_metadata(workspace=workspace))
        snapshot = AG.read_conversation(path, include_private=True)
        self.assertEqual(snapshot["totals"]["req"], 3)
        self.assertEqual(snapshot["totals"]["in"], 41)
        self.assertEqual(snapshot["totals"]["out"], 14)
        self.assertEqual(snapshot["totals"]["cr"], 5)
        self.assertEqual(snapshot["totals"]["reason"], 6)
        self.assertEqual(snapshot["totals"]["response_out"], 7)
        self.assertEqual(snapshot["user_messages"], 1)
        self.assertEqual(snapshot["assistant_messages"], 2)
        self.assertEqual(snapshot["tool_calls"], 1)
        self.assertEqual(snapshot["subagent_requests"], 1)
        invoke_events = [event for event in snapshot["events"] if event["kind"] == "subagent"]
        self.assertEqual(invoke_events[0]["status"], "error")
        self.assertTrue(invoke_events[0]["requested"])
        self.assertEqual(len(snapshot["compactions"]), 1)
        self.assertEqual(snapshot["title"], "Synthetic fixture title")
        self.assertEqual(snapshot["project"], "antigravity-fixture")
        self.assertEqual(snapshot["branch"], "fixture-branch")
        self.assertEqual(snapshot["cwd"], str(workspace))
        self.assertEqual(snapshot["usage_availability"]["input_output"], "recorded")
        self.assertEqual(snapshot["model_status"], "partial")
        self.assertEqual(len([e for e in snapshot["events"] if e["kind"] == "model"]), 1)
        self.assertFalse(any(e.get("intent_only") is False and e["kind"] == "compaction"
                             and e.get("summarization_failed") for e in snapshot["events"]))
        self.assertNotIn("cwd", AG.read_conversation(path))
        serialized = json.dumps(snapshot)
        for private in ("PRIVATE synthetic", "synthetic-response-a", "synthetic-response-b"):
            self.assertNotIn(private, serialized)

        filtered = AG.read_conversation(
            path, max_events=1,
            event_filter=lambda event: event.get("kind") == "request"
            and event.get("timestamp", "") >= "2026-10-20T10:00:20+00:00")
        self.assertEqual(len(filtered["events"]), 1)
        self.assertEqual(filtered["events"][0]["kind"], "request")
        self.assertTrue(filtered["events_truncated"])

        parsed = P.update_file(None, "antigravity", str(path), "Antigravity IDE", {})
        self.assertEqual(parsed["conversation_id"], path.stem)
        self.assertEqual(parsed["sessions"][0]["_sid"], path.stem)
        self.assertEqual(parsed["sessions"][0]["reason"], 6)
        self.assertEqual(parsed["sessions"][0]["response_out"], 7)
        self.assertEqual(parsed["totals"]["asst"], 2)
        self.assertEqual(parsed["totals"]["tools"], 1)
        self.assertEqual(parsed["sessions"][0]["ide"], "Antigravity IDE")
        self.assertEqual(parsed["reasoning_effort"]["days"][
            f"{AG._event_date(snapshot['records'][0]['timestamp'])}\tGemini 7.0 Flash"]["levels"],
            {"high": 2})

    def test_generation_splits_two_distinct_reconciled_receipts(self):
        left = usage_proto(model_enum=42, input_tokens=40, output_tokens=7,
                           request_id="split-left")
        right = usage_proto(model_enum=42, input_tokens=10, output_tokens=2,
                            cache_read=20, request_id="split-right")
        aggregate = usage_proto(model_enum=42, input_tokens=50, output_tokens=9,
                                cache_read=20)
        steps = [
            (13, 15, 3, step_metadata(self.seconds + 1, usage=left), None),
            (16, 15, 3, step_metadata(self.seconds + 2, usage=right), None),
        ]
        path = self.make_db("00000002-0000-4000-8000-000000000002.db", steps=steps,
                            generations=[generation(2, [13, 16], usage=aggregate,
                                                    seconds=self.seconds + 3)])
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["req"], 2)
        self.assertEqual(snapshot["totals"]["in"], 50)
        self.assertEqual(snapshot["totals"]["out"], 9)
        self.assertEqual(snapshot["totals"]["cr"], 20)
        self.assertEqual([row["model"] for row in snapshot["records"]], [MODEL, MODEL])
        self.assertEqual(snapshot["usage_availability"]["cache_read"], "partial")
        self.assertEqual(sum(snapshot["activity_buckets"]["assistant"].values()), 2)

    def test_generation_no_id_dedup_and_model_only_generation_falls_back_to_step(self):
        first = usage_proto(model_enum=42, input_tokens=13, output_tokens=4,
                            request_id="first-step")
        second = usage_proto(model_enum=42, input_tokens=9, output_tokens=3,
                             request_id="second-step")
        model_only = usage_proto(model_enum=42)
        no_id = usage_proto(model_enum=42, input_tokens=13, output_tokens=4)
        steps = [
            (1, 15, 3, step_metadata(self.seconds + 1, usage=first,
                                     model=model_info(MODEL)), None),
            (2, 15, 3, step_metadata(self.seconds + 2, usage=second), None),
        ]
        generations = [
            generation(0, [1], usage=no_id, seconds=self.seconds + 1),
            generation(1, [1], usage=no_id, seconds=self.seconds + 1),
            generation(2, [2], usage=model_only, seconds=self.seconds + 2),
        ]
        path = self.make_db("00000003-0000-4000-8000-000000000003.db", steps=steps,
                            generations=generations)
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["req"], 2)
        self.assertEqual(snapshot["totals"]["in"], 22)
        self.assertEqual(snapshot["totals"]["out"], 7)
        self.assertEqual({row["key"] for row in snapshot["records"]}, {"gen:1", "step:2"})

    def test_repeated_response_id_prefers_fullest_newer_receipt(self):
        partial = usage_proto(model_enum=42, input_tokens=15, output_tokens=5,
                              request_id="retry-same-id")
        full = usage_proto(model_enum=42, input_tokens=15, output_tokens=5,
                           cache_read=8, reasoning=2, response_output=4,
                           request_id="retry-same-id")
        steps = [
            (1, 15, 3, step_metadata(self.seconds + 1, usage=partial,
                                     model=model_info(MODEL)), None),
            (2, 15, 3, step_metadata(self.seconds + 2, usage=full,
                                     model=model_info(MODEL)), None),
        ]
        path = self.make_db("00000004-0000-4000-8000-000000000004.db", steps=steps)
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["req"], 1)
        self.assertEqual(snapshot["totals"]["cr"], 8)
        self.assertEqual(snapshot["totals"]["reason"], 2)
        self.assertEqual(snapshot["assistant_messages"], 1)
        self.assertEqual(len(snapshot["records"]), 1)
        self.assertTrue(snapshot["records"][0]["timestamp"].endswith(":02+00:00"))

    def test_reasoning_only_undated_usage_is_retained_without_becoming_output(self):
        reasoning = usage_proto(reasoning=7, request_id="reasoning-only")
        steps = [(1, 23, 3, step_metadata(None, source=5, usage=reasoning),
                  step_payload(1, "checkpoint", checkpoint(intent_only=True)))]
        path = self.make_db("00000005-0000-4000-8000-000000000005.db", steps=steps)
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["reason"], 7)
        self.assertEqual(snapshot["totals"]["out"], 0)
        self.assertEqual(snapshot["totals"]["req"], 1)
        self.assertEqual(snapshot["undated_usage"]["reason"], 7)
        self.assertEqual(snapshot["undated_usage"]["out"], 0)
        self.assertEqual(snapshot["usage_availability"]["input_output"], "unavailable")
        self.assertEqual(snapshot["usage_availability"]["reasoning"], "recorded")
        self.assertEqual(snapshot["compactions"], [])
        parsed = P.update_file(None, "antigravity", str(path), "Antigravity IDE", {})
        self.assertEqual(parsed["totals"]["reason"], 7)
        self.assertEqual(parsed["totals"]["out"], 0)
        self.assertEqual(parsed["antigravity"]["undated_usage"]["reason"], 7)
        self.assertEqual(parsed["records"], {})

    def test_foreign_trajectory_replay_suppresses_step_and_generation(self):
        inherited = usage_proto(model_enum=42, input_tokens=80, output_tokens=9,
                                request_id="inherited-replay")
        own = usage_proto(model_enum=42, input_tokens=12, output_tokens=3,
                          request_id="own-response")
        steps = [
            (1, 15, 3, step_metadata(self.seconds + 1, usage=inherited,
                                     trajectory="parent-trajectory", source_index=4,
                                     cascade="parent-cascade"), None),
            (2, 15, 3, step_metadata(self.seconds + 2, usage=own,
                                     trajectory="local-trajectory", source_index=1,
                                     cascade="local-cascade"), None),
        ]
        generations = [
            generation(0, [1], usage=inherited, seconds=self.seconds + 1),
            generation(1, [2], usage=own, seconds=self.seconds + 2),
        ]
        path = self.make_db("00000006-0000-4000-8000-000000000006.db", steps=steps,
                            generations=generations,
                            trajectory_rows=[("local-trajectory", "local-cascade")])
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["req"], 1)
        self.assertEqual(snapshot["totals"]["in"], 12)
        self.assertEqual(snapshot["totals"]["out"], 3)
        self.assertEqual(snapshot["assistant_messages"], 1)
        self.assertEqual(snapshot["activity_buckets"]["assistant"], {f"{AG._event_date(snapshot['records'][0]['timestamp'])}\t{MODEL}": 1})

    def test_oversized_optional_generation_keeps_step_usage(self):
        response = usage_proto(model_enum=42, input_tokens=17, output_tokens=6,
                               request_id="kept-step")
        steps = [(1, 15, 3, step_metadata(self.seconds + 1, usage=response,
                                         model=model_info(MODEL)), None)]
        path = self.make_db("00000007-0000-4000-8000-000000000007.db", steps=steps,
                            generations=[(0, b"\x00" * (AG.MAX_PROTO_BYTES + 1))])
        snapshot = AG.read_conversation(path)
        self.assertEqual(snapshot["totals"]["req"], 1)
        self.assertEqual(snapshot["totals"]["in"], 17)
        self.assertEqual(snapshot["detail_status"], "partial")

    def test_full_bounded_compactions_survive_the_timeline_event_cap(self):
        steps = [
            (idx, 23, 3, step_metadata(self.seconds + idx, source=5),
             step_payload(idx, "checkpoint", checkpoint(included_start=1, included_end=2)))
            for idx in range(AG.MAX_EVENTS + 1)
        ]
        path = self.make_db("00000011-0000-4000-8000-000000000011.db", steps=steps)
        snapshot = AG.read_conversation(path, max_events=1)
        self.assertEqual(len(snapshot["compactions"]), AG.MAX_EVENTS + 1)
        self.assertFalse(snapshot["compactions_truncated"])
        self.assertEqual(len(snapshot["events"]), 1)
        self.assertTrue(snapshot["events_truncated"])
        parsed = P.update_file(None, "antigravity", str(path), "Antigravity IDE", {})
        self.assertEqual(len(parsed["compactions"]), AG.MAX_EVENTS + 1)
        self.assertEqual(len(parsed["sessions"][0]["compactions"]), AG.MAX_EVENTS + 1)

    def test_activity_buckets_are_complete_beyond_event_cap_and_daily_tools_track(self):
        response = usage_proto(model_enum=42, input_tokens=3, output_tokens=2,
                               request_id="large-fixture-response")
        steps = [
            (0, 14, 3, step_metadata(self.seconds, source=4), None),
            (1, 15, 3, step_metadata(self.seconds + 1, usage=response,
                                     model=model_info(MODEL)), None),
        ]
        steps.extend((idx, 99, 3, step_metadata(self.seconds + idx, source=2,
                                                model=model_info(MODEL), tool="Read"), None)
                     for idx in range(2, AG.MAX_EVENTS + 3))
        path = self.make_db("00000008-0000-4000-8000-000000000008.db", steps=steps)
        snapshot = AG.read_conversation(path)
        self.assertEqual(len(snapshot["events"]), AG.MAX_EVENTS)
        self.assertTrue(snapshot["events_truncated"])
        self.assertEqual(snapshot["tool_calls"], AG.MAX_EVENTS + 1)
        self.assertEqual(sum(snapshot["activity_buckets"]["tools"].values()), AG.MAX_EVENTS + 1)
        parsed = P.update_file(None, "antigravity", str(path), "Antigravity IDE", {})
        self.assertEqual(parsed["totals"]["req"], 1)
        self.assertEqual(parsed["totals"]["asst"], 1)
        self.assertEqual(parsed["totals"]["user"], 1)
        self.assertEqual(parsed["totals"]["tools"], AG.MAX_EVENTS + 1)
        self.assertEqual(sum(row["req"] for row in parsed["records"].values()), parsed["totals"]["req"])
        self.assertEqual(sum(row["asst"] for row in parsed["records"].values()), parsed["totals"]["asst"])
        self.assertEqual(sum(row["user"] for row in parsed["records"].values()), parsed["totals"]["user"])
        self.assertEqual(sum(row["tools"] for row in parsed["records"].values()), parsed["totals"]["tools"])
        self.assertEqual(sum(row["msgs"] for row in parsed["hourly"].values()), 2)

    def test_discovery_exact_roots_symlinks_and_same_uuid_copies(self):
        first_root, second_root, outside = self.root / "ide", self.root / "cli", self.root / "outside"
        first_dir = first_root / "conversations"
        second_dir = second_root / "conversations"
        first_dir.mkdir(parents=True)
        second_dir.mkdir(parents=True)
        outside.mkdir()
        conversation_id = "00000009-0000-4000-8000-000000000009"
        first = first_dir / f"{conversation_id}.db"
        second = second_dir / f"{conversation_id}.db"
        first.write_bytes(b"a")
        second.write_bytes(b"a much longer but still synthetic database copy")
        external = outside / "external.db"
        external.write_bytes(b"outside")
        pb = first_dir / "legacy.pb"
        pb.write_bytes(b"opaque unsupported data")
        try:
            (first_dir / "symlink.db").symlink_to(external)
            (first_dir / "alias.db").symlink_to(first)
        except (OSError, NotImplementedError):
            pass
        root_link = self.root / "ide-link"
        try:
            root_link.symlink_to(first_root, target_is_directory=True)
        except (OSError, NotImplementedError):
            root_link = None
        roots = [(first_root, "Antigravity IDE"), (second_root, "Antigravity CLI")]
        if root_link:
            roots.append((root_link, "Antigravity App"))
        found = AG.discover_conversations(roots)
        self.assertEqual(len(found), 2)
        self.assertEqual({Path(path).name for path, _ in found}, {f"{conversation_id}.db"})
        self.assertEqual({label for _, label in found}, {"Antigravity IDE", "Antigravity CLI"})
        self.assertEqual(AG.legacy_pb_inventory(roots), 1)

    def test_read_only_connection_and_wal_snapshot(self):
        path = self.make_db(steps=[(1, 15, 3, step_metadata(self.seconds + 1,
            usage=usage_proto(input_tokens=2, output_tokens=1, request_id="wal-response")), None)])
        con = AG._open_snapshot(path)
        try:
            self.assertEqual(con.execute("PRAGMA query_only").fetchone()[0], 1)
            with self.assertRaises(sqlite3.OperationalError):
                con.execute("INSERT INTO steps(idx,step_type,status,metadata) VALUES(99,15,3,?)", (b"",))
        finally:
            con.rollback()
            con.close()

        writer = sqlite3.connect(path)
        try:
            writer.execute("PRAGMA journal_mode=WAL")
            writer.execute("INSERT INTO steps(idx,step_type,status,metadata) VALUES(2,15,3,?)",
                           (step_metadata(self.seconds + 2,
                            usage=usage_proto(input_tokens=5, output_tokens=3,
                                              request_id="wal-new-response")),))
            writer.commit()
            self.assertTrue(Path(str(path) + "-wal").exists())
            snapshot = AG.read_conversation(path)
            self.assertEqual(snapshot["totals"]["in"], 7)
            self.assertEqual(snapshot["totals"]["out"], 4)
        finally:
            writer.close()

    def test_malformed_required_metadata_preserves_old_aggregate_and_retries(self):
        valid = step_metadata(self.seconds + 1,
                              usage=usage_proto(input_tokens=12, output_tokens=4,
                                                request_id="transactional-response"))
        path = self.make_db(steps=[(1, 15, 3, valid, None)])
        old = P.update_file(None, "antigravity", str(path), "Antigravity IDE", {})
        old_signature = list(old["_sig"])
        old_totals = dict(old["totals"])
        con = sqlite3.connect(path)
        con.execute("UPDATE steps SET metadata=? WHERE idx=1", (b"\x80",))
        con.commit()
        con.close()
        with self.assertRaises(AG.AntigravityReadError):
            P.update_file(old, "antigravity", str(path), "Antigravity IDE", {})
        self.assertEqual(old["_sig"], old_signature)
        self.assertEqual(old["totals"], old_totals)
        con = sqlite3.connect(path)
        con.execute("UPDATE steps SET metadata=? WHERE idx=1", (valid,))
        con.commit()
        con.close()
        retried = P.update_file(old, "antigravity", str(path), "Antigravity IDE", {})
        self.assertNotEqual(retried["_sig"], old_signature)
        self.assertEqual(retried["totals"]["in"], 12)

    def test_required_scan_bound_and_unsupported_schema_fail_closed(self):
        path = self.make_db(steps=[
            (1, 15, 3, step_metadata(self.seconds + 1), None),
            (2, 15, 3, step_metadata(self.seconds + 2), None),
        ])
        with patch.object(AG, "MAX_ROWS", 1):
            with self.assertRaises(AG.AntigravityReadError):
                AG.read_conversation(path)
        bad = self.root / "00000010-0000-4000-8000-000000000010.db"
        con = sqlite3.connect(bad)
        con.execute("CREATE TABLE unrelated(value TEXT)")
        con.commit()
        con.close()
        with self.assertRaises(AG.AntigravityUnsupportedError):
            AG.read_conversation(bad)


if __name__ == "__main__":
    unittest.main()
