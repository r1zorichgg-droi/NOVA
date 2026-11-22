"""Тесты чистки списка программ: известные программы (Discord, Steam, Chrome…), удаление лишнего из библиотеки."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.database import Database  # noqa: E402
from launcher import apps, known_apps  # noqa: E402
from launcher.found import Found  # noqa: E402
from launcher.merge import merge_found, prune_unwanted  # noqa: E402

ENV = {"LOCALAPPDATA": r"C:\Users\Me\AppData\Local", "ProgramFiles": r"C:\Program Files",
       "ProgramFiles(x86)": r"C:\Program Files (x86)", "APPDATA": r"C:\Users\Me\AppData\Roaming"}
SYSTEM = r"C:\Windows"


class KnownAppsTests(unittest.TestCase):
    def test_expand_is_case_insensitive_and_keeps_unknown_vars(self):
        self.assertEqual(known_apps.expand(r"%localappdata%\X", ENV), r"C:\Users\Me\AppData\Local\X")
        self.assertEqual(known_apps.expand(r"%NOPE%\X", ENV), r"%NOPE%\X")

    def test_discord_steam_chrome_found_by_their_folders(self):
        present = {r"C:\Users\Me\AppData\Local\Discord\Update.exe",
                   r"C:\Program Files (x86)\Steam\steam.exe",
                   r"C:\Program Files\Google\Chrome\Application\chrome.exe"}
        found = {f.title: f for f in known_apps.scan_known_apps(ENV, lambda p: p in present)}
        self.assertEqual(set(found), {"Discord", "Steam", "Google Chrome"})
        self.assertEqual(found["Discord"].args, "--processStart Discord.exe")
        self.assertEqual(found["Discord"].category, "Общение")
        self.assertEqual(found["Google Chrome"].category, "Браузеры")
        self.assertTrue(all(f.kind == "app" and f.source == "shortcut" for f in found.values()))

    def test_nothing_installed_nothing_found(self):
        self.assertEqual(known_apps.scan_known_apps(ENV, lambda p: False), [])

    def test_missing_env_var_does_not_produce_bogus_path(self):
        seen: list[str] = []
        known_apps.scan_known_apps({}, lambda p: seen.append(p) or True)
        self.assertEqual(seen, [], "путь с неподставленной %переменной% проверять нельзя")

    def test_extra_path_from_registry_wins(self):
        custom = r"D:\Games\Steam\steam.exe"
        found = known_apps.scan_known_apps(ENV, lambda p: p == custom, {"Steam": custom})
        self.assertEqual([(f.title, f.exe_path) for f in found], [("Steam", custom)])

    def test_every_known_app_matches_its_own_title(self):
        for app in known_apps.KNOWN_APPS:
            self.assertIsNotNone(known_apps.match_known(app.title), app.title)
            self.assertEqual(known_apps.match_known(app.title).title, app.title)

    def test_known_names_are_not_treated_as_junk(self):
        self.assertFalse(apps.is_junk_shortcut("Discord", r"C:\Users\Me\AppData\Local\Discord\Update.exe",
                                               "--processStart Discord.exe"))
        self.assertFalse(apps.is_junk_shortcut("Steam", r"C:\Program Files (x86)\Steam\steam.exe"))
        self.assertTrue(apps.is_junk_shortcut("Discord updater", r"C:\x\Update.exe"))

    def test_squirrel_shortcut_with_process_start_is_kept_but_bare_update_is_junk(self):
        self.assertFalse(apps.is_junk_shortcut("Slack Tool", r"C:\L\slack\Update.exe", "--processStart slack.exe"))
        self.assertTrue(apps.is_junk_shortcut("Slack Tool", r"C:\L\slack\Update.exe"))

    def test_discord_shortcut_becomes_proper_app(self):
        item = apps.shortcut_to_found("Discord", r"C:\Users\Me\AppData\Local\Discord\Update.exe",
                                      "--processStart Discord.exe", "", SYSTEM)
        self.assertIsNotNone(item)
        self.assertEqual((item.title, item.kind, item.category), ("Discord", "app", "Общение"))

    def test_shortcut_and_known_scan_do_not_duplicate(self):
        exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        from_menu = apps.shortcut_to_found("Google Chrome", exe, "", "", SYSTEM)
        from_known = known_apps.scan_known_apps(ENV, lambda p: p == exe)
        merged = apps.dedupe_by_exe([from_menu] + from_known)
        self.assertEqual(len(merged), 1)


class PruneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.db.close)

    def _add(self, title, source, exe="", args="", ext="", **extra):
        return self.db.apps.add(title=title, source=source, exe_path=exe, args=args, external_id=ext, **extra)

    def test_unwanted_rules(self):
        self.assertTrue(apps.is_unwanted_item("shortcut", "Uninstall Foo", r"C:\Foo\foo.exe", "", "x", SYSTEM))
        self.assertTrue(apps.is_unwanted_item("shortcut", "Блокнот", r"C:\Windows\System32\notepad.exe", "", "x", SYSTEM))
        self.assertTrue(apps.is_unwanted_item("uwp", "Новости", "", "", "Microsoft.BingNews_8wekyb3d8bbwe!AppexNews", SYSTEM))
        self.assertTrue(apps.is_unwanted_item("uwp", "Candy", "", "", "king.com.CandyCrushSaga_kgqvnymyfvs32!App", SYSTEM))
        self.assertFalse(apps.is_unwanted_item("uwp", "Terminal", "", "", "Microsoft.WindowsTerminal_8wekyb3d8bbwe!App", SYSTEM))
        self.assertFalse(apps.is_unwanted_item("shortcut", "Discord", r"C:\L\Discord\Update.exe",
                                               "--processStart Discord.exe", "x", SYSTEM))
        self.assertFalse(apps.is_unwanted_item("manual", "Uninstall Foo", r"C:\Windows\x.exe", "", "", SYSTEM),
                         "добавленное вручную не трогаем")

    def test_prune_removes_only_unwanted_and_only_library_records(self):
        junk = self._add("Uninstall Foo", "shortcut", r"C:\Foo\unins000.exe", ext="a")
        news = self._add("Новости", "uwp", ext="Microsoft.BingNews_8wekyb3d8bbwe!AppexNews")
        good = self._add("Discord", "shortcut", r"C:\L\Discord\Update.exe", "--processStart Discord.exe", ext="b")
        manual = self._add("Моя утилита", "manual", r"C:\Windows\System32\cmd.exe")
        self.assertEqual(prune_unwanted(self.db.apps, system_dir=SYSTEM), 2)
        self.assertIsNone(self.db.apps.get(junk))
        self.assertIsNone(self.db.apps.get(news))
        self.assertIsNotNone(self.db.apps.get(good))
        self.assertIsNotNone(self.db.apps.get(manual))

    def test_prune_keeps_pinned_favorite_and_launched(self):
        pinned = self._add("Uninstall A", "shortcut", r"C:\A\a.exe", ext="p")
        fav = self._add("Uninstall B", "shortcut", r"C:\B\b.exe", ext="f", favorite=1)
        used = self._add("Uninstall C", "shortcut", r"C:\C\c.exe", ext="u", play_count=3)
        gone = self._add("Uninstall D", "shortcut", r"C:\D\d.exe", ext="g")
        self.assertEqual(prune_unwanted(self.db.apps, keep_ids=[pinned], system_dir=SYSTEM), 1)
        for kept in (pinned, fav, used):
            self.assertIsNotNone(self.db.apps.get(kept))
        self.assertIsNone(self.db.apps.get(gone))

    def test_prune_also_cleans_hidden_and_is_idempotent(self):
        junk = self._add("Uninstall Foo", "shortcut", r"C:\Foo\unins000.exe", ext="a", hidden=1)
        self.assertEqual(prune_unwanted(self.db.apps, system_dir=SYSTEM), 1)
        self.assertIsNone(self.db.apps.get(junk))
        self.assertEqual(prune_unwanted(self.db.apps, system_dir=SYSTEM), 0)

    def test_scan_adds_known_apps_and_rescan_keeps_them(self):
        found = [Found(kind="app", title="Discord", source="shortcut", external_id=r"c:\l\discord\update.exe",
                       exe_path=r"C:\L\Discord\Update.exe", category="Общение", args="--processStart Discord.exe")]
        res = merge_found(self.db.apps, found, {"shortcut"})
        self.assertEqual(res.added, 1)
        self.assertEqual(prune_unwanted(self.db.apps, system_dir=SYSTEM), 0)
        self.assertEqual(merge_found(self.db.apps, found, {"shortcut"}).added, 0)


if __name__ == "__main__":
    unittest.main()
