"""Список установленного в Windows: классические программы (реестр «Установка и удаление») и приложения Microsoft Store.

Здесь только чтение и разбор данных; удаление — в system/uninstall.py. Никаких запросов в интернет.
Разбор написан как чистые функции над словарями, поэтому проверяется тестами без Windows.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .debloat_rules import RISK_SAFE, RISK_SYSTEM, classify_appx, classify_win32, is_microsoft_appx, is_microsoft_win32

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

KIND_WIN32, KIND_APPX = "win32", "appx"
UNINSTALL_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"

_GUID_ANY = re.compile(r"\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}")
GUID_FULL = re.compile(r"^\{[0-9A-Fa-f]{8}-(?:[0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}\}$")
_UPDATE_NAME = re.compile(r"^(security update|update|hotfix|cumulative update)\b.*\bfor\b|^kb\d{6,}|\(kb\d{6,}\)\s*$", re.I)
_UPDATE_TYPES = {"update", "hotfix", "security update", "service pack", "update rollup", "critical update"}
APPX_FULLNAME = re.compile(r"^[A-Za-z0-9._~-]{3,200}$")


@dataclass(frozen=True)
class Program:
    key: str                        # уникальный ключ: путь в реестре или PackageFullName
    kind: str                       # KIND_WIN32 | KIND_APPX
    name: str
    publisher: str = ""
    version: str = ""
    size_bytes: int = 0             # 0 — размер неизвестен
    install_date: str = ""          # ГГГГММДД, если указан
    risk: str = RISK_SAFE
    reason: str = ""                # почему «Осторожно»/«Системное» (показывается в подсказке)
    removable: bool = True
    why_not: str = ""               # почему удалить нельзя
    microsoft: bool = False
    # классические программы
    uninstall: str = ""
    quiet_uninstall: str = ""
    msi_guid: str = ""
    machine_wide: bool = False      # установлена для всех пользователей (HKLM): удаление требует прав администратора
    icon_path: str = ""
    # приложения Store
    package_full_name: str = ""
    family: str = ""

    @property
    def hidden_by_default(self) -> bool:
        return self.risk == RISK_SYSTEM or not self.removable

    @property
    def kind_label(self) -> str:
        return "Microsoft Store" if self.kind == KIND_APPX else "Классическая"


@dataclass(frozen=True)
class RegEntry:
    key_id: str                     # «HKLM|64|{GUID}» — по нему можно проверить, остался ли раздел
    subkey: str
    machine: bool
    values: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class ProgramList:
    programs: list[Program] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)      # что не удалось прочитать (показывается пользователю)


# --------------------------------------------------------------------------------------
# Классические программы
# --------------------------------------------------------------------------------------
def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def msi_guid_of(subkey: str, uninstall: str, windows_installer: int) -> str:
    """GUID установки Windows Installer (MSI) или пустая строка."""
    if "msiexec" in uninstall.lower():
        match = _GUID_ANY.search(uninstall)
        if match:
            return match.group(0).upper()
    if windows_installer and GUID_FULL.match(subkey):
        return subkey.upper()
    return ""


def icon_path_of(display_icon: str) -> str:
    """Путь к значку из DisplayIcon («"C:\\x\\a.exe",0» -> «C:\\x\\a.exe»); только EXE и ICO."""
    text = display_icon.strip()
    if text.startswith('"'):
        end = text.find('"', 1)
        text = text[1:end] if end > 0 else text[1:]
    else:
        text = re.sub(r",\s*-?\d+$", "", text)
    text = text.strip()
    return text if text.lower().endswith((".exe", ".ico")) else ""


def parse_win32(entry: RegEntry) -> Program | None:
    """Раздел реестра -> Program либо None, если это не программа (обновление, пустая запись)."""
    v = entry.values
    name = _text(v.get("DisplayName"))
    if not name or (name.startswith("{") and name.endswith("}")):
        return None
    if _text(v.get("ParentKeyName")) or _text(v.get("ParentDisplayName")):
        return None                                              # дополнение или обновление другой программы
    if _text(v.get("ReleaseType")).lower() in _UPDATE_TYPES or _UPDATE_NAME.search(name):
        return None                                              # обновления Windows удаляются иначе
    publisher = _text(v.get("Publisher"))
    uninstall, quiet = _text(v.get("UninstallString")), _text(v.get("QuietUninstallString"))
    guid = msi_guid_of(entry.subkey, uninstall or quiet, _number(v.get("WindowsInstaller")))
    risk, reason = classify_win32(name, publisher, bool(_number(v.get("SystemComponent"))))
    removable, why_not = True, ""
    if _number(v.get("NoRemove")):
        removable, why_not = False, "Разработчик запретил удаление через Windows."
    elif not (uninstall or quiet or guid):
        removable, why_not = False, "У записи нет команды удаления (возможно, программа не устанавливалась, а просто скопирована)."
    return Program(
        key=entry.key_id, kind=KIND_WIN32, name=name, publisher=publisher, version=_text(v.get("DisplayVersion")),
        size_bytes=max(_number(v.get("EstimatedSize")), 0) * 1024, install_date=_text(v.get("InstallDate")),
        risk=risk, reason=reason, removable=removable, why_not=why_not, microsoft=is_microsoft_win32(name, publisher),
        uninstall=uninstall, quiet_uninstall=quiet, msi_guid=guid, machine_wide=entry.machine,
        icon_path=icon_path_of(_text(v.get("DisplayIcon"))))


def parse_win32_all(entries: Iterable[RegEntry]) -> list[Program]:
    """Все программы без повторов (одна и та же программа часто записана в нескольких разделах реестра)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[Program] = []
    for entry in entries:
        program = parse_win32(entry)
        if program is None:
            continue
        marker = (program.name.lower(), program.version, (program.uninstall or program.quiet_uninstall).lower())
        if marker in seen:
            continue
        seen.add(marker)
        out.append(program)
    return out


