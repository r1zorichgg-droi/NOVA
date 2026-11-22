"""Тесты автопоиска, слияния с библиотекой, запуска, режима производительности и отслеживания сессий."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.database import Database, StateStore  # noqa: E402
from database.models import LibraryItem  # noqa: E402
from launcher import apps, boost, categories, epic, exefind, session, steam  # noqa: E402
from launcher.found import Found  # noqa: E402
from launcher.launch import Launcher, resolve_priority  # noqa: E402
from launcher.merge import merge_found  # noqa: E402
from launcher.scanner import promote_minecraft  # noqa: E402
from launcher.vdf import parse_vdf  # noqa: E402
from system import power, tweaks  # noqa: E402
from tests.test_system import FakePowercfg, MemoryRegistry  # noqa: E402

LIBRARY_VDF = r'''
"libraryfolders"
{
    "0"
    {
        "path"      "C:\\Program Files (x86)\\Steam"
        "label"     ""
        "apps"      { "228980" "123" "730" "999" }
    }
    "1"
    {
        "path"      "D:\\SteamLibrary"
    }
    "contentstatsid"  "1234"
}
'''
OLD_LIBRARY_VDF = r'''
"LibraryFolders"
{
    "TimeNextStatsReport"   "1"
    "1"     "E:\\Games\\Steam"
}
'''


def manifest(appid: str, name: str, installdir: str, flags: int = 4) -> str:
    return f'''"AppState"
{{
    "appid"     "{appid}"
    "Universe"  "1"
    "name"      "{name}"
    "StateFlags"        "{flags}"
    "installdir"        "{installdir}"
}}'''


class VdfTests(unittest.TestCase):
    def test_nested_and_escaped_paths(self):
        data = parse_vdf(LIBRARY_VDF)
        self.assertEqual(data["libraryfolders"]["1"]["path"], "D:\\SteamLibrary")
        self.assertEqual(data["libraryfolders"]["0"]["apps"]["730"], "999")

    def test_comments_and_urls_inside_strings(self):
        data = parse_vdf('"a" { // комментарий\n "url" "http://x.y//z" }')
        self.assertEqual(data["a"]["url"], "http://x.y//z")

    def test_broken_file_raises_value_error(self):
        with self.assertRaises(ValueError):
            parse_vdf('"a" { "b" }')

    def test_library_folders_both_formats(self):
        self.assertEqual(steam.parse_library_folders(LIBRARY_VDF), ["C:\\Program Files (x86)\\Steam", "D:\\SteamLibrary"])
        self.assertEqual(steam.parse_library_folders(OLD_LIBRARY_VDF), ["E:\\Games\\Steam"])
        self.assertEqual(steam.parse_library_folders("мусор {"), [])


class SteamTests(unittest.TestCase):
    def test_manifest_filters(self):
        self.assertEqual(steam.parse_manifest(manifest("730", "Counter-Strike 2", "CS2"))["name"], "Counter-Strike 2")
        self.assertIsNone(steam.parse_manifest(manifest("730", "Game", "G", flags=1026)))          # скачивается
        self.assertIsNone(steam.parse_manifest(manifest("228980", "Steamworks Common Redistributables", "x")))
        self.assertIsNone(steam.parse_manifest(manifest("1", "Proton 9.0", "Proton 9.0")))
        self.assertIsNone(steam.parse_manifest("не vdf"))

    def test_scan_two_libraries_with_cover_and_exe(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Steam"
            lib2 = Path(tmp) / "Lib2"
            for lib, appid, name, folder in ((root, "10", "Alpha Game", "Alpha"), (lib2, "20", "Beta", "Beta")):
                (lib / "steamapps" / "common" / folder).mkdir(parents=True)
                (lib / "steamapps" / f"appmanifest_{appid}.acf").write_text(manifest(appid, name, folder), encoding="utf-8")
            (root / "steamapps" / "common" / "Alpha" / "Alpha.exe").write_bytes(b"MZ" + b"0" * 4000)
            (root / "steamapps" / "common" / "Alpha" / "UnityCrashHandler64.exe").write_bytes(b"MZ" + b"0" * 9000)
            (root / "appcache" / "librarycache").mkdir(parents=True)
            (root / "appcache" / "librarycache" / "10_library_600x900.jpg").write_bytes(b"jpg")
            vdf = '"libraryfolders" { "0" { "path" "%s" } "1" { "path" "%s" } }' % (
                str(root).replace("\\", "\\\\"), str(lib2).replace("\\", "\\\\"))
            (root / "steamapps" / "libraryfolders.vdf").write_text(vdf, encoding="utf-8")

            games, present = steam.scan_steam(root)
            self.assertTrue(present)
            by_id = {g.external_id: g for g in games}
            self.assertEqual(sorted(by_id), ["10", "20"])
            self.assertEqual(by_id["10"].launch_uri, "steam://rungameid/10")
            self.assertTrue(by_id["10"].exe_path.endswith("Alpha.exe"))      # крэш-хендлер не выбран
            self.assertTrue(by_id["10"].image_path.endswith("10_library_600x900.jpg"))
            self.assertEqual(by_id["20"].exe_path, "")                        # exe нет — не выдумываем

    def test_missing_install_folder_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "steamapps").mkdir()
            (root / "steamapps" / "appmanifest_5.acf").write_text(manifest("5", "Ghost", "Ghost"), encoding="utf-8")
            self.assertEqual(steam.scan_steam(root), ([], True))

    def test_no_steam(self):
        self.assertEqual(steam.scan_steam(Path("/nonexistent-nova-test"))[0], [])


class EpicTests(unittest.TestCase):
    def item(self, **over):
        data = {"AppName": "Fortnite", "DisplayName": "Fortnite", "InstallLocation": "D:\\Epic\\Fortnite",
                "LaunchExecutable": "FortniteGame\\Binaries\\Win64\\FortniteLauncher.exe", "CatalogNamespace": "fn",
                "CatalogItemId": "4fe75bbc5a674f4f9b356b5c90567da5", "AppCategories": ["public", "games", "applications"],
                "MainGameAppName": "Fortnite"}
        data.update(over)
        return data

    def test_parse_fortnite(self):
        found = epic.parse_item(self.item())
        self.assertEqual(found.source, "epic")
        self.assertEqual(found.external_id, "Fortnite")
        self.assertTrue(found.exe_path.endswith("FortniteLauncher.exe"))
        self.assertEqual(found.launch_uri,
                         "com.epicgames.launcher://apps/fn%3A4fe75bbc5a674f4f9b356b5c90567da5%3AFortnite?action=launch&silent=true")

    def test_dlc_engine_and_incomplete_are_skipped(self):
        self.assertIsNone(epic.parse_item(self.item(AppName="DLC1", MainGameAppName="Fortnite")))
        self.assertIsNone(epic.parse_item(self.item(AppCategories=["public", "applications"])))
        self.assertIsNone(epic.parse_item(self.item(bIsIncompleteInstall=True)))
        self.assertIsNone(epic.parse_item([]))

    def test_scan_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifests = Path(tmp) / "Manifests"
            game = Path(tmp) / "Game"
            game.mkdir()
            manifests.mkdir()
            data = self.item(InstallLocation=str(game), LaunchExecutable="g.exe")
            (manifests / "a.item").write_text(json.dumps(data), encoding="utf-8")
            (manifests / "broken.item").write_text("{не json", encoding="utf-8")
            games, present = epic.scan_epic(manifests)
            self.assertTrue(present)
            self.assertEqual([g.title for g in games], ["Fortnite"])
            self.assertEqual(games[0].exe_path, "")             # g.exe не существует — пути не выдумываем
            self.assertEqual(epic.scan_epic(Path(tmp) / "none"), ([], False))


class AppsTests(unittest.TestCase):
    def test_junk_shortcuts(self):
        self.assertTrue(apps.is_junk_shortcut("Uninstall Foo", r"C:\Foo\foo.exe"))
        self.assertTrue(apps.is_junk_shortcut("Foo", r"C:\Foo\unins000.exe"))
        self.assertTrue(apps.is_junk_shortcut("Foo website", r"C:\Foo\foo.exe"))
        self.assertTrue(apps.is_junk_shortcut("Foo", r"C:\Foo\readme.txt"))
        self.assertFalse(apps.is_junk_shortcut("Foo", r"C:\Foo\foo.exe"))

    def test_shortcut_to_found_game_app_and_system(self):
        app = apps.shortcut_to_found("Google Chrome", r"C:\Program Files\Google\Chrome\chrome.exe", "", "", r"C:\Windows")
        self.assertEqual((app.kind, app.category), ("app", "Браузеры"))
        game = apps.shortcut_to_found("Valorant", r"C:\Riot Games\VALORANT\live\VALORANT.exe", "", "", r"C:\Windows")
        self.assertEqual(game.kind, "game")
        self.assertIsNone(apps.shortcut_to_found("Calc", r"C:\Windows\System32\calc.exe", "", "", r"C:\Windows"))

    def test_start_apps_json_only_store_apps(self):
        text = json.dumps([
            {"Name": "Spotify", "AppID": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
            {"Name": "Notepad++", "AppID": "{6D809377-6AF0-444B-8957-A3773F02200E}\\Notepad++\\notepad++.exe"},
            {"Name": "Settings", "AppID": "windows.immersivecontrolpanel_cw5n1h2txyewy!microsoft.windows.immersivecontrolpanel"},
            {"Name": "Terminal", "AppID": "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App"},
        ])
        parsed = apps.parse_start_apps(text)
        self.assertEqual(len(parsed), 3)
        found = [f for f in (apps.uwp_to_found(n, a) for n, a in parsed) if f]
        self.assertEqual([f.title for f in found], ["Spotify", "Terminal"])
        self.assertEqual(found[0].launch_uri, "shell:AppsFolder\\SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify")
        self.assertEqual(apps.parse_start_apps("не json"), [])
        self.assertEqual(len(apps.parse_start_apps('{"Name": "One", "AppID": "A_b!App"}')), 1)   # единственный элемент — объект

    def test_scan_start_menu_with_fake_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            menu = Path(tmp) / "Programs"
            (menu / "Foo").mkdir(parents=True)
            (menu / "Administrative Tools").mkdir()
            exe = Path(tmp) / "foo.exe"
            exe.write_bytes(b"MZ")
            for rel in ("Foo/Foo.lnk", "Foo/Foo again.lnk", "Foo/Uninstall Foo.lnk", "Administrative Tools/Tool.lnk"):
                (menu / rel).write_bytes(b"lnk")
            found = apps.scan_start_menu(lambda p: (str(exe), "", ""), [menu])
            self.assertEqual(len(found), 1)             # дубль по EXE, удаление и служебная папка отброшены
            self.assertEqual(found[0].source, "shortcut")

    def test_minecraft_promoted_to_game(self):
        items = [Found("app", "Minecraft Launcher", "uwp", "Microsoft.4297127D64EC6_8wekyb3d8bbwe!Minecraft",
                       launch_uri="shell:AppsFolder\\x"),
                 Found("app", "Chrome", "shortcut", "c")]
        games, rest = promote_minecraft(items, None)
        self.assertEqual([(g.source, g.kind, g.category) for g in games], [("minecraft", "game", "Песочница")])
        self.assertEqual([r.title for r in rest], ["Chrome"])

    def test_categories(self):
        self.assertEqual(categories.guess_app_category("Discord"), "Общение")
        self.assertEqual(categories.guess_app_category("Digital Editions"), "")
        self.assertEqual(categories.guess_app_category("Мой инструмент", r"C:\x\chrome.exe"), "Браузеры")


class ExeFindTests(unittest.TestCase):
    def test_prefers_name_match_and_skips_utilities(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "Cool Game"
            (folder / "Binaries" / "Win64").mkdir(parents=True)
            (folder / "CoolGame.exe").write_bytes(b"MZ" + b"0" * 100)
            (folder / "vcredist_x64.exe").write_bytes(b"MZ" + b"0" * 5_000_000)
            (folder / "Binaries" / "Win64" / "Other.exe").write_bytes(b"MZ" + b"0" * 200)
            self.assertTrue(exefind.guess_main_exe(folder, "Cool Game").endswith("CoolGame.exe"))
            self.assertEqual(exefind.guess_main_exe(Path(tmp) / "nope"), "")


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)

    def found(self, ext="10", title="Alpha", exe=""):
        return Found("game", title, "steam", ext, exe_path=exe, launch_uri=f"steam://rungameid/{ext}", install_dir="D:\\A")

    def test_add_then_no_duplicates(self):
        first = merge_found(self.db.games, [self.found()], {"steam"})
        second = merge_found(self.db.games, [self.found()], {"steam"})
        self.assertEqual((first.added, second.added, second.skipped), (1, 0, 1))
        self.assertEqual(self.db.games.count(), 1)

    def test_user_title_and_favorite_survive_rescan(self):
        merge_found(self.db.games, [self.found()], {"steam"})
        game = self.db.games.items()[0]
        self.db.games.update(game.id, title="Моя Альфа", favorite=True, category="Шутер")
        merge_found(self.db.games, [self.found(title="Alpha (renamed by store)")], {"steam"})
        game = self.db.games.get(game.id)
        self.assertEqual((game.title, game.favorite, game.category), ("Моя Альфа", True, "Шутер"))

    def test_hidden_item_is_not_resurrected(self):
        merge_found(self.db.games, [self.found()], {"steam"})
        game_id = self.db.games.items()[0].id
        self.db.games.set_hidden(game_id, True)
        result = merge_found(self.db.games, [self.found()], {"steam"})
        self.assertEqual((result.added, result.skipped), (0, 1))
        self.assertEqual(self.db.games.items(), [])
        self.assertEqual(len(self.db.games.hidden_items()), 1)

    def test_manual_item_with_same_exe_blocks_duplicate(self):
        self.db.games.add(title="Моя игра", exe_path="D:\\A\\a.exe", source="manual")
        result = merge_found(self.db.games, [self.found(exe="d:\\a\\A.EXE")], {"steam"})
        self.assertEqual((result.added, result.skipped), (0, 1))

    def test_missing_then_restored(self):
        merge_found(self.db.games, [self.found()], {"steam"})
        result = merge_found(self.db.games, [], {"steam"})
        self.assertEqual(result.missing, 1)
        self.assertTrue(self.db.games.items()[0].opts.get("uninstalled"))
        result = merge_found(self.db.games, [self.found()], {"steam"})
        self.assertEqual(result.restored, 1)
        self.assertFalse(self.db.games.items()[0].opts.get("uninstalled"))

    def test_source_not_scanned_is_never_marked_missing(self):
        merge_found(self.db.games, [self.found()], {"steam"})
        self.db.games.add(title="Ручная", exe_path="C:\\m.exe", source="manual")
        result = merge_found(self.db.games, [], {"epic"})          # Steam в этот раз не читался
        self.assertEqual(result.missing, 0)

    def test_set_option_and_playtime(self):
        game_id = self.db.games.add(title="G", exe_path="C:\\g.exe")
        self.db.games.set_option(game_id, "priority", "high")
        self.db.games.set_option(game_id, "boost", False)
        self.assertEqual(self.db.games.get(game_id).opts, {"priority": "high", "boost": False})
        self.db.games.set_option(game_id, "priority", None)
        self.assertEqual(self.db.games.get(game_id).opts, {"boost": False})
        hid = self.db.games.record_launch(game_id)
        self.db.games.finish_launch(hid, time.time() + 90)
        self.assertAlmostEqual(self.db.games.playtime(game_id), 90, delta=2)
        self.db.games.finish_launch(hid, time.time() + 5000)       # повторное завершение игнорируется
        self.assertAlmostEqual(self.db.games.playtime(game_id), 90, delta=2)

    def test_migration_from_v1_keeps_data(self):
        import sqlite3
        path = Path(self.tmp.name) / "old.db"
        conn = sqlite3.connect(path)
        conn.executescript("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE games (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
              exe_path TEXT NOT NULL DEFAULT '', launch_uri TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'manual',
              external_id TEXT, image_path TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
              favorite INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0, last_played REAL,
              created_at REAL NOT NULL, UNIQUE (source, external_id));
            CREATE TABLE apps AS SELECT * FROM games WHERE 0;
            CREATE TABLE launch_history (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, item_id INTEGER NOT NULL,
              started_at REAL NOT NULL, ended_at REAL);
            INSERT INTO games (title, exe_path, created_at, favorite) VALUES ('Старая игра', 'C:\\old.exe', 1, 1);
            PRAGMA user_version = 1;
        """)
        conn.commit()
        conn.close()
        db = Database(path)
        self.addCleanup(db.close)
        game = db.games.items()[0]
        self.assertEqual((game.title, game.favorite, game.hidden, game.options, game.install_dir), ("Старая игра", True, False, "{}", ""))
        self.assertEqual(db.query_one("PRAGMA user_version")[0], 2)

    def test_reset_settings_keeps_state_keys(self):
        self.db.set_setting("theme", "light")
        StateStore(self.db).set("boost_restore", {"plan_boost": "x"})
        self.db.reset_settings()
        self.assertIsNone(self.db.get_setting("theme"))
        self.assertEqual(StateStore(self.db).get("boost_restore"), {"plan_boost": "x"})


