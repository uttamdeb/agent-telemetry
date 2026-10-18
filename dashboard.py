#!/usr/bin/env python3
"""
dashboard.py — Live local usage analytics for your AI coding tools.

Covers Claude Code, Claude Desktop, Codex, GitHub Copilot, Cursor, opencode and Hermes Agent.
Parses your local interaction logs (no data leaves the machine unless you turn on device
sharing in Settings), aggregates usage by day / model / tool / project / hour, and serves
an interactive dashboard.

    python3 dashboard.py            # serve at http://127.0.0.1:7878
    python3 dashboard.py --port 9000
    python3 dashboard.py --rebuild  # ignore cache, full re-parse

Stdlib only. First run parses everything (one large Codex log makes that take a
moment); results are cached, and subsequent refreshes are incremental & instant.
"""
import os, re, sys, json, time, glob, threading, argparse, shutil, mimetypes, platform, subprocess, copy
import gzip, hmac, io, ipaddress, secrets, socket, uuid, signal, urllib.parse, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__:
    from . import parser as P
    from . import native as N
    from . import session_names as SN
    from . import accounts as A
    from . import ledger_backups as LB
    from . import diagnostics as DG
    from . import session_timeline as ST
    from . import editor_snapshot as ES
    from .local_servers import LocalServerInventory
else:
    # Source checkouts and native app payloads retain their top-level scripts.
    import parser as P
    import native as N
    import session_names as SN
    import accounts as A
    import ledger_backups as LB
    import diagnostics as DG
    import session_timeline as ST
    import editor_snapshot as ES
    from local_servers import LocalServerInventory

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = HERE
CACHE_PATH = os.path.join(DATA_DIR, ".usage_cache.json")
CACHE_VERSION = 60


def _configure_data_dir(path):
    """Keep mutable app state outside a packaged, potentially read-only bundle."""
    global DATA_DIR, CACHE_PATH, PEERS_PATH, PEER_DIR
    DATA_DIR = os.path.abspath(os.path.expanduser(path or HERE))
    os.makedirs(DATA_DIR, exist_ok=True)
    CACHE_PATH = os.path.join(DATA_DIR, ".usage_cache.json")
    PEERS_PATH = os.path.join(DATA_DIR, ".peers.json")
    PEER_DIR = os.path.join(DATA_DIR, ".peers")


def _peer_cache_ok(version):
    # v1.7.0 mirrors lack optional attribution/precision fields, but the reader
    # still supports their rows just as it supports archived local entries.
    return type(version) is int and version in (52, 53, 54, 55, 56, 57, 58, 59, CACHE_VERSION)

# ---------------------------------------------------------------------------
# In-memory store of per-file aggregates, refreshed on a background interval.
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state = {"files": {}, "version": CACHE_VERSION}
_cache_error = {"v": None}
_meta = {"last_refresh": 0.0, "last_duration": 0.0, "files": 0, "building": False}
_backup_day = {"key": None}
_restore_tickets = {}
_refresh_sync_lock = threading.Lock()
_sync_manual = 0
_sync_start = uuid.uuid4().hex
_client_snapshot_lock = threading.Lock()
_client_snapshot = {"revision": None, "data": None, "summary": None}


def refresh_sync():
    """A small shared clock, without analytics or ledger writes.

    All local clients sample it once a second and fetch figures on the same
    clock tick. Manual refresh invalidates the tick for every open client.
    Log parsing retains its own --interval cadence.
    """
    seconds = N.refresh_interval()
    tick = int(time.time() // seconds) if seconds else 0
    with _refresh_sync_lock:
        revision = f"{_sync_start}:{seconds}:{tick}:{_sync_manual}"
    return {"seconds": seconds, "revision": revision}


def notify_refresh():
    global _sync_manual
    with _refresh_sync_lock:
        _sync_manual += 1


def _windows_os_label():
    release = platform.release()
    label = f"Windows {release}".strip()
    if release != "10":
        return label
    # Older Python releases call Windows 11 "10". Its first client build is
    # 22000; Server shares the 10.0 version/build family, so exclude it explicitly.
    try:
        if sys.getwindowsversion().product_type != 1:
            return label
        major, minor, build = map(int, platform.win32_ver()[1].split(".")[:3])
    except (AttributeError, OSError, TypeError, ValueError):
        return label
    return "Windows 11" if (major, minor) == (10, 0) and build >= 22000 else label


def _device():
    """The name this machine goes by in its own OS (System Settings > Sharing on a Mac),
    falling back to the bare hostname, plus a short OS label."""
    name, osl = "", ""
    if sys.platform == "darwin":
        try:
            name = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True,
                                  text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            sys.stderr.write(f"[device] scutil failed: {e}\n")
        ver = platform.mac_ver()[0]
        osl = f"macOS {ver}" if ver else "macOS"
    elif os.name == "nt":
        name = os.environ.get("COMPUTERNAME", "")
        osl = _windows_os_label()
    else:
        for path, key in (("/etc/machine-info", "PRETTY_HOSTNAME="), ("/etc/os-release", "PRETTY_NAME=")):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if line.startswith(key):
                            val = line.split("=", 1)[1].strip().strip('"')
                            if key.startswith("PRETTY_HOSTNAME"):
                                name = val
                            else:
                                osl = val
            except OSError:
                pass
        osl = osl or "Linux"
    host = platform.node()
    return {"name": name or host.split(".")[0] or "This computer", "host": host, "os": osl}


DEVICE = _device()

# ---------------------------------------------------------------------------
# Version + self-update. The version comes from this checkout's git metadata.
# Apart from opt-in device sharing (see Devices below), checking for an update is the
# only thing here that touches the network, and it runs only when the user clicks
# "Check for updates" — never on a timer.
# ---------------------------------------------------------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _git(*args, timeout=10):
    r = subprocess.run(["git", "-C", APP_DIR, *args], capture_output=True, text=True,
                       timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"git {args[0]} failed")
    return r.stdout.strip()


def _version():
    packaged_version = os.environ.get("AGENT_TELEMETRY_VERSION")
    if packaged_version:
        return {"git": False, "describe": packaged_version}
    try:
        return {"git": True, "commit": _git("rev-parse", "--short", "HEAD"),
                "date": _git("log", "-1", "--format=%cs"),
                "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                # "v1.4.2" on a release, "v1.4.2-7-g0bb26d6" seven commits past it
                "describe": (subprocess.run(["git", "-C", APP_DIR, "describe", "--tags"],
                                            capture_output=True, text=True, timeout=5).stdout.strip()
                             or None)}
    except (OSError, subprocess.SubprocessError, RuntimeError) as e:
        sys.stderr.write(f"[version] not a git checkout: {e}\n")
        return {"git": False}


VERSION = _version()


def _upstream():
    """What to update from: this branch's upstream while the remote still has it, else
    the remote's default branch. A feature branch that was merged and then deleted on
    the remote keeps its local tracking ref (a plain fetch never prunes), which would
    read "up to date" forever. Asks the remote, so it runs only from update_action."""
    try:
        up = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    except RuntimeError:
        up = None
    remote = up.split("/", 1)[0] if up else "origin"
    if up and _git("ls-remote", "--heads", remote, "refs/heads/" + up.split("/", 1)[1], timeout=30):
        return up
    m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$",
                  _git("ls-remote", "--symref", remote, "HEAD", timeout=30), re.M)
    return f"{remote}/{m.group(1) if m else 'main'}"


def update_action(action):
    """check: fetch and report how far behind this checkout is.
    apply: fast-forward to it (never a merge, never over local edits), then restart."""
    if not VERSION.get("git"):
        raise ValueError("This copy isn't a git checkout — download the latest release instead.")
    remote = "origin"
    try:
        up = _upstream()
        remote = up.split("/", 1)[0]
        _git("fetch", "--quiet", "--tags", remote, timeout=30)
        # re-read: a release can tag a commit this checkout already has, which is
        # "up to date", so nothing restarts, and the label would name the old tag
        VERSION.update(_version())
        behind = int(_git("rev-list", "--count", f"HEAD..{up}") or 0)
        ahead = int(_git("rev-list", "--count", f"{up}..HEAD") or 0)
        log = _git("log", "--format=%h %cs %s", "-n", "8", f"HEAD..{up}") if behind else ""
    except (RuntimeError, subprocess.SubprocessError) as e:
        raise ValueError(f"Couldn't reach {remote}: {e}")
    info = {"upstream": up, "behind": behind, "ahead": ahead,
            "changes": [ln for ln in log.splitlines() if ln], "current": VERSION}
    if action == "check":
        return info
    if action != "apply":
        raise ValueError("unknown action")
    if not behind:
        return dict(info, updated=False)
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("This checkout has local changes; commit or stash them, then update.")
    if ahead:
        raise ValueError(f"This checkout has {ahead} commit(s) not on {up}; update it with git.")
    with _refresh_lock:
        _preserve_ledger_locked()
    try:
        _git("merge", "--ff-only", "--quiet", up, timeout=30)
    except RuntimeError as e:
        raise ValueError(f"Update failed: {e}")
    threading.Thread(target=_restart, daemon=True).start()
    return dict(info, updated=True, restarting=True)


def _restart():
    """Re-exec this process on the new code once the response has gone out."""
    time.sleep(0.8)
    try:
        with _refresh_lock, _lock:
            if not save_cache():
                sys.stderr.write("[update] restart deferred until the ledger can be saved\n")
                return
    except Exception as e:
        sys.stderr.write(f"[update] cache save before restart failed: {e}\n")
        return
    sys.stderr.write("[update] restarting on the new version\n")
    # never carry --rebuild over: it deletes the cache just saved, archived history
    # included (./run.sh --rebuild once, then Update, would wipe the ledger)
    # (argparse also takes any prefix of it, down to --r)
    argv = [a for a in sys.argv if not (len(a) > 2 and "--rebuild".startswith(a))]
    os.execv(sys.executable, [sys.executable, *argv])

_dirty = {"v": True}          # a log was added, replaced or archived: save the cache now
_grew = {"v": False, "saved": 0.0}   # a log was appended to since the last save
CACHE_SAVE_EVERY = 300
_gen = {"v": 0}               # bumped whenever the local data changes (the export's ETag)


def _parse_mark(agg):
    """Where update_file got to in a log: it moves whenever the log was read further."""
    return agg and (agg.get("size"), agg.get("mtime"), agg.get("offset"))
# Only one refresh at a time: the background timer and the Rebuild button can now
# collide, and `gone = [p for p in files ...]` iterating while another thread
# inserts raises "dictionary changed size during iteration".
_refresh_lock = threading.Lock()


def load_cache():
    if not os.path.exists(CACHE_PATH):
        return
    try:
        with open(CACHE_PATH) as f:
            data = json.load(f)
        if (not isinstance(data, dict) or not isinstance(data.get("files"), dict)
                or any(not isinstance(a, dict) for a in data["files"].values())):
            raise ValueError("invalid ledger structure")
    except Exception as e:
        _cache_error["v"] = "Ledger unreadable; original preserved. Restore a backup and restart."
        sys.stderr.write(f"[cache] {type(e).__name__}: {e}; preserving {CACHE_PATH}\n")
        return
    files = data.get("files", {})
    _cache_error["v"] = None
    if data.get("version") == CACHE_VERSION:
        _state["files"] = files
        return
    if data.get("version") in (53, 54, 55, 56, 57, 58, 59):
        # Only optional compaction/pricing/account/SDK/effort metadata changed. Keep every entry
        # until its replacement parses successfully; update_file backfills Claude
        # and Codex logs lacking metadata, and Claude Haiku 5.5 pricing detail.
        _state["files"] = files
        _dirty["v"] = True
        sys.stderr.write("[cache] backfilling optional metadata; existing ledger retained\n")
        return
    # Version changed, so the shape may have. Live logs are simply re-parsed from
    # disk — but ARCHIVED entries CANNOT be: their source files are gone. Dropping
    # them would silently destroy history the Storage tab explicitly promises is
    # safe to delete ("the dashboard keeps every session it has already parsed").
    # Keep them: newer fields are read with .get() defaults everywhere, so an
    # old-shape archived entry degrades rather than breaks.
    kept = {p: a for p, a in files.items() if a.get("archived")}
    if kept:
        _state["files"] = kept
        sys.stderr.write(
            f"[cache] version {data.get('version')} -> {CACHE_VERSION}: re-parsing live "
            f"logs, retained {len(kept)} archived session(s) that cannot be re-read\n")


def save_cache():
    if _cache_error["v"]:
        return False
    try:
        _scheduled_backup_locked()
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CACHE_PATH)
        _scheduled_backup_locked()
        _meta.pop("cache_error", None)
        return True
    except Exception as e:
        _meta["cache_error"] = "Ledger save failed; will retry: " + str(e)
        sys.stderr.write(f"[cache] save failed: {e}\n")
        return False


def refresh(verbose=False):
    """Scan all sources and incrementally update changed files."""
    with _refresh_lock:
        return _refresh_locked(verbose)


def _refresh_locked(verbose=False):
    t0 = time.time()
    _meta["building"] = True
    proj_map = P._copilot_project_map()
    try:
        found = P.discover()
    except Exception as error:
        DG.begin_scan([])
        DG.record_failure("discovery", "", error)
        DG.finish_scan()
        _meta["building"] = False
        raise
    DG.begin_scan(source for source, _, _ in found)
    files = _state["files"]
    seen = set()
    changed = False
    for i, (source, path, editor) in enumerate(found):
        seen.add(path)
        prev = files.get(path)
        # Claude, Codex and Copilot logs are appended to and parsed in place: the
        # same dict comes back, grown. So compare where the parse stopped, not just
        # identity, which missed every appended turn: a connected device's pulls
        # stayed "unchanged" for as long as a session kept running.
        mark = _parse_mark(prev)
        try:
            updated = P.update_file(prev, source, path, editor, proj_map)
            if updated is None:                # disappeared between discover and stat
                continue
            if updated is not prev:
                changed = _dirty["v"] = True
            elif mark != _parse_mark(updated):
                changed = _grew["v"] = True
            files[path] = updated
            DG.record_success(source, path)
        except Exception as e:
            # A failed full reparse must not replace the last valid ledger entry
            # with an empty aggregate or advance its signature. Retry next time.
            sys.stderr.write(f"[parse:{source}] {path}: {type(e).__name__}: {e}\n")
            DG.record_failure(source, path, e)
        if verbose and (i % 5 == 0 or i == len(found) - 1):
            sys.stderr.write(f"\r[scan] {i+1}/{len(found)} files...")
            sys.stderr.flush()
    # Durable ledger: do NOT drop files that disappeared from disk. Claude Code &
    # Codex prune old transcripts (default 30-day retention), but once we've parsed
    # a session its aggregates stay counted here forever. Mark them archived so the
    # UI can distinguish them; their cost/tokens continue to contribute to totals.
    gone = [p for p in files if p not in seen]
    for p in gone:
        if not files[p].get("archived"):
            changed = _dirty["v"] = True
        files[p]["archived"] = True
    if verbose and gone:
        sys.stderr.write(f"\n[ledger] retaining {len(gone)} pruned-from-disk session(s)\n")
    if verbose:
        sys.stderr.write("\n")
    _meta.update(last_refresh=time.time(), last_duration=time.time() - t0,
                 files=len(files), building=False)
    DG.finish_scan()
    _meta["parse_errors"] = DG.get_scan_report()["errors"]
    if changed:
        _gen["v"] += 1                # a connected device's next pull gets the new data
    # The cache is ~1MB; rewriting it every interval when nothing changed is a
    # pointless few GB of disk writes a day (and this machine is short on space).
    # A log that only grew is saved at most every CACHE_SAVE_EVERY: until then it is
    # re-read from the saved offset on a restart, so nothing is lost meanwhile.
    if _dirty["v"] or (_grew["v"] and time.time() - _grew["saved"] >= CACHE_SAVE_EVERY):
        if save_cache():
            _dirty["v"] = _grew["v"] = False
            _grew["saved"] = time.time()


