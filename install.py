"""Install the matching native client for this OS; preserves all usage history."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import native

ROOT = Path(__file__).resolve().parent
BACKEND_FILES = ("dashboard.py", "parser.py", "native.py", "index.html", "chart.umd.min.js",
                 "manifest.json", "sw.js", "LICENSE")


def copy_windows_payload(destination):
    """Explicit allowlist: no checkout cache, logs, credentials or mirrors."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    for name in BACKEND_FILES:
        shutil.copy2(str(ROOT / name), str(destination / name))
    shutil.copytree(str(ROOT / "static"), str(destination / "static"))
    (destination / "windows").mkdir()
    for name in ("tray.py", "monitor.py"):
        shutil.copy2(str(ROOT / "windows" / name), str(destination / "windows" / name))
    forbidden = {".usage_cache.json", ".peers.json", ".peers", "server.log"}
    if any(path.name in forbidden for path in destination.rglob("*")):
        raise ValueError("Refusing to install a payload containing personal telemetry.")


def install_windows():
    base_python = Path(getattr(sys, "_base_executable", sys.executable))
    pythonw = base_python.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError("Use a standard Python 3.8+ Windows installation that includes pythonw.exe.")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    target = base / "Programs" / "AgentTelemetry"
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".AgentTelemetry-install-", dir=str(target.parent)))
    backup = target.with_name("AgentTelemetry.previous")
    if backup.exists():
        raise ValueError("A previous installer backup exists. Keep it safe and rename it before retrying.")
    moved = False
    try:
        copy_windows_payload(stage)
        if target.exists():
            target.rename(backup)
            moved = True
        stage.rename(target)
        if moved:
            shutil.rmtree(str(backup))
    except Exception:
        if moved and backup.exists() and not target.exists():
            backup.rename(target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(str(stage))
    entry = target / "windows" / "tray.py"
    # Paths travel via environment variables, never interpolated PowerShell code.
    script = ("$w = New-Object -ComObject WScript.Shell; "
        "$p = [Environment]::GetFolderPath('Programs'); "
        "$s = $w.CreateShortcut((Join-Path $p 'AgentTelemetry.lnk')); "
        "$s.TargetPath = $env:AT_INSTALL_PYTHONW; "
        "$s.Arguments = $env:AT_INSTALL_ARGUMENTS; "
        "$s.WorkingDirectory = $env:AT_INSTALL_DIRECTORY; $s.Save()")
    environment = dict(os.environ, AT_INSTALL_PYTHONW=str(pythonw),
        AT_INSTALL_ARGUMENTS=subprocess.list2cmdline([str(entry)]), AT_INSTALL_DIRECTORY=str(target))
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   check=True, env=environment)
    return {"platform": "win32", "entry": str(entry), "command": [str(pythonw), str(entry)], "version": "2.0.0"}


def install_macos(bundle=None):
    if bundle is None:
        script = ROOT / "macos" / "AgentTelemetryMac" / "Scripts" / "package-app.sh"
        subprocess.run([str(script)], check=True)
        bundle = ROOT / "macos" / "AgentTelemetryMac" / "build" / "AgentTelemetry.app"
    bundle = Path(bundle).resolve()
    if not (bundle / "Contents" / "MacOS" / "AgentTelemetryMac").is_file():
        raise ValueError("Choose a built AgentTelemetry.app bundle.")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)], check=True)
    applications = Path.home() / "Applications"
    applications.mkdir(exist_ok=True)
    target = applications / "AgentTelemetry.app"
    with tempfile.TemporaryDirectory(prefix=".AgentTelemetry-install-", dir=str(applications)) as temp:
        staged = Path(temp) / "AgentTelemetry.app"
        subprocess.run(["/usr/bin/ditto", str(bundle), str(staged)], check=True)
        old = Path(temp) / "previous.app"
        if target.exists():
            target.rename(old)
        try:
            staged.rename(target)
        except OSError:
            if old.exists():
                old.rename(target)
            raise
    return {"platform": "darwin", "entry": str(target),
            "command": ["/usr/bin/open", "-a", str(target), "--args"], "version": "2.0.0"}


def main():
    arguments = argparse.ArgumentParser(description=__doc__)
    arguments.add_argument("--no-launch", action="store_true", help="install without opening the native app")
    arguments.add_argument("--bundle", help="install an already built macOS .app instead of building it")
    options = arguments.parse_args()
    if native.status()["running"]:
        arguments.error("Quit the running native app before updating its installed files.")
    try:
        if sys.platform == "darwin":
            manifest = install_macos(options.bundle)
        elif sys.platform == "win32":
            if options.bundle:
                raise ValueError("--bundle is a macOS option.")
            manifest = install_windows()
        else:
            raise ValueError("Native clients support macOS and Windows. Run python3 dashboard.py on this OS.")
        native._write("native-install.json", manifest)
        print("Installed AgentTelemetry for " + ("macOS" if sys.platform == "darwin" else "Windows") + ".")
        print("The web Settings toggle can now launch or quit it. Open at Login stays opt-in.")
        if not options.no_launch:
            native.action(True)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        arguments.error(str(error))


if __name__ == "__main__":
    main()
