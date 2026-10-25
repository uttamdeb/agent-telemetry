"""Validate built Python distributions in a disposable environment, without logs."""
import argparse
from email.parser import BytesParser
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile


MODULES = {
    "__init__.py", "__main__.py", "cli.py", "dashboard.py", "editor_snapshot.py", "parser.py", "antigravity.py",
    "native.py", "accounts.py", "session_names.py", "release_notes.py",
    "local_servers.py", "local_servers_windows.py",
    "ledger_backups.py", "diagnostics.py", "session_timeline.py", "native_activity.py",
    "mcp_access.py", "mcp_backend.py", "mcp_protocol.py", "mcp_server.py",
    "mcp_tools.py", "telemetry_query.py", "telemetry_insights.py",
}
ASSETS = {
    "index.html", "chart.umd.min.js", "manifest.json", "sw.js",
    "static/app.css", "static/core.js", "static/charts.js", "static/views.js",
    "static/insights.js", "static/maintenance.js", "static/timeline.js",
    "static/mcp.js",
    "static/release-notes.js",
    "static/apple-touch-icon.png", "static/icon-192.png", "static/icon-512.png",
    "static/tool-icons/claude.svg", "static/tool-icons/antigravity.svg", "static/tool-icons/copilot.svg",
    "static/tool-icons/cursor.svg", "static/tool-icons/hermes.svg",
    "static/tool-icons/openai.svg", "static/tool-icons/openclaw.svg",
    "static/tool-icons/opencode.svg", "static/tool-icons/README.md",
    "static/tool-icons/LICENSE.hermes.txt", "static/tool-icons/LICENSE.antigravity.txt", "static/tool-icons/LICENSE.openclaw.txt",
    "static/tool-icons/LICENSE.simple-icons.txt",
}
METADATA = {"PKG-INFO", "SOURCES.txt", "dependency_links.txt", "entry_points.txt", "top_level.txt"}


def check_archives(wheel, sdist):
    with zipfile.ZipFile(wheel) as archive:
        names = {name for name in archive.namelist() if not name.endswith("/")}
        payload = {name[len("agent_telemetry/"):] for name in names
                   if name.startswith("agent_telemetry/")}
        assert payload == MODULES | ASSETS, f"Unexpected wheel payload: {payload ^ (MODULES | ASSETS)}"
        metadata_names = names - {"agent_telemetry/" + name for name in payload}
        assert all(".dist-info/" in name for name in metadata_names), metadata_names
        allowed_metadata = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt", "licenses/LICENSE"}
        assert {name.split(".dist-info/", 1)[1] for name in metadata_names} <= allowed_metadata
        metadata = BytesParser().parsebytes(archive.read(next(name for name in names if name.endswith("/METADATA"))))
        assert metadata["Name"] == "agenttelemetry-app"
        assert not metadata.get_all("Requires-Dist"), "Runtime dependencies were added"
        entry_points = archive.read(next(name for name in names if name.endswith("/entry_points.txt"))).decode()
        for command in ("agenttelemetry-app", "agenttelemetry", "agent-telemetry"):
            assert command + " = agent_telemetry.cli:main" in entry_points
    with tarfile.open(sdist) as archive:
        paths = {str(PurePosixPath(member.name).relative_to(PurePosixPath(member.name).parts[0]))
                 for member in archive.getmembers() if member.isfile()}
        expected = MODULES | ASSETS | {
            "pyproject.toml", "setup.py", "setup.cfg", "MANIFEST.in", "README.md",
            "LICENSE", "PKG-INFO", "docs/python-package.md",
            "docs/mcp.md", "docs/release-notes.md", "docs/native-activity.md",
            "docs/antigravity.md",
        }
        extras = paths - expected
        assert all(path.startswith("agenttelemetry_app.egg-info/")
                   and path.split("/", 1)[1] in METADATA for path in extras), extras
        assert MODULES | ASSETS <= paths, "Source distribution is missing runtime files"
    return metadata["Version"]


