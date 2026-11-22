"""Игры GOG (GOG Galaxy и офлайн-установщики) по записям реестра GOG.com — только чтение."""
from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from typing import Any

from .found import Found

REGISTRY_ROOT = r"SOFTWARE\WOW6432Node\GOG.com\Games"


def parse_entries(entries: Iterable[Mapping[str, Any]]) -> list[Found]:
    out: list[Found] = []
    seen: set[str] = set()
    for e in entries:
        game_id, name = str(e.get("gameID") or ""), str(e.get("gameName") or "")
        exe, folder = str(e.get("exe") or ""), str(e.get("path") or "")
        if not game_id or not name or not exe or game_id in seen or not os.path.isfile(exe):
            continue
        seen.add(game_id)
        out.append(Found(kind="game", title=name, source="gog", external_id=game_id, exe_path=exe,
                         install_dir=folder or os.path.dirname(exe), args=str(e.get("launchParam") or "")))
    return out


def read_registry() -> list[dict[str, Any]] | None:
    """Записи GOG из реестра. None — GOG на этом ПК нет (не ошибка)."""
    if sys.platform != "win32":
        return None
    import winreg
    rows: list[dict[str, Any]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REGISTRY_ROOT) as games:
            for i in range(winreg.QueryInfoKey(games)[0]):
                try:
                    with winreg.OpenKey(games, winreg.EnumKey(games, i)) as handle:
                        row: dict[str, Any] = {}
                        for name in ("gameID", "gameName", "exe", "path", "launchParam"):
                            try:
                                row[name] = winreg.QueryValueEx(handle, name)[0]
                            except OSError:
                                row[name] = ""
                        rows.append(row)
                except OSError:
                    continue
    except OSError:
        return None
    return rows


def scan_gog(entries: Iterable[Mapping[str, Any]] | None = None) -> tuple[list[Found], bool]:
    """Возвращает (найденные игры, есть ли GOG на этом ПК)."""
    rows = read_registry() if entries is None else list(entries)
    if rows is None:
        return [], False
    return parse_entries(rows), True
