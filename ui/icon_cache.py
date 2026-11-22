"""Значки и обложки карточек: обложка (если есть) → значок самой программы из EXE → буква на цветной плитке.

Всё берётся локально: файл обложки, указанный в карточке (например, из кэша Steam или выбранный вами),
и значок EXE, который Windows уже хранит у себя. Из интернета ничего не загружается.
"""
from __future__ import annotations

import hashlib
import os

from PySide6.QtCore import QFileInfo, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFileIconProvider

from database.models import LibraryItem


def tile_color(title: str) -> QColor:
    """Стабильный приятный цвет по названию (чтобы у карточки без значка он не менялся между запусками)."""
    digest = hashlib.md5(title.encode("utf-8", errors="ignore"), usedforsecurity=False).digest()
    hue = int.from_bytes(digest[:2], "big") % 360
    return QColor.fromHsl(hue, 120, 96)


def letter_tile(title: str, size: int, dpr: float = 2.0) -> QPixmap:
    """Скруглённая плитка с первой буквой названия."""
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = tile_color(title or "?")
    grad = QLinearGradient(0, 0, size, size)
    grad.setColorAt(0, base.lighter(130))
    grad.setColorAt(1, base.darker(115))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    p.fillPath(path, grad)
    font = QFont()
    font.setPixelSize(int(size * 0.46))
    font.setWeight(QFont.Weight.DemiBold)
    p.setFont(font)
    p.setPen(QColor(255, 255, 255, 235))
    p.drawText(QRectF(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, (title.strip()[:1] or "?").upper())
    p.end()
    return pix


def fit_square(pix: QPixmap, px: int, crop: bool = False) -> QPixmap:
    """Приводит картинку к точному размеру px×px пикселей (с запасом по прозрачности или с обрезкой по центру).

    Windows и Qt могут вернуть значок любого размера (вплоть до 256×256 и с собственным масштабом экрана),
    поэтому без такого выравнивания значок получается «раздутым» и вылезает за плитку.
    """
    pix.setDevicePixelRatio(1.0)                   # считаем в реальных пикселях; масштаб экрана задаёт вызывающий
    if pix.width() == px and pix.height() == px:
        return pix
    mode = Qt.AspectRatioMode.KeepAspectRatioByExpanding if crop else Qt.AspectRatioMode.KeepAspectRatio
    scaled = pix.scaled(px, px, mode, Qt.TransformationMode.SmoothTransformation)
    if crop:
        return scaled.copy(max((scaled.width() - px) // 2, 0), max((scaled.height() - px) // 2, 0), px, px)
    if scaled.width() == px and scaled.height() == px:
        return scaled
    canvas = QPixmap(px, px)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    painter.drawPixmap((px - scaled.width()) // 2, (px - scaled.height()) // 2, scaled)
    painter.end()
    return canvas


class IconCache:
    """Кэш значков: создаётся в потоке интерфейса и используется страницами и островом."""

    def __init__(self) -> None:
        self._provider = QFileIconProvider()
        self._icons: dict[tuple[object, ...], QPixmap] = {}
        self._covers: dict[tuple[object, ...], QPixmap | None] = {}

    def clear(self) -> None:
        self._icons.clear()
        self._covers.clear()

    @staticmethod
    def _stamp(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def exe_icon(self, exe_path: str, size: int, dpr: float = 2.0) -> QPixmap | None:
        if not exe_path or not os.path.isfile(exe_path):
            return None
        icon = self._provider.icon(QFileInfo(exe_path))
        if icon.isNull():
            return None
        px = int(size * dpr)
        pix = icon.pixmap(QSize(px, px))
        if pix.isNull():
            return None
        pix = fit_square(pix, px)      # и крупные значки (256×256), и мелкие (16×16 у старых программ) — ровно px×px
        pix.setDevicePixelRatio(dpr)
        return pix

    def pixmap(self, kind: str, item: LibraryItem, size: int, dpr: float = 2.0) -> QPixmap:
        """Квадратный значок карточки (для списков, острова и т.п.)."""
        key = (kind, item.id, item.exe_path, item.image_path, self._stamp(item.image_path), size, dpr)
        cached = self._icons.get(key)
        if cached is not None:
            return cached
        pix = self.exe_icon(item.exe_path, size, dpr)
        if pix is None and item.image_path and os.path.isfile(item.image_path):
            raw = QPixmap(item.image_path)
            if not raw.isNull():
                pix = fit_square(raw, int(size * dpr), crop=True)
                pix.setDevicePixelRatio(dpr)
        if pix is None:
            pix = letter_tile(item.title, size, dpr)
        if len(self._icons) > 600:
            self._icons.clear()
        self._icons[key] = pix
        return pix

    def cover(self, kind: str, item: LibraryItem, width: int, height: int, dpr: float = 2.0) -> QPixmap | None:
        """Обложка карточки, обрезанная по размеру width×height; None, если собственной картинки нет."""
        if not item.image_path or not os.path.isfile(item.image_path):
            return None
        key = (kind, item.id, item.image_path, self._stamp(item.image_path), width, height, dpr)
        if key in self._covers:
            return self._covers[key]
        raw = QPixmap(item.image_path)
        result: QPixmap | None = None
        if not raw.isNull():
            result = raw.scaled(int(width * dpr), int(height * dpr), Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                Qt.TransformationMode.SmoothTransformation)
            # обрезка по центру до нужного размера
            x, y = max((result.width() - int(width * dpr)) // 2, 0), max((result.height() - int(height * dpr)) // 2, 0)
            result = result.copy(x, y, int(width * dpr), int(height * dpr))
            result.setDevicePixelRatio(dpr)
        if len(self._covers) > 400:
            self._covers.clear()
        self._covers[key] = result
        return result
