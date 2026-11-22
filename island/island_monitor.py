"""Подготовка метрик для островка: компактные «чипы» и плитки раскрытого вида.

Модуль чистый (без Qt): принимает Snapshot, возвращает готовые строки.
Если датчика нет — всегда N/A, никаких выдуманных значений.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from system.collector import Snapshot
from system.fmt import NA, fmt_bytes, fmt_percent, fmt_speed_long, fmt_speed_short, fmt_temp

PLACEHOLDER = "…"  # первый замер ещё не готов


@dataclass(frozen=True)
class Chip:
    key: str
    label: str
    value: str
    widest: str     # самая широкая возможная строка значения — по ней считается фиксированная ширина


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    value: str
    extra: str = ""
    fraction: float | None = None   # 0..1 для полоски под значением
    warm: bool = False              # раскрашивать полоску по «температурной» шкале


def build_chips(snap: Snapshot | None, metrics: list[str], now: datetime | None = None) -> list[Chip]:
    chips: list[Chip] = []
    for key in metrics:
        if key == "clock":
            t = (now or datetime.now()).strftime("%H:%M")
            chips.append(Chip(key, "", t, "00:00"))
        elif key == "cpu":
            chips.append(Chip(key, "CPU", fmt_percent(snap.cpu.percent) if snap else PLACEHOLDER, "100%"))
        elif key == "ram":
            chips.append(Chip(key, "RAM", fmt_percent(snap.ram.percent) if snap else PLACEHOLDER, "100%"))
        elif key == "gpu":
            value = PLACEHOLDER if snap is None else fmt_percent(snap.gpu.percent if snap and snap.gpu else None)
            chips.append(Chip(key, "GPU", value, "100%"))
        elif key == "cpu_temp":
            chips.append(Chip(key, "CPU°", fmt_temp(snap.cpu_temp) if snap else PLACEHOLDER, "100°"))
        elif key == "gpu_temp":
            chips.append(Chip(key, "GPU°", fmt_temp(snap.gpu_temp) if snap else PLACEHOLDER, "100°"))
        elif key == "net":
            value = PLACEHOLDER if snap is None else f"↓{fmt_speed_short(snap.net.down_bps)} ↑{fmt_speed_short(snap.net.up_bps)}"
            chips.append(Chip(key, "", value, "↓88.8M ↑88.8M"))
    return chips


def build_tiles(snap: Snapshot | None) -> list[Tile]:
    """Шесть плиток раскрытого вида: CPU, GPU, RAM, CPU°, GPU°, сеть."""
    if snap is None:
        return [Tile(k, label, PLACEHOLDER) for k, label in (
            ("cpu", "CPU"), ("gpu", "GPU"), ("ram", "RAM"),
            ("cpu_temp", "CPU °C"), ("gpu_temp", "GPU °C"), ("net", "Сеть"))]

    gpu = snap.gpu
    gpu_extra = NA
    if gpu and gpu.vram_used is not None and gpu.vram_total:
        gpu_extra = f"{fmt_bytes(gpu.vram_used)} / {fmt_bytes(gpu.vram_total)}"
    elif gpu and gpu.name:
        gpu_extra = gpu.name

    def temp_tile(key: str, label: str, value: float | None) -> Tile:
        return Tile(key, label, f"{value:.0f}°C" if value is not None else NA,
                    "" if value is not None else "нет датчика",
                    min(value / 100.0, 1.0) if value is not None else None, warm=True)

    return [
        Tile("cpu", "CPU", fmt_percent(snap.cpu.percent), f"{snap.cpu.threads} потоков",
             snap.cpu.percent / 100.0),
        Tile("gpu", "GPU", fmt_percent(gpu.percent if gpu else None), gpu_extra,
             (gpu.percent / 100.0) if gpu and gpu.percent is not None else None),
        Tile("ram", "RAM", fmt_percent(snap.ram.percent),
             f"{fmt_bytes(snap.ram.used)} / {fmt_bytes(snap.ram.total)}", snap.ram.percent / 100.0),
        temp_tile("cpu_temp", "CPU °C", snap.cpu_temp),
        temp_tile("gpu_temp", "GPU °C", snap.gpu_temp),
        Tile("net", "Сеть", f"↓ {fmt_speed_long(snap.net.down_bps)}", f"↑ {fmt_speed_long(snap.net.up_bps)}"),
    ]
