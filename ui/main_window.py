"""Главное окно NOVA: боковая панель + страницы."""
from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray, QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QHBoxLayout, QMainWindow, QWidget

from app_context import AppContext
from config import APP_NAME, APP_VERSION, resource_path
from island.model import Notification, animation_scale
from system import winapi
from system.collector import Snapshot
from ui import theme
from ui.automation_page import AutomationPage
from ui.debloat_page import DebloatPage
from ui.disk_analyzer_page import DiskAnalyzerPage
from ui.home_page import HomePage
from ui.island_page import IslandPage
from ui.install_page import InstallPage
from ui.library_page import LibraryPage
from ui.monitor_page import MonitorPage
from ui.network_page import NetworkPage
from ui.overlay_page import OverlayPage
from ui.update_page import UpdatePage
from ui.process_page import ProcessesPage
from ui.system_page import SystemPage
from ui.tools_page import ToolsPage
from ui.optimize_page import OptimizePage
from ui.power_page import PowerPage
from ui.settings_page import SettingsPage
from ui.sidebar import NAV_ITEMS, Sidebar
from ui.style import apply_theme
from ui.toast import Toast
from ui.widgets import FadeStack, Page

log = logging.getLogger(__name__)
SAMPLE_INTERVAL_MS = 1000
PAGE_FADE_MS = 180
ISLAND_PAGE_INDEX = next(i for i, (key, _title) in enumerate(NAV_ITEMS) if key == "island")   # страницы идут в порядке NAV_ITEMS