def read_registry_entries() -> list[RegEntry]:
    """Читает разделы Uninstall: HKLM (64- и 32-битные программы) и HKCU. Только чтение, права администратора не нужны."""
    if not IS_WINDOWS:
        return []
    import winreg
    out: list[RegEntry] = []
    places = (("HKLM", winreg.HKEY_LOCAL_MACHINE, "64", winreg.KEY_WOW64_64KEY, True),
              ("HKLM", winreg.HKEY_LOCAL_MACHINE, "32", winreg.KEY_WOW64_32KEY, True),
              ("HKCU", winreg.HKEY_CURRENT_USER, "0", 0, False))
    for hive_name, hive, view_name, view, machine in places:
        try:
            root = winreg.OpenKey(hive, UNINSTALL_KEY, 0, winreg.KEY_READ | view)
        except OSError:
            continue
        with root:
            index = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, index)
                except OSError:
                    break
                index += 1
                values = _read_values(winreg, root, sub, view)
                if values:
                    out.append(RegEntry(f"{hive_name}|{view_name}|{sub}", sub, machine, values))
    return out


def _read_values(winreg: Any, root: Any, sub: str, view: int) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view) as key:
            count = winreg.QueryInfoKey(key)[1]
            for i in range(count):
                try:
                    name, data, _kind = winreg.EnumValue(key, i)
                except OSError:
                    break
                if isinstance(data, (str, int)):
                    values[name] = data
    except OSError:
        return {}
    return values


def registry_key_present(key_id: str) -> bool:
    """Остался ли раздел программы в реестре (проверка после удаления). При сомнении — «остался»."""
    if not IS_WINDOWS:
        return False
    import winreg
    try:
        hive_name, view_name, sub = key_id.split("|", 2)
    except ValueError:
        return False
    hive = winreg.HKEY_LOCAL_MACHINE if hive_name == "HKLM" else winreg.HKEY_CURRENT_USER
    view = {"64": winreg.KEY_WOW64_64KEY, "32": winreg.KEY_WOW64_32KEY}.get(view_name, 0)
    try:
        with winreg.OpenKey(hive, UNINSTALL_KEY + "\\" + sub, 0, winreg.KEY_READ | view):
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return True


# --------------------------------------------------------------------------------------
# Приложения Microsoft Store
# --------------------------------------------------------------------------------------
LIST_APPX_SCRIPT = (
    "$p = Get-AppxPackage | Select-Object Name, PackageFullName, PackageFamilyName, Publisher, "
    "@{n='Version';e={[string]$_.Version}}, IsFramework, NonRemovable, IsResourcePackage, IsBundle, "
    "@{n='SignatureKind';e={[string]$_.SignatureKind}}; "
    "$s = Get-StartApps | Select-Object Name, AppID; "
    "ConvertTo-Json -Compress -Depth 3 -InputObject @{ appx = @($p); start = @($s) }")


