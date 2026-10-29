"""Codex replaces an idle rollout with <rollout>.jsonl.zst.

Covers reading it, keeping one ledger entry per conversation across the plain and
compressed spellings, and the honest paths: no decoder on the machine, a damaged file.
Synthetic data only. Fixtures are built here, and nothing reads the real ~/.codex.

Decoder-dependent tests run against every decoder this machine really has (Python
3.14's compression.zstd and/or the zstd command) and skip, saying why, when it has
neither. The no-decoder tests need no decoder and run everywhere.
"""
import base64
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import diagnostics as DG
import parser as P
import session_names as SN
import session_timeline as ST


def zstd_store(data, block=64):
    """A valid zstd frame made only of raw blocks. Any decoder reads it like a real
    frame, and building one needs no compressor (a CI runner may have none)."""
    out = bytearray(b"\x28\xb5\x2f\xfd")         # magic number
    out += bytes((0x00, 0x38))                   # no checksum/dictionary; 128 KiB window
    pieces = [data[i:i + block] for i in range(0, len(data), block)] or [b""]
    for n, piece in enumerate(pieces):
        last = 1 if n == len(pieces) - 1 else 0  # block type 0 = raw
        out += ((len(piece) << 3) | last).to_bytes(3, "little") + piece
    return bytes(out)


def sample_text():
    return "".join(json.dumps({"timestamp": "2026-10-02T10:00:%02dZ" % n, "type": "event_msg",
                              "payload": {"type": "agent_message", "message": "fixture line %d" % n}})
                   + "\n" for n in range(40))


# sample_text() compressed by the zstd command at level 19: a genuinely compressed
# frame (entropy-coded blocks), unlike the raw-block frames the rest of this file builds.
REAL_FRAME = base64.b64decode(
    "KLUv/WDOEn0HAFLLIhlwzwPoVhtPIz4U7q/qM1KEkJTI+fLprScCwHWtlDFC+J5zrf27d/Mzfilj"
    "hPA951r7d+9mZ+xSxgjhe8619u/e3dxSxgjhe8619u/eDVzXMqONsjAKSZVSkGXDsmBpMqOIJtNi"
    "wx7TWar5JBFj1IZOlD5VXRZswQM34SxwC8/Zlw37MLNGlFHqWgFUqBFg03KTrTWwgyTDGhLAMAga"
    "huD/bwgwbOH5AZWKBbVKVBYorgRmfCtWFq6pUFC5pObM4CksUFyhUFXFKgmYHzwfqROSIERIg0IB"
    "JQERHpCk6oKw3JO9PhLmxza+AmwV")


def available_backends():
    """(label, backend) for every decoder this machine really has."""
    found = []
    try:
        from compression import zstd
        found.append(("module", ("module", zstd)))
    except ImportError:
        pass
    exe = shutil.which("zstd")
    if exe:
        found.append(("command", ("command", exe)))
    return found


BACKENDS = available_backends()
NEEDS_DECODER = unittest.skipUnless(
    BACKENDS, "no zstd decoder on this machine (needs Python 3.14+ or the zstd command)")
FAKE_BACKEND = ("command", "never-run")   # present, for tests that substitute the line source

THREAD = "019a1234-0000-7000-8000-00000000abcd"
PARENT = "019a1234-0000-7000-8000-00000000fff0"
STAMP = "rollout-2026-10-10T12-00-00-" + THREAD
USAGE = {"input_tokens": 100, "cached_input_tokens": 30, "cache_write_input_tokens": 10,
         "output_tokens": 12, "reasoning_output_tokens": 3}
TURN_TOKENS = 60 + 12 + 30 + 10          # fresh input + output + cache read + cache write


def event(kind, payload, stamp="2026-10-10T12:00:00Z"):
    return json.dumps({"type": kind, "timestamp": stamp, "payload": payload}) + "\n"


def head(thread=THREAD, originator="codex_cli", **meta):
    return (event("session_meta", {"id": thread, "cwd": "/fixture/project", "originator": originator,
                                   "cli_version": "0.200.0", **meta})
            + event("turn_context", {"model": "gpt-6.1-sol", "effort": "high", "turn_id": "turn-0"}))


def turn(n):
    """One model turn: adds TURN_TOKENS to the totals."""
    stamp = "2026-10-10T12:%02d:00Z" % n
    return (event("event_msg", {"type": "user_message", "message": "Explain fixture %d" % n}, stamp)
            + event("response_item", {"type": "function_call", "name": "exec_command",
                                      "arguments": '{"cmd":"ls"}'}, stamp)
            + event("token_usage_record", {"response_id": "response-%d" % n, "turn_id": "turn-0",
                                           "usage": USAGE}, stamp)
            + event("event_msg", {"type": "agent_message", "message": "Done"}, stamp))


def rollout(turns=1, **meta):
    return (head(**meta) + "".join(turn(n) for n in range(turns))
            + event("compacted", {"compaction_response_id": "compact-one"}, "2026-10-10T12:30:00Z"))


def comparable(agg):
    """An entry without what legitimately differs between a log and its compressed twin."""
    tree = copy.deepcopy(agg)
    for key in ("path", "size", "mtime", "offset"):
        tree.pop(key)
    tree["sessions"][0].pop("bytes")
    return tree


