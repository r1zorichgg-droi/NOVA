"""Определение полноэкранной игры. Только чтение состояния окна переднего плана раз в секунду.

Не использует хуки, не внедряется в процессы, не перехватывает клавиши и ничего не рисует поверх игр —
поэтому не конфликтует с античитами.
"""
from __future__ import annotations

import logging
import os

from PySide6.QtCore import QObject, QTimer, Signal

from island.fullscreen import is_fullscreen_window
from settings import AppSettings
from system import winapi

log = logging.getLogger(__name__)

EXIT_CONFIRMATIONS = 2  # сколько опросов подряд «не полноэкранно», чтобы вернуть островок (защита от мигания при Alt+Tab)


class GameDetector(QObject):
    fullscreen_changed = Signal(bool, str)   # (идёт ли полноэкранное приложение, имя процесса)

    def __init__(self, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._own_pid = os.getpid()
        self._active = False
        self._name = ""
        self._misses = 0
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)

    @property
    def active(self) -> bool:
        return self._active

    @property
    def process_name(self) -> str:
        return self._name

    def start(self) -> None:
        if winapi.IS_WINDOWS and not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self._active:
            self._active, self._name, self._misses = False, "", 0
            self.fullscreen_changed.emit(False, "")

    def _poll(self) -> None:
        try:
            ignore = frozenset(str(n).lower() for n in self._settings.get("island_fullscreen_ignore"))
            info = winapi.get_foreground_info()
            state = winapi.shell_notification_state() if info is not None else None
            full, name = is_fullscreen_window(info, self._own_pid, state, winapi.process_name, ignore)
        except Exception:
            log.exception("Сбой определения полноэкранного режима")
            return
        if full:
            self._misses = 0
            if not self._active or name != self._name:
                self._active, self._name = True, name
                self.fullscreen_changed.emit(True, name)
        elif self._active:
            self._misses += 1
            if self._misses >= EXIT_CONFIRMATIONS:
                self._active, self._name, self._misses = False, "", 0
                self.fullscreen_changed.emit(False, "")
