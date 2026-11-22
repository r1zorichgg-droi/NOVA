"""Популярные программы (Discord, Steam, Chrome и т.п.): ищутся по их обычным папкам, а не только по меню «Пуск».

Зачем: ярлыки некоторых программ выглядят как «служебные» (у Discord, Slack, GitHub Desktop ярлык ведёт на Update.exe
с параметром --processStart) и общий фильтр мусора их бы отбросил. Здесь для таких программ есть точные правила.
Ничего не запускается и не читается, кроме проверки «существует ли файл».
"""
from __future__ import annotations

import ntpath
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .found import Found

ENV_VAR = re.compile(r"%([^%]+)%")


@dataclass(frozen=True)
class KnownApp:
    title: str
    category: str
    paths: tuple[str, ...]          # кандидаты на EXE; %VAR% подставляется из окружения
    args: str = ""                  # параметры запуска (для Squirrel-программ вроде Discord)
    match: str = ""                 # регулярное выражение по названию ярлыка (чтобы его не отбросил фильтр мусора)


_PF = "%ProgramFiles%"
_PF86 = "%ProgramFiles(x86)%"
_LOCAL = "%LOCALAPPDATA%"
_ROAM = "%APPDATA%"

KNOWN_APPS: tuple[KnownApp, ...] = (
    KnownApp("Discord", "Общение", (rf"{_LOCAL}\Discord\Update.exe",), "--processStart Discord.exe", r"^discord$"),
    KnownApp("Steam", "Игровые лаунчеры", (rf"{_PF86}\Steam\steam.exe", rf"{_PF}\Steam\steam.exe"), "", r"^steam$"),
    KnownApp("Epic Games Launcher", "Игровые лаунчеры",
             (rf"{_PF86}\Epic Games\Launcher\Portal\Binaries\Win64\EpicGamesLauncher.exe",
              rf"{_PF86}\Epic Games\Launcher\Portal\Binaries\Win32\EpicGamesLauncher.exe"), "", r"^epic games launcher$"),
    KnownApp("Battle.net", "Игровые лаунчеры", (rf"{_PF86}\Battle.net\Battle.net Launcher.exe",), "", r"^battle\.net$"),
    KnownApp("Ubisoft Connect", "Игровые лаунчеры", (rf"{_PF86}\Ubisoft\Ubisoft Game Launcher\UbisoftConnect.exe",),
             "", r"^ubisoft connect$"),
    KnownApp("EA app", "Игровые лаунчеры", (rf"{_PF}\Electronic Arts\EA Desktop\EA Desktop\EADesktop.exe",), "", r"^ea( app)?$"),
    KnownApp("GOG Galaxy", "Игровые лаунчеры", (rf"{_PF86}\GOG Galaxy\GalaxyClient.exe",), "", r"^gog galaxy$"),
    KnownApp("Google Chrome", "Браузеры",
             (rf"{_PF}\Google\Chrome\Application\chrome.exe", rf"{_PF86}\Google\Chrome\Application\chrome.exe",
              rf"{_LOCAL}\Google\Chrome\Application\chrome.exe"), "", r"^google chrome$"),
    KnownApp("Microsoft Edge", "Браузеры",
             (rf"{_PF86}\Microsoft\Edge\Application\msedge.exe", rf"{_PF}\Microsoft\Edge\Application\msedge.exe"),
             "", r"^microsoft edge$"),
    KnownApp("Mozilla Firefox", "Браузеры", (rf"{_PF}\Mozilla Firefox\firefox.exe", rf"{_PF86}\Mozilla Firefox\firefox.exe"),
             "", r"^(mozilla )?firefox$"),
    KnownApp("Opera", "Браузеры", (rf"{_LOCAL}\Programs\Opera\opera.exe", rf"{_PF}\Opera\opera.exe"), "", r"^opera( stable)?$"),
    KnownApp("Opera GX", "Браузеры", (rf"{_LOCAL}\Programs\Opera GX\opera.exe",), "", r"^opera gx"),
    KnownApp("Brave", "Браузеры", (rf"{_PF}\BraveSoftware\Brave-Browser\Application\brave.exe",
                                   rf"{_LOCAL}\BraveSoftware\Brave-Browser\Application\brave.exe"), "", r"^brave$"),
    KnownApp("Яндекс Браузер", "Браузеры", (rf"{_LOCAL}\Yandex\YandexBrowser\Application\browser.exe",), "",
             r"^(yandex|яндекс)"),
    KnownApp("Telegram", "Общение", (rf"{_ROAM}\Telegram Desktop\Telegram.exe",), "", r"^telegram"),
    KnownApp("Spotify", "Музыка и видео", (rf"{_ROAM}\Spotify\Spotify.exe",), "", r"^spotify$"),
    KnownApp("Viber", "Общение", (rf"{_LOCAL}\Viber\Viber.exe",), "", r"^viber$"),
    KnownApp("WhatsApp", "Общение", (rf"{_LOCAL}\WhatsApp\WhatsApp.exe",), "", r"^whatsapp$"),
    KnownApp("Slack", "Общение", (rf"{_LOCAL}\slack\slack.exe",), "", r"^slack$"),
    KnownApp("Zoom", "Общение", (rf"{_ROAM}\Zoom\bin\Zoom.exe", rf"{_PF}\Zoom\bin\Zoom.exe"), "", r"^zoom"),
    KnownApp("OBS Studio", "Творчество", (rf"{_PF}\obs-studio\bin\64bit\obs64.exe",), "", r"^obs studio"),
    KnownApp("Visual Studio Code", "Разработка", (rf"{_LOCAL}\Programs\Microsoft VS Code\Code.exe",
                                                  rf"{_PF}\Microsoft VS Code\Code.exe"), "", r"^visual studio code$"),
    KnownApp("Notepad++", "Разработка", (rf"{_PF}\Notepad++\notepad++.exe", rf"{_PF86}\Notepad++\notepad++.exe"), "",
             r"^notepad\+\+$"),
    KnownApp("GitHub Desktop", "Разработка", (rf"{_LOCAL}\GitHubDesktop\GitHubDesktop.exe",), "", r"^github desktop$"),
    KnownApp("7-Zip", "Утилиты", (rf"{_PF}\7-Zip\7zFM.exe", rf"{_PF86}\7-Zip\7zFM.exe"), "", r"^7-zip"),
    KnownApp("WinRAR", "Утилиты", (rf"{_PF}\WinRAR\WinRAR.exe", rf"{_PF86}\WinRAR\WinRAR.exe"), "", r"^winrar$"),
    KnownApp("VLC media player", "Музыка и видео", (rf"{_PF}\VideoLAN\VLC\vlc.exe", rf"{_PF86}\VideoLAN\VLC\vlc.exe"),
             "", r"^vlc media player$"),
    KnownApp("qBittorrent", "Утилиты", (rf"{_PF}\qBittorrent\qbittorrent.exe",), "", r"^qbittorrent$"),
)

