"""Общие виджеты интерфейса: карточки, переключатель, сегментный выбор, графики, кольцевой индикатор."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QButtonGroup, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget)

from ui import theme
from ui.icons import icon_pixmap
from ui.theme import Color

NA = "N/A"


def qc(color: Color) -> QColor:
    return QColor(color[0], color[1], color[2], color[3])


def make_label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    label = QLabel(text)
    if name:
        label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


# --------------------------------------------------------------------------- карточки и страницы
class Card(QFrame):
    """Скруглённая карточка. Содержимое добавляется в `self.body` (QVBoxLayout)."""

    def __init__(self, margins: tuple[int, int, int, int] = (20, 18, 20, 18), spacing: int = 10,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)


class HLine(QFrame):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("line")
        self.setFixedHeight(1)


class Page(QWidget):
    """Страница с заголовком и прокручиваемым содержимым (`self.body` — вертикальный layout).

    needs_samples — нужны ли странице живые метрики (главное окно включает сэмплер только тогда).
    """
    needs_samples = False

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll = scroll
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(36, 30, 36, 32)
        self.body.setSpacing(16)
        self.title_label = make_label(title, "h1")
        self.subtitle_label = make_label(subtitle, "muted", wrap=True)
        self.body.addWidget(self.title_label)
        if subtitle:
            self.body.addWidget(self.subtitle_label)
        self.body.addSpacing(6)

    def finish(self) -> None:
        """Прижимает содержимое к верху (вызывается после добавления всех блоков)."""
        self.body.addStretch(1)

    def on_data(self, snap) -> None:  # noqa: ANN001 — переопределяется страницами с метриками
        """Новый снимок метрик (вызывается только пока страница видна)."""


class FadeStack(QStackedWidget):
    """QStackedWidget с коротким плавным появлением новой страницы."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._anim: QPropertyAnimation | None = None
        self._effect_target: QWidget | None = None
        self.duration_ms = 180

    def set_page(self, index: int) -> None:
        if index == self.currentIndex():
            return
        self._cleanup()
        self.setCurrentIndex(index)
        widget = self.currentWidget()
        if widget is None or self.duration_ms <= 0:
            return
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(self.duration_ms)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._cleanup)
        self._anim, self._effect_target = anim, widget
        anim.start()

    def _cleanup(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim.deleteLater()
            self._anim = None
        if self._effect_target is not None:
            self._effect_target.setGraphicsEffect(None)
            self._effect_target = None


class EmptyState(QWidget):
    def __init__(self, icon: str, title: str, text: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 26, 20, 26)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pic = QLabel()
        pic.setPixmap(icon_pixmap(icon, qc(theme.current().dim), 44))
        pic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        head = make_label(title, "h2")
        head.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body = make_label(text, "muted", wrap=True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        body.setMaximumWidth(520)
        self._text = body
        for w in (pic, head, body):
            lay.addWidget(w, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_text(self, text: str) -> None:
        self._text.setText(text)


class ElidedLabel(QLabel):
    """Однострочная подпись, которая сама обрезает текст многоточием под ширину (полный текст — во всплывающей подсказке)."""
    clicked = Signal()

    def __init__(self, text: str = "", name: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if name:
            self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = ""
        self.set_full_text(text)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._apply()

    def _apply(self) -> None:
        width = self.width()
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, width) if width > 12 else self._full
        if shown != self.text():
            self.setText(shown)

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self._apply()

    def mousePressEvent(self, event) -> None:  # noqa: N802, ANN001
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def setting_row(title: str, description: str, control: QWidget) -> QWidget:
    """Строка настройки: слева название и пояснение, справа элемент управления."""
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 6, 0, 6)
    lay.setSpacing(18)
    texts = QVBoxLayout()
    texts.setSpacing(2)
    texts.addWidget(make_label(title, "h3"))
    if description:
        texts.addWidget(make_label(description, "caption", wrap=True))
    lay.addLayout(texts, 1)
    lay.addWidget(control, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
    return row


# --------------------------------------------------------------------------- управление
class Toggle(QAbstractButton):
    """Переключатель в стиле iOS с плавным движением ползунка."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 26)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pos = 1.0 if checked else 0.0
        self.setChecked(checked)   # сигнал toggled ещё ни к чему не подключён — анимация не запустится
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)
        self.toggled.connect(self._animate)

    def sizeHint(self) -> QSize:
        return QSize(44, 26)

    def set_checked_silent(self, checked: bool) -> None:
        """Меняет состояние без сигналов (когда значение пришло из настроек, а не от пользователя)."""
        self._anim.stop()
        self.blockSignals(True)
        self.setChecked(checked)
        self.blockSignals(False)
        self._pos = 1.0 if checked else 0.0
        self.update()

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def _on_anim(self, value: object) -> None:
        self._pos = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        pal = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        off = (120, 120, 134, 90) if pal.dark else (150, 150, 164, 120)
        t = self._pos
        a = [off[i] + (pal.accent[i] - off[i]) * t for i in range(4)]
        color = QColor(int(a[0]), int(a[1]), int(a[2]), int(a[3] if t < 1 else 255))
        if not self.isEnabled():
            color.setAlpha(70)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(0, 0, 44, 26), 13, 13)
        p.setBrush(QColor(255, 255, 255))
        p.drawEllipse(QPointF(13 + 18 * t, 13), 10, 10)
        if self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qc(pal.accent), 1.5))
            p.drawRoundedRect(QRectF(-0.5, -0.5, 45, 27), 13.5, 13.5)


class Segmented(QFrame):
    """Сегментный выбор (замена выпадающего списка для 2–4 вариантов)."""
    changed = Signal(object)

    def __init__(self, options: Sequence[tuple[object, str]], value: object,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("segwrap")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[object, QPushButton] = {}
        self._value = value
        for val, text in options:
            btn = QPushButton(text)
            btn.setObjectName("seg")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _checked=False, v=val: self._select(v))
            self._group.addButton(btn)
            lay.addWidget(btn)
            self._buttons[val] = btn
        self.set_value(value)

    def value(self) -> object:
        return self._value

    def set_value(self, value: object) -> None:
        """Выбирает вариант без сигнала."""
        self._value = value
        btn = self._buttons.get(value)
        if btn is not None:
            btn.setChecked(True)

    def _select(self, value: object) -> None:
        self._value = value
        self.changed.emit(value)


def chip_button(text: str, checked: bool) -> QPushButton:
    btn = QPushButton(text)
    btn.setObjectName("chip")
    btn.setCheckable(True)
    btn.setChecked(checked)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


# --------------------------------------------------------------------------- графики
def safe_list(seq: Sequence[float]) -> list[float]:
    """Копия данных, которые параллельно дописывает поток сэмплера (deque может поменяться во время обхода)."""
    for _ in range(3):
        try:
            return list(seq)
        except RuntimeError:
            continue
    return []


class Chart(QWidget):
    """Линейный график реального времени: новые значения справа, плавная заливка под линией."""

    def __init__(self, max_points: int = 120, y_max: float | None = 100.0,
                 y_label: Callable[[float], str] | None = None, min_height: int = 120,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._max_points = max_points
        self._y_max = y_max
        self._y_label = y_label
        self._series: list[tuple[list[float], Color]] = []
        self.setMinimumHeight(min_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_series(self, series: Sequence[tuple[Sequence[float], Color]]) -> None:
        self._series = [(safe_list(values)[-self._max_points:], color) for values, color in series]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        pal = theme.current()
        w, h = float(self.width()), float(self.height())
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pad_t, pad_b = 6.0, 4.0
        plot_h = h - pad_t - pad_b
        grid = qc(pal.border)
        p.setPen(QPen(grid, 1))
        for i in range(4):
            y = pad_t + plot_h * i / 3
            p.drawLine(QPointF(0, y), QPointF(w, y))

        top = self._y_max
        if top is None:
            peak = max((max(v) for v, _ in self._series if v), default=0.0)
            top = max(peak * 1.2, 1.0)
        step = w / max(self._max_points - 1, 1)
        for values, color in self._series:
            n = len(values)
            if n < 2:
                continue
            pts = [QPointF(w - (n - 1 - i) * step, pad_t + plot_h * (1 - min(max(v / top, 0.0), 1.0)))
                   for i, v in enumerate(values)]
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            fill = QPainterPath(line)
            fill.lineTo(pts[-1].x(), pad_t + plot_h)
            fill.lineTo(pts[0].x(), pad_t + plot_h)
            fill.closeSubpath()
            grad = QLinearGradient(0, pad_t, 0, pad_t + plot_h)
            top_c, bottom_c = qc(color), qc(color)
            top_c.setAlpha(90 if len(self._series) == 1 else 55)
            bottom_c.setAlpha(0)
            grad.setColorAt(0, top_c)
            grad.setColorAt(1, bottom_c)
            p.fillPath(fill, QBrush(grad))
            p.setPen(QPen(qc(color), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(qc(color))
            p.drawEllipse(pts[-1], 3.2, 3.2)

        if self._y_label is not None and top is not None:
            font = QFont(self.font())
            font.setPixelSize(11)
            p.setFont(font)
            p.setPen(qc(pal.muted))
            p.drawText(QRectF(4, pad_t + 2, 160, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       self._y_label(top))


class RingGauge(QWidget):
    """Кольцевой индикатор 0..100 % с подписью в центре. Без данных показывает N/A."""

    def __init__(self, color: Color, size: int = 104, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = color
        self._value: float | None = None
        self._text = ""
        self.setFixedSize(size, size)

    def set_color(self, color: Color) -> None:
        self._color = color
        self.update()

    def set_value(self, percent: float | None, text: str | None = None) -> None:
        self._value = None if percent is None else min(max(percent, 0.0), 100.0)
        self._text = text if text is not None else (NA if percent is None else f"{percent:.0f}%")
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        pal = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        thick = 9.0
        rect = QRectF(thick / 2 + 1, thick / 2 + 1, self.width() - thick - 2, self.height() - thick - 2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc(pal.track), thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawEllipse(rect)
        if self._value is not None and self._value > 0.4:
            p.setPen(QPen(qc(self._color), thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(rect, 90 * 16, int(-self._value / 100.0 * 360 * 16))
        font = QFont(self.font())
        font.setPixelSize(int(self.height() * 0.22))
        font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(qc(pal.muted if self._value is None else pal.text))
        p.drawText(QRectF(0, 0, self.width(), self.height()), Qt.AlignmentFlag.AlignCenter, self._text)


class BarMeter(QWidget):
    """Тонкая полоса заполнения (использование диска и т.п.)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fraction = 0.0
        self._color: Color | None = None
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_fraction(self, fraction: float, color: Color | None = None) -> None:
        self._fraction, self._color = min(max(fraction, 0.0), 1.0), color
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802, ANN001
        pal = theme.current()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc(pal.track))
        p.drawRoundedRect(QRectF(0, 0, self.width(), 8), 4, 4)
        if self._fraction > 0:
            p.setBrush(qc(self._color or pal.accent))
            p.drawRoundedRect(QRectF(0, 0, max(self.width() * self._fraction, 8), 8), 4, 4)


class Stat(QWidget):
    """Подпись + значение (например, «Потоки» / «16»)."""

    def __init__(self, caption: str, value: str = NA, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(1)
        self._caption = make_label(caption, "caption")
        self._value = make_label(value, "value")
        lay.addWidget(self._caption)
        lay.addWidget(self._value)

    def set_value(self, text: str) -> None:
        self._value.setText(text)


def clear_layout(layout, delete: bool = True) -> None:  # noqa: ANN001
    """Убирает из раскладки все элементы. delete=True — вместе с виджетами и вложенными раскладками."""
    for i in reversed(range(layout.count())):
        child = layout.takeAt(i)
        if child is None or not delete:
            continue
        widget = child.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
            continue
        inner = child.layout()
        if inner is not None:
            clear_layout(inner)
            inner.deleteLater()
