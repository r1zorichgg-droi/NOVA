"""Поиск установленных игр Steam: читаются только локальные файлы Steam, в интернет NOVA не ходит."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .exefind import guess_main_exe
from .found import Found
from .vdf import parse_vdf, vget

log = logging.getLogger(__name__)

# Служебные «приложения» Steam, которые лежат рядом с играми, но играми не являются.
_NOT_GAMES_IDS = {"228980", "250820", "1493710", "1628350", "1826330", "2180100"}
_NOT_GAMES_PREFIXES = ("Steamworks Common Redistributables", "Proton", "Steam Linux Runtime", "SteamVR",
                       "Steam Controller", "Steam Client")
STATE_FULLY_INSTALLED = 4


def steam_root() -> Path | None:
    """Папка Steam по данным реестра пользователя; запасной вариант — стандартный путь."""
    candidates: list[str] = []
    if sys.platform == "win32":
        import winreg
        for hive, sub, name in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")):
            try:
                with winreg.OpenKey(hive, sub) as key:
                    candidates.append(str(winreg.QueryValueEx(key, name)[0]))
            except OSError:
                continue
        for env in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(env):
                candidates.append(str(Path(os.environ[env]) / "Steam"))
    for raw in candidates:
        path = Path(raw)
        if (path / "steamapps").is_dir():
            return path
    return None


def parse_library_folders(text: str) -> list[str]:
    """Пути библиотек из libraryfolders.vdf (поддерживает и новый формат, и старый)."""
    try:
        data = parse_vdf(text)
    except ValueError:
        return []
    root = vget(data, "libraryfolders", {})
    paths: list[str] = []
    if not isinstance(root, dict):
        return paths
    for key, value in root.items():
        if not key.isdigit():
            continue
        if isinstance(value, dict):
            path = vget(value, "path", "")
        else:
            path = value                                      # старый формат: "1" "D:\\SteamLibrary"
        if isinstance(path, str) and path:
            paths.append(path)
    return paths


def parse_manifest(text: str) -> dict[str, str] | None:
    """Поля appmanifest_*.acf, нужные NOVA, или None, если это не установленная игра."""
    try:
        state = vget(parse_vdf(text), "AppState", None)
    except ValueError:
        return None
    if not isinstance(state, dict):
        return None
    appid, name, installdir = (str(vget(state, k, "")) for k in ("appid", "name", "installdir"))
    if not (appid.isdigit() and name and installdir):
        return None
    try:
        flags = int(vget(state, "StateFlags", 0))
    except (TypeError, ValueError):
        flags = 0
    if not flags & STATE_FULLY_INSTALLED:
        return None                                           # скачивается или удалена
    if appid in _NOT_GAMES_IDS or name.startswith(_NOT_GAMES_PREFIXES):
        return None
    return {"appid": appid, "name": name, "installdir": installdir}


def find_cover(root: Path, appid: str) -> str:
    """Обложка из локального кэша Steam (его создаёт сам клиент). Пустая строка, если её нет."""
    cache = root / "appcache" / "librarycache"
    patterns = [f"{appid}_library_600x900.jpg", f"{appid}/library_600x900.jpg", f"{appid}/*/library_600x900.jpg",
                f"{appid}_header.jpg", f"{appid}/header.jpg", f"{appid}/*/header.jpg"]
    for pattern in patterns:
        try:
            for match in cache.glob(pattern):
                if match.is_file():
                    return str(match)
        except OSError:
            continue
    return ""


def scan_steam(root: Path | None = None) -> tuple[list[Found], bool]:
    """Возвращает (найденные игры, найден ли Steam вообще)."""
    root = root or steam_root()
    if root is None:
        return [], False
    libraries = [root]
    try:
        text = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
        libraries += [Path(p) for p in parse_library_folders(text)]
    except OSError:
        log.info("libraryfolders.vdf не прочитан — использую основную библиотеку Steam")
    games: list[Found] = []
    seen: set[str] = set()
    for lib in dict.fromkeys(libraries):
        apps_dir = lib / "steamapps"
        try:
            manifests = sorted(apps_dir.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for manifest in manifests:
            try:
                info = parse_manifest(manifest.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if not info or info["appid"] in seen:
                continue
            seen.add(info["appid"])
            folder = apps_dir / "common" / info["installdir"]
            if not folder.is_dir():
                continue
            games.append(Found(
                kind="game", title=info["name"], source="steam", external_id=info["appid"],
                exe_path=guess_main_exe(folder, info["name"]),
                launch_uri=f"steam://rungameid/{info['appid']}",
                install_dir=str(folder), image_path=find_cover(root, info["appid"])))
    return games, True
