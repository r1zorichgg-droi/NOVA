"""Страница «Выпиление»: все программы Windows (классические и из Microsoft Store, в том числе от Microsoft) и их удаление.

NOVA запускает штатное удаление Windows и проверяет по системе, что программа исчезла. Защиту Windows (то, что система
сама не разрешает удалять) она не обходит. Без подтверждения ничего не удаляется; рискованное требует отдельной галочки и слова.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QBrush, QIcon
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QDialog, QHBoxLayout, QHeaderView, QLineEdit, QPushButton,
                               QTreeWidget, QTreeWidgetItem)

from app_context import AppContext
from system import programs as pr
from system import uninstall as un
from system import winapi
from system.debloat_rules import RISK_CAREFUL, RISK_LABELS, RISK_ORDER, RISK_SAFE
from system.fmt import fmt_bytes
from ui import theme
from ui.debloat_dialogs import UninstallConfirmDialog, UninstallProgressDialog
from ui.icon_cache import letter_tile
from ui.item_dialogs import confirm, make_checkbox
from ui.widgets import Card, Page, chip_button, make_label, qc
from ui.workers import AsyncRunner, ProgressRunner

log = logging.getLogger(__name__)

COLUMNS = ("Программа", "Издатель", "Размер", "Версия", "Тип", "Оценка")
ICON_PX = 20
ICONS_PER_TICK = 20
UAC_HINT_USER = ("NOVA запущена без прав администратора: для программ, установленных для всех пользователей, Windows будет "
                 "спрашивать подтверждение (UAC). Чтобы не отвечать каждый раз, запустите NOVA от имени администратора "
                 "(правый клик по NOVA → «Запуск от имени администратора»).")
UAC_HINT_ADMIN = "NOVA запущена от имени администратора: отдельных запросов UAC для каждой программы не будет."


class _Row(QTreeWidgetItem):
    """Строка списка; сортировка идёт по заранее посчитанным ключам (размер — числом, оценка — по степени риска)."""

    def __init__(self, program: pr.Program) -> None:
        super().__init__([program.name, program.publisher, fmt_bytes(program.size_bytes) if program.size_bytes else "—",
                          program.version, program.kind_label, risk_text(program)])
        self.program = program
        risk_key = (0 if program.removable else 3, RISK_ORDER.get(program.risk, 0))
        self.sort_keys: tuple[Any, ...] = (program.name.casefold(), program.publisher.casefold(), program.size_bytes,
                                           program.version, program.kind_label, risk_key)

    def __lt__(self, other: object) -> bool:
        tree = self.treeWidget()
        column = int(tree.sortColumn()) if tree is not None else 0
        mine = self.sort_keys[column]
        theirs = other.sort_keys[column] if isinstance(other, _Row) else mine
        try:
            return bool(mine < theirs)
        except TypeError:
            return str(mine) < str(theirs)


def risk_text(program: pr.Program) -> str:
    return RISK_LABELS[program.risk] if program.removable else "Защищено Windows"


def tooltip_for(program: pr.Program) -> str:
    lines = [program.name]
    meta = " · ".join(x for x in (program.publisher, program.version) if x)
    if meta:
        lines.append(meta)
    if len(program.install_date) == 8 and program.install_date.isdigit():
        lines.append(f"Установлено: {program.install_date[6:]}.{program.install_date[4:6]}.{program.install_date[:4]}")
    note = program.reason if program.removable else program.why_not
    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)


class DebloatPage(Page):
    def __init__(self, ctx: AppContext, runner: un.Runner | None = None,
                 reader: Callable[[], pr.ProgramList] | None = None, available: bool | None = None) -> None:
        super().__init__("Выпиление", "Все программы, установленные в Windows: классические и из Microsoft Store, включая "
                                      "встроенные от Microsoft. Отметьте ненужные и удалите — NOVA запускает штатное удаление "
                                      "Windows и проверяет, что программа действительно исчезла.")
        self._ctx = ctx
        self._win: un.Runner = runner or un.WindowsRunner()
        self._uninstaller = un.Uninstaller(self._win)
        self._read = reader or self._read_system
        self.available = winapi.IS_WINDOWS if available is None else available
        self._async = AsyncRunner(self)
        self._progress_runner = ProgressRunner(self)
        # состояние хранится в обычных Python-объектах, а не в виджетах — так его можно проверять без отрисовки
        self._rows: list[_Row] = []
        self._checked: set[str] = set()
        self._visible: set[str] = set()
        self._query = ""
        self._group = "all"
        self._show_hidden = False
        self._loading = False
        self._busy = False
        self._loaded = False
        self._generation = 0
        self._cancel = threading.Event()
        self._dialog: UninstallProgressDialog | None = None
        self._icon_queue: list[_Row] = []

        # ---- поиск и фильтры ----
        bar = Card(spacing=10)
        top = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск по названию или издателю")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._on_query)
        self.btn_refresh = QPushButton("Обновить список")
        self.btn_refresh.clicked.connect(self._reload)
        top.addWidget(self.search, 1)
        top.addWidget(self.btn_refresh)
        bar.body.addLayout(top)
        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._chip_group = QButtonGroup(self)
        self._chip_group.setExclusive(True)
        for key, title in pr.FILTERS:
            chip = chip_button(title, key == "all")
            chip.clicked.connect(lambda _checked=False, k=key: self._on_group(k))
            self._chip_group.addButton(chip)
            chips.addWidget(chip)
        chips.addStretch(1)
        bar.body.addLayout(chips)
        options = QHBoxLayout()
        self.hidden_box = make_checkbox("Показывать системные компоненты и то, что Windows не даёт удалить")
        self.hidden_box.toggled.connect(self._on_show_hidden)
        self.count_label = make_label("", "muted")
        options.addWidget(self.hidden_box)
        options.addStretch(1)
        options.addWidget(self.count_label)
        bar.body.addLayout(options)
        self.body.addWidget(bar)

        # ---- список ----
        self.tree = QTreeWidget()
        self.tree.setColumnCount(len(COLUMNS))
        self.tree.setHeaderLabels(list(COLUMNS))
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setIconSize(QSize(ICON_PX, ICON_PX))
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.tree.setMinimumHeight(440)
        self.tree.setSortingEnabled(True)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column, width in ((1, 170), (2, 90), (3, 110), (4, 120), (5, 130)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
            self.tree.setColumnWidth(column, width)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.body.addWidget(self.tree)

        # ---- действия ----
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.btn_safe = QPushButton("Отметить всё безопасное")
        self.btn_safe.clicked.connect(self._check_safe)
        self.btn_all = QPushButton("Отметить всё показанное")
        self.btn_all.clicked.connect(self._check_all)
        self.btn_none = QPushButton("Снять отметки")
        self.btn_none.clicked.connect(self._clear_checks)
        self.selected_label = make_label("", "muted")
        self.btn_remove = QPushButton("Удалить выбранное…")
        self.btn_remove.setObjectName("destructive")
        self.btn_remove.clicked.connect(self._start_uninstall)
        for widget in (self.btn_safe, self.btn_all, self.btn_none):
            actions.addWidget(widget)
        actions.addStretch(1)
        actions.addWidget(self.selected_label)
        actions.addWidget(self.btn_remove)
        self.body.addLayout(actions)
        self.status = make_label("", "muted", wrap=True)
        self.body.addWidget(self.status)
        self.uac_label = make_label("", "caption", wrap=True)
        self.body.addWidget(self.uac_label)

        # ---- честно о пределах ----
        honest = Card(spacing=6)
        honest.body.addWidget(make_label("Что важно знать", "h2"))
        for line in (
                "Оценка «Можно удалять / Осторожно / Системное» — подсказка NOVA, а не запрет. «Осторожно» и «Системное» "
                "удаляются только после отдельного подтверждения; вернуть удалённое можно лишь переустановкой.",
                "Что Windows сама защищает от удаления (серые строки при включённой галочке: часть компонентов Windows, "
                "Microsoft Edge, «Безопасность Windows»), NOVA не обходит: принудительное удаление такого ломает систему.",
                "Приложения Microsoft Store удаляются для вашей учётной записи. Windows может вернуть встроенные приложения "
                "после крупного обновления или создать их заново для нового пользователя.",
                "Точка восстановления откатывает классические программы и настройки реестра, но не приложения Store и не "
                "ваши файлы. Windows создаёт такие точки не чаще раза в сутки.",
                "NOVA ничего не скачивает и не отправляет в интернет: список строится из данных вашей Windows."):
            honest.body.addWidget(make_label("•  " + line, "muted", wrap=True))
        self.body.addWidget(honest)
        self.finish()

        self._update_selection()
        if not self.available:
            self._set_status("«Выпиление» работает только в Windows: здесь нечего показывать.")
            self._set_controls_enabled(False)

    # ------------------------------------------------------------------ загрузка
    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        if not self._loaded and self.available and not self._busy:
            QTimer.singleShot(60, self._reload)          # сначала показываем страницу, потом читаем систему
        self.uac_label.setText(UAC_HINT_ADMIN if winapi.is_admin() else UAC_HINT_USER if self.available else "")

    def _read_system(self) -> pr.ProgramList:
        def powershell(script: str) -> str:
            run = self._win.run_powershell(script, False, 120.0)
            return run.output if run.started and run.exit_code == 0 else ""
        return pr.list_programs(pr.read_registry_entries, powershell)

    def _reload(self) -> None:
        if self._busy or not self.available:
            return
        self._busy = True
        self._set_controls_enabled(False)
        self._set_status("Читаю список установленных программ… Приложения Microsoft Store подгружаются несколько секунд.")
        self._async.submit(self._read, self._on_loaded)

    def _on_loaded(self, result: pr.ProgramList | None, error: Exception | None) -> None:
        self._busy = False
        self._set_controls_enabled(self.available)
        if error is not None or result is None:
            self._set_status("Не удалось прочитать список программ.")
            return
        self._loaded = True
        self._populate(result.programs)
        notes = " ".join(result.notes)
        self._set_status(notes or "Список построен по данным Windows. Ничего не удаляется без вашего подтверждения.")

    def _populate(self, programs: list[pr.Program]) -> None:
        self._generation += 1
        self._loading = True
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        keep = {p.key for p in programs} & self._checked          # отметки переживают «Обновить»
        self._checked = set()
        pal = theme.current()
        dim = QBrush(qc(pal.dim))
        risk_colors = {RISK_SAFE: QBrush(qc(pal.ok)), RISK_CAREFUL: QBrush(qc(pal.warn))}
        danger = QBrush(qc(pal.danger))
        self._rows = []
        for program in sorted(programs, key=lambda p: p.name.casefold()):
            row = _Row(program)
            tip = tooltip_for(program)
            row.setToolTip(0, tip)
            row.setToolTip(5, tip)
            row.setTextAlignment(2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if program.removable:
                row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                row.setCheckState(0, Qt.CheckState.Checked if program.key in keep else Qt.CheckState.Unchecked)
                row.setForeground(5, risk_colors.get(program.risk, danger))
                if program.key in keep:
                    self._checked.add(program.key)
            else:
                for column in range(len(COLUMNS)):
                    row.setForeground(column, dim)
            self.tree.addTopLevelItem(row)
            self._rows.append(row)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self._loading = False
        self._apply_filter()
        self._icon_queue = list(self._rows)
        QTimer.singleShot(20, lambda g=self._generation: self._load_icons(g))

    def _load_icons(self, generation: int) -> None:
        """Значки подгружаются порциями, чтобы список появился сразу, а окно не «замирало»."""
        if generation != self._generation:
            return
        icons = self._ctx.icons
        for _ in range(min(ICONS_PER_TICK, len(self._icon_queue))):
            row = self._icon_queue.pop()
            program = row.program
            pix = icons.exe_icon(program.icon_path, ICON_PX) if program.icon_path else None
            row.setIcon(0, QIcon(pix if pix is not None else letter_tile(program.name, ICON_PX)))
        if self._icon_queue:
            QTimer.singleShot(15, lambda: self._load_icons(generation))

    # ------------------------------------------------------------------ фильтры и отметки
    def _on_query(self, text: str) -> None:
        self._query = text
        self._apply_filter()

    def _on_group(self, key: str) -> None:
        self._group = key
        self._apply_filter()

    def _on_show_hidden(self, checked: bool) -> None:
        self._show_hidden = bool(checked)
        self._apply_filter()

    def _apply_filter(self) -> None:
        self._visible = {row.program.key for row in self._rows
                         if pr.program_matches(row.program, self._group, self._query, self._show_hidden)}
        for row in self._rows:
            row.setHidden(row.program.key not in self._visible)
        self.count_label.setText(f"Показано: {len(self._visible)} из {len(self._rows)}" if self._rows else "")
        self._update_selection()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._loading or column != 0 or not isinstance(item, _Row):
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self._checked.add(item.program.key)
        else:
            self._checked.discard(item.program.key)
        self._update_selection()

    def _set_checks(self, keys: set[str]) -> None:
        self._loading = True
        self._checked = {row.program.key for row in self._rows if row.program.key in keys and row.program.removable}
        for row in self._rows:
            if row.program.removable:
                row.setCheckState(0, Qt.CheckState.Checked if row.program.key in self._checked else Qt.CheckState.Unchecked)
        self._loading = False
        self._update_selection()

    def _check_safe(self) -> None:
        self._set_checks({p.key for p in un.default_selection([r.program for r in self._rows]) if p.key in self._visible})

    def _check_all(self) -> None:
        self._set_checks({row.program.key for row in self._rows if row.program.removable and row.program.key in self._visible})

    def _clear_checks(self) -> None:
        self._set_checks(set())

    def selected_programs(self) -> list[pr.Program]:
        """Отмеченное И показанное сейчас: скрытые фильтром строки удаляться не будут, даже если были отмечены."""
        return [row.program for row in self._rows
                if row.program.key in self._checked and row.program.key in self._visible and row.program.removable]

    def _update_selection(self) -> None:
        chosen = self.selected_programs()
        total = sum(p.size_bytes for p in chosen)
        text = f"Выбрано: {len(chosen)}" + (f" · {fmt_bytes(total)}" if total else "")
        self.selected_label.setText(text if chosen else "")
        self.btn_remove.setEnabled(bool(chosen) and self.available and not self._busy)

    def _set_controls_enabled(self, enabled: bool) -> None:
        for widget in (self.btn_refresh, self.btn_safe, self.btn_all, self.btn_none, self.search, self.hidden_box):
            widget.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled and bool(self.selected_programs()))

    def _set_status(self, text: str) -> None:
        self.status.setText(text)

    # ------------------------------------------------------------------ удаление
    def _start_uninstall(self) -> None:
        chosen = self.selected_programs()
        if not chosen or self._busy or not self.available:
            return
        dialog = UninstallConfirmDialog(chosen, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._begin(chosen, dialog.restore_point, dialog.silent)

    def _begin(self, chosen: list[pr.Program], restore_point: bool, silent: bool) -> None:
        self._busy = True
        self._set_controls_enabled(False)
        self._cancel.clear()
        progress = UninstallProgressDialog(len(chosen), self)
        progress.stop_requested.connect(self._cancel.set)
        self._dialog = progress
        progress.show()
        if restore_point:
            progress.set_stage("Создаю точку восстановления Windows… Появится запрос прав администратора — разрешите его.")
            self._async.submit(lambda: un.create_restore_point(self._win),
                               lambda res, err: self._after_restore_point(res, err, chosen, silent))
        else:
            self._run_batch(chosen, silent)

    def _after_restore_point(self, result: un.RestorePointResult | None, error: Exception | None,
                             chosen: list[pr.Program], silent: bool) -> None:
        progress = self._dialog
        if progress is None:
            return
        if error is None and result is not None and result.ok:
            progress.add_line("✓ Точка восстановления Windows создана.")
        else:
            reason = result.message if result is not None else "Не удалось создать точку восстановления."
            if not confirm(progress, "Точка восстановления не создана", f"{reason}\n\nПродолжить удаление без неё?", "Продолжить"):
                progress.finish_text("Отменено", "Ничего не удалено: вы отказались продолжать без точки восстановления.")
                self._finish_batch(reload=False)
                return
            progress.add_line(f"– Без точки восстановления: {reason}")
        self._run_batch(chosen, silent)

    def _run_batch(self, chosen: list[pr.Program], silent: bool) -> None:
        cancel = self._cancel

        def job(report: Callable[[Any], None]) -> list[un.UninstallResult]:
            return self._uninstaller.uninstall_many(
                chosen, silent, on_progress=lambda i, n, p: report(("start", i, n, p.name)),
                on_result=lambda r: report(("result", r)), cancelled=cancel.is_set)
        self._progress_runner.start(job, self._on_batch_progress, self._on_batch_done)

    def _on_batch_progress(self, payload: Any) -> None:
        progress = self._dialog
        if progress is None:
            return
        if payload[0] == "start":
            progress.on_start(payload[1], payload[2], payload[3])
        else:
            progress.on_result(payload[1])

    def _on_batch_done(self, results: list[un.UninstallResult] | None, error: Exception | None) -> None:
        progress = self._dialog
        if progress is not None:
            if error is not None or results is None:
                progress.finish_text("Ошибка", "Удаление прервалось из-за внутренней ошибки NOVA. Список сейчас обновится.")
            else:
                progress.finish(results)
        removed = sum(1 for r in results or [] if r.status == un.STATUS_REMOVED)
        if removed:
            self._ctx.library.notice.emit("info", f"Удалено программ: {removed}.")
        self._finish_batch(reload=True)

    def _finish_batch(self, reload: bool) -> None:
        self._busy = False
        self._set_controls_enabled(self.available)
        if reload:
            self._reload()
