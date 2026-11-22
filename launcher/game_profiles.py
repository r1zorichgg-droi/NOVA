"""Хранение и сопоставление игровых профилей по имени исполняемого файла."""
from __future__ import annotations

import ntpath
from typing import Any


def process_key(value: str) -> str:
    """Нормализует имя EXE или полный путь для сравнения без учёта регистра."""
    return ntpath.basename(str(value or "").strip()).casefold()


def find_game_profile(profiles: Any, process_name: str) -> dict[str, Any] | None:
    """Возвращает профиль, привязанный к конкретному EXE; повреждённые записи игнорируются."""
    key = process_key(process_name)
    if not key or not isinstance(profiles, list):
        return None
    for item in profiles:
        if not isinstance(item, dict):
            continue
        if process_key(str(item.get("process", ""))) == key:
            return dict(item)
    return None


def clean_profiles(profiles: Any) -> list[dict[str, Any]]:
    """Проверяет структуру и удаляет дубликаты по имени процесса."""
    if not isinstance(profiles, list):
        return []
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in profiles:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()[:80]
        process = ntpath.basename(str(item.get("process", "")).strip())[:160]
        key = process_key(process)
        if not name or not key or key in seen:
            continue
        priority = str(item.get("priority", "above_normal"))
        if priority not in {"normal", "above_normal", "high"}:
            priority = "above_normal"
        cleaned.append({
            "name": name,
            "process": process,
            "boost": bool(item.get("boost", True)),
            "fps_overlay": bool(item.get("fps_overlay", True)),
            "priority": priority,
        })
        seen.add(key)
    return cleaned
