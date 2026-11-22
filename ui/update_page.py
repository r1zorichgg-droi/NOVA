"""Страница NOVA Auto Update."""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QMessageBox, QProgressBar, QPushButton

from app_context import AppContext
from config import APP_VERSION, UPDATE_MANIFEST_URL
from island.model import Notification
from system.updater import check_update, download_installer, launch_installer
from ui.widgets import Card, Page, make_label
from ui.workers import AsyncRunner, ProgressRunner


class UpdatePage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("NOVA Auto Update", "Проверка новой версии, загрузка установщика и обновление без потери пользовательских настроек.")
        self._ctx = ctx
        # Если в старой установке пользователь вручную сохранил шаблон OWNER/NOVA,
        # автоматически переключаемся на адрес, встроенный в актуальную сборку.
        saved_manifest = str(ctx.settings.get("update_manifest_url"))
        if "OWNER/NOVA" in saved_manifest and "OWNER/NOVA" not in UPDATE_MANIFEST_URL:
            ctx.settings.set("update_manifest_url", UPDATE_MANIFEST_URL)
        self._runner = AsyncRunner(self)
        self._progress_runner = ProgressRunner(self)
        self._manifest: dict | None = None
        self._download_path = None
        self._busy = False

        current = Card(spacing=8)
        current.body.addWidget(make_label("Текущая версия", "h2"))
        self.version_label = make_label(f"NOVA {APP_VERSION}", "big")
        current.body.addWidget(self.version_label)
        self.status = make_label("Обновления проверяются через публичный HTTPS-манифест.", "muted", wrap=True)
        current.body.addWidget(self.status)
        self.body.addWidget(current)

        source = Card(spacing=8)
        source.body.addWidget(make_label("Источник обновлений", "h2"))
        source.body.addWidget(make_label(
            "Вставь URL latest.json из GitHub Releases. Для всех пользователей одной и той же сборки укажи постоянную "
            "ссылку releases/latest/download/latest.json и включи этот URL в следующую публикацию NOVA.",
            "muted", wrap=True))
        self.url_edit = QLineEdit(str(ctx.settings.get("update_manifest_url")))
        self.url_edit.setPlaceholderText("https://github.com/OWNER/NOVA/releases/latest/download/latest.json")
        source.body.addWidget(self.url_edit)
        self.auto_check = QCheckBox("Проверять обновления автоматически при запуске NOVA")
        self.auto_check.setChecked(bool(ctx.settings.get("update_auto_check")))
        self.auto_check.toggled.connect(lambda value: ctx.settings.set("update_auto_check", bool(value)))
        source.body.addWidget(self.auto_check)
        source_buttons = QHBoxLayout()
        self.save_button = QPushButton("Сохранить адрес")
        self.save_button.clicked.connect(self._save_url)
        self.check_button = QPushButton("Проверить обновления")
        self.check_button.setObjectName("primary")
        self.check_button.clicked.connect(self.check_now)
        source_buttons.addWidget(self.save_button)
        source_buttons.addWidget(self.check_button)
        source_buttons.addStretch(1)
        source.body.addLayout(source_buttons)
        self.body.addWidget(source)

        update = Card(spacing=8)
        update.body.addWidget(make_label("Доступное обновление", "h2"))
        self.release_label = make_label("Пока не проверено", "h3")
        update.body.addWidget(self.release_label)
        self.notes = make_label("", "muted", wrap=True)
        update.body.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        update.body.addWidget(self.progress)
        self.install_button = QPushButton("Скачать и установить обновление")
        self.install_button.setObjectName("primary")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_update)
        update.body.addWidget(self.install_button)
        self.body.addWidget(update)

        safety = Card(spacing=6)
        safety.body.addWidget(make_label("Как это работает", "h2"))
        safety.body.addWidget(make_label(
            "NOVA получает latest.json по HTTPS, сравнивает версию, скачивает Inno Setup EXE и сверяет SHA-256. "
            "Только после совпадения запускается установщик. База NOVA и настройки в %APPDATA% не удаляются. "
            "Установка требует согласия пользователя и закрывает работающую NOVA.",
            "muted", wrap=True))
        self.body.addWidget(safety)
        self.finish()

        if bool(ctx.settings.get("update_auto_check")):
            QTimer.singleShot(4500, self.check_now)

    def _save_url(self) -> None:
        self._ctx.settings.set("update_manifest_url", self.url_edit.text().strip())
        self.status.setText("Адрес обновлений сохранён.")

    def check_now(self) -> None:
        if self._busy:
            return
        url = self.url_edit.text().strip()
        if not url or "OWNER/NOVA" in url:
            self.status.setText("Сначала укажи публичную HTTPS-ссылку на latest.json своего GitHub-репозитория.")
            return
        self._busy = True
        self.check_button.setEnabled(False)
        self.status.setText("Проверяем доступность новой версии…")
        self._runner.submit(lambda: check_update(url), self._on_checked)

    def _on_checked(self, result, error) -> None:  # noqa: ANN001
        self._busy = False
        self.check_button.setEnabled(True)
        if error is not None or result is None:
            self._manifest = None
            self.install_button.setEnabled(False)
            self.status.setText(f"Не удалось проверить обновления: {error}")
            return
        self._manifest = result
        version = result["version"]
        if result["is_newer"]:
            self.release_label.setText(f"Доступна NOVA {version}")
            self.notes.setText(result.get("notes", "Для этой версии нет описания изменений."))
            self.install_button.setEnabled(True)
            self.status.setText("Новая версия найдена. Установщик будет проверен по SHA-256 перед запуском.")
            try:
                self._ctx.hub.post(Notification(
                    "system", "Доступно обновление NOVA",
                    f"Версия {version} найдена. Открой вкладку «Обновления NOVA».",
                    duration_ms=5200,
                ))
            except Exception:
                pass
        else:
            self.release_label.setText(f"У тебя актуальная версия NOVA {APP_VERSION}")
            self.notes.setText("Новых версий не найдено.")
            self.install_button.setEnabled(False)
            self.status.setText("Проверка завершена.")

    def install_update(self) -> None:
        if self._busy or not self._manifest or not self._manifest.get("is_newer"):
            return
        answer = QMessageBox.question(
            self, "Обновить NOVA", f"Загрузить и установить NOVA {self._manifest['version']}? Программа закроется на время обновления.")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self.install_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Загружаем установщик и проверяем контрольную сумму…")
        self._progress_runner.start(
            lambda report: download_installer(self._manifest or {}, report),
            self._on_download_progress,
            self._on_downloaded,
        )

    def _on_download_progress(self, value: int) -> None:
        self.progress.setValue(int(value))

    def _on_downloaded(self, path, error) -> None:  # noqa: ANN001
        self._busy = False
        if error is not None or path is None:
            self.status.setText(f"Обновление не установлено: {error}")
            self.install_button.setEnabled(True)
            self.check_button.setEnabled(True)
            return
        try:
            launch_installer(path)
        except OSError as exc:
            self.status.setText(f"Не удалось запустить установщик: {exc}")
            self.install_button.setEnabled(True)
            self.check_button.setEnabled(True)
            return
        self.status.setText("Установщик запущен. NOVA закрывается для обновления.")
        from PySide6.QtWidgets import QApplication
        QTimer.singleShot(500, QApplication.quit)
