"""Автоматизация Windows: запуск NOVA и выбранных приложений вместе с системой."""
from __future__ import annotations

from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QListWidget, QMessageBox, QPushButton

from app_context import AppContext
from system import automation
from ui.widgets import Card, HLine, Page, Toggle, make_label, setting_row


class AutomationPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Автоматизация", "Управляйте программами, которые запускаются вместе с Windows.")
        self._ctx = ctx

        nova = Card(spacing=6)
        nova.body.addWidget(make_label("Запуск NOVA", "h2"))
        self.autostart = Toggle(False)
        nova.body.addWidget(setting_row(
            "Запускать NOVA вместе с Windows",
            "NOVA запускается в фоне, а Dynamic Island появляется автоматически. Окно программы не мешает работе.",
            self.autostart))
        self.message = make_label("", "caption", wrap=True)
        nova.body.addWidget(self.message)
        self.body.addWidget(nova)

        apps = Card(spacing=8)
        apps.body.addWidget(make_label("Программы при запуске Windows", "h2"))
        apps.body.addWidget(make_label(
            "Здесь показаны записи автозапуска текущего пользователя: значения реестра Run и ярлыки папки Startup. "
            "Можно добавить EXE, удалить выбранную запись или ярлык. Общесистемный автозапуск не затрагивается.",
            "muted", wrap=True))
        self.startup_list = QListWidget()
        self.startup_list.setMinimumHeight(180)
        apps.body.addWidget(self.startup_list)
        row = QHBoxLayout()
        self.add_btn = QPushButton("Добавить программу…")
        self.add_btn.setObjectName("primary")
        self.add_btn.clicked.connect(self._add_app)
        self.remove_btn = QPushButton("Убрать выбранную")
        self.remove_btn.clicked.connect(self._remove_app)
        self.refresh_btn = QPushButton("Обновить")
        self.refresh_btn.clicked.connect(self._refresh_apps)
        row.addWidget(self.add_btn)
        row.addWidget(self.remove_btn)
        row.addWidget(self.refresh_btn)
        row.addStretch(1)
        apps.body.addLayout(row)
        self.apps_message = make_label("", "caption", wrap=True)
        apps.body.addWidget(self.apps_message)
        self.body.addWidget(apps)

        note = Card(spacing=6)
        note.body.addWidget(make_label("Как это работает", "h2"))
        note.body.addWidget(make_label(
            "Программы добавляются в автозапуск текущего пользователя Windows без запроса прав администратора. "
            "Для добавления выберите EXE в нужной папке. Удаление записи из этого списка не удаляет саму программу.",
            "muted", wrap=True))
        self.body.addWidget(note)
        self.finish()

        if not automation.IS_WINDOWS:
            self.autostart.setEnabled(False)
            self.add_btn.setEnabled(False)
            self.remove_btn.setEnabled(False)
            self.message.setText("Автозапуск доступен только в Windows.")
        self._sync()
        self.autostart.toggled.connect(self._on_toggled)
        self._refresh_apps()

    def showEvent(self, event) -> None:  # noqa: N802, ANN001
        super().showEvent(event)
        self._sync()
        self._refresh_apps()

    def _sync(self) -> None:
        actual = automation.is_autostart_enabled()
        if self.autostart.isChecked() != actual:
            self.autostart.set_checked_silent(actual)

    def _on_toggled(self, enabled: bool) -> None:
        if automation.set_autostart(enabled):
            self.message.setText("Автозапуск NOVA включён." if enabled else "Автозапуск NOVA выключен.")
        else:
            self.autostart.set_checked_silent(automation.is_autostart_enabled())
            self.message.setText("Не удалось изменить автозапуск (подробности в nova.log).")

    def _refresh_apps(self) -> None:
        self.startup_list.clear()
        for kind, token, display in automation.list_startup_entries():
            prefix = "Реестр" if kind == "registry" else "Папка Startup"
            self.startup_list.addItem(f"{prefix}  ·  {display}")
            self.startup_list.item(self.startup_list.count() - 1).setData(256, (kind, token))
        if not self.startup_list.count():
            self.startup_list.addItem("Пользовательский автозапуск пуст.")

    def _add_app(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите программу", "", "Программы (*.exe)")
        if not path:
            return
        ok, message = automation.add_startup_app(path)
        self.apps_message.setText(message)
        self._refresh_apps()

    def _remove_app(self) -> None:
        item = self.startup_list.currentItem()
        if item is None:
            self.apps_message.setText("Сначала выберите программу из списка.")
            return
        entry = item.data(256)
        if not entry:
            self.apps_message.setText("Сначала выберите запись автозапуска.")
            return
        answer = QMessageBox.question(
            self, "Убрать из автозапуска?",
            "Будет удалена только запись запуска или ярлык. Сама программа и её файлы останутся на компьютере.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        kind, token = entry
        if automation.remove_startup_entry(str(kind), str(token)):
            self.apps_message.setText("Запись убрана из автозапуска. Программа не удалена.")
        else:
            self.apps_message.setText("Не удалось убрать запись.")
        self._refresh_apps()
