"""Build an offline, per-user EXE from a clean release checkout (Inno Setup required)."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

PYTHON_URL = "https://www.python.org/ftp/python/3.13.16/python-3.13.16-embed-amd64.zip"
PYTHON_SHA256 = "97dae5274cc54867065e8d5a3226e48c35017ed332a0fdb0e27d5b5821961297"
HERE = Path(__file__).resolve().parent


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
    # Isolate imports from machine/user Python; expose only stdlib and this app.
    (runtime / "python313._pth").write_text("python313.zip\n.\n..\n", encoding="utf-8")
    shutil.copy2(HERE / "register_install.py", destination / "windows" / "register_install.py")
    commit = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    packaging_commit = subprocess.check_output(["git", "-C", str(HERE.parents[1]), "rev-parse", "HEAD"], text=True).strip()
    (destination / "BUILD-INFO.json").write_text(json.dumps({"version": version,
        "commit": commit, "packaging_commit": packaging_commit, "python": "3.13.16", "python_url": PYTHON_URL,
        "python_sha256": PYTHON_SHA256}, indent=2) + "\n", encoding="utf-8")
    assert (runtime / "pythonw.exe").is_file()
    forbidden = {".usage_cache.json", ".usage-backups", ".peers.json", ".peers", "server.log"}
    if any(path.name in forbidden for path in destination.rglob("*")):
        raise ValueError("Personal telemetry found in release payload.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--compiler", type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    actual = subprocess.check_output(["git", "-C", str(source), "describe", "--tags", "--exact-match"], text=True).strip()
    if actual != args.tag or not args.tag.startswith("v") or any(c not in "0123456789.v" for c in args.tag):
        parser.error("The source must be checked out at the requested release tag.")
    if subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        parser.error("Refusing a modified release checkout.")
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
