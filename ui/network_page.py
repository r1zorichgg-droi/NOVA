"""Network Monitor: трафик адаптеров, задержка и потери пакетов."""
from __future__ import annotations

from collections import deque

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout

from app_context import AppContext
from system.fmt import fmt_speed_long
from system.network_monitor import NetworkMonitor
from system.network_probe import ping_host
from ui import theme
from ui.widgets import Card, Chart, Page, make_label
from ui.workers import AsyncRunner


class NetworkPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Network Monitor", "Скорость соединения, задержка до выбранного узла и процент потерянных ответов.")
        self._ctx = ctx
        self._traffic = NetworkMonitor()
        self._runner = AsyncRunner(self)
        self._ping_busy = False
        self._pings: deque[float] = deque(maxlen=20)
        self._losses: deque[bool] = deque(maxlen=20)
        self._latency_samples: list[float] = []
        self._seconds = 0

        target = Card(spacing=9)
        target.body.addWidget(make_label("Узел проверки", "h2"))
        row = QHBoxLayout()
        self.host_edit = QLineEdit(str(ctx.settings.get("network_ping_host")))
        self.host_edit.setPlaceholderText("1.1.1.1 или example.com")
        self.host_edit.setMaximumWidth(260)
        self.save_host = QPushButton("Сохранить адрес")
        self.save_host.clicked.connect(self._save_host)
        row.addWidget(self.host_edit)
        row.addWidget(self.save_host)
        row.addStretch(1)
        target.body.addLayout(row)
        self.target_status = make_label("Проверка выполняется каждые 3 секунды, пока открыта эта вкладка.", "caption")
        target.body.addWidget(self.target_status)
        self.body.addWidget(target)

        grid = QGridLayout()
        grid.setSpacing(14)
        self.ping_value = self._metric("PING", "—", "Задержка до узла")
        self.loss_value = self._metric("ПОТЕРИ", "—", "Последние 20 проверок")
        self.down_value = self._metric("DOWNLOAD", "—", "Входящий трафик")
        self.up_value = self._metric("UPLOAD", "—", "Исходящий трафик")
        for i, widget in enumerate((self.ping_value, self.loss_value, self.down_value, self.up_value)):
            grid.addWidget(widget, i // 2, i % 2)
        self.body.addLayout(grid)

        chart_card = Card(spacing=8)
        chart_card.body.addWidget(make_label("История задержки", "h2"))
        self.chart = Chart(y_max=None, min_height=170)
        chart_card.body.addWidget(self.chart)
        chart_card.body.addWidget(make_label(
            "Это проверка сетевой задержки до выбранного узла, а не скорость тарифа провайдера. "
            "ICMP может блокироваться роутером или провайдером; отсутствие ответа не всегда означает отсутствие интернета.",
            "caption", wrap=True))
        self.body.addWidget(chart_card)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.finish()

    @staticmethod
    def _metric(title: str, value: str, subtitle: str) -> Card:
        card = Card(spacing=4)
        card.body.addWidget(make_label(title, "caption"))
        value_label = make_label(value, "big")
        card.body.addWidget(value_label)
        card.body.addWidget(make_label(subtitle, "caption"))
        card.value_label = value_label  # type: ignore[attr-defined]
        return card

    def _save_host(self) -> None:
        value = self.host_edit.text().strip()
        self._ctx.settings.set("network_ping_host", value)
        self._pings.clear()
        self._losses.clear()
        self._latency_samples.clear()
        self.target_status.setText(f"Адрес проверки сохранён: {value}")
        self._ping()

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._timer.start()
        self._tick()
        self._ping()

    def hideEvent(self, event) -> None:  # noqa: N802, ANN001
        self._timer.stop()
        super().hideEvent(event)

    def shutdown(self) -> None:
        self._timer.stop()

    def _tick(self) -> None:
        try:
            stats = self._traffic.read()
            self.down_value.value_label.setText(fmt_speed_long(stats.down_bps))
            self.up_value.value_label.setText(fmt_speed_long(stats.up_bps))
        except Exception as exc:  # noqa: BLE001
            self.target_status.setText(f"Не удалось прочитать сетевые счётчики: {exc}")
        self._seconds += 1
        if self._seconds % 3 == 0:
            self._ping()

    def _ping(self) -> None:
        if self._ping_busy:
            return
        self._ping_busy = True
        host = self.host_edit.text().strip()
        self._runner.submit(lambda: ping_host(host), self._on_ping)

    def _on_ping(self, result, error) -> None:  # noqa: ANN001
        self._ping_busy = False
        if error is not None or result is None:
            self.target_status.setText(f"Ошибка проверки сети: {error or 'нет результата'}")
            return
        self._losses.append(bool(result.success))
        if result.success and result.latency_ms is not None:
            self._pings.append(float(result.latency_ms))
            self._latency_samples.append(float(result.latency_ms))
            self._latency_samples = self._latency_samples[-120:]
            self.ping_value.value_label.setText(f"{result.latency_ms:.1f} ms")
            self.target_status.setText(f"Ответ от {result.host}")
        elif result.success:
            self.ping_value.value_label.setText("OK")
            self.target_status.setText(result.error or f"Ответ от {result.host}")
        else:
            self.ping_value.value_label.setText("Timeout")
            self.target_status.setText(f"{result.host}: {result.error or 'нет ответа'}")
        loss = 100.0 * (1 - sum(self._losses) / len(self._losses)) if self._losses else 0.0
        self.loss_value.value_label.setText(f"{loss:.0f}%")
        pal = theme.current()
        self.chart.set_series([(self._latency_samples[-60:], pal.c_down)])
