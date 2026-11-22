"""GPU: NVIDIA через NVML (пакет nvidia-ml-py / pynvml). Остальные видеокарты — через сенсоры LHM.

Если данные недоступны, поля остаются None, а интерфейс показывает N/A.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

try:
    import pynvml  # type: ignore[import-not-found,unused-ignore]
    _NVML_IMPORTED = True
except Exception:  # ImportError или сбой загрузки библиотеки
    pynvml = None
    _NVML_IMPORTED = False


@dataclass(frozen=True)
class GpuStats:
    name: str | None = None
    percent: float | None = None
    temp_c: float | None = None
    vram_used: int | None = None
    vram_total: int | None = None
    source: str | None = None       # "nvml" | "lhm" | None


class GpuMonitor:
    def __init__(self) -> None:
        self._handle = None
        self._name: str | None = None
        self.status = "pynvml не установлен"
        if not _NVML_IMPORTED:
            return
        try:
            pynvml.nvmlInit()
            if pynvml.nvmlDeviceGetCount() == 0:
                self.status = "NVIDIA-видеокарта не найдена"
                return
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            name = pynvml.nvmlDeviceGetName(self._handle)
            self._name = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)
            self.status = f"NVML: {self._name}"
        except Exception as exc:  # NVMLError, OSError при отсутствии драйвера
            log.info("NVML недоступен: %s", exc)
            self.status = "NVML недоступен (нет драйвера NVIDIA)"
            self._handle = None

    @property
    def available(self) -> bool:
        return self._handle is not None

    def read(self) -> GpuStats | None:
        if self._handle is None:
            return None
        try:
            util = pynvml.nvmlDeviceGetUtilizationRates(self._handle)
            mem = pynvml.nvmlDeviceGetMemoryInfo(self._handle)
            try:
                temp: float | None = float(pynvml.nvmlDeviceGetTemperature(self._handle, pynvml.NVML_TEMPERATURE_GPU))
            except Exception:
                temp = None
            return GpuStats(self._name, float(util.gpu), temp, int(mem.used), int(mem.total), "nvml")
        except Exception as exc:
            log.debug("Сбой чтения NVML: %s", exc)
            return None

    def shutdown(self) -> None:
        if self._handle is not None and pynvml is not None:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._handle = None
