"""Сведения о системе и оборудовании."""
from __future__ import annotations

import os
import platform

import psutil
from PySide6.QtWidgets import QGridLayout

from ui.widgets import Card, Page, make_label


class SystemPage(Page):
    def __init__(self) -> None:
        super().__init__("О системе", "Краткая информация о Windows и оборудовании этого компьютера.")
        grid = QGridLayout()
        grid.setSpacing(14)
        cards = [
            ("Операционная система", f"{platform.system()} {platform.release()}\n{platform.version()}"),
            ("Процессор", platform.processor() or "Модель не определена"),
            ("Ядра и потоки", f"Физические ядра: {psutil.cpu_count(logical=False) or 'N/A'}\nЛогические потоки: {psutil.cpu_count(logical=True) or 'N/A'}"),
            ("Оперативная память", self._memory_text()),
            ("Архитектура", f"ОС: {platform.machine()}\nPython: {platform.architecture()[0]}"),
            ("Диски", self._disk_text()),
        ]
        for index, (title, value) in enumerate(cards):
            card = Card(spacing=8)
            card.body.addWidget(make_label(title, "h3"))
            card.body.addWidget(make_label(value, "big", wrap=True))
            grid.addWidget(card, index // 2, index % 2)
        self.body.addLayout(grid)

        note = Card(spacing=6)
        note.body.addWidget(make_label("Диагностика", "h2"))
        note.body.addWidget(make_label(
            "Эта вкладка только читает сведения Windows. Она не меняет драйверы, системные файлы или параметры BIOS. "
            "Точные названия некоторых устройств Windows может не передавать обычным приложениям.",
            "muted", wrap=True,
        ))
        self.body.addWidget(note)
        self.finish()

    @staticmethod
    def _memory_text() -> str:
        mem = psutil.virtual_memory()
        total = mem.total / (1024 ** 3)
        available = mem.available / (1024 ** 3)
        return f"Всего: {total:.1f} ГБ\nДоступно сейчас: {available:.1f} ГБ"

    @staticmethod
    def _disk_text() -> str:
        parts = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            if usage.total:
                parts.append(f"{part.device} — свободно {usage.free / (1024 ** 3):.1f} из {usage.total / (1024 ** 3):.1f} ГБ")
        return "\n".join(parts[:8]) or "Данные о дисках недоступны"
