"""Вкладка «Приложения» острова (раскладка без Qt) и форматирование текстов библиотеки."""
from __future__ import annotations

import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from island import launcher_tab as lt  # noqa: E402
from system.fmt import fmt_duration, fmt_when, plural_ru  # noqa: E402


class LauncherTabLayoutTest(unittest.TestCase):
    def test_plus_tile_is_shown_until_limit(self) -> None:
        self.assertEqual(lt.slot_count(0), 1)
        self.assertEqual(lt.slot_count(4), 5)
        self.assertEqual(lt.slot_count(lt.MAX_ITEMS - 1), lt.MAX_ITEMS)
        self.assertEqual(lt.slot_count(lt.MAX_ITEMS), lt.MAX_ITEMS, "на десяти программах плитка «+» исчезает")

    def test_rows_and_height_grow(self) -> None:
        self.assertEqual(lt.row_count(0), 1)
        self.assertEqual(lt.row_count(4), 1)
        self.assertEqual(lt.row_count(5), 2)
        self.assertEqual(lt.row_count(lt.MAX_ITEMS), 2)
        self.assertGreater(lt.tab_height(5), lt.tab_height(4))
        self.assertGreaterEqual(lt.tab_height(0), lt.MIN_HEIGHT)

    def test_tiles_do_not_overlap_and_fit_width(self) -> None:
        for items in range(0, lt.MAX_ITEMS + 1):
            rects = lt.slot_rects(items)
            self.assertEqual(len(rects), lt.slot_count(items))
            for x, _y, w, _h in rects:
                self.assertGreaterEqual(x, lt.X0 - 0.01)
                self.assertLessEqual(x + w, lt.X0 + lt.CONTENT_W + 0.01)
            for i, a in enumerate(rects):
                for b in rects[i + 1:]:
                    overlap_x = a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
                    overlap_y = a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
                    self.assertFalse(overlap_x and overlap_y)

    def test_hit_test(self) -> None:
        rects = lt.slot_rects(3)
        for i, (x, y, w, h) in enumerate(rects):
            self.assertEqual(lt.hit_slot(3, x + w / 2, y + h / 2), i)
        self.assertIsNone(lt.hit_slot(3, 1.0, 1.0))
        x, y, w, h = rects[0]
        self.assertIsNone(lt.hit_slot(3, x + w + lt.GAP / 2, y + h / 2), "промежуток между плитками — не плитка")

    def test_parse_ref(self) -> None:
        self.assertEqual(lt.parse_ref("app:12"), ("app", 12))
        self.assertEqual(lt.parse_ref("game:3"), ("game", 3))
        for bad in ("", "app", "app:x", "tool:1", "+", "game:-1"):
            self.assertIsNone(lt.parse_ref(bad))


class FormatTest(unittest.TestCase):
    def test_plural(self) -> None:
        forms = ("игра", "игры", "игр")
        expected = {0: "игр", 1: "игра", 2: "игры", 4: "игры", 5: "игр", 11: "игр", 12: "игр", 14: "игр",
                    21: "игра", 22: "игры", 25: "игр", 101: "игра", 111: "игр"}
        for n, word in expected.items():
            self.assertEqual(plural_ru(n, *forms), word, n)

    def test_duration(self) -> None:
        self.assertEqual(fmt_duration(None), "—")
        self.assertEqual(fmt_duration(0), "—")
        self.assertEqual(fmt_duration(30), "меньше минуты")
        self.assertEqual(fmt_duration(45 * 60), "45 м")
        self.assertEqual(fmt_duration(3600), "1 ч")
        self.assertEqual(fmt_duration(3 * 3600 + 20 * 60), "3 ч 20 м")

    def test_when(self) -> None:
        now = datetime(2026, 11, 20, 12, 0, 0)
        self.assertEqual(fmt_when(None), "ещё не запускалась")
        self.assertEqual(fmt_when(datetime(2026, 11, 20, 9, 5).timestamp(), now), "сегодня 09:05")
        self.assertEqual(fmt_when(datetime(2026, 11, 19, 23, 40).timestamp(), now), "вчера 23:40")
        self.assertEqual(fmt_when(datetime(2026, 11, 3, 10, 0).timestamp(), now), "3 ноября 2026")


if __name__ == "__main__":
    unittest.main()