class FakePopen:
    def __init__(self, calls, raise_error=None):
        self.calls, self.raise_error = calls, raise_error

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.raise_error:
            raise self.raise_error
        return type("P", (), {"pid": 4242})()


class LaunchTests(unittest.TestCase):
    def item(self, **kw):
        return LibraryItem(id=1, title="G", exe_path="C:\\G\\g.exe", install_dir="C:\\G", **kw)

    def make(self, popen=None, start_calls=None, files=("C:\\G\\g.exe",), start_error=None):
        popen = popen or FakePopen([])
        started = start_calls if start_calls is not None else []

        def startfile(target, **kw):
            started.append((target, kw))
            if start_error:
                raise start_error

        return Launcher(popen=popen, startfile=startfile, isfile=lambda p: p in files, isdir=lambda p: True), started

    def test_direct_exe_with_args_priority_and_cwd(self):
        calls = []
        launcher, _ = self.make(FakePopen(calls))
        result = launcher.launch(self.item(args="-windowed -novid"), "high")
        self.assertTrue(result.ok)
        self.assertEqual((result.pid, result.via, result.priority), (4242, "exe", "high"))
        command, kwargs = calls[0]
        self.assertEqual(command, '"C:\\G\\g.exe" -windowed -novid')
        self.assertEqual((kwargs["cwd"], kwargs["creationflags"]), ("C:\\G", 0x80))

    def test_uri_preferred_and_priority_not_applied(self):
        calls = []
        launcher, started = self.make(FakePopen(calls))
        result = launcher.launch(self.item(launch_uri="steam://rungameid/1"), "high")
        self.assertEqual((result.ok, result.via, result.pid), (True, "uri", 0))
        self.assertEqual(started[0][0], "steam://rungameid/1")
        self.assertEqual(calls, [])

    def test_uri_failure_falls_back_to_exe(self):
        calls = []
        launcher, _ = self.make(FakePopen(calls), start_error=OSError("нет обработчика"))
        result = launcher.launch(self.item(launch_uri="steam://rungameid/1"))
        self.assertTrue(result.ok)
        self.assertEqual(len(calls), 1)

    def test_missing_file_is_reported(self):
        launcher, _ = self.make(files=())
        result = launcher.launch(self.item())
        self.assertFalse(result.ok)
        self.assertIn("не найден", result.message)

    def test_nothing_to_launch_and_uninstalled(self):
        launcher, _ = self.make()
        self.assertFalse(launcher.launch(LibraryItem(title="Пусто")).ok)
        self.assertFalse(launcher.launch(self.item(options='{"uninstalled": true}')).ok)

    def test_elevation_required_goes_through_shell(self):
        err = OSError(740, "requires elevation")
        err.winerror = 740
        launcher, started = self.make(FakePopen([], raise_error=err))
        result = launcher.launch(self.item(args="-x"))
        self.assertEqual((result.ok, result.via), (True, "exe-elevated"))
        self.assertEqual(started[0][1]["arguments"], "-x")

    def test_priority_resolution(self):
        self.assertEqual(resolve_priority("above_normal", ""), "above_normal")
        self.assertEqual(resolve_priority("above_normal", "high"), "high")
        self.assertEqual(resolve_priority("weird", ""), "normal")


