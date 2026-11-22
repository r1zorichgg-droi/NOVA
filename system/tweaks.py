"""Безопасные настройки Windows, влияющие на игры. Только реестр текущего пользователя (HKCU):
права администратора не нужны, всё обратимо.

Каждое изменение сопровождается записью «что было раньше», поэтому «Вернуть» восстанавливает исходное
значение (или удаляет параметр, если его раньше не было). Если пользователь сам изменил значение
после нас, при откате оно не затирается.

Что здесь НЕТ намеренно: твики, требующие администратора или перезагрузки (HAGS, MSI-режим, службы),
«очистка оперативной памяти» и прочие приёмы без измеримого эффекта.
"""
from __future__ import annotations

import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

GPU_PREF_KEY = r"Software\Microsoft\DirectX\UserGpuPreferences"
LAYERS_KEY = r"Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers"
FSO_FLAG = "DISABLEDXMAXIMIZEDWINDOWEDMODE"

GPU_DEFAULT, GPU_POWER_SAVING, GPU_HIGH_PERFORMANCE = 0, 1, 2


class TweakError(Exception):
    """Не удалось прочитать или записать реестр; текст можно показывать пользователю."""


@dataclass(frozen=True)
class RegValue:
    key: str                # путь внутри HKEY_CURRENT_USER
    name: str
    kind: str               # "dword" | "sz"
    value: int | str


@dataclass(frozen=True)
class Tweak:
    id: str
    title: str
    description: str
    values: tuple[RegValue, ...]


GAME_MODE = Tweak(
    "game_mode", "Игровой режим Windows",
    "Windows отдаёт игре больше ресурсов и не запускает обновления драйверов и установку обновлений в процессе игры.",
    (RegValue(r"Software\Microsoft\GameBar", "AllowAutoGameMode", "dword", 1),
     RegValue(r"Software\Microsoft\GameBar", "AutoGameModeEnabled", "dword", 1)))

GAME_DVR = Tweak(
    "game_dvr", "Фоновая запись игр (Xbox Game Bar)",
    "Выключает фоновую запись видео и клипов — она берёт часть видеокарты и диска. "
    "Ручная запись через Game Bar останется доступной.",
    (RegValue(r"System\GameConfigStore", "GameDVR_Enabled", "dword", 0),
     RegValue(r"Software\Microsoft\Windows\CurrentVersion\GameDVR", "AppCaptureEnabled", "dword", 0)))

