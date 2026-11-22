"""Тесты палитры и таблицы стилей (не требуют Qt)."""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import ACCENT_PRESETS  # noqa: E402
from ui import theme  # noqa: E402


class ThemeTests(unittest.TestCase):
    def test_parse_hex(self) -> None:
        self.assertEqual(theme.parse_hex("#7C5CFF"), (124, 92, 255, 255))
        self.assertEqual(theme.parse_hex("fff"), (255, 255, 255, 255))
        self.assertEqual(theme.parse_hex("мусор"), theme.DEFAULT_ACCENT)
        self.assertEqual(theme.parse_hex("#12345"), theme.DEFAULT_ACCENT)

    def test_css_colors(self) -> None:
        self.assertEqual(theme.css((1, 2, 3, 255)), "#010203")
        self.assertEqual(theme.css((1, 2, 3, 20)), "rgba(1, 2, 3, 20)")

    def test_stylesheet_is_complete_for_every_theme_and_accent(self) -> None:
        for name in ("dark", "light"):
            for accent in ACCENT_PRESETS + ["#123456", "битый"]:
                sheet = theme.build_stylesheet(theme.build_palette(name, accent))
                self.assertEqual(sheet.count("{"), sheet.count("}"))
                self.assertNotRegex(sheet, r"\{[a-z_]+\}")          # остались неподставленные поля
                self.assertNotIn("None", sheet)
                self.assertTrue(re.search(r"QFrame#card\s*\{", sheet))

    def test_text_is_readable_on_accent_buttons(self) -> None:
        for accent in ACCENT_PRESETS:
            pal = theme.build_palette("dark", accent)
            lum_bg, lum_fg = theme.luminance(pal.accent), theme.luminance(pal.on_accent)
            hi, lo = max(lum_bg, lum_fg), min(lum_bg, lum_fg)
            self.assertGreaterEqual((hi + 0.05) / (lo + 0.05), 3.0, accent)   # минимум для крупного текста

    def test_dark_is_default_and_light_differs(self) -> None:
        self.assertTrue(theme.build_palette("dark", "#7C5CFF").dark)
        self.assertTrue(theme.build_palette("что-то", "#7C5CFF").dark)
        light = theme.build_palette("light", "#7C5CFF")
        self.assertFalse(light.dark)
        self.assertGreater(theme.luminance(light.bg), theme.luminance(theme.build_palette("dark", "#7C5CFF").bg))


if __name__ == "__main__":
    unittest.main()
