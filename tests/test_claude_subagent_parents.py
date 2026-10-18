"""Claude child-directory evidence enriches sessions without changing usage."""
from contextlib import ExitStack
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard as D
import parser as P


PARENT = "12345678-1234-1234-1234-123456789abc"
OTHER_PARENT = "12345678-1234-1234-1234-123456789def"
ROOT = "/fixture/.claude/projects/project"


def aggregate(path, child=False, source="claude", device=None, archived=False):
    agg = P._blank_agg(source, path)
    agg.update(subagent=child, archived=archived, project="Fixture")
    if device is not None:
        agg["_device"] = device
    row = P._rec(agg, "2026-10-18", "Claude Sonnet 5.5")
    row.update({"in": 100, "out": 10, "asst": 1, "req": 1})
    agg["sessions"] = [{"id": P._leaf(path)[:8], "source": source,
        "subagent": child, "title": "Same invented title", "project": "Fixture",
        "model": "Claude Sonnet 5.5", "models": ["Claude Sonnet 5.5"],
        "start": "2026-10-18T10:00:00+00:00", "end": "2026-10-18T10:01:00+00:00",
        "in": 100, "out": 10, "asst": 1, "req": 1, "side": 0, "subagents": 0}]
    return agg


class ClaudeSubagentParentTests(unittest.TestCase):
    def payload(self, files, peers=(), counts=None):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(D._state, {"files": {str(i): a for i, a in enumerate(files)}}))
            stack.enter_context(patch.object(D, "_peer_items", return_value=list(peers)))
            stack.enter_context(patch.object(D, "_local_id", return_value="local"))
            stack.enter_context(patch.object(D, "_devices_meta", return_value=[]))
            stack.enter_context(patch.object(D, "_mcp_servers", return_value=[]))
            stack.enter_context(patch.object(D, "_codex_config", return_value={"effort": None}))
            stack.enter_context(patch.object(D, "_context_files", return_value=[]))
            stack.enter_context(patch.object(D, "_installed", return_value=[]))
            stack.enter_context(patch.object(D.SN, "availability", return_value=(False, "Fixture")))
            stack.enter_context(patch.object(D.P, "_ide_of", return_value="CLI"))
            stack.enter_context(patch.object(D.time, "time", return_value=123))
            if counts is not None:
                stack.enter_context(patch.object(D, "_claude_child_transcript_counts", return_value=counts))
            return D.build_payload()

    def test_full_relative_child_identities_count_once_with_archives(self):
        files = [aggregate(f"{ROOT}/{PARENT}.jsonl"),
            aggregate(f"{ROOT}/{PARENT}/subagents/agent-abcdef01000000001.jsonl", True),
            aggregate(f"{ROOT}/{PARENT}/subagents/workflows/wf_one/agent-abcdef01000000002.jsonl", True, archived=True),
            aggregate(f"{ROOT}/{PARENT}/subagents/workflows/wf_two/agent-abcdef01000000001.jsonl", True),
            aggregate(f"{ROOT}/{PARENT}/subagents/workflows/wf_two/agent-abcdef01000000001.jsonl", True, archived=True)]
        before = copy.deepcopy(files)
        payload = self.payload(files)
        parents = [s for s in payload["sessions"] if not s["subagent"]]
        self.assertEqual(parents[0]["subagent_children"], 3)
        self.assertEqual(parents[0]["subagents"], 0)
        self.assertTrue(all("subagent_children" not in s for s in payload["sessions"] if s["subagent"]))
        self.assertTrue(all(s["subagent_parent"] == {"id": PARENT, "title": "Same invented title",
            "source": "claude", "device": "local"} for s in payload["sessions"] if s["subagent"]))
        self.assertEqual(files, before, "Derived evidence must never mutate the durable aggregates")

    def test_short_agent_names_are_distinct_across_workflows(self):
        files = [aggregate(f"{ROOT}/{PARENT}.jsonl")]
        files += [aggregate(f"{ROOT}/{PARENT}/subagents/workflows/{workflow}/agent-a0.jsonl", True)
            for workflow in ("wf_first", "wf_second")]
        self.assertEqual(self.payload(files)["sessions"][0]["subagent_children"], 2)

    def test_usage_cost_and_other_payload_fields_are_identical(self):
        files = [aggregate(f"{ROOT}/{PARENT}.jsonl"),
            aggregate(f"{ROOT}/{PARENT}/subagents/workflows/wf_one/agent-first.jsonl", True)]
        files[0]["sessions"][0].update(side=25, subagents=3)
        baseline = self.payload(files, counts={})
        enriched = self.payload(files)
        parent = next(s for s in enriched["sessions"] if not s["subagent"])
        self.assertEqual(parent.pop("subagent_children"), 1)
        self.assertEqual(parent["side"], 25)
        self.assertEqual(parent["subagents"], 3, "Independent delegation evidence is not summed")
        # Parent descriptors are also presentation metadata only.
        for payload in (baseline, enriched):
            for session in payload["sessions"]:
                session.pop("subagent_parent", None)
        self.assertEqual(enriched, baseline)

    def test_device_project_root_and_full_parent_identity_isolate_associations(self):
        parent = aggregate(f"{ROOT}/{PARENT}.jsonl")
        remote_parent = aggregate(f"{ROOT}/{PARENT}.jsonl", device="remote")
        remote_child = aggregate(f"{ROOT}/{PARENT}/subagents/agent-same.jsonl", True, device="remote")
        peer_items = [("peer:remote:parent", remote_parent), ("peer:remote:child", remote_child)]
        files = [parent,
            aggregate(f"{ROOT}/{OTHER_PARENT}/subagents/agent-same.jsonl", True),
            aggregate(f"{ROOT}-other/{PARENT}/subagents/agent-same.jsonl", True)]
        payload = self.payload(files, peers=peer_items)
        local = next(s for s in payload["sessions"] if not s["subagent"] and s["device"] == "local")
        remote = next(s for s in payload["sessions"] if not s["subagent"] and s["device"] == "remote")
        self.assertNotIn("subagent_children", local)
        self.assertEqual(remote["subagent_children"], 1)
        child = next(s for s in payload["sessions"] if s["subagent"] and s["device"] == "remote")
        self.assertEqual(child["subagent_parent"]["device"], "remote")
        self.assertTrue(all("subagent_parent" not in s for s in payload["sessions"]
            if s["subagent"] and s["device"] == "local"))

    def test_copied_windows_separators_case_and_unc_paths_are_supported(self):
        for root in (r"C:\Users\Fixture\.claude\projects\project", r"\\server\share\project"):
            with self.subTest(root=root):
                parent = aggregate(root + "\\" + PARENT.upper() + ".jsonl", archived=True)
                child = aggregate(root.lower().replace("\\", "/") + "/" + PARENT +
                    "/subagents/workflows/wf_one/agent-abcdef01000000001.jsonl", True)
                sessions = self.payload([parent, child])["sessions"]
                self.assertEqual(sessions[0]["subagent_children"], 1)
                self.assertEqual(sessions[1]["subagent_parent"]["id"], PARENT)

    def test_ambiguous_or_missing_parents_do_not_resolve_a_child_descriptor(self):
        parent = aggregate(f"{ROOT}/{PARENT}.jsonl")
        duplicate = copy.deepcopy(parent)
        duplicate["archived"] = True
        child = aggregate(f"{ROOT}/{PARENT}/subagents/agent-one.jsonl", True)
        for files in ([child], [parent, duplicate, child]):
            with self.subTest(parent_count=len(files)-1):
                session = next(s for s in self.payload(files)["sessions"] if s["subagent"])
                self.assertNotIn("subagent_parent", session)

    def test_unknown_missing_and_unmarked_layouts_remain_unknown(self):
        paths = [f"{ROOT}/{PARENT}/subagents/other/agent-one.jsonl",
            f"{ROOT}/{PARENT}/subagents/workflows/not-a-workflow/agent-one.jsonl",
            f"{ROOT}/{PARENT}/subagents/agent-.jsonl",
            f"{ROOT}/{PARENT}/subagents/../subagents/agent-one.jsonl",
            f"{ROOT}/{PARENT}/subagents/agent-one.json",
            f"{ROOT}/not-a-full-uuid/subagents/agent-one.jsonl"]
        files = [aggregate(f"{ROOT}/{PARENT}.jsonl")]
        files += [aggregate(path, True) for path in paths]
        files += [aggregate(f"{ROOT}/{PARENT}/subagents/agent-unmarked.jsonl"),
            aggregate(f"{ROOT}/{PARENT}/subagents/agent-codex.jsonl", True, source="codex")]
        self.assertEqual(D._claude_child_transcript_counts(files, "local"), {})
        self.assertNotIn("subagent_children", self.payload(files)["sessions"][0])


if __name__ == "__main__":
    unittest.main()
