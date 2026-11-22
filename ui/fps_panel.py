"""Панель FPS в разделе игр и лёгкий плавающий оверлей NOVA."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from config import data_dir
from system.fps_history import FpsHistoryStore
from launcher.game_profiles import find_game_profile

from system.fps_monitor import FpsMonitor
from ui.widgets import Card, make_label


class FpsOverlay(QWidget):
    def __init__(self) -> None:
        flags = (Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                 Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        super().__init__(None, flags)
        self.setObjectName("novaFpsOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFixedWidth(252)
        self._movable = False
        self._drag_offset: QPoint | None = None
        self._position_initialized = False
        self._position_settings = QSettings("NOVA", "NOVA")
        self._frame = QFrame(self)
        self._frame.setObjectName("fpsHudFrame")
        self._frame.setStyleSheet(
            "QFrame#fpsHudFrame{background:rgba(12,13,21,226);border:1px solid rgba(158,136,255,150);"
            "border-radius:14px;} QLabel{color:#f5f4ff;}"
        )
        lay = QVBoxLayout(self._frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)
        top = QHBoxLayout()
        brand = QLabel("NOVA  ·  FPS")
        brand.setStyleSheet("color:#b7a9ff;font-size:10px;font-weight:800;letter-spacing:1px;")
        top.addWidget(brand)
        top.addStretch(1)
        self.game_label = QLabel("Ожидание игры")
        self.game_label.setStyleSheet("color:#9da0b3;font-size:10px;")
        top.addWidget(self.game_label)
        lay.addLayout(top)
        self.fps_label = QLabel("—")
        self.fps_label.setStyleSheet("font-size:34px; font-weight:900; color:#ffffff;")
        lay.addWidget(self.fps_label)
        self.sub_label = QLabel("AVG —   1% —   0.1% —")
        self.sub_label.setStyleSheet("color:#c4c1d5;font-size:10px;font-weight:600;")
        lay.addWidget(self.sub_label)
        self.frame_label = QLabel("Frametime — ms")
        self.frame_label.setStyleSheet("color:#a9abc0;font-size:10px;")
        lay.addWidget(self.frame_label)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._frame)
        # Пока оверлей заблокирован, он не перехватывает клики в игре.
        # В разблокированном режиме дочерние подписи пропускают события родительскому окну.
        self._frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        for child in self._frame.findChildren(QWidget):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_movable(False)
        self.hide()

    @property
    def movable(self) -> bool:
        return self._movable

    def set_movable(self, enabled: bool) -> None:
        """Включить перетаскивание или зафиксировать оверлей с пропуском кликов в игру.

        Для заблокированного top-level окна используется WindowTransparentForInput:
        WA_TransparentForMouseEvents сама по себе не гарантирует передачу кликов
        другому приложению под оверлеем на Windows.
        """
        enabled = bool(enabled)
        self._movable = enabled
        self._drag_offset = None

        was_visible = self.isVisible()
        # В заблокированном режиме Windows отправляет клики окну игры под оверлеем.
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, not enabled)
        # В режиме перемещения события идут в родительское окно-оверлей; дочерние
        # подписи и рамка остаются прозрачными для мыши, чтобы drag работал везде.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not enabled)
        self.setCursor(Qt.CursorShape.SizeAllCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setToolTip(
            "Зажми левую кнопку мыши и перетащи оверлей" if enabled
            else "Положение зафиксировано · клики проходят в игру"
        )
        if was_visible:
            self.show()
            self.raise_()

        # Фиксируем координаты сразу при блокировке, даже если пользователь
        # нажал кнопку без дополнительного движения мышью.
        if not enabled:
            self._position_settings.setValue("fps_overlay/position", self.pos())

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt event handler
        if self._movable and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt event handler
        if self._movable and self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt event handler
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self._position_settings.setValue("fps_overlay/position", self.pos())
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def show_overlay(self) -> None:
        if not self._position_initialized:
            saved_position = self._position_settings.value("fps_overlay/position")
            if isinstance(saved_position, QPoint):
                self.move(saved_position)
            else:
                screen = QGuiApplication.primaryScreen()
                if screen:
                    geo = screen.availableGeometry()
                    self.move(geo.x() + 22, geo.y() + 60)
            self._position_initialized = True
        self.show()
        self.raise_()

    def update_stats(self, data: dict) -> None:
        fps = int(data.get("fps", 0))
        avg = int(data.get("avg", 0))
        low = int(data.get("low_1", data.get("low", 0)))
        low_01 = int(data.get("low_01", 0))
        frame_ms = float(data.get("frame_ms", 0.0) or 0.0)
        game = str(data.get("game", ""))
        self.fps_label.setText(str(fps) if fps > 0 else "—")
        self.sub_label.setText(f"AVG {avg or '—'}   1% {low or '—'}   0.1% {low_01 or '—'}")
        self.frame_label.setText(f"Frametime {frame_ms:.1f} ms" if frame_ms > 0 else "Frametime — ms")
        if game:
            self.game_label.setText(game.replace(".exe", "")[:22])
        else:
            self.game_label.setText("Ожидание игры")


class FpsPanel(QWidget):
    """Control panel for the own FPS overlay; the game stays in focus while this widget is passive."""
    def __init__(self, parent: QWidget | None = None, ctx=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self._ctx = ctx
        self.monitor = FpsMonitor(self)
        self.overlay = FpsOverlay()
        self._active = False
        self.history = FpsHistoryStore(data_dir() / "fps_history.json")
        self._last_history_game = ""

        card = Card(spacing=10)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(card)
        header = QHBoxLayout()
        header.addWidget(make_label("Счётчик FPS NOVA", "h2"))
        header.addStretch(1)
        self.status = make_label("Выключен", "caption")
        header.addWidget(self.status)
        card.body.addLayout(header)

        description = make_label(
            "Встроенный FPS-счётчик NOVA: текущий FPS, средний FPS и 1% Low активной игры. "
            "Нажми «Включить счётчик», чтобы NOVA нашла игру и показала оверлей. "
            "Оверлей можно переместить кнопкой разблокировки; в заблокированном режиме он пропускает клики в игру. "
            "PresentMon загружается один раз и проверяется по SHA-256; Xbox Game Bar не используется.",
            "muted", wrap=True,
        )
        card.body.addWidget(description)

        metrics = QGridLayout()
        metrics.setSpacing(10)
        self.fps_value, self.fps_value_label = self._metric("FPS", "—", "#a58cff")
        self.avg_value, self.avg_value_label = self._metric("СРЕДНИЙ", "—", "#63c6a6")
        self.low_value, self.low_value_label = self._metric("1% LOW", "—", "#e8b66c")
        self.low01_value, self.low01_value_label = self._metric("0.1% LOW", "—", "#e78aa4")
        self.frame_value, self.frame_value_label = self._metric("FRAME TIME", "—", "#63b8e8")
        for widget, row, col in (
            (self.fps_value, 0, 0), (self.avg_value, 0, 1), (self.low_value, 0, 2),
            (self.low01_value, 1, 0), (self.frame_value, 1, 1),
        ):
            metrics.addWidget(widget, row, col)
        card.body.addLayout(metrics)

        controls = QHBoxLayout()
        self.toggle_button = QPushButton("Включить счётчик")
        self.toggle_button.setObjectName("primary")
        self.toggle_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle_button.clicked.connect(self._toggle)
        self.overlay_button = QPushButton("Показать оверлей")
        self.overlay_button.setObjectName("flat")
        self.overlay_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.overlay_button.clicked.connect(self._toggle_overlay)
        controls.addWidget(self.toggle_button)
        controls.addWidget(self.overlay_button)
        controls.addStretch(1)
        card.body.addLayout(controls)

        move_controls = QHBoxLayout()
        self.move_lock_button = QPushButton("Разблокировать перемещение")
        self.move_lock_button.setObjectName("flat")
        self.move_lock_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.move_lock_button.setToolTip("Разрешить перетаскивать FPS-оверлей мышью")
        self.move_lock_button.clicked.connect(self._toggle_overlay_movable)
        self.move_state_hint = make_label("Оверлей заблокирован · клики проходят сквозь него", "caption")
        move_controls.addWidget(self.move_lock_button)
        move_controls.addWidget(self.move_state_hint, 1)
        card.body.addLayout(move_controls)

        foot = make_label(
            "Для сбора кадров Windows может потребовать права администратора или членство в группе "
            "Performance Log Users. В оконном/безрамочном режиме оверлей обычно виден лучше; "
            "некоторые игры в эксклюзивном полноэкранном режиме скрывают сторонние оверлеи.",
            "caption", wrap=True,
        )
        card.body.addWidget(foot)

        history_card = Card(spacing=8)
        history_head = QHBoxLayout()
        history_head.addWidget(make_label("FPS History · последние игровые сессии", "h2"))
        history_head.addStretch(1)
        self.clear_history_button = QPushButton("Очистить историю")
        self.clear_history_button.setObjectName("flat")
        self.clear_history_button.clicked.connect(self._clear_history)
        history_head.addWidget(self.clear_history_button)
        history_card.body.addLayout(history_head)
        history_card.body.addWidget(make_label(
            "Сессии записываются локально в AppData\\NOVA. Здесь сохраняются средний и пиковый FPS, 1% Low и длительность.",
            "muted", wrap=True))
        self.history_rows = QVBoxLayout()
        self.history_rows.setSpacing(5)
        self._history_labels: list[QLabel] = []
        history_card.body.addLayout(self.history_rows)
        card.body.addWidget(history_card)
        self._refresh_history()

        self.monitor.status_changed.connect(self._set_status)
        self.monitor.stats_changed.connect(self._update_stats)
        self.monitor.progress_changed.connect(self._update_progress)

    @staticmethod
    def _metric(caption: str, value: str, accent: str) -> tuple[QFrame, QLabel]:
        frame = QFrame()
        frame.setObjectName("cardAlt")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(12, 9, 12, 9)
        lay.setSpacing(2)
        label = QLabel(caption)
        label.setStyleSheet(f"color:{accent};font-size:10px;font-weight:800;letter-spacing:1px;")
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size:24px;font-weight:800;")
        lay.addWidget(label)
        lay.addWidget(value_label)
        return frame, value_label

    def _toggle(self) -> None:
        if self._active:
            self.monitor.stop()
            self.history.finish()
            self._refresh_history()
            self.overlay.hide()
            self._active = False
            self.toggle_button.setText("Включить счётчик")
            self.overlay_button.setEnabled(False)
            self.overlay_button.setText("Показать оверлей")
        else:
            self._active = True
            self.toggle_button.setText("Выключить счётчик")
            self.overlay_button.setEnabled(True)
            self.monitor.start()

    def _toggle_overlay(self) -> None:
        if self.overlay.isVisible():
            self.overlay.hide()
            self.overlay_button.setText("Показать оверлей")
        else:
            self.overlay.show_overlay()
            self.overlay_button.setText("Скрыть оверлей")

    def _toggle_overlay_movable(self) -> None:
        enabled = not self.overlay.movable
        self.overlay.set_movable(enabled)
        if enabled:
            self.move_lock_button.setText("Заблокировать перемещение")
            self.move_lock_button.setToolTip("Зафиксировать положение FPS-оверлея и пропускать клики в игру")
            self.move_state_hint.setText("Перемещение разрешено · зажми ЛКМ на оверлее и перетащи")
        else:
            self.move_lock_button.setText("Разблокировать перемещение")
            self.move_lock_button.setToolTip("Разрешить перетаскивать FPS-оверлей мышью")
            self.move_state_hint.setText("Оверлей заблокирован · клики проходят сквозь него")

    def _set_status(self, message: str) -> None:
        self.status.setText(message)
        if self._active and "включён" in message.lower():
            self.overlay.show_overlay()
            self.overlay_button.setText("Скрыть оверлей")

    def _update_stats(self, data: dict) -> None:
        fps = int(data.get("fps", 0) or 0)
        avg = int(data.get("avg", 0) or 0)
        low = int(data.get("low_1", data.get("low", 0)) or 0)
        low01 = int(data.get("low_01", 0) or 0)
        frame_ms = float(data.get("frame_ms", 0.0) or 0.0)
        self.fps_value_label.setText(str(fps) if fps > 0 else "—")
        self.avg_value_label.setText(str(avg) if avg > 0 else "—")
        self.low_value_label.setText(str(low) if low > 0 else "—")
        self.low01_value_label.setText(str(low01) if low01 > 0 else "—")
        self.frame_value_label.setText(f"{frame_ms:.1f} ms" if frame_ms > 0 else "—")
        game = str(data.get("game", ""))
        if fps > 0 and game:
            self.history.record(game, data)
        self._apply_profile_overlay(game)
        self.overlay.update_stats(data)

    def _apply_profile_overlay(self, game: str) -> None:
        if not game or self._ctx is None:
            return
        profile = find_game_profile(self._ctx.settings.get("game_profiles"), game)
        if profile is not None and not bool(profile.get("fps_overlay", True)):
            self.overlay.hide()
            self.overlay_button.setText("Показать оверлей")
        elif self._active and not self.overlay.isVisible():
            self.overlay.show_overlay()
            self.overlay_button.setText("Скрыть оверлей")

    def _refresh_history(self) -> None:
        for old in self._history_labels:
            old.setParent(None)
            old.deleteLater()
        self._history_labels.clear()
        sessions = self.history.sessions(8)
        if not sessions:
            label = make_label("Пока нет сохранённых игровых сессий. Включи счётчик и запусти игру.", "muted")
            self._history_labels.append(label)
            self.history_rows.addWidget(label)
            return
        for session in sessions:
            start = float(session.get("started_at", 0) or 0)
            date = __import__("datetime").datetime.fromtimestamp(start).strftime("%d.%m.%Y %H:%M") if start else "—"
            game = str(session.get("game", "Игра"))
            avg = int(session.get("avg_fps", 0) or 0)
            peak = int(session.get("peak_fps", 0) or 0)
            low = int(session.get("low_1_min", 0) or 0)
            duration = int(session.get("duration_s", max(0, __import__("time").time() - start)) or 0)
            minutes, seconds = divmod(duration, 60)
            label = make_label(f"{game}   ·   {date}   ·   AVG {avg or '—'}   ·   PEAK {peak or '—'}   ·   1% LOW {low or '—'}   ·   {minutes} мин {seconds:02d} сек", "caption", wrap=True)
            self._history_labels.append(label)
            self.history_rows.addWidget(label)

    def _clear_history(self) -> None:
        answer = QMessageBox.question(self, "Очистить FPS History", "Удалить сохранённую историю FPS-сессий?")
        if answer == QMessageBox.StandardButton.Yes:
            self.history.clear()
            self._refresh_history()

    def _update_progress(self, value: int) -> None:
        self.status.setText(f"Загрузка FPS-компонента: {value}%")

    def shutdown(self) -> None:
        self.monitor.stop()
        self.history.finish()
        self.overlay.close()
