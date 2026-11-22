"""Настройки приложения: типизированный кэш поверх SQLite + сигнал об изменении.

Запись в базу откладывается на 300 мс и объединяется, чтобы ползунки не писали на диск на каждый пиксель.
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from config import DEFAULT_SETTINGS, coerce
from database.database import Database

log = logging.getLogger(__name__)


class AppSettings(QObject):
    changed = Signal(str)

    def __init__(self, db: Database, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db = db
        stored = db.all_settings()
        self._values: dict[str, Any] = {
            key: coerce(stored.get(key, default), default) for key, default in DEFAULT_SETTINGS.items()}
        self._dirty: set[str] = set()
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(300)
        self._flush_timer.timeout.connect(self.flush)

    def get(self, key: str) -> Any:
        return self._values[key]

    def set(self, key: str, value: Any) -> None:
        if key not in DEFAULT_SETTINGS:
            raise KeyError(key)
        value = coerce(value, DEFAULT_SETTINGS[key])
        if self._values.get(key) == value:
            return
        self._values[key] = value
        self._dirty.add(key)
        self._flush_timer.start()
        self.changed.emit(key)

    def flush(self) -> None:
        if not self._dirty:
            return
        try:
            self._db.set_settings({k: self._values[k] for k in self._dirty})
            self._dirty.clear()
        except Exception:
            log.exception("Не удалось сохранить настройки")

    def reset(self) -> None:
        """Возвращает все настройки к значениям по умолчанию (библиотека игр и приложений не затрагивается)."""
        self._db.reset_settings()
        self._dirty.clear()
        for key, default in DEFAULT_SETTINGS.items():
            if self._values.get(key) != default:
                self._values[key] = list(default) if isinstance(default, list) else default
                self.changed.emit(key)
