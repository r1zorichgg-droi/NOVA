"""Правило «это полноэкранная игра/приложение?» — чистая функция, без Qt и без обращений к WinAPI."""
from __future__ import annotations

from collections.abc import Callable

from system.winapi import Rect, WindowInfo

SHELL_CLASSES = frozenset({
    "Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "XamlExplorerHostIslandWindow",
})
WS_CAPTION = 0x00C00000
QUNS_RUNNING_D3D_FULL_SCREEN = 3


def covers(rect: Rect, monitor: Rect, tolerance: int = 1) -> bool:
    return (rect[0] <= monitor[0] + tolerance and rect[1] <= monitor[1] + tolerance
            and rect[2] >= monitor[2] - tolerance and rect[3] >= monitor[3] - tolerance)


def is_fullscreen_window(info: WindowInfo | None, own_pid: int, notify_state: int | None,
                         name_lookup: Callable[[int], str], ignore: frozenset[str]) -> tuple[bool, str]:
    """(полноэкранное ли окно, имя процесса).

    Эвристика:
      * эксклюзивный fullscreen Direct3D сообщает сама Windows (SHQueryUserNotificationState == 3);
      * borderless-окно: перекрывает весь монитор и не имеет заголовка (у развёрнутых обычных окон он есть);
      * рабочий стол, панель задач и сама NOVA никогда не считаются играми.
    """
    if info is None or info.pid == own_pid or info.class_name in SHELL_CLASSES:
        return False, ""
    d3d = notify_state == QUNS_RUNNING_D3D_FULL_SCREEN
    borderless = covers(info.rect, info.monitor_rect) and (info.style & WS_CAPTION) != WS_CAPTION
    if not (d3d or borderless):
        return False, ""
    name = name_lookup(info.pid)
    if name.lower() in ignore:
        return False, name
    return True, name
