"""Данные Dynamic Island без зависимости от Qt: состояние плеера, уведомления, анимация полосок."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MusicState:
    app_id: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    playing: bool = False
    position: float = 0.0          # секунды на момент опроса
    duration: float = 0.0          # 0 = приложение не сообщает длительность
    thumbnail: bytes | None = None
    can_prev: bool = False
    can_next: bool = False

    @property
    def active(self) -> bool:
        return bool(self.title)

    @property
    def track_key(self) -> tuple[str, str, str, str]:
        return (self.app_id, self.title, self.artist, self.album)


@dataclass(frozen=True)
class Notification:
    kind: str                      # usb | volume | app | music | system
    title: str
    text: str = ""
    progress: float | None = None  # 0..1, рисуется полоской (громкость)
    duration_ms: int = 2600


def bar_heights(t: float, level: float, n: int = 7) -> list[float]:
    """Высоты звуковых полосок 0..1.

    Это индикатор «музыка играет», а не спектр реального звука: Windows Media Session
    не отдаёт уровни сигнала. level (0..1) плавно гасит полоски при паузе.
    """
    level = min(max(level, 0.0), 1.0)
    heights = []
    for i in range(n):
        a = 0.5 + 0.5 * math.sin(t * (2.2 + 0.7 * i) + i * 1.3)
        b = 0.5 + 0.5 * math.sin(t * (3.1 + 0.4 * i) + i * 0.7)
        full = 0.18 + 0.82 * (0.6 * a + 0.4 * b)
        heights.append(0.12 + (full - 0.12) * level)
    return heights


_ANIMATION_SCALE = {"smooth": 1.0, "fast": 0.5, "off": 0.0}


def animation_scale(mode: str) -> float:
    return _ANIMATION_SCALE.get(mode, 1.0)


def friendly_process_name(name: str) -> str:
    stem = re.sub(r"\.exe$", "", name, flags=re.I).replace("_", " ").replace("-", " ").strip()
    return stem[:1].upper() + stem[1:] if stem.islower() else stem
