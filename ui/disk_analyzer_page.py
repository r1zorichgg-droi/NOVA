"""Анализ занятого места на диске. Только чтение: файлы не удаляются и не изменяются."""
from __future__ import annotations

import os
from pathlib import Path

import psutil
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QProgressBar, QPushButton, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout,
)

from system.fmt import fmt_bytes
from ui.widgets import Card, Page, make_label
from ui.workers import ProgressRunner


def _scan_folder(root: str, report) -> dict:
    """Рекурсивно считает размер файлов, не следует по symlink и пропускает недоступные каталоги."""
    stack = [root]
    total_bytes = 0
    files = 0
    folders = 0
    largest: list[tuple[int, str]] = []
    visited = 0
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            folders += 1
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            total_bytes += size
                            files += 1
                            largest.append((size, entry.path))
                            if len(largest) > 20:
                                largest.sort(reverse=True)
                                del largest[10:]
                        visited += 1
                        if visited % 500 == 0:
                            report((visited, files, folders, total_bytes))
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except (PermissionError, FileNotFoundError, NotADirectoryError, OSError):
            continue
    largest.sort(reverse=True)
    return {
        "root": root, "bytes": total_bytes, "files": files, "folders": folders,
        "largest": largest[:10], "visited": visited,
    }


class DiskAnalyzerPage(Page):
    def __init__(self) -> None:
        super().__init__(
            "Анализатор дисков",
            "Показывает занятое место на дисках и помогает найти самые большие файлы в выбранной папке.",
        )
        self._runner = ProgressRunner(self)
        self._busy = False

        drives = Card(spacing=10)
        drives.body.addWidget(make_label("Накопители", "h2"))
        self.drive_layout = QVBoxLayout()
        self.drive_layout.setSpacing(8)
        drives.body.addLayout(self.drive_layout)
        self.body.addWidget(drives)
        self._refresh_drives()

        scan = Card(spacing=10)
        scan.body.addWidget(make_label("Анализ папки", "h2"))
        scan.body.addWidget(make_label(
            "Выбери папку для подсчёта её размера. NOVA пройдёт по вложенным папкам в фоновом потоке "
            "и покажет 10 самых больших файлов. Системные и недоступные папки могут быть пропущены.",
            "muted", wrap=True))
        actions = QHBoxLayout()
        self.choose_button = QPushButton("Выбрать папку и анализировать…")
        self.choose_button.setObjectName("primary")
        self.choose_button.clicked.connect(self._choose_folder)
        self.refresh_button = QPushButton("Обновить диски")
        self.refresh_button.clicked.connect(self._refresh_drives)
        actions.addWidget(self.choose_button)
        actions.addWidget(self.refresh_button)
        actions.addStretch(1)
        scan.body.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.hide()
        scan.body.addWidget(self.progress)
        self.status = make_label("Папка ещё не выбрана.", "muted", wrap=True)
        scan.body.addWidget(self.status)
        self.results = QTreeWidget()
        self.results.setColumnCount(2)
        self.results.setHeaderLabels(["Файл", "Размер"])
        self.results.setMinimumHeight(230)
        self.results.setRootIsDecorated(False)
        self.results.setAlternatingRowColors(True)
        scan.body.addWidget(self.results)
        self.body.addWidget(scan)

        note = Card(spacing=6)
        note.body.addWidget(make_label("Безопасность", "h2"))
        note.body.addWidget(make_label(
            "Анализатор работает только на чтение. Он не удаляет, не перемещает и не изменяет файлы. "
            "Для проверки больших дисков выбирай отдельные папки: полный обход системного диска может занять много времени.",
            "muted", wrap=True))
        self.body.addWidget(note)
        self.finish()

    def _refresh_drives(self) -> None:
        while self.drive_layout.count():
            item = self.drive_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        found = 0
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (PermissionError, OSError):
                continue
            if not usage.total:
                continue
            found += 1
            row = QVBoxLayout()
            head = QHBoxLayout()
            head.addWidget(make_label(f"{part.device}  ·  {part.fstype or 'диск'}", "h3"))
            head.addStretch(1)
            head.addWidget(make_label(
                f"{fmt_bytes(usage.used)} занято / {fmt_bytes(usage.total)} · свободно {fmt_bytes(usage.free)}",
                "caption"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(usage.percent))
            bar.setFormat(f"{usage.percent:.1f}% занято")
            row.addLayout(head)
            row.addWidget(bar)
            holder = Card(spacing=6)
            holder.body.addLayout(row)
            self.drive_layout.addWidget(holder)
        if not found:
            self.drive_layout.addWidget(make_label("Информация о дисках недоступна.", "muted"))

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Выбери папку для анализа")
        if not folder:
            return
        self._start_scan(folder)

    def _start_scan(self, folder: str) -> None:
        if self._busy:
            return
        self._busy = True
        self.choose_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.results.clear()
        self.progress.show()
        self.status.setText(f"Анализирую: {folder}")
        self._runner.start(lambda report: _scan_folder(folder, report), self._on_progress, self._on_done)

    def _on_progress(self, payload) -> None:
        visited, files, folders, size = payload
        self.status.setText(
            f"Проверено элементов: {visited:,} · файлов: {files:,} · папок: {folders:,} · размер: {fmt_bytes(size)}"
        )

    def _on_done(self, result, error) -> None:
        self._busy = False
        self.choose_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.progress.hide()
        if error is not None or result is None:
            self.status.setText("Не удалось завершить анализ. Проверь права доступа к выбранной папке.")
            return
        self.status.setText(
            f"{result['root']}\nОбщий размер: {fmt_bytes(result['bytes'])} · "
            f"файлов: {result['files']:,} · папок: {result['folders']:,}"
        )
        for size, path in result["largest"]:
            item = QTreeWidgetItem([Path(path).name, fmt_bytes(size)])
            item.setToolTip(0, path)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            self.results.addTopLevelItem(item)
        self.results.resizeColumnToContents(0)
        self.results.resizeColumnToContents(1)
        if not result["largest"]:
            self.results.addTopLevelItem(QTreeWidgetItem(["Файлы не найдены", "—"]))
