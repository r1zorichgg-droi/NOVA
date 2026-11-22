"""Окна «Выпиления»: подтверждение удаления и ход удаления с итогами."""
from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

from system import uninstall as un
from system.debloat_rules import RISK_LABELS, RISK_SAFE
from system.fmt import fmt_bytes, plural_ru
from system.programs import Program
from ui.item_dialogs import make_checkbox
from ui.widgets import make_label

MARKS = {un.STATUS_REMOVED: "✓", un.STATUS_FAILED: "✗", un.STATUS_DECLINED: "✗", un.STATUS_SKIPPED: "–", un.STATUS_CANCELLED: "–"}


class UninstallConfirmDialog(QDialog):
    """Список того, что будет удалено, параметры и подтверждение. После exec() читаются restore_point и silent."""

    def __init__(self, programs: Sequence[Program], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._programs = list(programs)
        self.restore_point = True
        self.silent = True
        self.setWindowTitle("Удалить выбранное?")
        self.setMinimumSize(600, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        count = len(self._programs)
        lay.addWidget(make_label(f"Удалить {count} {plural_ru(count, 'программу', 'программы', 'программ')}?", "h2"))

        counts = un.risk_counts(self._programs)
        pieces = [f"{RISK_LABELS[risk].lower()}: {counts[risk]}" for risk in (RISK_SAFE, "careful", "system") if counts.get(risk)]
        lay.addWidget(make_label("В выборе — " + ", ".join(pieces) + ".", "muted", wrap=True))

        self.list = QListWidget()
        for program in self._programs:
            tail = "" if program.risk == RISK_SAFE else f"   [{RISK_LABELS[program.risk]}]"
            size = f"   ·   {fmt_bytes(program.size_bytes)}" if program.size_bytes else ""
            entry = QListWidgetItem(f"{program.name}{size}{tail}")
            entry.setToolTip(program.reason or program.publisher or program.name)
            self.list.addItem(entry)
        lay.addWidget(self.list, 1)

        self._ack = None
        if un.needs_risk_ack(self._programs):
            lay.addWidget(make_label(
                "В выборе есть компоненты, которые могут быть нужны другим программам, драйверам или самой Windows. "
                "После удаления что-то может перестать работать; вернуть удалённое можно только переустановкой.",
                "warn", wrap=True))
        lay.addWidget(make_label(
            "NOVA запустит для каждой программы штатное удаление Windows и проверит по системе, что она действительно "
            "исчезла. Некоторые удалители покажут свои окна или запросят права администратора. Ваши документы NOVA не "
            "трогает, но программа при удалении может убрать собственные данные.", "caption", wrap=True))

        self.restore_box = make_checkbox("Создать точку восстановления Windows перед удалением (нужны права администратора)", True)
        self.visible_box = make_checkbox("Показывать окна программ удаления (иначе — тихо там, где программа это умеет)", False)
        lay.addWidget(self.restore_box)
        lay.addWidget(self.visible_box)
        if un.needs_risk_ack(self._programs):
            self._ack = make_checkbox("Я понимаю риск: удаление этих компонентов может нарушить работу программ или Windows")
            self._ack.toggled.connect(self._refresh)
            lay.addWidget(self._ack)
        self._typed: QLineEdit | None = None
        if un.needs_typed_confirm(self._programs):
            lay.addWidget(make_label(f"Чтобы подтвердить, введите слово «{un.CONFIRM_WORD}»:", "muted"))
            self._typed = QLineEdit()
            self._typed.setPlaceholderText(un.CONFIRM_WORD)
            self._typed.textChanged.connect(self._refresh)
            lay.addWidget(self._typed)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        self.ok_btn = QPushButton("Удалить")
        self.ok_btn.setObjectName("destructive")
        self.ok_btn.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(self.ok_btn)
        lay.addLayout(row)
        self._refresh()

    def _refresh(self, *_args: object) -> None:
        ack = bool(self._ack.isChecked()) if self._ack is not None else False
        typed = self._typed.text() if self._typed is not None else ""
        self.ok_btn.setEnabled(un.confirm_ready(self._programs, ack, typed))

    def _accept(self) -> None:
        self.restore_point = bool(self.restore_box.isChecked())
        self.silent = not bool(self.visible_box.isChecked())
        self.accept()


class UninstallProgressDialog(QDialog):
    """Ход удаления: что удаляется сейчас, журнал и итог. Закрыть окно можно только после окончания."""

    stop_requested = Signal()

    def __init__(self, total: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._total = max(total, 1)
        self._running = True
        self._stop_sent = False
        self.setWindowTitle("Удаление программ")
        self.setMinimumSize(640, 480)
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        self.title = make_label("Идёт удаление…", "h2")
        self.current = make_label("Подготовка…", "muted", wrap=True)
        self.bar = QProgressBar()
        self.bar.setRange(0, self._total)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.summary = make_label("", "muted", wrap=True)
        for widget in (self.title, self.current, self.bar):
            lay.addWidget(widget)
        lay.addWidget(self.log, 1)
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        row.addStretch(1)
        self.stop_btn = QPushButton("Остановить после текущей")
        self.stop_btn.clicked.connect(self._stop)
        self.close_btn = QPushButton("Закрыть")
        self.close_btn.setObjectName("primary")
        self.close_btn.clicked.connect(self.accept)
        self.close_btn.setVisible(False)
        row.addWidget(self.stop_btn)
        row.addWidget(self.close_btn)
        lay.addLayout(row)

    # -- ход работы ----------------------------------------------------------------
    def set_stage(self, text: str) -> None:
        self.current.setText(text)

    def add_line(self, text: str) -> None:
        self.log.appendPlainText(text)

    def on_start(self, index: int, total: int, name: str) -> None:
        self.bar.setValue(index)
        self.current.setText(f"Удаляю: {name}  ({index + 1} из {total}). Программа может показать своё окно или запросить права "
                             "администратора — это нормально.")

    def on_result(self, result: un.UninstallResult) -> None:
        self.add_line(f"{MARKS.get(result.status, '•')} {result.program.name} — {result.message}")

    def request_stop(self) -> None:
        self._stop()

    def _stop(self) -> None:
        if self._stop_sent or not self._running:
            return
        self._stop_sent = True
        self.stop_btn.setEnabled(False)
        self.current.setText("Останавливаюсь: дождусь окончания текущего удаления, остальное пропущу.")
        self.stop_requested.emit()

    def finish(self, results: Sequence[un.UninstallResult]) -> None:
        self.bar.setValue(self._total)
        self.finish_text("Готово", un.summarize(results))

    def finish_text(self, title: str, text: str) -> None:
        self._running = False
        self.title.setText(title)
        self.current.setText("")
        self.summary.setText(text)
        self.stop_btn.setVisible(False)
        self.close_btn.setVisible(True)

    # -- закрытие ---------------------------------------------------------------------
    def reject(self) -> None:
        if self._running:
            self._stop()                # Esc во время работы = «остановить», а не «бросить посреди удаления»
            return
        super().reject()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._running:
            self._stop()
            event.ignore()
            return
        super().closeEvent(event)
