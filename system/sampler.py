"""Фоновый поток сбора метрик ПК — один на всё приложение.

Потребители (окно, островок) регистрируются через acquire(name, interval_ms) и снимаются через
release(name). Пока потребителей нет — поток спит и ничего не измеряет, поэтому NOVA не нагружает ПК зря.
"""
from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QThread, Signal

from config import MIN_SAMPLE_INTERVAL_MS
from system.collector import Collector, Snapshot

log = logging.getLogger(__name__)


class Sampler(QThread):
    updated = Signal(object)  # Snapshot

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._consumers: dict[str, int] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_flag = False
        self.collector = Collector()
        self.last: Snapshot | None = None

    @property
    def history(self):
        return self.collector.history

    # -- подписка потребителей ----------------------------------------------
    def acquire(self, name: str, interval_ms: int) -> None:
        with self._lock:
            self._consumers[name] = max(int(interval_ms), MIN_SAMPLE_INTERVAL_MS)
        self._wake.set()

    def release(self, name: str) -> None:
        with self._lock:
            self._consumers.pop(name, None)
        self._wake.set()

    def _interval(self) -> int | None:
        with self._lock:
            return min(self._consumers.values()) if self._consumers else None

    # -- поток -------------------------------------------------------------
    def run(self) -> None:
        while not self._stop_flag:
            interval = self._interval()
            if interval is None:
                self._wake.wait(1.0)
                self._wake.clear()
                continue
            started = time.monotonic()
            try:
                snap = self.collector.collect()
                self.last = snap
                self.updated.emit(snap)
            except Exception:
                log.exception("Сбой сбора метрик")
            spent = time.monotonic() - started
            self._wake.wait(max(interval / 1000.0 - spent, 0.05))
            self._wake.clear()

    def shutdown(self) -> None:
        self._stop_flag = True
        self._wake.set()
        self.wait(2000)
        self.collector.shutdown()
