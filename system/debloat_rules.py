"""Правила оценки «насколько безопасно удалять»: известные приложения Microsoft Store и шаблоны классических программ.

Только справочные данные и чистые функции: ничего не читает и не запускает. Оценка — подсказка, а не запрет:
удалить можно всё, что Windows сама разрешает удалить, но «Осторожно» и «Системное» показываются с причиной
и требуют отдельного подтверждения.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

RISK_SAFE, RISK_CAREFUL, RISK_SYSTEM = "safe", "careful", "system"
RISK_LABELS = {RISK_SAFE: "Можно удалять", RISK_CAREFUL: "Осторожно", RISK_SYSTEM: "Системное"}
RISK_ORDER = {RISK_SAFE: 0, RISK_CAREFUL: 1, RISK_SYSTEM: 2}


@dataclass(frozen=True)
class Known:
    display: str
    risk: str = RISK_SAFE
    reason: str = ""


_S, _C = RISK_SAFE, RISK_CAREFUL

# Приложения Microsoft Store: ключ — начало имени пакета (в нижнем регистре).
_APPX_KNOWN: tuple[tuple[str, Known], ...] = (
    ("microsoft.bingnews", Known("Новости", _S)),
    ("microsoft.bingweather", Known("Погода", _S)),
    ("microsoft.bingsearch", Known("Поиск Bing", _S)),
    ("microsoft.bingfinance", Known("Финансы", _S)),
    ("microsoft.bingsports", Known("Спорт", _S)),
    ("microsoft.bingtranslator", Known("Переводчик Bing", _S)),
    ("microsoft.gethelp", Known("Техническая поддержка", _S)),
    ("microsoft.getstarted", Known("Советы", _S)),
    ("microsoft.microsoftofficehub", Known("Microsoft 365 (Office)", _S)),
    ("microsoft.microsoftsolitairecollection", Known("Microsoft Solitaire Collection", _S)),
    ("microsoft.people", Known("Люди", _S)),
    ("microsoft.windowscommunicationsapps", Known("Почта и Календарь", _S)),
    ("microsoft.windowsfeedbackhub", Known("Центр отзывов", _S)),
    ("microsoft.yourphone", Known("Связь с телефоном", _S)),
    ("microsoft.549981c3f5f10", Known("Cortana", _S)),
    ("microsoft.mixedreality.portal", Known("Портал смешанной реальности", _S)),
    ("microsoft.microsoft3dviewer", Known("Средство 3D-просмотра", _S)),
    ("microsoft.print3d", Known("Print 3D", _S)),
    ("microsoft.3dbuilder", Known("3D Builder", _S)),
    ("microsoft.skypeapp", Known("Skype", _S)),
    ("microsoft.windowsmaps", Known("Карты", _S)),
    ("microsoft.zunemusic", Known("Музыка Groove", _S)),
    ("microsoft.zunevideo", Known("Кино и ТВ", _S)),
    ("microsoft.microsoftstickynotes", Known("Sticky Notes", _S)),
    ("microsoft.todos", Known("Microsoft To Do", _S)),
    ("microsoft.powerautomatedesktop", Known("Power Automate", _S)),
    ("microsoft.wallet", Known("Microsoft Pay", _S)),
    ("microsoft.oneconnect", Known("Платный Wi-Fi и сотовая связь", _S)),
    ("microsoft.office.onenote", Known("OneNote", _S)),
    ("microsoft.windowsalarms", Known("Часы и будильники", _S)),
    ("microsoft.windowssoundrecorder", Known("Звукозапись", _S)),
    ("microsoft.devhome", Known("Dev Home", _S)),
    ("microsoft.windows.devhome", Known("Dev Home", _S)),
    ("microsoft.copilot", Known("Copilot", _S)),
    ("microsoft.windowsnotepad", Known("Блокнот", _S, "Простой текстовый редактор Windows.")),
    ("microsoft.paint", Known("Paint", _S)),
    ("microsoft.screensketch", Known("Набросок на фрагменте экрана", _S, "Программа «Ножницы».")),
    ("microsoft.windowscamera", Known("Камера", _S)),
    ("microsoft.windows.photos", Known("Фотографии", _S, "Просмотрщик фото по умолчанию: изображения откроются в другой программе.")),
    ("microsoft.windowscalculator", Known("Калькулятор", _S)),
    ("microsoft.windowsterminal", Known("Терминал Windows", _S)),
    ("microsoft.outlookforwindows", Known("Outlook (новый)", _S)),
    ("microsoft.xboxgamingoverlay", Known("Xbox Game Bar", _S, "Окно Win+G перестанет открываться.")),
    ("microsoft.xboxspeechtotextoverlay", Known("Xbox: речь в текст", _S)),
    ("microsoft.xboxapp", Known("Компаньон консоли Xbox", _S)),
    ("microsoft.gamingapp", Known("Xbox", _C, "Нужен для игр из Game Pass и Microsoft Store.")),
    ("microsoft.xboxidentityprovider", Known("Поставщик удостоверений Xbox", _C, "Вход в Xbox-аккаунт в играх из Microsoft Store.")),
    ("microsoft.xbox.tcui", Known("Xbox: интерфейс игр", _C, "Нужен играм из Microsoft Store и Game Pass.")),
    ("microsoft.gamingservices", Known("Игровые службы Xbox", _C,
                                       "Без них не запустятся игры Game Pass и некоторые игры из Microsoft Store.")),
    ("microsoft.windowsstore", Known("Microsoft Store", _C,
                                     "Без него нельзя ставить и обновлять приложения из Store; "
                                     "вернуть можно только переустановкой средствами Windows.")),
    ("microsoft.storepurchaseapp", Known("Покупки в Microsoft Store", _C, "Нужно для покупок и оплаты в Store.")),
    ("microsoft.desktopappinstaller", Known("Установщик приложений (winget)", _C, "Нужен для winget и установки пакетов .msix.")),
    ("microsoft.sechealthui", Known("Безопасность Windows", _C, "Защита продолжит работать, но окно настроек защиты откроется не всегда.")),
    ("microsoftteams", Known("Microsoft Teams (личный)", _S)),
    ("msteams", Known("Microsoft Teams", _S)),
    ("microsoftcorporationii.quickassist", Known("Быстрая помощь", _S)),
    ("microsoftcorporationii.microsoftfamily", Known("Семья Microsoft", _S)),
    ("clipchamp.clipchamp", Known("Clipchamp", _S)),
)
_APPX_SORTED = tuple(sorted(_APPX_KNOWN, key=lambda row: -len(row[0])))

_MS_APPX_PREFIXES = ("microsoft.", "microsoftwindows.", "windows.", "microsoftcorporationii.", "msteams", "microsoftteams",
                     "clipchamp.", "microsoftedge")

# Классические программы: (регулярное выражение по названию в нижнем регистре, риск, причина). Первое совпадение выигрывает.
_WIN32_RULES: tuple[tuple[str, str, str], ...] = (
    (r"visual c\+\+.*(redistributable|runtime)|vc_redist",
     _C, "Библиотеки, которые нужны многим программам и играм: без них они могут не запускаться."),
    (r"microsoft \.net|\.net (runtime|sdk|desktop|framework|core)|asp\.net core|windows desktop runtime",
     _C, "Платформа .NET нужна многим программам; без неё они не запустятся."),
    (r"webview2", _C, "Нужен программам со встроенным браузером (Office, Teams, многие установщики)."),
    (r"^microsoft edge", _C, "Браузер Edge нужен части функций Windows; Windows может не разрешить его удаление."),
    (r"onedrive", _C, "Синхронизация остановится; файлы в папке OneDrive останутся на диске."),
    (r"nvidia.*(driver|драйвер|physx|hd audio|geforce|control panel|graphics)|^geforce|^nvidia (app|physx|hd audio)",
     _C, "Драйвер или компонент видеокарты: после удаления картинка может работать в упрощённом режиме."),
    (r"^amd |radeon|amd software", _C, "Драйвер или компонент видеокарты/процессора AMD: возможны сбои графики и звука."),
    (r"intel.*(graphics|driver|management engine|rapid storage|wireless|bluetooth|chipset|ethernet)",
     _C, "Драйвер Intel: после удаления могут пропасть Wi-Fi, Bluetooth, звук или нормальная графика."),
    (r"realtek.*(audio|ethernet|card reader|bluetooth|wlan|driver)",
     _C, "Драйвер Realtek: после удаления могут пропасть звук или сеть."),
    (r"windows driver package", _C, "Пакет драйвера устройства: устройство может перестать работать."),
    (r"directx", _C, "Компоненты DirectX нужны играм."),
    (r"easy anti-?cheat|battleye|riot vanguard|faceit",
     _C, "Защита от читов: игры с ней не запустятся, пока игра не установит её заново."),
    (r"^steam$", _C, "Вместе с клиентом может быть удалена папка Steam с установленными играми и сохранениями. "
                     "Сначала сделайте резервную копию."),
    (r"kaspersky|eset |bitdefender|norton|mcafee|avast|\bavg\b|malwarebytes|dr\.web|антивирус|antivirus",
     _C, "Антивирус: после удаления ПК останется без этой защиты."),
    (r"update health tools|microsoft defender|windows security",
     _C, "Служебный компонент защиты и обновлений Windows."),
)
_WIN32_COMPILED = tuple((re.compile(pattern), risk, reason) for pattern, risk, reason in _WIN32_RULES)


def known_appx(name: str) -> Known | None:
    low = name.lower()
    for prefix, known in _APPX_SORTED:
        if low.startswith(prefix):
            return known
    return None


def is_microsoft_appx(name: str, publisher: str = "") -> bool:
    return name.lower().startswith(_MS_APPX_PREFIXES) or "microsoft corporation" in publisher.lower()


def is_microsoft_win32(name: str, publisher: str = "") -> bool:
    return "microsoft" in publisher.lower() or name.lower().startswith(("microsoft ", "windows "))


def classify_win32(name: str, publisher: str = "", system_component: bool = False) -> tuple[str, str]:
    """(риск, причина) для классической программы."""
    if system_component:
        return RISK_SYSTEM, "Служебный компонент: Windows скрывает его из обычного списка программ."
    low = name.lower()
    for pattern, risk, reason in _WIN32_COMPILED:
        if pattern.search(low):
            return risk, reason
    return RISK_SAFE, ""


def classify_appx(name: str, publisher: str, *, has_start_entry: bool, is_framework: bool, non_removable: bool,
                  signature: str) -> tuple[str, str, str]:
    """(отображаемое имя по справочнику или пусто, риск, причина) для приложения Microsoft Store."""
    known = known_appx(name)
    display = known.display if known else ""
    if non_removable:
        return display, RISK_SYSTEM, "Windows защищает этот компонент от удаления."
    if is_framework:
        return display, RISK_SYSTEM, "Библиотека: от неё зависят другие приложения, Windows не даст удалить её, пока они установлены."
    if known:
        return display, known.risk, known.reason
    if signature.lower() == "system":
        return display, RISK_SYSTEM, "Встроенный компонент Windows."
    if is_microsoft_appx(name, publisher):
        if not has_start_entry:
            return display, RISK_SYSTEM, "Служебный компонент Windows без собственного значка в меню «Пуск»."
        return display, RISK_CAREFUL, "Приложение Microsoft: удаление может отключить часть функций Windows."
    if not has_start_entry:
        return display, RISK_CAREFUL, "Служебный компонент без значка в меню «Пуск» — вероятно, нужен другому приложению."
    return display, RISK_SAFE, ""
