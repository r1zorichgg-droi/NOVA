"""Модели данных библиотеки (игры и приложения хранятся одинаково)."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields
from typing import Any, Self


@dataclass
class LibraryItem:
    id: int = 0
    title: str = ""
    description: str = ""
    exe_path: str = ""          # путь к EXE (ручное добавление / найденные программы)
    launch_uri: str = ""        # steam://rungameid/..., com.epicgames.launcher://... и т.п.
    source: str = "manual"      # manual | steam | epic | minecraft | system | uwp
    external_id: str | None = None
    image_path: str = ""        # обложка игры / иконка приложения
    category: str = ""
    favorite: bool = False
    play_count: int = 0
    last_played: float | None = None
    created_at: float = 0.0
    install_dir: str = ""       # папка установки (для отслеживания процессов игры)
    args: str = ""              # аргументы командной строки при запуске
    options: str = "{}"         # JSON с настройками оптимизации и пометками (см. opts)
    hidden: bool = False        # убрано пользователем из библиотеки (автоопределение не вернёт)

    @classmethod
    def from_row(cls, row: Any) -> Self:   # sqlite3.Row или dict
        names = {f.name for f in fields(cls)}
        data = {k: row[k] for k in row.keys() if k in names}
        data["favorite"] = bool(data.get("favorite", 0))
        data["hidden"] = bool(data.get("hidden", 0))
        return cls(**data)

    @property
    def opts(self) -> dict[str, Any]:
        """Разобранные настройки записи; повреждённый JSON даёт пустой словарь."""
        try:
            value = json.loads(self.options or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}


class Game(LibraryItem):
    pass


class AppEntry(LibraryItem):
    pass


# Колонки, которые можно менять через add()/update() (защита от подстановки имён колонок).
ITEM_COLUMNS = (
    "title", "description", "exe_path", "launch_uri", "source", "external_id",
    "image_path", "category", "favorite", "play_count", "last_played",
    "install_dir", "args", "options", "hidden",
)
