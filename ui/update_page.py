"""Страница обязательного обновления NOVA."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressBar, QPushButton

from app_context import AppContext
from config import APP_VERSION, UPDATE_MANIFEST_URL
from system.updater import check_update, download_installer, launch_installer
from ui.widgets import Card, Page, make_label
from ui.workers import AsyncRunner, ProgressRunner


class UpdatePage(Page):
    """Проверяет обновления при запуске и периодически, не показывая технический URL."""

    CHECK_INTERVAL_MS = 60 * 60 * 1000

    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            "Обновления NOVA",
            "Обязательные обновления безопасности, исправления и новые возможности NOVA.",
        )
        self._ctx = ctx
        self._runner = AsyncRunner(self)
        self._progress_runner = ProgressRunner(self)
        self._manifest: dict | None = None
        self._download_path: Path | None = None
        self._busy = False
        self._prompt_open = False

        # URL фиксирован в конфигурации сборки; старые пользовательские значения мигрируют автоматически.
        ctx.settings.set("update_manifest_url", UPDATE_MANIFEST_URL)

        current = Card(spacing=8)
        current.body.addWidget(make_label("Установленная версия", "h2"))
        current.body.addWidget(make_label(f"NOVA {APP_VERSION}", "big"))
        self.status = make_label(
            "Автоматическая проверка выполняется при запуске и затем каждый час.",
            "muted",
            wrap=True,
        )
        current.body.addWidget(self.status)
        self.body.addWidget(current)

        channel = Card(spacing=8)
        channel.body.addWidget(make_label("Канал обновлений", "h2"))
        channel.body.addWidget(make_label(
            "Официальный канал NOVA Project · GitHub Releases. "
            "Загрузки проверяются по SHA-256 перед установкой.",
            "muted",
            wrap=True,
        ))
        channel.body.addWidget(make_label(
            "Когда новая версия опубликована, NOVA автоматически загружает её. "
            "Чтобы продолжить пользоваться программой, нужно установить обновление; "
            "отложить его и продолжить работу на старой версии нельзя.",
            "muted",
            wrap=True,
        ))
        self.check_button = QPushButton("Проверить сейчас")
        self.check_button.setObjectName("primary")
        self.check_button.clicked.connect(self.check_now)
        channel.body.addWidget(self.check_button)
        self.body.addWidget(channel)

        update = Card(spacing=8)
        update.body.addWidget(make_label("Состояние обновления", "h2"))
        self.release_label = make_label("Проверка ещё не выполнялась", "h3")
        update.body.addWidget(self.release_label)
        self.notes = make_label("", "muted", wrap=True)
        update.body.addWidget(self.notes)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        update.body.addWidget(self.progress)
        self.install_button = QPushButton("Повторить загрузку")
        self.install_button.setObjectName("primary")
        self.install_button.setEnabled(False)
        self.install_button.clicked.connect(self.install_update)
        update.body.addWidget(self.install_button)
        self.body.addWidget(update)

        info = Card(spacing=6)
        info.body.addWidget(make_label("Как устанавливается обновление", "h2"))
        info.body.addWidget(make_label(
            "NOVA проверяет HTTPS-манифест, скачивает установщик и сверяет SHA-256. "
            "После проверки появляется обязательное подтверждение установки. "
            "Можно установить обновление или закрыть NOVA. Настройки, игровые профили "
            "и история FPS в %APPDATA%\\NOVA сохраняются.",
            "muted",
            wrap=True,
        ))
        self.body.addWidget(info)
        self.finish()

        # Проверка обязательна и не зависит от пользовательского переключателя в старых настройках.
        QTimer.singleShot(3500, self.check_now)
        self._check_timer = QTimer(self)
        self._check_timer.setInterval(self.CHECK_INTERVAL_MS)
        self._check_timer.timeout.connect(self.check_now)
        self._check_timer.start()

    def check_now(self) -> None:
        if self._busy or self._prompt_open:
            return
        self._ctx.settings.set("update_manifest_url", UPDATE_MANIFEST_URL)
        self._busy = True
        self.check_button.setEnabled(False)
        self.status.setText("Проверяем официальные обновления NOVA…")
        self._runner.submit(lambda: check_update(UPDATE_MANIFEST_URL), self._on_checked)

    def _on_checked(self, result, error) -> None:  # noqa: ANN001
        self._busy = False
        self.check_button.setEnabled(True)
        if error is not None or result is None:
            self.status.setText(
                "Не удалось связаться с сервером обновлений. NOVA повторит проверку позже. "
                f"Подробности: {error}"
            )
            self.release_label.setText("Сервер обновлений временно недоступен")
            return

        self._manifest = result
        version = result["version"]
        if result["is_newer"]:
            self.release_label.setText(f"Обязательное обновление: NOVA {version}")
            self.notes.setText(result.get("notes", "Для этой версии нет описания изменений."))
            self.status.setText("Новая версия найдена. Начинаем загрузку установщика…")
            self._begin_download()
        else:
            self.release_label.setText(f"Установлена актуальная версия NOVA {APP_VERSION}")
            self.notes.setText("Новых версий не найдено.")
            self.status.setText("Проверка завершена. Следующая проверка — автоматически.")

    def _begin_download(self) -> None:
        if self._busy or not self._manifest or not self._manifest.get("is_newer"):
            return
        self._busy = True
        self.install_button.setEnabled(False)
        self.check_button.setEnabled(False)
        self.progress.setValue(0)
        self.progress.show()
        self.status.setText("Загружаем обязательное обновление и проверяем SHA-256…")
        self._progress_runner.start(
            lambda report: download_installer(self._manifest or {}, report),
            self._on_download_progress,
            self._on_downloaded,
        )

    def install_update(self) -> None:
        """Кнопка повтора после сетевой ошибки."""
        if self._download_path and self._download_path.is_file():
            self._require_install()
        else:
            self._begin_download()

    def _on_download_progress(self, value: int) -> None:
        self.progress.setValue(int(value))

    def _bring_window_forward(self) -> None:
        window = self.window()
        try:
            if window.isMinimized():
                window.showNormal()
            else:
                window.show()
            window.raise_()
            window.activateWindow()
        except Exception:
            pass

    def _on_downloaded(self, path, error) -> None:  # noqa: ANN001
        self._busy = False
        self.check_button.setEnabled(True)
        if error is not None or path is None:
            self.status.setText(f"Не удалось загрузить обновление: {error}")
            self.install_button.setText("Повторить загрузку")
            self.install_button.setEnabled(True)
            self._require_retry_or_exit()
            return

        self._download_path = Path(path)
        self.progress.setValue(100)
        self.status.setText("Установщик проверен. Требуется установить обновление.")
        self._require_install()

    def _require_retry_or_exit(self) -> None:
        if self._prompt_open:
            return
        self._prompt_open = True
        self._bring_window_forward()
        dialog = QMessageBox(self.window())
        dialog.setWindowTitle("Не удалось загрузить обновление NOVA")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setText("Для продолжения требуется обновить NOVA, но загрузка не удалась.")
        dialog.setInformativeText(
            "Проверь интернет-соединение. Можно повторить загрузку или закрыть NOVA; "
            "при следующем запуске проверка повторится."
        )
        retry = dialog.addButton("Повторить загрузку", QMessageBox.ButtonRole.AcceptRole)
        exit_button = dialog.addButton("Выйти из NOVA", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(retry)
        dialog.exec()
        self._prompt_open = False
        if dialog.clickedButton() is retry:
            self._begin_download()
        else:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def _require_install(self) -> None:
        if self._prompt_open:
            return
        self._prompt_open = True
        self._bring_window_forward()
        version = (self._manifest or {}).get("version", "новая версия")
        dialog = QMessageBox(self.window())
        dialog.setWindowTitle("Обязательное обновление NOVA")
        dialog.setIcon(QMessageBox.Icon.Information)
        dialog.setText(f"Готова NOVA {version}.")
        dialog.setInformativeText(
            "Чтобы продолжить пользоваться NOVA, установите обновление. "
            "Нажмите «Установить и перезапустить» или закройте программу. "
            "Установщик уже проверен по SHA-256."
        )
        install = dialog.addButton(
            "Установить и перезапустить", QMessageBox.ButtonRole.AcceptRole
        )
        dialog.addButton("Выйти из NOVA", QMessageBox.ButtonRole.RejectRole)
        dialog.setDefaultButton(install)
        dialog.exec()
        clicked = dialog.clickedButton()
        self._prompt_open = False

        if clicked is not install:
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return

        if not self._download_path or not self._download_path.is_file():
            self.status.setText("Установщик не найден. Повторяем загрузку…")
            self._begin_download()
            return
        try:
            launch_installer(self._download_path)
        except OSError as exc:
            QMessageBox.critical(
                self.window(),
                "Ошибка установки",
                f"Не удалось запустить установщик NOVA: {exc}",
            )
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return

        self.status.setText("Установщик запущен. NOVA закрывается для обновления.")
        QTimer.singleShot(500, QApplication.quit)
