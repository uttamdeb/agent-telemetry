"""Binary packaging guards; no network, installed app or personal data."""
import hashlib
import importlib.util
import json
import os
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
    def test_release_refresh_requires_clean_pinned_descendant_and_preserves_tag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()
            git("init", "-q")
            git("config", "user.name", "Fixture")
            git("config", "user.email", "fixture@example.invalid")
            item = root / "source.txt"
            item.write_text("original")
            git("add", ".")
            git("commit", "-qm", "original")
            original = git("rev-parse", "HEAD")
            git("tag", "v2.0.0")
            self.assertEqual(BUILD.validate_source(root, "v2.0.0"), original)
            item.write_text("updated")
            git("commit", "-qam", "update")
            updated = git("rev-parse", "HEAD")
            with self.assertRaisesRegex(ValueError, "release tag"):
                BUILD.validate_source(root, "v2.0.0")
            for wrong in ("main", updated[:8], original):
                with self.subTest(commit=wrong), self.assertRaisesRegex(ValueError, "full source commit"):
                    BUILD.validate_source(root, "v2.0.0", wrong)
            self.assertEqual(BUILD.validate_source(root, "v2.0.0", updated), updated)
            self.assertEqual(git("rev-parse", "v2.0.0"), original)
            item.write_text("dirty")
            with self.assertRaisesRegex(ValueError, "modified"):
                BUILD.validate_source(root, "v2.0.0", updated)
            git("checkout", "--", "source.txt")
            git("checkout", "-q", "--orphan", "unrelated")
            git("commit", "-qm", "unrelated root")
            with self.assertRaisesRegex(ValueError, "descend"):
                BUILD.validate_source(root, "v2.0.0", git("rev-parse", "HEAD"))

    @unittest.skipIf(os.name == "nt", "macOS packaging uses a POSIX shell")
    def test_macos_packaging_guard_refuses_private_ledger_backups(self):
        script = (ROOT / "macos/AgentTelemetryMac/Scripts/package-app.sh").read_text()
        start = script.index('if find "$AT_APP_PATH/Contents/Resources"')
        guard = script[start:script.index("\nfi", start) + len("\nfi")]
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "fixture.app"
            resources = app / "Contents" / "Resources"
            resources.mkdir(parents=True)
            environment = dict(os.environ, AT_APP_PATH=str(app))
            result = subprocess.run(["bash", "-c", guard], env=environment, capture_output=True)
            self.assertEqual(result.returncode, 0)
            backups = resources / ".usage-backups"
            backups.mkdir()
            (backups / "fixture.json").write_bytes(b"private synthetic history")
            result = subprocess.run(["bash", "-c", guard], env=environment, capture_output=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"Refusing to package user telemetry", result.stderr)

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
                zipped.writestr("python315._pth", "untrusted search path")
            checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
            with patch.object(BUILD, "PYTHON_SHA256", checksum), patch.object(BUILD.subprocess, "check_output", return_value="release-fixture\n"):
                BUILD.stage(ROOT, root / "payload", archive, "2.0.0")
            payload = root / "payload"
            # The bundled runtime is 3.15 (compression.zstd); it reads only python315._pth, app folder first.
            self.assertEqual(BUILD.PYTHON_TAG, "python315")
            self.assertEqual((payload / "runtime" / "python315._pth").read_text(), "..\npython315.zip\n.\n")
            self.assertEqual(sorted(p.name for p in (payload / "runtime").glob("*._pth")), ["python315._pth"])
            self.assertEqual(json.loads((payload / "BUILD-INFO.json").read_text())["python"], "3.15.2")
            self.assertEqual(json.loads((payload / "BUILD-INFO.json").read_text())["commit"], "release-fixture")
            self.assertTrue((payload / "windows" / "register_install.py").is_file())
            self.assertEqual((payload / "session_names.py").read_bytes(), (ROOT / "session_names.py").read_bytes())
            self.assertEqual((payload / "accounts.py").read_bytes(), (ROOT / "accounts.py").read_bytes())
            self.assertEqual((payload / "release_notes.py").read_bytes(), (ROOT / "release_notes.py").read_bytes())
            self.assertEqual((payload / "static" / "release-notes.js").read_bytes(),
                             (ROOT / "static" / "release-notes.js").read_bytes())
            for name in ("cursor_data.py", "ledger_backups.py", "session_timeline.py", "native_activity.py", "diagnostics.py", "editor_snapshot.py"):
                self.assertEqual((payload / name).read_bytes(), (ROOT / name).read_bytes())
            self.assertEqual((payload / "local_servers.py").read_bytes(), (ROOT / "local_servers.py").read_bytes())
            self.assertEqual((payload / "local_servers_windows.py").read_bytes(), (ROOT / "local_servers_windows.py").read_bytes())
            for icon in (ROOT / "static" / "tool-icons").glob("*.svg"):
                self.assertEqual((payload / "static" / "tool-icons" / icon.name).read_bytes(), icon.read_bytes())
            self.assertFalse(any(
                p.name in {".usage-backups", ".usage_cache.json", ".peers.json", ".peers", "server.log",
                           ".local_servers.json", "release-notes.json", "release-notes.lock"}
                or p.name.startswith((".local_servers.", ".release-notes-"))
                for p in payload.rglob("*")
            ))

    @unittest.skipUnless(hasattr(sys, "stdlib_module_names"), "needs Python 3.10+")
    def test_only_antigravity_shadows_a_standard_library_module(self):
        # The bundled runtime puts the app before the stdlib, so a module named like a stdlib one replaces it
        # for the whole app. Antigravity is the one deliberate case (and why the app must come first).
        names = {Path(name).stem for name in install.BACKEND_FILES if name.endswith(".py") and "/" not in name}
        self.assertIn("dashboard", names)
        self.assertEqual((names | {"windows"}) & set(sys.stdlib_module_names), {"antigravity"})

    def test_release_and_beta_tags_are_accepted_nothing_else(self):
        for tag in ("v2.2.0", "v2.2.0-beta.1", "v10.0.12-beta.30"):
            self.assertTrue(BUILD.RELEASE_TAG_RE.fullmatch(tag), tag)
        for tag in ("2.2.0", "v2.2", "v2.2.0-rc.1", "v2.2.0-beta", "v2.2.0-beta.1x", "v2.2.0+build", "beta"):
            self.assertIsNone(BUILD.RELEASE_TAG_RE.fullmatch(tag), tag)

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
