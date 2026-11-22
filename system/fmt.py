"""Форматирование чисел и дат для интерфейса (без зависимости от локали ОС)."""
from __future__ import annotations

from datetime import datetime

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня",
           "июля", "августа", "сентября", "октября", "ноября", "декабря"]
NA = "N/A"


def fmt_bytes(n: float | None) -> str:
    if n is None:
        return NA
    units = ["Б", "КБ", "МБ", "ГБ", "ТБ"]
    value, i = float(n), 0
    while value >= 1024 and i < len(units) - 1:
        value /= 1024
        i += 1
    return f"{value:.0f} {units[i]}" if i == 0 or value >= 100 else f"{value:.1f} {units[i]}"


def fmt_speed_short(bytes_per_s: float | None) -> str:
    """Компактная скорость для островка: '0B', '512K', '1.2M' (до 5 символов)."""
    if bytes_per_s is None:
        return NA
    value, unit = float(bytes_per_s), "B"
    for next_unit in ("K", "M", "G"):
        if value < 1000:
            break
        value /= 1024
        unit = next_unit
    return f"{value:.1f}{unit}" if (unit != "B" and value < 10) else f"{value:.0f}{unit}"


def fmt_speed_long(bytes_per_s: float | None) -> str:
    if bytes_per_s is None:
        return NA
    return fmt_bytes(bytes_per_s) + "/с"


def fmt_percent(value: float | None) -> str:
    return NA if value is None else f"{value:.0f}%"


def fmt_temp(value: float | None) -> str:
    return NA if value is None else f"{value:.0f}°"


def fmt_uptime(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return NA
    minutes = int(seconds // 60)
    days, rem = divmod(minutes, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days} д {hours} ч"
    if hours:
        return f"{hours} ч {mins} мин"
    return f"{mins} мин"


def fmt_clock(seconds: float) -> str:
    """Секунды -> m:ss или h:mm:ss (для плеера)."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def greeting(now: datetime | None = None) -> str:
    hour = (now or datetime.now()).hour
    if 5 <= hour < 12:
        return "Доброе утро"
    if 12 <= hour < 18:
        return "Добрый день"
    if 18 <= hour < 23:
        return "Добрый вечер"
    return "Доброй ночи"


def fmt_date_ru(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"{_WEEKDAYS[now.weekday()].capitalize()}, {now.day} {_MONTHS[now.month - 1]}"


def fmt_duration(seconds: float | None) -> str:
    """Длительность для времени в игре: «3 ч 20 м», «45 м», «меньше минуты»."""
    if seconds is None or seconds <= 0:
        return "—"
    total = int(seconds)
    hours, minutes = divmod(total // 60, 60)
    if hours:
        return f"{hours} ч {minutes} м" if minutes else f"{hours} ч"
    return f"{minutes} м" if minutes else "меньше минуты"


def fmt_when(ts: float | None, now: datetime | None = None) -> str:
    """Дата последнего запуска: «Сегодня 21:40», «Вчера 18:05», «15 ноября 2026»."""
    if not ts:
        return "ещё не запускалась"
    moment = datetime.fromtimestamp(ts)
    today = (now or datetime.now()).date()
    delta = (today - moment.date()).days
    if delta == 0:
        return f"сегодня {moment:%H:%M}"
    if delta == 1:
        return f"вчера {moment:%H:%M}"
    return f"{moment.day} {_MONTHS[moment.month - 1]} {moment.year}"


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Форма слова по числу: plural_ru(2, "игра", "игры", "игр") -> "игры"."""
    last, last2 = abs(n) % 10, abs(n) % 100
    if last == 1 and last2 != 11:
        return one
    if 2 <= last <= 4 and not 12 <= last2 <= 14:
        return few
    return many
