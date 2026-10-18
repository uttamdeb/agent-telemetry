"""MCP consent storage tests use temporary native preferences and ledger paths."""
import multiprocessing
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import mcp_access
import native


def _configure_in_process(root, data_dir, backend_url, barrier, results):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import native as child_native
    import mcp_access as child_access
    child_native.support_dir = lambda: Path(root)
    barrier.wait(timeout=10)
    try:
        child_access.configure(backend_url, data_dir, {"enabled": True, "confirmed": True})
        results.put(("ok", backend_url))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


class MCPAccessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-mcp-access-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.support = self.root / "AgentTelemetryNative"
        self.data = self.root / "ledger"
        self.other_data = self.root / "other-ledger"
        item = patch.object(native, "support_dir", return_value=self.support)
        item.start()
        self.addCleanup(item.stop)
        self.url = "http://127.0.0.1:7878"

    def test_disabled_by_default_and_status_never_returns_key_or_path(self):
        state = mcp_access.status(self.url, self.data)
        self.assertFalse(state["enabled"])
        self.assertFalse(state["include_names"])
        self.assertFalse(state["include_peers"])
        self.assertIn("cloud-backed agent", state["warning"])
        self.assertNotIn(str(self.data), repr(state))
        self.assertNotIn("token", state)
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.credentials(self.url)

    def test_enable_requires_confirmation_and_authorizes_only_configured_policy(self):
        with self.assertRaisesRegex(mcp_access.AccessError, "Confirm"):
            mcp_access.configure(self.url, self.data, {"enabled": True})
        state = mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        self.assertTrue(state["enabled"])
        token = mcp_access.credentials(self.url)
        self.assertGreaterEqual(len(token), 64)
        self.assertNotIn(token, repr(state))
        policy = mcp_access.authorize(self.url, self.data, "Bearer " + token)
        self.assertEqual(set(policy), {"include_names", "include_peers", "alias_key"})
        self.assertFalse(policy["include_names"])
        self.assertFalse(policy["include_peers"])
        self.assertRegex(policy["alias_key"], r"^[0-9a-f]{64}$")
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.data, "Bearer wrong")
        for header in (
            "Bearer " + token + " ",
            "Bearer " + token + "\n",
            "Bearer " + ("é" * 64),
        ):
            with self.subTest(header=repr(header)), self.assertRaises(mcp_access.AccessError):
                mcp_access.authorize(self.url, self.data, header)
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.other_data, "Bearer " + token)

    def test_scope_change_requires_confirmation_and_rotates_credential(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        first = mcp_access.credentials(self.url)
        with self.assertRaisesRegex(mcp_access.AccessError, "Confirm"):
            mcp_access.configure(self.url, self.data, {"enabled": True, "include_names": True})
        state = mcp_access.configure(self.url, self.data, {
            "enabled": True, "include_names": True, "confirmed": True,
        })
        second = mcp_access.credentials(self.url)
        self.assertNotEqual(first, second)
        self.assertTrue(state["include_names"])
        policy = mcp_access.authorize(self.url, self.data, "Bearer " + second)
        self.assertTrue(policy["include_names"])
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.data, "Bearer " + first)

    def test_revocation_invalidates_old_key_and_reenable_rotates_again(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        first = mcp_access.credentials(self.url)
        self.assertFalse(mcp_access.configure(self.url, self.data, {"enabled": False})["enabled"])
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.data, "Bearer " + first)
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.credentials(self.url)
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        second = mcp_access.credentials(self.url)
        self.assertNotEqual(first, second)

    def test_data_binding_change_disables_old_grant_until_confirmed_reenable(self):
        mcp_access.configure(self.url, self.data, {
            "enabled": True, "include_names": True, "confirmed": True,
        })
        token = mcp_access.credentials(self.url)
        self.assertFalse(mcp_access.status(self.url, self.other_data)["enabled"])
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.other_data, "Bearer " + token)
        state = mcp_access.configure(self.url, self.other_data, {"enabled": True, "confirmed": True})
        self.assertTrue(state["enabled"])
        self.assertFalse(state["include_names"])
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.authorize(self.url, self.data, "Bearer " + token)

    def test_ports_keep_independent_consents(self):
        other = "http://127.0.0.1:9000"
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        mcp_access.configure(other, self.data, {"enabled": True, "include_peers": True, "confirmed": True})
        self.assertTrue(mcp_access.status(self.url, self.data)["enabled"])
        self.assertTrue(mcp_access.status(other, self.data)["enabled"])
        mcp_access.configure(self.url, self.data, {"enabled": False})
        self.assertFalse(mcp_access.status(self.url, self.data)["enabled"])
        self.assertTrue(mcp_access.authorize(other, self.data, "Bearer " + mcp_access.credentials(other))["include_peers"])

    def test_backend_url_is_strict_loopback_and_canonical(self):
        self.assertEqual(mcp_access.canonical_backend_url("http://127.0.0.1"), "http://127.0.0.1:80")
        self.assertEqual(mcp_access.canonical_backend_url("http://[::1]:07878/"), "http://[::1]:7878")
        for url in (
            "https://127.0.0.1:7878", "http://localhost:7878", "http://127.0.0.2:7878",
            "http://127.1:7878", "http://example.test", "http://user@127.0.0.1",
            "http://127.0.0.1:0", "http://127.0.0.1:65536", "http://127.0.0.1/path",
            "http://127.0.0.1?", "http://127.0.0.1#fragment", "http://[::1%25lo]:7878",
        ):
            with self.subTest(url=url), self.assertRaises(mcp_access.AccessError):
                mcp_access.canonical_backend_url(url)

    def test_corrupt_and_oversized_files_are_preserved(self):
        self.support.mkdir(parents=True)
        path = self.support / "mcp-access.json"
        path.write_bytes(b"{broken")
        with self.assertRaisesRegex(mcp_access.AccessError, "corrupt"):
            mcp_access.status(self.url, self.data)
        self.assertEqual(path.read_bytes(), b"{broken")
        path.write_bytes(b" " * (mcp_access._MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(mcp_access.AccessError, "size limit"):
            mcp_access.status(self.url, self.data)
        self.assertEqual(path.stat().st_size, mcp_access._MAX_FILE_BYTES + 1)

    def test_malformed_unicode_credential_in_saved_inventory_fails_closed_and_is_preserved(self):
        mcp_access.configure(self.url, self.data, {"enabled": True, "confirmed": True})
        path = self.support / "mcp-access.json"
        state = json.loads(path.read_text(encoding="utf-8"))
        key = mcp_access._entry_key(self.url)
        state["endpoints"][key]["token"] = "é" * 64
        invalid = json.dumps(state, ensure_ascii=True, separators=(",", ":"))
        path.write_text(invalid, encoding="utf-8")
        with self.assertRaises(mcp_access.AccessError):
            mcp_access.status(self.url, self.data)
        self.assertEqual(path.read_text(encoding="utf-8"), invalid)

    def test_endpoint_inventory_has_a_fixed_entry_limit(self):
        for index in range(mcp_access._MAX_ENDPOINTS):
            mcp_access.configure(f"http://127.0.0.1:{10000 + index}", self.data, {"enabled": False})
        with self.assertRaisesRegex(mcp_access.AccessError, "maximum number"):
            mcp_access.configure(f"http://127.0.0.1:{10000 + mcp_access._MAX_ENDPOINTS}",
                                 self.data, {"enabled": False})
        self.assertEqual(len(mcp_access._load()["endpoints"]), mcp_access._MAX_ENDPOINTS)

    def test_parallel_process_grants_do_not_lose_other_ports(self):
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        results = ctx.Queue()
        urls = [self.url, "http://127.0.0.1:9001"]
        processes = [ctx.Process(target=_configure_in_process,
                                  args=(str(self.support), str(self.data), url, barrier, results))
                     for url in urls]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(15)
            self.assertTrue(all(process.exitcode == 0 for process in processes),
                            [process.exitcode for process in processes])
            answers = [results.get(timeout=2) for _ in processes]
            self.assertEqual(sorted(answer[0] for answer in answers), ["ok", "ok"])
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(2)
        self.assertTrue(mcp_access.status(self.url, self.data)["enabled"])
        self.assertTrue(mcp_access.status(urls[1], self.data)["enabled"])


if __name__ == "__main__":
    unittest.main()
