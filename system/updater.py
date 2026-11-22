"""Проверка и установка обновлений NOVA из HTTPS-манифеста.

Манифест latest.json содержит version, installer_url, sha256 и notes.
Установщик запускается с тихими ключами Inno Setup после проверки SHA-256 и PE-заголовка.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from config import APP_VERSION

MAX_MANIFEST_BYTES = 128 * 1024
MAX_INSTALLER_BYTES = 500 * 1024 * 1024
_SHA_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def version_key(value: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(value))
    if not parts:
        raise ValueError("Версия в манифесте имеет неверный формат")
    return tuple(int(x) for x in parts)


def is_newer_version(candidate: str, current: str = APP_VERSION) -> bool:
    left, right = version_key(candidate), version_key(current)
    length = max(len(left), len(right))
    return left + (0,) * (length - len(left)) > right + (0,) * (length - len(right))


def validate_manifest_url(url: str) -> str:
    value = str(url or "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Для автообновления используй HTTPS-ссылку на latest.json.")
    return value


def validate_manifest(raw: bytes, current_version: str = APP_VERSION) -> dict[str, Any]:
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ValueError("Файл обновления слишком большой")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать latest.json") from exc
    if not isinstance(data, dict):
        raise ValueError("Формат latest.json неверный")
    version = str(data.get("version", "")).strip()
    version_key(version)
    installer_url = str(data.get("installer_url", "")).strip()
    parsed = urlparse(installer_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("В latest.json отсутствует HTTPS-ссылка на установщик")
    sha256 = str(data.get("sha256", "")).strip()
    if not _SHA_RE.fullmatch(sha256):
        raise ValueError("В latest.json отсутствует корректный SHA-256 установщика")
    return {
        "version": version,
        "installer_url": installer_url,
        "sha256": sha256.lower(),
        "notes": str(data.get("notes", ""))[:8000],
        "is_newer": is_newer_version(version, current_version),
    }


def check_update(manifest_url: str, current_version: str = APP_VERSION) -> dict[str, Any]:
    url = validate_manifest_url(manifest_url)
    request = urllib.request.Request(url, headers={"User-Agent": f"NOVA/{current_version}"})
    with urllib.request.urlopen(request, timeout=12) as response:
        if urlparse(response.geturl()).scheme != "https":
            raise RuntimeError("Ссылка манифеста перенаправила на небезопасный HTTP")
        if response.status != 200:
            raise RuntimeError(f"Сервер вернул HTTP {response.status}")
        raw = response.read(MAX_MANIFEST_BYTES + 1)
    return validate_manifest(raw, current_version)


def download_installer(info: dict[str, Any], progress: Callable[[int], None] | None = None) -> Path:
    url = str(info.get("installer_url", ""))
    if urlparse(url).scheme != "https":
        raise ValueError("Загрузка разрешена только по HTTPS")
    expected = str(info.get("sha256", "")).lower()
    if not _SHA_RE.fullmatch(expected):
        raise ValueError("Некорректная контрольная сумма обновления")
    version = re.sub(r"[^0-9A-Za-z._-]", "_", str(info.get("version", "unknown")))
    target = Path(tempfile.gettempdir()) / f"NOVA_Update_{version}.exe"
    temp = target.with_suffix(".download")
    request = urllib.request.Request(url, headers={"User-Agent": f"NOVA/{APP_VERSION}"})
    digest = hashlib.sha256()
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, temp.open("wb") as stream:
            if urlparse(response.geturl()).scheme != "https":
                raise RuntimeError("Ссылка установщика перенаправила на небезопасный HTTP")
            content_length = response.headers.get("Content-Length")
            expected_size = int(content_length) if content_length and content_length.isdigit() else 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_INSTALLER_BYTES:
                    raise ValueError("Установщик превышает допустимый размер 500 МБ")
                stream.write(chunk)
                digest.update(chunk)
                if progress and expected_size > 0:
                    progress(min(99, int(total * 100 / expected_size)))
        if total < 100_000:
            raise ValueError("Загруженный установщик слишком маленький")
        with temp.open("rb") as stream:
            if stream.read(2) != b"MZ":
                raise ValueError("Файл не похож на Windows EXE — запуск отменён")
        if digest.hexdigest().lower() != expected:
            raise ValueError("SHA-256 установщика не совпал. Запуск отменён")
        temp.replace(target)
        if progress:
            progress(100)
        return target
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def launch_installer(path: Path) -> None:
    """Запускает Inno Setup Installer; вызывающий код должен завершить NOVA после запуска процесса."""
    if not path.is_file() or path.suffix.lower() != ".exe":
        raise FileNotFoundError("Установщик обновления не найден")
    subprocess.Popen(
        [str(path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        close_fds=True,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )
