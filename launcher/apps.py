"""Поиск установленных программ: ярлыки меню «Пуск» и приложения Microsoft Store (UWP).

Читаются только локальные ярлыки и список приложений Windows (Get-StartApps) — ничего не отправляется
в интернет. Ярлыки удаления, справки и сайтов, а также системные компоненты Windows пропускаются.
"""
from __future__ import annotations

import json
import logging
import ntpath
import os
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from .categories import guess_app_category
from .found import Found
from .known_apps import match_known

log = logging.getLogger(__name__)

Resolver = Callable[[str], "tuple[str, str, str] | None"]

_JUNK_WORDS = ("uninstall", "uninst", "удалить", "удаление", "readme", "справка", "help", "документац", "documentation",
               "manual", "руководство", "website", "веб-сайт", "сайт", "license", "лицензи", "release notes",
               "changelog", "report a", "crash", "updater", "обновлени", "redistributable", "visual c++", "directx",
               "diagnostic", "диагностик", "troubleshoot", "feedback", "обратная связь", "tutorial", "учебник",
               "support", "поддержка", "register", "регистрация", "configuration tool", "sdk", "command prompt",
               "командная строка", "windows powershell", "character map", "таблица символов", "ресурсы", "resources",
               "samples", "примеры", "migration", "миграци", "repair", "восстановление", "service pack")
_JUNK_EXE = ("unins", "uninst", "update", "crashpad", "setup", "installer")
# Папки Пуска со служебными ярлыками (русская и английская локализации)
_SKIP_FOLDERS = {"administrative tools", "windows tools", "windows system", "windows accessories", "windows powershell",
                 "служебные — windows", "средства администрирования windows", "стандартные — windows",
                 "accessibility", "startup", "автозагрузка", "microsoft office tools", "office tools",
                 "средства microsoft office", "windows kits", "microsoft .net", "visual studio tools", "sdk",
                 "windows ease of access", "специальные возможности"}
# Пути, по которым понятно, что это игра, установленная не через Steam/Epic.
_GAME_PATH_HINTS = ("\\games\\", "\\xboxgames\\", "\\riot games\\", "\\ea games\\", "\\gog galaxy\\games\\",
                    "\\ubisoft game launcher\\games\\", "\\rockstar games\\", "\\steamapps\\common\\",
                    "\\epic games\\", "\\origin games\\", "\\battle.net\\games\\")
# Приложения Microsoft Store: оставляем только полезные. Всё от Microsoft/Windows, кроме этого списка, — служебное
# или предустановленный «хлам» (Новости, Погода, Советы, Cortana...) и в библиотеку не попадает.
_UWP_KEEP = ("microsoft.windowsterminal", "microsoft.windowscalculator", "microsoft.windowsnotepad", "microsoft.paint",
             "microsoft.screensketch", "microsoft.windowscamera", "microsoft.windowsstore", "microsoft.gamingapp",
             "microsoft.windowsalarms", "microsoft.microsoftstickynotes", "microsoft.todos", "microsoft.zunevideo",
             "microsoft.zunemusic", "microsoft.windows.photos", "microsoft.powertoys", "microsoft.office.onenote",
             "microsoft.microsoftofficehub", "microsoft.outlookforwindows")
_UWP_SYSTEM_PREFIXES = ("microsoft.", "microsoftwindows.", "windows.", "microsoftcorporationii.", "microsoftteams",
                        "msteams", "ncsiuwpapp", "clipchamp.", "microsoftedge", "e2a4f912", "1527c705", "c5e2524a")
# Предустановленная реклама сторонних компаний.
_UWP_BLOAT = ("candycrush", "king.com", "tiktok", "disney", "amazon.com.amazon", "primevideo", "hulu", "facebook",
              "instagram", "linkedin", "booking", "dolby", "adobeexpress", "twitter", "pinterest", "farmville",
              "bubblewitch", "ebay", "skypeapp")


# --------------------------------------------------------------------------------------
# Чистая логика (тестируется без Windows)
# --------------------------------------------------------------------------------------
def is_junk_shortcut(name: str, target: str, args: str = "") -> bool:
    if match_known(name) is not None:
        return False                      # Discord, Steam, Chrome... — всегда нужные программы
    low = name.lower()
    if any(word in low for word in _JUNK_WORDS):
        return True
    stem = ntpath.basename(target).lower()
    if not stem.endswith(".exe"):
        return True
    if "--processstart" in args.lower():  # Squirrel-программы (Slack, GitHub Desktop...): ярлык ведёт на Update.exe
        return False
    return any(stem.startswith(prefix) for prefix in _JUNK_EXE)


def is_unwanted_item(source: str, title: str, exe_path: str, args: str, external_id: str, system_dir: str = "") -> bool:
    """Карточка, найденная автопоиском раньше, но по нынешним правилам — лишняя (служебный ярлык, реклама Store)."""
    if source == "uwp":
        return is_system_uwp(external_id)
    if source == "shortcut":
        if exe_path and system_dir:
            norm = ntpath.normcase(ntpath.normpath(exe_path))
            if norm.startswith(ntpath.normcase(system_dir).rstrip("\\") + "\\"):
                return True                 # программа из папки Windows (cmd, mmc, charmap...) — не для библиотеки
        return is_junk_shortcut(title, exe_path, args)
    return False                            # добавленное вручную не трогаем никогда


