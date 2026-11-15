"""Finding Codex's command-line program where its apps install it (no PATH from a shell)."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native as N


class CodexLocatorTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-codex-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name)
        patcher = patch.dict(N._CODEX_FOUND, {"at": None, "path": None})
        patcher.start(); self.addCleanup(patcher.stop)

    def program(self, *parts):
        path = self.home.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
        return str(path)

    def test_macos_order_override_path_extensions_newest_first_then_the_chatgpt_app(self):
        older = self.program(".vscode", "extensions", "openai.chatgpt-26.5825.1-darwin-arm64", "bin", "macos-aarch64", "codex")
        newer = self.program(".vscode-insiders", "extensions", "openai.chatgpt-26.51111.2-darwin-arm64", "bin", "macos-aarch64", "codex")
        self.program(".cursor", "extensions", "unrelated.extension-1.0.0", "bin", "macos-aarch64", "codex")
        found = N.codex_candidates(env={"PATH": "/fixture/bin:", "CODEX_BIN": "/fixture/override"},
                                   home=str(self.home), system="darwin", machine="arm64")
        app = "Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        norm = lambda paths: [os.path.normpath(path) for path in paths]  # the test also runs on Windows
        self.assertEqual(norm(found[:5]), norm(["/fixture/override", "/fixture/bin/codex", newer, older, "/" + app]))
        self.assertIn(os.path.normpath(str(self.home / app)), norm(found))
        self.assertIn(os.path.normpath(str(self.home / ".local" / "bin" / "codex")), norm(found))
        self.assertFalse(any("unrelated" in path for path in found))
        intel = N.codex_candidates(env={}, home=str(self.home), system="darwin", machine="x86_64")
        self.assertIn(os.path.normpath(older.replace("macos-aarch64", "macos-x86_64")), norm(intel))

    def test_windows_uses_codex_exe_on_path_and_the_npm_platform_package(self):
        found = N.codex_candidates(env={"Path": r"C:\Tools;C:\npm", "APPDATA": r"C:\Users\u\AppData\Roaming"},
                                   home=r"C:\Users\u", system="win32", machine="AMD64")
        self.assertEqual(found[:2], [r"C:\Tools\codex.exe", r"C:\npm\codex.exe"])
        self.assertIn(r"C:\Users\u\AppData\Roaming\npm\node_modules\@openai\codex-win32-x64"
                      r"\vendor\x86_64-pc-windows-msvc\bin\codex.exe", found)
        self.assertFalse(any(path.endswith(".cmd") for path in found))

    def test_the_first_runnable_program_is_used_and_cached(self):
        program = self.program(".vscode", "extensions", "openai.chatgpt-1.2.3", "bin", "macos-aarch64", "codex")
        missing = str(self.home / "missing" / "codex")
        with patch.object(N, "codex_candidates", return_value=[missing, program]) as candidates:
            self.assertEqual(N.codex_executable(), program)
            self.assertEqual(N.codex_executable(), program)
            self.assertEqual(candidates.call_count, 1)
        os.remove(program)
        with patch.object(N, "codex_candidates", return_value=[program]):
            self.assertIsNone(N.codex_executable())


if __name__ == "__main__":
    unittest.main()
