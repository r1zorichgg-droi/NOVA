"""Скорость сети: разница счётчиков psutil между двумя замерами."""
from __future__ import annotations

import time
from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class NetStats:
    down_bps: float   # байт/с
    up_bps: float


class NetworkMonitor:
    def __init__(self) -> None:
        self._last = psutil.net_io_counters()
        self._t = time.monotonic()

    def read(self) -> NetStats:
        now_t, cur = time.monotonic(), psutil.net_io_counters()
        dt = max(now_t - self._t, 1e-3)
        down = max(cur.bytes_recv - self._last.bytes_recv, 0) / dt
        up = max(cur.bytes_sent - self._last.bytes_sent, 0) / dt
        self._last, self._t = cur, now_t
        return NetStats(down, up)
