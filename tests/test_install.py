import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install


class WindowsPayloadTests(unittest.TestCase):
    def test_staged_dashboard_imports_all_allowlisted_backend_modules(self):
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-payload-") as temporary:
            root = Path(temporary)
            payload = root / "payload"
            install.copy_windows_payload(payload)

            self.assertTrue((payload / "local_servers.py").is_file())
            self.assertTrue((payload / "local_servers_windows.py").is_file())
            source = (
                "import sys; from pathlib import Path; "
                f"sys.path.insert(0, {str(payload)!r}); "
                "import dashboard, local_servers, local_servers_windows; "
                "assert Path(dashboard.__file__).resolve().parent == Path(sys.path[0]).resolve(); "
                "assert Path(local_servers.__file__).resolve().parent == Path(sys.path[0]).resolve()"
            )
            environment = dict(os.environ)
            environment.update({
                "HOME": str(root),
                "LOCALAPPDATA": str(root / "LocalAppData"),
                "APPDATA": str(root / "AppData"),
                "XDG_DATA_HOME": str(root / "xdg-data"),
                "PYTHONDONTWRITEBYTECODE": "1",
            })
            subprocess.run(
                [sys.executable, "-I", "-c", source],
                cwd=str(root), env=environment, check=True, capture_output=True, text=True,
            )

            forbidden = {
                ".usage_cache.json", ".peers.json", ".peers", "server.log",
                ".local_servers.json", ".local_servers.json.lock",
                ".local_servers.json.lock.start.0123456789abcdefabcd",
            }
            self.assertFalse(any(path.name in forbidden for path in payload.rglob("*")))

    def test_private_local_server_inventory_and_locks_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-private-payload-") as temporary:
            payload = Path(temporary)
            for name in (
                ".local_servers.json",
                ".local_servers.json.lock",
                ".local_servers.json.lock.start.0123456789abcdefabcd",
                ".local_servers.fixture.tmp",
            ):
                with self.subTest(name=name):
                    marker = payload / name
                    marker.write_text("private", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "personal telemetry"):
                        install._validate_windows_payload(payload)
                    marker.unlink()

    def test_legacy_credentials_and_other_private_files_are_still_rejected(self):
        # 2.1.0 users may still have these files beside a checkout or payload.
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-private-names-") as temporary:
            payload = Path(temporary)
            for name in (".usage_cache.json", ".peers.json", "mcp-access.json", "mcp-access.lock",
                         ".mcp-access-stage.tmp", "server.log", "project-names.json"):
                with self.subTest(name=name):
                    path = payload / name
                    path.write_text("private sentinel", encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "personal telemetry"):
                        install._validate_windows_payload(payload)
                    path.unlink()

    def test_payload_has_no_optional_extension_modules(self):
        with tempfile.TemporaryDirectory(prefix="agenttelemetry-payload-names-") as temporary:
            payload = Path(temporary) / "payload"
            install.copy_windows_payload(payload)
            names = {path.name for path in payload.rglob("*") if path.is_file()}
            self.assertFalse({name for name in names if name.startswith(("mcp_", "telemetry_"))})
            self.assertNotIn("mcp.js", names)
            self.assertNotIn("insights.js", names)


if __name__ == "__main__":
    unittest.main()
