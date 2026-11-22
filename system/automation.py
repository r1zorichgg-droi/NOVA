"""Автоматизация Windows. На этапе 1: автозапуск NOVA вместе с Windows (HKCU\\...\\Run, без прав администратора)."""
from __future__ import annotations

import logging
import os
import sys
import re
from pathlib import Path

from config import APP_NAME

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
IS_WINDOWS = sys.platform == "win32"


def autostart_command(main_script: Path | None = None) -> str:
    """Команда для автозапуска: собранный EXE или pythonw + main.py. Окно сразу сворачивается в трей."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else exe
    script = main_script or Path(sys.argv[0]).resolve()
    return f'"{interpreter}" "{script}" --minimized'


def is_autostart_enabled() -> bool:
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return bool(value)
    except FileNotFoundError:
        return False
    except OSError:
        log.exception("Не удалось прочитать автозапуск")
        return False


def set_autostart(enabled: bool) -> bool:
    """Включает/выключает автозапуск. Возвращает True, если состояние реально изменилось/совпало."""
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, APP_NAME)
                except FileNotFoundError:
                    pass
        return is_autostart_enabled() == enabled
    except OSError:
        log.exception("Не удалось изменить автозапуск")
        return False


# ---------------------------------------------------------------------------
# Пользовательские программы автозапуска NOVA (отдельные имена реестра)
CUSTOM_PREFIX = "NOVA_Auto_"


def list_custom_startup() -> list[tuple[str, str]]:
    """Возвращает только записи автозапуска, созданные пользователем через NOVA."""
    if not IS_WINDOWS:
        return []
    import winreg
    result: list[tuple[str, str]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if name.startswith(CUSTOM_PREFIX):
                    result.append((name[len(CUSTOM_PREFIX):], str(value)))
    except OSError:
        log.exception("Не удалось прочитать пользовательский автозапуск")
    return result


def add_startup_app(executable: str) -> tuple[bool, str]:
    """Добавляет выбранный EXE в автозапуск текущего пользователя."""
    if not IS_WINDOWS:
        return False, "Доступно только в Windows."
    path = Path(executable).expanduser()
    if not path.is_file() or path.suffix.lower() != ".exe":
        return False, "Выберите существующий EXE-файл."
    import winreg
    label = re.sub(r"[^A-Za-z0-9_-]+", "_", path.stem).strip("_")[:48] or "App"
    name = CUSTOM_PREFIX + label
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            # Не перезаписываем другую запись NOVA с тем же именем: добавляем суффикс.
            base, n = name, 2
            while True:
                try:
                    winreg.QueryValueEx(key, name)
                    name = f"{base}_{n}"
                    n += 1
                except FileNotFoundError:
                    break
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, f'"{path}"')
        return True, f"Добавлено в автозапуск: {path.name}"
    except OSError as exc:
        log.exception("Не удалось добавить программу в автозапуск")
        return False, f"Не удалось добавить программу: {exc}"


def remove_startup_app(registry_name: str) -> bool:
    """Удаляет только запись, имя которой начинается с нашего префикса."""
    if not IS_WINDOWS or not registry_name.startswith(CUSTOM_PREFIX):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, registry_name)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        log.exception("Не удалось удалить программу из автозапуска")
        return False


def list_startup_entries() -> list[tuple[str, str, str]]:
    """Список пользовательских записей Run и ярлыков папки Startup. Не читает общесистемный автозапуск."""
    if not IS_WINDOWS:
        return []
    import winreg
    entries: list[tuple[str, str, str]] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                entries.append(("registry", str(name), str(value)))
    except OSError:
        log.exception("Не удалось прочитать автозапуск реестра")
    startup_dir = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft/Windows/Start Menu/Programs/Startup"
    try:
        for path in sorted(startup_dir.iterdir(), key=lambda item: item.name.lower()):
            if path.is_file() and path.suffix.lower() in {".lnk", ".url", ".exe", ".bat", ".cmd"}:
                entries.append(("folder", str(path), path.name))
    except OSError:
        pass
    return entries


def remove_startup_entry(kind: str, token: str) -> bool:
    """Удаляет выбранную пользовательскую запись Run или файл/ярлык из Startup."""
    if not IS_WINDOWS:
        return False
    if kind == "registry":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, token)
            return True
        except (FileNotFoundError, OSError):
            log.exception("Не удалось удалить запись автозапуска %s", token)
            return False
    if kind == "folder":
        try:
            path = Path(token).resolve()
            startup_dir = (Path(os.environ.get("APPDATA", str(Path.home()))) /
                           "Microsoft/Windows/Start Menu/Programs/Startup").resolve()
            if path.parent != startup_dir or path.suffix.lower() not in {".lnk", ".url", ".exe", ".bat", ".cmd"}:
                return False
            path.unlink()
            return True
        except OSError:
            log.exception("Не удалось удалить ярлык из Startup: %s", token)
            return False
    return False
