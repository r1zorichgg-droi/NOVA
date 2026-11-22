"""Удаление программ: классических (штатные команды удаления из реестра, MSI) и приложений Microsoft Store.

NOVA ничего не «выпиливает» сама: она запускает ровно те команды, которые Windows использует в «Параметры → Приложения»
(UninstallString / msiexec /x / Remove-AppxPackage), и проверяет по системе, что программа действительно исчезла.
Защиту Windows (NonRemovable, NoRemove) NOVA не обходит.

Всё, что касается Windows (запуск процессов, UAC, PowerShell), вынесено в Runner — поэтому логика проверяется
тестами с подставным Runner.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from . import winapi
from .debloat_rules import RISK_SAFE
from .programs import APPX_FULLNAME, GUID_FULL, KIND_APPX, Program, registry_key_present

log = logging.getLogger(__name__)

STATUS_REMOVED, STATUS_FAILED, STATUS_SKIPPED = "removed", "failed", "skipped"
STATUS_DECLINED, STATUS_CANCELLED = "declined", "cancelled"

SILENT_TIMEOUT_S, VISIBLE_TIMEOUT_S, APPX_TIMEOUT_S = 1200.0, 3600.0, 300.0
SETTLE_S = 30.0                 # сколько ждать исчезновения записи, если программа удаления завершилась сразу
REBOOT_CODES = (3010, 1641)     # Windows Installer: «нужна перезагрузка»
MSI_MESSAGES = {
    1602: "Удаление отменено.",
    1603: "Windows Installer сообщил о фатальной ошибке (1603).",
    1605: "Windows Installer: такой программы уже нет, но запись о ней осталась (1605).",
    1618: "Сейчас идёт другая установка или удаление — повторите позже (1618).",
    1619: "Установочный пакет программы не найден (1619): удалить её штатно нельзя.",
    1620: "Установочный пакет программы повреждён (1620).",
    1925: "Не хватает прав администратора (1925).",
}
CONFIRM_WORD = "УДАЛИТЬ"
BIG_BATCH = 5                   # больше стольких программ за раз — подтверждение словом


# --------------------------------------------------------------------------------------
# Запуск процессов (граница с Windows)
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RunResult:
    started: bool
    exit_code: int | None = None
    output: str = ""
    error: str = ""                 # причина, если started == False
    declined: bool = False          # пользователь отказал в запросе UAC
    timed_out: bool = False


class Runner(Protocol):
    def run_command(self, command: str, elevate: bool, visible: bool, timeout_s: float) -> RunResult: ...

    def run_powershell(self, script: str, elevate: bool, timeout_s: float) -> RunResult: ...


def split_command(command: str) -> tuple[str, str]:
    """Командная строка Windows -> (файл, параметры); нужна для запуска с повышением прав."""
    text = command.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
        return text[1:], ""
    match = re.search(r"\.(exe|bat|cmd|com|msi)(?=\s|$)", text, re.I)
    if match:
        return text[:match.end()], text[match.end():].strip()
    first, _, rest = text.partition(" ")
    return first, rest.strip()


_UTF8 = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "


class WindowsRunner:
    """Настоящий запуск: subprocess, а при необходимости — ShellExecuteEx с запросом UAC."""

    def run_command(self, command: str, elevate: bool, visible: bool, timeout_s: float) -> RunResult:
        if not winapi.IS_WINDOWS:
            return RunResult(False, error="Удаление программ доступно только в Windows.")
        if not command.strip() or any(ch in command for ch in "\r\n\0"):
            return RunResult(False, error="Некорректная команда удаления.")
        if elevate and not winapi.is_admin():
            return self._elevated(command, visible, timeout_s)
        try:
            proc = subprocess.Popen(  # noqa: S603 — команду задаёт сама Windows (запись реестра), как в «Параметрах»
                command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=0 if visible else getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            if getattr(exc, "winerror", 0) == 740:           # программа сама требует права администратора
                return self._elevated(command, visible, timeout_s)
            return RunResult(False, error=f"Не удалось запустить программу удаления: {exc.strerror or exc}")
        try:
            return RunResult(True, proc.wait(timeout=timeout_s))
        except subprocess.TimeoutExpired:
            return RunResult(True, timed_out=True)          # не убиваем: прерванное удаление хуже долгого

    @staticmethod
    def _elevated(command: str, visible: bool, timeout_s: float) -> RunResult:
        file, params = split_command(command)
        return _from_exec(winapi.shell_execute_wait(file, params, elevate=True, show=visible, timeout_s=timeout_s))

    def run_powershell(self, script: str, elevate: bool, timeout_s: float) -> RunResult:
        if not winapi.IS_WINDOWS:
            return RunResult(False, error="PowerShell доступен только в Windows.")
        if elevate and not winapi.is_admin():
            return self._powershell_elevated(script, timeout_s)
        try:
            proc = subprocess.run(  # noqa: S603, S607
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", _UTF8 + script],
                capture_output=True, timeout=timeout_s, stdin=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return RunResult(True, timed_out=True)
        except OSError as exc:
            return RunResult(False, error=f"PowerShell недоступен: {exc.strerror or exc}")
        out = proc.stdout.decode("utf-8", errors="replace")
        err = proc.stderr.decode("utf-8", errors="replace")
        return RunResult(True, proc.returncode, out, err[:500])

    @staticmethod
    def _powershell_elevated(script: str, timeout_s: float) -> RunResult:
        """PowerShell от администратора; вывод передаётся через временный файл (у такого процесса нет общего канала)."""
        fd, path = tempfile.mkstemp(prefix="nova_ps_", suffix=".txt")
        os.close(fd)
        quoted = path.replace("'", "''")
        wrapped = (_UTF8 + f"$r = (& {{ {script} }}) 2>&1 | Out-String; "
                           f"Set-Content -LiteralPath '{quoted}' -Value $r -Encoding UTF8")
        encoded = base64.b64encode(wrapped.encode("utf-16-le")).decode("ascii")
        try:
            result = _from_exec(winapi.shell_execute_wait(
                "powershell.exe", f"-NoProfile -NonInteractive -WindowStyle Hidden -EncodedCommand {encoded}",
                elevate=True, show=False, timeout_s=timeout_s))
            try:
                with open(path, encoding="utf-8-sig", errors="replace") as handle:
                    output = handle.read()
            except OSError:
                output = ""
            return RunResult(result.started, result.exit_code, output, result.error, result.declined, result.timed_out)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


def _from_exec(result: winapi.ExecResult) -> RunResult:
    if result.started:
        return RunResult(True, result.exit_code, timed_out=result.timed_out)
    if result.error == winapi.ERROR_CANCELLED:
        return RunResult(False, error="Вы отказались от запроса прав администратора (UAC).", declined=True)
    return RunResult(False, error=f"Windows не смогла запустить программу удаления (ошибка {result.error}).")


# --------------------------------------------------------------------------------------
# Команды
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Command:
    line: str
    silent: bool                    # выполняется без окон
    kind: str                       # "msi" | "quiet" | "own"


def build_command(program: Program, silent: bool = True) -> Command | None:
    """Какую команду запускать. Тихий режим — только там, где программа сама его предусмотрела (MSI, QuietUninstallString)."""
    if program.msi_guid and GUID_FULL.match(program.msi_guid):
        return Command(f"msiexec.exe /x {program.msi_guid} {'/qn' if silent else '/qb'} /norestart", silent, "msi")
    if silent and program.quiet_uninstall:
        return Command(program.quiet_uninstall, True, "quiet")
    line = program.uninstall or program.quiet_uninstall
    if line and not any(ch in line for ch in "\r\n\0"):
        return Command(line, False, "own")
    return None


def remove_appx_script(package_full_name: str) -> str:
    if not APPX_FULLNAME.match(package_full_name):
        raise ValueError("недопустимое имя пакета")
    return (f"try {{ Remove-AppxPackage -Package '{package_full_name}' -ErrorAction Stop; 'OK' }} "
            f"catch {{ 'ERR ' + $_.Exception.Message; exit 1 }}")


def appx_present_script(package_full_name: str) -> str:
    if not APPX_FULLNAME.match(package_full_name):
        raise ValueError("недопустимое имя пакета")
    name = package_full_name.split("_", 1)[0]
    return f"@(Get-AppxPackage -Name '{name}' | Where-Object {{ $_.PackageFullName -eq '{package_full_name}' }}).Count"


def explain_appx_error(text: str) -> str:
    low = text.lower()
    if "0x80073cfa" in low:
        return "Windows не разрешила удалить это приложение (оно защищено или встроено в систему)."
    if "0x80073d19" in low:
        return "От этого компонента зависят другие приложения — сначала удалите их."
    if "0x80070005" in low or "access is denied" in low or "отказано в доступе" in low:
        return "Нет доступа: для этого приложения нужны права администратора."
    if "0x80073cf1" in low:
        return "Приложение уже удалено."
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return line[:200] if line else "Windows не смогла удалить приложение."


# --------------------------------------------------------------------------------------
# Удаление
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class UninstallResult:
    program: Program
    status: str
    message: str
    reboot: bool = False


class Uninstaller:
    def __init__(self, runner: Runner, registry_present: Callable[[str], bool] = registry_key_present,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 settle_s: float = SETTLE_S) -> None:
        self._runner, self._present = runner, registry_present
        self._sleep, self._clock, self._settle = sleep, clock, settle_s

    # -- одна программа ----------------------------------------------------------
    def uninstall(self, program: Program, silent: bool = True) -> UninstallResult:
        if not program.removable:
            return UninstallResult(program, STATUS_SKIPPED, program.why_not or "Windows не разрешает удалять это.")
        if program.kind == KIND_APPX:
            return self._remove_appx(program)
        return self._remove_win32(program, silent)

    def _remove_win32(self, program: Program, silent: bool) -> UninstallResult:
        command = build_command(program, silent)
        if command is None:
            return UninstallResult(program, STATUS_SKIPPED, "Нет команды удаления.")
        timeout = SILENT_TIMEOUT_S if command.silent else VISIBLE_TIMEOUT_S
        run = self._runner.run_command(command.line, program.machine_wide, not command.silent, timeout)
        if run.declined:
            return UninstallResult(program, STATUS_DECLINED, run.error)
        if not run.started:
            return UninstallResult(program, STATUS_FAILED, run.error or "Не удалось запустить программу удаления.")
        reboot = run.exit_code in REBOOT_CODES
        if self._wait_until_gone(program.key):
            note = " Для завершения нужна перезагрузка Windows." if reboot else ""
            return UninstallResult(program, STATUS_REMOVED, "Удалено." + note, reboot)
        if run.timed_out:
            return UninstallResult(program, STATUS_FAILED, "Программа удаления работает слишком долго — NOVA её не прерывала; "
                                                           "проверьте, не ждёт ли она ответа в своём окне.")
        code = run.exit_code
        if command.kind == "msi" and code in MSI_MESSAGES:
            return UninstallResult(program, STATUS_FAILED, MSI_MESSAGES[code])
        if code not in (0, None):
            return UninstallResult(program, STATUS_FAILED,
                                   f"Программа удаления завершилась с кодом {code}, но программа осталась в списке.")
        return UninstallResult(program, STATUS_FAILED, "Программа осталась в списке установленных — возможно, удаление "
                                                       "отменили в окне программы.")

    def _wait_until_gone(self, key: str) -> bool:
        """Многие программы удаления сразу завершаются, а работают дальше в другом процессе, — поэтому ждём запись."""
        deadline = self._clock() + self._settle
        while self._present(key):
            if self._clock() >= deadline:
                return False
            self._sleep(1.0)
        return True

    def _remove_appx(self, program: Program) -> UninstallResult:
        try:
            script = remove_appx_script(program.package_full_name)
        except ValueError:
            return UninstallResult(program, STATUS_FAILED, "Недопустимое имя пакета.")
        run = self._runner.run_powershell(script, False, APPX_TIMEOUT_S)
        if not run.started:
            return UninstallResult(program, STATUS_FAILED, run.error or "PowerShell недоступен.")
        reported_ok = run.exit_code == 0 and any(ln.strip() == "OK" for ln in run.output.splitlines())
        present = self._appx_present(program)
        if present is False or (present is None and reported_ok):
            return UninstallResult(program, STATUS_REMOVED, "Удалено.")
        if run.timed_out:
            return UninstallResult(program, STATUS_FAILED, "Windows слишком долго удаляет приложение; проверьте список позже.")
        return UninstallResult(program, STATUS_FAILED, explain_appx_error(run.output + "\n" + run.error))

    def _appx_present(self, program: Program) -> bool | None:
        """True/False — по данным Windows; None — проверить не удалось."""
        run = self._runner.run_powershell(appx_present_script(program.package_full_name), False, 60.0)
        text = run.output.strip().splitlines()[-1].strip() if run.started and run.output.strip() else ""
        return int(text) > 0 if text.isdigit() else None

    # -- пачка ----------------------------------------------------------------------
    def uninstall_many(self, programs: Sequence[Program], silent: bool = True,
                       on_progress: Callable[[int, int, Program], None] | None = None,
                       on_result: Callable[[UninstallResult], None] | None = None,
                       cancelled: Callable[[], bool] | None = None) -> list[UninstallResult]:
        results: list[UninstallResult] = []
        total = len(programs)
        for index, program in enumerate(programs):
            if cancelled is not None and cancelled():
                for rest in programs[index:]:
                    skipped = UninstallResult(rest, STATUS_CANCELLED, "Пропущено: вы остановили удаление.")
                    results.append(skipped)
                    if on_result:
                        on_result(skipped)
                break
            if on_progress:
                on_progress(index, total, program)
            try:
                result = self.uninstall(program, silent)
            except Exception as exc:  # noqa: BLE001 — сбой одной программы не должен прерывать остальные
                log.exception("Сбой при удалении %s", program.name)
                result = UninstallResult(program, STATUS_FAILED, f"Внутренняя ошибка: {exc}")
            results.append(result)
            if on_result:
                on_result(result)
        return results


# --------------------------------------------------------------------------------------
# Точка восстановления
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class RestorePointResult:
    ok: bool
    message: str


def restore_point_script(description: str) -> str:
    desc = description.replace("'", "''")
    return ("$w = $null; try { Checkpoint-Computer -Description '" + desc + "' -RestorePointType MODIFY_SETTINGS "
            "-WarningVariable w -WarningAction SilentlyContinue -ErrorAction Stop; "
            "if ($w) { 'WARN ' + ($w -join ' ') } else { 'OK' } } catch { 'ERR ' + $_.Exception.Message }")


def create_restore_point(runner: Runner, description: str = "NOVA: перед удалением программ") -> RestorePointResult:
    """Точка восстановления Windows (нужны права администратора — Windows покажет запрос UAC)."""
    run = runner.run_powershell(restore_point_script(description), True, 240.0)
    if run.declined:
        return RestorePointResult(False, "Вы отказались от запроса прав администратора.")
    if not run.started:
        return RestorePointResult(False, run.error or "PowerShell недоступен.")
    lines = [ln.strip() for ln in run.output.splitlines() if ln.strip()]
    if any(ln == "OK" for ln in lines):
        return RestorePointResult(True, "Точка восстановления создана.")
    text = next((ln for ln in lines if ln.startswith(("WARN", "ERR"))), "")
    low = text.lower()
    if "1440" in low or "already been created" in low:
        return RestorePointResult(False, "Windows создаёт точки восстановления не чаще раза в сутки: свежая точка уже есть, "
                                         "а новая не создана.")
    if "disabled" in low or "отключ" in low or "turned off" in low:
        return RestorePointResult(False, "Защита системы выключена, точку восстановления создать нельзя "
                                         "(включить: «Параметры → Система → О системе → Защита системы»).")
    if run.timed_out:
        return RestorePointResult(False, "Windows слишком долго создаёт точку восстановления.")
    detail = text.split(" ", 1)[1][:200] if " " in text else ""
    return RestorePointResult(False, "Точку восстановления создать не удалось" + (f": {detail}" if detail else "."))


# --------------------------------------------------------------------------------------
# Подтверждение
# --------------------------------------------------------------------------------------
def risk_counts(programs: Sequence[Program]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for program in programs:
        counts[program.risk] = counts.get(program.risk, 0) + 1
    return counts


def needs_risk_ack(programs: Sequence[Program]) -> bool:
    """Нужна отдельная галочка «понимаю риск»: в выборе есть «Осторожно» или «Системное»."""
    return any(p.risk != RISK_SAFE for p in programs)


def needs_typed_confirm(programs: Sequence[Program]) -> bool:
    """Нужно ввести слово-подтверждение: удаляется много программ сразу или есть рискованные."""
    return len(programs) > BIG_BATCH or needs_risk_ack(programs)


def default_selection(programs: Sequence[Program]) -> list[Program]:
    """«Выбрать всё безопасное»: только удаляемое и без пометки риска."""
    return [p for p in programs if p.removable and p.risk == RISK_SAFE]


def confirm_ready(programs: Sequence[Program], risk_ack: bool, typed: str) -> bool:
    """Можно ли нажимать «Удалить» в окне подтверждения."""
    if not programs:
        return False
    if needs_risk_ack(programs) and not risk_ack:
        return False
    if needs_typed_confirm(programs) and typed.strip().casefold() != CONFIRM_WORD.casefold():
        return False
    return True


def summarize(results: Sequence[UninstallResult]) -> str:
    """Итог одной строкой для окна с результатами."""
    labels = ((STATUS_REMOVED, "удалено"), (STATUS_FAILED, "не удалось"), (STATUS_DECLINED, "отказ в правах администратора"),
              (STATUS_SKIPPED, "пропущено"), (STATUS_CANCELLED, "остановлено вами"))
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    parts = [f"{label} — {counts[status]}" for status, label in labels if counts.get(status)]
    text = "Итог: " + (", ".join(parts) if parts else "ничего не выбрано") + "."
    if any(r.reboot for r in results):
        text += " Чтобы завершить удаление некоторых программ, перезагрузите Windows."
    return text
