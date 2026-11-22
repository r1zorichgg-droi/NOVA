"""Температуры и загрузка через LibreHardwareMonitor (встроенный веб-сервер, localhost).

Включается в LibreHardwareMonitor: Options -> Remote Web Server -> Run.
NOVA ничего не внедряет и не грузит драйверов — только читает JSON с локального адреса.
Если LHM не запущен, значения остаются None (в интерфейсе — N/A).
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from config import LHM_URL

log = logging.getLogger(__name__)

_NUM = re.compile(r"-?\d+(?:[.,]\d+)?")
_CPU_TEMP_PREFERENCE = ("cpu package", "core (tctl/tdie)", "core (tdie)", "tctl/tdie", "cpu (tctl/tdie)", "cpu die")
_GPU_HINTS = ("gpu", "nvidia", "geforce", "radeon", "rtx", "gtx", "arc ", "intel(r) graphics", "intel uhd", "iris")
_CPU_HINTS = ("cpu", "ryzen", "intel core", "core i", "xeon", "threadripper", "athlon", "pentium")


@dataclass(frozen=True)
class SensorReadings:
    cpu_temp: float | None = None
    gpu_temp: float | None = None
    gpu_load: float | None = None
    gpu_name: str | None = None


def _number(text: Any) -> float | None:
    match = _NUM.search(str(text or ""))
    return float(match.group().replace(",", ".")) if match else None


def _hardware_kind(node: dict[str, Any]) -> str | None:
    label = f"{node.get('Text', '')} {node.get('ImageURL', '')}".lower()
    if any(h in label for h in _GPU_HINTS):
        return "gpu"
    if any(h in label for h in _CPU_HINTS):
        return "cpu"
    return None


def parse_lhm_tree(tree: dict[str, Any]) -> SensorReadings:
    """Разбирает дерево data.json LibreHardwareMonitor."""
    cpu_temps: list[tuple[str, float]] = []
    gpu_temps: list[tuple[str, float]] = []
    gpu_loads: list[tuple[str, float]] = []
    gpu_name: list[str] = []

    def walk(node: dict[str, Any], kind: str | None) -> None:
        children = node.get("Children") or []
        value_text = str(node.get("Value", ""))
        if not children and value_text:
            label = str(node.get("Text", "")).lower()
            value = _number(value_text)
            if value is not None and kind:
                if "°c" in value_text.lower():
                    (cpu_temps if kind == "cpu" else gpu_temps).append((label, value))
                elif kind == "gpu" and "%" in value_text and label == "gpu core":
                    gpu_loads.append((label, value))
        else:
            sub = _hardware_kind(node) or kind
            if sub == "gpu" and kind != "gpu" and node.get("Text"):
                gpu_name.append(str(node["Text"]))
            for child in children:
                walk(child, sub)

    walk(tree, None)

    cpu_temp = None
    for wanted in _CPU_TEMP_PREFERENCE:
        cpu_temp = next((v for label, v in cpu_temps if label == wanted), None)
        if cpu_temp is not None:
            break
    if cpu_temp is None and cpu_temps:
        cpu_temp = max(v for _, v in cpu_temps)

    gpu_temp = next((v for label, v in gpu_temps if label == "gpu core"), None)
    if gpu_temp is None and gpu_temps:
        gpu_temp = gpu_temps[0][1]
    return SensorReadings(cpu_temp, gpu_temp, gpu_loads[0][1] if gpu_loads else None,
                          gpu_name[0] if gpu_name else None)


class SensorBridge:
    """Опрашивает LHM не чаще, чем раз в `min_interval` секунд, и не долбит выключенный сервер."""

    def __init__(self, url: str = LHM_URL, timeout: float = 0.4,
                 min_interval: float = 2.0, retry_after: float = 15.0) -> None:
        self._url, self._timeout = url, timeout
        self._min_interval, self._retry_after = min_interval, retry_after
        self._last = SensorReadings()
        self._next_try = 0.0
        self.connected = False

    def read(self) -> SensorReadings:
        now = time.monotonic()
        if now < self._next_try:
            return self._last
        try:
            with urllib.request.urlopen(self._url, timeout=self._timeout) as resp:  # noqa: S310 (localhost)
                tree = json.load(resp)
            self._last = parse_lhm_tree(tree)
            self.connected = True
            self._next_try = now + self._min_interval
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            if self.connected:
                log.info("LibreHardwareMonitor перестал отвечать")
            self.connected = False
            self._last = SensorReadings()
            self._next_try = now + self._retry_after
        return self._last
