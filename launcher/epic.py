"""Игры Epic Games Store (включая Fortnite) по локальным манифестам лаунчера Epic."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .found import Found

log = logging.getLogger(__name__)


def manifests_dir() -> Path:
    base = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(base) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


def epic_launch_uri(namespace: str, item_id: str, app_name: str) -> str:
    """Ссылка, которую понимает Epic Games Launcher: запускает игру со входом в аккаунт."""
    ident = "%3A".join(quote(part, safe="") for part in (namespace, item_id, app_name))
    return f"com.epicgames.launcher://apps/{ident}?action=launch&silent=true"


def parse_item(data: Any) -> Found | None:
    """Found из содержимого файла .item или None (DLC, редакторы, неполные установки)."""
    if not isinstance(data, dict):
        return None
    app_name = str(data.get("AppName") or "")
    title = str(data.get("DisplayName") or "")
    location = str(data.get("InstallLocation") or "")
    if not (app_name and title and location):
        return None
    if data.get("bIsIncompleteInstall"):
        return None
    main = str(data.get("MainGameAppName") or "")
    if main and main != app_name:
        return None                                           # дополнение к другой игре
    categories = [str(c).lower() for c in (data.get("AppCategories") or [])]
    if categories and "games" not in categories:
        return None                                           # движки, плагины, приложения
    exe = str(data.get("LaunchExecutable") or "")
    exe_path = str(Path(location) / exe) if exe else ""
    namespace, item_id = str(data.get("CatalogNamespace") or ""), str(data.get("CatalogItemId") or "")
    uri = epic_launch_uri(namespace, item_id, app_name) if namespace and item_id else ""
    return Found(kind="game", title=title, source="epic", external_id=app_name,
                 exe_path=exe_path, launch_uri=uri, install_dir=location)


def scan_epic(folder: Path | None = None) -> tuple[list[Found], bool]:
    """Возвращает (найденные игры, найден ли Epic Games Launcher)."""
    folder = folder or manifests_dir()
    if not folder.is_dir():
        return [], False
    games: list[Found] = []
    seen: set[str] = set()
    for item in sorted(folder.glob("*.item")):
        try:
            found = parse_item(json.loads(item.read_text(encoding="utf-8", errors="replace")))
        except (OSError, ValueError):
            log.info("Манифест Epic %s не прочитан", item.name)
            continue
        if found is None or found.external_id in seen or not Path(found.install_dir).is_dir():
            continue
        seen.add(found.external_id)
        if found.exe_path and not Path(found.exe_path).is_file():
            found = Found(**{**found.__dict__, "exe_path": ""})
        games.append(found)
    return games, True
