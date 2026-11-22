"""Просмотр запущенных процессов без завершения или изменения системных задач."""
from __future__ import annotations

import psutil
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView

from ui.widgets import Card, Page, make_label


class ProcessesPage(Page):
    def __init__(self) -> None:
        super().__init__("Процессы", "Смотри, какие приложения сейчас работают и сколько ресурсов они используют. Список только для просмотра.")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск процесса…")
        self.search.textChanged.connect(self._fill_table)
        self.refresh = QPushButton("Обновить")
        self.refresh.setObjectName("primary")
        self.refresh.clicked.connect(self.refresh_processes)
        tools = QHBoxLayout()
        tools.addWidget(self.search, 1)
        tools.addWidget(self.refresh)
        self.body.addLayout(tools)

        card = Card(spacing=8)
        card.body.addWidget(make_label("Активные процессы", "h2"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Процесс", "PID", "CPU", "Память"])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(390)
        card.body.addWidget(self.table)
        self.status = make_label("", "caption", wrap=True)
        card.body.addWidget(self.status)
        self.body.addWidget(card)
        self.body.addWidget(make_label(
            "Подсказка: высокий процент CPU или большой объём памяти не всегда означает проблему — "
            "это зависит от запущенной игры и программ. Для завершения задач используй Диспетчер задач Windows.",
            "muted", wrap=True,
        ))
        self.finish()

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh_processes)
        self.refresh_processes()

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self.refresh_processes()
        self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._timer.stop()
        super().hideEvent(event)

    def refresh_processes(self) -> None:
        rows = []
        total_cpus = max(psutil.cpu_count(logical=True) or 1, 1)
        for proc in psutil.process_iter(["pid", "name", "memory_info"]):
            try:
                info = proc.info
                name = str(info.get("name") or "<без имени>")
                memory = getattr(info.get("memory_info"), "rss", 0) / (1024 * 1024)
                cpu = proc.cpu_percent(interval=None) / total_cpus
                rows.append((name, int(info.get("pid") or 0), max(0.0, cpu), memory))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        rows.sort(key=lambda row: row[3], reverse=True)
        self._rows = rows[:120]
        self._fill_table()

    def _fill_table(self) -> None:
        query_value = self.search.text()
        query = query_value.strip().casefold() if isinstance(query_value, str) else ""
        rows = [row for row in getattr(self, "_rows", []) if query in row[0].casefold()]
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for index, (name, pid, cpu, memory) in enumerate(rows):
            values = (name, str(pid), f"{cpu:.1f}%", f"{memory:,.0f} МБ")
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, (name, pid, cpu, memory)[col])
                if col:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(index, col, item)
        self.table.setSortingEnabled(True)
        self.status.setText(f"Показано процессов: {len(rows)}. Обновление каждые 3 секунды, пока открыта эта вкладка.")
