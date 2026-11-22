"""Тесты «Выпиления»: разбор списка программ, оценка риска, команды удаления, пакетное удаление, точка восстановления.

Windows не нужна: реестр, PowerShell и запуск процессов подменены.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from system import debloat_rules as rules  # noqa: E402
from system import programs as pr  # noqa: E402
from system import uninstall as un  # noqa: E402

GUID = "{AC76BA86-7AD7-1033-7B44-AC0F074E4100}"


def reg(subkey: str = "Foo", machine: bool = True, **values) -> pr.RegEntry:
    return pr.RegEntry(f"HKLM|64|{subkey}", subkey, machine, values)


def program(**fields) -> pr.Program:
    base = dict(key="HKLM|64|Foo", kind=pr.KIND_WIN32, name="Foo", uninstall=r'"C:\Foo\unins000.exe" /SILENT', machine_wide=True)
    base.update(fields)
    return pr.Program(**base)


class Win32ParsingTests(unittest.TestCase):
    def test_basic_entry(self):
        p = pr.parse_win32(reg(DisplayName="Foo 1.0", Publisher="Foo Inc", DisplayVersion="1.0", EstimatedSize=2048,
                               UninstallString=r'"C:\Foo\unins000.exe"', DisplayIcon=r'"C:\Foo\foo.exe",0'))
        self.assertEqual((p.name, p.publisher, p.version, p.size_bytes), ("Foo 1.0", "Foo Inc", "1.0", 2048 * 1024))
        self.assertEqual((p.kind, p.removable, p.risk, p.machine_wide), ("win32", True, "safe", True))
        self.assertEqual(p.icon_path, r"C:\Foo\foo.exe")
        self.assertEqual(p.kind_label, "Классическая")

    def test_not_programs_are_skipped(self):
        self.assertIsNone(pr.parse_win32(reg(UninstallString="x")))                                   # нет названия
        self.assertIsNone(pr.parse_win32(reg(DisplayName="{ABC}", UninstallString="x")))
        self.assertIsNone(pr.parse_win32(reg(DisplayName="Update for Foo", ParentKeyName="Foo", UninstallString="x")))
        self.assertIsNone(pr.parse_win32(reg(DisplayName="Security Update for Windows (KB5030211)", UninstallString="x")))
        self.assertIsNone(pr.parse_win32(reg(DisplayName="Hotfix", ReleaseType="Hotfix", UninstallString="x")))

    def test_protected_and_commandless_entries_are_not_removable(self):
        locked = pr.parse_win32(reg(DisplayName="Locked", UninstallString="x", NoRemove=1))
        self.assertFalse(locked.removable)
        self.assertIn("запретил", locked.why_not)
        bare = pr.parse_win32(reg(DisplayName="Portable"))
        self.assertFalse(bare.removable)
        self.assertTrue(bare.why_not)

    def test_hidden_system_component(self):
        p = pr.parse_win32(reg(DisplayName="Internal", UninstallString="x", SystemComponent=1))
        self.assertEqual(p.risk, "system")
        self.assertTrue(p.hidden_by_default)

    def test_msi_detection(self):
        by_cmd = pr.parse_win32(reg("Zed", DisplayName="Zed", UninstallString=f"MsiExec.exe /I{GUID}"))
        self.assertEqual(by_cmd.msi_guid, GUID)
        by_key = pr.parse_win32(reg(GUID, DisplayName="Bar", WindowsInstaller=1))
        self.assertEqual(by_key.msi_guid, GUID)
        self.assertTrue(by_key.removable, "у MSI-записи без UninstallString команда строится из GUID")
        plain = pr.parse_win32(reg("Zed", DisplayName="Zed", UninstallString=r"C:\z\uninst.exe"))
        self.assertEqual(plain.msi_guid, "")

    def test_duplicates_in_registry_views_are_merged(self):
        entries = [reg("A", DisplayName="Foo", DisplayVersion="1", UninstallString="u"),
                   pr.RegEntry("HKLM|32|A", "A", True, {"DisplayName": "Foo", "DisplayVersion": "1", "UninstallString": "u"}),
                   reg("B", DisplayName="Foo", DisplayVersion="2", UninstallString="u2")]
        self.assertEqual(len(pr.parse_win32_all(entries)), 2)

    def test_icon_path(self):
        self.assertEqual(pr.icon_path_of(r'"C:\Program Files\X\x.exe",-5'), r"C:\Program Files\X\x.exe")
        self.assertEqual(pr.icon_path_of(r"C:\X\x.ico"), r"C:\X\x.ico")
        self.assertEqual(pr.icon_path_of(r"C:\X\res.dll,3"), "")
        self.assertEqual(pr.icon_path_of(""), "")


class RiskTests(unittest.TestCase):
    def test_win32_rules(self):
        risk = lambda name, pub="": rules.classify_win32(name, pub)[0]  # noqa: E731
        self.assertEqual(risk("Microsoft Visual C++ 2015-2022 Redistributable (x64)"), "careful")
        self.assertEqual(risk("Microsoft .NET Runtime - 8.0.1 (x64)"), "careful")
        self.assertEqual(risk("Microsoft Edge WebView2 Runtime"), "careful")
        self.assertEqual(risk("NVIDIA Графический драйвер 546.33"), "careful")
        self.assertEqual(risk("NVIDIA GeForce Experience"), "careful")
        self.assertEqual(risk("Realtek High Definition Audio Driver"), "careful")
        self.assertEqual(risk("Steam"), "careful")
        self.assertEqual(risk("Easy Anti-Cheat"), "careful")
        self.assertEqual(risk("Kaspersky Free"), "careful")
        self.assertEqual(risk("Discord"), "safe")
        self.assertEqual(risk("Google Chrome"), "safe")
        self.assertEqual(risk("Microsoft Office Professional Plus 2019"), "safe")
        self.assertEqual(risk("Telegram Desktop"), "safe")

    def test_win32_reason_is_given_when_risky(self):
        risk, reason = rules.classify_win32("Steam")
        self.assertEqual(risk, "careful")
        self.assertIn("игр", reason)
        self.assertEqual(rules.classify_win32("Discord"), ("safe", ""))
        self.assertEqual(rules.classify_win32("Whatever", system_component=True)[0], "system")

    def test_appx_known_and_generic(self):
        def c(name, pub="CN=Microsoft Corporation", start=True, fw=False, nr=False, sig="Store"):
            return rules.classify_appx(name, pub, has_start_entry=start, is_framework=fw, non_removable=nr, signature=sig)
        self.assertEqual(c("Microsoft.BingNews")[1], "safe")
        self.assertEqual(c("Microsoft.WindowsStore")[1], "careful")
        self.assertEqual(c("Microsoft.GamingServices", start=False)[1], "careful")
        self.assertEqual(c("Microsoft.VCLibs.140.00", start=False, fw=True)[1], "system")
        self.assertEqual(c("Microsoft.Windows.ShellExperienceHost", nr=True)[1], "system")
        self.assertEqual(c("Microsoft.SomethingNew")[1], "careful")                       # неизвестное от Microsoft — осторожно
        self.assertEqual(c("Microsoft.Windows.Internal", start=False)[1], "system")
        self.assertEqual(c("SpotifyAB.SpotifyMusic", pub="CN=Spotify AB")[1], "safe")
        self.assertEqual(c("Vendor.Helper", pub="CN=Vendor", start=False)[1], "careful")
        self.assertEqual(c("Foo.Bar", pub="CN=Foo", sig="System")[1], "system")

    def test_longest_prefix_wins(self):
        self.assertEqual(rules.known_appx("Microsoft.Windows.Photos_x").display, "Фотографии")
        self.assertEqual(rules.known_appx("Microsoft.Xbox.TCUI").risk, "careful")
        self.assertEqual(rules.known_appx("Microsoft.XboxGamingOverlay").risk, "safe")
        self.assertIsNone(rules.known_appx("Foo.Bar"))

    def test_microsoft_detection(self):
        self.assertTrue(rules.is_microsoft_appx("Microsoft.Todos"))
        self.assertTrue(rules.is_microsoft_appx("Other.App", "CN=Microsoft Corporation"))
        self.assertFalse(rules.is_microsoft_appx("SpotifyAB.SpotifyMusic", "CN=Spotify"))
        self.assertTrue(rules.is_microsoft_win32("Microsoft OneDrive"))
        self.assertTrue(rules.is_microsoft_win32("Teams Machine-Wide Installer", "Microsoft Corporation"))
        self.assertFalse(rules.is_microsoft_win32("Discord", "Discord Inc."))


APPX_JSON = json.dumps({
    "appx": [
        {"Name": "Microsoft.BingNews", "PackageFullName": "Microsoft.BingNews_4.55_x64__8wekyb3d8bbwe",
         "PackageFamilyName": "Microsoft.BingNews_8wekyb3d8bbwe", "Publisher": "CN=Microsoft Corporation, O=Microsoft Corporation",
         "Version": "4.55", "IsFramework": False, "NonRemovable": False, "IsResourcePackage": False, "IsBundle": False,
         "SignatureKind": "Store"},
        {"Name": "SpotifyAB.SpotifyMusic", "PackageFullName": "SpotifyAB.SpotifyMusic_1.2_x86__zpdnekdrzrea0",
         "PackageFamilyName": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0", "Publisher": 'CN="Spotify AB", O="Spotify AB"',
         "Version": "1.2", "IsFramework": False, "NonRemovable": False, "SignatureKind": "Store"},
        {"Name": "Microsoft.VCLibs.140.00", "PackageFullName": "Microsoft.VCLibs.140.00_14.0_x64__8wekyb3d8bbwe",
         "PackageFamilyName": "Microsoft.VCLibs.140.00_8wekyb3d8bbwe", "Publisher": "CN=Microsoft Corporation",
         "IsFramework": True, "NonRemovable": False, "SignatureKind": "Store"},
        {"Name": "Microsoft.Windows.ShellExperienceHost",
         "PackageFullName": "Microsoft.Windows.ShellExperienceHost_1_neutral_neutral_cw5n1h2txyewy",
         "PackageFamilyName": "Microsoft.Windows.ShellExperienceHost_cw5n1h2txyewy", "Publisher": "CN=Microsoft Windows",
         "NonRemovable": True, "SignatureKind": "System"},
        {"Name": "Microsoft.BingNews.Resources", "PackageFullName": "res_1", "PackageFamilyName": "x", "IsResourcePackage": True},
        None,
    ],
    "start": [
        {"Name": "Новости", "AppID": "Microsoft.BingNews_8wekyb3d8bbwe!AppexNews"},
        {"Name": "Spotify", "AppID": "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"},
        {"Name": "Блокнот++", "AppID": r"{6D809377-6AF0-444B-8957-A3773F02200E}\Notepad++\notepad++.exe"},
    ]})


class AppxParsingTests(unittest.TestCase):
    def setUp(self):
        rows, starts = pr.parse_appx_json(APPX_JSON)
        self.programs = {p.package_full_name.split("_")[0]: p for p in pr.parse_appx(rows, starts)}

    def test_resources_and_garbage_are_skipped(self):
        self.assertEqual(set(self.programs), {"Microsoft.BingNews", "SpotifyAB.SpotifyMusic", "Microsoft.VCLibs.140.00",
                                              "Microsoft.Windows.ShellExperienceHost"})

    def test_names_come_from_start_menu(self):
        self.assertEqual(self.programs["Microsoft.BingNews"].name, "Новости")
        self.assertEqual(self.programs["SpotifyAB.SpotifyMusic"].name, "Spotify")
        self.assertEqual(self.programs["Microsoft.VCLibs.140.00"].name, "Microsoft.VCLibs.140.00")

    def test_publisher_is_readable(self):
        self.assertEqual(self.programs["Microsoft.BingNews"].publisher, "Microsoft Corporation")
        self.assertEqual(self.programs["SpotifyAB.SpotifyMusic"].publisher, "Spotify AB")

    def test_risk_and_removable(self):
        news, spotify = self.programs["Microsoft.BingNews"], self.programs["SpotifyAB.SpotifyMusic"]
        self.assertEqual((news.risk, news.microsoft, news.kind_label), ("safe", True, "Microsoft Store"))
        self.assertEqual((spotify.risk, spotify.microsoft), ("safe", False))
        self.assertEqual(self.programs["Microsoft.VCLibs.140.00"].risk, "system")
        shell = self.programs["Microsoft.Windows.ShellExperienceHost"]
        self.assertFalse(shell.removable)
        self.assertTrue(shell.hidden_by_default)

    def test_bad_json(self):
        self.assertEqual(pr.parse_appx_json("не json"), ([], []))
        self.assertEqual(pr.parse_appx_json("[]"), ([], []))
        rows, _ = pr.parse_appx_json('{"appx": {"Name": "One"}, "start": null}')
        self.assertEqual(rows, [{"Name": "One"}])


class ListProgramsTests(unittest.TestCase):
    def test_both_sources_are_merged(self):
        result = pr.list_programs(lambda: [reg(DisplayName="Foo", UninstallString="u")], lambda script: APPX_JSON)
        self.assertEqual(len(result.programs), 5)
        self.assertEqual(result.notes, [])

    def test_failed_registry_does_not_hide_store_apps(self):
        def broken():
            raise OSError("нет доступа")
        result = pr.list_programs(broken, lambda script: APPX_JSON)
        self.assertEqual(len(result.programs), 4)
        self.assertTrue(result.notes)

    def test_failed_powershell_keeps_win32(self):
        def broken(script):
            raise OSError("нет PowerShell")
        result = pr.list_programs(lambda: [reg(DisplayName="Foo", UninstallString="u")], broken)
        self.assertEqual([p.name for p in result.programs], ["Foo"])


class CommandTests(unittest.TestCase):
    def test_split_command(self):
        self.assertEqual(un.split_command(r'"C:\Program Files\X\u.exe" /S /v"/qn"'), (r"C:\Program Files\X\u.exe", '/S /v"/qn"'))
        self.assertEqual(un.split_command(r"C:\Program Files\X\u.exe -uninstall"), (r"C:\Program Files\X\u.exe", "-uninstall"))
        self.assertEqual(un.split_command("MsiExec.exe /X{AAA}"), ("MsiExec.exe", "/X{AAA}"))
        self.assertEqual(un.split_command("msiexec /x{AAA}"), ("msiexec", "/x{AAA}"))
        self.assertEqual(un.split_command(r"C:\x\setup.exe"), (r"C:\x\setup.exe", ""))

    def test_msi_command_is_built_from_guid(self):
        silent = un.build_command(program(msi_guid=GUID, uninstall=f"MsiExec.exe /I{GUID}"), True)
        self.assertEqual((silent.line, silent.silent, silent.kind), (f"msiexec.exe /x {GUID} /qn /norestart", True, "msi"))
        visible = un.build_command(program(msi_guid=GUID), False)
        self.assertIn("/qb", visible.line)
        self.assertNotIn("/qn", visible.line)

    def test_bad_guid_is_never_put_into_command(self):
        cmd = un.build_command(program(msi_guid="{x} & calc", uninstall=r"C:\a\u.exe"), True)
        self.assertEqual(cmd.kind, "own")
        self.assertNotIn("calc", cmd.line)

    def test_quiet_string_only_in_silent_mode(self):
        p = program(uninstall=r"C:\a\u.exe", quiet_uninstall=r'"C:\a\u.exe" /VERYSILENT')
        self.assertEqual(un.build_command(p, True).kind, "quiet")
        shown = un.build_command(p, False)
        self.assertEqual((shown.line, shown.silent), (r"C:\a\u.exe", False))

    def test_program_without_quiet_flag_runs_visible_even_in_silent_mode(self):
        cmd = un.build_command(program(uninstall=r"C:\a\u.exe"), True)
        self.assertFalse(cmd.silent)

    def test_nothing_to_run(self):
        self.assertIsNone(un.build_command(program(uninstall="", quiet_uninstall=""), True))
        self.assertIsNone(un.build_command(program(uninstall="a.exe\n calc"), True))

    def test_appx_scripts_validate_names(self):
        full = "Microsoft.BingNews_4.55_x64__8wekyb3d8bbwe"
        self.assertIn(full, un.remove_appx_script(full))
        self.assertIn("-Name 'Microsoft.BingNews'", un.appx_present_script(full))
        for bad in ("x'; calc; '", "a b", "", "ab"):
            with self.assertRaises(ValueError):
                un.remove_appx_script(bad)
            with self.assertRaises(ValueError):
                un.appx_present_script(bad)

    def test_appx_error_texts(self):
        self.assertIn("защищено", un.explain_appx_error("ERR Removal failed 0x80073CFA"))
        self.assertIn("зависят", un.explain_appx_error("ERR ... 0x80073D19 ..."))
        self.assertIn("администратора", un.explain_appx_error("ERR Access is denied"))
        self.assertEqual(un.explain_appx_error("ERR что-то своё\nвторая строка"), "ERR что-то своё")
        self.assertTrue(un.explain_appx_error(""))


class FakeRunner:
    """Подменяет Windows: запоминает команды; «установленные» программы живут в множестве registry."""

    def __init__(self):
        self.registry: set[str] = set()
        self.appx: set[str] = set()
        self.commands: list[tuple[str, bool, bool]] = []
        self.scripts: list[tuple[str, bool]] = []
        self.on_command = None              # функция (command) -> RunResult
        self.ps_override = None             # функция (script, elevate) -> RunResult | None

    def run_command(self, command, elevate, visible, timeout_s):
        self.commands.append((command, elevate, visible))
        return self.on_command(command)

    def run_powershell(self, script, elevate, timeout_s):
        self.scripts.append((script, elevate))
        if self.ps_override:
            res = self.ps_override(script, elevate)
            if res is not None:
                return res
        full = next((a for a in self.appx if a in script), "")
        if script.startswith("try { Remove-AppxPackage"):
            self.appx.discard(full)
            return un.RunResult(True, 0, "OK\n")
        if "Get-AppxPackage -Name" in script:
            return un.RunResult(True, 0, f"{1 if full else 0}\n")
        return un.RunResult(True, 0, "")


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class UninstallerTests(unittest.TestCase):
    def setUp(self):
        self.runner, self.clock = FakeRunner(), Clock()
        self.runner.registry = {"HKLM|64|Foo", "HKLM|64|Bar"}
        self.u = un.Uninstaller(self.runner, lambda key: key in self.runner.registry, self.clock.sleep, self.clock, settle_s=5)

    def ok_and_remove(self, key):
        def run(command):
            self.runner.registry.discard(key)
            return un.RunResult(True, 0)
        self.runner.on_command = run

    def test_success_is_confirmed_by_registry(self):
        self.ok_and_remove("HKLM|64|Foo")
        res = self.u.uninstall(program())
        self.assertEqual((res.status, res.message), (un.STATUS_REMOVED, "Удалено."))
        command, elevate, visible = self.runner.commands[0]
        self.assertEqual((elevate, visible), (True, True), "без тихой команды окно программы удаления показывается")

    def test_quiet_command_runs_hidden(self):
        self.ok_and_remove("HKLM|64|Foo")
        self.u.uninstall(program(quiet_uninstall=r'"C:\Foo\unins000.exe" /VERYSILENT'))
        self.assertEqual(self.runner.commands[0], (r'"C:\Foo\unins000.exe" /VERYSILENT', True, False))

    def test_user_scope_program_is_not_elevated(self):
        self.runner.registry = {"HKCU|0|Foo"}
        self.runner.on_command = lambda c: (self.runner.registry.clear() or un.RunResult(True, 0))
        self.u.uninstall(program(key="HKCU|0|Foo", machine_wide=False))
        self.assertFalse(self.runner.commands[0][1])

    def test_detached_uninstaller_is_waited_for(self):
        """Удалитель сразу завершился (код 0), а запись пропала только через несколько секунд."""
        def run(command):
            self.clock.now = 0
            return un.RunResult(True, 0)
        self.runner.on_command = run
        original = self.u._present

        def present(key):
            if self.clock.now >= 3:
                self.runner.registry.discard(key)
            return original(key)
        self.u._present = present
        self.assertEqual(self.u.uninstall(program()).status, un.STATUS_REMOVED)
        self.assertGreaterEqual(self.clock.now, 3)

    def test_still_listed_means_failed_not_success(self):
        self.runner.on_command = lambda c: un.RunResult(True, 0)
        res = self.u.uninstall(program())
        self.assertEqual(res.status, un.STATUS_FAILED)
        self.assertIn("осталась в списке", res.message)
        self.assertGreaterEqual(self.clock.now, 5, "ждали положенное время, прежде чем признать неудачу")

    def test_exit_code_is_reported(self):
        self.runner.on_command = lambda c: un.RunResult(True, 2)
        self.assertIn("кодом 2", self.u.uninstall(program()).message)
        self.runner.on_command = lambda c: un.RunResult(True, 1618)
        msi = self.u.uninstall(program(msi_guid=GUID))
        self.assertIn("другая установка", msi.message)

    def test_reboot_code_with_removed_entry(self):
        def run(command):
            self.runner.registry.discard("HKLM|64|Foo")
            return un.RunResult(True, 3010)
        self.runner.on_command = run
        res = self.u.uninstall(program(msi_guid=GUID))
        self.assertEqual(res.status, un.STATUS_REMOVED)
        self.assertTrue(res.reboot)
        self.assertIn("перезагрузка", res.message)

    def test_declined_uac(self):
        self.runner.on_command = lambda c: un.RunResult(False, error="Вы отказались от запроса прав администратора (UAC).",
                                                        declined=True)
        self.assertEqual(self.u.uninstall(program()).status, un.STATUS_DECLINED)

    def test_cannot_start(self):
        self.runner.on_command = lambda c: un.RunResult(False, error="Не удалось запустить программу удаления: файл не найден")
        res = self.u.uninstall(program())
        self.assertEqual(res.status, un.STATUS_FAILED)
        self.assertIn("файл не найден", res.message)

    def test_timeout_is_not_called_success(self):
        self.runner.on_command = lambda c: un.RunResult(True, timed_out=True)
        res = self.u.uninstall(program())
        self.assertEqual(res.status, un.STATUS_FAILED)
        self.assertIn("долго", res.message)

    def test_protected_program_is_skipped_without_running_anything(self):
        res = self.u.uninstall(program(removable=False, why_not="Windows не разрешает"))
        self.assertEqual((res.status, res.message), (un.STATUS_SKIPPED, "Windows не разрешает"))
        self.assertEqual(self.runner.commands, [])

    def test_appx_removed_and_verified(self):
        full = "Microsoft.BingNews_4.55_x64__8wekyb3d8bbwe"
        self.runner.appx = {full}
        p = pr.Program(key=full, kind=pr.KIND_APPX, name="Новости", package_full_name=full)
        res = self.u.uninstall(p)
        self.assertEqual(res.status, un.STATUS_REMOVED)
        self.assertFalse(self.runner.scripts[0][1], "Store-приложения удаляются для текущего пользователя, без UAC")

    def test_appx_failure_is_explained(self):
        full = "Microsoft.Foo_1_x64__abc"
        self.runner.appx = {full}
        self.runner.ps_override = lambda s, e: (un.RunResult(True, 1, "ERR Removal failed 0x80073CFA\n")
                                                if s.startswith("try { Remove") else None)
        p = pr.Program(key=full, kind=pr.KIND_APPX, name="Foo", package_full_name=full)
        res = self.u.uninstall(p)
        self.assertEqual(res.status, un.STATUS_FAILED)
        self.assertIn("защищено", res.message)

    def test_appx_command_says_ok_but_package_is_still_there(self):
        full = "Microsoft.Foo_1_x64__abc"
        self.runner.appx = {full}
        self.runner.ps_override = lambda s, e: un.RunResult(True, 0, "OK\n") if s.startswith("try { Remove") else None
        p = pr.Program(key=full, kind=pr.KIND_APPX, name="Foo", package_full_name=full)
        self.assertEqual(self.u.uninstall(p).status, un.STATUS_FAILED, "успех определяется по системе, а не по ответу команды")

    def test_appx_unknown_presence_falls_back_to_command_result(self):
        full = "Microsoft.Foo_1_x64__abc"
        self.runner.ps_override = lambda s, e: (un.RunResult(True, 0, "")
                                                if "Get-AppxPackage -Name" in s else un.RunResult(True, 0, "OK\n"))
        p = pr.Program(key=full, kind=pr.KIND_APPX, name="Foo", package_full_name=full)
        self.assertEqual(self.u.uninstall(p).status, un.STATUS_REMOVED)
        self.runner.ps_override = lambda s, e: (un.RunResult(True, 0, "")
                                                if "Get-AppxPackage -Name" in s else un.RunResult(True, 1, "ERR boom\n"))
        self.assertEqual(self.u.uninstall(p).status, un.STATUS_FAILED)

    def test_powershell_missing(self):
        self.runner.ps_override = lambda s, e: un.RunResult(False, error="PowerShell недоступен")
        p = pr.Program(key="Microsoft.Foo_1_x64__abc", kind=pr.KIND_APPX, name="Foo", package_full_name="Microsoft.Foo_1_x64__abc")
        self.assertEqual(self.u.uninstall(p).status, un.STATUS_FAILED)


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.runner, self.clock = FakeRunner(), Clock()
        self.progs = [program(key=f"HKLM|64|P{i}", name=f"P{i}") for i in range(4)]
        self.runner.registry = {p.key for p in self.progs}
        self.u = un.Uninstaller(self.runner, lambda key: key in self.runner.registry, self.clock.sleep, self.clock, settle_s=2)

    def test_all_results_in_order_with_progress(self):
        progress, done = [], []
        self.runner.on_command = lambda c: (self.runner.registry.discard("HKLM|64|P1") or un.RunResult(True, 0))
        results = self.u.uninstall_many(self.progs[:2], True, lambda i, n, p: progress.append((i, n, p.name)), done.append)
        self.assertEqual(progress, [(0, 2, "P0"), (1, 2, "P1")])
        self.assertEqual([r.status for r in results], [un.STATUS_FAILED, un.STATUS_REMOVED])    # P0 в реестре остался
        self.assertEqual([r.program.name for r in done], ["P0", "P1"])

    def test_cancel_stops_after_current_and_reports_rest(self):
        calls = {"n": 0}

        def run(command):
            calls["n"] += 1
            return un.RunResult(True, 0)
        self.runner.on_command = run
        results = self.u.uninstall_many(self.progs, True, cancelled=lambda: calls["n"] >= 2)
        self.assertEqual(calls["n"], 2)
        self.assertEqual([r.status for r in results][2:], [un.STATUS_CANCELLED, un.STATUS_CANCELLED])
        self.assertEqual(len(results), 4)

    def test_one_crash_does_not_stop_the_rest(self):
        self.progs = [program(key=p.key, name=p.name, uninstall=rf'"C:\\{p.name}\\u.exe"') for p in self.progs]

        def run(command):
            if "P0" in command:
                raise RuntimeError("сломалось")
            return un.RunResult(True, 0)
        self.runner.on_command = run
        results = self.u.uninstall_many(self.progs, True)
        self.assertEqual(results[0].status, un.STATUS_FAILED)
        self.assertIn("Внутренняя ошибка", results[0].message)
        self.assertEqual(len(results), 4)
        self.assertEqual(len(self.runner.commands), 4, "остальные программы всё равно обработаны")


class RestorePointTests(unittest.TestCase):
    def run_with(self, result):
        runner = FakeRunner()
        runner.ps_override = lambda s, e: result
        return un.create_restore_point(runner), runner

    def test_created(self):
        res, runner = self.run_with(un.RunResult(True, 0, "OK\n"))
        self.assertTrue(res.ok)
        self.assertTrue(runner.scripts[0][1], "точка восстановления создаётся с повышением прав")
        self.assertIn("Checkpoint-Computer", runner.scripts[0][0])

    def test_once_a_day_rule(self):
        res, _ = self.run_with(un.RunResult(True, 0, "WARN A new system restore point cannot be created because one has "
                                                     "already been created within the past 1440 minutes.\n"))
        self.assertFalse(res.ok)
        self.assertIn("раза в сутки", res.message)

    def test_protection_disabled(self):
        res, _ = self.run_with(un.RunResult(True, 0, "ERR System Restore is disabled on this drive\n"))
        self.assertFalse(res.ok)
        self.assertIn("Защита системы выключена", res.message)

    def test_declined_and_unavailable(self):
        self.assertIn("отказались", self.run_with(un.RunResult(False, error="x", declined=True))[0].message)
        self.assertEqual(self.run_with(un.RunResult(False, error="PowerShell недоступен"))[0].message, "PowerShell недоступен")

    def test_other_error_text_is_shown(self):
        res, _ = self.run_with(un.RunResult(True, 1, "ERR Что-то пошло не так\n"))
        self.assertFalse(res.ok)
        self.assertIn("Что-то пошло не так", res.message)

    def test_description_quotes_are_escaped(self):
        self.assertIn("O''Brien", un.restore_point_script("O'Brien"))


class ConfirmationTests(unittest.TestCase):
    def test_selection_helpers(self):
        safe = [program(name=f"S{i}", key=str(i)) for i in range(3)]
        careful = program(name="C", key="c", risk="careful")
        locked = program(name="L", key="l", removable=False)
        self.assertEqual(un.default_selection(safe + [careful, locked]), safe)
        self.assertFalse(un.needs_risk_ack(safe))
        self.assertTrue(un.needs_risk_ack(safe + [careful]))
        self.assertFalse(un.needs_typed_confirm(safe))
        self.assertTrue(un.needs_typed_confirm(safe + [careful]))
        many = [program(name=f"M{i}", key=f"m{i}") for i in range(un.BIG_BATCH + 1)]
        self.assertTrue(un.needs_typed_confirm(many))
        self.assertEqual(un.risk_counts(safe + [careful]), {"safe": 3, "careful": 1})


class FilterTests(unittest.TestCase):
    def setUp(self):
        self.items = [program(name="Discord", key="d", publisher="Discord Inc."),
                      program(name="Microsoft Visual C++ 2015 Redistributable", key="v", risk="careful", microsoft=True,
                              publisher="Microsoft Corporation"),
                      pr.Program(key="n", kind=pr.KIND_APPX, name="Новости", microsoft=True, package_full_name="a_b_c"),
                      program(name="Internal", key="i", risk="system"),
                      program(name="Locked", key="l", removable=False, why_not="нельзя")]

    def names(self, **kw):
        return [p.name for p in self.items if pr.program_matches(p, **kw)]

    def test_hidden_items_need_the_checkbox(self):
        self.assertEqual(self.names(), ["Discord", "Microsoft Visual C++ 2015 Redistributable", "Новости"])
        self.assertEqual(len(self.names(show_hidden=True)), 5)

    def test_groups(self):
        self.assertEqual(self.names(group="win32"), ["Discord", "Microsoft Visual C++ 2015 Redistributable"])
        self.assertEqual(self.names(group="appx"), ["Новости"])
        self.assertEqual(self.names(group="microsoft"), ["Microsoft Visual C++ 2015 Redistributable", "Новости"])
        self.assertEqual(self.names(group="safe"), ["Discord", "Новости"])
        self.assertEqual(self.names(group="careful"), ["Microsoft Visual C++ 2015 Redistributable"])
        self.assertEqual(self.names(group="careful", show_hidden=True),
                         ["Microsoft Visual C++ 2015 Redistributable", "Internal"])

    def test_search_by_name_and_publisher_ignores_case(self):
        self.assertEqual(self.names(query="  DISCORD "), ["Discord"])
        self.assertEqual(self.names(query="microsoft corp"), ["Microsoft Visual C++ 2015 Redistributable"])
        self.assertEqual(self.names(query="новос"), ["Новости"])
        self.assertEqual(self.names(query="нет такого"), [])

    def test_filter_list_has_every_group(self):
        self.assertEqual([key for key, _t in pr.FILTERS], ["all", "win32", "appx", "microsoft", "safe", "careful"])

    def test_confirm_button_logic(self):
        safe = [program(name="S", key="s")]
        careful = safe + [program(name="C", key="c", risk="careful")]
        many = [program(name=f"M{i}", key=f"m{i}") for i in range(6)]
        self.assertFalse(un.confirm_ready([], True, "УДАЛИТЬ"))
        self.assertTrue(un.confirm_ready(safe, False, ""))
        self.assertFalse(un.confirm_ready(careful, False, "УДАЛИТЬ"), "рискованное: нужна галочка")
        self.assertFalse(un.confirm_ready(careful, True, ""), "рискованное: нужно слово")
        self.assertTrue(un.confirm_ready(careful, True, " удалить "))
        self.assertFalse(un.confirm_ready(many, False, "да"))
        self.assertTrue(un.confirm_ready(many, False, "УДАЛИТЬ"))


class SummaryTests(unittest.TestCase):
    def test_summary_text(self):
        a, b = program(name="A", key="a"), program(name="B", key="b")
        results = [un.UninstallResult(a, un.STATUS_REMOVED, "Удалено.", True), un.UninstallResult(b, un.STATUS_FAILED, "x"),
                   un.UninstallResult(b, un.STATUS_CANCELLED, "y")]
        text = un.summarize(results)
        self.assertIn("удалено — 1", text)
        self.assertIn("не удалось — 1", text)
        self.assertIn("остановлено вами — 1", text)
        self.assertIn("перезагрузите", text)
        self.assertNotIn("перезагрузите", un.summarize(results[1:]))
        self.assertIn("ничего", un.summarize([]))


class NonWindowsTests(unittest.TestCase):
    @unittest.skipIf(sys.platform == "win32", "проверяется поведение вне Windows")
    def test_real_runner_refuses_politely(self):
        runner = un.WindowsRunner()
        res = runner.run_command("calc.exe", True, True, 1)
        self.assertFalse(res.started)
        self.assertTrue(res.error)
        self.assertFalse(runner.run_powershell("1", False, 1).started)
        self.assertEqual(pr.read_registry_entries(), [])
        self.assertFalse(pr.registry_key_present("HKLM|64|x"))


if __name__ == "__main__":
    unittest.main()