SMOKE = r'''
import contextlib
import http.client
import json
import io
from pathlib import Path
import sys
import threading
from agent_telemetry import cli
sys.argv = ["agenttelemetry", "--help"]
with contextlib.redirect_stdout(io.StringIO()):
    try:
        cli.main()
    except SystemExit as error:
        assert error.code == 0
from agent_telemetry import dashboard as D
from agent_telemetry import parser as P, native as N, accounts as A, session_names as SN, release_notes as RN
from agent_telemetry import native_activity as NA
from agent_telemetry import __version__
from importlib.metadata import version
assert version("agenttelemetry-app") == EXPECTED_VERSION
assert __version__ == EXPECTED_VERSION.split(".post", 1)[0]
assert D.P is P and D.N is N and D.A is A and D.SN is SN and D.NA is NA and D.RN is RN
assert P.A is A and A.N is N and SN.N is N and RN.native is N
assert D.VERSION == {"git": False, "describe": __version__}, D.VERSION
assert not Path(D.CACHE_PATH).exists()
# Serve the installed resources directly; never discover or parse tool logs.
server = D.Server(("127.0.0.1", 0), D.Handler)
D.BIND.update(host="127.0.0.1", port=server.server_port)
worker = threading.Thread(target=server.serve_forever, daemon=True)
worker.start()
try:
    for path in RESOURCE_PATHS:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        connection.request("GET", path)
        response = connection.getresponse()
        assert response.status == 200, (path, response.status)
        body = response.read()
        assert body, path
        if path == "/api/native-activity":
            activity = json.loads(body)
            assert set(activity) == {"observed_at", "window_seconds", "sessions", "total"}
            assert activity["window_seconds"] == 900 and activity["sessions"] == []
        connection.close()
finally:
    server.shutdown()
    server.server_close()
    worker.join(timeout=5)
assert not Path(D.CACHE_PATH).exists()
print("Installed imports and HTTP resources passed")
'''


def check_install(wheel, version):
    with tempfile.TemporaryDirectory(prefix="agenttelemetry-package-check-") as directory:
        root = Path(directory)
        home = root / "home"
        home.mkdir()
        environment = os.environ.copy()
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            environment[name] = str(home)
        environment.pop("PYTHONPATH", None)
        environment.pop("AGENT_TELEMETRY_VERSION", None)
        target = root / "venv"
        venv.EnvBuilder(with_pip=True).create(target)
        scripts = target / ("Scripts" if os.name == "nt" else "bin")
        python = scripts / ("python.exe" if os.name == "nt" else "python")
        # Package-install tooling can create its own cache/config directories.
        # Keep those outside the application's empty mock home so the no-write
        # check measures AgentTelemetry, not whichever pip ships with the runner.
        installer_home = root / "installer-home"
        installer_home.mkdir()
        installer_environment = environment.copy()
        for name in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
            installer_environment[name] = str(installer_home)
        subprocess.run([str(python), "-m", "pip", "--isolated", "--disable-pip-version-check",
                        "--no-cache-dir", "install", "--no-index", "--no-deps", str(wheel)],
                       check=True, cwd=root, env=installer_environment, capture_output=True, text=True)

        def assert_home_empty(stage):
            paths = sorted(str(path.relative_to(home)) for path in home.rglob("*"))
            assert not paths, f"{stage} wrote mock user state: {paths}"

        assert_home_empty("Package installation")
        for command in ("agenttelemetry-app", "agenttelemetry", "agent-telemetry"):
            executable = scripts / (command + (".exe" if os.name == "nt" else ""))
            result = subprocess.run([str(executable), "--help"], check=True, cwd=root,
                                    env=environment, capture_output=True, text=True)
            assert "--data-dir" in result.stdout and "--port" in result.stdout
            assert_home_empty(command + " --help")
        subprocess.run([str(python), "-m", "agent_telemetry", "--help"], check=True,
                       cwd=root, env=environment, capture_output=True, text=True)
        assert_home_empty("python -m agent_telemetry --help")
        for arguments in (("--help",), ("--version",)):
            result = subprocess.run([str(python), "-m", "agent_telemetry.mcp_server", *arguments],
                                    cwd=root, env=environment, capture_output=True, text=True)
            assert result.returncode == 0, (arguments, result.stderr)
            assert ("AgentTelemetry MCP server" in result.stdout if arguments == ("--version",)
                    else "--backend" in result.stdout)
            assert_home_empty("python -m agent_telemetry.mcp_server " + " ".join(arguments))
        paths = ["/", "/chart.js", "/manifest.json", "/sw.js", "/api/health",
                 "/api/release-notes", "/api/native-activity"]
        paths += ["/" + asset for asset in sorted(ASSETS) if asset.startswith("static/")]
        code = "EXPECTED_VERSION = " + repr(version) + "\nRESOURCE_PATHS = " + repr(paths) + "\n" + SMOKE
        result = subprocess.run([str(python), "-c", code], cwd=root,
                                env=environment, capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        assert_home_empty("Installed imports and HTTP resources")
        print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel path, or directory containing one wheel and sdist")
    parser.add_argument("sdist", type=Path, nargs="?")
    args = parser.parse_args()
    wheel, sdist = args.wheel, args.sdist
    if wheel.is_dir() and sdist is None:
        wheels, sources = list(wheel.glob("*.whl")), list(wheel.glob("*.tar.gz"))
        if len(wheels) != 1 or len(sources) != 1:
            parser.error("the distribution directory must contain exactly one wheel and one sdist")
        wheel, sdist = wheels[0], sources[0]
    elif sdist is None:
        parser.error("provide both a wheel and sdist, or one distribution directory")
    version = check_archives(wheel, sdist)
    check_install(wheel.resolve(), version)
    print("Python wheel and sdist passed; no personal logs were read")


if __name__ == "__main__":
    main()
