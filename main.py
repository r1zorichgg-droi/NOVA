"""NOVA — лаунчер, центр управления ПК и Dynamic Island для Windows. Точка входа."""
from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from app_context import AppContext
from config import APP_NAME, APP_USER_MODEL_ID, APP_VERSION, db_path, setup_logging
from database.database import Database, StateStore
from island.game_detector import GameDetector
from island.island_window import IslandWindow
from island.model import Notification
from island.music_controller import MusicController
from island.notifications import AppLaunchWatcher, DeviceWatcher, NotificationHub, VolumeWatcher
from launcher.boost import BoostManager
from launcher.library_manager import LibraryManager
from settings import AppSettings
from system import winapi
from system.power import PowerManager
from system.sampler import Sampler
from system.tweaks import TweakManager
from ui.icon_cache import IconCache
from ui.main_window import MainWindow, app_icon
from ui.style import apply_theme, build_app_font
from ui.tray import Tray

log = logging.getLogger("nova")
SERVER_NAME = f"{APP_USER_MODEL_ID}.SingleInstance"


def _log_uncaught(exc_type, exc, tb) -> None:  # noqa: ANN001
    log.critical("Необработанное исключение", exc_info=(exc_type, exc, tb))


def _signal_running_instance() -> bool:
    """Если NOVA уже запущена, просит её показать окно и возвращает True."""
    sock = QLocalSocket()
    sock.connectToServer(SERVER_NAME)
    if not sock.waitForConnected(400):
        return False
    sock.write(b"show")
    sock.flush()
    sock.waitForBytesWritten(400)
    sock.disconnectFromServer()
    return True


def main() -> int:
    setup_logging()
    sys.excepthook = _log_uncaught
    threading.excepthook = lambda args: _log_uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    log.info("%s %s запускается", APP_NAME, APP_VERSION)
    start_minimized = "--minimized" in sys.argv[1:]

    winapi.set_app_user_model_id(APP_USER_MODEL_ID)
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setQuitOnLastWindowClosed(False)   # закрытие окна сворачивает в трей, выход — через меню трея
    app.setStyle("Fusion")
    app.setFont(build_app_font())
    app.setWindowIcon(app_icon())

    if _signal_running_instance():
        log.info("NOVA уже запущена — показываю её окно и выхожу")
        return 0
    QLocalServer.removeServer(SERVER_NAME)   # убирает «залипший» сокет после аварийного завершения
    server = QLocalServer()
    server.listen(SERVER_NAME)

    db = Database(db_path())
    settings = AppSettings(db)
    apply_theme(app, settings)

    # --- сервисы ---
    sampler = Sampler()
    sampler.start()
    music = MusicController()
    music.set_active(bool(settings.get("island_enabled")))
    island = IslandWindow(settings, sampler, music)
    hub = NotificationHub(settings)
    detector = GameDetector(settings)
    volume = VolumeWatcher()
    device_watcher = DeviceWatcher()
    app_watcher = AppLaunchWatcher()
    state = StateStore(db)               # служебное состояние (резервные копии твиков и режима производительности)
    power = PowerManager()
    tweak_manager = TweakManager(state)
    boost = BoostManager(power, tweak_manager, state)
    library = LibraryManager(db, settings, hub, boost, tweak_manager)
    icons = IconCache()
    island.attach_launcher(library, icons)
    ctx = AppContext(db=db, settings=settings, sampler=sampler, music=music, island=island,
                     hub=hub, detector=detector, volume=volume, state=state, power=power,
                     tweaks=tweak_manager, boost=boost, library=library, icons=icons)

    # --- уведомления и игровой режим ---
    hub.notify.connect(island.show_notification)

    def on_volume(level: int, muted: bool) -> None:
        if muted:
            hub.post(Notification("volume", "Звук выключен", progress=0.0, duration_ms=1600))
        else:
            hub.post(Notification("volume", f"Громкость {level}%", progress=level / 100.0, duration_ms=1600))

    volume.changed.connect(on_volume)
    app_watcher.app_started.connect(lambda name: hub.post(Notification("app", name, "Запущено")))
    if device_watcher.start():
        device_watcher.arrived.connect(lambda name: hub.post(Notification("usb", name, "Подключено")))
        device_watcher.removed.connect(lambda name: hub.post(Notification("usb", name, "Отключено")))

    def on_fullscreen(active: bool, name: str) -> None:
        island.on_fullscreen(active, name)
        if active:
            app_watcher.stop()    # в игре лишние опросы окон не нужны
        else:
            app_watcher.start()

    detector.fullscreen_changed.connect(on_fullscreen)

    # --- окно и трей ---
    window = MainWindow(ctx)
    tray: Tray | None = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = Tray(window, ctx, app_icon())
        tray.show()
        window.tray_available = True

    def on_second_instance() -> None:
        sock = server.nextPendingConnection()
        if sock is not None:
            sock.close()
            sock.deleteLater()
        window.bring_to_front()

    server.newConnection.connect(on_second_instance)
    island.settings_requested.connect(window.show_island_page)   # плитка «+» во вкладке «Приложения»

    def shutdown() -> None:
        log.info("NOVA завершает работу")
        steps: list[Callable[[], object]] = [
            library.shutdown,        # первым: возвращает схему питания и настройки Windows, если игра ещё идёт
            detector.stop, app_watcher.stop, volume.stop, device_watcher.close, island.shutdown,
            music.stop, sampler.shutdown, settings.flush, db.close]
        if tray is not None:
            steps.insert(0, tray.hide)
        for step in steps:
            try:
                step()
            except Exception:  # noqa: BLE001 — завершаем остальное, даже если один шаг упал
                log.exception("Ошибка при завершении (%s)", getattr(step, "__qualname__", step))

    app.aboutToQuit.connect(shutdown)

    # --- запуск ---
    music.start()
    volume.start()
    island.refresh_visibility()
    detector.start()
    app_watcher.start()
    library.recover()        # прошлый запуск завершился во время игры — вернуть настройки или продолжить слежение
    if settings.get("auto_scan"):
        QTimer.singleShot(1500, lambda: library.scan(manual=False))   # не мешаем появлению окна
    if not (start_minimized and tray is not None):
        window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
