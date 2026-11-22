"""Единая точка автопоиска: Steam, Epic (Fortnite), Minecraft, программы из «Пуска» и Microsoft Store."""
from __future__ import annotations

import logging
import ntpath
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import apps, epic, gog, known_apps, steam
from .found import Found

log = logging.getLogger(__name__)

SCAN_SOURCES = ("steam", "epic", "gog", "minecraft", "shortcut", "uwp")


@dataclass
class ScanResult:
    games: list[Found] = field(default_factory=list)
    apps: list[Found] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)          # что не найдено / не сработало — для честного отчёта
    scanned_sources: set[str] = field(default_factory=set)   # источники, которые удалось прочитать целиком


def minecraft_data_dir() -> Path | None:
    appdata = os.environ.get("APPDATA")
    path = Path(appdata) / ".minecraft" if appdata else None
    return path if path and path.is_dir() else None


def promote_minecraft(items: list[Found], data_dir: Path | None) -> tuple[list[Found], list[Found]]:
    """Лаунчеры Minecraft из «Пуска» и Store превращаем в игры (источник minecraft); остальное оставляем."""
    games: list[Found] = []
    rest: list[Found] = []
    for item in items:
        if item.title.lower().startswith("minecraft"):
            games.append(Found(
                kind="game", title=item.title, source="minecraft", external_id=f"{item.source}:{item.external_id}",
                exe_path=item.exe_path, launch_uri=item.launch_uri, args=item.args,
                # рабочие файлы и встроенная Java лежат в .minecraft — по ним определяется, что игра запущена
                install_dir=str(data_dir) if data_dir and item.source == "shortcut" else item.install_dir,
                category="Песочница"))
        else:
            rest.append(item)
    return games, rest


def _exe_hints() -> dict[str, str]:
    """Пути известных программ, которые лежат не в обычном месте (Steam — по данным реестра)."""
    hints: dict[str, str] = {}
    try:
        root = steam.steam_root()
        if root is not None and (root / "steam.exe").is_file():
            hints["Steam"] = str(root / "steam.exe")
    except Exception:
        log.debug("Путь к Steam не получен", exc_info=True)
    return hints


def _inside_any(path: str, folders: list[str]) -> bool:
    low = ntpath.normcase(ntpath.normpath(path))
    return any(low.startswith(ntpath.normcase(ntpath.normpath(f)).rstrip("\\") + "\\") for f in folders if f)


def scan_all(progress: Callable[[str], None] | None = None) -> ScanResult:
    """Ищет всё, что установлено. Работает в фоне; ошибки одного источника не мешают остальным."""
    result = ScanResult()
    say = progress or (lambda _msg: None)
    if sys.platform != "win32":
        result.notes.append("Автопоиск работает только в Windows.")
        return result

    say("Steam…")
    try:
        games, present = steam.scan_steam()
        result.games += games
        if present:
            result.scanned_sources.add("steam")
        else:
            result.notes.append("Steam не найден.")
    except Exception:
        log.exception("Ошибка поиска игр Steam")
        result.notes.append("Не удалось прочитать библиотеку Steam (подробности в nova.log).")

    say("Epic Games…")
    try:
        games, present = epic.scan_epic()
        result.games += games
        if present:
            result.scanned_sources.add("epic")
        else:
            result.notes.append("Epic Games Launcher не найден.")
    except Exception:
        log.exception("Ошибка поиска игр Epic")
        result.notes.append("Не удалось прочитать библиотеку Epic Games (подробности в nova.log).")

    say("GOG…")
    try:
        games, present = gog.scan_gog()
        result.games += games
        if present:
            result.scanned_sources.add("gog")
    except Exception:
        log.exception("Ошибка поиска игр GOG")
        result.notes.append("Не удалось прочитать список игр GOG (подробности в nova.log).")

    say("Программы…")
    found_apps: list[Found] = []
    try:
        found_apps += apps.scan_start_menu()
        result.scanned_sources.add("shortcut")
    except Exception:
        log.exception("Ошибка поиска ярлыков")
        result.notes.append("Не удалось прочитать меню «Пуск».")
    try:        # Discord, Steam, Chrome и т.п. — по их обычным папкам, даже если ярлык в «Пуске» нестандартный
        found_apps = apps.dedupe_by_exe(found_apps + known_apps.scan_known_apps(os.environ, os.path.isfile, _exe_hints()))
    except Exception:
        log.exception("Ошибка поиска известных программ")
    say("Microsoft Store…")
    try:
        uwp = apps.scan_uwp()
        found_apps += uwp
        if uwp:
            result.scanned_sources.add("uwp")
        else:
            result.notes.append("Приложения Microsoft Store не получены (PowerShell недоступен или список пуст).")
    except Exception:
        log.exception("Ошибка поиска приложений Store")
        result.notes.append("Не удалось получить приложения Microsoft Store.")

    mc_games, found_apps = promote_minecraft(found_apps, minecraft_data_dir())
    if mc_games:
        result.scanned_sources.add("minecraft")
    result.games += mc_games

    # Игры, найденные по ярлыкам, но лежащие внутри уже найденных библиотек Steam/Epic, — дубликаты
    known_dirs = [g.install_dir for g in result.games if g.source in ("steam", "epic", "gog")]
    for item in found_apps:
        if item.kind == "game":
            if not _inside_any(item.exe_path, known_dirs):
                result.games.append(item)
        elif not _inside_any(item.exe_path, known_dirs):
            result.apps.append(item)
    return result
