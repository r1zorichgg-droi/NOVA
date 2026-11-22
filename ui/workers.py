"""Выполнение тяжёлых функций в фоновом потоке с возвратом результата в поток интерфейса."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)

Callback = Callable[[Any, "Exception | None"], None]


class _Done(QObject):
    finished = Signal(object, object, object)   # (этот объект, результат, ошибка)

    def __init__(self, callback: Callback, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.callback = callback


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any], done: _Done) -> None:
        super().__init__()
        self._fn, self._done = fn, done
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result, error = self._fn(), None
        except Exception as exc:  # noqa: BLE001 — любая ошибка должна дойти до интерфейса, а не убить поток
            log.exception("Сбой фоновой задачи")
            result, error = None, exc
        self._done.finished.emit(self._done, result, error)


class AsyncRunner(QObject):
    """submit(fn, callback): fn выполняется в пуле потоков, callback(result, error) — в потоке интерфейса."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._alive: set[_Done] = set()

    def submit(self, fn: Callable[[], Any], callback: Callback) -> None:
        done = _Done(callback, self)
        self._alive.add(done)
        done.finished.connect(self._on_done)   # метод QObject из потока интерфейса -> доставка через очередь событий
        QThreadPool.globalInstance().start(_Task(fn, done))

    def _on_done(self, done: _Done, result: Any, error: Any) -> None:
        self._alive.discard(done)
        callback = done.callback
        done.deleteLater()
        callback(result, error)


class _ProgressJob(QObject):
    progress = Signal(object, object)           # (эта задача, данные о ходе работы)
    finished = Signal(object, object, object)   # (эта задача, результат, ошибка)

    def __init__(self, on_progress: Callable[[Any], None], on_done: Callback, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.on_progress, self.on_done = on_progress, on_done


class _ProgressTask(QRunnable):
    def __init__(self, fn: Callable[[Callable[[Any], None]], Any], job: _ProgressJob) -> None:
        super().__init__()
        self._fn, self._job = fn, job
        self.setAutoDelete(True)

    def run(self) -> None:
        job = self._job
        try:
            result, error = self._fn(lambda payload: job.progress.emit(job, payload)), None
        except Exception as exc:  # noqa: BLE001 — ошибка должна дойти до интерфейса
            log.exception("Сбой фоновой задачи")
            result, error = None, exc
        job.finished.emit(job, result, error)


class ProgressRunner(QObject):
    """start(fn, on_progress, on_done): fn(report) работает в пуле потоков и может сообщать о ходе работы через report(данные).

    on_progress и on_done вызываются в потоке интерфейса.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._alive: set[_ProgressJob] = set()

    def start(self, fn: Callable[[Callable[[Any], None]], Any], on_progress: Callable[[Any], None], on_done: Callback) -> None:
        job = _ProgressJob(on_progress, on_done, self)
        self._alive.add(job)
        job.progress.connect(self._on_progress)
        job.finished.connect(self._on_finished)
        QThreadPool.globalInstance().start(_ProgressTask(fn, job))

    def _on_progress(self, job: _ProgressJob, payload: Any) -> None:
        job.on_progress(payload)

    def _on_finished(self, job: _ProgressJob, result: Any, error: Any) -> None:
        self._alive.discard(job)
        callback = job.on_done
        job.deleteLater()
        callback(result, error)
