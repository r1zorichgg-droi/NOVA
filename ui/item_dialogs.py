"""Окна карточки: «Изменить», «Оптимизация», «Скрытые», «Закрыть фоновые программы», выбор программ для острова."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget)

from database.models import LibraryItem
from launcher.categories import GAME_CATEGORY_SUGGESTIONS
from launcher.launch import PRIORITY_LABELS
from launcher.library_manager import MAX_PINS, LibraryManager
from launcher.session import build_watch
from system import processes
from ui.icon_cache import IconCache
from ui.item_card import source_label
from ui.widgets import Card, HLine, Segmented, Toggle, make_label, setting_row
from ui.workers import AsyncRunner

APP_CATEGORY_SUGGESTIONS = ["Браузеры", "Общение", "Музыка и видео", "Разработка", "Офис", "Творчество",
                            "Утилиты", "Игровые лаунчеры"]
EXE_FILTER = "Программы (*.exe *.bat *.cmd);;Все файлы (*)"
IMAGE_FILTER = "Картинки (*.png *.jpg *.jpeg *.webp *.bmp *.ico);;Все файлы (*)"


def confirm(parent: QWidget | None, title: str, text: str, ok_text: str = "Да") -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(ok_text, QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def info(parent: QWidget | None, title: str, text: str, warning: bool = False) -> None:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information)
    box.setWindowTitle(title)
    box.setText(text)
    box.addButton("Понятно", QMessageBox.ButtonRole.AcceptRole)
    box.exec()


def _field(label: str, widget: QWidget, hint: str = "") -> QWidget:
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    lay.addWidget(make_label(label, "h3"))
    lay.addWidget(widget)
    if hint:
        lay.addWidget(make_label(hint, "caption", wrap=True))
    return box


def _path_row(edit: QLineEdit, browse: QPushButton, extra: QPushButton | None = None) -> QWidget:
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.addWidget(edit, 1)
    lay.addWidget(browse)
    if extra is not None:
        lay.addWidget(extra)
    return row


# ============================================================================================ изменить
class EditItemDialog(QDialog):
    def __init__(self, manager: LibraryManager, kind: str, item: LibraryItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager, self._kind, self._item = manager, kind, item
        self.setWindowTitle("Изменить карточку")
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label("Изменить карточку", "h2"))
        lay.addWidget(make_label(f"Источник: {source_label(item)}. Эти изменения хранятся только в NOVA — "
                                 "файлы на компьютере не затрагиваются.", "caption", wrap=True))

        self.title_edit = QLineEdit(item.title)
        lay.addWidget(_field("Название", self.title_edit))

        self.category = QComboBox()
        self.category.setEditable(True)
        suggestions = GAME_CATEGORY_SUGGESTIONS if kind == "game" else APP_CATEGORY_SUGGESTIONS
        known = manager.table(kind).categories()
        self.category.addItems(["", *dict.fromkeys([*known, *suggestions])])
        self.category.setCurrentText(item.category)
        lay.addWidget(_field("Категория", self.category, "Можно выбрать из списка или написать свою."))

        self.description = QPlainTextEdit(item.description)
        self.description.setFixedHeight(70)
        lay.addWidget(_field("Описание", self.description))

        self.exe_edit = QLineEdit(item.exe_path)
        browse_exe = QPushButton("Обзор…")
        browse_exe.clicked.connect(self._browse_exe)
        hint = ("Файл для запуска. " if not item.launch_uri else
                "Игра запускается через её лаунчер (Steam, Epic и т.п.) — так работают оверлей и облако. "
                "EXE нужен для значка и чтобы NOVA видела, что игра запущена. ")
        lay.addWidget(_field("Файл программы (EXE)", _path_row(self.exe_edit, browse_exe), hint.strip()))

        self.args_edit = QLineEdit(item.args)
        self.args_edit.setPlaceholderText("Например: -windowed -novid")
        lay.addWidget(_field("Параметры запуска", self.args_edit, "Необязательно. Используются при прямом запуске EXE."))

        self.cover_edit = QLineEdit(item.image_path)
        browse_cover = QPushButton("Обзор…")
        browse_cover.clicked.connect(self._browse_cover)
        clear_cover = QPushButton("Убрать")
        clear_cover.clicked.connect(lambda: self.cover_edit.setText(""))
        lay.addWidget(_field("Обложка", _path_row(self.cover_edit, browse_cover, clear_cover),
                             "Картинка с вашего компьютера. Без обложки показывается значок программы."))

        self.error = make_label("", "warn", wrap=True)
        self.error.hide()
        lay.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    def _browse_exe(self) -> None:
        start = os.path.dirname(self.exe_edit.text()) if self.exe_edit.text() else ""
        path, _ = QFileDialog.getOpenFileName(self, "Выберите программу", start, EXE_FILTER)
        if path:
            self.exe_edit.setText(os.path.normpath(path))

    def _browse_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите обложку", "", IMAGE_FILTER)
        if path:
            self.cover_edit.setText(os.path.normpath(path))

    def _fail(self, text: str) -> None:
        self.error.setText(text)
        self.error.show()

    def _save(self) -> None:
        title = self.title_edit.text().strip()
        exe, cover = self.exe_edit.text().strip().strip('"'), self.cover_edit.text().strip().strip('"')
        if not title:
            self._fail("Введите название.")
            return
        if exe and not os.path.isfile(exe):
            self._fail(f"Файл не найден: {exe}")
            return
        if exe and not exe.lower().endswith((".exe", ".bat", ".cmd", ".com")):
            self._fail("Нужен исполняемый файл (.exe).")
            return
        if cover and not os.path.isfile(cover):
            self._fail(f"Картинка не найдена: {cover}")
            return
        if not exe and not self._item.launch_uri:
            self._fail("Укажите файл программы — иначе карточку нечем запускать.")
            return
        self._manager.update_item(
            self._kind, self._item.id, title=title, category=self.category.currentText().strip(),
            description=self.description.toPlainText().strip(), exe_path=exe, args=self.args_edit.text().strip(),
            image_path=cover)
        if exe and self._item.opts.get("uninstalled"):
            self._manager.set_option(self._kind, self._item.id, "uninstalled", None)
        self.accept()


# ============================================================================================ оптимизация
class OptimizeDialog(QDialog):
    def __init__(self, manager: LibraryManager, kind: str, item: LibraryItem, icons: IconCache,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager, self._kind, self._item = manager, kind, item
        self.setWindowTitle("Оптимизация")
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)

        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(icons.pixmap(kind, item, 44))
        icon.setFixedSize(44, 44)
        head.addWidget(icon)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(make_label(item.title, "h2"))
        titles.addWidget(make_label("Оптимизация при запуске из NOVA", "caption"))
        head.addLayout(titles, 1)
        lay.addLayout(head)

        trackable = build_watch(item).trackable
        opts = item.opts
        card = Card(margins=(18, 12, 18, 12), spacing=2)

        self.boost = Toggle(manager.boost_wanted(kind, item) and trackable)
        self.boost.setEnabled(trackable)
        boost_desc = ("Включает схему питания «Максимальная производительность», «Игровой режим» Windows и выключает "
                      "фоновую запись; после выхода из игры всё возвращается. Прирост FPS зависит от ПК и игры — "
                      "обещать конкретные цифры нельзя.")
        if not trackable:
            boost_desc = ("Недоступно: NOVA не может определить, когда эта программа закроется, и не смогла бы вернуть "
                          "настройки. Укажите EXE в «Изменить».")
        card.body.addWidget(setting_row("Режим производительности при запуске", boost_desc, self.boost))
        card.body.addWidget(HLine())

        direct = not item.launch_uri
        self.priority = Segmented(
            [("", "Как в настройках")] + [(k, v) for k, v in PRIORITY_LABELS.items()], str(opts.get("priority") or ""))
        self.priority.setEnabled(direct and bool(item.exe_path))
        prio_desc = ("Приоритет процессора, который получит программа при запуске. «Высокий» может ухудшить работу "
                     "других программ.")
        if not direct:
            prio_desc = ("Эта игра запускается через её лаунчер (Steam, Epic, Store), поэтому процесс создаёт он, а NOVA "
                         "в процессы игр не вмешивается — так безопаснее для античитов. Приоритет не меняется.")
        card.body.addWidget(setting_row("Приоритет процесса", prio_desc, self.priority))
        card.body.addWidget(HLine())

        state = manager.exe_tweak_state(item)
        has_exe = state is not None
        gpu_now, fso_now = state if state else (False, False)
        self._gpu_before, self._fso_before = gpu_now, fso_now
        self.gpu = Toggle(gpu_now)
        self.gpu.setEnabled(has_exe)
        card.body.addWidget(setting_row(
            "Мощная видеокарта для этой программы",
            "Для ноутбуков и ПК с двумя видеокартами: Windows будет запускать программу на дискретной видеокарте "
            "(то же, что Параметры → Дисплей → Графика). С одной видеокартой эффекта нет."
            + ("" if has_exe else " Нужен EXE — укажите его в «Изменить»."), self.gpu))
        card.body.addWidget(HLine())
        self.fso = Toggle(fso_now)
        self.fso.setEnabled(has_exe)
        card.body.addWidget(setting_row(
            "Отключить полноэкранные оптимизации Windows",
            "Иногда убирает подвисания и проблемы с Alt+Tab в игре, но не всегда — результат зависит от игры. "
            "Включается и выключается этой же кнопкой.", self.fso))
        lay.addWidget(card)

        self.names = QLineEdit(", ".join(str(n) for n in (opts.get("process_names") or [])))
        self.names.setPlaceholderText("Например: game.exe, gamelauncher.exe")
        lay.addWidget(_field(
            "Имена процессов для отслеживания (необязательно)", self.names,
            "Если NOVA не замечает, что игра запущена или закрыта (так бывает с играми-обёртками), перечислите имена "
            "её процессов через запятую."))

        extra = QHBoxLayout()
        free = QPushButton("Закрыть фоновые программы…")
        free.clicked.connect(self._open_close_apps)
        extra.addWidget(free)
        extra.addStretch(1)
        lay.addLayout(extra)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        save = QPushButton("Сохранить")
        save.setObjectName("primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        lay.addLayout(buttons)

    def _open_close_apps(self) -> None:
        exclude = [d for d in (self._item.install_dir, os.path.dirname(self._item.exe_path)) if d]
        CloseAppsDialog(exclude, self).exec()

    def _save(self) -> None:
        m, kind, item = self._manager, self._kind, self._item
        if self.boost.isEnabled():
            m.table(kind).set_option(item.id, "boost", self.boost.isChecked())
        m.table(kind).set_option(item.id, "priority", str(self.priority.value()) or None)
        names = [n.strip() for n in self.names.text().split(",") if n.strip()]
        m.table(kind).set_option(item.id, "process_names", names or None)
        problem = ""
        if self.gpu.isEnabled() and (self.gpu.isChecked() != self._gpu_before or self.fso.isChecked() != self._fso_before):
            problem = m.apply_exe_tweaks(item, self.gpu.isChecked(), self.fso.isChecked())
        m.changed.emit()
        if problem:
            info(self, "Не все настройки применились", problem, warning=True)
        self.accept()


# ============================================================================================ скрытые
class HiddenDialog(QDialog):
    def __init__(self, manager: LibraryManager, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager, self._kind = manager, kind
        self.setWindowTitle("Убранные из NOVA")
        self.setMinimumSize(520, 420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label("Убранные из NOVA", "h2"))
        lay.addWidget(make_label("Автопоиск не добавляет эти карточки обратно. Файлы на компьютере никогда не затрагиваются.",
                                 "caption", wrap=True))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        restore = QPushButton("Вернуть в библиотеку")
        restore.setObjectName("primary")
        restore.clicked.connect(self._restore)
        forget = QPushButton("Забыть совсем")
        forget.clicked.connect(self._forget)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.accept)
        row.addWidget(restore)
        row.addWidget(forget)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        for item in self._manager.table(self._kind).hidden_items():
            entry = QListWidgetItem(f"{item.title}   ·   {source_label(item)}")
            entry.setData(Qt.ItemDataRole.UserRole, item.id)
            self.list.addItem(entry)

    def _selected_id(self) -> int | None:
        cur = self.list.currentItem()
        return int(cur.data(Qt.ItemDataRole.UserRole)) if cur is not None else None

    def _restore(self) -> None:
        item_id = self._selected_id()
        if item_id is not None:
            self._manager.unhide(self._kind, item_id)
            self._reload()

    def _forget(self) -> None:
        item_id = self._selected_id()
        if item_id is None:
            return
        if confirm(self, "Забыть карточку?", "Карточка и история её запусков будут удалены из базы NOVA. "
                   "Файлы на компьютере не затрагиваются. Автопоиск сможет найти программу заново.", "Забыть"):
            self._manager.forget(self._kind, item_id)
            self._reload()


# ============================================================================================ фоновые программы
class CloseAppsDialog(QDialog):
    """Показывает программы с окнами, которые можно закрыть, и закрывает только отмеченные — после подтверждения."""

    def __init__(self, exclude_dirs: list[str] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._exclude = exclude_dirs or []
        self._runner = AsyncRunner(self)
        self.setWindowTitle("Закрыть фоновые программы")
        self.setMinimumSize(560, 460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label("Закрыть фоновые программы", "h2"))
        lay.addWidget(make_label(
            "Отметьте программы, которые не нужны во время игры. NOVA попросит их закрыться — как при нажатии на крестик, "
            "так что они могут спросить про несохранённые данные. Системные процессы и сама NOVA в списке не показываются. "
            "Ничего не закрывается без вашей отметки.", "caption", wrap=True))
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        self.status = make_label("Загрузка списка…", "muted", wrap=True)
        lay.addWidget(self.status)
        row = QHBoxLayout()
        self.close_btn = QPushButton("Закрыть выбранные")
        self.close_btn.setObjectName("primary")
        self.close_btn.clicked.connect(self._close_selected)
        refresh = QPushButton("Обновить")
        refresh.clicked.connect(self._reload)
        done = QPushButton("Готово")
        done.clicked.connect(self.accept)
        row.addWidget(self.close_btn)
        row.addWidget(refresh)
        row.addStretch(1)
        row.addWidget(done)
        lay.addLayout(row)
        self._reload()

    def _reload(self) -> None:
        self.status.setText("Загрузка списка…")
        self.close_btn.setEnabled(False)
        self._runner.submit(lambda: processes.list_background_apps(self._exclude), self._on_loaded)

    def _on_loaded(self, apps: list[processes.BackgroundApp] | None, error: Exception | None) -> None:
        self.list.clear()
        if error is not None or apps is None:
            self.status.setText("Не удалось получить список программ.")
            return
        for app in apps:
            entry = QListWidgetItem(f"{app.name}   ·   {app.memory_mb:.0f} МБ")
            entry.setData(Qt.ItemDataRole.UserRole, app.pid)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(Qt.CheckState.Unchecked)
            entry.setToolTip(app.exe)
            self.list.addItem(entry)
        self.close_btn.setEnabled(bool(apps))
        self.status.setText("Нет программ, которые можно предложить закрыть." if not apps else
                            f"Найдено программ: {len(apps)}. Список отсортирован по занимаемой памяти.")

    def _checked(self) -> list[tuple[int, str]]:
        out = []
        for i in range(self.list.count()):
            entry = self.list.item(i)
            if entry.checkState() == Qt.CheckState.Checked:
                out.append((int(entry.data(Qt.ItemDataRole.UserRole)), entry.text().split("   ·")[0]))
        return out

    def _close_selected(self) -> None:
        chosen = self._checked()
        if not chosen:
            self.status.setText("Отметьте программы, которые нужно закрыть.")
            return
        names = ", ".join(n for _p, n in chosen[:6]) + ("…" if len(chosen) > 6 else "")
        if not confirm(self, "Закрыть программы?", f"NOVA попросит закрыться: {names}.\n\n"
                       "Программы могут спросить, сохранить ли данные.", "Закрыть"):
            return
        pids = [p for p, _n in chosen]
        processes.request_close(pids)
        self.status.setText("Отправлена просьба закрыться, проверяю результат…")
        self.close_btn.setEnabled(False)
        QTimer.singleShot(3500, lambda: self._after_close(pids))

    def _after_close(self, pids: list[int]) -> None:
        left = processes.still_running(pids)
        if left and confirm(
                self, "Не все программы закрылись",
                f"Не закрылось программ: {len(left)}. Обычно это значит, что они свернулись в трей или ждут вашего ответа.\n\n"
                "Завершить их принудительно? Несохранённые данные в них будут потеряны.", "Завершить принудительно"):
            processes.force_terminate(left)
        self._reload()


# ============================================================================================ программы для острова
class PinPickerDialog(QDialog):
    """Выбор программ и игр, которые показываются во вкладке «Приложения» острова."""

    def __init__(self, manager: LibraryManager, icons: IconCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._manager = manager
        self.setWindowTitle("Программы в острове")
        self.setMinimumSize(520, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        lay.addWidget(make_label("Программы в острове", "h2"))
        lay.addWidget(make_label(f"Отметьте до {MAX_PINS} программ или игр из вашей библиотеки.", "caption", wrap=True))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск")
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.list = QListWidget()
        self.list.setIconSize(QSize(28, 28))
        lay.addWidget(self.list, 1)
        self.status = make_label("", "warn", wrap=True)
        lay.addWidget(self.status)

        pinned = manager.pinned()
        self._order = list(pinned)
        for kind in ("app", "game"):
            for item in manager.table(kind).items():
                entry = QListWidgetItem(QIcon(icons.pixmap(kind, item, 28)), item.title + ("   (игра)" if kind == "game" else ""))
                entry.setData(Qt.ItemDataRole.UserRole, (kind, item.id))
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Checked if (kind, item.id) in pinned else Qt.CheckState.Unchecked)
                self.list.addItem(entry)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Готово")
        ok.setObjectName("primary")
        ok.clicked.connect(self._apply)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.list.count()):
            self.list.item(i).setHidden(bool(needle) and needle not in self.list.item(i).text().lower())

    def _apply(self) -> None:
        chosen = []
        for i in range(self.list.count()):
            entry = self.list.item(i)
            if entry.checkState() == Qt.CheckState.Checked:
                chosen.append(tuple(entry.data(Qt.ItemDataRole.UserRole)))
        # порядок: сначала уже закреплённые (как было), затем новые
        ordered = [r for r in self._order if r in chosen] + [r for r in chosen if r not in self._order]
        if not self._manager.set_pins(ordered):
            self.status.setText(f"Выбрано слишком много: в острове помещается не больше {MAX_PINS}.")
            return
        self.accept()


def make_checkbox(text: str, checked: bool = False) -> QCheckBox:
    box = QCheckBox(text)
    box.setChecked(checked)
    return box