def _scheduled_backup_locked():
    """At most one on-disk snapshot per UTC day, without delaying normal saves on failure."""
    key = (CACHE_PATH, time.strftime("%Y-%m-%d", time.gmtime()))
    if _backup_day["key"] == key or not os.path.isfile(CACHE_PATH):
        return
    try:
        item = LB.create_backup(CACHE_PATH, expected_version=CACHE_VERSION)
        if item is None or item.get("valid"):
            _backup_day["key"] = key
        _meta.pop("backup_error", None)
    except (OSError, ValueError):
        _meta["backup_error"] = "Automatic backup failed. Check free space and permissions in Settings."
        sys.stderr.write("[backup] automatic backup failed; normal ledger saving continues\n")


def _preserve_ledger_locked():
    """A failed safeguard blocks a destructive action, never the parser's normal saves."""
    if not _cache_error["v"] and not save_cache():
        raise ValueError("The current ledger could not be saved. Retry after checking free space and permissions.")
    try:
        return LB.create_backup(CACHE_PATH, reason="manual", force=True, expected_version=CACHE_VERSION)
    except (OSError, ValueError) as error:
        raise ValueError("A safety backup could not be created. Check free space and permissions before retrying.") from error


def _ledger_summary_locked():
    files = _state["files"]
    return {"files": len(files), "sessions": sum(len(a.get("sessions") or []) for a in files.values()),
            "archived": sum(bool(a.get("archived")) for a in files.values())}


def backup_status():
    with _refresh_lock:
        try:
            return {"backups": LB.list_backups(CACHE_PATH, expected_version=CACHE_VERSION),
                    "error": _meta.get("backup_error")}
        except (OSError, ValueError):
            return {"backups": [], "error": "Backups could not be read. Check directory permissions."}


def backup_action(body):
    with _refresh_lock:
        action = body.get("action")
        if action == "create":
            item = _preserve_ledger_locked()
            if item is None:
                raise ValueError("There is no saved ledger to back up yet.")
            return {"ok": True, "backup": item}
        if action == "preview":
            if not _cache_error["v"] and not save_cache():
                raise ValueError("The current ledger could not be saved. Resolve the save error before restoring.")
            preview = LB.preview_restore(CACHE_PATH, body.get("id"), expected_version=CACHE_VERSION)
            now = time.monotonic()
            for key in list(_restore_tickets):
                if _restore_tickets[key]["expires"] <= now:
                    del _restore_tickets[key]
            if len(_restore_tickets) >= 20:
                _restore_tickets.pop(next(iter(_restore_tickets)))
            ticket = secrets.token_urlsafe(32)
            _restore_tickets[ticket] = {"expires": now + 300, "generation": _gen["v"], "preview": preview}
            summary = preview["summary"]
            metadata = next((b for b in LB.list_backups(CACHE_PATH, expected_version=CACHE_VERSION)
                             if b["id"] == preview["backup_id"]), {})
            return {"ticket": ticket, "backup": dict(metadata, sessions=summary["sessions"]),
                    "current": _ledger_summary_locked(),
                    "warning": "Restoring replaces the current ledger, including archived history. A safety copy of the current ledger is saved first."}
        if action != "restore":
            raise ValueError("Choose create, preview or restore.")
        if body.get("confirmed") is not True:
            raise ValueError("Review and confirm the restore preview first.")
        ticket = body.get("ticket")
        if not isinstance(ticket, str):
            raise ValueError("Review a fresh restore preview first.")
        saved = _restore_tickets.pop(ticket, None)
        if saved is None or saved["expires"] <= time.monotonic():
            raise ValueError("The restore preview expired or was already used. Review a fresh preview.")
        if saved["generation"] != _gen["v"]:
            raise ValueError("Usage changed after the preview. Review a fresh preview before restoring.")
        preview = saved["preview"]
        LB.restore_backup(CACHE_PATH, preview["backup_id"], preview["sha256"],
                          preview["current_sha256"], expected_version=CACHE_VERSION)
        # The validated restore is an explicit request to retain all its entries,
        # including older live entries. Routine load_cache version migration can
        # intentionally drop old live entries, so do not route a restore through it.
        with open(CACHE_PATH, encoding="utf-8") as stream:
            restored = json.load(stream)
        _state.clear()
        _state.update(files=restored["files"], version=CACHE_VERSION)
        _cache_error["v"] = None
        _meta.pop("cache_error", None)
        _gen["v"] += 1
        _dirty["v"] = True
        _grew["v"] = False
        _meta.update(files=len(_state["files"]), building=False)
        _restore_tickets.clear()
        _peer["export"] = None
    notify_refresh()
    return {"ok": True}


def build_diagnostics():
    with _refresh_lock:
        report = DG.get_scan_report()
        present = {row["source"] for row in report["sources"]}
        for source in sorted({a.get("source") for a in _state["files"].values() if a.get("source")} - present):
            report["sources"].append({"source": source, "status": "retained_only", "discovered": 0,
                                      "read": 0, "errors": 0, "last_success": None,
                                      "hint": "Only retained history is available. New figures need readable source logs."})
        if _cache_error["v"]:
            report["ledger"] = {"status": "Unreadable", "hint": "Original preserved. Restore a valid backup below."}
        elif _meta.get("cache_error"):
            report["ledger"] = {"status": "Save failed", "hint": "Free disk space and check permissions, then refresh. In-memory figures are retained."}
        else:
            report["ledger"] = {"status": "Available"}
        if _meta.get("backup_error"):
            report["backup_error"] = _meta["backup_error"]
        missing = set()
        for aggregate in _state["files"].values():
            for _, bucket in _record_buckets(aggregate):
                for key, row in bucket.items():
                    date, model = key.split("\t", 1)
                    if model != "(user)" and any(row.get(f) for f in ("in", "out", "cr", "cc")) and not any(P.price_of(model, date)):
                        if aggregate["source"] not in ("opencode", "openclaw") or not row.get("cost"):
                            missing.add(model)
        report["missing_prices"] = sorted(missing)[:100]
        return report


def build_session_timeline(device, source, session_id, date_from=None, date_to=None):
    """Resolve a full native id from this ledger, never a caller-supplied filename."""
    if source not in ("claude", "codex", "claude-desktop") or not isinstance(session_id, str) or not 0 < len(session_id) <= 512:
        raise ValueError("Select a supported session with its full native identity.")
    if device != _local_id():
        return {"available": False, "events": [], "truncated": False, "detail": "Source events are available on the original device."}
    with _refresh_lock:
        selected = []
        for aggregate in _state["files"].values():
            if aggregate.get("source") != source or aggregate.get("archived"):
                continue
            if any(SN.identity(aggregate, s) == session_id for s in aggregate.get("sessions", [])):
                selected.append(aggregate.get("path", ""))
        if not selected:
            return {"available": False, "events": [], "truncated": False, "detail": "The source log is unavailable; retained daily detail is unchanged."}
        known = {(s, os.path.realpath(p)) for s, p, _ in P.discover()}
        verified = {os.path.realpath(path): path for path in selected
                    if (source, os.path.realpath(path)) in known}
        if not verified:
            return {"available": False, "events": [], "truncated": False, "detail": "The original source log could not be verified."}
        if len(verified) != 1:
            return {"available": False, "events": [], "truncated": False, "detail": "More than one original source log matches this session identity; source events are unavailable."}
        path = next(iter(verified.values()))
    # The API resolved the full child identity above. Claude's child records
    # retain the enclosing session UUID in their sessionId field.
    child = SN.claude_child_identity(path) if source == "claude" else ""
    transcript_id = child.split("/", 1)[0] if child == session_id else session_id
    return ST.timeline(source, path, transcript_id, date_from=date_from, date_to=date_to)


# ---------------------------------------------------------------------------
# Merge per-file aggregates → a single dataset payload for the frontend.
# ---------------------------------------------------------------------------
def _cost(source, model, inp, out, cr, cc5, cc1, cc_fallback=0, date=None, logged_cost=None, ws=0, long_context=None):
    # opencode's SQLite store logs the actual per-message cost; prefer it over a
    # list-price estimate. Callers pass None (not 0.0) when there is no logged
    # figure — the older opencode JSON layout records no cost at all, and
    # treating its 0.0 as authoritative would zero out those installs.
    if source == "opencode" and logged_cost is not None:
        return logged_cost
    # OpenClaw logs its own per-response estimate; it is used only for a model we
    # have no verified price for, so every priced model still bills the same way
    # whichever tool ran it
    if source == "openclaw" and logged_cost and not any(P.price_of(model, date)):
        return logged_cost
    # date-aware: usage from before a vendor price change bills at that day's rate
    pin, pout, pcw5, pcw1, pcr = P.price_of(model, date)
    # if a record only has the untiered total (cc_fallback), bill it at the 5-min rate
    if cc_fallback and not (cc5 or cc1):
        cc5 = cc_fallback
    # A cache write is still input the model read. A model with no published write
    # rate (most OpenAI rows, and a GPT-5.6 day priced from PRICE_HISTORY, whose old
    # page listed none) bills it at the plain input rate, never at $0.
    pcw5 = pcw5 or pin
    pcw1 = pcw1 or pcw5
    cost = (inp * pin + out * pout + cr * pcr + cc5 * pcw5 + cc1 * pcw1) / 1_000_000.0
    if long_context and any(long_context):
        high = P.price_of(model, date, long_context=True)
        base = (pin, pout, pcw5, pcw1, pcr)
        cost += sum(n * (h - b) for n, h, b in zip(long_context, high, base)) / 1_000_000.0
    return cost + ws * P.WEB_SEARCH_USD


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _conversation_key(path, agg):
    """Cache entries that hold the SAME conversation. Codex's archive feature
    moves a rollout into archived_sessions/: the old path stays in the ledger as
    archived while the new one parses live. A Copilot chat can sit under two
    paths too: <uuid>.json and <uuid>.jsonl across its storage-format migration,
    or the same chat in Cursor's storage after Cursor imported VS Code's. Every
    copy used to be counted in full. Copies are deduplicated within a device:
    an imported conversation must not hide another device's usage."""
    src = agg.get("source")
    device = agg.get("_device")              # None is this machine's own ledger
    if src == "codex":
        return (device, "codex", P._leaf(path))
    if src == "copilot":
        m = _UUID.search(P._leaf(path)) or _UUID.search(path)
        return (device, "copilot", m.group(0)) if m else None
    return None


def _one_per_conversation(items):
    """Keep one copy of each conversation: the fullest (a later copy is a superset
    of an earlier one), then the live one, then the newest."""
    best, out = {}, []
    migrated = {}
    for _, agg in items:
        if agg.get("source") == "opencode" and "opencode_ids" in agg:
            migrated.setdefault(agg.get("_device"), set()).update(
                tuple(x) for x in agg["opencode_ids"])
    for path, agg in items:
        if agg.get("source") == "opencode" and "opencode_messages" in agg:
            known = migrated.get(agg.get("_device"), set())
            messages = agg["opencode_messages"]
            remaining = [m for m in messages if tuple(m["id"]) not in known]
            if len(remaining) != len(messages):
                if not remaining:
                    continue
                agg = _opencode_remainder(agg, remaining)
        k = _conversation_key(path, agg)
        if k is None:
            out.append(agg)
            continue
        act = sum(r.get("asst", 0) + r.get("user", 0) + r.get("in", 0) + r.get("out", 0)
                  + r.get("cr", 0) + r.get("cc", 0) for r in agg.get("records", {}).values())
        rank = (act, not agg.get("archived"), agg.get("mtime") or 0)
        if k not in best or rank > best[k][0]:
            best[k] = (rank, agg)
    return out + [a for _, a in best.values()]


def _opencode_remainder(original, messages):
    """Project a legacy ledger without DB-backed copies; retain the raw ledger."""
    agg = P._blank_agg("opencode", original["path"])
    for field in ("project", "editor", "title", "branch", "archived", "_device", "mtime", "size"):
        if field in original:
            agg[field] = original[field]
    weights = {}
    for msg in messages:
        date, model = msg["key"].split("\t", 1)
        row = P._rec(agg, date, model)
        for field, value in msg["usage"].items():
            row[field] = row.get(field, 0) + value
            agg["totals"][field] = agg["totals"].get(field, 0) + value
        dt = P._from_iso(msg["ts"])
        P._bump_time(agg, dt, sum(msg["usage"].get(k, 0) for k in ("in", "out", "cr", "cc")), 1)
        for name in msg["tools"]:
            key = date + "\t" + name
            agg["tools"][key] = agg["tools"].get(key, 0) + 1
        weights[model] = weights.get(model, 0) + msg["usage"].get("in", 0) + msg["usage"].get("out", 0)
    agg["state"]["dom_model"] = max(weights, key=weights.get)
    P._finalize_session(agg, "opencode", agg["path"])
    return agg


def _did_something(s):
    """A session with a turn or completed compaction — the list and per-project
    session count must agree on what counts as one."""
    return bool(s.get("asst") or s.get("req") or s.get("in") or s.get("user") or s.get("compactions"))


def build_account_payload():
    # Copy only private metadata under the parser lock, then release it before
    # waiting on a possibly slow opt-in CLI lookup. Usage refresh stays available.
    with _refresh_lock:
        aggregates = [{"source": a.get("source"), "path": a.get("path", ""),
                       "account_metadata": copy.deepcopy(a.get("account_metadata") or {})}
                      for a in _state["files"].values()]
        device = _local_id()
    return A.build_accounts(aggregates, device)


