"""Неблокирующая по отношению к UI проверка ICMP-пинга через системную утилиту ping."""
from __future__ import annotations

import ipaddress
import re
import subprocess
import sys
from dataclasses import dataclass

_HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_TIME_RE = re.compile(r"(?:time|время)\s*[=<]\s*([0-9]+(?:[.,][0-9]+)?)\s*(?:ms|мс)?", re.IGNORECASE)


@dataclass(frozen=True)
class PingResult:
    host: str
    success: bool
    latency_ms: float | None
    error: str = ""


def validate_host(host: str) -> str:
    value = str(host or "").strip()
    if not value or not _HOST_RE.fullmatch(value) or ".." in value or value.startswith("-"):
        raise ValueError("Укажи корректный IP-адрес или доменное имя.")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        if any(not part or len(part) > 63 or part.startswith("-") or part.endswith("-")
               for part in value.rstrip(".").split(".")):
            raise ValueError("Укажи корректный IP-адрес или доменное имя.")
    return value


def ping_host(host: str = "1.1.1.1", timeout_ms: int = 1200,
              run=subprocess.run) -> PingResult:
    """Пингует заданный узел один раз, не используя shell (защита от подстановки команд)."""
    target = validate_host(host)
    timeout = max(300, min(int(timeout_ms), 5000))
    if sys.platform == "win32":
        args = ["ping", "-n", "1", "-w", str(timeout), target]
    else:
        args = ["ping", "-c", "1", "-W", str(max(1, (timeout + 999) // 1000)), target]
    try:
        result = run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                     timeout=(timeout / 1000) + 2, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PingResult(target, False, None, str(exc))
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    match = _TIME_RE.search(output)
    if int(result.returncode) == 0 and match:
        latency = float(match.group(1).replace(",", "."))
        return PingResult(target, True, latency)
    if int(result.returncode) == 0:
        # Some system locales print the successful time in a different format.
        return PingResult(target, True, None, "Ответ получен, но формат времени не распознан")
    return PingResult(target, False, None, "Нет ответа от узла")
