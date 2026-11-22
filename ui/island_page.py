"""Настройки Dynamic Island: включение, размер, положение, прозрачность, показатели, уведомления, проверка."""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app_context import AppContext
from config import ISLAND_METRICS
from island.model import Notification
from system import winapi
from ui.bindings import SliderRow, bind_segmented, bind_toggle
from ui.item_dialogs import PinPickerDialog
from ui.library_page import ADD_FILTER
from ui.widgets import Card, ElidedLabel, HLine, Page, chip_button, clear_layout, make_label, setting_row

METRIC_LABELS = {
    "cpu": "CPU", "gpu": "GPU", "ram": "RAM", "cpu_temp": "Температура CPU",
    "gpu_temp": "Температура GPU", "net": "Интернет", "clock": "Часы",
}


class IslandPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Dynamic Island", "Капсула сверху по центру экрана: музыка, показатели ПК и уведомления. "
                                           "Клик по капсуле раскрывает её; правая кнопка мыши или клик по пустому месту раскрытой "
                                           "капсулы сворачивает. Колесо мыши и точки справа переключают музыку, статистику и «Приложения».")
        self._ctx = ctx
        s = ctx.settings

        # --- основное ---
        main = Card(spacing=4)
        main.body.addWidget(make_label("Основное", "h2"))
        main.body.addWidget(setting_row("Показывать Dynamic Island", "Включает и выключает капсулу целиком.",
                                        bind_toggle(s, "island_enabled")))
        main.body.addWidget(HLine())
        main.body.addWidget(setting_row(
            "Скрывать в полноэкранных играх",
            "Капсула прячется, когда запущена полноэкранная игра, и возвращается после выхода. "
            "NOVA только читает состояние окон — в процессы игр не внедряется.",
            bind_toggle(s, "island_hide_in_fullscreen")))
        main.body.addWidget(HLine())
        main.body.addWidget(setting_row(
            "Музыкальный режим", "Название, исполнитель, обложка и кнопки управления — через Windows Media Session "
                                 "(Spotify, браузеры и другие плееры).", bind_toggle(s, "island_music_mode")))
        self.body.addWidget(main)
        self.body.addWidget(self._build_launcher_card())

        # --- внешний вид ---
        look = Card(spacing=4)
        look.body.addWidget(make_label("Внешний вид", "h2"))
        rows = [
            ("Ширина", "Минимальная ширина свёрнутой капсулы.",
             SliderRow(s, "island_width", 180, 400, fmt=lambda v: f"{v:.0f} px")),
            ("Высота", "Высота свёрнутой капсулы.",
             SliderRow(s, "island_height", 30, 60, fmt=lambda v: f"{v:.0f} px")),
            ("Масштаб раскрытого вида", "Размер плеера и статистики при раскрытии.",
             SliderRow(s, "island_scale", 80, 140, scale=100, fmt=lambda v: f"{v * 100:.0f}%")),
            ("Смещение по горизонтали", "От центра экрана: минус — влево, плюс — вправо.",
             SliderRow(s, "island_offset_x", -600, 600, fmt=lambda v: f"{v:.0f} px")),
            ("Отступ от верхнего края", "Расстояние от верха экрана.",
             SliderRow(s, "island_offset_y", 0, 200, fmt=lambda v: f"{v:.0f} px")),
            ("Прозрачность", "Прозрачность всей капсулы.",
             SliderRow(s, "island_opacity", 50, 100, scale=100, fmt=lambda v: f"{v * 100:.0f}%")),
        ]
        for title, desc, control in rows:
            look.body.addWidget(setting_row(title, desc, control))
            look.body.addWidget(HLine())
        look.body.addWidget(setting_row(
            "Анимации", "Плавность раскрытия капсулы и звуковых полосок.",
            bind_segmented(s, "animations", [("smooth", "Плавные"), ("fast", "Быстрые"), ("off", "Выкл")])))
        look.body.addWidget(HLine())
        look.body.addWidget(setting_row(
            "Скорость обновления показателей", "Чем реже, тем меньше нагрузка на ПК.",
            bind_segmented(s, "island_update_ms", [(500, "0,5 с"), (1000, "1 с"), (2000, "2 с"), (5000, "5 с")])))
        look.body.addWidget(HLine())
        look.body.addWidget(setting_row(
            "Автосворачивание", "Через сколько секунд после ухода курсора раскрытая капсула сворачивается.",
            bind_segmented(s, "island_autocollapse_s", [(0, "Выкл"), (4, "4 с"), (8, "8 с")])))
        self.body.addWidget(look)

        # --- показатели ---
        metrics = Card(spacing=10)
        metrics.body.addWidget(make_label("Показатели в свёрнутой капсуле", "h2"))
        metrics.body.addWidget(make_label(
            "Выберите, что показывать, когда музыка не играет. Недоступные датчики отображаются как N/A.",
            "muted", wrap=True))
        grid = QGridLayout()
        grid.setSpacing(8)
        self._metric_buttons: dict[str, QPushButton] = {}
        for i, key in enumerate(ISLAND_METRICS):
            btn = chip_button(METRIC_LABELS[key], key in s.get("island_metrics"))
            btn.toggled.connect(lambda _checked: self._on_metric_toggled())
            self._metric_buttons[key] = btn
            grid.addWidget(btn, i // 4, i % 4)
        grid.setColumnStretch(4, 1)
        metrics.body.addLayout(grid)
        self.body.addWidget(metrics)
        s.changed.connect(self._on_setting)

        # --- уведомления ---
        notes = Card(spacing=4)
        notes.body.addWidget(make_label("Уведомления в капсуле", "h2"))
        for i, (key, title, desc) in enumerate([
                ("island_notify_usb", "USB-устройства", "Подключение и отключение флешек и других устройств."),
                ("island_notify_volume", "Громкость", "Показывает уровень при изменении громкости Windows."),
                ("island_notify_apps", "Запуск приложений", "Появляется, когда открывается новое окно программы."),
                ("island_notify_music", "Смена трека", "Короткое уведомление, когда музыкальный режим выключен.")]):
            if i:
                notes.body.addWidget(HLine())
            notes.body.addWidget(setting_row(title, desc, bind_toggle(s, key)))
        notes.body.addWidget(make_label(
            "Чужие системные уведомления Windows (тосты) на этом этапе не перехватываются: для этого Windows требует "
            "отдельного разрешения и идентификатора приложения.", "caption", wrap=True))
        self.body.addWidget(notes)

        # --- проверка и состояние ---
        test = Card(spacing=10)
        test.body.addWidget(make_label("Проверка", "h2"))
        buttons = QHBoxLayout()
        self.btn_toast = QPushButton("Показать тестовое уведомление")
        self.btn_toast.clicked.connect(self._send_test)
        self.btn_toggle = QPushButton("Раскрыть / свернуть")
        self.btn_toggle.clicked.connect(ctx.island.toggle)
        buttons.addWidget(self.btn_toast)
        buttons.addWidget(self.btn_toggle)
        buttons.addStretch(1)
        test.body.addLayout(buttons)
        test.body.addWidget(HLine())
        self.status_music = make_label("", "muted", wrap=True)
        self.status_volume = make_label("", "muted", wrap=True)
        self.status_game = make_label("", "muted", wrap=True)
        for lbl in (self.status_music, self.status_volume, self.status_game):
            test.body.addWidget(lbl)
        self.body.addWidget(test)
        self.finish()

        ctx.detector.fullscreen_changed.connect(lambda *_: self._refresh_status())
        self._refresh_status()

    # -- быстрый запуск -------------------------------------------------------------
    def _build_launcher_card(self) -> Card:
        ctx = self._ctx
        card = Card(spacing=6)
        card.body.addWidget(make_label("Быстрый запуск в острове", "h2"))
        card.body.addWidget(setting_row(
            "Вкладка «Приложения»",
            "Отдельная вкладка раскрытой капсулы: плитки ваших программ и игр, которые открываются одним кликом.",
            bind_toggle(ctx.settings, "island_launcher_enabled")))
        card.body.addWidget(HLine())
        card.body.addWidget(make_label("Что показывать во вкладке", "h3"))
        self._pins_box = QVBoxLayout()
        self._pins_box.setSpacing(6)
        card.body.addLayout(self._pins_box)
        self._pins_empty = make_label("Пока ничего не добавлено. Выберите программы из библиотеки или добавьте EXE-файл.",
                                      "muted", wrap=True)
        card.body.addWidget(self._pins_empty)
        row = QHBoxLayout()
        row.setSpacing(10)
        pick = QPushButton("Добавить из библиотеки…")
        pick.setObjectName("primary")
        pick.clicked.connect(self._pick_pins)
        exe = QPushButton("Добавить EXE…")
        exe.clicked.connect(self._add_exe)
        row.addWidget(pick)
        row.addWidget(exe)
        row.addStretch(1)
        card.body.addLayout(row)
        card.body.addWidget(make_label(
            "Ещё проще: перетащите EXE-файл или ярлык прямо на раскрытую капсулу — программа появится в этой вкладке. "
            "Программа добавляется и в вашу библиотеку «Приложения». Убрать плитку можно здесь — файлы на ПК не удаляются.",
            "caption", wrap=True))
        ctx.library.changed.connect(self._refresh_pins)
        self._refresh_pins()
        return card

    def _refresh_pins(self) -> None:
        lib, icons = self._ctx.library, self._ctx.icons
        clear_layout(self._pins_box)
        refs = lib.pinned()
        self._pins_empty.setVisible(not refs)
        for kind, item_id in refs:
            item = lib.table(kind).get(item_id)
            if item is None:
                continue
            row = QFrame()
            row.setObjectName("cardAlt")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(12, 8, 8, 8)
            lay.setSpacing(10)
            icon = QLabel()
            icon.setFixedSize(QSize(28, 28))
            icon.setPixmap(icons.pixmap(kind, item, 28))
            lay.addWidget(icon)
            lay.addWidget(ElidedLabel(item.title + ("   (игра)" if kind == "game" else ""), "h3"), 1)
            for text, tip, handler in (
                    ("↑", "Выше", lambda _c=False, k=kind, i=item_id: lib.move_pin(k, i, -1)),
                    ("↓", "Ниже", lambda _c=False, k=kind, i=item_id: lib.move_pin(k, i, 1)),
                    ("✕", "Убрать из острова (программа остаётся в библиотеке)",
                     lambda _c=False, k=kind, i=item_id: lib.unpin(k, i))):
                btn = QPushButton(text)
                btn.setObjectName("dots")
                btn.setFixedSize(36, 32)
                btn.setToolTip(tip)
                btn.setCursor(Qt.CursorShape.PointingHandCursor)
                btn.clicked.connect(handler)
                lay.addWidget(btn)
            self._pins_box.addWidget(row)

    def _pick_pins(self) -> None:
        PinPickerDialog(self._ctx.library, self._ctx.icons, self).exec()
        self._refresh_pins()

    def _add_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Добавить программу в остров", "", ADD_FILTER)
        if not path:
            return
        ok, message = self._ctx.library.add_to_island(path)
        self._ctx.library.notice.emit("info" if ok else "error", message)

    # -- обработчики ----------------------------------------------------------------
    def _on_metric_toggled(self) -> None:
        chosen = [k for k in ISLAND_METRICS if self._metric_buttons[k].isChecked()]
        self._ctx.settings.set("island_metrics", chosen)

    def _on_setting(self, key: str) -> None:
        if key == "island_metrics":
            active = self._ctx.settings.get("island_metrics")
            for metric, btn in self._metric_buttons.items():
                if btn.isChecked() != (metric in active):
                    btn.blockSignals(True)
                    btn.setChecked(metric in active)
                    btn.blockSignals(False)
        if key == "island_enabled":
            self._refresh_status()
        if key == "island_launch_items":
            self._refresh_pins()

    def _send_test(self) -> None:
        self._ctx.hub.post(Notification("system", "NOVA", "Так выглядят уведомления"))

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._refresh_status()

    def _refresh_status(self) -> None:
        ctx = self._ctx
        enabled = bool(ctx.settings.get("island_enabled"))
        self.btn_toast.setEnabled(enabled)
        self.btn_toggle.setEnabled(enabled)
        self.status_music.setText(f"Музыка: {ctx.music.status}.")
        if ctx.volume.available:
            self.status_volume.setText("Громкость: отслеживается.")
        else:
            self.status_volume.setText(f"Громкость: недоступно ({ctx.volume.error or 'нужен пакет pycaw'}).")
        if not winapi.IS_WINDOWS:
            self.status_game.setText("Определение полноэкранных игр: работает только в Windows.")
        elif ctx.detector.active:
            self.status_game.setText(f"Определение игр: сейчас открыто полноэкранное приложение "
                                     f"({ctx.detector.process_name or 'неизвестно'}).")
        else:
            self.status_game.setText("Определение игр: работает, полноэкранных приложений сейчас нет.")
