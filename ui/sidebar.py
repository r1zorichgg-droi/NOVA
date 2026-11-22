"""Боковая панель навигации."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from config import APP_NAME, resource_path
from ui import theme
from ui.icons import draw_icon
from ui.widgets import make_label, qc

# (ключ значка, подпись) — порядок совпадает с порядком страниц в главном окне.
NAV_ITEMS: list[tuple[str, str]] = [
    ("home", "Главная"),
    ("games", "Игры"),
    ("apps", "Приложения"),
    ("install", "Установить"),
    ("power", "Электропитание"),
    ("optimize", "Оптимизация"),
    ("debloat", "Выпиление"),
    ("monitor", "Мониторинг ПК"),
    ("network", "Network Monitor"),
    ("processes", "Процессы"),
    ("tools", "Инструменты Windows"),
    ("system", "О системе"),
    ("island", "Dynamic Island"),
    ("automation", "Автоматизация"),
    ("updates", "Обновления NOVA"),
    ("settings", "Настройки"),
]
SETTINGS_INDEX = len(NAV_ITEMS) - 1
NAV_DESCRIPTIONS = {
    "home": "Сводка нагрузки и быстрый обзор компьютера",
    "games": "Библиотека игр и запуск игровых профилей",
    "apps": "Установленные программы и быстрый запуск",
    "install": "Загрузка и запуск официальных установщиков",
    "power": "Схемы питания и параметры энергопотребления",
    "optimize": "Игровой режим и безопасные настройки производительности",
    "debloat": "Управление установленными приложениями Windows",
    "monitor": "Графики CPU, GPU, RAM, сети и дисков",
    "network": "Задержка, потери пакетов и скорость интернет-соединения",
    "processes": "Просмотр активных процессов и потребления ресурсов",
    "tools": "Быстрый доступ к системным инструментам Windows",
    "system": "Сведения об ОС и оборудовании компьютера",
    "island": "Настройка Dynamic Island и его уведомлений",
    "automation": "Программы, запускаемые вместе с Windows",
    "updates": "Проверка, загрузка и установка новых версий NOVA",
    "settings": "Тема, оформление и поведение NOVA",
}


class NavButton(QAbstractButton):
    def __init__(self, icon: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._icon = icon
        self.setText(text)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(42)
        self._hover = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def sizeHint(self) -> QSize:
        return QSize(200, 42)

    def _go(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._hover)
        self._anim.setEndValue(target)
        self._anim.start()

    def _on_anim(self, value: object) -> None:
        self._hover = float(value)  # type: ignore[arg-type]
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802, ANN001
        self._go(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802, ANN001
        self._go(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        pal = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        body = QRectF(0, 0, self.width(), self.height())
        checked = self.isChecked()
        if checked:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(pal.accent_soft))
            p.drawRoundedRect(body, 12, 12)
            p.setBrush(qc(pal.accent))
            p.drawRoundedRect(QRectF(0, 12, 3.5, self.height() - 24), 1.75, 1.75)
        elif self._hover > 0.01:
            c = QColor(*pal.hover)
            c.setAlpha(int(pal.hover[3] * self._hover))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(c)
            p.drawRoundedRect(body, 12, 12)
        color = qc(pal.accent_fg) if checked else qc(pal.text if self._hover > 0.5 else pal.muted)
        draw_icon(p, self._icon, QRectF(16, (self.height() - 22) / 2, 22, 22), color)
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold if checked else QFont.Weight.Medium)
        p.setFont(font)
        p.setPen(color)
        p.drawText(QRectF(52, 0, self.width() - 60, self.height()),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.text())


class Sidebar(QFrame):
    page_selected = Signal(int)

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(232)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 18, 14, 14)
        lay.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(6, 0, 0, 0)
        brand.setSpacing(10)
        logo = QLabel()
        pix = QPixmap(str(resource_path("assets", "icons", "nova.png")))
        if not pix.isNull():
            scaled = pix.scaled(68, 68, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            scaled.setDevicePixelRatio(2.0)
            logo.setPixmap(scaled)
        logo.setFixedSize(34, 34)
        brand.addWidget(logo)
        brand.addWidget(make_label(APP_NAME, "h2"))
        brand.addStretch(1)
        lay.addLayout(brand)
        lay.addSpacing(18)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[NavButton] = []
        for index, (icon, text) in enumerate(NAV_ITEMS):
            btn = NavButton(icon, text)
            btn.setToolTip(NAV_DESCRIPTIONS.get(icon, text))
            btn.clicked.connect(lambda _checked=False, i=index: self.page_selected.emit(i))
            self._group.addButton(btn, index)
            self._buttons.append(btn)
            if index == SETTINGS_INDEX:
                lay.addStretch(1)
            lay.addWidget(btn)

        footer = make_label(f"Версия {version}", "caption")
        footer.setContentsMargins(10, 8, 0, 0)
        lay.addWidget(footer)

    def set_current(self, index: int) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setChecked(True)