def lines_of(text):
    """A stand-in for _zstd_lines, for tests that must not depend on a decoder."""
    return lambda path: (line for line in text.splitlines())


def codex_tokens(payload):
    return sum(r.get(k, 0) for r in payload["records"] if r["source"] == "codex"
               for k in ("in", "out", "cr", "cc"))


class LedgerCase(unittest.TestCase):
    """An isolated dashboard ledger over a throwaway Codex sessions directory."""

    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix="codex-zst-")
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.day = self.root / "sessions" / "2026" / "10" / "10"
        self.day.mkdir(parents=True)
        self.index = self.root / "session_index.jsonl"
        patches = [
            patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}),
            patch.dict(D._meta, D._meta.copy()), patch.dict(D._dirty, {"v": False}),
            patch.dict(D._grew, {"v": False, "saved": 0}), patch.dict(D._gen, D._gen.copy()),
            patch.dict(D._cache_error, {"v": None}),
            patch.object(D, "CACHE_PATH", str(self.root / "cache.json")),
            patch.object(DG, "tracker", DG.DiagnosticsTracker()),
            patch.object(P, "_copilot_project_map", return_value={}),
            patch.object(P, "_vscode_thread_owners", return_value={}),
            patch.object(P, "CODEX_SESSION_INDEX", str(self.index)),
            patch.dict(P._CODEX_NAMES, {"at": 0.0, "sig": None, "map": {}}),
            patch.dict(P._ZSTD_BAD, {}, clear=True),
            patch.object(D, "_peer_items", return_value=[]),
            patch.object(D, "_local_id", return_value="fixture-device"),
            patch.object(D, "_devices_meta", return_value=[]),
            patch.object(D, "_context_files", return_value=[]),
            patch.object(D, "_installed", return_value=[]),
            patch.object(D, "_mcp_servers", return_value={})]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def plain(self, turns, name=STAMP):
        path = self.day / (name + ".jsonl")
        path.write_text(rollout(turns), encoding="utf-8")
        return path

    def compressed(self, turns, name=STAMP, **meta):
        path = self.day / (name + ".jsonl.zst")
        path.write_bytes(zstd_store(rollout(turns, **meta).encode("utf-8")))
        return path

    def scan(self, *paths):
        """One dashboard refresh over exactly these files; returns what it wrote to stderr."""
        found = [("codex", str(p), None) for p in paths]
        with patch.object(P, "discover", return_value=found), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            D.refresh()
        return err.getvalue()

    def entries(self):
        return D._state["files"]

    def codex_sessions(self):
        return [s for s in D.build_payload()["sessions"] if s["source"] == "codex"]

    def each_backend(self, body):
        """Run `body` once per decoder this machine has, from an empty ledger each time."""
        for label, backend in BACKENDS:
            with self.subTest(backend=label), patch.object(P, "_zstd_backend", return_value=backend):
                D._state["files"].clear()
                D._meta.pop("codex_compressed_unread", None)
                P._ZSTD_BAD.clear()
                body()


