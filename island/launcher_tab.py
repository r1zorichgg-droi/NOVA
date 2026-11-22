"""Раскладка вкладки «Приложения» раскрытого Dynamic Island (без Qt — проверяется обычными тестами).

Координаты — в «дизайн-пикселях» раскрытого острова (ширина 480): плитки 5 в ряд, до двух рядов.
Последняя плитка «+» подсказывает, как добавить программу, пока в острове есть свободные места.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

COLS = 5
MAX_ITEMS = 10
X0, Y0 = 24.0, 18.0
CONTENT_W = 416.0
GAP = 10.0
TILE_W = (CONTENT_W - GAP * (COLS - 1)) / COLS
TILE_H = 84.0
ICON = 44
FOOTER_H = 34.0
MIN_HEIGHT = 150.0
ADD_KEY = "+"

Rect = tuple[float, float, float, float]  # x, y, ширина, высота


@dataclass(frozen=True)
class TileRef:
    key: str            # "app:12" / "game:3" / "+"
    kind: str           # app | game | add
    item_id: int


def parse_ref(raw: str) -> tuple[str, int] | None:
    kind, _, ident = str(raw).partition(":")
    if kind in ("app", "game") and ident.isdigit():
        return kind, int(ident)
    return None


def slot_count(items: int) -> int:
    return items + (1 if items < MAX_ITEMS else 0)


def row_count(items: int) -> int:
    return max(1, math.ceil(slot_count(items) / COLS))


def tab_height(items: int) -> float:
    rows = row_count(items)
    return max(MIN_HEIGHT, Y0 + rows * TILE_H + (rows - 1) * GAP + FOOTER_H)


def slot_rects(items: int) -> list[Rect]:
    """Прямоугольники всех плиток по порядку (сначала программы, затем плитка «+»)."""
    rects: list[Rect] = []
    for i in range(slot_count(items)):
        row, col = divmod(i, COLS)
        rects.append((X0 + col * (TILE_W + GAP), Y0 + row * (TILE_H + GAP), TILE_W, TILE_H))
    return rects


def hit_slot(items: int, x: float, y: float) -> int | None:
    """Индекс плитки под точкой (x, y) или None."""
    for i, (rx, ry, rw, rh) in enumerate(slot_rects(items)):
        if rx <= x <= rx + rw and ry <= y <= ry + rh:
            return i
    return None
