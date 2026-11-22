"""Фоновые программы, которые пользователь может закрыть перед игрой, чтобы освободить память и процессор.

NOVA никогда не закрывает ничего сама: функции только показывают кандидатов и выполняют закрытие тех
программ, которые пользователь отметил в окне подтверждения. Системные процессы, проводник, сама NOVA
и родительские процессы в список не попадают.
Сначала программе вежливо отправляется «закрыть окно» (как нажатие на крестик — она может спросить про
несохранённые данные). Принудительное завершение — отдельный шаг со своим предупреждением.
"""
from __future__ import annotations

import logging
import ntpath
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from . import winapi

log = logging.getLogger(__name__)

PROTECTED_NAMES = frozenset({
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe",
    "svchost.exe", "dwm.exe", "explorer.exe", "fontdrvhost.exe", "sihost.exe", "taskhostw.exe", "ctfmon.exe",
    "searchhost.exe", "searchapp.exe", "startmenuexperiencehost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "runtimebroker.exe", "audiodg.exe", "spoolsv.exe", "wudfhost.exe", "conhost.exe",
    "applicationframehost.exe", "securityhealthsystray.exe", "msmpeng.exe", "nvcontainer.exe", "nvidia overlay.exe",
    "taskmgr.exe", "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe",
})


@dataclass(frozen=True)
class BackgroundApp:
    pid: int
    name: str
    exe: str
    memory_mb: float


def _is_under(path: str, folder: str) -> bool:
    """Лежит ли path внутри folder (по правилам путей Windows: без учёта регистра, \\ и /)."""
    if not path or not folder:
        return False
    p, f = ntpath.normcase(ntpath.normpath(path)), ntpath.normcase(ntpath.normpath(folder))
    return p == f or p.startswith(f.rstrip("\\") + "\\")


def filter_candidates(rows: Iterable[Mapping[str, Any]], visible_pids: set[int], own_pids: set[int],
                      username: str, system_dir: str = "", exclude_dirs: Iterable[str] = ()) -> list[BackgroundApp]:
    """Из «сырых» строк о процессах оставляет только то, что безопасно предложить закрыть."""
    exclude = [d for d in exclude_dirs if d]
    out: list[BackgroundApp] = []
    for row in rows:
        pid, name = int(row.get("pid") or 0), str(row.get("name") or "")
        exe = str(row.get("exe") or "")
        if not pid or not name or pid in own_pids or pid not in visible_pids:
            continue
        if name.lower() in PROTECTED_NAMES or (system_dir and _is_under(exe, system_dir)):
            continue
        owner = str(row.get("username") or "")
        if username and owner and owner.lower() != username.lower():
            continue
        if any(_is_under(exe, d) for d in exclude):
            continue
        mem = float(row.get("rss") or 0) / (1024 * 1024)
        out.append(BackgroundApp(pid, name, exe, round(mem, 1)))
    out.sort(key=lambda a: a.memory_mb, reverse=True)
    return out


def list_background_apps(exclude_dirs: Iterable[str] = ()) -> list[BackgroundApp]:
    """Программы с видимыми окнами текущего пользователя, от самых «тяжёлых» по памяти."""
    if not winapi.IS_WINDOWS:
        return []
    try:
        import psutil
    except ImportError:
        return []
    own = {os.getpid()}
    try:
        for parent in psutil.Process().parents():
            own.add(parent.pid)
    except psutil.Error:
        pass
    try:
        username = psutil.Process().username()
    except psutil.Error:
        username = ""
    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "exe", "memory_info", "username"]):
        info = proc.info
        mem = info.get("memory_info")
        rows.append({"pid": info.get("pid"), "name": info.get("name"), "exe": info.get("exe"),
                     "rss": getattr(mem, "rss", 0), "username": info.get("username")})
    return filter_candidates(rows, winapi.visible_window_pids(), own, username,
                             os.environ.get("SystemRoot", ""), exclude_dirs)


def request_close(pids: Iterable[int]) -> int:
    """Вежливо просит программы закрыться. Возвращает, у скольких программ нашлись окна."""
    return sum(1 for pid in pids if winapi.request_close_windows(pid) > 0)


def still_running(pids: Iterable[int]) -> list[int]:
    try:
        import psutil
    except ImportError:
        return []
    return [pid for pid in pids if psutil.pid_exists(pid)]


def force_terminate(pids: Iterable[int]) -> tuple[int, int]:
    """Принудительно завершает программы. Возвращает (завершено, не удалось)."""
    try:
        import psutil
    except ImportError:
        return 0, len(list(pids))
    done = failed = 0
    for pid in pids:
        try:
            psutil.Process(pid).terminate()
            done += 1
        except psutil.NoSuchProcess:
            done += 1
        except psutil.Error:
            log.warning("Не удалось завершить процесс %s", pid)
            failed += 1
    return done, failed
