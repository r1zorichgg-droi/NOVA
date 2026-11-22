"""Анимации островка: пружинное изменение размера капсулы и плавное появление содержимого."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, QVariantAnimation, Signal

BASE_DURATION_MS = 420


class SizeTween(QObject):
    """Плавно меняет (ширина, высота). При расширении — лёгкий «пружинный» перелёт, как у Dynamic Island."""
    valueChanged = Signal(float, float)
    finished = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._from = (0.0, 0.0)
        self._to = (0.0, 0.0)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_value)
        self._anim.finished.connect(self._on_finished)

    @property
    def running(self) -> bool:
        return self._anim.state() == QVariantAnimation.State.Running

    def start(self, from_size: tuple[float, float], to_size: tuple[float, float],
              duration_ms: int, springy: bool) -> None:
        self._anim.stop()
        self._from, self._to = from_size, to_size
        curve = QEasingCurve(QEasingCurve.Type.OutBack if springy else QEasingCurve.Type.OutCubic)
        if springy:
            curve.setOvershoot(0.9)
        self._anim.setEasingCurve(curve)
        self._anim.setDuration(max(duration_ms, 1))
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()

    def _on_value(self, t: object) -> None:
        k = float(t)  # type: ignore[arg-type]
        self.valueChanged.emit(self._from[0] + (self._to[0] - self._from[0]) * k,
                               self._from[1] + (self._to[1] - self._from[1]) * k)

    def _on_finished(self) -> None:
        self.valueChanged.emit(*self._to)   # точно в цель, без накопленной погрешности
        self.finished.emit()


class Fade(QObject):
    """Прозрачность содержимого 0 -> 1 с медленным стартом (контент «проявляется» после роста капсулы)."""
    valueChanged = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.InQuad)
        self._anim.valueChanged.connect(lambda v: self.valueChanged.emit(float(v)))

    def start(self, duration_ms: int) -> None:
        self._anim.stop()
        self._anim.setDuration(max(duration_ms, 1))
        self._anim.start()

    def stop(self) -> None:
        self._anim.stop()
