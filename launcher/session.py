"""Определение того, что запущенная из NOVA игра или программа всё ещё работает (чистая логика, без Qt).

NOVA только читает список процессов (имя и путь EXE) — так же, как Диспетчер задач. К самим процессам игр
она не подключается. Благодаря этому режим производительности выключается сразу после выхода из игры.
Если процесс не удалось обнаружить в течение 3 минут после запуска, NOVA не оставляет режим включённым
«на всякий случай», а возвращает прежние настройки и сообщает об этом.
"""
from __future__ import annotations

import ntpath
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from database.models import LibraryItem

GRACE_S = 180.0         # сколько ждать появления процесса после запуска
MISSING_POLLS = 2       # сколько опросов подряд процесса нет, чтобы считать, что игра закрыта (защита от перезапусков)
POLL_INTERVAL_MS = 4000

# Папки, по которым нельзя определять «чужие» процессы: под них попадёт полсистемы.
_BROAD_NAMES = {"program files", "program files (x86)", "windows", "system32", "users", "programdata", "games",
                "desktop", "downloads", "documents", "appdata", "local", "roaming", "temp"}


@dataclass
class WatchSpec:
    pids: set[int] = field(default_factory=set)
    exes: list[str] = field(default_factory=list)       # полные пути к EXE (нижний регистр, нормализованные)
    dirs: list[str] = field(default_factory=list)       # папки установки (нормализованные, с завершающим «\»)
    names: list[str] = field(default_factory=list)      # имена процессов (нижний регистр)

    @property
    def trackable(self) -> bool:
        return bool(self.pids or self.exes or self.dirs or self.names)

    def to_dict(self) -> dict[str, Any]:
        return {"exes": self.exes, "dirs": self.dirs, "names": self.names}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WatchSpec:
        return cls(exes=list(data.get("exes") or []), dirs=list(data.get("dirs") or []),
                   names=list(data.get("names") or []))


def _norm(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path)) if path else ""


def is_broad_dir(path: str) -> bool:
    """Слишком общая папка (корень диска, Program Files, профиль пользователя ...)."""
    norm = _norm(path).rstrip("\\")
    if not norm:
        return True
    drive, rest = ntpath.splitdrive(norm)
    parts = [p for p in rest.split("\\") if p]
    if len(parts) <= 1:                                   # «d:\», «c:\games»
        return True
    if parts[-1] in _BROAD_NAMES:
        return True
    return len(parts) == 2 and parts[0] == "users"        # c:\users\имя


def build_watch(item: LibraryItem, pid: int = 0) -> WatchSpec:
    spec = WatchSpec()
    if pid:
        spec.pids.add(pid)
    if item.exe_path:
        exe = _norm(item.exe_path)
        spec.exes.append(exe)
        folder = item.install_dir or ntpath.dirname(item.exe_path)
    else:
        folder = item.install_dir
    if folder and not is_broad_dir(folder):
        spec.dirs.append(_norm(folder).rstrip("\\") + "\\")
    extra = item.opts.get("process_names")
    if isinstance(extra, list):
        spec.names += [str(n).lower() for n in extra if n]
    return spec


def find_matches(spec: WatchSpec, processes: Iterable[tuple[int, str, str]]) -> set[int]:
    """PID запущенных процессов, подходящих под spec. processes: (pid, имя, путь EXE или пустая строка)."""
    hits: set[int] = set()
    for pid, name, exe in processes:
        if pid in spec.pids:
            hits.add(pid)
            continue
        norm = _norm(exe)
        if norm and (norm in spec.exes or any(norm.startswith(d) for d in spec.dirs)):
            hits.add(pid)
        elif name.lower() in spec.names:
            hits.add(pid)
    return hits


@dataclass
class Session:
    kind: str                       # game | app
    item_id: int
    title: str
    history_id: int
    started: float
    spec: WatchSpec
    boosted: bool = False
    boost_pending: bool = False     # запрос на включение режима отправлен, ответа ещё нет
    seen_running: bool = False
    missing: int = 0
    last_pids: set[int] = field(default_factory=set)


def evaluate(session: Session, running: bool, now: float) -> tuple[bool, str]:
    """Обновляет сессию по результату опроса. Возвращает (закончилась ли, причина: exit | never_seen)."""
    if running:
        session.seen_running = True
        session.missing = 0
        return False, ""
    session.missing += 1
    if session.seen_running and session.missing >= MISSING_POLLS:
        return True, "exit"
    if not session.seen_running and now - session.started > GRACE_S:
        return True, "never_seen"
    return False, ""


def snapshot_processes() -> list[tuple[int, str, str]]:
    """Список процессов (pid, имя, путь EXE) — только чтение, как в Диспетчере задач."""
    try:
        import psutil
    except ImportError:
        return []
    out: list[tuple[int, str, str]] = []
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        info = proc.info
        out.append((int(info.get("pid") or 0), str(info.get("name") or ""), str(info.get("exe") or "")))
    return out
