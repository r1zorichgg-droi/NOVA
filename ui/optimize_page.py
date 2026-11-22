"""Страница «Оптимизация»: режим производительности для игр, твики Windows, фоновые программы, временные файлы.

Всё здесь обратимо и без прав администратора. Обещаний «+N FPS» нет: прирост зависит от ПК и игры.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPushButton

from app_context import AppContext
from launcher.launch import PRIORITY_LABELS
from system import cleanup, tweaks
from system.fmt import fmt_bytes, plural_ru
from ui.bindings import bind_segmented, bind_toggle
from ui.item_dialogs import CloseAppsDialog, confirm
from ui.widgets import Card, HLine, Page, Toggle, make_label, setting_row
from ui.workers import AsyncRunner

TWEAK_ORDER = ("game_mode", "game_dvr", "transparency")


class OptimizePage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Оптимизация", "Игровой режим NOVA и безопасные настройки Windows. Всё включается и выключается "
                                        "здесь, работает без прав администратора и обратимо.")
        self._ctx, self._tweaks = ctx, ctx.tweaks
        self._runner = AsyncRunner(self)
        self._scan: cleanup.TempScan | None = None
        self._shader_scan: cleanup.ShaderCacheScan | None = None
        s = ctx.settings

        # ---- режим производительности ----
        boost = Card(spacing=4)
        boost.body.addWidget(make_label("Режим производительности при запуске игры", "h2"))
        boost.body.addWidget(make_label(
            "Включается, когда вы запускаете игру из NOVA, и выключается сам после её закрытия: прежняя схема питания и "
            "настройки Windows возвращаются. Если NOVA закроют во время игры, настройки вернутся при следующем запуске.",
            "muted", wrap=True))
        boost.body.addWidget(HLine())
        rows = (
            ("boost_on_launch", "Включать при запуске игры",
             "Главный выключатель. Для отдельной игры или программы можно включить или выключить в меню «⋯» → «Оптимизация…»."),
            ("boost_power", "Схема питания «Максимальная производительность»",
             "Процессор не снижает частоту на время игры. Схему выбирают на странице «Электропитание»."),
            ("boost_game_mode", "«Игровой режим» Windows", "Windows отдаёт игре больше ресурсов и не мешает обновлениями."),
            ("boost_game_dvr", "Выключить фоновую запись игр", "Фоновая запись Xbox Game Bar берёт часть видеокарты и диска."),
            ("boost_notify", "Показывать уведомление в Dynamic Island", "Короткое сообщение, когда режим включился и выключился."),
        )
        for i, (key, title, desc) in enumerate(rows):
            if i:
                boost.body.addWidget(HLine())
            boost.body.addWidget(setting_row(title, desc, bind_toggle(s, key)))
        boost.body.addWidget(HLine())
        boost.body.addWidget(setting_row(
            "Приоритет процесса игры",
            "Применяется, когда NOVA сама запускает EXE игры. Игры из Steam, Epic и Store запускает их лаунчер — NOVA в процесс "
            "игры не вмешивается (так безопаснее для античитов). «Высокий» может замедлить другие программы.",
            bind_segmented(s, "boost_priority", list(PRIORITY_LABELS.items()))))
        self.body.addWidget(boost)

        # ---- настройки Windows ----
        win = Card(spacing=4)
        win.body.addWidget(make_label("Настройки Windows", "h2"))
        win.body.addWidget(make_label(
            "В отличие от режима производительности, эти настройки остаются, пока вы сами их не выключите. "
            "NOVA запоминает прежние значения и при выключении возвращает именно их.", "muted", wrap=True))
        self._toggles: dict[str, Toggle] = {}
        self._tweak_notes: dict[str, Any] = {}
        for tweak_id in TWEAK_ORDER:
            tweak = tweaks.TWEAKS[tweak_id]
            win.body.addWidget(HLine())
            toggle = Toggle(False)
            toggle.toggled.connect(lambda checked, t=tweak_id: self._on_tweak(t, checked))
            self._toggles[tweak_id] = toggle
            win.body.addWidget(setting_row(tweak.title, tweak.description, toggle))
            note = make_label("", "caption", wrap=True)
            self._tweak_notes[tweak_id] = note
            win.body.addWidget(note)
        self.tweaks_unavailable = make_label("", "warn", wrap=True)
        win.body.addWidget(self.tweaks_unavailable)
        self.body.addWidget(win)

        # ---- быстрый доступ к системным игровым настройкам ----
        quick = Card(spacing=8)
        quick.body.addWidget(make_label("Игровые настройки Windows", "h2"))
        quick.body.addWidget(make_label(
            "Быстро открой системные параметры видеокарты, игрового режима и записи. NOVA не меняет их без твоего действия.",
            "muted", wrap=True))
        quick_row = QHBoxLayout()
        for title, uri in (
                ("Графика и GPU", "ms-settings:display-advancedgraphics"),
                ("Игровой режим", "ms-settings:gaming-gamemode"),
                ("Запись игр", "ms-settings:gaming-captures"),
                ("Питание", "ms-settings:powersleep")):
            button = QPushButton(title)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, target=uri: QDesktopServices.openUrl(QUrl(target)))
            quick_row.addWidget(button)
        quick.body.addLayout(quick_row)
        self.body.addWidget(quick)

        # ---- фоновые программы ----
        bg = Card(spacing=8)
        bg.body.addWidget(make_label("Фоновые программы", "h2"))
        bg.body.addWidget(make_label(
            "Освободите память и процессор перед игрой: NOVA покажет программы с окнами, а закроет только те, что вы "
            "отметите, и попросит их закрыться как при нажатии на крестик. Системные процессы в списке не показываются.",
            "muted", wrap=True))
        row = QHBoxLayout()
        btn = QPushButton("Закрыть фоновые программы…")
        btn.clicked.connect(lambda: CloseAppsDialog([], self).exec())
        row.addWidget(btn)
        row.addStretch(1)
        bg.body.addLayout(row)
        self.body.addWidget(bg)

        # ---- временные файлы ----
        tmp = Card(spacing=8)
        tmp.body.addWidget(make_label("Временные файлы", "h2"))
        tmp.body.addWidget(make_label(
            "Очистка папки временных файлов пользователя. Сначала NOVA только считает, что можно удалить; удаляются "
            "лишь файлы старше часа и только после вашего подтверждения. Занятые программами файлы пропускаются.",
            "muted", wrap=True))
        buttons = QHBoxLayout()
        self.btn_scan = QPushButton("Проверить")
        self.btn_scan.clicked.connect(self._scan_temp)
        self.btn_clean = QPushButton("Очистить…")
        self.btn_clean.setObjectName("primary")
        self.btn_clean.setEnabled(False)
        self.btn_clean.clicked.connect(self._clean_temp)
        buttons.addWidget(self.btn_scan)
        buttons.addWidget(self.btn_clean)
        buttons.addStretch(1)
        tmp.body.addLayout(buttons)
        self.temp_status = make_label("", "muted", wrap=True)
        tmp.body.addWidget(self.temp_status)
        self.body.addWidget(tmp)

        # ---- кэш шейдеров видеокарты ----
        shaders = Card(spacing=8)
        shaders.body.addWidget(make_label("Кэш шейдеров видеокарты", "h2"))
        shaders.body.addWidget(make_label(
            "Проверяет известные пользовательские папки DirectX, NVIDIA, AMD и Intel. NOVA показывает только файлы "
            "старше 14 дней и ничего не удаляет до подтверждения. Очистка не гарантирует рост FPS; после неё игры "
            "могут заново компилировать шейдеры, поэтому первые запуски иногда подтормаживают.",
            "muted", wrap=True))
        shader_buttons = QHBoxLayout()
        self.btn_scan_shaders = QPushButton("Проверить кэш")
        self.btn_scan_shaders.clicked.connect(self._scan_shader_cache)
        self.btn_clean_shaders = QPushButton("Очистить старый кэш…")
        self.btn_clean_shaders.setObjectName("primary")
        self.btn_clean_shaders.setEnabled(False)
        self.btn_clean_shaders.clicked.connect(self._clean_shader_cache)
        shader_buttons.addWidget(self.btn_scan_shaders)
        shader_buttons.addWidget(self.btn_clean_shaders)
        shader_buttons.addStretch(1)
        shaders.body.addLayout(shader_buttons)
        self.shader_status = make_label("", "muted", wrap=True)
        shaders.body.addWidget(self.shader_status)
        self.body.addWidget(shaders)

        # ---- честно о пределах ----
        honest = Card(spacing=6)
        honest.body.addWidget(make_label("Что NOVA не делает", "h2"))
        for line in (
                "Не обещает конкретный прирост FPS: режим производительности убирает помехи (энергосбережение, фоновая "
                "запись), но не заменяет мощность видеокарты и процессора. Эффект зависит от ПК и игры.",
                "Не «чистит оперативную память», не отключает службы и не меняет реестр с правами администратора — "
                "такие приёмы либо бесполезны, либо рискованны.",
                "Не вмешивается в процессы игр и не перехватывает клавиши — поэтому античиты не должны возражать.",
                "Для максимального FPS также помогают: свежий драйвер видеокарты, закрытые лишние программы, "
                "разумные настройки графики в самой игре."):
            honest.body.addWidget(make_label("•  " + line, "muted", wrap=True))
        self.body.addWidget(honest)
        self.finish()

        self._sync_tweaks()

    # ------------------------------------------------------------------ настройки Windows
    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._sync_tweaks()

    def _sync_tweaks(self) -> None:
        available = self._tweaks.available
        self.tweaks_unavailable.setText("" if available else "Настройки Windows доступны только в Windows.")
        self.tweaks_unavailable.setVisible(not available)
        for tweak_id, toggle in self._toggles.items():
            toggle.setEnabled(available)
            try:
                on = available and self._tweaks.is_on(tweak_id)
            except tweaks.TweakError:
                on = False
            toggle.set_checked_silent(on)
            note = self._tweak_notes[tweak_id]
            if not available:
                note.setText("")
            elif on and self._tweaks.has_backup(tweak_id):
                note.setText("Включено NOVA. Выключатель вернёт прежнее значение.")
            elif on:
                note.setText("Уже включено в Windows, NOVA это не меняла — изменить можно в параметрах Windows.")
            else:
                note.setText("Сейчас выключено.")

    def _on_tweak(self, tweak_id: str, checked: bool) -> None:
        notice = self._ctx.library.notice
        title = tweaks.TWEAKS[tweak_id].title
        try:
            if checked:
                self._tweaks.enable(tweak_id)
                notice.emit("info", f"Включено: {title}.")
            elif not self._tweaks.has_backup(tweak_id):
                notice.emit("info", f"«{title}» включена в Windows не через NOVA, поэтому NOVA её не выключает — "
                                    "это можно сделать в параметрах Windows.")
            else:
                failed = self._tweaks.disable(tweak_id)
                if failed:
                    notice.emit("warning", f"«{title}»: не удалось вернуть параметров: {failed}.")
                else:
                    notice.emit("info", f"Выключено: {title}. Прежние значения возвращены.")
        except tweaks.TweakError as exc:
            notice.emit("error", str(exc))
        self._sync_tweaks()

    # ------------------------------------------------------------------ временные файлы
    def _scan_temp(self) -> None:
        self.btn_scan.setEnabled(False)
        self.btn_clean.setEnabled(False)
        self.temp_status.setText("Считаю временные файлы…")
        self._runner.submit(cleanup.scan_temp, self._on_scanned)

    def _on_scanned(self, scan: cleanup.TempScan | None, error: Exception | None) -> None:
        self.btn_scan.setEnabled(True)
        if error is not None or scan is None:
            self._scan = None
            self.temp_status.setText("Не удалось проверить папку временных файлов.")
            return
        self._scan = scan
        count = len(scan.files)
        if not count:
            self.temp_status.setText(f"Папка {scan.root}: нечего очищать (файлов старше часа нет).")
            return
        more = " (показана часть — проверьте ещё раз после очистки)" if scan.truncated else ""
        self.temp_status.setText(f"Папка {scan.root}: {count} {plural_ru(count, 'файл', 'файла', 'файлов')}, "
                                 f"{fmt_bytes(scan.total_bytes)} можно удалить{more}.")
        self.btn_clean.setEnabled(True)

    def _clean_temp(self) -> None:
        scan = self._scan
        if scan is None or not scan.files:
            return
        count = len(scan.files)
        if not confirm(self, "Очистить временные файлы?",
                       f"Будет удалено файлов: {count} ({fmt_bytes(scan.total_bytes)}) из папки\n{scan.root}\n\n"
                       "Это временные файлы программ старше часа. Документы и личные файлы не затрагиваются.",
                       "Очистить"):
            return
        self.btn_clean.setEnabled(False)
        self.btn_scan.setEnabled(False)
        self.temp_status.setText("Очищаю…")
        self._runner.submit(lambda: cleanup.clean_files(scan), self._on_cleaned)

    def _on_cleaned(self, result: cleanup.CleanResult | None, error: Exception | None) -> None:
        self.btn_scan.setEnabled(True)
        self._scan = None
        if error is not None or result is None:
            self.temp_status.setText("Не удалось выполнить очистку.")
            return
        text = f"Удалено файлов: {result.deleted}, освобождено {fmt_bytes(result.freed_bytes)}."
        if result.failed:
            text += f" Пропущено {result.failed} — они сейчас используются программами."
        self.temp_status.setText(text)
        self._ctx.library.notice.emit("info", text)


    # ------------------------------------------------------------------ кэш шейдеров GPU
    def _scan_shader_cache(self) -> None:
        self.btn_scan_shaders.setEnabled(False)
        self.btn_clean_shaders.setEnabled(False)
        self.shader_status.setText("Проверяю кэш шейдеров…")
        self._runner.submit(cleanup.scan_shader_cache, self._on_shader_scanned)

    def _on_shader_scanned(self, scan: cleanup.ShaderCacheScan | None, error: Exception | None) -> None:
        self.btn_scan_shaders.setEnabled(True)
        if error is not None or scan is None:
            self._shader_scan = None
            self.shader_status.setText("Не удалось проверить кэш видеокарты.")
            return
        self._shader_scan = scan
        if not scan.roots:
            self.shader_status.setText("Известные папки кэша не найдены. Возможно, драйвер ещё не создавал их.")
            return
        if not scan.files:
            self.shader_status.setText("Старых файлов не найдено. Файлы моложе 14 дней NOVA не трогает.")
            return
        tail = " Показана только часть файлов." if scan.truncated else ""
        self.shader_status.setText(
            f"Найдено {len(scan.files)} старых файлов · {fmt_bytes(scan.total_bytes)} в {len(scan.roots)} папках." + tail)
        self.btn_clean_shaders.setEnabled(True)

    def _clean_shader_cache(self) -> None:
        scan = self._shader_scan
        if scan is None or not scan.files:
            return
        if not confirm(
                self, "Очистить старый кэш шейдеров?",
                f"Будет удалено до {len(scan.files)} файлов ({fmt_bytes(scan.total_bytes)}) в пользовательских папках кэша GPU.\n\n"
                "Это не гарантирует прирост FPS. Игры могут пересобрать шейдеры, и первые запуски после очистки "
                "могут временно подтормаживать. Продолжить?", "Очистить кэш"):
            return
        self.btn_clean_shaders.setEnabled(False)
        self.btn_scan_shaders.setEnabled(False)
        self.shader_status.setText("Очищаю выбранные файлы…")
        self._runner.submit(lambda: cleanup.clean_shader_cache(scan), self._on_shader_cleaned)

    def _on_shader_cleaned(self, result: cleanup.CleanResult | None, error: Exception | None) -> None:
        self.btn_scan_shaders.setEnabled(True)
        self._shader_scan = None
        if error is not None or result is None:
            self.shader_status.setText("Не удалось очистить кэш шейдеров.")
            return
        message = f"Удалено {result.deleted} файлов · освобождено {fmt_bytes(result.freed_bytes)}."
        if result.failed:
            message += f" Не удалось удалить: {result.failed}."
        self.shader_status.setText(message)
        self._ctx.library.notice.emit("info", message)
