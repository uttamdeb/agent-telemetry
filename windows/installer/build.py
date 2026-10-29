"""Build an offline, per-user EXE from a clean release checkout (Inno Setup required)."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

# CPython 3.15 includes compression.zstd, so the installed app reads Codex's compressed rollouts itself.
PYTHON_VERSION = "3.15.2"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_SHA256 = "d012f3aa29cf07436dae8aea1fec0fd195c2612a0b059b1f915ae0afb9ad1f62"
# The embeddable runtime reads only the ._pth named for its own version (python315._pth for 3.15).
PYTHON_TAG = "python" + "".join(PYTHON_VERSION.split(".")[:2])
HERE = Path(__file__).resolve().parent


def validate_source(source, tag, source_commit=None):
    """Keep the release tag intact; an explicit refresh must pin its descendant."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise ValueError("Expected a release tag such as v2.1.0.")
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(source), *arguments], text=True).strip()
    tagged = git("rev-parse", "--verify", "refs/tags/" + tag + "^{commit}")
    head = git("rev-parse", "HEAD")
    if source_commit:
        if not re.fullmatch(r"[0-9a-f]{40}", source_commit) or head != source_commit:
            raise ValueError("A refresh requires the full source commit checked out at HEAD.")
        if subprocess.run(["git", "-C", str(source), "merge-base", "--is-ancestor", tagged, head]).returncode:
            raise ValueError("The refreshed source must descend from the original release tag.")
    elif head != tagged:
        raise ValueError("The source must be checked out at the requested release tag.")
    if git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Refusing a modified release checkout.")
    return head


def stage(source, destination, archive, version):
    spec = importlib.util.spec_from_file_location("release_install", source / "install.py")
    installer = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(source))
    try:
        spec.loader.exec_module(installer)
        installer.copy_windows_payload(destination)
    finally:
        sys.path.pop(0)
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != PYTHON_SHA256:
        raise ValueError("Bundled Python checksum mismatch.")
    runtime = destination / "runtime"
    runtime.mkdir()
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            path = Path(entry.filename)
            if path.is_absolute() or len(path.parts) != 1 or path.name in ("..", "."):
                raise ValueError("Unexpected path in embedded Python archive.")
        zipped.extractall(runtime)
    # Isolate imports from machine/user Python; expose only stdlib and this app. The app comes FIRST, as a
    # script's own folder does elsewhere: a ._pth also stops Python adding the script's folder, so with the
    # stdlib first `python dashboard.py` got the stdlib "antigravity" (the xkcd joke, which opens a browser)
    # instead of the Antigravity parser, and the backend could not start.
    (runtime / f"{PYTHON_TAG}._pth").write_text(f"..\n{PYTHON_TAG}.zip\n.\n", encoding="utf-8")
    shutil.copy2(HERE / "register_install.py", destination / "windows" / "register_install.py")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    packaging_commit = subprocess.check_output(["git", "-C", str(HERE.parents[1]), "rev-parse", "HEAD"], text=True).strip()
    (destination / "BUILD-INFO.json").write_text(json.dumps({"version": version,
        "commit": commit, "packaging_commit": packaging_commit, "python": PYTHON_VERSION, "python_url": PYTHON_URL,
        "python_sha256": PYTHON_SHA256}, indent=2) + "\n", encoding="utf-8")
    assert (runtime / "pythonw.exe").is_file()
    forbidden = {".usage_cache.json", ".usage-backups", ".peers.json", ".peers", "server.log",
                 ".local_servers.json", "mcp-access.json", "mcp-access.lock", "project-names.json",
                 "release-notes.json", "release-notes.lock"}
    if any(path.name in forbidden or path.name.startswith((".local_servers.", ".mcp-access-", ".release-notes-"))
           for path in destination.rglob("*")):
        raise ValueError("Personal telemetry found in release payload.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--source-commit", help="Explicit full commit for a refreshed build; never moves the release tag")
    parser.add_argument("--compiler", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    try:
        validate_source(source, args.tag, args.source_commit)
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.error(str(error))
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="AgentTelemetry-package-") as temporary:
        root = Path(temporary)
        archive = root / "python.zip"
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response:
            archive.write_bytes(response.read())
        payload = root / "payload"
        stage(source, payload, archive, args.tag[1:])
        subprocess.run([str(args.compiler), "/Qp", "/DPayload=" + str(payload),
            "/DOutput=" + str(output), "/DAppVersion=" + args.tag[1:],
            str(HERE / "AgentTelemetry.iss")], check=True)
    print("Created " + str(output / ("AgentTelemetry-" + args.tag[1:] + "-windows-x64-setup.exe")))


if __name__ == "__main__":
    main()
