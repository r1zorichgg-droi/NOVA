"""Категории программ по названию. Для игр категории NOVA не придумывает: жанр нельзя честно определить
по имени файла — его задаёт пользователь в окне «Изменить» (там есть список подсказок)."""
from __future__ import annotations

import re

GAME_CATEGORY_SUGGESTIONS = ["Шутер", "RPG", "Стратегия", "Выживание", "Песочница", "Гонки", "Спорт",
                             "Приключения", "Инди", "Симулятор", "Файтинг", "Хоррор"]

_APP_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (cat, re.compile(rf"(?<![0-9A-Za-zА-Яа-я])(?:{pattern})(?![0-9A-Za-zА-Яа-я])", re.I)) for cat, pattern in (
        ("Игровые лаунчеры", r"steam|epic games|battle\.?net|gog galaxy|ubisoft connect|uplay|ea app|origin|riot client|"
                             r"xbox|rockstar games launcher|minecraft launcher"),
        ("Браузеры", r"chrome|firefox|msedge|microsoft edge|opera|brave|vivaldi|yandex browser|яндекс браузер|tor browser"),
        ("Общение", r"discord|telegram|whatsapp|viber|skype|zoom|teams|slack|signal|element"),
        ("Музыка и видео", r"spotify|vlc|foobar|aimp|musicbee|itunes|yandex music|яндекс музыка|potplayer|mpc-hc|netflix|"
                           r"youtube|twitch"),
        ("Разработка", r"visual studio|vs ?code|pycharm|intellij|idea|webstorm|clion|rider|android studio|git|github|"
                       r"notepad\+\+|sublime|docker|postman|terminal|powershell|python|node\.?js|dbeaver|sqlite"),
        ("Офис", r"word|excel|powerpoint|outlook|onenote|libreoffice|openoffice|acrobat|foxit|notion|obsidian|onlyoffice"),
        ("Творчество", r"photoshop|illustrator|premiere|after effects|blender|davinci|audacity|gimp|krita|figma|obs studio|"
                       r"\bobs\b|capcut|lightroom|inkscape|fl studio|ableton"),
        ("Утилиты", r"7-?zip|winrar|everything|ccleaner|powertoys|hwinfo|cpu-?z|gpu-?z|afterburner|lghub|logitech|razer|"
                    r"nvidia|geforce|radeon|amd software|torrent|qbittorrent|wireguard|openvpn|anydesk|teamviewer"),
    ))


def guess_app_category(title: str, exe_path: str = "") -> str:
    """Категория программы по названию и имени файла или пустая строка, если нет уверенного совпадения."""
    haystack = f"{title} {exe_path.replace(chr(92), ' ').replace('/', ' ').rsplit(' ', 1)[-1]}"
    for category, pattern in _APP_RULES:
        if pattern.search(haystack):
            return category
    return ""