def build_payload():
    # Parsers append to nested record dictionaries in place. Hold the same lock
    # as refresh so a response is consistent and iteration cannot race an append.
    with _refresh_lock:
        return _build_payload_locked()


def set_session_name(body):
    """Confirm a native rename or a strictly local Claude-child display name."""
    device, source, session_id = (body.get(k) for k in ("device", "source", "id"))
    if any(not isinstance(v, str) or not v or len(v) > 512 for v in (device, source, session_id)):
        raise ValueError("Expected device, source and session id.")
    if device != _local_id():
        raise ValueError("Rename this session on its source device.")
    with _refresh_lock:
        discovered = {(s, os.path.realpath(p)) for s, p, _ in P.discover()}
        matches = []
        for candidate in _state["files"].values():
            if candidate["source"] != source: continue
            if (source, os.path.realpath(candidate.get("path", ""))) not in discovered: continue
            session = next((s for s in candidate.get("sessions", []) if SN.identity(candidate, s) == session_id), None)
            if session is not None and os.path.isfile(candidate.get("path", "")):
                matches.append((candidate, session))
        if not matches:
            raise ValueError("The original session log is unavailable. Its telemetry is preserved.")
        if len(matches) != 1:
            raise ValueError("More than one source matches this session identity. A name cannot be changed safely.")
        aggregate, session = matches[0]
        path = aggregate["path"]
        original = session.get("title") or session.get("project") or session_id
        display_only = source == "claude" and bool(session.get("subagent"))
        if body.get("action") == "preview":
            if display_only:
                return SN.prepare_display(device, source, path, session_id, body.get("name"), original)
            return SN.prepare(device, source, path, session_id, body.get("name"), original)
        if body.get("action") != "rename":
            raise ValueError("Review the name-change preview before saving.")
        if display_only:
            result = SN.commit_display(body.get("ticket"), device, source, path, session_id, body.get("confirmed"))
            notify_refresh()
            return result
        result = SN.commit(body.get("ticket"), device, source, path, session_id, body.get("confirmed"))
        # Parse the metadata through the existing source parser. No cache rebuild,
        # archived-history deletion or modifications to billed usage are involved.
        try:
            updated = P.update_file(aggregate, source, path, aggregate.get("editor"), {})
            if source == "codex":
                P._finalize_session(updated, source, path)
            for key, value in list(_state["files"].items()):
                if value is aggregate: _state["files"][key] = updated
            _dirty["v"] = True
            _gen["v"] += 1
            if save_cache(): _dirty["v"] = False
        except Exception as error:
            sys.stderr.write(f"[session-name:{source}] dashboard refresh pending: {type(error).__name__}: {error}\n")
            result["warning"] = "The tool's name was saved; the dashboard will retry its metadata refresh."
        result["title"] = result["name"]
    notify_refresh()
    return result


def client_snapshot():
    """Freeze both HTTP views for this display tick, even if parsing finishes between requests.

    Serialize while holding the parser lock: session dictionaries can still share
    nested fields with the ledger. Only the serialized snapshot is retained.
    This is transient display state, never saved into the durable ledger.
    """
    with _client_snapshot_lock:
        revision = refresh_sync()["revision"]
        if revision != _client_snapshot["revision"]:
            with _refresh_lock:
                payload = _build_payload_locked()
                today = time.strftime("%Y-%m-%d")
                rows = [row for row in payload["records"] if row["date"] == today]
                summary = {"date": today,
                           "tokens": sum(sum(int(row.get(field) or 0) for field in
                                             ("in", "out", "cr", "cc")) for row in rows),
                           "spend": round(sum(float(row.get("cost") or 0) for row in rows), 2)}
                data_json, summary_json = json.dumps(payload), json.dumps(summary)
            _client_snapshot.update(revision=revision, data=data_json, summary=summary_json)
        return _client_snapshot["data"], _client_snapshot["summary"]


def build_today_summary():
    """Aggregate only today's usage records for the menu bar, not the full dashboard."""
    today = time.strftime("%Y-%m-%d")
    tokens = 0
    spend = 0.0
    with _refresh_lock:
        with _lock:
            items = list(_state["files"].items())
        items += _peer_items()
        for agg in _one_per_conversation(items):
            source = agg["source"]
            has_logged_cost = ((source == "opencode" and str(agg.get("path", "")).endswith(".db"))
                               or source == "openclaw")
            for _, bucket in _record_buckets(agg):
                for key, row in bucket.items():
                    date, model = key.split("\t", 1)
                    if date != today:
                        continue
                    priced = _priced_usage(source, model, date, row, has_logged_cost)
                    tokens += sum(int(priced.get(field) or 0) for field in ("in", "out", "cr", "cc"))
                    spend += float(priced.get("cost") or 0)
    return {"date": today, "tokens": tokens, "spend": round(spend, 2)}


def build_editor_snapshot(query):
    """Read only this machine's retained aggregates under the parser lock."""
    ES.parse_query(query)
    with _refresh_lock:
        with _lock:
            items = list(_state["files"].items())
        local, aliases, alias_error = _local_id(), {}, False
        try:
            names = SN.display_names()
        except ValueError:
            names, alias_error = {}, True
        for path, agg in items if names else ():
            if (str(path).startswith("peer:") or agg.get("source") != "claude" or not agg.get("subagent")
                    or agg.get("_device") not in (None, "", local)):
                continue
            for session in agg.get("sessions", []):
                native_id = SN.identity(agg, session)
                if not native_id:
                    continue
                alias = SN.display_name(names, local, "claude", agg.get("path", ""), native_id)
                if alias:
                    aliases[(agg.get("path", ""), native_id)] = alias
        result = ES.build_snapshot(items, local, query, refresh_sync()["revision"],
                                   price_usage=_priced_usage, deduplicate=_one_per_conversation,
                                   identity=SN.identity, claude_parent_key=_claude_parent_key,
                                   display_aliases=aliases)
        if alias_error:
            result["limitations"].append("Saved session display names could not be read; recorded titles are shown.")
        return result


def _sum_usage(target, row):
    for field in _zero():
        target[field] = target.get(field, 0) + row.get(field, 0)
    for field in P._LONG_FIELDS + ("asst",):
        key = "lc_" + field
        if key in row:
            target[key] = target.get(key, 0) + row[key]


def _priced_usage(source, model, date, row, logged):
    out = {k: row.get(k, 0) for k in _zero()}
    out["cost"] = (0.0 if model == "(user)" else _cost(
        source, model, out["in"], out["out"], out["cr"], out["cc5"], out["cc1"],
        out["cc"], date, logged_cost=row.get("cost", 0) if logged else None, ws=out["ws"],
        long_context=P._long_values(row)))
    for field in P._LONG_FIELDS + ("asst",):
        key = "lc_" + field
        if key in row: out[key] = row[key]
    return out


def _record_buckets(agg):
    sessions = agg.get("sessions", [])
    if sessions and all("records" in s for s in sessions):
        return [(s.get("project") or agg.get("project") or "(unknown)", s["records"])
                for s in sessions]
    return [(agg.get("project") or "(unknown)", agg.get("records", {}))]


def _priced_days(bucket, source, logged):
    days, models = {}, {}
    for key, row in bucket.items():
        date, model = key.split("\t", 1)
        priced = _priced_usage(source, model, date, row, logged)
        _sum_usage(days.setdefault(date, _zero()), priced)
        _sum_usage(models.setdefault(model, {}).setdefault(date, _zero()), priced)
    return days, models


def _day_vectors(days):
    fields = ("cost", "in", "out", "cr", "cc", "asst", "user", "tools", "prem", "active",
              "req", "cc5", "cc1", "reason", "ws")
    return {d: [round(row.get(k, 0), 6) for k in fields] for d, row in sorted(days.items())}


def _model_history(model):
    base = model[:-len(P.US_SUFFIX)] if model.endswith(P.US_SUFFIX) else model
    if base.endswith(P.FAST_SUFFIX):
        return ()
    return P.PRICE_HISTORY.get(P._canonicalize(base), ())


def _claude_parent_key(path, child=False):
    """Recognize Claude's own parent/child transcript layout without disk reads.

    The parent UUID and project-root prefix must both agree. Workflow children
    use the same session directory as the older direct agent-*.jsonl files.
    Unknown layouts remain unassociated; titles and activity dates are not IDs.
    """
    if not isinstance(path, str):
        return None
    path = path.replace("\\", "/")
    if "\0" in path or any(part in (".", "..") for part in path.split("/")):
        return None
    uuid = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
    if child:
        match = re.fullmatch(
            rf"(.+)/({uuid})/(subagents/(?:workflows/wf_[A-Za-z0-9_-]+/)?"
            r"agent-[A-Za-z0-9_-]{1,128}\.jsonl)", path)
    else:
        match = re.fullmatch(rf"(.+)/({uuid})\.jsonl", path)
    if not match:
        return None
    root, parent = match.group(1), match.group(2).lower()
    # Copied Windows paths may differ in drive/directory case as well as slash.
    windows = bool(re.match(r"^[A-Za-z]:/", root) or root.startswith("//"))
    if windows:
        root = root.casefold()
    key = (root, parent)
    if child:
        identity = match.group(3)
        return key, identity.casefold() if windows else identity
    return key


def _claude_child_transcript_counts(files, local):
    """Positive-only, whole-session evidence derived from retained aggregates.

    Each full relative transcript identity counts once within its parent/device,
    including archives and shared copies. Workflow names are part of that ID:
    short agent basenames may be reused. This never changes usage accounting.
    """
    children = {}
    for agg in files:
        if agg.get("source") != "claude" or not agg.get("subagent"):
            continue
        recognized = _claude_parent_key(agg.get("path"), child=True)
        if recognized is None:
            continue
        parent, identity = recognized
        key = (agg.get("_device") or local, parent)
        children.setdefault(key, set()).add(identity)
    return {key: len(identities) for key, identities in children.items()}


def _claude_parent_descriptors(files, local):
    """Resolve only one retained parent in the child's exact source namespace."""
    candidates = {}
    for agg in files:
        if agg.get("source") != "claude" or agg.get("subagent"):
            continue
        parent = _claude_parent_key(agg.get("path"))
        sessions = agg.get("sessions") or []
        if parent is None or len(sessions) != 1 or sessions[0].get("subagent"):
            continue
        device = agg.get("_device") or local
        session = sessions[0]
        title = session.get("title") or session.get("project") or parent[1]
        if not isinstance(title, str):
            title = parent[1]
        candidates.setdefault((device, parent), []).append({
            "id": parent[1], "title": title, "source": "claude", "device": device})
    return {key: values[0] for key, values in candidates.items() if len(values) == 1}


