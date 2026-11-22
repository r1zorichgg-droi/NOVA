"""Поиск «главного» EXE в папке игры (нужен для значка карточки и как запасной вариант).

Это эвристика, а не знание: у многих игр несколько EXE. Поэтому найденный путь НЕ используется для запуска
Steam-игр (они запускаются через steam://, чтобы работали оверлей и облачные сохранения), а только
как источник значка и подсказка для отслеживания процесса.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

_SKIP = re.compile(
    r"unins|uninst|setup|install|redist|vcredist|vc_redist|dxsetup|dotnet|crash|report|updater|helper|"
    r"easyanticheat|eac_|beservice|battleye|unitycrash|ue4prereq|cefprocess|webhelper|notification|"
    r"benchmark|configtool|config\.exe|oalinst|physx|directx", re.I)
_BIN_DIRS = {"bin", "bin64", "binaries", "win64", "x64", "win32", "game", "release", "client"}
_MAX_VISITED = 400


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-zа-яё]", "", text.lower())


def candidate_exes(folder: Path, max_depth: int = 3) -> list[Path]:
    """EXE в папке и в типичных подпапках (bin, binaries, win64 ...), без обхода всего дерева."""
    found: list[Path] = []
    visited = 0
    stack: list[tuple[Path, int]] = [(folder, 0)]
    while stack and visited < _MAX_VISITED:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            visited += 1
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".exe"):
                    found.append(Path(entry.path))
                elif (entry.is_dir(follow_symlinks=False) and depth < max_depth
                      and (depth == 0 and entry.name.lower() in _BIN_DIRS or entry.name.lower() in _BIN_DIRS
                           or entry.name.lower().endswith("game"))):
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return found


def guess_main_exe(folder: Path | str, title: str = "") -> str:
    """Лучший кандидат на главный EXE или пустая строка, если подходящего нет."""
    folder = Path(folder)
    if not folder.is_dir():
        return ""
    best, best_score = "", 0.0
    folder_key, title_key = _norm(folder.name), _norm(title)
    for exe in candidate_exes(folder):
        if _SKIP.search(exe.stem):
            continue
        stem = _norm(exe.stem.replace("-win64-shipping", "").replace("-win32-shipping", ""))
        score = 1.0
        if stem and (stem == folder_key or stem == title_key):
            score += 100
        elif stem and (stem in folder_key or folder_key in stem or (title_key and (stem in title_key or title_key in stem))):
            score += 40
        try:
            score += min(exe.stat().st_size / (8 * 1024 * 1024), 20)    # крупные файлы вероятнее игра, чем утилита
        except OSError:
            continue
        if score > best_score:
            best, best_score = str(exe), score
    return best
