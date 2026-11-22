"""Схемы электропитания Windows через штатный powercfg.

Что делает: показывает схемы, переключает активную, создаёт (копия существующей), переименовывает,
удаляет, меняет несколько параметров схемы (мин./макс. состояние процессора, отключение дисплея и сна).
Ничего не пишет в реестр напрямую и не требует драйверов — только команды powercfg.

Windows может запретить часть операций без прав администратора или на конкретном ноутбуке
(например, схему «Максимальная производительность» на некоторых моделях создать нельзя) —
в таком случае PowerError несёт текст самой Windows, а интерфейс показывает его пользователю.
Вне Windows все операции возвращают понятную ошибку, ничего не имитируя.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

GUID_RE = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
BALANCED = "381b4222-f694-41f0-9685-ff5bb260df2e"
HIGH_PERFORMANCE = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
POWER_SAVER = "a1841308-3541-4fab-bc81-f71556f20b4a"
ULTIMATE = "e9a42b02-d5df-448d-aa00-03f14749eb61"

NOVA_PLAN_NAME = "NOVA — максимальная производительность"

# Псевдонимы powercfg (не зависят от языка Windows).
_SUB_PROCESSOR, _THROTTLE_MIN, _THROTTLE_MAX = "SUB_PROCESSOR", "PROCTHROTTLEMIN", "PROCTHROTTLEMAX"
_SUB_VIDEO, _VIDEO_IDLE = "SUB_VIDEO", "VIDEOIDLE"
_SUB_SLEEP, _SLEEP_IDLE = "SUB_SLEEP", "STANDBYIDLE"

_PLAN_LINE = re.compile(rf"({GUID_RE})\s+\((.*)\)\s*(\*)?\s*$")
_ANY_GUID = re.compile(GUID_RE)
_HEX_VALUE = re.compile(r"0x[0-9a-fA-F]{8}")

_RANK = {"ultimate": 3, "high": 2, "balanced": 1, "saver": 0, "custom": 1}
_NAME_HINTS = (
    ("ultimate", re.compile(r"ultimate|максимальн\w*\s+производительн", re.I)),
    ("high", re.compile(r"high\s+performance|высок\w*\s+производительн", re.I)),
    ("balanced", re.compile(r"balanced|сбалансирован", re.I)),
    ("saver", re.compile(r"power\s+saver|экономи", re.I)),
)

Runner = Callable[[Sequence[str]], tuple[int, str]]


class PowerError(Exception):
    """Ошибка работы со схемами питания; текст можно показывать пользователю."""


@dataclass(frozen=True)
class PowerPlan:
    guid: str
    name: str
    active: bool = False

    @property
    def kind(self) -> str:
        """ultimate | high | balanced | saver | custom — по GUID, а для копий — по названию."""
        guid = self.guid.lower()
        known = {ULTIMATE: "ultimate", HIGH_PERFORMANCE: "high", BALANCED: "balanced", POWER_SAVER: "saver"}
        if guid in known:
            return known[guid]
        for kind, pattern in _NAME_HINTS:
            if pattern.search(self.name):
                return kind
        return "custom"

    @property
    def is_builtin(self) -> bool:
        return self.guid.lower() in (ULTIMATE, HIGH_PERFORMANCE, BALANCED, POWER_SAVER)

    @property
    def performance_rank(self) -> int:
        return _RANK[self.kind]


# --------------------------------------------------------------------------------------
# Разбор вывода powercfg (чистые функции — тестируются без Windows)
# --------------------------------------------------------------------------------------
def parse_plans(text: str) -> list[PowerPlan]:
    plans: list[PowerPlan] = []
    for line in text.splitlines():
        m = _PLAN_LINE.search(line.strip())
        if m:
            plans.append(PowerPlan(m.group(1).lower(), m.group(2).strip(), bool(m.group(3))))
    return plans


def parse_active(text: str) -> PowerPlan | None:
    for line in text.splitlines():
        m = _PLAN_LINE.search(line.strip())
        if m:
            return PowerPlan(m.group(1).lower(), m.group(2).strip(), True)
    return None


def parse_setting_values(text: str) -> tuple[int, int] | None:
    """Из вывода `powercfg /query ...` берёт текущие значения (от сети, от батареи).

    В конце вывода всегда идут два индекса: AC и DC. Подписи зависят от языка Windows,
    поэтому ориентируемся на сами шестнадцатеричные числа, а не на текст.
    """
    values = _HEX_VALUE.findall(text)
    if len(values) < 2:
        return None
    return int(values[-2], 16), int(values[-1], 16)


def best_performance_plan(plans: Sequence[PowerPlan], preferred_guid: str = "") -> PowerPlan | None:
    """Выбор схемы для режима производительности: выбранная пользователем, иначе самая «быстрая»."""
    preferred = preferred_guid.lower()
    if preferred:
        for plan in plans:
            if plan.guid == preferred:
                return plan
    ranked = [p for p in plans if p.performance_rank >= 2]
    if not ranked:
        return None
    # при равенстве — схема, созданная NOVA, затем остальные по названию
    ranked.sort(key=lambda p: (p.performance_rank, p.name.startswith("NOVA"), p.is_builtin), reverse=True)
    return ranked[0]


# --------------------------------------------------------------------------------------
# Запуск powercfg
# --------------------------------------------------------------------------------------
def _decode(raw: bytes) -> str:
    for codec in ("oem", "utf-8", "cp1251"):
        try:
            return raw.decode(codec)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("latin-1", errors="replace")


def run_powercfg(args: Sequence[str]) -> tuple[int, str]:
    """Запускает powercfg без окна консоли. Возвращает (код возврата, текст вывода)."""
    if not IS_WINDOWS:
        return 127, "Управление электропитанием работает только в Windows."
    try:
        proc = subprocess.run(
            ["powercfg", *args], capture_output=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"Не удалось запустить powercfg: {exc}"
    return proc.returncode, _decode(proc.stdout + proc.stderr).strip()


class PowerManager:
    """Операции над схемами питания. runner подменяется в тестах."""

    def __init__(self, runner: Runner = run_powercfg) -> None:
        self._run = runner

    @property
    def available(self) -> bool:
        return IS_WINDOWS

    def _ok(self, args: Sequence[str]) -> str:
        code, out = self._run(args)
        if code != 0:
            hint = ""
            if IS_WINDOWS and not out:
                hint = " (возможно, нужны права администратора)"
            raise PowerError((out or f"powercfg завершился с кодом {code}") + hint)
        return out

    # -- чтение --------------------------------------------------------------
    def plans(self) -> list[PowerPlan]:
        return parse_plans(self._ok(["/list"]))

    def active(self) -> PowerPlan | None:
        return parse_active(self._ok(["/getactivescheme"]))

    # -- изменение схем ------------------------------------------------------
    def set_active(self, guid: str) -> None:
        self._ok(["/setactive", guid])

    def create(self, name: str, base_guid: str) -> PowerPlan:
        """Создаёт копию схемы base_guid под новым именем."""
        name = name.strip()
        if not name:
            raise PowerError("Укажите название схемы.")
        out = self._ok(["/duplicatescheme", base_guid])
        found = _ANY_GUID.search(out)
        if not found:
            raise PowerError("Windows создала схему, но не вернула её идентификатор.")
        guid = found.group(0).lower()
        self._ok(["/changename", guid, name])
        return PowerPlan(guid, name, False)

    def rename(self, guid: str, name: str) -> None:
        name = name.strip()
        if not name:
            raise PowerError("Название не может быть пустым.")
        self._ok(["/changename", guid, name])

    def delete(self, guid: str) -> None:
        """Удаляет схему. Активную и единственную схемы удалить нельзя."""
        plans = self.plans()
        target = next((p for p in plans if p.guid == guid.lower()), None)
        if target is None:
            raise PowerError("Такой схемы уже нет.")
        if target.active:
            raise PowerError("Активную схему удалить нельзя: сначала сделайте активной другую.")
        if len(plans) <= 1:
            raise PowerError("Нельзя удалить единственную схему питания.")
        self._ok(["/delete", guid])

    # -- параметры схемы (питание от сети) -----------------------------------
    def _query(self, guid: str, sub: str, setting: str) -> tuple[int, int]:
        values = parse_setting_values(self._ok(["/query", guid, sub, setting]))
        if values is None:
            raise PowerError("Не удалось прочитать параметр схемы.")
        return values

    def _write(self, guid: str, sub: str, setting: str, ac: int, dc: int | None = None) -> None:
        self._ok(["/setacvalueindex", guid, sub, setting, str(int(ac))])
        if dc is not None:
            self._ok(["/setdcvalueindex", guid, sub, setting, str(int(dc))])

    def cpu_state(self, guid: str) -> tuple[int, int]:
        """(минимальное, максимальное) состояние процессора в процентах при питании от сети."""
        return self._query(guid, _SUB_PROCESSOR, _THROTTLE_MIN)[0], self._query(guid, _SUB_PROCESSOR, _THROTTLE_MAX)[0]

    def set_cpu_state(self, guid: str, minimum: int, maximum: int) -> None:
        minimum, maximum = max(0, min(100, minimum)), max(0, min(100, maximum))
        if minimum > maximum:
            raise PowerError("Минимальное состояние процессора не может быть больше максимального.")
        self._write(guid, _SUB_PROCESSOR, _THROTTLE_MIN, minimum)
        self._write(guid, _SUB_PROCESSOR, _THROTTLE_MAX, maximum)

    def timeouts(self, guid: str) -> tuple[int, int]:
        """(выключение дисплея, переход в сон) в минутах при питании от сети; 0 = никогда."""
        return (self._query(guid, _SUB_VIDEO, _VIDEO_IDLE)[0] // 60,
                self._query(guid, _SUB_SLEEP, _SLEEP_IDLE)[0] // 60)

    def set_timeouts(self, guid: str, display_min: int, sleep_min: int) -> None:
        self._write(guid, _SUB_VIDEO, _VIDEO_IDLE, max(0, display_min) * 60)
        self._write(guid, _SUB_SLEEP, _SLEEP_IDLE, max(0, sleep_min) * 60)

    def apply_to_active(self, guid: str) -> None:
        """Изменения параметров вступают в силу после повторной активации схемы."""
        active = self.active()
        if active and active.guid == guid.lower():
            self.set_active(guid)

    # -- схема для режима производительности ---------------------------------
    def find_performance_plan(self, preferred_guid: str = "") -> PowerPlan | None:
        return best_performance_plan(self.plans(), preferred_guid)

    def ensure_performance_plan(self, preferred_guid: str = "") -> tuple[PowerPlan, bool]:
        """Возвращает схему максимальной производительности; если её нет в списке — создаёт копию
        встроенной (сначала «Максимальная», затем «Высокая»). Второй элемент: схема только что создана."""
        existing = self.find_performance_plan(preferred_guid)
        if existing is not None:
            return existing, False
        last_error = ""
        for base in (ULTIMATE, HIGH_PERFORMANCE):
            try:
                return self.create(NOVA_PLAN_NAME, base), True
            except PowerError as exc:
                last_error = str(exc)
                log.info("Не удалось создать схему из %s: %s", base, exc)
        raise PowerError("На этом ПК Windows не даёт создать схему высокой производительности"
                         + (f": {last_error}" if last_error else "."))
