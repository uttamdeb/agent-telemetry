"""Private account metadata and account-wide quota snapshots. Stdlib only.

No credential decoding, background network lookup, or quota arithmetic from tokens.
The parser stores only allowlisted observations; peer exports exclude this module's
metadata. Live CLI queries run only after an explicit dashboard action.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import queue
import shutil
import subprocess
import sys
import threading
import time

if __package__:
    from . import native as N
else:
    import native as N

STALE_AFTER = 300
METADATA_VERSION = 1
_lock = threading.Lock()
_live = {}


class AccountChanged(ValueError):
    """A lookup cannot safely retain the previous account's figures."""


def text(value):
    if not isinstance(value, str) or not value or len(value) > 240:
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    return value


def number(value, minimum=0, maximum=None):
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        return None
    if value < minimum or (maximum is not None and value > maximum):
        return None
    return value


def timestamp(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None: return None
            value = parsed.timestamp()
        except (ValueError, OverflowError): return None
    return number(value, 1, 253402300799)


def window(value, label, minutes=None, camel=False):
    if not isinstance(value, dict): return None
    used = number(value.get("usedPercent" if camel else "used_percent"), 0, 100)
    reset = timestamp(value.get("resetsAt" if camel else "resets_at"))
    duration = number(value.get("windowDurationMins" if camel else "window_minutes"), 1, 52560000)
    minutes = duration if duration is not None else minutes
    # An absent percentage means unavailable, never zero used / 100% remaining.
    return {"label": label, "used_percent": used,
            "remaining_percent": None if used is None else round(100 - used, 4),
            "resets_at": reset, "window_minutes": minutes}


def codex_snapshot(value, observed_at, camel=False):
    at = timestamp(observed_at)
    if not isinstance(value, dict) or at is None: return None
    windows = []
    for key in ("primary", "secondary"):
        w = window(value.get(key), key.title(), camel=camel)
        if w:
            mins = w["window_minutes"]
            w["label"] = "5-hour limit" if mins == 300 else "Weekly limit" if mins == 10080 else (
                f"{mins:g}-minute limit" if mins is not None else key.title() + " limit")
            windows.append(w)
    return {"observed_at": at, "limit_id": text(value.get("limitId" if camel else "limit_id")) or "codex",
            "limit_name": text(value.get("limitName" if camel else "limit_name")),
            "plan": text(value.get("planType" if camel else "plan_type")), "windows": windows}


def observe_codex(agg, kind, payload, at):
    metadata = agg.setdefault("account_metadata", {})
    if kind == "session_meta":
        for key in ("creator_account_id", "creator_user_id"):
            value = text(payload.get(key))
            if value: metadata[key] = value
    if kind == "event_msg" and payload.get("type") == "token_count":
        snapshot = codex_snapshot(payload.get("rate_limits"), at)
        if snapshot:
            snapshots = metadata.setdefault("quota_snapshots", {})
            key = snapshot["limit_id"]
            previous = snapshots.get(key)
            if previous is None or snapshot["observed_at"] >= previous["observed_at"]:
                if key in snapshots or len(snapshots) < 16: snapshots[key] = snapshot


def observe_claude(agg, record):
    value = text(record.get("accountUuid"))
    if value:
        ids = agg.setdefault("account_metadata", {}).setdefault("recorded_account_ids", [])
        if value not in ids and len(ids) < 16: ids.append(value)


def session_metadata(agg):
    m = agg.get("account_metadata") or {}
    # Deliberately omit quotas: they describe an account, not a session's usage.
    return {key: m[key] for key in ("creator_account_id", "creator_user_id", "recorded_account_ids") if key in m}


def _profile(source):
    return str(Path(os.environ.get("CODEX_HOME" if source == "codex" else "CLAUDE_CONFIG_DIR")
                    or Path.home() / (".codex" if source == "codex" else ".claude")).resolve())


def _auth_signature(source):
    # Only stat credential files. Never read/decode tokens, API keys or keychains.
    root = Path(_profile(source))
    paths = [root / "auth.json"] if source == "codex" else [root / ".credentials.json", root.with_suffix(".json")]
    signatures = []
    for path in paths:
        try:
            st = path.stat(); signatures.append((str(path), st.st_size, st.st_mtime_ns))
        except OSError: signatures.append((str(path), None, None))
    # Environment values are never included in a response or persisted.
    names = ("OPENAI_API_KEY", "CODEX_AUTH_TOKEN") if source == "codex" else (
        "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY")
    return (tuple(signatures), tuple(hashlib.sha256(os.environ.get(name, "").encode("utf-8")).digest() for name in names))


def _environment(source):
    environment = os.environ.copy()
    environment.pop("AGENT_TELEMETRY_CONTROL_TOKEN", None)
    if source == "codex": environment["CODEX_HOME"] = _profile(source)
    else:
        environment["CLAUDE_CONFIG_DIR"] = _profile(source)
        environment["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
    return environment


def _codex_read():
    executable = shutil.which("codex")
    if not executable: raise ValueError("Codex CLI is unavailable on the dashboard's PATH.")
    # This child exposes only read methods. Never start/resume a thread/model turn.
    process = subprocess.Popen([executable, "app-server", "--listen", "stdio://", "-c", "analytics.enabled=false"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, encoding="utf-8", env=_environment("codex"),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    replies = queue.Queue()
    def reader():
        try:
            for line in process.stdout:
                try: message = json.loads(line)
                except ValueError: continue
                if isinstance(message, dict) and type(message.get("id")) is int:
                    replies.put(message)
        finally:
            # The reader owns this stream. Closing it on another thread can block
            # forever on a read lock when a CLI descendant inherits its pipe.
            process.stdout.close()
            replies.put(None)
    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + 25
    def request(identifier, method, params):
        process.stdin.write(json.dumps({"id": identifier, "method": method, "params": params}) + "\n")
        process.stdin.flush()
        while True:
            try: message = replies.get(timeout=max(.01, deadline - time.monotonic()))
            except queue.Empty: raise ValueError("Codex account lookup timed out; retry or update its CLI.")
            if message is None: raise ValueError("This Codex installation does not expose the account API.")
            if message.get("id") != identifier: continue
            if "error" in message: raise ValueError("Codex could not read account limits. Check sign-in or update its CLI.")
            result = message.get("result")
            if not isinstance(result, dict): raise ValueError("Codex returned an unsupported account response.")
            return result
    try:
        request(1, "initialize", {"clientInfo": {"name": "agent_telemetry", "title": "AgentTelemetry", "version": "2.1.0"}})
        process.stdin.write('{"method":"initialized","params":{}}\n'); process.stdin.flush()
        before = request(2, "account/read", {"refreshToken": False}).get("account")
        limits = request(3, "account/rateLimits/read", {}) if isinstance(before, dict) and before.get("type") == "chatgpt" else {}
        after = request(4, "account/read", {"refreshToken": False}).get("account")
        if before != after: raise AccountChanged("Codex account changed during lookup; discard and retry.")
        return before, limits
    except (OSError, BrokenPipeError):
        raise ValueError("Codex account connection failed; retry or update its CLI.") from None
    finally:
        process.stdin.close()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.terminate()  # only our metadata child, never the user's Codex
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: pass


def _claude_read():
    executable = shutil.which("claude")
    if not executable: raise ValueError("Claude Code CLI is unavailable on the dashboard's PATH.")
    try:
        result = subprocess.run([executable, "auth", "status"], capture_output=True,
            text=True, encoding="utf-8", timeout=20, env=_environment("claude"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        data = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ValueError("Claude auth status is unavailable; check sign-in or update its CLI.") from None
    if result.returncode not in (0, 1) or not isinstance(data, dict) or type(data.get("loggedIn")) is not bool:
        raise ValueError("Claude returned an unsupported auth status response.")
    return data


def refresh_account(source, confirmed=False):
    if source not in ("codex", "claude"): raise ValueError("Unsupported account source.")
    if confirmed is not True: raise ValueError("Explicit consent is required for a CLI account lookup that may contact the provider.")
    with _lock:
        signature = _auth_signature(source)
        try:
            if source == "codex":
                account, limits = _codex_read()
                account = account if isinstance(account, dict) else {}
                identity = {"auth": text(account.get("type")) or "signed out / unavailable",
                            "email": text(account.get("email")), "plan": text(account.get("planType")),
                            "credential_source": text(account.get("credentialSource"))}
                buckets = limits.get("rateLimitsByLimitId")
                values = list(buckets.values())[:16] if isinstance(buckets, dict) and buckets else [limits.get("rateLimits")]
                snapshots = [s for s in (codex_snapshot(v, time.time(), camel=True) for v in values) if s]
            else:
                account = _claude_read()
                identity = {"auth": text(account.get("authMethod")) if account["loggedIn"] else "signed out",
                            "email": text(account.get("email")), "plan": text(account.get("subscriptionType")),
                            "provider": text(account.get("apiProvider")), "organization_id": text(account.get("orgId"))}
                snapshots = []
            if signature != _auth_signature(source):
                raise AccountChanged("Account files changed during lookup; discard and retry.")
            _live[source] = {"identity": identity, "snapshots": snapshots,
                             "observed_at": time.time(), "signature": signature, "error": None}
        except AccountChanged:
            _live.pop(source, None)
            raise
        except ValueError as error:
            old = _live.get(source)
            if old and old["signature"] == signature: old["error"] = str(error)
            else: _live.pop(source, None)
            raise


def _present(snapshot, now, force_stale=False):
    result = dict(snapshot)
    age = now - snapshot["observed_at"]
    result["stale"] = force_stale or age < 0 or age > STALE_AFTER
    result["windows"] = [dict(w, stale=result["stale"] or w["resets_at"] is None or now >= w["resets_at"],
                              expired=w["resets_at"] is not None and now >= w["resets_at"])
                         for w in snapshot["windows"]]
    return result


def build_accounts(aggregates, device, now=None):
    now = time.time() if now is None else now
    # Different profiles and metered buckets stay separate. Never sum percentages.
    observed = {}
    for agg in aggregates:
        if agg.get("_device") and agg["_device"] != device: continue
        if agg.get("source") != "codex": continue
        # Ledger paths can come from another OS. Inspect components without
        # interpreting a copied Windows path as one filename on macOS/Linux.
        path = PurePosixPath(str(agg.get("path", "")).replace("\\", "/"))
        root = next((p.parent for p in path.parents if p.name in ("sessions", "archived_sessions")), None)
        profile = str(root) if root else "unresolved profile"
        for key, snapshot in (agg.get("account_metadata", {}).get("quota_snapshots") or {}).items():
            slot = (profile, key)
            if slot not in observed or snapshot["observed_at"] > observed[slot]["observed_at"]:
                observed[slot] = snapshot
    entries = []
    with _lock:
        for source in ("codex", "claude"):
            entry = {"source": source, "device": device, "profile": _profile(source), "identity": None,
                     "identity_stale": True, "snapshots": [], "error": None}
            live = _live.get(source)
            if live and live["signature"] == _auth_signature(source):
                entry.update(identity=live["identity"], observed_at=live["observed_at"],
                             identity_stale=not 0 <= now - live["observed_at"] <= STALE_AFTER or bool(live["error"]),
                             error=live["error"])
                entry["snapshots"] = [dict(_present(s, now, bool(live["error"])), origin="Codex account API",
                    attribution="Account reported by this CLI lookup; other running processes may use different auth.") for s in live["snapshots"]]
            elif live:
                entry["error"] = "Sign-in metadata changed. Refresh account details; previous account limits hidden."
            if source == "claude":
                try:
                    capture = json.loads((N.support_dir() / "claude-quota-snapshot.json").read_text(encoding="utf-8"))
                    captured = claude_snapshot(capture.get("rate_limits"), capture.get("observed_at"))
                    if captured: entry["snapshots"].append(dict(_present(captured, now), origin="Claude status-line capture",
                        attribution="Account identity is not included in the status-line payload; cannot match this to the displayed sign-in."))
                except FileNotFoundError: pass
                except (OSError, ValueError, AttributeError, TypeError):
                    entry["error"] = "Claude status-line capture is unreadable; no quota inferred."
            entry["unavailable"] = ("Refresh account details to read this CLI's auth and plan." if not entry["identity"] else
                "Subscription quotas are unavailable for API-key or other non-ChatGPT auth." if source == "codex" and entry["identity"]["auth"] != "chatgpt" else
                "No quota snapshot returned." if source == "codex" else
                "Claude auth status has no quota percentages. Configure the optional local status-line capture.")
            entries.append(entry)
    # Offline log observations are intentionally separate from current sign-in.
    for (profile, _), snapshot in sorted(observed.items()):
        entries.append({"source": "codex", "device": device, "profile": profile, "identity": None,
            "snapshots": [dict(_present(snapshot, now), origin="Local Codex log",
                attribution="Account-wide observation; billing account is not recorded with this snapshot. It may belong to a previous sign-in.")],
            "unavailable": "Historical log observation; current account/auth unavailable.", "identity_stale": True})
    return {"entries": entries, "stale_after_seconds": STALE_AFTER, "device": device}


def claude_snapshot(value, observed_at):
    if not isinstance(value, dict) or timestamp(observed_at) is None: return None
    windows = []
    for key, label, mins in (("five_hour", "5-hour limit", 300), ("seven_day", "Weekly limit", 10080)):
        raw = value.get(key)
        if isinstance(raw, dict):
            w = window({"used_percent": raw.get("used_percentage"), "resets_at": raw.get("resets_at")}, label, mins)
            windows.append(w)
    return {"observed_at": timestamp(observed_at), "limit_id": "claude", "plan": None, "windows": windows}


def capture_claude(data):
    if not isinstance(data, dict): raise ValueError("Expected a Claude status-line JSON object.")
    snapshot = claude_snapshot(data.get("rate_limits"), time.time())
    # Store exactly the two documented subscriber windows, never prompts, paths,
    # spend-limit gateway data, credentials or context-window percentages.
    rate = {}
    for w in (snapshot or {}).get("windows", []):
        key = "five_hour" if w["window_minutes"] == 300 else "seven_day"
        rate[key] = {"used_percentage": w["used_percent"], "resets_at": w["resets_at"]}
    # An API-key / missing-windows payload replaces the old snapshot, not retains it.
    N._write("claude-quota-snapshot.json", {"observed_at": time.time(), "rate_limits": rate})


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description="Capture only Claude's documented quota fields from status-line stdin.")
    cli.add_argument("--capture-claude", action="store_true", required=True)
    cli.parse_args()
    try:
        data = json.loads(sys.stdin.read(1024 * 1024))
        capture_claude(data)
        # Usable as a standalone status line; wrappers can discard our output.
        snapshot = claude_snapshot(data.get("rate_limits"), time.time())
        parts = [f"{w['label']}: {100 - w['used_percent']:g}% left" for w in (snapshot or {}).get("windows", [])
                 if w["used_percent"] is not None]
        print(" · ".join(parts) or "AgentTelemetry: subscription limits unavailable")
    except (OSError, ValueError) as error:
        sys.stderr.write("AgentTelemetry quota capture failed: " + type(error).__name__ + "\n")
        sys.exit(1)
