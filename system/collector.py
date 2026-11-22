"""Сбор одного «снимка» метрик ПК. Не зависит от Qt — поэтому тестируется отдельно."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

import psutil

from system.cpu_monitor import CpuMonitor, CpuStats
from system.gpu_monitor import GpuMonitor, GpuStats
from system.network_monitor import NetStats, NetworkMonitor
from system.ram_monitor import RamMonitor, RamStats
from system.sensors import SensorBridge

HISTORY_LEN = 120


@dataclass(frozen=True)
class Snapshot:
    ts: float
    cpu: CpuStats
    ram: RamStats
    gpu: GpuStats | None
    net: NetStats
    cpu_temp: float | None
    gpu_temp: float | None
    uptime_s: float | None


def merge_gpu(nvml: GpuStats | None, gpu_load: float | None, gpu_temp: float | None,
              gpu_name: str | None) -> GpuStats | None:
    """NVML приоритетнее; для остальных видеокарт берём данные LibreHardwareMonitor, если он запущен."""
    if nvml is not None:
        return GpuStats(nvml.name, nvml.percent, nvml.temp_c if nvml.temp_c is not None else gpu_temp,
                        nvml.vram_used, nvml.vram_total, nvml.source)
    if gpu_load is None and gpu_temp is None:
        return None
    return GpuStats(gpu_name, gpu_load, gpu_temp, None, None, "lhm")


class Collector:
    def __init__(self) -> None:
        self.cpu_monitor = CpuMonitor()
        self.ram_monitor = RamMonitor()
        self.net_monitor = NetworkMonitor()
        self.gpu_monitor = GpuMonitor()
        self.sensors = SensorBridge()
        self.history: dict[str, deque[float]] = {
            k: deque(maxlen=HISTORY_LEN) for k in ("cpu", "ram", "gpu", "net_down", "net_up")}

    def collect(self) -> Snapshot:
        cpu, ram, net = self.cpu_monitor.read(), self.ram_monitor.read(), self.net_monitor.read()
        sens = self.sensors.read()
        gpu = merge_gpu(self.gpu_monitor.read(), sens.gpu_load, sens.gpu_temp, sens.gpu_name)
        try:
            uptime: float | None = time.time() - psutil.boot_time()
        except Exception:
            uptime = None
        h = self.history
        h["cpu"].append(cpu.percent)
        h["ram"].append(ram.percent)
        if gpu and gpu.percent is not None:
            h["gpu"].append(gpu.percent)
        h["net_down"].append(net.down_bps)
        h["net_up"].append(net.up_bps)
        return Snapshot(time.time(), cpu, ram, gpu, net, sens.cpu_temp,
                        gpu.temp_c if gpu else None, uptime)

    def shutdown(self) -> None:
        self.gpu_monitor.shutdown()
