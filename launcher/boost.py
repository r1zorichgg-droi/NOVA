"""Режим производительности на время игры: что включается, и — главное — как это гарантированно возвращается.

Что делает NOVA, когда вы запускаете игру из неё (каждый пункт можно выключить в настройках):
  1. включает самую быструю схему электропитания (Максимальная → Высокая производительность);
  2. включает «Игровой режим» Windows и выключает фоновую запись игр (только HKCU, без администратора);
  3. при прямом запуске EXE — повышает приоритет процесса флагом при создании (см. launch.py).

Чего NOVA НЕ делает: не обращается к процессу игры (никаких handles, инъекций и хуков), не «чистит» память,
не трогает службы и драйверы, не обещает конкретный прирост FPS. Реальный эффект зависит от ПК и игры:
на ноутбуке от батареи он заметен, на настольном ПК с уже включённой высокой производительностью — почти нулевой.

Надёжность возврата: перед первым изменением «что было» записывается на диск (ключ state.boost_restore).
Если NOVA или Windows завершились аварийно, при следующем запуске прежние настройки будут возвращены.
Если вы сами сменили схему питания во время игры, NOVA её не трогает.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from system import tweaks
from system.power import BALANCED, PowerError, PowerManager

log = logging.getLogger(__name__)

STATE_KEY = "boost_restore"
MAX_RESTORE_ATTEMPTS = 3


@dataclass(frozen=True)
class BoostConfig:
    power: bool = True
    plan_guid: str = ""
    game_mode: bool = True
    game_dvr: bool = True

    @property
    def anything(self) -> bool:
        return self.power or self.game_mode or self.game_dvr


@dataclass
class BoostStep:
    label: str
    ok: bool
    detail: str = ""
    changed: bool = False       # True, если NOVA действительно что-то изменила (а не «уже так было»)


@dataclass
class BoostReport:
    steps: list[BoostStep] = field(default_factory=list)
    created_plan_guid: str = ""         # NOVA создала схему питания — её GUID стоит запомнить в настройках
    already_active: bool = False

    @property
    def changed(self) -> bool:
        return any(s.changed for s in self.steps)

    @property
    def failures(self) -> list[BoostStep]:
        return [s for s in self.steps if not s.ok]

    def summary(self) -> str:
        parts = [f"{s.label}: {s.detail}" if s.detail else s.label for s in self.steps if s.ok]
        return " · ".join(parts)


class BoostManager:
    """Потокобезопасен (внутренняя блокировка); вызывается из фонового потока."""

    def __init__(self, power: PowerManager, tweak_manager: tweaks.TweakManager, state: Any) -> None:
        self._power, self._tweaks, self._state = power, tweak_manager, state
        self._lock = threading.Lock()

    # -- состояние -------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        data = self._state.get(STATE_KEY)
        return dict(data) if isinstance(data, dict) else {}

    def _save(self, data: dict[str, Any]) -> None:
        self._state.set(STATE_KEY, data)

    def is_active(self) -> bool:
        return bool(self._load())

    def pending(self) -> dict[str, Any]:
        """Сохранённое состояние (для восстановления после сбоя)."""
        return self._load()

    # -- включение -------------------------------------------------------------
    def apply(self, cfg: BoostConfig, label: str = "", watch: dict[str, Any] | None = None) -> BoostReport:
        with self._lock:
            if self._load():
                return BoostReport(already_active=True)
            report = BoostReport()
            st: dict[str, Any] = {"started": time.time(), "label": label, "plan_before": "", "plan_boost": "",
                                  "registry": [], "watch": watch or {}, "attempts": 0}
            if cfg.power:
                self._apply_power(cfg, st, report)
            for enabled, tweak in ((cfg.game_mode, tweaks.GAME_MODE), (cfg.game_dvr, tweaks.GAME_DVR)):
                if enabled:
                    self._apply_tweak(tweak, st, report)
            if st["plan_boost"] or st["registry"]:
                self._save(st)
            return report

    def _apply_power(self, cfg: BoostConfig, st: dict[str, Any], report: BoostReport) -> None:
        label = "Электропитание"
        try:
            before = self._power.active()
            target, created = self._power.ensure_performance_plan(cfg.plan_guid)
            if created:
                report.created_plan_guid = target.guid
            if before is not None and before.guid == target.guid:
                report.steps.append(BoostStep(label, True, f"уже «{target.name}»"))
                return
            # Сначала сохраняем «что было», потом переключаем: при аварии откат всё равно будет возможен.
            st["plan_before"], st["plan_boost"] = (before.guid if before else ""), target.guid
            self._save(st)
            try:
                self._power.set_active(target.guid)
            except PowerError:
                st["plan_before"] = st["plan_boost"] = ""
                self._state.set(STATE_KEY, {})      # переключение не удалось — откатывать нечего
                raise
            report.steps.append(BoostStep(label, True, f"«{target.name}»", changed=True))
        except PowerError as exc:
            log.warning("Режим производительности: электропитание не переключено: %s", exc)
            report.steps.append(BoostStep(label, False, str(exc)))

    def _apply_tweak(self, tweak: tweaks.Tweak, st: dict[str, Any], report: BoostReport) -> None:
        try:
            records = tweaks.apply_values(self._tweaks.backend, tweak.values)
        except tweaks.TweakError as exc:
            log.warning("Режим производительности: %s не применён: %s", tweak.id, exc)
            report.steps.append(BoostStep(tweak.title, False, str(exc)))
            return
        if records:
            st["registry"] = [*st["registry"], *records]
            self._save(st)
        report.steps.append(BoostStep(tweak.title, True, "включено" if records else "уже настроено", changed=bool(records)))

    # -- возврат ---------------------------------------------------------------
    def restore(self) -> BoostReport:
        """Возвращает всё, что изменил apply(). Безопасно вызывать повторно и когда ничего не включено."""
        with self._lock:
            st = self._load()
            report = BoostReport()
            if not st:
                return report
            hard_failure = False
            records = st.get("registry") or []
            if records:
                failed = tweaks.restore_records(self._tweaks.backend, records)
                report.steps.append(BoostStep("Настройки Windows", failed == 0,
                                              "возвращены" if failed == 0 else f"не удалось вернуть: {failed}", changed=True))
                hard_failure |= failed > 0
            if st.get("plan_boost"):
                step = self._restore_power(st)
                report.steps.append(step)
                hard_failure |= not step.ok
            if hard_failure and int(st.get("attempts", 0)) + 1 < MAX_RESTORE_ATTEMPTS:
                st["attempts"] = int(st.get("attempts", 0)) + 1
                self._save(st)                      # попробуем ещё раз при следующем запуске
            else:
                self._state.set(STATE_KEY, {})
            return report

    def _restore_power(self, st: dict[str, Any]) -> BoostStep:
        label = "Электропитание"
        try:
            current = self._power.active()
            if current is None or current.guid != st["plan_boost"]:
                return BoostStep(label, True, "схему во время игры сменили вручную — оставлена как есть")
            plans = self._power.plans()
            guids = {p.guid for p in plans}
            target = st.get("plan_before") if st.get("plan_before") in guids else (BALANCED if BALANCED in guids else "")
            if not target:
                return BoostStep(label, False, "прежняя схема удалена — выберите схему на странице «Электропитание»")
            self._power.set_active(target)
            name = next((p.name for p in plans if p.guid == target), "прежняя")
            return BoostStep(label, True, f"возвращена «{name}»", changed=True)
        except PowerError as exc:
            return BoostStep(label, False, str(exc))
