from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from launcher.game_profiles import clean_profiles, find_game_profile, process_key
from system.fps_history import FpsHistoryStore
from system.network_probe import ping_host, validate_host
from system.updater import is_newer_version, validate_manifest


class GameProfileTests(unittest.TestCase):
    def test_process_key_normalizes_windows_paths(self):
        self.assertEqual(process_key(r"C:\Games\Fortnite\FortniteClient.exe"), "fortniteclient.exe")

    def test_profile_lookup_is_case_insensitive(self):
        profiles = [{"name": "Fortnite", "process": "FortniteClient.exe", "boost": True}]
        self.assertEqual(find_game_profile(profiles, "fortniteclient.EXE")["name"], "Fortnite")
        self.assertIsNone(find_game_profile(profiles, "other.exe"))

    def test_clean_profiles_deduplicates_and_validates_priority(self):
        rows = clean_profiles([
            {"name": "A", "process": "a.exe", "priority": "bad"},
            {"name": "B", "process": r"C:\Games\A.exe"},
            {"name": "empty", "process": ""},
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["priority"], "above_normal")


class FpsHistoryTests(unittest.TestCase):
    def test_record_and_finish_persists_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FpsHistoryStore(Path(tmp) / "history.json")
            store.record("game.exe", {"fps": 120, "avg": 110, "low_1": 70, "low_01": 55, "frame_ms": 8.3}, 1000)
            store.record("game.exe", {"fps": 130, "avg": 115, "low_1": 75, "low_01": 60, "frame_ms": 7.9}, 1002)
            store.finish(1005)
            session = store.sessions(1)[0]
            self.assertEqual(session["game"], "game.exe")
            self.assertEqual(session["avg_fps"], 112)
            self.assertEqual(session["peak_fps"], 130)
            self.assertEqual(session["duration_s"], 5)
            again = FpsHistoryStore(Path(tmp) / "history.json")
            self.assertEqual(again.sessions(1)[0]["peak_fps"], 130)

    def test_new_game_closes_previous_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FpsHistoryStore(Path(tmp) / "history.json")
            store.record("one.exe", {"fps": 60, "avg": 60}, 100)
            store.record("two.exe", {"fps": 90, "avg": 90}, 105)
            rows = store.sessions(3)
            self.assertEqual(rows[0]["game"], "two.exe")
            self.assertEqual(rows[1]["game"], "one.exe")
            self.assertEqual(rows[1]["ended_at"], 105)


class NetworkProbeTests(unittest.TestCase):
    def test_host_validation_rejects_command_injection(self):
        with self.assertRaises(ValueError):
            validate_host("1.1.1.1 & whoami")
        self.assertEqual(validate_host("example.com"), "example.com")

    def test_parses_latency(self):
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=0, stdout="Reply from 1.1.1.1: time=14ms", stderr="")
        result = ping_host("1.1.1.1", run=runner)
        self.assertTrue(result.success)
        self.assertEqual(result.latency_ms, 14.0)

    def test_packet_loss_result(self):
        def runner(*args, **kwargs):
            return SimpleNamespace(returncode=1, stdout="Request timed out.", stderr="")
        result = ping_host("1.1.1.1", run=runner)
        self.assertFalse(result.success)
        self.assertIsNone(result.latency_ms)


class UpdateManifestTests(unittest.TestCase):
    def test_version_compare(self):
        self.assertTrue(is_newer_version("1.05.7", "1.05.6"))
        self.assertFalse(is_newer_version("1.05.6", "1.05.6"))
        self.assertFalse(is_newer_version("1.5.5", "1.05.6"))

    def test_manifest_requires_https_and_sha256(self):
        payload = {
            "version": "1.05.7",
            "installer_url": "https://github.com/example/NOVA/releases/download/v1.05.7/NOVA_Setup.exe",
            "sha256": "a" * 64,
            "notes": "New version",
        }
        parsed = validate_manifest(json.dumps(payload).encode())
        self.assertTrue(parsed["is_newer"])
        payload["installer_url"] = "http://example.com/setup.exe"
        with self.assertRaises(ValueError):
            validate_manifest(json.dumps(payload).encode())


if __name__ == "__main__":
    unittest.main()
