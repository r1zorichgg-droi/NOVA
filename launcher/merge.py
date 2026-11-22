"""Слияние результатов автопоиска с библиотекой: без дубликатов, без возврата удалённых пользователем карточек."""
from __future__ import annotations

import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from database.database import ItemTable

from . import apps as apps_rules
from .found import Found


@dataclass
class MergeResult:
    added: int = 0
    updated: int = 0
    skipped: int = 0        # уже были в библиотеке, либо убраны пользователем
    missing: int = 0        # раньше находились, а теперь на ПК не найдены
    restored: int = 0       # снова найдены после пометки «не найдена»

    def __iadd__(self, other: MergeResult) -> MergeResult:
        self.added += other.added
        self.updated += other.updated
        self.skipped += other.skipped
        self.missing += other.missing
        self.restored += other.restored
        return self


def prune_unwanted(table: ItemTable[Any], keep_ids: Iterable[int] = (), system_dir: str = "") -> int:
    """Удаляет из библиотеки лишние карточки, найденные автопоиском (служебные ярлыки, реклама Store).

    Не трогает: добавленное вручную, избранное, то, что вы запускали из NOVA, и закреплённое в острове (keep_ids).
    Сами программы на компьютере не затрагиваются — удаляется только запись в библиотеке NOVA.
    """
    keep = set(keep_ids)
    removed = 0
    for item in table.items(include_hidden=True):
        if item.id in keep or item.favorite or item.play_count > 0:
            continue
        if apps_rules.is_unwanted_item(item.source, item.title, item.exe_path, item.args, item.external_id or "", system_dir):
            table.delete(item.id)
            removed += 1
    return removed


def _file_exists(path: str) -> bool:
    return bool(path) and os.path.isfile(path)


def merge_found(table: ItemTable[Any], found: Iterable[Found], scanned_sources: set[str] | None = None) -> MergeResult:
    """Добавляет новое, обновляет пути у уже известного и помечает исчезнувшее.

    - Ключ записи: (источник, внешний id). Вручную добавленная карточка с тем же EXE считается дубликатом.
    - Карточки, убранные пользователем (hidden), не возвращаются и не меняются.
    - Название, категорию, избранное и пользовательские настройки автопоиск не перезаписывает.
    - Карточка, пропавшая из источника, который удалось прочитать, получает пометку «не найдена»
      (но не удаляется: пользователь мог перенести игру на другой диск).
    """
    result = MergeResult()
    seen: set[tuple[str, str]] = set()
    for f in found:
        key = (f.source, f.external_id)
        if key in seen:
            continue
        seen.add(key)
        existing = table.find_by_external(f.source, f.external_id)
        if existing is None:
            duplicate = table.find_by_exe(f.exe_path) if f.exe_path else None
            if duplicate is not None:
                result.skipped += 1
                continue
            table.add(title=f.title, source=f.source, external_id=f.external_id, exe_path=f.exe_path,
                      launch_uri=f.launch_uri, install_dir=f.install_dir, category=f.category,
                      image_path=f.image_path, args=f.args)
            result.added += 1
            continue
        if existing.hidden:
            result.skipped += 1
            continue
        changes: dict[str, Any] = {}
        for attr in ("exe_path", "launch_uri", "install_dir"):
            new = getattr(f, attr)
            if new and new != getattr(existing, attr):
                changes[attr] = new
        if f.image_path and not _file_exists(existing.image_path):
            changes["image_path"] = f.image_path
        opts = existing.opts
        if opts.pop("uninstalled", None):
            changes["options"] = json.dumps(opts, ensure_ascii=False)
            result.restored += 1
        if changes:
            table.update(existing.id, **changes)
            result.updated += 1
        else:
            result.skipped += 1

    if scanned_sources:
        for item in table.items(include_hidden=True):
            if (item.source in scanned_sources and item.external_id is not None
                    and (item.source, item.external_id) not in seen and not item.opts.get("uninstalled")):
                table.set_option(item.id, "uninstalled", True)
                result.missing += 1
    return result
