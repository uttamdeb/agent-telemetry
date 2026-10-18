"""Check the universal editor archive, without reading logs or user settings."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def check_archive(path):
    expected_backend = set(json.loads(subprocess.check_output(
        ["node", "-e", "process.stdout.write(JSON.stringify(require('./vscode/scripts/backend-files')))"],
        cwd=ROOT, text=True)))
    manifest = json.loads((ROOT / "vscode" / "package.json").read_text())
    with zipfile.ZipFile(path) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        assert "extension/package.json" in names
        packaged = json.loads(archive.read("extension/package.json"))
        assert packaged["publisher"] == manifest["publisher"]
        assert packaged["name"] == manifest["name"]
        assert packaged["version"] == manifest["version"]
        assert packaged["extensionKind"] == ["ui"]
        assert not packaged.get("dependencies"), "Editor runtime dependencies were added"
        assert "browser" not in packaged, "A local Node service cannot run in a browser extension host"
        # VSCE normalizes the documentation names in the archive.
        expected = {"extension/" + relative for relative in (
            "package.json", "readme.md", "LICENSE.txt", "changelog.md")}
        for archived, source in (("changelog.md", "CHANGELOG.md"),
                                 ("LICENSE.txt", "LICENSE")):
            assert archive.read("extension/" + archived) == (ROOT / "vscode" / source).read_bytes()
        readme = (ROOT / "vscode" / "README.md").read_text()
        readme = readme.replace('src="media/', 'src="https://github.com/uttamdeb/agent-telemetry/raw/HEAD/vscode/media/')
        readme = readme.replace('(LICENSE)', '(https://github.com/uttamdeb/agent-telemetry/blob/HEAD/vscode/LICENSE)')
        assert archive.read("extension/readme.md").decode() == readme
        for folder in ("src", "media"):
            expected.update("extension/" + str(item.relative_to(ROOT / "vscode")).replace("\\", "/")
                            for item in (ROOT / "vscode" / folder).rglob("*") if item.is_file())
        expected.update("extension/backend/" + relative for relative in expected_backend)
        expected.add("extension/backend/BUILD-INFO.json")
        # VSCE owns these two archive metadata files; everything else is allowlisted.
        assert names == expected | {"extension.vsixmanifest", "[Content_Types].xml"}, names ^ expected
        info = json.loads(archive.read("extension/backend/BUILD-INFO.json"))
        assert info["version"] == manifest["version"]
        assert set(info["files"]) == expected_backend
        for relative in expected_backend:
            data = archive.read("extension/backend/" + relative)
            assert hashlib.sha256(data).hexdigest() == info["files"][relative], relative
            assert data == (ROOT / relative).read_bytes(), relative
        for name in names:
            assert not any(part in {".usage_cache.json", ".peers.json", ".peers", ".usage-backups",
                                    ".local_servers.json", "server.log", "node_modules", ".git",
                                    "test", "tests", "__pycache__"} for part in name.split("/")), name
            assert archive.getinfo(name).file_size < 10_000_000, name
        assert sum(archive.getinfo(name).file_size for name in names) < 20_000_000
    print(f"Editor VSIX validated: {manifest['publisher']}.{manifest['name']} {manifest['version']}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python tests/check_editor_vsix.py dist/agenttelemetry-2.0.0.vsix")
    check_archive(Path(sys.argv[1]))
