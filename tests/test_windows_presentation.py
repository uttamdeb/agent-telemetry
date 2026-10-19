"""Portable regression checks for tray dates, edge anchoring and footer layout."""
import datetime
import struct
import unittest
from pathlib import Path

from windows.presentation import figures, layout, local_servers, position, recent_sessions


class TrayPresentationTests(unittest.TestCase):
    def test_today_and_rollover_do_not_label_yesterdays_usage_today(self):
        snapshot = {"date": "2026-10-10", "tokens": 15094633, "spend": 2.59}
        self.assertEqual(figures(snapshot, "2026-10-10"), ("15.1M", "$2.59"))
        self.assertEqual(figures(snapshot, "2026-10-11"), ("—", "—"))
        self.assertEqual(figures(dict(snapshot, tokens=0, spend=0), "2026-10-10"), ("0", "$0.00"))

    def test_bottom_top_and_negative_coordinate_monitors(self):
        self.assertEqual(position((1900, 1040, 1920, 1060), (0, 0, 1920, 1040), (310, 268)), (1610, 764))
        self.assertEqual(position((500, 0, 520, 24), (0, 24, 1920, 1080), (310, 268)), (210, 32))
        x, y = position((-1900, 900, -1880, 924), (-1920, 0, 0, 1040), (620, 600))
        self.assertEqual(x, -1920)
        self.assertTrue(0 <= y <= 440)

    def test_footer_stays_below_expanded_content_and_warnings(self):
        for expanded in (False, True):
            for stale in (False, True):
                for warning in (False, True):
                    g = layout(expanded, stale, warning)
                    self.assertGreaterEqual(g["dashboard"], g["settings"] + (178 if expanded else 0))
                    self.assertGreater(g["footer"], g["dashboard"] + 30)
                    self.assertGreater(g["height"], g["footer"] + 26)

    def test_icons_include_real_transparent_images_at_multiple_sizes(self):
        root = Path(__file__).resolve().parents[1] / "windows" / "assets"
        for name in ("TrayIcon.ico", "AgentTelemetry.ico"):
            raw = (root / name).read_bytes()
            reserved, kind, count = struct.unpack_from("<HHH", raw)
            self.assertEqual((reserved, kind), (0, 1))
            sizes = set()
            for i in range(count):
                width, height, _, _, planes, depth, length, offset = struct.unpack_from("<BBBBHHII", raw, 6 + i * 16)
                self.assertEqual(width, height)
                sizes.add(width or 256)
                self.assertEqual((planes, depth), (1, 32))
                self.assertGreater(length, 100)
                self.assertEqual(raw[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
                self.assertLessEqual(offset + length, len(raw))
            self.assertTrue({16, 20, 24, 32, 48, 64, 256} <= sizes)

    def test_small_work_area_scrolls_settings_keeps_footer_visible(self):
        g = layout(True, True, True, max_height=480)
        self.assertEqual(g["height"], 480)
        self.assertLess(g["settings_height"], g["settings_content_height"])
        self.assertGreater(g["settings_height"], 0)
        self.assertLess(g["footer"] + 26, 480)

    def test_recent_sessions_are_newest_first_and_age_out_without_a_refresh(self):
        now = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)
        def row(minutes, title, provider="OpenAI"):
            stamp = (now - datetime.timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")
            return {"tool": "Codex", "provider": provider, "title": title,
                    "tokens": 1234, "last_activity_at": stamp, "subagent": False}
        snapshot = {"activity": {"window_seconds": 900, "sessions": [
            row(15, "expires at the boundary"), row(3, "older"), row(1, "newest"),
            row(2, "fourth session is omitted")
        ]}}
        rows = recent_sessions(snapshot, now)
        self.assertEqual([item["title"] for item in rows], ["newest", "fourth session is omitted", "older"])
        self.assertEqual(rows[0]["age"], "1 min ago")
        self.assertEqual(recent_sessions(snapshot, now + datetime.timedelta(minutes=15)), [])

    def test_local_server_rows_keep_only_running_or_pinned_and_never_add_a_scheme(self):
        snapshot = {"servers": {"servers": [
            {"title": "Stopped unpinned", "state": "stopped", "host": "127.0.0.1", "port": 80},
            {"project": "pinned", "state": "stopped", "host": "localhost", "port": 3000, "isPinned": True},
            {"process": "node", "state": "running", "host": "::1", "port": 9000},
            {"process": "wildcard", "state": "running", "host": "0.0.0.0", "port": 8080},
        ]}}
        rows = local_servers(snapshot)
        self.assertEqual([row["title"] for row in rows], ["pinned", "node", "wildcard"])
        self.assertEqual(rows[0]["state"], "stopped")
        self.assertEqual(rows[1]["address"], "[::1]:9000")
        self.assertNotIn("http", rows[1]["address"])
        self.assertEqual(rows[2]["address"], "localhost:8080")

    def test_default_layout_includes_lists_and_expanded_settings_share_a_clamped_scroll_area(self):
        for scale in (1, 1.5, 2):
            logical_work_height = int(round(480 * scale) / scale)
            for expanded in (False, True):
                g = layout(expanded, stale=True, warning=True,
                           max_height=logical_work_height, list_height=320)
                self.assertEqual(g["height"], 480)
                self.assertGreater(g["settings_height"], 0)
                self.assertLessEqual(g["settings_height"], g["settings_content_height"])
                self.assertGreater(g["footer"], g["settings"] + g["settings_height"])
        collapsed = layout(False, list_height=320)
        self.assertEqual(collapsed["settings_height"], 320)
        expanded = layout(True, list_height=320)
        self.assertGreater(expanded["settings_content_height"], collapsed["settings_content_height"])


if __name__ == "__main__":
    unittest.main()
