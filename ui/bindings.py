"""Привязка элементов управления к настройкам: изменение элемента пишет в AppSettings, а изменение
настройки (в том числе сброс) обновляет элемент без обратной записи."""
from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSlider, QWidget

from settings import AppSettings
from ui.widgets import Segmented, Toggle


def bind_toggle(settings: AppSettings, key: str) -> Toggle:
    toggle = Toggle(bool(settings.get(key)))
    toggle.toggled.connect(lambda value: settings.set(key, bool(value)))

    def on_change(changed: str) -> None:
        if changed == key and toggle.isChecked() != bool(settings.get(key)):
            toggle.set_checked_silent(bool(settings.get(key)))

    settings.changed.connect(on_change)
    return toggle


def bind_segmented(settings: AppSettings, key: str, options: Sequence[tuple[object, str]]) -> Segmented:
    seg = Segmented(options, settings.get(key))
    seg.changed.connect(lambda value: settings.set(key, value))
    settings.changed.connect(lambda changed: seg.set_value(settings.get(key)) if changed == key else None)
    return seg


class SliderRow(QWidget):
    """Ползунок со значением справа. scale: во сколько раз целое значение ползунка больше значения настройки."""

    def __init__(self, settings: AppSettings, key: str, low: int, high: int, scale: float = 1.0,
                 fmt: Callable[[float], str] = lambda v: f"{v:g}", width: int = 190,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings, self._key, self._scale, self._fmt = settings, key, scale, fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(low, high)
        self.slider.setFixedWidth(width)
        self.slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.label = QLabel()
        self.label.setObjectName("muted")
        self.label.setFixedWidth(64)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self.slider)
        lay.addWidget(self.label)
        self._load()
        self.slider.valueChanged.connect(self._on_slider)
        settings.changed.connect(self._on_setting)

    def _load(self) -> None:
        value = float(self._settings.get(self._key))
        self.slider.blockSignals(True)
        self.slider.setValue(round(value * self._scale))
        self.slider.blockSignals(False)
        self.label.setText(self._fmt(value))

    def _on_slider(self, raw: int) -> None:
        value = raw / self._scale
        self.label.setText(self._fmt(value))
        self._settings.set(self._key, value if self._scale != 1.0 else int(raw))

    def _on_setting(self, changed: str) -> None:
        if changed == self._key:
            self._load()
