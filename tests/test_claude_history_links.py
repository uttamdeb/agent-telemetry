"""Directory aliases preserve Claude history without duplicating telemetry."""
import copy
import gzip
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


class ClaudeHistoryLinksTests(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.old = self.root / "original"
        self.old.mkdir()
        self.alias = self.root / "renamed"
        try:
            self.alias.symlink_to(self.old, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlinks unavailable")

    def aggregate(self, path, archived=False, device=None, tokens=100):
        agg = P._blank_agg("claude", str(path))
        agg.update(archived=archived, size=32)
        P._rec(agg, "2026-10-18", "Claude Sonnet 5.5")["in"] = tokens
        if device:
            agg["_device"] = device
        return agg

    def test_discovery_keeps_one_physical_parent_and_each_workflow_child(self):
        names = ["parent.jsonl", "parent/subagents/workflows/one/agent-a0.jsonl",
                 "parent/subagents/workflows/two/agent-a0.jsonl"]
        for name in names:
            file = self.old / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("{}\n")
        with patch.object(P, "CLAUDE_GLOBS", [str(self.root / "**/*.jsonl")]):
            found = [p for source, p, _ in P.discover() if source == "claude"]
        self.assertCountEqual(found, [str((self.old / name).resolve()) for name in names])

    def test_ledger_alias_is_counted_once_without_mutating_archives(self):
        path = self.old / "parent.jsonl"
        path.write_text("{}\n")
        archived = self.aggregate(self.alias / path.name, archived=True)
        live = self.aggregate(path)
        items = [(a["path"], a) for a in (archived, live)]
        before = copy.deepcopy(items)
        self.assertEqual(D._one_per_conversation(items), [live])
        # Archived history still deduplicates after source pruning, as long as
        # the directory compatibility link is retained.
        path.unlink()
        self.assertEqual(D._one_per_conversation(items), [live])
        self.assertEqual(items, before)
        archived["records"]["2026-10-18\tClaude Sonnet 5.5"]["in"] = 200
        self.assertEqual(D._one_per_conversation(items), [archived])

    def test_same_names_in_unrelated_roots_and_peer_paths_are_not_collapsed(self):
        other = self.root / "unrelated" / "parent.jsonl"
        local = self.aggregate(self.old / "parent.jsonl")
        distinct = self.aggregate(other)
        peer1 = self.aggregate(self.old / "parent.jsonl", device="peer")
        peer2 = self.aggregate(self.alias / "parent.jsonl", device="peer")
        items = [(a["path"], a) for a in (local, distinct, peer1, peer2)]
        self.assertEqual(len(D._one_per_conversation(items)), 4)

    def test_peer_export_deduplicates_local_alias_and_preserves_ledger(self):
        archived = self.aggregate(self.alias / "parent.jsonl", archived=True)
        live = self.aggregate(self.old / "parent.jsonl")
        files = {a["path"]: a for a in (archived, live)}
        before = copy.deepcopy(files)
        with patch.dict(D._state, {"files": files}), patch.dict(D._peer, {"export": None}), \
             patch.object(D.P, "_ide_of", return_value="VS Code"), \
             patch.object(D.P, "activity_of", return_value={}), \
             patch.object(D, "_local_id", return_value="fixture"):
            _, body = D._export_body()
        exported = json.loads(gzip.decompress(body))["files"]
        self.assertEqual(list(exported), [live["path"]])
        self.assertEqual(files, before)


if __name__ == "__main__":
    unittest.main()