def _build_payload_locked():
    try:
        display_names, display_name_error = SN.display_names(), ""
    except ValueError as error:
        display_names, display_name_error = {}, str(error)
    records = {}      # (date, source, model, project, ide, device) -> aggregates
    tools = {}        # (date, source, name, device) -> count
    projects = {}     # (project, source) -> {tokens, msgs, sessions, cost}
    hourly = {}       # (date, hour, source, device) -> {tokens, msgs}
    sessions = []
    skills = {}       # (date, skill, device) -> tokens/cost
    ctxb = {}         # (date, bucket, source, device) -> tokens/requests
    model_meta = {}   # model -> vendor
    ai_lines = {}     # (date, device) -> Cursor's suggested/accepted line counts
    activity = {}     # (date, source, model, category, project, ide, device) -> turn counts + cost
    mcp_inv = {}      # server -> set of tool names Claude Code offered
    mcp_loaded = {}   # (date, server, project) -> sessions it was offered in
    mcp_calls = {}    # (date, source, server, project) -> calls + distinct tool names
    reads = {}        # (date, source, project, device) -> [reads, re-reads, junk, re-read tok, junk tok]
    used_ext = {}     # (date, kind, name) -> uses of an installed skill / agent
    cwds = {}         # (source family, cwd) -> first day Claude Code there read AGENTS.md
    undecoded = 0     # OpenClaw events stored compressed that couldn't be read here

    with _lock:
        items = list(_state["files"].items())
    # a connected device's aggregates join the loop under its own id; everything
    # below is keyed by device the same way it is by IDE
    items += _peer_items()
    files = _one_per_conversation(items)
    local = _local_id()
    claude_children = _claude_child_transcript_counts(files, local)
    claude_parents = _claude_parent_descriptors(files, local)

    for agg in files:
        source = agg["source"]
        # A connected device's copy is usage only. Its setup (instruction files, MCP
        # config, installed skills) lives on ITS disk, so the Optimize setup checks,
        # which read this machine's, skip it.
        device = agg.get("_device") or local
        remote = device != local
        # Only the SQLite store records a real per-message cost. The older
        # opencode JSON layout logs none, so its records carry a placeholder
        # 0.0 that must NOT be mistaken for "this was free".
        has_logged_cost = ((source == "opencode" and str(agg.get("path", "")).endswith(".db"))
                           or source == "openclaw")
        project = agg.get("project") or "(unknown)"
        # One IDE per file for every source (Copilot's is the editor whose storage
        # it came from; Claude/Codex stamp an entrypoint; the rest run in exactly
        # one place), so it is resolved here rather than per record.
        # the exporting device resolved its own IDE (Codex's editor lookup reads that
        # machine's editor storage, not this one's)
        ide = agg.get("ide") if remote and agg.get("ide") else P._ide_of(source, agg)
        for row_project, bucket in _record_buckets(agg):
            pr = projects.setdefault((row_project, source),
                                    {"tokens": 0, "msgs": 0, "sessions": 0, "cost": 0.0})
            for key, r in bucket.items():
                date, model = key.split("\t", 1)
                priced = _priced_usage(source, model, date, r, has_logged_cost)
                rk = (date, source, model, row_project, ide, device)
                slot = records.setdefault(rk, _zero())
                _sum_usage(slot, priced)
                if "exact" in r:
                    exact = slot.setdefault("exact", _zero())
                    _sum_usage(exact, _priced_usage(source, model, date, r["exact"], False))
                model_meta[model] = P.vendor_of(model)
                tokens = sum(r.get(k, 0) for k in ("in", "out", "cr", "cc"))
                pr["tokens"] += tokens; pr["msgs"] += r.get("asst", 0)
                pr["cost"] += priced["cost"]
        for ak, v in P.activity_of(agg).items():
            date, model, cat = ak.split("\t")
            e = activity.setdefault((date, source, model, cat, project, ide, device),
                                    {"turns": 0, "edits": 0, "oneshot": 0, "retries": 0,
                                     "tok": 0, "cost": 0.0, "edit_cost": 0.0, "retry_cost": 0.0})
            for f in ("turns", "edits", "oneshot", "retries"):
                e[f] += v.get(f, 0)
            e["tok"] += v.get("in", 0) + v.get("out", 0) + v.get("cr", 0) + v.get("cc5", 0) + v.get("cc1", 0)
            e["cost"] += _cost(source, model, v.get("in", 0), v.get("out", 0), v.get("cr", 0),
                               v.get("cc5", 0), v.get("cc1", 0), 0, date, ws=v.get("ws", 0),
                               long_context=P._long_values(v))
            if v.get("ein") or v.get("eout"):
                e["edit_cost"] += _cost(source, model, v.get("ein", 0), v.get("eout", 0),
                                        v.get("ecr", 0), v.get("ecc5", 0), v.get("ecc1", 0), 0,
                                        date, ws=v.get("ews", 0), long_context=P._long_values(v, "e"))
            if v.get("rin") or v.get("rout"):
                e["retry_cost"] += _cost(source, model, v.get("rin", 0), v.get("rout", 0),
                                         v.get("rcr", 0), v.get("rcc5", 0), v.get("rcc1", 0), 0,
                                         date, ws=v.get("rws", 0), long_context=P._long_values(v, "r"))
        if not agg.get("subagent") and not remote:
            for server, e in (agg.get("mcp_offered") or {}).items():
                mcp_inv.setdefault(server, set()).update(e.get("tools") or ())
                k = (e.get("d") or "", server, project)
                mcp_loaded[k] = mcp_loaded.get(k, 0) + 1
        for tk, c in agg.get("tools", {}).items():
            date, _, name = tk.partition("\t")
            if name.startswith("mcp__") and not remote:
                k = (date, source, name[5:].split("__", 1)[0], project)
                call = mcp_calls.setdefault(k, {"calls": 0, "tools": set()})
                call["calls"] += c
                call["tools"].add(name)
        for date, e in (agg.get("reads") or {}).items():
            slot = reads.setdefault((date, source, project, device), [0, 0, 0, 0, 0])
            for i, v in enumerate(e[:5]):
                slot[i] += v
        for k, c in (agg.get("used_ext") or {}).items():
            if remote:                        # measured against THIS machine's installs
                break
            date, kind, name = (k.split("\t") + ["", "", ""])[:3]
            used_ext[(date, kind, name)] = used_ext.get((date, kind, name), 0) + c
        if source == "openclaw" and not remote:
            undecoded += int((agg.get("state") or {}).get("undecoded") or 0)
        if (agg.get("cwd") and source in ("claude", "claude-desktop", "codex")
                and not agg.get("archived") and not remote):
            ck = ("codex" if source == "codex" else "claude", agg["cwd"])
            # when Claude Code started reading AGENTS.md here. A file keeps only its
            # newest version, so take its LAST day: `since` can start late, never early
            since = ((agg.get("last_ts") or "")[:10] or None
                     if ck[0] == "claude" and _ver_tuple(agg.get("cliver")) >= CLAUDE_AGENTS_MD_SINCE
                     else None)
            prev = cwds.get(ck)
            cwds[ck] = min(prev, since) if prev and since else (prev or since)
        for sk, v in agg.get("skills", {}).items():
            date, _, name = sk.partition("\t")
            if not name:
                continue
            e = skills.setdefault((date, name, device), {"tok": 0, "asst": 0, "cost": 0.0})
            e["tok"] += v.get("tok", 0)
            e["asst"] += v.get("asst", 0)
            # Retain the older dominant-model approximation for other models;
            # Haiku's request-tier contributions carry their own model/rates.
            haiku = v.get("haiku_usage", {})
            remaining = {f: max(0, v.get(f, 0) - sum(h.get(f, 0) for h in haiku.values()))
                         for f in ("in", "out", "cr")}
            remaining["cc"] = max(0, v.get("cc", 0) - sum(h.get("cc5", 0) + h.get("cc1", 0) for h in haiku.values()))
            e["cost"] += _cost(source, agg.get("state", {}).get("dom_model") or "Unknown",
                               remaining["in"], remaining["out"], remaining["cr"], remaining["cc"], 0, 0, date)
            for model, h in haiku.items():
                e["cost"] += _cost(source, model, h.get("in", 0), h.get("out", 0), h.get("cr", 0),
                    h.get("cc5", 0), h.get("cc1", 0), 0, date, long_context=P._long_values(h))
        for ck, v in agg.get("ctx", {}).items():
            date, _, b = ck.partition("\t")
            if not b:
                continue
            e = ctxb.setdefault((date, b, source, device), {"tok": 0, "n": 0})
            e["tok"] += v.get("tok", 0)
            e["n"] += v.get("n", 0)
        for tk, c in agg.get("tools", {}).items():
            date, _, name = tk.partition("\t")
            if not name:                      # pre-v16 cache shape — skip
                continue
            k = (date, source, name, device)
            tools[k] = tools.get(k, 0) + c
        for hk, v in agg.get("hourly", {}).items():
            date, _, hour = hk.partition("\t")
            if not hour:                      # pre-v16 cache shape — skip
                continue
            slot = hourly.setdefault((date, int(hour), source, device), {"tokens": 0, "msgs": 0})
            slot["tokens"] += v["tokens"]; slot["msgs"] += v["msgs"]
        for session in agg.get("sessions", []):
            if _did_something(session):
                pk = (session.get("project") or project, source)
                pr = projects.setdefault(pk, {"tokens": 0, "msgs": 0, "sessions": 0, "cost": 0.0})
                pr["sessions"] += 1
        for day, v in (agg.get("state", {}).get("ai_lines") or {}).items():
            slot = ai_lines.setdefault((day, device), {"tab_suggested": 0, "tab_accepted": 0,
                                             "composer_suggested": 0, "composer_accepted": 0})
            for f in slot:
                slot[f] += v.get(f, 0)
        # Model-specific contributions price before rolling into session days.
        # Archived older stores can lack this detail; never copy a whole DB's
        # daily totals into each session to fill that gap.
        for session in agg.get("sessions", []):
            s2 = dict(session)
            bucket = session.get("records")
            if bucket is None and len(agg.get("sessions", [])) == 1:
                bucket = agg.get("records", {})
            if bucket is not None:
                days, model_days = _priced_days(bucket, source, has_logged_cost)
                s2["model_days"] = {m: _day_vectors(ds) for m, ds in model_days.items()}
                if source == "copilot":
                    exact = {k: r["exact"] for k, r in bucket.items() if "exact" in r}
                    exact_days, exact_models = _priced_days(exact, source, False)
                    s2["exact_days"] = _day_vectors(exact_days)
                    s2["exact_model_days"] = {m: _day_vectors(ds) for m, ds in exact_models.items()}
            else:
                own = session.get("days")
                days = {}
                if own:
                    for date, r in own.items():
                        days[date] = _priced_usage(source, session["model"], date, r, has_logged_cost)
                s2["detail_limited"] = True
            s2["days"] = _day_vectors(days) if days or bucket is not None else None
            s2["archived"] = bool(agg.get("archived"))
            s2["subagent"] = bool(session.get("subagent"))
            s2["device"] = device
            if source == "claude" and not s2["subagent"]:
                parent = _claude_parent_key(agg.get("path"))
                child_count = claude_children.get((device, parent))
                if child_count:
                    # Absence means no known association, never proof of zero.
                    # Keep this separate from source spawn counts/inline usage.
                    s2["subagent_children"] = child_count
            elif source == "claude" and agg.get("subagent"):
                recognized = _claude_parent_key(agg.get("path"), child=True)
                parent = claude_parents.get((device, recognized[0])) if recognized else None
                if parent:
                    s2["subagent_parent"] = dict(parent)
            if not remote and source in ("codex", "claude"):
                s2["account_metadata"] = A.session_metadata(agg)
            available, reason = SN.availability(source, agg.get("path", ""), remote)
            s2["rename_id"] = SN.identity(agg, session)
            if not s2["rename_id"]:
                available, reason = False, "The full native session identity is unavailable."
            if source == "claude" and session.get("subagent"):
                available, reason = SN.display_availability(source, agg.get("path", ""), True, remote)
                if not remote:
                    s2["rename_scope"] = "display"
                    if display_name_error:
                        available, reason = False, display_name_error
                    alias = SN.display_name(display_names, device, source, agg.get("path", ""), s2["rename_id"])
                    if alias:
                        s2["original_title"] = session.get("title") or session.get("project") or s2["rename_id"]
                        s2["title"] = alias
                        s2["display_alias"] = True
            s2["rename_available"], s2["rename_reason"] = available, reason
            s2["cost"] = (sum(d["cost"] for d in days.values()) if days else
                          _priced_usage(source, session["model"],
                                        (session.get("end") or session.get("start") or "")[:10],
                                        session, has_logged_cost)["cost"])
            sessions.append(s2)

    # Parent resolution uses full source IDs and the exporting device, never a
    # title, project label, shared root-session ID or eight-character table ID.
    parent_candidates = {}
    for session in sessions:
        identity = session.get("rename_id")
        if session.get("source") == "codex" and isinstance(identity, str) and re.fullmatch(
                r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", identity):
            key = (session["device"], identity.lower())
            parent_candidates.setdefault(key, []).append(session)
    for session in sessions:
        parent_id = session.get("parent_id")
        if session.get("source") != "codex" or not session.get("subagent") or not isinstance(parent_id, str):
            continue
        candidates = parent_candidates.get((session["device"], parent_id), [])
        if len(candidates) == 1 and candidates[0] is not session:
            parent = candidates[0]
            session["subagent_parent"] = {
                "id": parent["rename_id"], "title": parent.get("title") or parent.get("project") or parent_id,
                "source": "codex", "device": parent["device"]}

    rec_list = []
    for (date, source, model, project, ide, device), v in records.items():
        rec_list.append({"date": date, "source": source, "model": model,
                         "project": project, "ide": ide, "device": device, **v})
    rec_list.sort(key=lambda x: (x["date"], x["source"]))

    # keep the payload bounded: the long tail of one-off tool names folds into
    # a single "(other)" row rather than shipping thousands of day rows
    name_tot = {}
    for (d, s, n, dv), c in tools.items():
        name_tot[n] = name_tot.get(n, 0) + c
    keep = set(sorted(name_tot, key=lambda n: -name_tot[n])[:60])
    tl = {}
    for (d, s, n, dv), c in tools.items():
        k = (d, s, n if n in keep else "(other)", dv)
        tl[k] = tl.get(k, 0) + c
    tool_list = [{"date": d, "source": s, "name": n, "device": dv, "count": c}
                 for (d, s, n, dv), c in tl.items()]
    tool_list.sort(key=lambda x: -x["count"])

    proj_list = [{"project": p, "source": s, **v} for (p, s), v in projects.items()]
    proj_list.sort(key=lambda x: -x["tokens"])

    hour_list = [{"date": d, "hour": h, "source": s, "device": dv, **v}
                 for (d, h, s, dv), v in hourly.items()]

    # A session where you typed but never got a reply is still something that
    # happened — `records` counts those user turns, so dropping the session here
    # made the two paths disagree (1,982 prompts vs 1,980).
    sessions = [s for s in sessions if _did_something(s)]
    sessions.sort(key=lambda s: (s.get("end") or ""), reverse=True)

    return {
        "generated_at": time.time(),
        "meta": dict(_meta),
        "cache_error": _cache_error["v"] or _meta.get("cache_error"),
        "device": dict(DEVICE, id=local),
        # every device in the data: this one first, then each connected one
        "devices": _devices_meta(),
        "version": VERSION,
        "home": P.HOME,        # to show instruction-file paths as ~/...
        "openclaw_undecoded": undecoded,
        "mcp_servers": _mcp_servers(),
        # Real per-1M rates for the models THIS user actually ran, so the client can
        # cost a "what if this had run on X" without any hardcoded model list.
        "prices": {m: list(P.price_of(m)) for m in model_meta},
        "codex_effort": _codex_config()["effort"],
        "skills": [{"date": d, "name": n, "device": dv, **v} for (d, n, dv), v in skills.items()],
        # what each turn was for (Claude Code, Claude Desktop, Codex) — see parser.py ACTIVITY
        "activity": [{"date": d, "source": src, "model": m, "category": c, "project": pj, "ide": i,
                      "device": dv,
                      **{k: (round(x, 6) if isinstance(x, float) else x) for k, x in v.items()}}
                     for (d, src, m, c, pj, i, dv), v in activity.items()],
        "mcp_inventory": {"servers": {sv: {"tools": sorted(t)} for sv, t in mcp_inv.items()},
                          "loaded": [{"date": d, "server": sv, "project": pj, "sessions": n}
                                     for (d, sv, pj), n in sorted(mcp_loaded.items())],
                          "calls": [{"date": d, "source": src, "server": sv, "project": pj,
                                     "calls": v["calls"], "tools": sorted(v["tools"])}
                                    for (d, src, sv, pj), v in sorted(mcp_calls.items())]},
        # reads that added tokens for nothing (Claude Code) — see parser.py READ HYGIENE
        "reads": [{"date": d, "source": src, "project": pj, "device": dv, "reads": v[0], "rereads": v[1],
                   "junk": v[2], "reread_tok": v[3], "junk_tok": v[4]}
                  for (d, src, pj, dv), v in reads.items()],
        # sizes of the CLAUDE.md / AGENTS.md files each request carries
        "context_files": _context_files(cwds),
        "installed": {"items": _installed(cwds),
                      "used": [{"date": d, "kind": k, "name": n, "n": c}
                               for (d, k, n), c in used_ext.items()]},
        "ctx": [{"date": d, "bucket": b, "source": src, "device": dv, **v}
                for (d, b, src, dv), v in ctxb.items()],
        "records": rec_list,
        "tools": tool_list,
        "projects": proj_list,
        "hourly": hour_list,
        "sessions": sessions[:2000],
        "sessions_total": len(sessions),
        "model_vendor": model_meta,
        "ai_lines": [{"date": d, "source": "cursor", "device": dv, **v}
                     for (d, dv), v in sorted(ai_lines.items())],
        "pricing": {k: list(v) for k, v in {**P.PRICING,
                    **{m: P.price_of(m) for m in model_meta}}.items()},
        "pricing_history": {m: [[d, list(P.price_of(m, d))] for d, _ in _model_history(m)]
                            for m in set(P.PRICE_HISTORY) | set(model_meta)},
        "pricing_long_context": {m: list(P.price_of(m, long_context=True))
            for m in set(P.LONG_CONTEXT_PRICING) | set(model_meta)
            if P.price_of(m, long_context=True) != P.price_of(m)},
        "pricing_note": ("Anthropic costs use current list pricing (Fable 5 $10/$50, Opus 5.5 "
                         "$4/$20, Opus 5 & 4.x $5/$25, Sonnet 5 & 5.5 $2/$10, Sonnet 4.x "
                         "$3/$15, Haiku 4.5 $1/$5 per Mtok). Haiku 5.5 uses $0.10/$0.50 "
                         "up to 100K prompt tokens and $0.50/$2.50 above 100K, including cache "
                         "reads/writes in each request's threshold; available Claude logs retain "
                         "that split, while older archives/peers and other tools without request "
                         "detail use the short-prompt rate. Cache write is billed at 1.25x (5-min) "
                         "/ 2x (1-hour) input and cache read at 0.1x (0.05x on Opus 5.5, 0.025x on Fable 5.1); OpenAI/"
                         "Codex/Copilot/Cursor prices are estimates. Codex GPT-5.4/5.5/5.6/6 use "
                         "verified OpenAI list rates (e.g. GPT-6 Sol $2/$10, cached $0.20; GPT-6.1 Sol cached $0.10), each "
                         "day priced at the rate in force then — OpenAI cut GPT-5.6 prices on "
                         "2026-07-30 and 2026-08-21; a >272K-input surcharge is not modeled, so "
                         "heavy-context sessions may be higher. Gemini 4 Argon uses Google's "
                         "announced introductory rates ($2 input/$10 output, cached input $0.10 "
                         "per Mtok). Other Gemini text models use Standard rates for prompts "
                         "up to 200K tokens. "
                         "Actual "
                         "billing may differ. Claude Code/Desktop & Codex can run on either "
                         "subscription or API billing and the logs don't record which, so $ is "
                         "shown as API-equivalent value (all such usage is included either way). "
                         "GitHub Copilot uses recorded request token counts when available. "
                         "Copilot SDK/Agent Host reads saved VS Code per-turn model totals and "
                         "SDK shutdown summaries; missing usage is shown as unavailable or partial, "
                         "never estimated from chat length. GLM 5.3 uses Cloudflare Workers AI "
                         "list rates ($1.40 input/$4.40 output/$0.26 cached input per Mtok). "
                         "Older chats and requests still in progress are estimated from visible "
                         "message text, excluding hidden context (files, system prompts and tool "
                         "outputs), so those estimates can undercount. Request count and the "
                         "premium-request multiplier are also shown. "
                         "Cursor: messages, tool calls, per-session model, mode and timestamps are "
                         "exact, but it records token counts on only ~2% of messages (it meters usage "
                         "server-side for its request-based plan) — so Cursor tokens/cost here are a "
                         "LOWER BOUND; see Cursor's own dashboard for real usage. Gemini CLI is "
                         "not parsed: its local logs persist no usable prompt/token/model data."),
    }


# ---------------------------------------------------------------------------
# Storage — how much of the drive these interaction logs occupy.
# The per-file aggregates already carry each log's byte size, so the per-tool
# rollup is free; only the "related but unparsed" dirs need a walk, and that is
# cached because it touches multi-GB trees.
# ---------------------------------------------------------------------------
HOME = os.path.expanduser("~")
_extras_cache = {"at": 0.0, "rows": []}
EXTRAS_TTL = 300


def _dir_bytes(path, cap=400000):
    """Recursive size of a directory. Skips symlinks; gives up past `cap` files."""
    total, files, stack, n = 0, 0, [path], 0
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir():
                            stack.append(e.path)
                        else:
                            total += e.stat().st_size
                            files += 1
                            n += 1
                    except OSError:
                        continue
        except OSError:
            continue
        if n > cap:
            break
    return total, files


def _extras():
    """AI-related data on disk that the dashboard does NOT parse into analytics."""
    if time.time() - _extras_cache["at"] < EXTRAS_TTL:
        return _extras_cache["rows"]
    cands = [
        (os.path.join(HOME, ".gemini", "tmp"), "Gemini CLI logs",
         "not parsed — Gemini persists no tokens/model, so this is pure dead weight"),
        (os.path.join(HOME, ".claude", "file-history"), "Claude Code file history",
         "edit snapshots used for undo"),
        (os.path.join(HOME, ".claude", "shell-snapshots"), "Claude Code shell snapshots", ""),
        (os.path.join(HOME, ".claude", "backups"), "Claude Code backups", ""),
        (os.path.join(HOME, ".claude", "cache"), "Claude Code cache", ""),
        (os.path.join(HOME, ".codex", "archived_sessions"), "Codex archived sessions",
         "parsed — counted in Codex above"),
    ]
    rows = []
    for path, label, note in cands:
        if not os.path.isdir(path):
            continue
        b, n = _dir_bytes(path)
        if b:
            rows.append({"label": label, "path": path, "bytes": b, "files": n, "note": note})
    rows.sort(key=lambda r: -r["bytes"])
    _extras_cache.update(at=time.time(), rows=rows)
    return rows


IS_WINDOWS = os.name == "nt"

# What the server actually bound to, filled in by main(). The CSRF guard compares
# the Host header against THIS, never against Host itself — see _csrf_ok.
BIND = {"host": "127.0.0.1", "port": 7878}

# ---------------------------------------------------------------------------
# Settings — lets the dashboard edit the *tool's own* config, not its own.
# Currently just Claude Code's log-retention window (settings.json → cleanupPeriodDays).
# ---------------------------------------------------------------------------
CLAUDE_SETTINGS_PATH = os.path.join(P.HOME, ".claude", "settings.json")
CLAUDE_CLEANUP_DEFAULT = 30


def _read_claude_settings():
    if not os.path.exists(CLAUDE_SETTINGS_PATH):
        return {}
    try:
        with open(CLAUDE_SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _write_claude_settings(data):
    """Atomic write with a .bak of whatever was there — this file belongs to Claude
    Code, not to us, so a failed or unwanted edit must be trivially recoverable."""
    d = os.path.dirname(CLAUDE_SETTINGS_PATH)
    os.makedirs(d, exist_ok=True)
    if os.path.exists(CLAUDE_SETTINGS_PATH):
        shutil.copy2(CLAUDE_SETTINGS_PATH, CLAUDE_SETTINGS_PATH + ".bak")
    tmp = CLAUDE_SETTINGS_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, CLAUDE_SETTINGS_PATH)


_mcp_cache = {"at": 0.0, "data": None}


_ctxfile_cache = {"at": 0.0, "key": None, "data": None}


# Claude Code reads a project's AGENTS.md itself from this version on
CLAUDE_AGENTS_MD_SINCE = (2, 1, 277)
CODEX_DOC_MAX_BYTES = 32 * 1024     # Codex's project_doc_max_bytes default


def _ver_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:3])


