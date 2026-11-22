"""Минимальный разбор текстового формата Valve (VDF / ACF): файлы libraryfolders.vdf и appmanifest_*.acf."""
from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r'//[^\n]*|"((?:[^"\\]|\\.)*)"|([{}])|([^\s"{}]+)')
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}
MAX_DEPTH = 32


def _unescape(raw: str) -> str:
    return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(1)), raw)


def parse_vdf(text: str) -> dict[str, Any]:
    """Возвращает вложенный словарь. Повреждённый файл даёт ValueError (вызывающий пропускает его)."""
    tokens: list[tuple[str, str]] = []
    for m in _TOKEN.finditer(text):
        if m.group(0).startswith("//"):
            continue
        if m.group(1) is not None:
            tokens.append(("s", _unescape(m.group(1))))
        elif m.group(2):
            tokens.append((m.group(2), m.group(2)))
        elif m.group(3):
            tokens.append(("s", m.group(3)))
    pos = 0

    def block(depth: int) -> dict[str, Any]:
        nonlocal pos
        if depth > MAX_DEPTH:
            raise ValueError("слишком глубокая вложенность")
        out: dict[str, Any] = {}
        while pos < len(tokens):
            kind, key = tokens[pos]
            pos += 1
            if kind == "}":
                return out
            if kind != "s" or pos >= len(tokens):
                raise ValueError("неожиданный элемент")
            kind2, value = tokens[pos]
            pos += 1
            if kind2 == "{":
                out[key] = block(depth + 1)
            elif kind2 == "s":
                out[key] = value
            else:
                raise ValueError("неожиданный элемент")
        return out

    return block(0)


def vget(data: dict[str, Any], key: str, default: Any = None) -> Any:
    """Поиск ключа без учёта регистра (Valve пишет и «appid», и «AppID»)."""
    low = key.lower()
    for k, v in data.items():
        if k.lower() == low:
            return v
    return default