def parse_appx_json(text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Вывод LIST_APPX_SCRIPT -> (пакеты, значки меню «Пуск»). Мусор и пустой вывод дают пустые списки."""
    try:
        data = json.loads(text)
    except ValueError:
        return [], []
    if not isinstance(data, dict):
        return [], []

    def rows(key: str) -> list[dict[str, Any]]:
        raw = data.get(key)
        raw = [raw] if isinstance(raw, dict) else raw
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []
    return rows("appx"), rows("start")


def start_names_by_family(start_rows: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """{PackageFamilyName (в нижнем регистре): название в меню «Пуск»} — так у приложений Store появляются нормальные имена."""
    names: dict[str, str] = {}
    for row in start_rows:
        title, app_id = _text(row.get("Name")), _text(row.get("AppID"))
        if title and "!" in app_id:
            names.setdefault(app_id.split("!", 1)[0].lower(), title)
    return names


def _flag(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def parse_appx(rows: Iterable[Mapping[str, Any]], start_rows: Iterable[Mapping[str, Any]] = ()) -> list[Program]:
    starts = start_names_by_family(start_rows)
    out: list[Program] = []
    seen: set[str] = set()
    for row in rows:
        name, full = _text(row.get("Name")), _text(row.get("PackageFullName"))
        if not name or not full or full in seen:
            continue
        if _flag(row.get("IsResourcePackage")) or _flag(row.get("IsBundle")):
            continue                                            # языковые и графические ресурсы — не приложения
        seen.add(full)
        family = _text(row.get("PackageFamilyName"))
        publisher = _text(row.get("Publisher"))
        start_title = starts.get(family.lower(), "")
        non_removable = _flag(row.get("NonRemovable"))
        display, risk, reason = classify_appx(
            name, publisher, has_start_entry=bool(start_title), is_framework=_flag(row.get("IsFramework")),
            non_removable=non_removable, signature=_text(row.get("SignatureKind")))
        out.append(Program(
            key=full, kind=KIND_APPX, name=start_title or display or name, publisher=_publisher_name(publisher),
            version=_text(row.get("Version")), risk=risk, reason=reason, removable=not non_removable,
            why_not="Windows не разрешает удалять этот компонент." if non_removable else "",
            microsoft=is_microsoft_appx(name, publisher), package_full_name=full, family=family))
    return out


def _publisher_name(subject: str) -> str:
    """«CN=Microsoft Corporation, O=Microsoft Corporation, …» -> «Microsoft Corporation»."""
    match = re.search(r"(?:^|,\s*)CN=(\"[^\"]+\"|[^,]+)", subject)
    return match.group(1).strip('"') if match else subject


# --------------------------------------------------------------------------------------
# Общий список
# --------------------------------------------------------------------------------------
def list_programs(read_registry: Callable[[], list[RegEntry]], run_powershell: Callable[[str], str]) -> ProgramList:
    """Объединяет классические программы и приложения Store. Источник, который не удалось прочитать, не мешает второму."""
    result = ProgramList()
    try:
        result.programs.extend(parse_win32_all(read_registry()))
    except Exception:
        log.exception("Не удалось прочитать список классических программ")
        result.notes.append("Не удалось прочитать список классических программ из реестра.")
    try:
        text = run_powershell(LIST_APPX_SCRIPT)
    except Exception:
        log.exception("Не удалось получить список приложений Microsoft Store")
        text = ""
    appx_rows, start_rows = parse_appx_json(text)
    if appx_rows:
        result.programs.extend(parse_appx(appx_rows, start_rows))
    elif IS_WINDOWS:
        result.notes.append("Не удалось получить список приложений Microsoft Store (PowerShell недоступен или заблокирован).")
    return result


# --------------------------------------------------------------------------------------
# Фильтры списка (для страницы «Выпиление»)
# --------------------------------------------------------------------------------------
FILTERS: tuple[tuple[str, str], ...] = (("all", "Все"), ("win32", "Классические"), ("appx", "Microsoft Store"),
                                        ("microsoft", "От Microsoft"), ("safe", "Можно удалять"), ("careful", "Осторожно"))


def program_matches(program: Program, group: str = "all", query: str = "", show_hidden: bool = False) -> bool:
    """Показывать ли программу при выбранном фильтре, поиске и галочке «Показывать системные компоненты»."""
    if program.hidden_by_default and not show_hidden:
        return False
    if group == "win32" and program.kind != KIND_WIN32:
        return False
    if group == "appx" and program.kind != KIND_APPX:
        return False
    if group == "microsoft" and not program.microsoft:
        return False
    if group == "safe" and program.risk != RISK_SAFE:
        return False
    if group == "careful" and program.risk == RISK_SAFE:
        return False
    text = query.strip().casefold()
    return not text or text in program.name.casefold() or text in program.publisher.casefold()
