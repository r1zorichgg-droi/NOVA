"""Тесты базового слоя без Qt: запуск `python -m unittest discover -s tests -v` из папки NOVA."""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import DEFAULT_SETTINGS, coerce  # noqa: E402
from database.database import Database  # noqa: E402
from system import fmt  # noqa: E402
from system.collector import Collector, merge_gpu  # noqa: E402
from system.automation import autostart_command  # noqa: E402
from system.disk_monitor import DiskMonitor  # noqa: E402
from system.gpu_monitor import GpuStats  # noqa: E402
from system.sensors import parse_lhm_tree  # noqa: E402


class CoerceTests(unittest.TestCase):
    def test_types_are_enforced(self):
        self.assertEqual(coerce("x", 5), 5)
        self.assertEqual(coerce(3, 5), 3)
        self.assertEqual(coerce(True, 5), 5)          # bool не должен сходить за int
        self.assertEqual(coerce(2, 1.0), 2.0)
        self.assertEqual(coerce("dark", "light"), "dark")
        self.assertEqual(coerce(None, "light"), "light")
        self.assertEqual(coerce(["cpu"], ["ram"]), ["cpu"])
        self.assertEqual(coerce("cpu", ["ram"]), ["ram"])
        self.assertIs(coerce(1, False), False)

    def test_defaults_are_self_consistent(self):
        for key, default in DEFAULT_SETTINGS.items():
            self.assertEqual(coerce(default, default), default, key)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_settings_roundtrip_and_reset(self):
        self.assertEqual(self.db.get_setting("missing", 42), 42)
        self.db.set_setting("accent", "#fff")
        self.db.set_settings({"island_metrics": ["cpu", "ram"], "island_enabled": False})
        self.assertEqual(self.db.get_setting("accent"), "#fff")
        self.assertEqual(self.db.get_setting("island_metrics"), ["cpu", "ram"])
        self.assertFalse(self.db.get_setting("island_enabled"))
        self.db.reset_settings()
        self.assertEqual(self.db.all_settings(), {})

    def test_game_crud_and_history(self):
        gid = self.db.games.add(title="Minecraft", exe_path="C:/mc.exe", category="Песочницы")
        self.db.games.add(title="Fortnite", source="epic", external_id="fn")
        self.assertEqual([g.title for g in self.db.games.items()], ["Fortnite", "Minecraft"])
        self.assertEqual([g.title for g in self.db.games.items(search="mine")], ["Minecraft"])
        self.assertEqual(self.db.games.items(search="%"), [])      # спецсимволы LIKE экранируются
        self.db.games.update(gid, favorite=True, description="Блоки")
        game = self.db.games.get(gid)
        assert game is not None
        self.assertTrue(game.favorite)
        self.assertEqual(game.description, "Блоки")
        self.assertEqual([g.id for g in self.db.games.favorites()], [gid])
        self.db.games.record_launch(gid)
        self.db.games.record_launch(gid)
        self.assertEqual(self.db.games.get(gid).play_count, 2)    # type: ignore[union-attr]
        self.assertEqual(len(self.db.games.history(gid)), 2)
        self.assertEqual([g.id for g in self.db.games.recent()], [gid])
        self.assertEqual(self.db.games.categories(), ["Песочницы"])

    def test_delete_removes_only_the_record(self):
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            exe = f.name
        try:
            gid = self.db.apps.add(title="Tool", exe_path=exe)
            self.db.apps.record_launch(gid)
            self.db.apps.delete(gid)
            self.assertIsNone(self.db.apps.get(gid))
            self.assertEqual(self.db.apps.history(), [])
            self.assertTrue(os.path.exists(exe), "файл программы не должен удаляться")
        finally:
            os.unlink(exe)

    def test_validation(self):
        with self.assertRaises(ValueError):
            self.db.games.add(exe_path="x")                 # нет title
        gid = self.db.games.add(title="A")
        with self.assertRaises(ValueError):
            self.db.games.update(gid, **{"title = 'x'; DROP TABLE games; --": 1})
        with self.assertRaises(sqlite3.IntegrityError):     # UNIQUE(source, external_id)
            self.db.games.add(title="B", source="steam", external_id="1")
            self.db.games.add(title="C", source="steam", external_id="1")

    def test_manual_items_with_null_external_id_do_not_collide(self):
        self.db.games.add(title="One")
        self.db.games.add(title="Two")
        self.assertEqual(len(self.db.games.items()), 2)

    def test_persistence_between_connections(self):
        path = Path(self.tmp.name) / "p.db"
        a = Database(path)
        a.set_setting("theme", "light")
        a.games.add(title="Persist")
        a.close()
        b = Database(path)
        self.assertEqual(b.get_setting("theme"), "light")
        self.assertEqual([g.title for g in b.games.items()], ["Persist"])
        b.close()


