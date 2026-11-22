"""Значок NOVA в системном трее."""
from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from app_context import AppContext
from config import APP_NAME


class Tray(QSystemTrayIcon):
    def __init__(self, window, ctx: AppContext, icon: QIcon) -> None:  # noqa: ANN001 — MainWindow, без цикла импорта
        super().__init__(icon, window)
        self._window, self._ctx = window, ctx
        self.setToolTip(APP_NAME)
        self._menu = QMenu()   # ссылка нужна, иначе меню соберёт сборщик мусора
        open_action = self._menu.addAction("Открыть NOVA")
        open_action.triggered.connect(window.bring_to_front)
        self._island_action = self._menu.addAction("Dynamic Island")
        self._island_action.setCheckable(True)
        self._island_action.setChecked(bool(ctx.settings.get("island_enabled")))
        self._island_action.triggered.connect(lambda checked: ctx.settings.set("island_enabled", bool(checked)))
        self._menu.addSeparator()
        quit_action = self._menu.addAction("Выход")
        quit_action.triggered.connect(window.request_quit)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        ctx.settings.changed.connect(self._on_setting)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
            self._window.bring_to_front()

    def _on_setting(self, key: str) -> None:
        if key == "island_enabled":
            self._island_action.setChecked(bool(self._ctx.settings.get("island_enabled")))