_COMPILED = tuple((app, re.compile(app.match, re.I) if app.match else None) for app in KNOWN_APPS)


def expand(path: str, env: Mapping[str, str]) -> str:
    """Подставляет %VAR% из словаря окружения; без значения переменная остаётся как есть (такой путь не существует)."""
    lowered = {k.lower(): v for k, v in env.items()}
    return ENV_VAR.sub(lambda m: lowered.get(m.group(1).lower(), m.group(0)), path)


def match_known(title: str) -> KnownApp | None:
    """Известная программа по названию ярлыка (для обхода общего фильтра мусора)."""
    name = title.strip()
    for app, pattern in _COMPILED:
        if pattern is not None and pattern.search(name):
            return app
    return None


def scan_known_apps(env: Mapping[str, str], exists: Callable[[str], bool],
                    extra: Mapping[str, str] | None = None) -> list[Found]:
    """Известные программы, установленные на этом ПК. extra: {название: путь к EXE} — например, Steam из реестра."""
    found: list[Found] = []
    for app in KNOWN_APPS:
        candidates = [expand(p, env) for p in app.paths]
        if extra and extra.get(app.title):
            candidates.insert(0, extra[app.title])
        exe = next((c for c in candidates if "%" not in c and exists(c)), "")
        if not exe:
            continue
        norm = ntpath.normcase(ntpath.normpath(exe))
        found.append(Found(kind="app", title=app.title, source="shortcut", external_id=norm, exe_path=exe,
                           install_dir=ntpath.dirname(exe), category=app.category, args=app.args))
    return found