class FormatTests(unittest.TestCase):
    def test_bytes_and_speed(self):
        self.assertEqual(fmt.fmt_bytes(None), "N/A")
        self.assertEqual(fmt.fmt_bytes(512), "512 Б")
        self.assertEqual(fmt.fmt_bytes(1536), "1.5 КБ")
        self.assertEqual(fmt.fmt_speed_short(0), "0B")
        self.assertEqual(fmt.fmt_speed_short(512 * 1024), "512K")
        self.assertEqual(fmt.fmt_speed_short(1.2 * 1024 * 1024), "1.2M")
        self.assertLessEqual(len(fmt.fmt_speed_short(999 * 1024 * 1024)), 5)

    def test_uptime_clock_greeting(self):
        self.assertEqual(fmt.fmt_uptime(None), "N/A")
        self.assertEqual(fmt.fmt_uptime(5 * 60), "5 мин")
        self.assertEqual(fmt.fmt_uptime(3 * 3600 + 12 * 60), "3 ч 12 мин")
        self.assertEqual(fmt.fmt_uptime(2 * 86400 + 4 * 3600), "2 д 4 ч")
        self.assertEqual(fmt.fmt_clock(169), "2:49")
        self.assertEqual(fmt.fmt_clock(3725), "1:02:05")
        self.assertEqual(fmt.greeting(datetime(2026, 1, 1, 8)), "Доброе утро")
        self.assertEqual(fmt.greeting(datetime(2026, 1, 1, 22, 34)), "Добрый вечер")
        self.assertEqual(fmt.greeting(datetime(2026, 1, 1, 3)), "Доброй ночи")
        self.assertEqual(fmt.fmt_date_ru(datetime(2026, 11, 17)), "Вторник, 17 ноября")


LHM_SAMPLE = {
    "Text": "Sensor", "Children": [{
        "Text": "DESKTOP-PC", "Children": [
            {"Text": "AMD Ryzen 7 5800X", "ImageURL": "images_icon/cpu.png", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "Core (Tctl/Tdie)", "Value": "61.5 °C", "Children": []},
                    {"Text": "CCD1 (Tdie)", "Value": "55.0 °C", "Children": []}]},
                {"Text": "Load", "Children": [
                    {"Text": "CPU Total", "Value": "12,3 %", "Children": []}]}]},
            {"Text": "NVIDIA GeForce RTX 4070", "ImageURL": "images_icon/nvidia.png", "Children": [
                {"Text": "Temperatures", "Children": [
                    {"Text": "GPU Core", "Value": "48,0 °C", "Children": []}]},
                {"Text": "Load", "Children": [
                    {"Text": "GPU Core", "Value": "7,0 %", "Children": []},
                    {"Text": "GPU Memory", "Value": "20,0 %", "Children": []}]}]}]}]}


class SensorTests(unittest.TestCase):
    def test_parse_lhm_tree(self):
        r = parse_lhm_tree(LHM_SAMPLE)
        self.assertEqual(r.cpu_temp, 61.5)
        self.assertEqual(r.gpu_temp, 48.0)
        self.assertEqual(r.gpu_load, 7.0)
        self.assertEqual(r.gpu_name, "NVIDIA GeForce RTX 4070")

    def test_empty_tree_gives_none_not_fake_values(self):
        r = parse_lhm_tree({"Text": "Sensor", "Children": []})
        self.assertIsNone(r.cpu_temp)
        self.assertIsNone(r.gpu_temp)
        self.assertIsNone(r.gpu_load)

    def test_merge_gpu(self):
        self.assertIsNone(merge_gpu(None, None, None, None))
        lhm = merge_gpu(None, 9.0, 50.0, "Radeon")
        assert lhm is not None
        self.assertEqual((lhm.percent, lhm.temp_c, lhm.source, lhm.vram_total), (9.0, 50.0, "lhm", None))
        nv = GpuStats("RTX", 30.0, None, 1, 2, "nvml")
        merged = merge_gpu(nv, 99.0, 44.0, "ignored")
        assert merged is not None
        self.assertEqual((merged.percent, merged.temp_c, merged.source), (30.0, 44.0, "nvml"))


class CollectorTests(unittest.TestCase):
    def test_collect_returns_real_values_and_na_for_missing(self):
        c = Collector()
        c.collect()
        snap = c.collect()
        self.assertTrue(0 <= snap.cpu.percent <= 100)
        self.assertTrue(0 <= snap.ram.percent <= 100)
        self.assertGreater(snap.ram.total, 0)
        self.assertGreater(snap.cpu.threads, 0)
        self.assertGreaterEqual(snap.net.down_bps, 0)
        self.assertIsNotNone(snap.uptime_s)
        # В песочнице нет ни NVIDIA, ни LibreHardwareMonitor: вместо выдуманных значений должно быть None.
        self.assertIsNone(snap.cpu_temp)
        self.assertIsNone(snap.gpu)
        self.assertEqual(len(c.history["cpu"]), 2)
        c.shutdown()

    def test_disks(self):
        disks = DiskMonitor().read()
        self.assertTrue(all(d.total > 0 and 0 <= d.percent <= 100 for d in disks))


class AutostartTests(unittest.TestCase):
    def test_command_is_quoted_and_minimized(self):
        cmd = autostart_command(Path("/opt/nova app/main.py"))
        self.assertTrue(cmd.endswith("--minimized"))
        self.assertIn('"/opt/nova app/main.py"', cmd)


if __name__ == "__main__":
    unittest.main()
