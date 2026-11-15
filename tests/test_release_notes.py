"""Release notes state and opt-in GitHub fetch behavior."""

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import native
import release_notes as RN


def _release(tag, body="Release notes", **extra):
    value = {
        "tag_name": tag,
        "name": "AgentTelemetry " + tag,
        "published_at": "2026-10-19T12:00:00Z",
        "draft": False,
        "prerelease": False,
        "html_url": "https://evil.example/should-not-be-used",
        "body": body,
    }
    value.update(extra)
    return value


class _Response:
    def __init__(self, value, url, *, raw=None, status=200, headers=None):
        if raw is None:
            raw = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.raw = raw
        self.url = url
        self.status = status
        self.headers = headers or {"Content-Length": str(len(raw))}
        self.closed = False

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status

    def read(self, limit):
        return self.raw[:limit]

    def close(self):
        self.closed = True


class _FakeOpener:
    def __init__(self, replies, urls):
        self.replies = list(replies)
        self.urls = urls

    def open(self, request, timeout):
        self.urls.append(request.full_url)
        self.assertions(request, timeout)
        reply = self.replies.pop(0)
        if callable(reply):
            return reply(request)
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, tuple) and reply and reply[0] == "raw":
            return _Response({}, request.full_url, raw=reply[1], headers=reply[2] if len(reply) > 2 else None)
        if isinstance(reply, tuple) and reply and reply[0] == "redirect":
            return _Response(reply[2], reply[1])
        return _Response(reply, request.full_url)

    @staticmethod
    def assertions(request, timeout):
        if timeout != 8:
            raise AssertionError("release requests must use the short timeout")
        headers = {key.lower(): value for key, value in request.header_items()}
        if "authorization" in headers:
            raise AssertionError("release requests must not send credentials")


class ReleaseNotesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="telemetry-release-notes-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.support = self.root / "AgentTelemetryNative"
        self.ledger = self.root / "ledger"
        self.app = self.root / "application"
        item = patch.object(native, "support_dir", return_value=self.support)
        item.start()
        self.addCleanup(item.stop)
        self.path = self.support / "release-notes.json"

    def client(self, version="v2.0.0", app=None):
        return RN.ReleaseNotes(self.app if app is None else app, {"git": True, "describe": version})

    def api(self, *replies):
        urls = []
        opener = _FakeOpener(replies, urls)
        built = []

        def build(*handlers):
            built.append(handlers)
            return opener

        patcher = patch.object(RN.urllib.request, "build_opener", side_effect=build)
        patcher.start()
        self.addCleanup(patcher.stop)
        return urls, built, opener

    def test_first_install_baseline_and_check_before_claim(self):
        client = self.client("v2.0.0")
        before = client.status()
        self.assertFalse(before["pending"])
        self.assertFalse(self.support.exists(), "offline status must not create preference files")

        urls, built, _ = self.api(_release("v2.0.0"))
        checked = client.check()
        self.assertEqual(checked["status"], "available")
        self.assertFalse(checked["update_available"])
        self.assertEqual(len(urls), 1, "the running release is reused when it is the latest release")
        self.assertEqual(checked["download_url"], RN._RELEASE_ROOT + "v2.0.0")
        self.assertTrue(built)
        self.assertIsInstance(built[0][0], RN.urllib.request.ProxyHandler)
        self.assertEqual(built[0][0].proxies, {})
        self.assertTrue(any(isinstance(handler, RN._NoRedirect) for handler in built[0]))

        # A clicked check establishes the install baseline; the following open
        # of this same app version must not produce a popup.
        restarted = self.client("v2.0.0")
        self.assertFalse(restarted.claim()["pending"])
        self.assertFalse(restarted.claim()["pending"])

    def test_upgrade_claim_survives_restart_and_shown_acknowledges_once(self):
        self.client("v1.8.0").claim()
        current = self.client("v2.0.0")
        first = current.claim()
        self.assertTrue(first["pending"])
        self.assertRegex(first["claim"], r"^[0-9a-f]{64}$")
        self.assertEqual(first["release"]["availability"], "unavailable")
        self.assertTrue(current.status()["pending"])

        # Restarting the dashboard while a popup is already being displayed
        # cannot produce a second popup for the same version.
        duplicate = self.client("v2.0.0").claim()
        self.assertFalse(duplicate["pending"])
        self.assertIsNone(duplicate["claim"])

        shown = current.shown(first["claim"])
        self.assertTrue(shown["shown"])
        self.assertFalse(self.client("v2.0.0").claim()["pending"])
        with self.assertRaises(RN.ReleaseNotesError):
            current.shown(first["claim"])

    def test_older_running_backend_cannot_erase_newer_version_claim(self):
        older = self.client("v1.0.0")
        older.claim()
        newer = self.client("v2.0.0")
        claim = newer.claim()
        self.assertTrue(claim["pending"])

        self.assertFalse(older.claim()["pending"])
        self.assertTrue(newer.shown(claim["claim"])["shown"])

    def test_concurrent_claims_issue_one_popup_claim(self):
        self.client("v1.0.0").claim()
        client = self.client("v2.0.0")
        barrier = threading.Barrier(3)
        results = []
        errors = []

        def run():
            try:
                barrier.wait(timeout=5)
                results.append(client.claim())
            except Exception as exc:  # collected so the worker failure is visible to unittest
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait(timeout=5)
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(errors)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(1 for item in results if item["pending"]), 1)
        self.assertEqual(len({item["claim"] for item in results if item["claim"]}), 1)

    def test_expired_or_abandoned_claim_can_be_retried_and_old_claim_cannot_ack(self):
        self.client("v1.0.0").claim()
        client = self.client("v2.0.0")
        with patch.object(RN.time, "time", return_value=1000):
            first = client.claim()
        with patch.object(RN.time, "time", return_value=1121):
            second = client.claim()
            self.assertTrue(second["pending"])
            self.assertNotEqual(first["claim"], second["claim"])
            with self.assertRaises(RN.ReleaseNotesError):
                client.shown(first["claim"])
            self.assertTrue(client.shown(second["claim"])["shown"])

    def test_rollback_does_not_repeat_an_already_seen_version(self):
        self.client("v1.0.0").claim()
        version_two = self.client("v2.0.0")
        claim = version_two.claim()
        version_two.shown(claim["claim"])

        rollback = self.client("v1.0.0").claim()
        self.assertFalse(rollback["pending"])
        # Returning to the highest acknowledged version does not show it again.
        self.assertFalse(self.client("v2.0.0").claim()["pending"])

    def test_unknown_dev_and_prerelease_versions_never_create_false_popup(self):
        for version in (None, "main", "v2.0.0-rc.1", "v2.0.0+local"):
            with self.subTest(version=version):
                result = self.client(version).claim()
                self.assertFalse(result["pending"])
                self.assertIsNone(result["claim"])
                self.assertIsNone(result["current_version"])
        self.assertFalse(self.support.exists())

        urls, _, _ = self.api(_release("v3.0.0"))
        result = self.client("v3.0.0-rc.1").check()
        self.assertEqual(len(urls), 1)
        self.assertFalse(result["update_available"], "a prerelease/dev build is not compared as a stable version")
        self.assertIsNone(result["release"])

    def test_numeric_version_comparison_and_running_notes_are_independent_of_latest(self):
        current = self.client("v2.9.0-3-g1234abcd")
        urls, _, _ = self.api(_release("v2.10.0", "Newer stable"),
                              _release("v2.9.0", "Currently running"))
        result = current.check()
        self.assertEqual(result["current_version"], "2.9.0")
        self.assertTrue(result["update_available"], "2.10.0 must compare greater than 2.9.0 numerically")
        self.assertEqual(result["latest"]["tag"], "v2.10.0")
        self.assertEqual(result["release"]["body"], "Currently running")
        self.assertEqual(result["download_url"], RN._RELEASE_ROOT + "v2.10.0")
        self.assertEqual(urls, [
            RN._API_ROOT + "/releases/latest",
            RN._API_ROOT + "/releases/tags/v2.9.0",
        ])

    def test_api_release_text_is_bounded_and_only_canonical_release_urls_are_used(self):
        body = "<b>Release notes are text</b>\x00\n" + ("x" * 49_000)
        urls, _, _ = self.api(_release("v2.0.0", body))
        result = self.client("2.0.0").check()
        release = result["release"]
        self.assertEqual(urls, [RN._API_ROOT + "/releases/latest"])
        self.assertEqual(release["url"], RN._RELEASE_ROOT + "v2.0.0")
        self.assertNotIn("evil.example", release["url"])
        self.assertTrue(release["truncated"])
        self.assertLessEqual(len(release["body"]), RN._MAX_BODY_CHARS)
        self.assertTrue(release["body"].startswith("<b>Release notes are text</b> "))
        self.assertEqual(release["availability"], "available")

    def test_empty_notes_are_explicit_and_missing_v_tag_falls_back_to_unprefixed_tag(self):
        self.api(_release("2.0.0", None))
        result = self.client("v2.0.0").check()
        self.assertTrue(result["release"]["empty"])
        self.assertEqual(result["release"]["body"], "")

        # A packaged version string may omit the tag's v prefix. A 404 is
        # expected for the first spelling, then the exact stable equivalent is tried.
        class NotFound:
            def __call__(self, request):
                raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

        urls, _, _ = self.api(_release("v2.1.0"), NotFound(), _release("2.0.0", "Exact release"))
        result = self.client("2.0.0").check()
        self.assertEqual(result["release"]["body"], "Exact release")
        self.assertEqual(urls[-2:], [
            RN._API_ROOT + "/releases/tags/2.0.0",
            RN._API_ROOT + "/releases/tags/v2.0.0",
        ])

    def test_packaged_version_links_to_the_v_prefixed_release_page(self):
        # Packaged apps (the DMG, the EXE, Pro) report "2.2.0"; the public tag is v2.2.0.
        for version in ("2.2.0", "v2.2.0"):
            release = self.client(version).status()["release"]
            self.assertEqual(release["url"], RN._RELEASE_ROOT + "v2.2.0")
            self.assertEqual(release["availability"], "unavailable")

    def test_network_and_rate_limit_failures_keep_cached_notes_and_safe_errors(self):
        client = self.client("v2.0.0")
        self.api(_release("v2.0.0", "Cached release"))
        first = client.check()
        self.assertEqual(first["status"], "available")
        saved = self.path.read_bytes()

        def rate_limited(request):
            raise urllib.error.HTTPError(request.full_url, 429, "rate limit", {}, io.BytesIO(b"private"))

        self.api(rate_limited, rate_limited)
        second = client.check()
        self.assertEqual(second["status"], "unavailable")
        self.assertEqual(second["latest"]["body"], "Cached release")
        self.assertEqual(second["release"]["body"], "Cached release")
        self.assertIn("rate limited", second["error"])
        self.assertNotIn("private", second["error"])
        self.assertEqual(self.path.read_bytes(), saved, "a failed check must retain the previous cache")

    def test_malformed_oversized_and_redirected_responses_are_unavailable(self):
        cases = (
            (b"{broken", None),
            (b"{}", {"Content-Length": str(RN._MAX_API_BYTES + 1)}),
            (b"x" * (RN._MAX_API_BYTES + 1), None),
        )
        for raw, headers in cases:
            with self.subTest(length=len(raw), headers=headers):
                if headers is None:
                    self.api(("raw", raw))
                else:
                    self.api(("raw", raw, headers))
                result = self.client("dev-build").check()
                self.assertEqual(result["status"], "unavailable")
                self.assertIn("GitHub", result["error"])

        self.api(("redirect", "https://evil.example/releases", _release("v3.0.0")))
        result = self.client("dev-build").check()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("redirected", result["error"])

        self.api(_release("https://evil.example/tag/v3.0.0"))
        result = self.client("dev-build").check()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("unsupported release tag", result["error"])

    def test_corrupt_state_is_preserved_and_explicit_check_returns_safe_failure(self):
        self.support.mkdir(parents=True)
        self.path.write_bytes(b"{broken state")
        original = self.path.read_bytes()
        status = self.client("v2.0.0").status()
        self.assertIn("corrupt", status["error"])
        self.assertEqual(self.path.read_bytes(), original)
        with self.assertRaises(RN.ReleaseNotesError):
            self.client("v2.0.0").claim()

        self.api(_release("v2.0.0"))
        result = self.client("v2.0.0").check()
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["release"]["tag"], "v2.0.0")
        self.assertIn("corrupt", result["error"])
        self.assertEqual(self.path.read_bytes(), original)

    def test_offline_methods_never_construct_a_network_client_or_touch_ledger(self):
        client = self.client("v1.0.0")
        with patch.object(RN.urllib.request, "build_opener", side_effect=AssertionError("offline method used network")):
            client.status()
            initial = client.claim()
            self.assertFalse(initial["pending"])
            with self.assertRaises(RN.ReleaseNotesError):
                client.shown("0" * 64)
        self.assertFalse(self.ledger.exists())
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(set(saved), {"version", "installs"})
        self.assertNotIn(str(self.app), self.path.read_text(encoding="utf-8"))
        self.assertFalse(any(self.ledger.iterdir()) if self.ledger.exists() else False)


if __name__ == "__main__":
    unittest.main()
