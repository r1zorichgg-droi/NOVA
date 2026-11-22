"""Результат автопоиска: найденная игра или программа (ещё не запись в базе)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Found:
    kind: str                   # "game" | "app"
    title: str
    source: str                 # steam | epic | minecraft | shortcut | uwp
    external_id: str            # стабильный ключ внутри источника (appid, AppName, AUMID, путь к EXE)
    exe_path: str = ""
    launch_uri: str = ""
    install_dir: str = ""
    category: str = ""
    image_path: str = ""        # локальная обложка, если нашлась (без скачивания из интернета)
    args: str = ""
