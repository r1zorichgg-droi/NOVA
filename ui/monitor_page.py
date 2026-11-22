"""Мониторинг ПК: CPU, GPU, RAM, диски, сеть и состояние датчиков — с графиками реального времени."""
from __future__ import annotations

import logging

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from app_context import AppContext
from system.collector import Snapshot
from system.disk_monitor import DiskMonitor, DiskStats
from system.fmt import NA, fmt_bytes, fmt_percent, fmt_speed_long, fmt_temp
from ui import theme
from ui.widgets import BarMeter, Card, Chart, Page, Stat, make_label, safe_list
from ui.workers import AsyncRunner

log = logging.getLogger(__name__)
DISK_REFRESH_MS = 5000


class ChartCard(Card):
    def __init__(self, title: str, stats: list[str], y_max: float | None = 100.0, y_label=None,
                 big: bool = True) -> None:
        super().__init__(spacing=10)
        head = QHBoxLayout()
        head.addWidget(make_label(title, "h3"))
        head.addStretch(1)
        self.subtitle = make_label("", "caption")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.subtitle)
        self.body.addLayout(head)
        self.big = make_label(NA, "big")
        if big:
            self.body.addWidget(self.big)
        self.chart = Chart(y_max=y_max, y_label=y_label, min_height=130)
        self.body.addWidget(self.chart)
        row = QHBoxLayout()
        row.setSpacing(24)
        self.stats = [Stat(c) for c in stats]
        for stat in self.stats:
            row.addWidget(stat)
        row.addStretch(1)
        self.body.addLayout(row)
        self.note = make_label("", "caption", wrap=True)
        self.note.hide()
        self.body.addWidget(self.note)

    def set_values(self, values: list[str]) -> None:
        for stat, value in zip(self.stats, values, strict=False):
            stat.set_value(value)

    def set_note(self, text: str) -> None:
        self.note.setText(text)
        self.note.setVisible(bool(text))


class DiskRow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.name = make_label("", "h3")
        self.info = make_label("", "caption")
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.name)
        head.addStretch(1)
        head.addWidget(self.info)
        lay.addLayout(head)
        self.bar = BarMeter()
        lay.addWidget(self.bar)

    def set_disk(self, d: DiskStats) -> None:
        pal = theme.current()
        self.name.setText(f"{d.mountpoint}  ·  {d.fstype}")
        self.info.setText(f"свободно {fmt_bytes(d.free)} из {fmt_bytes(d.total)}  ·  занято {d.percent:.0f}%")
        color = pal.danger if d.percent >= 90 else pal.warn if d.percent >= 75 else pal.accent
        self.bar.set_fraction(d.percent / 100.0, color)


