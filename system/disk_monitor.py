"""Диски: объём, свободное место, использование (psutil). Вызывается редко — по запросу."""
from __future__ import annotations

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class DiskStats:
    mountpoint: str
    fstype: str
    total: int
    used: int
    free: int
    percent: float


class DiskMonitor:
    def read(self) -> list[DiskStats]:
        disks: list[DiskStats] = []
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue  # пустые приводы и неразмеченные тома
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            disks.append(DiskStats(part.mountpoint, part.fstype, usage.total, usage.used, usage.free, usage.percent))
        return disks