def _size(path):
    """Size of a regular file, or 0 when there is none."""
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError as e:
        sys.stderr.write(f"[context files] {path}: {e}\n")
        return 0


def _claude_instruction_mode():
    """Claude Code's "Project instructions" setting (pluginConfigs → agents-md@builtin).
    Only that one key is read from settings.json."""
    try:
        cfg = _read_claude_settings().get("pluginConfigs") or {}
        mode = ((cfg.get("agents-md@builtin") or {}).get("options") or {}).get("instructionFiles")
        return mode if isinstance(mode, str) else "claude-md-or-agents-md"
    except (AttributeError, TypeError):
        return "claude-md-or-agents-md"


def _context_files(cwds):
    """SIZES (never contents) of the instruction files each agent loads at the start of
    every session, following each agent's own documented rules:

    Claude Code — ~/.claude/CLAUDE.md, then CLAUDE.md, CLAUDE.local.md and
    .claude/CLAUDE.md in the working directory and EVERY directory above it. From
    2.1.277 a project with none of those gets its AGENTS.md / .claude/AGENTS.md
    instead (or as well, with the "claude-md-and-agents-md" setting).
    Codex — ~/.codex/AGENTS.override.md, else ~/.codex/AGENTS.md; then from the git
    root DOWN to the working directory, at most one file per directory (the override
    wins), and it stops adding once those total project_doc_max_bytes (32 KiB).

    `bytes` is a file's size, `sent` how much of it goes into the request. `since`
    is the first day the file could have been loaded, when that isn't forever.
    `cwds` is {(source family, cwd): first day with AGENTS.md support, or None}."""
    key = tuple(sorted(cwds.items(), key=lambda kv: kv[0]))
    if (time.time() - _ctxfile_cache["at"] < 60 and _ctxfile_cache["key"] == key
            and _ctxfile_cache["data"] is not None):
        return _ctxfile_cache["data"]
    out, seen = [], set()
    home = os.path.normpath(P.HOME)
    user_claude = os.path.join(home, ".claude", "CLAUDE.md")

    def add(source, project, path, size, sent=None, since=None):
        if size and (source, project, path) not in seen:
            seen.add((source, project, path))
            out.append({"source": source, "project": project, "file": path, "bytes": size,
                        "sent": size if sent is None else sent, "since": since})

    add("claude", "*", user_claude, _size(user_claude))
    codex_home = os.path.join(home, ".codex")
    for n in ("AGENTS.override.md", "AGENTS.md"):      # the first non-empty one
        p = os.path.join(codex_home, n)
        if _size(p):
            add("codex", "*", p, _size(p))
            break
    cfg = ""
    try:
        with open(os.path.join(codex_home, "config.toml"), encoding="utf-8") as f:
            cfg = f.read()
    except OSError:
        pass
    m = re.search(r"^\s*project_doc_max_bytes\s*=\s*(\d+)", cfg, re.M)
    codex_cap = int(m.group(1)) if m else CODEX_DOC_MAX_BYTES
    claude_mode = _claude_instruction_mode()

    for (source, cwd), agents_since in list(key)[:300]:
        d = os.path.normpath(cwd)
        if not os.path.isdir(d):
            continue
        project = P._leaf(cwd) or cwd
        chain = [d]                                     # the dir, then every parent
        while os.path.dirname(chain[-1]) != chain[-1] and len(chain) < 64:
            chain.append(os.path.dirname(chain[-1]))
        if source == "claude":
            if claude_mode == "managed-only":
                continue
            claude_md, agents_md = [], []
            for dd in chain:
                # home and above load for every project under home: one row, "*"
                pj = "*" if home == dd or home.startswith(dd.rstrip(os.sep) + os.sep) else project
                for n in ("CLAUDE.md", "CLAUDE.local.md", os.path.join(".claude", "CLAUDE.md"),
                          "AGENTS.md", os.path.join(".claude", "AGENTS.md")):
                    p = os.path.join(dd, n)
                    if p == user_claude:                # already counted as the user file
                        continue
                    s = _size(p)
                    if s and "AGENTS" in n:             # loads per project, so never "*"
                        agents_md.append((project, p, s))
                    elif s:
                        claude_md.append((pj, p, s))
            for pj, p, s in claude_md:
                add(source, pj, p, s)
            if agents_since and (claude_mode == "claude-md-and-agents-md" or not claude_md):
                for pj, p, s in agents_md:
                    add(source, pj, p, s, since=agents_since)
        elif source == "codex":
            root = next((dd for dd in chain if os.path.exists(os.path.join(dd, ".git"))), d)
            left = codex_cap
            for dd in reversed(chain[:chain.index(root) + 1]):   # root down to cwd
                if left <= 0:
                    break
                for n in ("AGENTS.override.md", "AGENTS.md"):
                    p = os.path.join(dd, n)
                    s = _size(p)
                    if s:
                        add(source, project, p, s, sent=min(s, left))
                        left -= s
                        break
    _ctxfile_cache.update(at=time.time(), key=key, data=out)
    return out


_installed_cache = {"at": 0.0, "key": None, "data": None}


def _frontmatter_desc_len(path):
    """Length of a skill/agent file's `description:` — what Claude Code lists in
    every session. Only the length leaves this function."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(8192)
    except OSError as e:
        sys.stderr.write(f"[installed] {path}: {e}\n")
        return 0, None
    m = re.match(r"---\s*\n(.*?)\n---", head, re.S)
    if not m:
        return 0, None
    fm = m.group(1)
    name = re.search(r"^name:\s*(.+)$", fm, re.M)
    desc = re.search(r"^description:\s*(.*(?:\n[ \t]+.*)*)", fm, re.M)
    return (len(desc.group(1).strip()) if desc else 0,
            name.group(1).strip().strip("'\"") if name else None)


def _installed(cwds):
    """Claude Code skills and subagents installed for the user (~/.claude) or a
    project (<cwd>/.claude), with how big a description each adds to every session."""
    key = tuple(sorted(c for s_, c in cwds if s_ == "claude"))
    if (time.time() - _installed_cache["at"] < 60 and _installed_cache["key"] == key
            and _installed_cache["data"] is not None):
        return _installed_cache["data"]
    out = []
    roots = [("user", "*", os.path.join(P.HOME, ".claude"))]
    roots += [("project", P._leaf(c) or c, os.path.join(c, ".claude")) for c in key[:300]
              if os.path.normpath(c) != os.path.normpath(P.HOME)]
    seen = set()
    for scope, project, root in roots:
        for kind, pattern in (("skill", os.path.join(root, "skills", "*", "SKILL.md")),
                              ("agent", os.path.join(root, "agents", "*.md"))):
            for path in glob.glob(pattern):
                if path in seen or not os.path.isfile(path):   # a dangling symlink
                    continue
                seen.add(path)
                n, fm_name = _frontmatter_desc_len(path)
                name = fm_name or (os.path.basename(os.path.dirname(path)) if kind == "skill"
                                   else os.path.splitext(os.path.basename(path))[0])
                out.append({"kind": kind, "name": name, "scope": scope, "project": project,
                            "desc_chars": n})
    _installed_cache.update(at=time.time(), key=key, data=out)
    return out


def _mcp_servers():
    """Which MCP servers the user has CONFIGURED for Claude Code.

    Every connected server's tool definitions are injected into the system prompt
    of every request, so one that is never called is a standing tax on each turn.
    Read from ~/.claude.json: `mcpServers` globally plus per-project overrides.
    """
    if time.time() - _mcp_cache["at"] < 60 and _mcp_cache["data"] is not None:
        return _mcp_cache["data"]
    out = {}
    path = os.path.join(P.HOME, ".claude.json")
    try:
        with open(path) as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}
    for name in (cfg.get("mcpServers") or {}):
        out.setdefault(name, {"name": name, "scope": "global", "projects": []})
    for proj, v in (cfg.get("projects") or {}).items():
        for name in ((v or {}).get("mcpServers") or {}):
            e = out.setdefault(name, {"name": name, "scope": "project", "projects": []})
            e["projects"].append(P._leaf(proj) or proj)
    for name in _codex_config()["servers"]:
        out.setdefault("codex:" + name,
                       {"name": name, "scope": "global", "projects": [], "tool": "codex"})
    for e in out.values():
        e.setdefault("tool", "claude")
    data = sorted(out.values(), key=lambda x: (x["tool"], x["name"].lower()))
    _mcp_cache.update(at=time.time(), data=data)
    return data


_CODEX_MCP_RE = __import__("re").compile(r"^\s*\[mcp_servers\.([^.\]]+)\]", __import__("re").M)


def _codex_config():
    """Codex's config.toml, read WITHOUT a TOML parser — tomllib is 3.11+ and this
    project supports 3.8. Only two things are needed and both are line-shaped."""
    path = os.path.join(P.HOME, ".codex", "config.toml")
    try:
        with open(path) as f:
            txt = f.read()
    except Exception:
        return {"servers": [], "effort": None}
    servers = sorted(set(_CODEX_MCP_RE.findall(txt)))
    m = __import__("re").search(r'^\s*model_reasoning_effort\s*=\s*"([^"]+)"', txt, __import__("re").M)
    return {"servers": servers, "effort": m.group(1) if m else None}


def build_settings():
    cur = _read_claude_settings()
    return {
        "claude_settings_path": CLAUDE_SETTINGS_PATH,
        "claude_settings_exists": os.path.exists(CLAUDE_SETTINGS_PATH),
        "claude_cleanup_days": cur.get("cleanupPeriodDays"),
        "claude_cleanup_default": CLAUDE_CLEANUP_DEFAULT,
        "cache_path": CACHE_PATH,
        "cache_bytes": os.path.getsize(CACHE_PATH) if os.path.exists(CACHE_PATH) else 0,
        "cache_files": len(_state["files"]),
    }


def save_claude_cleanup_days(value):
    """value: an int (new retention window), or None to remove the override and
    fall back to Claude Code's own default."""
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("cleanupPeriodDays must be a whole number of days")
        if value < 1 or value > 36500:
            raise ValueError("cleanupPeriodDays must be between 1 and 36500")
    data = _read_claude_settings()
    if value is None:
        data.pop("cleanupPeriodDays", None)
    else:
        data["cleanupPeriodDays"] = value
    _write_claude_settings(data)
    return build_settings()


