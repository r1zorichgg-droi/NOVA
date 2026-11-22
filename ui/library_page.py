"""Страницы «Игры» и «Приложения»: сетка карточек, поиск, автопоиск, ручное добавление и страница карточки.

Карточка: значок или обложка, кнопка «Запустить» и меню «⋯» (оптимизация, изменить, закрепить в острове,
удалить из NOVA). «Удалить из NOVA» убирает только карточку — файлы на компьютере не затрагиваются.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit, QPushButton, QVBoxLayout, QWidget)

from app_context import AppContext
from database.models import LibraryItem
from launcher.library_manager import EXE_SUFFIXES, ScanSummary
from launcher.session import build_watch
from system import winapi
from system.fmt import fmt_duration, fmt_when, plural_ru
from ui.item_card import CoverWidget, ItemCard, meta_text
from ui.item_dialogs import EditItemDialog, HiddenDialog, OptimizeDialog, confirm
from ui.fps_panel import FpsPanel
from ui.game_profiles import GameBoostWidget, GameProfilesWidget
from ui.widgets import Card, ElidedLabel, EmptyState, HLine, Page, Stat, chip_button, clear_layout, make_label

ADD_FILTER = "Программы и ярлыки (*.exe *.lnk *.bat *.cmd);;Все файлы (*)"
DROP_SUFFIXES = (*EXE_SUFFIXES, ".lnk")
MAX_DROPPED = 20
ALL_CATEGORIES = ""


@dataclass(frozen=True)
class KindSpec:
    title: str
    subtitle: str
    icon: str
    search_hint: str
    add_text: str
    play_text: str
    big_text: str
    empty_title: str
    empty_text: str
    one: str
    few: str
    many: str


SPECS = {
    "game": KindSpec(
        title="Игры",
        subtitle="Ваша коллекция игр: найденные автоматически и добавленные вручную. Игры запускаются кнопкой «Запустить», "
                 "а в меню «⋯» — оптимизация, изменение и удаление из NOVA.",
        icon="games", search_hint="Поиск по играм", add_text="Добавить игру…",
        play_text="Запустить", big_text="ИГРАТЬ",
        empty_title="Игр пока нет",
        empty_text="Нажмите «Найти автоматически» — NOVA найдёт установленные игры Steam, Epic Games, GOG и Minecraft. "
                   "Любую другую игру можно добавить вручную: кнопка «Добавить игру…» или перетащите EXE-файл в это окно.",
        one="игра", few="игры", many="игр"),
    "app": KindSpec(
        title="Приложения",
        subtitle="Программы для быстрого запуска: найденные в меню «Пуск» и Microsoft Store или добавленные вручную. "
                 "Нужные можно закрепить во вкладке «Приложения» в Dynamic Island.",
        icon="apps", search_hint="Поиск по программам", add_text="Добавить программу…",
        play_text="Запустить", big_text="ЗАПУСТИТЬ",
        empty_title="Программ пока нет",
        empty_text="Нажмите «Найти автоматически» — NOVA найдёт программы из меню «Пуск» и Microsoft Store. "
                   "Любую другую программу можно добавить вручную: кнопка «Добавить программу…» или перетащите EXE-файл "
                   "в это окно.",
        one="программа", few="программы", many="программ"),
}


class CardGrid(QWidget):
    """Сетка карточек, которая сама подбирает число столбцов под ширину окна."""
    GAP = 16

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._lay = QGridLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(self.GAP)
        self._lay.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._cards: list[QWidget] = []
        self._cols = 0

    def columns(self) -> int:
        return max(1, (self.width() + self.GAP) // (ItemCard.WIDTH + self.GAP))

    def set_cards(self, cards: list[QWidget]) -> None:
        clear_layout(self._lay, delete=False)
        for old in self._cards:
            old.hide()
            old.setParent(None)
            old.deleteLater()
        self._cards = cards
        self._place(force=True)

    def _place(self, force: bool = False) -> None:
        cols = self.columns()
        if cols == self._cols and not force:
            return
        self._cols = cols
        clear_layout(self._lay, delete=False)
        for i, card in enumerate(self._cards):
            self._lay.addWidget(card, i // cols, i % cols)
            card.show()

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self._place()


def dropped_paths(mime) -> list[str]:  # noqa: ANN001
    """Локальные файлы .exe/.lnk/.bat/.cmd из перетаскивания."""
    if mime is None or not mime.hasUrls():
        return []
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    return [p for p in paths if p.lower().endswith(DROP_SUFFIXES)][:MAX_DROPPED]


class LibraryPage(Page):
    def __init__(self, ctx: AppContext, kind: str) -> None:
        spec = SPECS[kind]
        super().__init__(spec.title, spec.subtitle)
        self._spec = spec
        self._ctx, self._kind, self._lib, self._icons = ctx, kind, ctx.library, ctx.icons
        self._filter = "all"                 # all | fav | recent
        self._category = ALL_CATEGORIES
        self._detail_id: int | None = None
        self._dirty = True
        self._refresh_queued = False
        self.setAcceptDrops(True)

        # ---- вид «список» ----
        self.list_view = QWidget()
        lv = QVBoxLayout(self.list_view)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(14)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self._spec.search_hint)
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(220)
        self.search.setMaximumWidth(340)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(180)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.btn_scan = QPushButton("Найти автоматически")
        self.btn_scan.setToolTip("Поиск установленных игр и программ на этом ПК. Найденное добавляется в библиотеку, "
                                 "то, что вы убрали, не возвращается.")
        self.btn_scan.clicked.connect(lambda: self._lib.scan(manual=True))
        self.btn_add = QPushButton(self._spec.add_text)
        self.btn_add.setObjectName("primary")
        self.btn_add.clicked.connect(self._add_manual)
        self.btn_hidden = QPushButton("Убранные")
        self.btn_hidden.setObjectName("flat")
        self.btn_hidden.clicked.connect(self._open_hidden)
        bar.addWidget(self.search)
        bar.addStretch(1)
        bar.addWidget(self.btn_hidden)
        bar.addWidget(self.btn_scan)
        bar.addWidget(self.btn_add)
        lv.addLayout(bar)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        self._chips = {
            "all": chip_button("Все", True), "fav": chip_button("Избранное", False),
            "recent": chip_button("Недавние", False)}
        for key, chip in self._chips.items():
            chip.clicked.connect(lambda _c=False, k=key: self._set_filter(k))
            chips.addWidget(chip)
        self.category_box = QComboBox()
        self.category_box.setMinimumWidth(170)
        self.category_box.currentIndexChanged.connect(self._on_category)
        chips.addSpacing(8)
        chips.addWidget(self.category_box)
        chips.addStretch(1)
        self.count_label = make_label("", "caption")
        chips.addWidget(self.count_label)
        lv.addLayout(chips)

        self.status = make_label("", "muted", wrap=True)
        self.status.hide()
        lv.addWidget(self.status)

        self.grid = CardGrid()
        lv.addWidget(self.grid)
        self.empty_none = EmptyState(self._spec.icon, self._spec.empty_title, self._spec.empty_text)
        self.empty_filtered = EmptyState(self._spec.icon, "Ничего не найдено", "Измените поиск или фильтр.")
        lv.addWidget(self.empty_none)
        lv.addWidget(self.empty_filtered)
        self.fps_panel: FpsPanel | None = None
        self.game_profiles: GameProfilesWidget | None = None
        self.game_boost: GameBoostWidget | None = None
        if kind == "game":
            self.fps_panel = FpsPanel(self, ctx)
            self.body.addWidget(self.fps_panel)
            self.game_boost = GameBoostWidget(ctx, self)
            self.body.addWidget(self.game_boost)
            self.game_profiles = GameProfilesWidget(ctx, self)
            self.body.addWidget(self.game_profiles)

        self.body.addWidget(self.list_view)

        # ---- вид «карточка» ----
        self.detail_view = QWidget()
        self._detail_lay = QVBoxLayout(self.detail_view)
        self._detail_lay.setContentsMargins(0, 0, 0, 0)
        self._detail_lay.setSpacing(16)
        self.detail_view.hide()
        self.body.addWidget(self.detail_view)
        self.finish()

        lib = self._lib
        lib.changed.connect(self._on_changed)
        lib.scan_state.connect(self._on_scan_state)
        lib.scan_done.connect(self._on_scan_done)
        self._on_scan_state(lib.scanning)
        self.refresh()

    def shutdown(self) -> None:
        """Останавливает игровой FPS-сборщик перед выходом из NOVA."""
        if self.fps_panel is not None:
            self.fps_panel.shutdown()

    # ------------------------------------------------------------------ обновление
    def _on_changed(self) -> None:
        if not self.isVisible():
            self._dirty = True
            return
        if not self._refresh_queued:        # несколько изменений подряд (поиск, запуск) — одна перерисовка
            self._refresh_queued = True
            QTimer.singleShot(0, self._flush_refresh)

    def _flush_refresh(self) -> None:
        self._refresh_queued = False
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        table = self._lib.table(self._kind)
        total = table.count()
        self._fill_categories(table.categories())
        query = self.search.text()
        favorites = self._filter == "fav"
        items = table.items(query, category=self._category or None, favorites_only=favorites)
        if self._filter == "recent":
            items = sorted((i for i in items if i.last_played), key=lambda i: i.last_played or 0.0, reverse=True)
        pinned = set(self._lib.pinned())
        cards = []
        for item in items:
            card = ItemCard(self._kind, item, self._icons, self._lib.is_running(self._kind, item.id),
                            (self._kind, item.id) in pinned)
            card.action.connect(self._on_action)
            cards.append(card)
        self.grid.set_cards(cards)
        self.grid.setVisible(bool(cards))
        self.empty_none.setVisible(total == 0)
        self.empty_filtered.setVisible(total > 0 and not cards)
        spec = self._spec
        self.count_label.setText(f"{total} {plural_ru(total, spec.one, spec.few, spec.many)}" if total else "")
        hidden = len(table.hidden_items())
        self.btn_hidden.setVisible(hidden > 0)
        self.btn_hidden.setText(f"Убранные ({hidden})")
        if self._detail_id is not None:
            self._show_detail(self._detail_id, keep_scroll=True)

    def _fill_categories(self, categories: list[str]) -> None:
        self.category_box.blockSignals(True)
        self.category_box.clear()
        self.category_box.addItem("Все категории", ALL_CATEGORIES)
        for name in categories:
            self.category_box.addItem(name, name)
        index = self.category_box.findData(self._category)
        if index < 0:
            self._category, index = ALL_CATEGORIES, 0
        self.category_box.setCurrentIndex(index)
        self.category_box.setVisible(bool(categories))
        self.category_box.blockSignals(False)

    def _set_filter(self, key: str) -> None:
        self._filter = key
        for k, chip in self._chips.items():
            chip.setChecked(k == key)
        self.refresh()

    def _on_category(self, _index: int) -> None:
        self._category = str(self.category_box.currentData() or ALL_CATEGORIES)
        self.refresh()

    # ------------------------------------------------------------------ автопоиск и добавление
    def _on_scan_state(self, scanning: bool) -> None:
        self.btn_scan.setEnabled(not scanning)
        self.btn_scan.setText("Идёт поиск…" if scanning else "Найти автоматически")
        if scanning:
            self._set_status("Идёт поиск игр и программ на этом ПК…")

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _on_scan_done(self, summary: ScanSummary) -> None:
        res = summary.games if self._kind == "game" else summary.apps
        spec = self._spec
        parts = []
        if res.added:
            parts.append(f"добавлено: {res.added}")
        if res.restored:
            parts.append(f"снова найдено: {res.restored}")
        if res.missing:
            parts.append(f"больше не найдено на ПК: {res.missing}")
        if self._kind == "app" and summary.pruned:
            parts.append(f"убрано лишнего: {summary.pruned}")
        if not parts:
            if not summary.manual:
                self._set_status("")
                return
            parts.append(f"новых {spec.many} не найдено")
        lines = ["Поиск завершён — " + ", ".join(parts) + "."]
        if summary.manual:
            lines.extend(summary.notes)
        self._set_status("\n".join(lines))

    def _add_manual(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self._spec.add_text.rstrip("…"), "", ADD_FILTER)
        self._add_paths(paths)

    def _add_paths(self, paths: list[str]) -> None:
        if not paths:
            return
        done, errors = 0, []
        last = ""
        for path in paths[:MAX_DROPPED]:
            ok, message = self._lib.add_manual(self._kind, os.path.normpath(path))
            last = message
            if ok:
                done += 1
            elif "уже есть" not in message:
                errors.append(message)
        if len(paths) == 1:
            self._lib.notice.emit("info" if done or "уже есть" in last else "error", last)
        else:
            text = f"Добавлено: {done} из {len(paths)}."
            if errors:
                text += " " + errors[0]
            self._lib.notice.emit("info" if done else "warning", text)

    def dragEnterEvent(self, event) -> None:  # noqa: N802, ANN001
        if dropped_paths(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802, ANN001
        paths = dropped_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self._add_paths(paths)

    def _open_hidden(self) -> None:
        HiddenDialog(self._lib, self._kind, self).exec()

    # ------------------------------------------------------------------ действия карточки
    def _item(self, item_id: int) -> LibraryItem | None:
        return self._lib.table(self._kind).get(item_id)

    def _on_action(self, action: str, _kind: str, item_id: int) -> None:
        item = self._item(item_id)
        if item is None:
            return
        lib, kind = self._lib, self._kind
        if action == "launch":
            lib.launch(kind, item_id)
        elif action == "open":
            self._show_detail(item_id)
        elif action == "optimize":
            OptimizeDialog(lib, kind, item, self._icons, self).exec()
        elif action == "edit":
            EditItemDialog(lib, kind, item, self).exec()
        elif action == "favorite":
            lib.toggle_favorite(kind, item_id)
        elif action == "pin":
            self._toggle_pin(item)
        elif action == "folder":
            self._reveal(item)
        elif action == "remove":
            self._remove(item)

    def _toggle_pin(self, item: LibraryItem) -> None:
        lib = self._lib
        if lib.is_pinned(self._kind, item.id):
            lib.unpin(self._kind, item.id)
            lib.notice.emit("info", f"«{item.title}» убрана из острова.")
        elif lib.pin(self._kind, item.id):
            lib.notice.emit("info", f"«{item.title}» закреплена в острове — её видно во вкладке «Приложения».")
        else:
            lib.notice.emit("warning", "В острове можно закрепить не больше 10 программ — открепите лишнюю.")
        self.refresh()

    def _reveal(self, item: LibraryItem) -> None:
        folder = os.path.dirname(item.exe_path) if item.exe_path else item.install_dir
        if item.exe_path and os.path.isfile(item.exe_path) and winapi.reveal_in_explorer(item.exe_path):
            return
        if folder and os.path.isdir(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
        else:
            self._lib.notice.emit("warning", "Папка не найдена — возможно, программа удалена с компьютера.")

    def _remove(self, item: LibraryItem) -> None:
        if item.source == "manual":
            text = (f"Карточка «{item.title}» будет удалена из NOVA.\n\nСама программа и её файлы на компьютере "
                    "не удаляются и не изменяются.")
        else:
            text = (f"Карточка «{item.title}» будет убрана из NOVA, и автопоиск её больше не добавит "
                    "(вернуть можно в разделе «Убранные»).\n\nСама программа и её файлы на компьютере "
                    "не удаляются и не изменяются.")
        if not confirm(self, "Удалить из NOVA?", text, "Удалить из NOVA"):
            return
        self._lib.notice.emit("info", self._lib.remove(self._kind, item.id))
        if self._detail_id == item.id:
            self._show_list()

    # ------------------------------------------------------------------ страница карточки
    def _show_list(self) -> None:
        self._detail_id = None
        self.title_label.setVisible(True)
        self.subtitle_label.setVisible(True)
        self.detail_view.hide()
        self.list_view.show()
        self.refresh()

    def _show_detail(self, item_id: int, keep_scroll: bool = False) -> None:
        item = self._item(item_id)
        if item is None or item.hidden:
            self._show_list()
            return
        scroll_pos = self.scroll.verticalScrollBar().value()
        self._detail_id = item_id
        clear_layout(self._detail_lay)
        self._build_detail(item)
        self.title_label.setVisible(False)
        self.subtitle_label.setVisible(False)
        self.list_view.hide()
        self.detail_view.show()
        self.scroll.verticalScrollBar().setValue(scroll_pos if keep_scroll else 0)

    def _build_detail(self, item: LibraryItem) -> None:
        lib, kind, spec, lay = self._lib, self._kind, self._spec, self._detail_lay
        running = lib.is_running(kind, item.id)
        missing = bool(item.opts.get("uninstalled"))

        back = QPushButton("←  Назад")
        back.setObjectName("flat")
        back.setCursor(Qt.CursorShape.PointingHandCursor)
        back.clicked.connect(self._show_list)
        lay.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)

        head = QHBoxLayout()
        head.setSpacing(24)
        cover = CoverWidget(kind, item, self._icons, QSize(320, 200), radius=18, running=running)
        cover.favorite_toggled.connect(lambda: lib.toggle_favorite(kind, item.id))
        head.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)

        info = QVBoxLayout()
        info.setSpacing(6)
        title = make_label(item.title, "h1", wrap=True)
        info.addWidget(title)
        info.addWidget(make_label(meta_text(item), "muted"))
        if item.description:
            info.addWidget(make_label(item.description, "", wrap=True))
        info.addSpacing(8)

        buttons = QHBoxLayout()
        buttons.setSpacing(10)
        play = QPushButton("Запущено" if running else ("Не найдена на ПК" if missing else spec.big_text))
        play.setObjectName("big")
        play.setCursor(Qt.CursorShape.PointingHandCursor)
        play.setMinimumSize(220, 52)
        play.setEnabled(not running and not missing)
        play.clicked.connect(lambda: lib.launch(kind, item.id))
        buttons.addWidget(play)
        for text, action in (("Оптимизация…", "optimize"), ("Изменить…", "edit")):
            btn = QPushButton(text)
            btn.setFixedHeight(52)
            btn.clicked.connect(lambda _c=False, a=action: self._on_action(a, kind, item.id))
            buttons.addWidget(btn)
        buttons.addStretch(1)
        info.addLayout(buttons)
        info.addWidget(make_label(self._boost_line(item, missing), "caption", wrap=True))
        info.addStretch(1)
        head.addLayout(info, 1)
        lay.addLayout(head)

        # ---- статистика ----
        table = lib.table(kind)
        stats = Card(spacing=12)
        stats.body.addWidget(make_label("Статистика", "h2"))
        row = QHBoxLayout()
        row.setSpacing(32)
        for caption, value in (
                ("Запусков из NOVA", str(item.play_count)),
                ("Последний запуск", fmt_when(item.last_played)),
                ("Время в игре" if kind == "game" else "Время работы", fmt_duration(table.playtime(item.id)))):
            row.addWidget(Stat(caption, value))
        row.addStretch(1)
        stats.body.addLayout(row)
        stats.body.addWidget(make_label(
            "NOVA считает только запуски, сделанные из неё, и только то время, пока она видит запущенный процесс.",
            "caption", wrap=True))
        lay.addWidget(stats)

        # ---- расположение ----
        where = Card(spacing=8)
        where.body.addWidget(make_label("Расположение", "h2"))
        method = "через лаунчер (" + meta_text(item).split(" · ")[0] + ")" if item.launch_uri else "напрямую, файлом программы"
        where.body.addWidget(make_label(f"Запуск: {method}", "muted", wrap=True))
        if item.exe_path:
            where.body.addWidget(ElidedLabel(f"Файл: {item.exe_path}", "muted"))
        if item.install_dir:
            where.body.addWidget(ElidedLabel(f"Папка: {item.install_dir}", "muted"))
        if item.args:
            where.body.addWidget(ElidedLabel(f"Параметры: {item.args}", "muted"))
        lay.addWidget(where)

        # ---- история ----
        history = table.history(item.id, limit=8)
        if history:
            hist = Card(spacing=6)
            hist.body.addWidget(make_label("Последние запуски", "h2"))
            for i, rec in enumerate(history):
                if i:
                    hist.body.addWidget(HLine())
                started, ended = rec.get("started_at"), rec.get("ended_at")
                length = fmt_duration(ended - started) if ended and started and ended >= started else "длительность не определена"
                hist.body.addWidget(make_label(f"{fmt_when(started).capitalize()}  ·  {length}", "muted"))
            lay.addWidget(hist)

        # ---- прочее ----
        more = QHBoxLayout()
        more.setSpacing(10)
        pinned = lib.is_pinned(kind, item.id)
        for text, action in (
                ("Убрать из избранного" if item.favorite else "В избранное", "favorite"),
                ("Открепить от острова" if pinned else "Закрепить в острове", "pin"),
                ("Показать в проводнике", "folder")):
            if action == "folder" and not (item.exe_path or item.install_dir):
                continue
            btn = QPushButton(text)
            btn.clicked.connect(lambda _c=False, a=action: self._on_action(a, kind, item.id))
            more.addWidget(btn)
        remove = QPushButton("Удалить из NOVA…")
        remove.setObjectName("danger")
        remove.clicked.connect(lambda: self._on_action("remove", kind, item.id))
        more.addStretch(1)
        more.addWidget(remove)
        lay.addLayout(more)
        lay.addWidget(make_label("«Удалить из NOVA» убирает только карточку — сама программа и её файлы остаются на компьютере.",
                                 "caption", wrap=True))
        lay.addStretch(1)

    def _boost_line(self, item: LibraryItem, missing: bool) -> str:
        if missing:
            return ("NOVA не нашла эту программу на компьютере — возможно, она удалена. "
                    "Если файл переехал, укажите новый в «Изменить».")
        if not build_watch(item).trackable:
            return ("Режим производительности недоступен: NOVA не может определить, когда программа закроется. "
                    "Укажите EXE в «Изменить».")
        if self._lib.boost_wanted(self._kind, item):
            return "При запуске из NOVA включится режим производительности; после выхода всё вернётся как было."
        return "Режим производительности при запуске выключен — включить можно в «Оптимизация…»."
