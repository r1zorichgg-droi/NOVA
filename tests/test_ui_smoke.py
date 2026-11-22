"""Дымовой тест интерфейса: создаёт сервисы и главное окно так же, как main.py, и обходит все страницы.

С настоящим PySide6 тест работает в «offscreen»-режиме (окно не показывается на экране).
Без PySide6 (например, в CI на Linux без Qt) подставляется заглушка из qt_stub.py: она проверяет
только Python-логику (имена, аргументы, привязки настроек), но не отрисовку.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT), str(Path(__file__).resolve().parent)):
    if p not in sys.path:
        sys.path.insert(0, p)

import qt_stub  # noqa: E402

REAL_QT = qt_stub.real_qt_available()
if REAL_QT:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
else:
    qt_stub.install()

from PySide6.QtWidgets import QApplication  # noqa: E402

from app_context import AppContext  # noqa: E402
from database.database import Database, StateStore  # noqa: E402
from island.game_detector import GameDetector  # noqa: E402
from island.island_window import IslandWindow  # noqa: E402
from island.music_controller import MusicController  # noqa: E402
from island.notifications import NotificationHub, VolumeWatcher  # noqa: E402
from launcher.boost import BoostManager  # noqa: E402
from launcher.library_manager import LibraryManager  # noqa: E402
from settings import AppSettings  # noqa: E402
from system.power import PowerManager  # noqa: E402
from system.sampler import Sampler  # noqa: E402
from system.tweaks import TweakManager  # noqa: E402
from ui.icon_cache import IconCache  # noqa: E402
from ui.main_window import MainWindow  # noqa: E402
from ui.style import apply_theme  # noqa: E402


class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db = Database(Path(cls.tmp.name) / "nova_test.db")
        cls.settings = AppSettings(cls.db)
        apply_theme(cls.app, cls.settings)
        cls.sampler = Sampler()
        cls.music = MusicController()
        cls.music.set_active(False)
        cls.island = IslandWindow(cls.settings, cls.sampler, cls.music)
        hub = NotificationHub(cls.settings)
        state = StateStore(cls.db)
        power = PowerManager()
        tweak_manager = TweakManager(state)
        boost = BoostManager(power, tweak_manager, state)
        cls.library = LibraryManager(cls.db, cls.settings, hub, boost, tweak_manager)
        icons = IconCache()
        cls.island.attach_launcher(cls.library, icons)
        cls.ctx = AppContext(db=cls.db, settings=cls.settings, sampler=cls.sampler, music=cls.music,
                             island=cls.island, hub=hub, detector=GameDetector(cls.settings), volume=VolumeWatcher(),
                             state=state, power=power, tweaks=tweak_manager, boost=boost, library=cls.library,
                             icons=icons)
        cls.window = MainWindow(cls.ctx)

    def setUp(self) -> None:
        for table in (self.db.games, self.db.apps):      # каждый тест начинает с пустой библиотеки
            for item in table.items(include_hidden=True):
                table.delete(item.id)
        self.settings.set("island_launch_items", [])

    @classmethod
    def tearDownClass(cls) -> None:
        cls.db.close()
        cls.tmp.cleanup()

    def test_all_pages_open_and_take_metrics(self) -> None:
        snap = self.sampler.collector.collect()
        self.sampler.last = snap
        self.assertEqual(len(self.window.pages), 16)
        for index in range(len(self.window.pages)):
            self.window.show_page(index)
            page = self.window.current_page()
            if page.needs_samples:
                page.on_data(snap)
        if REAL_QT:
            self.assertEqual(self.window.stack.currentIndex(), 13)

    def test_install_catalog_contains_popular_launchers_and_music_services(self) -> None:
        from ui.install_page import INSTALLERS

        by_name = {item.name: item for item in INSTALLERS}
        for name in ("Steam", "Epic Games Launcher", "Rockstar Games Launcher", "Spotify",
                     "SoundCloud", "Google Chrome", "Mozilla Firefox", "Opera GX", "Brave",
                     "Discord", "Telegram Desktop", "WinRAR", "OBS Studio", "Visual Studio Code",
                     "Prism Launcher", "Minecraft Launcher — Windows Legacy (Mojang)"):
            self.assertIn(name, by_name)
        self.assertEqual(len(INSTALLERS), 16)
        self.assertEqual(len({item.category for item in INSTALLERS}), 5)

        rockstar = by_name["Rockstar Games Launcher"]
        self.assertEqual(rockstar.mode, "download")
        self.assertEqual(rockstar.url, "https://gamedownloads.rockstargames.com/public/installer/Rockstar-Games-Launcher.exe")
        self.assertEqual(rockstar.filename, "Rockstar-Games-Launcher.exe")
        self.assertEqual(rockstar.fallback_url, "https://socialclub.rockstargames.com/rockstar-games-launcher")

        prism = by_name["Prism Launcher"]
        self.assertEqual(prism.mode, "download")
        self.assertIn("PrismLauncher/PrismLauncher/releases/download/11.1.1/", prism.url)
        self.assertIn("лиценз", prism.note.casefold())
        self.assertEqual(prism.category, "Игровые платформы")
        self.assertTrue(prism.fallback_url.startswith("https://prismlauncher.org/"))

        soundcloud = by_name["SoundCloud"]
        self.assertEqual(soundcloud.mode, "pwa")
        self.assertEqual(soundcloud.url, "https://soundcloud.com")
        self.assertIn("Windows", soundcloud.note)

        legacy = by_name["Minecraft Launcher — Windows Legacy (Mojang)"]
        self.assertEqual(legacy.mode, "download")
        self.assertEqual(legacy.kind, "msi")
        self.assertIn("launcher.mojang.com", legacy.url)
        self.assertEqual(legacy.category, "Игровые платформы")

        self.assertNotIn("TLauncher", by_name)
        self.assertNotIn("Legacy Launcher", by_name)  # third-party account/license-bypass clients are not bundled
        self.assertNotIn("7-Zip", by_name)
        self.assertNotIn("Microsoft PowerToys", by_name)
        self.assertNotIn("Playnite", by_name)
        self.assertNotIn("Everything", by_name)
        for removed in ("Battle.net", "Ubisoft Connect", "EA app", "GOG GALAXY", "Riot Client",
                        "Xbox app", "Minecraft Launcher", "itch.io", "Microsoft Edge", "Vivaldi",
                        "Яндекс Браузер", "WhatsApp", "Zoom Workplace", "Microsoft Teams", "Signal",
                        "VLC media player", "ShareX"):
            self.assertNotIn(removed, by_name)
        for item in INSTALLERS:
            if item.mode in {"download", "pwa"}:
                self.assertTrue(item.url.startswith("https://"), item.name)
                if item.mode == "download":
                    self.assertTrue(item.filename, item.name)
            else:
                self.assertTrue(item.package_id, f"У {item.name} должен быть package_id")
                self.assertIn(item.mode, {"winget_download", "winget_install"})

    def test_fps_overlay_has_drag_lock_api(self) -> None:
        from ui.fps_panel import FpsOverlay

        self.assertTrue(callable(getattr(FpsOverlay, "set_movable", None)))
        self.assertTrue(callable(getattr(FpsOverlay, "mousePressEvent", None)))
        self.assertTrue(callable(getattr(FpsOverlay, "mouseMoveEvent", None)))
        self.assertTrue(callable(getattr(FpsOverlay, "mouseReleaseEvent", None)))
        overlay = FpsOverlay()
        self.assertFalse(overlay.movable, "оверлей должен запускаться заблокированным")
        overlay.set_movable(True)
        self.assertTrue(overlay.movable, "разблокировка должна включать перетаскивание")
        overlay.set_movable(False)
        self.assertFalse(overlay.movable, "повторная блокировка должна выключать перетаскивание")
        overlay.close()

    def test_games_page_has_embedded_fps_panel(self) -> None:
        from ui.library_page import LibraryPage
        from ui.fps_panel import FpsPanel

        games_page = next(page for page in self.window.pages
                          if isinstance(page, LibraryPage) and page._kind == "game")
        self.assertIsInstance(games_page.fps_panel, FpsPanel)

    def test_new_games_widgets_are_present(self) -> None:
        from ui.library_page import LibraryPage
        from ui.game_profiles import GameBoostWidget, GameProfilesWidget

        games_page = next(page for page in self.window.pages
                          if isinstance(page, LibraryPage) and page._kind == "game")
        self.assertIsInstance(games_page.game_boost, GameBoostWidget)
        self.assertIsInstance(games_page.game_profiles, GameProfilesWidget)
        self.assertEqual(self.settings.get("game_profiles"), [])

    def test_network_and_update_pages_are_registered(self) -> None:
        from ui.network_page import NetworkPage
        from ui.update_page import UpdatePage

        self.assertTrue(any(isinstance(page, NetworkPage) for page in self.window.pages))
        self.assertTrue(any(isinstance(page, UpdatePage) for page in self.window.pages))

    def test_fps_presentmon_csv_parser(self) -> None:
        from system.fps_monitor import parse_frametime_csv_row

        header = ["Application", "ProcessID", "MsBetweenPresents", "Dropped"]
        self.assertAlmostEqual(parse_frametime_csv_row(header, ["game.exe", "123", "16.67", "0"]), 16.67)
        self.assertIsNone(parse_frametime_csv_row(header, ["game.exe", "123", "0", "0"]))
        self.assertIsNone(parse_frametime_csv_row(header, ["game.exe", "123"]))
        self.assertIsNone(parse_frametime_csv_row(["Application", "ProcessID"], ["game.exe", "123"]))

    def test_theme_and_accent_switch(self) -> None:
        self.settings.set("theme", "light")
        self.settings.set("accent", "#10B981")
        self.settings.set("theme", "dark")
        self.assertEqual(self.settings.get("theme"), "dark")

    def test_island_page_controls_write_settings(self) -> None:
        from ui.island_page import IslandPage
        page = next(p for p in self.window.pages if isinstance(p, IslandPage))
        self.settings.set("island_metrics", ["cpu", "gpu", "ram"])
        if REAL_QT:   # настоящая кнопка-«чип» должна записать выбор в настройки
            page._metric_buttons["gpu"].setChecked(False)
            self.assertEqual(self.settings.get("island_metrics"), ["cpu", "ram"])
        else:
            self.assertEqual(self.settings.get("island_metrics"), ["cpu", "gpu", "ram"])

    def test_library_pages_follow_database_changes(self) -> None:
        from ui.library_page import LibraryPage
        games, apps = [p for p in self.window.pages if isinstance(p, LibraryPage)]
        item_id = self.db.apps.add(title="Тестовая программа", source="manual", exe_path=r"C:\Tools\tool.exe")
        self.library.changed.emit()
        apps.refresh()
        games.refresh()
        self.assertEqual(self.db.apps.count(), 1)
        self.assertEqual(self.db.games.count(), 0)
        self.assertTrue(self.library.pin("app", item_id))
        self.assertEqual(self.settings.get("island_launch_items"), [f"app:{item_id}"])
        self.assertFalse(self.library.pin("app", item_id), "повторное закрепление не должно дублировать плитку")
        self.library.unpin("app", item_id)
        self.assertEqual(self.settings.get("island_launch_items"), [])

    def test_optimize_and_power_pages_are_safe_without_windows(self) -> None:
        from ui.optimize_page import OptimizePage
        from ui.power_page import PowerPage
        kinds = {type(p) for p in self.window.pages}
        self.assertIn(OptimizePage, kinds)
        self.assertIn(PowerPage, kinds)
        optimize_page = next(page for page in self.window.pages if isinstance(page, OptimizePage))
        self.assertTrue(callable(getattr(optimize_page, "_scan_shader_cache", None)))
        self.assertTrue(hasattr(optimize_page, "btn_scan_shaders"))
        self.assertTrue(hasattr(optimize_page, "btn_clean_shaders"))
        if not sys.platform.startswith("win"):
            self.assertFalse(self.ctx.power.available)
            self.assertFalse(self.ctx.tweaks.available)

    def test_debloat_page_selection_rules(self) -> None:
        """Выпиление: что показывается, что отмечается кнопками и что реально попадёт в удаление."""
        from system import programs as pr
        from system.uninstall import RunResult
        from ui.debloat_page import DebloatPage

        class Runner:
            def run_command(self, command, elevate, visible, timeout_s):  # noqa: ANN001
                return RunResult(False, error="не должно вызываться при выборе")

            def run_powershell(self, script, elevate, timeout_s):  # noqa: ANN001
                return RunResult(False, error="нет")

        def prog(key, name, **kw):  # noqa: ANN001, ANN003
            return pr.Program(key=key, kind=pr.KIND_WIN32, name=name, uninstall="u.exe", **kw)

        items = [prog("a", "Discord"), prog("b", "Telegram"), prog("c", "Microsoft Visual C++ Redistributable", risk="careful"),
                 prog("d", "Internal", risk="system"), prog("e", "Locked", removable=False, why_not="нельзя"),
                 pr.Program(key="f_1_x__z", kind=pr.KIND_APPX, name="Новости", microsoft=True, package_full_name="f_1_x__z")]
        page = DebloatPage(self.ctx, runner=Runner(), reader=lambda: pr.ProgramList(items), available=True)
        page._on_loaded(pr.ProgramList(items), None)
        keys = lambda: sorted(p.key for p in page.selected_programs())  # noqa: E731
        self.assertEqual(sorted(page._visible), ["a", "b", "c", "f_1_x__z"], "системное и защищённое скрыто по умолчанию")

        page._check_safe()
        self.assertEqual(keys(), ["a", "b", "f_1_x__z"], "«всё безопасное» не трогает «Осторожно»")
        page._check_all()
        self.assertEqual(keys(), ["a", "b", "c", "f_1_x__z"])

        page._on_group("appx")                       # скрытое фильтром не удаляется, даже если было отмечено
        self.assertEqual(keys(), ["f_1_x__z"])
        page._on_query("нет такого")
        self.assertEqual(keys(), [])
        page._on_query("")
        page._on_group("all")
        self.assertEqual(keys(), ["a", "b", "c", "f_1_x__z"], "отметки сохраняются при смене фильтра")

        page._on_show_hidden(True)
        self.assertIn("d", page._visible)
        self.assertIn("e", page._visible)
        page._check_all()
        self.assertEqual(keys(), ["a", "b", "c", "d", "f_1_x__z"], "защищённое Windows отметить нельзя никогда")
        page._set_checks({"e", "a"})                    # даже прямой вызов не отметит защищённое
        self.assertEqual(page._checked, {"a"})
        page._clear_checks()
        self.assertEqual(keys(), [])

    def test_debloat_page_keeps_checks_after_refresh_and_handles_errors(self) -> None:
        from system import programs as pr
        from ui.debloat_page import DebloatPage
        items = [pr.Program(key="a", kind=pr.KIND_WIN32, name="Discord", uninstall="u.exe")]
        page = DebloatPage(self.ctx, reader=lambda: pr.ProgramList(items), available=True)
        page._on_loaded(pr.ProgramList(items), None)
        page._check_all()
        page._on_loaded(pr.ProgramList(items), None)
        self.assertEqual([p.key for p in page.selected_programs()], ["a"])
        page._on_loaded(pr.ProgramList([]), None)
        self.assertEqual(page.selected_programs(), [])
        page._on_loaded(None, RuntimeError("boom"))                 # ошибка чтения не роняет страницу

    def test_debloat_page_is_inert_without_windows(self) -> None:
        from ui.debloat_page import DebloatPage
        page = DebloatPage(self.ctx, available=False)
        page._reload()
        self.assertFalse(page._busy)
        self.assertEqual(page.selected_programs(), [])

    def test_debloat_progress_dialog_cannot_be_closed_midway(self) -> None:
        from system import programs as pr
        from system import uninstall as un
        from ui.debloat_dialogs import UninstallProgressDialog
        stops: list[int] = []
        dialog = UninstallProgressDialog(2)
        dialog.stop_requested.connect(lambda: stops.append(1))
        dialog.reject()                       # Esc во время работы = «остановить», а не бросить на полпути
        dialog.request_stop()
        self.assertEqual(stops, [1], "сигнал остановки отправляется один раз")
        self.assertTrue(dialog._running)
        done = un.UninstallResult(pr.Program(key="a", kind=pr.KIND_WIN32, name="A"), un.STATUS_REMOVED, "Удалено.")
        dialog.on_result(done)
        dialog.finish([done])
        self.assertFalse(dialog._running)

    def test_game_detail_view_and_dialogs_build(self) -> None:
        """Страница игры и окна карточки собираются без ошибок (окна не показываются)."""
        from ui.item_dialogs import CloseAppsDialog, EditItemDialog, HiddenDialog, OptimizeDialog, PinPickerDialog
        from ui.library_page import LibraryPage
        games = next(p for p in self.window.pages if isinstance(p, LibraryPage))
        game_id = self.db.games.add(title="Тестовая игра", source="steam", category="Экшен",
                                    exe_path=r"C:\Games\Hero\hero.exe", install_dir=r"C:\Games\Hero")
        self.db.games.record_launch(game_id)
        games.refresh()
        games._on_action("open", "game", game_id)
        games._show_detail(game_id, keep_scroll=True)
        item = self.db.games.get(game_id)
        OptimizeDialog(self.library, "game", item, self.ctx.icons)
        EditItemDialog(self.library, "game", item)
        HiddenDialog(self.library, "game")
        CloseAppsDialog([])
        PinPickerDialog(self.library, self.ctx.icons)
        games._on_action("favorite", "game", game_id)
        self.assertTrue(self.db.games.get(game_id).favorite)
        games._on_action("pin", "game", game_id)
        self.assertTrue(self.library.is_pinned("game", game_id))
        games._on_action("pin", "game", game_id)
        self.assertFalse(self.library.is_pinned("game", game_id))
        games._show_list()

    def test_power_page_with_fake_powercfg(self) -> None:
        """Ветка для Windows: список схем, строки, диалог добавления (powercfg подменён)."""
        import dataclasses

        from system.power import PowerManager
        from test_system import FakePowercfg
        from ui.power_page import NewPlanDialog, PowerPage

        class WinPower(PowerManager):
            @property
            def available(self) -> bool:
                return True

        power = WinPower(runner=FakePowercfg())
        page = PowerPage(dataclasses.replace(self.ctx, power=power))
        plans = power.plans()
        self.assertGreaterEqual(len(plans), 3)
        page._on_plans(plans, None)
        page._on_plans(None, RuntimeError("boom"))
        dialog = NewPlanDialog(plans)
        if REAL_QT:
            name, base = dialog.result_values()
            self.assertTrue(name)
            self.assertEqual(len(base), 36)
        page._refresh_boost()

    def test_optimize_page_tweak_switches_restore_original(self) -> None:
        import dataclasses

        from system.tweaks import TweakManager
        from test_system import FakeSettings, MemoryRegistry
        from ui.optimize_page import OptimizePage
        from system import cleanup

        manager = TweakManager(FakeSettings(), MemoryRegistry())
        page = OptimizePage(dataclasses.replace(self.ctx, tweaks=manager))
        page._on_tweak("game_mode", True)
        self.assertTrue(manager.is_on("game_mode"))
        page._on_tweak("game_mode", False)
        self.assertFalse(manager.is_on("game_mode"), "выключатель должен вернуть прежнее состояние")
        page._on_scanned(cleanup.TempScan(Path(self.tmp.name), [Path(self.tmp.name) / "a.tmp"], 2048), None)
        page._on_scanned(cleanup.TempScan(Path(self.tmp.name)), None)
        page._on_cleaned(cleanup.CleanResult(deleted=3, freed_bytes=4096, failed=1), None)
        page._on_scanned(None, RuntimeError("x"))


if __name__ == "__main__":
    unittest.main()
