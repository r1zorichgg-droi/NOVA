"""Тесты логики этапа 2 без Windows и Qt: powercfg, твики реестра, закрытие программ, очистка temp."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system import cleanup, power, processes, tweaks  # noqa: E402

EN_LIST = """Existing Power Schemes (* Active)
-----------------------------------
Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *
Power Scheme GUID: 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c  (High performance)
Power Scheme GUID: a1841308-3541-4fab-bc81-f71556f20b4a  (Power saver)
"""
RU_LIST = """Существующие схемы управления питанием (* активная)
-----------------------------------
GUID схемы питания: 381b4222-f694-41f0-9685-ff5bb260df2e  (Сбалансированная)
GUID схемы питания: 11111111-2222-3333-4444-555555555555  (Моя схема (игры)) *
GUID схемы питания: e9a42b02-d5df-448d-aa00-03f14749eb61  (Максимальная производительность)
"""
QUERY_OUT = """  GUID схемы питания: 381b4222-f694-41f0-9685-ff5bb260df2e  (Сбалансированная)
  GUID подгруппы: 54533251-82be-4824-96c1-47b60b740d00  (Управление питанием процессора)
    GUID параметра питания: 893dee8e-2bef-41e0-89c6-b55d0929964c  (Минимальное состояние процессора)
      Минимальное возможное значение: 0x00000000
      Максимальное возможное значение: 0x00000064
      Шаг возможных значений: 0x00000001
      Единицы измерения: %
    Текущий индекс настройки сети переменного тока: 0x00000005
    Текущий индекс настройки питания от батареи: 0x00000000
