"""Тонкая обёртка над WinAPI (ctypes). Только чтение состояния окон + оформление окон NOVA.

Никакой инъекции в чужие процессы, хуков и перехвата клавиш: только GetForegroundWindow,
GetWindowRect, EnumWindows и подобные «безопасные» вызовы, которые не конфликтуют с античитами.
На не-Windows все функции возвращают безопасные заглушки.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

IS_WINDOWS = sys.platform == "win32"

Rect = tuple[int, int, int, int]  # left, top, right, bottom


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    pid: int
    class_name: str
    rect: Rect
    monitor_rect: Rect
    style: int


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    _dwm = ctypes.WinDLL("dwmapi", use_last_error=True)

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class _SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR),
                    ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
                    ("dwHotKey", wintypes.DWORD), ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    _shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_SHELLEXECUTEINFOW)]
    _shell32.ShellExecuteExW.restype = wintypes.BOOL
    _kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel32.WaitForSingleObject.restype = wintypes.DWORD
    _kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _user32.GetForegroundWindow.restype = wintypes.HWND
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    _user32.MonitorFromWindow.restype = wintypes.HANDLE
    _user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(_MONITORINFO)]
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    _user32.GetWindow.restype = wintypes.HWND
    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, wintypes.UINT]
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _dwm.DwmSetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    _dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]

_GWL_STYLE, _GWL_EXSTYLE = -16, -20
_WS_EX_TOOLWINDOW, _WS_EX_APPWINDOW = 0x00000080, 0x00040000
_GW_OWNER = 4
_DWMWA_CLOAKED = 14


def get_foreground_info() -> WindowInfo | None:
    """Окно переднего плана: процесс, класс, прямоугольник и прямоугольник его монитора."""
    if not IS_WINDOWS:
        return None
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    cls = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, cls, 256)
    rect = wintypes.RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    mon = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not mon or not _user32.GetMonitorInfoW(mon, ctypes.byref(info)):
        return None
    m = info.rcMonitor
    style = _user32.GetWindowLongW(hwnd, _GWL_STYLE) & 0xFFFFFFFF
    return WindowInfo(int(hwnd), int(pid.value), cls.value,
                      (rect.left, rect.top, rect.right, rect.bottom),
                      (m.left, m.top, m.right, m.bottom), style)


def shell_notification_state() -> int | None:
    """SHQueryUserNotificationState: 3 = полноэкранное Direct3D-приложение (эксклюзивный fullscreen)."""
    if not IS_WINDOWS:
        return None
    state = ctypes.c_int()
    if _shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return None
    return int(state.value)


def visible_window_pids() -> set[int]:
    """PID процессов, у которых есть видимое окно верхнего уровня (то, что видно как «приложение»)."""
    if not IS_WINDOWS:
        return set()
    pids: set[int] = set()

    def callback(hwnd: int, _lparam: int) -> bool:
        try:
            if not _user32.IsWindowVisible(hwnd) or _user32.GetWindowTextLengthW(hwnd) == 0:
                return True
            if _user32.GetWindow(hwnd, _GW_OWNER):
                return True
            ex = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE) & 0xFFFFFFFF
            if ex & _WS_EX_TOOLWINDOW and not ex & _WS_EX_APPWINDOW:
                return True
            cloaked = wintypes.DWORD(0)
            _dwm.DwmGetWindowAttribute(hwnd, _DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
            if cloaked.value:
                return True  # окна «на других рабочих столах» и спящие UWP
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                pids.add(int(pid.value))
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return pids


def request_close_windows(pid: int) -> int:
    """Посылает видимым окнам процесса WM_CLOSE — то же, что нажатие на крестик: программа сама решит,
    закрыться ли и не спросить ли про несохранённые данные. Используется только для программ, которые
    пользователь сам отметил в окне «Закрыть фоновые программы». Возвращает число окон."""
    if not IS_WINDOWS:
        return 0
    count = 0

    def callback(hwnd: int, _lparam: int) -> bool:
        nonlocal count
        try:
            owner = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid and _user32.IsWindowVisible(hwnd):
                _user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
                count += 1
        except Exception:
            pass
        return True

    _user32.EnumWindows(_WNDENUMPROC(callback), 0)
    return count


def set_topmost(hwnd: int) -> None:
    """Повторно «прижимает» окно к верху z-порядка, не забирая фокус."""
    if not IS_WINDOWS:
        return
    HWND_TOPMOST, flags = wintypes.HWND(-1), 0x0001 | 0x0002 | 0x0010 | 0x0040  # NOSIZE|NOMOVE|NOACTIVATE|SHOWWINDOW
    _user32.SetWindowPos(wintypes.HWND(hwnd), HWND_TOPMOST, 0, 0, 0, 0, flags)


def style_window(hwnd: int, dark: bool, caption_rgb: tuple[int, int, int], text_rgb: tuple[int, int, int]) -> None:
    """Тёмная/светлая рамка заголовка, цвет шапки и скруглённые углы (Windows 10 20H1+/11; ошибки игнорируются)."""
    if not IS_WINDOWS:
        return

    def put(attr: int, value: int) -> None:
        val = ctypes.c_int(value)
        _dwm.DwmSetWindowAttribute(wintypes.HWND(hwnd), attr, ctypes.byref(val), ctypes.sizeof(val))

    def colorref(rgb: tuple[int, int, int]) -> int:
        return rgb[0] | (rgb[1] << 8) | (rgb[2] << 16)

    try:
        put(20, 1 if dark else 0)            # DWMWA_USE_IMMERSIVE_DARK_MODE
        put(33, 2)                           # DWMWA_WINDOW_CORNER_PREFERENCE = DWMWCP_ROUND
        put(35, colorref(caption_rgb))       # DWMWA_CAPTION_COLOR (Windows 11)
        put(36, colorref(text_rgb))          # DWMWA_TEXT_COLOR (Windows 11)
    except Exception:
        pass


def set_app_user_model_id(app_id: str) -> None:
    """Чтобы значок NOVA на панели задач не склеивался с python.exe."""
    if IS_WINDOWS:
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(app_id)
        except Exception:
            pass


def process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def reveal_in_explorer(path: str) -> bool:
    """Открывает Проводник с выделенным файлом. False — не Windows или не получилось (тогда открывают папку)."""
    if not IS_WINDOWS or not path:
        return False
    import os
    import subprocess
    try:
        subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])  # noqa: S603, S607
        return True
    except OSError:
        return False


ERROR_CANCELLED = 1223          # пользователь отказался в окне UAC
_SEE_NOCLOSEPROCESS, _SEE_NOASYNC, _SEE_FLAG_NO_UI = 0x40, 0x100, 0x400
_WAIT_TIMEOUT = 0x102


@dataclass(frozen=True)
class ExecResult:
    started: bool
    exit_code: int | None = None    # None — не удалось узнать (нет описателя процесса или не дождались)
    error: int = 0                  # код ошибки Windows, если started == False
    timed_out: bool = False


def is_admin() -> bool:
    """Запущена ли NOVA с правами администратора (на не-Windows — False)."""
    if not IS_WINDOWS:
        return False
    try:
        return bool(_shell32.IsUserAnAdmin())
    except Exception:
        return False


def shell_execute_wait(file: str, params: str = "", elevate: bool = True, show: bool = True,
                       timeout_s: float = 1800.0) -> ExecResult:
    """Запускает программу через ShellExecuteEx (при elevate — с запросом UAC) и ждёт её завершения.

    Процесс по таймауту НЕ убивается: недоделанное удаление хуже, чем долгое ожидание.
    """
    if not IS_WINDOWS:
        return ExecResult(False, error=-1)
    info = _SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_NOCLOSEPROCESS | _SEE_NOASYNC | _SEE_FLAG_NO_UI
    info.lpVerb = "runas" if elevate else "open"
    info.lpFile = file
    info.lpParameters = params or None
    info.nShow = 1 if show else 0
    if not _shell32.ShellExecuteExW(ctypes.byref(info)):
        return ExecResult(False, error=ctypes.get_last_error())
    handle = info.hProcess
    if not handle:
        return ExecResult(True)
    try:
        if _kernel32.WaitForSingleObject(handle, int(timeout_s * 1000)) == _WAIT_TIMEOUT:
            return ExecResult(True, timed_out=True)
        code = wintypes.DWORD()
        if not _kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return ExecResult(True)
        return ExecResult(True, int(code.value))
    finally:
        _kernel32.CloseHandle(handle)
