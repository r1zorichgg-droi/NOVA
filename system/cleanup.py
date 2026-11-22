"""Очистка временных файлов пользователя (папка %TEMP%).

Двухшаговая: `scan_temp` только считает, что можно удалить, а `clean_files` удаляет — и вызывается
из интерфейса только после явного подтверждения пользователя.
Защиты: удаляются только обычные файлы внутри папки temp, старше заданного возраста (по умолчанию 1 час —
чтобы не задеть файлы, которые прямо сейчас распаковывает установщик); ссылки и точки соединения не
открываются; занятые другими программами файлы Windows не даст удалить — они просто пропускаются.
Системная папка C:\\Windows\\Temp не затрагивается (нужны права администратора).
"""
from __future__ import annotations

import logging
import os
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

MAX_ENTRIES = 300_000


@dataclass
class TempScan:
    root: Path
    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False


@dataclass
class CleanResult:
    deleted: int = 0
    freed_bytes: int = 0
    failed: int = 0


@dataclass
class ShaderCacheScan:
    roots: list[Path] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    total_bytes: int = 0
    truncated: bool = False
    min_age_s: float = 14 * 24 * 60 * 60


def temp_root() -> Path:
    return Path(tempfile.gettempdir())


def is_safe_root(root: Path) -> bool:
    """Не даём очистить диск целиком, домашнюю папку или её родителей — только вложенную папку."""
    try:
        resolved = root.resolve()
    except OSError:
        return False
    home = Path.home().resolve()
    if resolved == home or resolved in home.parents:
        return False
    return len(resolved.parts) >= 3 and resolved != Path(resolved.anchor)


def scan_temp(root: Path | None = None, min_age_s: float = 3600.0, now: float | None = None) -> TempScan:
    root = root or temp_root()
    scan = TempScan(root)
    if not is_safe_root(root) or not root.is_dir():
        return scan
    now = time.time() if now is None else now
    seen = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # не заходим в ссылки / точки соединения
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        for name in filenames:
            seen += 1
            if seen > MAX_ENTRIES:
                scan.truncated = True
                return scan
            path = Path(dirpath, name)
            try:
                st = path.lstat()
            except OSError:
                continue
            if path.is_symlink() or now - st.st_mtime < min_age_s:
                continue
            scan.files.append(path)
            scan.total_bytes += st.st_size
    return scan


def _inside(path: Path, root: Path) -> bool:
    try:
        return root.resolve() in path.resolve().parents
    except OSError:
        return False


def clean_files(scan: TempScan) -> CleanResult:
    """Удаляет файлы из результата сканирования. Возвращает, сколько удалено и освобождено."""
    result = CleanResult()
    if not is_safe_root(scan.root):
        return result
    for path in scan.files:
        try:
            if path.is_symlink() or not path.is_file() or not _inside(path, scan.root):
                continue
            size = path.stat().st_size
            path.unlink()
            result.deleted += 1
            result.freed_bytes += size
        except OSError:
            result.failed += 1              # файл занят программой или нет доступа — пропускаем
    # убираем только опустевшие подпапки (rmdir не удалит непустую)
    for dirpath, _dirs, _files in os.walk(scan.root, topdown=False):
        if Path(dirpath) == scan.root:
            continue
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return result


# --------------------------------------------------------------------------------------
# Кэш шейдеров GPU (только известные пользовательские каталоги)
# --------------------------------------------------------------------------------------
SHADER_CACHE_AGE_S = 14 * 24 * 60 * 60

def default_shader_cache_roots() -> list[Path]:
    """Известные кэши DirectX/NVIDIA/AMD/Intel внутри LOCALAPPDATA. Ничего системного."""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return []
    base = Path(local)
    candidates = (
        base / "D3DSCache",
        base / "NVIDIA" / "DXCache",
        base / "NVIDIA" / "GLCache",
        base / "AMD" / "DxCache",
        base / "AMD" / "GLCache",
        base / "Intel" / "ShaderCache",
        base / "Intel" / "D3DSCache",
    )
    result: list[Path] = []
    for root in candidates:
        try:
            if root.is_dir() and not root.is_symlink() and not getattr(os.path, "isjunction", lambda _p: False)(str(root)):
                result.append(root)
        except OSError:
            continue
    return result

def _is_link_or_junction(path: Path) -> bool:
    try:
        return path.is_symlink() or getattr(os.path, "isjunction", lambda _p: False)(str(path))
    except OSError:
        return True

def scan_shader_cache(roots: list[Path] | None = None, min_age_s: float = SHADER_CACHE_AGE_S,
                      now: float | None = None) -> ShaderCacheScan:
    """Только считает старые файлы в заранее выбранных GPU cache roots; ничего не удаляет."""
    scan = ShaderCacheScan(min_age_s=max(0.0, float(min_age_s)))
    now = time.time() if now is None else now
    selected = default_shader_cache_roots() if roots is None else list(roots)
    for root in selected:
        try:
            if not root.is_dir() or _is_link_or_junction(root) or not is_safe_root(root):
                continue
            root = root.resolve()
        except OSError:
            continue
        scan.roots.append(root)
        seen = 0
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = [d for d in dirnames if not _is_link_or_junction(Path(dirpath, d))]
            for name in filenames:
                seen += 1
                if seen > MAX_ENTRIES:
                    scan.truncated = True
                    return scan
                path = Path(dirpath, name)
                try:
                    st = path.lstat()
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or _is_link_or_junction(path) or now - st.st_mtime < scan.min_age_s:
                    continue
                scan.files.append(path)
                scan.total_bytes += st.st_size
    return scan

def clean_shader_cache(scan: ShaderCacheScan, now: float | None = None) -> CleanResult:
    """Удаляет только файлы из предварительного сканирования, старше порога и внутри точных корней."""
    result = CleanResult()
    now = time.time() if now is None else now
    safe_roots: list[Path] = []
    for root in scan.roots:
        try:
            if root.is_dir() and not _is_link_or_junction(root) and is_safe_root(root):
                safe_roots.append(root.resolve())
        except OSError:
            continue
    for path in scan.files:
        try:
            if _is_link_or_junction(path) or not path.is_file():
                continue
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or now - st.st_mtime < scan.min_age_s:
                continue
            resolved = path.resolve()
            if not any(root == resolved.parent or root in resolved.parents for root in safe_roots):
                continue
            size = st.st_size
            path.unlink()
            result.deleted += 1
            result.freed_bytes += size
        except OSError:
            result.failed += 1
    for root in safe_roots:
        for dirpath, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
            directory = Path(dirpath)
            if directory == root or _is_link_or_junction(directory):
                continue
            try:
                os.rmdir(directory)
            except OSError:
                pass
    return result
