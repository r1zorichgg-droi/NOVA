"""FPS capture service based on the official PresentMon console collector.

PresentMon is downloaded on first use from its upstream GitHub release and verified
against the published SHA-256 of the mirrored release asset before execution.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
import statistics
import sys
import time
from collections import deque
from pathlib import Path

import psutil
from PySide6.QtCore import QObject, QProcess, QSaveFile, QStandardPaths, QTimer, QIODevice, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

log = logging.getLogger(__name__)

PRESENTMON_VERSION = "2.6.0"
PRESENTMON_URL = (
    "https://github.com/GameTechDev/PresentMon/releases/download/v2.6.0/"
    "PresentMon-2.6.0-x64.exe"
)
PRESENTMON_SHA256 = "b2a706bc6ad475749e3b7e3409263aa1e6906d45bdcf993f6dbc0f660188f1af"
IGNORED_FOREGROUND = {
    "nova.exe", "explorer.exe", "searchhost.exe", "startmenuexperiencehost.exe",
    "shellexperiencehost.exe", "applicationframehost.exe", "lockapp.exe",
    "dwm.exe", "textinputhost.exe", "taskmgr.exe",
}


def presentmon_tool_path() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not base:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".nova")
    return Path(base) / "tools" / f"PresentMon-{PRESENTMON_VERSION}-x64.exe"


def verify_presentmon(path: Path) -> bool:
    """Check PE signature and SHA-256 before running the downloaded collector."""
    try:
        if path.stat().st_size < 500_000:
            return False
        with path.open("rb") as stream:
            if stream.read(2) != b"MZ":
                return False
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest().lower() == PRESENTMON_SHA256
    except OSError:
        return False


def foreground_process_name() -> str | None:
    """Return the process owning the focused window. Only available on Windows."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return None
        name = psutil.Process(pid.value).name()
        if name.lower() in IGNORED_FOREGROUND:
            return None
        return name
    except Exception:  # noqa: BLE001 - foreground window may disappear during lookup
        return None


def parse_frametime_csv_row(header: list[str] | None, row: list[str]) -> float | None:
    """Read a positive PresentMon frame-time value in milliseconds from one CSV row."""
    if not header or len(row) < len(header):
        return None
    columns = {name.strip(): index for index, name in enumerate(header)}
    index = next((columns[key] for key in ("MsBetweenPresents", "MsBetweenDisplayChange", "FrameTime")
                  if key in columns), None)
    if index is None:
        return None
    try:
        value = float(row[index].strip())
    except (TypeError, ValueError, IndexError):
        return None
    return value if 0.05 <= value <= 10_000 else None