@NEEDS_DECODER
class ReadingCompressedRollouts(LedgerCase):
    def test_a_real_compressed_frame_decodes_with_every_decoder(self):
        path = self.root / "real.jsonl.zst"
        path.write_bytes(REAL_FRAME)

        def body():
            self.assertEqual("".join(line + "\n" for line in P._zstd_lines(str(path))), sample_text())
            with P.zstd_stream(str(path)) as stream:
                self.assertEqual(stream.readline(), sample_text().split("\n")[0].encode() + b"\n")
        self.each_backend(body)

    def test_lines_split_on_newlines_only_whatever_the_chunking(self):
        records = ["plain", "one \u2028 two", "emoji \U0001F600 here", "x" * 50, "", "last"]
        data = ("\n".join(records[:3]) + "\r\n" + "\n".join(records[3:])).encode("utf-8")
        path = self.root / "lines.jsonl.zst"
        path.write_bytes(zstd_store(data, block=5))

        def body():
            for chunk in (2, 3, 7, 16, 1 << 20):
                with patch.object(P, "_ZSTD_CHUNK", chunk):
                    self.assertEqual(list(P._zstd_lines(str(path))), records, chunk)
        self.each_backend(body)

    def test_concatenated_frames_read_as_one_stream_and_are_never_read_incrementally(self):
        first, second = rollout(1), turn(1)
        path = self.root / "frames.jsonl.zst"
        path.write_bytes(zstd_store(first.encode("utf-8")) + zstd_store(second.encode("utf-8")))

        def body():
            self.assertEqual("".join(line + "\n" for line in P._zstd_lines(str(path))), first + second)
            # a compressed file that grew is read again from its start: 1 + 1 turns, once
            agg = P.update_file(None, "codex", str(path), None, {})
            self.assertEqual(agg["totals"]["req"], 2)
        self.each_backend(body)

    def test_a_record_too_large_to_hold_is_refused_not_buffered(self):
        path = self.root / "huge.jsonl.zst"
        path.write_bytes(zstd_store(b"x" * 500 + b"\n"))

        def body():
            with patch.object(P, "_ZSTD_CHUNK", 64), patch.object(P, "_ZSTD_MAX_LINE", 100):
                with self.assertRaises(P.ZstdReadError):
                    list(P._zstd_lines(str(path)))
        self.each_backend(body)

    def test_compressed_rollout_parses_equal_to_its_plain_twin(self):
        plain = self.plain(3)
        packed = self.compressed(3)

        def body():
            expected = P.update_file(None, "codex", str(plain), None, {})
            got = P.update_file(None, "codex", str(packed), None, {})
            self.assertEqual(got["path"], str(packed))
            self.assertEqual(got["size"], packed.stat().st_size)         # bytes on disk, as Storage shows
            self.assertEqual(got["offset"], got["size"])
            self.assertEqual(comparable(got), comparable(expected))
            self.assertEqual(got["totals"]["req"], 3)
            self.assertEqual(len(got["compactions"]), 1)
            self.assertEqual(got["sessions"][0]["ide"], "CLI")
        self.each_backend(body)

    def test_an_unchanged_compressed_log_is_not_decoded_again(self):
        path = self.compressed(2)

        def body():
            first = P.update_file(None, "codex", str(path), None, {})
            with patch.object(P, "_zstd_lines", side_effect=AssertionError("decoded again")):
                self.assertIs(P.update_file(first, "codex", str(path), None, {}), first)
            with patch.object(P, "_zstd_backend", return_value=None):    # not even a decoder is needed
                self.assertIs(P.update_file(first, "codex", str(path), None, {}), first)
        self.each_backend(body)

    def test_a_replaced_compressed_log_is_read_in_full_never_appended(self):
        path = self.compressed(2)

        def body():
            first = P.update_file(None, "codex", str(path), None, {})
            before = copy.deepcopy(first)
            path.write_bytes(zstd_store(rollout(5).encode("utf-8")))     # recompressed after more work
            second = P.update_file(first, "codex", str(path), None, {})
            self.assertIsNot(second, first)
            self.assertEqual(first, before)                              # the old entry was not touched
            self.assertEqual(second["totals"]["in"], 5 * 60)             # 5 turns once, not 2 + 5
            self.assertEqual(second["offset"], path.stat().st_size)
            path.write_bytes(zstd_store(rollout(2).encode("utf-8")))
        self.each_backend(body)

    def test_stale_metadata_reparses_compressed_and_plain_alike(self):
        targets = (("plain", self.plain(2)), ("compressed", self.compressed(2)))
        markers = ("parent_metadata_version", "reasoning_effort_version",
                   "account_metadata_version", "compactions")

        def body():
            for name, target in targets:
                for marker in markers:
                    with self.subTest(file=name, marker=marker):
                        entry = P.update_file(None, "codex", str(target), None, {})
                        del entry[marker]
                        fresh = P.update_file(entry, "codex", str(target), None, {})
                        self.assertIn(marker, fresh)
                        self.assertEqual(fresh["totals"]["req"], 2)      # re-read, not double counted
                with self.subTest(file=name, marker="compaction_timing"):
                    entry = P.update_file(None, "codex", str(target), None, {})
                    entry["state"]["compaction_timing"]["version"] = 0
                    fresh = P.update_file(entry, "codex", str(target), None, {})
                    self.assertEqual(fresh["state"]["compaction_timing"]["version"],
                                     P._CODEX_COMPACTION_TIMING_VERSION)
        self.each_backend(body)

    def test_titles_ids_ide_and_subagents_work_for_compressed_files(self):
        self.index.write_text(json.dumps({"id": THREAD, "thread_name": "Old name"}) + "\n"
                              + json.dumps({"id": THREAD, "thread_name": "Compressed thread"}) + "\n")
        packed = self.compressed(1, originator="codex_vscode")
        child_name = "rollout-2026-10-10T12-05-00-019a1234-0000-7000-8000-0000000000c1"
        child = self.day / (child_name + ".jsonl.zst")
        child.write_bytes(zstd_store((
            event("session_meta", {"id": "019a1234-0000-7000-8000-0000000000c1",
                                   "thread_source": "subagent", "parent_thread_id": PARENT,
                                   "agent_path": "/root/science_audit", "originator": "codex_cli",
                                   "cwd": "/fixture/project"})
            + event("turn_context", {"model": "gpt-6.1-sol"})
            + event("token_usage_record", {"usage": USAGE})).encode("utf-8")))

        def body():
            agg = P.update_file(None, "codex", str(packed), None, {})
            session = agg["sessions"][0]
            self.assertEqual(agg["_session_id"], THREAD)
            self.assertEqual(session["id"], "019a1234")                  # the plain rollout's short id
            self.assertEqual(session["title"], "Compressed thread")      # Codex's own name, last line wins
            self.assertEqual(session["ide"], "VS Code")
            with patch.object(P, "_vscode_thread_owners", return_value={THREAD: "VS Code - Insiders"}):
                self.assertEqual(P._ide_of("codex", agg), "VS Code - Insiders")
            self.assertEqual(SN.identity(agg, session), THREAD)
            kid = P.update_file(None, "codex", str(child), None, {})
            self.assertTrue(kid["subagent"])
            self.assertEqual(kid["sessions"][0]["parent_id"], PARENT)
            self.assertEqual(kid["sessions"][0]["title"], "Science audit")
            self.assertEqual(kid["sessions"][0]["id"], "019a1234")
        self.each_backend(body)

    def test_a_compressed_twin_keeps_the_session_id_of_the_plain_log(self):
        for name in ("ab", "fixture", "rollout-no-thread-id"):
            with self.subTest(name=name):
                plain = self.day / (name + ".jsonl")
                plain.write_text(rollout(1), encoding="utf-8")
                packed = self.day / (name + ".jsonl.zst")
                packed.write_bytes(zstd_store(rollout(1).encode("utf-8")))

                def body():
                    want = P.update_file(None, "codex", str(plain), None, {})["sessions"][0]["id"]
                    got = P.update_file(None, "codex", str(packed), None, {})["sessions"][0]["id"]
                    self.assertEqual(got, want)
                self.each_backend(body)

    def test_plain_then_compressed_counts_the_conversation_once(self):
        def body():
            plain = self.plain(2)
            self.scan(plain)
            self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)
            # a turn was appended, then Codex compressed the rollout: the plain file is gone
            plain.unlink()
            packed = self.compressed(3)
            self.scan(packed)
            self.assertTrue(self.entries()[str(plain)]["archived"])      # its entry is kept, as ever
            self.assertNotIn("archived", self.entries()[str(packed)])
            payload = D.build_payload()
            self.assertEqual(codex_tokens(payload), 3 * TURN_TOKENS)     # once, and with the new turn
            sessions = self.codex_sessions()
            self.assertEqual(len(sessions), 1)
            self.assertFalse(sessions[0]["archived"])                    # the live compressed copy
            self.assertEqual(sessions[0]["rename_id"], THREAD)
        self.each_backend(body)

    def test_the_live_compressed_copy_wins_a_tie_against_the_archived_plain_entry(self):
        def body():
            plain = self.plain(2)
            self.scan(plain)
            plain.unlink()
            self.scan(self.compressed(2))                                # nothing appended before compression
            self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)
            self.assertEqual([s["archived"] for s in self.codex_sessions()], [False])
        self.each_backend(body)

    def test_compressed_then_plain_counts_once_and_the_plain_copy_is_live(self):
        def body():
            packed = self.compressed(2)
            self.scan(packed)
            packed.unlink()
            plain = self.plain(3)           # Codex decompressed it to resume the thread, and wrote a turn
            self.scan(plain)
            self.assertTrue(self.entries()[str(packed)]["archived"])
            self.assertNotIn("archived", self.entries()[str(plain)])
            self.assertEqual(codex_tokens(D.build_payload()), 3 * TURN_TOKENS)
            self.assertEqual([s["archived"] for s in self.codex_sessions()], [False])
        self.each_backend(body)

    def test_a_returning_log_is_live_again_and_keeps_counting_what_is_appended(self):
        """The same path coming back, bigger: its entry grows and is no longer archived."""
        def body():
            plain = self.plain(2)
            self.scan(plain)
            away = plain.with_name("moved-away")
            plain.rename(away)
            self.scan()
            self.assertTrue(self.entries()[str(plain)]["archived"])
            away.rename(plain)
            with plain.open("a", encoding="utf-8") as handle:
                handle.write(turn(2))
            self.scan(plain)
            self.assertNotIn("archived", self.entries()[str(plain)])
            self.assertEqual(codex_tokens(D.build_payload()), 3 * TURN_TOKENS)
            self.assertEqual([s["archived"] for s in self.codex_sessions()], [False])
        self.each_backend(body)

    def test_a_damaged_log_is_reported_never_silently_skipped(self):
        good = zstd_store(rollout(2).encode("utf-8"))
        damaged = {"truncated": good[:len(good) // 2], "wrong magic": b"\x00" + good[1:],
                   "not zstd": b"this is plain text, not zstd" * 4}

        def body():
            for label, content in damaged.items():
                with self.subTest(damage=label):
                    D._state["files"].clear()
                    P._ZSTD_BAD.clear()
                    path = self.day / (STAMP + ".jsonl.zst")
                    path.write_bytes(content)
                    with patch.object(P, "_zstd_lines", wraps=P._zstd_lines) as spy:
                        err = self.scan(path)
                        self.assertEqual(spy.call_count, 1)
                        self.assertIn("[parse:codex]", err)
                        self.assertIn("ZstdReadError", err)
                        self.assertNotIn("plain text", err)
                        self.assertEqual(D._meta["parse_errors"], 1)
                        self.assertNotIn(str(path), self.entries())      # nothing was invented for it
                        report = D.build_diagnostics()
                        self.assertEqual([row["errors"] for row in report["sources"]], [1])
                        self.assertNotIn(str(self.root), json.dumps(report))
                        # an immutable damaged file fails the same way again: it is not decoded again
                        self.scan(path)
                        self.assertEqual(spy.call_count, 1)
                        self.assertEqual(D._meta["parse_errors"], 1)
                    path.write_bytes(good)                               # a fixed copy replaces it
                    self.scan(path)
                    self.assertEqual(D._meta["parse_errors"], 0)
                    self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)
        self.each_backend(body)

    def test_a_damaged_replacement_leaves_the_previous_entry_untouched(self):
        path = self.compressed(2)

        def body():
            first = P.update_file(None, "codex", str(path), None, {})
            before = copy.deepcopy(first)
            path.write_bytes(path.read_bytes()[:50])
            with self.assertRaises(P.ZstdReadError):
                P.update_file(first, "codex", str(path), None, {})
            self.assertEqual(first, before)
            path.write_bytes(zstd_store(rollout(2).encode("utf-8")))
        self.each_backend(body)

    def test_a_zero_byte_compressed_file_is_not_an_error_and_never_zeroes_an_entry(self):
        def body():
            path = self.day / (STAMP + ".jsonl.zst")
            path.write_bytes(b"")
            self.assertEqual(self.scan(path), "")
            self.assertEqual(D._meta["parse_errors"], 0)
            self.assertNotIn(str(path), self.entries())
            self.scan(self.compressed(1))                                # it fills in
            self.assertEqual(codex_tokens(D.build_payload()), TURN_TOKENS)
            path.write_bytes(b"")
            self.scan(path)
            self.assertEqual(codex_tokens(D.build_payload()), TURN_TOKENS)
        self.each_backend(body)

    def test_session_timeline_reads_a_compressed_rollout_like_its_plain_twin(self):
        plain = self.plain(2)
        packed = self.compressed(2)
        expected = ST.timeline("codex", plain, THREAD)

        def body():
            got = ST.timeline("codex", packed, THREAD)
            self.assertTrue(got["available"])
            self.assertEqual(got["events"], expected["events"])
            self.assertEqual(got["detail"], "source log")
            self.assertEqual(sum(e["kind"] == "request" for e in got["events"]), 2)
            serialized = json.dumps(got)
            for private in ("Explain fixture", "/fixture/project", str(self.root)):
                self.assertNotIn(private, serialized)
            self.assertFalse(ST.timeline("codex", packed, "another-thread")["available"])
        self.each_backend(body)

    def test_the_timeline_bound_applies_to_decoded_bytes(self):
        packed = self.compressed(30)
        decoded, on_disk = len(rollout(30).encode("utf-8")), packed.stat().st_size
        self.assertLess(decoded, on_disk)        # raw blocks add framing: the file is bigger than its text

        def body():
            with patch.object(ST, "MAX_BYTES", 2000):
                got = ST.timeline("codex", packed, THREAD)
            self.assertTrue(got["available"])
            self.assertTrue(got["truncated"])
            self.assertEqual(got["detail"], "partial")
            self.assertLess(sum(e["kind"] == "request" for e in got["events"]), 30)
            # the file on disk is over this bound, the text it holds is not: nothing is cut off
            with patch.object(ST, "MAX_BYTES", (decoded + on_disk) // 2):
                whole = ST.timeline("codex", packed, THREAD)
            self.assertFalse(whole["truncated"])
            self.assertEqual(whole["detail"], "source log")
            self.assertEqual(sum(e["kind"] == "request" for e in whole["events"]), 30)
        self.each_backend(body)

    def test_a_damaged_compressed_rollout_has_no_timeline_instead_of_a_partial_one(self):
        packed = self.compressed(3)
        packed.write_bytes(packed.read_bytes()[:-9])

        def body():
            got = ST.timeline("codex", packed, THREAD)
            self.assertFalse(got["available"])
            self.assertEqual(got["events"], [])
            self.assertIn("damaged", got["detail"])
        self.each_backend(body)

    def test_the_dashboard_resolves_a_compressed_rollout_for_its_timeline(self):
        packed = self.compressed(2)

        def body():
            self.scan(packed)
            with patch.object(P, "discover", return_value=[("codex", str(packed), None)]):
                got = D.build_session_timeline("fixture-device", "codex", THREAD)
                self.assertTrue(got["available"])
                self.assertEqual(sum(e["kind"] == "request" for e in got["events"]), 2)
                # only a ledger entry's own discovered path is ever opened, never a caller's name
                with patch.object(D.ST, "timeline") as reader:
                    self.assertFalse(D.build_session_timeline("fixture-device", "codex",
                                                              "../../" + STAMP)["available"])
                    reader.assert_not_called()
        self.each_backend(body)

    def test_a_decoder_that_appears_later_reads_what_was_waiting(self):
        plain = self.plain(2)
        self.scan(plain)
        plain.unlink()
        packed = self.compressed(3)
        with patch.object(P, "_zstd_backend", return_value=None):
            self.scan(packed)
            self.assertEqual(D._meta["codex_compressed_unread"], 1)
            self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)
        with patch.object(P, "_zstd_backend", return_value=BACKENDS[0][1]):
            self.scan(packed)
            self.assertEqual(D._meta["codex_compressed_unread"], 0)
            payload = D.build_payload()
            self.assertEqual(payload["codex_compressed_unread"], 0)
            self.assertEqual(codex_tokens(payload), 3 * TURN_TOKENS)

    def test_openclaws_compressed_events_use_the_same_decoder_selection(self):
        def body():
            self.assertEqual(P._zstd_decode_many({"a": REAL_FRAME, "b": zstd_store(b"[1]")}),
                             {"a": sample_text(), "b": "[1]"})
        self.each_backend(body)