class MemState:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class BoostTests(unittest.TestCase):
    def make(self, text=None, registry=None):
        fake = FakePowercfg(text) if text else FakePowercfg()
        state, reg = MemState(), registry or MemoryRegistry()
        manager = boost.BoostManager(power.PowerManager(fake), tweaks.TweakManager(MemState(), reg), state)
        return manager, fake, state, reg

    def test_apply_switches_plan_sets_tweaks_and_restores(self):
        manager, fake, state, reg = self.make()
        report = manager.apply(boost.BoostConfig(), "Игра")
        self.assertTrue(report.changed)
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.HIGH_PERFORMANCE)
        self.assertTrue(tweaks.is_applied(reg, tweaks.GAME_MODE.values))
        self.assertTrue(manager.is_active())
        self.assertEqual(state.data[boost.STATE_KEY]["plan_before"], power.BALANCED)

        restored = manager.restore()
        self.assertTrue(all(s.ok for s in restored.steps))
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.BALANCED)
        self.assertFalse(tweaks.is_applied(reg, tweaks.GAME_MODE.values))
        self.assertFalse(manager.is_active())

    def test_second_apply_does_not_capture_boosted_plan_as_previous(self):
        manager, fake, state, _ = self.make()
        manager.apply(boost.BoostConfig())
        again = manager.apply(boost.BoostConfig())
        self.assertTrue(again.already_active)
        manager.restore()
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.BALANCED)

    def test_user_changed_plan_meanwhile_is_left_alone(self):
        manager, fake, _, _ = self.make()
        manager.apply(boost.BoostConfig(game_mode=False, game_dvr=False))
        power.PowerManager(fake).set_active(power.POWER_SAVER)         # пользователь сам выбрал экономию
        report = manager.restore()
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.POWER_SAVER)
        self.assertIn("оставлена как есть", report.steps[0].detail)

    def test_already_on_performance_plan_changes_nothing(self):
        text = "Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance) *"
        manager, _, state, _ = self.make(text)
        report = manager.apply(boost.BoostConfig(game_mode=False, game_dvr=False))
        self.assertFalse(report.changed)
        self.assertFalse(manager.is_active())                         # откатывать нечего
        self.assertEqual(state.data.get(boost.STATE_KEY), None)

    def test_registry_failure_is_reported_not_raised(self):
        manager, _, _, _ = self.make(registry=MemoryRegistry(fail_on="AutoGameModeEnabled"))
        report = manager.apply(boost.BoostConfig(power=False, game_dvr=False))
        self.assertEqual(len(report.failures), 1)
        self.assertFalse(manager.is_active())

    def test_power_failure_leaves_no_state(self):
        fake = FakePowercfg("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *",
                            fail_duplicate={power.ULTIMATE, power.HIGH_PERFORMANCE})
        state = MemState()
        manager = boost.BoostManager(power.PowerManager(fake), tweaks.TweakManager(MemState(), MemoryRegistry()), state)
        report = manager.apply(boost.BoostConfig(game_mode=False, game_dvr=False))
        self.assertEqual(len(report.failures), 1)
        self.assertFalse(manager.is_active())

    def test_created_plan_is_reported_for_settings(self):
        manager, fake, _, _ = self.make("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *")
        report = manager.apply(boost.BoostConfig(game_mode=False, game_dvr=False))
        self.assertTrue(report.created_plan_guid)
        self.assertEqual(report.created_plan_guid, next(p for p in fake.plans if p.active).guid)

    def test_crash_recovery_state_survives_and_restores(self):
        manager, fake, state, reg = self.make()
        manager.apply(boost.BoostConfig(), "Игра", watch={"exes": ["c:\\g\\g.exe"]})
        # «перезапуск NOVA»: новый менеджер с тем же сохранённым состоянием
        manager2 = boost.BoostManager(power.PowerManager(fake), tweaks.TweakManager(MemState(), reg), state)
        self.assertEqual(manager2.pending()["watch"], {"exes": ["c:\\g\\g.exe"]})
        manager2.restore()
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.BALANCED)

    def test_deleted_previous_plan_falls_back_to_balanced(self):
        manager, fake, _, _ = self.make(
            "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n"
            "Power Scheme GUID: 11111111-2222-3333-4444-555555555555  (Моя) *\n"
            "Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)")
        manager.apply(boost.BoostConfig(game_mode=False, game_dvr=False))
        power.PowerManager(fake).delete("11111111-2222-3333-4444-555555555555")
        manager.restore()
        self.assertEqual(next(p for p in fake.plans if p.active).guid, power.BALANCED)


