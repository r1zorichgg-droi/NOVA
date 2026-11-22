"""Менеджер библиотеки: автопоиск, ручное добавление, запуск, режим производительности, отслеживание сессий.

Это Qt-слой над чистыми модулями launcher/* и system/*: тяжёлое (поиск, powercfg, список процессов)
выполняется в фоновых потоках, а результаты и сигналы приходят в поток интерфейса.
"""
from __future__ import annotations

import logging
import ntpath
import os
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from database.database import Database, ItemTable
from database.models import LibraryItem
from island.model import Notification
from island.notifications import NotificationHub
from settings import AppSettings
from system import tweaks
from ui.workers import AsyncRunner

from . import apps as apps_scan
from .boost import BoostConfig, BoostManager, BoostReport
from .categories import guess_app_category
from .launch import LaunchResult, Launcher, resolve_priority
from .game_profiles import find_game_profile
from .merge import MergeResult, merge_found, prune_unwanted
from .scanner import scan_all
from .session import (POLL_INTERVAL_MS, Session, WatchSpec, build_watch, evaluate, find_matches,
                      snapshot_processes)

log = logging.getLogger(__name__)

EXE_SUFFIXES = (".exe", ".bat", ".cmd", ".com")
PIN_KEY = "island_launch_items"
MAX_PINS = 10


@dataclass
class ScanSummary:
    games: MergeResult = field(default_factory=MergeResult)
    apps: MergeResult = field(default_factory=MergeResult)
    notes: list[str] = field(default_factory=list)
    manual: bool = False
    pruned: int = 0         # убрано лишних карточек (служебные ярлыки, реклама Store)

    @property
    def added(self) -> int:
        return self.games.added + self.apps.added


@dataclass
class SessionInfo:
    kind: str
    item_id: int
    title: str
    seconds: float = 0.0
    boosted: bool = False
    reason: str = ""            # exit | never_seen


class LibraryManager(QObject):
    changed = Signal()                  # библиотека изменилась — страницы перерисовывают карточки
    scan_state = Signal(bool)           # идёт ли поиск
    scan_done = Signal(object)          # ScanSummary
    notice = Signal(str, str)           # (info | warning | error, текст) — короткие сообщения для интерфейса
    session_started = Signal(object)    # SessionInfo
    session_ended = Signal(object)      # SessionInfo
    boost_changed = Signal(bool)        # режим производительности включён / выключен

    def __init__(self, db: Database, settings: AppSettings, hub: NotificationHub | None,
                 boost: BoostManager, tweak_manager: tweaks.TweakManager, launcher: Launcher | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._db, self._settings, self._hub = db, settings, hub
        self._boost, self._tweaks = boost, tweak_manager
        self._launcher = launcher or Launcher()
        self._runner = AsyncRunner(self)
        self._sessions: list[Session] = []
        self._scanning = False
        self._polling = False
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll)

    # ------------------------------------------------------------------ доступ
    def table(self, kind: str) -> ItemTable[Any]:
        return self._db.games if kind == "game" else self._db.apps

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def boost_active(self) -> bool:
        return self._boost.is_active()

    @property
    def running_titles(self) -> list[str]:
        return [s.title for s in self._sessions]

    def is_running(self, kind: str, item_id: int) -> bool:
        return any(s.kind == kind and s.item_id == item_id for s in self._sessions)

    # ------------------------------------------------------------------ автопоиск
    def scan(self, manual: bool = False) -> None:
        if self._scanning:
            return
        self._scanning = True
        self.scan_state.emit(True)

        def job() -> ScanSummary:
            found = scan_all()
            return ScanSummary(
                games=merge_found(self._db.games, found.games, found.scanned_sources),
                apps=merge_found(self._db.apps, found.apps, found.scanned_sources),
                notes=found.notes, manual=manual)

        self._runner.submit(job, self._on_scan_done)

    def _on_scan_done(self, summary: ScanSummary | None, error: Exception | None) -> None:
        self._scanning = False
        self.scan_state.emit(False)
        if error is not None or summary is None:
            self.notice.emit("error", "Не удалось выполнить поиск игр и программ. Подробности — в nova.log.")
            return
        self._settings.set("last_scan", time.time())
        try:        # убираем найденное раньше, но теперь признанное лишним; закреплённое в острове остаётся
            keep = [item_id for kind, item_id in self.pinned() if kind == "app"]
            summary.pruned = prune_unwanted(self._db.apps, keep, os.environ.get("SystemRoot", ""))
        except Exception:
            log.exception("Не удалось почистить список программ")
        self.changed.emit()
        self.scan_done.emit(summary)

    # ------------------------------------------------------------------ ручное добавление
    @staticmethod
    def _resolve_target(path: str) -> tuple[str, str, str]:
        """Приводит выбранный путь к EXE: (путь, аргументы, текст ошибки или пустая строка)."""
        path = path.strip().strip('"')
        args = ""
        if path.lower().endswith(".lnk"):
            resolved = apps_scan.resolve_shortcut(path)
            if not resolved or not resolved[0]:
                return path, "", "Не удалось прочитать ярлык. Выберите сам файл программы (.exe)."
            path, args = resolved[0], resolved[1]
        if not path.lower().endswith(EXE_SUFFIXES):
            return path, "", "Нужен исполняемый файл программы (.exe)."
        if not ntpath.isabs(path) and not path.startswith("/"):
            return path, "", "Укажите полный путь к файлу."
        if not os.path.isfile(path):
            return path, "", f"Файл не найден: {path}"
        return path, args, ""

    def _add_or_find(self, kind: str, path: str, title: str = "") -> tuple[LibraryItem | None, str, bool]:
        """Возвращает (карточка, сообщение, создана_или_возвращена). Карточка None — путь не подошёл."""
        path, args, error = self._resolve_target(path)
        if error:
            return None, error, False
        table = self.table(kind)
        existing = table.find_by_exe(path)
        if existing is not None:
            if existing.hidden:
                table.set_hidden(existing.id, False)
                self.changed.emit()
                return table.get(existing.id), f"«{existing.title}» возвращена в библиотеку.", True
            return existing, f"«{existing.title}» уже есть в библиотеке.", False
        name = title.strip() or os.path.splitext(os.path.basename(path))[0].replace("_", " ").strip()
        new_id = table.add(title=name, exe_path=path, source="manual", install_dir=os.path.dirname(path), args=args,
                           category="" if kind == "game" else guess_app_category(name, path))
        self.changed.emit()
        return table.get(new_id), f"«{name}» добавлена.", True

    def add_manual(self, kind: str, path: str, title: str = "") -> tuple[bool, str]:
        """Добавляет EXE (или ярлык .lnk) в библиотеку. Возвращает (успех, сообщение для пользователя)."""
        _item, message, ok = self._add_or_find(kind, path, title)
        return ok, message

    # ------------------------------------------------------------------ вкладка «Приложения» в острове
    def pinned(self) -> list[tuple[str, int]]:
        """Закреплённые в острове карточки (только существующие и не убранные)."""
        refs: list[tuple[str, int]] = []
        for raw in self._settings.get(PIN_KEY):
            kind, _, ident = str(raw).partition(":")
            if kind in ("game", "app") and ident.isdigit():
                item = self.table(kind).get(int(ident))
                if item is not None and not item.hidden and (kind, int(ident)) not in refs:
                    refs.append((kind, int(ident)))
        return refs

    def _save_pins(self, refs: list[tuple[str, int]]) -> None:
        self._settings.set(PIN_KEY, [f"{k}:{i}" for k, i in refs[:MAX_PINS]])

    def set_pins(self, refs: list[tuple[str, int]]) -> bool:
        """Задаёт весь список закреплённого в острове (окно выбора). False — превышен предел."""
        unique = list(dict.fromkeys(refs))
        if len(unique) > MAX_PINS:
            return False
        self._save_pins(unique)
        return True

    def pin(self, kind: str, item_id: int) -> bool:
        """Закрепляет карточку в острове. False — уже закреплена или достигнут предел."""
        refs = self.pinned()
        if (kind, item_id) in refs or len(refs) >= MAX_PINS or self.table(kind).get(item_id) is None:
            return False
        self._save_pins([*refs, (kind, item_id)])
        return True

    def unpin(self, kind: str, item_id: int) -> None:
        self._save_pins([r for r in self.pinned() if r != (kind, item_id)])

    def move_pin(self, kind: str, item_id: int, delta: int) -> None:
        refs = self.pinned()
        if (kind, item_id) not in refs:
            return
        i = refs.index((kind, item_id))
        j = min(max(i + delta, 0), len(refs) - 1)
        refs.insert(j, refs.pop(i))
        self._save_pins(refs)

    def is_pinned(self, kind: str, item_id: int) -> bool:
        return (kind, item_id) in self.pinned()

    def add_to_island(self, path: str) -> tuple[bool, str]:
        """Перетаскивание EXE/ярлыка на остров или кнопка «Добавить EXE»: карточка попадает в библиотеку
        приложений (если её не было ни среди приложений, ни среди игр) и закрепляется в острове."""
        target, _args, error = self._resolve_target(path)
        if error:
            return False, error
        item: LibraryItem
        game = self._db.games.find_by_exe(target)
        if game is not None and not game.hidden:
            kind, item = "game", game
        else:
            found, message, _created = self._add_or_find("app", target)
            if found is None:
                return False, message
            kind, item = "app", found
        if self.is_pinned(kind, item.id):
            return False, f"«{item.title}» уже закреплена в острове."
        if not self.pin(kind, item.id):
            return False, f"В острове можно закрепить не больше {MAX_PINS} программ."
        return True, f"«{item.title}» добавлена в остров."

    # ------------------------------------------------------------------ правка и удаление из NOVA
    def update_item(self, kind: str, item_id: int, **fields: Any) -> None:
        table = self.table(kind)
        item = table.get(item_id)
        if item is None:
            return
        if "exe_path" in fields and fields["exe_path"] != item.exe_path and item.source in ("manual", "shortcut"):
            fields.setdefault("install_dir", os.path.dirname(fields["exe_path"]))
        table.update(item_id, **fields)
        self.changed.emit()

    def set_option(self, kind: str, item_id: int, key: str, value: Any) -> None:
        self.table(kind).set_option(item_id, key, value)
        self.changed.emit()

    def toggle_favorite(self, kind: str, item_id: int) -> None:
        item = self.table(kind).get(item_id)
        if item is not None:
            self.table(kind).update(item_id, favorite=not item.favorite)
            self.changed.emit()

    def remove(self, kind: str, item_id: int) -> str:
        """«Удалить из NOVA». Файлы на диске никогда не затрагиваются.
        Найденное автоматически скрывается (чтобы автопоиск не вернул его), добавленное вручную — удаляется."""
        table = self.table(kind)
        item = table.get(item_id)
        if item is None:
            return ""
        self.unpin(kind, item_id)
        if item.source == "manual":
            table.delete(item_id)
            text = f"«{item.title}» удалена из NOVA. Файлы на компьютере не тронуты."
        else:
            table.set_hidden(item_id, True)
            text = (f"«{item.title}» убрана из NOVA, файлы на компьютере не тронуты. "
                    "Автопоиск её больше не добавит; вернуть можно в разделе «Убранные».")
        self.changed.emit()
        return text

    def unhide(self, kind: str, item_id: int) -> None:
        self.table(kind).set_hidden(item_id, False)
        self.changed.emit()

    def forget(self, kind: str, item_id: int) -> None:
        """Окончательно стирает запись из базы NOVA (автопоиск сможет найти её заново)."""
        self.unpin(kind, item_id)
        self.table(kind).delete(item_id)
        self.changed.emit()

    # ------------------------------------------------------------------ настройки программы (реестр)
    def exe_tweak_state(self, item: LibraryItem) -> tuple[bool, bool] | None:
        """(высокопроизводительная видеокарта, отключены ли fullscreen-оптимизации) или None, если нет EXE."""
        if not item.exe_path:
            return None
        try:
            return (self._tweaks.gpu_preference(item.exe_path) == tweaks.GPU_HIGH_PERFORMANCE,
                    self._tweaks.fullscreen_optimizations_disabled(item.exe_path))
        except tweaks.TweakError:
            return None

    def apply_exe_tweaks(self, item: LibraryItem, gpu_high: bool, fso_off: bool) -> str:
        """Применяет настройки для EXE карточки. Возвращает текст ошибки или пустую строку."""
        if not item.exe_path:
            return "У карточки нет EXE — укажите его в «Изменить»."
        try:
            self._tweaks.set_gpu_preference(item.exe_path, tweaks.GPU_HIGH_PERFORMANCE if gpu_high else None)
            self._tweaks.set_fullscreen_optimizations_disabled(item.exe_path, fso_off)
        except tweaks.TweakError as exc:
            return str(exc)
        return ""

    # ------------------------------------------------------------------ запуск
    def boost_config(self) -> BoostConfig:
        s = self._settings
        return BoostConfig(power=bool(s.get("boost_power")), plan_guid=str(s.get("boost_plan")),
                           game_mode=bool(s.get("boost_game_mode")), game_dvr=bool(s.get("boost_game_dvr")))

    def boost_wanted(self, kind: str, item: LibraryItem) -> bool:
        """Включать ли режим производительности: явная настройка карточки важнее общей.
        По умолчанию — для игр да (если включено в настройках), для программ нет."""
        explicit = item.opts.get("boost")
        if isinstance(explicit, bool):
            return explicit
        if kind == "game":
            profile = find_game_profile(self._settings.get("game_profiles"), item.exe_path)
            if profile is not None:
                return bool(profile.get("boost", True))
            return bool(self._settings.get("boost_on_launch"))
        return False

    def effective_priority(self, kind: str, item: LibraryItem) -> str:
        base = str(self._settings.get("boost_priority")) if self.boost_wanted(kind, item) else "normal"
        if kind == "game":
            profile = find_game_profile(self._settings.get("game_profiles"), item.exe_path)
            if profile and profile.get("priority") in {"normal", "above_normal", "high"}:
                base = str(profile["priority"])
        return resolve_priority(base, str(item.opts.get("priority") or ""))

    def activate_boost_now(self) -> None:
        """Включает Game Boost вручную с тем же журналом отката, что используется при запуске игр."""
        if self._boost.is_active():
            self.notice.emit("info", "Game Boost уже активен.")
            self.boost_changed.emit(True)
            return
        self._runner.submit(lambda: self._boost.apply(self.boost_config(), "Game Boost (вручную)"),
                            self._on_manual_boost)

    def _on_manual_boost(self, report: BoostReport | None, error: Exception | None) -> None:
        if error is not None or report is None:
            self.notice.emit("error", "Не удалось включить Game Boost. Подробности — в nova.log.")
            self.boost_changed.emit(self._boost.is_active())
            return
        if report.failures:
            self.notice.emit("warning", "Game Boost включён не полностью: " + "; ".join(
                f"{step.label} — {step.detail}" for step in report.failures))
        elif report.changed:
            self.notice.emit("info", "Game Boost включён. Прежние настройки сохранены для восстановления.")
        self.boost_changed.emit(self._boost.is_active())

    def launch(self, kind: str, item_id: int) -> LaunchResult | None:
        table = self.table(kind)
        item = table.get(item_id)
        if item is None:
            return None
        wanted = self.boost_wanted(kind, item)
        result = self._launcher.launch(item, self.effective_priority(kind, item))
        if not result.ok:
            self.notice.emit("error", result.message)
            return result
        history_id = table.record_launch(item_id)
        spec = build_watch(item, result.pid)
        session = Session(kind, item_id, item.title, history_id, time.time(), spec)
        if spec.trackable:
            self._sessions.append(session)
            self._timer.start()
            self.session_started.emit(SessionInfo(kind, item_id, item.title))
            if wanted:
                self._start_boost(session)
        elif wanted:
            self.notice.emit("warning", f"Режим производительности не включён: NOVA не может определить, когда "
                                        f"«{item.title}» закроется, и не смогла бы вернуть настройки.")
        if result.message:
            self.notice.emit("info", result.message)
        self.changed.emit()
        return result

    # ------------------------------------------------------------------ режим производительности
    def _start_boost(self, session: Session) -> None:
        session.boost_pending = True
        cfg = self.boost_config()
        watch = {**session.spec.to_dict(), "kind": session.kind}
        self._runner.submit(lambda: self._boost.apply(cfg, session.title, watch),
                            lambda report, err: self._on_boost_applied(session, report, err))

    def _on_boost_applied(self, session: Session, report: BoostReport | None, error: Exception | None) -> None:
        session.boost_pending = False
        if error is not None or report is None:
            self.notice.emit("error", "Не удалось включить режим производительности (подробности в nova.log).")
            return
        if report.created_plan_guid:
            self._settings.set("boost_plan", report.created_plan_guid)
        session.boosted = report.changed or report.already_active
        if report.failures:
            self.notice.emit("warning", "Режим производительности включён не полностью: "
                             + "; ".join(f"{s.label} — {s.detail}" for s in report.failures))
        if report.changed:
            self._post_boost("Режим производительности", self._short(report))
        self.boost_changed.emit(self._boost.is_active())
        if session not in self._sessions:          # игра успела закрыться, пока включался режим
            self._maybe_restore()

    @staticmethod
    def _short(report: BoostReport) -> str:
        for step in report.steps:
            if step.ok and step.changed and step.label == "Электропитание":
                return step.detail.strip("«»")
        return next((s.label for s in report.steps if s.ok and s.changed), "")

    def _post_boost(self, title: str, text: str) -> None:
        if self._hub is not None:
            self._hub.post(Notification("boost", title, text, duration_ms=3500))

    def _maybe_restore(self) -> None:
        if any(s.boosted or s.boost_pending for s in self._sessions) or not self._boost.is_active():
            return
        self._runner.submit(self._boost.restore, self._on_restored)

    def _on_restored(self, report: BoostReport | None, error: Exception | None) -> None:
        self.boost_changed.emit(self._boost.is_active())
        if error is not None or report is None:
            self.notice.emit("error", "Не удалось вернуть настройки после игры (подробности в nova.log).")
            return
        if report.failures:
            self.notice.emit("warning", "Не всё удалось вернуть: " + "; ".join(
                f"{s.label} — {s.detail}" for s in report.failures) + " NOVA попробует ещё раз при следующем запуске.")
        elif report.changed:
            detail = next((s.detail for s in report.steps if s.label == "Электропитание" and s.ok), "")
            self._post_boost("Режим производительности выключен", detail.strip("«»").replace("возвращена ", ""))

    def stop_boost_now(self) -> None:
        """Кнопка «Выключить сейчас»: возвращает прежние настройки, игра остаётся запущенной."""
        for s in self._sessions:
            s.boosted = s.boost_pending = False
        if self._boost.is_active():
            self._runner.submit(self._boost.restore, self._on_restored)

    def recover(self) -> None:
        """Вызывается при старте: если прошлый запуск завершился в режиме производительности, либо
        продолжаем следить за ещё запущенной игрой, либо возвращаем прежние настройки."""
        state = self._boost.pending()
        if not state:
            return
        self._runner.submit(snapshot_processes, lambda procs, err: self._on_recover(state, procs or []))

    def _on_recover(self, state: dict[str, Any], processes: list[tuple[int, str, str]]) -> None:
        watch = state.get("watch") or {}
        spec = WatchSpec.from_dict(watch)
        if spec.trackable and find_matches(spec, processes):
            title = str(state.get("label") or "Игра")
            self._sessions.append(Session(str(watch.get("kind") or "game"), 0, title, 0, time.time(), spec,
                                          boosted=True, seen_running=True))
            self._timer.start()
            self.boost_changed.emit(True)
            self.notice.emit("info", f"«{title}» всё ещё запущена — режим производительности продолжается.")
        else:
            self._runner.submit(self._boost.restore, self._on_restored)

    def shutdown(self) -> None:
        """Выход из NOVA: если режим производительности включён, прежние настройки возвращаются сразу —
        иначе после закрытия NOVA схема питания осталась бы изменённой."""
        self._timer.stop()
        try:
            if self._boost.is_active():
                self._boost.restore()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось вернуть настройки при выходе")

    # ------------------------------------------------------------------ отслеживание процессов
    def _poll(self) -> None:
        if self._polling:
            return
        if not self._sessions:
            self._timer.stop()
            return
        self._polling = True
        self._runner.submit(snapshot_processes, self._on_snapshot)

    def _on_snapshot(self, processes: list[tuple[int, str, str]] | None, error: Exception | None) -> None:
        self._polling = False
        if error is not None or processes is None:
            return
        now = time.time()
        finished: list[tuple[Session, str]] = []
        for session in list(self._sessions):
            hits = find_matches(session.spec, processes)
            session.last_pids = hits
            done, reason = evaluate(session, bool(hits), now)
            if done:
                finished.append((session, reason))
        for session, reason in finished:
            self._sessions.remove(session)
            seconds = max(0.0, now - session.started) if reason == "exit" else 0.0
            if session.history_id:
                self.table(session.kind).finish_launch(session.history_id, session.started + seconds)
            if reason == "never_seen" and session.boosted:
                self.notice.emit("warning", f"NOVA не увидела запущенный процесс «{session.title}» и выключила режим "
                                            "производительности. Укажите EXE игры в «Изменить», чтобы NOVA могла за ней следить.")
            self.session_ended.emit(SessionInfo(session.kind, session.item_id, session.title, seconds,
                                                session.boosted, reason))
        if finished:
            self._maybe_restore()
            self.changed.emit()
        if not self._sessions:
            self._timer.stop()
