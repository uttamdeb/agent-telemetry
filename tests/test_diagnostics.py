import unittest

from diagnostics import DiagnosticsTracker


class DiagnosticsTrackerTests(unittest.TestCase):
    def test_absent_sources_and_idle_scan_are_small_and_empty(self):
        tracker = DiagnosticsTracker()
        tracker.begin_scan({})
        tracker.finish_scan()
        report = tracker.snapshot()
        self.assertEqual(report["sources"], [])
        self.assertEqual(report["status"], "idle")
        self.assertEqual(report["discovered"], 0)

    def test_all_fail_is_an_error_and_never_claims_success(self):
        tracker = DiagnosticsTracker()
        tracker.begin_scan({"claude": 2, "codex": 1})
        tracker.record_failure("claude", r"C:\Users\private\secret.jsonl", PermissionError(r"failed C:\Users\private\secret.jsonl"))
        tracker.record_failure("claude", "secret prompt", ValueError("private prompt body"))
        tracker.record_failure("codex", "private path", RuntimeError("access token=secret"))
        tracker.finish_scan()
        report = tracker.snapshot()
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["read"], 0)
        self.assertEqual(report["errors"], 3)
        encoded = repr(report)
        for private in ("private", "secret", "prompt body", "access token", "C:\\Users"):
            self.assertNotIn(private, encoded)
        self.assertIn("Check its file permissions", report["issues"][0]["hint"])

    def test_partial_parse_then_recovery_has_current_scan_state(self):
        tracker = DiagnosticsTracker()
        tracker.begin_scan({"opencode": 2})
        tracker.record_success("opencode", "private.db")
        tracker.record_failure("opencode", "private.db", ValueError("bad data at C:\\secret"))
        partial = tracker.snapshot()
        self.assertEqual(partial["sources"][0]["status"], "partial_error")
        self.assertEqual(partial["sources"][0]["read"], 1)
        self.assertIsNotNone(partial["sources"][0]["last_success"])
        tracker.begin_scan({"opencode": 1})
        tracker.record_success("opencode", "private.db")
        tracker.finish_scan()
        recovered = tracker.snapshot()
        self.assertEqual(recovered["sources"][0]["status"], "available")
        self.assertEqual(recovered["sources"][0]["errors"], 0)
        self.assertEqual(recovered["issues"], [])

    def test_path_like_source_keys_are_redacted(self):
        tracker = DiagnosticsTracker()
        tracker.begin_scan({r"C:\Users\private\project": 1})
        tracker.record_failure(r"C:\Users\private\project", "private.log", RuntimeError("failure"))
        self.assertEqual(tracker.snapshot()["sources"][0]["source"], "unknown")
        self.assertNotIn("private", repr(tracker.snapshot()))

    def test_issue_history_is_capped_and_no_path_or_message_is_kept(self):
        tracker = DiagnosticsTracker()
        tracker.begin_scan({"codex": 20})
        for index in range(20):
            tracker.record_failure("codex", f"C:/workspace/project-{index}.jsonl", RuntimeError(f"prompt {index} api_key=secret"))
        report = tracker.snapshot()
        self.assertEqual(len(report["issues"]), 12)
        self.assertNotIn("workspace", repr(report))
        self.assertNotIn("api_key", repr(report))
        self.assertEqual(report["sources"][0]["last_success"], None)


if __name__ == "__main__":
    unittest.main()
