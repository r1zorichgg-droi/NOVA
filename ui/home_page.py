"""Главная страница: приветствие, время, живые метрики, недавние игры и избранные приложения."""
from __future__ import annotations

import getpass
from datetime import datetime

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout

from app_context import AppContext
from system.collector import Snapshot
from system.fmt import NA, fmt_bytes, fmt_date_ru, fmt_temp, fmt_uptime, greeting
from ui import theme
from ui.widgets import Card, Chart, Page, RingGauge, Stat, make_label, safe_list


def display_name(ctx: AppContext) -> str:
    name = str(ctx.settings.get("user_name")).strip()
    if name:
        return name
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 — в редких окружениях имя пользователя недоступно
        return ""


class MetricCard(Card):
    """Карточка метрики: кольцо с процентами и несколько подписанных значений рядом."""

    def __init__(self, title: str, color_key: str, captions: list[str]) -> None:
        super().__init__(spacing=12)
        self._color_key = color_key
        head = QHBoxLayout()
        head.addWidget(make_label(title, "h3"))
        head.addStretch(1)
        self.subtitle = make_label("", "caption")
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.subtitle)
        self.body.addLayout(head)
        row = QHBoxLayout()
        row.setSpacing(20)
        self.ring = RingGauge(getattr(theme.current(), color_key))
        row.addWidget(self.ring)
        col = QVBoxLayout()
        col.setSpacing(8)
        self.stats = [Stat(c) for c in captions]
        for stat in self.stats:
            col.addWidget(stat)
        row.addLayout(col, 1)
        self.body.addLayout(row)

    def set_subtitle(self, text: str) -> None:
        metrics = QFontMetrics(self.subtitle.font())
        self.subtitle.setText(metrics.elidedText(text, Qt.TextElideMode.ElideRight, 190))
        self.subtitle.setToolTip(text)

    def update_values(self, percent: float | None, values: list[str]) -> None:
        self.ring.set_color(getattr(theme.current(), self._color_key))
        self.ring.set_value(percent)
        for stat, value in zip(self.stats, values, strict=False):
            stat.set_value(value)


class ListCard(Card):
    """Карточка со списком строк (недавние игры / избранные приложения) и сообщением, если список пуст."""

    def __init__(self, title: str, empty_text: str) -> None:
        super().__init__(spacing=8)
        self.body.addWidget(make_label(title, "h3"))
        self._empty_text = empty_text
        self._rows = QVBoxLayout()
        self._rows.setSpacing(6)
        self.body.addLayout(self._rows)
        self.body.addStretch(1)

    def set_items(self, items: list[str]) -> None:
        while self._rows.count():
            item = self._rows.takeAt(0)
            if item is not None and item.widget() is not None:
                item.widget().deleteLater()
        if not items:
            self._rows.addWidget(make_label(self._empty_text, "muted", wrap=True))
            return
        for text in items:
            self._rows.addWidget(make_label(text))


