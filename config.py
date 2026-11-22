"""Константы, пути и значения настроек NOVA по умолчанию.

Модуль не зависит от Qt, поэтому его можно импортировать и тестировать где угодно.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Any
from logging.handlers import RotatingFileHandler
from pathlib import Path

APP_NAME = "NOVA"
APP_VERSION = "1.05.7"
UPDATE_MANIFEST_URL = "https://github.com/r1zorichgg-droi/NOVA/releases/latest/download/latest.json"
APP_USER_MODEL_ID = "NOVA.Launcher"

# LibreHardwareMonitor -> Options -> Remote Web Server -> Run (порт по умолчанию 8085)
LHM_URL = "http://127.0.0.1:8085/data.json"

ACCENT_PRESETS = ["#7C5CFF", "#3B82F6", "#06B6D4", "#10B981", "#F59E0B", "#EF4444", "#EC4899"]

# Метрики, которые можно показывать в компактном островке (порядок = порядок отображения).
ISLAND_METRICS = ["cpu", "gpu", "ram", "cpu_temp", "gpu_temp", "net", "clock"]

DEFAULT_SETTINGS: dict[str, object] = {
    # --- общие ---
    "theme": "dark",                    # dark | light
    "accent": ACCENT_PRESETS[0],
    "window_opacity": 1.0,              # 0.80 .. 1.0
    "animations": "smooth",             # smooth | fast | off
    "user_name": "",                    # пусто = имя пользователя Windows
    "close_to_tray": True,
    "window_geometry": "",              # base64 от QWidget.saveGeometry()
    # --- Dynamic Island ---
    "island_enabled": True,
    "island_width": 230,                # компактная ширина (минимум)
    "island_height": 38,
    "island_scale": 1.0,                # масштаб раскрытого состояния
    "island_offset_x": 0,               # смещение от центра экрана, px
    "island_offset_y": 10,              # отступ от верха экрана, px
    "island_opacity": 1.0,              # 0.5 .. 1.0
    "island_metrics": ["cpu", "gpu", "ram", "net"],
    "island_update_ms": 1000,           # 500 / 1000 / 2000 / 5000
    "island_music_mode": True,
    "island_music_pause_timeout_s": 15,  # сколько держать плеер после паузы
    "island_autocollapse_s": 4,         # 0 = не сворачивать автоматически
    "island_hide_in_fullscreen": True,
    "island_fullscreen_ignore": ["explorer.exe"],
    "island_notify_usb": True,
    "island_notify_volume": True,
    "island_notify_apps": True,
    "island_notify_music": True,
    "island_launcher_enabled": True,    # вкладка «Приложения» в раскрытом острове
    "island_launch_items": [],          # закреплённые в острове программы: ["app:12", "game:3", ...]
    # --- библиотека ---
    "auto_scan": True,                  # искать игры и программы при запуске NOVA
    "last_scan": 0.0,                   # unix-время последнего поиска
    # --- режим производительности при запуске из NOVA ---
    "boost_on_launch": True,            # главный выключатель (для игр; для программ — опция в карточке)
    "boost_power": True,                # переключать схему электропитания на максимальную
    "boost_plan": "",                   # GUID выбранной схемы; пусто = автоматически (Ultimate -> Высокая)
    "boost_game_mode": True,            # включить «Игровой режим» Windows (HKCU)
    "boost_game_dvr": True,             # выключить фоновую запись игр Windows (HKCU)
    "boost_priority": "above_normal",   # normal | above_normal | high — при прямом запуске EXE
    "boost_notify": True,               # показывать уведомление в Dynamic Island
    # --- игровые профили и обновления ---
    "game_profiles": [],                # профили по имени exe: boost / fps_overlay / priority
    "update_manifest_url": UPDATE_MANIFEST_URL,
    "update_auto_check": True,
    "network_ping_host": "1.1.1.1",
}

MIN_SAMPLE_INTERVAL_MS = 250


def coerce(value: Any, default: Any) -> Any:
    """Приводит прочитанное из базы значение к типу значения по умолчанию, иначе возвращает default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, float):
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return list(value) if isinstance(value, list) else list(default)
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else dict(default)
    return value


def resource_path(*parts: str) -> Path:
    """Путь к ресурсу и в исходниках, и внутри собранного PyInstaller-EXE."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base.joinpath(*parts)


def data_dir() -> Path:
    """Папка с пользовательскими данными (база, логи). NOVA_DATA_DIR переопределяет путь."""
    override = os.environ.get("NOVA_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def db_path() -> Path:
    return data_dir() / "nova.db"


def setup_logging() -> None:
    """Лог в файл (с ротацией) и в консоль, если она есть."""
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    try:
        fh = RotatingFileHandler(data_dir() / "nova.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass
    if sys.stderr is not None:  # в windowed-EXE stderr может быть None
        sh = logging.StreamHandler(sys.stderr)
        sh.setFormatter(fmt)
        root.addHandler(sh)
