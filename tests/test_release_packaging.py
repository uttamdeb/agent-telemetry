"""Binary packaging guards; no network, installed app or personal data."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import install

ROOT = Path(__file__).resolve().parents[1]


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


BUILD = module("windows_release_build", ROOT / "windows" / "installer" / "build.py")
REGISTER = module("windows_release_register", ROOT / "windows" / "installer" / "register_install.py")


class PackagingTests(unittest.TestCase):
    def test_source_installer_cannot_remove_a_bundled_runtime_or_uninstaller(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python = root / "source-python" / "python.exe"
            python.parent.mkdir()
            python.with_name("pythonw.exe").write_bytes(b"fixture")
            app = root / "Programs" / "AgentTelemetry"
            (app / "runtime").mkdir(parents=True)
            (app / "runtime" / "pythonw.exe").write_bytes(b"keep runtime")
            (app / "BUILD-INFO.json").write_text("{}")
            (app / "unins000.exe").write_bytes(b"keep uninstaller")
            with patch.object(sys, "_base_executable", str(python), create=True), patch.dict("os.environ", {"LOCALAPPDATA": str(root)}):
                with self.assertRaisesRegex(ValueError, "EXE"):
                    install.install_windows()
            self.assertEqual((app / "runtime" / "pythonw.exe").read_bytes(), b"keep runtime")
            self.assertEqual((app / "unins000.exe").read_bytes(), b"keep uninstaller")
            self.assertFalse(app.with_name("AgentTelemetry.previous").exists())

    def test_stage_uses_allowlist_and_bundled_isolated_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "python.zip"
            with zipfile.ZipFile(archive, "w") as zipped:
                zipped.writestr("pythonw.exe", b"runtime fixture")
                zipped.writestr("python.exe", b"runtime fixture")
                zipped.writestr("python313._pth", "untrusted search path")
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(BUILD, "PYTHON_SHA256", checksum), patch.object(BUILD.subprocess, "check_output", return_value="release-fixture\n"):
                BUILD.stage(ROOT, root / "payload", archive, "2.0.0")
            payload = root / "payload"
            self.assertEqual((payload / "runtime" / "python313._pth").read_text(), "python313.zip\n.\n..\n")
            self.assertEqual(json.loads((payload / "BUILD-INFO.json").read_text())["commit"], "release-fixture")
            self.assertTrue((payload / "windows" / "register_install.py").is_file())
            self.assertEqual((payload / "session_names.py").read_bytes(), (ROOT / "session_names.py").read_bytes())
            self.assertEqual((payload / "accounts.py").read_bytes(), (ROOT / "accounts.py").read_bytes())
            for name in ("ledger_backups.py", "session_timeline.py", "diagnostics.py"):
                self.assertEqual((payload / name).read_bytes(), (ROOT / name).read_bytes())
            for icon in (ROOT / "static" / "tool-icons").glob("*.svg"):
                self.assertEqual((payload / "static" / "tool-icons" / icon.name).read_bytes(), icon.read_bytes())
            self.assertFalse(any(p.name in {".usage_cache.json", ".usage-backups", ".peers.json", ".peers", "server.log"} for p in payload.rglob("*")))

    def test_runtime_checksum_failure_never_extracts_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "unverified.zip"
            archive.write_bytes(b"tampered fixture")
            with self.assertRaisesRegex(ValueError, "checksum"):
                BUILD.stage(ROOT, root / "payload", archive, "2.0.0")
            self.assertFalse((root / "payload" / "runtime").exists())

    def test_guard_refuses_live_and_closing_clients(self):
        with patch.object(sys, "argv", ["register", "check"]), patch.object(REGISTER.native, "status", return_value={"running": True}):
            self.assertEqual(REGISTER.main(), 10)
        with patch.object(sys, "argv", ["register", "register"]), patch.object(REGISTER.native, "status", return_value={"running": True}), patch.object(REGISTER.native, "_write") as write:
            self.assertEqual(REGISTER.main(), 1)
            write.assert_not_called()

    def test_register_and_uninstall_only_change_owned_manifest(self):
        with patch.object(REGISTER.native, "status", return_value={"running": False}), patch.object(REGISTER, "update_login") as login, patch.object(REGISTER.native, "_write") as write:
            current = {"entry": str(REGISTER.ROOT / "windows" / "tray.py"), "command": ["pythonw", "tray"]}
            with patch.object(REGISTER, "manifest", return_value=current), patch.object(REGISTER.native, "_read", return_value={}), patch.object(sys, "argv", ["register", "register"]):
                self.assertEqual(REGISTER.main(), 0)
                write.assert_called_once_with("native-install.json", current)
            write.reset_mock()
            with patch.object(REGISTER.native, "_read", return_value={"entry": "another install"}), patch.object(sys, "argv", ["register", "unregister"]):
                self.assertEqual(REGISTER.main(), 0)
                write.assert_not_called()
            with patch.object(REGISTER.native, "_read", return_value=current), patch.object(sys, "argv", ["register", "unregister"]):
                self.assertEqual(REGISTER.main(), 0)
                write.assert_called_once_with("native-install.json", {})
                login.assert_called_with(current, None)


if __name__ == "__main__":
    unittest.main()
