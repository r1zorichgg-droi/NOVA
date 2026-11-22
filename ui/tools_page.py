"""Быстрый доступ к встроенным инструментам и страницам настроек Windows."""
from __future__ import annotations

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QPushButton

from ui.widgets import Card, Page, make_label


TOOLS = [
    ("Диспетчер задач", "Процессы и нагрузка", "process", "taskmgr.exe"),
    ("Диспетчер устройств", "Драйверы и подключённое оборудование", "process", "devmgmt.msc"),
    ("Службы Windows", "Состояние системных служб", "services", "services.msc"),
    ("Просмотр событий", "Журналы Windows и диагностика ошибок", "events", "eventvwr.msc"),
    ("Управление дисками", "Разделы и накопители", "disk", "diskmgmt.msc"),
    ("Параметры звука", "Устройства вывода и ввода", "sound", "ms-settings:sound"),
    ("Параметры экрана", "Разрешение и несколько мониторов", "display", "ms-settings:display"),
    ("Центр обновления", "Обновления и перезапуск Windows", "update", "ms-settings:windowsupdate"),
    ("Сеть и Интернет", "Wi-Fi, Ethernet и параметры сети", "network", "ms-settings:network-status"),
    ("Установленные приложения", "Список приложений Windows", "apps", "ms-settings:appsfeatures"),
    ("Хранилище", "Место на дисках и очистка Windows", "disk", "ms-settings:storagesense"),
    ("Bluetooth", "Подключённые Bluetooth-устройства", "bluetooth", "ms-settings:bluetooth"),
]


class ToolsPage(Page):
    def __init__(self) -> None:
        super().__init__("Инструменты Windows", "Часто используемые системные окна в одном месте. NOVA просто открывает штатные инструменты Windows.")
        grid = QGridLayout()
        grid.setSpacing(12)
        for index, (title, description, _icon, target) in enumerate(TOOLS):
            card = Card(spacing=7)
            card.body.addWidget(make_label(title, "h3"))
            card.body.addWidget(make_label(description, "muted", wrap=True))
            button = QPushButton("Открыть")
            button.clicked.connect(lambda _checked=False, value=target: self._open(value))
            card.body.addWidget(button)
            grid.addWidget(card, index // 2, index % 2)
        self.body.addLayout(grid)
        self.body.addWidget(make_label(
            "Некоторые системные окна могут запросить подтверждение администратора. Это обычный запрос Windows.",
            "caption", wrap=True,
        ))
        self.finish()

    @staticmethod
    def _open(target: str) -> None:
        if target.startswith("ms-settings:"):
            QDesktopServices.openUrl(QUrl(target))
        elif target.lower().endswith(".msc"):
            QProcess.startDetached("mmc.exe", [target])
        else:
            QProcess.startDetached(target, [])