class MonitorPage(Page):
    needs_samples = True

    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Мониторинг ПК", "Данные обновляются каждую секунду, пока окно NOVA открыто. "
                                          "Если датчика нет, вместо числа показывается N/A.")
        self._ctx = ctx
        self._runner = AsyncRunner(self)
        self._disk_monitor = DiskMonitor()
        self._disk_busy = False
        self._disk_rows: list[DiskRow] = []

        grid = QGridLayout()
        grid.setSpacing(16)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.cpu = ChartCard("Процессор", ["Потоки", "Частота", "Температура"])
        self.gpu = ChartCard("Видеокарта", ["Температура", "Видеопамять", "Источник"])
        self.ram = ChartCard("Оперативная память", ["Всего", "Используется", "Свободно"])
        self.net = ChartCard("Интернет", ["Загрузка", "Отдача"], y_max=None, y_label=fmt_speed_long, big=False)
        grid.addWidget(self.cpu, 0, 0)
        grid.addWidget(self.gpu, 0, 1)
        grid.addWidget(self.ram, 1, 0)
        grid.addWidget(self.net, 1, 1)

        self.disk_card = Card(spacing=8)
        self.disk_card.body.addWidget(make_label("Диски", "h3"))
        self.disk_layout = QVBoxLayout()
        self.disk_layout.setSpacing(6)
        self.disk_card.body.addLayout(self.disk_layout)
        self.disk_hint = make_label("Читаю список дисков…", "muted")
        self.disk_card.body.addWidget(self.disk_hint)
        grid.addWidget(self.disk_card, 2, 0, 1, 2)

        self.sensor_card = Card(spacing=6)
        self.sensor_card.body.addWidget(make_label("Датчики", "h3"))
        self.sensor_gpu = make_label("", "muted", wrap=True)
        self.sensor_lhm = make_label("", "muted", wrap=True)
        self.sensor_card.body.addWidget(self.sensor_gpu)
        self.sensor_card.body.addWidget(self.sensor_lhm)
        grid.addWidget(self.sensor_card, 3, 0, 1, 2)

        self.body.addLayout(grid)
        self.finish()

        self._disk_timer = QTimer(self)
        self._disk_timer.setInterval(DISK_REFRESH_MS)
        self._disk_timer.timeout.connect(self._refresh_disks)

    # -- жизненный цикл -----------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._refresh_disks()
        self._disk_timer.start()
        snap = self._ctx.sampler.last
        if snap is not None:
            self.on_data(snap)
        else:
            self._update_sensor_status()

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._disk_timer.stop()
        super().hideEvent(event)

    # -- диски (в фоновом потоке: спящий сетевой диск не должен подвешивать окно) ----
    def _refresh_disks(self) -> None:
        if self._disk_busy:
            return
        self._disk_busy = True
        self._runner.submit(self._disk_monitor.read, self._on_disks)

    def _on_disks(self, result: list[DiskStats] | None, error: Exception | None) -> None:
        self._disk_busy = False
        if error is not None or result is None:
            self.disk_hint.setText("Не удалось прочитать список дисков.")
            self.disk_hint.show()
            return
        self.disk_hint.setVisible(not result)
        if not result:
            self.disk_hint.setText("Диски не найдены.")
        while len(self._disk_rows) < len(result):
            row = DiskRow()
            self._disk_rows.append(row)
            self.disk_layout.addWidget(row)
        while len(self._disk_rows) > len(result):
            self._disk_rows.pop().deleteLater()
        for row, disk in zip(self._disk_rows, result, strict=True):
            row.set_disk(disk)

    # -- метрики ------------------------------------------------------------------
    def on_data(self, snap: Snapshot) -> None:
        pal = theme.current()
        hist = self._ctx.sampler.history
        cpu, ram, gpu = snap.cpu, snap.ram, snap.gpu

        self.cpu.big.setText(fmt_percent(cpu.percent))
        self.cpu.subtitle.setText(f"{cpu.cores} ядер")
        self.cpu.chart.set_series([(safe_list(hist["cpu"]), pal.c_cpu)])
        freq = f"{cpu.freq_mhz / 1000:.2f} ГГц" if cpu.freq_mhz else NA
        self.cpu.set_values([str(cpu.threads), freq, fmt_temp(snap.cpu_temp)])
        self.cpu.set_note("" if snap.cpu_temp is not None else
                          "Температура процессора недоступна: запустите LibreHardwareMonitor (см. «Датчики» ниже).")

        self.gpu.big.setText(fmt_percent(gpu.percent if gpu else None))
        self.gpu.subtitle.setText(gpu.name if gpu and gpu.name else "нет данных")
        self.gpu.chart.set_series([(safe_list(hist["gpu"]), pal.c_gpu)])
        vram = NA
        if gpu and gpu.vram_used is not None and gpu.vram_total:
            vram = f"{fmt_bytes(gpu.vram_used)} / {fmt_bytes(gpu.vram_total)}"
        source = {"nvml": "NVIDIA (NVML)", "lhm": "LibreHardwareMonitor"}.get(gpu.source or "", NA) if gpu else NA
        self.gpu.set_values([fmt_temp(snap.gpu_temp), vram, source])
        self.gpu.set_note("" if gpu else "Нет данных о видеокарте — причина указана в блоке «Датчики».")

        self.ram.big.setText(fmt_percent(ram.percent))
        self.ram.subtitle.setText(f"{fmt_bytes(ram.used)} из {fmt_bytes(ram.total)}")
        self.ram.chart.set_series([(safe_list(hist["ram"]), pal.c_ram)])
        self.ram.set_values([fmt_bytes(ram.total), fmt_bytes(ram.used), fmt_bytes(ram.available)])

        self.net.chart.set_series([(safe_list(hist["net_down"]), pal.c_down), (safe_list(hist["net_up"]), pal.c_up)])
        self.net.set_values([f"↓ {fmt_speed_long(snap.net.down_bps)}", f"↑ {fmt_speed_long(snap.net.up_bps)}"])
        self.net.subtitle.setText("все сетевые адаптеры")
        self._update_sensor_status()

    def _update_sensor_status(self) -> None:
        collector = self._ctx.sampler.collector
        self.sensor_gpu.setText(f"Видеокарта NVIDIA: {collector.gpu_monitor.status}.")
        if collector.sensors.connected:
            self.sensor_lhm.setText("LibreHardwareMonitor: подключён — температуры и данные других видеокарт доступны.")
        else:
            self.sensor_lhm.setText(
                "LibreHardwareMonitor: не найден. Для температур запустите его от имени администратора и включите "
                "Options → Remote Web Server → Run (порт 8085). NOVA только читает данные с localhost.")