def cache_action(action):
    # Clearing the store during a refresh otherwise strands its updates in the
    # old dictionary and can race a cache save. Serialize both actions too.
    with _refresh_lock:
        return _cache_action_locked(action)


def _cache_action_locked(action):
    """Rebuild or delete this app's OWN analytics cache (never a tool's logs).

    Both discard the durable ledger: sessions whose logs a tool has already pruned
    from disk exist ONLY in this cache, and nothing can bring them back. The UI
    says so before either button is pressed.
    """
    if action not in ("rebuild", "delete"):
        raise ValueError("action must be 'rebuild' or 'delete'")
    _preserve_ledger_locked()
    removed = False
    if os.path.exists(CACHE_PATH):
        try:
            os.remove(CACHE_PATH)
            removed = True
        except OSError as e:
            raise ValueError(f"could not delete the cache: {e}")
    _cache_error["v"] = None       # explicitly confirmed destructive action
    with _lock:
        had = len(_state["files"])
        _state["files"] = {}
    _gen["v"] += 1
    if action == "rebuild":
        _dirty["v"] = True
        _refresh_locked(verbose=False)
        with _lock:
            now = len(_state["files"])
        return {"action": "rebuild", "dropped": had, "files": now,
                "seconds": round(_meta["last_duration"], 1)}
    # delete: leave the store empty; the next background refresh repopulates it
    # from whatever logs are still on disk.
    _dirty["v"] = False
    return {"action": "delete", "dropped": had, "file_removed": removed}


def _cleanup_targets():
    """Directories the cleanup commands sweep, as (dir, path-glob, name-glob).

    Derived from the same globs `parser.discover()` scans, so the commands and the
    "reclaimable" figure are two views of ONE rule and cannot drift apart.
    Cursor and Hermes are absent on purpose: each keeps a single live SQLite store,
    and deleting it would destroy its chat history rather than reclaim stale logs.
    """
    t = []
    for g in P.CLAUDE_GLOBS + P.CODEX_GLOBS:
        head = g.split("**")[0].rstrip(os.sep)
        if os.path.isdir(head):
            t.append((head, None, "*.jsonl"))
    for g in P.CLAUDE_DESKTOP_GLOBS:                 # skip the audit.jsonl mirror
        head = g.split("**")[0].rstrip(os.sep)
        if os.path.isdir(head):
            t.append((head, "*/.claude/projects/*", "*.jsonl"))
    for r in P.COPILOT_ROOTS:                        # sessions are .json AND .jsonl
        ws = os.path.join(r, "User", "workspaceStorage")
        if os.path.isdir(ws):
            t.append((ws, "*/chatSessions/*", None))
        ew = os.path.join(r, "User", "globalStorage", "emptyWindowChatSessions")
        if os.path.isdir(ew):
            t.append((ew, None, None))
    return t


def _swept_by_cleanup(path, targets):
    """True if the generated commands would delete this file."""
    import fnmatch
    norm = path.replace("\\", "/")
    for d, pathglob, nameglob in targets:
        if not norm.startswith(d.replace("\\", "/").rstrip("/") + "/"):
            continue
        if pathglob and not fnmatch.fnmatch(norm, "*" + pathglob.lstrip("*")):
            continue
        if nameglob and not fnmatch.fnmatch(os.path.basename(norm), nameglob):
            continue
        return True
    return False


def _is_stale(mtime, now, days):
    """Exactly the predicate this platform's cleanup command uses, so the
    "reclaimable" figure always equals what running it would delete.

    POSIX `find -mtime +N` compares the age in WHOLE 24h units and matches only
    when that integer is > N (so a 90.5-day-old file is NOT matched by +90).
    PowerShell's `LastWriteTime -lt (Get-Date).AddDays(-N)` is an exact instant.
    """
    if not mtime:
        return False
    if IS_WINDOWS:
        return mtime < now - days * 86400
    return int((now - mtime) // 86400) > days


def _cleanup_plan(days=90):
    """Delete-old-logs commands for THIS machine: real paths, right shell.

    The dashboard never deletes anything itself — it hands over commands the user
    can read first. Paths come from the parser's own globs, so they are correct on
    macOS, Linux and Windows alike."""
    cmds = []
    for d, pathglob, nameglob in _cleanup_targets():
        if IS_WINDOWS:
            filt = f'-Filter {nameglob} ' if nameglob else ""
            frag = pathglob.strip("*/").split("/")[0] if pathglob else None
            extra = f'$_.FullName -like "*{frag}*" -and ' if frag else ""
            cmds.append(f'Get-ChildItem -LiteralPath "{d}" -Recurse -File {filt}| '
                        f'Where-Object {{ {extra}'
                        f'$_.LastWriteTime -lt (Get-Date).AddDays(-{days}) }} | Remove-Item -Force')
        else:
            pf = f"-path '{pathglob}' " if pathglob else ""
            nf = f"-name '{nameglob}' " if nameglob else ""
            cmds.append(f"find '{d}' {pf}{nf}-type f -mtime +{days} -delete")
    return {"shell": "PowerShell" if IS_WINDOWS else "bash / zsh",
            "days": days, "commands": cmds}


def build_storage():
    # Only scalar aggregate fields are used below; copy them under the refresh
    # lock, then release it before the filesystem scans.
    with _refresh_lock, _lock:
        files = [dict(a) for a in _state["files"].values()]
    per, rows, growth = {}, [], {}
    for agg in files:
        src = agg.get("source", "?")
        slot = per.setdefault(src, {"source": src, "files": 0, "bytes": 0,
                                    "archived_files": 0})
        if agg.get("archived"):
            slot["archived_files"] += 1
            continue                       # pruned from disk: costs no space now
        size = int(agg.get("size") or 0)
        slot["files"] += 1
        slot["bytes"] += size
        last = agg.get("last_ts") or ""
        day = last[:10] or "unknown"
        gk = (day, src)
        growth[gk] = growth.get(gk, 0) + size
        if size:
            rows.append({
                "path": agg.get("path", ""),
                "source": src,
                "project": agg.get("project") or "(unknown)",
                "title": agg.get("title"),
                "bytes": size,
                "mtime": agg.get("mtime") or 0,
                "last": last,
            })
    rows.sort(key=lambda r: -r["bytes"])
    # Reclaimable is computed over EVERY live file, not over the truncated list
    # the UI receives, so the headline number can't quietly undercount.
    now = time.time()
    reclaim = {}
    targets = _cleanup_targets()
    cleanable = [r for r in rows if _swept_by_cleanup(r["path"], targets)]
    for d in (30, 90, 180):
        stale = [r for r in cleanable if _is_stale(r["mtime"], now, d)]
        reclaim[str(d)] = {"files": len(stale), "bytes": sum(r["bytes"] for r in stale)}
    try:
        du = shutil.disk_usage(HOME)
        disk = {"total": du.total, "used": du.used, "free": du.free}
    except Exception:
        disk = {"total": 0, "used": 0, "free": 0}
    try:
        cache_bytes = os.path.getsize(CACHE_PATH)
    except OSError:
        cache_bytes = 0
    return {
        "generated_at": time.time(),
        "sources": sorted(per.values(), key=lambda r: -r["bytes"]),
        "files": rows[:1000],
        "files_total": len(rows),
        "reclaimable": reclaim,
        "cleanup": _cleanup_plan(),
        "platform": ("windows" if IS_WINDOWS else
                     "macos" if sys.platform == "darwin" else "linux"),
        "growth": [{"date": d, "source": s, "bytes": b} for (d, s), b in growth.items()],
        "extras": _extras(),
        "disk": disk,
        "cache_bytes": cache_bytes,
        "home": HOME,
    }


# ---------------------------------------------------------------------------
# Devices — see your other machines' usage here, and let them see this one's.
# Strictly opt-in from Settings, over the local network, and the ONLY mode in
# which anything leaves this machine. Two separate switches:
#   * Share: a SECOND, read-only listener on the network (PEER_PORT) answering one
#     route, GET /api/peer/export, and only to a caller holding this device's
#     pairing code. The dashboard itself stays on 127.0.0.1, so its write
#     endpoints are never reachable from the network.
#   * Connect: pull another device's export every PEER_PULL_EVERY seconds and keep
#     the last copy in .peers/<id>.json, so it still shows while that one sleeps.
# An export carries only this machine's OWN logs, never the copies it pulled, so
# two devices connected both ways never count each other twice. Nothing is
# encrypted: the code keeps others on the Wi-Fi from reading it, not from sniffing.
# ---------------------------------------------------------------------------
PEERS_PATH = os.path.join(DATA_DIR, ".peers.json")   # this install's id, sharing code, connections
PEER_DIR = os.path.join(DATA_DIR, ".peers")          # the last copy pulled from each device
PEER_PORT = 7879
PEER_PULL_EVERY = 60
PEER_PROTO = 1
PEER_MAX_BYTES = 512 * 1024 * 1024               # decompressed; a typical export is a few MB
_CODE_ALPHA = "ABCDEFGHJKMNPQRSTVWXYZ23456789"   # no 0/O, 1/I/L or U: it is typed by hand
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_START = int(time.time())

_peer_lock = threading.RLock()
_sync_lock = threading.Lock()     # one pull / connect / disconnect at a time
_peer = {"cfg": None, "mirrors": {}, "server": None, "share_error": None,
         "pulled_by": {}, "export": None}


def _new_code():
    raw = "".join(secrets.choice(_CODE_ALPHA) for _ in range(12))
    return "-".join(raw[i:i + 4] for i in range(0, 12, 4))


def _norm_code(c):
    return re.sub(r"[^A-Z0-9]", "", str(c or "").upper())


def _save_peer_cfg():
    """Atomic, and 0600: it holds pairing codes."""
    tmp = PEERS_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(_peer["cfg"], f, indent=1)
    os.replace(tmp, PEERS_PATH)


def _peer_cfg():
    with _peer_lock:
        if _peer["cfg"] is None:
            cfg, fresh = {}, not os.path.exists(PEERS_PATH)
            if not fresh:
                try:
                    with open(PEERS_PATH, encoding="utf-8") as f:
                        cfg = json.load(f)
                except (OSError, ValueError) as e:
                    sys.stderr.write(f"[devices] {PEERS_PATH} unreadable, starting empty: {e}\n")
            if not isinstance(cfg, dict):
                cfg = {}
            if not _ID_RE.match(str(cfg.get("device_id") or "")):
                cfg["device_id"], fresh = uuid.uuid4().hex, True
            share = cfg.setdefault("share", {})
            share.setdefault("on", False)
            share.setdefault("code", None)
            share.setdefault("port", PEER_PORT)
            cfg.setdefault("peers", {})
            _peer["cfg"] = cfg
            if fresh:
                _save_peer_cfg()
        return _peer["cfg"]


def _local_id():
    return _peer_cfg()["device_id"]


def _mirror_path(pid):
    return os.path.join(PEER_DIR, pid + ".json")


def _set_mirror(pid, m):
    """Accept current or explicitly compatible copies; reject unknown shapes."""
    with _peer_lock:
        p = _peer_cfg()["peers"].get(pid)
        if p is None:                          # disconnected while the pull ran
            return
        if not _peer_cache_ok(m.get("cache_version")):
            _peer["mirrors"].pop(pid, None)
            p["error"] = ("Its saved copy is from a different AgentTelemetry version. "
                          "Update both devices to the same version.")
            return
        files = m.get("files") or {}
        for a in files.values():
            a["_device"] = pid
        _peer["mirrors"][pid] = files


def _load_mirrors():
    for pid in list(_peer_cfg()["peers"]):
        try:
            with open(_mirror_path(pid), encoding="utf-8") as f:
                m = json.load(f)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            sys.stderr.write(f"[devices] copy of {pid} unreadable: {e}\n")
            continue
        _set_mirror(pid, m)


def _peer_items():
    with _peer_lock:
        return [(f"peer:{pid}:{path}", a) for pid, files in _peer["mirrors"].items()
                for path, a in files.items()]


def _devices_meta():
    cfg = _peer_cfg()
    out = [{"id": cfg["device_id"], "name": DEVICE["name"], "os": DEVICE["os"], "local": True}]
    with _peer_lock:
        for pid, p in cfg["peers"].items():
            out.append({"id": pid, "name": p.get("name") or "Other device", "os": p.get("os") or "",
                        "local": False, "synced": p.get("last_ok"), "error": p.get("error"),
                        "shown": pid in _peer["mirrors"]})
    return out


# What leaves this machine for each log: what build_payload reads, nothing else.
# Parser resume state, the working directory and MCP/skill setup stay here.
_EXPORT_KEYS = ("source", "path", "mtime", "archived", "project", "title", "branch", "editor",
                "entry", "cliver", "subagent", "first_ts", "last_ts", "records", "tools",
                "skills", "ctx", "hourly", "reads", "totals", "sessions", "open_ctx",
                "opencode_ids", "opencode_messages", "compactions", "copilot_sdk")


def _export_agg(agg):
    a = {k: agg[k] for k in _EXPORT_KEYS if k in agg}
    # resolved here: Codex's editor lookup reads THIS machine's editor storage
    a["ide"] = P._ide_of(agg["source"], agg)
    # the still-open last turn folded in, so the importer needs no parser state
    a["activity"] = P.activity_of(agg)
    st = agg.get("state") or {}
    a["state"] = {k: st[k] for k in ("dom_model", "ai_lines") if k in st}
    return a


def _export_body():
    """gzip'd JSON of this machine's own aggregates, rebuilt only when they changed."""
    etag = f'"{_local_id()[:12]}-{_START}-{_gen["v"]}-{CACHE_VERSION}"'
    cached = _peer["export"]
    if cached and cached[0] == etag:
        return cached
    # the parser mutates aggregates in place while it runs
    if not _refresh_lock.acquire(timeout=20):
        raise TimeoutError
    try:
        with _lock:
            items = list(_state["files"].items())
        files = {path: _export_agg(a) for path, a in items}
    finally:
        _refresh_lock.release()
    doc = {"proto": PEER_PROTO, "cache_version": CACHE_VERSION, "device_id": _local_id(),
           "device": DEVICE, "app": VERSION.get("describe") or VERSION.get("commit"),
           "files": files}
    _peer["export"] = (etag, gzip.compress(json.dumps(doc).encode("utf-8"), 6))
    return _peer["export"]


class PeerHandler(BaseHTTPRequestHandler):
    """The only thing on this machine reachable from the network, and only while
    sharing is on: one read-only route behind the pairing code."""
    server_version = "AgentTelemetry"

    def log_message(self, *a):
        pass

    def _plain(self, code, text):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] != "/api/peer/export":
            self._plain(404, "not found")
            return
        share = _peer_cfg()["share"]
        auth = self.headers.get("Authorization") or ""
        given = _norm_code(auth[7:] if auth[:7].lower() == "bearer " else "")
        want = _norm_code(share.get("code"))
        if not (share.get("on") and want and hmac.compare_digest(given.encode(), want.encode())):
            time.sleep(1)                      # every wrong guess costs a second
            self._plain(401, "pairing code required")
            return
        with _peer_lock:
            _peer["pulled_by"][self.client_address[0]] = {
                "at": time.time(),
                "name": urllib.parse.unquote(self.headers.get("X-AgentTelemetry-Device") or "")[:80]}
        try:
            etag, body = _export_body()
        except TimeoutError:
            self._plain(503, "busy parsing, try again shortly")
            return
        # where else this device answers, so the reader can fall back when the
        # address it was given stops working (a new IP, a .local lookup that failed)
        port = self.server.server_address[1]
        addrs = ", ".join(f"{a}:{port}" for a in _lan_addrs())
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("X-AgentTelemetry-Addrs", addrs)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("ETag", etag)
        self.send_header("X-AgentTelemetry-Addrs", addrs)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def _share_start():
    port = int(_peer_cfg()["share"].get("port") or PEER_PORT)
    with _peer_lock:
        if _peer["server"]:
            return True
        try:
            srv = Server(("0.0.0.0", port), PeerHandler)
        except OSError as e:
            _peer["share_error"] = f"Couldn't open port {port}: {e.strerror or e}"
            sys.stderr.write(f"[devices] {_peer['share_error']}\n")
            return False
        _peer["server"], _peer["share_error"] = srv, None
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    sys.stderr.write(f"[devices] sharing this device's usage on port {port}\n")
    return True