"""


class FakePowercfg:
    """Имитация powercfg: хранит схемы в памяти, чтобы проверить логику PowerManager."""

    def __init__(self, text: str = EN_LIST, fail_duplicate: set[str] | None = None) -> None:
        self.plans = power.parse_plans(text)
        self.calls: list[list[str]] = []
        self.fail_duplicate = fail_duplicate or set()
        self.counter = 0

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        cmd = args[0]
        if cmd == "/list":
            return 0, "\n".join(f"Power Scheme GUID: {p.guid}  ({p.name})" + (" *" if p.active else "")
                                for p in self.plans)
        if cmd == "/getactivescheme":
            active = next(p for p in self.plans if p.active)
            return 0, f"Power Scheme GUID: {active.guid}  ({active.name})"
        if cmd == "/setactive":
            self.plans = [power.PowerPlan(p.guid, p.name, p.guid == args[1].lower()) for p in self.plans]
            return 0, ""
        if cmd == "/duplicatescheme":
            if args[1].lower() in self.fail_duplicate:
                return 1, "The power scheme, subgroup or setting specified does not exist."
            self.counter += 1
            guid = f"00000000-0000-0000-0000-{self.counter:012d}"
            self.plans.append(power.PowerPlan(guid, "copy", False))
            return 0, f"Power Scheme GUID: {guid}  (copy)"
        if cmd == "/changename":
            self.plans = [power.PowerPlan(p.guid, args[2] if p.guid == args[1].lower() else p.name, p.active)
                          for p in self.plans]
            return 0, ""
        if cmd == "/delete":
            self.plans = [p for p in self.plans if p.guid != args[1].lower()]
            return 0, ""
        if cmd == "/query":
            return 0, QUERY_OUT
        return 0, ""


class PowerParsingTests(unittest.TestCase):
    def test_parse_english_and_active_flag(self):
        plans = power.parse_plans(EN_LIST)
        self.assertEqual([p.name for p in plans], ["Balanced", "High performance", "Power saver"])
        self.assertEqual([p.active for p in plans], [True, False, False])

    def test_parse_russian_with_parentheses_in_name(self):
        plans = power.parse_plans(RU_LIST)
        self.assertEqual(len(plans), 3)
        self.assertEqual(plans[1].name, "Моя схема (игры)")
        self.assertTrue(plans[1].active)

    def test_kind_by_guid_and_localized_name(self):
        plans = {p.guid: p for p in power.parse_plans(RU_LIST)}
        self.assertEqual(plans[power.ULTIMATE].kind, "ultimate")
        self.assertEqual(plans[power.BALANCED].kind, "balanced")
        self.assertEqual(power.PowerPlan("aaaaaaaa-0000-0000-0000-000000000000", "Высокая производительность").kind, "high")
        self.assertEqual(power.PowerPlan("aaaaaaaa-0000-0000-0000-000000000000", "Моя схема").kind, "custom")

    def test_setting_values_take_last_two_hex(self):
        self.assertEqual(power.parse_setting_values(QUERY_OUT), (5, 0))
        self.assertIsNone(power.parse_setting_values("ничего полезного"))

    def test_best_plan_prefers_ultimate_then_high(self):
        plans = power.parse_plans(RU_LIST)
        self.assertEqual(power.best_performance_plan(plans).guid, power.ULTIMATE)
        only_high = power.parse_plans(EN_LIST)
        self.assertEqual(power.best_performance_plan(only_high).guid, power.HIGH_PERFORMANCE)
        self.assertIsNone(power.best_performance_plan(power.parse_plans(EN_LIST)[:1]))

    def test_preferred_guid_wins(self):
        plans = power.parse_plans(RU_LIST)
        chosen = power.best_performance_plan(plans, "11111111-2222-3333-4444-555555555555")
        self.assertEqual(chosen.name, "Моя схема (игры)")
        # устаревший выбор (схему удалили) — откатываемся к автоматическому
        self.assertEqual(power.best_performance_plan(plans, "ffffffff-0000-0000-0000-000000000000").guid, power.ULTIMATE)


class PowerManagerTests(unittest.TestCase):
    def test_create_renames_copy(self):
        fake = FakePowercfg()
        plan = power.PowerManager(fake).create("Игры", power.HIGH_PERFORMANCE)
        self.assertEqual(plan.name, "Игры")
        self.assertIn("Игры", [p.name for p in power.PowerManager(fake).plans()])

    def test_create_rejects_empty_name(self):
        with self.assertRaises(power.PowerError):
            power.PowerManager(FakePowercfg()).create("   ", power.BALANCED)

    def test_cannot_delete_active_or_last(self):
        mgr = power.PowerManager(FakePowercfg())
        with self.assertRaises(power.PowerError):
            mgr.delete(power.BALANCED)                   # активная
        mgr.delete(power.POWER_SAVER)                    # неактивную можно
        self.assertEqual(len(mgr.plans()), 2)
        single = power.PowerManager(FakePowercfg("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *"))
        with self.assertRaises(power.PowerError):
            single.delete(power.BALANCED)

    def test_ensure_creates_only_when_missing(self):
        fake = FakePowercfg()
        plan, created = power.PowerManager(fake).ensure_performance_plan()
        self.assertEqual(plan.guid, power.HIGH_PERFORMANCE)
        self.assertFalse(created)
        self.assertFalse(any(c[0] == "/duplicatescheme" for c in fake.calls))

        fake = FakePowercfg("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *")
        plan, created = power.PowerManager(fake).ensure_performance_plan()
        self.assertTrue(created)
        self.assertEqual(plan.name, power.NOVA_PLAN_NAME)

    def test_ensure_falls_back_to_high_when_ultimate_unavailable(self):
        fake = FakePowercfg("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *",
                            fail_duplicate={power.ULTIMATE})
        _plan, created = power.PowerManager(fake).ensure_performance_plan()
        self.assertTrue(created)
        self.assertEqual([c[1] for c in fake.calls if c[0] == "/duplicatescheme"], [power.ULTIMATE, power.HIGH_PERFORMANCE])

    def test_ensure_reports_when_nothing_works(self):
        fake = FakePowercfg("Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced) *",
                            fail_duplicate={power.ULTIMATE, power.HIGH_PERFORMANCE})
        with self.assertRaises(power.PowerError) as ctx:
            power.PowerManager(fake).ensure_performance_plan()
        self.assertIn("не даёт создать", str(ctx.exception))

    def test_cpu_state_validation(self):
        mgr = power.PowerManager(FakePowercfg())
        with self.assertRaises(power.PowerError):
            mgr.set_cpu_state(power.BALANCED, 90, 50)
        self.assertEqual(mgr.cpu_state(power.BALANCED), (5, 5))

    def test_failure_text_is_passed_on(self):
        mgr = power.PowerManager(lambda args: (1, "Доступ запрещён"))
        with self.assertRaises(power.PowerError) as ctx:
            mgr.plans()
        self.assertIn("Доступ запрещён", str(ctx.exception))

    @unittest.skipIf(power.IS_WINDOWS, "проверка поведения вне Windows")
    def test_not_windows_is_honest(self):
        with self.assertRaises(power.PowerError) as ctx:
            power.PowerManager().plans()
        self.assertIn("только в Windows", str(ctx.exception))


class MemoryRegistry:
    def __init__(self, data=None, fail_on: str | None = None) -> None:
        self.data: dict[tuple[str, str], tuple[str, object]] = dict(data or {})
        self.fail_on = fail_on

    def read(self, key, name):
        return self.data.get((key, name))

    def write(self, key, name, kind, value):
        if self.fail_on == name:
            raise tweaks.TweakError("нет доступа")
        self.data[(key, name)] = (kind, value)

    def delete(self, key, name):
        self.data.pop((key, name), None)


class FakeSettings:
    def __init__(self) -> None:
        self.data: dict = {}

    def get(self, key):
        return self.data.get(key, {})

    def set(self, key, value):
        self.data[key] = value


class TweakTests(unittest.TestCase):
    def test_apply_and_restore_original_values(self):
        reg = MemoryRegistry({(r"Software\Microsoft\GameBar", "AutoGameModeEnabled"): ("dword", 0)})
        records = tweaks.apply_values(reg, tweaks.GAME_MODE.values)
        self.assertTrue(tweaks.is_applied(reg, tweaks.GAME_MODE.values))
        self.assertEqual(tweaks.restore_records(reg, records), 0)
        self.assertEqual(reg.read(r"Software\Microsoft\GameBar", "AutoGameModeEnabled"), ("dword", 0))
        self.assertIsNone(reg.read(r"Software\Microsoft\GameBar", "AllowAutoGameMode"))   # раньше не было — удалён

    def test_already_applied_changes_nothing(self):
        reg = MemoryRegistry({(v.key, v.name): (v.kind, v.value) for v in tweaks.GAME_DVR.values})
        self.assertEqual(tweaks.apply_values(reg, tweaks.GAME_DVR.values), [])

    def test_restore_does_not_overwrite_user_change(self):
        reg = MemoryRegistry()
        records = tweaks.apply_values(reg, tweaks.GAME_MODE.values)
        reg.write(r"Software\Microsoft\GameBar", "AutoGameModeEnabled", "dword", 7)   # пользователь изменил сам
        tweaks.restore_records(reg, records)
        self.assertEqual(reg.read(r"Software\Microsoft\GameBar", "AutoGameModeEnabled"), ("dword", 7))

    def test_failed_apply_rolls_back_partial_changes(self):
        reg = MemoryRegistry(fail_on="AutoGameModeEnabled")
        with self.assertRaises(tweaks.TweakError):
            tweaks.apply_values(reg, tweaks.GAME_MODE.values)
        self.assertIsNone(reg.read(r"Software\Microsoft\GameBar", "AllowAutoGameMode"))

    def test_manager_enable_disable_persists_backup(self):
        settings, reg = FakeSettings(), MemoryRegistry()
        mgr = tweaks.TweakManager(settings, reg)
        mgr.enable("transparency")
        self.assertTrue(mgr.is_on("transparency"))
        self.assertTrue(mgr.has_backup("transparency"))
        self.assertEqual(mgr.disable("transparency"), 0)
        self.assertFalse(mgr.is_on("transparency"))
        self.assertFalse(mgr.has_backup("transparency"))

    def test_gpu_preference_keeps_other_fields(self):
        self.assertEqual(tweaks.merge_gpu_pref("SwapEffectUpgradeEnable=1;", 2), "GpuPreference=2;SwapEffectUpgradeEnable=1;")
        self.assertEqual(tweaks.merge_gpu_pref("GpuPreference=1;SwapEffectUpgradeEnable=1;", 2),
                         "GpuPreference=2;SwapEffectUpgradeEnable=1;")
        self.assertEqual(tweaks.merge_gpu_pref("GpuPreference=2;", None), None)
        self.assertEqual(tweaks.parse_gpu_pref("SwapEffectUpgradeEnable=1;GpuPreference=2;"), 2)
        self.assertIsNone(tweaks.parse_gpu_pref(None))

    def test_layers_flag_merge(self):
        self.assertEqual(tweaks.merge_layers(None, tweaks.FSO_FLAG, True), f"~ {tweaks.FSO_FLAG}")
        self.assertEqual(tweaks.merge_layers("~ RUNASADMIN", tweaks.FSO_FLAG, True), f"~ RUNASADMIN {tweaks.FSO_FLAG}")
        self.assertEqual(tweaks.merge_layers(f"~ RUNASADMIN {tweaks.FSO_FLAG}", tweaks.FSO_FLAG, False), "~ RUNASADMIN")
        self.assertIsNone(tweaks.merge_layers(f"~ {tweaks.FSO_FLAG}", tweaks.FSO_FLAG, False))

    def test_per_exe_settings_roundtrip(self):
        mgr = tweaks.TweakManager(FakeSettings(), MemoryRegistry())
        exe = os.path.join("C:\\", "Games", "game.exe")
        mgr.set_gpu_preference(exe, tweaks.GPU_HIGH_PERFORMANCE)
        self.assertEqual(mgr.gpu_preference(exe), 2)
        mgr.set_gpu_preference(exe, None)
        self.assertIsNone(mgr.gpu_preference(exe))
        mgr.set_fullscreen_optimizations_disabled(exe, True)
        self.assertTrue(mgr.fullscreen_optimizations_disabled(exe))
        mgr.set_fullscreen_optimizations_disabled(exe, False)
        self.assertFalse(mgr.fullscreen_optimizations_disabled(exe))

    @unittest.skipIf(tweaks.IS_WINDOWS, "проверка поведения вне Windows")
    def test_not_windows_is_honest(self):
        mgr = tweaks.TweakManager(FakeSettings())
        with self.assertRaises(tweaks.TweakError):
            mgr.enable("game_mode")


class ProcessFilterTests(unittest.TestCase):
    def rows(self):
        mb = 1024 * 1024
        return [
            {"pid": 10, "name": "chrome.exe", "exe": r"C:\Program Files\Google\chrome.exe", "rss": 900 * mb, "username": "PC\\me"},
            {"pid": 11, "name": "explorer.exe", "exe": r"C:\Windows\explorer.exe", "rss": 100 * mb, "username": "PC\\me"},
            {"pid": 12, "name": "discord.exe", "exe": r"C:\Users\me\Discord\discord.exe", "rss": 300 * mb, "username": "PC\\me"},
            {"pid": 13, "name": "nova.exe", "exe": r"C:\NOVA\nova.exe", "rss": 150 * mb, "username": "PC\\me"},
            {"pid": 14, "name": "other.exe", "exe": r"C:\x\other.exe", "rss": 50 * mb, "username": "PC\\someone"},
            {"pid": 15, "name": "hidden.exe", "exe": r"C:\x\hidden.exe", "rss": 50 * mb, "username": "PC\\me"},
            {"pid": 16, "name": "game.exe", "exe": r"D:\Games\Cool\game.exe", "rss": 2000 * mb, "username": "PC\\me"},
            {"pid": 17, "name": "tool.exe", "exe": r"C:\Windows\System32\tool.exe", "rss": 20 * mb, "username": "PC\\me"},
        ]

    def test_only_safe_visible_user_apps_sorted_by_memory(self):
        found = processes.filter_candidates(
            self.rows(), visible_pids={10, 11, 12, 13, 14, 16, 17}, own_pids={13}, username="PC\\me",
            system_dir=r"C:\Windows", exclude_dirs=[r"D:\Games\Cool"])
        self.assertEqual([a.name for a in found], ["chrome.exe", "discord.exe"])
        self.assertEqual(found[0].memory_mb, 900.0)

    def test_empty_when_nothing_visible(self):
        self.assertEqual(processes.filter_candidates(self.rows(), set(), set(), "me"), [])


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "deep" / "Temp"
        (self.root / "sub").mkdir(parents=True)
        self.old = self.root / "old.tmp"
        self.old.write_bytes(b"x" * 1000)
        self.sub_old = self.root / "sub" / "old2.tmp"
        self.sub_old.write_bytes(b"y" * 500)
        self.fresh = self.root / "fresh.tmp"
        self.fresh.write_bytes(b"z" * 100)
        past = time.time() - 7200
        for p in (self.old, self.sub_old):
            os.utime(p, (past, past))

    def tearDown(self):
        self.tmp.cleanup()

    def test_scan_counts_only_old_files(self):
        scan = cleanup.scan_temp(self.root)
        self.assertEqual(sorted(p.name for p in scan.files), ["old.tmp", "old2.tmp"])
        self.assertEqual(scan.total_bytes, 1500)
        self.assertTrue(self.fresh.exists())          # сканирование ничего не удаляет

    def test_clean_removes_only_scanned_files_and_empty_dirs(self):
        scan = cleanup.scan_temp(self.root)
        result = cleanup.clean_files(scan)
        self.assertEqual((result.deleted, result.freed_bytes, result.failed), (2, 1500, 0))
        self.assertFalse(self.old.exists())
        self.assertTrue(self.fresh.exists())          # свежий файл остался
        self.assertTrue(self.root.exists())           # сама папка temp остаётся
        self.assertFalse((self.root / "sub").exists())

    def test_refuses_unsafe_roots(self):
        self.assertFalse(cleanup.is_safe_root(Path(Path.home().anchor)))
        self.assertFalse(cleanup.is_safe_root(Path.home()))
        self.assertEqual(cleanup.scan_temp(Path.home()).files, [])


    def test_shader_cache_scan_only_selects_old_files_and_clean_is_scoped(self):
        root = self.root / "GPUCache"
        root.mkdir(parents=True)
        old_file = root / "old.dxcache"
        fresh_file = root / "fresh.dxcache"
        old_file.write_bytes(b"a" * 1200)
        fresh_file.write_bytes(b"b" * 200)
        now = time.time()
        old_time = now - 30 * 24 * 60 * 60
        fresh_time = now - 2 * 24 * 60 * 60
        os.utime(old_file, (old_time, old_time))
        os.utime(fresh_file, (fresh_time, fresh_time))
        outside = self.root / "outside.keep"
        outside.write_bytes(b"keep")
        os.utime(outside, (old_time, old_time))

        scan = cleanup.scan_shader_cache([root], min_age_s=14 * 24 * 60 * 60, now=now)
        self.assertEqual(scan.files, [old_file])
        self.assertEqual(scan.total_bytes, 1200)
        self.assertTrue(fresh_file.exists())
        result = cleanup.clean_shader_cache(scan, now=now)
        self.assertEqual((result.deleted, result.freed_bytes, result.failed), (1, 1200, 0))
        self.assertFalse(old_file.exists())
        self.assertTrue(fresh_file.exists())
        self.assertTrue(outside.exists())

    def test_shader_cache_skips_symlinked_files(self):
        root = self.root / "GPUCacheLinks"
        root.mkdir(parents=True)
        outside = self.root / "outside_shader.keep"
        outside.write_bytes(b"keep")
        old = time.time() - 30 * 24 * 60 * 60
        os.utime(outside, (old, old))
        link = root / "shader.link"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        scan = cleanup.scan_shader_cache([root], min_age_s=14 * 24 * 60 * 60)
        self.assertEqual(scan.files, [])
        cleanup.clean_shader_cache(scan)
        self.assertTrue(outside.exists())

    def test_symlink_is_not_followed(self):
        outside = Path(self.tmp.name) / "outside.txt"
        outside.write_bytes(b"keep")
        past = time.time() - 7200
        os.utime(outside, (past, past))
        link = self.root / "link.tmp"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("символические ссылки недоступны")
        cleanup.clean_files(cleanup.scan_temp(self.root))
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
