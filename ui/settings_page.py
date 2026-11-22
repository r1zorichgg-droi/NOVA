"""Настройки и персонализация: тема, акцентный цвет, прозрачность, анимации, имя, поведение окна."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton, QColorDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget)

from app_context import AppContext
from config import ACCENT_PRESETS, APP_VERSION, data_dir, resource_path
from ui import theme
from ui.bindings import SliderRow, bind_segmented, bind_toggle
from ui.widgets import Card, HLine, Page, make_label, qc, setting_row


class Swatch(QAbstractButton):
    """Круглый образец акцентного цвета."""

    def __init__(self, color_hex: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.color_hex = color_hex
        self.setCheckable(True)
        self.setFixedSize(30, 30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(color_hex.upper())

    def sizeHint(self) -> QSize:
        return QSize(30, 30)

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(self.color_hex))
        p.drawEllipse(QRectF(5, 5, 20, 20))
        if self.isChecked():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qc(theme.current().text), 2))
            p.drawEllipse(QRectF(1.5, 1.5, 27, 27))


class SettingsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Настройки", "Внешний вид и поведение NOVA. Всё сохраняется автоматически.")
        self._ctx = ctx
        s = ctx.settings

        look = Card(spacing=4)
        look.body.addWidget(make_label("Внешний вид", "h2"))
        look.body.addWidget(setting_row("Тема", "Тёмная тема — по умолчанию.",
                                        bind_segmented(s, "theme", [("dark", "Тёмная"), ("light", "Светлая")])))
        look.body.addWidget(HLine())

        swatches = QWidget()
        sw_lay = QHBoxLayout(swatches)
        sw_lay.setContentsMargins(0, 0, 0, 0)
        sw_lay.setSpacing(4)
        self._swatches: list[Swatch] = []
        for color in ACCENT_PRESETS:
            sw = Swatch(color)
            sw.clicked.connect(lambda _checked=False, c=color: s.set("accent", c))
            self._swatches.append(sw)
            sw_lay.addWidget(sw)
        custom = QPushButton("Свой цвет…")
        custom.clicked.connect(self._pick_custom_color)
        sw_lay.addSpacing(6)
        sw_lay.addWidget(custom)
        look.body.addWidget(setting_row("Акцентный цвет", "Цвет кнопок, переключателей, графиков и Dynamic Island.",
                                        swatches))
        look.body.addWidget(HLine())
        look.body.addWidget(setting_row(
            "Прозрачность окна", "Делает главное окно NOVA слегка прозрачным.",
            SliderRow(s, "window_opacity", 80, 100, scale=100, fmt=lambda v: f"{v * 100:.0f}%")))
        look.body.addWidget(HLine())
        look.body.addWidget(setting_row(
            "Анимации", "Плавность переходов между страницами и анимации Dynamic Island.",
            bind_segmented(s, "animations", [("smooth", "Плавные"), ("fast", "Быстрые"), ("off", "Выкл")])))
        self.body.addWidget(look)

        behavior = Card(spacing=4)
        behavior.body.addWidget(make_label("Поведение", "h2"))
        self.name_edit = QLineEdit(str(s.get("user_name")))
        self.name_edit.setPlaceholderText("Имя пользователя Windows")
        self.name_edit.setFixedWidth(240)
        self.name_edit.editingFinished.connect(lambda: s.set("user_name", self.name_edit.text().strip()))
        behavior.body.addWidget(setting_row("Имя в приветствии", "Пусто — используется имя пользователя Windows.",
                                            self.name_edit))
        behavior.body.addWidget(HLine())
        behavior.body.addWidget(setting_row(
            "Сворачивать в трей при закрытии", "Закрытие окна не завершает NOVA: Dynamic Island продолжает работать. "
                                               "Полностью выйти можно через значок в трее.",
            bind_toggle(s, "close_to_tray")))
        self.body.addWidget(behavior)

        library = Card(spacing=4)
        library.body.addWidget(make_label("Библиотека игр и программ", "h2"))
        library.body.addWidget(setting_row(
            "Искать автоматически при запуске NOVA",
            "Через пару секунд после старта NOVA находит новые игры (Steam, Epic Games, GOG, Minecraft) и программы "
            "(меню «Пуск», Microsoft Store). Убранные вами карточки не возвращаются. Файлы NOVA только читает.",
            bind_toggle(s, "auto_scan")))
        self.body.addWidget(library)

        notifications = Card(spacing=4)
        notifications.body.addWidget(make_label("Уведомления Dynamic Island", "h2"))
        for key, title, desc in (
            ("island_notify_usb", "USB-устройства", "Сообщать о подключении и отключении USB-устройств."),
            ("island_notify_volume", "Громкость", "Показывать уведомление при изменении громкости."),
            ("island_notify_apps", "Запуск программ", "Сообщать о запуске приложений."),
            ("island_notify_music", "Музыка", "Показывать музыкальные уведомления, если источник их поддерживает."),
        ):
            notifications.body.addWidget(setting_row(title, desc, bind_toggle(s, key)))
            notifications.body.addWidget(HLine())
        self.body.addWidget(notifications)

        data = Card(spacing=6)
        data.body.addWidget(make_label("Данные", "h2"))
        data.body.addWidget(make_label(f"База данных, настройки и лог хранятся здесь:\n{data_dir()}", "muted", wrap=True))
        row = QHBoxLayout()
        open_btn = QPushButton("Открыть папку")
        open_btn.clicked.connect(self._open_data_dir)
        reset_btn = QPushButton("Сбросить настройки")
        reset_btn.setObjectName("danger")
        reset_btn.clicked.connect(self._reset)
        row.addWidget(open_btn)
        row.addWidget(reset_btn)
        row.addStretch(1)
        data.body.addLayout(row)
        data.body.addWidget(make_label(
            "Сброс возвращает настройки к значениям по умолчанию. Библиотека игр и приложений не затрагивается, "
            "файлы на компьютере не удаляются. NOVA не отправляет ваши данные на сторонние серверы.", "caption", wrap=True))
        self.body.addWidget(data)

        about = Card(spacing=8)
        about_row = QHBoxLayout()
        about_row.setContentsMargins(0, 0, 0, 0)
        about_row.setSpacing(14)

        about_logo = QLabel()
        about_logo.setFixedSize(84, 84)
        about_logo.setStyleSheet("background: transparent;")
        about_pixmap = QPixmap(str(resource_path("assets", "icons", "nova.png")))
        if not about_pixmap.isNull():
            about_logo.setPixmap(about_pixmap.scaled(
                84, 84, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        about_row.addWidget(about_logo, 0, Qt.AlignmentFlag.AlignVCenter)

        about_info = QVBoxLayout()
        about_info.setContentsMargins(0, 0, 0, 0)
        about_info.setSpacing(6)
        about_info.addWidget(make_label("О программе и создателе", "h2"))
        about_info.addWidget(make_label(f"Версия {APP_VERSION}", "muted"))
        about_btn = QPushButton("О программе")
        about_btn.setObjectName("primary")
        about_btn.clicked.connect(self._open_about)
        about_info.addWidget(about_btn, 0, Qt.AlignmentFlag.AlignLeft)
        about_row.addLayout(about_info, 1)
        about.body.addLayout(about_row)
        self.body.addWidget(about)
        self.finish()

        s.changed.connect(self._on_setting)
        self._sync_swatches()

    def _on_setting(self, key: str) -> None:
        if key == "accent":
            self._sync_swatches()
        elif key == "user_name" and self.name_edit.text().strip() != self._ctx.settings.get("user_name"):
            self.name_edit.setText(str(self._ctx.settings.get("user_name")))

    def _sync_swatches(self) -> None:
        current = str(self._ctx.settings.get("accent")).lower()
        for sw in self._swatches:
            sw.setChecked(sw.color_hex.lower() == current)

    def _pick_custom_color(self) -> None:
        start = QColor(str(self._ctx.settings.get("accent")))
        color = QColorDialog.getColor(start, self, "Акцентный цвет")
        if color.isValid():
            self._ctx.settings.set("accent", color.name())

    def _open_data_dir(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(data_dir())))

    def _open_about(self) -> None:
        about_file = resource_path("assets", "about.html")
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(about_file)))

    def _reset(self) -> None:
        answer = QMessageBox.question(
            self, "Сброс настроек",
            "Вернуть все настройки NOVA к значениям по умолчанию?\n\nБиблиотека игр и приложений и файлы на "
            "компьютере не затрагиваются.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self._ctx.settings.reset()