def _share_stop():
    with _peer_lock:
        srv, _peer["server"] = _peer["server"], None
        _peer["pulled_by"].clear()
    if srv:
        srv.shutdown()
        srv.server_close()
        sys.stderr.write("[devices] sharing stopped\n")


_HOME_NETS = [ipaddress.ip_network(n) for n in ("192.168.0.0/16", "10.0.0.0/8")]
_VIRTUAL_NETS = [ipaddress.ip_network(n) for n in ("172.16.0.0/12", "100.64.0.0/10")]
_addr_cache = {"at": 0.0, "v": []}


def _lan_addrs():
    """How the other device can reach this one: its .local name (stable across
    networks), then its Wi-Fi/Ethernet address.

    The address the OS would use to reach the internet is not enough on its own. A
    VPN takes that route over (1.1.1.1's WARP answers as 172.16.0.2), and so can a
    VM or WSL adapter, and nothing else on the Wi-Fi can reach those. So every
    address the machine holds is ranked: home and office ranges first, then the
    ranges VPNs, WSL, Docker and Tailscale use, which are shown only when there's
    nothing better."""
    if time.time() - _addr_cache["at"] < 30:
        return _addr_cache["v"]
    names, ips = [], []
    if sys.platform == "darwin":
        try:
            n = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True,
                               text=True, timeout=2).stdout.strip()
            if n:
                names.append(n + ".local")
        except (OSError, subprocess.SubprocessError) as e:
            sys.stderr.write(f"[devices] scutil failed: {e}\n")
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))        # a UDP connect only picks a route; nothing is sent
            ips.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:                                       # every adapter's address on Windows and macOS
        ips += socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        pass
    rank = {}
    for ip in ips:
        try:
            a = ipaddress.IPv4Address(ip)
        except ValueError:
            continue
        if a.is_loopback or a.is_link_local or a.is_multicast or a.is_unspecified:
            continue
        rank.setdefault(ip, 0 if any(a in n for n in _HOME_NETS)
                        else 1 if any(a in n for n in _VIRTUAL_NETS) else 2)
    best = min(rank.values(), default=None)
    out = names + [ip for ip, r in rank.items() if r == best][:2]
    _addr_cache.update(at=time.time(), v=out)
    return out


def _hostport(addr):
    """(host, port) from what the user typed: 192.168.1.20, its-name.local:7879,
    or a pasted http:// URL."""
    a = re.sub(r"^\s*https?://", "", str(addr or "").strip()).split("/")[0]
    m = re.fullmatch(r"([A-Za-z0-9.-]+)(?::(\d{1,5}))?", a)
    port = int(m.group(2) or PEER_PORT) if m else 0
    if not m or not 0 < port < 65536:
        raise ValueError("Enter the other device's address, like 192.168.1.20 or its-name.local")
    return m.group(1).lower(), port


def _addr_key(addr):
    try:
        return "%s:%d" % _hostport(addr)
    except ValueError:
        return None


_NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # it's the LAN


class _Unreachable(ValueError):
    """Nothing answered at that address, so another one may still work."""


_REACH_HINT = ("Check that both devices are on the same Wi-Fi and that the other one's "
               "firewall lets AgentTelemetry in. A VPN (like 1.1.1.1 with WARP) on either "
               "device can also get in the way.")


def _reach(addr):
    """Fail in seconds, not the pull's 30s, and say why in words: a name that
    didn't resolve, a port with nothing on it, or silence."""
    host, port = _hostport(addr)
    try:
        socket.create_connection((host, port), timeout=5).close()
    except socket.gaierror:
        raise _Unreachable(f"Couldn't find {host} on this network. Try its IP address instead; "
                           "it's shown under Share this device over there.")
    except ConnectionRefusedError:
        raise _Unreachable(f"Nothing is listening at {host}:{port}. Turn on sharing on that "
                           "device, and check the address.")
    except socket.timeout:
        raise _Unreachable(f"{host}:{port} didn't answer. {_REACH_HINT}")
    except OSError as e:
        raise _Unreachable(f"Couldn't reach {host}:{port} ({e.strerror or e}). {_REACH_HINT}")
    return host, port


def _alt_addrs(headers):
    """The other addresses the sharing device says it answers on."""
    out = []
    for a in (headers.get("X-AgentTelemetry-Addrs") or "").split(","):
        k = _addr_key(a)
        if k and k not in out:
            out.append(k)
    return out[:4]


def _pull(addr, code, etag=None):
    """(export, etag, other addresses) from another device. export is None when
    it hasn't changed."""
    host, port = _reach(addr)
    req = urllib.request.Request(f"http://{host}:{port}/api/peer/export", headers={
        "Authorization": "Bearer " + _norm_code(code), "Accept-Encoding": "gzip",
        # quoted: a header is Latin-1, and names are not ("Uttam’s MacBook Air")
        "X-AgentTelemetry-Device": urllib.parse.quote(DEVICE["name"])})
    if etag:
        req.add_header("If-None-Match", etag)
    try:
        with _NO_PROXY.open(req, timeout=30) as r:
            raw = r.read(PEER_MAX_BYTES + 1)
            new_etag = r.headers.get("ETag")
            alts = _alt_addrs(r.headers)
            gz = (r.headers.get("Content-Encoding") or "").lower() == "gzip"
    except urllib.error.HTTPError as e:
        if e.code == 304:
            return None, etag, _alt_addrs(e.headers)
        if e.code == 401:
            raise ValueError("The pairing code was rejected. Check it in the other device's "
                             "Settings → Your devices.")
        if e.code == 503:
            raise ValueError("That device is still parsing its logs; it will be retried.")
        raise ValueError(f"That address answered HTTP {e.code}, not as AgentTelemetry.")
    except (urllib.error.URLError, OSError) as e:
        raise _Unreachable(f"Couldn't reach {host}:{port} ({getattr(e, 'reason', e)}). "
                           f"{_REACH_HINT}")
    if gz:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as g:
            raw = g.read(PEER_MAX_BYTES + 1)
    if len(raw) > PEER_MAX_BYTES:
        raise ValueError("That device's export is too large.")
    try:
        data = json.loads(raw)
    except ValueError:
        data = None
    if (not isinstance(data, dict) or data.get("proto") != PEER_PROTO
            or not isinstance(data.get("files"), dict)
            or not _ID_RE.match(str(data.get("device_id") or ""))):
        raise ValueError("That address answered, but not as AgentTelemetry.")
    if not _peer_cache_ok(data.get("cache_version")):
        raise ValueError(f"That device runs a different AgentTelemetry version "
                         f"({data.get('app') or 'unknown'}). Update both to the same version.")
    # a malformed entry would break the whole payload; drop it rather than guess
    data["files"] = {p: a for p, a in data["files"].items()
                     if isinstance(a, dict) and isinstance(a.get("source"), str)
                     and isinstance(a.get("records", {}), dict)
                     and isinstance(a.get("sessions", []), list)}
    return data, new_etag, alts


def _store_mirror(pid, data):
    m = {"cache_version": data["cache_version"], "device": data.get("device") or {},
         "app": data.get("app"), "pulled_at": time.time(), "files": data["files"]}
    os.makedirs(PEER_DIR, exist_ok=True)
    tmp = _mirror_path(pid) + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(m, f)
    os.replace(tmp, _mirror_path(pid))
    _set_mirror(pid, m)


def _pull_any(p, etag):
    """_pull, trying the address that last worked, then the one the user typed,
    then the others the device said it answers on. An IP changes when the router
    hands out a new one, and a .local name sometimes fails to resolve, so one of
    the others often still works."""
    tried, first = [], None
    for a in [p.get("last_addr"), p.get("addr")] + list(p.get("alts") or []):
        k = _addr_key(a)
        if not k or k in tried:
            continue
        tried.append(k)
        try:
            data, etag, alts = _pull(a, p.get("code"), etag)
        except _Unreachable as e:
            first = first or e
            continue
        p.update(last_addr=k, alts=alts or p.get("alts") or [])
        return data, etag
    if first and len(tried) > 1:
        raise _Unreachable(f"{first} Also tried {', '.join(tried[1:])}.")
    raise first or ValueError("This device has no address saved. Disconnect and connect again.")


def _sync_peer(pid):
    """Pull one connected device. Call with _sync_lock held."""
    p = _peer_cfg()["peers"].get(pid)
    if p is None:
        return
    p["last_try"] = time.time()
    try:
        data, etag = _pull_any(p, p.get("etag") if pid in _peer["mirrors"] else None)
        if data is not None:
            if data["device_id"] != pid:
                raise ValueError("A different device now answers at that address. "
                                 "Disconnect this one and connect again.")
            dev = data.get("device") or {}
            p.update(name=dev.get("name") or p.get("name"), os=dev.get("os") or p.get("os"),
                     app=data.get("app"))
            _store_mirror(pid, data)
        p.update(etag=etag, last_ok=time.time(), error=None)
    except ValueError as e:
        p["error"] = str(e)
    with _peer_lock:
        _save_peer_cfg()


def peer_puller(stopping=None):
    while True:
        for pid in list(_peer_cfg()["peers"]):
            try:
                with _sync_lock:
                    _sync_peer(pid)
            except Exception as e:             # never let one bad pull stop the loop
                sys.stderr.write(f"[devices] pulling {pid}: {e}\n")
        if stopping is None:
            time.sleep(PEER_PULL_EVERY)
        elif stopping.wait(PEER_PULL_EVERY):
            return


def devices_status():
    cfg = _peer_cfg()
    share = cfg["share"]
    with _peer_lock:
        sharing = bool(_peer["server"])
        by = sorted(({"ip": ip, **v} for ip, v in _peer["pulled_by"].items()),
                    key=lambda x: -x["at"])
        peers = [{"id": pid, "name": p.get("name") or "Other device", "os": p.get("os") or "",
                  "addr": p.get("addr"), "app": p.get("app"), "last_ok": p.get("last_ok"),
                  # set only when it answered somewhere other than the address typed
                  "via": (p.get("last_addr") if p.get("last_addr")
                          and p.get("last_addr") != _addr_key(p.get("addr")) else None),
                  "last_try": p.get("last_try"), "error": p.get("error"),
                  "logs": len(_peer["mirrors"].get(pid) or {}), "shown": pid in _peer["mirrors"]}
                 for pid, p in cfg["peers"].items()]
        err = _peer["share_error"]
    return {"this": {"id": cfg["device_id"], "name": DEVICE["name"], "os": DEVICE["os"],
                     "addresses": _lan_addrs() if sharing else [],
                     "port": int(share.get("port") or PEER_PORT), "sharing": sharing,
                     "code": share.get("code") if sharing else None, "error": err,
                     "pulled_by": by, "platform": ("windows" if IS_WINDOWS else "macos"
                                                   if sys.platform == "darwin" else "linux")},
            "peers": peers, "pull_every": PEER_PULL_EVERY}