class HomePage(Page):
    needs_samples = True

    def __init__(self, ctx: AppContext) -> None:
        super().__init__("NOVA", " ")   # тексты заголовка и даты задаёт _tick()
        self._ctx = ctx
        self._legend_colors: list[tuple[int, int, int, int]] = []

        grid = QGridLayout()
        grid.setSpacing(16)
        for col in range(3):
            grid.setColumnStretch(col, 1)
        self.cpu = MetricCard("Процессор", "c_cpu", ["Потоки", "Частота", "Температура"])
        self.gpu = MetricCard("Видеокарта", "c_gpu", ["Температура", "Видеопамять", "Источник данных"])
        self.ram = MetricCard("Память", "c_ram", ["Используется", "Свободно", "Всего"])
        grid.addWidget(self.cpu, 0, 0)
        grid.addWidget(self.gpu, 0, 1)
        grid.addWidget(self.ram, 0, 2)

        chart_card = Card(spacing=8)
        head = QHBoxLayout()
        head.addWidget(make_label("Нагрузка за последние 2 минуты", "h3"))
        head.addStretch(1)
        self._legend: list[QLabel] = []
        for text in ("CPU", "GPU", "RAM"):
            lbl = make_label(text, "caption")
            head.addWidget(lbl)
            self._legend.append(lbl)
        chart_card.body.addLayout(head)
        self.chart = Chart(y_max=100.0, min_height=150)
        chart_card.body.addWidget(self.chart)
        grid.addWidget(chart_card, 1, 0, 1, 3)

        self.uptime_card = Card(spacing=6)
        self.uptime_card.body.addWidget(make_label("Компьютер работает", "h3"))
        self.uptime_label = make_label(NA, "big")
        self.uptime_card.body.addWidget(self.uptime_label)
        self.uptime_card.body.addWidget(make_label("с момента последней загрузки Windows", "caption", wrap=True))
        self.uptime_card.body.addStretch(1)
        self.games_card = ListCard("Недавние игры", "Здесь появятся игры, которые вы запустили через NOVA.")
        self.apps_card = ListCard("Избранные приложения", "Отметьте приложения звёздочкой в библиотеке — они появятся здесь.")
        grid.addWidget(self.uptime_card, 2, 0)
        grid.addWidget(self.games_card, 2, 1)
        grid.addWidget(self.apps_card, 2, 2)

        self.body.addLayout(grid)
        self.finish()

        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self._tick)
        ctx.settings.changed.connect(lambda key: self._tick() if key == "user_name" else None)
        self._tick()

    # -- время и приветствие ------------------------------------------------------
    def _tick(self) -> None:
        now = datetime.now()
        name = display_name(self._ctx)
        self.title_label.setText(f"{greeting(now)}, {name}" if name else greeting(now))
        self.subtitle_label.setText(f"{fmt_date_ru(now)} · {now:%H:%M:%S}")

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._tick()
        self._clock.start()
        self._refresh_library()
        snap = self._ctx.sampler.last
        if snap is not None:
            self.on_data(snap)

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._clock.stop()
        super().hideEvent(event)

    def _refresh_library(self) -> None:
        self.games_card.set_items([g.title for g in self._ctx.db.games.recent(6)])
        self.apps_card.set_items([a.title for a in self._ctx.db.apps.favorites(8)])

    # -- метрики ------------------------------------------------------------------
    def on_data(self, snap: Snapshot) -> None:
        pal = theme.current()
        cpu, ram, gpu = snap.cpu, snap.ram, snap.gpu
        freq = f"{cpu.freq_mhz / 1000:.2f} ГГц" if cpu.freq_mhz else NA
        self.cpu.update_values(cpu.percent, [str(cpu.threads), freq, fmt_temp(snap.cpu_temp)])
        self.cpu.set_subtitle(f"{cpu.cores} ядер")

        vram = NA
        if gpu and gpu.vram_used is not None and gpu.vram_total:
            vram = f"{fmt_bytes(gpu.vram_used)} / {fmt_bytes(gpu.vram_total)}"
        source = {"nvml": "NVIDIA (NVML)", "lhm": "LibreHardwareMonitor"}.get(gpu.source or "", NA) if gpu else NA
        self.gpu.update_values(gpu.percent if gpu else None, [fmt_temp(snap.gpu_temp), vram, source])
        self.gpu.set_subtitle(gpu.name if gpu and gpu.name else "нет данных")

        self.ram.update_values(ram.percent, [fmt_bytes(ram.used), fmt_bytes(ram.available), fmt_bytes(ram.total)])
        self.ram.set_subtitle(f"{ram.percent:.0f}%")

        hist = self._ctx.sampler.history
        self.chart.set_series([(safe_list(hist["cpu"]), pal.c_cpu), (safe_list(hist["gpu"]), pal.c_gpu),
                               (safe_list(hist["ram"]), pal.c_ram)])
        colors = [pal.c_cpu, pal.c_gpu, pal.c_ram]
        if colors != self._legend_colors:   # перекрашиваем подписи только при смене темы/акцента
            self._legend_colors = colors
            for lbl, color in zip(self._legend, colors, strict=True):
                lbl.setStyleSheet(f"color: rgb({color[0]}, {color[1]}, {color[2]});")
        self.uptime_label.setText(fmt_uptime(snap.uptime_s))
