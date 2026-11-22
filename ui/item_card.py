"""Карточка игры или программы: обложка/значок, название, кнопка «Запустить» и меню «⋯»."""
from __future__ import annotations

import math

from PySide6.QtCore import QPoint, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QMenu, QPushButton, QVBoxLayout, QWidget

from database.models import LibraryItem
from ui.icon_cache import IconCache, tile_color
from ui.widgets import ElidedLabel

SOURCE_LABELS = {
    "steam": "Steam", "epic": "Epic Games", "gog": "GOG", "minecraft": "Minecraft",
    "shortcut": "Программа", "uwp": "Microsoft Store", "manual": "Добавлено вручную",
}


def source_label(item: LibraryItem) -> str:
    return SOURCE_LABELS.get(item.source, item.source or "—")


def meta_text(item: LibraryItem) -> str:
    parts = [source_label(item)]
    if item.category:
        parts.append(item.category)
    return " · ".join(parts)


def star_polygon(cx: float, cy: float, outer: float) -> QPolygonF:
    inner = outer * 0.45
    pts = []
    for i in range(10):
        r = outer if i % 2 == 0 else inner
        a = -math.pi / 2 + i * math.pi / 5
        pts.append(QPointF(cx + r * math.cos(a), cy + r * math.sin(a)))
    return QPolygonF(pts)


class CoverWidget(QWidget):
    """Обложка карточки: картинка, а если её нет — значок программы на цветном фоне. Звезда в углу — избранное."""
    clicked = Signal()
    favorite_toggled = Signal()

    def __init__(self, kind: str, item: LibraryItem, icons: IconCache, size: QSize, radius: float = 12.0,
                 running: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._kind, self._item, self._icons, self._radius = kind, item, icons, radius
        self._running = running
        self.setFixedSize(size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._star = QRectF(size.width() - 38, 8, 30, 30)

    def set_item(self, item: LibraryItem, running: bool) -> None:
        self._item, self._running = item, running
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        w, h = float(self.width()), float(self.height())
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, w, h), self._radius, self._radius)
        p.setClipPath(clip)
        dpr = self.devicePixelRatioF()
        item = self._item
        cover = self._icons.cover(self._kind, item, self.width(), self.height(), dpr)
        if cover is not None:
            p.drawPixmap(0, 0, cover)
        else:
            base = tile_color(item.title)
            grad = QLinearGradient(0, 0, w, h)
            grad.setColorAt(0, base.lighter(118))
            grad.setColorAt(1, base.darker(165))
            p.fillRect(QRectF(0, 0, w, h), grad)
            side = int(min(h * 0.52, 72))
            pix = self._icons.pixmap(self._kind, item, side, max(dpr, 2.0))
            p.drawPixmap(QRectF((w - side) / 2, (h - side) / 2, side, side), pix, QRectF(pix.rect()))
        scrim = QLinearGradient(0, 0, 0, 46)
        scrim.setColorAt(0, QColor(0, 0, 0, 105))
        scrim.setColorAt(1, QColor(0, 0, 0, 0))
        p.fillRect(QRectF(0, 0, w, 46), scrim)

        # избранное
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(self._star)
        star = star_polygon(self._star.center().x(), self._star.center().y() + 0.5, 8.5)
        if item.favorite:
            p.setBrush(QColor(255, 204, 51))
            p.drawPolygon(star)
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(255, 255, 255, 215), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                          Qt.PenJoinStyle.RoundJoin))
            p.drawPolygon(star)

        # состояние
        badge = ("Запущено", QColor(52, 211, 153)) if self._running else (
            ("Не найдена на ПК", QColor(248, 113, 113)) if item.opts.get("uninstalled") else None)
        if badge:
            font = QFont(self.font())
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            p.setFont(font)
            text_w = QFontMetrics(font).horizontalAdvance(badge[0]) + 16
            pill = QRectF(8, 10, text_w, 22)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 150))
            p.drawRoundedRect(pill, 11, 11)
            p.setBrush(badge[1])
            p.drawEllipse(QPointF(pill.x() + 9, pill.center().y()), 3, 3)
            p.setPen(QColor(255, 255, 255, 240))
            p.drawText(pill.adjusted(16, 0, 0, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, badge[0])

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if self._star.contains(event.position()):
            self.favorite_toggled.emit()
        else:
            self.clicked.emit()


class ItemCard(QFrame):
    """action(действие, вид, id): launch | open | optimize | edit | favorite | pin | folder | remove."""
    action = Signal(str, str, int)
    WIDTH, COVER_H = 204, 128

    def __init__(self, kind: str, item: LibraryItem, icons: IconCache, running: bool, pinned: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind, self.item, self._running, self._pinned = kind, item, running, pinned
        self.setObjectName("card")
        self.setFixedWidth(self.WIDTH)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 12)
        lay.setSpacing(6)

        self.cover = CoverWidget(kind, item, icons, QSize(self.WIDTH - 18, self.COVER_H), running=running)
        self.cover.clicked.connect(lambda: self._emit("open"))
        self.cover.favorite_toggled.connect(lambda: self._emit("favorite"))
        lay.addWidget(self.cover)

        title = ElidedLabel(item.title, "h3")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.clicked.connect(lambda: self._emit("open"))
        title.setContentsMargins(4, 2, 4, 0)
        lay.addWidget(title)
        meta = ElidedLabel(meta_text(item), "caption")
        meta.setContentsMargins(4, 0, 4, 4)
        lay.addWidget(meta)

        row = QHBoxLayout()
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(6)
        missing = bool(item.opts.get("uninstalled"))
        self.play_btn = QPushButton("Запущено" if running else ("Не найдена" if missing else "Запустить"))
        self.play_btn.setObjectName("primary")
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setEnabled(not running and not missing)
        self.play_btn.setFixedHeight(36)
        self.play_btn.clicked.connect(lambda: self._emit("launch"))
        self.menu_btn = QPushButton("⋯")
        self.menu_btn.setObjectName("dots")
        self.menu_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.menu_btn.setFixedSize(40, 36)
        self.menu_btn.setToolTip("Оптимизация, изменить, удалить из NOVA")
        self.menu_btn.clicked.connect(self._open_menu)
        row.addWidget(self.play_btn, 1)
        row.addWidget(self.menu_btn)
        lay.addLayout(row)

    def _emit(self, key: str) -> None:
        self.action.emit(key, self.kind, self.item.id)

    def _open_menu(self) -> None:
        item = self.item
        menu = QMenu(self)
        launch = menu.addAction("Запустить")
        launch.setEnabled(not self._running and not item.opts.get("uninstalled"))
        launch.triggered.connect(lambda: self._emit("launch"))
        menu.addSeparator()
        for text, key in (("Оптимизация…", "optimize"), ("Изменить…", "edit"),
                          ("Убрать из избранного" if item.favorite else "В избранное", "favorite"),
                          ("Открепить от острова" if self._pinned else "Закрепить в острове", "pin")):
            menu.addAction(text).triggered.connect(lambda _checked=False, k=key: self._emit(k))
        if item.exe_path or item.install_dir:
            menu.addAction("Показать в проводнике").triggered.connect(lambda: self._emit("folder"))
        menu.addSeparator()
        menu.addAction("Удалить из NOVA…").triggered.connect(lambda: self._emit("remove"))
        menu.exec(self.menu_btn.mapToGlobal(QPoint(0, self.menu_btn.height() + 4)))
