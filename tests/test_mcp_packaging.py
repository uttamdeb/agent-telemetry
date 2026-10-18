"""MCP runtime modules are explicitly packaged without private local state."""
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install


MCP_RUNTIME_FILES = (
    "mcp_access.py", "mcp_backend.py", "mcp_protocol.py", "mcp_server.py",
    "mcp_tools.py", "telemetry_insights.py", "telemetry_query.py",
)


class MCPPackagingTests(unittest.TestCase):
    def test_mcp_runtime_is_in_both_explicit_native_payloads(self):
        for name in MCP_RUNTIME_FILES:
            with self.subTest(module=name):
                self.assertIn(name, install.BACKEND_FILES)
                self.assertTrue((ROOT / name).is_file())

        mac_package = (ROOT / "macos" / "AgentTelemetryMac" / "Scripts" / "package-app.sh").read_text(
            encoding="utf-8")
        for name in MCP_RUNTIME_FILES:
            with self.subTest(mac_module=name):
                self.assertIn(f'"$AT_REPO_ROOT/{name}"', mac_package)
        for private_pattern in ("mcp-access.json", "mcp-access.lock", ".mcp-access-*", "project-names.json"):
            with self.subTest(mac_private_pattern=private_pattern):
                self.assertIn(private_pattern, mac_package)

    def test_windows_payload_contains_mcp_runtime_and_frontend(self):
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-mcp-package-") as temporary:
            destination = Path(temporary) / "payload"
            install.copy_windows_payload(destination)
            for name in MCP_RUNTIME_FILES:
                with self.subTest(module=name):
                    self.assertTrue((destination / name).is_file())
            self.assertTrue((destination / "static" / "mcp.js").is_file())
            self.assertFalse((destination / "package.json").exists())

    def test_payload_guard_rejects_ledger_sharing_and_mcp_credentials(self):
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-mcp-private-") as temporary:
            payload = Path(temporary)
            forbidden_files = (
                ".usage_cache.json", ".peers.json", ".local_servers.json",
                ".local_servers.secret", "mcp-access.json", "mcp-access.lock",
                ".mcp-access-stage.tmp", "server.log", "project-names.json",
            )
            for name in forbidden_files:
                with self.subTest(name=name):
                    path = payload / name
                    path.write_text("private sentinel", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "personal telemetry"):
                        install._validate_windows_payload(payload)
                    path.unlink()


if __name__ == "__main__":
    unittest.main()
