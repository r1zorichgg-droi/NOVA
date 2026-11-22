"""Запуск игр и программ из карточки.

Как запускается:
- Steam / Epic / Microsoft Store — через штатные ссылки (steam://, com.epicgames.launcher://, shell:AppsFolder),
  поэтому работают оверлей, облачные сохранения и вход в аккаунт. Процесс игры при этом создаёт сам лаунчер,
  и NOVA его приоритет не меняет — NOVA не вмешивается в процессы игр (так безопаснее для античитов).
- Обычный EXE — напрямую, с рабочей папкой и аргументами из карточки. Только здесь приоритет задаётся флагом
  создания процесса при старте (никаких обращений к уже запущенному процессу).
- Если EXE требует прав администратора, Windows спрашивает подтверждение (UAC) — NOVA сама ничего не повышает.
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from database.models import LibraryItem

log = logging.getLogger(__name__)

ERROR_ELEVATION_REQUIRED = 740
PRIORITY_FLAGS = {"normal": 0, "above_normal": 0x00008000, "high": 0x00000080}
PRIORITY_LABELS = {"normal": "Обычный", "above_normal": "Выше обычного", "high": "Высокий"}


@dataclass
class LaunchResult:
    ok: bool
    message: str = ""
    pid: int = 0                # 0 — процесс создал лаунчер или Windows, PID неизвестен
    via: str = ""               # exe | uri | store | exe-elevated
    priority: str = "normal"    # какой приоритет реально применён


def resolve_priority(global_value: str, item_value: str) -> str:
    """Приоритет карточки перекрывает общий; неизвестные значения дают «обычный»."""
    value = item_value or global_value
    return value if value in PRIORITY_FLAGS else "normal"


def _startfile(target: str, **kwargs: Any) -> None:
    os.startfile(target, **kwargs)     # type: ignore[attr-defined]  # только Windows


class Launcher:
    """Функции запуска подставляются в тестах; на практике — subprocess.Popen и os.startfile."""

    def __init__(self, popen: Callable[..., Any] = subprocess.Popen,
                 startfile: Callable[..., None] = _startfile,
                 isfile: Callable[[str], bool] = os.path.isfile,
                 isdir: Callable[[str], bool] = os.path.isdir) -> None:
        self._popen, self._startfile, self._isfile, self._isdir = popen, startfile, isfile, isdir

    def launch(self, item: LibraryItem, priority: str = "normal") -> LaunchResult:
        if item.opts.get("uninstalled"):
            return LaunchResult(False, "Эта запись больше не находится на ПК — возможно, программу удалили или перенесли. "
                                       "Удалите карточку или добавьте программу заново.")
        uri, exe = item.launch_uri.strip(), item.exe_path.strip()
        if uri:
            result = self._launch_uri(uri)
            if result.ok or not exe or not self._isfile(exe):
                return result
            log.info("Ссылка %s не сработала, запускаю EXE напрямую", uri)
        if exe:
            return self._launch_exe(item, exe, priority)
        return LaunchResult(False, "У карточки нет ни файла запуска, ни ссылки. Откройте «Изменить» и укажите EXE.")

    # -- реализации -----------------------------------------------------------
    def _launch_uri(self, uri: str) -> LaunchResult:
        via = "store" if uri.lower().startswith("shell:") else "uri"
        try:
            self._startfile(uri)
        except OSError as exc:
            log.warning("Не удалось открыть %s: %s", uri, exc)
            return LaunchResult(False, f"Windows не смогла открыть ссылку запуска ({exc.strerror or exc}). "
                                       "Проверьте, что нужный лаунчер установлен.")
        return LaunchResult(True, "", 0, via)

    def _launch_exe(self, item: LibraryItem, exe: str, priority: str) -> LaunchResult:
        if not self._isfile(exe):
            return LaunchResult(False, f"Файл не найден: {exe}\nОткройте «Изменить» и укажите новый путь.")
        cwd = item.install_dir if item.install_dir and self._isdir(item.install_dir) else os.path.dirname(exe)
        command: str | list[str] = f'"{exe}" {item.args}'.strip() if item.args.strip() else [exe]
        flags = PRIORITY_FLAGS.get(priority, 0)
        try:
            proc = self._popen(command, cwd=cwd or None, creationflags=flags, close_fds=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) == ERROR_ELEVATION_REQUIRED:
                return self._launch_elevated(item, exe, cwd)
            log.warning("Не удалось запустить %s: %s", exe, exc)
            return LaunchResult(False, f"Не удалось запустить: {exc.strerror or exc}")
        return LaunchResult(True, "", int(getattr(proc, "pid", 0) or 0), "exe", priority)

    def _launch_elevated(self, item: LibraryItem, exe: str, cwd: str) -> LaunchResult:
        """EXE просит права администратора: отдаём запуск оболочке Windows, она покажет окно UAC."""
        try:
            self._startfile(exe, arguments=item.args, cwd=cwd or None)
        except OSError as exc:
            return LaunchResult(False, f"Программа требует прав администратора, запуск отменён или не удался: "
                                       f"{exc.strerror or exc}")
        return LaunchResult(True, "Программа запущена с запросом прав администратора — приоритет не менялся.",
                            0, "exe-elevated")
