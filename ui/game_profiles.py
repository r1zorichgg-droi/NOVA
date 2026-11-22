"""Виджеты Game Profiles и ручного Game Boost."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget

from app_context import AppContext
from launcher.game_profiles import clean_profiles
from ui.widgets import Card, HLine, make_label


class GameProfilesWidget(QWidget):
    """Профили для каждой игры: режим производительности, FPS HUD и приоритет при прямом запуске EXE."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._profiles = clean_profiles(ctx.settings.get("game_profiles"))

        card = Card(spacing=9)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        card.body.addWidget(make_label("Game Profiles", "h2"))
        card.body.addWidget(make_label(
            "Создай профиль по имени EXE игры. Game Boost и приоритет применяются при запуске игры из библиотеки NOVA; "
            "настройка FPS-оверлея применяется, когда эта игра становится активным окном. "
            "Приоритет можно задать только для прямого запуска EXE — NOVA не меняет приоритет лаунчеров и античитов.",
            "muted", wrap=True))

        row = QHBoxLayout()
        self.profile_box = QComboBox()
        self.profile_box.setMinimumWidth(220)
        self.profile_box.currentIndexChanged.connect(self._load_selected)
        row.addWidget(self.profile_box, 1)
        self.new_button = QPushButton("Новый профиль")
        self.new_button.clicked.connect(self._new_profile)
        row.addWidget(self.new_button)
        self.delete_button = QPushButton("Удалить")
        self.delete_button.clicked.connect(self._delete_profile)
        row.addWidget(self.delete_button)
        card.body.addLayout(row)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Название профиля, например Fortnite")
        card.body.addWidget(self.name_edit)
        self.process_edit = QLineEdit()
        self.process_edit.setPlaceholderText("Имя процесса, например FortniteClient-Win64-Shipping.exe")
        card.body.addWidget(self.process_edit)

        self.boost_check = QCheckBox("Включать Game Boost для этой игры")
        self.boost_check.setChecked(True)
        self.overlay_check = QCheckBox("Показывать FPS-оверлей для этой игры")
        self.overlay_check.setChecked(True)
        card.body.addWidget(self.boost_check)
        card.body.addWidget(self.overlay_check)

        priority_row = QHBoxLayout()
        priority_row.addWidget(make_label("Приоритет для прямого запуска", "caption"))
        self.priority_box = QComboBox()
        self.priority_box.addItem("Обычный", "normal")
        self.priority_box.addItem("Выше обычного", "above_normal")
        self.priority_box.addItem("Высокий", "high")
        self.priority_box.setCurrentIndex(1)
        priority_row.addWidget(self.priority_box)
        priority_row.addStretch(1)
        card.body.addLayout(priority_row)

        card.body.addWidget(HLine())
        actions = QHBoxLayout()
        self.save_button = QPushButton("Сохранить профиль")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self._save_profile)
        actions.addWidget(self.save_button)
        actions.addStretch(1)
        card.body.addLayout(actions)
        self.hint = make_label("Профиль привязывается к имени EXE. Его можно посмотреть в Диспетчере задач → Подробности.",
                               "caption", wrap=True)
        card.body.addWidget(self.hint)
        self._refresh_profiles()

    def _refresh_profiles(self, selected: int = 0) -> None:
        self._profiles = clean_profiles(self._ctx.settings.get("game_profiles"))
        self.profile_box.blockSignals(True)
        self.profile_box.clear()
        for item in self._profiles:
            self.profile_box.addItem(f"{item['name']} · {item['process']}")
        self.profile_box.blockSignals(False)
        self.profile_box.setCurrentIndex(min(selected, len(self._profiles) - 1) if self._profiles else -1)
        self.delete_button.setEnabled(bool(self._profiles))
        if self._profiles:
            self._load_selected(self.profile_box.currentIndex())
        else:
            self._new_profile()

    def _load_selected(self, index: int) -> None:
        if not 0 <= index < len(self._profiles):
            return
        item = self._profiles[index]
        self.name_edit.setText(item["name"])
        self.process_edit.setText(item["process"])
        self.boost_check.setChecked(item["boost"])
        self.overlay_check.setChecked(item["fps_overlay"])
        self.priority_box.setCurrentIndex(max(0, self.priority_box.findData(item["priority"])))

    def _new_profile(self) -> None:
        self.profile_box.setCurrentIndex(-1)
        self.name_edit.clear()
        self.process_edit.clear()
        self.boost_check.setChecked(True)
        self.overlay_check.setChecked(True)
        self.priority_box.setCurrentIndex(1)
        self.delete_button.setEnabled(bool(self._profiles))

    def _save_profile(self) -> None:
        name = self.name_edit.text().strip()
        process = self.process_edit.text().strip()
        if not name or not process:
            self.hint.setText("Заполни название профиля и имя EXE процесса.")
            return
        value = {
            "name": name,
            "process": process,
            "boost": self.boost_check.isChecked(),
            "fps_overlay": self.overlay_check.isChecked(),
            "priority": str(self.priority_box.currentData() or "above_normal"),
        }
        selected = self.profile_box.currentIndex()
        if 0 <= selected < len(self._profiles):
            self._profiles[selected] = value
        else:
            self._profiles.append(value)
            selected = len(self._profiles) - 1
        self._profiles = clean_profiles(self._profiles)
        self._ctx.settings.set("game_profiles", self._profiles)
        self._refresh_profiles(selected)
        self.hint.setText("Профиль сохранён. Он будет применяться при следующем запуске/обнаружении игры.")

    def _delete_profile(self) -> None:
        index = self.profile_box.currentIndex()
        if not 0 <= index < len(self._profiles):
            return
        del self._profiles[index]
        self._ctx.settings.set("game_profiles", self._profiles)
        self._refresh_profiles(max(0, index - 1))


class GameBoostWidget(QWidget):
    """Ручной запуск/откат безопасного режима Game Boost, использующий существующий журнал восстановления."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._library = ctx.library
        card = Card(spacing=8)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        head = QHBoxLayout()
        head.addWidget(make_label("Game Boost", "h2"))
        head.addStretch(1)
        self.status = make_label("Выключен", "caption")
        head.addWidget(self.status)
        card.body.addLayout(head)
        card.body.addWidget(make_label(
            "Временно включает выбранную схему электропитания, Игровой режим Windows и отключает фоновую запись. "
            "NOVA сохраняет прежние значения и возвращает их кнопкой ниже или при завершении работы.",
            "muted", wrap=True))
        actions = QHBoxLayout()
        self.enable_button = QPushButton("Включить Game Boost")
        self.enable_button.setObjectName("primary")
        self.enable_button.clicked.connect(self._enable)
        self.restore_button = QPushButton("Восстановить настройки")
        self.restore_button.clicked.connect(self._restore)
        actions.addWidget(self.enable_button)
        actions.addWidget(self.restore_button)
        actions.addStretch(1)
        card.body.addLayout(actions)
        self._sync()
        ctx.library.boost_changed.connect(lambda _active: self._sync())

    def _sync(self) -> None:
        active = self._library.boost_active
        self.status.setText("Активен" if active else "Выключен")
        self.enable_button.setEnabled(not active)
        self.restore_button.setEnabled(active)

    def _enable(self) -> None:
        self._library.activate_boost_now()
        self._sync()

    def _restore(self) -> None:
        self._library.stop_boost_now()
        self._sync()
