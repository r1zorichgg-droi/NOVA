"""Игровой оверлей NOVA: компактные системные метрики поверх окон и игр."""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from system.collector import Snapshot
from system.fmt import fmt_bytes, fmt_percent, fmt_speed_long, fmt_temp
from ui import theme


class GameOverlayWindow(QWidget):
    """Перетаскиваемое always-on-top окно. Не внедряется в процессы игр и не читает память игр."""

    def __init__(self, opacity: float = 0.92, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("novaGameOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setWindowTitle("NOVA Game Overlay")
        self.setFixedWidth(276)
        self._drag_offset: QPoint | None = None
        self._opacity = max(0.45, min(1.0, float(opacity)))

        self.panel = QFrame(self)
        self.panel.setObjectName("overlayPanel")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.panel)
        body = QVBoxLayout(self.panel)
        body.setContentsMargins(14, 10, 14, 12)
        body.setSpacing(8)

        header = QHBoxLayout()
        brand = QLabel("NOVA")
        brand.setStyleSheet("font-size: 12px; font-weight: 800; letter-spacing: 1px;")
        self.status = QLabel("GAME OVERLAY")
        self.status.setObjectName("overlayMuted")
        header.addWidget(brand)
        header.addStretch(1)
        header.addWidget(self.status)
        body.addLayout(header)

        self.cpu = self._metric(body, "CPU")
        self.gpu = self._metric(body, "GPU")
        self.ram = self._metric(body, "RAM")
        self.net = self._metric(body, "СЕТЬ")
        self._apply_style()
        self.setWindowOpacity(self._opacity)
        self._place_default()

    @staticmethod
    def _metric(layout: QVBoxLayout, name: str) -> QLabel:
        row = QHBoxLayout()
        title = QLabel(name)
        title.setObjectName("overlayMuted")
        value = QLabel("—")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        value.setStyleSheet("font-size: 13px; font-weight: 650;")
        row.addWidget(title)
        row.addStretch(1)
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def _apply_style(self) -> None:
        pal = theme.current()
        self.panel.setStyleSheet(
            "QFrame#overlayPanel {"
            f"background: rgba({pal.card[0]}, {pal.card[1]}, {pal.card[2]}, 238);"
            f"border: 1px solid rgba({pal.accent[0]}, {pal.accent[1]}, {pal.accent[2]}, 150);"
            "border-radius: 14px;}"
            f"QLabel {{ color: rgb({pal.text[0]}, {pal.text[1]}, {pal.text[2]}); }}"
            f"QLabel#overlayMuted {{ color: rgb({pal.muted[0]}, {pal.muted[1]}, {pal.muted[2]});"
            "font-size: 10px; font-weight: 600; }"
        )

    def _place_default(self) -> None:
        screen = self.screen() or self.screenAtCursor()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 28, area.top() + 90)

    @staticmethod
    def screenAtCursor():
        from PySide6.QtGui import QCursor, QGuiApplication
        return QGuiApplication.screenAt(QCursor.pos())

    def update_snapshot(self, snap: Snapshot) -> None:
        self.cpu.setText(fmt_percent(snap.cpu.percent) + "  ·  " +
                         (fmt_temp(snap.cpu_temp) if snap.cpu_temp is not None else "темп. N/A"))
        gpu = snap.gpu
        self.gpu.setText((fmt_percent(gpu.percent) if gpu and gpu.percent is not None else "N/A") +
                         "  ·  " + (fmt_temp(snap.gpu_temp) if snap.gpu_temp is not None else "темп. N/A"))
        self.ram.setText(f"{fmt_percent(snap.ram.percent)}  ·  {fmt_bytes(snap.ram.used)}")
        self.net.setText(f"↓ {fmt_speed_long(snap.net.down_bps)}   ↑ {fmt_speed_long(snap.net.up_bps)}")

    def set_opacity(self, value: int) -> None:
        self._opacity = max(0.45, min(1.0, value / 100.0))
        self.setWindowOpacity(self._opacity)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class OverlayPage(QWidget):
    """Страница управления отдельным игровым оверлеем."""

    def __init__(self, ctx, parent: QWidget | None = None) -> None:  # noqa: ANN001
        from PySide6.QtWidgets import QSlider, QVBoxLayout, QHBoxLayout, QPushButton, QLabel
        from PySide6.QtCore import Qt
        from ui.widgets import Card, Page, Toggle, make_label, setting_row

        super().__init__(parent)
        self._ctx = ctx
        self._sampler = ctx.sampler
        self._overlay = GameOverlayWindow(float(ctx.settings.get("overlay_opacity")) / 100.0)
        self._enabled = bool(ctx.settings.get("overlay_enabled"))
        self._sampler.updated.connect(self._on_snapshot)

        # Use the standard Page layout and reparent its contents into this widget.
        self._page = Page("Игровой оверлей", "Системные показатели поверх игры. NOVA не внедряется в игру и не взаимодействует с античитом.")
        self._page.setParent(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._page)

        card = Card(spacing=12)
        card.body.addWidget(make_label("Оверлей", "h2"))
        self.toggle = Toggle(self._enabled)
        self.toggle.toggled.connect(self._set_enabled)
        card.body.addWidget(setting_row("Показывать игровой оверлей",
            "Компактное окно поверх рабочего стола и оконных/безрамочных игр.", self.toggle))

        self.opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(45, 100)
        self.opacity_slider.setValue(int(ctx.settings.get("overlay_opacity")))
        self.opacity_slider.setMaximumWidth(220)
        self.opacity_value = QLabel(f"{self.opacity_slider.value()}%")
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(make_label("Прозрачность", "h3"))
        opacity_row.addStretch(1)
        opacity_row.addWidget(self.opacity_slider)
        opacity_row.addWidget(self.opacity_value)
        card.body.addLayout(opacity_row)
        self.opacity_slider.valueChanged.connect(self._set_opacity)

        self.preview = QPushButton("Показать пример")
        self.preview.clicked.connect(self._show_preview)
        card.body.addWidget(self.preview)
        self._page.body.addWidget(card)

        note = Card(spacing=6)
        note.body.addWidget(make_label("Что отображается", "h2"))
        note.body.addWidget(make_label(
            "CPU и температура (если датчик доступен), GPU и температура, использование RAM, "
            "скорость загрузки и отдачи сети. Перетащи капсулу мышью в удобное место.",
            "muted", wrap=True))
        note.body.addWidget(make_label(
            "Важно: поверх полноэкранных игр в эксклюзивном режиме Windows может не показывать сторонние окна. "
            "Для оверлея используй режим «Оконный без рамки» или «Оконный».",
            "caption", wrap=True))
        self._page.body.addWidget(note)
        self._page.finish()

        if self._enabled:
            self._activate()

    def _set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self._ctx.settings.set("overlay_enabled", self._enabled)
        if enabled:
            self._activate()
        else:
            self._deactivate()

    def _activate(self) -> None:
        self._sampler.acquire("game_overlay", 1000)
        if self._sampler.last is not None:
            self._overlay.update_snapshot(self._sampler.last)
        self._overlay.show()

    def _deactivate(self) -> None:
        self._sampler.release("game_overlay")
        self._overlay.hide()

    def _set_opacity(self, value: int) -> None:
        self.opacity_value.setText(f"{value}%")
        self._overlay.set_opacity(value)
        self._ctx.settings.set("overlay_opacity", value)

    def _show_preview(self) -> None:
        if not self._enabled:
            self.toggle.setChecked(True)
        self._overlay.show()
        self._overlay.raise_()

    def _on_snapshot(self, snap: Snapshot) -> None:
        if self._enabled and self._overlay.isVisible():
            self._overlay.update_snapshot(snap)

    def shutdown(self) -> None:
        self._deactivate()
        try:
            self._sampler.updated.disconnect(self._on_snapshot)
        except (RuntimeError, TypeError):
            pass
        self._overlay.close()