class FpsMonitor(QObject):
    status_changed = Signal(str)
    stats_changed = Signal(object)
    progress_changed = Signal(int)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._network = QNetworkAccessManager(self)
        self._download_reply: QNetworkReply | None = None
        self._save_file: QSaveFile | None = None
        self._process: QProcess | None = None
        self._download_path: Path | None = None
        self._enabled = False
        self._target = ""
        self._header: list[str] | None = None
        self._buffer = ""
        self._samples: deque[tuple[float, float]] = deque()
        self._poll = QTimer(self)
        self._poll.setInterval(1200)
        self._poll.timeout.connect(self._check_foreground)
        self._publish = QTimer(self)
        self._publish.setInterval(400)
        self._publish.timeout.connect(self._emit_stats)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self) -> None:
        if self._enabled:
            return
        if sys.platform != "win32":
            self.status_changed.emit("FPS-счётчик доступен только в Windows 10/11")
            return
        self._enabled = True
        path = presentmon_tool_path()
        if verify_presentmon(path):
            self._begin_capture()
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._enabled = False
            self.status_changed.emit(f"Не удалось создать папку FPS-компонента: {exc}")
            return
        self._download_path = path
        save_file = QSaveFile(str(path))
        if not save_file.open(QIODevice.OpenModeFlag.WriteOnly):
            self._enabled = False
            self.status_changed.emit(f"Не удалось сохранить PresentMon: {save_file.errorString()}")
            return
        self._save_file = save_file
        request = QNetworkRequest(QUrl(PRESENTMON_URL))
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self._network.get(request)
        self._download_reply = reply
        reply.readyRead.connect(self._write_download_chunk)
        reply.downloadProgress.connect(self._download_progress)
        reply.finished.connect(lambda: self._download_finished(reply))
        self.status_changed.emit("Скачиваем FPS-компонент PresentMon с GitHub…")

    def stop(self) -> None:
        self._enabled = False
        self._poll.stop()
        self._publish.stop()
        if self._download_reply is not None:
            self._download_reply.abort()
            self._download_reply = None
        if self._save_file is not None:
            self._save_file.cancelWriting()
            self._save_file = None
        self._stop_process()
        self._target = ""
        self._samples.clear()
        self.status_changed.emit("Счётчик FPS выключен")

    def _write_download_chunk(self) -> None:
        if self._download_reply is None or self._save_file is None:
            return
        data = bytes(self._download_reply.readAll())
        if data and self._save_file.write(data) != len(data):
            self._download_reply.abort()

    def _download_progress(self, received: int, total: int) -> None:
        if total > 0:
            self.progress_changed.emit(min(100, int(received * 100 / total)))

    def _download_finished(self, reply: QNetworkReply) -> None:
        self._write_download_chunk()
        error = reply.error()
        error_text = reply.errorString()
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        reply.deleteLater()
        self._download_reply = None
        save_file, path = self._save_file, self._download_path
        self._save_file = None
        self._download_path = None
        if save_file is None or path is None:
            return
        if error != QNetworkReply.NetworkError.NoError or (status is not None and int(status) >= 400):
            save_file.cancelWriting()
            self._enabled = False
            self.status_changed.emit(f"Не удалось скачать PresentMon: {error_text or status}")
            return
        if not save_file.commit() or not verify_presentmon(path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            self._enabled = False
            self.status_changed.emit("Проверка PresentMon не пройдена. Файл удалён, запуск отменён.")
            return
        self.progress_changed.emit(100)
        self._begin_capture()

    def _begin_capture(self) -> None:
        if not self._enabled:
            return
        self._poll.start()
        self._publish.start()
        self.status_changed.emit("FPS-счётчик включён. Переключись в игру — NOVA найдёт активное окно.")
        self._check_foreground()

    def _check_foreground(self) -> None:
        if not self._enabled:
            return
        name = foreground_process_name()
        if not name:
            return
        if name.lower() == self._target.lower():
            return
        self._stop_process()
        self._target = name
        self._header = None
        self._buffer = ""
        self._samples.clear()
        path = presentmon_tool_path()
        if not verify_presentmon(path):
            self.status_changed.emit("FPS-компонент не найден или повреждён. Выключи и включи счётчик снова.")
            return
        process = QProcess(self)
        process.setProgram(str(path))
        process.setArguments([
            "--process_name", name,
            "--output_stdout",
            "--no_console_stats",
            "--v1_metrics",
            "--exclude_dropped",
            "--terminate_on_proc_exit",
        ])
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(lambda: self._read_capture_output(process))
        process.errorOccurred.connect(lambda _error: self._capture_error(process))
        process.finished.connect(lambda _code, _status: self._capture_finished(process))
        self._process = process
        process.start()
        self.status_changed.emit(f"Считываем кадры: {name}")

    def _read_capture_output(self, process: QProcess) -> None:
        if self._process is not process:
            return
        try:
            self._buffer += bytes(process.readAllStandardOutput()).decode("utf-8-sig", errors="replace")
        except Exception:
            return
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                row = next(csv.reader([line]))
            except (csv.Error, StopIteration):
                continue
            if self._header is None:
                if "Application" in row and "ProcessID" in row:
                    self._header = row
                continue
            frame_ms = parse_frametime_csv_row(self._header, row)
            if frame_ms is not None:
                now = time.monotonic()
                self._samples.append((now, frame_ms))
                self._trim_samples(now)

    def _trim_samples(self, now: float) -> None:
        while self._samples and now - self._samples[0][0] > 30.0:
            self._samples.popleft()

    def _emit_stats(self) -> None:
        if not self._enabled:
            return
        now = time.monotonic()
        self._trim_samples(now)
        if not self._samples:
            self.stats_changed.emit({"fps": 0, "avg": 0, "low": 0, "low_1": 0, "low_01": 0,
                                     "frame_ms": 0.0, "stutters": 0, "peak": 0, "game": self._target})
            return
        recent = [frame_ms for stamp, frame_ms in self._samples if now - stamp <= 1.0]
        times = [frame_ms for _stamp, frame_ms in self._samples]
        fps = len(recent)
        avg_fps = round(1000.0 / statistics.fmean(times)) if times else 0
        slow_sorted = sorted(times, reverse=True)
        slow_1_count = max(1, (len(slow_sorted) + 99) // 100)
        slow_01_count = max(1, (len(slow_sorted) + 999) // 1000)
        slow_1 = slow_sorted[:slow_1_count]
        slow_01 = slow_sorted[:slow_01_count]
        low_1_fps = round(1000.0 / statistics.fmean(slow_1)) if slow_1 else 0
        low_01_fps = round(1000.0 / statistics.fmean(slow_01)) if slow_01 else 0
        frame_ms = statistics.fmean(recent) if recent else statistics.fmean(times)
        stutters = sum(1 for value in recent if value >= 33.3)
        peak_fps = max((round(1000.0 / value) for value in recent if value > 0), default=0)
        self.stats_changed.emit({
            "fps": fps, "avg": avg_fps, "low": low_1_fps, "low_1": low_1_fps,
            "low_01": low_01_fps, "frame_ms": frame_ms, "stutters": stutters,
            "peak": peak_fps, "game": self._target,
        })

    def _capture_error(self, process: QProcess) -> None:
        if self._process is process:
            self.status_changed.emit(
                "PresentMon не получил доступ к сбору кадров. Запусти NOVA от имени администратора "
                "или проверь членство в группе Performance Log Users."
            )

    def _capture_finished(self, process: QProcess) -> None:
        if self._process is process and self._enabled:
            self._process = None
            self.status_changed.emit("Сбор FPS остановлен. Переключись в игру или перезапусти счётчик.")

    def _stop_process(self) -> None:
        process, self._process = self._process, None
        if process is None:
            return
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                if not process.waitForFinished(700):
                    process.kill()
            process.deleteLater()
        except Exception:  # noqa: BLE001
            log.debug("Ошибка остановки PresentMon", exc_info=True)
