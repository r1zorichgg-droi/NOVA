"""Проверка оркестрации LibraryManager: запуск → режим производительности → слежение → выход → возврат настроек.

Фоновые задачи подменены синхронным «бегуном», процессы — списком, который тест меняет сам.
Без PySide6 используется заглушка (см. qt_stub.py): проверяется Python-логика, а не отрисовка.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import qt_stub  # noqa: E402

if qt_stub.real_qt_available():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
else:
    qt_stub.install()

from PySide6.QtWidgets import QApplication  # noqa: E402

from database.database import Database  # noqa: E402
from launcher import library_manager as lm  # noqa: E402
from launcher.boost import BoostReport, BoostStep  # noqa: E402
from launcher.launch import LaunchResult  # noqa: E402
from launcher.library_manager import LibraryManager  # noqa: E402
from settings import AppSettings  # noqa: E402


class SyncRunner:
    """Выполняет задачу сразу и тут же вызывает callback."""

    def submit(self, fn, callback) -> None:  # noqa: ANN001
        try:
            result, error = fn(), None
        except Exception as exc:  # noqa: BLE001
            result, error = None, exc
        callback(result, error)


class FakeLauncher:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.result = LaunchResult(True, "", 4242, "exe")

    def launch(self, item, priority: str) -> LaunchResult:  # noqa: ANN001
        self.calls.append((item.title, priority))
        return self.result


class FakeBoost:
    def __init__(self) -> None:
        self.active = False
        self.applied: list[tuple[str, dict[str, Any]]] = []
        self.restores = 0

    def apply(self, cfg, label: str, watch: dict[str, Any]) -> BoostReport:  # noqa: ANN001
        self.active = True
        self.applied.append((label, watch))
        return BoostReport([BoostStep("Электропитание", True, "включена «Высокая»", changed=True)])

    def restore(self) -> BoostReport:
        self.restores += 1
        self.active = False
        return BoostReport([BoostStep("Электропитание", True, "возвращена «Сбалансированная»", changed=True)])

    def is_active(self) -> bool:
        return self.active

    def pending(self) -> dict[str, Any]:
        return {}


class FakeHub:
    def __init__(self) -> None:
        self.posted: list[Any] = []

    def post(self, notification) -> None:  # noqa: ANN001
        self.posted.append(notification)


class LibraryManagerFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.settings = AppSettings(self.db)
        self.launcher, self.boost, self.hub = FakeLauncher(), FakeBoost(), FakeHub()
        self.manager = LibraryManager(self.db, self.settings, self.hub, self.boost, object(), launcher=self.launcher)
        self.manager._runner = SyncRunner()
        self.processes: list[tuple[int, str, str]] = []
        self._orig_snapshot = lm.snapshot_processes
        lm.snapshot_processes = lambda: list(self.processes)
        self.notices: list[tuple[str, str]] = []
        self.manager.notice.connect(lambda level, text: self.notices.append((level, text)))
        self.ended: list[Any] = []
        self.manager.session_ended.connect(self.ended.append)
        self.game_id = self.db.games.add(title="Игра", source="manual", exe_path=r"C:\Games\Hero\hero.exe",
                                         install_dir=r"C:\Games\Hero")
        self.app_id = self.db.apps.add(title="Блокнот", source="manual", exe_path=r"C:\Tools\notes.exe")

    def tearDown(self) -> None:
        lm.snapshot_processes = self._orig_snapshot
        self.db.close()
        self.tmp.cleanup()

    def _poll(self) -> None:
        self.manager._polling = False
        self.manager._poll()

    def test_game_launch_boosts_then_restores_after_exit(self) -> None:
        self.settings.set("boost_on_launch", True)
        result = self.manager.launch("game", self.game_id)
        self.assertTrue(result and result.ok)
        self.assertEqual(self.launcher.calls[0][0], "Игра")
        self.assertTrue(self.boost.active, "режим производительности должен включиться при запуске игры")
        self.assertTrue(self.manager.is_running("game", self.game_id))
        self.assertEqual(self.db.games.get(self.game_id).play_count, 1)

        self.processes = [(4242, "hero.exe", r"C:\Games\Hero\hero.exe")]
        self._poll()
        self.assertTrue(self.boost.active)
        self.assertFalse(self.ended)

        self.processes = []                  # игра закрылась; нужно два опроса подряд, чтобы не принять мигание за выход
        self._poll()
        self.assertTrue(self.boost.active)
        self._poll()
        self.assertFalse(self.boost.active, "после выхода из игры настройки должны вернуться")
        self.assertEqual(self.boost.restores, 1)
        self.assertEqual(len(self.ended), 1)
        self.assertEqual(self.ended[0].reason, "exit")
        self.assertFalse(self.manager.is_running("game", self.game_id))

    def test_boost_off_for_apps_by_default_and_when_disabled(self) -> None:
        self.manager.launch("app", self.app_id)
        self.assertFalse(self.boost.active, "программы по умолчанию запускаются без режима производительности")
        self.settings.set("boost_on_launch", False)
        self.manager.launch("game", self.game_id)
        self.assertFalse(self.boost.active)

    def test_item_option_overrides_global_setting(self) -> None:
        self.settings.set("boost_on_launch", True)
        self.manager.set_option("game", self.game_id, "boost", False)
        self.manager.launch("game", self.game_id)
        self.assertFalse(self.boost.active)
        self.manager.set_option("app", self.app_id, "boost", True)
        self.manager.launch("app", self.app_id)
        self.assertTrue(self.boost.active)

    def test_failed_launch_changes_nothing(self) -> None:
        self.launcher.result = LaunchResult(False, "Файл не найден")
        result = self.manager.launch("game", self.game_id)
        self.assertFalse(result.ok)
        self.assertFalse(self.boost.active)
        self.assertEqual(self.db.games.get(self.game_id).play_count, 0)
        self.assertIn(("error", "Файл не найден"), self.notices)
        self.assertFalse(self.manager.is_running("game", self.game_id))

    def test_boost_is_kept_while_another_game_runs(self) -> None:
        other = self.db.games.add(title="Вторая", source="manual", exe_path=r"C:\Games\Two\two.exe",
                                  install_dir=r"C:\Games\Two")
        self.manager.launch("game", self.game_id)
        self.launcher.result = LaunchResult(True, "", 5151, "exe")
        self.manager.launch("game", other)
        self.processes = [(4242, "hero.exe", r"C:\Games\Hero\hero.exe"), (5151, "two.exe", r"C:\Games\Two\two.exe")]
        self._poll()
        self.processes = [(5151, "two.exe", r"C:\Games\Two\two.exe")]    # первая игра закрылась
        self._poll()
        self._poll()
        self.assertEqual(len(self.ended), 1)
        self.assertTrue(self.boost.active, "пока идёт вторая игра, режим должен оставаться включённым")
        self.processes = []
        self._poll()
        self._poll()
        self.assertFalse(self.boost.active)
        self.assertEqual(self.boost.restores, 1)

    def test_stop_boost_now_keeps_game_running(self) -> None:
        self.manager.launch("game", self.game_id)
        self.assertTrue(self.boost.active)
        self.manager.stop_boost_now()
        self.assertFalse(self.boost.active)
        self.assertTrue(self.manager.is_running("game", self.game_id))
        self.processes = []
        self._poll()
        self._poll()                          # игра закрылась, но режим уже выключен — повторного возврата быть не должно
        self.assertEqual(self.boost.restores, 1)

    def test_shutdown_restores_boost(self) -> None:
        self.manager.launch("game", self.game_id)
        self.assertTrue(self.boost.active)
        self.manager.shutdown()
        self.assertFalse(self.boost.active)

    def test_unremovable_game_untracked_does_not_boost(self) -> None:
        """Если NOVA не может понять, когда игра закроется, режим не включается (иначе не вернуть настройки)."""
        item = self.db.games.add(title="Через лаунчер", source="manual")      # ни EXE, ни папки
        self.launcher.result = LaunchResult(True, "", 0, "uri")
        self.manager.launch("game", item)
        self.assertFalse(self.boost.active)
        self.assertTrue(any(level == "warning" for level, _ in self.notices))

    def test_pins_are_limited_and_survive_removal(self) -> None:
        ids = [self.db.apps.add(title=f"A{i}", source="manual", exe_path=rf"C:\T\a{i}.exe") for i in range(lm.MAX_PINS + 1)]
        for i in ids[:lm.MAX_PINS]:
            self.assertTrue(self.manager.pin("app", i))
        self.assertFalse(self.manager.pin("app", ids[-1]), "больше десяти плиток закрепить нельзя")
        self.manager.move_pin("app", ids[0], 3)
        self.assertEqual(self.manager.pinned()[3], ("app", ids[0]))
        self.manager.remove("app", ids[1])
        self.assertNotIn(("app", ids[1]), self.manager.pinned())
        self.assertEqual(len(self.manager.pinned()), lm.MAX_PINS - 1)

    def test_remove_never_touches_files(self) -> None:
        exe = Path(self.tmp.name) / "keep.exe"
        exe.write_bytes(b"MZ")
        item = self.db.apps.add(title="Keep", source="manual", exe_path=str(exe))
        text = self.manager.remove("app", item)
        self.assertTrue(exe.exists())
        self.assertIn("не тронуты", text)
        self.assertIsNone(self.db.apps.get(item))


if __name__ == "__main__":
    unittest.main()