class WithoutADecoder(LedgerCase):
    """Python < 3.14 and no zstd command: nothing is dropped, and the machine says so."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(P, "_zstd_backend", return_value=None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_unread_logs_keep_the_archived_entry_and_are_counted_and_reported(self):
        plain = self.plain(2)
        self.scan(plain)
        before = codex_tokens(D.build_payload())
        self.assertEqual(before, 2 * TURN_TOKENS)
        plain.unlink()
        packed = self.compressed(3)                       # its content is never decoded here
        err = self.scan(packed)
        self.assertTrue(self.entries()[str(plain)]["archived"])   # the retained entry keeps counting
        self.assertNotIn(str(packed), self.entries())             # nothing is invented for the unread file
        payload = D.build_payload()
        self.assertEqual(codex_tokens(payload), before)
        self.assertEqual(payload["codex_compressed_unread"], 1)
        self.assertEqual(payload["meta"]["codex_compressed_unread"], 1)
        self.assertEqual(D._meta["parse_errors"], 0)              # a missing decoder is not a log error
        self.assertIn("[codex] 1 compressed rollout log(s) not read: install zstd or use Python 3.14+", err)
        for private in (str(self.root), "rollout-", THREAD, ".zst"):
            self.assertNotIn(private, err)
        self.assertEqual(len([line for line in err.splitlines() if "compressed" in line]), 1)
        self.assertEqual(self.scan(packed), "")                   # not repeated while the count is the same
        self.assertEqual(D.build_payload()["codex_compressed_unread"], 1)

    def test_a_fresh_install_with_only_compressed_logs_reports_them_and_invents_nothing(self):
        paths = [self.compressed(1, name="rollout-2026-10-10T12-00-0%d-019a1234-0000-7000-8000-00000000000%d" % (n, n),
                                 thread="019a1234-0000-7000-8000-00000000000%d" % n) for n in (1, 2, 3)]
        err = self.scan(*paths)
        payload = D.build_payload()
        self.assertEqual(self.entries(), {})
        self.assertEqual(codex_tokens(payload), 0)
        self.assertEqual(payload["codex_compressed_unread"], 3)
        self.assertIn("3 compressed rollout log(s) not read", err)
        self.assertEqual(D.build_diagnostics()["sources"][0]["errors"], 0)

    def test_an_entry_read_earlier_is_kept_when_its_file_changes_and_nothing_can_read_it(self):
        path = self.compressed(2)
        with patch.object(P, "_zstd_backend", return_value=FAKE_BACKEND), \
             patch.object(P, "_zstd_lines", side_effect=lines_of(rollout(2))):
            self.scan(path)
        entry = self.entries()[str(path)]
        before = copy.deepcopy(entry)
        path.write_bytes(zstd_store(rollout(5).encode("utf-8")))         # replaced, and unreadable now
        self.scan(path)
        self.assertIs(self.entries()[str(path)], entry)
        self.assertEqual(entry, before)
        self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)
        self.assertEqual(D._meta["codex_compressed_unread"], 1)

    def test_a_current_entry_needs_no_decoder_and_is_not_unread(self):
        path = self.compressed(2)
        with patch.object(P, "_zstd_backend", return_value=FAKE_BACKEND), \
             patch.object(P, "_zstd_lines", side_effect=lines_of(rollout(2))):
            entry = P.update_file(None, "codex", str(path), None, {})
        self.assertIs(P.update_file(entry, "codex", str(path), None, {}), entry)
        D._state["files"][str(path)] = entry
        self.scan(path)
        self.assertEqual(D._meta["codex_compressed_unread"], 0)
        self.assertEqual(codex_tokens(D.build_payload()), 2 * TURN_TOKENS)

    def test_update_file_signals_the_missing_decoder_and_changes_nothing(self):
        path = self.compressed(1)
        with self.assertRaises(P.ZstdUnavailable):
            P.update_file(None, "codex", str(path), None, {})
        with self.assertRaises(P.ZstdUnavailable):
            with P.zstd_stream(str(path)):
                pass
        self.assertEqual(P._ZSTD_BAD, {})
        self.assertFalse(P.zstd_available())
        self.assertEqual(P._zstd_decode_many({"row": zstd_store(b"{}")}), {})   # OpenClaw: the same policy

    def test_the_timeline_of_a_compressed_rollout_says_it_is_unavailable(self):
        got = ST.timeline("codex", self.compressed(2), THREAD)
        self.assertFalse(got["available"])
        self.assertEqual(got["events"], [])
        self.assertIn("zstd", got["detail"])

    def test_diagnostics_explain_the_unread_logs_in_settings(self):
        self.scan(self.compressed(1))
        row = D.build_diagnostics()["sources"][0]
        self.assertIn("1 compressed log not read", row["hint"])
        self.assertIn("install the zstd command or use Python 3.14+", row["hint"])
        self.assertEqual(row["errors"], 0)
        self.scan(self.plain(1))                                         # nothing unread any more
        self.assertFalse(D.build_diagnostics()["sources"][0].get("hint"))


class CleanupOffers(unittest.TestCase):
    def targets(self, backend):
        with tempfile.TemporaryDirectory() as scratch:
            roots = [os.path.join(scratch, "sessions"), os.path.join(scratch, "archived_sessions")]
            for root in roots:
                os.makedirs(root)
            globs = [os.path.join(r, "**", n) for r in roots for n in ("*.jsonl", "*.jsonl.zst")]
            with patch.object(P, "CODEX_GLOBS", globs), patch.object(P, "CLAUDE_GLOBS", []), \
                 patch.object(P, "CLAUDE_DESKTOP_GLOBS", []), patch.object(P, "COPILOT_ROOTS", []), \
                 patch.object(P, "_zstd_backend", return_value=backend):
                return sorted(name for _, _, name in D._cleanup_targets()), D._cleanup_plan()["commands"]

    def test_compressed_rollouts_are_swept_only_where_this_install_can_read_them(self):
        names, commands = self.targets(FAKE_BACKEND)
        self.assertEqual(names, ["*.jsonl", "*.jsonl", "*.jsonl.zst", "*.jsonl.zst"])
        self.assertEqual(sum("*.jsonl.zst" in c for c in commands), 2)
        names, commands = self.targets(None)       # unread logs never reached the ledger
        self.assertEqual(names, ["*.jsonl", "*.jsonl"])
        self.assertFalse(any(".zst" in c for c in commands))

    def test_a_compressed_rollout_matches_the_sweep_it_belongs_to(self):
        sweep = [("/h/.codex/sessions", None, "*.jsonl.zst")]
        self.assertTrue(D._swept_by_cleanup("/h/.codex/sessions/2026/10/x.jsonl.zst", sweep))
        self.assertFalse(D._swept_by_cleanup("/h/.codex/sessions/2026/10/x.jsonl", sweep))
        self.assertFalse(D._swept_by_cleanup("/h/.codex/sessions/2026/10/x.jsonl.zst",
                                             [("/h/.codex/sessions", None, "*.jsonl")]))


class Discovery(unittest.TestCase):
    def test_both_codex_roots_discover_plain_and_compressed_rollouts(self):
        self.assertEqual(sorted(os.path.basename(g) for g in P.CODEX_GLOBS),
                         ["*.jsonl", "*.jsonl", "*.jsonl.zst", "*.jsonl.zst"])
        with tempfile.TemporaryDirectory() as scratch:
            real = os.path.join(P.HOME, ".codex")
            moved = [g.replace(real, scratch, 1) for g in P.CODEX_GLOBS]
            self.assertTrue(all(g.startswith(scratch) for g in moved))
            expected = []
            for root in ("sessions/2026/10/10", "archived_sessions"):
                os.makedirs(os.path.join(scratch, root))
                for suffix in (".jsonl", ".jsonl.zst"):
                    path = os.path.join(scratch, root, STAMP + suffix)
                    open(path, "wb").close()
                    expected.append(path)
                open(os.path.join(scratch, root, STAMP + ".jsonl.zst.tmp"), "wb").close()   # a temp file
            with patch.object(P, "CODEX_GLOBS", moved), patch.object(P, "CLAUDE_GLOBS", []), \
                 patch.object(P, "CLAUDE_DESKTOP_GLOBS", []), patch.object(P, "ANTIGRAVITY_ROOTS", []), \
                 patch.object(P, "CURSOR_DBS", []), patch.object(P, "HERMES_DB", os.path.join(scratch, "x")), \
                 patch.object(P, "_openclaw_roots", return_value=[]), patch.object(P, "OPENCODE_DBS", []), \
                 patch.object(P, "OPENCODE_ROOTS", []), patch.object(P, "COPILOT_SDK_GLOBS", []), \
                 patch.object(P, "COPILOT_ROOTS", []):
                found = sorted(path for source, path, _ in P.discover() if source == "codex")
            self.assertEqual(found, sorted(expected))


class ConversationIdentity(unittest.TestCase):
    """_one_per_conversation treats <rollout>.jsonl and <rollout>.jsonl.zst as one."""

    def entry(self, path, tokens, archived=False, mtime=1.0, device=None):
        agg = {"source": "codex", "path": path, "archived": archived, "mtime": mtime,
               "records": {"2026-10-10\tgpt-6.1-sol": {"in": tokens, "out": 0, "cr": 0, "cc": 0,
                                                      "asst": 0, "user": 0}}}
        if device:
            agg["_device"] = device
        return path, agg

    def kept(self, *items):
        return sorted(a["path"] for a in D._one_per_conversation(list(items)))

    def key(self, path, **extra):
        return D._conversation_key(path, {"source": "codex", **extra})

    def test_leaf_ignores_the_compression_suffix_and_either_separator(self):
        plain = "/home/u/.codex/sessions/2026/10/10/%s.jsonl" % STAMP
        for other in (plain + ".zst", plain + ".ZST",
                      "C:\\Users\\u\\.codex\\archived_sessions\\%s.jsonl.zst" % STAMP):
            with self.subTest(other=other):
                self.assertEqual(self.key(plain), self.key(other))
        self.assertNotEqual(self.key(plain), self.key(plain.replace("abcd", "abce")))
        self.assertNotEqual(self.key(plain), self.key(plain, _device="other"))

    def test_identity_without_a_recorded_thread_id_comes_from_either_file_name(self):
        for suffix in (".jsonl", ".jsonl.zst", ".JSONL.ZST"):
            with self.subTest(suffix=suffix):
                aggregate = {"source": "codex", "path": "/h/.codex/sessions/%s%s" % (STAMP, suffix)}
                self.assertEqual(SN.identity(aggregate, {}), THREAD)
        self.assertEqual(SN.identity({"source": "codex", "path": "/h/notes.jsonl.zst"}, {}), "")

    def test_the_fullest_copy_wins_then_the_live_one_then_the_newest(self):
        plain = "/h/.codex/sessions/%s.jsonl" % STAMP
        packed = plain + ".zst"
        self.assertEqual(self.kept(self.entry(plain, 200, archived=True), self.entry(packed, 300)), [packed])
        self.assertEqual(self.kept(self.entry(plain, 300, archived=True), self.entry(packed, 200)), [plain])
        self.assertEqual(self.kept(self.entry(plain, 200, archived=True), self.entry(packed, 200)), [packed])
        self.assertEqual(self.kept(self.entry(plain, 200, mtime=9.0), self.entry(packed, 200, mtime=1.0)), [plain])
        self.assertEqual(self.kept(self.entry(packed, 200, mtime=9.0), self.entry(plain, 200, mtime=1.0)), [packed])

    def test_other_conversations_and_other_devices_stay_separate(self):
        a = "/h/.codex/sessions/rollout-2026-10-10T12-00-00-aaaaaaaa.jsonl.zst"
        b = "/h/.codex/sessions/rollout-2026-10-10T12-00-00-bbbbbbbb.jsonl.zst"
        self.assertEqual(self.kept(self.entry(a, 1), self.entry(b, 1)), [a, b])
        self.assertEqual(len(D._one_per_conversation([self.entry(a, 1),
                                                      self.entry("peer:x:" + a, 1, device="x")])), 2)

    def test_a_peers_plain_and_compressed_entries_deduplicate_on_this_device(self):
        plain = "peer:x:C:\\Users\\u\\.codex\\sessions\\%s.jsonl" % STAMP
        packed = plain + ".zst"
        kept = D._one_per_conversation([self.entry(plain, 100, archived=True, device="x"),
                                        self.entry(packed, 100, device="x")])
        self.assertEqual([a["path"] for a in kept], [packed])


class PeerExport(LedgerCase):
    def test_a_compressed_entry_exports_and_deduplicates_on_the_receiving_device(self):
        plain = self.plain(2)
        packed = self.compressed(3)
        old = P.update_file(None, "codex", str(plain), None, {})
        old["archived"] = True
        with patch.object(P, "_zstd_backend", return_value=FAKE_BACKEND), \
             patch.object(P, "_zstd_lines", side_effect=lines_of(rollout(3))):
            new = P.update_file(None, "codex", str(packed), None, {})
        received = []
        for agg in (old, new):
            shipped = json.loads(json.dumps(D._export_agg(agg)))          # what a peer would send
            shipped["_device"] = "peer-device"
            received.append(("peer:peer-device:" + shipped["path"], shipped))
        self.assertTrue(received[1][0].endswith(".jsonl.zst"))
        kept = D._one_per_conversation(received)
        self.assertEqual([a["path"] for a in kept], [str(packed)])
        self.assertEqual(kept[0]["totals"]["req"], 3)


class CompatibleLedgers(unittest.TestCase):
    def test_older_ledgers_keep_every_entry_through_the_v64_migration(self):
        self.assertGreaterEqual(D.CACHE_VERSION, 64)
        entries = {"/old/rollout.jsonl": {"source": "codex", "archived": True, "path": "/old/rollout.jsonl",
                                          "records": {}, "totals": {}, "sessions": []},
                   "/live/rollout.jsonl": {"source": "codex", "path": "/live/rollout.jsonl",
                                           "records": {}, "totals": {}, "sessions": []}}
        for version in range(53, D.CACHE_VERSION):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as scratch, \
                 patch.object(D, "CACHE_PATH", os.path.join(scratch, "cache.json")), \
                 patch.dict(D._state, {"files": {}, "version": D.CACHE_VERSION}), \
                 patch.dict(D._dirty, {"v": False}), patch.dict(D._cache_error, {"v": None}):
                Path(D.CACHE_PATH).write_text(json.dumps({"version": version, "files": entries}))
                with contextlib.redirect_stderr(io.StringIO()):
                    D.load_cache()
                self.assertEqual(sorted(D._state["files"]), sorted(entries))   # live entries too
                self.assertTrue(D._dirty["v"])

    def test_peers_from_v52_up_are_readable_and_others_are_refused(self):
        for version in range(52, D.CACHE_VERSION + 1):
            self.assertTrue(D._peer_cache_ok(version), version)
        for version in (None, True, "63", 51, D.CACHE_VERSION + 1):
            self.assertFalse(D._peer_cache_ok(version), version)


if __name__ == "__main__":
    unittest.main()
