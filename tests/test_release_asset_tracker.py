import datetime as dt
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("track_release_assets", ROOT / "scripts" / "track_release_assets.py")
tracker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracker)


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class ReleaseAssetTrackerTests(unittest.TestCase):
    def test_fetches_latest_or_requested_tag_with_read_only_api_headers(self):
        release = {"tag_name": "v2.1.0", "assets": []}
        requests = []

        def urlopen(request, timeout):
            requests.append((request, timeout))
            return FakeResponse(release)

        self.assertIsNotNone(tracker.fetch_release("uttamdeb/agent-telemetry", urlopen=urlopen))
        self.assertEqual(requests[0][0].full_url,
                         "https://api.github.com/repos/uttamdeb/agent-telemetry/releases/latest")
        self.assertEqual(requests[0][1], 20)
        self.assertEqual(requests[0][0].get_header("Accept"), "application/vnd.github+json")
        tracker.fetch_release("uttamdeb/agent-telemetry", "v2.1.0", token="test-token", urlopen=urlopen)
        self.assertTrue(requests[1][0].full_url.endswith("/releases/tags/v2.1.0"))
        self.assertEqual(requests[1][0].get_header("Authorization"), "Bearer test-token")

    def test_repository_and_tag_are_validated_and_escaped(self):
        with self.assertRaises(tracker.TrackerError):
            tracker.fetch_release("https://example.com/owner/repo")
        self.assertTrue(tracker._release_endpoint("owner/repo", "release/2.1")
                        .endswith("/releases/tags/release%2F2.1"))

    def test_api_rate_limit_error_explains_optional_token(self):
        def rate_limited(_request, timeout):
            raise HTTPError("https://api.github.com/", 403, "rate limit", {}, None)

        with self.assertRaisesRegex(tracker.TrackerError, "GH_TOKEN or GITHUB_TOKEN"):
            tracker.fetch_release("owner/repo", urlopen=rate_limited)

    def test_snapshot_sorts_assets_and_totals_downloads(self):
        release = {
            "tag_name": "v2.1.0",
            "name": "AgentTelemetry 2.1.0",
            "html_url": "https://github.com/uttamdeb/agent-telemetry/releases/tag/v2.1.0",
            "published_at": "2026-10-20T00:00:00Z",
            "assets": [
                {"name": "z.vsix", "download_count": 5, "size": 300, "updated_at": "now"},
                {"name": "A.dmg", "download_count": 17, "size": 200, "updated_at": "now"},
            ],
        }
        stamp = dt.datetime(2026, 10, 20, tzinfo=dt.timezone(dt.timedelta(hours=6)))
        snapshot = tracker.make_snapshot("uttamdeb/agent-telemetry", release, stamp)
        self.assertEqual(snapshot["fetched_at"], "2026-10-19T18:00:00Z")
        self.assertEqual(snapshot["asset_count"], 2)
        self.assertEqual(snapshot["total_downloads"], 22)
        self.assertEqual([asset["name"] for asset in snapshot["assets"]], ["A.dmg", "z.vsix"])

    def test_invalid_download_counts_fail_loudly(self):
        for invalid in (-1, True, "12", None):
            with self.subTest(invalid=invalid), self.assertRaises(tracker.TrackerError):
                tracker.make_snapshot("owner/repo", {"assets": [
                    {"name": "asset.zip", "download_count": invalid, "size": 0},
                ]})

    def test_summary_and_json_snapshot_keep_per_asset_counts(self):
        snapshot = tracker.make_snapshot("owner/repo", {
            "tag_name": "v1.0",
            "assets": [{"name": "setup|windows.exe", "download_count": 7, "size": 1024}],
        }, dt.datetime(2026, 10, 20, tzinfo=dt.timezone.utc))
        summary = tracker.render_summary(snapshot)
        self.assertIn("**7** across 1 assets", summary)
        self.assertIn("setup\\|windows.exe", summary)
        self.assertIn("not unique people", summary)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "snapshot.json"
            tracker.write_snapshot(snapshot, path)
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["assets"][0]["download_count"], 7)

    def test_command_does_not_print_or_store_token(self):
        release = {"tag_name": "v2.1.0", "assets": []}
        with tempfile.TemporaryDirectory() as directory, patch.object(
            tracker, "fetch_release", return_value=release
        ), patch.dict("os.environ", {"GITHUB_REPOSITORY": "owner/repo", "GH_TOKEN": "secret-token"}):
            output = Path(directory) / "snapshot.json"
            self.assertEqual(tracker.main(["--output", str(output)]), 0)
            self.assertNotIn("secret-token", output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