def devices_action(body):
    act = body.get("action")
    cfg = _peer_cfg()
    if act == "share":
        on = bool(body.get("on"))
        with _peer_lock:
            if on and not cfg["share"].get("code"):
                cfg["share"]["code"] = _new_code()
            cfg["share"]["on"] = on
            _save_peer_cfg()
        if not on:
            _share_stop()
        elif not _share_start():
            with _peer_lock:
                cfg["share"]["on"] = False
                _save_peer_cfg()
            raise ValueError(_peer["share_error"])
    elif act == "new_code":
        # every device connected with the old code loses access until it reconnects
        with _peer_lock:
            cfg["share"]["code"] = _new_code()
            _save_peer_cfg()
    elif act == "connect":
        addr, code = str(body.get("address") or "").strip(), body.get("code")
        if not addr:
            raise ValueError("Enter the other device's address.")
        if not _norm_code(code):
            raise ValueError("Enter the pairing code shown on the other device.")
        with _sync_lock:
            data, etag, alts = _pull(addr, code)
            pid = data["device_id"]
            if pid == cfg["device_id"]:
                raise ValueError("That's this device. Enter the address of your other one.")
            dev = data.get("device") or {}
            now = time.time()
            with _peer_lock:
                cfg["peers"][pid] = {"addr": addr, "code": _norm_code(code),
                                     "name": dev.get("name"), "os": dev.get("os"),
                                     "app": data.get("app"), "added": now, "last_ok": now,
                                     "last_try": now, "etag": etag, "error": None,
                                     "last_addr": _addr_key(addr), "alts": alts}
                _save_peer_cfg()
            _store_mirror(pid, data)
    elif act == "disconnect":
        pid = str(body.get("id") or "")
        with _sync_lock, _peer_lock:
            if pid not in cfg["peers"]:
                raise ValueError("That device isn't connected.")
            del cfg["peers"][pid]
            _peer["mirrors"].pop(pid, None)
            _save_peer_cfg()
            try:
                os.remove(_mirror_path(pid))
            except FileNotFoundError:
                pass
    elif act == "sync":
        with _sync_lock:
            for pid in list(cfg["peers"]):
                _sync_peer(pid)
    else:
        raise ValueError("unknown action")
    return devices_status()


def _zero():
    return {"in": 0, "out": 0, "cr": 0, "cc": 0, "cc5": 0, "cc1": 0, "reason": 0,
            "asst": 0, "user": 0, "req": 0, "tools": 0, "prem": 0.0, "cost": 0.0,
            "active": 0.0, "ws": 0}


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, name, ctype):
        path = os.path.join(HERE, name)
        if not os.path.exists(path):
            self._send(404, "not found", "text/plain")
            return
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def _static(self, route):
        """Serve static/* — the split frontend (css/js). Path-traversal guarded."""
        rel = route[len("/static/"):]
        base = os.path.join(HERE, "static")
        path = os.path.normpath(os.path.join(base, rel))
        if not path.startswith(base + os.sep) or not os.path.isfile(path):
            self._send(404, "not found", "text/plain")
            return
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
            ctype += "; charset=utf-8"
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if not self._origin_ok():
            self._send(403, json.dumps({"error": "cross-site request refused"}))
            return
        route = self.path.split("?")[0]
        if route in ("/", "/index.html"):
            self._file("index.html", "text/html; charset=utf-8")
        elif route == "/api/health":
            self._send(200, json.dumps({
                "service": "agent-telemetry",
                "version": VERSION.get("describe") or VERSION.get("commit") or "development",
                "ready": not bool(_meta.get("building")),
                "building": bool(_meta.get("building")),
                "pid": os.getpid(),
            }))
        elif route == "/api/native":
            self._send(200, json.dumps(N.status()))
        elif route == "/api/sync":
            self._send(200, json.dumps(refresh_sync()))
        elif route == "/api/accounts":
            self._send(200, json.dumps(build_account_payload()))
        elif route == "/api/backups":
            self._send(200, json.dumps(backup_status()))
        elif route == "/api/diagnostics":
            self._send(200, json.dumps(build_diagnostics()))
        elif route == "/api/editor":
            if not self._loopback_client():
                self._send(403, json.dumps({"error": "editor details are available only on this machine"}))
                return
            try:
                result = build_editor_snapshot(urllib.parse.urlsplit(self.path).query)
                self._send(200, json.dumps(result, allow_nan=False))
            except ValueError:
                self._send(400, json.dumps({"error": "Invalid editor scope, range or roots."}))
            except Exception:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": "Editor usage is temporarily unavailable."}))
        elif route == "/api/session-timeline":
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try:
                result = build_session_timeline(*(query.get(k, [""])[0] for k in ("device", "source", "id")),
                                                date_from=query.get("from", [None])[0], date_to=query.get("to", [None])[0])
                self._send(200, json.dumps(result))
            except ValueError as error:
                self._send(400, json.dumps({"error": str(error)}))
            except OSError:
                self._send(200, json.dumps({"available": False, "events": [], "truncated": False,
                                           "detail": "The source log could not be read."}))
        elif route == "/api/summary":
            try:
                self._send(200, client_snapshot()[1])
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/chart.js":
            self._file("chart.umd.min.js", "application/javascript")
        elif route == "/manifest.json":
            self._file("manifest.json", "application/manifest+json; charset=utf-8")
        elif route == "/sw.js":
            self._file("sw.js", "application/javascript; charset=utf-8")
        elif route.startswith("/static/"):
            self._static(route)
        elif route == "/api/storage":
            try:
                self._send(200, json.dumps(build_storage()))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/api/local-servers":
            if not self._loopback_client():
                self._send(403, json.dumps({"error": "local server details are available only on this machine"}))
                return
            try:
                self._send(200, json.dumps(LocalServerInventory(DATA_DIR).snapshot()))
            except Exception as e:
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/api/refresh":
            self._send(405, json.dumps({"error": "use POST with a JSON body"}))
        elif route == "/api/data":
            try:
                self._send(200, client_snapshot()[0])
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/api/devices":
            try:
                self._send(200, json.dumps(devices_status()))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        elif route == "/api/settings":
            try:
                self._send(200, json.dumps(build_settings()))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, "not found", "text/plain")

    def _origin_ok(self):
        """Pin every dashboard request to this listener, including private GETs."""
        try:
            authority = urllib.parse.urlsplit("//" + (self.headers.get("Host") or ""))
            host, port = authority.hostname, authority.port
        except ValueError:
            return False
        if (authority.username is not None or authority.path or authority.query or authority.fragment):
            return False
        bound = str(BIND["host"]).strip().lower()
        allowed_hosts = {"127.0.0.1", "localhost", "::1", "0.0.0.0", bound}
        if bound in ("0.0.0.0", "::"):
            allowed_hosts.update(str(x).lower() for x in _lan_addrs())
        if not host or host.lower() not in allowed_hosts:
            return False
        if port is not None and port != BIND["port"]:
            return False
        site = (self.headers.get("Sec-Fetch-Site") or "").strip().lower()
        if site and site not in ("same-origin", "none"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                parsed = urllib.parse.urlsplit(origin)
                origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
            except ValueError:
                return False
            if (parsed.scheme not in ("http", "https") or parsed.hostname != host
                    or origin_port != BIND["port"] or parsed.path not in ("", "/")
                    or parsed.query or parsed.fragment or parsed.username is not None):
                return False
        return True

    def _csrf_ok(self):
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        return ctype == "application/json" and self._origin_ok()

    def _loopback_client(self):
        try:
            return ipaddress.ip_address(self.client_address[0]).is_loopback
        except (ValueError, TypeError):
            return False

    def _discard_small_post_body(self):
        """Avoid a TCP reset when rejecting a small, separately sent body.

        Never parse rejected JSON or wait indefinitely for an untrusted sender.
        Unknown, oversized and transfer-encoded requests still close directly.
        """
        if self.headers.get("Transfer-Encoding"):
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            return
        if not 0 < length <= 16384:
            return
        previous_timeout = self.connection.gettimeout()
        deadline = time.monotonic() + 0.2
        try:
            while length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(length)
                if not chunk:
                    return
                length -= len(chunk)
        except (OSError, ValueError):
            return
        finally:
            self.connection.settimeout(previous_timeout)

    def do_POST(self):
        route = self.path.split("?")[0]
        local_server_routes = {
            "/api/local-servers/stop",
            "/api/local-servers/configure",
            "/api/local-servers/pin",
            "/api/local-servers/start",
            "/api/local-servers/restart",
        }
        if route in ("/api/settings", "/api/cache", "/api/update", "/api/devices", "/api/refresh", "/api/native", "/api/shutdown", "/api/sync", "/api/session-name", "/api/accounts", "/api/backups") or route in local_server_routes:
            if route in local_server_routes and not self._loopback_client():
                self._discard_small_post_body()
                self._send(403, json.dumps({"error": "local servers can be managed only on this machine"}))
                return
            if not self._csrf_ok():
                self._discard_small_post_body()
                self._send(403, json.dumps({"error": "cross-site request refused"}))
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
                if length < 0 or (route in local_server_routes and length > 16384):
                    raise ValueError("invalid body length")
            except ValueError:
                self._send(400, json.dumps({"error": "invalid request body length"}))
                return
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("expected a JSON object")
            except Exception:
                self._send(400, json.dumps({"error": "invalid JSON body"}))
                return
            try:
                if route == "/api/accounts":
                    A.refresh_account(body.get("source"), body.get("confirmed"))
                    result = build_account_payload()
                elif route == "/api/backups":
                    try:
                        result = backup_action(body)
                    except OSError as error:
                        raise ValueError("Backup operation failed. Check free space and directory permissions, then retry.") from error
                elif route == "/api/session-name":
                    result = set_session_name(body)
                elif route == "/api/native":
                    result = N.action(body.get("enabled"))
                elif route == "/api/sync":
                    N.set_refresh_interval(body.get("seconds"))
                    notify_refresh()
                    result = refresh_sync()
                elif route == "/api/shutdown":
                    token = os.environ.get("AGENT_TELEMETRY_CONTROL_TOKEN")
                    if not token or not hmac.compare_digest(token, self.headers.get("X-AgentTelemetry-Control") or ""):
                        self._send(403, json.dumps({"error": "only the owning native app can stop this service"}))
                        return
                    self.server.stopping.set()
                    result = {"ok": True}
                elif route == "/api/refresh":
                    refresh(verbose=False)
                    notify_refresh()
                    result = {"ok": True, "meta": dict(_meta)}
                elif route == "/api/cache":
                    result = cache_action(body.get("action"))
                    notify_refresh()
                elif route == "/api/update":
                    result = update_action(body.get("action"))
                elif route == "/api/devices":
                    result = devices_action(body)
                    notify_refresh()
                elif route.startswith("/api/local-servers/"):
                    if route in ("/api/local-servers/start", "/api/local-servers/stop", "/api/local-servers/restart") and body.get("confirmed") is not True:
                        raise ValueError("Confirm this server action before continuing.")
                    inventory = LocalServerInventory(DATA_DIR)
                    if route == "/api/local-servers/stop":
                        result = inventory.stop(body.get("id"))
                    elif route == "/api/local-servers/configure":
                        result = inventory.configure(body.get("id"), body.get("command"), body.get("cwd"))
                    elif route == "/api/local-servers/pin":
                        result = inventory.set_pinned(body.get("id"), body.get("pinned"))
                    elif route == "/api/local-servers/start":
                        result = inventory.start(body.get("profileId"))
                    else:
                        result = inventory.restart(body.get("profileId"), body.get("serverId"))
                else:
                    result = save_claude_cleanup_days(body.get("cleanupPeriodDays"))
                self._send(200, json.dumps(result))
            except ValueError as e:
                self._send(400, json.dumps({"error": str(e)}))
            except Exception as e:
                import traceback; traceback.print_exc()
                self._send(500, json.dumps({"error": str(e)}))
        else:
            self._send(404, "not found", "text/plain")


class Server(ThreadingHTTPServer):
    # socketserver's default listen backlog is 5. A page load opens about seven
    # connections at once (page, Chart.js, three scripts, the stylesheet, the data),
    # so a burst could overflow it and the kernel reset one — a script that
    # silently failed to load ("heroHTML is not defined") once in a few dozen loads.
    request_queue_size = 128
    daemon_threads = True
    allow_reuse_port = False
    # Windows SO_REUSEADDR can let a second listener take the same port. The
    # ledger needs a single owner, so reserve the address exclusively there.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # A browser tab closed/refreshed mid-response is normal traffic, not a
        # server fault — don't spam stderr with a traceback for it.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


def background_refresher(interval, stopping=None):
    while True:
        if stopping is None:
            time.sleep(interval)
        elif stopping.wait(interval):
            return
        try:
            refresh(verbose=False)
        except Exception as e:
            sys.stderr.write(f"[refresh] {e}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=7878)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--interval", type=int, default=20,
                    help="seconds between background incremental refreshes")
    ap.add_argument("--data-dir", help="directory for the usage cache and device-sharing state")
    ap.add_argument("--rebuild", action="store_true", help="ignore cache, full reparse")
    args = ap.parse_args()

    # Reserve the service port before loading, rebuilding or saving the shared
    # ledger. A second package/native launch must fail without becoming a second
    # writer; a separate "is this port free?" probe leaves a race before bind.
    try:
        srv = Server((args.host, args.port), Handler)
    except OSError as e:
        ap.error(f"cannot listen on {args.host}:{args.port}: {e}")

    stopping = threading.Event()
    srv.stopping = stopping
    loaded = False
    try:
        try:
            _configure_data_dir(args.data_dir)
        except OSError as e:
            ap.error(f"cannot use data directory: {e}")

        def _request_shutdown(signum, frame):
            stopping.set()

        signal.signal(signal.SIGTERM, _request_shutdown)
        signal.signal(signal.SIGINT, _request_shutdown)

        if args.rebuild and os.path.exists(CACHE_PATH):
            os.remove(CACHE_PATH)
        load_cache()
        loaded = True
        sys.stderr.write("[init] parsing local logs (first run reads everything, "
                         "incl. one large Codex log)...\n")
        refresh(verbose=True)
        sys.stderr.write(f"[init] {_meta['files']} files in {_meta['last_duration']:.1f}s\n")
        if stopping.is_set():
            return

        threading.Thread(target=background_refresher, args=(args.interval, stopping), daemon=True).start()

        # other devices: only if the user turned them on in Settings
        _load_mirrors()
        if _peer_cfg()["share"].get("on"):
            _share_start()
        threading.Thread(target=peer_puller, args=(stopping,), daemon=True).start()

        BIND.update(host=args.host, port=srv.server_port)
        url = f"http://{args.host}:{srv.server_port}"
        sys.stderr.write(f"\n  ✦ AgentTelemetry live at  {url}  ·  {DEVICE['name']}\n")
        sys.stderr.write(f"    refreshing every {args.interval}s · Ctrl-C to stop\n\n")
        srv.timeout = 0.5
        while not stopping.is_set():
            srv.handle_request()
    finally:
        stopping.set()
        try:
            if loaded:
                _share_stop()
                with _refresh_lock, _lock:
                    if not save_cache():
                        sys.stderr.write("[shutdown] cache flush failed; see the cache error above\n")
                sys.stderr.write("\nbye\n")
        finally:
            # Keep ownership through the final flush: another launcher may use
            # this same ledger as soon as the service port becomes available.
            srv.server_close()


if __name__ == "__main__":
    main()
