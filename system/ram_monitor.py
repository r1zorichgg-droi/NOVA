"""Оперативная память (psutil)."""
from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class RamStats:
    percent: float
    total: int
    used: int
    available: int


class RamMonitor:
    def read(self) -> RamStats:
        vm = psutil.virtual_memory()
        return RamStats(vm.percent, vm.total, vm.total - vm.available, vm.available)