def is_game_path(path: str) -> bool:
    low = ntpath.normcase(path).replace("/", "\\")
    return any(hint in low for hint in _GAME_PATH_HINTS)


def parse_start_apps(text: str) -> list[tuple[str, str]]:
    """Вывод `Get-StartApps | ConvertTo-Json` -> [(название, AUMID)] только для приложений Store (UWP)."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):
        data = [data]
    out: list[tuple[str, str]] = []
    for row in data if isinstance(data, list) else []:
        if not isinstance(row, dict):
            continue
        name, app_id = str(row.get("Name") or "").strip(), str(row.get("AppID") or "").strip()
        if name and "!" in app_id and "\\" not in app_id and "/" not in app_id:
            out.append((name, app_id))
    return out


def is_system_uwp(app_id: str) -> bool:
    """True — приложение Store не нужно в библиотеке (служебное, от Microsoft вне списка полезных, или реклама)."""
    low = app_id.lower()
    if low.startswith(_UWP_KEEP):
        return False
    if low.startswith(_UWP_SYSTEM_PREFIXES):
        return True
    return any(word in low for word in _UWP_BLOAT)


def shortcut_to_found(name: str, target: str, args: str, workdir: str, system_dir: str = "") -> Found | None:
    """Ярлык -> Found (программа или игра) либо None, если его нужно пропустить."""
    if is_junk_shortcut(name, target, args):
        return None
    norm = ntpath.normcase(ntpath.normpath(target))
    if system_dir and norm.startswith(ntpath.normcase(system_dir).rstrip("\\") + "\\"):
        return None
    known = match_known(name)
    kind = "game" if is_game_path(target) and known is None else "app"
    install = workdir or ntpath.dirname(target)
    title = known.title if known else name
    category = "" if kind == "game" else (known.category if known else guess_app_category(name, target))
    return Found(kind=kind, title=title, source="shortcut", external_id=norm, exe_path=target, install_dir=install,
                 category=category, args=args)


def uwp_to_found(name: str, app_id: str) -> Found | None:
    if is_system_uwp(app_id):
        return None
    return Found(kind="app", title=name, source="uwp", external_id=app_id,
                 launch_uri=f"shell:AppsFolder\\{app_id}", category=guess_app_category(name))


def dedupe_by_exe(items: Iterable[Found]) -> list[Found]:
    seen: set[str] = set()
    out: list[Found] = []
    for item in items:
        key = ntpath.normcase(item.exe_path) or item.external_id
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


# --------------------------------------------------------------------------------------
# Windows-специфичная часть
# --------------------------------------------------------------------------------------
def start_menu_folders() -> list[Path]:
    folders = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            folders.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [f for f in folders if f.is_dir()]


def resolve_shortcut(path: str) -> tuple[str, str, str] | None:
    """(цель, аргументы, рабочая папка) ярлыка .lnk через штатный WScript.Shell."""
    try:
        from comtypes.client import CreateObject
        link = CreateObject("WScript.Shell").CreateShortCut(path)
        return str(link.TargetPath or ""), str(link.Arguments or ""), str(link.WorkingDirectory or "")
    except Exception:
        log.debug("Ярлык %s не разобран", path, exc_info=True)
        return None


def scan_start_menu(resolver: Resolver = resolve_shortcut, folders: list[Path] | None = None) -> list[Found]:
    folders = folders if folders is not None else start_menu_folders()
    system_dir = os.environ.get("SystemRoot", "")
    uses_com = resolver is resolve_shortcut
    if uses_com:
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            return []
    found: list[Found] = []
    try:
        for root in folders:
            for lnk in root.rglob("*.lnk"):
                if any(part.lower() in _SKIP_FOLDERS for part in lnk.relative_to(root).parts[:-1]):
                    continue
                resolved = resolver(str(lnk))
                if not resolved or not resolved[0]:
                    continue
                target, args, workdir = resolved
                if not Path(target).is_file():
                    continue
                item = shortcut_to_found(lnk.stem, target, args, workdir, system_dir)
                if item:
                    found.append(item)
    finally:
        if uses_com:
            try:
                import comtypes
                comtypes.CoUninitialize()
            except Exception:
                pass
    return dedupe_by_exe(found)


def run_powershell(script: str, timeout: float = 40.0) -> str:
    if sys.platform != "win32":
        return ""
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", prefix + script],
            capture_output=True, timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError):
        log.info("PowerShell недоступен — приложения Microsoft Store не найдены")
        return ""
    return proc.stdout.decode("utf-8", errors="replace") if proc.returncode == 0 else ""


def scan_uwp(runner: Callable[[str], str] = run_powershell) -> list[Found]:
    items = parse_start_apps(runner("Get-StartApps | ConvertTo-Json -Compress"))
    found = [f for f in (uwp_to_found(name, app_id) for name, app_id in items) if f]
    return found