def app_icon() -> QIcon:
    return QIcon(str(resource_path("assets", "icons", "nova.png")))


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__()
        self._ctx = ctx
        self._quitting = False
        self.tray_available = False   # main.py ставит True, когда значок в трее создан
        self.toast: Toast | None = None   # создаётся ниже; resizeEvent приходит раньше

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.setMinimumSize(980, 620)
        self.resize(1200, 780)
        self._restore_geometry()

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.sidebar = Sidebar(APP_VERSION)
        self.stack = FadeStack()
        self.pages: list[Page] = [
            HomePage(ctx), LibraryPage(ctx, "game"), LibraryPage(ctx, "app"), InstallPage(), PowerPage(ctx), OptimizePage(ctx),
            DebloatPage(ctx), MonitorPage(ctx), DiskAnalyzerPage(), NetworkPage(ctx), OverlayPage(ctx), ProcessesPage(), ToolsPage(), SystemPage(), IslandPage(ctx),
            AutomationPage(ctx), UpdatePage(ctx), SettingsPage(ctx)]
        for page in self.pages:
            self.stack.addWidget(page)
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack, 1)
        self.toast = Toast(root)
        ctx.library.notice.connect(self._on_notice)
        ctx.library.scan_done.connect(self._on_scan_done)

        self.sidebar.page_selected.connect(self.show_page)
        self.sidebar.set_current(0)
        ctx.sampler.updated.connect(self._on_snapshot)
        ctx.settings.changed.connect(self._on_setting)
        self.setWindowOpacity(float(ctx.settings.get("window_opacity")))
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self._shutdown_pages)

    # -- навигация -----------------------------------------------------------------
    def show_page(self, index: int) -> None:
        if not 0 <= index < len(self.pages):
            return
        self.stack.duration_ms = int(PAGE_FADE_MS * animation_scale(str(self._ctx.settings.get("animations"))))
        self.stack.set_page(index)
        self.sidebar.set_current(index)
        self._sync_sampler()

    def show_island_page(self) -> None:
        self.bring_to_front()
        self.show_page(ISLAND_PAGE_INDEX)

    def current_page(self) -> Page:
        return self.pages[self.stack.currentIndex()]

    # -- метрики: сэмплер работает, только пока окно видно и открыта страница с графиками ----
    def _sync_sampler(self) -> None:
        sampler = self._ctx.sampler
        if self.isVisible() and not self.isMinimized() and self.current_page().needs_samples:
            sampler.acquire("main", SAMPLE_INTERVAL_MS)
        else:
            sampler.release("main")

    def _on_snapshot(self, snap: Snapshot) -> None:
        if self.isVisible() and not self.isMinimized():
            page = self.current_page()
            if page.needs_samples:
                page.on_data(snap)

    # -- сообщения библиотеки ---------------------------------------------------------
    def _visible_to_user(self) -> bool:
        return self.isVisible() and not self.isMinimized()

    def _on_notice(self, level: str, text: str) -> None:
        if not text:
            return
        if self._visible_to_user() and self.toast is not None:
            self.toast.show_message(level, text)
        else:                                   # окно в трее — показываем сообщение в Dynamic Island
            self._ctx.hub.post(Notification("system", "NOVA", text[:90], duration_ms=4200))

    def _on_scan_done(self, summary) -> None:  # noqa: ANN001 — launcher.library_manager.ScanSummary
        games, apps = summary.games.added, summary.apps.added
        if games or apps or summary.pruned:
            tail = f" Убрано лишнего из списка программ: {summary.pruned}." if summary.pruned else ""
            self._on_notice("info", f"Поиск завершён: добавлено игр — {games}, программ — {apps}.{tail}")
        elif summary.manual:
            note = " " + summary.notes[0] if summary.notes else ""
            self._on_notice("info", "Поиск завершён: новых игр и программ не найдено." + note)

    # -- настройки -----------------------------------------------------------------
    def _on_setting(self, key: str) -> None:
        if key in ("theme", "accent"):
            app = QApplication.instance()
            if isinstance(app, QApplication):
                apply_theme(app, self._ctx.settings)
            self._apply_native_style()
        elif key == "window_opacity":
            self.setWindowOpacity(float(self._ctx.settings.get("window_opacity")))

    def _apply_native_style(self) -> None:
        """Цвет и тёмный режим системной рамки окна (Windows 10/11); на других ОС ничего не делает."""
        pal = theme.current()
        bg, fg = pal.bg, pal.text
        winapi.style_window(int(self.winId()), pal.dark, (bg[0], bg[1], bg[2]), (fg[0], fg[1], fg[2]))

    def _shutdown_pages(self) -> None:
        for page in self.pages:
            shutdown = getattr(page, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception:  # noqa: BLE001
                    log.exception("Не удалось корректно остановить страницу %s", type(page).__name__)

    # -- геометрия окна ------------------------------------------------------------
    def _restore_geometry(self) -> None:
        raw = str(self._ctx.settings.get("window_geometry"))
        if raw:
            try:
                self.restoreGeometry(QByteArray.fromBase64(raw.encode("ascii")))
            except Exception:  # noqa: BLE001 — повреждённое значение не должно мешать запуску
                log.warning("Не удалось восстановить положение окна", exc_info=True)

    def _save_geometry(self) -> None:
        self._ctx.settings.set("window_geometry", self.saveGeometry().toBase64().data().decode("ascii"))

    # -- события окна ---------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._apply_native_style()
        self._sync_sampler()

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._ctx.sampler.release("main")
        super().hideEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        if self.toast is not None and self.toast.isVisible():
            self.toast.reposition()

    def changeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            self._sync_sampler()

    def closeEvent(self, event) -> None:  # noqa: N802, ANN001
        self._save_geometry()
        if not self._quitting and self.tray_available and self._ctx.settings.get("close_to_tray"):
            event.ignore()
            self.hide()
            return
        self._quitting = True
        event.accept()
        QApplication.quit()

    # -- действия из трея и от второго экземпляра ---------------------------------------
    def bring_to_front(self) -> None:
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def request_quit(self) -> None:
        self._quitting = True
        self._save_geometry()
        QApplication.quit()

    @property
    def quitting(self) -> bool:
        return self._quitting
