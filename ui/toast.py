"""Всплывающее сообщение внизу главного окна (результат запуска, поиска, ошибки)."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from ui import theme
from ui.theme import css


class Toast(QFrame):
    MAX_WIDTH = 640

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(18, 12, 18, 12)
        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        lay.addWidget(self._label)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self.hide()

    def show_message(self, level: str, text: str, duration_ms: int = 6500) -> None:
        pal = theme.current()
        color = {"error": pal.danger, "warning": pal.warn}.get(level, pal.accent)
        self.setStyleSheet(f"QFrame#toast {{ background: {css(pal.card)}; border: 1px solid {css(color)}; "
                           f"border-radius: 12px; }}")
        self._label.setText(text)
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start(duration_ms)

    def reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        width = min(self.MAX_WIDTH, max(parent.width() - 48, 240))
        self.setFixedWidth(width)
        self._label.setFixedWidth(width - 36)
        self.setFixedHeight(max(self._label.heightForWidth(width - 36), self._label.sizeHint().height()) + 24)
        self.move((parent.width() - self.width()) // 2, parent.height() - self.height() - 24)

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        self.hide()          # клик закрывает сообщение
        super().mousePressEvent(event)
