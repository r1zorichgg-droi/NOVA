"""Векторные значки, нарисованные кодом (QPainter): не нужны файлы, чётко выглядят на любом масштабе."""
from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


def _home(p: QPainter, _c: QColor) -> None:
    p.drawPolyline([QPointF(3.5, 11.5), QPointF(12, 4), QPointF(20.5, 11.5)])
    p.drawPolyline([QPointF(5.8, 10), QPointF(5.8, 20), QPointF(10, 20), QPointF(10, 14.5),
                    QPointF(14, 14.5), QPointF(14, 20), QPointF(18.2, 20), QPointF(18.2, 10)])


def _games(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(2.8, 7.5, 18.4, 10.5), 5.2, 5.2)
    p.drawLine(QPointF(7.6, 10.6), QPointF(7.6, 14.8))
    p.drawLine(QPointF(5.5, 12.7), QPointF(9.7, 12.7))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(15.4, 11.4), 1.1, 1.1)
    p.drawEllipse(QPointF(17.8, 13.8), 1.1, 1.1)


def _apps(p: QPainter, _c: QColor) -> None:
    for x, y in ((4, 4), (13.5, 4), (4, 13.5), (13.5, 13.5)):
        p.drawRoundedRect(QRectF(x, y, 6.5, 6.5), 2, 2)


def _monitor(p: QPainter, _c: QColor) -> None:
    p.drawPolyline([QPointF(2.5, 12.5), QPointF(7, 12.5), QPointF(10, 5), QPointF(14, 19.5),
                    QPointF(17, 12.5), QPointF(21.5, 12.5)])


def _island(p: QPainter, c: QColor) -> None:
    p.drawRoundedRect(QRectF(2.8, 8.2, 18.4, 7.6), 3.8, 3.8)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(7.6, 12), 1.2, 1.2)


def _automation(p: QPainter, _c: QColor) -> None:
    path = QPainterPath()
    path.moveTo(13.2, 2.8)
    for x, y in ((6, 13.4), (11.2, 13.4), (10.4, 21.2), (18, 10.2), (12.6, 10.2)):
        path.lineTo(x, y)
    path.closeSubpath()
    p.drawPath(path)


def _settings(p: QPainter, _c: QColor) -> None:
    p.drawEllipse(QPointF(12, 12), 3.1, 3.1)
    p.drawEllipse(QPointF(12, 12), 7.0, 7.0)
    for i in range(8):
        a = math.radians(i * 45)
        p.drawLine(QPointF(12 + 7.0 * math.cos(a), 12 + 7.0 * math.sin(a)),
                   QPointF(12 + 9.6 * math.cos(a), 12 + 9.6 * math.sin(a)))


def _power(p: QPainter, _c: QColor) -> None:
    p.drawLine(QPointF(12, 3.2), QPointF(12, 11.6))
    p.drawArc(QRectF(4.6, 5.4, 14.8, 14.8), 120 * 16, 300 * 16)


def _optimize(p: QPainter, c: QColor) -> None:
    p.drawArc(QRectF(3.2, 4.2, 17.6, 17.6), 210 * 16, -240 * 16)
    p.drawLine(QPointF(12, 13), QPointF(16.2, 8.6))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(c)
    p.drawEllipse(QPointF(12, 13), 1.5, 1.5)


def _debloat(p: QPainter, _c: QColor) -> None:
    p.drawLine(QPointF(4, 6.5), QPointF(20, 6.5))
    p.drawPolyline([QPointF(9.5, 6.5), QPointF(9.5, 4), QPointF(14.5, 4), QPointF(14.5, 6.5)])
    p.drawPolyline([QPointF(6, 6.5), QPointF(7, 20), QPointF(17, 20), QPointF(18, 6.5)])
    p.drawLine(QPointF(10, 10.5), QPointF(10, 16))
    p.drawLine(QPointF(14, 10.5), QPointF(14, 16))


def _bolt(p: QPainter, _c: QColor) -> None:
    _automation(p, _c)


def _install(p: QPainter, _c: QColor) -> None:
    p.drawLine(QPointF(12, 3.5), QPointF(12, 14.5))
    p.drawPolyline([QPointF(7.5, 10.5), QPointF(12, 15), QPointF(16.5, 10.5)])
    p.drawPolyline([QPointF(4, 16), QPointF(4, 20), QPointF(20, 20), QPointF(20, 16)])


def _processes(p: QPainter, _c: QColor) -> None:
    for y, width in ((5, 15), (10, 11), (15, 17), (20, 8)):
        p.drawRoundedRect(QRectF(4, y - 1.5, width, 3), 1.5, 1.5)


def _tools(p: QPainter, _c: QColor) -> None:
    p.drawEllipse(QPointF(8, 8), 3.3, 3.3)
    p.drawLine(QPointF(10.5, 10.5), QPointF(19.5, 19.5))
    p.drawLine(QPointF(5.5, 5.5), QPointF(3.5, 3.5))
    p.drawLine(QPointF(10.5, 5.5), QPointF(12.5, 3.5))


def _system(p: QPainter, _c: QColor) -> None:
    p.drawRoundedRect(QRectF(3.5, 3.5, 17, 17), 4, 4)
    p.drawLine(QPointF(12, 10), QPointF(12, 16))
    p.drawEllipse(QPointF(12, 7), 0.9, 0.9)


def _network(p: QPainter, _c: QColor) -> None:
    p.drawArc(QRectF(3, 5, 18, 14), 25 * 16, 130 * 16)
    p.drawArc(QRectF(6, 8, 12, 9), 25 * 16, 130 * 16)
    p.drawEllipse(QPointF(12, 18), 1.5, 1.5)


def _updates(p: QPainter, _c: QColor) -> None:
    p.drawArc(QRectF(4, 4, 16, 16), 35 * 16, 285 * 16)
    p.drawPolyline([QPointF(16, 4), QPointF(20, 4), QPointF(20, 8)])
    p.drawPolyline([QPointF(8, 20), QPointF(4, 20), QPointF(4, 16)])


_ICONS: dict[str, Callable[[QPainter, QColor], None]] = {
    "home": _home, "games": _games, "apps": _apps, "monitor": _monitor,
    "island": _island, "automation": _automation, "settings": _settings,
    "power": _power, "optimize": _optimize, "debloat": _debloat, "bolt": _bolt,
    "install": _install, "processes": _processes, "tools": _tools, "system": _system,
    "network": _network, "updates": _updates,
}


def draw_icon(p: QPainter, name: str, rect: QRectF, color: QColor, width: float = 1.8) -> None:
    """Рисует значок `name` в квадрате rect. Сетка значков — 24x24."""
    fn = _ICONS.get(name)
    if fn is None:
        return
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    s = min(rect.width(), rect.height()) / 24.0
    p.translate(rect.center().x() - 12 * s, rect.center().y() - 12 * s)
    p.scale(s, s)
    p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    fn(p, color)
    p.restore()


def icon_pixmap(name: str, color: QColor, size: int, dpr: float = 2.0) -> QPixmap:
    pix = QPixmap(int(size * dpr), int(size * dpr))
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    draw_icon(p, name, QRectF(0, 0, size, size), color)
    p.end()
    return pix
