"""Загрузка и частота процессора (psutil)."""
from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class CpuStats:
    percent: float
    threads: int
    cores: int
    freq_mhz: float | None   # по данным Windows; под нагрузкой может не отражать буст


class CpuMonitor:
    def __init__(self) -> None:
        psutil.cpu_percent(interval=None)  # первый вызов всегда 0 — «прогреваем»
        self._threads = psutil.cpu_count(logical=True) or 0
        self._cores = psutil.cpu_count(logical=False) or self._threads

    def read(self) -> CpuStats:
        try:
            freq = psutil.cpu_freq()
            freq_mhz = float(freq.current) if freq and freq.current else None
        except (NotImplementedError, OSError, AttributeError):
            freq_mhz = None
        return CpuStats(psutil.cpu_percent(interval=None), self._threads, self._cores, freq_mhz)