TRANSPARENCY = Tweak(
    "transparency", "Эффекты прозрачности Windows",
    "Отключает размытие и прозрачность панели задач и окон. Даёт небольшой выигрыш на слабых видеокартах; "
    "на мощных ПК разницы почти нет.",
    (RegValue(r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize", "EnableTransparency", "dword", 0),))

TWEAKS: dict[str, Tweak] = {t.id: t for t in (GAME_MODE, GAME_DVR, TRANSPARENCY)}
# Применяются автоматически на время игры (если включено в настройках режима производительности).
BOOST_TWEAKS = (GAME_MODE, GAME_DVR)


# --------------------------------------------------------------------------------------
# Доступ к реестру
# --------------------------------------------------------------------------------------
class RegistryBackend(Protocol):
    def read(self, key: str, name: str) -> tuple[str, int | str] | None: ...
    def write(self, key: str, name: str, kind: str, value: int | str) -> None: ...
    def delete(self, key: str, name: str) -> None: ...


class WinRegistry:
    """HKEY_CURRENT_USER через winreg."""

    def read(self, key: str, name: str) -> tuple[str, int | str] | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_READ) as handle:
                value, reg_type = winreg.QueryValueEx(handle, name)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise TweakError(f"Не удалось прочитать реестр: {exc}") from exc
        if reg_type == winreg.REG_DWORD:
            return "dword", int(value)
        if reg_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
            return "sz", str(value)
        return None

    def write(self, key: str, name: str, kind: str, value: int | str) -> None:
        import winreg
        try:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                if kind == "dword":
                    winreg.SetValueEx(handle, name, 0, winreg.REG_DWORD, int(value))
                else:
                    winreg.SetValueEx(handle, name, 0, winreg.REG_SZ, str(value))
        except OSError as exc:
            raise TweakError(f"Не удалось записать в реестр: {exc}") from exc

    def delete(self, key: str, name: str) -> None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise TweakError(f"Не удалось удалить параметр реестра: {exc}") from exc


class UnavailableRegistry:
    """Заглушка для не-Windows: честно сообщает, что настройка невозможна, ничего не имитируя."""

    def _fail(self) -> TweakError:
        return TweakError("Настройки Windows доступны только в Windows.")

    def read(self, key: str, name: str) -> tuple[str, int | str] | None:
        raise self._fail()

    def write(self, key: str, name: str, kind: str, value: int | str) -> None:
        raise self._fail()

    def delete(self, key: str, name: str) -> None:
        raise self._fail()


def default_backend() -> RegistryBackend:
    return WinRegistry() if IS_WINDOWS else UnavailableRegistry()


# --------------------------------------------------------------------------------------
# Применение и откат (с записью «что было»)
# --------------------------------------------------------------------------------------
def is_applied(backend: RegistryBackend, values: Sequence[RegValue]) -> bool:
    for v in values:
        current = backend.read(v.key, v.name)
        if current is None or current[1] != v.value:
            return False
    return True


def apply_values(backend: RegistryBackend, values: Sequence[RegValue]) -> list[dict[str, Any]]:
    """Записывает значения. Возвращает записи для отката — только по тем параметрам, что реально менялись."""
    records: list[dict[str, Any]] = []
    try:
        for v in values:
            current = backend.read(v.key, v.name)
            if current is not None and current[1] == v.value:
                continue
            records.append({"key": v.key, "name": v.name, "kind": v.kind, "new": v.value,
                            "old": None if current is None else current[1],
                            "old_kind": None if current is None else current[0]})
            backend.write(v.key, v.name, v.kind, v.value)
    except TweakError:
        restore_records(backend, records)       # не оставляем половину изменений
        raise
    return records


def restore_records(backend: RegistryBackend, records: Sequence[dict[str, Any]]) -> int:
    """Возвращает прежние значения. Если значение с тех пор изменил сам пользователь — не трогает его.
    Возвращает число записей, которые не удалось откатить."""
    failed = 0
    for rec in reversed(list(records)):
        try:
            current = backend.read(rec["key"], rec["name"])
            if current is None or current[1] != rec["new"]:
                continue
            if rec["old"] is None:
                backend.delete(rec["key"], rec["name"])
            else:
                backend.write(rec["key"], rec["name"], rec.get("old_kind") or rec["kind"], rec["old"])
        except (TweakError, KeyError):
            log.exception("Не удалось откатить параметр реестра %s", rec)
            failed += 1
    return failed


# --------------------------------------------------------------------------------------
# Настройки отдельной программы (путь к EXE → значение)
# --------------------------------------------------------------------------------------
def norm_exe(path: str) -> str:
    return os.path.normpath(path)


def merge_gpu_pref(existing: str | None, preference: int | None) -> str | None:
    """Меняет только GpuPreference в строке вида 'GpuPreference=2;SwapEffectUpgradeEnable=1;'.
    None — убрать предпочтение; если строка опустела, вернёт None (параметр нужно удалить)."""
    pairs = [p for p in (existing or "").split(";") if p.strip() and not p.strip().startswith("GpuPreference=")]
    if preference is not None:
        pairs.insert(0, f"GpuPreference={int(preference)}")
    return (";".join(p.strip() for p in pairs) + ";") if pairs else None


def parse_gpu_pref(value: str | None) -> int | None:
    for part in (value or "").split(";"):
        part = part.strip()
        if part.startswith("GpuPreference="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                return None
    return None


def merge_layers(existing: str | None, flag: str, on: bool) -> str | None:
    """Добавляет/убирает один флаг совместимости, не трогая остальные (например, RUNASADMIN)."""
    flags = [t for t in (existing or "").split() if t != "~"]
    if on and flag not in flags:
        flags.append(flag)
    if not on:
        flags = [f for f in flags if f != flag]
    return ("~ " + " ".join(flags)) if flags else None


class TweakManager:
    """Фасад: твики Windows с сохранением исходных значений и настройки отдельных программ."""

    BACKUP_KEY = "tweaks_backup"

    def __init__(self, state: Any, backend: RegistryBackend | None = None) -> None:
        """state — хранилище с get(key)/set(key, value) (в приложении это StateStore из базы данных)."""
        self._settings = state
        self.backend: RegistryBackend = backend or default_backend()

    @property
    def available(self) -> bool:
        return IS_WINDOWS or not isinstance(self.backend, UnavailableRegistry)

    # -- глобальные твики ----------------------------------------------------
    def _backups(self) -> dict[str, list[dict[str, Any]]]:
        data = self._settings.get(self.BACKUP_KEY)
        return dict(data) if isinstance(data, dict) else {}

    def is_on(self, tweak_id: str) -> bool:
        return is_applied(self.backend, TWEAKS[tweak_id].values)

    def enable(self, tweak_id: str) -> None:
        tweak = TWEAKS[tweak_id]
        records = apply_values(self.backend, tweak.values)
        if records:
            backups = self._backups()
            backups[tweak_id] = records + backups.get(tweak_id, [])
            self._settings.set(self.BACKUP_KEY, backups)

    def disable(self, tweak_id: str) -> int:
        """Возвращает исходные значения. Число — сколько параметров не удалось откатить."""
        backups = self._backups()
        records = backups.pop(tweak_id, [])
        failed = restore_records(self.backend, records)
        self._settings.set(self.BACKUP_KEY, backups)
        return failed

    def has_backup(self, tweak_id: str) -> bool:
        return bool(self._backups().get(tweak_id))

    # -- отдельные программы -------------------------------------------------
    def gpu_preference(self, exe: str) -> int | None:
        current = self.backend.read(GPU_PREF_KEY, norm_exe(exe))
        return parse_gpu_pref(str(current[1])) if current else None

    def set_gpu_preference(self, exe: str, preference: int | None) -> None:
        """preference: 2 — высокая производительность, 1 — энергосбережение, None — по умолчанию Windows."""
        name = norm_exe(exe)
        current = self.backend.read(GPU_PREF_KEY, name)
        merged = merge_gpu_pref(str(current[1]) if current else None, preference)
        if merged is None:
            self.backend.delete(GPU_PREF_KEY, name)
        else:
            self.backend.write(GPU_PREF_KEY, name, "sz", merged)

    def fullscreen_optimizations_disabled(self, exe: str) -> bool:
        current = self.backend.read(LAYERS_KEY, norm_exe(exe))
        return current is not None and FSO_FLAG in str(current[1]).split()

    def set_fullscreen_optimizations_disabled(self, exe: str, disabled: bool) -> None:
        name = norm_exe(exe)
        current = self.backend.read(LAYERS_KEY, name)
        merged = merge_layers(str(current[1]) if current else None, FSO_FLAG, disabled)
        if merged is None:
            self.backend.delete(LAYERS_KEY, name)
        else:
            self.backend.write(LAYERS_KEY, name, "sz", merged)
