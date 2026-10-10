"""Portable regression checks for tray dates, edge anchoring and footer layout."""
import struct
import unittest
from pathlib import Path

from windows.presentation import figures, layout, position


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
        self.assertLess(g["settings_height"], 178)
        self.assertGreater(g["settings_height"], 0)
        self.assertLess(g["footer"] + 26, 480)


if __name__ == "__main__":
    unittest.main()