class SessionTests(unittest.TestCase):
    def item(self, **kw):
        base = dict(id=1, title="G", exe_path="D:\\Games\\Cool\\cool.exe", install_dir="D:\\Games\\Cool")
        base.update(kw)
        return LibraryItem(**base)

    def test_broad_dirs(self):
        for broad in ("D:\\", "C:\\Games", "C:\\Program Files", "C:\\Users\\me", "C:\\Windows\\System32", ""):
            self.assertTrue(session.is_broad_dir(broad), broad)
        for ok in ("D:\\Games\\Cool", "C:\\Program Files\\Foo", "D:\\SteamLibrary\\steamapps\\common\\X"):
            self.assertFalse(session.is_broad_dir(ok), ok)

    def test_matches_by_pid_exe_dir_and_name(self):
        spec = session.build_watch(self.item(options='{"process_names": ["CoolLauncher.exe"]}'), pid=77)
        procs = [(77, "x.exe", ""), (5, "cool.exe", "d:\\games\\cool\\COOL.exe"), (6, "helper.exe", "D:\\Games\\Cool\\bin\\helper.exe"),
                 (7, "coollauncher.exe", ""), (8, "chrome.exe", "C:\\Program Files\\Chrome\\chrome.exe"),
                 (9, "other.exe", "D:\\Games\\Cool2\\other.exe")]
        self.assertEqual(session.find_matches(spec, procs), {77, 5, 6, 7})

    def test_broad_install_dir_matches_exe_only(self):
        spec = session.build_watch(self.item(exe_path="C:\\Program Files\\app.exe", install_dir="C:\\Program Files"))
        self.assertEqual(spec.dirs, [])
        self.assertEqual(session.find_matches(spec, [(1, "app.exe", "C:\\Program Files\\app.exe"),
                                                     (2, "b.exe", "C:\\Program Files\\b.exe")]), {1})

    def test_uri_only_item_without_dir_is_untrackable(self):
        spec = session.build_watch(LibraryItem(title="Store app", launch_uri="shell:AppsFolder\\x!y"))
        self.assertFalse(spec.trackable)

    def test_evaluate_exit_needs_two_missing_polls(self):
        s = session.Session("game", 1, "G", 1, started=0.0, spec=session.WatchSpec(pids={1}))
        self.assertEqual(session.evaluate(s, True, 10), (False, ""))
        self.assertEqual(session.evaluate(s, False, 13), (False, ""))      # лаунчер → игра: короткий разрыв
        self.assertEqual(session.evaluate(s, True, 16), (False, ""))
        self.assertEqual(session.evaluate(s, False, 19), (False, ""))
        self.assertEqual(session.evaluate(s, False, 22), (True, "exit"))

    def test_evaluate_never_seen_waits_grace_period(self):
        s = session.Session("game", 1, "G", 1, started=0.0, spec=session.WatchSpec(dirs=["d:\\g\\"]))
        self.assertEqual(session.evaluate(s, False, 60), (False, ""))
        self.assertEqual(session.evaluate(s, False, session.GRACE_S + 1), (True, "never_seen"))

    def test_watch_roundtrip(self):
        spec = session.build_watch(self.item())
        self.assertEqual(session.WatchSpec.from_dict(spec.to_dict()).dirs, spec.dirs)


if __name__ == "__main__":
    unittest.main()
