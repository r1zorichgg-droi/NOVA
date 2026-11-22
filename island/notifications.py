"""Источники уведомлений островка: USB-устройства, громкость, запуск приложений.

Системные тосты Windows (чужие уведомления) на этапе 1 не перехватываются: для этого нужен
UserNotificationListener, который требует удостоверения приложения (package identity) и согласия пользователя.
Вместо этого NotificationHub.post() доступен самой NOVA — например, «игра запущена», «очистка завершена».
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import uuid

import psutil
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QWidget

from island.model import Notification, friendly_process_name
from settings import AppSettings
from system import winapi

log = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"
SETTING_FOR_KIND = {
    "usb": "island_notify_usb", "volume": "island_notify_volume",
    "app": "island_notify_apps", "music": "island_notify_music", "boost": "boost_notify",
}


class NotificationHub(QObject):
    """Фильтрует уведомления по настройкам и отдаёт островку."""
    notify = Signal(object)  # Notification

    def __init__(self, settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings

    def post(self, notification: Notification) -> None:
        key = SETTING_FOR_KIND.get(notification.kind)
        if key is None or self._settings.get(key):
            self.notify.emit(notification)


# --------------------------------------------------------------------------- USB
_GUID_USB_DEVICE = uuid.UUID("A5DCBF10-6530-11D2-901F-00C04FB951ED")
_WM_DEVICECHANGE = 0x0219
_DBT_DEVICEARRIVAL, _DBT_DEVICEREMOVECOMPLETE = 0x8000, 0x8004
_DBT_DEVTYP_VOLUME, _DBT_DEVTYP_DEVICEINTERFACE = 2, 5
_DEVICE_NOTIFY_WINDOW_HANDLE = 0
_NAME_OFFSET = 28   # смещение dbcc_name в DEV_BROADCAST_DEVICEINTERFACE_W


def _friendly_usb_name(device_path: str) -> str:
    """Имя устройства из реестра (Enum\\USB\\VID_xxxx&PID_xxxx\\...), иначе «VID_xxxx PID_xxxx»."""
    parts = device_path.split("#")
    if len(parts) < 3:
        return "USB-устройство"
    vid_pid, instance = parts[1], parts[2]
    ids = vid_pid.replace("&", " ")
    if IS_WINDOWS:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                rf"SYSTEM\CurrentControlSet\Enum\USB\{vid_pid}\{instance}") as key:
                for value_name in ("FriendlyName", "DeviceDesc"):
                    try:
                        text = str(winreg.QueryValueEx(key, value_name)[0])
                    except FileNotFoundError:
                        continue
                    text = text.split(";")[-1].strip()   # "@oem1.inf,%name%;Реальное имя"
                    if text:
                        return text
        except OSError:
            pass
    return f"USB {ids}"


def _drive_letters(unit_mask: int) -> list[str]:
    return [f"{chr(ord('A') + i)}:" for i in range(26) if unit_mask & (1 << i)]


class DeviceWatcher(QWidget):
    """Невидимое окно, получающее WM_DEVICECHANGE (подключение/отключение USB и дисков)."""
    arrived = Signal(str)
    removed = Signal(str)

    def __init__(self) -> None:
        super().__init__(None)
        self._notify_handle = None
        self._pending_iface: dict[str, QTimer] = {}
        self._volume_recent = False

    def start(self) -> bool:
        if not IS_WINDOWS:
            return False
        try:
            hwnd = int(self.winId())  # создаёт нативное окно, не показывая его

            class _Filter(ctypes.Structure):
                _fields_ = [("size", ctypes.c_uint32), ("devtype", ctypes.c_uint32), ("reserved", ctypes.c_uint32),
                            ("guid", ctypes.c_ubyte * 16), ("name", ctypes.c_wchar * 1)]

            flt = _Filter()
            flt.size, flt.devtype = ctypes.sizeof(_Filter), _DBT_DEVTYP_DEVICEINTERFACE
            flt.guid = (ctypes.c_ubyte * 16).from_buffer_copy(_GUID_USB_DEVICE.bytes_le)
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.RegisterDeviceNotificationW.restype = ctypes.c_void_p
            user32.RegisterDeviceNotificationW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
            handle = user32.RegisterDeviceNotificationW(hwnd, ctypes.byref(flt), _DEVICE_NOTIFY_WINDOW_HANDLE)
            self._notify_handle = handle
            return bool(handle)
        except Exception:
            log.exception("Не удалось подписаться на события USB")
            return False

    def nativeEvent(self, eventType, message):  # noqa: N802 (имя из Qt)
        try:
            if IS_WINDOWS and bytes(eventType) == b"windows_generic_MSG":
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == _WM_DEVICECHANGE and msg.wParam in (_DBT_DEVICEARRIVAL, _DBT_DEVICEREMOVECOMPLETE) \
                        and msg.lParam:
                    self._handle_change(int(msg.wParam), int(msg.lParam))
        except Exception:
            log.debug("Сбой разбора WM_DEVICECHANGE", exc_info=True)
        return False, 0

    def _handle_change(self, event: int, lparam: int) -> None:
        devtype = ctypes.c_uint32.from_address(lparam + 4).value
        arrival = event == _DBT_DEVICEARRIVAL
        if devtype == _DBT_DEVTYP_VOLUME:
            mask = ctypes.c_uint32.from_address(lparam + 12).value
            letters = ", ".join(_drive_letters(mask)) or "диск"
            for timer in self._pending_iface.values():
                timer.stop()             # накопитель уже опознан по тому — общий USB-тост не нужен
            self._pending_iface.clear()
            (self.arrived if arrival else self.removed).emit(f"Накопитель {letters}")
        elif devtype == _DBT_DEVTYP_DEVICEINTERFACE:
            path = ctypes.wstring_at(lparam + _NAME_OFFSET)
            name = _friendly_usb_name(path)
            if not arrival:
                self.removed.emit(name)
                return
            if name in self._pending_iface:
                return                   # составные устройства шлют несколько событий подряд
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda n=name: self._emit_iface(n))
            self._pending_iface[name] = timer
            timer.start(700)

    def _emit_iface(self, name: str) -> None:
        self._pending_iface.pop(name, None)
        self.arrived.emit(name)


# ------------------------------------------------------------------------ громкость
class VolumeWatcher(QThread):
    """Опрашивает общую громкость (pycaw) раз в 250 мс; при отсутствии pycaw просто не стартует."""
    changed = Signal(int, bool)   # процент, выключен ли звук

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stop_event = threading.Event()
        self.available = IS_WINDOWS
        self.error = "" if IS_WINDOWS else "только Windows"

    @staticmethod
    def _endpoint():
        from pycaw.pycaw import AudioUtilities  # type: ignore[import-not-found,unused-ignore]
        device = AudioUtilities.GetSpeakers()
        endpoint = getattr(device, "EndpointVolume", None)   # новые версии pycaw
        if endpoint is not None:
            return endpoint
        from comtypes import CLSCTX_ALL  # type: ignore[import-not-found,unused-ignore]
        from pycaw.pycaw import IAudioEndpointVolume  # type: ignore[import-not-found,unused-ignore]
        interface = device.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        return ctypes.cast(interface, ctypes.POINTER(IAudioEndpointVolume))

    def run(self) -> None:
        if not IS_WINDOWS:
            return
        try:
            import comtypes  # type: ignore[import-not-found,unused-ignore]
            comtypes.CoInitialize()
        except Exception as exc:
            self.available, self.error = False, f"comtypes: {exc}"
            return
        last: tuple[int, bool] | None = None
        endpoint = None
        refresh_at = 0
        tick = 0
        try:
            while not self._stop_event.wait(0.25):
                tick += 1
                try:
                    if endpoint is None or tick >= refresh_at:       # раз в ~30 с — вдруг сменили устройство вывода
                        endpoint, refresh_at = self._endpoint(), tick + 120
                    level = int(round(endpoint.GetMasterVolumeLevelScalar() * 100))
                    muted = bool(endpoint.GetMute())
                except Exception as exc:
                    endpoint, refresh_at = None, tick + 20
                    if isinstance(exc, ImportError):
                        self.available, self.error = False, "pycaw не установлен"
                        return
                    continue
                current = (level, muted)
                if last is not None and current != last:
                    self.changed.emit(level, muted)
                last = current
        finally:
            try:
                comtypes.CoUninitialize()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(1500)


# ---------------------------------------------------------------- запуск приложений
_IGNORED_PROCESSES = {
    "explorer.exe", "applicationframehost.exe", "textinputhost.exe", "searchhost.exe", "shellexperiencehost.exe",
    "startmenuexperiencehost.exe", "lockapp.exe", "dwm.exe", "ctfmon.exe", "searchapp.exe", "widgets.exe",
    "msedgewebview2.exe", "backgroundtaskhost.exe", "runtimebroker.exe", "systemsettings.exe",
}


class AppLaunchWatcher(QObject):
    """Раз в 2 с смотрит, у каких процессов появилось окно; новые PID (которых не было после старта) — «запущено»."""
    app_started = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._seen: set[int] = set()
        self._primed = False
        self._own = os.getpid()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._poll)

    def start(self) -> None:
        if winapi.IS_WINDOWS and not self._timer.isActive():
            self._primed = False
            self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _poll(self) -> None:
        try:
            current = winapi.visible_window_pids() - {self._own}
            if not self._primed:
                self._seen, self._primed = set(current), True   # то, что уже было открыто, не «запуск»
                return
            for pid in sorted(current - self._seen):
                name = winapi.process_name(pid)
                if name and name.lower() not in _IGNORED_PROCESSES:
                    self.app_started.emit(friendly_process_name(name))
            self._seen = (self._seen | current) & set(psutil.pids())
        except Exception:
            log.debug("Сбой опроса запущенных окон", exc_info=True)
